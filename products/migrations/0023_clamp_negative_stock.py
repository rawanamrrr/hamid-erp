"""
Clamp negative WarehouseStock to zero.

Negative physical stock is impossible and is a residue of the old double-deduction
bug. After clamping, the (already 0) batch totals match the (now 0) warehouse stock,
restoring the inventory invariant for these rows.
"""
from decimal import Decimal
from django.db import migrations


def clamp_negatives(apps, schema_editor):
    WarehouseStock = apps.get_model('products', 'WarehouseStock')
    Product = apps.get_model('products', 'Product')

    n = 0
    for ws in WarehouseStock.objects.filter(quantity__lt=0):
        ws.quantity = Decimal('0.00')
        ws.save(update_fields=['quantity'])
        n += 1
    for p in Product.objects.filter(stock_quantity__lt=0):
        p.stock_quantity = Decimal('0.00')
        p.save(update_fields=['stock_quantity'])
    if n:
        print(f"  Clamped {n} negative warehouse-stock rows to zero.")


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('products', '0022_reconcile_stock_batches'),
    ]

    operations = [
        migrations.RunPython(clamp_negatives, noop_reverse),
    ]
