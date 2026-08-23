import sys

from django.db.models.signals import post_save, post_migrate
from django.dispatch import receiver
from .models import Warehouse, WarehouseStock, Product, PurchaseOrder


def _safe_print(msg):
    """print() that never crashes the whole migrate run over an Arabic string. Windows
    defaults stdout/stderr to the system codepage (cp1252) for any process that isn't
    attached to a real console — the packaged desktop EXE (pos_launcher.py, no console
    window) hits this on EVERY first launch via create_default_warehouse below, which
    used to take down the entire startup migrate with an unhandled UnicodeEncodeError
    before the app ever got a chance to serve a single page. Re-encoding to the stream's
    OWN codec with errors='replace' (not utf-8) is what actually avoids the crash — the
    failure happens when the underlying stream tries to write the string in ITS codec,
    so degrading unencodable characters to '?' in that same codec is guaranteed safe;
    round-tripping through utf-8 first would still contain the same un-encodable
    characters and crash again on the real write.
    """
    try:
        print(msg)
    except UnicodeEncodeError:
        enc = getattr(sys.stdout, 'encoding', None) or 'ascii'
        try:
            print(str(msg).encode(enc, errors='replace').decode(enc, errors='replace'))
        except Exception:
            pass


@receiver(post_save, sender=WarehouseStock)
def update_product_global_stock(sender, instance, **kwargs):
    """
    Whenever a specific warehouse stock changes, update the main Product.stock_quantity
    to reflect the total sum across all warehouses.
    """
    instance.product.calculate_total_stock()


@receiver(post_save, sender=PurchaseOrder)
def generate_po_number(sender, instance, created, **kwargs):
    """
    Auto-generates PO number in format PO-{year}-{id:04d} after first save.
    """
    if created and not instance.po_number:
        from django.utils import timezone
        year = timezone.now().year
        po_number = f"PO-{year}-{instance.id:04d}"
        PurchaseOrder.objects.filter(pk=instance.pk).update(po_number=po_number)


def create_default_warehouse(sender, **kwargs):
    """
    Run after migrations.
    1. Check if any Warehouse exists. If not, create 'المخزن الرئيسي' (Main Warehouse).
    2. Check if Products have stock_quantity > 0 but NO WarehouseStock entries.
       If so, move that 'orphan' stock into the Main Warehouse.
    3. Seed default UnitOfMeasure records if none exist.
    """
    if sender.name == 'products':
        # 1. Create Default Warehouse if needed
        if not Warehouse.objects.exists():
            main_wh = Warehouse.objects.create(
                name="المخزن الرئيسي",
                address="الفرع الرئيسي",
                is_active=True,
                is_sales_point=True,
                warehouse_type='BOTH'
            )
            _safe_print("Created Default Warehouse: المخزن الرئيسي")
        else:
            main_wh = Warehouse.objects.first()

        # 2. Migrate existing global stock to WarehouseStock
        products_with_stock = Product.objects.filter(stock_quantity__gt=0)
        count = 0
        for product in products_with_stock:
            if not WarehouseStock.objects.filter(product=product).exists():
                WarehouseStock.objects.create(
                    product=product,
                    warehouse=main_wh,
                    quantity=product.stock_quantity
                )
                count += 1
        if count > 0:
            _safe_print(f"Migrated stock for {count} products to '{main_wh.name}'")

        # 3. Seed default UnitOfMeasure
        from .models import UnitOfMeasure
        if not UnitOfMeasure.objects.exists():
            defaults = [
                ('متر', 'MTR'),
                ('ياردة', 'YRD'),
                ('رول / توب', 'ROLL'),
                ('قطعة', 'PCS'),
                ('كيلوجرام', 'KG'),
            ]
            for name, abbr in defaults:
                UnitOfMeasure.objects.get_or_create(name=name, defaults={'abbreviation': abbr})
            _safe_print("Seeded default UnitOfMeasure records")

        # 4. Seed default Sizes
        from .models import Size
        if not Size.objects.exists():
            # Numeric sizes (38/40/42/44/46) and XL/XXL are shoe/clothing sizes — only
            # seeded for a clothes store. A cafe/restaurant (or any other market type)
            # never needs them, and having them pre-seeded just clutters the size picker
            # on every product. S/M/L stay universal (also used for drink/portion sizes).
            try:
                from settings.models import SystemSetting
                settings_obj = SystemSetting.objects.first()
                is_clothes = bool(settings_obj and settings_obj.market_type == 'clothes')
            except Exception:
                is_clothes = False

            alpha_sizes = [('S', 0), ('M', 1), ('L', 2)]
            if is_clothes:
                alpha_sizes += [('XL', 3), ('XXL', 4)]
            for name, order in alpha_sizes:
                Size.objects.get_or_create(name=name, defaults={'size_type': 'alpha', 'sort_order': order})

            if is_clothes:
                numeric_sizes = [('38', 10), ('40', 11), ('42', 12), ('44', 13), ('46', 14)]
                for name, order in numeric_sizes:
                    Size.objects.get_or_create(name=name, defaults={'size_type': 'numeric', 'sort_order': order})
            _safe_print("Seeded default Size records")