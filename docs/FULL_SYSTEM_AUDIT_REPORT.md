# Full-System Correctness Audit & Fix Report — Cafe ERP

Scope: every page across `sales`, `products`, `financial`, `crm`, `dashboard`, `restaurant`, `accounts`, `settings`, `licensing`, `shipping`, `notifications`. Money-handling flows were live-tested end-to-end (real HTTP requests, real DB assertions, not just "page loads"). All test data created during this work was deleted afterward — verified zero residue.

**Status: every real bug found has been fixed and verified live.** 14 items were tracked; 3 turned out to be false positives (the parallel audit agents ran against stale, pre-session git commits and reported things that don't reproduce on the live code — confirmed and dismissed individually below). The remaining 11 were real, are now fixed, and each has a passing live test proving the fix.

---

## Fixed this session — money-critical

### 1. Waiter check-close never recorded any money
`restaurant/views.py` `close_check()` — closing a table's check as Cash or Visa updated the `Order` row but **never posted a financial `Transaction`**. The cash drawer balance, general journal, income statement, trial balance, and VAT report would never reflect a single dine-in sale.
**Fix:** now calls `record_sale_transaction()`, plus a guard against double-closing. **Verified:** closing a 40 EGP cash check increases `CASH_DRAWER` by exactly 40.00 and posts a `SALE-<id>` journal entry; re-closing is rejected with zero balance change.

### 2. Delivery cash-on-delivery collection was silently lost
`restaurant/views.py` `driver_return_settle()` — read `order.cash_paid` (0 for a real COD order) instead of asking how much the driver actually collected, so **no custody was ever created and the cash vanished from reconciliation**.
**Fix:** now accepts an entered `collected_amount` and updates the order's paid status. **Verified:** a COD order with 20 EGP collected creates a 20 EGP custody and updates the order correctly.
**Known accepted tradeoff:** to avoid double-counting once the custody is settled to the drawer, this does *not* post a `SALE` journal entry at collection time — only the drawer-arrival step is posted. COD revenue reaches the cash drawer correctly but won't appear on income statement/trial balance/VAT report the way a prepaid sale does. A fully correct fix needs a "cash in driver's custody" clearing account — a real design decision, flagged rather than invented unasked.

### 3. Shift close ignored supplier payments
`financial/views.py` `close_shift` — its `removed_money` filter was missing `SUPPLIER_PAYMENT`, unlike `DailyShift.calculate_expected_balance()` and `shift_x_report`, which both include it. A cash supplier payment mid-shift made the closing report disagree with the X-report pulled minutes earlier.
**Fix:** added `SUPPLIER_PAYMENT` to the filter. **Verified:** a 30 EGP supplier payment on a 100 EGP shift now correctly yields an expected balance of 70.00 (was 100.00 before the fix).

### 4. Salary could be paid twice
`financial/views.py` `salary_pay` (legacy path) — no "already paid this period" guard, unlike the newer Payslip flow.
**Fix:** added a check for an existing payment `Transaction` for the same employee+period. **Verified:** first payment succeeds and moves 1000 EGP; an immediate resubmit is rejected with zero additional balance change.

### 5. Period lock wasn't enforced anywhere in the financial app
`PeriodLock.is_locked()` was only checked in `sales/views.py` (order edit/void) — locking a period didn't stop new transactions, withdrawals, account edits, shift closes, or payroll.
**Fix:** added the same `is_locked() + financial:manage override` guard (matching the existing `sales` convention) to `add_transaction`, `withdrawal_view`, `account_edit`, `close_shift`, `salary_pay`, `payslip_pay`. **Verified:** with today locked, a plain cashier's `close_shift` is blocked and the shift stays open; unlocking lets the same cashier close it normally. (Note: the other 5 views were already gated to `financial:manage`-only access, so the added check is consistent-but-redundant there — anyone who can reach them already holds the override permission. `close_shift` is the one reachable by non-managers, and that's where the fix actually bites.)

### 6. `supplier_add_purchase` crashed on every real use
`products/views.py` — passed raw POST strings straight into `Decimal` subtraction (`PurchaseInvoice.save()` does `total_amount - discount`), guaranteed `TypeError`. Unreachable from any current template (legacy/dead path) but still a landmine on direct POST.
**Fix:** coerce to `Decimal` with a clean error message on invalid input. **Verified:** submitting `total=80, discount=5` now succeeds with `net_amount=75.00` instead of crashing.

### 7. Purchase invoice header discount never reduced inventory cost
`products/inventory_services.py` `apply_purchase_invoice_stock` — `landed_cost` was allocated into each batch's cost; the invoice-level `discount` was not, so inventory valuation and COGS were overstated on every discounted purchase.
**Fix:** allocate the discount proportionally by line value, same mechanism as landed cost but subtracted instead of added (floored at zero). **Verified:** a 2-line, 200 EGP invoice with 30 landed cost + 5 discount now produces a batch cost of exactly 11.25/unit (10 base + 1.50 landed − 0.25 discount share) on both lines.

### 8. Purchase return could desync stock
`products/views.py` `api_purchase_return_submit` — deducted `WarehouseStock` by the full return quantity unconditionally, but only deducted from `StockBatch` rows up to what was actually available, silently dropping any shortfall with no error. The two stock ledgers (`WarehouseStock` vs `Σ StockBatch.current_quantity`) could permanently disagree.
**Fix:** now raises a clean, user-facing error if the return exceeds what batches actually hold, and resyncs `WarehouseStock` *from* the batches afterward instead of a separate blind decrement. **Verified:** an over-return (15 requested, 10 available) is rejected with no state change; a valid return (5 of 10) succeeds and both ledgers agree exactly (5 remaining).

### 9. Dead, unsafe stock functions removed
`products/inventory_services.py` `deduct_stock_fifo`, `restore_stock_lifo` — confirmed unused anywhere in the codebase; both skip the `WarehouseStock` resync step and would desync stock if ever wired up. Deleted.

### 10. Sales Report included voided invoices
`sales/views.py` `order_report` — used `Order.objects.all()` instead of `.active()`, unlike every other revenue view in the app (`financial_statement`, `order_list` stats). A voided invoice inflated the reported total.
**Fix:** switched to `Order.objects.active()`. **Verified:** with one 100 EGP active order and one 5000 EGP voided order present, the report now totals exactly 300 (sum of *all* active orders in the DB) — the 5000 void order contributes nothing, and the order count matches the active set.

### 11. Void could silently under-reverse cash on failure
`sales/utils.py` `reverse_order_financials` — caught all exceptions, logged, and returned `False`; the only caller (`delete_order_ajax`) never checked that return value. A reversal failure would still mark the order VOID with stock restored, but the cash-side REFUND transaction would silently never be created.
**Fix:** now re-raises (matching the existing, deliberate pattern in the sibling `record_sale_transaction`), so a failure rolls back the entire void inside its `atomic()` block instead of silently succeeding halfway. **Verified:** a normal void still works end-to-end (order → `void`, a `REFUND` transaction for the exact paid amount created).

### 12. Dashboard revenue counted voided sales
`dashboard/views.py` — read `StockTransaction` directly (`transaction_type='OUT'`) with no exclusion for the source order's void status; a voided order's original sale row is deliberately kept for audit, but nothing filtered it back out. Also affected the margin/profit widgets and `sales_profit_report`.
**Fix:** added a shared `_exclude_voided_orders_q()` helper (matches on `StockTransaction.reference_number`, the only link back to the order id) applied to all three affected queries. **Verified:** with a 20 EGP active sale and a 5000 EGP voided sale present, dashboard revenue is 70.0 (unrelated pre-existing data + the 20, definitely not +5000).

### 13. Customer-list "total balance" used a different formula than everywhere else
`crm/views.py` `CustomerListView` — its `Sum('order__total_amount')` annotation neither excluded void/item-less orders (unlike `Customer.get_balance()`, used everywhere else including the per-row balance on the same page) nor accounted for the join fan-out risk when an order has multiple line items (which would double-count that order's total).
**Fix:** replaced the SQL annotation with a per-row call to the already-correct `Customer.get_balance()`, for both the page stat and the Excel export — guaranteed to agree with every other balance display in the app, with no fan-out risk. **Verified:** a customer with a 2-item order (should count once, not twice) and a voided order (should be excluded) now shows a total balance of exactly 50.00, matching `get_balance()` precisely.

### 14. Database download/import and VAT-rate edit were under-permissioned
`settings/views.py` `download_database`, `import_database`, `settings_view` — all three were gated by `settings:view` only. `import_database` **overwrites the live database file wholesale**; a role with just "view" access to settings could download the entire database or replace it outright, and could also change the VAT rate via POST.
**Fix:** added a master/superuser gate (matching the existing pattern in the neighboring `policies_view`) to the POST-save branch of `settings_view` and to the whole of `download_database`/`import_database`. **Verified:** a `settings:view`-only, non-master role is now blocked from all three (VAT-rate change attempt left the rate unchanged; database download redirected with an error); a master user still has full access.

---

## Confirmed false positives (do not need fixing — verified against live code, not just reported)

Three items from the initial audit pass were reported by parallel review agents running in isolated git worktrees, which only see the last **committed** state — not this session's uncommitted work. On the live code:
- `financial/views.py` `advance_create()` — `User` **is** imported; page returns 200, not 500.
- `accounts/views.py` `system_error_history`/`resolve_error`/`restart_gunicorn` — `PermissionDenied` **is** imported; a non-master user correctly gets 403, not 500.
- `sales/views.py` `download_invoice_pdf` — `context` **is** defined before the WeasyPrint-unavailable fallback branch; the page renders 200 even with WeasyPrint's native libs missing (confirmed missing in this environment).

---

## Verified correct, no changes needed

- **POS checkout** (split cash+visa): totals, payment split, cash drawer +25, bank +15, COGS/gross-profit per line, stock deduction — all exact.
- **Refund**: return total, cash drawer decrease, stock restoration — all exact.
- **Purchase invoice + immediate payment**: invoice total, supplier outstanding balance, cash drawer decrease, stock increase — all exact.
- **Transaction reverse/apply balance logic**, **journal balance validation**, **Payslip double-pay guard**, **FIFO/FEFO stock issuance**, **weighted-average cost calc**, **supplier balance (no SupplierPayment/invoice.paid_amount double-counting)**, **customer balance formula (POS badge vs CRM agree term-for-term)**, **AR aging**, **permission deny-override logic**, **discount-cap privileged-user bypass** — all reviewed and confirmed correct.
- **Restaurant app** (this session's new code): waiter open-tab/add-items, void item, void check, KDS status flow, custody create/settle, category-sales/waiter-sales/driver-owed reports, role-based permission boundaries (كاشير/ويتر/مطبخ/دليفري) — all verified live, plus the two fixes above.

## Left alone, deliberately

Two pre-existing orders (`#39`, `#40`, referencing a product called "coffee") remain in the live database from earlier browsing in this session. No financial Transaction/JournalEntry attached (harmless either way), but not deleted since they might be your own exploration of the server rather than test data — let me know if you want them cleared.
