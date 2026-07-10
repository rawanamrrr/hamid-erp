"""
Custom Admin Dashboard View
Aggregates data from all apps for the admin panel.
"""
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib import admin
from django.shortcuts import render
from django.utils.decorators import method_decorator
from django.utils import timezone
from django.db.models import Sum, Count, Q, F, Avg
from django.contrib.auth.models import User
from datetime import timedelta, date
import json


@staff_member_required
def admin_dashboard(request):
    today = timezone.now().date()
    last_30 = today - timedelta(days=29)
    last_7 = today - timedelta(days=6)

    # ── SALES ──────────────────────────────────────────────────────────────
    from sales.models import Order, OrderItem, ReturnInvoice, Expense
    orders_qs = Order.objects.all()

    today_sales = orders_qs.filter(created_at__date=today).aggregate(
        total=Sum('total_amount'), count=Count('id'))
    month_sales = orders_qs.filter(created_at__date__gte=last_30).aggregate(
        total=Sum('total_amount'), count=Count('id'))
    total_sales = orders_qs.aggregate(total=Sum('total_amount'), count=Count('id'))

    total_revenue       = float(total_sales['total'] or 0)
    total_orders_count  = total_sales['count'] or 0
    today_revenue       = float(today_sales['total'] or 0)
    today_orders        = today_sales['count'] or 0
    month_revenue       = float(month_sales['total'] or 0)
    month_orders        = month_sales['count'] or 0

    # Daily sales for line chart (last 30 days)
    daily_sales = (
        orders_qs
        .filter(created_at__date__gte=last_30)
        .values('created_at__date')
        .annotate(total=Sum('total_amount'), count=Count('id'))
        .order_by('created_at__date')
    )
    daily_labels  = [str(d['created_at__date']) for d in daily_sales]
    daily_revenue = [float(d['total'] or 0) for d in daily_sales]
    daily_counts  = [d['count'] for d in daily_sales]

    # Payment method breakdown
    payment_breakdown = (
        orders_qs
        .values('payment_method')
        .annotate(total=Sum('total_amount'), count=Count('id'))
        .order_by('-total')
    )
    payment_labels  = [p['payment_method'] for p in payment_breakdown]
    payment_totals  = [float(p['total'] or 0) for p in payment_breakdown]

    # Recent orders
    recent_orders = orders_qs.select_related('customer', 'user').order_by('-created_at')[:10]

    # Returns & Expenses
    total_returns   = float(ReturnInvoice.objects.aggregate(s=Sum('total_refund_amount'))['s'] or 0)
    total_expenses  = float(Expense.objects.aggregate(s=Sum('amount'))['s'] or 0)
    net_profit      = total_revenue - total_returns - total_expenses

    # ── PRODUCTS ───────────────────────────────────────────────────────────
    from products.models import Product, Supplier, PurchaseInvoice, WarehouseStock, StockTransaction
    total_products = Product.objects.count()
    active_products = Product.objects.filter(is_active=True).count()
    low_stock = Product.objects.filter(stock_quantity__gt=0, stock_quantity__lte=F('low_stock_threshold')).count()
    out_of_stock = Product.objects.filter(stock_quantity__lte=0).count()
    total_suppliers = Supplier.objects.count()

    stock_value = float(
        Product.objects.aggregate(v=Sum(F('stock_quantity') * F('cost_price')))['v'] or 0
    )

    # Top selling products (by stock transactions OUT, last 30 days)
    top_products = (
        StockTransaction.objects
        .filter(transaction_type='OUT', created_at__date__gte=last_30)
        .values('product__name')
        .annotate(sold=Sum('quantity'))
        .order_by('-sold')[:8]
    )
    top_product_labels  = [p['product__name'] for p in top_products]
    top_product_values  = [float(p['sold'] or 0) for p in top_products]

    # Category breakdown for doughnut chart
    from products.models import Category
    category_data = (
        Product.objects
        .values('category__name')
        .annotate(count=Count('id'), stock=Sum('stock_quantity'))
        .order_by('-count')[:8]
    )
    cat_labels  = [c['category__name'] or 'بدون تصنيف' for c in category_data]
    cat_counts  = [c['count'] for c in category_data]

    # Low stock products list
    low_stock_products = (
        Product.objects
        .filter(stock_quantity__lte=F('low_stock_threshold'))
        .order_by('stock_quantity')[:10]
    )

    # Purchase history last 30 days
    purchase_data = (
        PurchaseInvoice.objects
        .filter(created_at__date__gte=last_30)
        .values('created_at__date')
        .annotate(total=Sum('total_amount'))
        .order_by('created_at__date')
    )
    purchase_labels = [str(p['created_at__date']) for p in purchase_data]
    purchase_totals = [float(p['total'] or 0) for p in purchase_data]
    total_purchases = float(PurchaseInvoice.objects.aggregate(s=Sum('total_amount'))['s'] or 0)

    # ── CUSTOMERS / CRM ────────────────────────────────────────────────────
    from crm.models import Customer
    total_customers     = Customer.objects.count()
    new_customers_month = Customer.objects.filter(created_at__date__gte=last_30).count()

    # Top customers by revenue
    top_customers = (
        Order.objects
        .filter(customer__isnull=False)
        .values('customer__first_name', 'customer__last_name', 'customer__id')
        .annotate(total=Sum('total_amount'), orders=Count('id'))
        .order_by('-total')[:8]
    )
    top_cust_labels = [f"{c['customer__first_name']} {c['customer__last_name']}" for c in top_customers]
    top_cust_values = [float(c['total'] or 0) for c in top_customers]

    # Customer type breakdown
    cust_type_data = (
        Customer.objects
        .values('customer_type')
        .annotate(count=Count('id'))
    )
    cust_type_labels = [c['customer_type'] or 'غير محدد' for c in cust_type_data]
    cust_type_counts = [c['count'] for c in cust_type_data]

    # ── USERS / ACCOUNTS ───────────────────────────────────────────────────
    from accounts.models import UserActivityLog, SystemError
    total_users       = User.objects.count()
    active_users      = User.objects.filter(is_active=True).count()
    superusers        = User.objects.filter(is_superuser=True).count()
    total_errors      = SystemError.objects.count()
    unresolved_errors = SystemError.objects.filter(is_resolved=False).count()

    # Sales by user (cashier performance)
    user_sales = (
        Order.objects
        .filter(created_at__date__gte=last_30)
        .values('user__username', 'user__first_name')
        .annotate(total=Sum('total_amount'), count=Count('id'))
        .order_by('-total')[:8]
    )
    user_labels = [u['user__first_name'] or u['user__username'] for u in user_sales]
    user_totals = [float(u['total'] or 0) for u in user_sales]

    # Recent activity log
    recent_activity = (
        UserActivityLog.objects
        .select_related('user')
        .order_by('-timestamp')[:15]
    )

    # ── FINANCIAL ──────────────────────────────────────────────────────────
    from financial.models import Account, Transaction, DailyShift
    try:
        cash_drawer = Account.objects.filter(account_type='CASH_DRAWER').first()
        drawer_balance = float(cash_drawer.balance) if cash_drawer else 0
    except Exception:
        drawer_balance = 0

    active_shift = DailyShift.objects.filter(is_closed=False).first()

    # Transaction trend last 30 days
    tx_trend = (
        Transaction.objects
        .filter(created_at__date__gte=last_30, transaction_type='SALE')
        .values('created_at__date')
        .annotate(total=Sum('amount'))
        .order_by('created_at__date')
    )
    tx_labels = [str(t['created_at__date']) for t in tx_trend]
    tx_totals = [float(t['total'] or 0) for t in tx_trend]

    # ── SHIPPING ───────────────────────────────────────────────────────────
    from shipping.models import Shipment
    total_shipments   = Shipment.objects.count()
    pending_shipments = Shipment.objects.filter(status='pending').count()
    delivered_shipments = Shipment.objects.filter(status='delivered').count()

    # ── NOTIFICATIONS ──────────────────────────────────────────────────────
    from notifications.models import Notification
    unread_notifications = Notification.objects.filter(is_read=False).count()

    # ── KPI SCORES ─────────────────────────────────────────────────────────
    avg_order_value = (month_revenue / month_orders) if month_orders > 0 else 0

    # ── WEEKLY ORDERS (bar chart) ──────────────────────────────────────────
    weekly = (
        orders_qs
        .filter(created_at__date__gte=last_7)
        .values('created_at__date')
        .annotate(count=Count('id'), total=Sum('total_amount'))
        .order_by('created_at__date')
    )
    weekly_labels  = [str(w['created_at__date']) for w in weekly]
    weekly_counts  = [w['count'] for w in weekly]
    weekly_revenue = [float(w['total'] or 0) for w in weekly]

    context = {
        # Sales KPIs
        'total_revenue': total_revenue,
        'total_orders_count': total_orders_count,
        'today_revenue': today_revenue,
        'today_orders': today_orders,
        'month_revenue': month_revenue,
        'month_orders': month_orders,
        'net_profit': net_profit,
        'total_returns': total_returns,
        'total_expenses': total_expenses,
        'avg_order_value': avg_order_value,

        # Sales charts
        'daily_labels_json': json.dumps(daily_labels),
        'daily_revenue_json': json.dumps(daily_revenue),
        'daily_counts_json': json.dumps(daily_counts),
        'payment_labels_json': json.dumps(payment_labels),
        'payment_totals_json': json.dumps(payment_totals),
        'weekly_labels_json': json.dumps(weekly_labels),
        'weekly_counts_json': json.dumps(weekly_counts),
        'weekly_revenue_json': json.dumps(weekly_revenue),

        # Recent orders
        'recent_orders': recent_orders,

        # Products
        'total_products': total_products,
        'active_products': active_products,
        'low_stock': low_stock,
        'out_of_stock': out_of_stock,
        'total_suppliers': total_suppliers,
        'stock_value': stock_value,
        'total_purchases': total_purchases,
        'top_product_labels_json': json.dumps(top_product_labels),
        'top_product_values_json': json.dumps(top_product_values),
        'cat_labels_json': json.dumps(cat_labels),
        'cat_counts_json': json.dumps(cat_counts),
        'low_stock_products': low_stock_products,

        # Customers
        'total_customers': total_customers,
        'new_customers_month': new_customers_month,
        'top_cust_labels_json': json.dumps(top_cust_labels),
        'top_cust_values_json': json.dumps(top_cust_values),
        'cust_type_labels_json': json.dumps(cust_type_labels),
        'cust_type_counts_json': json.dumps(cust_type_counts),

        # Users / Accounts
        'total_users': total_users,
        'active_users': active_users,
        'superusers': superusers,
        'total_errors': total_errors,
        'unresolved_errors': unresolved_errors,
        'user_labels_json': json.dumps(user_labels),
        'user_totals_json': json.dumps(user_totals),
        'recent_activity': recent_activity,

        # Financial
        'drawer_balance': drawer_balance,
        'active_shift': active_shift,
        'tx_labels_json': json.dumps(tx_labels),
        'tx_totals_json': json.dumps(tx_totals),

        # Shipping
        'total_shipments': total_shipments,
        'pending_shipments': pending_shipments,
        'delivered_shipments': delivered_shipments,

        # Misc
        'unread_notifications': unread_notifications,
        'today': today,

        # Django admin sidebar needs these
        'available_apps': admin.site.get_app_list(request),
        'has_permission': True,
        'is_popup': False,
        'site_header': admin.site.site_header,
        'site_title': admin.site.site_title,
        'title': 'لوحة التحكم',
    }
    return render(request, 'admin/custom_dashboard.html', context)
