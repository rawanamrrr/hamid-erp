# MASTER PLAN — Wholesale POS v4 → Full Market/ERP System

Goal: evolve v4 from a single-shop POS into a hardened, accounting-correct, multi-market ERP/POS
that exceeds legacy systems (SKY SOFT used only as a baseline reference).

> **Progress log — `erp-hardening` branch:**
> **Phase 0 COMPLETE** — 0.1 git baseline · 0.2 env settings + prod security · 0.3 migration
> chain repaired (fresh-replay clean) + Postgres cutover guide · 0.4 invariant tests · 0.5
> automated backups (`backup_db`) · 0.6 500-error alerting.
> **Phase 1 COMPLETE** — 1.1 double-deduction · 1.2 Transaction→document FKs (incl. debt-collection)
> · 1.3/1.5/1.6 returns+credit+validation · 1.7 order.warehouse · 1.9 COGS profit · 1.10 VOID+revisions
> · 1.11 stock cache sync · 1.12 fail-loud financials · 1.14 FEFO expiry guard.
> **Phase 2 (partial)** — 2.1 central inventory service · 2.2 shared OrderService (create/edit).
> **Phase 3 (partial)** — 3.1 RBAC across sales/financial/crm/shipping + server-side warehouse ·
> 3.2 per-user operational limits (max discount %, sell-below-cost, price-edit; enforced at checkout)
> · 3.5 invoice visibility scoping (cashiers see own; managers see all) · 3.6 Gmail secret from
> env · 3.8 searchable audit-log browser.
> **Phase 4 (core in progress)** — 4.2 real double-entry posting engine (`financial/posting.py`:
> post_sale/post_refund/post_cash_transaction, balanced & idempotent, replaces broken stub) ·
> 4.6 trial balance + income statement from the journal (`financial/reports.py` + views) ·
> 4.3 customer & supplier account statements / كشف حساب (`crm/statements.py`,
> `products/statements.py` + printable views; closing == get_balance/outstanding_balance) ·
> `rebuild_journal` command (verified BALANCED on dev data). Fixed strip-COGS overstatement.
> Data repaired: 6 divergent stock pairs reconciled, negative stock clamped, 55 txn links backfilled,
> journal rebuilt clean.
> · 4.5 period/day close (`PeriodLock`: blocks editing/voiding documents in a closed period).
> · 4.4 vouchers (receipt RV- / payment PV- numbers on customer & supplier payments).
> **Phase 6 (started)** — 6.1 gap-free document numbering (`DocumentSequence`; orders now
> numbered INV-YYYY-NNNNN, 68 backfilled; shown on list + printed invoice) · 6.4 sales returns
> as first-class document (RET- number, structured reason, returns register) · 6.2 quotations
> (QUO- document, status workflow, printable) · 6.6 quantity-break
> pricing (ProductPriceBreak + resolve_price) · 6.9 credit-limit
> enforcement at checkout (block over-limit credit sales; manager override).
> · 4.10 cash-drawer X report (mid-shift snapshot) + fixed expected-balance to subtract cash refunds
> · 4.8 VAT report (output VAT from VAT-inclusive sales − input VAT from purchases; configurable rate).
> **Phase 5 (started)** — 5.2 item movement card (حركة الصنف, running balance) · 5.7 low-stock /
> reorder report (grouped by supplier, suggested qty + cost).
> **Phase 8 (started)** — 8.1 AR aging report (FIFO-allocated buckets current/31-60/61-90/90+,
> reconcile to get_balance) · 8.2 payment allocation (receipts applied to oldest invoices,
> Order.outstanding, persisted PaymentAllocation) · 8.5 WhatsApp payment-reminder links ·
> 8.6 richer customer data (phone2, tax number, blacklist + credit guard at checkout).
> **Phase 7 (started)** — 7.2 landed costs (freight/customs allocated into batch cost) ·
> 7.4 supplier payables aging (FIFO, mirrors AR) · 7.5 supplier price comparison.
> **Phase 9 (more)** — 9.1 daily summary · 9.3 profitability (by product/category, margin) ·
> 9.4 consolidated financial position (cash/AR/AP/inventory/expenses-by-category) ·
> 9.5 voided register · 9.6 sales analytics (best sellers, by hour/weekday/cashier/payment) ·
> 9.8 CSV/Excel export on key reports · 9.9 system-health page (live invariants).
> **Phase 5 (more)** — 5.1 stocktake/جرد (count→variance→apply via ADJ service) ·
> 5.11 stock valuation · 5.6 near-expiry report.
> **Phase 3 (more)** — 3.6 Gmail secret read from env.
> **Phase 10 (started)** — 10.2 e-invoicing readiness (Product.egs_code + ETA-style invoice
> JSON export per order; pluggable submission later) · 10.5 inventory insights (sales velocity,
> days-of-cover, reorder urgency, dead-stock detection).
> **Phase 2.6b** — all frontend assets self-hosted (Tailwind/FA/fonts/charts in static/vendor);
> UI works with zero internet (CDN outage previously left every page unstyled).
> Tests: **35 green** (`run_tests.bat`). Also fixed a timezone off-by-one in the daily report. Caught+fixed real bugs (get_balance Decimal/float;
> non-idempotent migration 0008; duplicate financial migrations 0006/0009; legacy received-amount
> & overpaid orders in posting).
> **NEXT:** 4.4 vouchers · 4.7 post-dated cheques · 4.8 VAT · 4.10 drawer X/Z reports ·
> 6.2 quotations · 6.4 sales-returns rework · 6.6 price lists.
> Also open: 2.4 integer stock units · 2.5 move DealDiscount out of payroll · 2.6 images→files ·
> 2.7 csrf_exempt removal · 2.9 background jobs · 3.2–3.8 (op flags/policy/approvals) · Phases 5–11.

Rules that govern every phase:
1. **Nothing ships without tests for money/stock invariants** (see Phase 0.4).
2. **Documents are immutable** — no hard deletes of invoices/transactions; reversals only.
3. **One source of truth per ledger** — stock derives from batches; money derives from journal lines.
4. **All business logic in services, not views.** Views parse/validate/respond only.
5. **Every permission decision is data, not code** — driven by the policy/permission tables.

---

## PHASE 0 — Foundation & Safety Net (prerequisite for everything)

| # | Task | Notes |
|---|------|-------|
| 0.1 | Initialize git repo, `.gitignore` (venv, db.sqlite3, *.exe, *.dll, staticfiles, __pycache__), remove junk files (test_*.py in root, dump.html, SumatraPDF installer, tmp_*) | First commit = current state, before any fix |
| 0.2 | Settings split: `settings/base.py`, `dev.py`, `prod.py`; SECRET_KEY + DB + email creds from environment (`django-environ`); `DEBUG=False` in prod; SECURE_* / SESSION_COOKIE_SECURE / CSRF_COOKIE_SECURE | Production currently runs DEBUG=True on a public domain |
| 0.3 | Migrate SQLite → **PostgreSQL** (dev + prod). Write a verified data-migration script (dumpdata/loaddata with integrity checks) | SQLite will deadlock under multi-cashier writes |
| 0.4 | Test harness: pytest-django + factory_boy. Write **invariant tests first**: (a) Σ StockBatch.current_quantity == WarehouseStock.quantity per product/warehouse; (b) Account.balance == Σ journal lines; (c) order payments == Σ linked transactions; (d) customer ledger balance == orders − payments − returns + opening | These tests will FAIL today — they define "fixed" |
| 0.5 | Automated nightly DB backup (pg_dump → dated file → off-server copy) + documented restore procedure + weekly restore test | Currently zero disaster recovery |
| 0.6 | Error monitoring: keep SystemError model but add admin alerting (email/Telegram) on new 500s | Foundation exists already |

**Exit criteria:** repo under git, prod config hardened, Postgres live, invariant tests written (red), backups proven restorable.

---

## PHASE 1 — Critical Bug Fixes (data corruption — do immediately after Phase 0)

| # | Bug | Fix |
|---|-----|-----|
| 1.1 | Double stock deduction in no-batch fallback (`submit_order_ajax`) | Single service `deduct_stock(product, warehouse, qty)` that updates batches AND WarehouseStock atomically in one place; delete the duplicated block |
| 1.2 | Substring-matched transaction reversal corrupts other orders (`edit_order_ajax`) | Add `Transaction.order = FK(sales.Order, null=True)` (+ `return_invoice`, `expense`, `customer_payment`, `supplier_payment` FKs or a GenericFK). Data migration: parse legacy descriptions ONCE to backfill. Forbid description matching forever |
| 1.3 | Customer returns don't reduce customer debt + always pay cash | `ReturnInvoice.refund_method` ('cash' / 'customer_credit') like PurchaseReturn; include returns in balance calculation; when original order was unpaid credit → default to credit, require permission for cash payout |
| 1.4 | Refunds bypass StockBatch ledger | Returns restore via batch service (restore to original batch if known via `ReturnItem.batch` FK, else newest); never touch WarehouseStock directly |
| 1.5 | No server-side return validation | Enforce: returned qty ≤ sold qty − already returned (per OrderItem link); refund price ≤ original price unless permission |
| 1.6 | `credit_paid` recorded as new SALE revenue into invalid account type | credit_paid creates a customer-ledger entry only (debt consumption), never a drawer/SALE transaction |
| 1.7 | `Order.warehouse` never set at checkout | Set it; remove all warehouse-guessing from delete/edit paths |
| 1.8 | Shift expected-balance counts only `shift.employee`'s orders while shift is global | Decision: make shifts **per-user** (each cashier opens/closes own session) + optional register concept. Expected balance from `order.shift` FK, not user+time-window |
| 1.9 | net_profit ignores COGS; orders_debt ignores credit_paid | Recompute from OrderItem.cost_price (already stored); fix debt formula |
| 1.10 | Order edit/delete destroys history | Replace hard delete with `status='VOID'` + reversing stock/financial documents; edits create an `OrderRevision` snapshot (before/after JSON) and increment `revision_no` printed on invoice |
| 1.11 | Stale `Product.stock_quantity` cache | Recalc inside the single stock service (1.1) or drop the cached field and annotate queries |
| 1.12 | Swallowed exceptions in `record_sale_transaction` | Raise inside the atomic block — a sale must not commit without its financial record |
| 1.13 | `Draft` save references nonexistent `updated_at` | Add the field |
| 1.14 | Expired batches sold first by FIFO | FEFO with expiry guard: skip/block expired batches; setting decides block vs warn |
| 1.15 | Delete `fix_transactions.py` / `fix_audit_transactions.py` after 1.2 lands | Their existence = symptom |

**Exit criteria:** all Phase 0.4 invariant tests green; manual QA script (sell→return→edit→void across cash/credit/split) leaves ledgers balanced.

---

## PHASE 2 — Architecture Refactor (make correctness structural)

| # | Task |
|---|------|
| 2.1 | **Inventory service module** (`inventory/services.py`): `receive()`, `issue()`, `transfer()`, `adjust()`, `reserve()/release()`. Batches are the only mutable store; WarehouseStock becomes a maintained denormalization updated only inside these functions. All callers (POS, purchases, returns, transfers, manufacturing, stocktake) go through it |
| 2.2 | **OrderService**: `create_order()`, `edit_order()`, `void_order()` shared by POS web/mobile/API. Kill the duplicated logic between submit/edit views |
| 2.3 | Replace `Transaction.save()` balance mutation with a **posting engine** (see Phase 4); until then, centralize in one `post_transaction()` service |
| 2.4 | Store stock in **smallest unit as integers** (strips/pieces); boxes derived. Migration converts existing fractional quantities |
| 2.5 | Move `DealDiscount` out of `financial/payroll_models.py` into `sales`; split payroll into its own app |
| 2.6 | ProductImage / logo: base64 TEXT → ImageField files + thumbnails |
| 2.7 | Remove `@csrf_exempt`; send CSRF token from POS JS. Standardize AJAX responses (status codes, error envelope) |
| 2.8 | Introduce DRF for all POS/AJAX endpoints (serializers = single validation point for web POS, mobile POS, future apps) |
| 2.9 | Background jobs (Celery + Redis, or django-q2 if simpler ops): notification fan-out, emails, nightly integrity checker (runs invariant queries, alerts on drift), backups |

---

## PHASE 3 — Security, Permissions & Policy Engine

| # | Task |
|---|------|
| 3.1 | Apply `require_permission` to **every** view in sales, financial, crm, shipping, notifications (currently zero). Module/action matrix documented in `docs/PERMISSIONS.md` |
| 3.2 | **Operational permission flags** (the SKY SOFT per-user grid, as data): sell below cost / below last purchase / below price tier; max discount % per user; sell with zero/negative stock; edit own invoice (time-boxed, e.g. 15 min) / edit any; void invoice; backdate documents; view cost & profit; return without original invoice; reprint invoice; open drawer without sale; change prices; access reports. UI: per-role grid + per-user override grid |
| 3.3 | **Policy/Settings engine** ("system constants"): `Policy(key, scope: global/branch/user, value JSON)` with typed registry + admin UI grouped by tabs. Replaces hard-coded behavior branches. Examples: require customer on credit sale, auto-print after save, default price tier, allow price edit at POS, FEFO block vs warn, rounding rule |
| 3.4 | **Approval workflow engine**: `ApprovalRequest(action_type, payload, requested_by, status, decided_by)`. Wire to: refund > X, void invoice, debt write-off, stock adjustment > X, price change > Y%, customer credit-limit override. Manager gets notification; action executes on approval |
| 3.5 | Object-level enforcement: warehouse restriction enforced **server-side** in checkout/transfer; invoice visibility scoping (cashier sees own, manager sees branch) |
| 3.6 | Secrets: encrypt Gmail app password at rest (or move to env); mask in UI |
| 3.7 | Session hardening: per-user concurrent-session limit option, re-auth (PIN) for sensitive POS actions (void, refund, drawer open), failed-login lockout |
| 3.8 | Surface the existing audit log: searchable browser UI (filter by user/module/action/date), plus auto-logging middleware for all POST/PUT/DELETE on financial modules |

---

## PHASE 4 — Accounting Core (real double-entry)

| # | Task |
|---|------|
| 4.1 | **Chart of accounts** seeded properly: Assets (cash drawers per register, banks, wallets, AR, inventory), Liabilities (AP, VAT payable, post-dated cheques), Equity (capital, drawings per partner), Revenue (sales, other income), Expenses (COGS, categorized opex). Account tree UI |
| 4.2 | **Posting engine**: every business event emits one balanced JournalEntry via declarative posting rules. Cash sale: Dr Cash / Cr Sales, Dr COGS / Cr Inventory. Credit sale: Dr AR. Return, purchase, payment, expense, transfer, payroll — all mapped. `Account.balance` becomes a derived/cached value verified nightly |
| 4.3 | **Customer & Supplier subledgers**: append-only ledger rows (document type, ref, debit, credit, running balance). Replace `get_balance()` aggregations. Statement of account (كشف حساب) printable per customer/supplier per period |
| 4.4 | **Vouchers as documents**: receipt voucher (from customer / general), payment voucher (supplier / expense / partner), each with its own numbered sequence, print template, ledger + journal posting. Link `CustomerPayment`/`SupplierPayment` into this |
| 4.5 | **Period management**: financial periods, day-close (locks edits before today w/o permission), month-close, year-close with retained earnings roll |
| 4.6 | **Financial statements**: trial balance, P&L (with COGS), balance sheet, cash-flow summary — all derived from journals, by period/branch |
| 4.7 | **Post-dated cheques** module: incoming/outgoing register, due-date alerts, deposit/clear/bounce state machine with journal postings |
| 4.8 | **VAT**: tax codes table, tax on sales lines (inclusive/exclusive per policy), tax report by period; purchases already have per-line tax — unify |
| 4.9 | Multi-currency (phase-light): currency table + rate per document on purchases/bank accounts; reports in base currency |
| 4.10 | Drawer management: denominations count at open/close, X report (mid-shift), Z report (close), cash drop to safe, drawer-open audit events |

---

## PHASE 5 — Inventory Completeness

| # | Task |
|---|------|
| 5.1 | **Stocktake module**: count sessions per warehouse (full/partial/by category), count sheets (blind mode policy), variance report, approval (Phase 3.4), posts ADJ via inventory service. Freeze-or-track movements during count |
| 5.2 | **Item card (حركة الصنف)**: per product+warehouse: opening, every movement, running balance, cost, reference doc link. Export/print |
| 5.3 | **Multi-unit hierarchy**: ProductUnit table (unit, factor, barcode, price tier overrides, default-for-sale/purchase flags) → piece/box/carton each with own barcode + prices. Replaces single sub_unit + pieces_per_package |
| 5.4 | **Multi-barcode**: many barcodes per product-unit; scale barcode support (prefix + embedded weight/price parsing for grocery) |
| 5.5 | **Barcode/label printing**: label designer-lite (name, price, barcode), batch print from purchase invoice or selection |
| 5.6 | Expiry: near-expiry report (30/60/90), expired-stock quarantine action, FEFO policy (done in 1.14) |
| 5.7 | **Reorder management**: min/max/reorder-point per product(+warehouse), shortage report (تذييل النواقص), one-click draft PO grouped by primary supplier |
| 5.8 | Two-step **stock transfer** (dispatched → in-transit → received w/ discrepancy handling) + transfer document printing |
| 5.9 | **Serial-number tracking** (electronics): serials captured on receive, picked at sale, warranty lookup by serial |
| 5.10 | **BOM / bundles**: bill of materials, assembly order (consume components → produce finished via existing MFG transaction types), sellable bundle kits (explode at sale) |
| 5.11 | Stagnant-stock report (no movement N days), stock valuation report (qty × batch cost) reconciling to the inventory GL account |

---

## PHASE 6 — Sales Cycle Completeness

| # | Task |
|---|------|
| 6.1 | **Document numbering service**: per type + per branch + per year sequences (INV-2026-00001, RET-, QUO-, SO-, PO-, RV-, PV-), gap-free, printed everywhere instead of DB pk |
| 6.2 | **Quotations** (customer + from-supplier capture): validity date, convert→sale order/invoice, status tracking |
| 6.3 | **Sale orders & reservation orders**: reserve stock (inventory service `reserve()`) without deduction; expiry of reservations; convert to invoice with deposit handling (عربون → customer ledger credit) |
| 6.4 | **Sales returns rework**: first-class document linked line-by-line to original invoice (qty caps from 1.5), own number sequence, reasons list, exchange flow (return + new sale in one screen) |
| 6.5 | **Server-side pricing**: price resolved from customer's tier + active price list; client price accepted only within permission bounds (3.2). Line-level discount persisted on OrderItem |
| 6.6 | **Price lists & quantity breaks**: named price lists with effective dates; qty-break rules (1–9 → A, 10+ → B) — wholesale essential |
| 6.7 | Promotions: server-side validation/application of buy_x_get_y and n-for-price (free items computed server-side); usage limits, per-customer-type/branch scoping, usage counters |
| 6.8 | POS UX for control: customer panel (balance, credit limit, last 5 prices for scanned item), salesman field separate from cashier, invoice notes per line, reprint counter on receipt, "last edited at" on invoice |
| 6.9 | Credit control at checkout: block/warn when balance + new debt > credit_limit (policy + override permission + approval option) |
| 6.10 | Tailoring/production orders: proper status pipeline with dates and notification on due — generalize to "service orders" so other market types can use it |

---

## PHASE 7 — Purchasing & Suppliers

| # | Task |
|---|------|
| 7.1 | PO → purchase invoice conversion with over/under-receipt control; PO approval threshold |
| 7.2 | **Landed costs**: freight/customs/other allocated across invoice lines into batch cost |
| 7.3 | On-receipt price update prompt: cost changed → recalculate sale prices by stored margin %, with notification (notify_price_changes exists — wire it) |
| 7.4 | Supplier invoice due dates → payables schedule, payment-due alerts, supplier aging |
| 7.5 | Supplier price comparison screen (SupplierProduct data exists — build the UI) + RFQ-lite |
| 7.6 | Reconcile `PurchaseInvoice.paid_amount` vs `SupplierPayment` into the supplier subledger (single truth, remove the double-count comment hack) |

---

## PHASE 8 — CRM, AR & Loyalty

| # | Task |
|---|------|
| 8.1 | AR **aging report** (current/30/60/90+), collection worklist sorted by overdue, credit-hold flag auto-set by policy |
| 8.2 | Payment allocation: customer payments applied to specific invoices (FIFO auto or manual pick) — enables accurate aging |
| 8.3 | Tier system: configurable thresholds, tie tiers to price lists/discounts automatically |
| 8.4 | **Loyalty points** (beyond SKY SOFT): earn rate per policy, redeem as payment method (posts via journal), expiry |
| 8.5 | Customer communication: WhatsApp/SMS templates — invoice link, payment reminder, statement; opt-in flags |
| 8.6 | Customer data: multiple phones/addresses, tax registration number (B2B e-invoice), birthday promos, blacklist flag |

---

## PHASE 9 — Reports, Dashboards & Monitoring

| # | Task |
|---|------|
| 9.1 | **Daily movement summary** (ملخص الحركة اليومية): sales, returns, purchases, vouchers, drawer ins/outs, net — one printable page per day/branch |
| 9.2 | Cashier session (X/Z) reports; cash drawer day sheet |
| 9.3 | Profitability: per invoice / product / category / cashier / customer / period (COGS-based), gated by view-profit permission |
| 9.4 | Lists with balances: customers, suppliers, partners, expense categories |
| 9.5 | Deleted/voided/edited invoice register (persisted, browsable, filterable) |
| 9.6 | Sales analytics: by hour heatmap, by category, best/worst sellers, basket analysis-lite (top co-purchased pairs) |
| 9.7 | Dashboard rebuild with drill-down (click KPI → underlying documents), per-role dashboards (owner vs manager vs cashier) |
| 9.8 | Report engine conventions: every report = filter set + HTML + print CSS + Excel export (openpyxl) + saved filter presets |
| 9.9 | Ops monitoring: integrity-checker results page, backup status, license status, error log triage |

---

## PHASE 10 — Beyond Legacy Systems (modern differentiators)

| # | Task |
|---|------|
| 10.1 | **Offline-tolerant POS**: PWA service worker — queue sales locally when network drops, sync with conflict rules (stock re-check on sync, flag conflicts for review). The single biggest practical win over web-only competitors |
| 10.2 | **E-invoicing readiness (Egypt ETA)**: tax IDs, item GS1/EGS coding fields, invoice JSON export now; pluggable submission adapter later. Legal requirement trajectory makes this strategic |
| 10.3 | Payment integrations: card terminals / Paymob / Fawry adapter interface; QR-pay on receipt |
| 10.4 | Hardware layer: customer display, cash-drawer kick via printer, scale integration, barcode scanner config page — abstract behind a local device-bridge service |
| 10.5 | **AI/analytics (you have the data)**: demand forecasting → smarter reorder quantities; dead-stock and price-elasticity hints; anomaly detection on cashier behavior (refund spikes, after-hours voids, drawer variance patterns) feeding the audit dashboard |
| 10.6 | Online storefront sync: expose products/stock via API; accept online orders into the existing is_online + Shipment pipeline; shipping-company COD settlement reconciliation |
| 10.7 | Owner mobile app / Telegram bot: daily Z-report push, big-transaction alerts, approval actions (approve refund from phone — pairs with 3.4) |
| 10.8 | Multi-language UI completion (ar/en toggle exists in profile — actually internationalize templates) |
| 10.9 | In-app help: contextual tooltips + the USER_GUIDE.md content surfaced per screen |

---

## PHASE 11 — Multi-Branch & SaaS Scale

| # | Task |
|---|------|
| 11.1 | **Branch model**: branch FK on warehouse/order/shift/sequence/policy; per-branch reports; inter-branch transfers (builds on 5.8) |
| 11.2 | Replace singleton SystemSetting with company + branch settings |
| 11.3 | Consolidated reporting across branches; branch P&L via cost centers (account dimension added in 4.x) |
| 11.4 | Multi-tenant SaaS option: tenant isolation strategy (schema-per-tenant on Postgres), provisioning, plan limits wired to existing `licensing` app |
| 11.5 | Performance: query audit (pos_view currently loops all products/customers in Python — paginate + search-as-you-type endpoints), caching, read replicas when needed |

---

## Sequencing & Dependencies

```
Phase 0 ──► Phase 1 ──► Phase 2 ──► Phase 3 ──► Phase 4 ──► Phases 5/6/7 (parallel) ──► 8/9 ──► 10 ──► 11
   (week 1)   (weeks 1-3)  (weeks 3-6)  (weeks 6-8)  (weeks 8-12)        (months 3-5)      (5-6)  (6-8)  (8+)
```

- 1 (bugs) cannot wait; everything else is wasted if data keeps corrupting.
- 2 (services) before 3-7, or every new feature re-implements broken patterns.
- 4 (accounting) before vouchers/aging/statements/VAT — they all post through it.
- 5.3 (multi-unit) before 5.4/5.5 (barcodes hang off units).
- 6.1 (numbering) early in Phase 6 — every later document type needs it.
- 10.1 (offline POS) can start any time after 2.8 (API layer) exists.

## Definition of Done (system-level invariants, enforced by CI + nightly checker)

1. Σ batch quantities == warehouse stock == product total, always.
2. Every journal entry balanced; every account balance == Σ its lines.
3. Every order's payments == Σ its linked transactions; customer ledger == orders − payments − returns + opening.
4. No financial/stock document can be hard-deleted; every void has a reversing document.
5. Every money- or stock-mutating endpoint has a permission decorator and an audit log entry.
6. Backups restore successfully in the weekly drill.
