"""
Accounts-receivable aging (Phase 8.1).

A customer's balance is built from invoices (debits) and payments (credits) that are not
tied to specific invoices. To age the outstanding debt we replay the customer's history
chronologically and allocate every credit to the OLDEST open debit first (FIFO). What is
left unpaid is then bucketed by the age of the debit it belongs to.

By construction the buckets sum to Customer.get_balance() whenever that balance is
positive (the customer owes the shop).
"""
from collections import deque
from decimal import Decimal


def customer_aging(customer, as_of=None):
    """Return aged outstanding debt for one customer:
    {'current', 'd30', 'd60', 'd90', 'total', 'oldest_days'}.
    `current` = 0–30 days, d30 = 31–60, d60 = 61–90, d90 = 90+.
    """
    from django.utils import timezone
    if as_of is None:
        as_of = timezone.localdate()

    events = []  # (date, amount)  amount>0 debit (owes more), <0 credit (paid)

    opening = Decimal(str(customer.opening_balance or '0.00'))
    base_date = customer.created_at.date() if getattr(customer, 'created_at', None) else as_of
    if opening != 0:
        events.append((base_date, opening))

    for o in customer.order_set.filter(items__isnull=False).exclude(status='void').distinct():
        d = o.created_at.date()
        total = Decimal(str(o.total_amount or 0))
        received = Decimal(str(o.received_amount or 0))
        if total:
            events.append((d, total))
        if received:
            events.append((d, -received))

    for p in customer.payments_log.all():
        d = p.created_at.date()
        amt = Decimal(str(p.amount or 0))
        if p.transaction_type == 'payment':
            events.append((d, -amt))
        else:  # debt
            events.append((d, amt))

    events.sort(key=lambda e: e[0])

    # FIFO: open debits queue of [date, remaining]; credits consume oldest first.
    open_debits = deque()
    credit_carry = Decimal('0.00')  # unallocated credit (customer overpaid / has credit)
    for d, amt in events:
        if amt > 0:
            # apply any carried credit first
            if credit_carry > 0:
                use = min(credit_carry, amt)
                amt -= use
                credit_carry -= use
            if amt > 0:
                open_debits.append([d, amt])
        else:
            credit = -amt
            while credit > 0 and open_debits:
                head = open_debits[0]
                use = min(head[1], credit)
                head[1] -= use
                credit -= use
                if head[1] <= 0:
                    open_debits.popleft()
            if credit > 0:
                credit_carry += credit

    buckets = {'current': Decimal('0.00'), 'd30': Decimal('0.00'),
               'd60': Decimal('0.00'), 'd90': Decimal('0.00')}
    oldest_days = 0
    for d, remaining in open_debits:
        if remaining <= 0:
            continue
        age = (as_of - d).days
        oldest_days = max(oldest_days, age)
        if age <= 30:
            buckets['current'] += remaining
        elif age <= 60:
            buckets['d30'] += remaining
        elif age <= 90:
            buckets['d60'] += remaining
        else:
            buckets['d90'] += remaining

    total = sum(buckets.values(), Decimal('0.00'))
    return {**buckets, 'total': total, 'oldest_days': oldest_days}


def ar_aging(as_of=None, min_balance=Decimal('0.01')):
    """Aging for every customer who owes money. Returns (rows, totals)."""
    from .models import Customer
    rows = []
    totals = {'current': Decimal('0.00'), 'd30': Decimal('0.00'),
              'd60': Decimal('0.00'), 'd90': Decimal('0.00'), 'total': Decimal('0.00')}
    for c in Customer.objects.all():
        bal = c.get_balance()
        if bal is None or bal < min_balance:
            continue
        ag = customer_aging(c, as_of)
        if ag['total'] < min_balance:
            continue
        rows.append({'customer': c, **ag})
        for k in totals:
            totals[k] += ag[k]
    rows.sort(key=lambda r: r['oldest_days'], reverse=True)
    return rows, totals
