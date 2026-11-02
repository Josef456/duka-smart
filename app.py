import os
import math
import uuid
import hmac
import secrets
import smtplib
import click
from email.message import EmailMessage
from hashlib import sha256
from datetime import datetime, date, timedelta, timezone
from functools import wraps
from flask import Flask, abort, current_app, g, render_template, request, redirect, url_for, flash, session, jsonify, send_from_directory
from flask_migrate import Migrate
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from werkzeug.security import generate_password_hash, check_password_hash

MIN_PASSWORD_LENGTH = 4

app = Flask(__name__)
production = (
    bool(os.environ.get('RENDER'))
    or os.environ.get('APP_ENV', '').lower() == 'production'
    or os.environ.get('FLASK_ENV', '').lower() == 'production'
)
secret_key = os.environ.get('SESSION_SECRET')
if production and (not secret_key or len(secret_key) < 32):
    raise RuntimeError('Set SESSION_SECRET to a random value of at least 32 characters.')
if not secret_key:
    secret_key = secrets.token_hex(32)
    app.logger.warning('SESSION_SECRET is not set; a temporary development key was generated.')
app.config.update(
    SECRET_KEY=secret_key,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SECURE=production or os.environ.get('SESSION_COOKIE_SECURE') == 'true',
    SESSION_COOKIE_SAMESITE='Lax',
    PERMANENT_SESSION_LIFETIME=timedelta(hours=8),
    SESSION_REFRESH_EACH_REQUEST=False,
    MAX_CONTENT_LENGTH=1024 * 1024,
)
db_url = os.environ.get('DATABASE_URL', 'sqlite:///dukas_mart.db')
if db_url.startswith('postgres://'):
    db_url = db_url.replace('postgres://', 'postgresql://', 1)
app.config['SQLALCHEMY_DATABASE_URI'] = db_url
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)
migrate = Migrate(app, db)
if production:
    from werkzeug.middleware.proxy_fix import ProxyFix
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1)

class Business(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    business_id = db.Column(db.Integer, db.ForeignKey('business.id'), nullable=False, index=True)
    role = db.Column(db.String(20), nullable=False, default='administrator')
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    email = db.Column(db.String(254), nullable=True)
    must_change_password = db.Column(db.Boolean, nullable=False, default=False)
    last_login_at = db.Column(db.DateTime, nullable=True)
    business = db.relationship('Business')

class LoginAttempt(db.Model):
    ip_hash = db.Column(db.String(64), primary_key=True)
    failed_attempts = db.Column(db.Integer, nullable=False, default=0)
    window_started_at = db.Column(db.DateTime, nullable=False)
    locked_until = db.Column(db.DateTime, nullable=True)

class LoginEvent(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True, index=True)
    business_id = db.Column(db.Integer, db.ForeignKey('business.id'), nullable=True, index=True)
    username = db.Column(db.String(80), nullable=False)
    event_type = db.Column(db.String(30), nullable=False)
    ip_hash = db.Column(db.String(64), nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, index=True)
    user = db.relationship('User')

class UserSession(db.Model):
    id = db.Column(db.String(36), primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False, index=True)
    business_id = db.Column(db.Integer, db.ForeignKey('business.id'), nullable=False, index=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    last_seen_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    expires_at = db.Column(db.DateTime, nullable=False, index=True)
    ended_at = db.Column(db.DateTime, nullable=True)
    ended_reason = db.Column(db.String(30), nullable=True)
    ip_hash = db.Column(db.String(64), nullable=True)
    user = db.relationship('User')

class PasswordReset(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False, index=True)
    token_hash = db.Column(db.String(64), nullable=False, unique=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    expires_at = db.Column(db.DateTime, nullable=False)
    used_at = db.Column(db.DateTime, nullable=True)
    user = db.relationship('User')

class ActivityLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    business_id = db.Column(db.Integer, db.ForeignKey('business.id'), nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True, index=True)
    action = db.Column(db.String(60), nullable=False)
    entity_type = db.Column(db.String(40), nullable=False)
    entity_id = db.Column(db.String(80), nullable=True)
    summary = db.Column(db.String(240), nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, index=True)
    user = db.relationship('User')

class APIKey(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(80), nullable=False)
    key_hash = db.Column(db.String(64), nullable=False, unique=True)
    key_prefix = db.Column(db.String(24), nullable=False)
    scopes = db.Column(db.String(160), nullable=False)
    business_id = db.Column(db.Integer, db.ForeignKey('business.id'), nullable=False, index=True)
    created_by_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    last_used_at = db.Column(db.DateTime, nullable=True)
    expires_at = db.Column(db.DateTime, nullable=True)
    revoked_at = db.Column(db.DateTime, nullable=True)
    rate_window_started_at = db.Column(db.DateTime, nullable=True)
    rate_request_count = db.Column(db.Integer, nullable=False, default=0)

class Product(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(160), nullable=False)
    sku = db.Column(db.String(80), unique=True, nullable=False)
    category = db.Column(db.String(30), nullable=False, default='general', index=True)
    cost_price = db.Column(db.Float, nullable=False, default=0)
    selling_price = db.Column(db.Float, nullable=False, default=0)
    quantity = db.Column(db.Integer, nullable=False, default=0)
    low_stock_at = db.Column(db.Integer, nullable=False, default=5)
    base_unit = db.Column(db.String(30), nullable=False, default='unit')
    purchase_unit = db.Column(db.String(30), nullable=False, default='unit')
    units_per_purchase_unit = db.Column(db.Integer, nullable=False, default=1)
    business_id = db.Column(db.Integer, db.ForeignKey('business.id'), nullable=False, index=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class Transaction(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    kind = db.Column(db.String(20), nullable=False)
    description = db.Column(db.String(240), nullable=False)
    amount = db.Column(db.Float, nullable=False)
    quantity = db.Column(db.Integer, nullable=True)
    product_id = db.Column(db.Integer, db.ForeignKey('product.id'), nullable=True)
    business_id = db.Column(db.Integer, db.ForeignKey('business.id'), nullable=False, index=True)
    created_by_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    sale_id = db.Column(db.Integer, db.ForeignKey('checkout_sale.id'), nullable=True, index=True)
    payment_method = db.Column(db.String(30), nullable=True)
    sync_id = db.Column(db.String(36), unique=True, index=True, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    product = db.relationship('Product')

class CheckoutSale(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    idempotency_key = db.Column(db.String(36), unique=True, nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    business_id = db.Column(db.Integer, db.ForeignKey('business.id'), nullable=False, index=True)
    subtotal = db.Column(db.Float, nullable=False)
    discount_total = db.Column(db.Float, nullable=False)
    total = db.Column(db.Float, nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    user = db.relationship('User')
    lines = db.relationship('CheckoutLine', back_populates='sale', cascade='all, delete-orphan')
    payments = db.relationship('SalePayment', back_populates='sale', cascade='all, delete-orphan')

class CheckoutLine(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    sale_id = db.Column(db.Integer, db.ForeignKey('checkout_sale.id'), nullable=False, index=True)
    product_id = db.Column(db.Integer, db.ForeignKey('product.id'), nullable=False)
    product_name = db.Column(db.String(160), nullable=False)
    quantity = db.Column(db.Integer, nullable=False)
    unit_price = db.Column(db.Float, nullable=False)
    unit_cost = db.Column(db.Float, nullable=False)
    discount_amount = db.Column(db.Float, nullable=False, default=0)
    line_total = db.Column(db.Float, nullable=False)
    sale = db.relationship('CheckoutSale', back_populates='lines')
    product = db.relationship('Product')

class SalePayment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    sale_id = db.Column(db.Integer, db.ForeignKey('checkout_sale.id'), nullable=False, index=True)
    method = db.Column(db.String(30), nullable=False)
    amount = db.Column(db.Float, nullable=False)
    sale = db.relationship('CheckoutSale', back_populates='payments')

class StockMovement(db.Model):
    __table_args__ = (
        db.Index('ix_stock_movement_product_created_at', 'product_id', 'created_at'),
        db.Index('ix_stock_movement_created_at', 'created_at'),
    )

    id = db.Column(db.Integer, primary_key=True)
    product_id = db.Column(db.Integer, db.ForeignKey('product.id'), nullable=False)
    business_id = db.Column(db.Integer, db.ForeignKey('business.id'), nullable=False, index=True)
    transaction_id = db.Column(db.Integer, db.ForeignKey('transaction.id'), nullable=True)
    movement_type = db.Column(db.String(20), nullable=False)
    quantity_delta = db.Column(db.Integer, nullable=False)
    unit_cost = db.Column(db.Float, nullable=True)
    reason = db.Column(db.String(240), nullable=True)
    created_by_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    product = db.relationship('Product', backref='stock_movements')
    created_by = db.relationship('User', foreign_keys=[created_by_id])

class CustomerDebt(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    customer_name = db.Column(db.String(160), nullable=False)
    phone = db.Column(db.String(40), nullable=True)
    description = db.Column(db.String(240), nullable=False)
    amount = db.Column(db.Float, nullable=False)
    amount_paid = db.Column(db.Float, nullable=False, default=0)
    business_id = db.Column(db.Integer, db.ForeignKey('business.id'), nullable=False, index=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

def login_required(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        if 'user_id' not in session:
            if request.path.startswith('/api/'):
                return jsonify({'error': 'Sign in to synchronize sales.'}), 401
            return redirect(url_for('login'))
        user = db.session.get(User, session['user_id'])
        user_session = db.session.get(UserSession, session.get('session_id'))
        now = datetime.utcnow()
        if (not user or not user_session or user_session.user_id != user.id
                or user_session.ended_at is not None or user_session.expires_at <= now):
            if user_session and user_session.ended_at is None:
                end_user_session(user_session, 'session_expired')
                db.session.commit()
            session.clear()
            if request.path.startswith('/api/'):
                return jsonify({'error': 'Your session has expired. Sign in again.'}), 401
            flash('Your session has expired. Please sign in again.', 'error')
            return redirect(url_for('login'))
        if not user.is_active:
            end_user_session(user_session, 'account_disabled')
            db.session.commit()
            session.clear()
            if request.path.startswith('/api/'):
                return jsonify({'error': 'This account is disabled.'}), 403
            flash('This account is disabled. Contact your administrator.', 'error')
            return redirect(url_for('login'))
        user_session.last_seen_at = now
        g.current_user = user
        g.business_id = user.business_id
        db.session.commit()
        if user.must_change_password and request.endpoint not in ('change_password', 'logout'):
            return redirect(url_for('change_password'))
        return fn(*args, **kwargs)
    return wrapped

def roles_required(*allowed_roles):
    def decorator(fn):
        @wraps(fn)
        @login_required
        def wrapped(*args, **kwargs):
            if g.current_user.role not in allowed_roles:
                abort(403)
            return fn(*args, **kwargs)
        return wrapped
    return decorator

API_KEY_SCOPES = {
    'products:read': 'Read product and stock information',
    'sales:read': 'Read recorded sales',
    'expenses:read': 'Read expense records',
    'credit:read': 'Read customer credit balances',
    'reports:read': 'Read business report summaries',
}
API_KEY_RATE_LIMIT = 120
API_KEY_RATE_WINDOW = timedelta(minutes=1)

def api_error(code, message, status):
    response = jsonify({'error': {'code': code, 'message': message}})
    if status == 401:
        response.headers['WWW-Authenticate'] = 'Bearer'
    return response, status

def api_key_required(scope):
    def decorator(fn):
        @wraps(fn)
        def wrapped(*args, **kwargs):
            scheme, separator, token = request.headers.get('Authorization', '').partition(' ')
            if scheme.lower() != 'bearer' or not separator or not token:
                return api_error('authentication_required', 'Provide an API key using Bearer authentication.', 401)

            key = db.session.query(APIKey).filter_by(
                key_hash=sha256(token.encode()).hexdigest(),
            ).with_for_update().first()
            now = datetime.utcnow()
            if (not key or key.revoked_at is not None
                    or (key.expires_at is not None and key.expires_at <= now)):
                return api_error('invalid_api_key', 'The API key is invalid, expired, or revoked.', 401)
            if scope not in key.scopes.split(','):
                return api_error('insufficient_scope', f'This API key does not have the {scope} permission.', 403)

            if (key.rate_window_started_at is None
                    or key.rate_window_started_at <= now - API_KEY_RATE_WINDOW):
                key.rate_window_started_at = now
                key.rate_request_count = 0
            if key.rate_request_count >= API_KEY_RATE_LIMIT:
                retry_after = (
                    key.rate_window_started_at + API_KEY_RATE_WINDOW - now
                ).total_seconds()
                db.session.commit()
                response, status = api_error('rate_limit_exceeded', 'Too many API requests. Retry shortly.', 429)
                response.headers['Retry-After'] = str(max(1, math.ceil(retry_after)))
                return response, status

            key.rate_request_count += 1
            key.last_used_at = now
            g.api_key = key
            g.business_id = key.business_id
            db.session.commit()
            return fn(*args, **kwargs)
        return wrapped
    return decorator

def role_home_endpoint(role):
    if role in ('administrator', 'manager'):
        return 'dashboard'
    if role == 'cashier':
        return 'sales'
    return 'products'

@app.before_request
def protect_state_changing_requests():
    session.setdefault('_csrf_token', secrets.token_urlsafe(32))
    if request.method not in ('POST', 'PUT', 'PATCH', 'DELETE'):
        return None
    if request.path.startswith('/api/') and 'user_id' not in session:
        return None
    submitted_token = request.headers.get('X-CSRF-Token') or request.form.get('csrf_token', '')
    expected_token = session.get('_csrf_token', '')
    if not submitted_token or not expected_token or not hmac.compare_digest(submitted_token, expected_token):
        if request.path.startswith('/api/'):
            return jsonify({'error': 'The request security token is missing or expired. Refresh and retry.'}), 400
        abort(400, description='The request security token is missing or expired. Refresh the page and retry.')
    return None

@app.after_request
def add_security_headers(response):
    response.headers.setdefault('X-Content-Type-Options', 'nosniff')
    response.headers.setdefault('X-Frame-Options', 'DENY')
    response.headers.setdefault('Referrer-Policy', 'strict-origin-when-cross-origin')
    response.headers.setdefault('Cross-Origin-Opener-Policy', 'same-origin')
    response.headers.setdefault('Cross-Origin-Resource-Policy', 'same-origin')
    response.headers.setdefault('Permissions-Policy', 'camera=(), microphone=(), geolocation=()')
    response.headers.setdefault(
        'Content-Security-Policy',
        "default-src 'self'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'; "
        "object-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; connect-src 'self'",
    )
    if request.path.startswith('/static/'):
        response.headers['Cache-Control'] = 'public, max-age=0, must-revalidate'
    else:
        response.headers['Cache-Control'] = 'private, no-store'
    if production and request.is_secure:
        response.headers.setdefault('Strict-Transport-Security', 'max-age=31536000')
    return response

@app.context_processor
def globals_for_templates():
    return {
        'ugx': lambda value: f'UGX {value:,.0f}',
        'csrf_token': session.get('_csrf_token', ''),
        'current_user': getattr(g, 'current_user', None),
        'min_password_length': MIN_PASSWORD_LENGTH,
    }

def login_ip_hash():
    client_ip = request.remote_addr or 'unknown'
    return hmac.new(app.config['SECRET_KEY'].encode(), client_ip.encode(), sha256).hexdigest()

def record_login_event(event_type, username, user=None, ip_hash=None):
    db.session.add(LoginEvent(
        user_id=user.id if user else None,
        business_id=user.business_id if user else None,
        username=username[:80] or '(empty)',
        event_type=event_type,
        ip_hash=ip_hash if ip_hash is not None else login_ip_hash(),
    ))

def record_activity(action, entity_type, entity_id, summary, business_id=None, user_id=None):
    user = getattr(g, 'current_user', None)
    business_id = business_id or (user.business_id if user else session.get('business_id'))
    if not business_id:
        raise RuntimeError('Cannot write an audit event without a business context.')
    db.session.add(ActivityLog(
        business_id=business_id,
        user_id=user_id or (user.id if user else session.get('user_id')),
        action=action,
        entity_type=entity_type,
        entity_id=str(entity_id) if entity_id is not None else None,
        summary=summary[:240],
    ))

def end_user_session(user_session, reason):
    if user_session.ended_at is not None:
        return
    user_session.ended_at = datetime.utcnow()
    user_session.ended_reason = reason
    user = user_session.user
    record_login_event(reason, user.username, user, user_session.ip_hash)

def send_password_reset_email(user, token):
    server = os.environ.get('MAIL_SERVER')
    sender = os.environ.get('MAIL_FROM')
    if not server or not sender:
        raise RuntimeError('Password reset email is not configured; set MAIL_SERVER and MAIL_FROM.')
    base_url = os.environ.get('APP_BASE_URL', '').rstrip('/')
    if not base_url or (production and not base_url.startswith('https://')):
        raise RuntimeError('Set APP_BASE_URL to the public app URL; production reset links must use HTTPS.')
    port = int(os.environ.get('MAIL_PORT', '587'))
    username = os.environ.get('MAIL_USERNAME')
    password = os.environ.get('MAIL_PASSWORD')
    use_tls = os.environ.get('MAIL_USE_TLS', 'true').lower() == 'true'
    if username and not use_tls:
        raise RuntimeError('SMTP authentication requires TLS.')
    reset_url = base_url + url_for('reset_password', token=token)
    message = EmailMessage()
    message['Subject'] = 'DukaSmart password reset'
    message['From'] = sender
    message['To'] = user.email
    message.set_content(
        'A password reset was requested for your DukaSmart account. '
        f'Use this one-time link within 30 minutes: {reset_url}\n'
        'If you did not request this, you can ignore this email.'
    )
    with smtplib.SMTP(server, port, timeout=15) as smtp:
        if use_tls:
            smtp.starttls()
        if username:
            smtp.login(username, password or '')
        smtp.send_message(message)

@app.route('/setup', methods=['GET', 'POST'])
def setup():
    if User.query.first():
        return redirect(url_for('login'))
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        email = request.form.get('email', '').strip().lower()
        business_name = request.form.get('business_name', '').strip()
        if (not username or len(username) > 80 or len(password) < MIN_PASSWORD_LENGTH or len(password) > 256
                or not business_name or len(business_name) > 120 or '@' not in email):
            flash(f'Enter a business name, username, valid email and a password between {MIN_PASSWORD_LENGTH} and 256 characters.', 'error')
        else:
            business = Business.query.order_by(Business.id.asc()).first()
            if business:
                business.name = business_name
            else:
                business = Business(name=business_name)
                db.session.add(business)
                db.session.flush()
            db.session.add(User(
                username=username,
                password_hash=generate_password_hash(password),
                email=email,
                business_id=business.id,
                role='administrator',
            ))
            record_activity(
                'business_created', 'business', business.id,
                'Initial business workspace created', business_id=business.id,
            )
            db.session.commit()
            flash('Administrator created. Sign in with your new credentials.', 'success')
            return redirect(url_for('login'))
    return render_template('setup.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        ip_hash = login_ip_hash()
        attempt = db.session.query(LoginAttempt).filter_by(ip_hash=ip_hash).with_for_update().first()
        if attempt and attempt.locked_until and attempt.locked_until > now:
            username = request.form.get('username', '').strip()
            record_login_event('blocked', username)
            db.session.commit()
            flash('Too many unsuccessful sign-in attempts. Wait 15 minutes before trying again.', 'error')
            return render_template('login.html'), 429
        if attempt and attempt.window_started_at < now - timedelta(minutes=15):
            attempt.failed_attempts = 0
            attempt.window_started_at = now
            attempt.locked_until = None
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        user = User.query.filter_by(username=username).first() if len(username) <= 80 and len(password) <= 256 else None
        if user and user.is_active and check_password_hash(user.password_hash, password):
            if attempt:
                db.session.delete(attempt)
            user.last_login_at = now
            session.clear()
            session['_csrf_token'] = secrets.token_urlsafe(32)
            session.permanent = True
            session['user_id'] = user.id
            session['username'] = user.username
            session['business_id'] = user.business_id
            session['session_id'] = str(uuid.uuid4())
            user_session = UserSession(
                id=session['session_id'],
                user_id=user.id,
                business_id=user.business_id,
                expires_at=now + timedelta(hours=8),
                ip_hash=ip_hash,
            )
            db.session.add(user_session)
            record_login_event('sign_in', user.username, user)
            db.session.commit()
            return redirect(url_for(role_home_endpoint(user.role)))
        if not attempt:
            attempt = LoginAttempt(ip_hash=ip_hash, failed_attempts=0, window_started_at=now)
            db.session.add(attempt)
        attempt.failed_attempts += 1
        if attempt.failed_attempts >= 5:
            attempt.locked_until = now + timedelta(minutes=15)
        record_login_event('failed_sign_in', username, user)
        db.session.commit()
        flash('Incorrect username or password.', 'error')
    return render_template('login.html')

@app.route('/logout', methods=['POST'])
@login_required
def logout():
    user = db.session.get(User, session['user_id'])
    user_session = db.session.get(UserSession, session.get('session_id'))
    if user_session:
        end_user_session(user_session, 'sign_out')
    if user:
        record_activity('sign_out', 'user_session', session.get('session_id'), 'User signed out')
    db.session.commit()
    session.clear()
    return redirect(url_for('login'))

@app.route('/account/password', methods=['GET', 'POST'])
@login_required
def change_password():
    if request.method == 'POST':
        current = request.form.get('current_password', '')
        password = request.form.get('password', '')
        confirmation = request.form.get('confirm_password', '')
        if not check_password_hash(g.current_user.password_hash, current):
            flash('Current password is incorrect.', 'error')
        elif len(password) < MIN_PASSWORD_LENGTH or len(password) > 256 or password != confirmation:
            flash(f'Use a matching new password between {MIN_PASSWORD_LENGTH} and 256 characters.', 'error')
        else:
            g.current_user.password_hash = generate_password_hash(password)
            g.current_user.must_change_password = False
            for active_session in UserSession.query.filter_by(
                user_id=g.current_user.id,
                ended_at=None,
            ).all():
                if active_session.id != session.get('session_id'):
                    end_user_session(active_session, 'password_changed')
            record_activity(
                'password_changed', 'user', g.current_user.id,
                'Account password changed',
            )
            db.session.commit()
            flash('Password updated.', 'success')
            return redirect(url_for(role_home_endpoint(g.current_user.role)))
    return render_template('change_password.html')

@app.route('/forgot-password', methods=['GET', 'POST'])
def forgot_password():
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        email = request.form.get('email', '').strip().lower()
        user = User.query.filter_by(username=username, email=email, is_active=True).first()
        if user:
            now = datetime.utcnow()
            record_login_event('password_reset_requested', user.username, user)
            recent_requests = PasswordReset.query.filter(
                PasswordReset.user_id == user.id,
                PasswordReset.created_at >= now - timedelta(minutes=15),
            ).count()
            if recent_requests < 3:
                token = secrets.token_urlsafe(32)
                db.session.add(PasswordReset(
                    user_id=user.id,
                    token_hash=sha256(token.encode()).hexdigest(),
                    expires_at=now + timedelta(minutes=30),
                ))
                db.session.commit()
                try:
                    send_password_reset_email(user, token)
                except (OSError, smtplib.SMTPException, RuntimeError, ValueError) as error:
                    current_app.logger.error('Unable to send password reset email: %s', error)
            else:
                db.session.commit()
        flash('If the account details match, password reset instructions will be emailed.', 'success')
        return redirect(url_for('login'))
    return render_template('forgot_password.html')

@app.route('/reset-password/<token>', methods=['GET', 'POST'])
def reset_password(token):
    token_hash = sha256(token.encode()).hexdigest()
    reset = PasswordReset.query.filter_by(token_hash=token_hash, used_at=None).first()
    now = datetime.utcnow()
    if not reset or reset.expires_at <= now or not reset.user.is_active:
        abort(400, description='This password reset link is invalid or has expired.')
    if request.method == 'POST':
        password = request.form.get('password', '')
        confirmation = request.form.get('confirm_password', '')
        if len(password) < MIN_PASSWORD_LENGTH or len(password) > 256 or password != confirmation:
            flash(f'Use a matching password between {MIN_PASSWORD_LENGTH} and 256 characters.', 'error')
        else:
            user = reset.user
            user.password_hash = generate_password_hash(password)
            user.must_change_password = False
            reset.used_at = now
            for active_session in UserSession.query.filter_by(user_id=user.id, ended_at=None).all():
                end_user_session(active_session, 'password_reset')
            record_login_event('password_reset_completed', user.username, user)
            record_activity(
                'password_reset', 'user', user.id,
                'Password reset completed by email',
                business_id=user.business_id, user_id=user.id,
            )
            db.session.commit()
            flash('Password reset complete. Sign in with your new password.', 'success')
            return redirect(url_for('login'))
    return render_template('reset_password.html')

@app.cli.command('create-business')
def create_business_command():
    """Provision a separate business and its first administrator."""
    name = click.prompt('Business name').strip()
    username = click.prompt('Administrator username').strip()
    email = click.prompt('Administrator email').strip().lower()
    password = click.prompt(
        f'Administrator password ({MIN_PASSWORD_LENGTH}+ characters)',
        hide_input=True,
        confirmation_prompt=True,
    )
    if (not name or len(name) > 120 or not username or len(username) > 80
            or '@' not in email or len(password) < MIN_PASSWORD_LENGTH or len(password) > 256):
        raise click.ClickException(
            f'Provide valid business/account details and a {MIN_PASSWORD_LENGTH}-256 character password.'
        )
    if User.query.filter_by(username=username).first():
        raise click.ClickException('That username is already in use.')
    business = Business(name=name)
    db.session.add(business)
    db.session.flush()
    admin = User(
        username=username,
        email=email,
        password_hash=generate_password_hash(password),
        business_id=business.id,
        role='administrator',
    )
    db.session.add(admin)
    db.session.flush()
    record_activity(
        'business_created', 'business', business.id,
        'Business workspace provisioned by command line',
        business_id=business.id, user_id=admin.id,
    )
    db.session.commit()
    click.echo(f'Created business {business.id} with administrator {username}.')

@app.route('/admin/users', methods=['GET', 'POST'])
@roles_required('administrator')
def admin_users():
    if request.method == 'POST':
        action = request.form.get('action', '')
        if action == 'create':
            username = request.form.get('username', '').strip()
            email = request.form.get('email', '').strip().lower()
            password = request.form.get('password', '')
            role = request.form.get('role', '')
            if (not username or len(username) > 80 or '@' not in email
                    or len(password) < MIN_PASSWORD_LENGTH or len(password) > 256
                    or role not in ('administrator', 'manager', 'cashier', 'stock_clerk')):
                flash(
                    f'Enter valid account details, role and a {MIN_PASSWORD_LENGTH}-256 character temporary password.',
                    'error',
                )
            elif User.query.filter_by(username=username).first():
                flash('That username is already in use.', 'error')
            else:
                user = User(
                    username=username,
                    email=email,
                    password_hash=generate_password_hash(password),
                    business_id=g.business_id,
                    role=role,
                    must_change_password=True,
                )
                db.session.add(user)
                db.session.flush()
                record_activity(
                    'user_created', 'user', user.id,
                    f'Created account {username} with role {role}',
                )
                db.session.commit()
                flash('Employee account created. They must change the temporary password after signing in.', 'success')
                return redirect(url_for('admin_users'))
        elif action in ('update', 'disable', 'activate'):
            try:
                user_id = int(request.form.get('user_id', '0'))
            except ValueError:
                user_id = 0
            user = User.query.filter_by(id=user_id, business_id=g.business_id).first()
            if not user:
                abort(404)
            if action == 'disable':
                if user.id == g.current_user.id:
                    flash('You cannot disable your own account.', 'error')
                elif user.role == 'administrator' and user.is_active and User.query.filter_by(
                    business_id=g.business_id, role='administrator', is_active=True,
                ).count() <= 1:
                    flash('The business must retain at least one active administrator.', 'error')
                else:
                    user.is_active = False
                    for active_session in UserSession.query.filter_by(user_id=user.id, ended_at=None).all():
                        end_user_session(active_session, 'account_disabled')
                    record_activity('user_disabled', 'user', user.id, f'Disabled account {user.username}')
                    db.session.commit()
                    flash('Account disabled and active sessions revoked.', 'success')
                    return redirect(url_for('admin_users'))
            elif action == 'activate':
                user.is_active = True
                record_activity('user_activated', 'user', user.id, f'Reactivated account {user.username}')
                db.session.commit()
                flash('Account reactivated.', 'success')
                return redirect(url_for('admin_users'))
            else:
                role = request.form.get('role', '')
                email = request.form.get('email', '').strip().lower()
                if role not in ('administrator', 'manager', 'cashier', 'stock_clerk') or '@' not in email:
                    flash('Select a valid role and email address.', 'error')
                elif user.role == 'administrator' and user.is_active and (
                    role != 'administrator' or not user.is_active
                ) and User.query.filter_by(
                    business_id=g.business_id, role='administrator', is_active=True,
                ).count() <= 1:
                    flash('The business must retain at least one active administrator.', 'error')
                else:
                    changes = []
                    if user.role != role:
                        changes.append(f'role {user.role} to {role}')
                    if user.email != email:
                        changes.append('email address')
                    user.role = role
                    user.email = email
                    if changes:
                        record_activity(
                            'user_updated', 'user', user.id,
                            f'Updated {user.username}: {", ".join(changes)}',
                        )
                    db.session.commit()
                    flash('Account details saved.', 'success')
                    return redirect(url_for('admin_users'))

    now = datetime.utcnow()
    expired_sessions = UserSession.query.filter(
        UserSession.business_id == g.business_id,
        UserSession.ended_at.is_(None),
        UserSession.expires_at <= now,
    ).all()
    for expired in expired_sessions:
        end_user_session(expired, 'session_expired')
    if expired_sessions:
        db.session.commit()
    users = User.query.filter_by(business_id=g.business_id).order_by(User.username.asc()).all()
    active_sessions = UserSession.query.filter(
        UserSession.business_id == g.business_id,
        UserSession.ended_at.is_(None),
        UserSession.expires_at > now,
    ).order_by(UserSession.last_seen_at.desc()).all()
    login_events = LoginEvent.query.filter_by(
        business_id=g.business_id,
    ).order_by(LoginEvent.created_at.desc()).limit(100).all()
    return render_template(
        'admin_users.html',
        users=users,
        active_sessions=active_sessions,
        login_events=login_events,
    )

@app.route('/admin/sessions/<session_id>/revoke', methods=['POST'])
@roles_required('administrator')
def revoke_user_session(session_id):
    user_session = UserSession.query.filter_by(
        id=session_id,
        business_id=g.business_id,
        ended_at=None,
    ).first()
    if not user_session:
        abort(404)
    end_user_session(user_session, 'revoked')
    record_activity(
        'session_revoked', 'user_session', user_session.id,
        f'Revoked session for {user_session.user.username}',
    )
    db.session.commit()
    flash('Session revoked.', 'success')
    return redirect(url_for('admin_users'))

@app.route('/admin/audit')
@roles_required('administrator')
def admin_audit():
    events = ActivityLog.query.filter_by(
        business_id=g.business_id,
    ).order_by(ActivityLog.created_at.desc()).limit(500).all()
    return render_template('admin_audit.html', events=events)

@app.route('/admin/business', methods=['GET', 'POST'])
@roles_required('administrator')
def admin_business():
    business = db.session.get(Business, g.business_id)
    if not business:
        abort(404)
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        if not name or len(name) > 120:
            flash('Enter a business name of 1 to 120 characters.', 'error')
        elif business.name != name:
            old_name = business.name
            business.name = name
            record_activity(
                'business_settings_changed', 'business', business.id,
                f'Changed business name from {old_name} to {name}',
            )
            db.session.commit()
            flash('Business settings saved.', 'success')
            return redirect(url_for('admin_business'))
    return render_template('admin_business.html', business=business)

@app.route('/admin/api-keys', methods=['GET', 'POST'])
@roles_required('administrator')
def admin_api_keys():
    new_key = None
    if request.method == 'POST':
        action = request.form.get('action', 'create')
        if action == 'revoke':
            try:
                key_id = int(request.form.get('key_id', '0'))
            except ValueError:
                key_id = 0
            key = APIKey.query.filter_by(
                id=key_id,
                business_id=g.business_id,
                revoked_at=None,
            ).first()
            if not key:
                abort(404)
            key.revoked_at = datetime.utcnow()
            record_activity(
                'api_key_revoked', 'api_key', key.id,
                f'Revoked API key {key.name} ({key.key_prefix})',
            )
            db.session.commit()
            flash('API key revoked.', 'success')
            return redirect(url_for('admin_api_keys'))

        name = request.form.get('name', '').strip()
        scopes = sorted(set(request.form.getlist('scopes')))
        expiry = request.form.get('expires_in_days', '90')
        if (not name or len(name) > 80 or not scopes
                or any(scope not in API_KEY_SCOPES for scope in scopes)
                or expiry not in ('30', '90', '365', 'never')):
            flash('Enter a key name, select valid permissions and choose a valid expiry.', 'error')
        else:
            token = f'dsk_live_{secrets.token_urlsafe(32)}'
            key = APIKey(
                name=name,
                key_hash=sha256(token.encode()).hexdigest(),
                key_prefix=token[:16],
                scopes=','.join(scopes),
                business_id=g.business_id,
                created_by_id=g.current_user.id,
                expires_at=(
                    datetime.utcnow() + timedelta(days=int(expiry))
                    if expiry != 'never' else None
                ),
            )
            db.session.add(key)
            db.session.flush()
            record_activity(
                'api_key_created', 'api_key', key.id,
                f'Created API key {name} with scopes {", ".join(scopes)}',
            )
            db.session.commit()
            new_key = token
    keys = APIKey.query.filter_by(
        business_id=g.business_id,
    ).order_by(APIKey.created_at.desc()).all()
    return render_template(
        'admin_api_keys.html',
        keys=keys,
        scopes=API_KEY_SCOPES,
        new_key=new_key,
        now=datetime.utcnow(),
    )

@app.route('/')
@roles_required('administrator', 'manager')
def dashboard():
    products = Product.query.filter_by(business_id=g.business_id).order_by(Product.name.asc()).all()
    recent = Transaction.query.filter_by(
        business_id=g.business_id,
    ).order_by(Transaction.created_at.desc()).limit(8).all()
    start = datetime.combine(date.today(), datetime.min.time())
    today_tx = Transaction.query.filter(
        Transaction.business_id == g.business_id,
        Transaction.created_at >= start,
    ).all()
    sales = sum(t.amount for t in today_tx if t.kind == 'sale')
    expenses = sum(t.amount for t in today_tx if t.kind == 'expense')
    cogs = sum(
        (transaction.quantity or 0) * (transaction.product.cost_price if transaction.product else 0)
        for transaction in today_tx if transaction.kind == 'sale' and transaction.sale_id is None
    ) + sum(
        line.quantity * line.unit_cost
        for line in CheckoutLine.query.join(CheckoutSale).filter(
            CheckoutSale.business_id == g.business_id,
            CheckoutSale.created_at >= start,
        ).all()
    )
    debts = sum(
        max(0, d.amount - d.amount_paid)
        for d in CustomerDebt.query.filter_by(business_id=g.business_id).all()
    )
    return render_template('dashboard.html', products=products, transactions=recent, sales=sales,
        expenses=expenses, estimated_profit=sales-cogs-expenses, low_count=sum(p.quantity <= p.low_stock_at for p in products),
        debt_total=debts, stock_value=sum(p.cost_price*p.quantity for p in products))

@app.route('/products', methods=['GET', 'POST'])
@roles_required('administrator', 'manager', 'stock_clerk')
def products():
    if request.method == 'POST':
        name = request.form.get('name', '').strip(); sku = request.form.get('sku', '').strip()
        try:
            cost = float(request.form.get('cost_price', 0)); price = float(request.form.get('selling_price', 0))
            qty = int(request.form.get('quantity', 0)); low = int(request.form.get('low_stock_at', 5))
            if (not name or not sku or not math.isfinite(cost) or not math.isfinite(price)
                    or min(cost, price, qty, low) < 0 or qty > 2_147_483_647
                    or low > 2_147_483_647
                    or not math.isfinite(cost * qty)):
                raise ValueError()
            if Product.query.filter_by(sku=sku, business_id=g.business_id).first():
                flash('That product code already exists. Use a unique code.', 'error')
            else:
                p = Product(
                    name=name,
                    sku=sku,
                    category='general',
                    cost_price=cost,
                    selling_price=price,
                    quantity=qty,
                    low_stock_at=low,
                    base_unit='unit',
                    purchase_unit='unit',
                    units_per_purchase_unit=1,
                    business_id=g.business_id,
                )
                db.session.add(p)
                db.session.flush()
                if qty:
                    transaction = Transaction(
                        kind='restock',
                        description=f'Opening stock: {name}',
                        amount=cost * qty,
                        quantity=qty,
                        product_id=p.id,
                        business_id=g.business_id,
                        created_by_id=g.current_user.id,
                    )
                    db.session.add(transaction)
                    db.session.flush()
                    db.session.add(StockMovement(
                        product_id=p.id,
                        business_id=g.business_id,
                        transaction_id=transaction.id,
                        movement_type='opening',
                        quantity_delta=qty,
                        unit_cost=cost,
                        reason='Opening stock',
                        created_by_id=session['user_id'],
                    ))
                    record_activity(
                        'stock_adjusted', 'product', p.id,
                        f'Opening stock: {qty} units of {name}',
                    )
                record_activity('product_created', 'product', p.id, f'Created product {name}')
                db.session.commit()
                flash('Product added.', 'success'); return redirect(url_for('products'))
        except (ValueError, TypeError): flash('Enter valid, non-negative numbers and product details.', 'error')
    return render_template(
        'products.html',
        products=Product.query.filter_by(business_id=g.business_id).order_by(Product.name.asc()).all(),
    )

@app.route('/sales', methods=['GET', 'POST'])
@roles_required('administrator', 'manager', 'cashier')
def sales():
    products = Product.query.filter_by(business_id=g.business_id).order_by(Product.name.asc()).all()
    if request.method == 'POST':
        try:
            p = db.session.query(Product).filter_by(
                id=int(request.form.get('product_id', 0)),
                business_id=g.business_id,
            ).with_for_update().first(); qty = int(request.form.get('quantity', 0))
            method = request.form.get('payment_method', 'Cash')
            if not p or qty <= 0: raise ValueError()
            if qty > p.quantity: flash(f'Not enough stock. Available: {p.quantity}.', 'error')
            else:
                total = p.selling_price * qty; p.quantity -= qty
                transaction = Transaction(
                    kind='sale',
                    description=f'{p.name} × {qty}',
                    amount=total,
                    quantity=qty,
                    product_id=p.id,
                    business_id=g.business_id,
                    created_by_id=g.current_user.id,
                    payment_method=method,
                )
                db.session.add(transaction)
                db.session.flush()
                db.session.add(StockMovement(
                    product_id=p.id,
                    business_id=g.business_id,
                    transaction_id=transaction.id,
                    movement_type='sale',
                    quantity_delta=-qty,
                    unit_cost=p.cost_price,
                    reason='Sale',
                    created_by_id=session['user_id'],
                ))
                record_activity(
                    'sale_recorded', 'transaction', transaction.id,
                    f'Recorded sale of {qty} {p.name} for UGX {total:,.0f}',
                )
                db.session.commit()
                flash(f'Sale recorded: UGX {total:,.0f}.', 'success')
                return redirect(url_for('sales'))
        except (ValueError, TypeError): flash('Choose a product and enter a valid quantity.', 'error')
    return render_template('sales.html', products=products)

@app.route('/api/products')
@roles_required('administrator', 'manager', 'cashier')
def api_products():
    products = Product.query.filter_by(
        business_id=g.business_id,
    ).order_by(Product.name.asc()).all()
    response = jsonify([{
        'id': p.id,
        'name': p.name,
        'sku': p.sku,
        'selling_price': p.selling_price,
        'quantity': p.quantity,
    } for p in products])
    response.headers['X-CSRF-Token'] = session['_csrf_token']
    return response

def api_paginated(query, serialize=lambda item: dict(item._mapping)):
    try:
        page = int(request.args.get('page', '1'))
        per_page = int(request.args.get('per_page', '50'))
        if page < 1 or not 1 <= per_page <= 100:
            raise ValueError()
    except (TypeError, ValueError):
        return api_error('invalid_pagination', 'page must be positive and per_page must be between 1 and 100.', 400)
    result = query.paginate(page=page, per_page=per_page, error_out=False)
    return jsonify({
        'data': [serialize(item) for item in result.items],
        'pagination': {
            'page': result.page,
            'per_page': result.per_page,
            'total': result.total,
            'pages': result.pages,
            'has_next': result.has_next,
            'has_previous': result.has_prev,
        },
    })

@app.route('/api/v1/products')
@api_key_required('products:read')
def external_api_products():
    query = Product.query.filter_by(
        business_id=g.business_id,
    ).order_by(Product.id.asc())
    return api_paginated(query.with_entities(
        Product.id.label('id'),
        Product.name.label('name'),
        Product.sku.label('sku'),
        Product.cost_price.label('cost_price'),
        Product.selling_price.label('selling_price'),
        Product.quantity.label('quantity'),
        Product.low_stock_at.label('low_stock_at'),
        Product.base_unit.label('unit'),
    ))

@app.route('/api/v1/sales')
@api_key_required('sales:read')
def external_api_sales():
    query = Transaction.query.filter_by(
        business_id=g.business_id,
        kind='sale',
    ).order_by(Transaction.created_at.desc(), Transaction.id.desc())
    serialized = query.with_entities(
        Transaction.id.label('id'),
        Transaction.description.label('description'),
        Transaction.amount.label('amount'),
        Transaction.quantity.label('quantity'),
        Transaction.product_id.label('product_id'),
        Transaction.payment_method.label('payment_method'),
        Transaction.created_at.label('created_at'),
    )
    return api_paginated(serialized, lambda item: {
            'id': item.id,
            'description': item.description,
            'amount': item.amount,
            'quantity': item.quantity,
            'product_id': item.product_id,
            'payment_method': item.payment_method,
            'created_at': item.created_at.isoformat() if item.created_at else None,
        })

@app.route('/api/v1/expenses')
@api_key_required('expenses:read')
def external_api_expenses():
    query = Transaction.query.filter_by(
        business_id=g.business_id,
        kind='expense',
    ).order_by(Transaction.created_at.desc(), Transaction.id.desc())
    return api_paginated(query.with_entities(
        Transaction.id.label('id'),
        Transaction.description.label('description'),
        Transaction.amount.label('amount'),
        Transaction.payment_method.label('payment_method'),
        Transaction.created_at.label('created_at'),
    ), lambda item: {
        'id': item.id,
        'description': item.description,
        'amount': item.amount,
        'payment_method': item.payment_method,
        'created_at': item.created_at.isoformat() if item.created_at else None,
    })

@app.route('/api/v1/credit')
@api_key_required('credit:read')
def external_api_credit():
    outstanding = (CustomerDebt.amount - CustomerDebt.amount_paid).label('outstanding')
    query = CustomerDebt.query.filter_by(
        business_id=g.business_id,
    ).order_by(CustomerDebt.created_at.desc(), CustomerDebt.id.desc())
    return api_paginated(query.with_entities(
        CustomerDebt.id.label('id'),
        CustomerDebt.customer_name.label('customer_name'),
        CustomerDebt.description.label('description'),
        CustomerDebt.amount.label('amount'),
        CustomerDebt.amount_paid.label('amount_paid'),
        outstanding,
        CustomerDebt.created_at.label('created_at'),
    ), lambda item: {
        'id': item.id,
        'customer_name': item.customer_name,
        'description': item.description,
        'amount': item.amount,
        'amount_paid': item.amount_paid,
        'outstanding': item.outstanding,
        'created_at': item.created_at.isoformat() if item.created_at else None,
    })

@app.route('/api/v1/reports')
@api_key_required('reports:read')
def external_api_reports():
    business_id = g.business_id
    sales_total = db.session.query(
        func.coalesce(func.sum(Transaction.amount), 0),
    ).filter_by(business_id=business_id, kind='sale').scalar()
    expenses_total = db.session.query(
        func.coalesce(func.sum(Transaction.amount), 0),
    ).filter_by(business_id=business_id, kind='expense').scalar()
    standard_cogs = db.session.query(
        func.coalesce(func.sum(Transaction.quantity * Product.cost_price), 0),
    ).join(Product, Product.id == Transaction.product_id).filter(
        Transaction.business_id == business_id,
        Transaction.kind == 'sale',
        Transaction.sale_id.is_(None),
        Product.business_id == business_id,
    ).scalar()
    checkout_cogs = db.session.query(
        func.coalesce(func.sum(CheckoutLine.quantity * CheckoutLine.unit_cost), 0),
    ).join(CheckoutSale, CheckoutSale.id == CheckoutLine.sale_id).filter(
        CheckoutSale.business_id == business_id,
    ).scalar()
    stock_value = db.session.query(
        func.coalesce(func.sum(Product.cost_price * Product.quantity), 0),
    ).filter_by(business_id=business_id).scalar()
    cogs = standard_cogs + checkout_cogs
    return jsonify({'data': {
        'sales_total': sales_total,
        'expenses_total': expenses_total,
        'cost_of_goods_sold': cogs,
        'estimated_profit': sales_total - cogs - expenses_total,
        'stock_value_at_cost': stock_value,
        'currency': 'UGX',
        'generated_at': datetime.utcnow().isoformat(),
    }})

@app.route('/api/sales/sync', methods=['POST'])
@roles_required('administrator', 'manager', 'cashier')
def sync_sales():
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict) or not isinstance(payload.get('sales'), list):
        return jsonify({'error': 'Expected a sales list.'}), 400
    if not payload['sales'] or len(payload['sales']) > 50:
        return jsonify({'error': 'Submit between 1 and 50 sales per batch.'}), 400

    results = []
    for item in payload['sales']:
        if not isinstance(item, dict):
            results.append({'id': None, 'status': 'rejected', 'message': 'Invalid sale data.'})
            continue
        client_id = item.get('id')
        try:
            sale_id = str(uuid.UUID(client_id))
            product_id = item.get('product_id')
            quantity = item.get('quantity')
            if (not isinstance(product_id, int) or isinstance(product_id, bool) or product_id <= 0
                    or not isinstance(quantity, int) or isinstance(quantity, bool) or quantity <= 0):
                raise ValueError()
        except (ValueError, TypeError, AttributeError):
            results.append({'id': client_id, 'status': 'rejected', 'message': 'Invalid sale identifier, product or quantity.'})
            continue

        if item.get('owner') != session.get('username'):
            results.append({'id': sale_id, 'status': 'rejected', 'message': 'This sale belongs to a different signed-in user.'})
            continue
        method = item.get('payment_method')
        if method not in ('Cash', 'Mobile Money', 'Bank', 'Credit'):
            results.append({'id': sale_id, 'status': 'rejected', 'message': 'Invalid payment method.'})
            continue
        try:
            sold_at = datetime.fromisoformat(item.get('sold_at', '').replace('Z', '+00:00'))
            if sold_at.tzinfo is not None:
                sold_at = sold_at.astimezone(timezone.utc).replace(tzinfo=None)
        except (ValueError, TypeError, AttributeError):
            results.append({'id': sale_id, 'status': 'rejected', 'message': 'Invalid sale date.'})
            continue

        unit_price = item.get('unit_price')
        if (not isinstance(unit_price, (int, float)) or isinstance(unit_price, bool)
                or not math.isfinite(unit_price) or unit_price < 0):
            results.append({'id': sale_id, 'status': 'rejected', 'message': 'Invalid sale price.'})
            continue
        existing = Transaction.query.filter_by(
            sync_id=sale_id,
            business_id=g.business_id,
        ).first()
        if existing:
            results.append({'id': sale_id, 'status': 'duplicate'})
            db.session.rollback()
            continue

        product = db.session.query(Product).filter_by(
            id=product_id,
            business_id=g.business_id,
        ).with_for_update().first()
        if not product:
            results.append({'id': sale_id, 'status': 'rejected', 'message': 'Product no longer exists.'})
            db.session.rollback()
            continue
        if unit_price != product.selling_price:
            results.append({'id': sale_id, 'status': 'conflict', 'message': 'The product price changed; review this sale before synchronization.'})
            db.session.rollback()
            continue
        if quantity > product.quantity:
            results.append({'id': sale_id, 'status': 'conflict', 'message': f'Only {product.quantity} in stock on the server.'})
            db.session.rollback()
            continue

        total = product.selling_price * quantity
        product.quantity -= quantity
        transaction = Transaction(
            kind='sale',
            description=f'{product.name} × {quantity}',
            amount=total,
            quantity=quantity,
            product_id=product.id,
            business_id=g.business_id,
            created_by_id=g.current_user.id,
            payment_method=method,
            sync_id=sale_id,
            created_at=sold_at,
        )
        db.session.add(transaction)
        try:
            db.session.flush()
            db.session.add(StockMovement(
                product_id=product.id,
                business_id=g.business_id,
                transaction_id=transaction.id,
                movement_type='sale',
                quantity_delta=-quantity,
                unit_cost=product.cost_price,
                reason='Synchronized offline sale',
                created_by_id=session['user_id'],
                created_at=sold_at,
            ))
            record_activity(
                'sale_synchronized', 'transaction', transaction.id,
                f'Synchronized offline sale of {quantity} {product.name} for UGX {total:,.0f}',
            )
            db.session.commit()
            results.append({'id': sale_id, 'status': 'synced'})
        except IntegrityError:
            db.session.rollback()
            existing = Transaction.query.filter_by(
                sync_id=sale_id,
                business_id=g.business_id,
            ).first()
            if existing:
                results.append({'id': sale_id, 'status': 'duplicate'})
            else:
                raise
    return jsonify({'results': results})

@app.route('/service-worker.js')
def service_worker():
    response = send_from_directory(app.static_folder, 'sw.js')
    response.headers['Service-Worker-Allowed'] = '/'
    response.headers['Cache-Control'] = 'no-cache'
    return response

@app.route('/expenses', methods=['GET', 'POST'])
@roles_required('administrator', 'manager')
def expenses():
    if request.method == 'POST':
        desc = request.form.get('description', '').strip()
        try:
            amount = float(request.form.get('amount', 0))
            if not desc or amount <= 0: raise ValueError()
            transaction = Transaction(
                kind='expense',
                description=desc,
                amount=amount,
                business_id=g.business_id,
                created_by_id=g.current_user.id,
                payment_method=request.form.get('payment_method', 'Cash'),
            )
            db.session.add(transaction)
            db.session.flush()
            record_activity(
                'expense_recorded', 'transaction', transaction.id,
                f'Recorded expense {desc} for UGX {amount:,.0f}',
            )
            db.session.commit()
            flash('Expense recorded.', 'success')
            return redirect(url_for('expenses'))
        except (ValueError, TypeError): flash('Enter a description and amount greater than zero.', 'error')
    items = Transaction.query.filter_by(
        kind='expense',
        business_id=g.business_id,
    ).order_by(Transaction.created_at.desc()).limit(100).all()
    return render_template('expenses.html', items=items)

@app.route('/debts', methods=['GET', 'POST'])
@roles_required('administrator', 'manager', 'cashier')
def debts():
    if request.method == 'POST':
        name = request.form.get('customer_name', '').strip(); desc = request.form.get('description', '').strip()
        phone = request.form.get('phone', '').strip()
        try:
            amount = float(request.form.get('amount', 0))
            if not name or not desc or amount <= 0: raise ValueError()
            debt = CustomerDebt(
                customer_name=name,
                phone=phone,
                description=desc,
                amount=amount,
                business_id=g.business_id,
            )
            db.session.add(debt)
            db.session.flush()
            record_activity(
                'customer_credit_recorded', 'customer_debt', debt.id,
                f'Recorded customer credit of UGX {amount:,.0f}',
            )
            db.session.commit()
            flash('Customer credit recorded.', 'success')
            return redirect(url_for('debts'))
        except (ValueError, TypeError): flash('Enter customer name, details and a valid amount.', 'error')
    return render_template(
        'debts.html',
        debts=CustomerDebt.query.filter_by(
            business_id=g.business_id,
        ).order_by(CustomerDebt.created_at.desc()).all(),
    )

@app.route('/debts/<int:debt_id>/payment', methods=['POST'])
@roles_required('administrator', 'manager', 'cashier')
def debt_payment(debt_id):
    debt = CustomerDebt.query.filter_by(
        id=debt_id,
        business_id=g.business_id,
    ).first()
    if not debt: return redirect(url_for('debts'))
    try:
        amount = float(request.form.get('amount', 0)); outstanding = debt.amount-debt.amount_paid
        if amount <= 0 or amount > outstanding: raise ValueError()
        debt.amount_paid += amount
        record_activity(
            'customer_credit_payment', 'customer_debt', debt.id,
            f'Recorded customer credit payment of UGX {amount:,.0f}',
        )
        db.session.commit()
        flash('Payment recorded.', 'success')
    except (ValueError, TypeError): flash('Enter a payment within the outstanding balance.', 'error')
    return redirect(url_for('debts'))

@app.route('/reports')
@roles_required('administrator', 'manager')
def reports():
    items = Transaction.query.filter_by(
        business_id=g.business_id,
    ).order_by(Transaction.created_at.desc()).all()
    sales_total = sum(t.amount for t in items if t.kind == 'sale')
    expenses_total = sum(t.amount for t in items if t.kind == 'expense')
    cogs = sum(
        (transaction.quantity or 0) * (transaction.product.cost_price if transaction.product else 0)
        for transaction in items if transaction.kind == 'sale' and transaction.sale_id is None
    ) + sum(
        line.quantity * line.unit_cost for line in CheckoutLine.query.join(CheckoutSale).filter(
            CheckoutSale.business_id == g.business_id,
        ).all()
    )
    return render_template('reports.html', items=items, sales_total=sales_total, expense_total=expenses_total,
        cogs=cogs, estimated_profit=sales_total-cogs-expenses_total,
        stock_value=sum(
            p.cost_price * p.quantity
            for p in Product.query.filter_by(business_id=g.business_id).all()
        ))

@app.route('/health')
def health(): return jsonify({'status': 'ok', 'app': 'DukaSmart Uganda'})

if __name__ == '__main__':
    app.run(host='127.0.0.1', port=int(os.environ.get('PORT', 5000)), debug=os.environ.get('FLASK_DEBUG') == '1')
