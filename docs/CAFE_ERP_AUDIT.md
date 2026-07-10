# Cafe ERP — Full System Audit

**Date:** 2026-07-09
**Scope:** Every app (`accounts`, `products`, `sales`, `restaurant`, `financial`, `crm`, `settings`, `notifications`, `licensing`, `shipping`, `dashboard`) reviewed for cafe-fitness, correctness, and publish-readiness.

**Verdict: not yet ready to publish as-is.** The core sale→kitchen→payment→books loop works, but there are several real bugs that will produce wrong inventory/financial numbers in production, plus the "textile → cafe" conversion described in `docs/CAFE_ERP_PLAN.md` is only partially finished — several textile-only screens and fields are still live, not just cosmetically ugly but in some cases actually reachable and functional when they shouldn't be for a cafe.

Findings are grouped by severity. File:line references point to the exact spot.

---

## 🔴 Critical — will produce wrong numbers, fix before publish

1. **Selling a menu item through the regular cashier/POS checkout never deducts recipe ingredients.**
   `sales/services.py`'s `issue_cart_items()` (the function behind every POS sale) only issues stock for the sold product itself and explicitly zeroes the oversold-shortfall for menu items — it has **no** `Recipe`/`RecipeItem` deduction logic at all. Only the **waiter** flow (`restaurant/views.py` `waiter_open_or_append`, lines ~251-262) actually deducts ingredients via the recipe. Since today's fix made cashier-rung menu items correctly reach the kitchen, a cashier can now sell 50 lattes with zero milk ever leaving stock, while the same drink sold via the waiter screen correctly depletes milk. **This is the single most important fix** — ingredient stock accuracy depends on it.

2. **COD delivery revenue never posts to the general ledger.**
   `restaurant/views.py:648-669` (`driver_return_settle`) deliberately skips `record_sale_transaction()`/journal posting when a driver returns with cash — it's only posted later via `CashCustody.settle()`. This is self-documented in a code comment, but it means income statement, trial balance, and VAT report all **undercount delivery sales** until (and unless) that custody gets settled. Confirmed by the financial audit as a live, not just theoretical, gap.

3. **Low-stock badge counter is inconsistent with the low-stock page itself.**
   `products/views.py` `api_stock_alerts_count` (~line 3372) does **not** exclude `category__is_menu_category`, while `stock_alerts` (the actual page it links to, ~line 3943) does. The bell/badge will show a nonzero count driven by menu items with fake zero stock, but clicking through shows nothing wrong — confusing and will generate support complaints.

4. **Purchase orders can still add menu items.**
   `purchase_order_create` (`products/views.py:4045`) builds its product picker from `Product.objects.filter(is_active=True)` with no `is_menu_category` exclusion (unlike `purchase_invoice_create`, which correctly excludes them). A manager can accidentally order "spanish latte" from a supplier.

5. **Stocktake includes phantom-stock menu items.**
   `stocktake_create` (`products/views.py:4930`) snapshots **all** `WarehouseStock` rows with no menu-category exclusion, so every stocktake session is polluted with "شortage" lines for drinks that were never meant to carry stock.

---

## 🟠 Important — should fix soon, not launch-blocking

6. **No seeded roles.** `Role` has zero seed data anywhere in migrations, despite `docs/CAFE_ERP_PLAN.md` explicitly promising "seed roles (cashier/waiter/kitchen/driver/manager)" in Phase 1. Every fresh install starts with an empty permission matrix — a new owner has to hand-build cashier/waiter/kitchen roles from scratch before the system is usable. This is a rough first-run experience for a "ready to publish" product.

7. **The cafe market profile is half-wired.** `settings/market_profiles.py` does define a real `cafe` profile with a feature list, and it's consumed by `product_form.html` — but `templates/includes/sidebar.html` **never references the market profile at all**. Switching to "cafe" mode does not hide a single textile-only sidebar section (bulk/wholesale add, full PO submenu, etc.). The plan's "REMOVE/HIDE via market profile" intent for textile-only modules was never implemented at the navigation level.

8. **Tailoring machinery is fully live, not just hidden.** `sales/views.py` (`submit_order_ajax` lines ~1031-1264, plus standalone tailoring create/edit/status views) fully processes `is_tailoring`/`tailor_name`/`tailoring_status` — it's gated by template/permission visibility only, not disabled in code. Same for the fashion size/color variant picker still embedded in `templates/sales/pos.html` (~2396-2555). Cosmetically hidden ≠ actually removed; a cafe install can still hit these code paths.

9. **Dashboard shows a tailoring-cost widget.** `templates/dashboard.html:302` — "إجمالي تكلفة أوامر التفصيل" sits in the main revenue dashboard regardless of business type.

10. **Two separate, non-integrated delivery systems.** `shipping/` (ShippingCompany/Shipment/tracking-number, mail-order style) and `restaurant/`'s own driver/`CashCustody` delivery flow both exist and don't share code. For a cafe, `shipping/` is largely dead weight and a source of confusion (which "delivery" screen is authoritative?).

11. **CRM is still textile/wholesale-shaped.** `Customer.customer_type` = retail/semi_wholesale/wholesale; core CRM logic is credit-limit/aging/statements. No loyalty-points model exists despite `licensing/features.py` already registering a `loyalty` feature flag — the flag is declared but nothing implements it.

12. **`sales_analytics`/VAT report have no order-type breakdown.** `financial/reports.py` doesn't split by `Order.order_type` (dine_in/takeaway/delivery) even though the field exists — a cafe owner can't see revenue mix by channel from the financial reports.

---

## 🟡 Textile leftovers — cosmetic/confusing, not broken

- Product form (`templates/products/product_form.html`) unconditionally shows color/season/material/serial-number/warranty fields for every product — including a menu item like "Coffee."
- Product list shows a "sizes" and "material" column for all products.
- Sidebar still shows "إضافة أصناف بالجملة" (bulk wholesale add) and the full PO submenu unconditionally.
- `UnitOfMeasure` has create/delete but **no edit** — minor but odd gap.
- No PO-vs-invoice cost-variance tracking (partial receiving works; price variance doesn't get flagged).
- Notifications are generic (low-stock only) — no cafe-specific events (order-ready-for-pickup already has a live websocket toast, but that's separate from the persisted `Notification` model used for the bell icon).

---

## 🟢 Confirmed working correctly

- POS split payments, discounts, refunds, invoice PDF generation.
- Waiter table map, modifiers, notes, void item/check, close-check (cash/visa/CL).
- KDS + cashier incoming-orders queue — the `is_menu_category`-based grouping/filtering added this session is consistent and correct everywhere in `restaurant/views.py`.
- Recipe unit-conversion math (`compatible_units`/`base_quantity`) — correct wherever it's actually invoked (waiter flow only — see Critical #1).
- Raw material / regular product separation — consistently applied across POS, waiter menu, dashboard, product list.
- `record_sale_transaction`/`post_sale` double-entry posting — correctly wired into every path *except* the COD-delivery gap above.
- `CashCustody` settle flow — correctly posts to `CASH_DRAWER` with shift linkage.
- Login-landing (`default_landing`) — fully wired, no gaps found.
- Licensing gate — doesn't appear to block any cafe-critical screen.
- Website integration plan (`docs/WEBSITE_INTEGRATION_PLAN.md`) — confirmed **not built at all** (no `integrations` app exists); this was known, just confirming it's still just a plan, not a partially-broken implementation.

---

## Recommended fix order

1. Recipe deduction on cashier/POS checkout (Critical #1) — inventory-accuracy blocker.
2. The three `is_menu_category` exclusion gaps (#3, #4, #5) — same fix pattern repeated, quick to close.
3. COD delivery ledger posting (#2) — financial-accuracy blocker.
4. Seed default roles (#6) — first-run experience.
5. Sidebar market-profile gating (#7) — the biggest remaining "feels unfinished" item, but also the largest chunk of work.
6. Everything else, prioritized by what the owner actually plans to use (e.g. skip CRM loyalty/shipping consolidation if not needed yet).
