from django.shortcuts import render, get_object_or_404, redirect
from django.http import JsonResponse, HttpResponse
from django.contrib.auth.decorators import login_required
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from django.db import transaction
from django.db.models import Sum, Q, Count
from django.utils import timezone
from django.contrib.auth.models import User
from django.contrib import messages
from decimal import Decimal
import json
import logging
from datetime import datetime
from django.template.loader import render_to_string

# --- Models ---
from products.models import Product, Category, StockTransaction, Warehouse, WarehouseStock
from crm.models import Customer
from settings.models import SystemSetting
from .models import Order, OrderItem, Expense, ReturnInvoice, ReturnItem, OtherIncome, CashSettlement, Draft, SavedOrder, SavedOrderItem, Reservation, ReservationItem, DocumentSequence
from .forms import ExpenseForm, OtherIncomeForm, CashSettlementForm
from shipping.models import Shipment 

# --- Financial Integration Imports ---
from .utils import record_sale_transaction, get_active_shift, get_or_create_active_shift
from financial.models import Account, Transaction, DailyShift

# --- RBAC (Phase 3.1) ---
from accounts.permissions import require_permission, has_permission, require_granular_action, require_granular_action_open, cashier_can_open_shift, cashier_can_close_shift
from settings.policies import get_policy
from financial.views import suggested_shift_start_balance as _suggested_shift_start_balance

# --- Printer Import ---
from .printer_utils import print_html_to_backend

logger = logging.getLogger(__name__)

# --- PDF Generation Import (WeasyPrint) ---
# FIXED: Now catches OSError to prevent server crash if GTK is missing
try:
    from weasyprint import HTML, CSS
except (ImportError, OSError):
    HTML = None # Handle gracefully if not installed or libraries missing

# --- Notification Import ---
try:
    from notifications.models import Notification
except ImportError:
    Notification = None

# --- Helper: Arabic Tafqeet (Python Version for PDF & Backend) ---
def tafqeet_ar(number):
    """
    Converts a number to Arabic words (Simple implementation for currency).
    """
    if number == 0: return "صفر"
    
    units = ["", "واحد", "اثنان", "ثلاثة", "أربعة", "خمسة", "ستة", "سبعة", "ثمانية", "تسعة", "عشرة", 
             "أحد عشر", "اثنا عشر", "ثلاثة عشر", "أربعة عشر", "خمسة عشر", "ستة عشر", "سبعة عشر", "ثمانية عشر", "تسعة عشر"]
    tens = ["", "", "عشرون", "ثلاثون", "أربعون", "خمسون", "ستون", "سبعون", "ثمانون", "تسعون"]
    hundreds = ["", "مائة", "مائتان", "ثلاثمائة", "أربعمائة", "خمسمائة", "ستمائة", "سبعمائة", "ثمانمائة", "تسعمائة"]
    
    def convert_group(n):
        if n == 0: return ""
        if n < 20: return units[n]
        if n < 100: return tens[n // 10] + ((" و" + units[n % 10]) if n % 10 != 0 else "")
        if n < 1000: return hundreds[n // 100] + ((" و" + convert_group(n % 100)) if n % 100 != 0 else "")
        return ""

    # Handle float/decimal properly
    val_str = "{:.2f}".format(float(number))
    parts = val_str.split('.')
    whole = int(parts[0])
    decimal = int(parts[1])
    
    result = ""
    
    if whole < 1000:
        result = convert_group(whole)
    elif whole < 1000000:
        th = whole // 1000
        rem = whole % 1000
        if th == 1: th_str = "ألف"
        elif th == 2: th_str = "ألفان"
        elif 3 <= th <= 10: th_str = convert_group(th) + " آلاف"
        else: th_str = convert_group(th) + " ألف"
        result = th_str + ((" و" + convert_group(rem)) if rem != 0 else "")
    elif whole < 1000000000:
        mil = whole // 1000000
        rem = whole % 1000000
        if mil == 1: mil_str = "مليون"
        elif mil == 2: mil_str = "مليونان"
        else: mil_str = convert_group(mil) + " مليون"
        
        # Process remainder (thousands part)
        if rem > 0:
            th = rem // 1000
            rem_units = rem % 1000
            
            th_str = ""
            if th > 0:
                if th == 1: th_str = "ألف"
                elif th == 2: th_str = "ألفان"
                elif 3 <= th <= 10: th_str = convert_group(th) + " آلاف"
                else: th_str = convert_group(th) + " ألف"
            
            unit_str = convert_group(rem_units)
            
            mil_str += (" و" + th_str) if th_str else ""
            mil_str += (" و" + unit_str) if unit_str else ""
            
        result = mil_str
    else:
        result = str(whole) # Fallback for extremely large numbers

    result += " جنيهاً"
    if decimal > 0:
        result += " و " + convert_group(decimal) + " قرشاً"
        
    return result + " لا غير"


def _recipe_product_ids():
    from restaurant.models import Recipe
    return Recipe.objects.filter(is_active=True).values_list('product_id', flat=True).distinct()


@login_required
@require_permission('pos', 'view')
def api_check_recipe_stock(request):
    """Read-only recipe-stock pre-check for a SINGLE cart line, called the moment a
    recipe-bearing product is added to the cart (not just at final checkout) — see
    preview_recipe_shortages() for why this never blocks the sale outright."""
    try:
        data = json.loads(request.body)
    except (ValueError, json.JSONDecodeError):
        return JsonResponse({'status': 'error', 'message': 'بيانات غير صالحة'}, status=400)

    items = data.get('items') or []
    warehouse_id = data.get('warehouse_id')
    warehouse = Warehouse.objects.filter(id=warehouse_id, is_active=True).first() if warehouse_id else None
    if not warehouse:
        warehouse = Warehouse.objects.filter(is_active=True, is_sales_point=True).first()
    if not warehouse or not items:
        return JsonResponse({'shortages': []})

    from .services import preview_recipe_shortages
    return JsonResponse({'shortages': preview_recipe_shortages(items, warehouse)})


@login_required
@require_permission('pos', 'view')
def pos_view(request):
    """
    Main Point of Sale (POS) View.
    Handles GET for the UI. POST requests for orders are now handled by submit_order_ajax.
    Allows selecting a specific warehouse via 'warehouse_id' GET parameter.
    """
    # GET Request: Render POS UI
    categories = Category.objects.all()
    customers = Customer.objects.all()
    sys_settings = SystemSetting.objects.first()
    
    # --- WAREHOUSE SELECTION LOGIC ---
    # Fetch all active sales point warehouses
    warehouses = Warehouse.objects.filter(is_active=True, is_sales_point=True)
    
    if not request.user.is_superuser:
        profile = request.user.profile
        if profile.allowed_warehouses.exists():
            warehouses = warehouses.filter(id__in=profile.allowed_warehouses.all())
    
    # Determine Active Warehouse
    active_warehouse = None
    warehouse_id = request.GET.get('warehouse_id')
    
    if warehouse_id:
        active_warehouse = warehouses.filter(id=warehouse_id).first()
    
    # Fallback to first available if not selected or invalid
    if not active_warehouse and warehouses.exists():
        active_warehouse = warehouses.first()
    
    # Build customer balance map for POS display. Calls each customer's own
    # get_balance() directly instead of a hand-rolled reimplementation of its formula —
    # a prior duplicate here had drifted out of sync (it predated the COD-shortfall
    # exclusion added to get_balance(), so a customer with only pending
    # cash-on-delivery orders showed a large debt in this POS dropdown while their CRM
    # profile page — which does call get_balance() — correctly showed them settled).
    customer_balances = {c.id: round(float(c.get_balance()), 2) for c in customers}

    products_data = []
    if active_warehouse:
        # Fetch products and annotate with stock from the SELECTED warehouse
        active_products = Product.objects.filter(is_active=True, is_raw_material=False).prefetch_related('price_breaks')
        # Phase 6.3: stock held by OPEN reservations is not available for normal sale.
        # When converting a reservation (?reservation=ID), exclude its hold so its items
        # show as available for this very conversion.
        from .models import reserved_quantities
        _reserved = reserved_quantities(active_warehouse.id, exclude_reservation_id=request.GET.get('reservation'))
        for p in active_products:
            # Get stock for this specific warehouse
            ws = WarehouseStock.objects.filter(product=p, warehouse=active_warehouse).first()
            qty = (ws.quantity if ws else 0) - _reserved.get(p.id, 0)  # available = on-hand − reserved

            # Always show the product (searchable/findable) — the "نفد" badge already
            # renders when stock_quantity <= 0. Whether it can actually be ADDED to the cart
            # is a separate check (POS_POLICIES.negativeStockAllowed / blockWhenOutOfStock).
            p.stock_quantity = qty # OVERRIDE the global stock with available (local − reserved)
            products_data.append(p)
    else:
        # Fallback if no warehouse set up yet
        products_data = Product.objects.filter(is_active=True, is_raw_material=False)
    # ---------------------------------

    # Shift Info for POS Header — GLOBAL shift
    current_shift = get_active_shift()
    shift_start_time = None
    shift_invoices_count = 0
    
    if current_shift:
        shift_start_time = current_shift.start_time.isoformat()
        # Count sales transactions in this shift
        shift_invoices_count = Transaction.objects.filter(
            shift=current_shift, 
            transaction_type='SALE'
        ).count()
    
    # --- ORDER EDITING LOGIC ---
    editing_order_data = None
    order_id = request.GET.get('order_id')
    if order_id:
        order = Order.objects.filter(id=order_id).first()
        if order:
            editing_order_data = {
                'id': order.id,
                'customer_id': order.customer.id if order.customer else None,
                'customer_name': f"{order.customer.first_name} {order.customer.last_name}" if order.customer else "عميل نقدي",
                'discount': float(order.discount),
                'discount_type': order.discount_type,
                'delivery_cost': float(order.delivery_cost),
                'notes': order.notes,
                'warehouse_id': order.warehouse.id if order.warehouse else (active_warehouse.id if active_warehouse else None),
                # --- FIX #1: Include payment split so frontend can pre-fill the payment modal ---
                'payment_method': order.payment_method,
                'cash_paid': float(order.cash_paid),
                'wallet_paid': float(order.wallet_paid),
                'instapay_paid': float(order.instapay_paid),
                'visa_paid': float(order.visa_paid),
                'received_amount': float(order.received_amount),
                'driver_id': order.driver_id,
                'order_type': order.order_type,
                # ----------------------------------------------------------------------------------
                'items': [
                    {
                        'id': item.product.id,
                        'name': item.product.name,
                        'qty': float(item.quantity),
                        'price': float(item.price),
                        'price_tier': item.price_tier,
                    } for item in order.items.all()
                ]
            }
    # ---------------------------

    # Quantity-break pricing map for the products shown in the POS (Phase 6.6).
    # { product_id: [{'t': tier, 'q': min_qty, 'p': unit_price}, ...] } — the cart
    # uses this to auto-apply the best wholesale break as the cashier changes quantity.
    price_breaks_map = {}
    for p in products_data:
        brs = list(p.price_breaks.all())
        if brs:
            price_breaks_map[p.id] = [
                {'t': b.customer_type, 'q': float(b.min_quantity), 'p': float(b.unit_price)}
                for b in brs
            ]

    # Active delivery drivers for this branch — POS lets the cashier assign one directly
    # at order-creation time instead of the old 2-step "create order, then separately go
    # to the delivery dashboard to assign a driver" flow.
    from restaurant.models import Driver
    drivers = (Driver.objects.filter(branch=active_warehouse, is_active=True).order_by('name')
               if active_warehouse else Driver.objects.none())

    # Fixed-choice modifier groups (مستوى السكر، إضافات...) per product — same picker
    # the waiter cart already uses, now shared with the cashier POS too.
    from restaurant.services import build_modifier_map
    modifier_map_json = json.dumps(build_modifier_map())

    # ALL active products for transfer/movement modals (not filtered by warehouse stock)
    all_active_products = Product.objects.filter(is_active=True).order_by('name')
    # ALL active warehouses for transfer (not just sales points)
    all_warehouses = Warehouse.objects.filter(is_active=True)

    # Get active promotions (Deals & Discounts) to render in Cashier POS
    from financial.payroll_models import DealDiscount
    active_deals = DealDiscount.objects.filter(is_active=True, start_date__lte=timezone.now(), end_date__gte=timezone.now())
    active_deals_data = []
    for d in active_deals:
        active_deals_data.append({
            'id': d.id,
            'name': d.name,
            'promo_type': d.promo_type,
            'discount_type': d.discount_type,
            'value': float(d.value),
            'buy_x_qty': d.buy_x_qty,
            'get_y_qty': d.get_y_qty,
            'buy_n_qty': d.buy_n_qty,
            'for_price': float(d.for_price),
            'minimum_order_value': float(d.minimum_order_value),
            'coupon_code': d.coupon_code or '',
            'apply_to_all': d.apply_to_all,
            'product_ids': list(d.get_scoped_product_ids() or []),
            'get_product_ids': list(d.get_products.values_list('id', flat=True)),
            'is_active': True
        })

    context = {
        'categories': categories,
        'products': products_data,
        'all_products': all_active_products, # For transfer/movement product pickers
        'customers': customers,
        'customer_balances': customer_balances,
        'title': 'نقطة البيع (POS)',
        'sys_settings': sys_settings,
        'current_shift': current_shift,
        'shift_start_time': shift_start_time,
        'shift_invoices_count': shift_invoices_count,
        'warehouses': warehouses, # List of sales-point warehouses for the POS dropdown
        'all_warehouses': all_warehouses, # All active warehouses for transfers
        'active_warehouse': active_warehouse, # The currently selected warehouse
        'editing_order': editing_order_data, # For template logic
        'editing_order_json': json.dumps(editing_order_data),  # json.dumps(None) → "null" (safe JS); never Python None
        'active_deals_json': json.dumps(active_deals_data),
        'price_breaks_json': json.dumps(price_breaks_map),
        'require_open_shift': get_policy('shifts.require_open_shift_before_sales'),
        'can_open_shift': cashier_can_open_shift(request.user),
        'can_close_shift': cashier_can_close_shift(request.user),
        'suggested_start_balance': (_suggested_shift_start_balance() if current_shift is None else None),
        'drivers': drivers,
        'modifier_map_json': modifier_map_json,
        # Product ids with an active recipe — lets the POS page skip the recipe-stock
        # check entirely for products that have no recipe at all (most of the catalog),
        # instead of firing a check request on every single add-to-cart click.
        'recipe_product_ids_json': json.dumps(list(_recipe_product_ids())),
    }
    return render(request, 'sales/pos.html', context)


@login_required
@require_permission('pos', 'view')
def last_customer_price(request):
    """Phase 6.8: the last unit price a customer paid for a product (most recent
    non-void order). Used by the POS customer panel to surface the previous price."""
    customer_id = request.GET.get('customer_id')
    product_id = request.GET.get('product_id')
    if not customer_id or not product_id:
        return JsonResponse({'status': 'ok', 'price': None})
    item = (OrderItem.objects
            .filter(order__customer_id=customer_id, product_id=product_id)
            .exclude(order__status=Order.STATUS_VOID)
            .order_by('-order__created_at')
            .first())
    if item:
        return JsonResponse({
            'status': 'ok',
            'price': float(item.price),
            'date': item.order.created_at.strftime('%Y-%m-%d'),
        })
    return JsonResponse({'status': 'ok', 'price': None})


# ── Phase 6.11: named saved/recurring orders per customer ───────────────────────────

@login_required
@require_permission('pos', 'create')
def save_saved_order(request):
    """Save the current cart as a named, reusable order for a customer."""
    if request.method != 'POST':
        return JsonResponse({'status': 'error', 'message': 'Invalid method'}, status=405)
    data = json.loads(request.body)
    customer_id = data.get('customer_id')
    name = (data.get('name') or '').strip()
    items = data.get('items', [])
    if not customer_id:
        return JsonResponse({'status': 'error', 'message': 'يجب اختيار عميل لحفظ الطلب.'})
    if not name:
        return JsonResponse({'status': 'error', 'message': 'يرجى إدخال اسم للطلب المحفوظ.'})
    if not items:
        return JsonResponse({'status': 'error', 'message': 'السلة فارغة.'})
    customer = Customer.objects.filter(id=customer_id).first()
    if not customer:
        return JsonResponse({'status': 'error', 'message': 'العميل غير موجود.'})
    with transaction.atomic():
        so = SavedOrder.objects.create(customer=customer, name=name[:100], created_by=request.user)
        for it in items:
            pid = it.get('id') or it.get('product_id')
            if not pid:
                continue
            SavedOrderItem.objects.create(
                saved_order=so, product_id=pid,
                quantity=Decimal(str(it.get('quantity') or it.get('qty', 1))),
                sell_unit=it.get('sell_unit', 'box') or 'box',
            )
    return JsonResponse({'status': 'success', 'id': so.id, 'name': so.name})


@login_required
@require_permission('pos', 'view')
def list_saved_orders(request):
    """List a customer's saved orders (id, name, item count)."""
    customer_id = request.GET.get('customer_id')
    if not customer_id:
        return JsonResponse({'status': 'ok', 'orders': []})
    orders = SavedOrder.objects.filter(customer_id=customer_id).annotate(
        n=Count('items')).order_by('-updated_at')
    return JsonResponse({'status': 'ok', 'orders': [
        {'id': o.id, 'name': o.name, 'count': o.n,
         'updated': o.updated_at.strftime('%Y-%m-%d')} for o in orders]})


@login_required
@require_permission('pos', 'view')
def get_saved_order(request, pk):
    """Return a saved order's items for POS autofill (product id, qty, sell_unit, name)."""
    so = SavedOrder.objects.filter(pk=pk).first()
    if not so:
        return JsonResponse({'status': 'error', 'message': 'الطلب غير موجود.'}, status=404)
    items = [{
        'product_id': i.product_id,
        'name': i.product.name if i.product else '',
        'quantity': float(i.quantity),
        'sell_unit': i.sell_unit,
    } for i in so.items.select_related('product').all()]
    return JsonResponse({'status': 'ok', 'id': so.id, 'name': so.name, 'items': items})


@login_required
@require_permission('pos', 'create')
def delete_saved_order(request, pk):
    """Delete a saved order."""
    if request.method not in ('POST', 'DELETE'):
        return JsonResponse({'status': 'error', 'message': 'Invalid method'}, status=405)
    so = SavedOrder.objects.filter(pk=pk).first()
    if so:
        so.delete()
    return JsonResponse({'status': 'success'})


# ── Phase 6.3: stock reservations / sale orders ─────────────────────────────────────

@login_required
@require_permission('pos', 'create')
def create_reservation(request):
    """Create a stock reservation from the POS cart for a customer.

    Holds the items (they reduce available stock while OPEN). An optional deposit (عربون)
    is recorded as a customer credit so it can be applied when the reservation is converted.
    """
    if request.method != 'POST':
        return JsonResponse({'status': 'error', 'message': 'Invalid method'}, status=405)
    data = json.loads(request.body)
    customer_id = data.get('customer_id')
    warehouse_id = data.get('warehouse_id')
    items = data.get('items', [])
    deposit = Decimal(str(data.get('deposit', 0) or 0))
    notes = (data.get('notes') or '').strip()

    if not customer_id:
        return JsonResponse({'status': 'error', 'message': 'يجب اختيار عميل للحجز.'})
    if not warehouse_id:
        return JsonResponse({'status': 'error', 'message': 'لم يتم تحديد المخزن.'})
    if not items:
        return JsonResponse({'status': 'error', 'message': 'السلة فارغة.'})
    customer = Customer.objects.filter(id=customer_id).first()
    warehouse = Warehouse.objects.filter(id=warehouse_id, is_active=True).first()
    if not customer or not warehouse:
        return JsonResponse({'status': 'error', 'message': 'العميل أو المخزن غير موجود.'})

    # A deposit (عربون) can't exceed the reservation total (deposit == total = fully prepaid).
    res_total = Decimal('0')
    for it in items:
        res_total += Decimal(str(it.get('quantity') or it.get('qty', 1))) * Decimal(str(it.get('price', 0) or 0))
    if deposit < 0:
        return JsonResponse({'status': 'error', 'message': 'العربون لا يمكن أن يكون سالباً.'})
    if deposit > res_total:
        return JsonResponse({'status': 'error', 'message': f'العربون ({deposit:.2f}) أكبر من إجمالي الحجز ({res_total:.2f}).'})

    with transaction.atomic():
        res = Reservation.objects.create(
            customer=customer, warehouse=warehouse,
            deposit_amount=deposit, notes=notes, created_by=request.user,
            reservation_number=DocumentSequence.next_number('SO'),
        )
        for it in items:
            pid = it.get('id') or it.get('product_id')
            if not pid:
                continue
            ReservationItem.objects.create(
                reservation=res, product_id=pid,
                quantity=Decimal(str(it.get('quantity') or it.get('qty', 1))),
                unit_price=Decimal(str(it.get('price', 0) or 0)),
            )
        # Deposit (عربون) → customer ledger credit, usable at conversion (Phase 6.3).
        if deposit > 0:
            from crm.models import CustomerPayment
            CustomerPayment.objects.create(
                customer=customer, user=request.user, amount=deposit,
                transaction_type='payment', payment_method='cash',
                notes=f'عربون حجز {res.reservation_number}',
                voucher_number=DocumentSequence.next_number('RV'),
            )
    return JsonResponse({'status': 'success', 'id': res.id, 'number': res.reservation_number})


@login_required
@require_permission('pos', 'view')
def get_reservation(request, pk):
    """Return a reservation's items for POS prefill (convert-to-invoice)."""
    res = Reservation.objects.filter(pk=pk).first()
    if not res:
        return JsonResponse({'status': 'error', 'message': 'الحجز غير موجود.'}, status=404)
    items = [{
        'product_id': i.product_id,
        'name': i.product.name if i.product else '',
        'quantity': float(i.quantity),
        'unit_price': float(i.unit_price),
    } for i in res.items.select_related('product').all()]
    return JsonResponse({
        'status': 'ok', 'id': res.id, 'number': res.reservation_number,
        'customer_id': res.customer_id, 'status_val': res.status, 'items': items,
    })


@login_required
@require_permission('pos', 'create')
def cancel_reservation(request, pk):
    """Cancel an open reservation (releases the held stock automatically)."""
    if request.method != 'POST':
        return JsonResponse({'status': 'error', 'message': 'Invalid method'}, status=405)
    res = Reservation.objects.filter(pk=pk).first()
    if res and res.status == Reservation.STATUS_OPEN:
        res.status = Reservation.STATUS_CANCELLED
        res.save(update_fields=['status'])
    return JsonResponse({'status': 'success'})


@login_required
@require_granular_action('sales', 'reservations', 'pos', 'view')
def reservation_list(request):
    """List reservations (newest first), filterable by status."""
    status = request.GET.get('status', '')
    qs = Reservation.objects.select_related('customer', 'warehouse').all()
    if status:
        qs = qs.filter(status=status)
    return render(request, 'sales/reservation_list.html', {
        'reservations': qs, 'status': status, 'title': 'الحجوزات',
    })


@login_required
@require_permission('pos', 'view')
def reservation_detail(request, pk):
    res = get_object_or_404(Reservation, pk=pk)
    return render(request, 'sales/reservation_detail.html', {
        'res': res, 'items': res.items.select_related('product').all(),
        'title': f'حجز {res.display_number}',
    })

@login_required
@require_permission('pos', 'view')
def pos_mobile(request):
    """
    Mobile-Specific POS View.
    Allows selecting a specific warehouse via 'warehouse_id' GET parameter.
    """
    categories = Category.objects.all()
    customers = Customer.objects.all()
    sys_settings = SystemSetting.objects.first()
    
    # --- WAREHOUSE SELECTION LOGIC ---
    warehouses = Warehouse.objects.filter(is_active=True, is_sales_point=True)
    
    if not request.user.is_superuser:
        profile = request.user.profile
        if profile.allowed_warehouses.exists():
            warehouses = warehouses.filter(id__in=profile.allowed_warehouses.all())
    
    active_warehouse = None
    warehouse_id = request.GET.get('warehouse_id')
    
    if warehouse_id:
        active_warehouse = warehouses.filter(id=warehouse_id).first()
    
    if not active_warehouse and warehouses.exists():
        active_warehouse = warehouses.first()

    products_data = []
    if active_warehouse:
        active_products = Product.objects.filter(is_active=True)
        for p in active_products:
            ws = WarehouseStock.objects.filter(product=p, warehouse=active_warehouse).first()
            qty = ws.quantity if ws else 0
            # Always show the product — the "نفد" badge already renders when stock <= 0.
            p.stock_quantity = qty
            products_data.append(p)
    else:
        products_data = Product.objects.filter(is_active=True)
    # ----------------------------------

    # Simple Shift Check — GLOBAL
    current_shift = get_active_shift()
    
    # ALL active products for transfer/movement modals (not filtered by warehouse stock)
    all_active_products = Product.objects.filter(is_active=True).order_by('name')
    all_warehouses = Warehouse.objects.filter(is_active=True)

    context = {
        'categories': categories,
        'products': products_data,
        'all_products': all_active_products,
        'customers': customers,
        'title': 'موبايل كاشير',
        'sys_settings': sys_settings,
        'current_shift': current_shift,
        'warehouses': warehouses,
        'all_warehouses': all_warehouses,
        'active_warehouse': active_warehouse,
        'require_open_shift': get_policy('shifts.require_open_shift_before_sales'),
        'can_open_shift': cashier_can_open_shift(request.user),
        'can_close_shift': cashier_can_close_shift(request.user),
    }
    return render(request, 'sales/pos_mobile.html', context)


def _serialize_cart_items(raw_items):
    serialized_items = []
    for item in raw_items or []:
        product_id = item.get('id') or item.get('product_id')
        if not product_id:
            continue
            
        qty = float(item.get('quantity', 0) or 0)
        if qty.is_integer():
            qty = int(qty)
            
        serialized_items.append({
            'id': int(product_id),
            'name': item.get('name', ''),
            'quantity': qty,
            'price': float(item.get('price', 0) or 0),
            'discount': float(item.get('discount', 0) or 0),
        })
    return serialized_items


@login_required
@transaction.atomic
def save_draft_ajax(request):
    if request.method != 'POST':
        return JsonResponse({'status': 'error', 'message': 'Invalid request method'}, status=405)

    try:
        data = json.loads(request.body or '{}')
        draft_id = data.get('draft_id')
        customer_id = data.get('customer_id')
        warehouse_id = data.get('warehouse_id')
        if not warehouse_id:
            return JsonResponse({'status': 'error', 'message': 'يجب تحديد مخزن المسودة'}, status=400)

        warehouse = Warehouse.objects.filter(id=warehouse_id, is_active=True).first()
        if not warehouse:
            return JsonResponse({'status': 'error', 'message': 'المخزن غير صالح'}, status=400)

        customer = None
        if customer_id:
            customer = Customer.objects.filter(id=customer_id).first()

        cart_data = _serialize_cart_items(data.get('items', []))
        if not cart_data:
            return JsonResponse({'status': 'error', 'message': 'لا يمكن حفظ مسودة فارغة'}, status=400)

        discount = Decimal(str(data.get('discount', 0) or 0))
        discount_type = data.get('discount_type', 'fixed')
        delivery_cost = Decimal(str(data.get('delivery_cost', 0) or 0))
        notes = data.get('notes', '')

        if draft_id:
            draft = Draft.objects.filter(id=draft_id, user=request.user, status=Draft.STATUS_OPEN).first()
            if not draft:
                return JsonResponse({'status': 'error', 'message': 'المسودة غير موجودة'}, status=404)
        else:
            draft = Draft(user=request.user)

        draft.customer = customer
        draft.warehouse = warehouse
        draft.cart_data = cart_data
        draft.discount = discount
        draft.discount_type = discount_type
        draft.delivery_cost = delivery_cost
        draft.notes = notes
        
        # Save split payment state to draft
        draft.cash_paid = Decimal(str(data.get('cash_paid', 0) or 0))
        draft.wallet_paid = Decimal(str(data.get('wallet_paid', 0) or 0))
        draft.instapay_paid = Decimal(str(data.get('instapay_paid', 0) or 0))
        draft.visa_paid = Decimal(str(data.get('visa_paid', 0) or 0))
        draft.credit_paid = Decimal(str(data.get('credit_paid', 0) or 0))
        
        draft.status = Draft.STATUS_OPEN
        draft.save()

        return JsonResponse({
            'status': 'success',
            'message': 'تم حفظ المسودة بنجاح',
            'draft': draft.to_payload(),
            'summary': draft.to_summary(),
        })
    except Exception as e:
        return JsonResponse({'status': 'error', 'message': str(e)}, status=500)


@login_required
def list_drafts_ajax(request):
    drafts = Draft.objects.filter(user=request.user, status=Draft.STATUS_OPEN).select_related('customer')
    return JsonResponse({'status': 'success', 'drafts': [draft.to_summary() for draft in drafts]})


@login_required
def get_draft_ajax(request, pk):
    draft = get_object_or_404(Draft, id=pk, user=request.user)
    if draft.status != Draft.STATUS_OPEN:
        return JsonResponse({'status': 'error', 'message': 'المسودة غير متاحة'}, status=404)
    return JsonResponse({'status': 'success', 'draft': draft.to_payload()})


@login_required
@transaction.atomic
def delete_draft_ajax(request, pk):
    if request.method not in ['DELETE', 'POST']:
        return JsonResponse({'status': 'error', 'message': 'Invalid request method'}, status=405)
    draft = get_object_or_404(Draft, id=pk, user=request.user, status=Draft.STATUS_OPEN)
    draft.status = Draft.STATUS_CLOSED
    draft.save(update_fields=['status', 'updated_at'])
    return JsonResponse({'status': 'success', 'message': 'تم حذف المسودة'})


@login_required
def draft_invoice_view(request, pk):
    draft = get_object_or_404(Draft.objects.select_related('user', 'customer', 'warehouse'), id=pk, user=request.user)
    if draft.status != Draft.STATUS_OPEN:
        return HttpResponse("Draft not found", status=404)

    style = request.GET.get('style', 'thermal')
    if style not in ['thermal', 'a4', 'a5']:
        style = 'thermal'

    draft_items = []
    for item in draft.cart_data or []:
        qty = Decimal(str(item.get('quantity', 0) or 0))
        price = Decimal(str(item.get('price', 0) or 0))
        discount = Decimal(str(item.get('discount', 0) or 0))
        line_total = (qty * price) - discount
        draft_items.append({
            'name': item.get('name', ''),
            'quantity': qty,
            'price': price,
            'discount': discount,
            'subtotal': line_total if line_total > 0 else Decimal('0'),
        })

    context = {
        'draft': draft,
        'draft_items': draft_items,
        'print_style': style,
        'sys_settings': SystemSetting.objects.first(),
        'is_draft_invoice': True,
        'draft_total': draft.total_amount,
        'draft_subtotal': draft.subtotal_amount,
        'draft_discount_type': draft.discount_type,
    }
    if style == 'thermal':
        return render(request, 'sales/draft_invoice.html', context)
    return render(request, 'sales/draft_invoice_a4.html', context)

@login_required
@require_permission('pos', 'create')
def submit_order_ajax(request):
    """
    API View to handle order submission from POS via AJAX.
    Now supports explicit warehouse selection.
    """
    if request.method == 'POST':
        try:
            data = json.loads(request.body)
            
            # 1. Parse Basic Data
            customer_id = data.get('customer_id')
            cart_items = data.get('items', [])
            if not cart_items:
                return JsonResponse({'status': 'error', 'message': 'لا يمكن إتمام البيع بدون إضافة منتجات'})

            from settings.policies import get_policy as _shift_policy
            if _shift_policy('shifts.require_open_shift_before_sales') and get_active_shift() is None:
                return JsonResponse({'status': 'error', 'message': 'لا يمكن إتمام البيع بدون فتح وردية أولاً. يرجى فتح وردية من لوحة التحكم أو الخزنة.'})
            discount = Decimal(str(data.get('discount', 0)))
            discount_type = data.get('discount_type', 'fixed')
            delivery_cost = Decimal(str(data.get('delivery_cost', 0)))
            notes = data.get('notes', '')
            applied_deal_id = data.get('applied_deal_id')
            
            cash_paid = Decimal(str(data.get('cash_paid', 0)))
            wallet_paid = Decimal(str(data.get('wallet_paid', 0)))
            instapay_paid = Decimal(str(data.get('instapay_paid', 0)))
            visa_paid = Decimal(str(data.get('visa_paid', 0)))
            credit_paid = Decimal(str(data.get('credit_paid', 0)))

            # CRITICAL: received_amount for Customer Balance calculation
            # MUST only include NEW money (Cash, Wallet, Insta, Visa).
            # credit_paid is OLD money already accounted for in the balance.
            received_amount = cash_paid + wallet_paid + instapay_paid + visa_paid

            # Determine primary payment method for display purposes
            payment_method = 'custom'
            if wallet_paid == 0 and instapay_paid == 0 and visa_paid == 0 and cash_paid > 0:
                payment_method = 'cash'
            elif cash_paid == 0 and instapay_paid == 0 and visa_paid == 0 and wallet_paid > 0:
                payment_method = 'wallet'
            elif cash_paid == 0 and wallet_paid == 0 and visa_paid == 0 and instapay_paid > 0:
                payment_method = 'instapay'
            elif cash_paid == 0 and wallet_paid == 0 and instapay_paid == 0 and visa_paid > 0:
                payment_method = 'visa'
            
            # Online Order Flag
            is_online = data.get('is_online', False)
            linked_draft_id = data.get('linked_draft_id')
            reservation_id = data.get('reservation_id')  # Phase 6.3: converting a reservation
            driver_id = data.get('driver_id') or None

            # Customer handling
            customer = None
            if customer_id:
                customer = Customer.objects.filter(id=customer_id).first()

            # Any order with a delivery cost is a shipping order too, even if the cashier
            # forgot to flip the "طلب أونلاين" toggle — both need a named customer with a
            # real phone + address so the shipment/invoice/dashboard have somewhere to go.
            requires_shipping = bool(is_online) or delivery_cost > 0
            if requires_shipping:
                if not customer:
                    return JsonResponse({'status': 'error', 'message': 'يجب اختيار عميل مسجل لطلبات الأونلاين/الشحن.'})
                if not (customer.phone or '').strip():
                    return JsonResponse({'status': 'error', 'message': 'رقم هاتف العميل مطلوب لطلبات الأونلاين/الشحن.'})
                if not (customer.address or '').strip():
                    return JsonResponse({'status': 'error', 'message': 'عنوان العميل مطلوب لطلبات الأونلاين/الشحن.'})

            # --- IDENTIFY WAREHOUSE ---
            # Try to get warehouse_id from the payload
            warehouse_id = data.get('warehouse_id')
            warehouse = None
            
            if warehouse_id:
                 warehouse = Warehouse.objects.filter(id=warehouse_id, is_active=True).first()
            
            # Fallback if not provided (though frontend should provide it)
            if not warehouse:
                warehouse = Warehouse.objects.filter(is_active=True, is_sales_point=True).first()
                
            if not warehouse:
                return JsonResponse({'status': 'error', 'message': 'لا يوجد مخزن بيع محدد أو نشط'})

            # Recipe stock is checked (and warned about, non-blocking) at add-to-cart time
            # instead — see sales.views.api_check_recipe_stock, called the moment a
            # recipe-bearing product/variant is added on the POS/waiter screens. Checking
            # again here at final checkout would just re-show the same warning for an item
            # the cashier already confirmed adding despite the shortage.

            # Phase 3.1: enforce warehouse restriction SERVER-SIDE (not just the dropdown).
            if not request.user.is_superuser:
                profile = getattr(request.user, 'profile', None)
                if profile and profile.allowed_warehouses.exists():
                    if not profile.allowed_warehouses.filter(id=warehouse.id).exists():
                        return JsonResponse({'status': 'error', 'message': 'غير مصرح لك بالبيع من هذا المخزن'})
            # --------------------------

            # --- Phase 3.2: per-user operational limits ---
            profile = getattr(request.user, 'profile', None)

            # --- Phase 6.5: server-side price resolution / enforcement ---
            # Resolve each standard ('box') line's price from the customer's tier + quantity
            # breaks. Cashiers WITHOUT price-edit permission cannot deviate from the resolved
            # price — the client-sent price is overridden, so a tampered POST can't underprice.
            # Strip/sub-unit lines keep their own (sub-unit) pricing and are not touched here.
            from products.pricing import resolve_price as _resolve_price
            _tier = customer.customer_type if customer else 'retail'
            _can_edit_price = profile.allows_price_edit() if profile else True
            if not _can_edit_price:
                for it in cart_items:
                    if (it.get('sell_unit', 'box') or 'box') != 'box':
                        continue
                    _prod = Product.objects.filter(id=(it.get('id') or it.get('product_id'))).first()
                    if not _prod:
                        continue
                    _qty = Decimal(str(it.get('quantity') or it.get('qty', 1)))
                    it['price'] = float(_resolve_price(_prod, _tier, _qty))
            # ------------------------------------------------------------

            # --- Layer 2: store-wide policy guards (settings/policies.py) ---
            from settings.policies import get_policy as _policy
            # Store-level discount switch (independent of the per-user discount cap below).
            if discount and discount > 0 and not _policy('cashier.allow_discount'):
                return JsonResponse({'status': 'error', 'message': 'الخصم غير مسموح به في إعدادات المتجر'})
            # Credit (آجل) sales must name a customer when the policy requires it.
            if credit_paid and credit_paid > 0 and not customer and _policy('sales.require_customer_on_credit'):
                return JsonResponse({'status': 'error', 'message': 'يجب تحديد العميل لإتمام البيع الآجل'})
            # ----------------------------------------------------------------

            # --- Phase 3.3: per-user guards now collect violations and may be escalated to a
            #     manager (reject OR authorize inline). An optional approver in the payload
            #     authorizes ALL collected overrides; otherwise we return approval_required.
            from accounts import approvals as _appr
            violations = []  # [{kind, message, detail}]
            _appr_supplied = bool(data.get('approver_username') or data.get('approver_password'))
            approver = _appr.authorize_inline(
                data.get('approver_username'), data.get('approver_password'), request.user)

            if profile:
                # 1) Discount cap
                cart_subtotal = Decimal('0')
                for it in cart_items:
                    cart_subtotal += Decimal(str(it.get('quantity') or it.get('qty', 1))) * Decimal(str(it.get('price', 0)))
                if discount and discount > 0:
                    if discount_type == 'percent':
                        applied_pct = discount
                        applied_amount = cart_subtotal * (discount / Decimal('100'))
                    else:
                        applied_pct = (discount / cart_subtotal * Decimal('100')) if cart_subtotal > 0 else Decimal('0')
                        applied_amount = discount
                    cap = profile.discount_cap()
                    if applied_pct > cap + Decimal('0.001'):
                        violations.append({'kind': _appr.OVER_DISCOUNT,
                                           'message': f'الخصم ({applied_pct:.1f}%) يتجاوز الحد المسموح لك ({cap:.0f}%)',
                                           'detail': {'applied_pct': float(applied_pct), 'cap': float(cap)}})
                    # Independent fixed-amount cap (0 = unlimited) — a % cap alone doesn't
                    # bound a fixed discount's actual currency value on a large invoice.
                    amount_cap = profile.discount_amount_cap()
                    if amount_cap > 0 and applied_amount > amount_cap + Decimal('0.01'):
                        violations.append({'kind': _appr.OVER_DISCOUNT,
                                           'message': f'قيمة الخصم ({applied_amount:.2f}) تتجاوز الحد المسموح لك ({amount_cap:.2f})',
                                           'detail': {'applied_amount': float(applied_amount), 'amount_cap': float(amount_cap)}})
                # 2) Below-cost guard
                if not profile.allows_below_cost():
                    for it in cart_items:
                        prod_id = it.get('id') or it.get('product_id')
                        prod = Product.objects.filter(id=prod_id).first()
                        if prod and prod.cost_price and Decimal(str(it.get('price', 0))) < prod.cost_price:
                            violations.append({'kind': _appr.BELOW_COST,
                                               'message': f'البيع تحت سعر التكلفة للصنف: {prod.name}',
                                               'detail': {'product_id': prod.id, 'name': prod.name,
                                                          'price': float(it.get('price', 0)), 'cost': float(prod.cost_price)}})
                            break
                # 3) Below-sale-price guard (Phase 3.2 expanded): block manual prices below the
                #    resolved tier price for this customer/qty. Doesn't affect wholesale tiers
                #    (the resolved price IS the tier price) — only blocks going under it.
                if not profile.allows_below_sale_price():
                    for it in cart_items:
                        if (it.get('sell_unit', 'box') or 'box') != 'box':
                            continue
                        prod = Product.objects.filter(id=(it.get('id') or it.get('product_id'))).first()
                        if not prod:
                            continue
                        _q = Decimal(str(it.get('quantity') or it.get('qty', 1)))
                        tier_price = _resolve_price(prod, _tier, _q)
                        if tier_price and Decimal(str(it.get('price', 0))) < tier_price - Decimal('0.001'):
                            violations.append({'kind': _appr.BELOW_SALE_PRICE,
                                               'message': f'البيع تحت سعر القطاعي للصنف: {prod.name}',
                                               'detail': {'product_id': prod.id, 'name': prod.name,
                                                          'price': float(it.get('price', 0)), 'tier_price': float(tier_price)}})
                            break
                # 4) Change-unit guard: restricted cashiers can only sell in the base unit.
                if not profile.allows_change_unit():
                    for it in cart_items:
                        if (it.get('sell_unit', 'box') or 'box') != 'box':
                            violations.append({'kind': _appr.CHANGE_UNIT,
                                               'message': 'تغيير وحدة البيع غير مصرح به',
                                               'detail': {'sell_unit': it.get('sell_unit')}})
                            break

            # Approval gate: if there are violations and no authorized approver, escalate.
            if violations and not approver:
                return JsonResponse({
                    'status': 'approval_required',
                    'message': 'هذه العملية تتطلب موافقة مدير',
                    'approver_error': _appr_supplied,  # creds were supplied but rejected
                    'violations': [{'kind': v['kind'], 'message': v['message']} for v in violations],
                })
            if violations and approver:
                _appr.record_approvals(request.user, approver, violations)

            with transaction.atomic():
                active_shift, _ = get_or_create_active_shift(request.user)

                # --- 0. PRE-CHECK STOCK ---
                # This ensures we don't start creating the order if any item is out of stock
                # improving consistency and UX.
                # Phase 6.3: stock held by OPEN reservations is unavailable, except the one
                # being converted (its held stock is what this very sale consumes).
                from .models import reserved_quantities
                _reserved = reserved_quantities(warehouse.id, exclude_reservation_id=reservation_id)
                _neg_ok = _policy('sales.allow_negative_stock') or (profile and profile.allows_below_zero_stock())
                _recipe_ids = set(_recipe_product_ids())
                for item in cart_items:
                    prod_id = item.get('id') or item.get('product_id')
                    product = Product.objects.get(id=prod_id)

                    # Made-to-order items are prepared on demand — they're never stocked as
                    # a finished good, so their own stock_quantity/ProductVariant.stock_quantity
                    # is always ~0 and irrelevant here. This covers both: (a) any item whose
                    # category is a menu category (Category.is_menu_category — the same flag
                    # POS/waiter already use to never show these as "منتهي"), and (b) items
                    # with an active Recipe specifically (ingredient availability is a
                    # separate, non-blocking warning — see sales.views.api_check_recipe_stock).
                    if (product.category_id and product.category.is_menu_category) or product.id in _recipe_ids:
                        continue

                    qty = Decimal(str(item.get('quantity') or item.get('qty', 1)))
                    sell_unit = item.get('sell_unit', 'box')
                    strips_per_box = item.get('strips_per_box') or product.strips_per_box or 1

                    # Fashion variant: check the variant's own stock, not warehouse stock.
                    variant_id = item.get('variant_id')
                    if variant_id:
                        from products.models import ProductVariant
                        _v = ProductVariant.objects.filter(id=variant_id, product=product).first()
                        if not _v:
                            raise Exception(f"خيار غير موجود للصنف: {product.name}")
                        if _v.stock_quantity < qty and not _neg_ok:
                            raise Exception(f"الكمية غير متوفرة للخيار {_v.label} ({product.name}) — المتاح: {_v.stock_quantity}")
                        continue

                    deduction_qty = qty
                    if sell_unit == 'strip' and strips_per_box > 1:
                        deduction_qty = qty / Decimal(str(strips_per_box))

                    wh_stock, created = WarehouseStock.objects.get_or_create(warehouse=warehouse, product=product)
                    available = wh_stock.quantity - _reserved.get(product.id, Decimal('0'))
                    # Layer 2: 'block_sale_when_out_of_stock' blocks zero-stock items even if
                    # negative stock is otherwise allowed; 'allow_negative_stock' lets a sale
                    # exceed the available quantity. Defaults preserve the original behavior.
                    if available <= 0 and _policy('inventory.block_sale_when_out_of_stock'):
                        raise Exception(f"الصنف نفد من المخزون في {warehouse.name}: {product.name}")
                    if available < deduction_qty and not _neg_ok:
                        raise Exception(f"الكمية غير متوفرة في {warehouse.name} للصنف: {product.name} (المتاح: {available})")

                # A driver picked in the POS at creation time makes this a delivery order —
                # only accept one that actually belongs to this branch (a driver dropdown is
                # scoped to active_warehouse, but the POST body isn't trusted blindly).
                driver = None
                if driver_id:
                    from restaurant.models import Driver
                    driver = Driver.objects.filter(id=driver_id, branch=warehouse, is_active=True).first()

                # Snapshot the store's VAT rate/mode NOW — vat_breakdown() locks onto this
                # forever, so a later rate change never rewrites this invoice's own VAT.
                from .services import current_vat_snapshot
                _vat_rate_snap, _vat_included_snap = current_vat_snapshot()

                # Create Order
                order = Order(
                    user=request.user,
                    vat_rate_snapshot=_vat_rate_snap,
                    vat_included_snapshot=_vat_included_snap,
                    shift=active_shift,
                    customer=customer,
                    warehouse=warehouse,  # FIX #1.7: persist the sale's warehouse
                    discount=discount,
                    discount_type=discount_type,
                    delivery_cost=delivery_cost,
                    notes=notes,
                    salesman_name=(data.get('salesman_name') or '').strip()[:100],  # Phase 6.8
                    # Payment info
                    payment_method=payment_method,
                    received_amount=received_amount,
                    cash_paid=cash_paid,
                    wallet_paid=wallet_paid,
                    instapay_paid=instapay_paid,
                    visa_paid=visa_paid,
                    credit_paid=credit_paid,
                    is_online_order=requires_shipping,
                    # A driver assigned at POS time (or the online/delivery-cost flag) always
                    # wins and routes this straight to the restaurant app's delivery dashboard
                    # (driver cash-custody flow) — otherwise the cashier's own صالة/تيك أواي
                    # pick from the POS is used.
                    order_type=(Order.ORDER_TYPE_DELIVERY if (driver or requires_shipping)
                                else (data.get('order_type') if data.get('order_type') in
                                      (Order.ORDER_TYPE_DINE_IN, Order.ORDER_TYPE_TAKEAWAY)
                                      else Order.ORDER_TYPE_DINE_IN)),
                    driver=driver,
                    # Tailoring (if any)
                    is_tailoring=data.get('is_tailoring', False),
                    tailoring_type=data.get('tailoring_type', ''),
                    tailoring_cost=Decimal(str(data.get('tailoring_cost', 0))),
                    tailoring_status='pending' if data.get('is_tailoring') else 'delivered'
                )
                order.save()

                if driver:
                    from restaurant.consumers import push_event
                    push_event('delivery', warehouse.id, {
                        'event': 'order_assigned', 'order_id': order.id, 'driver_id': driver.id,
                    })

                # Phase 6.1: assign a gap-free human invoice number (INV-YYYY-NNNNN).
                from .models import DocumentSequence
                order.invoice_number = DocumentSequence.next_number('INV')
                order.save(update_fields=['invoice_number'])

                # Fetch applied deal if any
                applied_deal = None
                if applied_deal_id:
                    from financial.payroll_models import DealDiscount
                    applied_deal = DealDiscount.objects.filter(id=applied_deal_id, is_active=True).first()

                # Process items + compute totals via the shared OrderService (Phase 2.2)
                from .services import issue_cart_items, compute_discount_and_total, compute_dine_in_service_charge
                subtotal, qualified_subtotal = issue_cart_items(
                    order, cart_items, warehouse, request.user, applied_deal,
                    note_prefix="فاتورة مبيعات",
                    allow_negative_stock=_neg_ok,
                )
                order.subtotal_amount = subtotal

                result = compute_discount_and_total(
                    subtotal, qualified_subtotal,
                    discount=discount, discount_type=discount_type,
                    applied_deal=applied_deal, delivery_cost=delivery_cost,
                    tailoring_cost=order.tailoring_cost,
                    service_charge=compute_dine_in_service_charge(subtotal, order.order_type),
                    vat_rate=order.vat_rate_snapshot, vat_included=order.vat_included_snapshot,
                    cart_items=cart_items,
                )
                order.discount = result['discount']
                order.discount_type = result['discount_type']
                order.applied_deal = result['applied_deal']
                order.total_amount = result['total']
                order.service_charge = result['service_charge']
                order.vat_amount = result['vat_amount']

                # Completion Status
                if order.remaining_amount <= 0:
                    order.is_completed = True
                elif requires_shipping:
                    # Cash-on-delivery: unpaid at sale time by design — the driver collects
                    # this from the customer at the door and settles it back at the shop
                    # (driver_return_settle), it's never real store credit extended to the
                    # customer. Skip the "بيع آجل" gates (blacklist/credit-limit) entirely;
                    # a registered customer with phone/address was already required above.
                    order.is_completed = False
                else:
                    # Layer 2: 'sales.require_customer_on_credit' — the store decides whether a
                    # credit/deferred sale (paid < total) needs a named customer. This is the
                    # real "بيع آجل" case (unlike the credit_paid check above, which is about
                    # redeeming a customer's existing store-credit balance, not creating debt).
                    if not customer:
                        if _policy('sales.require_customer_on_credit'):
                            raise Exception("لا يمكن تسجيل مديونية لعميل نقدي عام. يرجى اختيار عميل مسجل.")
                    else:
                        # Phase 8.6: blacklisted customers may not take new credit.
                        if getattr(customer, 'is_blacklisted', False):
                            raise Exception("هذا العميل في القائمة السوداء — غير مسموح بالبيع الآجل له.")
                        # Phase 6.9 / 3.3: enforce the customer's credit limit. Overridable by a
                        # manager — either the cashier themselves has financial:manage, or an
                        # authorized approver was supplied inline (recorded as an ApprovalRequest).
                        # Layer-2 policy gate: store may disable credit-limit enforcement entirely.
                        can_override = (request.user.is_superuser
                                        or has_permission(request.user, 'financial', 'manage')
                                        or approver is not None)
                        if _policy('customers.enforce_credit_limit') and customer.would_exceed_credit(order.remaining_amount):
                            if not can_override:
                                avail = customer.credit_available()
                                raise Exception(f"تجاوز حد الائتمان للعميل (المتاح: {avail:.2f}). يلزم موافقة المدير.")
                            if approver is not None:
                                _appr.record_approvals(request.user, approver, [{
                                    'kind': _appr.OVER_CREDIT_LIMIT,
                                    'message': f'تجاوز حد ائتمان العميل: {customer.first_name} {customer.last_name}',
                                    'detail': {'customer_id': customer.id, 'debt': float(order.remaining_amount),
                                               'available': float(customer.credit_available() or 0)},
                                }])
                    order.is_completed = False # Means there is debt/COD

                order.save()

                # If it's an online/shipping order, automatically create a Shipment record
                # so it shows up on لوحة متابعة الشحن والأونلاين regardless of whether the
                # cashier used the toggle or just added a delivery cost.
                if requires_shipping:
                    Shipment.objects.get_or_create(
                        order=order,
                        defaults={
                            'status': 'new',
                            'delivery_type': 'shipping',
                            'shipping_address': customer.address if customer and customer.address else (order.shipping_address or 'عنوان غير مسجل')
                        }
                    )
                
                # --- Financial Integration: Record Sale Transaction ---
                record_sale_transaction(order, request.user)

                # A cashier ringing up a cafe menu item directly (not through the waiter
                # screen) must still send it to the kitchen — otherwise the drink/food
                # never reaches the KDS or gets a prep ticket printed.
                from restaurant.services import notify_kitchen_for_order
                notify_kitchen_for_order(request, order)

                # --- Create Notifications ---
                if Notification:
                    # Notify all active users EXCEPT the one who made the invoice
                    recipients = User.objects.filter(is_active=True).exclude(id=request.user.id)
                    notif_title = f"فاتورة جديدة #{order.id}"
                    notif_msg = f"تم إنشاء فاتورة بقيمة {order.total_amount} ج.م بواسطة {request.user.first_name}"
                    notif_link = f"/sales/invoice/{order.id}/" # Matches your invoice URL pattern

                    notifications_to_create = [
                        Notification(
                            recipient=user,
                            title=notif_title,
                            message=notif_msg,
                            link=notif_link,
                            created_by=request.user
                        ) for user in recipients
                    ]
                    Notification.objects.bulk_create(notifications_to_create)
                # ---------------------------------

                # Close linked draft if order was created from it.
                if linked_draft_id:
                    Draft.objects.filter(
                        id=linked_draft_id,
                        user=request.user,
                        status=Draft.STATUS_OPEN
                    ).update(status=Draft.STATUS_CLOSED)

                # Phase 6.3: if this sale converts a reservation, close it and link the order.
                if reservation_id:
                    from .models import Reservation
                    Reservation.objects.filter(
                        id=reservation_id, status=Reservation.STATUS_OPEN
                    ).update(status=Reservation.STATUS_CONVERTED, converted_order=order)

            # Get updated balance if a customer was selected
            updated_balance = 0
            if customer:
                # Refresh from DB to get latest balance after transaction
                customer.refresh_from_db()
                updated_balance = float(customer.get_balance())

            return JsonResponse({
                'status': 'success', 
                'order_id': order.id,
                'updated_balance': updated_balance
            })

        except Exception as e:
            # logger.error (not print/console) — Windows consoles use cp1252 and a bare
            # print() crashes on Arabic text, turning this into an unhandled 500 that the
            # frontend's fetch().then(r => r.json()) can't parse, so the error is silently
            # swallowed client-side instead of showing the real message.
            logger.error("Order Processing Error: %s", e)
            return JsonResponse({'status': 'error', 'message': str(e)})
    
    return JsonResponse({'status': 'error', 'message': 'Invalid Request Method'})

@login_required
def create_customer_ajax(request):
    """
    AJAX view to create a new customer directly from POS.
    """
    if request.method == 'POST':
        try:
            data = json.loads(request.body)
            first_name = data.get('first_name')
            last_name = data.get('last_name', '')
            phone = data.get('phone')
            address = data.get('address', '') 
            customer_type = data.get('customer_type', 'retail')

            if not first_name or not phone:
                return JsonResponse({'status': 'error', 'message': 'الاسم ورقم الهاتف مطلوبان'})

            if Customer.objects.filter(phone=phone).exists():
                return JsonResponse({'status': 'error', 'message': 'رقم الهاتف مسجل بالفعل لعميل آخر'})

            customer = Customer.objects.create(
                first_name=first_name,
                last_name=last_name,
                phone=phone,
                address=address, 
                customer_type=customer_type
            )

            return JsonResponse({
                'status': 'success',
                'customer': {
                    'id': customer.id,
                    'name': f"{customer.first_name} {customer.last_name}",
                    'phone': customer.phone,
                    'type': customer.customer_type,
                    'display_type': customer.get_customer_type_display(),
                    'address': customer.address 
                }
            })
        except Exception as e:
            return JsonResponse({'status': 'error', 'message': str(e)})
    
    return JsonResponse({'status': 'error', 'message': 'Invalid request method'})

@login_required
def update_tailoring_status_ajax(request):
    """
    AJAX view to update tailoring status.
    """
    if request.method == 'POST':
        try:
            data = json.loads(request.body)
            order_id = data.get('order_id')
            new_status = data.get('status')
            new_tailor_name = data.get('tailor_name')
            new_tailoring_cost = data.get('tailoring_cost')

            order = Order.objects.get(id=order_id)
            order.tailoring_status = new_status
            if new_tailor_name is not None:
                order.tailor_name = new_tailor_name
            if new_tailoring_cost not in (None, ''):
                try:
                    order.tailoring_cost = Decimal(str(new_tailoring_cost))
                    if order.is_tailoring:
                        order.subtotal_amount = order.tailoring_cost
                        order.total_amount = order.tailoring_cost
                except Exception:
                    pass
            order.save(update_fields=['tailoring_status', 'tailor_name', 'tailoring_cost',
                                       'subtotal_amount', 'total_amount'])

            # On delivery ("استلام"), auto-collect whatever's still owed on a work order.
            if order.is_tailoring and new_status == 'delivered':
                from .services import collect_tailoring_payment
                remaining = order.total_amount - order.received_amount
                if remaining > 0:
                    collect_tailoring_payment(order, remaining, request.user,
                                               note=f"تحصيل المتبقي عند الاستلام - أمر شغل #{order.id}")

            return JsonResponse({'status': 'success'})
        except Exception as e:
            return JsonResponse({'status': 'error', 'message': str(e)})
    return JsonResponse({'status': 'error', 'message': 'Invalid Method'})

@login_required
@require_granular_action_open('sales', 'orders')
def order_list(request):
    from django.db.models import Exists, OuterRef, Subquery, Sum, Value, DecimalField
    from django.db.models.functions import Coalesce
    _returned_amount_sq = ReturnInvoice.objects.filter(
        original_order=OuterRef('pk')
    ).values('original_order').annotate(total=Sum('total_refund_amount')).values('total')
    orders = Order.objects.annotate(
        has_oversold=Exists(OrderItem.objects.filter(order=OuterRef('pk'), shortfall_qty__gt=0)),
        has_return=Exists(ReturnInvoice.objects.filter(original_order=OuterRef('pk'))),
        returned_amount=Coalesce(
            Subquery(_returned_amount_sq, output_field=DecimalField(max_digits=10, decimal_places=2)),
            Value(0), output_field=DecimalField(max_digits=10, decimal_places=2)
        ),
    ).order_by('-created_at')

    # Phase 3.5: invoice visibility scoping. Cashiers see only their own invoices unless
    # granted 'sales:view_all' (managers/superusers/master always see everything).
    can_see_all = (request.user.is_superuser
                   or getattr(getattr(request.user, 'profile', None), 'is_master', False)
                   or has_permission(request.user, 'sales', 'view_all')
                   or has_permission(request.user, 'financial', 'manage'))
    if not can_see_all:
        orders = orders.filter(user=request.user)

    # Date Filter
    date_from = request.GET.get('date_from')
    date_to = request.GET.get('date_to')
    if date_from in [None, '', 'None', 'undefined']: date_from = None
    if date_to in [None, '', 'None', 'undefined']: date_to = None

    if date_from:
        orders = orders.filter(created_at__date__gte=date_from)
    if date_to:
        orders = orders.filter(created_at__date__lte=date_to)

    # User Filter
    user_id = request.GET.get('user')
    if user_id:
        orders = orders.filter(user_id=user_id)

    # Search Filter
    q = request.GET.get('q')
    if q:
        orders = orders.filter(
            Q(id__icontains=q) |
            Q(customer__first_name__icontains=q) |
            Q(customer__phone__icontains=q)
        )

    # Stats
    # Stats exclude voided invoices; the list itself still shows them (with a badge).
    stats_qs = orders.exclude(status='void')
    orders_total = stats_qs.aggregate(Sum('total_amount'))['total_amount__sum'] or 0
    orders_collected = stats_qs.aggregate(Sum('received_amount'))['received_amount__sum'] or 0
    orders_credit_used = stats_qs.aggregate(Sum('credit_paid'))['credit_paid__sum'] or 0
    orders_count = stats_qs.count()
    # Debt = total - new money received - credit already applied (matches Order.remaining_amount)
    orders_debt = float(orders_total) - float(orders_collected) - float(orders_credit_used)

    users = User.objects.all()

    context = {
        'orders': orders,
        'orders_total': orders_total,
        'orders_collected': orders_collected,
        'orders_debt': orders_debt,
        'orders_count': orders_count,
        'users': users,
        'sys_settings': SystemSetting.objects.first(),
        'date_from': date_from,
        'date_to': date_to,
        'user_filter': user_id,
        'search_query': q,
    }
    return render(request, 'sales/order_list.html', context)


@login_required
def order_report(request):
    # .active() excludes voided orders — every other revenue view in this app
    # (order_list stats, financial_statement) already does this; a voided invoice
    # must not inflate the reported sales total.
    orders = Order.objects.active().order_by('-created_at')
    
    date_from = request.GET.get('date_from')
    date_to = request.GET.get('date_to')
    if date_from in [None, '', 'None', 'undefined']: date_from = None
    if date_to in [None, '', 'None', 'undefined']: date_to = None

    if date_from: orders = orders.filter(created_at__date__gte=date_from)
    if date_to: orders = orders.filter(created_at__date__lte=date_to)
    
    user_id = request.GET.get('user')
    if user_id: orders = orders.filter(user_id=user_id)

    total_sales = orders.aggregate(Sum('total_amount'))['total_amount__sum'] or 0
    
    context = {
        'orders': orders,
        'total_sales': total_sales,
        'date_from': date_from,
        'date_to': date_to,
        'sys_settings': SystemSetting.objects.first()
    }
    return render(request, 'sales/order_report.html', context)

@login_required
def order_invoice(request, pk):
    """
    Renders invoice. Switches template based on 'style' param (thermal, a4, a5).
    """
    order = get_object_or_404(Order, pk=pk)
    sys_settings = SystemSetting.objects.first()
    
    # Get print style (thermal, a4, a5)
    print_style = request.GET.get('style', 'thermal')
    
    # Calculate Arabic text for the browser view too
    arabic_total_text = tafqeet_ar(order.total_amount)

    context = {
        'order': order,
        'sys_settings': sys_settings,
        'print_style': print_style,
        'arabic_total_text': arabic_total_text
    }
    
    # Route to new template for A4/A5
    if print_style in ['a4', 'a5']:
        return render(request, 'sales/invoice_a4.html', context)
    else:
        # Default/Thermal
        return render(request, 'sales/invoice.html', context)

@login_required
@require_granular_action_open('sales', 'factory')
def factory_list(request):
    orders = Order.objects.filter(is_tailoring=True).order_by('-created_at')

    status = request.GET.get('status')
    if status:
        orders = orders.filter(tailoring_status=status)

    context = {
        'orders': orders,
        'status_choices': Order.TAILORING_STATUS_CHOICES
    }
    return render(request, 'sales/factory_list.html', context)

@login_required
@require_granular_action_open('sales', 'factory_create')
def factory_order_create(request):
    """Standalone creation of a tailoring/work order (أمر شغل), independent of a POS checkout.

    Used for jobs taken in without a full sale yet (e.g. measurements taken at the counter) —
    total_amount stays 0 so it never inflates revenue reports; tailoring_cost just tracks the
    job's value on the factory page itself.
    """
    from crm.models import Customer

    if request.method == 'POST':
        customer_id = request.POST.get('customer_id')
        customer = Customer.objects.filter(pk=customer_id).first() if customer_id else None
        tailoring_type = (request.POST.get('tailoring_type') or '').strip()
        tailor_name = (request.POST.get('tailor_name') or '').strip()
        notes = (request.POST.get('notes') or '').strip()
        try:
            tailoring_cost = Decimal(str(request.POST.get('tailoring_cost') or '0'))
        except Exception:
            tailoring_cost = Decimal('0')
        try:
            deposit_now = Decimal(str(request.POST.get('deposit_now') or '0'))
        except Exception:
            deposit_now = Decimal('0')

        if not tailoring_type:
            messages.error(request, 'يرجى إدخال نوع التفصيل.')
        else:
            from .services import collect_tailoring_payment
            order = Order.objects.create(
                user=request.user,
                customer=customer,
                is_tailoring=True,
                tailoring_type=tailoring_type,
                tailoring_cost=tailoring_cost,
                tailoring_status='pending',
                tailor_name=tailor_name,
                notes=notes,
                is_completed=False,
                subtotal_amount=tailoring_cost,
                total_amount=tailoring_cost,
            )
            if deposit_now > 0:
                collect_tailoring_payment(order, deposit_now, request.user,
                                           note=f"دفعة مقدمة أمر شغل #{order.id}")
            messages.success(request, f'تم إنشاء أمر الشغل #{order.id} بنجاح.')
            return redirect('factory_list')

    customers = Customer.objects.order_by('first_name', 'last_name')[:500]
    return render(request, 'sales/factory_order_create.html', {'customers': customers})

@login_required
def factory_order_edit(request, pk):
    """Edit an existing tailoring/work order's details (type, cost, tailor, customer, notes, status)."""
    from crm.models import Customer

    order = get_object_or_404(Order, pk=pk, is_tailoring=True)

    if request.method == 'POST':
        customer_id = request.POST.get('customer_id')
        tailoring_type = (request.POST.get('tailoring_type') or '').strip()
        tailor_name = (request.POST.get('tailor_name') or '').strip()
        notes = (request.POST.get('notes') or '').strip()
        tailoring_status = request.POST.get('tailoring_status') or order.tailoring_status
        try:
            tailoring_cost = Decimal(str(request.POST.get('tailoring_cost') or '0'))
        except Exception:
            tailoring_cost = Decimal('0')
        try:
            deposit_now = Decimal(str(request.POST.get('deposit_now') or '0'))
        except Exception:
            deposit_now = Decimal('0')

        if not tailoring_type:
            messages.error(request, 'يرجى إدخال نوع التفصيل.')
        else:
            from .services import collect_tailoring_payment
            order.customer = Customer.objects.filter(pk=customer_id).first() if customer_id else None
            order.tailoring_type = tailoring_type
            order.tailoring_cost = tailoring_cost
            order.subtotal_amount = tailoring_cost
            order.total_amount = tailoring_cost
            order.tailor_name = tailor_name
            order.notes = notes
            order.tailoring_status = tailoring_status
            order.save(update_fields=['customer', 'tailoring_type', 'tailoring_cost', 'subtotal_amount',
                                       'total_amount', 'tailor_name', 'notes', 'tailoring_status'])

            # Collect whatever the user is depositing right now.
            if deposit_now > 0:
                collect_tailoring_payment(order, deposit_now, request.user,
                                           note=f"دفعة أمر شغل #{order.id}")
            # On delivery ("استلام"), auto-collect whatever's still owed.
            if tailoring_status == 'delivered':
                remaining = order.total_amount - order.received_amount
                if remaining > 0:
                    collect_tailoring_payment(order, remaining, request.user,
                                               note=f"تحصيل المتبقي عند الاستلام - أمر شغل #{order.id}")

            messages.success(request, f'تم تعديل أمر الشغل #{order.id} بنجاح.')
            return redirect('factory_list')

    customers = Customer.objects.order_by('first_name', 'last_name')[:500]
    return render(request, 'sales/factory_order_create.html', {
        'customers': customers,
        'order': order,
        'status_choices': Order.TAILORING_STATUS_CHOICES,
    })

@login_required
def factory_order_delete(request, pk):
    """Delete a standalone work order. If it has any deposit/payment collected, the user
    chooses whether to return that deposit to the customer:
      - return_deposit='yes': the linked cash-drawer Transaction(s) are deleted too, which
        reverses the account balance (Transaction.delete() handles that) — money leaves the
        drawer, since it's going back to the customer.
      - otherwise: the Transaction(s) stay as-is (shop keeps the deposit), only their order
        FK is cleared by the DB (SET_NULL) when the order itself is deleted.
    Either way the order disappears from أوامر الشغل and from the revenue dashboard total.
    """
    order = get_object_or_404(Order, pk=pk, is_tailoring=True)

    if order.items.exists():
        messages.error(request, 'لا يمكن حذف هذا الأمر لأنه مرتبط بفاتورة بيع فعلية (يحتوي على أصناف). استخدم المرتجعات بدلاً من ذلك.')
        return redirect('factory_list')

    if request.method == 'POST':
        return_deposit = request.POST.get('return_deposit') == 'yes'
        if return_deposit:
            for tx in order.financial_transactions.all():
                tx.delete()  # reverses the cash-drawer balance
        order_id = order.id
        order.delete()
        messages.success(request, f'تم حذف أمر الشغل #{order_id} بنجاح.')

    return redirect('factory_list')

@login_required
def factory_invoice(request, pk):
    order = get_object_or_404(Order, pk=pk)
    sys_settings = SystemSetting.objects.first()
    return render(request, 'sales/factory_invoice.html', {'order': order, 'sys_settings': sys_settings})

# --- NEW: AJAX Endpoint to fetch Order Details for Refund ---
@login_required
def get_order_details_ajax(request):
    """
    Fetch order items to populate the Refund Form.
    """
    order_id = request.GET.get('id')
    if not order_id:
        return JsonResponse({'status': 'error', 'message': 'رقم الفاتورة مطلوب'})
    
    order = Order.objects.filter(id=order_id).first()
    if not order:
        return JsonResponse({'status': 'error', 'message': 'الفاتورة غير موجودة'})

    items = []
    for item in order.items.all():
        # Scope "already returned" to the exact (product, variant) pair — otherwise two
        # different sizes of the same product sold in one order would incorrectly share
        # a single returnable-quantity pool.
        already_returned = (
            ReturnItem.objects
            .filter(return_invoice__original_order=order, product=item.product, variant=item.variant)
            .aggregate(s=Sum('quantity'))['s'] or Decimal('0')
        )
        returnable = item.quantity - already_returned
        items.append({
            'product_id': item.product.id,
            'variant_id': item.variant_id,
            'variant_label': item.variant.label if item.variant else '',
            'name': item.product.name,
            'qty': item.quantity,
            'price': item.price, # Original selling price
            'already_returned': float(already_returned),
            'max_qty': float(returnable) if returnable > 0 else 0,  # real remaining, not original qty
        })
    
    customer_name = f"{order.customer.first_name} {order.customer.last_name}" if order.customer else "عميل نقدي"
    
    return JsonResponse({
        'status': 'success',
        'items': items,
        'customer_name': customer_name,
        'customer_id': order.customer.id if order.customer else None
    })

@login_required
@require_granular_action('sales', 'refunds', 'sales', 'refund')
def refund_view(request):
    """
    Robust Refund View:
    1. Handles JSON data from the modern frontend.
    2. Validates Invoice ID.
    3. Restocks Warehouse (Specific or Main).
    4. Records Financial Expense.
    """
    if request.method == 'POST':
        try:
            # 1. Parse Data
            # The frontend now sends a JSON string in 'refund_items'
            refund_items_json = request.POST.get('refund_items')
            invoice_id = request.POST.get('invoice_id')
            reason = request.POST.get('reason', '')
            customer_id = request.POST.get('customer')
            # Phase 1.3: how the refund is settled — cash from drawer, or credited to
            # the customer's account (reduces what they owe). Defaults to cash.
            refund_method = request.POST.get('refund_method', 'cash')
            if refund_method not in ('cash', 'customer_credit'):
                refund_method = 'cash'
            reason_category = request.POST.get('reason_category', '')  # Phase 6.4

            # --- WAREHOUSE SELECTION FOR REFUND ---
            warehouse_id = request.POST.get('warehouse_id')
            warehouse = None
            if warehouse_id:
                warehouse = Warehouse.objects.filter(id=warehouse_id, is_active=True).first()
            if not warehouse:
                warehouse = Warehouse.objects.filter(is_active=True, is_sales_point=True).first()
            # --------------------------------------

            if not refund_items_json:
                messages.error(request, "لم يتم اختيار منتجات للاسترجاع")
                return redirect('refund_view')
            
            refund_items = json.loads(refund_items_json)
            
            with transaction.atomic():
                original_order = None
                customer = None
                
                # Try to link to Original Order
                if invoice_id:
                    original_order = Order.objects.filter(id=invoice_id).first()
                    if original_order:
                        customer = original_order.customer
                
                # If no order customer, try manual customer selection
                if not customer and customer_id:
                    customer = Customer.objects.filter(id=customer_id).first()

                # A customer is required to settle a refund as account credit.
                if refund_method == 'customer_credit' and not customer:
                    raise Exception("لا يمكن إضافة المرتجع كرصيد بدون اختيار عميل مسجل.")

                # 3. Create Return Invoice Record
                from .models import DocumentSequence
                return_invoice = ReturnInvoice.objects.create(
                    original_order=original_order,
                    customer=customer,
                    user=request.user,
                    reason=reason,
                    reason_category=reason_category,
                    refund_method=refund_method,
                    return_number=DocumentSequence.next_number('RET'),  # Phase 6.4
                )

                total_refund = Decimal(0)

                # 4. Process Each Returned Item
                for item in refund_items:
                    product_id = item.get('product_id')
                    variant_id = item.get('variant_id')
                    qty = Decimal(str(item.get('quantity', 0)))
                    price = Decimal(str(item.get('price', 0))) # Refund price

                    if qty > 0:
                        product = Product.objects.get(id=product_id)
                        variant = None
                        if variant_id:
                            from products.models import ProductVariant
                            variant = ProductVariant.objects.filter(id=variant_id, product=product).first()

                        # --- Phase 1.5: SERVER-SIDE RETURN VALIDATION ---
                        # When linked to an original invoice, you cannot return more than
                        # was sold (minus what was already returned), nor refund above the
                        # original unit price. This closes the "return 1000 units" payout hole.
                        # Scoped to (product, variant) — two different sizes of the same
                        # product sold in one order must not share a returnable-qty pool.
                        if original_order:
                            sold_item = original_order.items.filter(product=product, variant=variant).first()
                            if not sold_item:
                                raise Exception(f"الصنف {product.name} غير موجود في الفاتورة الأصلية #{original_order.id}")
                            already_returned = (
                                ReturnItem.objects
                                .filter(return_invoice__original_order=original_order, product=product, variant=variant)
                                .aggregate(s=Sum('quantity'))['s'] or Decimal('0')
                            )
                            returnable = sold_item.quantity - already_returned
                            if qty > returnable:
                                raise Exception(
                                    f"كمية المرتجع للصنف {product.name} ({qty}) تتجاوز المتاح للإرجاع ({returnable})."
                                )
                            if price > sold_item.price:
                                raise Exception(
                                    f"سعر استرداد الصنف {product.name} ({price}) أكبر من سعر البيع الأصلي ({sold_item.price})."
                                )

                        if variant:
                            # Fashion: restore the SIZE's own stock counter, and mirror the
                            # product/warehouse ledger too (same dual-ledger update the sale
                            # side does), so Product.stock_quantity / dashboard revenue stay
                            # in sync instead of only ever moving on the sale side.
                            variant.stock_quantity = (variant.stock_quantity or Decimal('0')) + qty
                            variant.save(update_fields=['stock_quantity'])
                            if warehouse:
                                from products.inventory_services import restore_stock
                                restore_stock(
                                    product, warehouse, qty,
                                    user=request.user,
                                    transaction_type='RET_IN',
                                    reference=str(original_order.id) if original_order else f"RET-{return_invoice.id}",
                                    note=f"مرتجع فاتورة #{original_order.id if original_order else 'عام'} (مقاس: {variant.label}) - مرتجع رقم {return_invoice.id}",
                                )
                        elif warehouse:
                            # A+B. Restock through the inventory service (batches + WarehouseStock
                            # + Product cache in one atomic call) and log the RET_IN movement.
                            from products.inventory_services import restore_stock
                            restore_stock(
                                product, warehouse, qty,
                                user=request.user,
                                transaction_type='RET_IN',
                                reference=str(original_order.id) if original_order else f"RET-{return_invoice.id}",
                                note=f"مرتجع فاتورة #{original_order.id if original_order else 'عام'} - مرتجع رقم {return_invoice.id} (إلى {warehouse.name})",
                            )

                        # C. Save Return Item Details
                        ReturnItem.objects.create(
                            return_invoice=return_invoice,
                            product=product,
                            variant=variant,
                            quantity=qty,
                            refund_price=price
                        )

                        total_refund += qty * price

                # total_refund so far only sums qty*OrderItem.price — the customer must
                # also get back their proportional share of the order-level extras
                # (service charge, VAT) that price never included, or a returned item on
                # a dine-in check silently shorts them:
                #   - Service charge (always an on-top add-on, in EITHER VAT mode — see
                #     _recompute_order_totals): refunded proportionally to how much of the
                #     order's item subtotal is being returned (frac).
                #   - VAT depends on the store's pricing mode at the time of the original
                #     sale:
                #     - Not included (added on top): OrderItem.price is the NET pre-VAT
                #       line price, so the customer is entitled to their proportional
                #       share of the VAT add-on back too (frac * order.vat_amount — NOT
                #       total_refund/vat_breakdown()['net'], which conflates the service
                #       charge into the ratio and silently shrinks the VAT refund whenever
                #       a service charge is present).
                #     - Included in price: OrderItem.price is already the GROSS,
                #       VAT-inclusive amount — total_refund (the item portion) is already
                #       the right amount to hand back for the goods; VAT is only EXTRACTED
                #       from it for the ledger split, never added again.
                if original_order and total_refund > 0:
                    base_subtotal = original_order.subtotal_amount or Decimal('0')
                    frac = min(total_refund / base_subtotal, Decimal('1')) if base_subtotal > 0 else Decimal('0')

                    service_refund = Decimal('0.00')
                    if original_order.service_charge:
                        service_refund = (original_order.service_charge * frac).quantize(Decimal('0.01'))

                    original_vat = original_order.vat_breakdown()
                    vat_refund = Decimal('0.00')
                    if original_vat and original_vat['rate'] > 0:
                        if original_vat['included']:
                            rate = original_vat['rate']
                            vat_refund = (
                                total_refund * rate / (Decimal('100') + rate)
                            ).quantize(Decimal('0.01'))
                        elif original_order.vat_amount:
                            vat_refund = (original_order.vat_amount * frac).quantize(Decimal('0.01'))
                            total_refund += vat_refund

                    return_invoice.vat_amount = vat_refund
                    total_refund += service_refund

                return_invoice.total_refund_amount = total_refund
                return_invoice.save()

                # 5. Settle the refund (Phase 1.3 — returns now affect the customer balance).
                # Goods value is ALWAYS credited to the customer (reduces what they owe).
                # For a CASH refund we additionally re-debit the customer and pay from the
                # drawer, so a fully-cash refund nets zero on their balance but moves cash;
                # a CREDIT refund simply lowers their debt with no cash movement.
                if total_refund > 0:
                    from crm.models import CustomerPayment
                    if customer:
                        CustomerPayment.objects.create(
                            customer=customer, user=request.user, amount=total_refund,
                            transaction_type='payment', payment_method='return_credit',
                            notes=f"رصيد مرتجع #{return_invoice.id}",
                        )
                        if refund_method == 'cash':
                            CustomerPayment.objects.create(
                                customer=customer, user=request.user, amount=total_refund,
                                transaction_type='debt', payment_method='return_cash_payout',
                                notes=f"صرف نقدي لمرتجع #{return_invoice.id}",
                            )

                    if refund_method == 'cash':
                        account = Account.objects.filter(account_type='CASH_DRAWER').first()
                        if account:
                            current_shift = get_active_shift()
                            Transaction.objects.create(
                                shift=current_shift,
                                account=account,
                                transaction_type='REFUND',
                                amount=total_refund,
                                description=f"مرتجع نقدي فاتورة #{return_invoice.id} (أصل: {invoice_id or 'بدون'})",
                                created_by=request.user,
                                return_invoice=return_invoice,  # Phase 1.2: FK link
                            )

                # Phase 4.2: post the return's double-entry (sales returns / cash-or-AR
                # + inventory / COGS for goods back).
                from financial.posting import post_refund
                post_refund(return_invoice)

            messages.success(request, f"تم تسجيل المرتجع بنجاح برقم #{return_invoice.id}")
            return redirect('refund_view')

        except Exception as e:
            messages.error(request, f"حدث خطأ أثناء حفظ المرتجع: {str(e)}")
            return redirect('refund_view')

    # GET Request
    # Fetch warehouses for the refund form dropdown
    warehouses = Warehouse.objects.filter(is_active=True, is_sales_point=True)
    
    context = {
        'customers': Customer.objects.all(),
        'products': Product.objects.filter(is_active=True),
        'sys_settings': SystemSetting.objects.first(),
        'title': 'تسجيل مرتجع',
        'warehouses': warehouses
    }
    return render(request, 'sales/refund_form.html', context)

@login_required
@require_granular_action('sales', 'expenses', 'financial', 'view')
def expense_list(request):
    expenses = Expense.objects.all().order_by('-date')
    if request.method == 'POST':
        form = ExpenseForm(request.POST)
        if form.is_valid():
            exp = form.save(commit=False)
            exp.user = request.user
            exp.save()
            
            # --- Financial Integration: Record Expense Transaction ---
            try:
                # Deduct from Cash Drawer (Or select account?) 
                # Ideally expense form should let you pick account, but for now default to CASH_DRAWER to fix "0 balance"
                account, _ = Account.objects.get_or_create(
                    account_type='CASH_DRAWER', 
                    defaults={'name': 'درج الكاشير', 'balance': 0}
                )
                
                current_shift = get_active_shift()  # FIX #4: global shift
                Transaction.objects.create(
                    shift=current_shift,
                    account=account,
                    transaction_type='EXPENSE',
                    amount=exp.amount,
                    description=f"مصروف: {exp.description or exp.category}",
                    created_by=request.user
                )
            except Exception as e:
                print(f"Error creating financial transaction for expense: {e}")

            return redirect('expense_list')
    else:
        form = ExpenseForm()
    
    return render(request, 'sales/expense_list.html', {'expenses': expenses, 'form': form})

@login_required
def expense_invoice(request, pk):
    expense = get_object_or_404(Expense, pk=pk)
    sys_settings = SystemSetting.objects.first()
    return render(request, 'sales/expense_invoice.html', {'expense': expense, 'sys_settings': sys_settings})

# --- Helper for Financials ---
def calculate_system_cash():
    """
    Returns the current balance of the CASH_DRAWER account.
    """
    account = Account.objects.filter(account_type='CASH_DRAWER').first()
    return account.balance if account else Decimal('0.00')

@login_required
@require_permission('financial', 'view')
def financial_statement(request):
    date_from = request.GET.get('date_from')
    date_to = request.GET.get('date_to')
    
    orders = Order.objects.active()  # exclude voided invoices from the P&L
    expenses = Expense.objects.all()
    returns = ReturnInvoice.objects.all()
    other_incomes = OtherIncome.objects.all()
    settlements = CashSettlement.objects.all().order_by('-date')

    if date_from:
        orders = orders.filter(created_at__date__gte=date_from)
        expenses = expenses.filter(date__gte=date_from)
        returns = returns.filter(created_at__date__gte=date_from)
        other_incomes = other_incomes.filter(date__gte=date_from)
    
    if date_to:
        orders = orders.filter(created_at__date__lte=date_to)
        expenses = expenses.filter(date__lte=date_to)
        returns = returns.filter(created_at__date__lte=date_to)
        other_incomes = other_incomes.filter(date__lte=date_to)
        
    # Summaries
    total_income_sales = orders.aggregate(Sum('total_amount'))['total_amount__sum'] or 0
    total_other_income = other_incomes.aggregate(Sum('amount'))['amount__sum'] or 0

    total_expenses = expenses.aggregate(Sum('amount'))['amount__sum'] or 0
    total_returns = returns.aggregate(Sum('total_refund_amount'))['total_refund_amount__sum'] or 0

    # Phase 1.9: real profit must subtract Cost of Goods Sold. COGS is captured per
    # line at sale time (OrderItem.cost_price, per box unit); OrderItem.cogs converts
    # strip sales back to box units so strips aren't overstated (Phase 4.2 fix).
    from .models import OrderItem
    total_cogs = sum(
        (it.cogs for it in OrderItem.objects.filter(order__in=orders).select_related('product')),
        Decimal('0.00'),
    )

    gross_profit = Decimal(str(total_income_sales)) - Decimal(str(total_cogs))
    # Net profit = gross profit + other income - operating expenses - customer refunds
    net_profit = gross_profit + Decimal(str(total_other_income)) - Decimal(str(total_expenses)) - Decimal(str(total_returns))
    
    # Current Drawer Status
    current_drawer_balance = calculate_system_cash()

    # Handle Forms
    if request.method == 'POST':
        if 'add_income' in request.POST:
            inc_form = OtherIncomeForm(request.POST)
            if inc_form.is_valid():
                inc = inc_form.save(commit=False)
                inc.user = request.user
                inc.save()
                
                # --- Financial Integration: Record Other Income ---
                try:
                    account, _ = Account.objects.get_or_create(
                        account_type='CASH_DRAWER', 
                        defaults={'name': 'درج الكاشير', 'balance': 0}
                    )
                    
                    current_shift = get_active_shift()  # FIX #4: global shift
                    Transaction.objects.create(
                        shift=current_shift,
                        account=account,
                        transaction_type='INCOME',
                        amount=inc.amount,
                        description=f"إيراد آخر: {inc.title}", # Corrected from .source to .title based on likely model field, or generic fallback
                        created_by=request.user
                    )
                except Exception as e:
                    print(f"Error creating financial transaction for income: {e}")

                return redirect('financial_statement')
        
        elif 'settle_cash' in request.POST:
            set_form = CashSettlementForm(request.POST)
            if set_form.is_valid():
                settle = set_form.save(commit=False)
                settle.user = request.user
                settle.expected_cash = current_drawer_balance
                settle.difference = settle.actual_cash - settle.expected_cash
                settle.save()
                return redirect('financial_statement')

    else:
        inc_form = OtherIncomeForm()
        set_form = CashSettlementForm()

    context = {
        'total_income_sales': total_income_sales,
        'total_other_income': total_other_income,
        'total_expenses': total_expenses,
        'total_returns': total_returns,
        'total_cogs': total_cogs,
        'gross_profit': gross_profit,
        'net_profit': net_profit,
        'current_drawer_balance': current_drawer_balance,
        
        'other_incomes': other_incomes,
        'settlements': settlements,
        
        'inc_form': inc_form,
        'set_form': set_form,
        
        'date_from': date_from,
        'date_to': date_to
    }
    return render(request, 'sales/financial_statement.html', context)

# --- NEW DIRECT PRINT VIEW ---
@login_required
def print_receipt_backend(request, pk):
    """
    Generates the invoice HTML internally and sends it to the server's connected printer.
    Now correctly passes base_url to fix CSS/Image loading issues.
    """
    try:
        order = get_object_or_404(Order, pk=pk)
        sys_settings = SystemSetting.objects.first()
        
        if not sys_settings or not sys_settings.printer_name:
            return JsonResponse({'status': 'error', 'message': 'لم يتم تحديد طابعة في الإعدادات'})

        # Render the invoice template to a string
        # Ensure 'print_mode' is True to hide buttons or adjust styles for the physical receipt
        html_content = render_to_string('sales/invoice.html', {
            'order': order,
            'sys_settings': sys_settings,
            'print_mode': True 
        }, request=request)

        # CRITICAL: This gets the full root URL of your site (e.g., http://127.0.0.1:8000/)
        # This allows WeasyPrint to resolve /static/css/style.css to a full absolute path.
        base_url = request.build_absolute_uri('/')

        # Send to printer
        success, message = print_html_to_backend(html_content, sys_settings.printer_name, base_url=base_url)

        if success:
            return JsonResponse({'status': 'success', 'message': message})
        else:
            return JsonResponse({'status': 'error', 'message': message})

    except Exception as e:
        return JsonResponse({'status': 'error', 'message': str(e)})

# --- NEW: PDF DOWNLOAD VIEW (Using WeasyPrint) ---
@login_required
def download_invoice_pdf(request, pk):
    """
    Generates a PDF download for the invoice using WeasyPrint.
    Usage: /sales/invoice/pdf/<pk>/?size=a4 (or a5)
    """
    order = get_object_or_404(Order, pk=pk)
    sys_settings = SystemSetting.objects.first()
    size = request.GET.get('size', 'a4') # Default to A4

    # Convert numbers to Arabic words in Python
    arabic_total_text = tafqeet_ar(order.total_amount)

    context = {
        'order': order,
        'sys_settings': sys_settings,
        'print_style': size, # 'a4' or 'a5'
        'arabic_total_text': arabic_total_text,
        'is_pdf': True, # Flag to trigger PDF-specific CSS
    }

    if not HTML:
        # Fallback to HTML print preview if WeasyPrint is missing dependencies (like GTK on Windows)
        context['is_print_preview'] = True
        html_string = render_to_string('sales/invoice_a4.html', context, request=request)
        return HttpResponse(html_string)

    # Render HTML content using the A4/A5 template
    html_string = render_to_string('sales/invoice_a4.html', context, request=request)
    
    try:
        # Create PDF
        # base_url is needed for WeasyPrint to find images and fonts (uses request to get domain)
        html = HTML(string=html_string, base_url=request.build_absolute_uri('/'))
        
        # Generate PDF bytes
        pdf_file = html.write_pdf()
        
        # Return as download
        response = HttpResponse(pdf_file, content_type='application/pdf')
        filename = f"Invoice_{order.id}_{size.upper()}.pdf"
        response['Content-Disposition'] = f'inline; filename="{filename}"' # 'inline' opens in browser, 'attachment' downloads
        return response
    except Exception:
        # Final fallback if something goes wrong during PDF generation
        context['is_print_preview'] = True
        html_string = render_to_string('sales/invoice_a4.html', context, request=request)
        return HttpResponse(html_string)
@login_required
@require_permission('sales', 'delete')
def delete_order_ajax(request):
    """
    API View to handle order deletion.
    Reverses stock, financials, and logs the action.
    """
    if request.method == 'POST':
        try:
            data = json.loads(request.body)
            order_id = data.get('order_id')
            reason = data.get('reason')
            
            if not reason:
                return JsonResponse({'status': 'error', 'message': 'يجب ذكر سبب الحذف'})
            
            order = get_object_or_404(Order, id=order_id)

            if order.is_void:
                return JsonResponse({'status': 'error', 'message': 'الفاتورة ملغاة بالفعل'})

            # Phase 4.5: block voiding documents inside a closed accounting period.
            from financial.models import PeriodLock
            if PeriodLock.is_locked(order.created_at) and not has_permission(request.user, 'financial', 'manage'):
                return JsonResponse({'status': 'error', 'message': 'الفترة المحاسبية لهذه الفاتورة مغلقة، لا يمكن الإلغاء'})

            with transaction.atomic():
                # 1. Identify Warehouse for Stock Reversal
                warehouse = order.warehouse
                if not warehouse:
                     from products.models import StockTransaction
                     st = StockTransaction.objects.filter(note__icontains=f"#{order.id}").first()
                     if st:
                         warehouse = st.warehouse
                
                if not warehouse:
                    from products.models import Warehouse
                    warehouse = Warehouse.objects.filter(is_active=True, is_sales_point=True).first()
                
                # 2. Reverse Stock through the inventory service (batches + WarehouseStock
                #    + Product cache + RET_IN log in one atomic call). The original OUT
                #    transactions are kept as historical record (no hard delete of ledger).
                from products.inventory_services import restore_stock
                for item in order.items.all():
                    # Calculate correct box quantity for restoration
                    restoration_qty = item.quantity
                    if item.sell_unit == 'strip' and item.product.strips_per_box > 1:
                        restoration_qty = item.quantity / Decimal(str(item.product.strips_per_box))

                    restore_stock(
                        item.product, warehouse, restoration_qty,
                        user=request.user,
                        transaction_type='RET_IN',
                        cost_price=item.cost_price,
                        reference=str(order.id),
                        note=f"إلغاء فاتورة مبيعات {item.sell_unit} #{order.id} - السبب: {reason}",
                    )
                
                # 3. Reverse Financials
                from .utils import reverse_order_financials
                reverse_order_financials(order, request.user, reason)

                # Phase 4.2: remove the invoice's journal entry (the reversal above
                # already records the cash movement back out).
                from financial.posting import unpost
                unpost(f"SALE-{order.id}")
                
                # 4. Create Audit Log
                try:
                    from accounts.models import UserActivityLog
                    UserActivityLog.objects.create(
                        user=request.user,
                        action_type='DELETE',
                        module='sales',
                        description=f"حذف فاتورة مبيعات #{order.id} - السبب: {reason}",
                        before_data={
                            'order_id': order.id,
                            'total': float(order.total_amount),
                            'customer': str(order.customer),
                            'items': [{'p': i.product.name, 'q': float(i.quantity)} for i in order.items.all()]
                        }
                    )
                except Exception:
                    pass # Audit log failure shouldn't block deletion
                
                # 5. Notify Superusers
                try:
                    from django.contrib.auth.models import User
                    from notifications.models import Notification
                    superusers = User.objects.filter(is_superuser=True)
                    for su in superusers:
                        Notification.objects.create(
                            recipient=su,
                            title="حذف فاتورة مبيعات",
                            message=f"قام {request.user.first_name} بحذف الفاتورة #{order.id}. السبب: {reason}",
                            created_by=request.user
                        )
                except Exception:
                    pass
                
                # 5b. Send Email Alert
                try:
                    from .email_utils import send_alert_email
                    from django.template.loader import render_to_string
                    from django.utils.timezone import localtime
                    html_body = render_to_string('emails/order_edited.html', {
                        'action': 'DELETE',
                        'order_id': order.id,
                        'customer_name': order.customer.name if order.customer else 'عميل نقدي',
                        'user_name': request.user.get_full_name() or request.user.username,
                        'timestamp': localtime().strftime('%Y-%m-%d %H:%M:%S'),
                        'reason': reason,
                        'total': float(order.total_amount),
                        'items': [{'p': i.product.name, 'q': float(i.quantity)} for i in order.items.all()]
                    })
                    send_alert_email(f"تنبيه حذف فاتورة #{order.id}", html_body)
                except Exception:
                    pass
                
                
                # 6. VOID the order (Phase 1.10) — preserve the invoice, its items and
                #    the full stock/financial reversal trail instead of hard-deleting.
                order.status = Order.STATUS_VOID
                order.voided_at = timezone.now()
                order.voided_by = request.user
                order.void_reason = reason
                order.is_completed = True
                order.save(update_fields=['status', 'voided_at', 'voided_by', 'void_reason', 'is_completed', 'updated_at'])

            return JsonResponse({'status': 'success', 'message': 'تم إلغاء الفاتورة (Void) وعكس الحركات المالية والكميات مع حفظ السجل'})
        except Exception as e:
            return JsonResponse({'status': 'error', 'message': str(e)})
    
    return JsonResponse({'status': 'error', 'message': 'Invalid Request Method'})

@login_required
@require_permission('sales', 'edit')
def edit_order_ajax(request):
    if request.method != 'POST':
        return JsonResponse({'status': 'error', 'message': 'Invalid Method'}, status=400)
    
    try:
        with transaction.atomic():
            data = json.loads(request.body)
            order_id = data.get('order_id')
            if not order_id:
                 return JsonResponse({'status': 'error', 'message': 'Order ID missing'}, status=400)
             
            order = Order.objects.select_for_update().get(id=order_id)

            if order.is_void:
                return JsonResponse({'status': 'error', 'message': 'لا يمكن تعديل فاتورة ملغاة'}, status=400)

            # Phase 4.5: block editing documents inside a closed accounting period.
            from financial.models import PeriodLock
            if PeriodLock.is_locked(order.created_at) and not has_permission(request.user, 'financial', 'manage'):
                return JsonResponse({'status': 'error', 'message': 'الفترة المحاسبية لهذه الفاتورة مغلقة، لا يمكن التعديل'}, status=403)

            # Any order with a delivery cost is a shipping order too, even if the cashier forgot
            # to flip the "طلب أونلاين" toggle — both need a named customer with a real phone +
            # address. Validated here, BEFORE the stock-reversal/item-deletion steps below run,
            # since this whole view is one @transaction.atomic block but its own try/except
            # catches exceptions internally — an error raised after those destructive steps
            # would still silently commit them.
            _edit_customer_id = data.get('customer_id')
            _edit_delivery_cost = Decimal(str(data.get('delivery_cost', 0) or 0))
            requires_shipping = bool(data.get('is_online', False)) or _edit_delivery_cost > 0
            if requires_shipping:
                _edit_customer = Customer.objects.filter(id=_edit_customer_id).first() if _edit_customer_id else None
                if not _edit_customer:
                    return JsonResponse({'status': 'error', 'message': 'يجب اختيار عميل مسجل لطلبات الأونلاين/الشحن.'})
                if not (_edit_customer.phone or '').strip():
                    return JsonResponse({'status': 'error', 'message': 'رقم هاتف العميل مطلوب لطلبات الأونلاين/الشحن.'})
                if not (_edit_customer.address or '').strip():
                    return JsonResponse({'status': 'error', 'message': 'عنوان العميل مطلوب لطلبات الأونلاين/الشحن.'})

            # Phase 1.10: snapshot the invoice BEFORE editing so history is never lost.
            from .models import OrderRevision
            OrderRevision.objects.create(
                order=order,
                revision_no=order.revision_no + 1,
                edited_by=request.user,
                reason=data.get('edit_reason', ''),
                before_data={
                    'total': float(order.total_amount),
                    'discount': float(order.discount),
                    'delivery_cost': float(order.delivery_cost),
                    'customer_id': order.customer_id,
                    'warehouse_id': order.warehouse_id,
                    'cash_paid': float(order.cash_paid),
                    'wallet_paid': float(order.wallet_paid),
                    'instapay_paid': float(order.instapay_paid),
                    'visa_paid': float(order.visa_paid),
                    'credit_paid': float(order.credit_paid),
                    'items': [
                        {'product_id': i.product_id, 'name': (i.product.name if i.product else ''),
                         'qty': float(i.quantity), 'price': float(i.price),
                         'sell_unit': i.sell_unit, 'cost_price': float(i.cost_price)}
                        for i in order.items.all()
                    ],
                },
            )
            order.revision_no = order.revision_no + 1

            # 1. Financial Reversal — Phase 1.2: select by the FK relation, NOT by matching
            #    the order id inside the description (which matched #12 against #120, #1200…
            #    and silently corrupted unrelated orders' books).
            from .utils import record_sale_transaction
            from financial.models import Transaction

            old_trans = Transaction.objects.filter(order=order, transaction_type='SALE')
            for t in old_trans:
                # Transaction.delete() already reverses the balance it applied on save()
                # (see financial/models.py) — doing it again here double-deducted every
                # edited order's cash/bank/wallet balance by the old sale amount.
                t.delete()
        
            # 2. Stock Restoration through the inventory service (batches + WarehouseStock
            #    + Product cache, all in sync). Logs RET_IN reversal movements; the original
            #    OUT rows are removed so the re-issue below does not duplicate them.
            from products.inventory_services import restore_stock
            old_warehouse = order.warehouse or Warehouse.objects.filter(is_active=True, is_sales_point=True).first()
            for item in order.items.all():
                restoration_qty = item.quantity
                if item.sell_unit == 'strip' and item.product.strips_per_box > 1:
                    restoration_qty = item.quantity / Decimal(str(item.product.strips_per_box))

                restore_stock(
                    item.product, old_warehouse, restoration_qty,
                    user=request.user,
                    transaction_type='RET_IN',
                    cost_price=item.cost_price,
                    reference=str(order.id),
                    note=f"عكس مخزون قبل تعديل الفاتورة #{order.id}",
                )

            # Remove prior OUT movements for this order to avoid duplication on re-issue.
            StockTransaction.objects.filter(
                transaction_type='OUT',
                reference_number=str(order.id)
            ).delete()
            # 3. Clear Old Items
            order.items.all().delete()
        
            # 4. Update Order Metadata
            customer_id = data.get('customer_id')
            if customer_id:
                order.customer = Customer.objects.get(id=customer_id)
            else:
                order.customer = None
            
            order.discount = Decimal(str(data.get('discount', 0)))
            order.discount_type = data.get('discount_type', 'fixed')
            order.delivery_cost = Decimal(str(data.get('delivery_cost', 0)))
            order.notes = data.get('notes', '')
            # (Also fixes a bug: this used to set the nonexistent `order.is_online` attribute
            # instead of the real field `is_online_order`, so it was never persisted.)
            order.is_online_order = requires_shipping
            applied_deal_id = data.get('applied_deal_id')
            applied_deal = None
            if applied_deal_id:
                from financial.payroll_models import DealDiscount
                applied_deal = DealDiscount.objects.filter(id=applied_deal_id, is_active=True).first()
        
            # Update Payment Fields
            order.cash_paid = Decimal(str(data.get('cash_paid', 0)))
            order.wallet_paid = Decimal(str(data.get('wallet_paid', 0)))
            order.instapay_paid = Decimal(str(data.get('instapay_paid', 0)))
            order.visa_paid = Decimal(str(data.get('visa_paid', 0)))
            order.credit_paid = Decimal(str(data.get('credit_paid', 0)))

            # FIX #2: Derive and set payment_method based on new split amounts.
            # This ensures record_sale_transaction's fallback path never fires with
            # a stale payment_method value.
            if order.wallet_paid == 0 and order.instapay_paid == 0 and order.visa_paid == 0 and order.credit_paid == 0 and order.cash_paid > 0:
                order.payment_method = 'cash'
            elif order.cash_paid == 0 and order.instapay_paid == 0 and order.visa_paid == 0 and order.credit_paid == 0 and order.wallet_paid > 0:
                order.payment_method = 'wallet'
            elif order.cash_paid == 0 and order.wallet_paid == 0 and order.visa_paid == 0 and order.credit_paid == 0 and order.instapay_paid > 0:
                order.payment_method = 'instapay'
            elif order.cash_paid == 0 and order.wallet_paid == 0 and order.instapay_paid == 0 and order.credit_paid == 0 and order.visa_paid > 0:
                order.payment_method = 'visa'
            else:
                order.payment_method = 'custom'
        
            new_wh_id = data.get('warehouse_id')
            if new_wh_id:
                order.warehouse = Warehouse.objects.get(id=new_wh_id)
        
            # 5. Process New Items & Deduct Stock — shared OrderService (Phase 2.2)
            new_items = data.get('items', [])
            new_wh = order.warehouse or old_warehouse

            # Resolved BEFORE compute_discount_and_total (not after, like the old code did)
            # so the dine-in service charge is computed against the order type this save
            # is actually landing on, not the stale one from before the edit.
            _edit_driver_id = data.get('driver_id') or None
            _edit_driver = None
            if _edit_driver_id:
                from restaurant.models import Driver
                _edit_driver = Driver.objects.filter(id=_edit_driver_id, branch=new_wh, is_active=True).first()
            if _edit_driver or requires_shipping:
                new_order_type = Order.ORDER_TYPE_DELIVERY
            elif data.get('order_type') in (Order.ORDER_TYPE_DINE_IN, Order.ORDER_TYPE_TAKEAWAY):
                new_order_type = data.get('order_type')
            else:
                new_order_type = order.order_type

            # Layer 2: same 'sales.allow_negative_stock' / per-user override as submit_order_ajax.
            from settings.policies import get_policy as _policy
            profile = getattr(request.user, 'profile', None)
            _neg_ok = _policy('sales.allow_negative_stock') or (profile and profile.allows_below_zero_stock())

            # Pre-check availability (friendly early error before issuing anything).
            for it in new_items:
                product = Product.objects.get(id=it['id'])
                qty = Decimal(str(it['quantity']))
                sell_unit = it.get('sell_unit', 'box')
                strips_per_box = it.get('strips_per_box') or product.strips_per_box or 1
                deduction_qty = qty
                if sell_unit == 'strip' and int(strips_per_box) > 1:
                    deduction_qty = qty / Decimal(str(strips_per_box))
                ws, _ = WarehouseStock.objects.get_or_create(product=product, warehouse=new_wh)
                if ws.quantity < deduction_qty and not _neg_ok:
                    raise Exception(f"الكمية غير متوفرة في {new_wh.name} للصنف: {product.name} (المتاح: {ws.quantity})")

            from .services import issue_cart_items, compute_discount_and_total, compute_dine_in_service_charge
            total_amount, qualified_subtotal = issue_cart_items(
                order, new_items, new_wh, request.user, applied_deal,
                note_prefix="تخصيم تعديل فاتورة",
                allow_negative_stock=_neg_ok,
            )
            order.subtotal_amount = total_amount

            result = compute_discount_and_total(
                total_amount, qualified_subtotal,
                discount=order.discount, discount_type=order.discount_type,
                applied_deal=applied_deal, delivery_cost=order.delivery_cost,
                tailoring_cost=order.tailoring_cost,
                service_charge=compute_dine_in_service_charge(total_amount, new_order_type),
                vat_rate=order.vat_rate_snapshot, vat_included=order.vat_included_snapshot,
                cart_items=new_items,
            )
            order.discount = result['discount']
            order.discount_type = result['discount_type']
            order.applied_deal = result['applied_deal']
            order.total_amount = result['total']
            order.service_charge = result['service_charge']
            order.vat_amount = result['vat_amount']

            # received_amount should only be NEW money
            order.received_amount = order.cash_paid + order.wallet_paid + order.instapay_paid + order.visa_paid
        
            # Completion Status
            if order.remaining_amount <= 0:
                order.is_completed = True
            else:
                if not order.customer:
                     raise Exception("لا يمكن تسجيل مديونية لعميل نقدي عام. يرجى اختيار عميل مسجل.")
                order.is_completed = False

            # A driver picked in the POS edit screen — same treatment as submit_order_ajax:
            # only accept one that actually belongs to the order's (possibly just-changed)
            # warehouse. Both already resolved above, before the service charge calc.
            order.driver = _edit_driver
            order.order_type = new_order_type

            order.save()

            if order.driver_id:
                from restaurant.consumers import push_event
                push_event('delivery', order.warehouse_id, {
                    'event': 'order_assigned', 'order_id': order.id, 'driver_id': order.driver_id,
                })

            # If it's an online/shipping order, ensure a Shipment record exists so it shows up
            # on لوحة متابعة الشحن والأونلاين (mirrors submit_order_ajax's create-path behavior).
            if requires_shipping:
                Shipment.objects.get_or_create(
                    order=order,
                    defaults={
                        'status': 'new',
                        'delivery_type': 'shipping',
                        'shipping_address': order.customer.address if order.customer and order.customer.address else (order.shipping_address or 'عنوان غير مسجل')
                    }
                )

            linked_draft_id = data.get('linked_draft_id')
            if linked_draft_id:
                Draft.objects.filter(
                    id=linked_draft_id,
                    user=request.user,
                    status=Draft.STATUS_OPEN
                ).update(status=Draft.STATUS_CLOSED)
        
            # 6. Record New Financials
            record_sale_transaction(order, request.user)

            # Same as submit_order_ajax: an edit that adds/keeps a cafe menu item must
            # still reach the kitchen even though it went through the edit-invoice path.
            from restaurant.services import notify_kitchen_for_order
            notify_kitchen_for_order(request, order)

            # 7. Log Activity
            try:
                from accounts.models import UserActivityLog
                UserActivityLog.objects.create(
                    user=request.user,
                    action_type='UPDATE',
                    module='sales',
                    description=f"تعديل الفاتورة #{order.id} - الإجمالي الجديد: {total_amount}",
                    after_data={
                        'order_id': order.id,
                        'total': float(total_amount),
                        'items': [{'p': it['name'], 'q': float(it['quantity'])} for it in new_items]
                    }
                )
            except Exception:
                pass
        
            # 8. Notify Superusers
            try:
                from django.contrib.auth.models import User
                from notifications.models import Notification
                superusers = User.objects.filter(is_superuser=True)
                for su in superusers:
                    Notification.objects.create(
                        recipient=su,
                        title="تعديل فاتورة مبيعات",
                        message=f"قام {request.user.first_name} بتعديل الفاتورة #{order.id}. الإجمالي الجديد: {total_amount}",
                        created_by=request.user
                    )
            except Exception:
                pass

            # 8b. Send Email Alert
            try:
                from .email_utils import send_alert_email
                from django.template.loader import render_to_string
                from django.utils.timezone import localtime
                html_body = render_to_string('emails/order_edited.html', {
                    'action': 'EDIT',
                    'order_id': order.id,
                    'customer_name': order.customer.name if order.customer else 'عميل نقدي',
                    'user_name': request.user.get_full_name() or request.user.username,
                    'timestamp': localtime().strftime('%Y-%m-%d %H:%M:%S'),
                    'total': float(total_amount),
                    'items': [{'p': it['name'], 'q': float(it['quantity'])} for it in new_items]
                })
                send_alert_email(f"تنبيه تعديل فاتورة #{order.id}", html_body)
            except Exception:
                pass
        
            # 9. Get updated balance
            updated_balance = 0
            if order.customer:
                order.customer.refresh_from_db()
                updated_balance = float(order.customer.get_balance())
        
            return JsonResponse({
                'status': 'success', 
                'message': 'تم تحديث الفاتورة بنجاح', 
                'order_id': order.id,
                'updated_balance': updated_balance
            })
        
    except Order.DoesNotExist:
        return JsonResponse({'status': 'error', 'message': 'الفاتورة غير موجودة'}, status=404)
    except Exception as e:
        return JsonResponse({'status': 'error', 'message': f"خطأ: {str(e)}"}, status=500)


@login_required
@require_granular_action('sales', 'voided', 'financial', 'view')
def voided_orders_register(request):
    """Audit register of all voided invoices + edited-invoice count (Phase 9.5)."""
    from django.db.models import Sum as _Sum
    qs = Order.objects.filter(status=Order.STATUS_VOID).select_related('customer', 'voided_by').order_by('-voided_at')

    q = request.GET.get('q', '').strip()
    if q:
        qs = qs.filter(
            Q(invoice_number__icontains=q) | Q(void_reason__icontains=q) |
            Q(customer__first_name__icontains=q) | Q(customer__phone__icontains=q)
        )
    df = request.GET.get('date_from')
    dt = request.GET.get('date_to')
    if df:
        qs = qs.filter(voided_at__date__gte=df)
    if dt:
        qs = qs.filter(voided_at__date__lte=dt)

    total_voided = qs.aggregate(s=_Sum('total_amount'))['s'] or 0
    edited_count = Order.objects.filter(revision_no__gt=0).count()

    return render(request, 'sales/voided_register.html', {
        'title': 'سجل الفواتير الملغاة',
        'orders': qs,
        'total_voided': total_voided,
        'voided_count': qs.count(),
        'edited_count': edited_count,
        'q': q, 'date_from': df or '', 'date_to': dt or '',
    })


@login_required
@require_granular_action('sales', 'returns_register', 'sales', 'refund')
def returns_register(request):
    """Browsable register of all sales returns (Phase 6.4)."""
    from django.db.models import Sum as _Sum
    qs = ReturnInvoice.objects.select_related('customer', 'user', 'original_order').order_by('-created_at')

    q = request.GET.get('q', '').strip()
    if q:
        qs = qs.filter(
            Q(return_number__icontains=q) | Q(reason__icontains=q) |
            Q(customer__first_name__icontains=q) | Q(customer__phone__icontains=q)
        )
    cat = request.GET.get('cat', '')
    if cat:
        qs = qs.filter(reason_category=cat)
    df = request.GET.get('date_from')
    dt = request.GET.get('date_to')
    if df:
        qs = qs.filter(created_at__date__gte=df)
    if dt:
        qs = qs.filter(created_at__date__lte=dt)

    total = qs.aggregate(s=_Sum('total_refund_amount'))['s'] or 0
    return render(request, 'sales/returns_register.html', {
        'title': 'سجل المرتجعات',
        'returns': qs,
        'total': total,
        'count': qs.count(),
        'reason_choices': ReturnInvoice.REASON_CHOICES,
        'q': q, 'cat': cat, 'date_from': df or '', 'date_to': dt or '',
    })


@login_required
@require_granular_action('sales', 'oversold', 'sales', 'view')
def oversold_register(request):
    """Browsable register of order lines sold beyond real available stock (Layer 2
    'sales.allow_negative_stock'). The stock ledger never shows this on its own —
    batches floor at 0 instead of going negative — so this is the one screen that
    surfaces it after the fact."""
    from django.db.models import Sum as _Sum

    qs = (OrderItem.objects
          .filter(shortfall_qty__gt=0)
          .exclude(order__status=Order.STATUS_VOID)
          .select_related('order', 'order__customer', 'order__user', 'product')
          .order_by('-order__created_at'))

    q = request.GET.get('q', '').strip()
    if q:
        qs = qs.filter(
            Q(product__name__icontains=q) | Q(order__invoice_number__icontains=q) |
            Q(order__customer__first_name__icontains=q) | Q(order__customer__phone__icontains=q)
        )
    df = request.GET.get('date_from')
    dt = request.GET.get('date_to')
    if df:
        qs = qs.filter(order__created_at__date__gte=df)
    if dt:
        qs = qs.filter(order__created_at__date__lte=dt)

    total_shortfall = qs.aggregate(s=_Sum('shortfall_qty'))['s'] or 0
    return render(request, 'sales/oversold_register.html', {
        'title': 'سجل مبيعات تجاوزت المخزون المتاح',
        'items': qs,
        'total_shortfall': total_shortfall,
        'count': qs.count(),
        'q': q, 'date_from': df or '', 'date_to': dt or '',
    })


# ──────────────────────────────────────────────
#  QUOTATIONS (Phase 6.2) — standalone, no stock/financial impact
# ──────────────────────────────────────────────
@login_required
@require_granular_action('sales', 'quotations', 'pos', 'view')
def quotation_list(request):
    from .models import Quotation
    from crm.models import Customer
    quotes = Quotation.objects.select_related('customer', 'created_by').all()
    return render(request, 'sales/quotation_list.html', {
        'title': 'عروض الأسعار', 'quotes': quotes,
        'customers': Customer.objects.all(),
    })


@login_required
@require_permission('pos', 'create')
@require_POST
def quotation_create(request):
    from .models import Quotation, DocumentSequence
    from crm.models import Customer
    cust = None
    cid = request.POST.get('customer_id')
    if cid:
        cust = Customer.objects.filter(id=cid).first()
    q = Quotation.objects.create(
        number=DocumentSequence.next_number('QUO'),
        customer=cust,
        customer_name=request.POST.get('customer_name', ''),
        valid_until=_parse_date_or_none(request.POST.get('valid_until')),
        notes=request.POST.get('notes', ''),
        created_by=request.user,
    )
    messages.success(request, f"تم إنشاء عرض السعر {q.display_number}.")
    return redirect('quotation_detail', pk=q.id)


@login_required
@require_permission('pos', 'view')
def quotation_detail(request, pk):
    from .models import Quotation
    from products.models import Product
    q = get_object_or_404(Quotation.objects.select_related('customer'), pk=pk)
    return render(request, 'sales/quotation_detail.html', {
        'title': q.display_number, 'q': q,
        'products': Product.objects.filter(is_active=True).order_by('name'),
        'print_mode': request.GET.get('print') == '1',
    })


@login_required
@require_permission('pos', 'create')
@require_POST
def quotation_add_item(request, pk):
    from .models import Quotation, QuotationItem
    from products.models import Product
    q = get_object_or_404(Quotation, pk=pk)
    if q.status == Quotation.STATUS_CONVERTED:
        messages.error(request, "لا يمكن تعديل عرض تحوّل لفاتورة.")
        return redirect('quotation_detail', pk=pk)
    product = Product.objects.filter(id=request.POST.get('product_id')).first()
    try:
        qty = Decimal(str(request.POST.get('quantity', '1')))
        price = Decimal(str(request.POST.get('unit_price', '0')))
    except (InvalidOperation, TypeError):
        messages.error(request, "قيم غير صحيحة.")
        return redirect('quotation_detail', pk=pk)
    QuotationItem.objects.create(
        quotation=q, product=product,
        description=request.POST.get('description', '') or (product.name if product else ''),
        quantity=qty, unit_price=price,
    )
    return redirect('quotation_detail', pk=pk)


@login_required
@require_permission('pos', 'create')
@require_POST
def quotation_delete_item(request, pk):
    from .models import QuotationItem
    item = get_object_or_404(QuotationItem, pk=pk)
    qid = item.quotation_id
    item.delete()
    return redirect('quotation_detail', pk=qid)


@login_required
@require_permission('pos', 'create')
@require_POST
def quotation_set_status(request, pk):
    from .models import Quotation
    q = get_object_or_404(Quotation, pk=pk)
    new_status = request.POST.get('status')
    if new_status in dict(Quotation.STATUS_CHOICES) and new_status != Quotation.STATUS_CONVERTED:
        q.status = new_status
        q.save(update_fields=['status'])
        messages.success(request, "تم تحديث حالة العرض.")
    return redirect('quotation_detail', pk=pk)


def _parse_date_or_none(s):
    from datetime import datetime
    try:
        return datetime.strptime(s, '%Y-%m-%d').date()
    except (ValueError, TypeError):
        return None


@login_required
@require_permission('financial', 'view')
def order_einvoice_json(request, pk):
    """Download an ETA-style e-invoice JSON for an order (Phase 10.2)."""
    import json as _json
    from financial.einvoice import build_invoice_json
    order = get_object_or_404(Order, pk=pk)
    doc = build_invoice_json(order)
    resp = HttpResponse(_json.dumps(doc, ensure_ascii=False, indent=2),
                        content_type='application/json; charset=utf-8')
    resp['Content-Disposition'] = f'attachment; filename="einvoice_{order.id}.json"'
    return resp
