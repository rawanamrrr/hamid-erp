from django.contrib import admin
from .models import Customer

@admin.register(Customer)
class CustomerAdmin(admin.ModelAdmin):
    # الأعمدة التي تظهر في قائمة العملاء
    list_display = ('get_full_name', 'phone', 'customer_type', 'get_tier_display', 'get_balance_display', 'opening_balance')
    
    # الفلاتر الجانبية
    list_filter = ('customer_type', 'created_at')
    
    # حقول البحث
    search_fields = ('first_name', 'last_name', 'phone', 'address')
    
    # الحقول للقراءة فقط (لعرض الحسابات دون تعديل يدوي)
    readonly_fields = ('created_at', 'get_total_spent_display', 'get_balance_display')
    
    # عدد العناصر في الصفحة
    list_per_page = 25
    
    # الترتيب الافتراضي (الأحدث إضافة)
    ordering = ('-created_at',)

    # تقسيم الحقول إلى مجموعات
    fieldsets = (
        ('البيانات الشخصية', {
            'fields': ('first_name', 'last_name', 'phone', 'address')
        }),
        ('التصنيف والبيانات المالية', {
            # Added opening_balance here so admin can edit it
            'fields': ('customer_type', 'opening_balance', 'get_total_spent_display', 'get_balance_display', 'created_at')
        }),
    )

    # --- دوال العرض المخصصة ---

    def get_full_name(self, obj):
        return f"{obj.first_name} {obj.last_name}"
    get_full_name.short_description = "الاسم الكامل"
    get_full_name.admin_order_field = 'first_name'

    def get_tier_display(self, obj):
        return obj.get_tier_display()
    get_tier_display.short_description = "شريحة العميل"

    def get_total_spent_display(self, obj):
        return f"{obj.get_total_spent()} ج.م"
    get_total_spent_display.short_description = "إجمالي المشتريات"

    def get_balance_display(self, obj):
        balance = obj.get_balance()
        if balance > 0:
            return f"{balance} (عليه)"
        elif balance < 0:
            return f"{abs(balance)} (له)"
        return "0 (خالص)"
    get_balance_display.short_description = "رصيد الحساب"