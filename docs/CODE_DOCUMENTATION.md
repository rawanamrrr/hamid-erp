# Wholesale POS System - Code Documentation

## 1) Overview

This project is a Django-based wholesale/retail POS and operations platform.  
It combines:

- Point of Sale (POS) with draft invoices and mobile POS
- Inventory and warehouse stock management
- CRM/customer debt tracking
- Financial shifts, accounts, and transaction ledger
- Shipping/fulfillment workflows
- Notifications and system administration

Core stack:

- Backend: Django 5.2
- Database: SQLite (`db.sqlite3`)
- Templates: Django templates (server-rendered UI)
- Static assets: `static/` + `staticfiles/`
- Runtime server options: Django dev server, Waitress, Gunicorn

---

## 2) Project Structure

Top-level applications:

- `accounts/`: auth, users, roles, onboarding, activity/error tracking
- `dashboard/`: home dashboard and performance reports
- `products/`: catalog, categories, suppliers, warehouses, stock transactions, costing
- `crm/`: customer records, debt/payment actions, reports
- `sales/`: POS, orders, invoices, draft carts, returns, expenses
- `financial/`: daily shifts, account management, cash/ledger transactions
- `shipping/`: shipping companies, shipments, status/payment updates
- `notifications/`: in-app notification APIs and broadcast
- `settings/`: system settings + DB backup/import actions
- `search_system/`: global search endpoint
- `camera_view/`: camera dashboard/live feed

Framework-level folders:

- `templates/`: shared + app templates
- `static/`: source static files
- `textile_pos/`: project settings and root URL routing

---

## 3) Configuration and Runtime

Main settings file: `textile_pos/settings.py`

Important current configuration:

- `DEBUG = True` (development-oriented)
- DB engine: SQLite
- Language: Arabic (`ar`)
- Timezone: Cairo (`Africa/Cairo`)
- Custom middleware in use:
  - `accounts.middleware.RequireOnboardingMiddleware`
  - `accounts.middleware.SystemErrorCaptureMiddleware`
- Global template context processor:
  - `settings.context_processors.system_settings`

Root URL mapping: `textile_pos/urls.py`

- `/accounts/` -> `accounts.urls`
- `/` -> `dashboard.urls`
- `/products/` -> `products.urls`
- `/crm/` -> `crm.urls`
- `/sales/` -> `sales.urls`
- `/financial/` -> `financial.urls`
- `/notifications/` -> `notifications.urls`
- plus `settings`, `search`, `shipping`, and `camera`

---

## 4) Data Model Summary

### Accounts app

- `Role`: permission grouping and role definition
- `UserProfile`: links users to roles and profile metadata
- `UserActivityLog`: user actions audit trail
- `UserIPHistory`: user login/access IP history
- `SystemError`: captured backend/system error records

### Products app

- `Category`: product categorization
- `Supplier`: supplier profiles
- `PurchaseInvoice` + `SupplierPayment`: supplier financial lifecycle
- `Product`: product master (SKU/pricing/cost metadata)
- `Warehouse`: stock location
- `WarehouseStock`: per-product per-warehouse stock
- `StockTransaction`: movement/adjustment record
- `ProductCosting`: costing calculations snapshots/history

### CRM app

- `Customer`: customer profile, type, balance/debt related data

### Sales app

- `Order`: commercial transaction header
- `OrderItem`: sold item lines
- `Draft`: saved POS cart draft (open/closed states)
- `ReturnInvoice` + `ReturnItem`: returns/refund records
- `Expense`: operational expenses
- `OtherIncome`: non-sales income entries
- `CashSettlement`: cash reconciliation snapshots

### Financial app

- `Account`: cash/bank/wallet or financial account entity
- `DailyShift`: shift open/close lifecycle and totals
- `Transaction`: ledger movement records
- `ShiftEmailLog`: shift report dispatch tracking

### Shipping app

- `ShippingCompany`: provider definitions
- `Shipment`: shipping record per order/customer
- `ShipmentLog`: status/progress log entries

### Notifications app

- `Notification`: in-app notification queue/state

### Camera app

- `Camera`: configured camera endpoints/metadata

### Settings app

- `SystemSetting`: system-wide configuration (branding, options, etc.)

---

## 5) Endpoint Map by App (High-Level)

## Accounts (`accounts/urls.py`)

- Authentication: login/logout
- Onboarding flow
- User CRUD
- Role management
- Activity logs
- System error history + resolve/restart operations
- JS error logging API

## Dashboard (`dashboard/urls.py`)

- Main dashboard
- Margin report
- Sales profit report

## Products (`products/urls.py`)

- Product CRUD + public SKU detail
- Bulk add, bulk price, bulk cost updates
- Import/export (Excel/PDF/barcode/QR)
- Warehouse CRUD + transfers (single/bulk + POS-integrated transfer API)
- Supplier CRUD + profile, purchase, payments
- Categories CRUD
- Stock transaction pages
- Costing pages + costing APIs

## CRM (`crm/urls.py`)

- Customer list/detail/create/update/delete
- Bulk import/template
- Customer payment/debt actions
- Print/report endpoints

## Sales (`sales/urls.py`)

- POS desktop + mobile
- Draft APIs:
  - save
  - list
  - retrieve
  - delete
  - print draft invoice
- Orders list/report
- Invoice rendering + PDF + direct print
- Factory/tailoring operations
- Refund flow + order lookup API
- Expenses and financial statement page
- POS APIs:
  - create customer
  - submit order
  - edit/delete order
  - update tailoring status

## Financial (`financial/urls.py`, namespace `financial`)

- Financial dashboard
- Transaction list/add/print
- Withdrawal page
- Shift history/start/close/print
- Accounts CRUD/detail

## Shipping (`shipping/urls.py`)

- Shipping dashboard
- Shipment creation from order
- Company management
- Label print
- Address update
- Shipment/payment status update APIs

## Notifications (`notifications/urls.py`)

- Get/check notifications
- Mark one/all as read
- Broadcast notification

## Settings (`settings/urls.py`)

- System settings page
- Database backup download
- Database import

## Search (`search_system/urls.py`)

- Global search results endpoint

## Camera (`camera_view/urls.py`)

- Camera dashboard
- Live feed route by camera ID and stream ID

---

## 6) POS + Draft Workflow (Current Behavior)

Primary template: `templates/sales/pos.html`

Flow:

1. User builds cart and order details.
2. User can explicitly save draft using `save_draft_ajax`.
3. User can list/open/delete/print drafts from modal.
4. On page refresh/leave with non-empty cart, frontend attempts auto-save:
   - Uses `navigator.sendBeacon` first
   - Fallback to `fetch(..., keepalive: true)`
5. On next load, user receives toast indicating cart was saved as draft on refresh.

Backend draft persistence endpoint:

- `sales.views.save_draft_ajax`

Draft table migration required:

- `sales/migrations/0013_draft.py`

---

## 7) Important Cross-App Dependencies

- `sales.Order.shift` references `financial.DailyShift`
- `sales.Order.customer` references `crm.Customer`
- `sales.Order.warehouse` and `sales.Draft.warehouse` reference `products.Warehouse`
- POS and stock updates depend on `products` stock/transaction models
- System settings injected globally to templates through context processor
- Auth and onboarding middleware affect access to all secured views

---

## 8) Local Development Setup

## Requirements

- Python 3.10+
- Virtual environment

## Commands

```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```

If new migrations are added:

```bash
python manage.py makemigrations
python manage.py migrate
```

---

## 9) Deployment Notes (Current Repository Style)

- WSGI app is available under `textile_pos.wsgi`
- `waitress_server.py` exists for Waitress-based serving
- `run_production.bat` and `setup_prod.sh` indicate mixed Windows/Linux deployment support
- `STATIC_ROOT = staticfiles` is set; run collectstatic in production:

```bash
python manage.py collectstatic --noinput
```

---

## 10) Logging, Errors, and Reliability

- System errors can be captured and reviewed via `accounts.SystemError`
- JS client errors can be sent to `/accounts/api/log-js-error/`
- Activity logs and IP history exist for operational auditing
- Recommendation: wrap JSON APIs with defensive error JSON output to avoid HTML-response parsing issues in frontend fetch calls

---

## 11) Testing and Validation Suggestions

Current repo includes lightweight scripts (`test_500.py`, `test_accounts.py`, `test_bug1.py`) but not a full structured test suite.

Recommended additions:

- Unit tests for critical services:
  - stock deduction/validation
  - draft save/retrieve/delete
  - shift open/close rules
- Integration tests for POS checkout and refund flow
- Regression tests for migration-dependent features (e.g., drafts)

---

## 12) Maintenance Checklist

- Apply migrations after pulling updates.
- Keep `requirements.txt` synchronized with runtime environment.
- Review unresolved `SystemError` entries regularly.
- Back up `db.sqlite3` before schema or deployment changes.
- Validate financial closing workflows at end of each shift/day.

