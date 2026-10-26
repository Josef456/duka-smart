const DB_NAME = 'dukas-smart-offline';
const DB_VERSION = 1;

function openOfflineDb() {
    return new Promise((resolve, reject) => {
        const request = indexedDB.open(DB_NAME, DB_VERSION);
        request.onupgradeneeded = () => {
            const db = request.result;
            db.createObjectStore('products', { keyPath: 'id' });
            db.createObjectStore('sales', { keyPath: 'id' });
            db.createObjectStore('settings', { keyPath: 'key' });
        };
        request.onsuccess = () => resolve(request.result);
        request.onerror = () => reject(request.error);
    });
}

async function readAll(storeName) {
    const db = await openOfflineDb();
    return new Promise((resolve, reject) => {
        const request = db.transaction(storeName).objectStore(storeName).getAll();
        request.onsuccess = () => { db.close(); resolve(request.result); };
        request.onerror = () => { db.close(); reject(request.error); };
    });
}

async function readSetting(key) {
    const db = await openOfflineDb();
    return new Promise((resolve, reject) => {
        const request = db.transaction('settings').objectStore('settings').get(key);
        request.onsuccess = () => { db.close(); resolve(request.result?.value); };
        request.onerror = () => { db.close(); reject(request.error); };
    });
}

async function writeRows(storeName, rows, clear = false) {
    const db = await openOfflineDb();
    return new Promise((resolve, reject) => {
        const transaction = db.transaction(storeName, 'readwrite');
        const store = transaction.objectStore(storeName);
        if (clear) store.clear();
        rows.forEach(row => store.put(row));
        transaction.oncomplete = () => { db.close(); resolve(); };
        transaction.onerror = () => { db.close(); reject(transaction.error); };
        transaction.onabort = () => { db.close(); reject(transaction.error); };
    });
}

async function saveSetting(key, value) {
    return writeRows('settings', [{ key, value }]);
}

function createSaleId() {
    if (crypto.randomUUID) return crypto.randomUUID();
    const bytes = crypto.getRandomValues(new Uint8Array(16));
    bytes[6] = (bytes[6] & 0x0f) | 0x40;
    bytes[8] = (bytes[8] & 0x3f) | 0x80;
    const hex = Array.from(bytes, byte => byte.toString(16).padStart(2, '0')).join('');
    return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}

document.addEventListener('DOMContentLoaded', async () => {
    const productSelect = document.getElementById('product');
    const quantityInput = document.getElementById('quantity');
    const total = document.getElementById('total');
    const form = document.querySelector('[data-sale-form]');
    const status = document.getElementById('sync-status');
    const message = document.getElementById('sale-message');
    const pendingList = document.getElementById('pending-sales');
    const ownerFromPage = form?.dataset.owner || document.body.dataset.user || '';
    let products = [];
    let pendingSales = [];

    if (!form && !status && !ownerFromPage) return;

    const setMessage = (text, category = 'success') => {
        if (!message) return;
        message.textContent = text;
        message.className = `flash ${category}`;
    };

    const updateTotal = () => {
        if (!productSelect || !quantityInput || !total) return;
        const option = productSelect.options[productSelect.selectedIndex];
        const price = Number(option?.dataset.price || 0);
        const stock = Number(option?.dataset.stock || 0);
        quantityInput.max = String(stock);
        total.textContent = `UGX ${(price * Math.max(0, Number(quantityInput.value || 0))).toLocaleString('en-UG', { maximumFractionDigits: 0 })}`;
    };

    const renderPendingSales = () => {
        if (!pendingList) return;
        pendingList.replaceChildren();
        if (pendingSales.length === 0) {
            const empty = document.createElement('p');
            empty.className = 'empty';
            empty.textContent = 'No sales are waiting to synchronize.';
            pendingList.append(empty);
            return;
        }
        pendingSales.forEach(sale => {
            const product = products.find(item => item.id === sale.product_id);
            const row = document.createElement('div');
            row.className = 'tip';
            const heading = document.createElement('b');
            heading.textContent = `${product?.name || `Product #${sale.product_id}`} × ${sale.quantity} · UGX ${(sale.unit_price * sale.quantity).toLocaleString('en-UG')}`;
            const details = document.createElement('p');
            const date = new Date(sale.sold_at).toLocaleString();
            details.textContent = sale.message
                ? `Needs attention: ${sale.message} · ${date}`
                : `Waiting to synchronize · ${date}`;
            row.append(heading, details);
            pendingList.append(row);
        });
    };

    const renderProducts = () => {
        if (productSelect) {
            const selected = productSelect.value;
            const queuedByProduct = new Map();
            pendingSales.forEach(sale => {
                queuedByProduct.set(sale.product_id, (queuedByProduct.get(sale.product_id) || 0) + sale.quantity);
            });
            productSelect.replaceChildren(new Option('Choose product', ''));
            products.forEach(product => {
                const stock = Math.max(0, product.quantity - (queuedByProduct.get(product.id) || 0));
                const option = new Option(`${product.name} — ${stock} in stock — UGX ${Number(product.selling_price).toLocaleString('en-UG')}`, product.id);
                option.dataset.price = product.selling_price;
                option.dataset.stock = String(stock);
                option.disabled = stock === 0;
                productSelect.add(option);
            });
            if (products.some(product => String(product.id) === selected)) productSelect.value = selected;
            updateTotal();
        }
        renderPendingSales();
    };

    const refreshStatus = () => {
        if (!status) return;
        const conflicts = pendingSales.filter(sale => sale.status === 'conflict' || sale.status === 'rejected');
        const connection = navigator.onLine ? 'Online' : 'Offline';
        let text = `${connection} · ${pendingSales.length} sale${pendingSales.length === 1 ? '' : 's'} waiting to synchronize.`;
        if (conflicts.length) {
            text += ` ${conflicts.length} need attention: ${conflicts[0].message || 'server validation failed'}`;
            status.className = 'notice';
        } else {
            status.className = navigator.onLine ? 'notice goodtext' : 'notice';
        }
        status.textContent = text;
    };

    const loadCachedSales = async () => {
        pendingSales = await readAll('sales');
        renderProducts();
        refreshStatus();
    };

    const storeProducts = async latest => {
        products = latest;
        await writeRows('products', latest, true);
        pendingSales = await readAll('sales');
        renderProducts();
    };

    const fetchProducts = async () => {
        if (!navigator.onLine) return;
        const response = await fetch('/api/products', { credentials: 'same-origin' });
        if (!response.ok) {
            if (response.status !== 401) throw new Error(`Could not refresh products (${response.status}).`);
            return;
        }
        const csrfToken = response.headers.get('X-CSRF-Token');
        if (csrfToken) await saveSetting('csrf_token', csrfToken);
        await storeProducts(await response.json());
    };

    const syncPending = async () => {
        if (!navigator.onLine || pendingSales.length === 0) return;
        const owner = ownerFromPage || await readSetting('owner');
        if (!owner) return;
        const csrfToken = document.querySelector('meta[name="csrf-token"]')?.content
            || await readSetting('csrf_token');
        if (!csrfToken) {
            if (status) status.textContent = 'Refresh the sales page online to securely synchronize pending sales.';
            return;
        }
        const salesToSync = [...pendingSales];
        for (let offset = 0; offset < salesToSync.length; offset += 50) {
            const batch = salesToSync.slice(offset, offset + 50);
            const response = await fetch('/api/sales/sync', {
                method: 'POST',
                credentials: 'same-origin',
                headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrfToken },
                body: JSON.stringify({ sales: batch.map(sale => ({ ...sale, owner: sale.owner || owner })) }),
            });
            if (response.status === 401) {
                if (status) status.textContent = 'Sign in again to synchronize pending sales.';
                return;
            }
            if (!response.ok) throw new Error(`Synchronization failed (${response.status}).`);
            const data = await response.json();
            const byId = new Map(data.results.map(result => [result.id, result]));
            const remaining = [];
            for (const sale of pendingSales) {
                const result = byId.get(sale.id);
                if (!result) {
                    remaining.push(sale);
                } else if (result.status === 'synced' || result.status === 'duplicate') {
                    continue;
                } else {
                    remaining.push({ ...sale, status: result.status, message: result.message });
                }
            }
            await writeRows('sales', remaining, true);
            pendingSales = remaining;
        }
        if (pendingSales.length === 0) setMessage('All pending sales have synchronized.');
        await fetchProducts();
        await loadCachedSales();
    };

    const queueSale = async sale => {
        pendingSales = [...pendingSales, sale];
        await writeRows('sales', pendingSales, true);
        renderProducts();
        refreshStatus();
        setMessage('Sale saved on this device and will synchronize when online.');
        if ('serviceWorker' in navigator) {
            navigator.serviceWorker.ready.then(registration => {
                if (registration.sync) return registration.sync.register('dukasales-sync');
                return undefined;
            }).catch(error => console.error('Background sale synchronization could not be scheduled:', error));
        }
        if (navigator.onLine) {
            window.setTimeout(() => syncPending().catch(error => {
                console.error('Could not retry queued sales:', error);
            }), 1000);
        }
    };

    try {
        if (ownerFromPage) await saveSetting('owner', ownerFromPage);
        products = await readAll('products');
        await loadCachedSales();
        try {
            await fetchProducts();
            await syncPending();
        } catch (error) {
            console.error('Could not refresh or synchronize offline sales:', error);
            if (status && navigator.onLine) status.textContent = 'Could not reach the server. Sales will remain safely queued on this device.';
        }
    } catch (error) {
        console.error('Offline sales storage is unavailable:', error);
        if (status) status.textContent = 'Offline storage is unavailable in this browser. Do not record sales while disconnected.';
    }

    productSelect?.addEventListener('change', updateTotal);
    quantityInput?.addEventListener('input', updateTotal);
    window.addEventListener('online', async () => {
        try {
            await fetchProducts();
            await syncPending();
        } catch (error) {
            console.error('Could not synchronize pending sales:', error);
            if (status) status.textContent = 'Connection restored, but synchronization failed. Pending sales remain on this device.';
        }
    });
    window.addEventListener('offline', refreshStatus);
    form?.addEventListener('submit', async event => {
        event.preventDefault();
        const product = products.find(item => String(item.id) === productSelect.value);
        const quantity = Number(quantityInput.value);
        const stock = Number(productSelect.options[productSelect.selectedIndex]?.dataset.stock || 0);
        if (!product || !Number.isInteger(quantity) || quantity < 1 || quantity > stock) {
            setMessage('Choose an available product and enter a quantity within the shown stock.', 'error');
            return;
        }
        let owner;
        try {
            owner = ownerFromPage || await readSetting('owner');
        } catch (error) {
            console.error('Could not read the saved shop identity:', error);
            setMessage('The saved shop identity is unavailable. Reopen the sales page online before recording a sale.', 'error');
            return;
        }
        if (!owner) {
            setMessage('Open this sales page online and sign in before recording offline sales.', 'error');
            return;
        }
        const sale = {
            id: createSaleId(),
            owner,
            product_id: product.id,
            quantity,
            payment_method: document.getElementById('payment-method')?.value || 'Cash',
            unit_price: product.selling_price,
            sold_at: new Date().toISOString(),
            status: 'pending',
        };
        try {
            if (!navigator.onLine) {
                await queueSale(sale);
            } else {
                const response = await fetch('/api/sales/sync', {
                    method: 'POST',
                    credentials: 'same-origin',
                    headers: {
                        'Content-Type': 'application/json',
                        'X-CSRF-Token': document.querySelector('meta[name="csrf-token"]')?.content || '',
                    },
                    body: JSON.stringify({ sales: [sale] }),
                });
                if (response.status >= 500) {
                    await queueSale(sale);
                } else if (response.status === 401) {
                    setMessage('Sign in again before recording this sale.', 'error');
                } else if (!response.ok) {
                    setMessage(`Sale was not saved (${response.status}).`, 'error');
                } else {
                    const result = (await response.json()).results[0];
                    if (result.status === 'synced' || result.status === 'duplicate') {
                        setMessage('Sale synchronized successfully.');
                        await fetchProducts();
                        await loadCachedSales();
                        form.reset();
                    } else {
                        setMessage(result.message || 'The sale needs review before it can synchronize.', 'error');
                    }
                }
            }
        } catch (error) {
            if (error instanceof TypeError) {
                try {
                    await queueSale(sale);
                } catch (storageError) {
                    console.error('Could not save the sale offline:', storageError);
                    setMessage('The sale could not be saved. Do not close this page; check device storage.', 'error');
                }
            } else {
                console.error('Could not record the sale:', error);
                setMessage('The sale was not confirmed. Retry when the connection is stable.', 'error');
            }
        }
    });
});

document.querySelector('[data-print]')?.addEventListener('click', () => window.print());
if ('serviceWorker' in navigator) {
    navigator.serviceWorker.register('/service-worker.js').catch(error => {
        console.error('Service worker registration failed:', error);
    });
}
