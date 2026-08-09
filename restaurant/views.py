import json
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction as db_transaction
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.dateparse import parse_datetime
from django.views.decorators.http import require_POST

from accounts.permissions import has_permission, require_permission
from products.models import Category, Product, Warehouse
from sales.models import Order, OrderItem
from sales.utils import get_active_shift

from .consumers import push_event
from .models import (
    CashCustody, Driver, MenuModifier, MenuModifierGroup, Recipe, RecipeItem, Section,
    SubRecipe, SubRecipeItem, Table, compatible_units,
)


def _recompute_order_totals(order):
    """Recalculate subtotal/service-charge/total from current non-void items.

    Shared by add-items and void-item so a void always re-derives the service charge
    from the surviving subtotal instead of leaving a stale (too-high) charge behind.
    Caller is responsible for order.save(update_fields=[...]).
    """
    from sales.services import compute_dine_in_service_charge, compute_vat_amount

    order.subtotal_amount = sum((i.subtotal for i in order.items.filter(is_void=False)), Decimal('0'))
    # VAT and the service charge are each an independent percentage of the same
    # post-discount subtotal, added side by side — not compounded on top of each other.
    # Service charge used to be computed on the raw subtotal_amount (before discount),
    # so a discounted dine-in order still paid full service charge on the un-discounted
    # amount while VAT correctly used the discounted base.
    discounted_subtotal = max(Decimal('0'), order.subtotal_amount - Decimal(str(order.discount)))
    order.service_charge = compute_dine_in_service_charge(discounted_subtotal, order.order_type)
    pre_extras_total = discounted_subtotal + Decimal(str(order.delivery_cost))
    # Pinned to the rate this order was actually opened under (vat_rate_snapshot), not
    # whatever the store's live VAT setting happens to be right now — otherwise a rate
    # change would silently re-price an already-open table's VAT on every add-item/void.
    # Excludes delivery_cost from the base, same as compute_discount_and_total() — the
    # delivery fee is a flat pass-through charge, not part of the taxable sale.
    order.vat_amount = compute_vat_amount(discounted_subtotal, order.vat_rate_snapshot, order.vat_included_snapshot)
    order.total_amount = pre_extras_total + order.service_charge + order.vat_amount


def _active_branch(request):
    """Resolve the branch (Warehouse) the current user is working in.

    Mirrors the POS warehouse-selection logic: honor ?branch_id, else the user's
    first allowed sales-point warehouse.
    """
    warehouses = Warehouse.objects.filter(is_active=True, is_sales_point=True)
    if not request.user.is_superuser:
        profile = getattr(request.user, 'profile', None)
        if profile and profile.allowed_warehouses.exists():
            warehouses = warehouses.filter(id__in=profile.allowed_warehouses.all())
    branch_id = request.GET.get('branch_id')
    if branch_id:
        b = warehouses.filter(id=branch_id).first()
        if b:
            return b, warehouses
    return warehouses.first(), warehouses


# ─────────────────────────────────────────────
#  SETUP — sections/tables/drivers (manager-facing, no Django admin needed)
# ─────────────────────────────────────────────

@login_required
@require_permission('pos', 'edit')
def restaurant_setup(request):
    branch, warehouses = _active_branch(request)
    if not branch:
        messages.error(request, "لا يوجد فرع نشط متاح لك.")
        return redirect('dashboard')

    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'add_section':
            name = request.POST.get('name', '').strip()
            if name:
                Section.objects.create(branch=branch, name=name)
                messages.success(request, f"تمت إضافة الصالة: {name}")
        elif action == 'add_table':
            number = request.POST.get('number', '').strip()
            section_id = request.POST.get('section_id') or None
            seats = request.POST.get('seats') or 4
            if number:
                section = Section.objects.filter(id=section_id, branch=branch).first() if section_id else None
                Table.objects.get_or_create(branch=branch, number=number, defaults={
                    'section': section, 'seats': seats,
                })
                messages.success(request, f"تمت إضافة الترابيزة: {number}")
        elif action == 'add_driver':
            name = request.POST.get('name', '').strip()
            phone = request.POST.get('phone', '').strip()
            if name:
                Driver.objects.create(branch=branch, name=name, phone=phone)
                messages.success(request, f"تمت إضافة الطيار: {name}")
        elif action == 'edit_section':
            section = get_object_or_404(Section, id=request.POST.get('id'), branch=branch)
            name = request.POST.get('name', '').strip()
            if name:
                section.name = name
                section.save(update_fields=['name'])
                messages.success(request, "تم تعديل الصالة.")
        elif action == 'delete_section':
            # Soft-delete (is_active=False), not a hard delete — tables already reference
            # this section by FK (on_delete=SET_NULL only fires on row deletion, and a
            # hard delete here would also silently detach every table in it). Every list
            # already filters is_active=True, so this simply drops it from view.
            section = get_object_or_404(Section, id=request.POST.get('id'), branch=branch)
            section.is_active = False
            section.save(update_fields=['is_active'])
            messages.success(request, "تم حذف الصالة.")
        elif action == 'edit_table':
            table = get_object_or_404(Table, id=request.POST.get('id'), branch=branch)
            number = request.POST.get('number', '').strip()
            section_id = request.POST.get('section_id') or None
            seats = request.POST.get('seats') or table.seats
            if number:
                table.number = number
                table.section = Section.objects.filter(id=section_id, branch=branch).first() if section_id else None
                try:
                    table.seats = int(seats)
                except (TypeError, ValueError):
                    pass
                table.save(update_fields=['number', 'section', 'seats'])
                messages.success(request, "تم تعديل الترابيزة.")
        elif action == 'delete_table':
            # Soft-delete — a busy/reserved table (or one with historical orders pointing
            # at it via Order.table) must not be hard-deleted out from under those orders.
            table = get_object_or_404(Table, id=request.POST.get('id'), branch=branch)
            table.is_active = False
            table.save(update_fields=['is_active'])
            messages.success(request, "تم حذف الترابيزة.")
        elif action == 'edit_driver':
            driver = get_object_or_404(Driver, id=request.POST.get('id'), branch=branch)
            name = request.POST.get('name', '').strip()
            phone = request.POST.get('phone', '').strip()
            if name:
                driver.name = name
                driver.phone = phone
                driver.save(update_fields=['name', 'phone'])
                messages.success(request, "تم تعديل بيانات الطيار.")
        elif action == 'delete_driver':
            # Soft-delete — historical orders/custodies keep a FK to this driver.
            driver = get_object_or_404(Driver, id=request.POST.get('id'), branch=branch)
            driver.is_active = False
            driver.save(update_fields=['is_active'])
            messages.success(request, "تم حذف الطيار.")
        return redirect(f"{request.path}?branch_id={branch.id}")

    sections = Section.objects.filter(branch=branch, is_active=True).prefetch_related('tables')
    tables = Table.objects.filter(branch=branch, is_active=True).select_related('section')
    drivers = Driver.objects.filter(branch=branch, is_active=True)

    return render(request, 'restaurant/setup.html', {
        'branch': branch, 'warehouses': warehouses,
        'sections': sections, 'tables': tables, 'drivers': drivers,
        'tables_free': tables.filter(status=Table.STATUS_FREE).count(),
        'tables_busy': tables.filter(status=Table.STATUS_BUSY).count(),
        'tables_reserved': tables.filter(status=Table.STATUS_RESERVED).count(),
    })


# ─────────────────────────────────────────────
#  WAITER SCREEN — table map
# ─────────────────────────────────────────────

@login_required
@require_permission('waiter', 'view')
def waiter_table_map(request):
    branch, warehouses = _active_branch(request)
    if not branch:
        messages.error(request, "لا يوجد فرع نشط متاح لك.")
        return redirect('dashboard')

    sections = Section.objects.filter(branch=branch, is_active=True).prefetch_related('tables')
    tables = Table.objects.filter(branch=branch, is_active=True).select_related('section')

    # Table-less orders (started via "طلب جديد بدون ترابيزة") stay open/in-progress just
    # like a table's tab — list them here too, or a waiter has no way back to one once
    # they navigate away from it.
    no_table_orders = (Order.objects.filter(warehouse=branch, table__isnull=True, is_open=True)
                       .exclude(status='void').select_related('waiter').order_by('-created_at'))

    return render(request, 'restaurant/waiter_tables.html', {
        'branch': branch,
        'warehouses': warehouses,
        'sections': sections,
        'tables': tables,
        'no_table_orders': no_table_orders,
    })


@login_required
@require_POST
def reserve_table(request, table_id):
    """Mark a free table 'محجوزة' with the reserving guest's details — a table can only
    be reserved while it's free; an already-open (busy) table has a live order on it and
    reservation doesn't apply.
    """
    table = get_object_or_404(Table, id=table_id)
    if table.status == Table.STATUS_BUSY:
        return JsonResponse({'status': 'error', 'message': 'الترابيزة مشغولة بطلب حالياً — لا يمكن حجزها.'})

    table.status = Table.STATUS_RESERVED
    table.reserved_name = (request.POST.get('name') or '').strip()[:100]
    table.reserved_phone = (request.POST.get('phone') or '').strip()[:30]
    table.reserved_notes = (request.POST.get('notes') or '').strip()[:200]
    party_size = request.POST.get('party_size')
    try:
        table.reserved_party_size = int(party_size) if party_size else None
    except ValueError:
        table.reserved_party_size = None
    time_str = request.POST.get('time')
    if time_str:
        parsed = parse_datetime(time_str)
        table.reserved_time = parsed
    else:
        table.reserved_time = None
    table.save()

    push_event('waiter', table.branch_id, {'event': 'table_status', 'table_id': table.id, 'status': table.status})
    return JsonResponse({'status': 'ok'})


@login_required
@require_POST
def unreserve_table(request, table_id):
    """Release a reserved table back to free — the guest didn't show, or the reservation
    was made in error.
    """
    table = get_object_or_404(Table, id=table_id)
    if table.status == Table.STATUS_RESERVED:
        table.status = Table.STATUS_FREE
        table.reserved_name = ''
        table.reserved_phone = ''
        table.reserved_notes = ''
        table.reserved_party_size = None
        table.reserved_time = None
        table.save()
        push_event('waiter', table.branch_id, {'event': 'table_status', 'table_id': table.id, 'status': table.status})
    return JsonResponse({'status': 'ok'})


def _waiter_menu_context(order):
    """Modifier/variant pickers + the current cart snapshot — identical whether the
    order being built belongs to a table or not (table-less takeaway orders reuse
    this exact same building UI).
    """
    from .services import build_modifier_map

    categories = Category.objects.filter(is_active=True).prefetch_related('products').order_by('name')
    modifier_map = build_modifier_map()

    # Same "اختر المقاس" mechanism as POS — a product with sizes (ProductVariant,
    # color='') must have one picked before it can be added to the cart at all.
    variant_map = {}
    for p in Product.objects.filter(is_active=True, has_variants=True).prefetch_related('variants'):
        variants = [{'id': v.id, 'label': v.label, 'price': float(v.price)}
                    for v in p.variants.filter(is_active=True, color='')]
        if variants:
            variant_map[p.id] = variants

    order_json = None
    if order:
        vat = order.vat_breakdown()
        svc = order.service_charge_breakdown()
        order_json = json.dumps({
            'id': order.id,
            'subtotal_amount': float(order.subtotal_amount),
            'service_charge': float(svc['amount']) if svc else 0,
            'service_charge_included': svc['included'] if svc else False,
            'vat_amount': float(vat['tax']) if vat else 0,
            'vat_included': vat['included'] if vat else False,
            'total_amount': float(order.total_amount),
            'items': [{
                'id': it.id,
                'product_id': it.product_id,
                'product_name': it.product.name if it.product else '',
                'variant_label': it.variant.label if it.variant_id else '',
                'quantity': float(it.quantity),
                'subtotal': float(it.subtotal),
                'is_void': it.is_void,
                'note': it.note,
                'modifiers': it.modifiers or [],
            } for it in order.items.all()],
        })

    # Calories/allergens/serve-temperature per product — looked up by the cart's "ℹ️"
    # info button (see showItemInfo in waiter_order.html) for both pending and
    # already-sent lines, without a round trip per click.
    product_specs = {}
    for cat in categories:
        for p in cat.products.all():
            product_specs[p.id] = {
                'name': p.name,
                'calories': p.calories,
                'allergens': p.allergens,
                'serve_temperature': p.get_serve_temperature_display() if p.serve_temperature else '',
            }

    # Customer list for the آجل/partial-payment close — lets the waiter attach the
    # unpaid remainder to a customer's balance instead of it vanishing into credit_paid
    # with no ledger trace (see close_check).
    from crm.models import Customer
    customers_json = json.dumps([
        {'id': c.id, 'name': (f"{c.first_name} {c.last_name}").strip(), 'phone': c.phone or ''}
        for c in Customer.objects.all().order_by('first_name', 'last_name')
    ])

    from sales.views import _recipe_product_ids
    return {
        'categories': categories,
        'order_json': order_json,
        'modifier_map_json': json.dumps(modifier_map),
        'variant_map_json': json.dumps(variant_map),
        'product_specs_json': json.dumps(product_specs),
        'customers_json': customers_json,
        # Same purpose as sales/views.py pos_view's identical context var — lets the
        # waiter cart skip the recipe-stock check round trip for products that have no
        # recipe at all (most of the menu).
        'recipe_product_ids_json': json.dumps(list(_recipe_product_ids())),
    }


def _add_items_to_order(request, order, items, branch):
    """Append cart lines to an already-existing order (table-bound or not) — the
    shared core of waiter_open_or_append / waiter_add_items_no_table.
    """
    from products.inventory_services import issue_stock

    new_items = []
    for it in items:
        product = Product.objects.filter(id=it.get('product_id'), is_active=True).first()
        if not product:
            continue
        qty = Decimal(str(it.get('quantity', 1)))
        modifiers = it.get('modifiers') or []

        # A size (ProductVariant, color='') carries its OWN full price — same "اختر
        # المقاس before it's added" mechanism as POS, just via the waiter cart instead.
        variant = None
        variant_id = it.get('variant_id')
        if variant_id:
            variant = product.variants.filter(id=variant_id, color='').first()

        price = variant.price if variant else product.price_retail
        for m in modifiers:
            try:
                price += Decimal(str(m.get('price_delta', 0)))
            except (TypeError, ValueError):
                pass

        # Menu-category items (cafe drinks/food, prepared on demand) never carry a real
        # stock count — mirrors sales.services.issue_cart_items's exact same exemption so
        # a waiter-rung sale doesn't print a false "exceeded available stock" warning.
        # A product flagged track_stock_no_recipe (bought ready-made, not prepared from
        # a recipe) is excluded from this exemption — it needs the real stock check.
        if product.category_id and product.category.is_menu_category and not product.track_stock_no_recipe:
            shortfall = Decimal('0')
        else:
            from products.inventory_services import available_quantity
            available = available_quantity(product, branch)
            shortfall = max(Decimal('0'), qty - available)

        # A waiter-added item previously only deducted its recipe's ingredients (below)
        # and never issued stock for the sold product itself — meaning it never created
        # a StockTransaction row at all, silently undercounting every stock/revenue report
        # built from StockTransaction (dashboard revenue, sold-items report, stock levels)
        # for every table/takeaway order ever rung up through the waiter screen.
        item_cost_price = issue_stock(
            product, branch, qty,
            user=request.user, reference=str(order.id),
            note=f"طلب ويتر {'(مقاس: ' + variant.label + ') ' if variant else ''}#{order.id}",
            transaction_type='OUT',
            unit_price=price,
            allow_negative=True,
        )

        new_item = OrderItem.objects.create(
            order=order, product=product, variant=variant, quantity=qty, price=price,
            cost_price=item_cost_price, shortfall_qty=shortfall,
            modifiers=modifiers, note=(it.get('note') or '').strip(),
        )
        new_items.append(new_item)

        # Recipe ingredients are NOT deducted here — the kitchen hasn't actually made the
        # item yet. They're deducted once, when kitchen_status is set to "جاهز" (see
        # kds_set_status/kds_set_order_status below), so raw material stock reflects
        # what's actually been consumed, not what's merely been ordered.

    _recompute_order_totals(order)
    update_fields = ['subtotal_amount', 'service_charge', 'vat_amount', 'total_amount']
    # New items just went in — the ticket needs (re-)preparing even if the cashier had
    # already marked it ready/delivered for the items sent earlier.
    if new_items and order.kitchen_status in (Order.PREP_READY, Order.PREP_DELIVERED):
        order.kitchen_status = Order.PREP_PENDING
        update_fields.append('kitchen_status')
    order.save(update_fields=update_fields)

    push_event('kds', branch.id, {
        'event': 'order_updated', 'order_id': order.id,
        'table': order.table.number if order.table_id else None,
    })
    push_event('cashier', branch.id, {
        'event': 'order_updated', 'order_id': order.id,
    })
    if order.table_id:
        push_event('waiter', branch.id, {
            'event': 'table_status', 'table_id': order.table_id, 'status': order.table.status,
        })

    _print_kitchen_tickets(request, order, new_items)
    return new_items


@login_required
@require_permission('waiter', 'view')
def waiter_order_screen(request, table_id):
    """Build/edit the open check for a table: category grid + running cart."""
    branch, _ = _active_branch(request)
    table = get_object_or_404(Table, pk=table_id)
    order = table.open_order

    context = _waiter_menu_context(order)
    context.update({'branch': branch, 'table': table, 'order': order})
    return render(request, 'restaurant/waiter_order.html', context)


@login_required
@require_permission('waiter', 'create')
@require_POST
def waiter_open_or_append(request, table_id):
    """Create the table's open tab if none exists, then append submitted items to it.

    Payload: {"items": [{"product_id": 1, "quantity": 2, "modifiers": [...]}]}
    """
    table = get_object_or_404(Table, pk=table_id)
    try:
        data = json.loads(request.body)
    except (ValueError, json.JSONDecodeError):
        return JsonResponse({'status': 'error', 'message': 'بيانات غير صالحة'}, status=400)

    items = data.get('items') or []
    if not items:
        return JsonResponse({'status': 'error', 'message': 'لا يوجد أصناف لإضافتها'}, status=400)

    # The table already pins the branch — don't re-guess it from the request (which has
    # no ?branch_id on this POST and would silently fall back to the wrong warehouse).
    branch = table.branch
    if not branch:
        return JsonResponse({'status': 'error', 'message': 'لا يوجد فرع نشط'}, status=400)

    # Recipe stock is checked (and warned about, non-blocking) at add-to-cart time
    # instead — see sales.views.api_check_recipe_stock, called from the waiter cart the
    # moment a recipe-bearing product/variant is added. Checking again here at
    # "send to kitchen" would just re-show the same warning for an item the waiter
    # already confirmed adding despite the shortage.

    shift = get_active_shift()

    with db_transaction.atomic():
        order = table.open_order
        created = False
        if order is None:
            from sales.services import current_vat_snapshot
            _vat_rate_snap, _vat_included_snap = current_vat_snapshot()
            order = Order.objects.create(
                user=request.user,
                shift=shift,
                warehouse=branch,
                order_type=Order.ORDER_TYPE_DINE_IN,
                table=table,
                waiter=request.user,
                is_open=True,
                is_completed=False,
                vat_rate_snapshot=_vat_rate_snap,
                vat_included_snapshot=_vat_included_snap,
            )
            table.status = Table.STATUS_BUSY
            table.save(update_fields=['status'])
            created = True

        _add_items_to_order(request, order, items, branch)

    return JsonResponse({'status': 'ok', 'order_id': order.id, 'created': created,
                         'total_amount': float(order.total_amount)})


@login_required
@require_permission('waiter', 'create')
@require_POST
def waiter_new_order_no_table(request):
    """Start a table-less order taken by a waiter — each click creates its own
    brand-new, independent Order; it's never shared/merged with any other table-less
    order the way a table's open tab is shared across repeat visits.

    Defaults to dine-in (not takeaway) — a waiter serving a customer without an assigned
    table number (bar seating, standing customers, overflow) is still a Dine-in guest and
    should get the same service charge a seated table does; the cashier/waiter can still
    switch it to تيك أواي afterward if it's actually a takeaway/collection order.
    """
    branch, _ = _active_branch(request)
    if not branch:
        return JsonResponse({'status': 'error', 'message': 'لا يوجد فرع نشط'}, status=400)

    shift = get_active_shift()
    from sales.services import current_vat_snapshot
    _vat_rate_snap, _vat_included_snap = current_vat_snapshot()
    order = Order.objects.create(
        user=request.user,
        shift=shift,
        warehouse=branch,
        order_type=Order.ORDER_TYPE_DINE_IN,
        table=None,
        waiter=request.user,
        is_open=True,
        is_completed=False,
        vat_rate_snapshot=_vat_rate_snap,
        vat_included_snapshot=_vat_included_snap,
    )
    return JsonResponse({'status': 'ok', 'order_id': order.id})


@login_required
@require_permission('waiter', 'view')
def waiter_order_screen_no_table(request, order_id):
    """Same building UI as waiter_order_screen, for an order with no table (takeaway
    order started via waiter_new_order_no_table)."""
    branch, _ = _active_branch(request)
    order = get_object_or_404(Order, pk=order_id, table__isnull=True)

    context = _waiter_menu_context(order)
    context.update({'branch': branch, 'table': None, 'order': order})
    return render(request, 'restaurant/waiter_order.html', context)


@login_required
@require_permission('waiter', 'create')
@require_POST
def waiter_add_items_no_table(request, order_id):
    """Append cart lines to an existing table-less order — same mechanics as
    waiter_open_or_append, minus the table lookup/creation."""
    order = get_object_or_404(Order, pk=order_id, table__isnull=True)
    try:
        data = json.loads(request.body)
    except (ValueError, json.JSONDecodeError):
        return JsonResponse({'status': 'error', 'message': 'بيانات غير صالحة'}, status=400)

    items = data.get('items') or []
    if not items:
        return JsonResponse({'status': 'error', 'message': 'لا يوجد أصناف لإضافتها'}, status=400)

    branch = order.warehouse
    if not branch:
        return JsonResponse({'status': 'error', 'message': 'لا يوجد فرع نشط'}, status=400)

    # Recipe stock is checked at add-to-cart time instead — see waiter_open_or_append.

    with db_transaction.atomic():
        _add_items_to_order(request, order, items, branch)

    return JsonResponse({'status': 'ok', 'order_id': order.id, 'created': False,
                         'total_amount': float(order.total_amount)})


def _print_kitchen_tickets(request, order, items):
    """Group newly-sent items by their category's KitchenStation and print one ticket
    per station. Best-effort: the printing backend is currently stubbed out (see
    sales/printer_utils.py) so this always reports failure until printing is re-enabled —
    the ticket is still rendered correctly, ready to send once a real printer is wired.
    Never raises: a printer problem must never block the order/kitchen-display flow.
    """
    from collections import defaultdict

    from django.template.loader import render_to_string

    from sales.printer_utils import print_html_to_backend

    by_station = defaultdict(list)
    for item in items:
        station = item.product.category.station if (item.product and item.product.category) else None
        by_station[station].append(item)

    base_url = request.build_absolute_uri('/')
    for station, station_items in by_station.items():
        if not station or not station.printer_target:
            continue
        try:
            html = render_to_string('restaurant/kitchen_ticket.html', {
                'order': order, 'items': station_items, 'station': station,
            })
            print_html_to_backend(html, station.printer_target, base_url=base_url)
        except Exception:
            pass


@login_required
@require_permission('waiter', 'void')
@require_POST
def void_order_item(request, item_id):
    """Cancel one line of an open/active check (void item)."""
    item = get_object_or_404(OrderItem, pk=item_id)
    order = item.order
    if order.is_void:
        return JsonResponse({'status': 'error', 'message': 'الفاتورة ملغاة بالفعل'}, status=400)

    reason = ''
    if request.body:
        try:
            reason = json.loads(request.body).get('reason', '')
        except (ValueError, json.JSONDecodeError):
            pass

    with db_transaction.atomic():
        item.is_void = True
        item.void_reason = reason
        item.voided_by = request.user
        item.voided_at = timezone_now()
        item.save(update_fields=['is_void', 'void_reason', 'voided_by', 'voided_at'])

        _recompute_order_totals(order)
        order.save(update_fields=['subtotal_amount', 'service_charge', 'vat_amount', 'total_amount'])

    if order.warehouse_id:
        push_event('kds', order.warehouse_id, {
            'event': 'item_voided', 'order_id': order.id, 'item_id': item.id,
        })

    return JsonResponse({'status': 'ok', 'total_amount': float(order.total_amount)})


@login_required
@require_permission('waiter', 'void')
@require_POST
def void_order(request, order_id):
    """Void the whole check (existing Order.STATUS_VOID lifecycle) and free its table."""
    order = get_object_or_404(Order, pk=order_id)
    reason = ''
    if request.body:
        try:
            reason = json.loads(request.body).get('reason', '')
        except (ValueError, json.JSONDecodeError):
            pass

    with db_transaction.atomic():
        order.status = Order.STATUS_VOID
        order.voided_by = request.user
        order.void_reason = reason
        order.voided_at = timezone_now()
        order.is_open = False
        order.save(update_fields=['status', 'voided_by', 'void_reason', 'voided_at', 'is_open'])

        if order.table_id:
            Table.objects.filter(pk=order.table_id).update(status=Table.STATUS_FREE)

    if order.warehouse_id:
        push_event('kds', order.warehouse_id, {'event': 'order_voided', 'order_id': order.id})
        push_event('waiter', order.warehouse_id, {
            'event': 'table_status', 'table_id': order.table_id, 'status': Table.STATUS_FREE,
        })

    return JsonResponse({'status': 'ok'})


def timezone_now():
    from django.utils import timezone
    return timezone.now()


# ─────────────────────────────────────────────
#  CASHIER DASHBOARD — incoming orders queue (order-level workflow)
# ─────────────────────────────────────────────

@login_required
@require_permission('pos', 'view')
def cashier_dashboard(request):
    """Queue of order tickets (not per-item, unlike the station-scoped KDS) with the
    Pending -> Preparing -> Ready -> Delivered workflow buttons a cashier drives by hand.
    """
    branch, _ = _active_branch(request)
    if not branch:
        messages.error(request, "لا يوجد فرع نشط متاح لك.")
        return redirect('dashboard')

    # Every Order defaults kitchen_status='pending' regardless of what's in it — without
    # this filter, a pure-retail POS sale (e.g. a coffee machine, no kitchen item at all)
    # would sit in this queue forever since nobody ever advances a status nothing needs.
    orders = (Order.objects
              .filter(warehouse=branch, items__product__category__is_menu_category=True)
              .exclude(status=Order.STATUS_VOID)
              .exclude(kitchen_status=Order.PREP_DELIVERED)
              .select_related('table', 'waiter')
              .prefetch_related('items', 'items__product')
              .distinct()
              .order_by('created_at'))

    return render(request, 'restaurant/cashier_dashboard.html', {'branch': branch, 'orders': orders})


@login_required
@require_permission('pos', 'edit')
@require_POST
def cashier_set_order_status(request, order_id):
    """Advance an order's ticket-wide kitchen_status. Notifies the waiter screen when an
    order becomes ready so they know to go pick it up from the counter/station.
    """
    from django.utils import timezone

    order = get_object_or_404(Order, pk=order_id)
    new_status = request.POST.get('status') or (json.loads(request.body or b'{}').get('status') if request.body else None)
    valid = dict(Order.PREP_STATUS_CHOICES)
    if new_status not in valid:
        return JsonResponse({'status': 'error', 'message': 'حالة غير صالحة'}, status=400)

    update_fields = ['kitchen_status']
    order.kitchen_status = new_status
    now = timezone.now()
    if new_status == Order.PREP_PREPARING and not order.prep_started_at:
        order.prep_started_at = now
        update_fields.append('prep_started_at')
    elif new_status == Order.PREP_READY:
        order.ready_at = now
        update_fields.append('ready_at')
    elif new_status == Order.PREP_DELIVERED:
        order.delivered_at = now
        update_fields.append('delivered_at')
    order.save(update_fields=update_fields)

    if order.warehouse_id:
        push_event('cashier', order.warehouse_id, {
            'event': 'order_status', 'order_id': order.id, 'status': new_status,
        })
        if new_status == Order.PREP_READY:
            push_event('waiter', order.warehouse_id, {
                'event': 'order_ready', 'order_id': order.id,
                'table': order.table.number if order.table_id else None,
            })

    return JsonResponse({'status': 'ok'})


# ─────────────────────────────────────────────
#  KDS — kitchen display
# ─────────────────────────────────────────────

@login_required
@require_permission('kitchen', 'view')
def kds_view(request):
    """One ticket card per ORDER (table), not per item — a table with 2 coffees is a
    single ticket the kitchen preps together, not two separate cards.
    """
    from django.utils import timezone

    branch, _ = _active_branch(request)
    if not branch:
        messages.error(request, "لا يوجد فرع نشط متاح لك.")
        return redirect('dashboard')

    # A user whose access is limited to the kitchen screen (no other module) has no use
    # for the sidebar/header — they can never navigate anywhere else with it anyway — so
    # hiding it gives the ticket grid the extra vertical room to render taller cards.
    # Master/superuser always keep the normal chrome even if their profile has no
    # explicit role permissions recorded.
    kitchen_only_user = False
    prof = getattr(request.user, 'profile', None)
    if not request.user.is_superuser and not getattr(prof, 'is_master', False) and prof is not None:
        perms = prof.get_all_permissions()
        granted_modules = {m for m, actions in perms.items() if actions}
        kitchen_only_user = granted_modules == {'kitchen'}

    # `order__is_open` only means "waiter's running dine-in tab" — a cashier walk-in sale
    # (rung up and paid immediately at POS) is never "open" yet still needs the kitchen to
    # prepare it. Scope by "today, not yet served" instead, and only for menu-category
    # items — a retail sale (e.g. an espresso machine) has nothing for the kitchen to do.
    items = (OrderItem.objects
             .filter(order__warehouse=branch, is_void=False,
                     order__created_at__date=timezone.localdate(),
                     product__category__is_menu_category=True)
             .exclude(order__status='void')
             .exclude(kitchen_status=OrderItem.KITCHEN_SERVED)
             .select_related('order', 'order__table', 'product')
             .order_by('order__created_at', 'id'))

    tickets = []
    tickets_by_order = {}
    for it in items:
        ticket = tickets_by_order.get(it.order_id)
        if ticket is None:
            ticket = {'order': it.order, 'items': []}
            tickets_by_order[it.order_id] = ticket
            tickets.append(ticket)
        ticket['items'].append(it)

    # One-step workflow: a ticket is either still pending or it's done — no more
    # new/preparing/ready staging. The single "تم التحضير" button on the card marks
    # every item served at once (see kds_set_order_status), which also drops it off
    # this screen since the query below excludes served items.
    for ticket in tickets:
        ticket['status'] = 'pending'
        ticket['is_cancelled'] = False

    # A waiter/cashier cancelling an item (or the whole order) AFTER it was already sent
    # to the kitchen used to just make it vanish from this screen mid-prep — the chef had
    # no idea it was cancelled, only that the card disappeared. Instead, keep showing it
    # here as its own red "ملغي" notice until the chef explicitly dismisses it (remove
    # button, or the order-number+Enter shortcut) via kds_ack_void below.
    from django.db.models import Q
    cancelled_items = (OrderItem.objects
                        .filter(order__warehouse=branch,
                                order__created_at__date=timezone.localdate(),
                                product__category__is_menu_category=True,
                                kitchen_void_acknowledged=False)
                        .filter(Q(is_void=True) | Q(order__status='void'))
                        .exclude(kitchen_status=OrderItem.KITCHEN_SERVED)
                        .select_related('order', 'order__table', 'product')
                        .order_by('order__created_at', 'id'))

    cancelled_by_order = {}
    for it in cancelled_items:
        ticket = cancelled_by_order.get(it.order_id)
        if ticket is None:
            ticket = {'order': it.order, 'items': [], 'status': 'cancelled', 'is_cancelled': True}
            cancelled_by_order[it.order_id] = ticket
            tickets.append(ticket)
        ticket['items'].append(it)

    # A ticket with a lot of items no longer scrolls inside its own card, and the card
    # itself never grows past what fits on screen without a page scroll either — instead
    # the ticket splits across multiple same-sized cards (each labeled #<order> — جزء
    # N/M). Split purely by count (not by whether an item happens to have modifiers/a
    # note) — the card's item padding/spacing is tuned so MAX_ITEMS_PER_CARD items fit
    # regardless of content, so there's no real place left over that a complexity
    # weighting would be protecting; that only forced a split earlier than actually
    # needed whenever an item had a note.
    MAX_ITEMS_PER_CARD = 4

    split_tickets = []
    for ticket in tickets:
        items_list = ticket['items']
        chunks = [items_list[i:i + MAX_ITEMS_PER_CARD] for i in range(0, len(items_list), MAX_ITEMS_PER_CARD)]

        if len(chunks) <= 1:
            ticket['part_num'] = 1
            ticket['total_parts'] = 1
            split_tickets.append(ticket)
            continue
        for idx, chunk in enumerate(chunks, start=1):
            split_tickets.append({
                'order': ticket['order'], 'items': chunk, 'status': ticket['status'],
                'is_cancelled': ticket.get('is_cancelled', False),
                'part_num': idx, 'total_parts': len(chunks),
            })
    tickets = split_tickets

    from settings.policies import get_policy
    from django.core.paginator import Paginator

    # Real pagination — "عدد الطلبات الظاهرة قبل الحاجة للتمرير" is now literally the
    # page size: only that many tickets render at once, with page 1/2/3 controls instead
    # of one long scrolling list (this replaced an earlier "scroll only, never paginate"
    # design per a later request from the store owner).
    try:
        orders_per_page = int(get_policy('kitchen.orders_per_page') or 12)
    except (TypeError, ValueError):
        orders_per_page = 12
    paginator = Paginator(tickets, orders_per_page)
    page_number = request.GET.get('page') or 1
    try:
        page_obj = paginator.page(page_number)
    except Exception:
        page_obj = paginator.page(1)
    tickets = list(page_obj.object_list)
    # Tailwind class strings per size tier — resolved here (not in the template) since
    # Django's {% with %}/{% if %} tags can't share one {% endwith %} across separate
    # {% if %}/{% elif %}/{% else %} branches. One entry per dropdown choice (not banded
    # into 3 buckets) — banding used to make 8/12 render identically and 16/20 render
    # identically, so picking a different option from the settings page appeared to do
    # nothing for those pairs.
    # `cols` is chosen to EXACTLY divide orders_per_page (rows = orders_per_page / cols,
    # no remainder) so the grid's real capacity (rows*cols) matches the number the admin
    # picked — cols=6 for the "8" tier used to leave a half-empty 2nd row capable of
    # holding 12, so up to 12 tickets rendered with no scroll needed even though the
    # setting said 8. Within that constraint, cols is picked as high as reasonable so the
    # card still lands taller than wide (fewer rows for a given height = taller cards).
    CARD_SIZE_CLASSES = {
        4: dict(card_pad='p-5', item_pad='px-3.5 py-2', qty_size='text-base min-w-[2.5rem] px-2 py-1',
                name_size='text-base', size_size='text-xs px-2.5 py-1.5', note_size='text-sm',
                btn_pad='py-3 text-base', gap='gap-3', cols=4, card_h='436px'),
        8: dict(card_pad='p-4', item_pad='px-3 py-1', qty_size='text-sm min-w-[2.25rem] px-2 py-1',
                name_size='text-sm', size_size='text-xs px-2 py-1', note_size='text-sm',
                btn_pad='py-2.5 text-sm', gap='gap-2', cols=4, card_h='225px'),
        12: dict(card_pad='p-3.5', item_pad='px-2.5 py-1', qty_size='text-sm min-w-[2rem] px-1.5 py-0.5',
                 name_size='text-sm', size_size='text-[11px] px-2 py-1', note_size='text-xs',
                 btn_pad='py-2 text-sm', gap='gap-2', cols=6, card_h='218px'),
        16: dict(card_pad='p-3', item_pad='px-2 py-1', qty_size='text-xs min-w-[1.875rem] px-1.5 py-0.5',
                 name_size='text-xs', size_size='text-[11px] px-1.5 py-0.5', note_size='text-xs',
                 btn_pad='py-1.5 text-xs', gap='gap-1.5', cols=8, card_h='218px'),
        20: dict(card_pad='p-2.5', item_pad='px-2 py-0.5', qty_size='text-xs min-w-[1.75rem] px-1 py-0.5',
                 name_size='text-xs', size_size='text-[10px] px-1.5 py-0.5', note_size='text-[11px]',
                 btn_pad='py-1.5 text-xs', gap='gap-1.5', cols=10, card_h='218px'),
    }
    card_size = CARD_SIZE_CLASSES.get(orders_per_page) or min(
        CARD_SIZE_CLASSES.items(), key=lambda kv: abs(kv[0] - orders_per_page))[1]

    return render(request, 'restaurant/kds.html', {
        'branch': branch, 'tickets': tickets, 'cs': card_size, 'orders_per_page': orders_per_page,
        'page_obj': page_obj, 'paginator': paginator, 'kitchen_only_user': kitchen_only_user,
    })


@login_required
@require_permission('kitchen', 'view')
def kitchen_ticket_preview(request, order_id):
    """Browser-printable ticket for one order (window.print() fallback) — for stations
    with no printer_target wired up yet, or a manual reprint of the whole check."""
    order = get_object_or_404(Order, pk=order_id)
    items = order.items.filter(is_void=False).select_related('product')
    return render(request, 'restaurant/kitchen_ticket.html', {
        'order': order, 'items': items,
        'station': {'name': 'تذكرة المطبخ'},
    })


def _deduct_recipe_for_item(item, user):
    """Consume a ready item's recipe ingredients — called the moment the kitchen marks
    it "جاهز", not when it was originally ordered (see _add_items_to_order /
    sales.services.issue_cart_items, which deliberately no longer do this). Idempotent
    via recipe_deducted, since kds_set_status/kds_set_order_status can re-fire "ready"
    on an item that's already been deducted (e.g. toggled back to preparing and forward
    again) without double-consuming its ingredients.
    """
    if item.recipe_deducted or item.is_void or not item.product_id or not item.order.warehouse_id:
        return
    from sales.services import _deduct_recipe
    recipe = Recipe.for_product(item.product, size_id=item.variant.size_id if item.variant_id else None)
    if recipe:
        _deduct_recipe(recipe, item.quantity, item.order.warehouse, user, item.order, item.product.name)
    item.recipe_deducted = True
    item.save(update_fields=['recipe_deducted'])


@login_required
@require_permission('kitchen', 'edit')
@require_POST
def kds_set_status(request, item_id):
    item = get_object_or_404(OrderItem, pk=item_id)
    new_status = request.POST.get('status') or (json.loads(request.body or b'{}').get('status') if request.body else None)
    valid = dict(OrderItem.KITCHEN_STATUS_CHOICES)
    if new_status not in valid:
        return JsonResponse({'status': 'error', 'message': 'حالة غير صالحة'}, status=400)

    item.kitchen_status = new_status
    item.save(update_fields=['kitchen_status'])

    if new_status == OrderItem.KITCHEN_READY:
        _deduct_recipe_for_item(item, request.user)

    if item.order.warehouse_id:
        push_event('kds', item.order.warehouse_id, {
            'event': 'item_status', 'item_id': item.id, 'status': new_status,
        })

    return JsonResponse({'status': 'ok'})


@login_required
@require_permission('kitchen', 'edit')
@require_POST
def kds_set_order_status(request, order_id):
    """Advance every non-void, non-served item of a ticket together — the KDS screen
    groups a table's items into one card, so one button preps/readies the whole order.
    """
    order = get_object_or_404(Order, pk=order_id)
    new_status = request.POST.get('status') or (json.loads(request.body or b'{}').get('status') if request.body else None)
    valid = dict(OrderItem.KITCHEN_STATUS_CHOICES)
    if new_status not in valid:
        return JsonResponse({'status': 'error', 'message': 'حالة غير صالحة'}, status=400)

    items_qs = order.items.filter(is_void=False).exclude(kitchen_status=OrderItem.KITCHEN_SERVED)
    if new_status in (OrderItem.KITCHEN_READY, OrderItem.KITCHEN_SERVED):
        # Per-item save (not a bulk .update()) so each one can deduct its own recipe.
        # The KDS screen is now a single "تم التحضير" button that jumps straight from
        # new → served (no separate "ready" step anymore) — recipe ingredients still
        # need deducting right here, or they'd never get deducted at all.
        for item in items_qs.select_related('product', 'variant'):
            item.kitchen_status = new_status
            item.save(update_fields=['kitchen_status'])
            _deduct_recipe_for_item(item, request.user)
    else:
        items_qs.update(kitchen_status=new_status)

    if order.warehouse_id:
        push_event('kds', order.warehouse_id, {
            'event': 'order_items_status', 'order_id': order.id, 'status': new_status,
        })

    return JsonResponse({'status': 'ok'})


@login_required
@require_permission('kitchen', 'edit')
@require_POST
def kds_ack_void(request, order_id):
    """Dismiss a ticket's red "ملغي" cancellation notice from the KDS screen — called
    when the chef taps its remove button, or types its order number + Enter (see
    kds.html). Marks every voided item of this order as acknowledged so kds_view's
    cancelled-items query stops surfacing it; doesn't touch is_void/order.status.
    """
    from django.db.models import Q
    order = get_object_or_404(Order, pk=order_id)
    updated = order.items.filter(
        Q(is_void=True) | Q(order__status='void'),
        kitchen_void_acknowledged=False,
    ).update(kitchen_void_acknowledged=True)

    if order.warehouse_id:
        push_event('kds', order.warehouse_id, {
            'event': 'order_void_acked', 'order_id': order.id,
        })

    return JsonResponse({'status': 'ok', 'updated': updated})


# ─────────────────────────────────────────────
#  DELIVERY
# ─────────────────────────────────────────────

def _delivery_orders_payload(branch):
    """Today's delivery orders for the branch, serialized for the tabbed dashboard
    (All/Assigned/Unassigned) and its websocket-driven incremental refresh."""
    from django.utils import timezone

    orders = (Order.objects
              .filter(warehouse=branch, order_type=Order.ORDER_TYPE_DELIVERY,
                      created_at__date=timezone.localdate())
              .exclude(status='void')
              .select_related('driver', 'customer')
              .order_by('-created_at'))

    payload = []
    for o in orders:
        payload.append({
            'id': o.id,
            'display_number': o.display_number,
            'customer_name': str(o.customer) if o.customer_id else '—',
            'address': o.shipping_address or (o.customer.address if o.customer_id else '') or '',
            'total_amount': float(o.total_amount),
            'delivery_cost': float(o.delivery_cost or 0),
            'outstanding': float(max(Decimal('0'), Decimal(str(o.total_amount)) - Decimal(str(o.received_amount or 0)))),
            'driver_id': o.driver_id,
            'driver_name': o.driver.name if o.driver_id else None,
            'driver_assigned_at': o.driver_assigned_at.isoformat() if o.driver_assigned_at else None,
            'is_completed': bool(o.driver_settled_at),
        })
    return payload


def _delivery_drivers_payload(branch):
    drivers = Driver.objects.filter(branch=branch, is_active=True)
    return [{'id': d.id, 'name': d.name, 'phone': d.phone, 'owed': float(d.owed_today())} for d in drivers]


@login_required
@require_permission('delivery', 'view')
def delivery_dashboard(request):
    branch, _ = _active_branch(request)
    if not branch:
        messages.error(request, "لا يوجد فرع نشط متاح لك.")
        return redirect('dashboard')

    drivers = list(Driver.objects.filter(branch=branch, is_active=True))
    for d in drivers:
        d.owed = d.owed_today()

    return render(request, 'restaurant/delivery.html', {
        'branch': branch, 'drivers': drivers,
        'orders_json': json.dumps(_delivery_orders_payload(branch)),
        'drivers_json': json.dumps(_delivery_drivers_payload(branch)),
    })


@login_required
@require_permission('delivery', 'view')
def delivery_orders_feed(request):
    """JSON feed backing the dashboard's websocket-triggered incremental refresh (no
    full page reload, so running timers on other cards don't restart). Includes both
    orders and drivers (with their live owed balance) since assigning/settling an
    order or clearing a driver's account can change either."""
    branch, _ = _active_branch(request)
    if not branch:
        return JsonResponse({'orders': [], 'drivers': []})
    return JsonResponse({
        'orders': _delivery_orders_payload(branch),
        'drivers': _delivery_drivers_payload(branch),
    })


@login_required
@require_permission('delivery', 'edit')
@require_POST
def assign_driver(request, order_id):
    from django.utils import timezone

    order = get_object_or_404(Order, pk=order_id)
    driver_id = request.POST.get('driver_id')
    driver = get_object_or_404(Driver, pk=driver_id)
    order.driver = driver
    order.order_type = Order.ORDER_TYPE_DELIVERY
    order.driver_assigned_at = timezone.now()
    order.save(update_fields=['driver', 'order_type', 'driver_assigned_at'])

    if order.warehouse_id:
        push_event('delivery', order.warehouse_id, {
            'event': 'order_assigned', 'order_id': order.id, 'driver_id': driver.id,
            'driver_assigned_at': order.driver_assigned_at.isoformat(),
        })
    return JsonResponse({
        'status': 'ok',
        'print_url': reverse('restaurant:delivery_check_preview', args=[order.id]),
    })


@login_required
@require_permission('delivery', 'view')
def delivery_check_preview(request, order_id):
    """Browser-printable handoff check for the flyer: order id, customer name/phone/
    address, and the item list — printed the moment a driver is assigned."""
    order = get_object_or_404(Order, pk=order_id)
    items = order.items.filter(is_void=False).select_related('product')
    return render(request, 'restaurant/delivery_check.html', {'order': order, 'items': items})


@login_required
@require_permission('delivery', 'edit')
@require_POST
def driver_return_settle(request, order_id):
    """Driver came back: mark delivered, and open a CashCustody for the cash they collected.

    Cash-on-delivery orders are typically created unpaid (order.cash_paid == 0 — the
    payment doesn't exist yet at order time, only once the driver actually collects it
    at the door). But a delivery order can also have been paid in full up front (card/
    wallet at POS, no COD) — in that case there's nothing left for the flyer to collect,
    and crediting them here anyway would double the customer's payment on record.

    So the amount actually collected at the door is capped at the order's outstanding
    balance (total - what's already been received), never blindly re-added on top of an
    already-settled order. Of whatever was actually collected, the flyer keeps
    delivery_cost as their own earning — only the remainder is money they owe back to
    the owner (the CashCustody amount).
    """
    order = get_object_or_404(Order, pk=order_id)
    if not order.driver_id:
        return JsonResponse({'status': 'error', 'message': 'لا يوجد طيار مرتبط بهذا الطلب'}, status=400)

    outstanding = Decimal(str(order.total_amount)) - Decimal(str(order.received_amount or 0))
    if outstanding < 0:
        outstanding = Decimal('0')

    collected_raw = request.POST.get('collected_amount')
    if collected_raw not in (None, ''):
        try:
            collected = Decimal(str(collected_raw))
        except Exception:
            return JsonResponse({'status': 'error', 'message': 'مبلغ غير صالح'}, status=400)
    else:
        collected = outstanding
    # Can never collect more from the customer than what's actually still owed on the
    # invoice — this is the guard that prevents double-counting a prepaid order.
    if collected > outstanding:
        collected = outstanding
    if collected < 0:
        collected = Decimal('0')

    owed_to_owner = collected - Decimal(str(order.delivery_cost or 0))
    if owed_to_owner < 0:
        owed_to_owner = Decimal('0')

    custody = None
    if owed_to_owner > 0:
        custody = CashCustody.objects.create(
            branch=order.warehouse, kind=CashCustody.KIND_DRIVER,
            holder_name=order.driver.name, driver=order.driver,
            amount=owed_to_owner, created_by=request.user,
        )
        custody.orders.add(order)

    # Mark the order paid/complete now that the driver has actually collected the money
    # (for COD orders this is the first time any payment exists on the order at all).
    # Deliberately does NOT call record_sale_transaction()/post a CASH_DRAWER Transaction
    # here — the cash is physically with the driver, not in the drawer yet. It only hits
    # CASH_DRAWER once via CashCustody.settle() when the driver hands it over to the
    # cashier. Posting it here too would double-count that cash on top of the settle
    # deposit. The order's revenue was already posted to the journal (as an AR debit) by
    # post_sale() when it was first created unpaid — CashCustody.settle()'s DEPOSIT
    # transaction now clears that AR leg via post_cash_transaction (financial/posting.py),
    # so the journal/VAT/income-statement do end up reflecting it, just at settlement time.
    from django.utils import timezone

    order.cash_paid = (order.cash_paid or Decimal('0')) + collected
    order.received_amount = (order.received_amount or Decimal('0')) + collected
    order.is_completed = True
    order.driver_settled_at = timezone.now()
    order.save(update_fields=['cash_paid', 'received_amount', 'is_completed', 'driver_settled_at'])

    if order.warehouse_id:
        push_event('delivery', order.warehouse_id, {
            'event': 'order_returned', 'order_id': order.id,
            'custody_id': custody.id if custody else None,
        })

    return JsonResponse({'status': 'ok', 'custody_id': custody.id if custody else None})


@login_required
@require_permission('financial', 'edit')
@require_POST
def driver_account_settle(request, driver_id):
    """"تخليص حساب" / partial payment from the delivery dashboard's driver header.

    Settles the driver's held custodies (oldest first) up to `amount`. Omit `amount`
    (or send it >= what's owed) for a full "تخليص حساب" — every held custody is
    settled and the driver's owed balance drops to zero. A smaller amount only
    settles that much: it fully settles as many of the oldest custodies as it covers,
    then splits the next one into a settled portion (this payment) and a remaining
    held portion (still owed), so the balance is reduced by exactly `amount` rather
    than only in whole-custody increments.

    Scoped to TODAY's held custodies only, matching Driver.owed_today() — the exact
    figure shown on the driver's card/modal ("معاه اليوم"). Settling used to sweep
    every held custody regardless of age, so an old un-settled delivery from days/weeks
    earlier (one the cashier had simply forgotten about) got silently bundled into
    "تخليص حساب" on top of the amount actually shown, adding far more cash to the
    drawer than the cashier saw on screen and expected. Older un-settled custodies are
    left untouched here and need a separate, explicit reconciliation.
    """
    driver = get_object_or_404(Driver, pk=driver_id)
    shift = get_active_shift()
    from django.utils import timezone
    held = list(CashCustody.objects.filter(
        driver=driver, status=CashCustody.STATUS_HELD, created_at__date=timezone.localdate(),
    ).order_by('created_at'))
    owed = sum((c.amount for c in held), Decimal('0'))

    amount_raw = request.POST.get('amount')
    if amount_raw not in (None, ''):
        try:
            amount = Decimal(str(amount_raw))
        except Exception:
            return JsonResponse({'status': 'error', 'message': 'مبلغ غير صالح'}, status=400)
    else:
        amount = owed

    if amount <= 0:
        return JsonResponse({'status': 'error', 'message': 'المبلغ يجب أن يكون أكبر من صفر'}, status=400)
    if amount > owed:
        amount = owed

    remaining = amount
    try:
        with db_transaction.atomic():
            for custody in held:
                if remaining <= 0:
                    break
                if custody.amount <= remaining:
                    remaining -= custody.amount
                    custody.settle(settled_by=request.user, shift=shift)
                else:
                    paid_portion = CashCustody.objects.create(
                        branch=custody.branch, kind=custody.kind, holder_name=custody.holder_name,
                        driver=custody.driver, amount=remaining, created_by=request.user,
                        note=f"دفعة جزئية من الوديعة #{custody.id}",
                    )
                    paid_portion.orders.set(custody.orders.all())
                    paid_portion.settle(settled_by=request.user, shift=shift)
                    custody.amount = custody.amount - remaining
                    custody.save(update_fields=['amount'])
                    remaining = Decimal('0')
    except ValueError as e:
        return JsonResponse({'status': 'error', 'message': str(e)}, status=400)

    return JsonResponse({'status': 'ok', 'settled_amount': float(amount), 'owed_after': float(driver.owed_today())})


# ─────────────────────────────────────────────
#  CASH CUSTODY (وديعة) — settle to cashier drawer
# ─────────────────────────────────────────────

@login_required
@require_permission('financial', 'view')
def custody_list(request):
    branch, _ = _active_branch(request)
    custodies = CashCustody.objects.filter(branch=branch).select_related('driver') if branch else CashCustody.objects.none()
    return render(request, 'restaurant/custody_list.html', {'branch': branch, 'custodies': custodies})


@login_required
@require_permission('financial', 'view')
def reconciliation_report(request):
    """Unified daily جرد: cash in / visa in / who's still holding a وديعة — one screen
    instead of stitching together the shift X-report and the custody list separately.
    """
    from datetime import datetime as _dt

    from financial.reports import daily_summary

    branch, _ = _active_branch(request)
    day_str = request.GET.get('day')
    if day_str:
        try:
            day = _dt.strptime(day_str, '%Y-%m-%d').date()
        except ValueError:
            day = None
    else:
        day = None
    if day is None:
        from django.utils import timezone
        day = timezone.localdate()

    summary = daily_summary(day)

    custody_qs = CashCustody.objects.filter(created_at__date=day)
    if branch:
        custody_qs = custody_qs.filter(branch=branch)
    held = custody_qs.filter(status=CashCustody.STATUS_HELD).select_related('driver')
    settled = custody_qs.filter(status=CashCustody.STATUS_SETTLED).select_related('driver')
    held_total = sum((c.amount for c in held), Decimal('0'))
    settled_total = sum((c.amount for c in settled), Decimal('0'))

    return render(request, 'restaurant/reconciliation_report.html', {
        'branch': branch,
        'day': day,
        'summary': summary,
        'held': held,
        'settled': settled,
        'held_total': held_total,
        'settled_total': settled_total,
    })


@login_required
@require_permission('financial', 'edit')
@require_POST
def custody_settle(request, custody_id):
    custody = get_object_or_404(CashCustody, pk=custody_id)
    shift = get_active_shift()
    try:
        custody.settle(settled_by=request.user, shift=shift)
    except ValueError as e:
        return JsonResponse({'status': 'error', 'message': str(e)}, status=400)
    return JsonResponse({'status': 'ok'})


# ─────────────────────────────────────────────
#  Check close type (Cash / Visa / CL)
# ─────────────────────────────────────────────

@login_required
@require_permission('waiter', 'edit')
@require_POST
def close_check(request, order_id):
    """Close an open tab as cash / visa / CL (آجل), optionally partial.

    A dine-in table order never had a customer attached before — so an آجل close (or a
    partial cash/visa/wallet/instapay payment) had nowhere to record the unpaid الباقي:
    it just sat in order.credit_paid with no link to anyone's account, invisible on any
    customer statement. Now: any close with received < total requires customer_id, so
    the shortfall shows up on that customer's بdiscoun (order.customer + total_amount vs
    received_amount is exactly what crm.statements.build_customer_statement already
    reads for every other order type).
    """
    from decimal import Decimal, InvalidOperation
    order = get_object_or_404(Order, pk=order_id)
    close_type = request.POST.get('close_type')
    if close_type not in dict(Order.CLOSE_TYPE_CHOICES):
        return JsonResponse({'status': 'error', 'message': 'طريقة قفل غير صالحة'}, status=400)

    # Guard against double-closing: record_sale_transaction() below creates a brand new
    # Transaction (and re-posts the journal) every time it's called, so calling this twice
    # on the same order would double the cash-drawer balance and double-book revenue.
    if order.close_type:
        return JsonResponse({'status': 'error', 'message': 'الشيك مُقفل بالفعل'}, status=400)

    total = order.total_amount
    if close_type == Order.CLOSE_TYPE_CL:
        received = Decimal('0.00')
    else:
        raw_received = request.POST.get('received_amount')
        try:
            received = Decimal(str(raw_received)) if raw_received not in (None, '') else total
        except InvalidOperation:
            return JsonResponse({'status': 'error', 'message': 'المبلغ المستلم غير صالح'}, status=400)
        received = max(Decimal('0.00'), min(received, total))

    remaining = total - received
    customer_id = request.POST.get('customer_id') or None
    if remaining > Decimal('0.01'):
        if not customer_id:
            return JsonResponse({'status': 'error', 'message': 'يجب اختيار عميل لتسجيل الباقي كمديونية عليه'}, status=400)
        from crm.models import Customer
        customer = Customer.objects.filter(pk=customer_id).first()
        if not customer:
            return JsonResponse({'status': 'error', 'message': 'العميل غير موجود'}, status=400)
    else:
        customer = None

    with db_transaction.atomic():
        order.close_type = close_type
        order.is_open = False
        order.is_completed = True
        if customer:
            order.customer = customer
        if close_type == Order.CLOSE_TYPE_CASH:
            order.cash_paid = received
        elif close_type == Order.CLOSE_TYPE_VISA:
            order.visa_paid = received
        elif close_type == Order.CLOSE_TYPE_WALLET:
            order.wallet_paid = received
        elif close_type == Order.CLOSE_TYPE_INSTAPAY:
            order.instapay_paid = received
        elif close_type == Order.CLOSE_TYPE_CL:
            # آجل: not collected as cash — tracked as credit, excluded from cash reconciliation.
            order.credit_paid = total
        order.received_amount = received
        order.save(update_fields=['close_type', 'is_open', 'is_completed', 'cash_paid',
                                  'visa_paid', 'wallet_paid', 'instapay_paid', 'received_amount',
                                  'credit_paid', 'customer'])
        if order.table_id:
            Table.objects.filter(pk=order.table_id).update(status=Table.STATUS_FREE)

        # Post the sale to the cash drawer / bank account and the general journal —
        # without this, a waiter-closed check never touches Account.balance or the
        # JournalEntry/trial-balance/income-statement/VAT reports at all (the shift's
        # own expected-balance math happens to read Order.cash_paid directly so it isn't
        # affected, but every other financial report that reads Account/Journal would
        # silently miss every dine-in sale).
        from sales.utils import record_sale_transaction
        record_sale_transaction(order, request.user)

    if order.warehouse_id:
        push_event('waiter', order.warehouse_id, {
            'event': 'table_status', 'table_id': order.table_id, 'status': Table.STATUS_FREE,
        })

    return JsonResponse({'status': 'ok'})


# ─────────────────────────────────────────────
#  Reports: sales per waiter / per driver EOD owed
# ─────────────────────────────────────────────

@login_required
@require_permission('financial', 'view')
def waiter_sales_report(request):
    from django.db.models import Sum, Count, Q
    branch, _ = _active_branch(request)
    orders_all = Order.objects.exclude(waiter__isnull=True)
    if branch:
        orders_all = orders_all.filter(warehouse=branch)

    rows = (orders_all.exclude(status='void')
            .values('waiter__id', 'waiter__username', 'waiter__first_name', 'waiter__last_name')
            .annotate(total_sales=Sum('total_amount'), orders_count=Count('id'),
                      completed_orders=Count('id', filter=Q(is_completed=True)))
            .order_by('-total_sales'))

    # Voided orders aren't in `rows` at all (excluded above) — count them separately per
    # waiter so a manager can see "أحمد: 25 طلب، منهم 2 ملغى" instead of the void simply
    # vanishing from the report with no trace.
    voided_qs = orders_all.filter(status='void').values('waiter_id').annotate(c=Count('id'))
    voided_counts = {v['waiter_id']: v['c'] for v in voided_qs}

    for r in rows:
        r['avg_order_value'] = (r['total_sales'] / r['orders_count']) if r['orders_count'] else 0
        r['voided_count'] = voided_counts.get(r['waiter__id'], 0)
    return render(request, 'restaurant/waiter_sales_report.html', {'branch': branch, 'rows': rows})


@login_required
@require_permission('financial', 'view')
def waiter_sales_detail(request, waiter_id):
    """Per-product breakdown for one waiter — "قهوة: 12 كوب، عصير برتقال: 6 أكواب...".
    Drill-down from waiter_sales_report, same period filter as product_sales_report.
    """
    from datetime import timedelta

    from django.contrib.auth.models import User
    from django.db.models import Sum
    from django.utils import timezone

    waiter = get_object_or_404(User, pk=waiter_id)
    branch, _ = _active_branch(request)

    period = request.GET.get('period', 'all')
    today = timezone.localdate()
    if period == 'today':
        date_from = today
    elif period == 'week':
        date_from = today - timedelta(days=7)
    elif period == 'month':
        date_from = today - timedelta(days=30)
    else:
        period = 'all'
        date_from = None

    items = (OrderItem.objects
             .filter(order__waiter=waiter, is_void=False)
             .exclude(order__status='void')
             .select_related('product', 'variant__size'))
    if branch:
        items = items.filter(order__warehouse=branch)
    if date_from:
        items = items.filter(order__created_at__date__gte=date_from)

    # Group by (product, variant/size) so each size appears as its own row.
    rows_by_key = {}
    for it in items:
        if not it.product_id:
            continue
        size_label = it.variant.label if it.variant_id else ''
        key = (it.product_id, it.variant_id)
        row = rows_by_key.setdefault(key, {
            'product': it.product,
            'size_label': size_label,
            'quantity': Decimal('0'),
            'revenue': Decimal('0'),
        })
        row['quantity'] += it.quantity
        row['revenue'] += it.subtotal
    rows = sorted(rows_by_key.values(), key=lambda r: r['revenue'], reverse=True)

    return render(request, 'restaurant/waiter_sales_detail.html', {
        'branch': branch, 'waiter': waiter, 'rows': rows, 'period': period,
    })


@login_required
@require_permission('financial', 'view')
def product_sales_report(request):
    """Per-product sales for a period (today/week/month/all) — quantity sold, revenue,
    and a best-sellers ranking, as distinct from category_sales_report's rollup by category.
    """
    from datetime import timedelta
    from django.utils import timezone

    branch, _ = _active_branch(request)
    period = request.GET.get('period', 'today')
    today = timezone.localdate()
    if period == 'week':
        date_from = today - timedelta(days=7)
    elif period == 'month':
        date_from = today - timedelta(days=30)
    elif period == 'all':
        date_from = None
    else:
        period = 'today'
        date_from = today

    items = (OrderItem.objects
             .filter(is_void=False)
             .exclude(order__status='void')
             .select_related('product', 'variant__size'))
    if branch:
        items = items.filter(order__warehouse=branch)
    if date_from:
        items = items.filter(order__created_at__date__gte=date_from)

    # Group by (product, variant/size) — size is a primary dimension in reporting.
    rows_by_key = {}
    for it in items:
        if not it.product_id:
            continue
        size_label = it.variant.label if it.variant_id else ''
        key = (it.product_id, it.variant_id)
        row = rows_by_key.setdefault(key, {
            'product': it.product,
            'size_label': size_label,
            'quantity': Decimal('0'),
            'revenue': Decimal('0'),
        })
        row['quantity'] += it.quantity
        row['revenue'] += it.subtotal

    rows = sorted(rows_by_key.values(), key=lambda r: r['revenue'], reverse=True)

    return render(request, 'restaurant/product_sales_report.html', {
        'branch': branch, 'rows': rows, 'period': period,
    })


@login_required
@require_permission('financial', 'view')
def driver_owed_report(request):
    branch, _ = _active_branch(request)
    drivers = Driver.objects.filter(branch=branch, is_active=True) if branch else Driver.objects.none()
    rows = [{'driver': d, 'owed': d.owed_today()} for d in drivers]
    return render(request, 'restaurant/driver_owed_report.html', {'branch': branch, 'rows': rows})


@login_required
@require_permission('financial', 'view')
def category_sales_report(request):
    """Sales rolled up by category (via each item's Category FK). Each category's
    reserved code range (e.g. عصائر = 1-1000) is shown alongside for reference — items
    are expected to be coded within their category's range, but grouping here still
    uses the FK, since that's what OrderItem actually links.
    """

    branch, _ = _active_branch(request)
    items = (OrderItem.objects
             .filter(is_void=False)
             .exclude(order__status='void')
             .select_related('product', 'product__category'))
    if branch:
        items = items.filter(order__warehouse=branch)

    categories = Category.objects.filter(is_active=True).order_by('name')
    rows = []
    for cat in categories:
        cat_items = items.filter(product__category=cat)
        # subtotal (quantity * price) computed per-row in Python — Sum('price') alone
        # would ignore quantity and Django can't multiply two annotated fields here
        # without an extra annotate() pass, so this stays simple.
        revenue = sum((it.subtotal for it in cat_items), Decimal('0'))
        qty = sum((it.quantity for it in cat_items), Decimal('0'))
        rows.append({
            'category': cat, 'quantity': qty, 'revenue': revenue,
            'code_range': (f"{cat.code_range_start}-{cat.code_range_end}"
                          if cat.code_range_start and cat.code_range_end else '—'),
        })
    rows.sort(key=lambda r: r['revenue'], reverse=True)

    return render(request, 'restaurant/category_sales_report.html', {'branch': branch, 'rows': rows})


# ─────────────────────────────────────────────
#  RECIPE (BOM) — optional per product. Most cafe items sell with no recipe at all
#  (no ingredient stock deduction); this screen only matters for the few items an
#  owner *does* want to auto-deduct raw ingredients for.
# ─────────────────────────────────────────────

@login_required
@require_permission('products', 'view')
def menu_recipe_list(request):
    """"الوصفة" tab under إدارة المنيو: every menu-category item in one list — with its
    sizes and their prices right there — instead of having to open each product's own
    detail page one by one just to reach its recipe."""
    # Raw materials (خامات) are ingredients, not sellable menu items — they never belong
    # in this list even if someone left them in a menu-flagged category by mistake (they
    # get picked as ingredients *inside* a recipe, not as the product a recipe is for).
    products = (Product.objects.filter(is_active=True, is_raw_material=False, category__is_menu_category=True)
                .select_related('category')
                .prefetch_related('variants__size')
                .order_by('category__name', 'name'))

    rows = []
    for p in products:
        sizes = [{'id': v.id, 'size_id': v.size_id, 'name': v.size.name if v.size_id else 'عام',
                  'price': float(v.price)} for v in p.variants.filter(color='', is_active=True)]
        recipe_size_ids = set(Recipe.objects.filter(product=p, is_active=True).values_list('size_id', flat=True))
        rows.append({
            'product': p,
            'sizes': sizes,
            'has_base_recipe': None in recipe_size_ids,
            'sized_recipe_count': len([s for s in recipe_size_ids if s is not None]),
        })

    return render(request, 'restaurant/menu_recipe_list.html', {'rows': rows})


@login_required
@require_permission('products', 'edit')
def recipe_edit(request, product_id):
    product = get_object_or_404(Product, pk=product_id)

    # Sizes this menu item is actually sold in (its ProductVariants) — each can carry its
    # own recipe. size_id=None ("عام"/base) is always available as a fallback tab, used
    # as-is for items with no sizes and as the default for any size left uncovered.
    sizes = [{'id': None, 'size_id': None, 'label': 'عام (كل الأحجام)', 'price': float(product.price_retail)}]
    for v in product.variants.filter(color='', is_active=True).select_related('size').order_by('size__sort_order'):
        if v.size_id:
            sizes.append({'id': v.id, 'size_id': v.size_id, 'label': v.size.name, 'price': float(v.price)})

    requested_size_id = request.POST.get('size_id') if request.method == 'POST' else request.GET.get('size_id')
    try:
        active_size_id = int(requested_size_id) if requested_size_id else None
    except (TypeError, ValueError):
        active_size_id = None
    if active_size_id not in [s['size_id'] for s in sizes]:
        active_size_id = None

    recipe = Recipe.objects.filter(product=product, size_id=active_size_id).first()

    if request.method == 'POST':
        action = request.POST.get('action')

        if action == 'remove_recipe':
            if recipe:
                recipe.delete()
                messages.success(request, "تم إلغاء الوصفة — الصنف هيتباع من غير خصم أي خامة من المخزون.")
            return redirect(f"{reverse('restaurant:recipe_edit', args=[product.id])}?size_id={active_size_id or ''}")

        # action == 'save' (create the recipe on first save, or update its items)
        if recipe is None:
            recipe = Recipe.objects.create(product=product, size_id=active_size_id)

        recipe.notes = request.POST.get('notes', '').strip()
        recipe.is_active = request.POST.get('is_active') == 'on'
        recipe.save(update_fields=['notes', 'is_active'])

        recipe.items.all().delete()
        row_types = request.POST.getlist('row_type')
        ingredient_ids = request.POST.getlist('ingredient_id')
        sub_recipe_ids = request.POST.getlist('sub_recipe_id')
        quantities = request.POST.getlist('quantity')
        units = request.POST.getlist('unit')
        for row_type, ing_id, sr_id, qty, unit in zip(row_types, ingredient_ids, sub_recipe_ids, quantities, units):
            if not qty:
                continue
            try:
                qty_val = Decimal(str(qty))
            except Exception:
                continue
            if qty_val <= 0:
                continue

            if row_type == 'sub_recipe':
                if not sr_id:
                    continue
                sub_recipe = SubRecipe.objects.filter(id=sr_id).first()
                if not sub_recipe:
                    continue
                RecipeItem.objects.create(recipe=recipe, sub_recipe=sub_recipe, quantity=qty_val, unit='')
            else:
                if not ing_id:
                    continue
                ingredient = Product.objects.filter(id=ing_id).first()
                if not ingredient:
                    continue
                # A unit outside this ingredient's convertible family (see compatible_units)
                # is meaningless — fall back to the ingredient's own unit rather than save
                # something base_quantity can't convert.
                valid_units = {u for u, _ in compatible_units(ingredient.unit_measure)}
                if unit not in valid_units:
                    unit = ingredient.unit_measure
                RecipeItem.objects.create(recipe=recipe, ingredient=ingredient, quantity=qty_val, unit=unit)

        messages.success(request, "تم حفظ الوصفة بنجاح.")
        return redirect(f"{reverse('restaurant:recipe_edit', args=[product.id])}?size_id={active_size_id or ''}")

    # Only raw materials show as pickable ingredients — a recipe consumes خامات, not
    # other sellable menu items (which get their own "إضافة منتج" flow).
    ingredients = (Product.objects.filter(is_active=True, is_raw_material=True).exclude(id=product.id)
                   .order_by('name').only('id', 'name', 'sku', 'unit_measure'))
    sub_recipes = SubRecipe.objects.filter(is_active=True).order_by('name')
    recipe_items = recipe.items.select_related('ingredient', 'sub_recipe').all() if recipe else []

    # Per ingredient: which units a recipe line may be entered in (its own unit, plus
    # any unit convertible to it — e.g. an ingredient tracked in KG also accepts G).
    # base_quantity converts whatever's chosen back into the ingredient's own unit before
    # stock is actually deducted, so choosing "G" for a KG-tracked ingredient is safe.
    ingredient_unit_choices = {
        ing.id: [{'value': u, 'label': label} for u, label in compatible_units(ing.unit_measure)]
        for ing in ingredients
    }
    ingredient_base_units = {ing.id: ing.unit_measure for ing in ingredients}

    # Which sizes already have their own recipe — shown as a dot/badge on their tab.
    covered_size_ids = set(Recipe.objects.filter(product=product, is_active=True).values_list('size_id', flat=True))

    return render(request, 'restaurant/recipe_edit.html', {
        'product': product, 'recipe': recipe, 'recipe_items': recipe_items,
        'ingredients': ingredients, 'sub_recipes': sub_recipes,
        'sizes': sizes, 'active_size_id': active_size_id, 'covered_size_ids': covered_size_ids,
        'ingredient_unit_choices_json': json.dumps(ingredient_unit_choices),
        'ingredient_base_units_json': json.dumps(ingredient_base_units),
    })


# ─────────────────────────────────────────────
#  SUB-RECIPES (وصفات فرعية) — reusable batch recipes (e.g. عجينة) pulled into one or
#  more product recipes as a single line, instead of repeating the same ingredients in
#  every product/size recipe that uses them.
# ─────────────────────────────────────────────

@login_required
@require_permission('products', 'view')
def subrecipe_list(request):
    sub_recipes = SubRecipe.objects.all().order_by('name')
    return render(request, 'restaurant/subrecipe_list.html', {'sub_recipes': sub_recipes})


@login_required
@require_permission('products', 'edit')
def subrecipe_edit(request, pk=None):
    sub_recipe = get_object_or_404(SubRecipe, pk=pk) if pk else None

    if request.method == 'POST':
        if request.POST.get('action') == 'delete' and sub_recipe:
            if sub_recipe.used_in_recipes.exists():
                messages.error(request, "لا يمكن حذف هذه الوصفة الفرعية — مستخدمة في وصفة صنف واحد أو أكثر.")
                return redirect('restaurant:subrecipe_edit', pk=sub_recipe.pk)
            sub_recipe.delete()
            messages.success(request, "تم حذف الوصفة الفرعية.")
            return redirect('restaurant:subrecipe_list')

        name = request.POST.get('name', '').strip()
        if not name:
            messages.error(request, "اسم الوصفة الفرعية مطلوب.")
        else:
            if sub_recipe is None:
                sub_recipe = SubRecipe.objects.create(name=name)
            else:
                sub_recipe.name = name
            sub_recipe.notes = request.POST.get('notes', '').strip()
            sub_recipe.is_active = request.POST.get('is_active') == 'on'
            sub_recipe.save()

            sub_recipe.items.all().delete()
            ingredient_ids = request.POST.getlist('ingredient_id')
            quantities = request.POST.getlist('quantity')
            units = request.POST.getlist('unit')
            for ing_id, qty, unit in zip(ingredient_ids, quantities, units):
                if not ing_id or not qty:
                    continue
                try:
                    qty_val = Decimal(str(qty))
                except Exception:
                    continue
                if qty_val <= 0:
                    continue
                ingredient = Product.objects.filter(id=ing_id).first()
                if not ingredient:
                    continue
                valid_units = {u for u, _ in compatible_units(ingredient.unit_measure)}
                if unit not in valid_units:
                    unit = ingredient.unit_measure
                SubRecipeItem.objects.create(sub_recipe=sub_recipe, ingredient=ingredient, quantity=qty_val, unit=unit)

            messages.success(request, "تم حفظ الوصفة الفرعية.")
            return redirect('restaurant:subrecipe_edit', pk=sub_recipe.pk)

    ingredients = (Product.objects.filter(is_active=True, is_raw_material=True)
                   .order_by('name').only('id', 'name', 'sku', 'unit_measure'))
    sub_recipe_items = sub_recipe.items.select_related('ingredient').all() if sub_recipe else []
    ingredient_unit_choices = {
        ing.id: [{'value': u, 'label': label} for u, label in compatible_units(ing.unit_measure)]
        for ing in ingredients
    }

    return render(request, 'restaurant/subrecipe_edit.html', {
        'sub_recipe': sub_recipe, 'sub_recipe_items': sub_recipe_items,
        'ingredients': ingredients,
        'ingredient_unit_choices_json': json.dumps(ingredient_unit_choices),
    })


# ─────────────────────────────────────────────
#  RAW MATERIAL USAGE REPORT — per ingredient: how much has been consumed via recipes
#  (i.e. sold away inside finished menu items) vs. how much stock is left, plus which
#  menu items actually use it.
# ─────────────────────────────────────────────

@login_required
@require_permission('products', 'view')
def raw_material_usage_report(request):
    from datetime import timedelta

    from django.db.models import Sum
    from django.utils import timezone

    from products.models import StockTransaction

    period = request.GET.get('period', 'all')
    today = timezone.localdate()
    if period == 'today':
        date_from = today
    elif period == 'week':
        date_from = today - timedelta(days=7)
    elif period == 'month':
        date_from = today - timedelta(days=30)
    else:
        period = 'all'
        date_from = None

    materials = Product.objects.filter(is_raw_material=True).order_by('name')

    # Recipe consumption is logged by issue_stock() with this exact note prefix (see
    # waiter_open_or_append) — the only way to isolate "used inside a recipe" from other
    # stock movements (purchases, manual adjustments, stocktakes...) on the same ledger.
    consumption_qs = StockTransaction.objects.filter(
        transaction_type='OUT', note__startswith='استهلاك وصفة',
    )
    if date_from:
        consumption_qs = consumption_qs.filter(created_at__date__gte=date_from)

    consumed_by_product = {
        row['product_id']: row['total']
        for row in consumption_qs.values('product_id').annotate(total=Sum('quantity'))
    }

    # Which menu items use each raw material, and how much of it per unit sold —
    # so an owner can see e.g. "بن: 30G goes into كل turkish coffee, كل spanish latte".
    # A recipe line can be a direct ingredient OR a sub_recipe (see RecipeItem) — a
    # sub_recipe row is expanded into ITS ingredients (scaled by batches-per-item) so a
    # raw material only used inside e.g. "عجينة" still shows up here.
    unit_labels = dict(Product.UNIT_CHOICES)
    recipe_items = (RecipeItem.objects.select_related('recipe__product', 'ingredient', 'sub_recipe')
                    .prefetch_related('sub_recipe__items__ingredient')
                    .filter(recipe__is_active=True))
    used_in_by_ingredient = {}
    for ri in recipe_items:
        product_label = ri.recipe.product.name + (f" ({ri.recipe.size.name})" if ri.recipe.size_id else "")
        if ri.ingredient_id:
            unit_code = ri.unit or ri.ingredient.unit_measure
            used_in_by_ingredient.setdefault(ri.ingredient_id, []).append({
                'product_name': product_label,
                'quantity': ri.quantity, 'unit': unit_labels.get(unit_code, unit_code),
            })
        elif ri.sub_recipe_id:
            for sub_item in ri.sub_recipe.items.all():
                unit_code = sub_item.unit or sub_item.ingredient.unit_measure
                used_in_by_ingredient.setdefault(sub_item.ingredient_id, []).append({
                    'product_name': f"{product_label} (عبر: {ri.sub_recipe.name})",
                    'quantity': sub_item.quantity * ri.quantity, 'unit': unit_labels.get(unit_code, unit_code),
                })

    rows = []
    for m in materials:
        rows.append({
            'material': m,
            'consumed': consumed_by_product.get(m.id, Decimal('0')),
            'remaining': m.stock_quantity,
            'used_in': used_in_by_ingredient.get(m.id, []),
        })

    return render(request, 'restaurant/raw_material_usage_report.html', {
        'rows': rows, 'period': period,
    })


# ─────────────────────────────────────────────
#  MODIFIER GROUPS (مستوى السكر، الحجم، إضافات...) — fixed-choice options for a menu
#  item, so a waiter/cashier taps a button instead of typing the same note every time.
# ─────────────────────────────────────────────

@login_required
@require_permission('products', 'view')
def modifier_group_list(request):
    groups = (MenuModifierGroup.objects.prefetch_related('options', 'products', 'categories')
              .order_by('sort_order', 'name'))
    return render(request, 'restaurant/modifier_group_list.html', {'groups': groups})


@login_required
@require_permission('products', 'edit')
def modifier_group_create(request):
    return _modifier_group_form(request, group=None)


@login_required
@require_permission('products', 'edit')
def modifier_group_update(request, pk):
    group = get_object_or_404(MenuModifierGroup, pk=pk)
    return _modifier_group_form(request, group=group)


def _modifier_group_form(request, group):
    all_products = Product.objects.filter(is_active=True, is_raw_material=False).order_by('name')
    all_categories = Category.objects.filter(is_active=True).order_by('name')

    if request.method == 'POST':
        name = (request.POST.get('name') or '').strip()
        if not name:
            messages.error(request, "اسم المجموعة مطلوب.")
        else:
            if group is None:
                group = MenuModifierGroup.objects.create(name=name)
            else:
                group.name = name
            group.is_required = request.POST.get('is_required') == 'on'
            group.show_on_receipt = request.POST.get('show_on_receipt') == 'on'
            try:
                group.min_select = int(request.POST.get('min_select') or 0)
            except ValueError:
                group.min_select = 0
            try:
                group.max_select = int(request.POST.get('max_select') or 1)
            except ValueError:
                group.max_select = 1
            group.save()

            group.products.set(request.POST.getlist('product_ids'))
            group.categories.set(request.POST.getlist('category_ids'))

            group.options.all().delete()
            names = request.POST.getlist('option_name')
            deltas = request.POST.getlist('option_price_delta')
            for i, (opt_name, delta) in enumerate(zip(names, deltas)):
                opt_name = opt_name.strip()
                if not opt_name:
                    continue
                try:
                    delta_val = Decimal(str(delta or 0))
                except (InvalidOperation, TypeError):
                    delta_val = Decimal('0')
                MenuModifier.objects.create(group=group, name=opt_name, price_delta=delta_val, sort_order=i)

            messages.success(request, "تم حفظ مجموعة الاختيارات بنجاح.")
            return redirect('restaurant:modifier_group_list')

    return render(request, 'restaurant/modifier_group_form.html', {
        'group': group,
        'title': 'تعديل مجموعة اختيارات' if group else 'إضافة مجموعة اختيارات',
        'all_products': all_products,
        'all_categories': all_categories,
        'selected_product_ids': list(group.products.values_list('id', flat=True)) if group else [],
        'selected_category_ids': list(group.categories.values_list('id', flat=True)) if group else [],
        'options': list(group.options.order_by('sort_order').values('name', 'price_delta')) if group else [],
    })


@login_required
@require_permission('products', 'edit')
@require_POST
def modifier_group_delete(request, pk):
    group = get_object_or_404(MenuModifierGroup, pk=pk)
    group.delete()
    messages.success(request, "تم حذف مجموعة الاختيارات.")
    return redirect('restaurant:modifier_group_list')
