from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from accounts.permissions import require_permission, require_granular_action  # RBAC (Phase 3.1)
from django.db.models import Q, Count, Sum, Avg, F
import json
from decimal import Decimal

from .models import Shipment, ShippingCompany, ShipmentLog
from .forms import ShippingCompanyForm, ShipmentForm, CustomerAddressForm
from sales.models import Order
from products.models import Product, WarehouseStock, Warehouse, StockTransaction
from crm.models import Customer
from settings.models import SystemSetting
from financial.models import Account, Transaction, DailyShift
from sales.utils import get_active_shift

@login_required
@require_granular_action('shipping', 'dashboard', 'shipping', 'view')
def shipping_dashboard(request):
    """Dashboard for Online and Shipping Orders"""
    
    shipments = Shipment.objects.select_related('order', 'order__customer', 'shipping_company').all().order_by('-created_at')

    # Quick Summary Data
    summary_stats = {
        'total': Shipment.objects.count(),
        'new': Shipment.objects.filter(status='new').count(),
        'shipped': Shipment.objects.filter(status='shipped').count(),
        'arrived': Shipment.objects.filter(status='arrived').count(),
        'returned': Shipment.objects.filter(status='returned').count(),
    }

    # Filters
    status_filter = request.GET.get('status')
    if status_filter:
        shipments = shipments.filter(status=status_filter)

    company_filter = request.GET.get('company')
    if company_filter:
        shipments = shipments.filter(shipping_company_id=company_filter)

    search_query = request.GET.get('q')
    if search_query:
        shipments = shipments.filter(
            Q(order__id__icontains=search_query) |
            Q(order__customer__first_name__icontains=search_query) |
            Q(order__customer__phone__icontains=search_query) |
            Q(tracking_number__icontains=search_query)
        )

    context = {
        'shipments': shipments,
        'companies': ShippingCompany.objects.filter(is_active=True),
        'title': 'لوحة متابعة الشحن والأونلاين',
        'status_choices': Shipment.STATUS_CHOICES,
        'summary_stats': summary_stats,
    }
    return render(request, 'shipping/dashboard.html', context)

@login_required
@require_permission('shipping', 'edit')
def create_shipment_for_order(request, order_id):
    """Convert a normal order to a shipping/online order"""
    order = get_object_or_404(Order, pk=order_id)
    
    # Check if shipment already exists
    if hasattr(order, 'shipment'):
        return redirect('shipping_dashboard')

    if request.method == 'POST':
        form = ShipmentForm(request.POST)
        if form.is_valid():
            shipment = form.save(commit=False)
            shipment.order = order
            
            # 1. Update Customer Address if user entered a new one in the text box
            new_address = shipment.shipping_address
            if order.customer and new_address:
                # If the entered address is valid and different from the saved one, update profile
                if order.customer.address != new_address:
                    order.customer.address = new_address
                    order.customer.save()

            # 2. Fallback: If text box was empty, try to grab from customer again (safety check)
            if not shipment.shipping_address and order.customer and order.customer.address:
                shipment.shipping_address = order.customer.address
            
            shipment.save()
            
            # Log initial status
            ShipmentLog.objects.create(
                shipment=shipment,
                status='new',
                comment='تم إنشاء طلب الشحن',
                created_by=request.user
            )
            return redirect('shipping_dashboard')
    else:
        # Pre-fill address from customer
        initial_data = {}
        if order.customer and order.customer.address:
            # Only fill if there is actual text, avoiding "None" or empty strings
            if len(order.customer.address.strip()) > 0:
                initial_data['shipping_address'] = order.customer.address
                
        form = ShipmentForm(initial=initial_data)

    return render(request, 'shipping/create_shipment.html', {
        'form': form, 
        'order': order,
        'title': f'تجهيز شحن للفاتورة #{order.id}'
    })

@login_required
@require_permission('shipping', 'edit')
def update_shipment_status(request):
    """Update shipment status and handle stock revert on returns"""
    if request.method == 'POST':
        try:
            data = json.loads(request.body)
            shipment_id = data.get('shipment_id')
            new_status = data.get('status')
            comment = data.get('comment', '')
            # Staff choice made in the return prompt: credit the customer's balance for this
            # order's value, or leave it untouched (e.g. they never actually paid, or it's
            # being handled separately). Only meaningful when transitioning INTO 'returned'.
            refund_balance = bool(data.get('refund_balance'))

            shipment = get_object_or_404(Shipment, pk=shipment_id)
            old_status = shipment.status
            shipment.status = new_status

            # The "أضف ملاحظة" button used to only log the comment into ShipmentLog
            # (history) without ever saving it onto the Shipment itself — so it never
            # showed up anywhere. Persist it as the current note too.
            if comment:
                shipment.comment = comment

            # If Returned, save reason and REVERT STOCK
            if new_status == 'returned' and old_status != 'returned':
                shipment.return_reason = comment

                # Revert Stock Logic
                from django.db import transaction as db_transaction
                with db_transaction.atomic():
                    order = shipment.order
                    # We need to find the warehouse used for this order
                    # Usually tracked in StockTransaction linked to this order
                    for item in order.items.all():
                        product = item.product
                        qty = item.quantity

                        # Try to find the warehouse from StockTransaction
                        last_st = StockTransaction.objects.filter(
                            product=product,
                            note__contains=f"#{order.id}",
                            transaction_type='OUT'
                        ).first()

                        warehouse = last_st.warehouse if last_st else Warehouse.objects.filter(is_active=True, is_sales_point=True).first()

                        if warehouse:
                            ws, _ = WarehouseStock.objects.get_or_create(warehouse=warehouse, product=product)
                            ws.quantity += qty
                            ws.save()

                            # Log Revert Transaction
                            StockTransaction.objects.create(
                                product=product,
                                warehouse=warehouse,
                                transaction_type='RET',
                                quantity=qty,
                                note=f"مرتجع تلقائي (فشل شحن) فاتورة #{order.id}"
                            )

                # Credit the customer's balance if staff chose to return the money.
                # Guarded by balance_refunded so flipping the status back and forth can't
                # double-credit the same order.
                if refund_balance and order.customer and not shipment.balance_refunded:
                    from crm.models import CustomerPayment
                    CustomerPayment.objects.create(
                        customer=order.customer, user=request.user, amount=order.total_amount,
                        transaction_type='payment', payment_method='return_credit',
                        notes=f"رصيد مرتجع شحن — فاتورة #{order.id}",
                    )
                    shipment.balance_refunded = True

            shipment.save()
            
            # Log history
            ShipmentLog.objects.create(
                shipment=shipment,
                status=new_status,
                comment=comment,
                created_by=request.user
            )
            
            return JsonResponse({'status': 'success', 'new_status_display': shipment.get_status_display()})
        except Exception as e:
            return JsonResponse({'status': 'error', 'message': str(e)})
    return JsonResponse({'status': 'error'})

@login_required
@require_permission('shipping', 'edit')
def update_payment_info(request):
    """AJAX: Update payment amount and method with Financial recording"""
    if request.method == 'POST':
        try:
            data = json.loads(request.body)
            order_id = data.get('order_id')
            received_amount = Decimal(str(data.get('received_amount', 0)))
            payment_method = data.get('payment_method')
            
            order = get_object_or_404(Order, pk=order_id)
            old_received = order.received_amount
            
            diff = received_amount - old_received
            
            order.received_amount = received_amount
            order.payment_method = payment_method
            
            # Update completion status
            if order.remaining_amount <= 0:
                order.is_completed = True
            else:
                order.is_completed = False
                
            order.save()
            
            # FINANCIAL INTEGRATION: If there is a positive difference, record it as a Sale
            if diff > 0:
                current_shift = get_active_shift()  # global, no auto-create
                
                # Map method to account
                acc_type = 'CASH_DRAWER'
                acc_name = 'درج الكاشير'
                
                pm = str(payment_method).lower()
                if 'visa' in pm or 'bank' in pm:
                    acc_type, acc_name = 'BANK', 'حساب البنك'
                elif 'vodafone' in pm or 'wallet' in pm:
                    acc_type, acc_name = 'VODAFONE_CASH', 'محفظة فودافون كاش'
                elif 'insta' in pm:
                    acc_type, acc_name = 'INSTAPAY', 'إنستا باي'
                
                account, _ = Account.objects.get_or_create(
                    account_type=acc_type,
                    defaults={'name': acc_name, 'balance': 0}
                )
                
                Transaction.objects.create(
                    shift=current_shift,
                    account=account,
                    transaction_type='SALE',
                    amount=diff,
                    description=f"تحصيل متبقي شحن فاتورة #{order.id}",
                    created_by=request.user
                )
                
            return JsonResponse({
                'status': 'success', 
                'remaining': str(order.remaining_amount),
                'is_completed': order.is_completed
            })
        except Exception as e:
            return JsonResponse({'status': 'error', 'message': str(e)})
    return JsonResponse({'status': 'error'})

@login_required
@require_permission('shipping', 'edit')
def update_customer_address(request, customer_id):
    """Modal View to update address"""
    customer = get_object_or_404(Customer, pk=customer_id)
    if request.method == 'POST':
        form = CustomerAddressForm(request.POST, instance=customer)
        if form.is_valid():
            form.save()
            return redirect(request.META.get('HTTP_REFERER', 'shipping_dashboard'))
    return redirect('shipping_dashboard')

# --- Printing ---
@login_required
@require_permission('shipping', 'view')
def print_shipping_label(request, shipment_id):
    shipment = get_object_or_404(Shipment, pk=shipment_id)
    sys_settings = SystemSetting.objects.first()
    
    # Calculate amount to collect (COD)
    amount_to_collect = shipment.order.total_amount - shipment.order.received_amount
    if amount_to_collect < 0: amount_to_collect = 0

    context = {
        'shipment': shipment,
        'order': shipment.order,
        'customer': shipment.order.customer,
        'sys_settings': sys_settings,
        'amount_to_collect': amount_to_collect,
        'style': request.GET.get('style', 'thermal') # thermal, a4, a5
    }
    return render(request, 'shipping/print_label.html', context)

# --- Company CRUD ---
@login_required
@require_granular_action('shipping', 'companies', 'shipping', 'view')
def company_list(request):
    companies = ShippingCompany.objects.annotate(
        total_shipments=Count('shipment'),
        delivered_shipments=Count('shipment', filter=Q(shipment__status='arrived')),
        returned_shipments=Count('shipment', filter=Q(shipment__status='returned')),
        pending_shipments=Count('shipment', filter=Q(shipment__status__in=['new', 'shipped']))
    ).all()
    
    if request.method == 'POST':
        form = ShippingCompanyForm(request.POST)
        if form.is_valid():
            form.save()
            return redirect('company_list')
    else:
        form = ShippingCompanyForm()
    return render(request, 'shipping/company_list.html', {'companies': companies, 'form': form, 'title': 'شركات الشحن'})

@login_required
@require_permission('shipping', 'edit')
def company_delete(request, pk):
    company = get_object_or_404(ShippingCompany, pk=pk)
    if request.method == 'POST':
        company.delete()
    return redirect('company_list')