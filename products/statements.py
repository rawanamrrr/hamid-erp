"""
Supplier account statement / كشف حساب مورد (Phase 4.3).

Chronological payables subledger for a supplier: opening balance, confirmed purchase
invoices (increase what we owe), direct payments on invoices, supplier returns credited
to the account, and payment vouchers (decrease what we owe), with a running balance.

The closing balance equals Supplier.current_balance — a positive value is what the shop
still owes the supplier.
"""
from decimal import Decimal


def build_supplier_statement(supplier, date_from=None, date_to=None):
    opening = Decimal(str(supplier.opening_balance or '0.00'))

    movements = []  # {dt, doc, ref, debit(increase owed), credit(decrease owed)}

    # Confirmed purchase invoices increase the payable; any amount paid directly on the
    # invoice reduces it at the same moment.
    for inv in supplier.invoices.filter(status='CONFIRMED'):
        net = Decimal(str(inv.net_amount or 0))
        movements.append({'dt': inv.created_at, 'doc': 'فاتورة مشتريات', 'ref': f"#{inv.id}",
                          'debit': net, 'credit': Decimal('0.00')})
        paid = Decimal(str(inv.paid_amount or 0))
        if paid > 0:
            movements.append({'dt': inv.created_at, 'doc': 'مدفوع مع الفاتورة', 'ref': f"#{inv.id}",
                              'debit': Decimal('0.00'), 'credit': paid})

    # Supplier returns credited to the account (refund_method='debt') reduce what we owe.
    for ret in supplier.returns.filter(refund_method='debt'):
        movements.append({'dt': ret.created_at, 'doc': 'مرتجع مشتريات (رصيد)', 'ref': f"#{ret.id}",
                          'debit': Decimal('0.00'), 'credit': Decimal(str(ret.total_amount or 0))})

    # Payment vouchers.
    for pay in supplier.payments.all():
        ref = pay.voucher_number or (pay.notes or '')
        movements.append({'dt': pay.date, 'doc': 'سند صرف', 'ref': ref,
                          'debit': Decimal('0.00'), 'credit': Decimal(str(pay.amount or 0))})

    movements.sort(key=lambda m: m['dt'])

    brought_forward = opening
    in_range = []
    for m in movements:
        d = m['dt'].date()
        if date_from and d < date_from:
            brought_forward += m['debit'] - m['credit']
            continue
        if date_to and d > date_to:
            continue
        in_range.append(m)

    rows = []
    running = brought_forward
    for m in in_range:
        running += m['debit'] - m['credit']
        rows.append({**m, 'date': m['dt'].date(), 'balance': running})

    return {
        'opening': brought_forward,
        'rows': rows,
        'closing': running,
        'total_debit': sum((m['debit'] for m in in_range), Decimal('0.00')),
        'total_credit': sum((m['credit'] for m in in_range), Decimal('0.00')),
    }
