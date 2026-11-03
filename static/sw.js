const CACHE_NAME = 'dukas-smart-shell-v8';
const APP_SHELL = [
    '/static/style.css?v=20261103',
    '/static/style.css',
    '/static/app.js',
    '/static/manifest.webmanifest',
    '/static/offline-sales.html',
];
const SALES_DB = 'dukas-smart-offline';

self.addEventListener('install', event => {
    event.waitUntil(caches.open(CACHE_NAME).then(cache => cache.addAll(APP_SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener('activate', event => {
    event.waitUntil(
        caches.keys()
            .then(keys => Promise.all(keys.filter(key => key.startsWith('dukas-smart-shell-') && key !== CACHE_NAME).map(key => caches.delete(key))))
            .then(() => self.clients.claim()),
    );
});

self.addEventListener('fetch', event => {
    const request = event.request;
    if (request.method !== 'GET') return;
    const url = new URL(request.url);
    if (url.origin !== self.location.origin || url.pathname.startsWith('/api/')) return;

    if (request.mode === 'navigate') {
        event.respondWith(fetch(request).catch(async () => {
            const cached = await caches.match('/static/offline-sales.html');
            return cached || new Response('Offline sales page is not available. Reconnect and open the app first.', {
                status: 503,
                headers: { 'Content-Type': 'text/plain; charset=utf-8' },
            });
        }));
        return;
    }

    if (APP_SHELL.includes(url.pathname)) {
        event.respondWith(caches.match(request).then(cached => cached || fetch(request)));
    }
});

self.addEventListener('sync', event => {
    if (event.tag === 'dukasales-sync') event.waitUntil(synchronizeQueuedSales());
});

function openDb() {
    return new Promise((resolve, reject) => {
        const request = indexedDB.open(SALES_DB, 1);
        request.onsuccess = () => resolve(request.result);
        request.onerror = () => reject(request.error);
    });
}

function getQueuedSales(db) {
    return new Promise((resolve, reject) => {
        const request = db.transaction('sales').objectStore('sales').getAll();
        request.onsuccess = () => resolve(request.result);
        request.onerror = () => reject(request.error);
    });
}

function getSavedCsrfToken(db) {
    return new Promise((resolve, reject) => {
        const request = db.transaction('settings').objectStore('settings').get('csrf_token');
        request.onsuccess = () => resolve(request.result?.value);
        request.onerror = () => reject(request.error);
    });
}

function replaceQueuedSales(db, sales) {
    return new Promise((resolve, reject) => {
        const transaction = db.transaction('sales', 'readwrite');
        const store = transaction.objectStore('sales');
        store.clear();
        sales.forEach(sale => store.put(sale));
        transaction.oncomplete = resolve;
        transaction.onerror = () => reject(transaction.error);
        transaction.onabort = () => reject(transaction.error);
    });
}

async function synchronizeQueuedSales() {
    const db = await openDb();
    try {
        const sales = await getQueuedSales(db);
        const csrfToken = await getSavedCsrfToken(db);
        if (!csrfToken) return;
        const completed = new Set();
        for (let offset = 0; offset < sales.length; offset += 50) {
            const batch = sales.slice(offset, offset + 50);
            const response = await fetch('/api/sales/sync', {
                method: 'POST',
                credentials: 'same-origin',
                headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrfToken },
                body: JSON.stringify({ sales: batch }),
            });
            if (!response.ok) throw new Error(`Sales synchronization failed (${response.status}).`);
            const data = await response.json();
            const results = new Map(data.results.map(result => [result.id, result]));
            sales.forEach(sale => {
                const result = results.get(sale.id);
                if (result?.status === 'synced' || result?.status === 'duplicate') {
                    completed.add(sale.id);
                    return;
                }
                if (result) {
                    sale.status = result.status;
                    sale.message = result.message;
                }
            });
        }
        await replaceQueuedSales(db, sales.filter(sale => !completed.has(sale.id)));
    } finally {
        db.close();
    }
}
