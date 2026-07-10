from django.contrib import admin
from .models import Product, Category, Supplier, StockTransaction, Warehouse, WarehouseStock

@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ('name', 'products_count', 'created_at')
    search_fields = ('name',)
    
    def products_count(self, obj):
        return obj.products.count()
    products_count.short_description = "عدد المنتجات"

@admin.register(Supplier)
class SupplierAdmin(admin.ModelAdmin):
    list_display = ('name', 'contact_name', 'phone', 'products_count')
    search_fields = ('name', 'contact_name', 'phone')
    
    def products_count(self, obj):
        return obj.product_set.count()
    products_count.short_description = "عدد المنتجات الموردة"

class WarehouseStockInline(admin.TabularInline):
    model = WarehouseStock
    extra = 0
    verbose_name = "رصيد في مخزن"
    verbose_name_plural = "توزيع الأرصدة في المخازن"

@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ('name', 'sku', 'category', 'stock_quantity', 'price_retail', 'is_active')
    list_filter = ('category', 'is_active', 'supplier')
    search_fields = ('name', 'sku', 'material', 'pattern')
    list_editable = ('price_retail', 'is_active')
    readonly_fields = ('stock_quantity', 'created_at', 'updated_at') # Stock is now read-only here
    inlines = [WarehouseStockInline]
    
    fieldsets = (
        ('المعلومات الأساسية', {
            'fields': ('name', 'sku', 'category', 'supplier', 'is_active')
        }),
        ('التفاصيل والمواصفات', {
            'fields': ('material', 'pattern', 'color', 'unit_measure')
        }),
        ('التسعير والمخزون', {
            'fields': ('cost_price', 'price_retail', 'price_semi_wholesale', 'price_wholesale', 'stock_quantity', 'low_stock_threshold')
        }),
        ('تاريخ النظام', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',),
        }),
    )

@admin.register(StockTransaction)
class StockTransactionAdmin(admin.ModelAdmin):
    list_display = ('product', 'warehouse', 'get_transaction_type_display', 'quantity', 'created_at')
    list_filter = ('transaction_type', 'warehouse', 'created_at')
    search_fields = ('product__name', 'product__sku', 'note')
    date_hierarchy = 'created_at'
    
    def get_transaction_type_display(self, obj):
        return obj.get_transaction_type_display()
    get_transaction_type_display.short_description = "نوع الحركة"
    
    def has_change_permission(self, request, obj=None):
        return False

@admin.register(Warehouse)
class WarehouseAdmin(admin.ModelAdmin):
    list_display = ('name', 'address', 'is_active', 'is_sales_point')
    list_filter = ('is_active', 'is_sales_point')
    search_fields = ('name',)

@admin.register(WarehouseStock)
class WarehouseStockAdmin(admin.ModelAdmin):
    list_display = ('product', 'warehouse', 'quantity')
    list_filter = ('warehouse', 'product__category')
    search_fields = ('product__name', 'warehouse__name')