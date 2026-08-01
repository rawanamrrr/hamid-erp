from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import JsonResponse
from django.utils import timezone
from django.db.models import Sum, Q, Count, F, ExpressionWrapper, DecimalField, Value, Case, When
from django.db.models.functions import ExtractHour
from .models import DailyShift, Transaction, Account, ShiftEmailLog
from .forms import StartShiftForm, CloseShiftForm, TransactionForm, AccountForm
from decimal import Decimal
from sales.models import Order, OrderItem, ReturnInvoice
from products.models import Product
from django.core.paginator import Paginator
from django.template.loader import render_to_string
from django.core.mail import EmailMessage
from django.core.mail.backends.smtp import EmailBackend
from django.conf import settings
from settings.models import SystemSetting
from accounts.permissions import require_permission, require_profit_view, require_granular_action, cashier_can_open_shift, cashier_can_close_shift, has_permission  # RBAC (Phase 3.1) + profit gate (3.2)
from django.core.exceptions import PermissionDenied
import logging
import re
from datetime import timedelta

logger = logging.getLogger(__name__)

# See Account.exclude_dead_duplicates() — filters out the dead coded
# chart-of-accounts duplicates from account-selection querysets.
_exclude_dead_coded_accounts = Account.exclude_dead_duplicates


def _get_shift_orders(shift):
    linked_orders = shift.order_set.all().order_by('-created_at')
    if linked_orders.exists():
        return linked_orders

    # Fallback 1: derive order IDs from SALE transactions bound to this shift
    sale_tx = shift.transactions.filter(transaction_type='SALE').only('description')
    ids = []
    for t in sale_tx:
        m = re.search(r'#(\d+)', t.description or '')
        if m:
            ids.append(int(m.group(1)))
    if ids:
        q = Order.objects.filter(id__in=ids).order_by('-created_at')
        if q.exists():
            return q

    # Fallback 2: time range with a small grace window for edge timing issues
    end_t = shift.end_time if shift.end_time else timezone.now()
    start_t = shift.start_time - timedelta(minutes=5)
    return Order.objects.filter(created_at__range=(start_t, end_t)).select_related('customer').order_by('-created_at')


def send_shift_report_email(shift, base_url):
    try:
        settings_obj = SystemSetting.objects.first()
        if not settings_obj:
            ShiftEmailLog.objects.create(shift=shift, success=False, error_message='SystemSetting not found')
            return

        sender = (settings_obj.gmail_sender_email or '').strip()
        app_password = (settings_obj.gmail_app_password or '').strip()
        recipients = [e.strip() for e in (settings_obj.email_recipients or '').split(',') if e.strip()][:5]

        if not sender or not app_password or not recipients:
            ShiftEmailLog.objects.create(shift=shift, success=False, error_message='Missing sender/app password/recipients')
            return

        start_t = shift.start_time
        end_t = shift.end_time or timezone.now()
        orders = _get_shift_orders(shift).select_related('customer')
        order_ids = list(orders.values_list('id', flat=True))

        returns = ReturnInvoice.objects.filter(created_at__range=(start_t, end_t)).select_related('customer', 'original_order').order_by('-created_at')
        expenses_withdrawals = Transaction.objects.filter(
            shift=shift,
            transaction_type__in=['EXPENSE', 'WITHDRAWAL']
        ).order_by('-created_at')

        # Manual / other transactions (transfers, deposits, income, supplier payments)
        manual_transactions = Transaction.objects.filter(
            shift=shift,
            transaction_type__in=['TRANSFER', 'DEPOSIT', 'INCOME', 'SUPPLIER_PAYMENT']
        ).select_related('account', 'to_account', 'created_by').order_by('-created_at')

        totals = orders.aggregate(
            sales_count=Count('id'),
            sales_total=Sum('total_amount'),
            cash_total=Sum('cash_paid'),
            wallet_total=Sum('wallet_paid'),
            instapay_total=Sum('instapay_paid'),
            visa_total=Sum('visa_paid'),
        )
        sales_count = totals.get('sales_count') or 0
        other_total = (totals.get('wallet_total') or Decimal('0.00')) + (totals.get('instapay_total') or Decimal('0.00'))

        refund_cash_total = Transaction.objects.filter(
            shift=shift,
            transaction_type='REFUND'
        ).aggregate(v=Sum('amount'))['v'] or Decimal('0.00')

        # SECTION 6: TOP PRODUCTS
        item_revenue_expr = ExpressionWrapper(F('quantity') * F('price'), output_field=DecimalField(max_digits=14, decimal_places=2))
        top_products = (
            OrderItem.objects
            .filter(order_id__in=order_ids)
            .values('product__name', 'product__id')
            .annotate(total_qty=Sum('quantity'), total_revenue=Sum(item_revenue_expr))
            .order_by('-total_qty')[:10]
        ) if order_ids else []

        # SECTION 7: RUSH HOURS
        rush_hours = (
            orders
            .annotate(hour=ExtractHour('created_at'))
            .values('hour')
            .annotate(invoice_count=Count('id'), total_sales=Sum('total_amount'))
            .order_by('-invoice_count', 'hour')[:5]
        ) if sales_count > 0 else []
        rush_hours = list(rush_hours)
        max_rush_count = max([r.get('invoice_count') or 0 for r in rush_hours], default=0)
        for row in rush_hours:
            hour = int(row.get('hour') or 0)
            suffix = 'ص' if hour < 12 else 'م'
            hour12 = hour % 12
            if hour12 == 0:
                hour12 = 12
            row['hour_label'] = f"{hour12:02d}:00 {suffix}"
            count_val = row.get('invoice_count') or 0
            row['bar_width'] = int((count_val / max_rush_count) * 200) if max_rush_count > 0 else 0

        # SECTION 8: LOW / OUT OF STOCK
        try:
            Product._meta.get_field('min_stock_alert')
            low_stock = Product.objects.filter(
                stock_quantity__gt=0,
                stock_quantity__lte=F('min_stock_alert')
            ).order_by('stock_quantity')[:30]
        except Exception:
            if any(f.name == 'low_stock_threshold' for f in Product._meta.fields):
                low_stock = Product.objects.filter(
                    stock_quantity__gt=0,
                    stock_quantity__lte=F('low_stock_threshold')
                ).order_by('stock_quantity')[:30]
            else:
                low_threshold = getattr(settings, 'LOW_STOCK_THRESHOLD', 5)
                low_stock = Product.objects.filter(
                    stock_quantity__gt=0,
                    stock_quantity__lte=low_threshold
                ).order_by('stock_quantity')[:30]

        out_of_stock = Product.objects.filter(stock_quantity__lte=0).order_by('name')[:30]

        # SECTION 9: PROFIT ANALYSIS
        profit_analysis = []
        winners = []
        losers = []
        products_missing_cost = 0
        profit_summary = {
            'total_revenue': Decimal('0.00'),
            'total_cost': Decimal('0.00'),
            'total_profit': Decimal('0.00'),
            'margin_percent': Decimal('0.00'),
        }

        if sales_count > 0 and order_ids:
            base_items = OrderItem.objects.filter(order_id__in=order_ids)
            products_missing_cost = (
                base_items
                .filter(Q(product__cost_price__isnull=True) | Q(product__cost_price__lte=0))
                .values('product_id')
                .distinct()
                .count()
            )

            valid_items = base_items.filter(product__cost_price__gt=0)
            profit_qs = (
                valid_items
                .values('product__name', 'product__id')
                .annotate(
                    total_qty=Sum('quantity'),
                    total_revenue=Sum(item_revenue_expr),
                    total_cost=Sum(ExpressionWrapper(F('quantity') * F('product__cost_price'), output_field=DecimalField(max_digits=14, decimal_places=2))),
                )
                .annotate(
                    total_profit=ExpressionWrapper(F('total_revenue') - F('total_cost'), output_field=DecimalField(max_digits=14, decimal_places=2)),
                    profit_margin=Case(
                        When(total_revenue__gt=0, then=ExpressionWrapper((F('total_revenue') - F('total_cost')) * Value(100.0) / F('total_revenue'), output_field=DecimalField(max_digits=7, decimal_places=2))),
                        default=Value(0),
                        output_field=DecimalField(max_digits=7, decimal_places=2),
                    )
                )
                .order_by('-total_profit')
            )
            profit_analysis = list(profit_qs)
            winners = profit_analysis[:5]
            losers = list(profit_qs.order_by('total_profit')[:5])

            if profit_analysis:
                total_revenue = sum([(p.get('total_revenue') or Decimal('0.00')) for p in profit_analysis], Decimal('0.00'))
                total_cost = sum([(p.get('total_cost') or Decimal('0.00')) for p in profit_analysis], Decimal('0.00'))
                total_profit = total_revenue - total_cost
                margin_percent = (total_profit * Decimal('100') / total_revenue) if total_revenue > 0 else Decimal('0.00')
                profit_summary = {
                    'total_revenue': total_revenue,
                    'total_cost': total_cost,
                    'total_profit': total_profit,
                    'margin_percent': margin_percent,
                }

            for row in winners:
                margin = row.get('profit_margin') or Decimal('0')
                row['margin_bar'] = min(abs(float(margin)), 100.0)
            for row in losers:
                margin = row.get('profit_margin') or Decimal('0')
                row['margin_bar'] = min(abs(float(margin)), 100.0)

        # --- Prepare logo CID for email embedding ---
        logo_cid = ''
        logo_data = None
        logo_mime = 'image/png'
        if settings_obj.logo_base64:
            try:
                import base64 as b64
                raw = settings_obj.logo_base64
                # Strip data URI prefix: "data:image/png;base64,AAAA..."
                if ',' in raw:
                    header_part, b64_part = raw.split(',', 1)
                    if 'jpeg' in header_part or 'jpg' in header_part:
                        logo_mime = 'image/jpeg'
                    else:
                        logo_mime = 'image/png'
                    logo_data = b64.b64decode(b64_part)
                    logo_cid = 'cid:shift_logo'
            except Exception:
                logo_cid = ''
                logo_data = None

        context = {
            'BASE_URL': base_url.rstrip('/'),
            'system_settings': settings_obj,
            'shift': shift,
            'logo_cid': logo_cid,
            'cash_returns_total': refund_cash_total,
            'sales_count': sales_count,
            'sales_total': totals.get('sales_total') or Decimal('0.00'),
            'cash_total': totals.get('cash_total') or Decimal('0.00'),
            'card_total': totals.get('wallet_total') or Decimal('0.00'),
            'visa_total': totals.get('visa_total') or Decimal('0.00'),
            'other_total': other_total,
            'expenses_withdrawals': expenses_withdrawals,
            'manual_transactions': manual_transactions,
            'returns': returns,
            'invoices': orders,
            'top_products': top_products,
            'low_stock': low_stock,
            'out_of_stock': out_of_stock,
            'rush_hours': rush_hours,
            'max_rush_count': max_rush_count,
            'winners': winners,
            'losers': losers,
            'profit_analysis': profit_analysis,
            'products_missing_cost': products_missing_cost,
            'profit_summary': profit_summary,
            'shift_has_sales': sales_count > 0,
        }

        html_body = render_to_string('emails/shift_report.html', context)

        subject = f"تقرير إغلاق الشيفت #{shift.id} - {shift.start_time.date()}"
        backend = EmailBackend(
            host='smtp.gmail.com',
            port=587,
            username=sender,
            password=app_password,
            use_tls=True,
            fail_silently=False
        )
        msg = EmailMessage(subject, html_body, sender, recipients, connection=backend)
        msg.content_subtype = 'html'

        # Embed logo as CID attachment so email clients render it inline
        if logo_data and logo_cid:
            from email.mime.image import MIMEImage
            mime_img = MIMEImage(logo_data, _subtype=logo_mime.split('/')[-1])
            mime_img.add_header('Content-ID', '<shift_logo>')
            mime_img.add_header('Content-Disposition', 'inline', filename='logo.png')
            msg.attach(mime_img)
            msg.mixed_subtype = 'related'

        msg.send()
        ShiftEmailLog.objects.create(shift=shift, success=True, error_message='')
    except Exception as exc:
        ShiftEmailLog.objects.create(shift=shift, success=False, error_message=str(exc))
        logger.exception("Shift email failed for shift %s: %s", shift.id, str(exc))


# ─────────────────────────────────────────────
#  FINANCIAL DASHBOARD
# ─────────────────────────────────────────────

@login_required
@require_permission('financial', 'view')
def financial_dashboard(request):
    """
    لوحة التحكم المالية: تعرض الأرصدة الحالية وحالة الشيفت
    """
    # exclude_dead_duplicates() so the "الحسابات والمحافظ" list on this page doesn't
    # show the same coded-duplicate clutter that /financial/accounts/ had.
    accounts = _exclude_dead_coded_accounts(Account.objects.filter(is_active=True))

    # SQLite's SUM() aggregate does its arithmetic in floating point internally, so summing
    # 2+ DecimalField rows can come back as e.g. Decimal('34565.8600000000') instead of
    # Decimal('34565.86') — quantize immediately so that noise never reaches a template
    # (a naive |intcomma or |floatformat can print those extra digits verbatim).
    def _cash_sum(account_type_in):
        raw = accounts.filter(account_type__in=account_type_in).aggregate(Sum('balance'))['balance__sum'] or Decimal('0')
        return Decimal(str(raw)).quantize(Decimal('0.01'))

    total_cash = _cash_sum(['CASH_DRAWER', 'SAFE'])
    total_vodafone = _cash_sum(['VODAFONE_CASH'])
    total_instapay = _cash_sum(['INSTAPAY'])
    total_bank = _cash_sum(['BANK'])

    # GLOBAL shift (not per-user)
    current_shift = DailyShift.objects.filter(is_closed=False).last()

    recent_transactions = Transaction.objects.select_related('account', 'created_by').all().order_by('-created_at')[:20]

    context = {
        'accounts': accounts,
        'total_cash': total_cash,
        'total_vodafone': total_vodafone,
        'total_instapay': total_instapay,
        'total_bank': total_bank,
        'current_shift': current_shift,
        'recent_transactions': recent_transactions,
    }
    return render(request, 'financial/dashboard.html', context)


# ─────────────────────────────────────────────
#  ACCOUNT MANAGEMENT (CRUD)
# ─────────────────────────────────────────────

@login_required
@require_granular_action('financial', 'accounts', 'financial', 'view')
def account_list(request):
    """إدارة حسابات النظام المالية"""
    accounts = _exclude_dead_coded_accounts(Account.objects.all()).order_by('account_type', 'name')
    total_balance = accounts.filter(is_active=True).aggregate(Sum('balance'))['balance__sum'] or 0
    context = {
        'accounts': accounts,
        'total_balance': total_balance,
    }
    return render(request, 'financial/account_list.html', context)


@login_required
@require_granular_action('financial', 'accounts', 'financial', 'manage')
def account_create(request):
    """إنشاء حساب مالي جديد"""
    if request.method == 'POST':
        form = AccountForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, "تم إنشاء الحساب بنجاح.")
            return redirect('financial:account_list')
    else:
        form = AccountForm()
    return render(request, 'financial/account_form.html', {'form': form, 'title': 'إضافة حساب جديد'})


@login_required
@require_permission('financial', 'manage')
def account_edit(request, pk):
    """تعديل حساب مالي"""
    account = get_object_or_404(Account, pk=pk)
    if request.method == 'POST':
        from .models import PeriodLock
        if PeriodLock.is_locked(timezone.now()) and not has_permission(request.user, 'financial', 'manage'):
            messages.error(request, "الفترة المحاسبية الحالية مغلقة، لا يمكن تعديل رصيد حساب.")
            return redirect('financial:account_list')

        form = AccountForm(request.POST, instance=account)
        if form.is_valid():
            form.save()
            messages.success(request, "تم تحديث الحساب بنجاح.")
            return redirect('financial:account_list')
    else:
        form = AccountForm(instance=account)
    return render(request, 'financial/account_form.html', {'form': form, 'title': f'تعديل: {account.name}', 'account': account})


@login_required
@require_permission('financial', 'view')
def account_detail(request, pk):
    """عرض تفاصيل حساب وسجل معاملاته"""
    account = get_object_or_404(Account, pk=pk)
    transactions_list = Transaction.objects.filter(
        Q(account=account) | Q(to_account=account)
    ).select_related('created_by', 'shift').order_by('-created_at')

    # Date filter
    date_from = request.GET.get('date_from')
    date_to = request.GET.get('date_to')
    if date_from in [None, '', 'None', 'undefined']: date_from = None
    if date_to in [None, '', 'None', 'undefined']: date_to = None

    if date_from:
        transactions_list = transactions_list.filter(created_at__date__gte=date_from)
    if date_to:
        transactions_list = transactions_list.filter(created_at__date__lte=date_to)

    # Fix: Correctly calculate In/Out totals including Transfers
    total_in = transactions_list.filter(
        Q(account=account, transaction_type__in=['INCOME', 'DEPOSIT', 'SALE']) |
        Q(to_account=account, transaction_type='TRANSFER')
    ).aggregate(total=Sum('amount'))['total'] or 0
    
    total_out = transactions_list.filter(
        Q(account=account, transaction_type__in=['EXPENSE', 'WITHDRAWAL', 'SUPPLIER_PAYMENT', 'REFUND', 'TRANSFER'])
    ).aggregate(total=Sum('amount'))['total'] or 0

    paginator = Paginator(transactions_list, 30)
    page_obj = paginator.get_page(request.GET.get('page'))

    context = {
        'account': account,
        'page_obj': page_obj,
        'stats': {
            'total_in': total_in,
            'total_out': total_out
        },
        'date_from': date_from,
        'date_to': date_to,
    }
    return render(request, 'financial/account_detail.html', context)


# ─────────────────────────────────────────────
#  ALL TRANSACTIONS
# ─────────────────────────────────────────────

@login_required
@require_permission('financial', 'view')
def all_transactions(request):
    """صفحة عرض جميع العمليات المالية مع إحصائيات شاملة وفلترة."""
    transactions = Transaction.objects.select_related('account', 'created_by').all().order_by('-created_at')
    accounts = Account.objects.all()

    # --- Filtering ---
    date_from = request.GET.get('date_from')
    date_to = request.GET.get('date_to')
    if date_from in [None, '', 'None', 'undefined']: date_from = None
    if date_to in [None, '', 'None', 'undefined']: date_to = None

    if date_from:
        transactions = transactions.filter(created_at__date__gte=date_from)
    if date_to:
        transactions = transactions.filter(created_at__date__lte=date_to)

    trans_type = request.GET.get('type')
    if trans_type:
        transactions = transactions.filter(transaction_type=trans_type)

    account_id = request.GET.get('account')
    if account_id:
        transactions = transactions.filter(account_id=account_id)

    # --- Statistics (FIX: include SUPPLIER_PAYMENT and REFUND in expenses) ---
    stats = transactions.aggregate(
        total_income=Sum('amount', filter=Q(transaction_type__in=['INCOME', 'DEPOSIT', 'SALE'])),
        total_expense=Sum('amount', filter=Q(transaction_type__in=['EXPENSE', 'WITHDRAWAL', 'SUPPLIER_PAYMENT', 'REFUND'])),
        count_total=Count('id'),
        count_sales=Count('id', filter=Q(transaction_type='SALE')),
        count_expense=Count('id', filter=Q(transaction_type='EXPENSE')),
        count_income=Count('id', filter=Q(transaction_type__in=['INCOME', 'DEPOSIT'])),
        count_withdrawal=Count('id', filter=Q(transaction_type='WITHDRAWAL')),
        count_refund=Count('id', filter=Q(transaction_type='REFUND')),
    )

    total_income = stats['total_income'] or 0
    total_expense = stats['total_expense'] or 0
    net_flow = total_income - total_expense

    paginator = Paginator(transactions, 50)
    page_obj = paginator.get_page(request.GET.get('page'))

    context = {
        'page_obj': page_obj,
        'accounts': accounts,
        'stats': stats,
        'net_flow': net_flow,
        'total_income': total_income,
        'total_expense': total_expense,
        'date_from': date_from,
        'date_to': date_to,
        'selected_type': trans_type,
        'selected_account': int(account_id) if account_id else None,
        'transaction_types': Transaction.TRANSACTION_TYPES,
    }
    return render(request, 'financial/all_transactions.html', context)


@login_required
@require_permission('financial', 'view')
def print_all_transactions(request):
    """عرض مخصص لطباعة تقرير العمليات."""
    transactions = Transaction.objects.all().order_by('-created_at')

    date_from = request.GET.get('date_from')
    date_to = request.GET.get('date_to')
    if date_from in [None, '', 'None', 'undefined']: date_from = None
    if date_to in [None, '', 'None', 'undefined']: date_to = None

    if date_from:
        transactions = transactions.filter(created_at__date__gte=date_from)
    if date_to:
        transactions = transactions.filter(created_at__date__lte=date_to)

    trans_type = request.GET.get('type')
    if trans_type:
        transactions = transactions.filter(transaction_type=trans_type)

    account_id = request.GET.get('account')
    if account_id:
        transactions = transactions.filter(account_id=account_id)

    stats = transactions.aggregate(
        total_income=Sum('amount', filter=Q(transaction_type__in=['INCOME', 'DEPOSIT', 'SALE'])),
        total_expense=Sum('amount', filter=Q(transaction_type__in=['EXPENSE', 'WITHDRAWAL', 'SUPPLIER_PAYMENT', 'REFUND'])),
        count_total=Count('id')
    )
    total_income = stats['total_income'] or 0
    total_expense = stats['total_expense'] or 0
    net_flow = total_income - total_expense

    style = request.GET.get('style', 'a4')
    context = {
        'transactions': transactions,
        'stats': stats,
        'total_income': total_income,
        'total_expense': total_expense,
        'net_flow': net_flow,
        'date_from': date_from,
        'date_to': date_to,
        'style': style,
        'generated_at': timezone.now(),
        'user': request.user,
    }
    return render(request, 'financial/print_transactions.html', context)


# ─────────────────────────────────────────────
#  SHIFT MANAGEMENT
# ─────────────────────────────────────────────

def suggested_shift_start_balance():
    """رصيد بداية الوردية المقترح: العد الفعلي لآخر شيفت مغلق، أو رصيد الدرج الحالي احتياطياً."""
    last_closed = DailyShift.objects.filter(is_closed=True).order_by('-end_time').first()
    if last_closed and last_closed.actual_closing_balance is not None:
        return last_closed.actual_closing_balance
    cash_drawer = Account.objects.filter(account_type='CASH_DRAWER').first()
    return cash_drawer.balance if cash_drawer else Decimal('0.00')


def _open_shift(user, start_balance, opening_notes=''):
    """ينشئ شيفت جديد ويضبط رصيد درج الكاشير مع رصيد بداية الوردية المُدخل.

    مشتركة بين صفحة فتح الوردية الكاملة (start_shift) ونافذة فتح الوردية السريعة
    داخل شاشة الكاشير (start_shift_ajax) لتفادي تكرار منطق التسوية.
    """
    start_balance = start_balance or Decimal('0.00')
    shift = DailyShift.objects.create(
        employee=user, start_time=timezone.now(),
        start_balance=start_balance, opening_notes=opening_notes,
    )

    cash_drawer, created = Account.objects.get_or_create(
        account_type='CASH_DRAWER',
        defaults={'name': 'درج الكاشير', 'balance': start_balance}
    )

    if not created:
        system_balance = cash_drawer.balance
        diff = start_balance - system_balance
        if diff != 0:
            # FIX #10: عجز = مصروف إداري (ليس سحب). زيادة = إيداع.
            if diff > 0:
                t_type = 'DEPOSIT'
                t_desc = f"تسوية افتتاحية للشيفت — زيادة نقدية ({diff:+.2f} ج.م)"
            else:
                t_type = 'EXPENSE'
                t_desc = f"عجز افتتاحي في الشيفت ({diff:.2f} ج.م) — يستوجب المراجعة"

            Transaction.objects.create(
                shift=shift, account=cash_drawer, transaction_type=t_type,
                amount=abs(diff), description=t_desc, created_by=user,
            )

    return shift


@login_required
def start_shift(request):
    """بدء شيفت جديد"""
    if not cashier_can_open_shift(request.user):
        raise PermissionDenied("You do not have permission to open a shift.")

    # منع أي شيفت مكرر — التحقق عالمياً
    if DailyShift.objects.filter(is_closed=False).exists():
        messages.warning(request, "يوجد شيفت مفتوح بالفعل في النظام! يرجى إغلاقه أولاً لضمان دقة الحسابات.")
        return redirect('financial:dashboard')

    if request.method == 'POST':
        form = StartShiftForm(request.POST)
        if form.is_valid():
            _open_shift(request.user, form.cleaned_data['start_balance'], form.cleaned_data.get('opening_notes', ''))
            messages.success(request, "تم فتح الشيفت بنجاح.")
            return redirect('financial:dashboard')
    else:
        form = StartShiftForm(initial={'start_balance': suggested_shift_start_balance()})

    return render(request, 'financial/shift_form.html', {'form': form})


@login_required
def start_shift_ajax(request):
    """فتح وردية سريع من داخل شاشة الكاشير (Popup) بدل الانتقال لصفحة منفصلة."""
    if not cashier_can_open_shift(request.user):
        return JsonResponse({'status': 'error', 'message': 'لا تمتلك صلاحية كافية للقيام بهذا الإجراء.'}, status=403)

    if request.method != 'POST':
        return JsonResponse({'status': 'error', 'message': 'Invalid method'}, status=405)

    if DailyShift.objects.filter(is_closed=False).exists():
        return JsonResponse({'status': 'error', 'message': 'يوجد شيفت مفتوح بالفعل في النظام.'}, status=400)

    try:
        start_balance = Decimal(str(request.POST.get('start_balance') or '0'))
    except Exception:
        return JsonResponse({'status': 'error', 'message': 'رصيد بداية غير صالح.'}, status=400)
    if start_balance < 0:
        return JsonResponse({'status': 'error', 'message': 'رصيد بداية الوردية لا يمكن أن يكون سالباً.'}, status=400)

    opening_notes = (request.POST.get('opening_notes') or '').strip()
    shift = _open_shift(request.user, start_balance, opening_notes)
    return JsonResponse({'status': 'success', 'message': 'تم فتح الوردية بنجاح.', 'shift_id': shift.id})


@login_required
def close_shift(request, pk):
    """إغلاق الشيفت وحساب العجز/الزيادة"""
    if not cashier_can_close_shift(request.user):
        raise PermissionDenied("You do not have permission to close a shift.")
    shift = get_object_or_404(DailyShift, pk=pk)

    end_t = shift.end_time if shift.end_time else timezone.now()

    # 1. مبيعات الفترة (كل المبيعات خلال وقت الشيفت — لا يُقيَّد بمستخدم)
    orders = _get_shift_orders(shift)

    total_sales_amount = orders.aggregate(Sum('total_amount'))['total_amount__sum'] or Decimal('0.00')
    sales_cash = orders.aggregate(Sum('cash_paid'))['cash_paid__sum'] or Decimal('0.00')
    total_collected_wallet = orders.aggregate(Sum('wallet_paid'))['wallet_paid__sum'] or 0
    total_collected_instapay = orders.aggregate(Sum('instapay_paid'))['instapay_paid__sum'] or 0
    total_collected_visa = orders.aggregate(Sum('visa_paid'))['visa_paid__sum'] or 0
    total_collected = orders.aggregate(Sum('received_amount'))['received_amount__sum'] or 0
    total_deferred = total_sales_amount - total_collected

    # 2. معاملات الشيفت على درج الكاشير
    shift_transactions = Transaction.objects.filter(shift=shift, account__account_type='CASH_DRAWER')

    # Exclude _open_shift()'s own opening reconciliation entry — it exists purely to
    # bring the CASH_DRAWER ledger balance up to start_balance, which is already
    # counted via start_bal below. Including it here double-counts that same money.
    added_money = shift_transactions.filter(
        transaction_type__in=['DEPOSIT', 'INCOME']
    ).exclude(description__startswith='تسوية افتتاحية للشيفت').aggregate(Sum('amount'))['amount__sum'] or Decimal('0.00')

    # Must mirror shift_x_report's `removed_money`/`supplier_pay` set and
    # DailyShift.calculate_expected_balance() exactly, or a cash supplier payment made
    # mid-shift makes this closing figure disagree with the X-report pulled minutes
    # earlier for the same shift.
    removed_money = shift_transactions.filter(
        transaction_type__in=['EXPENSE', 'WITHDRAWAL', 'REFUND', 'SUPPLIER_PAYMENT']
    ).exclude(description__startswith='عجز افتتاحي في الشيفت').aggregate(Sum('amount'))['amount__sum'] or Decimal('0.00')

    # FIX #11: احسب المسحوبات والمرتجعات منفصلة للعرض
    total_withdrawals_display = shift_transactions.filter(
        transaction_type='WITHDRAWAL'
    ).aggregate(Sum('amount'))['amount__sum'] or Decimal('0.00')

    total_refunds_display = shift_transactions.filter(
        transaction_type='REFUND'
    ).aggregate(Sum('amount'))['amount__sum'] or Decimal('0.00')

    total_expenses_display = shift_transactions.filter(
        transaction_type='EXPENSE'
    ).aggregate(Sum('amount'))['amount__sum'] or Decimal('0.00')

    # 3. الرصيد المتوقع
    start_bal = shift.start_balance or Decimal('0.00')
    expected_balance = start_bal + sales_cash + added_money - removed_money

    # للعرض
    all_expenses = Transaction.objects.filter(shift=shift, transaction_type='EXPENSE')
    all_withdrawals = Transaction.objects.filter(shift=shift, transaction_type='WITHDRAWAL')
    all_refunds = Transaction.objects.filter(shift=shift, transaction_type='REFUND')
    other_transactions = Transaction.objects.filter(shift=shift).exclude(
        transaction_type__in=['SALE', 'EXPENSE', 'WITHDRAWAL', 'REFUND']
    )

    # Accounts available for end-of-shift withdrawal (exclude the cash drawer itself)
    withdrawal_accounts = _exclude_dead_coded_accounts(
        Account.objects.filter(is_active=True).exclude(account_type='CASH_DRAWER')
    ).order_by('account_type', 'name')

    if request.method == 'POST':
        if not request.user.is_superuser and shift.employee != request.user:
            messages.error(request, "لا يمكنك إغلاق شيفت مستخدم آخر إلا بصلاحيات المدير.")
            return redirect('financial:dashboard')

        from .models import PeriodLock
        if PeriodLock.is_locked(shift.start_time) and not has_permission(request.user, 'financial', 'manage'):
            messages.error(request, "الفترة المحاسبية لهذا الشيفت مغلقة، لا يمكن إغلاقه.")
            return redirect('financial:dashboard')

        form = CloseShiftForm(request.POST, instance=shift)
        if form.is_valid():
            final_shift = form.save(commit=False)
            final_shift.end_time = timezone.now()
            final_shift.expected_closing_balance = expected_balance
            final_shift.total_sales_cash = sales_cash
            final_shift.total_sales_visa = total_collected_visa

            # FIX #8: احفظ إجمالي المسحوبات
            final_shift.total_withdrawals = total_withdrawals_display
            # احفظ المصروفات والإيرادات
            final_shift.total_expenses = total_expenses_display

            actual = final_shift.actual_closing_balance or Decimal('0.00')
            final_shift.difference = actual - expected_balance
            final_shift.is_closed = True
            final_shift.save()

            # ─── Handle end-of-shift withdrawal/transfer ───
            withdraw_account_id = request.POST.get('withdraw_account_id', '').strip()
            if withdraw_account_id and withdraw_account_id != '0' and actual > 0:
                try:
                    target_account = Account.objects.get(pk=int(withdraw_account_id), is_active=True)
                    cash_drawer = Account.objects.filter(account_type='CASH_DRAWER').first()
                    if cash_drawer and cash_drawer.balance >= actual:
                        Transaction.objects.create(
                            shift=final_shift,
                            account=cash_drawer,
                            to_account=target_account,
                            transaction_type='TRANSFER',
                            amount=actual,
                            description=f"تحويل إغلاق الشيفت #{final_shift.id} إلى {target_account.name}",
                            created_by=request.user,
                        )
                        messages.info(request, f"تم تحويل {actual} ج.م إلى «{target_account.name}».")
                    else:
                        # This used to fail silently — the cashier would believe the
                        # transfer happened just because the shift closed successfully.
                        drawer_balance = cash_drawer.balance if cash_drawer else Decimal('0.00')
                        messages.error(
                            request,
                            f"لم يتم تحويل {actual} ج.م إلى «{target_account.name}» — رصيد درج الكاشير "
                            f"({drawer_balance} ج.م) غير كافٍ. راجع الأمر يدوياً من صفحة الحسابات."
                        )
                except (Account.DoesNotExist, ValueError):
                    messages.error(request, "لم يتم تحويل مبلغ إغلاق الشيفت — حساب الوجهة المختار غير صالح.")

            try:
                base_url = request.build_absolute_uri('/').rstrip('/')
                send_shift_report_email(final_shift, base_url)
            except Exception as exc:
                logger.exception("Failed to send shift report email for shift %s: %s", final_shift.id, str(exc))

            messages.success(request, f"تم إغلاق الشيفت. الفرق: {final_shift.difference} ج.م")
            return redirect('financial:close_shift', pk=shift.pk)
    else:
        form = CloseShiftForm(instance=shift)

    context = {
        'form': form,
        'shift': shift,
        'expected_balance': expected_balance,
        'cash_sales': sales_cash,
        'orders': orders,
        'total_sales_amount': total_sales_amount,
        'total_collected_cash': sales_cash,
        'total_collected_wallet': total_collected_wallet,
        'total_collected_instapay': total_collected_instapay,
        'total_collected_visa': total_collected_visa,
        'total_collected': total_collected,
        'total_deferred': total_deferred,
        'expenses': all_expenses,
        'total_expenses': total_expenses_display,
        'withdrawals': all_withdrawals,
        'total_withdrawals': total_withdrawals_display,
        'refunds': all_refunds,
        'total_refunds': total_refunds_display,
        'other_transactions': other_transactions,
        'added_money': added_money,
        'withdrawal_accounts': withdrawal_accounts,
    }
    return render(request, 'financial/close_shift.html', context)


@login_required
@require_permission('pos', 'view')
def print_shift_summary(request, pk):
    """طباعة ملخص الشيفت"""
    shift = get_object_or_404(DailyShift, pk=pk)
    end_t = shift.end_time if shift.end_time else timezone.now()

    orders = _get_shift_orders(shift)

    total_sales_amount = orders.aggregate(Sum('total_amount'))['total_amount__sum'] or 0
    total_collected_cash = orders.aggregate(Sum('cash_paid'))['cash_paid__sum'] or 0
    total_collected_wallet = orders.aggregate(Sum('wallet_paid'))['wallet_paid__sum'] or 0
    total_collected_instapay = orders.aggregate(Sum('instapay_paid'))['instapay_paid__sum'] or 0
    total_collected_visa = orders.aggregate(Sum('visa_paid'))['visa_paid__sum'] or 0
    total_collected = orders.aggregate(Sum('received_amount'))['received_amount__sum'] or 0
    total_deferred = total_sales_amount - total_collected

    expenses = Transaction.objects.filter(shift=shift, transaction_type='EXPENSE')
    total_expenses = expenses.aggregate(Sum('amount'))['amount__sum'] or 0

    # FIX #14: إضافة المسحوبات والمرتجعات للطباعة
    withdrawals = Transaction.objects.filter(shift=shift, transaction_type='WITHDRAWAL')
    total_withdrawals = withdrawals.aggregate(Sum('amount'))['amount__sum'] or 0

    refunds = Transaction.objects.filter(shift=shift, transaction_type='REFUND')
    total_refunds = refunds.aggregate(Sum('amount'))['amount__sum'] or 0

    other_transactions = Transaction.objects.filter(shift=shift).exclude(
        transaction_type__in=['SALE', 'EXPENSE', 'WITHDRAWAL', 'REFUND']
    )

    style = request.GET.get('style', 'thermal')

    context = {
        'shift': shift,
        'orders': orders,
        'total_sales_amount': total_sales_amount,
        'total_collected_cash': total_collected_cash,
        'total_collected_wallet': total_collected_wallet,
        'total_collected_instapay': total_collected_instapay,
        'total_collected_visa': total_collected_visa,
        'total_collected': total_collected,
        'total_deferred': total_deferred,
        'expenses': expenses,
        'total_expenses': total_expenses,
        'withdrawals': withdrawals,
        'total_withdrawals': total_withdrawals,
        'refunds': refunds,
        'total_refunds': total_refunds,
        'other_transactions': other_transactions,
        'style': style,
    }
    return render(request, 'financial/print_shift_summary.html', context)


@login_required
@require_permission('financial', 'manage')
def add_transaction(request):
    """إضافة عملية مالية (مصروف، سحب، تحويل، إيداع)"""
    # ربط بالشيفت المفتوح عالمياً
    current_shift = DailyShift.objects.filter(is_closed=False).last()

    initial_type = request.GET.get('type')
    initial_data = {}
    if initial_type:
        initial_data['transaction_type'] = initial_type

    if request.method == 'POST':
        # Phase 4.5: same period-lock guard sales/views.py already applies to order
        # edits/voids — a closed accounting period must also block new cash movements.
        from .models import PeriodLock
        if PeriodLock.is_locked(timezone.now()) and not has_permission(request.user, 'financial', 'manage'):
            messages.error(request, "الفترة المحاسبية الحالية مغلقة، لا يمكن إضافة عمليات مالية.")
            return redirect('financial:dashboard')

        form = TransactionForm(request.POST)
        if form.is_valid():
            trans = form.save(commit=False)
            trans.created_by = request.user
            if current_shift:
                trans.shift = current_shift
            trans.save()
            messages.success(request, "تم تسجيل العملية بنجاح.")
            return redirect('financial:dashboard')
    else:
        form = TransactionForm(initial=initial_data)

    return render(request, 'financial/transaction_form.html', {
        'form': form,
        'current_shift': current_shift,
    })


@login_required
@require_granular_action('financial', 'withdraw', 'financial', 'manage')
def withdrawal_view(request):
    """صفحة مخصصة لتسجيل المسحوبات (سحب المالك)"""
    current_shift = DailyShift.objects.filter(is_closed=False).last()

    if request.method == 'POST':
        from .models import PeriodLock
        if PeriodLock.is_locked(timezone.now()) and not has_permission(request.user, 'financial', 'manage'):
            messages.error(request, "الفترة المحاسبية الحالية مغلقة، لا يمكن تسجيل مسحوبات.")
            return redirect('financial:dashboard')

        form = TransactionForm(request.POST)
        if form.is_valid():
            trans = form.save(commit=False)
            trans.created_by = request.user
            trans.transaction_type = 'WITHDRAWAL'
            if current_shift:
                trans.shift = current_shift
            trans.save()
            messages.success(request, f"تم تسجيل السحب: {trans.amount} ج.م بنجاح.")
            return redirect('financial:dashboard')
    else:
        # يملأ النوع مسبقاً WITHDRAWAL
        form = TransactionForm(initial={'transaction_type': 'WITHDRAWAL'})

    return render(request, 'financial/withdrawal_form.html', {
        'form': form,
        'current_shift': current_shift,
    })


@login_required
@require_granular_action('financial', 'shift_history', 'financial', 'view')
def shift_history(request):
    """سجل الشيفتات"""
    shifts_list = DailyShift.objects.all().order_by('-start_time')

    search_query = request.GET.get('q')
    if search_query:
        from django.db.models import Q as DQ
        shifts_list = shifts_list.filter(
            DQ(employee__username__icontains=search_query) |
            DQ(id__icontains=search_query)
        )

    date_from = request.GET.get('date_from')
    date_to = request.GET.get('date_to')
    if date_from in [None, '', 'None', 'undefined']: date_from = None
    if date_to in [None, '', 'None', 'undefined']: date_to = None

    if date_from:
        shifts_list = shifts_list.filter(start_time__date__gte=date_from)
    if date_to:
        shifts_list = shifts_list.filter(start_time__date__lte=date_to)

    status = request.GET.get('status')
    if status == 'open':
        shifts_list = shifts_list.filter(is_closed=False)
    elif status == 'closed':
        shifts_list = shifts_list.filter(is_closed=True)

    paginator = Paginator(shifts_list, 20)
    page_obj = paginator.get_page(request.GET.get('page'))

    context = {
        'page_obj': page_obj,
        'search_query': search_query,
        'date_from': date_from,
        'date_to': date_to,
        'status': status,
    }
    return render(request, 'financial/shift_history.html', context)


# User-Facing Salaries Views
@login_required
@require_granular_action('financial', 'payroll', 'financial', 'view')
def salary_list(request):
    from .payroll_models import EmployeeSalary
    from decimal import Decimal
    salaries = EmployeeSalary.objects.all().select_related('employee')
    for sal in salaries:
        pending = sum(
            (a.due_amount() for a in sal.employee.advances.filter(is_settled=False)),
            Decimal('0'),
        )
        sal.pending_advance = pending
        sal.net_after_advance = max(Decimal('0.00'), sal.net_salary - pending)
    # Only real payment sources — a salary is paid out of cash/instapay/vodafone cash,
    # never out of a bookkeeping category (إيرادات/أصول/خصوم...) which just confused the
    # user with a dozen 0.00 options that aren't places money can actually come from.
    accounts = _exclude_dead_coded_accounts(
        Account.objects.filter(is_active=True, account_type__in=['CASH_DRAWER', 'INSTAPAY', 'VODAFONE_CASH'])
    ).order_by('name')
    return render(request, 'financial/salary_list.html', {'salaries': salaries, 'accounts': accounts})


@login_required
@require_permission('financial', 'manage')
def salary_create(request):
    from .payroll_models import EmployeeSalary
    from .forms import EmployeeSalaryForm
    if request.method == 'POST':
        form = EmployeeSalaryForm(request.POST)
        if form.is_valid():
            salary = form.save()
            messages.success(request, f"تم حفظ إعدادات مرتب الموظف {salary.employee.username} بنجاح.")
            return redirect('financial:salary_list')
    else:
        form = EmployeeSalaryForm()
    return render(request, 'financial/salary_form.html', {'form': form, 'is_edit': False})


@login_required
@require_permission('financial', 'manage')
def salary_edit(request, pk):
    from .payroll_models import EmployeeSalary
    from .forms import EmployeeSalaryForm
    salary = get_object_or_404(EmployeeSalary, pk=pk)
    if request.method == 'POST':
        form = EmployeeSalaryForm(request.POST, instance=salary)
        if form.is_valid():
            form.save()
            messages.success(request, f"تم تحديث إعدادات مرتب الموظف {salary.employee.username} بنجاح.")
            return redirect('financial:salary_list')
    else:
        form = EmployeeSalaryForm(instance=salary)
    return render(request, 'financial/salary_form.html', {'form': form, 'is_edit': True, 'salary': salary})


@login_required
@require_permission('financial', 'manage')
def salary_pay(request, pk):
    from .payroll_models import EmployeeSalary
    from django.utils import timezone
    from django.db import transaction as db_tx
    from decimal import Decimal
    import calendar

    if request.method != 'POST':
        messages.error(request, "طريقة طلب غير صالحة للصرف.")
        return redirect('financial:salary_list')

    salary_config = get_object_or_404(EmployeeSalary, pk=pk)
    account_id = request.POST.get('account_id')

    if not account_id:
        messages.error(request, "يرجى تحديد حساب الدفع.")
        return redirect('financial:salary_list')

    from .models import PeriodLock
    if PeriodLock.is_locked(timezone.now()) and not has_permission(request.user, 'financial', 'manage'):
        messages.error(request, "الفترة المحاسبية الحالية مغلقة، لا يمكن صرف رواتب.")
        return redirect('financial:salary_list')

    payment_account = get_object_or_404(Account, id=account_id)

    # Deduct this period's outstanding سلف installments from the net before paying —
    # same rule the قسيمة راتب (Payslip) flow already applies (see payslip_pay).
    plan = []
    advance_deducted = Decimal('0')
    for a in salary_config.employee.advances.filter(is_settled=False):
        due = a.due_amount()
        if due > 0:
            plan.append((a, due))
            advance_deducted += due
    net_to_pay = max(Decimal('0.00'), salary_config.net_salary - advance_deducted)

    if payment_account.balance < net_to_pay:
        messages.error(request, f"رصيد حساب {payment_account.name} ({payment_account.balance} ج.م) لا يكفي لصرف المرتب ({net_to_pay} ج.م).")
        return redirect('financial:salary_list')

    # Deduct via Transaction for the current month
    now = timezone.now()
    month_name = calendar.month_name[now.month]
    current_period = f"{month_name} {now.year}"

    # Guard against double-payment: unlike the newer Payslip flow (unique_together on
    # employee+period), this legacy path has no structural record of "already paid this
    # period" — a double-click or resubmit would otherwise pay the same salary twice.
    already_paid = Transaction.objects.filter(
        transaction_type='EXPENSE',
        description__startswith=f"صرف راتب {current_period} - {salary_config.employee.username}",
    ).exists()
    if already_paid:
        messages.error(request, f"تم صرف راتب {salary_config.employee.username} لشهر {current_period} بالفعل.")
        return redirect('financial:salary_list')

    with db_tx.atomic():
        for a, due in plan:
            a.apply(due)
        desc = f"صرف راتب {current_period} - {salary_config.employee.username}"
        if advance_deducted > 0:
            desc += f" (بعد خصم سلفة {advance_deducted} ج.م)"
        Transaction.objects.create(
            account=payment_account,
            transaction_type='EXPENSE',
            amount=net_to_pay,
            description=desc,
            created_by=request.user,
        )
        # Same cash outflow the Transaction above records, but categorized under
        # "رواتب موظفين" — so this payout also appears in the position report's
        # "المصروفات حسب التصنيف" box, which only ever reads sales.Expense and had no
        # idea payroll payouts existed (they used to only post to the plain
        # Transaction ledger, uncategorized).
        from sales.models import Expense
        Expense.objects.create(
            title=desc, category='salary', amount=net_to_pay,
            description=f"صرف راتب {salary_config.employee.username} — {current_period}",
            user=request.user,
        )

    messages.success(request, f"تم صرف راتب شهر {current_period} ({net_to_pay} ج.م) بنجاح للموظف {salary_config.employee.username} من حساب {payment_account.name}!")
    return redirect('financial:salary_list')


# ── Payroll register: payslips, adjustments, advances (قسائم الرواتب) ──────────
def _current_period():
    return timezone.now().strftime('%Y-%m')


@login_required
@require_permission('financial', 'manage')
def payslip_generate(request, salary_pk):
    """Create (or open) this period's payslip for an employee, snapshotting their config
    and previewing the advance installment due."""
    from .payroll_models import EmployeeSalary, Payslip, PayslipAdjustment
    from decimal import Decimal
    cfg = get_object_or_404(EmployeeSalary, pk=salary_pk)
    period = request.POST.get('period') or request.GET.get('period') or _current_period()
    ps, created = Payslip.objects.get_or_create(
        employee=cfg.employee, period_month=period,
        defaults={'basic_salary': cfg.basic_salary, 'allowances': cfg.allowances},
    )
    if created:
        if cfg.deductions and cfg.deductions > 0:
            PayslipAdjustment.objects.create(payslip=ps, kind='deduction',
                                             label='خصومات ثابتة', amount=cfg.deductions)
        due = sum((a.due_amount() for a in cfg.employee.advances.filter(is_settled=False)), Decimal('0'))
        if due:
            ps.advance_deducted = due
            ps.save(update_fields=['advance_deducted'])
        messages.success(request, f"تم إنشاء قسيمة راتب {period} للموظف {cfg.employee.username}.")
    return redirect('financial:payslip_detail', pk=ps.pk)


@login_required
@require_permission('financial', 'view')
def payslip_detail(request, pk):
    from .payroll_models import Payslip, PayslipAdjustment
    from decimal import Decimal
    ps = get_object_or_404(Payslip, pk=pk)
    if request.method == 'POST':
        if ps.status == 'paid':
            messages.error(request, "لا يمكن تعديل قسيمة مدفوعة.")
            return redirect('financial:payslip_detail', pk=pk)
        action = request.POST.get('action')
        if action == 'add_adjustment':
            try:
                amt = Decimal(str(request.POST.get('amount')))
                kind = request.POST.get('kind')
                if amt > 0 and kind in dict(PayslipAdjustment.KIND_CHOICES):
                    PayslipAdjustment.objects.create(payslip=ps, kind=kind, amount=amt,
                                                     label=request.POST.get('label', '')[:150])
                    messages.success(request, "تمت إضافة البند.")
                else:
                    messages.error(request, "بيانات غير صحيحة.")
            except Exception:
                messages.error(request, "مبلغ غير صحيح.")
        elif action == 'set_absence':
            try:
                ps.days_absent = Decimal(str(request.POST.get('days_absent') or 0))
                ps.save(update_fields=['days_absent'])
            except Exception:
                messages.error(request, "قيمة غير صحيحة.")
        elif action == 'del_adjustment':
            PayslipAdjustment.objects.filter(pk=request.POST.get('adj_id'), payslip=ps).delete()
        elif action == 'refresh_advance':
            due = sum((a.due_amount() for a in ps.employee.advances.filter(is_settled=False)), Decimal('0'))
            ps.advance_deducted = due
            ps.save(update_fields=['advance_deducted'])
        return redirect('financial:payslip_detail', pk=pk)
    accounts = _exclude_dead_coded_accounts(Account.objects.filter(is_active=True)).order_by('name')
    return render(request, 'financial/payslip_detail.html', {
        'ps': ps, 'accounts': accounts, 'kinds': PayslipAdjustment.KIND_CHOICES,
    })


@login_required
@require_permission('financial', 'manage')
def payslip_pay(request, pk):
    from .payroll_models import Payslip
    from decimal import Decimal
    from django.db import transaction as db_tx
    ps = get_object_or_404(Payslip, pk=pk)
    if request.method != 'POST':
        return redirect('financial:payslip_detail', pk=pk)
    if ps.status == 'paid':
        messages.error(request, "القسيمة مدفوعة بالفعل.")
        return redirect('financial:payslip_detail', pk=pk)

    from .models import PeriodLock
    if PeriodLock.is_locked(timezone.now()) and not has_permission(request.user, 'financial', 'manage'):
        messages.error(request, "الفترة المحاسبية الحالية مغلقة، لا يمكن صرف القسيمة.")
        return redirect('financial:payslip_detail', pk=pk)

    account = get_object_or_404(Account, id=request.POST.get('account_id'))

    # Recompute the advance deduction from live balances and plan what to apply.
    plan = []
    applied_total = Decimal('0')
    for a in ps.employee.advances.filter(is_settled=False):
        due = a.due_amount()
        if due > 0:
            plan.append((a, due))
            applied_total += due
    ps.advance_deducted = applied_total
    net = ps.net
    if account.balance < net:
        messages.error(request, f"رصيد {account.name} ({account.balance} ج.م) لا يكفي لصرف الصافي ({net} ج.م).")
        return redirect('financial:payslip_detail', pk=pk)

    with db_tx.atomic():
        for a, due in plan:
            a.apply(due)
        desc = f"صرف راتب {ps.period_month} - {ps.employee.username}"
        Transaction.objects.create(
            account=account, transaction_type='EXPENSE', amount=net,
            description=desc,
            created_by=request.user,
        )
        # Same cash outflow, categorized as "رواتب موظفين" so it shows up in the
        # position report's "المصروفات حسب التصنيف" box (see salary_pay for the
        # identical fix on the legacy quick-pay flow).
        from sales.models import Expense
        Expense.objects.create(
            title=desc, category='salary', amount=net,
            description=f"صرف راتب {ps.employee.username} — {ps.period_month}",
            user=request.user,
        )
        ps.status = 'paid'
        ps.paid_at = timezone.now()
        ps.paid_account = account
        ps.net_paid = net
        ps.save()
    messages.success(request, f"تم صرف راتب {ps.period_month} ({net} ج.م) للموظف {ps.employee.username}.")
    return redirect('financial:payslip_detail', pk=pk)


@login_required
@require_permission('financial', 'view')
def payslip_print(request, pk):
    from .payroll_models import Payslip
    ps = get_object_or_404(Payslip, pk=pk)
    return render(request, 'financial/payslip_print.html', {'ps': ps})


@login_required
@require_granular_action('financial', 'payroll', 'financial', 'view')
def advance_list(request):
    from .payroll_models import EmployeeAdvance
    advances = EmployeeAdvance.objects.select_related('employee').all()
    return render(request, 'financial/advance_list.html', {'advances': advances})


@login_required
@require_permission('financial', 'manage')
def advance_create(request):
    from .payroll_models import EmployeeAdvance
    from decimal import Decimal
    from django.contrib.auth.models import User
    if request.method == 'POST':
        try:
            emp = get_object_or_404(User, pk=request.POST.get('employee'))
            EmployeeAdvance.objects.create(
                employee=emp,
                amount=Decimal(str(request.POST.get('amount'))),
                per_period_deduction=Decimal(str(request.POST.get('per_period_deduction') or 0)),
                notes=request.POST.get('notes', ''),
            )
            messages.success(request, f"تم تسجيل سلفة للموظف {emp.username}.")
            return redirect('financial:advance_list')
        except Exception as e:
            messages.error(request, f"تعذّر الحفظ: {e}")
    employees = User.objects.filter(is_active=True).order_by('username')
    return render(request, 'financial/advance_form.html', {'employees': employees})


@login_required
@require_permission('financial', 'manage')
def advance_edit(request, pk):
    from .payroll_models import EmployeeAdvance
    from decimal import Decimal
    from django.contrib.auth.models import User

    advance = get_object_or_404(EmployeeAdvance, pk=pk)
    if request.method == 'POST':
        try:
            advance.employee = get_object_or_404(User, pk=request.POST.get('employee'))
            advance.amount = Decimal(str(request.POST.get('amount')))
            advance.per_period_deduction = Decimal(str(request.POST.get('per_period_deduction') or 0))
            advance.remaining = Decimal(str(request.POST.get('remaining') or 0))
            date_taken = request.POST.get('date_taken')
            if date_taken:
                advance.date_taken = date_taken
            advance.notes = request.POST.get('notes', '')
            advance.is_settled = advance.remaining <= 0
            advance.save()
            messages.success(request, f"تم تعديل سلفة الموظف {advance.employee.username}.")
            return redirect('financial:advance_list')
        except Exception as e:
            messages.error(request, f"تعذّر الحفظ: {e}")
    employees = User.objects.filter(is_active=True).order_by('username')
    return render(request, 'financial/advance_form.html', {'employees': employees, 'advance': advance})


# User-Facing Deals & Promotions Views
@login_required
@require_granular_action('financial', 'deals', 'financial', 'view')
def deal_list(request):
    from .payroll_models import DealDiscount
    deals = DealDiscount.objects.all()
    return render(request, 'financial/deal_list.html', {'deals': deals})


@login_required
@require_permission('financial', 'view')
def deal_report(request):
    """Promotions performance (Phase 6.7): per-promo usage count + total discount given."""
    from django.db.models import Count, Sum
    from django.db.models.functions import Coalesce
    from decimal import Decimal
    from sales.models import Order

    df = request.GET.get('date_from') or None
    dt = request.GET.get('date_to') or None
    orders = Order.objects.active().filter(applied_deal__isnull=False)
    if df:
        orders = orders.filter(created_at__date__gte=df)
    if dt:
        orders = orders.filter(created_at__date__lte=dt)

    rows = (orders.values('applied_deal', 'applied_deal__name', 'applied_deal__promo_type')
            .annotate(times_used=Count('id'),
                      total_discount=Coalesce(Sum('discount'), Decimal('0.00')),
                      total_sales=Coalesce(Sum('total_amount'), Decimal('0.00')))
            .order_by('-total_discount'))

    totals = {
        'times': sum(r['times_used'] for r in rows),
        'discount': sum((r['total_discount'] for r in rows), Decimal('0.00')),
        'sales': sum((r['total_sales'] for r in rows), Decimal('0.00')),
    }
    return render(request, 'financial/deal_report.html', {
        'title': 'أداء العروض والخصومات', 'rows': rows, 'totals': totals,
        'date_from': df or '', 'date_to': dt or '',
    })


@login_required
@require_permission('financial', 'manage')
def deal_create(request):
    from .payroll_models import DealDiscount
    from .forms import DealDiscountForm
    from products.models import Product
    import json
    if request.method == 'POST':
        form = DealDiscountForm(request.POST)
        if form.is_valid():
            deal = form.save()
            messages.success(request, f"تم إنشاء العرض الترويجي «{deal.name}» بنجاح.")
            return redirect('financial:deal_list')
    else:
        form = DealDiscountForm()
        
    # Prices (+ owning category, for the JS "filter products by selected category" live
    # filter) for the dynamic frontend preview. Menu items only, never raw materials —
    # matches DealDiscountForm's own product querysets.
    available_products = Product.objects.filter(is_active=True, is_raw_material=False)

    prices_dict = {str(p.id): {'name': p.name, 'price': float(p.price_retail), 'category_id': p.category_id}
                   for p in available_products}
    product_prices_json = json.dumps(prices_dict)

    return render(request, 'financial/deal_form.html', {
        'form': form,
        'product_prices_json': product_prices_json,
        'is_edit': False
    })


@login_required
@require_permission('financial', 'manage')
def deal_edit(request, pk):
    from .payroll_models import DealDiscount
    from .forms import DealDiscountForm
    from products.models import Product
    import json

    deal = get_object_or_404(DealDiscount, pk=pk)

    if request.method == 'POST':
        form = DealDiscountForm(request.POST, instance=deal)
        if form.is_valid():
            deal = form.save()
            messages.success(request, f"تم تحديث العرض الترويجي «{deal.name}» بنجاح.")
            return redirect('financial:deal_list')
    else:
        form = DealDiscountForm(instance=deal)

    available_products = Product.objects.filter(is_active=True, is_raw_material=False)

    prices_dict = {str(p.id): {'name': p.name, 'price': float(p.price_retail), 'category_id': p.category_id}
                   for p in available_products}
    product_prices_json = json.dumps(prices_dict)
        
    return render(request, 'financial/deal_form.html', {
        'form': form,
        'product_prices_json': product_prices_json,
        'is_edit': True,
        'deal': deal
    })


@login_required
@require_permission('financial', 'manage')
def deal_delete(request, pk):
    from .payroll_models import DealDiscount
    
    if request.method != 'POST':
        messages.error(request, "طريقة طلب غير صالحة.")
        return redirect('financial:deal_list')
        
    deal = get_object_or_404(DealDiscount, pk=pk)
    name = deal.name
    deal.delete()
    
    messages.success(request, f"تم حذف العرض الترويجي «{name}» بنجاح. ستعود أسعار المنتجات إلى طبيعتها للعمليات القادمة.")
    return redirect('financial:deal_list')


# ──────────────────────────────────────────────
#  FINANCIAL STATEMENTS (Phase 4.6) — from the general journal
# ──────────────────────────────────────────────
def _parse_date(s):
    from datetime import datetime
    if not s:
        return None
    try:
        return datetime.strptime(s, '%Y-%m-%d').date()
    except (ValueError, TypeError):
        return None


@login_required
@require_granular_action('financial', 'reports', 'financial', 'view')
def trial_balance_view(request):
    from .reports import trial_balance
    date_from = _parse_date(request.GET.get('date_from'))
    date_to = _parse_date(request.GET.get('date_to'))
    data = trial_balance(date_from, date_to)
    return render(request, 'financial/trial_balance.html', {
        'title': 'ميزان المراجعة',
        'tb': data,
        'date_from': request.GET.get('date_from', ''),
        'date_to': request.GET.get('date_to', ''),
    })


@login_required
@require_granular_action('financial', 'reports', 'financial', 'view')
@require_profit_view
def income_statement_view(request):
    from .reports import income_statement
    date_from = _parse_date(request.GET.get('date_from'))
    date_to = _parse_date(request.GET.get('date_to'))
    data = income_statement(date_from, date_to)
    return render(request, 'financial/income_statement.html', {
        'title': 'قائمة الدخل (الأرباح والخسائر)',
        'pnl': data,
        'date_from': request.GET.get('date_from', ''),
        'date_to': request.GET.get('date_to', ''),
    })


@login_required
@require_permission('financial', 'manage')
def period_lock_view(request):
    """Set or clear the accounting period lock (Phase 4.5)."""
    from .models import PeriodLock
    lock = PeriodLock.get_solo()
    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'lock':
            lock_date = _parse_date(request.POST.get('locked_up_to'))
            if not lock_date:
                messages.error(request, "يرجى اختيار تاريخ صحيح للإغلاق.")
            else:
                lock.locked_up_to = lock_date
                lock.locked_by = request.user
                lock.locked_at = timezone.now()
                lock.note = request.POST.get('note', '')
                lock.save()
                messages.success(request, f"تم إغلاق الفترة المحاسبية حتى {lock_date}.")
        elif action == 'unlock':
            lock.locked_up_to = None
            lock.locked_by = request.user
            lock.locked_at = timezone.now()
            lock.save()
            messages.success(request, "تم فتح الفترة المحاسبية (إلغاء الإغلاق).")
        return redirect('financial:period_lock')

    return render(request, 'financial/period_lock.html', {
        'title': 'إغلاق الفترة المحاسبية',
        'lock': lock,
    })


@login_required
@require_granular_action('financial', 'reports', 'financial', 'view')
def daily_summary_view(request):
    """ملخص الحركة اليومية — one printable page for a given day (Phase 9.1)."""
    from .reports import daily_summary
    # localdate() matches how the DB __date lookup extracts the date (active TZ),
    # avoiding an off-by-one near midnight that timezone.now().date() (UTC) would cause.
    day = _parse_date(request.GET.get('day')) or timezone.localdate()
    data = daily_summary(day)
    return render(request, 'financial/daily_summary.html', {
        'title': f'ملخص الحركة اليومية — {day}',
        'd': data,
        'day': day.strftime('%Y-%m-%d'),
        'print_mode': request.GET.get('print') == '1',
    })


@login_required
@require_granular_action('financial', 'shift_report', 'pos', 'view')
def shift_x_report(request):
    """X report — read-only mid-shift drawer snapshot (Phase 4.10). Does NOT close the
    shift; cashiers can pull it any time to count the drawer against the system."""
    shift = DailyShift.objects.filter(is_closed=False).last()
    if not shift:
        messages.info(request, "لا يوجد شيفت مفتوح حالياً.")
        return redirect('financial:dashboard')

    end_t = timezone.now()
    txns = shift.transactions.filter(account__account_type='CASH_DRAWER')
    cash_sales = Order.objects.filter(
        created_at__range=(shift.start_time, end_t)
    ).exclude(status='void').aggregate(s=Sum('cash_paid'))['s'] or Decimal('0.00')
    # Exclude _open_shift()'s own opening reconciliation entry — see close_shift for why.
    cash_in = txns.filter(transaction_type__in=['DEPOSIT', 'INCOME']).exclude(description__startswith='تسوية افتتاحية للشيفت').aggregate(s=Sum('amount'))['s'] or Decimal('0.00')
    refunds = txns.filter(transaction_type='REFUND').aggregate(s=Sum('amount'))['s'] or Decimal('0.00')
    expenses = txns.filter(transaction_type='EXPENSE').exclude(description__startswith='عجز افتتاحي في الشيفت').aggregate(s=Sum('amount'))['s'] or Decimal('0.00')
    withdrawals = txns.filter(transaction_type='WITHDRAWAL').aggregate(s=Sum('amount'))['s'] or Decimal('0.00')
    supplier_pay = txns.filter(transaction_type='SUPPLIER_PAYMENT').aggregate(s=Sum('amount'))['s'] or Decimal('0.00')
    start_bal = shift.start_balance or Decimal('0.00')
    expected = start_bal + cash_sales + cash_in - refunds - expenses - withdrawals - supplier_pay

    return render(request, 'financial/shift_x_report.html', {
        'title': f'تقرير X — شيفت #{shift.id}',
        'shift': shift,
        'now': end_t,
        'start_bal': start_bal,
        'cash_sales': cash_sales,
        'cash_in': cash_in,
        'refunds': refunds,
        'expenses': expenses,
        'withdrawals': withdrawals,
        'supplier_pay': supplier_pay,
        'expected': expected,
        'print_mode': request.GET.get('print') == '1',
    })


@login_required
@require_granular_action('financial', 'reports', 'financial', 'view')
@require_profit_view
def profitability_view(request):
    """Profitability by product/category for a period (Phase 9.3)."""
    from .reports import profitability
    group = request.GET.get('group', 'product')
    if group not in ('product', 'category'):
        group = 'product'
    date_from = _parse_date(request.GET.get('date_from'))
    date_to = _parse_date(request.GET.get('date_to'))
    rows, totals = profitability(date_from, date_to, group)
    if request.GET.get('export') == 'csv':
        from .reports import csv_response
        header = ['الصنف/القسم', 'الكمية', 'المبيعات', 'التكلفة', 'الربح', 'هامش %']
        data = [[r['name'], r['qty'], r['revenue'], r['cogs'], r['profit'], round(r['margin'], 1)] for r in rows]
        return csv_response('profitability', header, data)
    return render(request, 'financial/profitability.html', {
        'title': 'تقرير الربحية', 'rows': rows, 'totals': totals, 'group': group,
        'date_from': request.GET.get('date_from', ''), 'date_to': request.GET.get('date_to', ''),
        'print_mode': request.GET.get('print') == '1',
    })


@login_required
@require_permission('financial', 'view')
def system_health(request):
    """Operational integrity dashboard (Phase 9.9): live invariant checks."""
    from decimal import Decimal
    from django.db.models import Sum
    from products.models import WarehouseStock, StockBatch
    from financial.models import JournalLine
    from crm.models import Customer

    # 1) Stock invariant: Σ batches == warehouse stock
    stock_bad = 0
    for ws in WarehouseStock.objects.all():
        bsum = StockBatch.objects.filter(product=ws.product, warehouse=ws.warehouse).aggregate(s=Sum('current_quantity'))['s'] or Decimal('0')
        if abs(Decimal(str(bsum)) - (ws.quantity or Decimal('0'))) > Decimal('0.01'):
            stock_bad += 1

    # 2) Journal balanced
    agg = JournalLine.objects.aggregate(d=Sum('debit'), c=Sum('credit'))
    jd = agg['d'] or Decimal('0'); jc = agg['c'] or Decimal('0')
    journal_ok = abs(jd - jc) < Decimal('0.01')

    # 3) Customer statement closing == get_balance
    from crm.statements import build_customer_statement
    stmt_bad = 0
    for c in Customer.objects.all():
        try:
            if abs(build_customer_statement(c)['closing'] - c.get_balance()) > Decimal('0.01'):
                stmt_bad += 1
        except Exception:
            stmt_bad += 1

    checks = [
        {'name': 'تطابق المخزون (الدفعات = رصيد المخزن)', 'ok': stock_bad == 0, 'detail': f'{stock_bad} صنف غير متطابق'},
        {'name': 'توازن دفتر اليومية (مدين = دائن)', 'ok': journal_ok, 'detail': f'مدين {jd} / دائن {jc}'},
        {'name': 'تطابق كشوف حسابات العملاء', 'ok': stmt_bad == 0, 'detail': f'{stmt_bad} عميل غير متطابق'},
    ]
    return render(request, 'financial/system_health.html', {
        'title': 'صحة النظام', 'checks': checks, 'all_ok': all(c['ok'] for c in checks),
    })


@login_required
@require_granular_action('financial', 'reports', 'financial', 'view')
def vat_report_view(request):
    """VAT report (Phase 4.8)."""
    from .reports import vat_report
    date_from = _parse_date(request.GET.get('date_from'))
    date_to = _parse_date(request.GET.get('date_to'))
    data = vat_report(date_from, date_to)
    return render(request, 'financial/vat_report.html', {
        'title': 'تقرير ضريبة القيمة المضافة', 'v': data,
        'date_from': request.GET.get('date_from', ''), 'date_to': request.GET.get('date_to', ''),
        'print_mode': request.GET.get('print') == '1',
    })


@login_required
@require_granular_action('financial', 'reports', 'financial', 'view')
@require_profit_view
def sales_analytics_view(request):
    """Sales analytics dashboard (Phase 9.6)."""
    from .reports import sales_analytics
    date_from = _parse_date(request.GET.get('date_from'))
    date_to = _parse_date(request.GET.get('date_to'))
    data = sales_analytics(date_from, date_to)
    return render(request, 'financial/sales_analytics.html', {
        'title': 'تحليلات المبيعات', 'a': data,
        'date_from': request.GET.get('date_from', ''), 'date_to': request.GET.get('date_to', ''),
    })


@login_required
@require_granular_action('financial', 'reports', 'financial', 'view')
def financial_position_view(request):
    """Consolidated financial position (Phase 9.4)."""
    from .reports import financial_position
    date_from = _parse_date(request.GET.get('date_from'))
    date_to = _parse_date(request.GET.get('date_to'))
    data = financial_position(date_from, date_to)
    return render(request, 'financial/financial_position.html', {
        'title': 'المركز المالي', 'p': data,
        'date_from': request.GET.get('date_from', ''), 'date_to': request.GET.get('date_to', ''),
        'print_mode': request.GET.get('print') == '1',
    })
