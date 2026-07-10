from django.contrib import admin
from .models import (
    Order, OrderItem, 
    Expense, 
    ReturnInvoice, ReturnItem, 
    OtherIncome, 
    CashSettlement
)

# --- Order Admin ---
class OrderItemInline(admin.TabularInline):
    """
    Inline editor for products within an order.
    Allows editing quantity and price directly.
    """
    model = OrderItem
    extra = 0 # Don't show extra empty rows by default
    fields = ('product', 'quantity', 'price', 'subtotal')
    readonly_fields = ('subtotal',) 
    verbose_name = "منتج"
    verbose_name_plural = "محتويات الفاتورة"
    can_delete = True
    
    # Use autocomplete for product selection if you have many products
    # autocomplete_fields = ['product'] 

    def has_add_permission(self, request, obj=None):
        return True

@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = ('id', 'get_customer', 'total_amount', 'get_payment_method', 'created_at', 'is_completed', 'is_online_order')
    list_filter = ('created_at', 'payment_method', 'is_completed', 'is_online_order', 'user')
    search_fields = ('id', 'customer__first_name', 'customer__last_name', 'customer__phone', 'notes')
    
    # Use autocomplete for customer to handle large lists easily
    autocomplete_fields = ['customer'] 
    
    readonly_fields = ('created_at', 'subtotal_display', 'remaining_display')
    inlines = [OrderItemInline]
    date_hierarchy = 'created_at'
    list_per_page = 20
    ordering = ('-created_at',)
    
    # Save buttons on top as well
    save_on_top = True

    fieldsets = (
        ('بيانات الفاتورة الأساسية', {
            'fields': (
                ('id', 'created_at'),
                ('user', 'customer'),
                ('is_online_order', 'is_completed'),
            )
        }),
        ('التفاصيل المالية (يمكن التعديل)', {
            'fields': (
                ('subtotal_amount', 'discount', 'delivery_cost'),
                ('total_amount', 'received_amount'),
                'payment_method',
            ),
            'description': 'يرجى توخي الحذر عند تعديل القيم المالية يدوياً.'
        }),
        ('تفاصيل السداد (Split Payment)', {
             'fields': (('cash_paid', 'wallet_paid', 'instapay_paid', 'visa_paid'),),
             'classes': ('collapse',),
        }),
        ('الشحن والملاحظات', {
            'fields': ('shipping_address', 'notes')
        }),
        # Collapsed section for legacy/less used tailoring fields
        ('بيانات التفصيل (اختياري)', {
            'fields': ('is_tailoring', 'tailoring_type', 'tailoring_cost', 'tailoring_status', 'tailor_name'),
            'classes': ('collapse',),
        }),
    )

    def get_readonly_fields(self, request, obj=None):
        # Make ID read-only if the object already exists
        if obj:
            return self.readonly_fields + ('id',)
        return self.readonly_fields

    def get_customer(self, obj):
        if obj.customer:
            return f"{obj.customer.first_name} {obj.customer.last_name}"
        return "عميل نقدي/غير مسجل"
    get_customer.short_description = "العميل"
    get_customer.admin_order_field = 'customer'

    def get_payment_method(self, obj):
        return obj.get_payment_method_display()
    get_payment_method.short_description = "طريقة الدفع"
    get_payment_method.admin_order_field = 'payment_method'

    def subtotal_display(self, obj):
        return obj.subtotal_amount
    subtotal_display.short_description = "المجموع الفرعي (محسوب)"

    def remaining_display(self, obj):
        return obj.remaining_amount
    remaining_display.short_description = "المبلغ المتبقي"


# --- Return/Refund Admin ---
class ReturnItemInline(admin.TabularInline):
    model = ReturnItem
    extra = 0
    fields = ('product', 'quantity', 'refund_price')
    readonly_fields = ('refund_price',) # Price usually fixed based on original order
    can_delete = False
    verbose_name = "منتج مرتجع"
    verbose_name_plural = "المنتجات المرتجعة"

@admin.register(ReturnInvoice)
class ReturnInvoiceAdmin(admin.ModelAdmin):
    list_display = ('id', 'original_order', 'customer', 'total_refund_amount', 'created_at', 'user')
    list_filter = ('created_at', 'user')
    search_fields = ('id', 'original_order__id', 'customer__phone')
    autocomplete_fields = ['original_order', 'customer']
    inlines = [ReturnItemInline]
    date_hierarchy = 'created_at'
    verbose_name = "مرتجع"
    verbose_name_plural = "المرتجعات"


# --- Expense Admin ---
@admin.register(Expense)
class ExpenseAdmin(admin.ModelAdmin):
    list_display = ('title', 'category', 'amount', 'date', 'user')
    list_filter = ('category', 'date', 'user')
    search_fields = ('title', 'description')
    date_hierarchy = 'date'
    list_per_page = 25
    verbose_name = "مصروف"
    verbose_name_plural = "المصروفات"


# --- Other Income Admin ---
@admin.register(OtherIncome)
class OtherIncomeAdmin(admin.ModelAdmin):
    list_display = ('title', 'amount', 'date', 'user')
    list_filter = ('date', 'user')
    search_fields = ('title', 'description')
    date_hierarchy = 'date'
    verbose_name = "إيراد آخر"
    verbose_name_plural = "إيرادات أخرى"


# --- Cash Settlement Admin ---
@admin.register(CashSettlement)
class CashSettlementAdmin(admin.ModelAdmin):
    list_display = ('id', 'date', 'expected_cash', 'actual_cash', 'difference', 'user')
    list_filter = ('date', 'user')
    readonly_fields = ('date', 'difference', 'expected_cash') 
    verbose_name = "جرد نقدية"
    verbose_name_plural = "جرد النقدية اليومي"