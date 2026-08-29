from django.contrib.auth.decorators import login_required
from django.shortcuts import render
from django.db.models import Q
from accounts.permissions import has_permission
from products.models import Product
from crm.models import Customer
from sales.models import Order

# Modules that make someone an order-facing user. Kept in step with
# sales.views.ORDER_FACING_MODULES: CRM is deliberately absent, since a customer-service
# account manages customer records and has no claim on the sales ledger.
_ORDER_MODULES = ('pos', 'sales', 'cashier', 'waiter', 'kitchen', 'delivery')


@login_required
def search_view(request):
    """Global search.

    Each section is filtered by what the signed-in user is actually allowed to see. The
    search box sits in the header on every page, so without this it becomes a way to read
    the product catalogue, customer names and phone numbers, and order records from a role
    that was never granted any of them.
    """
    query = request.GET.get('q', '').strip()
    can_products = has_permission(request.user, 'products', 'view')
    can_customers = has_permission(request.user, 'crm', 'view')
    can_orders = any(has_permission(request.user, m, 'view') for m in _ORDER_MODULES)

    results = {
        'products': [],
        'customers': [],
        'orders': [],
        'count': 0
    }

    if query:
        # 1. Search Products (Name, SKU, ID)
        if can_products:
            products = Product.objects.filter(
                Q(name__icontains=query) |
                Q(sku__icontains=query) |
                Q(id__icontains=query)
            )[:10]
            results['products'] = products

        # 2. Search Customers (Name, Phone)
        if can_customers:
            customers = Customer.objects.filter(
                Q(first_name__icontains=query) |
                Q(last_name__icontains=query) |
                Q(phone__icontains=query)
            )[:10]
            results['customers'] = customers

        # 3. Search Orders (Receipt Number / ID)
        # Only search ID if the query is a number
        if can_orders and query.isdigit():
            results['orders'] = Order.objects.filter(id=query)[:10]

        # Calculate total results found
        results['count'] = (len(results['products'])
                            + len(results['customers'])
                            + len(results['orders']))

    return render(request, 'search_system/search_results.html', {
        'results': results,
        'query': query,
        'can_search_products': can_products,
        'can_search_customers': can_customers,
        'can_search_orders': can_orders,
    })
