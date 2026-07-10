from django.core.management.base import BaseCommand
from products.models import Product

class Command(BaseCommand):
    help = "Recalculate weighted average cost price for all products based on active batches."

    def handle(self, *args, **options):
        products = Product.objects.all()
        count = 0
        self.stdout.write(f"Starting cost price recalculation for {products.count()} products...")
        
        for product in products:
            old_cost = product.cost_price
            product.update_cost_price()
            product.refresh_from_db()
            if old_cost != product.cost_price:
                self.stdout.write(
                    self.style.SUCCESS(
                        f"Updated {product.name} (SKU: {product.sku}): {old_cost} -> {product.cost_price}"
                    )
                )
                count += 1
            else:
                self.stdout.write(
                    f"No change for {product.name} (SKU: {product.sku}): {product.cost_price}"
                )
                
        self.stdout.write(self.style.SUCCESS(f"Recalculation complete. {count} products updated."))
