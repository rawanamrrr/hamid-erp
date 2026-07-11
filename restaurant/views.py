import json
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction as db_transaction
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.dateparse import parse_datetime
from django.views.decorators.http import require_POST

from accounts.permissions import has_permission, require_permission
from products.models import Category, Product, Warehouse
from sales.models import Order, OrderItem
from sales.utils import get_active_shift

from .consumers import push_event
from .models import (
    CashCustody, Driver, MenuModifier, MenuModifierGroup, Recipe, RecipeItem, Section, Table,
    compatible_units,
)


def _recompute_order_totals(order):
    """Recalculate subtotal/service-charge/total from current non-void items.

    Shared by add-items and void-item so a void always re-derives the service charge
    from the surviving subtotal instead of leaving a stale (too-high) charge behind.
    Caller is responsible for order.save(update_fields=[...]).
    """
    from sales.services import compute_dine_in_service_charge, compute_vat_amount

    order.subtotal_amount = sum((i.subtotal for i in order.items.filter(is_void=False)), Decimal('0'))
    order.service_charge = compute_dine_in_service_charge(order.subtotal_amount, order.order_type)
    # VAT and the service charge are each an independent percentage of the same
    # pre-extras total, added side by side — not compounded on top of each other.
    pre_extras_total = (order.subtotal_amount - Decimal(str(order.discount))
                        + Decimal(str(order.delivery_cost)))
    order.vat_amount = compute_vat_amount(pre_extras_total)
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
        return redirect(f"{request.path}?branch_id={branch.id}")

    sections = Section.objects.filter(branch=branch, is_active=True).prefetch_related('tables')
    tables = Table.objects.filter(branch=branch, is_active=True).select_related('section')
    drivers = Driver.objects.filter(branch=branch, is_active=True)

    return render(request, 'restaurant/setup.html', {
        'branch': branch, 'warehouses': warehouses,
        'sections': sections, 'tables': tables, 'drivers': drivers,
    })


# ─────────────────────────────────────────────
#  WAITER SCREEN — table map
# ─────────────────────────────────────────────

@login_required
@require_permission('pos', 'view')
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
                'product_name': it.product.name if it.product else '',
                'variant_label': it.variant.label if it.variant_id else '',
                'quantity': float(it.quantity),
                'subtotal': float(it.subtotal),
                'is_void': it.is_void,
                'note': it.note,
                'modifiers': it.modifiers or [],
            } for it in order.items.all()],
        })

    return {
        'categories': categories,
        'order_json': order_json,
        'modifier_map_json': json.dumps(modifier_map),
        'variant_map_json': json.dumps(variant_map),
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
        if product.category_id and product.category.is_menu_category:
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

        # Hybrid recipes: if this menu item has a BOM, deduct its ingredients too.
        # Missing/insufficient ingredient stock never blocks the sale of the menu item —
        # a cafe still serves the drink; the shortfall shows up on the stock report.
        recipe = getattr(product, 'recipe', None)
        if recipe and recipe.is_active:
            for ri in recipe.items.select_related('ingredient').all():
                try:
                    # base_quantity converts the recipe line's entered unit (e.g. "30 G")
                    # into the ingredient's own tracked unit (e.g. KG) before deducting —
                    # issue_stock always operates in the ingredient's own unit.
                    issue_stock(
                        ri.ingredient, branch, ri.base_quantity * qty,
                        user=request.user, reference=f"Order #{order.id}",
                        note=f"استهلاك وصفة: {product.name}",
                        allow_negative=True,
                    )
                except ValueError:
                    pass

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
@require_permission('pos', 'view')
def waiter_order_screen(request, table_id):
    """Build/edit the open check for a table: category grid + running cart."""
    branch, _ = _active_branch(request)
    table = get_object_or_404(Table, pk=table_id)
    order = table.open_order

    context = _waiter_menu_context(order)
    context.update({'branch': branch, 'table': table, 'order': order})
    return render(request, 'restaurant/waiter_order.html', context)


@login_required
@require_permission('pos', 'create')
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

    shift = get_active_shift()

    with db_transaction.atomic():
        order = table.open_order
        created = False
        if order is None:
            order = Order.objects.create(
                user=request.user,
                shift=shift,
                warehouse=branch,
                order_type=Order.ORDER_TYPE_DINE_IN,
                table=table,
                waiter=request.user,
                is_open=True,
                is_completed=False,
            )
            table.status = Table.STATUS_BUSY
            table.save(update_fields=['status'])
            created = True

        _add_items_to_order(request, order, items, branch)

    return JsonResponse({'status': 'ok', 'order_id': order.id, 'created': created,
                         'total_amount': float(order.total_amount)})


@login_required
@require_permission('pos', 'create')
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
    order = Order.objects.create(
        user=request.user,
        shift=shift,
        warehouse=branch,
        order_type=Order.ORDER_TYPE_DINE_IN,
        table=None,
        waiter=request.user,
        is_open=True,
        is_completed=False,
    )
    return JsonResponse({'status': 'ok', 'order_id': order.id})


@login_required
@require_permission('pos', 'view')
def waiter_order_screen_no_table(request, order_id):
    """Same building UI as waiter_order_screen, for an order with no table (takeaway
    order started via waiter_new_order_no_table)."""
    branch, _ = _active_branch(request)
    order = get_object_or_404(Order, pk=order_id, table__isnull=True)

    context = _waiter_menu_context(order)
    context.update({'branch': branch, 'table': None, 'order': order})
    return render(request, 'restaurant/waiter_order.html', context)


@login_required
@require_permission('pos', 'create')
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
@require_permission('pos', 'void')
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
@require_permission('pos', 'void')
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
@require_permission('pos', 'view')
def kds_view(request):
    """One ticket card per ORDER (table), not per item — a table with 2 coffees is a
    single ticket the kitchen preps together, not two separate cards.
    """
    from django.utils import timezone

    branch, _ = _active_branch(request)
    if not branch:
        messages.error(request, "لا يوجد فرع نشط متاح لك.")
        return redirect('dashboard')

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

    # A mixed ticket (one item already 'preparing', another still 'new') shows/acts on
    # its least-advanced item, so the whole ticket always has one obvious next action.
    status_priority = {OrderItem.KITCHEN_NEW: 0, OrderItem.KITCHEN_PREPARING: 1, OrderItem.KITCHEN_READY: 2}
    for ticket in tickets:
        ticket['status'] = min((it.kitchen_status for it in ticket['items']),
                               key=lambda s: status_priority.get(s, 0))

    return render(request, 'restaurant/kds.html', {'branch': branch, 'tickets': tickets})


@login_required
@require_permission('pos', 'view')
def kitchen_ticket_preview(request, order_id):
    """Browser-printable ticket for one order (window.print() fallback) — for stations
    with no printer_target wired up yet, or a manual reprint of the whole check."""
    order = get_object_or_404(Order, pk=order_id)
    items = order.items.filter(is_void=False).select_related('product')
    return render(request, 'restaurant/kitchen_ticket.html', {
        'order': order, 'items': items,
        'station': {'name': 'تذكرة المطبخ'},
    })


@login_required
@require_permission('pos', 'edit')
@require_POST
def kds_set_status(request, item_id):
    item = get_object_or_404(OrderItem, pk=item_id)
    new_status = request.POST.get('status') or (json.loads(request.body or b'{}').get('status') if request.body else None)
    valid = dict(OrderItem.KITCHEN_STATUS_CHOICES)
    if new_status not in valid:
        return JsonResponse({'status': 'error', 'message': 'حالة غير صالحة'}, status=400)

    item.kitchen_status = new_status
    item.save(update_fields=['kitchen_status'])

    if item.order.warehouse_id:
        push_event('kds', item.order.warehouse_id, {
            'event': 'item_status', 'item_id': item.id, 'status': new_status,
        })

    return JsonResponse({'status': 'ok'})


@login_required
@require_permission('pos', 'edit')
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

    order.items.filter(is_void=False).exclude(kitchen_status=OrderItem.KITCHEN_SERVED).update(kitchen_status=new_status)

    if order.warehouse_id:
        push_event('kds', order.warehouse_id, {
            'event': 'order_items_status', 'order_id': order.id, 'status': new_status,
        })

    return JsonResponse({'status': 'ok'})


# ─────────────────────────────────────────────
#  DELIVERY
# ─────────────────────────────────────────────

@login_required
@require_permission('pos', 'view')
def delivery_dashboard(request):
    branch, _ = _active_branch(request)
    if not branch:
        messages.error(request, "لا يوجد فرع نشط متاح لك.")
        return redirect('dashboard')

    drivers = list(Driver.objects.filter(branch=branch, is_active=True))
    for d in drivers:
        d.owed = d.owed_today()
    open_orders = Order.objects.filter(
        warehouse=branch, order_type=Order.ORDER_TYPE_DELIVERY,
    ).exclude(status='void').select_related('driver', 'customer').order_by('-created_at')[:100]

    driver_owed = {d.id: d.owed for d in drivers}

    return render(request, 'restaurant/delivery.html', {
        'branch': branch, 'drivers': drivers, 'orders': open_orders, 'driver_owed': driver_owed,
    })


@login_required
@require_permission('pos', 'edit')
@require_POST
def assign_driver(request, order_id):
    order = get_object_or_404(Order, pk=order_id)
    driver_id = request.POST.get('driver_id')
    driver = get_object_or_404(Driver, pk=driver_id)
    order.driver = driver
    order.order_type = Order.ORDER_TYPE_DELIVERY
    order.save(update_fields=['driver', 'order_type'])

    if order.warehouse_id:
        push_event('delivery', order.warehouse_id, {
            'event': 'order_assigned', 'order_id': order.id, 'driver_id': driver.id,
        })
    return JsonResponse({'status': 'ok'})


@login_required
@require_permission('pos', 'edit')
@require_POST
def driver_return_settle(request, order_id):
    """Driver came back: mark delivered, and open a CashCustody for the cash they collected.

    Cash-on-delivery orders are typically created unpaid (order.cash_paid == 0 — the
    payment doesn't exist yet at order time, only once the driver actually collects it
    at the door), so the amount the driver is holding must be entered here rather than
    read off the order. Falls back to the order's outstanding amount so a prepaid order
    (cash_paid already set) still works without extra input.
    """
    order = get_object_or_404(Order, pk=order_id)
    if not order.driver_id:
        return JsonResponse({'status': 'error', 'message': 'لا يوجد طيار مرتبط بهذا الطلب'}, status=400)

    collected_raw = request.POST.get('collected_amount')
    if collected_raw not in (None, ''):
        try:
            amount = Decimal(str(collected_raw))
        except Exception:
            return JsonResponse({'status': 'error', 'message': 'مبلغ غير صالح'}, status=400)
    else:
        outstanding = Decimal(str(order.total_amount)) - Decimal(str(order.received_amount or 0))
        amount = outstanding if outstanding > 0 else (order.cash_paid or Decimal('0'))

    custody = None
    if amount > 0:
        custody = CashCustody.objects.create(
            branch=order.warehouse, kind=CashCustody.KIND_DRIVER,
            holder_name=order.driver.name, driver=order.driver,
            amount=amount, created_by=request.user,
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
    order.cash_paid = (order.cash_paid or Decimal('0')) + amount
    order.received_amount = (order.received_amount or Decimal('0')) + amount
    order.is_completed = True
    order.save(update_fields=['cash_paid', 'received_amount', 'is_completed'])

    if order.warehouse_id:
        push_event('delivery', order.warehouse_id, {
            'event': 'order_returned', 'order_id': order.id,
            'custody_id': custody.id if custody else None,
        })

    return JsonResponse({'status': 'ok', 'custody_id': custody.id if custody else None})


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
@require_permission('pos', 'edit')
@require_POST
def close_check(request, order_id):
    """Close an open tab as cash / visa / CL (آجل — the owner ate it, no cash collected)."""
    order = get_object_or_404(Order, pk=order_id)
    close_type = request.POST.get('close_type')
    if close_type not in dict(Order.CLOSE_TYPE_CHOICES):
        return JsonResponse({'status': 'error', 'message': 'طريقة قفل غير صالحة'}, status=400)

    # Guard against double-closing: record_sale_transaction() below creates a brand new
    # Transaction (and re-posts the journal) every time it's called, so calling this twice
    # on the same order would double the cash-drawer balance and double-book revenue.
    if order.close_type:
        return JsonResponse({'status': 'error', 'message': 'الشيك مُقفل بالفعل'}, status=400)

    with db_transaction.atomic():
        order.close_type = close_type
        order.is_open = False
        order.is_completed = True
        if close_type == Order.CLOSE_TYPE_CASH:
            order.cash_paid = order.total_amount
            order.received_amount = order.total_amount
        elif close_type == Order.CLOSE_TYPE_VISA:
            order.visa_paid = order.total_amount
            order.received_amount = order.total_amount
        elif close_type == Order.CLOSE_TYPE_CL:
            # آجل: not collected as cash — tracked as credit, excluded from cash reconciliation.
            order.credit_paid = order.total_amount
        order.save(update_fields=['close_type', 'is_open', 'is_completed', 'cash_paid',
                                  'visa_paid', 'received_amount', 'credit_paid'])
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
             .select_related('product'))
    if branch:
        items = items.filter(order__warehouse=branch)
    if date_from:
        items = items.filter(order__created_at__date__gte=date_from)

    rows_by_product = {}
    for it in items:
        if not it.product_id:
            continue
        row = rows_by_product.setdefault(it.product_id, {
            'product': it.product, 'quantity': Decimal('0'), 'revenue': Decimal('0'),
        })
        row['quantity'] += it.quantity
        row['revenue'] += it.subtotal
    rows = sorted(rows_by_product.values(), key=lambda r: r['revenue'], reverse=True)

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
             .select_related('product'))
    if branch:
        items = items.filter(order__warehouse=branch)
    if date_from:
        items = items.filter(order__created_at__date__gte=date_from)

    rows_by_product = {}
    for it in items:
        if not it.product_id:
            continue
        row = rows_by_product.setdefault(it.product_id, {
            'product': it.product, 'quantity': Decimal('0'), 'revenue': Decimal('0'),
        })
        row['quantity'] += it.quantity
        row['revenue'] += it.subtotal

    rows = sorted(rows_by_product.values(), key=lambda r: r['revenue'], reverse=True)

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
@require_permission('products', 'edit')
def recipe_edit(request, product_id):
    product = get_object_or_404(Product, pk=product_id)
    recipe = Recipe.objects.filter(product=product).first()

    if request.method == 'POST':
        action = request.POST.get('action')

        if action == 'remove_recipe':
            if recipe:
                recipe.delete()
                messages.success(request, "تم إلغاء الوصفة — الصنف هيتباع من غير خصم أي خامة من المخزون.")
            return redirect('restaurant:recipe_edit', product_id=product.id)

        # action == 'save' (create the recipe on first save, or update its items)
        if recipe is None:
            recipe = Recipe.objects.create(product=product)

        recipe.notes = request.POST.get('notes', '').strip()
        recipe.is_active = request.POST.get('is_active') == 'on'
        recipe.save(update_fields=['notes', 'is_active'])

        recipe.items.all().delete()
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
            # A unit outside this ingredient's convertible family (see compatible_units)
            # is meaningless — fall back to the ingredient's own unit rather than save
            # something base_quantity can't convert.
            valid_units = {u for u, _ in compatible_units(ingredient.unit_measure)}
            if unit not in valid_units:
                unit = ingredient.unit_measure
            RecipeItem.objects.create(recipe=recipe, ingredient=ingredient, quantity=qty_val, unit=unit)

        messages.success(request, "تم حفظ الوصفة بنجاح.")
        return redirect('restaurant:recipe_edit', product_id=product.id)

    # Only raw materials show as pickable ingredients — a recipe consumes خامات, not
    # other sellable menu items (which get their own "إضافة منتج" flow).
    ingredients = (Product.objects.filter(is_active=True, is_raw_material=True).exclude(id=product.id)
                   .order_by('name').only('id', 'name', 'sku', 'unit_measure'))
    recipe_items = recipe.items.select_related('ingredient').all() if recipe else []

    # Per ingredient: which units a recipe line may be entered in (its own unit, plus
    # any unit convertible to it — e.g. an ingredient tracked in KG also accepts G).
    # base_quantity converts whatever's chosen back into the ingredient's own unit before
    # stock is actually deducted, so choosing "G" for a KG-tracked ingredient is safe.
    ingredient_unit_choices = {
        ing.id: [{'value': u, 'label': label} for u, label in compatible_units(ing.unit_measure)]
        for ing in ingredients
    }
    ingredient_base_units = {ing.id: ing.unit_measure for ing in ingredients}

    return render(request, 'restaurant/recipe_edit.html', {
        'product': product, 'recipe': recipe, 'recipe_items': recipe_items,
        'ingredients': ingredients,
        'ingredient_unit_choices_json': json.dumps(ingredient_unit_choices),
        'ingredient_base_units_json': json.dumps(ingredient_base_units),
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
    unit_labels = dict(Product.UNIT_CHOICES)
    recipe_items = (RecipeItem.objects.select_related('recipe__product', 'ingredient')
                    .filter(recipe__is_active=True))
    used_in_by_ingredient = {}
    for ri in recipe_items:
        unit_code = ri.unit or ri.ingredient.unit_measure
        used_in_by_ingredient.setdefault(ri.ingredient_id, []).append({
            'product_name': ri.recipe.product.name,
            'quantity': ri.quantity, 'unit': unit_labels.get(unit_code, unit_code),
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
