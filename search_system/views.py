from django.shortcuts import render
from django.db.models import Q
from products.models import Product
from crm.models import Customer
from sales.models import Order

def search_view(request):
    query = request.GET.get('q', '').strip()
    results = {
        'products': [],
        'customers': [],
        'orders': [],
        'count': 0
    }
    
    if query:
        # 1. Search Products (Name, SKU, ID)
        products = Product.objects.filter(
            Q(name__icontains=query) | 
            Q(sku__icontains=query) |
            Q(id__icontains=query)
        )[:10] 
        results['products'] = products

        # 2. Search Customers (Name, Phone)
        customers = Customer.objects.filter(
            Q(first_name__icontains=query) | 
            Q(last_name__icontains=query) | 
            Q(phone__icontains=query)
        )[:10]
        results['customers'] = customers

        # 3. Search Orders (Receipt Number / ID)
        # Only search ID if the query is a number
        if query.isdigit():
             orders = Order.objects.filter(id=query)[:10]
             results['orders'] = orders
        
        # Calculate total results found
        results['count'] = len(products) + len(customers) + (len(results['orders']) if 'orders' in results else 0)

    return render(request, 'search_system/search_results.html', {'results': results, 'query': query})