"""
Customer payment allocation (Phase 8.2).

When a customer pays down their account, the receipt is applied to their oldest open
invoices first (FIFO) and recorded as PaymentAllocation rows. Each invoice can then
report exactly how much of it is still outstanding.
"""
from decimal import Decimal

from django.db import transaction as db_transaction


def order_invoice_debt(order):
    """The credit portion of an invoice at sale time = total - money received then."""
    total = Decimal(str(order.total_amount or 0))
    received = Decimal(str(order.received_amount or 0))
    debt = total - received
    return debt if debt > 0 else Decimal('0.00')


def order_allocated(order):
    from django.db.models import Sum
    return order.payment_allocations.aggregate(s=Sum('amount'))['s'] or Decimal('0.00')


def order_outstanding(order):
    """How much of this invoice's credit is still unpaid (after later receipts)."""
    out = order_invoice_debt(order) - order_allocated(order)
    return out if out > 0 else Decimal('0.00')


def allocate_payment_fifo(payment):
    """Apply a 'payment'-type CustomerPayment across the customer's open invoices,
    oldest first. Returns the unallocated remainder (customer prepaid / has credit)."""
    from .models import PaymentAllocation

    if payment.transaction_type != 'payment':
        return Decimal('0.00')
    # Skip the internal return-ledger entries (they aren't cash receipts to allocate).
    if payment.payment_method in ('return_credit', 'return_cash_payout'):
        return Decimal(str(payment.amount or 0))

    remaining = Decimal(str(payment.amount or 0))
    if remaining <= 0 or not payment.customer_id:
        return remaining

    with db_transaction.atomic():
        already = order_allocated_for_payment(payment)
        remaining -= already

        open_orders = (
            payment.customer.order_set
            .filter(items__isnull=False)
            .exclude(status='void')
            .distinct()
            .order_by('created_at')
        )
        for o in open_orders:
            if remaining <= 0:
                break
            out = order_outstanding(o)
            if out <= 0:
                continue
            use = min(out, remaining)
            PaymentAllocation.objects.create(payment=payment, order=o, amount=use)
            remaining -= use

    return remaining


def order_allocated_for_payment(payment):
    from django.db.models import Sum
    return payment.allocations.aggregate(s=Sum('amount'))['s'] or Decimal('0.00')
