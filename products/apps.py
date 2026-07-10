from django.apps import AppConfig
from django.db.models.signals import post_migrate

class ProductsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'products'

    def ready(self):
        import products.signals
        # Register the post_migrate signal to handle initial data setup
        post_migrate.connect(products.signals.create_default_warehouse, sender=self)