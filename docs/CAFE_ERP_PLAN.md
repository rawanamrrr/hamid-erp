# Cafe ERP — Conversion Plan (for review)

Converting the existing **textile_pos** retail ERP into a **Cafe/Restaurant ERP**.
Django + (new) Django Channels/ASGI. Arabic-first UI.

## Confirmed decisions
1. **Inventory:** Hybrid — recipes/BOM optional per menu item (some items deduct ingredients, some are simple).
2. **Data:** Wipe existing textile data, start fresh with cafe data (repo already has DB backups).
3. **Branches:** Multi-branch from day one (tables, drivers, KDS, reports all scoped per branch).
4. **Modifiers:** Yes — per-item options (size, extra shot, sugar level, add-ons) that adjust price and print to kitchen.
5. **Live updates:** Websockets via **Django Channels + Redis + ASGI** (replaces the current waitress/WSGI runner).
6. **Kitchen output:** KDS screen **and** per-station prep-ticket printers.
7. **Charges:** Service charge % + VAT. (No tips line.)

---

## KEEP (reuse largely as-is)
- **Financial core:** `DailyShift`, `Account` (drawer/safe/visa/instapay), `Transaction` (double-entry), `JournalEntry`, period lock, X-report, daily summary. → backbone for جرد / وديعة / cashier-safe.
- **Accounts:** roles, JSON permissions, per-user limits, **approval overrides** (reused for void authorization).
- **Order engine:** split payments (cash/wallet/instapay/visa/**credit**), **void lifecycle** + revisions, invoice numbering, shift linkage, `salesman_name` → waiter.
- **Category**, expenses, returns, reports, licensing, settings/policies engine, market-profile engine.

## REMOVE / HIDE (textile-only)
Tailoring & factory, size×color variants, wholesale/semi-wholesale tiers, pharmacy strips/FEFO, electronics serials, reservations, quotations, purchase-order machinery (kept minimal only for ingredient purchasing). Removed by: new **`cafe` market profile** that hides these fields/sections + sidebar/permission gating. Code kept in tree (reversible), not mass-deleted.

## Branch model
Reuse **`Warehouse` (is_sales_point=True)** as the branch/outlet — stock, tables, drivers, KDS all scope to it. Avoids a risky schema migration. Add a friendly "الفرع/Branch" label via the cafe profile.

---

## New app: `restaurant`
- `Section(name, branch)` — floor sections (indoor/outdoor…).
- `Table(number, section, branch, seats, status[free/busy/reserved])`.
- `KitchenStation(name, branch, printer_target)` — e.g. مطبخ / بار العصائر.
- `MenuModifierGroup(name, min, max, required)` + `MenuModifier(group, name, price_delta)`; link groups to `Product` (menu item) and/or `Category`.
- `Recipe(product)` + `RecipeItem(recipe, ingredient_product, qty, unit)` — optional per item (hybrid). Selling deducts ingredient stock via existing inventory service.
- `Driver(user?, name, phone, branch, is_active)`.
- `CashCustody(holder_user?, holder_name, kind[waiter/driver/other], branch, amount, status[held/settled], related_orders M2M, opened_shift, settled_shift, created_at, settled_at)` — "اسم الشخص معاه كام"; settling posts a `DEPOSIT` transaction into the cashier `CASH_DRAWER`.

## Changes to existing models
- **`Order`:** `+order_type`(dine_in/takeaway/delivery), `+table` FK, `+driver` FK, `+waiter` FK, `+is_open`(running tab), `+service_charge`, `+close_type`(cash/visa/CL).
- **`OrderItem`:** `+is_void`, `+void_reason`, `+kitchen_status`(new/preparing/ready/served), `+printed`, `+modifiers`(JSON snapshot).
- **`Category`:** `+code_range_start`, `+code_range_end` (e.g. عصاير = 1–1000), `+station` FK.

---

## Screens / endpoints

### شاشة الويتر (Waiter)
Pick section→table (busy tables flagged) → build order (category grid + code entry + modifiers) → submit with **table # attached**. Opens/updates the table's **open tab**. Actions on an order: **void check**, **add items** (append to same open check), **void item** (manager-approved). Live table map via websocket.

### شاشة الكاشير (Cashier)
Adapted POS: category grid, open-tab picker, take payment, **close check as Cash / Visa / CL**. CL = آجل/على الحساب (owner/on-the-house) → recorded as `credit` close, excluded from cash collection but tracked. Handles takeaway + settles delivery/waiter custody into the drawer.

### شاشة المطبخ (KDS)
Per-station live queue of non-void items (websocket push). Buttons: بدأ التحضير → جاهز → served. New/changed orders also print a prep ticket to the station printer. Voided items disappear/flag.

### شاشة الدليفري (Delivery)
Drivers per branch. Assign delivery orders to a driver → driver out → mark **returned** → auto-create/settle **custody** for cash they collected → EOD **report of money owed per driver**.

### Reports
- **Sales per waiter** (group by waiter).
- **Reconciliation/جرد** (extends shift close/X-report): cash in / visa in / **deposits (وديعة) with whom** / expenses / withdrawals / expected vs actual.
- **Sales per category** (leveraging code ranges).
- **Driver EOD owed** report.

---

## Infrastructure change (websockets)
Add `channels`, `channels-redis`, Redis; add ASGI app + routing/consumers (`kds`, `waiter/tables`, `delivery`); run under Daphne/Uvicorn instead of waitress. Update `pos_launcher.py` / run scripts / installer accordingly. Fallback polling kept for degraded mode.

---

## Phased delivery
1. **Foundation** — cafe market profile; hide textile modules; seed roles (cashier/waiter/kitchen/driver/manager); Category code-ranges; branch labeling; fresh-data reset.
2. **ASGI/Channels** — infra + base websocket plumbing.
3. **Tables + Waiter screen + open tabs + modifiers**.
4. **KDS + station printers**.
5. **Void item / void check** (approval-gated).
6. **Custody (وديعة) + reconciliation/جرد report**.
7. **Delivery (drivers, assign, return, settle, EOD report)**.
8. **Check close (Cash/Visa/CL) + waiter/category/driver reports + polish + recipes/BOM deduction**.

## Open items to confirm before build
- Roles & who may void an item/check and close as CL.
- Menu structure sample (categories + code ranges + a few items with recipes/modifiers) to seed with.
- Station printer models/interface (same as current receipt printing?).
- Service charge default % and whether it applies to dine-in only.
