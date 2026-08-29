from django.shortcuts import render, resolve_url, redirect
from django.core.exceptions import PermissionDenied
from django.db.models import Sum, F, Q, DecimalField, Value
from django.db.models.functions import Coalesce
from django.contrib.auth.decorators import login_required
from accounts.permissions import require_permission, has_permission, require_profit_view, dashboard_widget_visible, has_granular_action, cashier_can_open_shift, cashier_can_close_shift
from django.contrib.auth.models import User
from products.models import Product, Supplier, StockTransaction
from django.utils import timezone
from datetime import datetime, timedelta
from decimal import Decimal
import json

# --- Helper Function for Permissions ---
def is_manager(user):
    """
    Consistency helper: checks if a user has dashboard view permission.
    """
    return has_permission(user, 'dashboard', 'view')


def _exclude_voided_orders_q(field_prefix=''):
    """A Q() that excludes 'OUT' StockTransaction rows belonging to a voided Order.

    A voided order's original OUT transaction is deliberately kept (no hard delete of
    the ledger — see sales/views.py delete_order_ajax) with a separate RET_IN reversal
    added alongside it. StockTransaction has no FK to Order, only a free-text
    `reference_number` that the sale flow sets to str(order.id) — so revenue/profit
    widgets built directly from StockTransaction (unlike financial_statement/
    daily_summary, which read Order.objects.active()) must filter it out explicitly or
    a voided invoice inflates these figures forever.

    `field_prefix` lets callers reach the field through a related-manager annotation
    (e.g. 'transactions__' when filtering Product.objects.annotate(...) via its
    `transactions` reverse relation) instead of querying StockTransaction directly.
    """
    from sales.models import Order
    voided_ids = [str(i) for i in Order.objects.filter(status='void').values_list('id', flat=True)]
    if not voided_ids:
        return Q()
    return ~Q(**{f'{field_prefix}reference_number__in': voided_ids})


def _exclude_unpaid_orders_q(field_prefix=''):
    """A Q() that excludes 'OUT' StockTransaction rows belonging to an order that
    hasn't actually been paid yet (Order.is_completed=False).

    A waiter sending items to the kitchen (dine-in tab, table or no-table) creates the
    order and its StockTransaction rows immediately — is_completed only flips to True
    once the check is closed/paid (see restaurant/views.py close_check). Same story for
    a cash-on-delivery order: is_completed stays False until driver_return_settle. Without
    this exclusion, every revenue/VAT-relevant widget built from StockTransaction (or
    from Order directly) would count the sale the moment it's sent to the kitchen, long
    before any money actually changed hands.
    """
    from sales.models import Order
    unpaid_ids = [str(i) for i in Order.objects.filter(is_completed=False).values_list('id', flat=True)]
    if not unpaid_ids:
        return Q()
    return ~Q(**{f'{field_prefix}reference_number__in': unpaid_ids})

@login_required
def dashboard(request):
    # Waiters/cashiers land straight on their work screen instead of hitting a 403 on
    # the analytics dashboard they usually have no permission to view. Delegates to
    # get_best_landing_url() — the single source of truth for this decision (also used
    # right after login) — instead of duplicating its own (and, previously, inconsistent)
    # copy of the same default_landing→URL mapping here.
    if not has_permission(request.user, 'dashboard', 'view'):
        from accounts.permissions import get_best_landing_url
        target = get_best_landing_url(request.user)
        if target != resolve_url('dashboard'):
            return redirect(target)

    if not has_permission(request.user, 'dashboard', 'view'):
        raise PermissionDenied("You do not have permission to perform this action.")

    # --- Date Filters ---
    date_from = request.GET.get('date_from')
    date_to = request.GET.get('date_to')

    # Base Filter for Sales Queries (Global Stats)
    sales_filter = Q(transaction_type='OUT') & _exclude_voided_orders_q() & _exclude_unpaid_orders_q()
    if date_from:
        sales_filter &= Q(created_at__date__gte=date_from)
    if date_to:
        sales_filter &= Q(created_at__date__lte=date_to)

    # 1. Basic Counters
    total_products = Product.objects.count()
    total_suppliers = Supplier.objects.count()
    # Kitchen-routed menu items (prepared on demand) never carry a meaningful stock count —
    # excluded here so a cafe's drinks/desserts don't inflate the "low/out of stock" widgets.
    # Raw materials are kept even if they fall back to the generic "بدون قسم" category
    # (is_menu_category=True) since they have no category field of their own.
    _stock_tracked = Product.objects.exclude(
        Q(category__is_menu_category=True) & Q(is_raw_material=False) & Q(track_stock_no_recipe=False)
    )
    low_stock_count = _stock_tracked.filter(stock_quantity__lte=F('low_stock_threshold'), stock_quantity__gt=0).count()
    out_of_stock_count = _stock_tracked.filter(stock_quantity__lte=0).count()
    total_users = User.objects.count()

    # Expiry — batches entered with an expiry date via a purchase invoice, still holding
    # stock. "قارب على الانتهاء" mirrors expiry_report's own 30-day near-expiry window.
    from products.models import StockBatch
    _today = timezone.localdate()
    _expiring_batches = StockBatch.objects.filter(current_quantity__gt=0, expiry_date__isnull=False)
    expired_batch_count = _expiring_batches.filter(expiry_date__lt=_today).count()
    near_expiry_batch_count = _expiring_batches.filter(expiry_date__gte=_today, expiry_date__lte=_today + timedelta(days=30)).count()
    
    stock_value = Product.objects.aggregate(
        total_value=Sum(F('stock_quantity') * F('cost_price'))
    )['total_value'] or 0

    # 2. Sales Stats
    sales_qs = StockTransaction.objects.filter(sales_filter)
    total_sales_ops = sales_qs.count()
    total_items_sold = sales_qs.aggregate(total=Sum('quantity'))['total'] or 0
    
    revenue = 0
    for sale in sales_qs:
        revenue += sale.total_price

    # Work-order (تفصيل) revenue isn't a stock OUT transaction, so it never showed up in
    # `revenue` above — add it in separately, and keep it as its own figure too.
    from sales.models import Order
    tailoring_filter = Q(is_tailoring=True)
    if date_from:
        tailoring_filter &= Q(created_at__date__gte=date_from)
    if date_to:
        tailoring_filter &= Q(created_at__date__lte=date_to)
    # Only count what's actually been collected (deposit/received), not the full job
    # price — otherwise revenue and cash liquidity would disagree with what's really
    # in the drawer until the job is fully paid off.
    tailoring_revenue = Order.objects.filter(tailoring_filter).exclude(status='void').aggregate(
        total=Sum('received_amount'))['total'] or 0
    revenue = float(revenue) + float(tailoring_revenue)

    # Dine-in service charge is real earned income (unlike VAT, which is only ever
    # collected on the government's behalf) but never creates a StockTransaction line,
    # so it never showed up in `revenue` above either — add it in separately, same as
    # tailoring. This is what a cashier-created dine-in order was missing entirely.
    service_charge_filter = Q(service_charge__gt=0)
    if date_from:
        service_charge_filter &= Q(created_at__date__gte=date_from)
    if date_to:
        service_charge_filter &= Q(created_at__date__lte=date_to)
    service_charge_revenue = Order.objects.filter(service_charge_filter, is_completed=True).exclude(status='void').aggregate(
        total=Sum('service_charge'))['total'] or 0
    revenue = revenue + float(service_charge_revenue)

    # When VAT is configured as "included in price" (settings.policies
    # 'tax.vat_included_in_price'), the line prices summed into `revenue` above still have
    # that VAT baked into them — e.g. a 100 EGP item with 12 EGP of included VAT should
    # only count 88 as real revenue, the other 12 belongs in the VAT report/payable, not
    # here. This never affected the "not included" (added-on-top) mode, since that VAT is
    # a separate order-level addition that never touches a line's own price to begin with.
    orders_for_vat = Order.objects.active().filter(is_completed=True)
    if date_from:
        orders_for_vat = orders_for_vat.filter(created_at__date__gte=date_from)
    if date_to:
        orders_for_vat = orders_for_vat.filter(created_at__date__lte=date_to)
    included_vat_total = sum(
        (vb['tax'] for vb in (o.vat_breakdown() for o in orders_for_vat) if vb and vb['included']),
        Decimal('0'),
    )
    revenue = revenue - float(included_vat_total)

    # Net out sales returns — otherwise this KPI only ever grows, even for goods that
    # came back (the income-statement report already nets this via post_refund's
    # 'Sales Returns' contra-revenue journal line; this ad-hoc widget didn't).
    #
    # total_refund_amount is the GROSS amount actually handed back to the customer (VAT
    # included when the original sale's price was), but `revenue` above is already NET
    # (VAT stripped out) — so only (total_refund_amount − vat_amount) of each return
    # should come back out of it, or a return would subtract more than that sale ever
    # added to revenue in the first place.
    from sales.models import ReturnInvoice
    returns_filter = Q()
    if date_from:
        returns_filter &= Q(created_at__date__gte=date_from)
    if date_to:
        returns_filter &= Q(created_at__date__lte=date_to)
    returns_agg = ReturnInvoice.objects.filter(returns_filter).aggregate(
        total=Sum('total_refund_amount'), vat=Sum('vat_amount'))
    total_returns = (returns_agg['total'] or 0) - (returns_agg['vat'] or 0)
    revenue = revenue - float(total_returns)

    # Same problem hits "عمليات البيع" and "القطع المباعة" — both are built purely from
    # StockTransaction 'OUT' rows above, so a returned item still counts as sold forever.
    # Net out the returned lines/quantities the same way `revenue` was netted out.
    from sales.models import ReturnItem
    return_items_qs = ReturnItem.objects.filter(return_invoice__in=ReturnInvoice.objects.filter(returns_filter))
    returned_ops = return_items_qs.count()
    returned_qty = return_items_qs.aggregate(total=Sum('quantity'))['total'] or 0
    total_sales_ops = total_sales_ops - returned_ops
    total_items_sold = total_items_sold - returned_qty

    # Recent Transactions
    recent_qs = StockTransaction.objects.select_related('product').order_by('-created_at')
    
    if date_from or date_to:
        general_filter = Q()
        if date_from: general_filter &= Q(created_at__date__gte=date_from)
        if date_to: general_filter &= Q(created_at__date__lte=date_to)
        recent_transactions = recent_qs.filter(general_filter)[:50]
    else:
        recent_transactions = recent_qs[:5]

    # 3. Product Analytics for Realized Profit
    annotation_filter = Q(transactions__transaction_type='OUT') & _exclude_voided_orders_q('transactions__')
    if date_from:
        annotation_filter &= Q(transactions__created_at__date__gte=date_from)
    if date_to:
        annotation_filter &= Q(transactions__created_at__date__lte=date_to)

    # Raw materials (flour, sugar, milk...) aren't sold directly to a customer — they're
    # only ever consumed via a recipe, so they have no place in a "best sellers" widget.
    products_qs = Product.objects.exclude(is_raw_material=True).annotate(
        total_sold=Coalesce(Sum('transactions__quantity', filter=annotation_filter), Value(0, output_field=DecimalField())),
        total_revenue_val=Coalesce(Sum((F('transactions__quantity') * F('transactions__unit_price')) - F('transactions__discount'), filter=annotation_filter, output_field=DecimalField()), Value(0, output_field=DecimalField())),
        total_cost_of_sales=Coalesce(Sum(F('transactions__quantity') * F('cost_price'), filter=annotation_filter, output_field=DecimalField()), Value(0, output_field=DecimalField()))
    )

    for p in products_qs:
        p.realized_profit = float(p.total_revenue_val) - float(p.total_cost_of_sales)

    # Sort for Widgets
    top_winners = sorted([p for p in products_qs if p.total_sold > 0], key=lambda x: x.realized_profit, reverse=True)[:5]
    bottom_performers = sorted([p for p in products_qs if p.total_sold > 0], key=lambda x: x.realized_profit)[:5]
    winners_count = len([p for p in products_qs if p.realized_profit > 0])

    # Sparkline data
    end_date = timezone.localtime(timezone.now()).date()
    if date_to:
        try: end_date = datetime.strptime(date_to, '%Y-%m-%d').date()
        except ValueError: pass
            
    start_date = end_date - timedelta(days=6)
    history_data = StockTransaction.objects.filter(transaction_type='OUT', created_at__date__range=[start_date, end_date]).values('product_id', 'created_at__date', 'quantity')
    sales_map = {}
    for item in history_data:
        pid, d_str = item['product_id'], item['created_at__date'].strftime('%Y-%m-%d')
        if pid not in sales_map: sales_map[pid] = {}
        sales_map[pid][d_str] = sales_map[pid].get(d_str, 0) + float(item['quantity'])

    date_labels = []
    for i in range(7):
        date_labels.append((end_date - timedelta(days=6-i)).strftime('%Y-%m-%d'))

    products_widget_data = []
    for p in products_qs.order_by('-total_sold'):
        p_history = []
        p_map = sales_map.get(p.id, {})
        for d in date_labels: p_history.append(p_map.get(d, 0))
        products_widget_data.append({'id': p.id, 'name': p.name, 'sku': p.sku, 'stock': float(p.stock_quantity), 'sold': float(p.total_sold), 'revenue': float(p.total_revenue_val), 'history': p_history, 'url': resolve_url('product_detail', pk=p.id)})

    # --- 4. Profit Margin Alerts ---
    margin_alerts = {'critical': [], 'warning': [], 'notice': []}
    alert_products = Product.objects.filter(is_active=True, cost_price__gt=0)
    for p in alert_products:
        p.cost = float(p.cost_price)
        p.price = float(p.price_retail)
        p.p_profit = p.price - p.cost
        if p.price <= p.cost: margin_alerts['critical'].append(p)
        else:
            p.margin_pct = (p.p_profit / p.cost) * 100
            if p.margin_pct < 5: margin_alerts['warning'].append(p)
            elif p.margin_pct < 10: margin_alerts['notice'].append(p)
    
    alert_counts = {'critical': len(margin_alerts['critical']), 'warning': len(margin_alerts['warning']), 'notice': len(margin_alerts['notice'])}

    # --- 5. Integrated Graph Data (Smart Overview) ---
    health_score_data = {
        'labels': ['المنتجات الرابحة', 'تنبيهات الهوامش', 'نواقص المخزون', 'نفد من المخزون'],
        'values': [winners_count, alert_counts['critical'] + alert_counts['warning'], low_stock_count, out_of_stock_count],
        'colors': ['rgba(16, 185, 129, 0.7)', 'rgba(245, 158, 11, 0.7)', 'rgba(59, 130, 246, 0.7)', 'rgba(239, 68, 68, 0.7)']
    }

    # --- 6. Financial Overview Metrics ---
    from financial.models import Account, Transaction

    # Sum of balances in real cash/bank/wallet accounts only — NOT every active
    # account. Account.balance is also used by non-cash nominal accounts (AR,
    # inventory valuation, equity, revenue, expense), and summing all of them would
    # silently inflate "cash liquidity" the moment any of those ever picks up a
    # balance (they're currently all zero, but nothing guarantees that stays true).
    total_cash_liquidity = Account.objects.filter(
        is_active=True, account_type__in=['CASH_DRAWER', 'SAFE', 'BANK', 'VODAFONE_CASH', 'INSTAPAY']
    ).aggregate(total=Sum('balance'))['total'] or Decimal('0.00')

    # Sum of outstanding_balance of all active suppliers
    total_supplier_debt = sum(s.outstanding_balance for s in Supplier.objects.filter(is_active=True))

    # Net Financial Balance = Cash Liquidity - Supplier Debts
    net_financial_balance = total_cash_liquidity - total_supplier_debt

    # Oversold sales: order lines sold beyond real available stock (Layer 2
    # 'sales.allow_negative_stock'). Neither the stock ledger nor the invoice normally
    # surfaces this, so the dashboard is the one place a manager sees it happened.
    from sales.models import OrderItem
    from sales.models import Order as _Order
    oversold_qs = OrderItem.objects.filter(shortfall_qty__gt=0).exclude(order__status=_Order.STATUS_VOID)
    if date_from:
        oversold_qs = oversold_qs.filter(order__created_at__date__gte=date_from)
    if date_to:
        oversold_qs = oversold_qs.filter(order__created_at__date__lte=date_to)
    oversold_count = oversold_qs.count()
    oversold_qty_total = oversold_qs.aggregate(total=Sum('shortfall_qty'))['total'] or Decimal('0.00')

    # Latest financial transactions - RESPECTS DATE FILTERS
    fin_tx_qs = Transaction.objects.select_related('account', 'created_by').order_by('-created_at')
    if date_from:
        fin_tx_qs = fin_tx_qs.filter(created_at__date__gte=date_from)
    if date_to:
        fin_tx_qs = fin_tx_qs.filter(created_at__date__lte=date_to)
    # If no filter, default to today's transactions first, then last 20
    if not date_from and not date_to:
        from django.utils import timezone as tz
        today = tz.localtime(tz.now()).date()
        today_txs = fin_tx_qs.filter(created_at__date=today)
        recent_financial_transactions = today_txs if today_txs.exists() else fin_tx_qs[:20]
    else:
        recent_financial_transactions = fin_tx_qs[:50]


    context = {
        'total_products': total_products, 'total_suppliers': total_suppliers,
        'low_stock_count': low_stock_count, 'out_of_stock_count': out_of_stock_count,
        'expired_batch_count': expired_batch_count, 'near_expiry_batch_count': near_expiry_batch_count,
        'stock_value': stock_value, 'total_sales_ops': total_sales_ops,
        'total_items_sold': total_items_sold, 'revenue': revenue, 'tailoring_revenue': tailoring_revenue,
        'total_returns': total_returns,
        'total_users': total_users,
        'recent_transactions': recent_transactions, 'products_widget_json': json.dumps(products_widget_data),
        'margin_alerts': margin_alerts, 'alert_counts': alert_counts,
        'top_winners': top_winners, 'bottom_performers': bottom_performers,
        'health_graph_json': json.dumps(health_score_data),
        'total_cash_liquidity': total_cash_liquidity,
        'total_supplier_debt': total_supplier_debt,
        'net_financial_balance': net_financial_balance,
        'recent_financial_transactions': recent_financial_transactions,
        'oversold_count': oversold_count, 'oversold_qty_total': oversold_qty_total,
        'title': 'لوحة التحكم', 'date_from': date_from, 'date_to': date_to,
        'show_widget_items_sold': dashboard_widget_visible(request.user, 'items_sold'),
        'show_widget_revenue': dashboard_widget_visible(request.user, 'revenue'),
        'show_widget_stock_value': dashboard_widget_visible(request.user, 'stock_value'),
        'show_widget_cash_summary': dashboard_widget_visible(request.user, 'cash_summary'),
        # The four above were the only gated cards; every other panel on this screen was
        # unconditional, so an admin who ticked ONLY "القطع المباعة" still handed the user
        # the live cash movements, the inventory statistics and the pricing analysis.
        'show_widget_sales_count': dashboard_widget_visible(request.user, 'sales_count'),
        'show_widget_live_operations': dashboard_widget_visible(request.user, 'live_operations'),
        'show_widget_inventory_tools': dashboard_widget_visible(request.user, 'inventory_tools'),
        'show_widget_top_products': dashboard_widget_visible(request.user, 'top_products'),
        'show_widget_catalog_health': dashboard_widget_visible(request.user, 'catalog_health'),
        'show_widget_pricing_tips': dashboard_widget_visible(request.user, 'pricing_tips'),
        'show_widget_supply_alerts': dashboard_widget_visible(request.user, 'supply_alerts'),
    }

    # Quick shift open/close action buttons on the dashboard (mirrors the ones
    # already in the الخزنة والشيفتات navbar dropdown) — same granular
    # open_shift/close_shift permissions apply here.
    from financial.models import DailyShift
    open_shift = DailyShift.objects.filter(is_closed=False).last()
    context['open_shift'] = open_shift
    context['can_open_shift'] = (open_shift is None) and cashier_can_open_shift(request.user)
    context['can_close_shift'] = (open_shift is not None) and cashier_can_close_shift(request.user)
    return render(request, 'dashboard.html', context)

@login_required
@require_permission('dashboard', 'view')
@require_profit_view
def margin_report(request):
    margin_alerts = {'critical': [], 'warning': [], 'notice': []}
    products = Product.objects.filter(is_active=True, cost_price__gt=0).order_by('name')
    for p in products:
        p.cost, p.price = float(p.cost_price), float(p.price_retail)
        p.p_profit = p.price - p.cost
        if p.price <= p.cost: margin_alerts['critical'].append(p)
        else:
            p.margin_pct = (p.p_profit / p.cost) * 100
            if p.margin_pct < 5: margin_alerts['warning'].append(p)
            elif p.margin_pct < 10: margin_alerts['notice'].append(p)
    return render(request, 'dashboard/margin_report.html', {'margin_alerts': margin_alerts, 'title': 'تقرير هوامش الربح', 'generated_at': timezone.now()})

@login_required
@require_permission('dashboard', 'view')
@require_profit_view
def sales_profit_report(request):
    df = request.GET.get('date_from')
    dt = request.GET.get('date_to')
    
    # Clean inputs
    if df in [None, '', 'None', 'undefined']: df = None
    if dt in [None, '', 'None', 'undefined']: dt = None
    af = Q(transactions__transaction_type='OUT') & _exclude_voided_orders_q('transactions__')
    if df: af &= Q(transactions__created_at__date__gte=df)
    if dt: af &= Q(transactions__created_at__date__lte=dt)
    products_qs = Product.objects.annotate(
        total_sold=Coalesce(Sum('transactions__quantity', filter=af), Value(0, output_field=DecimalField())),
        total_revenue_val=Coalesce(Sum((F('transactions__quantity') * F('transactions__unit_price')) - F('transactions__discount'), filter=af, output_field=DecimalField()), Value(0, output_field=DecimalField())),
        total_cost_of_sales=Coalesce(Sum(F('transactions__quantity') * F('cost_price'), filter=af, output_field=DecimalField()), Value(0, output_field=DecimalField()))
    ).filter(total_sold__gt=0)
    results = []
    for p in products_qs:
        p.real_profit = float(p.total_revenue_val) - float(p.total_cost_of_sales)
        results.append(p)
    results = sorted(results, key=lambda x: x.real_profit, reverse=True)
    return render(request, 'dashboard/sales_profit_report.html', {'results': results, 'title': 'تقرير أداء المبيعات والأرباح', 'generated_at': timezone.now(), 'date_from': df, 'date_to': dt})
