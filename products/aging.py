"""
Accounts-payable (supplier) aging (Phase 7.4) and inventory valuation/expiry helpers.

Supplier aging mirrors the customer AR aging: confirmed purchase invoices are debts we
owe (aged by invoice date), with direct invoice payments and later supplier payment
vouchers allocated to the oldest invoices first (FIFO).
"""
from collections import deque
from decimal import Decimal


def supplier_aging(supplier, as_of=None):
    from django.utils import timezone
    if as_of is None:
        as_of = timezone.localdate()

    # Open invoice debts (net - paid_on_invoice), oldest first.
    debits = deque()
    for inv in supplier.invoices.filter(status='CONFIRMED').order_by('created_at'):
        net = Decimal(str(inv.net_amount or 0))
        paid = Decimal(str(inv.paid_amount or 0))
        owed = net - paid
        if owed > 0:
            debits.append([inv.created_at.date(), owed])

    # Opening balance as an old debt.
    opening = Decimal(str(supplier.opening_balance or '0.00'))
    if opening > 0:
        base = supplier.created_at.date() if getattr(supplier, 'created_at', None) else as_of
        debits.appendleft([base, opening])

    # Apply payment vouchers + credit returns FIFO to oldest debts.
    credits = Decimal('0.00')
    for pay in supplier.payments.all():
        credits += Decimal(str(pay.amount or 0))
    for ret in supplier.returns.filter(refund_method='debt'):
        credits += Decimal(str(ret.total_amount or 0))

    while credits > 0 and debits:
        head = debits[0]
        use = min(head[1], credits)
        head[1] -= use
        credits -= use
        if head[1] <= 0:
            debits.popleft()

    buckets = {'current': Decimal('0.00'), 'd30': Decimal('0.00'),
               'd60': Decimal('0.00'), 'd90': Decimal('0.00')}
    oldest = 0
    for d, rem in debits:
        if rem <= 0:
            continue
        age = (as_of - d).days
        oldest = max(oldest, age)
        if age <= 30:
            buckets['current'] += rem
        elif age <= 60:
            buckets['d30'] += rem
        elif age <= 90:
            buckets['d60'] += rem
        else:
            buckets['d90'] += rem
    total = sum(buckets.values(), Decimal('0.00'))
    return {**buckets, 'total': total, 'oldest_days': oldest}


def ap_aging(as_of=None, min_balance=Decimal('0.01')):
    from .models import Supplier
    rows = []
    totals = {'current': Decimal('0.00'), 'd30': Decimal('0.00'),
              'd60': Decimal('0.00'), 'd90': Decimal('0.00'), 'total': Decimal('0.00')}
    for s in Supplier.objects.all():
        ag = supplier_aging(s, as_of)
        if ag['total'] < min_balance:
            continue
        rows.append({'supplier': s, **ag})
        for k in totals:
            totals[k] += ag[k]
    rows.sort(key=lambda r: r['oldest_days'], reverse=True)
    return rows, totals
