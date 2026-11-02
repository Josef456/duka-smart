# DukaSmart Uganda — MVP starter

A responsive Flask starter for small-business inventory, sales, expenses, customer credit and basic reports.

## Run on Windows PowerShell

```powershell
cd path\to\DukaSmart_Uganda
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python -m flask --app app db upgrade
python app.py
```

If your system uses `python3` instead of `python`, use `python3 -m venv .venv` and `python3 app.py`.

Open `http://127.0.0.1:5000/setup` on first launch and create your own administrator username and password (minimum 4 characters). There is no default password. On later visits, use `/login`.

## Deploy to Render

1. Extract this ZIP and push the project folder to a GitHub repository.
2. In Render, create a Blueprint and select that repository; Render reads `render.yaml` to configure the web service and PostgreSQL database. The start command applies versioned migrations before starting Gunicorn.
3. Deploy, then open `/setup` once to create the administrator.
4. Confirm the generated `SESSION_SECRET` and database connection are set. Keep secrets out of GitHub.

Offline sales require HTTPS (Render provides HTTPS) or localhost, plus a browser that supports service workers and IndexedDB.

## MVP features

- Password-hashed administrator login
- Employee accounts with administrator, manager, cashier and stock clerk roles
- Business-scoped access, server-side role checks and employee account management
- Sign-in history, revocable active sessions, last-sign-in status and activity audit
- One-time email password reset with session revocation
- Business-level data isolation for shared multi-business deployments
- Product catalogue with SKU, cost, selling price, quantity and low-stock threshold; inventory can be filtered by product name/code and stock status
- Sales recording with stock deduction, product name/code search and offline synchronization
- Expense records
- Customer credit ledger and partial payments
- Basic transaction report and print support
- Offline sales queue with automatic synchronization when the device reconnects

## Important limitations

Offline support is limited to sales. Sign in and open the sales page online at least once on the device so its product catalogue and offline application shell are stored. Sales created without a connection are kept in that browser's IndexedDB and synchronize when the app is open and the connection returns; browsers supporting Background Sync may also upload them in the background. Keep the device secure and do not clear browser data until pending sales have synchronized. The queue is not a backup and is not shared between devices.

Database schema changes use Flask-Migrate. Back up the database before applying migrations in production. The `0005_accounts_and_businesses` migration assigns pre-existing users and records to an initial business, sets existing accounts to administrator, and preserves their password hashes and transaction IDs. It adds business ownership and login/session/audit records without deleting existing data. Bar Mode has been retired from the user interface and its routes are unavailable; its schema and historical transactions remain retained and reportable.

The four roles are checked on the server: administrators manage business settings, users, sessions and audit history; managers can use dashboard, inventory, sales, expenses, customer credit and reports; cashiers can use sales and customer credit; stock clerks can use inventory. The first administrator is created at `/setup`. Administrators can create, disable, reactivate and update employees at `/admin/users`, and change the business name at `/admin/business`. Employee accounts require a temporary password and must change it at first sign-in. `flask --app app create-business` interactively provisions a separate business and its first administrator; usernames and product SKUs are globally unique across businesses. Every operational query and offline sale synchronization is scoped to the signed-in user's business.

External server-to-server integrations can use read-only business data endpoints at `/api/v1/products`, `/api/v1/sales`, `/api/v1/expenses`, `/api/v1/credit`, and `/api/v1/reports`. An administrator creates and revokes credentials at `/admin/api-keys`; each key is restricted to the selected permission and business, can be set to expire, is limited to 120 requests per minute, and is stored as a hash. The full key is shown only once. Send it in an HTTPS `Authorization: Bearer <key>` header; never put credentials in a URL, browser code, or source control. For example, `curl -H "Authorization: Bearer <key>" "https://your-domain/api/v1/products?per_page=50"`. List endpoints use `data` and `pagination` objects; `page` defaults to 1 and `per_page` defaults to 50 (maximum 100). Credit responses omit customer phone numbers. Report totals are all-time estimates and are not statutory accounting. These credentials do not grant access to employee, session, audit, or business-settings administration. Write operations will be added in a later API phase.

Session records expire eight hours after sign-in and are checked on each protected request. Expiry is recorded when the expired session is next encountered or when an administrator opens session monitoring; an idle browser is not polled in the background. Administrators can inspect/revoke active sessions and review business-scoped sign-in history. Activity records are written for successful sales and offline synchronization, opening-stock adjustments, expenses, customer-credit changes, account management, session actions and password changes. Existing historical rows without an actor remain unattributed. This application has no refund workflow or general stock-adjustment route, so those operations cannot currently be performed or logged; audit hooks should be added alongside any future such workflow.

Email password resets require the following environment variables; configure them in the deployment provider, never in source control: `APP_BASE_URL` (production must be HTTPS), `MAIL_SERVER`, `MAIL_PORT` (default `587`), `MAIL_USERNAME`, `MAIL_PASSWORD`, `MAIL_FROM`, and optionally `MAIL_USE_TLS` (default `true`). For Gmail, use `smtp.gmail.com`, port `587`, and TLS. Use a Google App Password (not the normal Google account password); App Passwords require 2-Step Verification and may be disabled by Google Workspace administrators. Set `MAIL_USERNAME` and `MAIL_FROM` to the sending Gmail address. In Render, set the mail variables and the public HTTPS app URL in the service's Environment settings; the Render blueprint provides Gmail SMTP defaults and leaves the private values for entry in the dashboard. For local PowerShell runs, set the same variables in the terminal that starts Flask; use `APP_BASE_URL=http://127.0.0.1:5000` for local testing, whose reset link only works on that computer. Keep SMTP credentials private. Reset links are random, stored as hashes, expire after 30 minutes, and can be used once. Reset requests require both the account username and email and return the same response whether or not the account exists. Delivery failures are logged in the server logs while the public response stays generic to prevent account enumeration.

Security hardening includes CSRF checks on form/API writes, security response headers, secure session-cookie settings in production, a 4-character minimum for newly created passwords, and database-backed sign-in throttling (five failed attempts per client IP per 15-minute window). A 4-character minimum is weak; use a long, unique password, especially for administrator accounts. The `0004_security_hardening` migration stores keyed client-IP hashes rather than raw addresses. Production (`RENDER`, `APP_ENV=production`, or `FLASK_ENV=production`) requires `SESSION_SECRET` to be at least 32 characters; the Render blueprint generates this value. Local development without a configured secret uses a temporary random key, so sessions expire when the app restarts.

The existing `/sales` page remains available for one-product sales and offline synchronization; offline conflicts remain queued for review. Offline expenses, customer credit, and inventory changes are not available offline. Payment methods are labels only; the application does not collect card or Mobile Money funds. This is an early MVP, not production-ready accounting software. There is no EFRIS integration, tax filing, automatic Mobile Money collection, audit-grade accounting, export/restore workflow, or exhaustive automated test suite. Profit is an estimate and needs accounting validation. This hardening is a baseline, not a substitute for a professional security review. Before commercial launch, implement data export and backup/restore, reviewed offline conflict handling, privacy terms, security tests and review of current URA requirements.
