# Website ↔ ERP Integration — Implementation Plan

**Goal:** Connect the cafe's public website (custom PHP/Laravel app, MySQL on smarterasp.net)
to this Django ERP so that every order placed on the website is created correctly in the ERP,
routed to the right place (kitchen or shipping), and posted to the books.

**Status:** Draft for review — not yet built.
**Last updated:** 2026-07-09

---

## 1. The two order types

| Website order type | Website tables | ERP representation | Goes to |
|---|---|---|---|
| **Retail** — espresso/coffee machines, coffee packs | `store_products` (has `sku`, `stock_qty`) | `Order(order_type=delivery/takeaway, is_online_order=True)` | Stock deducted → **shipping** queue. **Never** the kitchen. |
| **Cafe** — drinks, desserts (staff-placed) | `menu_items` (slug + price, **no SKU/stock**) | `Order` with items whose Category has a `KitchenStation` | Pushed **live to KDS** + prints prep tickets. |

The retail-vs-cafe split is driven **per line item** by the product's category → kitchen station.
An order may contain both.

---

## 2. Architecture — website **pushes** to a secure ERP API

Confirmed: the ERP has a public URL and the web developer can add code to the website.
So the website calls the ERP when an order is placed; the ERP calls a webhook back on the
website as the order progresses.

```
 WEBSITE (smarterasp.net)                          ERP (public URL, Django)
 ┌────────────────────────┐   POST /api/v1/orders  ┌───────────────────────────────┐
 │ checkout / place order ├───────────────────────▶│  integrations app (new)        │
 │  (retail OR cafe)      │   Bearer token + HMAC   │  1. auth + verify signature    │
 │                        │                         │  2. dedupe by website_order_id │
 │                        │◀──────────────────────  │  3. map items by SKU / menu id │
 │  order status page  ◀──┤   webhook callback      │  4. create Order + OrderItems  │
 │                        │   as status changes     │  5. cafe→KDS  / retail→shipping│
 └────────────────────────┘                         │  6. post payment to finance    │
                                                     └───────────────────────────────┘
```

---

## 3. What the ERP already provides (reused, not rebuilt)

- **Orders** (`sales/models.py`) — `order_type` (dine_in/takeaway/delivery), `is_online_order`,
  split payments, void/audit lifecycle.
- **Kitchen** — `OrderItem.kitchen_status` (new→preparing→ready→served) + live **KDS** over
  websockets (`restaurant/views.py`, `push_event('kds', branch_id, …)`).
- **Kitchen routing/printing** — `Category.station` → `KitchenStation`; per-station prep tickets.
- **Recipes/BOM** — selling a drink auto-deducts ingredient stock (`restaurant/models.py`).
- **Finance** — `record_sale_transaction()` posts to cash drawer/bank + double-entry journal.
- **Branches** — `Warehouse(is_sales_point=True)`.
- **Products** — keyed by unique `sku` + `barcode`.

**No kitchen/POS/finance system needs to be built — only the bridge.**

---

## 4. Website database (`db_a9c631_rh`) — relevant schema

Custom app: UUIDs, JSON columns, soft deletes (`deleted_at`), bilingual translation tables.
All tables currently empty (0 rows), so enum values below must be confirmed with the web dev.

**`orders`**: `id, uuid, order_number(UNI), customer_id, guest_contact(json), branch_id,
channel, status, fulfillment_type, address_id, subtotal, discount_total, tax_total,
delivery_fee, grand_total, currency, placed_at, created_at, updated_at, deleted_at`

**`order_items`**: `id, order_id, store_product_id(nullable), name_snapshot, sku_snapshot,
unit_price, quantity, line_total, options(json)`
⚠️ **No `menu_item_id` column** — see the required schema change in §8.

**`store_products`**: `id, uuid, category_id, slug, sku(UNI), price, compare_at_price,
stock_qty, is_active, …`

**`menu_items`**: `id, uuid, category_id, slug(UNI), price, currency, badge, is_active, …`
(no SKU, no stock)

**`payments`**: `id, uuid, order_id, method_id, amount, status, provider_ref, proof_media_id,
reviewed_by, reviewed_at, notes, …` — supports both auto-paid gateways and manual proof review.

**`payment_methods`**: `id, code(UNI), name, is_active, config(json), sort_order`

**`branches`**: `id, uuid, code(UNI), name, address, phone, timezone, currency, is_active, …`

**`order_status_history`**: `id, order_id, status, note, changed_by, created_at` —
this is what the customer-facing status UI reads; ERP writes back here.

---

## 5. Field mapping (website → ERP)

| Website | ERP (`sales.Order` / `OrderItem`) | Notes |
|---|---|---|
| `orders.order_number` (+`uuid`) | `WebsiteOrder.website_order_id` | idempotency key — retries safe |
| `orders.branch_id` → `branches.code` | `Order.warehouse` | branch-code → ERP-warehouse map (branch per order) |
| `orders.fulfillment_type` | `Order.order_type` | pickup→takeaway, delivery→delivery, dine_in→dine_in |
| `orders.channel` | selects retail vs cafe flow | **confirm exact values** |
| `orders.subtotal / discount_total / delivery_fee / grand_total` | `subtotal_amount / discount / delivery_cost / total_amount` | VAT derived by ERP |
| `orders.customer_id` / `guest_contact(json)` | `crm.Customer` (find/create by phone) | |
| `order_items` **with** `store_product_id` | retail line → ERP product by `sku_snapshot`, **auto-created if new** | machines/packs |
| `order_items` **without** `store_product_id` | cafe line → **menu_item_id → ERP product** (mapping table) | needs §8 schema fix |
| `order_items.options(json)` | `OrderItem.modifiers` | size / extra shot / sugar |
| `payments.method_id`→`code`, `.status`, `.provider_ref` | posts to `CASH_DRAWER`/`INSTAPAY`/`BANK`/`ONLINE`; `gateway_ref` | see §7 |

---

## 6. Product mapping rules

- **Retail line** (`store_product_id` set): match ERP product by `sku_snapshot`.
  If unknown → **auto-provision** a Product in a "منتجات الموقع / Website" category
  (no kitchen station), price from payload, cost = payload `unit_cost` or 0, flagged for a
  manager to set cost/stock.
- **Cafe line** (`store_product_id` null): resolve via a **menu_item → ERP product** mapping
  table (cafe items have no shared SKU). If unmapped → **reject the order** with a clear
  `unmatched_cafe_item` error (never route a phantom item to the kitchen).

---

## 7. Payments (both prepaid and pay-later)

The `payments` table already models both cases (auto gateways vs `proof_media_id`/`reviewed_by`).

- `payments.status = 'paid'` → ERP creates the order **paid**; `record_sale_transaction()`
  posts to the matching account (card → BANK/ONLINE, instapay → INSTAPAY, wallet → VODAFONE_CASH,
  cash → CASH_DRAWER).
- pending / proof-review / COD / pay-on-pickup → order created **unpaid**; cash posts when
  confirmed/collected (delivery reuses the existing driver **custody** flow; pickup = cashier
  takes payment at the counter).
- A new **`ONLINE`** financial account keeps website revenue separable from in-store cash.

---

## 8. Required change on the WEBSITE side (blocker for cafe orders)

`order_items` has no `menu_item_id`, so a cafe line can only be identified by free text
(`name_snapshot`). That's not reliable enough to pick the correct kitchen station / recipe.

**Web developer must:** add a `menu_item_id` column to `order_items` (FK to `menu_items`), and
populate `sku_snapshot` from a stable menu code. Then every cafe line carries a real identifier.

---

## 9. API contract (what the web developer builds against)

### 9.1 Create order — `POST https://<erp>/api/v1/website-orders/`
Headers: `Authorization: Bearer <API_TOKEN>`, `X-Signature: <HMAC-SHA256 of raw body>`

```jsonc
{
  "website_order_id": "WEB-10432",         // orders.order_number — unique, makes retries safe
  "branch_code": "MAIN",                    // branches.code
  "channel": "store|menu",
  "fulfillment": "delivery|pickup|dine_in",
  "customer": { "name": "...", "phone": "...", "address": "..." },
  "payment": { "status": "paid|pending|cod|pay_on_pickup",
               "method": "card|instapay|wallet|cash",
               "amount_paid": 1500.00, "gateway_ref": "..." },
  "items": [
    { "kind": "store|menu",
      "sku": "ESP-MACHINE-01",              // store items
      "menu_item_id": 42,                    // menu items (after §8)
      "name": "Espresso Machine X",
      "quantity": 1, "unit_price": 1500.00,
      "unit_cost": 1100.00,                  // optional, retail COGS
      "modifiers": [ {"group":"Size","option":"Large","price_delta":5} ],
      "notes": "extra hot" }
  ],
  "delivery_cost": 30.00, "discount": 0.00, "notes": "..."
}
```

Responses:
- `201 { "erp_order_id": 8123, "invoice_number": "INV-2026-01234", "erp_status": "received" }`
- `200 { "erp_order_id": 8123, "duplicate": true }` (already imported)
- `422 { "code": "unmatched_cafe_item", "items": [ ... ] }`
- `401 / 403` auth/signature failure

### 9.2 Status webhook — ERP → `POST <website webhook URL>`
Fires on KDS status change, dispatch, and close:
```jsonc
{ "website_order_id":"WEB-10432", "erp_order_id":8123,
  "erp_status":"preparing|ready|out_for_delivery|shipped|completed|cancelled",
  "updated_at":"2026-07-09T12:34:00Z" }
```
Website applies it to `orders.status` and inserts an `order_status_history` row.

### 9.3 Security
Shared bearer token **+ HMAC signature of the body** (a leaked token alone can't forge orders)
+ HTTPS, plus an IP allowlist if smarterasp provides a static outbound IP.

---

## 10. ERP build — new `integrations` app

- `integrations/models.py` — `WebsiteOrder` (links `website_order_id` ↔ ERP `Order`, stores raw
  payload + sync status; guarantees idempotency), `WebsiteBranchMap`, `WebsiteMenuMap`.
- `integrations/api.py` — token/HMAC-authenticated JSON endpoints (plain Django `JsonResponse`,
  matching existing code style — no heavy new dependency).
- `integrations/services.py` — mapping + order-creation logic (reuses `record_sale_transaction`,
  `push_event`, `issue_stock`, `_print_kitchen_tickets`).
- `integrations/webhooks.py` — status write-back with retry, hooked into KDS status changes and
  order close.
- Small additions: an `ONLINE` financial account; a "Website" product category; a permanent
  "Online" shift + virtual "Online / أونلاين" user; config (API token, webhook URL, default
  branch) in the settings screen; a manager page listing website orders + unmatched items.

---

## 11. Phased delivery

1. `integrations` app + `WebsiteOrder` + auth + create-order endpoint (dedupe, validation).
2. Product mapping + auto-provisioning of retail items; cafe menu-id matching.
3. Routing: cafe → KDS + tickets; retail → stock + shipping.
4. Finance posting for both payment modes (+ `ONLINE` account, Online shift/user).
5. Status webhook back to the website + reconciliation/monitoring page.
6. Hardening: retries, logging, manual re-sync, health indicator.

---

## 12. Open items to confirm before build

**From you:**
- Confirm the "Online account + permanent Online shift + virtual Online user" approach.

**From the web developer (tables are empty, so values unknown):**
1. `orders.channel` possible values + how a purely-cafe order is distinguished from a store order.
2. `payment_methods.code` values (e.g. `card`, `instapay`, `cod`, `cash`) → map each to an ERP account.
3. `orders.status` vocabulary the website expects back (so callbacks use the right words).
4. Agreement to **add `menu_item_id` to `order_items`** (§8) — required for cafe orders.
5. The branch `code`s and which ERP branch each maps to.
