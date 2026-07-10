from django.contrib import admin
from .models import ShippingCompany, Shipment, ShipmentLog

@admin.register(ShippingCompany)
class ShippingCompanyAdmin(admin.ModelAdmin):
    list_display = ('name', 'phone', 'contact_person', 'is_active')
    list_filter = ('is_active',)
    search_fields = ('name', 'phone', 'contact_person')

class ShipmentLogInline(admin.TabularInline):
    model = ShipmentLog
    extra = 0
    readonly_fields = ('created_at',)

@admin.register(Shipment)
class ShipmentAdmin(admin.ModelAdmin):
    list_display = ('id', 'get_order_display', 'shipping_company', 'status', 'delivery_type', 'tracking_number', 'created_at')
    list_filter = ('status', 'delivery_type', 'created_at', 'shipping_company')
    search_fields = ('order__id', 'tracking_number', 'shipping_address', 'order__customer__phone', 'order__customer__first_name')
    raw_id_fields = ('order', 'shipping_company')
    inlines = [ShipmentLogInline]
    readonly_fields = ('created_at', 'updated_at')

    def get_order_display(self, obj):
        return f"Order #{obj.order.id}"
    get_order_display.short_description = 'الفاتورة'
    get_order_display.admin_order_field = 'order'

@admin.register(ShipmentLog)
class ShipmentLogAdmin(admin.ModelAdmin):
    list_display = ('shipment', 'status', 'created_by', 'created_at')
    list_filter = ('status', 'created_at')
    search_fields = ('shipment__order__id', 'comment')
    readonly_fields = ('created_at',)