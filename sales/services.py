"""
sales/services.py — shared order logic (Phase 2.2).

Both POS checkout (submit_order_ajax) and invoice edit (edit_order_ajax) used to carry
their own near-identical copies of "issue stock for each line" and "compute discount +
total". They had already drifted apart (e.g. edit clamped totals to zero, checkout added
tailoring cost). Centralising them here gives ONE validated path, so create and edit can
never disagree again, and the future REST/mobile layer reuses the same code.

These functions assume they run inside an open transaction.atomic() block (the callers
provide it) and that the Order row already exists (items need order_id).
"""
from decimal import Decimal

from products.models import Product
from products.inventory_services import issue_stock, available_quantity
from .models import OrderItem


def _to_decimal(v, default='0'):
    try:
        return Decimal(str(v if v is not None else default))
    except Exception:
        return Decimal(default)


def issue_cart_items(order, cart_items, warehouse, user, applied_deal=None, note_prefix="فاتورة مبيعات",
                      allow_negative_stock=False):
    """Issue stock for every cart line and create its OrderItem (with COGS).

    Returns (subtotal, qualified_subtotal) where qualified_subtotal is the portion of
    the subtotal eligible for `applied_deal` (used for percentage/scoped promotions).

    Stock is removed via the central inventory service (FEFO + WarehouseStock + COGS +
    OUT log), so this is the single place sales touch stock. `allow_negative_stock` mirrors
    the Layer 2 'sales.allow_negative_stock' policy (or a per-user override) already checked
    by the caller's pre-check — it must be threaded through here too, since issue_stock()
    does its own authoritative stock validation independent of that pre-check.
    """
    subtotal = Decimal('0')
    qualified_subtotal = Decimal('0')

    # Pre-resolve the deal's product scope once (avoids a query per line). Scope is
    # explicit products UNION every product in the deal's selected categories.
    deal_all = bool(applied_deal and applied_deal.apply_to_all)
    deal_product_ids = set()
    if applied_deal and not deal_all:
        deal_product_ids = applied_deal.get_scoped_product_ids() or set()

    for item in cart_items:
        prod_id = item.get('id') or item.get('product_id')
        product = Product.objects.get(id=prod_id)
        qty = _to_decimal(item.get('quantity') or item.get('qty', 1), '1')
        price = _to_decimal(item.get('price', 0))
        sell_unit = item.get('sell_unit', 'box')
        strips_per_box = item.get('strips_per_box') or product.strips_per_box or 1
        price_tier = item.get('price_tier')
        if price_tier not in ('retail', 'semi_wholesale', 'wholesale'):
            price_tier = 'retail'

        # Fashion: a variant line's real availability constraint is the VARIANT's own
        # stock, not the product's warehouse/batch total (those are two separate
        # ledgers — see ProductVariant docstring). But the product-level ledger still
        # needs to move too, or Product.stock_quantity (and the dashboard revenue,
        # which sums StockTransaction OUT rows) silently ignore every variant sale.
        # So: deduct the variant counter (the authoritative check) AND issue_stock()
        # the product/warehouse side (allow_negative=True — the variant check already
        # gated this sale; a batch-level shortfall here is a pre-existing ledger drift,
        # not a reason to block a sale already validated against real per-size stock).
        variant_id = item.get('variant_id')
        if variant_id:
            from products.models import ProductVariant
            variant = ProductVariant.objects.select_for_update().get(id=variant_id, product=product)
            # A menu-category size variant (e.g. "قهوة - كبير") is prepared on demand just
            # like the plain product — its stock isn't real, so it must never compute a
            # false shortfall (same exemption as the non-variant branch below).
            is_menu_item = bool(product.category_id and product.category.is_menu_category)
            variant_shortfall = Decimal('0') if is_menu_item else max(Decimal('0'), qty - variant.stock_quantity)
            variant.stock_quantity = variant.stock_quantity - qty
            variant.save(update_fields=['stock_quantity'])

            item_cost_price = issue_stock(
                product, warehouse, qty,
                user=user,
                reference=str(order.id),
                note=f"{note_prefix} (مقاس: {variant.label}) #{order.id}",
                transaction_type='OUT',
                unit_price=price,
                allow_negative=True,
            )

            OrderItem.objects.create(
                order=order, product=product, variant=variant, quantity=qty, price=price,
                sell_unit=sell_unit, cost_price=item_cost_price,
                serial_number=(item.get('serial_number') or '')[:100],
                shortfall_qty=variant_shortfall, price_tier=price_tier,
                modifiers=item.get('modifiers') or [],
                note=(item.get('note') or '').strip(),
            )

            # Same hybrid-recipe deduction as the plain (non-sized) branch below — a sized
            # menu item (e.g. "Large Latte") still consumes its recipe's ingredients.
            recipe = getattr(product, 'recipe', None)
            if recipe and recipe.is_active:
                for ri in recipe.items.select_related('ingredient').all():
                    try:
                        issue_stock(
                            ri.ingredient, warehouse, ri.base_quantity * qty,
                            user=user, reference=str(order.id),
                            note=f"استهلاك وصفة: {product.name}",
                            allow_negative=True,
                        )
                    except ValueError:
                        pass

            line_total = qty * price
            subtotal += line_total
            if applied_deal and (deal_all or product.id in deal_product_ids):
                qualified_subtotal += line_total
            continue

        # Strips are stored as fractional boxes for deduction.
        deduction_qty = qty
        if sell_unit == 'strip' and strips_per_box and int(strips_per_box) > 1:
            deduction_qty = qty / Decimal(str(strips_per_box))

        # Layer 2 'sales.allow_negative_stock': issue_stock() floors batch deduction at 0
        # rather than going negative, so the shortfall must be captured here — before the
        # deduction — or it's lost (see OrderItem.shortfall_qty docstring).
        # Menu-category items (cafe drinks/food, prepared on demand) never carry a real
        # stock count — every sale would otherwise compute against ~0 available and print
        # a false "exceeded available stock" warning on every single invoice.
        if product.category_id and product.category.is_menu_category:
            shortfall = Decimal('0')
        else:
            available = available_quantity(product, warehouse)
            shortfall = max(Decimal('0'), deduction_qty - available)

        item_cost_price = issue_stock(
            product, warehouse, deduction_qty,
            user=user,
            reference=str(order.id),
            note=f"{note_prefix} {sell_unit} #{order.id}",
            transaction_type='OUT',
            unit_price=price,
            allow_negative=allow_negative_stock,
        )

        OrderItem.objects.create(
            order=order, product=product, quantity=qty, price=price,
            sell_unit=sell_unit, cost_price=item_cost_price,
            serial_number=(item.get('serial_number') or '')[:100],
            shortfall_qty=shortfall, price_tier=price_tier,
            modifiers=item.get('modifiers') or [],
            note=(item.get('note') or '').strip(),
        )

        # Hybrid recipes: a menu item sold through the regular cashier/POS checkout must
        # deduct its ingredients exactly like the waiter flow does (restaurant/views.py
        # waiter_open_or_append) — otherwise ingredient stock never moves for every sale
        # rung up directly at the counter instead of through a table. Never blocks the
        # sale itself: a cafe still serves the drink even if an ingredient runs short.
        recipe = getattr(product, 'recipe', None)
        if recipe and recipe.is_active:
            for ri in recipe.items.select_related('ingredient').all():
                try:
                    issue_stock(
                        ri.ingredient, warehouse, ri.base_quantity * qty,
                        user=user, reference=str(order.id),
                        note=f"استهلاك وصفة: {product.name}",
                        allow_negative=True,
                    )
                except ValueError:
                    pass

        line_total = qty * price
        subtotal += line_total
        if applied_deal and (deal_all or product.id in deal_product_ids):
            qualified_subtotal += line_total

    return subtotal, qualified_subtotal


def _expand_units(cart_items, product_ids, *, deal_all=False):
    """Expand cart lines into a flat list of per-unit prices, for lines whose product is in
    `product_ids` (or every line when deal_all). Quantities are floored to whole units —
    quantity-based promos (BOGO / N-for-price) operate on whole pieces."""
    units = []
    for it in cart_items or []:
        pid = it.get('id') or it.get('product_id')
        try:
            pid = int(pid)
        except (TypeError, ValueError):
            continue
        if deal_all or pid in product_ids:
            price = _to_decimal(it.get('price', 0))
            qty = int(_to_decimal(it.get('quantity') or it.get('qty', 1)))
            units.extend([price] * max(0, qty))
    return units


def compute_deal_discount(applied_deal, cart_items, qualified_subtotal):
    """Discount amount for ANY promo type (Phase 6.7 fix).

    - discount         : %/fixed on the eligible (qualified) subtotal — original behaviour.
    - buy_x_get_y       : for every X bought (per Y free), the cheapest eligible units are free.
                          Free items come from `get_products` if set, else from the buy scope
                          (in which case the group size is X+Y so the freebie isn't double-counted).
    - buy_n_for_price   : every N eligible units cost `for_price` instead of their sum; the
                          saving per group (dearest units grouped first, customer-favourable) is the discount.
    Returns a Decimal, never exceeding the eligible subtotal.
    """
    promo = getattr(applied_deal, 'promo_type', 'discount') or 'discount'
    deal_all = bool(applied_deal.apply_to_all)
    buy_ids = set() if deal_all else (applied_deal.get_scoped_product_ids() or set())

    if promo == 'buy_x_get_y' and applied_deal.buy_x_qty > 0 and applied_deal.get_y_qty > 0:
        x, y = applied_deal.buy_x_qty, applied_deal.get_y_qty
        buy_units = _expand_units(cart_items, buy_ids, deal_all=deal_all)
        free_ids = set(applied_deal.get_products.values_list('id', flat=True))
        if free_ids:
            groups = len(buy_units) // x
            free_pool = sorted(_expand_units(cart_items, free_ids))
        else:
            groups = len(buy_units) // (x + y)          # freebie comes from the same scope
            free_pool = sorted(buy_units)
        num_free = min(groups * y, len(free_pool))
        return sum(free_pool[:num_free], Decimal('0'))

    if promo == 'buy_n_for_price' and applied_deal.buy_n_qty > 0:
        n = applied_deal.buy_n_qty
        units = sorted(_expand_units(cart_items, buy_ids, deal_all=deal_all), reverse=True)
        discount = Decimal('0')
        for g in range(len(units) // n):
            group = units[g * n:(g + 1) * n]
            discount += max(Decimal('0'), sum(group, Decimal('0')) - applied_deal.for_price)
        return discount

    # Plain discount (default / promo_type == 'discount')
    if applied_deal.discount_type == 'PERCENTAGE':
        return (qualified_subtotal * applied_deal.value) / Decimal('100')
    return min(applied_deal.value, qualified_subtotal)


def compute_vat_amount(base_amount):
    """VAT amount to add on top of `base_amount` — 0 when unset or configured as "included
    in price" (already baked into the entered prices, nothing more to add).

    Mirrors compute_dine_in_service_charge(): the store's setting decides whether VAT is
    a real amount added to what's collected, or purely an informational split of an
    already-inclusive total (see Order.vat_breakdown(), which reads the *result* of this —
    total_amount — rather than recomputing independently).
    """
    from settings.policies import get_policy

    if get_policy('tax.vat_included_in_price'):
        return Decimal('0')
    rate = Decimal(str(get_policy('tax.vat_rate') or 0))
    if rate <= 0:
        return Decimal('0')
    return (_to_decimal(base_amount) * rate / Decimal('100')).quantize(Decimal('0.01'))


def compute_dine_in_service_charge(subtotal, order_type):
    """Service-charge amount to add on top for a new/edited order — dine-in only, and 0
    when the store's rate is unset or configured as "included in price" (in that case the
    charge is already baked into the subtotal; Order.service_charge_breakdown() extracts
    an informational portion for the receipt instead of adding anything here).

    Shared by both submit_order_ajax and edit_order_ajax so a cashier-created dine-in
    order gets exactly the same treatment the waiter flow already applies.
    """
    from .models import Order
    from settings.policies import get_policy

    if order_type != Order.ORDER_TYPE_DINE_IN:
        return Decimal('0')
    if get_policy('tax.service_charge_included_in_price'):
        return Decimal('0')
    pct = Decimal(str(get_policy('tax.service_charge_percent') or 0))
    if pct <= 0:
        return Decimal('0')
    return (_to_decimal(subtotal) * pct / Decimal('100')).quantize(Decimal('0.01'))


def compute_discount_and_total(subtotal, qualified_subtotal, *, discount, discount_type,
                               applied_deal, delivery_cost, tailoring_cost=Decimal('0'),
                               service_charge=Decimal('0'), cart_items=None):
    """Resolve the discount amount and final total for an order.

    Returns a dict:
        applied_discount, total, discount, discount_type, applied_deal, service_charge
    where (discount, discount_type, applied_deal) are the NORMALISED values to persist
    on the Order. Raises ValueError if a deal's minimum-order-value isn't met.

    `service_charge` is the caller-computed dine-in service charge amount (already 0 for
    takeaway/delivery orders, or when the store's service charge is configured as
    "included in price" — see settings.service_charge_included_in_price) — added on top
    the same way delivery_cost/tailoring_cost are, and passed straight through so the
    caller can persist it on Order.service_charge.

    Total is clamped at zero (a discount larger than the subtotal never goes negative).
    """
    subtotal = _to_decimal(subtotal)
    qualified_subtotal = _to_decimal(qualified_subtotal)
    discount = _to_decimal(discount)
    delivery_cost = _to_decimal(delivery_cost)
    tailoring_cost = _to_decimal(tailoring_cost)
    service_charge = _to_decimal(service_charge)

    if applied_deal:
        if subtotal < applied_deal.minimum_order_value:
            raise ValueError(
                f"قيمة الفاتورة ({subtotal}) أقل من الحد الأدنى لتطبيق هذا العرض "
                f"({applied_deal.minimum_order_value})"
            )
        applied_discount = compute_deal_discount(applied_deal, cart_items, qualified_subtotal)
        norm_discount = applied_discount
        norm_type = 'fixed'
    else:
        if discount_type == 'percent':
            applied_discount = (subtotal * discount) / Decimal('100')
        else:
            applied_discount = discount
        norm_discount = discount
        norm_type = discount_type

    # Never let the discount drive the line subtotal below zero.
    applied_discount = min(applied_discount, subtotal)
    pre_extras_total = subtotal - applied_discount + delivery_cost + tailoring_cost
    if pre_extras_total < 0:
        pre_extras_total = Decimal('0')

    # VAT and the service charge are each an independent percentage of the same
    # pre-extras total, added side by side — NOT compounded (VAT must not also apply to
    # the service charge amount, or vice versa). E.g. 100 + 12% service + 14% VAT = 126,
    # not 100 * 1.12 * 1.14. Both are 0 when configured as "included in price".
    vat_amount = compute_vat_amount(pre_extras_total)
    total = pre_extras_total + service_charge + vat_amount

    return {
        'applied_discount': applied_discount,
        'total': total,
        'discount': norm_discount,
        'discount_type': norm_type,
        'applied_deal': applied_deal,
        'service_charge': service_charge,
        'vat_amount': vat_amount,
    }


def collect_tailoring_payment(order, amount, user, *, note=''):
    """Record cash collected against a standalone work order (deposit, or remaining
    balance on delivery) into the main cash drawer + a linked Transaction, and update
    the order's own payment fields so `remaining_amount` stays accurate.

    `amount` is the amount being collected RIGHT NOW (not a running total). No-op for
    amount <= 0.
    """
    from financial.models import Account, Transaction
    from .utils import get_active_shift

    amount = _to_decimal(amount)
    if amount <= 0:
        return

    order.received_amount = (order.received_amount or Decimal('0')) + amount
    order.cash_paid = (order.cash_paid or Decimal('0')) + amount
    order.is_completed = order.received_amount >= order.total_amount
    order.save(update_fields=['received_amount', 'cash_paid', 'is_completed'])

    account, _ = Account.objects.get_or_create(
        account_type='CASH_DRAWER',
        defaults={'name': 'درج الكاشير', 'balance': 0},
    )
    Transaction.objects.create(
        shift=get_active_shift(),
        account=account,
        transaction_type='DEPOSIT',
        amount=amount,
        description=note or f"دفعة أمر شغل #{order.id}",
        created_by=user,
        order=order,
    )
