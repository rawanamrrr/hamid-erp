"""
Inventory insights (Phase 10.5-lite): demand velocity, days-of-cover, reorder urgency,
and dead-stock detection — derived from sales history. Read-only.
"""
from datetime import timedelta
from decimal import Decimal


def inventory_insights(window_days=90, dead_days=60, lead_days=14):
    from django.db.models import Sum, Max
    from django.utils import timezone
    from products.models import Product
    from sales.models import OrderItem, Order

    today = timezone.localdate()
    start = today - timedelta(days=window_days)

    active_orders = Order.objects.active().filter(created_at__date__gte=start)
    # Units sold and last sale date per product within the window.
    agg = (OrderItem.objects.filter(order__in=active_orders, product__isnull=False)
           .values('product_id')
           .annotate(sold=Sum('quantity'), last_sale=Max('order__created_at')))
    sold_map = {r['product_id']: r for r in agg}

    rows, dead = [], []
    window = Decimal(str(window_days))
    for p in Product.objects.filter(is_active=True):
        stock = p.stock_quantity or Decimal('0')
        rec = sold_map.get(p.id)
        sold = Decimal(str(rec['sold'])) if rec else Decimal('0')
        last_sale = rec['last_sale'] if rec else None
        velocity = (sold / window) if window > 0 else Decimal('0')  # units/day
        days_cover = (stock / velocity) if velocity > 0 else None

        if stock > 0 and sold == 0:
            status = 'dead'
        elif velocity > 0 and days_cover is not None and days_cover <= lead_days:
            status = 'reorder'
        elif velocity == 0:
            status = 'idle'
        else:
            status = 'healthy'

        row = {
            'product': p, 'stock': stock, 'sold': sold,
            'velocity': velocity, 'days_cover': days_cover,
            'last_sale': last_sale, 'status': status,
        }
        if status == 'dead':
            dead.append(row)
        else:
            rows.append(row)

    # Most urgent first: reorder (lowest days_cover) at top.
    rows.sort(key=lambda r: (r['status'] != 'reorder',
                             r['days_cover'] if r['days_cover'] is not None else 1e9))
    dead.sort(key=lambda r: r['stock'], reverse=True)
    return {
        'rows': rows, 'dead': dead,
        'window_days': window_days, 'dead_days': dead_days, 'lead_days': lead_days,
        'reorder_count': sum(1 for r in rows if r['status'] == 'reorder'),
        'dead_count': len(dead),
    }
