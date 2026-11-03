import os
import re
import sys
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
TEST_DIRECTORY = tempfile.TemporaryDirectory()
os.environ['DATABASE_URL'] = f"sqlite:///{(Path(TEST_DIRECTORY.name) / 'tests.sqlite').as_posix()}"
os.environ['SESSION_SECRET'] = 'test-only-session-secret'

import app as application


class DukaApplicationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = application.app
        cls.app.config['TESTING'] = True
        cls.db = application.db
        result = cls.app.test_cli_runner().invoke(args=['db', 'upgrade'])
        if result.exit_code:
            raise AssertionError(result.output)

    @classmethod
    def tearDownClass(cls):
        with cls.app.app_context():
            cls.db.session.remove()
            cls.db.engine.dispose()
        TEST_DIRECTORY.cleanup()

    def setUp(self):
        with self.app.app_context():
            application.PasswordReset.query.delete()
            application.ActivityLog.query.delete()
            application.LoginEvent.query.delete()
            application.UserSession.query.delete()
            application.APIKey.query.delete()
            application.SalePayment.query.delete()
            application.CheckoutLine.query.delete()
            application.Transaction.query.delete()
            application.CheckoutSale.query.delete()
            application.LoginAttempt.query.delete()
            application.StockMovement.query.delete()
            application.CustomerDebt.query.delete()
            application.Product.query.delete()
            application.User.query.delete()
            business = application.Business.query.order_by(application.Business.id.asc()).first()
            if not business:
                business = application.Business(name='Test Shop')
                self.db.session.add(business)
                self.db.session.flush()
            user = application.User(
                username='duka-admin',
                password_hash=application.generate_password_hash('test-password-123'),
                business_id=business.id,
                email='admin@example.test',
                role='administrator',
            )
            self.db.session.add(user)
            self.db.session.commit()
        self.client = self.app.test_client()

    def csrf_token(self, client=None):
        client = client or self.client
        with client.session_transaction() as user_session:
            token = user_session.get('_csrf_token')
        if token:
            return token
        client.get('/login')
        with client.session_transaction() as user_session:
            return user_session['_csrf_token']

    def post_form(self, path, data=None, client=None):
        client = client or self.client
        form_data = dict(data or {})
        form_data['csrf_token'] = self.csrf_token(client)
        return client.post(path, data=form_data)

    def login(self):
        self.login_as(self.client, 'duka-admin', 'test-password-123')

    def login_as(self, client, username, password):
        response = self.post_form(
            '/login',
            {'username': username, 'password': password},
            client=client,
        )
        self.assertEqual(response.status_code, 302)
        return response

    def create_api_key(self, scopes=('products:read', 'sales:read')):
        response = self.post_form('/admin/api-keys', {
            'action': 'create',
            'name': 'Accounting export',
            'scopes': list(scopes),
            'expires_in_days': '90',
        })
        self.assertEqual(response.status_code, 200)
        match = re.search(r'id="new-api-key" value="([^"]+)"', response.get_data(as_text=True))
        self.assertIsNotNone(match)
        return match.group(1)

    def test_bar_mode_routes_are_retired_and_regular_pages_remain(self):
        self.login()
        for path in ('/bar', '/bar/inventory', '/bar/checkout', '/api/bar/checkout'):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 404)
        self.assertEqual(self.post_form('/bar/inventory/receive').status_code, 404)
        self.assertEqual(self.client.post(
            '/api/bar/checkout',
            json={},
            headers={'X-CSRF-Token': self.csrf_token()},
        ).status_code, 404)
        for path in ('/', '/products', '/sales', '/expenses', '/debts', '/reports'):
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 200)
        page = self.client.get('/')
        self.assertNotIn(b'Bar mode', page.data)
        self.assertNotIn(b'Fast checkout', page.data)

    def test_regular_product_form_uses_general_item_defaults(self):
        self.login()
        response = self.post_form('/products', {
            'name': 'Cooking oil',
            'sku': 'OIL-001',
            'quantity': '8',
            'cost_price': '3000',
            'selling_price': '4000',
            'low_stock_at': '2',
        })
        self.assertEqual(response.status_code, 302)
        with self.app.app_context():
            product = application.Product.query.filter_by(sku='OIL-001').one()
            self.assertEqual(product.category, 'general')
            self.assertEqual(product.base_unit, 'unit')
            self.assertEqual(product.purchase_unit, 'unit')
            self.assertEqual(product.units_per_purchase_unit, 1)
            self.assertEqual(product.quantity, 8)
        page = self.client.get('/products').get_data(as_text=True)
        self.assertNotIn('Drink (Bar Mode)', page)
        self.assertNotIn('purchase pack', page.lower())

    def test_admin_can_adjust_stock_with_audited_movement(self):
        self.login()
        created = self.post_form('/products', {
            'name': 'Soda',
            'sku': 'SODA-001',
            'quantity': '8',
            'cost_price': '1000',
            'selling_price': '1500',
            'low_stock_at': '2',
        })
        self.assertEqual(created.status_code, 302)
        with self.app.app_context():
            product_id = application.Product.query.filter_by(sku='SODA-001').one().id

        adjusted = self.post_form(f'/products/{product_id}/adjust-stock', {
            'adjustment_type': 'add',
            'quantity': '5',
            'reason': 'Supplier delivery',
        })
        self.assertEqual(adjusted.status_code, 302)
        with self.app.app_context():
            product = self.db.session.get(application.Product, product_id)
            self.assertEqual(product.quantity, 13)
            movement = application.StockMovement.query.filter_by(
                product_id=product_id,
                movement_type='adjustment',
            ).one()
            self.assertEqual(movement.quantity_delta, 5)
            self.assertEqual(movement.reason, 'Supplier delivery')
            self.assertEqual(movement.unit_cost, 1000)
            self.assertEqual(movement.created_by.username, 'duka-admin')
            audit = application.ActivityLog.query.filter_by(
                action='stock_adjusted',
                entity_id=str(product_id),
            ).order_by(application.ActivityLog.id.desc()).first()
            self.assertIn('Supplier delivery', audit.summary)
        page = self.client.get('/products').get_data(as_text=True)
        self.assertIn('Adjust stock', page)
        self.assertIn('Supplier delivery', page)

    def test_stock_adjustment_rejects_negative_stock_and_missing_reason(self):
        with self.app.app_context():
            product = application.Product(
                name='Limited stock item',
                sku='LIMITED-ADJUST-001',
                cost_price=2000,
                selling_price=3000,
                quantity=3,
                low_stock_at=1,
                business_id=application.Business.query.first().id,
            )
            self.db.session.add(product)
            self.db.session.commit()
            product_id = product.id
        self.login()

        too_much = self.post_form(f'/products/{product_id}/adjust-stock', {
            'adjustment_type': 'remove',
            'quantity': '4',
            'reason': 'Damaged',
        })
        no_reason = self.post_form(f'/products/{product_id}/adjust-stock', {
            'adjustment_type': 'add',
            'quantity': '1',
            'reason': '  ',
        })
        self.assertEqual(too_much.status_code, 302)
        self.assertEqual(no_reason.status_code, 302)
        with self.app.app_context():
            self.assertEqual(self.db.session.get(application.Product, product_id).quantity, 3)
            self.assertEqual(application.StockMovement.query.filter_by(product_id=product_id).count(), 0)

    def test_stock_adjustment_is_business_scoped_and_rejects_cashier(self):
        with self.app.app_context():
            business_id = application.Business.query.first().id
            other_business = application.Business(name='Other Shop')
            self.db.session.add(other_business)
            self.db.session.flush()
            product = application.Product(
                name='Other shop item',
                sku='OTHER-001',
                cost_price=1000,
                selling_price=1500,
                quantity=7,
                low_stock_at=1,
                business_id=other_business.id,
            )
            cashier = application.User(
                username='stock-test-cashier',
                password_hash=application.generate_password_hash('test-password-123'),
                business_id=business_id,
                email='cashier@example.test',
                role='cashier',
            )
            self.db.session.add_all([product, cashier])
            self.db.session.commit()
            product_id = product.id

        self.login()
        isolated = self.post_form(f'/products/{product_id}/adjust-stock', {
            'adjustment_type': 'remove',
            'quantity': '1',
            'reason': 'Correction',
        })
        self.assertEqual(isolated.status_code, 404)

        cashier_client = self.app.test_client()
        self.login_as(cashier_client, 'stock-test-cashier', 'test-password-123')
        denied = self.post_form(f'/products/{product_id}/adjust-stock', {
            'adjustment_type': 'remove',
            'quantity': '1',
            'reason': 'Correction',
        }, client=cashier_client)
        self.assertEqual(denied.status_code, 403)
        with self.app.app_context():
            self.assertEqual(self.db.session.get(application.Product, product_id).quantity, 7)
            self.assertEqual(application.StockMovement.query.filter_by(product_id=product_id).count(), 0)

    def test_existing_sale_records_and_offline_sync_remain_available(self):
        with self.app.app_context():
            product = application.Product(
                name='Sugar',
                sku='SUG-001',
                cost_price=2000,
                selling_price=3000,
                quantity=5,
                low_stock_at=1,
                business_id=application.Business.query.first().id,
            )
            self.db.session.add(product)
            self.db.session.commit()
            product_id = product.id
        self.login()
        sale = self.post_form('/sales', {
            'product_id': str(product_id),
            'quantity': '2',
            'payment_method': 'Cash',
        })
        self.assertEqual(sale.status_code, 302)
        with self.app.app_context():
            self.assertEqual(self.db.session.get(application.Product, product_id).quantity, 3)
            self.assertEqual(application.Transaction.query.filter_by(kind='sale').count(), 1)
        page = self.client.get('/products').get_data(as_text=True)
        self.assertNotIn('name="category"', page)
        self.assertIn('name="csrf_token"', page)
        response = self.client.post(
            '/api/sales/sync',
            json={'sales': [{'id': 'not-a-uuid'}]},
            headers={'X-CSRF-Token': self.csrf_token()},
        )
        self.assertEqual(response.status_code, 200)
        sale_payload = {
            'sales': [{
                'id': '6b3b0d45-a73c-43ee-bdde-94dcff0f8841',
                'owner': 'duka-admin',
                'product_id': product_id,
                'quantity': 1,
                'payment_method': 'Cash',
                'sold_at': '2026-11-02T10:00:00Z',
                'unit_price': 3000,
            }],
        }
        headers = {'X-CSRF-Token': self.csrf_token()}
        synced = self.client.post('/api/sales/sync', json=sale_payload, headers=headers)
        duplicate = self.client.post('/api/sales/sync', json=sale_payload, headers=headers)
        self.assertEqual(synced.json['results'][0]['status'], 'synced')
        self.assertEqual(duplicate.json['results'][0]['status'], 'duplicate')
        with self.app.app_context():
            self.assertEqual(self.db.session.get(application.Product, product_id).quantity, 2)
            self.assertEqual(application.Transaction.query.filter_by(sync_id=sale_payload['sales'][0]['id']).count(), 1)

    def test_sale_with_insufficient_stock_does_not_change_stock_or_write_sale(self):
        with self.app.app_context():
            product = application.Product(
                name='Limited stock item',
                sku='LIMITED-001',
                cost_price=2000,
                selling_price=3000,
                quantity=1,
                low_stock_at=0,
                business_id=application.Business.query.first().id,
            )
            self.db.session.add(product)
            self.db.session.commit()
            product_id = product.id
        self.login()
        response = self.post_form('/sales', {
            'product_id': str(product_id),
            'quantity': '2',
            'payment_method': 'Cash',
        })
        self.assertEqual(response.status_code, 200)
        with self.app.app_context():
            self.assertEqual(self.db.session.get(application.Product, product_id).quantity, 1)
            self.assertEqual(application.Transaction.query.filter_by(kind='sale').count(), 0)
            self.assertEqual(application.StockMovement.query.filter_by(product_id=product_id).count(), 0)

    def test_login_csrf_protection_and_throttling_remain_enabled(self):
        self.client.get('/login')
        old_token = self.csrf_token()
        response = self.post_form('/login', {
            'username': 'duka-admin',
            'password': 'test-password-123',
        })
        self.assertEqual(response.status_code, 302)
        self.assertNotEqual(old_token, self.csrf_token())
        self.assertEqual(self.client.get('/logout').status_code, 405)
        self.assertEqual(self.client.post('/logout').status_code, 400)
        self.assertEqual(self.client.post('/api/sales/sync', json={'sales': []}).status_code, 400)

    def test_production_requests_redirect_to_https(self):
        with patch.object(application, 'production', True):
            response = self.client.get('/login', base_url='http://dukas.example.test')
        self.assertEqual(response.status_code, 308)
        self.assertEqual(response.headers['Location'], 'https://dukas.example.test/login')

    def test_initial_administrator_requires_twelve_character_password(self):
        with self.app.app_context():
            application.User.query.filter_by(username='duka-admin').delete()
            self.db.session.commit()
        short_password_response = self.post_form('/setup', {
            'business_name': 'Test Shop',
            'username': 'short-password-admin',
            'email': 'short-password@example.test',
            'password': 'too-short',
        })
        self.assertEqual(short_password_response.status_code, 200)
        with self.app.app_context():
            self.assertIsNone(application.User.query.filter_by(username='short-password-admin').first())
        response = self.post_form('/setup', {
            'business_name': 'Test Shop',
            'username': 'long-password-admin',
            'email': 'long-password@example.test',
            'password': 'Long-password-2026',
        })
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.location.endswith('/login'))
        with self.app.app_context():
            user = application.User.query.filter_by(username='long-password-admin').one()
            self.assertTrue(application.check_password_hash(user.password_hash, 'Long-password-2026'))

    def test_existing_short_password_is_forced_to_change_after_signin(self):
        with self.app.app_context():
            business_id = application.Business.query.first().id
            user = application.User(
                username='legacy-admin',
                password_hash=application.generate_password_hash('weak'),
                business_id=business_id,
                email='legacy-admin@example.test',
                role='administrator',
            )
            self.db.session.add(user)
            self.db.session.commit()
        client = self.app.test_client()
        login = self.post_form('/login', {
            'username': 'legacy-admin',
            'password': 'weak',
        }, client=client)
        self.assertEqual(login.status_code, 302)
        self.assertTrue(client.get('/').location.endswith('/account/password'))
        changed = self.post_form('/account/password', {
            'current_password': 'weak',
            'password': 'New-unique-password-2026',
            'confirm_password': 'New-unique-password-2026',
        }, client=client)
        self.assertEqual(changed.status_code, 302)
        with self.app.app_context():
            user = application.User.query.filter_by(username='legacy-admin').one()
            self.assertFalse(user.must_change_password)
            self.assertTrue(application.check_password_hash(
                user.password_hash, 'New-unique-password-2026',
            ))

    def test_external_api_credentials_are_hashed_scoped_and_revocable(self):
        self.login()
        admin_page = self.client.get('/admin/api-keys')
        self.assertEqual(admin_page.status_code, 200)
        self.assertIn('Create an integration key', admin_page.get_data(as_text=True))
        token = self.create_api_key()
        with self.app.app_context():
            key = application.APIKey.query.one()
            key_id = key.id
            self.assertNotEqual(key.key_hash, token)
            self.assertEqual(key.key_hash, application.sha256(token.encode()).hexdigest())
            self.assertEqual(key.business_id, application.User.query.filter_by(
                username='duka-admin',
            ).one().business_id)

        headers = {'Authorization': f'Bearer {token}'}
        products = self.client.get('/api/v1/products?per_page=10', headers=headers)
        self.assertEqual(products.status_code, 200)
        self.assertEqual(products.get_json()['data'], [])
        self.assertEqual(products.get_json()['pagination']['per_page'], 10)
        self.assertEqual(self.client.get('/api/v1/sales', headers=headers).status_code, 200)

        anonymous = self.app.test_client()
        unauthorized = anonymous.get('/api/v1/products')
        self.assertEqual(unauthorized.status_code, 401)
        self.assertEqual(unauthorized.get_json()['error']['code'], 'authentication_required')

        revoked = self.post_form('/admin/api-keys', {
            'action': 'revoke',
            'key_id': str(key_id),
        })
        self.assertEqual(revoked.status_code, 302)
        self.assertEqual(self.client.get('/api/v1/products', headers=headers).status_code, 401)
        page = self.client.get('/admin/api-keys').get_data(as_text=True)
        self.assertIn('Revoked', page)
        self.assertNotIn(token, page)

    def test_external_api_enforces_scopes_tenant_isolation_and_pagination(self):
        with self.app.app_context():
            business_id = application.User.query.filter_by(username='duka-admin').one().business_id
            self.db.session.add_all([
                application.Product(
                    name='Tea', sku='TEA-001', cost_price=100, selling_price=200,
                    quantity=10, low_stock_at=2, business_id=business_id,
                ),
                application.Product(
                    name='Rice', sku='RICE-001', cost_price=500, selling_price=700,
                    quantity=8, low_stock_at=2, business_id=business_id,
                ),
            ])
            other_business = application.Business(name='External API Other Shop')
            self.db.session.add(other_business)
            self.db.session.flush()
            self.db.session.add(application.Product(
                name='Private item', sku='PRIVATE-001', cost_price=1, selling_price=2,
                quantity=1, low_stock_at=0, business_id=other_business.id,
            ))
            self.db.session.commit()

        self.login()
        token = self.create_api_key(scopes=('products:read',))
        headers = {'Authorization': f'Bearer {token}'}
        first_page = self.client.get('/api/v1/products?page=1&per_page=1', headers=headers)
        self.assertEqual(first_page.status_code, 200)
        self.assertEqual(first_page.get_json()['pagination']['total'], 2)
        self.assertTrue(first_page.get_json()['pagination']['has_next'])
        self.assertIn(first_page.get_json()['data'][0]['sku'], ('TEA-001', 'RICE-001'))
        self.assertNotIn('Private item', first_page.get_data(as_text=True))
        denied = self.client.get('/api/v1/sales', headers=headers)
        self.assertEqual(denied.status_code, 403)
        self.assertEqual(denied.get_json()['error']['code'], 'insufficient_scope')
        invalid_page = self.client.get('/api/v1/products?per_page=101', headers=headers)
        self.assertEqual(invalid_page.status_code, 400)
        self.assertEqual(invalid_page.get_json()['error']['code'], 'invalid_pagination')

    def test_external_expense_credit_and_reports_endpoints_are_scoped(self):
        with self.app.app_context():
            business_id = application.User.query.filter_by(username='duka-admin').one().business_id
            product = application.Product(
                name='Flour',
                sku='FLOUR-001',
                cost_price=300,
                selling_price=500,
                quantity=4,
                low_stock_at=1,
                business_id=business_id,
            )
            self.db.session.add(product)
            self.db.session.flush()
            self.db.session.add_all([
                application.Transaction(
                    kind='sale',
                    description='Flour x 2',
                    amount=1000,
                    quantity=2,
                    product_id=product.id,
                    business_id=business_id,
                    payment_method='Cash',
                ),
                application.Transaction(
                    kind='expense',
                    description='Shop rent',
                    amount=200,
                    business_id=business_id,
                    payment_method='Cash',
                ),
                application.CustomerDebt(
                    customer_name='Customer One',
                    phone='0700000000',
                    description='Flour on credit',
                    amount=800,
                    amount_paid=300,
                    business_id=business_id,
                ),
            ])
            other_business = application.Business(name='Accounting API Other Shop')
            self.db.session.add(other_business)
            self.db.session.flush()
            self.db.session.add(application.Transaction(
                kind='expense',
                description='Private rent',
                amount=9999,
                business_id=other_business.id,
            ))
            self.db.session.add(application.CustomerDebt(
                customer_name='Private customer',
                description='Private credit',
                amount=5000,
                amount_paid=0,
                business_id=other_business.id,
            ))
            self.db.session.commit()

        self.login()
        token = self.create_api_key((
            'expenses:read',
            'credit:read',
            'reports:read',
        ))
        headers = {'Authorization': f'Bearer {token}'}

        expenses = self.client.get('/api/v1/expenses', headers=headers)
        self.assertEqual(expenses.status_code, 200)
        self.assertEqual(expenses.get_json()['data'][0]['description'], 'Shop rent')
        self.assertNotIn('Private rent', expenses.get_data(as_text=True))

        credit = self.client.get('/api/v1/credit', headers=headers)
        self.assertEqual(credit.status_code, 200)
        credit_data = credit.get_json()['data'][0]
        self.assertEqual(credit_data['outstanding'], 500)
        self.assertNotIn('phone', credit_data)
        self.assertNotIn('Private customer', credit.get_data(as_text=True))

        reports = self.client.get('/api/v1/reports', headers=headers)
        self.assertEqual(reports.status_code, 200)
        report_data = reports.get_json()['data']
        self.assertEqual(report_data['sales_total'], 1000)
        self.assertEqual(report_data['expenses_total'], 200)
        self.assertEqual(report_data['cost_of_goods_sold'], 600)
        self.assertEqual(report_data['estimated_profit'], 200)
        self.assertEqual(report_data['stock_value_at_cost'], 1200)

        missing_scope = self.client.get(
            '/api/v1/reports',
            headers={'Authorization': f'Bearer {self.create_api_key(("products:read",))}'},
        )
        self.assertEqual(missing_scope.status_code, 403)

    def test_inventory_and_sales_pages_include_product_search_controls(self):
        with self.app.app_context():
            business_id = application.User.query.filter_by(username='duka-admin').one().business_id
            self.db.session.add(application.Product(
                name='Cooking oil',
                sku='OIL-001',
                cost_price=1000,
                selling_price=1500,
                quantity=4,
                low_stock_at=1,
                business_id=business_id,
            ))
            self.db.session.commit()
        self.login()
        inventory = self.client.get('/products').get_data(as_text=True)
        self.assertIn('id="inventory-search"', inventory)
        self.assertIn('id="inventory-stock-filter"', inventory)
        self.assertIn('data-product-sku="oil-001"', inventory)
        sales = self.client.get('/sales').get_data(as_text=True)
        self.assertIn('id="product-search"', sales)
        self.assertIn('name="product_id"', sales)

    def test_external_api_rate_limit_and_expiry(self):
        self.login()
        token = self.create_api_key(scopes=('products:read',))
        headers = {'Authorization': f'Bearer {token}'}
        with self.app.app_context():
            key = application.APIKey.query.one()
            key.rate_window_started_at = application.datetime.utcnow()
            key.rate_request_count = application.API_KEY_RATE_LIMIT
            self.db.session.commit()
        limited = self.client.get('/api/v1/products', headers=headers)
        self.assertEqual(limited.status_code, 429)
        self.assertEqual(limited.get_json()['error']['code'], 'rate_limit_exceeded')
        self.assertGreaterEqual(int(limited.headers['Retry-After']), 1)

        with self.app.app_context():
            key = application.APIKey.query.one()
            key.expires_at = application.datetime.utcnow() - application.timedelta(seconds=1)
            key.rate_window_started_at = None
            key.rate_request_count = 0
            self.db.session.commit()
        expired = self.client.get('/api/v1/products', headers=headers)
        self.assertEqual(expired.status_code, 401)
        self.assertEqual(expired.get_json()['error']['code'], 'invalid_api_key')

    def test_historical_checkout_transactions_remain_reportable(self):
        with self.app.app_context():
            user = application.User.query.one()
            product = application.Product(
                name='Historic Lager',
                sku='HIST-001',
                category='drink',
                cost_price=1000,
                selling_price=2500,
                quantity=4,
                low_stock_at=1,
                base_unit='bottle',
                purchase_unit='crate',
                units_per_purchase_unit=24,
                business_id=user.business_id,
            )
            sale = application.CheckoutSale(
                idempotency_key='00000000-0000-0000-0000-000000000001',
                user_id=user.id,
                business_id=user.business_id,
                subtotal=5000,
                discount_total=0,
                total=5000,
            )
            self.db.session.add_all([product, sale])
            self.db.session.flush()
            self.db.session.add(application.CheckoutLine(
                sale_id=sale.id,
                product_id=product.id,
                product_name='Historic Lager',
                quantity=2,
                unit_price=2500,
                unit_cost=1000,
                discount_amount=0,
                line_total=5000,
            ))
            self.db.session.add(application.Transaction(
                kind='sale',
                description='Historic Lager checkout',
                amount=5000,
                quantity=2,
                product_id=product.id,
                business_id=user.business_id,
                created_by_id=user.id,
                sale_id=sale.id,
                payment_method='Cash',
            ))
            self.db.session.commit()
            sale_id = sale.id
        self.login()
        self.assertEqual(self.client.get('/reports').status_code, 200)
        with self.app.app_context():
            self.assertIsNotNone(self.db.session.get(application.CheckoutSale, sale_id))
            self.assertEqual(application.CheckoutLine.query.count(), 1)
            self.assertEqual(application.Transaction.query.filter_by(sale_id=sale_id).count(), 1)

    def create_user(self, username, role, password='employee-temp-123'):
        with self.app.app_context():
            business_id = application.User.query.filter_by(username='duka-admin').one().business_id
            user = application.User(
                username=username,
                email=f'{username}@example.test',
                password_hash=application.generate_password_hash(password),
                business_id=business_id,
                role=role,
            )
            self.db.session.add(user)
            self.db.session.commit()
            return user.id

    def test_server_side_roles_allow_only_authorized_pages(self):
        self.create_user('shop-cashier', 'cashier')
        cashier = self.app.test_client()
        cashier_login = self.login_as(cashier, 'shop-cashier', 'employee-temp-123')
        self.assertTrue(cashier_login.location.endswith('/sales'))
        self.assertEqual(cashier.get('/sales').status_code, 200)
        self.assertEqual(cashier.get('/expenses').status_code, 403)
        self.assertEqual(cashier.get('/reports').status_code, 403)
        self.assertEqual(cashier.get('/products').status_code, 403)
        self.assertEqual(cashier.get('/admin/users').status_code, 403)

        self.create_user('shop-stock', 'stock_clerk')
        stock_clerk = self.app.test_client()
        stock_login = self.login_as(stock_clerk, 'shop-stock', 'employee-temp-123')
        self.assertTrue(stock_login.location.endswith('/products'))
        self.assertEqual(stock_clerk.get('/products').status_code, 200)
        self.assertEqual(stock_clerk.get('/sales').status_code, 403)

    def test_navigation_has_accessible_active_state_and_role_scoped_items(self):
        self.login()
        dashboard = self.client.get('/').get_data(as_text=True)
        self.assertIn('aria-label="Main navigation"', dashboard)
        self.assertIn('href="/" class="nav-link active" aria-current="page"', dashboard)
        self.assertIn('<span>Dashboard</span>', dashboard)
        self.assertIn('viewBox="0 0 24 24"', dashboard)

        self.create_user('nav-cashier', 'cashier')
        cashier = self.app.test_client()
        self.login_as(cashier, 'nav-cashier', 'employee-temp-123')
        sales_page = cashier.get('/sales').get_data(as_text=True)
        self.assertIn('aria-current="page"', sales_page)
        self.assertIn('<span>Sales</span>', sales_page)
        self.assertNotIn('<span>Reports</span>', sales_page)
        self.assertNotIn('<span>Business settings</span>', sales_page)

    def test_admin_can_create_and_disable_employee_and_sessions_are_revoked(self):
        self.login()
        response = self.post_form('/admin/users', {
            'action': 'create',
            'username': 'new-cashier',
            'email': 'cashier@example.test',
            'password': 'temporary-password-123',
            'role': 'cashier',
        })
        self.assertEqual(response.status_code, 302)
        employee = self.app.test_client()
        self.login_as(employee, 'new-cashier', 'temporary-password-123')
        forced = employee.get('/sales')
        self.assertEqual(forced.status_code, 302)
        self.assertIn('/account/password', forced.location)
        changed = self.post_form('/account/password', {
            'current_password': 'temporary-password-123',
            'password': 'employee-new-password-456',
            'confirm_password': 'employee-new-password-456',
        }, client=employee)
        self.assertEqual(changed.status_code, 302)
        self.assertEqual(employee.get('/expenses').status_code, 403)

        with self.app.app_context():
            employee_row = application.User.query.filter_by(username='new-cashier').one()
            self.assertFalse(employee_row.must_change_password)
        disabled = self.post_form('/admin/users', {
            'action': 'disable',
            'user_id': employee_row.id,
        })
        self.assertEqual(disabled.status_code, 302)
        self.assertEqual(employee.get('/sales').status_code, 302)
        with self.app.app_context():
            employee_row = application.User.query.filter_by(username='new-cashier').one()
            self.assertFalse(employee_row.is_active)
            self.assertEqual(
                application.UserSession.query.filter_by(
                    user_id=employee_row.id,
                    ended_reason='account_disabled',
                ).count(),
                1,
            )

    def test_tenant_scoping_hides_other_business_products_and_transactions(self):
        with self.app.app_context():
            other_business = application.Business(name='Other Shop')
            self.db.session.add(other_business)
            self.db.session.flush()
            other_user = application.User(
                username='other-admin',
                email='other@example.test',
                password_hash=application.generate_password_hash('other-password-123'),
                business_id=other_business.id,
                role='administrator',
            )
            product = application.Product(
                name='Other Shop Item',
                sku='OTHER-001',
                cost_price=500,
                selling_price=1000,
                quantity=12,
                low_stock_at=2,
                business_id=other_business.id,
            )
            self.db.session.add_all([other_user, product])
            self.db.session.add(application.Transaction(
                kind='expense',
                description='Other shop rent',
                amount=9000,
                business_id=other_business.id,
            ))
            self.db.session.commit()
        self.login()
        products = self.client.get('/api/products').get_json()
        self.assertFalse(any(item['sku'] == 'OTHER-001' for item in products))
        report = self.client.get('/reports').get_data(as_text=True)
        self.assertNotIn('Other shop rent', report)
        self.assertNotIn('Other Shop Item', self.client.get('/products').get_data(as_text=True))
        self.assertNotIn('other-admin', self.client.get('/admin/users').get_data(as_text=True))
        self.assertNotIn('Other Shop Item', self.client.get('/admin/audit').get_data(as_text=True))

    def test_cli_can_provision_a_separate_business_and_administrator(self):
        result = self.app.test_cli_runner().invoke(
            args=['create-business'],
            input='Second Shop\nsecond-admin\nsecond@example.test\nsecond-password-123\nsecond-password-123\n',
        )
        self.assertEqual(result.exit_code, 0, result.output)
        with self.app.app_context():
            business = application.Business.query.filter_by(name='Second Shop').one()
            admin = application.User.query.filter_by(username='second-admin').one()
            self.assertEqual(admin.business_id, business.id)
            self.assertEqual(admin.role, 'administrator')

    def test_login_history_tracks_failed_successful_signout_and_last_signin(self):
        self.client.get('/login')
        failed = self.post_form('/login', {
            'username': 'duka-admin',
            'password': 'wrong-password',
        })
        self.assertEqual(failed.status_code, 200)
        self.login()
        self.assertEqual(self.client.get('/admin/users').status_code, 200)
        self.post_form('/logout')
        with self.app.app_context():
            event_types = [
                event.event_type
                for event in application.LoginEvent.query.order_by(application.LoginEvent.id).all()
            ]
            self.assertIn('failed_sign_in', event_types)
            self.assertIn('sign_in', event_types)
            self.assertIn('sign_out', event_types)
            self.assertIsNotNone(application.User.query.filter_by(username='duka-admin').one().last_login_at)

    def test_email_password_reset_is_one_time_and_revokes_existing_sessions(self):
        self.login()
        with patch.object(application, 'send_password_reset_email') as send_email:
            response = self.post_form('/forgot-password', {
                'username': 'duka-admin',
                'email': 'admin@example.test',
            })
        self.assertEqual(response.status_code, 302)
        self.assertTrue(send_email.called)
        token = send_email.call_args.args[1]
        short_password_response = self.post_form(f'/reset-password/{token}', {
            'password': 'too-short',
            'confirm_password': 'too-short',
        })
        self.assertEqual(short_password_response.status_code, 200)
        reset_response = self.post_form(f'/reset-password/{token}', {
            'password': 'reset-password-789012',
            'confirm_password': 'reset-password-789012',
        })
        self.assertEqual(reset_response.status_code, 302)
        self.assertEqual(self.client.get(f'/reset-password/{token}').status_code, 400)
        self.assertEqual(self.client.get('/').status_code, 302)
        with self.app.app_context():
            user = application.User.query.filter_by(username='duka-admin').one()
            self.assertTrue(application.check_password_hash(user.password_hash, 'reset-password-789012'))
            self.assertEqual(
                application.UserSession.query.filter_by(user_id=user.id, ended_reason='password_reset').count(),
                1,
            )

    def test_sales_and_expenses_write_audit_events(self):
        self.login()
        self.post_form('/products', {
            'name': 'Sugar',
            'sku': 'SUG-001',
            'quantity': '4',
            'cost_price': '2000',
            'selling_price': '3000',
            'low_stock_at': '1',
        })
        with self.app.app_context():
            product_id = application.Product.query.filter_by(sku='SUG-001').one().id
        self.post_form('/sales', {
            'product_id': str(product_id),
            'quantity': '1',
            'payment_method': 'Cash',
        })
        self.post_form('/expenses', {
            'description': 'Shop utility',
            'amount': '5000',
            'payment_method': 'Cash',
        })
        with self.app.app_context():
            actions = {event.action for event in application.ActivityLog.query.all()}
            self.assertIn('product_created', actions)
            self.assertIn('stock_adjusted', actions)
            self.assertIn('sale_recorded', actions)
            self.assertIn('expense_recorded', actions)

    def test_customer_credit_and_payments_remain_business_scoped_and_audited(self):
        self.login()
        created = self.post_form('/debts', {
            'customer_name': 'Test Customer',
            'phone': '0700000000',
            'description': 'Groceries',
            'amount': '20000',
        })
        self.assertEqual(created.status_code, 302)
        with self.app.app_context():
            debt = application.CustomerDebt.query.filter_by(
                customer_name='Test Customer',
            ).one()
            debt_id = debt.id
        payment = self.post_form(f'/debts/{debt_id}/payment', {'amount': '5000'})
        self.assertEqual(payment.status_code, 302)
        page = self.client.get('/debts').get_data(as_text=True)
        self.assertIn('UGX 15,000', page)
        with self.app.app_context():
            debt = self.db.session.get(application.CustomerDebt, debt_id)
            self.assertEqual(debt.amount_paid, 5000)
            actions = {
                event.action
                for event in application.ActivityLog.query.filter_by(
                    entity_type='customer_debt',
                    entity_id=str(debt_id),
                ).all()
            }
            self.assertEqual(actions, {'customer_credit_recorded', 'customer_credit_payment'})

    def test_session_expiry_is_enforced_and_recorded(self):
        self.login()
        with self.client.session_transaction() as browser_session:
            session_id = browser_session['session_id']
        with self.app.app_context():
            user_session = self.db.session.get(application.UserSession, session_id)
            user_session.expires_at = application.datetime.utcnow() - application.timedelta(seconds=1)
            self.db.session.commit()
        response = self.client.get('/sales')
        self.assertEqual(response.status_code, 302)
        with self.app.app_context():
            user_session = self.db.session.get(application.UserSession, session_id)
            self.assertEqual(user_session.ended_reason, 'session_expired')
            self.assertEqual(
                application.LoginEvent.query.filter_by(event_type='session_expired').count(),
                1,
            )

    def test_admin_can_revoke_another_session_and_change_business_settings(self):
        self.create_user('other-manager', 'manager')
        manager_client = self.app.test_client()
        self.login_as(manager_client, 'other-manager', 'employee-temp-123')
        with manager_client.session_transaction() as browser_session:
            session_id = browser_session['session_id']
        self.login()
        revoke = self.post_form(
            f'/admin/sessions/{session_id}/revoke',
            {},
        )
        self.assertEqual(revoke.status_code, 302)
        self.assertEqual(manager_client.get('/sales').status_code, 302)
        change = self.post_form('/admin/business', {'name': 'Renamed Shop'})
        self.assertEqual(change.status_code, 302)
        with self.app.app_context():
            self.assertEqual(application.Business.query.one().name, 'Renamed Shop')
            self.assertTrue(application.ActivityLog.query.filter_by(
                action='business_settings_changed',
            ).one())
            self.assertEqual(application.LoginEvent.query.filter_by(event_type='revoked').count(), 1)

    def test_failed_signins_are_throttled_and_logged(self):
        self.client.get('/login')
        for _ in range(5):
            response = self.post_form('/login', {
                'username': 'duka-admin',
                'password': 'incorrect-password',
            })
            self.assertEqual(response.status_code, 200)
        limited = self.post_form('/login', {
            'username': 'duka-admin',
            'password': 'incorrect-password',
        })
        self.assertEqual(limited.status_code, 429)
        with self.app.app_context():
            self.assertEqual(
                application.LoginEvent.query.filter_by(
                    username='duka-admin',
                    event_type='failed_sign_in',
                ).count(),
                5,
            )
            self.assertEqual(
                application.LoginEvent.query.filter_by(event_type='blocked').count(),
                1,
            )


if __name__ == '__main__':
    unittest.main()
