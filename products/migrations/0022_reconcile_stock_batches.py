"""
Phase 2.1 data repair: reconcile StockBatch totals with WarehouseStock.

WarehouseStock.quantity is treated as the authoritative physical count (it is what
availability checks and the cached Product total rely on). For every divergent
(product, warehouse) pair we adjust batches so that
    Σ StockBatch.current_quantity == WarehouseStock.quantity
After this runs, the inventory service keeps them locked together forever.
"""
from decimal import Decimal
from django.db import migrations
from django.db.models import Sum


def reconcile(apps, schema_editor):
    Product = apps.get_model('products', 'Product')
    Warehouse = apps.get_model('products', 'Warehouse')
    WarehouseStock = apps.get_model('products', 'WarehouseStock')
    StockBatch = apps.get_model('products', 'StockBatch')

    fixed = 0
    for ws in WarehouseStock.objects.all():
        target = ws.quantity or Decimal('0.00')
        batches = list(
            StockBatch.objects.filter(product_id=ws.product_id, warehouse_id=ws.warehouse_id)
            .order_by('-created_at')
        )
        batch_sum = sum((b.current_quantity for b in batches), Decimal('0.00'))
        delta = Decimal(str(target)) - batch_sum
        if abs(delta) <= Decimal('0.001'):
            continue

        if delta > 0:
            # shortfall: add a synthetic batch at product cost
            product = Product.objects.filter(pk=ws.product_id).first()
            cost = (product.cost_price if product else Decimal('0.00')) or Decimal('0.00')
            StockBatch.objects.create(
                product_id=ws.product_id,
                warehouse_id=ws.warehouse_id,
                purchase_price=cost,
                initial_quantity=delta,
                current_quantity=delta,
                batch_number='RECONCILE',
            )
        else:
            # surplus: trim newest batches down until they match
            to_trim = -delta
            for b in batches:
                if to_trim <= 0:
                    break
                trim = min(b.current_quantity, to_trim)
                b.current_quantity -= trim
                b.save(update_fields=['current_quantity'])
                to_trim -= trim
        fixed += 1

    if fixed:
        print(f"  Reconciled {fixed} product/warehouse stock-batch pairs.")


def noop_reverse(apps, schema_editor):
    # Reconciliation is not reversible (it repairs corrupted data).
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('products', '0021_market_features_phase1'),
    ]

    operations = [
        migrations.RunPython(reconcile, noop_reverse),
    ]
