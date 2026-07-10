from django.core.management.base import BaseCommand, CommandError
from django.db import transaction


class Command(BaseCommand):
    help = (
        "DESTRUCTIVE: wipes textile-era sales/product/customer data so the system can "
        "start fresh as a cafe. Does NOT touch users, roles, accounts (financial chart), "
        "or the new restaurant app's own data (tables/drivers/etc). "
        "Requires --yes to actually run — otherwise it just prints what it would delete."
    )

    def add_arguments(self, parser):
        parser.add_argument('--yes', action='store_true',
                            help='Actually perform the deletion (required to run for real).')

    def handle(self, *args, **options):
        from sales.models import Order, OrderItem, Draft, Quotation, Reservation, ReturnInvoice, Expense, OtherIncome
        from products.models import Product, PurchaseInvoice, PurchaseOrder, StockTransaction, StockBatch, WarehouseStock
        from crm.models import Customer
        from financial.models import Transaction, JournalEntry

        # Order matters: PurchaseInvoiceItem/PurchaseOrderItem PROTECT their Product, so
        # invoices/orders (whose items cascade-delete with them) must go before Product,
        # or the Product delete raises ProtectedError and rolls the whole batch back.
        targets = [
            ('Order/OrderItem (sales history)', Order),
            ('Draft', Draft),
            ('Quotation', Quotation),
            ('Reservation', Reservation),
            ('ReturnInvoice', ReturnInvoice),
            ('Expense', Expense),
            ('OtherIncome', OtherIncome),
            ('PurchaseInvoice', PurchaseInvoice),
            ('PurchaseOrder', PurchaseOrder),
            ('StockTransaction', StockTransaction),
            ('StockBatch', StockBatch),
            ('WarehouseStock', WarehouseStock),
            ('Product (menu items — reseed for cafe after this)', Product),
            ('Customer', Customer),
        ]

        self.stdout.write(self.style.WARNING("This will permanently delete the following:"))
        for label, model in targets:
            count = model.objects.count()
            self.stdout.write(f"  - {label}: {count} rows")

        self.stdout.write(self.style.WARNING(
            "\nNOT deleted: users/roles, financial Accounts (chart of accounts), "
            "existing Transaction/JournalEntry history (financial audit trail stays intact), "
            "restaurant app data (Tables/Drivers/Sections/etc)."
        ))

        if not options['yes']:
            self.stdout.write(self.style.NOTICE(
                "\nDry run only — nothing was deleted. Re-run with --yes to actually wipe this data. "
                "A DB backup is strongly recommended first (copy db.sqlite3)."
            ))
            return

        with transaction.atomic():
            for label, model in targets:
                deleted, _ = model.objects.all().delete()
                self.stdout.write(f"Deleted {label}: {deleted} rows")

        self.stdout.write(self.style.SUCCESS(
            "\nDone. Next steps: set the market type to 'cafe' in Settings, "
            "create branches/tables/categories/menu items, then run seed_cafe_roles."
        ))
