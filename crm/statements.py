"""
Customer account statement / كشف حساب (Phase 4.3).

Builds a chronological subledger for a customer: opening balance, then every document
(sales invoices, payments at sale, debt-collection receipts, manual adjustments, return
credits) with a running balance. The closing balance always equals Customer.get_balance().

A positive balance means the customer owes the shop (مدين); negative means the shop owes
the customer / they have credit (دائن).
"""
from decimal import Decimal


def build_customer_statement(customer, date_from=None, date_to=None):
    opening = Decimal(str(customer.opening_balance or '0.00'))

    movements = []  # {dt, date, doc, ref, debit, credit}

    # Sales invoices (real, non-void) — the full invoice is a debit; cash taken at the
    # time of sale is an immediate credit.
    for o in customer.order_set.filter(items__isnull=False).exclude(status='void').distinct():
        total = Decimal(str(o.total_amount or 0))
        movements.append({
            'dt': o.created_at, 'doc': 'فاتورة مبيعات', 'ref': f"#{o.id}",
            'debit': total, 'credit': Decimal('0.00'),
        })
        received = Decimal(str(o.received_amount or 0))
        if received > 0:
            movements.append({
                'dt': o.created_at, 'doc': 'دفعة مع الفاتورة', 'ref': f"#{o.id}",
                'debit': Decimal('0.00'), 'credit': received,
            })

    # CustomerPayment ledger: payment = credit (reduces debt), debt = debit (manual).
    for p in customer.payments_log.all():
        amt = Decimal(str(p.amount or 0))
        if p.transaction_type == 'payment':
            label = 'سداد / رصيد'
            if p.payment_method == 'return_credit':
                label = 'رصيد مرتجع'
            movements.append({'dt': p.created_at, 'doc': label, 'ref': p.notes or '',
                              'debit': Decimal('0.00'), 'credit': amt})
        else:  # debt
            movements.append({'dt': p.created_at, 'doc': 'مديونية يدوية', 'ref': p.notes or '',
                              'debit': amt, 'credit': Decimal('0.00')})

    movements.sort(key=lambda m: m['dt'])

    # Brought-forward: collapse everything before date_from into the opening balance.
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
