"""
Backfill Transaction.order from legacy description strings (one time).

Historically, sale/refund transactions only recorded the order id inside the free-text
description ("مبيعات أوردر رقم #123"). This parses that id ONCE into the new FK so all
future reversals/look-ups use the relation instead of substring matching (which could
match #123 against #1230, corrupting unrelated orders).
"""
import re
from django.db import migrations

# Matches the order id after a '#': "...#123" / "...# 123"
_ID_RE = re.compile(r'#\s*(\d+)')


def backfill(apps, schema_editor):
    Transaction = apps.get_model('financial', 'Transaction')
    Order = apps.get_model('sales', 'Order')

    valid_ids = set(Order.objects.values_list('id', flat=True))
    linked = 0
    # Only sale/refund transactions reference an order in their description.
    qs = Transaction.objects.filter(
        transaction_type__in=['SALE', 'REFUND'], order__isnull=True
    ).exclude(description='')
    for t in qs.iterator():
        m = _ID_RE.search(t.description or '')
        if not m:
            continue
        oid = int(m.group(1))
        if oid in valid_ids:
            t.order_id = oid
            t.save(update_fields=['order'])
            linked += 1
    if linked:
        print(f"  Backfilled order link on {linked} transactions.")


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('financial', '0020_transaction_customer_payment_transaction_expense_and_more'),
        ('sales', '0020_remove_cashsettlement_master_store_and_more'),
    ]

    operations = [
        migrations.RunPython(backfill, noop_reverse),
    ]
