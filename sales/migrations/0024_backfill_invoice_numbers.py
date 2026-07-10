"""
Backfill gap-free invoice numbers for existing orders (Phase 6.1).

Numbers are assigned in id order, grouped by the order's creation year, and the
DocumentSequence counters are advanced so new invoices continue without collisions.
"""
from django.db import migrations


def backfill(apps, schema_editor):
    Order = apps.get_model('sales', 'Order')
    DocumentSequence = apps.get_model('sales', 'DocumentSequence')

    per_year = {}
    qs = Order.objects.filter(invoice_number__isnull=True).order_by('id')
    for order in qs.iterator():
        year = order.created_at.year if order.created_at else 2025
        n = per_year.get(year, 0) + 1
        per_year[year] = n
        order.invoice_number = f"INV-{year}-{n:05d}"
        order.save(update_fields=['invoice_number'])

    # Advance the sequence counters so future numbers don't collide with backfilled ones.
    for year, last in per_year.items():
        seq, _ = DocumentSequence.objects.get_or_create(
            doc_type='INV', year=year, defaults={'last_number': 0})
        if seq.last_number < last:
            seq.last_number = last
            seq.save(update_fields=['last_number'])

    if per_year:
        total = sum(per_year.values())
        print(f"  Backfilled {total} invoice numbers across {len(per_year)} year(s).")


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('sales', '0023_order_invoice_number_documentsequence'),
    ]

    operations = [
        migrations.RunPython(backfill, noop_reverse),
    ]
