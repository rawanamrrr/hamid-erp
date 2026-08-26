"""restaurant/services.py — shared "send this order to the kitchen" logic.

The waiter's add-items flow (waiter_open_or_append) already pushes new items to the
KDS/cashier queue and attempts a prep-ticket print. A sale rung up directly by a
cashier at the POS (sales.views.submit_order_ajax) went through a completely separate
code path that never touched the kitchen at all — a menu item sold that way silently
never reached the kitchen. This is the single place both flows should call into.
"""
from .consumers import push_event


def build_modifier_map():
    """{product_id: [{group, is_required, max_select, options: [...]}]} — every fixed-
    choice group (مستوى السكر، إضافات...) applicable to each product, whether linked
    directly or via its category. Shared by the waiter cart AND the cashier POS, so a
    group set up once (restaurant:modifier_group_list) shows up as a picker in both —
    previously only the waiter screen ever read this data at all.
    """
    from .models import MenuModifierGroup

    modifier_map = {}
    for g in MenuModifierGroup.objects.prefetch_related('options', 'products', 'categories'):
        options = [{'id': o.id, 'name': o.name, 'price_delta': float(o.price_delta)}
                   for o in g.options.filter(is_active=True)]
        if not options:
            continue
        group_data = {'group': g.name, 'is_required': g.is_required,
                      'max_select': g.max_select, 'options': options,
                      'show_on_receipt': g.show_on_receipt}
        for p in g.products.all():
            modifier_map.setdefault(p.id, []).append(group_data)
        for c in g.categories.all():
            for p in c.products.filter(is_active=True):
                modifier_map.setdefault(p.id, []).append(group_data)
    return modifier_map


def notify_kitchen_for_order(request, order, items=None):
    """Push `order` to the KDS + cashier queue and attempt to print prep tickets — but
    only for menu-category items (cafe drinks/food); a retail item (e.g. an espresso
    machine sold at the counter) has nothing for the kitchen to prepare.

    Safe no-op if the order has no kitchen-relevant items, or no warehouse/branch.

    Returns True when a ticket actually reached a printer, so the caller can tell the
    browser not to also put the ticket preview on screen — see _print_kitchen_tickets.
    """
    if not order.warehouse_id:
        return False

    candidate_items = items if items is not None else order.items.filter(is_void=False)
    kitchen_items = [
        it for it in candidate_items
        if it.product_id and it.product.category_id and it.product.category.is_menu_category
    ]
    if not kitchen_items:
        return False

    push_event('kds', order.warehouse_id, {
        'event': 'order_updated', 'order_id': order.id,
        'table': order.table.number if order.table_id else None,
    })
    push_event('cashier', order.warehouse_id, {
        'event': 'order_updated', 'order_id': order.id,
    })

    from .views import _print_kitchen_tickets
    return _print_kitchen_tickets(request, order, kitchen_items)
