"""
Financial statements derived purely from the general journal (Phase 4.6).

These read JournalLine only — never Account.balance — so they are true double-entry
reports. A correct journal guarantees the trial balance's debit and credit columns are
equal.
"""
from decimal import Decimal

from django.db.models import Sum
from django.db.models.functions import Coalesce

from .models import Account, JournalLine

ZERO = Decimal('0.00')


def csv_response(filename, header, rows):
    """Build a UTF-8 (BOM) CSV download — opens cleanly in Excel incl. Arabic (Phase 9.8)."""
    import csv
    from django.http import HttpResponse
    resp = HttpResponse(content_type='text/csv; charset=utf-8-sig')
    resp['Content-Disposition'] = f'attachment; filename="{filename}.csv"'
    resp.write('﻿')  # BOM so Excel detects UTF-8
    writer = csv.writer(resp)
    writer.writerow(header)
    for r in rows:
        writer.writerow(r)
    return resp

# Account types whose natural balance is a debit (assets/expenses) vs credit.
DEBIT_NATURE = {'ASSET', 'EXPENSE', 'CASH_DRAWER', 'SAFE', 'BANK', 'VODAFONE_CASH', 'INSTAPAY'}


def _lines(date_from=None, date_to=None):
    qs = JournalLine.objects.select_related('account', 'entry')
    if date_from:
        qs = qs.filter(entry__posted_at__date__gte=date_from)
    if date_to:
        qs = qs.filter(entry__posted_at__date__lte=date_to)
    return qs


def account_balances(date_from=None, date_to=None):
    """Return per-account totals: list of dicts with debit, credit and signed balance
    (positive = debit-side). Only accounts with movement are included."""
    rows = (
        _lines(date_from, date_to)
        .values('account_id', 'account__name', 'account__code', 'account__account_type')
        .annotate(
            debit=Coalesce(Sum('debit'), ZERO),
            credit=Coalesce(Sum('credit'), ZERO),
        )
        .order_by('account__code', 'account__name')
    )
    out = []
    for r in rows:
        debit = r['debit'] or ZERO
        credit = r['credit'] or ZERO
        out.append({
            'id': r['account_id'],
            'code': r['account__code'] or '',
            'name': r['account__name'],
            'type': r['account__account_type'],
            'debit': debit,
            'credit': credit,
            'balance': debit - credit,  # signed: + = net debit
        })
    return out


def trial_balance(date_from=None, date_to=None):
    """Trial balance: each account's net shown in the debit or credit column, with
    column totals that must be equal."""
    rows = account_balances(date_from, date_to)
    tb = []
    total_debit = ZERO
    total_credit = ZERO
    for r in rows:
        bal = r['balance']
        debit_col = bal if bal > 0 else ZERO
        credit_col = -bal if bal < 0 else ZERO
        total_debit += debit_col
        total_credit += credit_col
        tb.append({**r, 'debit_col': debit_col, 'credit_col': credit_col})
    return {
        'rows': tb,
        'total_debit': total_debit,
        'total_credit': total_credit,
        'balanced': (total_debit - total_credit).copy_abs() < Decimal('0.01'),
    }


def financial_position(date_from=None, date_to=None):
    """Consolidated position (Phase 9.4): cash/bank balances, total AR, total AP,
    inventory value, and expenses by category for the period. Read-only."""
    from django.db.models import Count
    from .models import Account
    from crm.models import Customer
    from products.models import Supplier, StockBatch
    from sales.models import Expense

    # Cash & bank operational accounts.
    cash_types = ['CASH_DRAWER', 'SAFE', 'BANK', 'VODAFONE_CASH', 'INSTAPAY']
    cash_rows = list(Account.objects.filter(account_type__in=cash_types)
                     .values('name', 'account_type', 'balance').order_by('account_type'))
    cash_total = sum((Decimal(str(r['balance'] or 0)) for r in cash_rows), ZERO)

    # Receivables (customers who owe us) and payables (suppliers we owe).
    ar = ZERO
    for c in Customer.objects.all():
        bal = c.get_balance()
        if bal and bal > 0:
            ar += bal
    ap = ZERO
    for s in Supplier.objects.all():
        bal = getattr(s, 'outstanding_balance', ZERO) or ZERO
        if bal > 0:
            ap += bal

    # Inventory value at cost.
    inv_value = ZERO
    for b in StockBatch.objects.filter(current_quantity__gt=0).only('current_quantity', 'purchase_price'):
        inv_value += (b.current_quantity or ZERO) * (b.purchase_price or ZERO)

    # Expenses by category for the period.
    exp = Expense.objects.all()
    if date_from:
        exp = exp.filter(date__gte=date_from)
    if date_to:
        exp = exp.filter(date__lte=date_to)
    exp_rows = list(exp.values('category').annotate(total=Coalesce(Sum('amount'), ZERO), n=Count('id')).order_by('-total'))
    cat_labels = dict(Expense.EXPENSE_CATEGORIES)
    for r in exp_rows:
        r['label'] = cat_labels.get(r['category'], r['category'] or 'أخرى')
    exp_total = sum((r['total'] for r in exp_rows), ZERO)

    return {
        'cash_rows': cash_rows, 'cash_total': cash_total,
        'ar': ar, 'ap': ap, 'inventory_value': inv_value,
        'net_worth_est': cash_total + ar + inv_value - ap,
        'exp_rows': exp_rows, 'exp_total': exp_total,
    }


def sales_analytics(date_from=None, date_to=None):
    """Sales analytics (Phase 9.6): best sellers, profit leaders, and breakdowns by hour,
    weekday, cashier and payment method. Read-only."""
    from django.db.models import Count, F, DecimalField, ExpressionWrapper
    from django.db.models.functions import ExtractHour, ExtractWeekDay
    from sales.models import Order, OrderItem

    orders = Order.objects.active()
    if date_from:
        orders = orders.filter(created_at__date__gte=date_from)
    if date_to:
        orders = orders.filter(created_at__date__lte=date_to)

    summary = orders.aggregate(
        invoices=Count('id'),
        total=Coalesce(Sum('total_amount'), ZERO),
    )
    inv = summary['invoices'] or 0
    avg_basket = (summary['total'] / inv) if inv else ZERO

    rev = ExpressionWrapper(F('quantity') * F('price'), output_field=DecimalField(max_digits=14, decimal_places=2))
    items = OrderItem.objects.filter(order__in=orders)

    best_qty = list(items.values('product__name').annotate(
        qty=Coalesce(Sum('quantity'), ZERO), revenue=Coalesce(Sum(rev), ZERO)
    ).order_by('-qty')[:10])
    top_rev = list(items.values('product__name').annotate(
        qty=Coalesce(Sum('quantity'), ZERO), revenue=Coalesce(Sum(rev), ZERO)
    ).order_by('-revenue')[:10])

    # Profit leaders (COGS-based, in Python to use OrderItem.cogs property).
    prof = {}
    for it in items.select_related('product'):
        name = it.product.name if it.product else 'صنف محذوف'
        prof[name] = prof.get(name, ZERO) + it.gross_profit
    top_profit = sorted(({'name': k, 'profit': v} for k, v in prof.items()), key=lambda r: r['profit'], reverse=True)[:10]

    by_hour = {h: ZERO for h in range(24)}
    for r in orders.annotate(h=ExtractHour('created_at')).values('h').annotate(t=Coalesce(Sum('total_amount'), ZERO)):
        by_hour[r['h']] = r['t']
    hour_rows = [{'label': f'{h:02d}:00', 'total': by_hour[h]} for h in range(24)]
    hour_max = max(by_hour.values()) or ZERO

    wd_names = {1: 'الأحد', 2: 'الإثنين', 3: 'الثلاثاء', 4: 'الأربعاء', 5: 'الخميس', 6: 'الجمعة', 7: 'السبت'}
    by_wd = {i: ZERO for i in range(1, 8)}
    for r in orders.annotate(w=ExtractWeekDay('created_at')).values('w').annotate(t=Coalesce(Sum('total_amount'), ZERO)):
        by_wd[r['w']] = r['t']
    wd_rows = [{'label': wd_names[i], 'total': by_wd[i]} for i in range(1, 8)]
    wd_max = max(by_wd.values()) or ZERO

    by_cashier = list(orders.values('user__username').annotate(
        invoices=Count('id'), total=Coalesce(Sum('total_amount'), ZERO)).order_by('-total')[:15])

    by_payment = orders.aggregate(
        cash=Coalesce(Sum('cash_paid'), ZERO), wallet=Coalesce(Sum('wallet_paid'), ZERO),
        instapay=Coalesce(Sum('instapay_paid'), ZERO), visa=Coalesce(Sum('visa_paid'), ZERO))

    return {
        'summary': summary, 'avg_basket': avg_basket,
        'best_qty': best_qty, 'top_rev': top_rev, 'top_profit': top_profit,
        'hour_rows': hour_rows, 'hour_max': hour_max,
        'wd_rows': wd_rows, 'wd_max': wd_max,
        'by_cashier': by_cashier, 'by_payment': by_payment,
    }


def vat_report(date_from=None, date_to=None):
    """VAT summary (Phase 4.8) — output VAT on sales (prices treated as VAT-inclusive at
    the configured rate) minus input VAT on confirmed purchases = net VAT payable.

    Touches no checkout logic; if the configured rate is 0 the report simply shows zeros.
    """
    from settings.models import SystemSetting
    from sales.models import Order, ReturnInvoice
    from products.models import PurchaseInvoiceItem

    s = SystemSetting.objects.first()
    rate = Decimal(str(s.vat_rate)) if (s and s.vat_rate) else ZERO

    orders = Order.objects.active()
    returns = ReturnInvoice.objects.all()
    pitems = PurchaseInvoiceItem.objects.filter(invoice__status='CONFIRMED')
    if date_from:
        orders = orders.filter(created_at__date__gte=date_from)
        returns = returns.filter(created_at__date__gte=date_from)
        pitems = pitems.filter(invoice__created_at__date__gte=date_from)
    if date_to:
        orders = orders.filter(created_at__date__lte=date_to)
        returns = returns.filter(created_at__date__lte=date_to)
        pitems = pitems.filter(invoice__created_at__date__lte=date_to)

    sales_gross = orders.aggregate(s=Coalesce(Sum('total_amount'), ZERO))['s']
    returns_gross = returns.aggregate(s=Coalesce(Sum('total_refund_amount'), ZERO))['s']
    net_sales = sales_gross - returns_gross

    # VAT-inclusive extraction: tax = gross * rate / (100 + rate)
    factor = (rate / (Decimal('100') + rate)) if rate > 0 else ZERO
    output_vat = (net_sales * factor).quantize(Decimal('0.01'))
    net_sales_ex = net_sales - output_vat

    input_vat = pitems.aggregate(s=Coalesce(Sum('tax_amount'), ZERO))['s']

    return {
        'rate': rate,
        'vat_number': s.vat_number if s else '',
        'sales_gross': sales_gross,
        'returns_gross': returns_gross,
        'net_sales': net_sales,
        'net_sales_ex': net_sales_ex,
        'output_vat': output_vat,
        'input_vat': input_vat,
        'net_payable': output_vat - input_vat,
    }


def profitability(date_from=None, date_to=None, group='product'):
    """Profitability by product or category over a period (Phase 9.3), COGS-based."""
    from django.db.models import Count
    from sales.models import Order, OrderItem

    orders = Order.objects.active()
    if date_from:
        orders = orders.filter(created_at__date__gte=date_from)
    if date_to:
        orders = orders.filter(created_at__date__lte=date_to)

    items = OrderItem.objects.filter(order__in=orders).select_related('product', 'product__category')
    buckets = {}
    for it in items:
        if group == 'category':
            key = it.product.category.name if it.product and it.product.category else 'بدون قسم'
        else:
            key = it.product.name if it.product else 'صنف محذوف'
        b = buckets.setdefault(key, {'name': key, 'qty': Decimal('0'), 'revenue': Decimal('0.00'), 'cogs': Decimal('0.00')})
        b['qty'] += Decimal(str(it.quantity or 0))
        b['revenue'] += Decimal(str(it.subtotal or 0))
        b['cogs'] += Decimal(str(it.cogs or 0))

    rows = []
    tot_rev = tot_cogs = Decimal('0.00')
    for b in buckets.values():
        profit = b['revenue'] - b['cogs']
        margin = (profit / b['revenue'] * 100) if b['revenue'] > 0 else Decimal('0')
        rows.append({**b, 'profit': profit, 'margin': margin})
        tot_rev += b['revenue']
        tot_cogs += b['cogs']
    rows.sort(key=lambda r: r['profit'], reverse=True)
    tot_profit = tot_rev - tot_cogs
    totals = {'revenue': tot_rev, 'cogs': tot_cogs, 'profit': tot_profit,
              'margin': (tot_profit / tot_rev * 100) if tot_rev > 0 else Decimal('0')}
    return rows, totals


def daily_summary(day):
    """One-day movement summary (Phase 9.1): sales, returns, receipts, expenses,
    withdrawals, supplier payments, and the net cash position — all for `day`."""
    from django.db.models import Count
    from sales.models import Order, ReturnInvoice
    from financial.models import Transaction
    from crm.models import CustomerPayment
    from products.models import SupplierPayment

    orders = Order.objects.active().filter(created_at__date=day)
    o = orders.aggregate(
        count=Count('id'),
        total=Coalesce(Sum('total_amount'), ZERO),
        cash=Coalesce(Sum('cash_paid'), ZERO),
        wallet=Coalesce(Sum('wallet_paid'), ZERO),
        instapay=Coalesce(Sum('instapay_paid'), ZERO),
        visa=Coalesce(Sum('visa_paid'), ZERO),
        received=Coalesce(Sum('received_amount'), ZERO),
    )
    credit_sales = (o['total'] or ZERO) - (o['received'] or ZERO)

    returns = ReturnInvoice.objects.filter(created_at__date=day)
    r = returns.aggregate(count=Count('id'), total=Coalesce(Sum('total_refund_amount'), ZERO))
    cash_refunds = returns.filter(refund_method='cash').aggregate(
        t=Coalesce(Sum('total_refund_amount'), ZERO))['t']

    def _txn_sum(ttype):
        return Transaction.objects.filter(transaction_type=ttype, created_at__date=day).aggregate(
            t=Coalesce(Sum('amount'), ZERO))['t']

    expenses = _txn_sum('EXPENSE')
    withdrawals = _txn_sum('WITHDRAWAL')
    other_income = _txn_sum('INCOME')

    # Genuine customer cash receipts (exclude the internal return ledger entries).
    receipts_qs = CustomerPayment.objects.filter(transaction_type='payment', created_at__date=day).exclude(
        payment_method__in=['return_credit', 'return_cash_payout'])
    receipts = receipts_qs.aggregate(count=Count('id'), total=Coalesce(Sum('amount'), ZERO))

    supplier_pay = SupplierPayment.objects.filter(date__date=day).aggregate(
        count=Count('id'), total=Coalesce(Sum('amount'), ZERO))

    cash_in = (o['cash'] or ZERO) + (receipts['total'] or ZERO) + other_income
    cash_out = (cash_refunds or ZERO) + expenses + withdrawals + (supplier_pay['total'] or ZERO)

    return {
        'day': day,
        'sales': o,
        'credit_sales': credit_sales,
        'returns': r,
        'cash_refunds': cash_refunds,
        'expenses': expenses,
        'withdrawals': withdrawals,
        'other_income': other_income,
        'receipts': receipts,
        'supplier_pay': supplier_pay,
        'cash_in': cash_in,
        'cash_out': cash_out,
        'net_cash': cash_in - cash_out,
    }


def income_statement(date_from=None, date_to=None):
    """Profit & loss from the journal, by nominal code."""
    rows = {r['code']: r for r in account_balances(date_from, date_to)}

    def credit_bal(code):
        r = rows.get(code)
        return (-r['balance']) if r else ZERO  # revenue is credit-natured

    def debit_bal(code):
        r = rows.get(code)
        return r['balance'] if r else ZERO

    sales_revenue = credit_bal('4100')
    sales_returns = debit_bal('4110')   # contra-revenue (natural debit)
    other_income = credit_bal('4200')
    cogs = debit_bal('5100')
    opex = debit_bal('5200')

    net_revenue = sales_revenue - sales_returns
    gross_profit = net_revenue - cogs
    net_profit = gross_profit + other_income - opex

    return {
        'sales_revenue': sales_revenue,
        'sales_returns': sales_returns,
        'net_revenue': net_revenue,
        'cogs': cogs,
        'gross_profit': gross_profit,
        'other_income': other_income,
        'opex': opex,
        'net_profit': net_profit,
    }
