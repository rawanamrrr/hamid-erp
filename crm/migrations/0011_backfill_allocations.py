"""Backfill payment allocations FIFO for existing customer receipts (Phase 8.2).

Processes each customer's 'payment' receipts in chronological order, applying every one
to that customer's oldest open invoices first.
"""
from decimal import Decimal
from django.db import migrations
from django.db.models import Sum


def backfill(apps, schema_editor):
    Customer = apps.get_model('crm', 'Customer')
    CustomerPayment = apps.get_model('crm', 'CustomerPayment')
    PaymentAllocation = apps.get_model('crm', 'PaymentAllocation')
    Order = apps.get_model('sales', 'Order')

    def invoice_debt(o):
        debt = Decimal(str(o.total_amount or 0)) - Decimal(str(o.received_amount or 0))
        return debt if debt > 0 else Decimal('0.00')

    n = 0
    for cust in Customer.objects.all():
        # Outstanding per order, mutated as we allocate.
        orders = list(
            Order.objects.filter(customer=cust, items__isnull=False)
            .exclude(status='void').distinct().order_by('created_at')
        )
        outstanding = {o.id: invoice_debt(o) for o in orders}

        payments = (CustomerPayment.objects
                    .filter(customer=cust, transaction_type='payment')
                    .exclude(payment_method__in=['return_credit', 'return_cash_payout'])
                    .order_by('created_at', 'id'))
        for p in payments:
            remaining = Decimal(str(p.amount or 0))
            if remaining <= 0:
                continue
            for o in orders:
                if remaining <= 0:
                    break
                out = outstanding.get(o.id, Decimal('0.00'))
                if out <= 0:
                    continue
                use = min(out, remaining)
                PaymentAllocation.objects.create(payment_id=p.id, order_id=o.id, amount=use)
                outstanding[o.id] = out - use
                remaining -= use
                n += 1
    if n:
        print(f"  Backfilled {n} payment allocations.")


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):
    dependencies = [
        ('crm', '0010_paymentallocation'),
        ('sales', '0026_backfill_return_numbers'),
    ]
    operations = [migrations.RunPython(backfill, noop)]
