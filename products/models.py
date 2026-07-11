from django.db import models
from django.db.models import Sum, Avg, F
from decimal import Decimal
from django.contrib.auth.models import User
from django.utils import timezone

__all__ = (
    'Category', 'Kind', 'Size', 'UnitOfMeasure', 'Supplier', 'SupplierProduct',
    'PurchaseInvoice', 'PurchaseInvoiceItem', 'SupplierPayment', 'Product',
    'ProductImage', 'Warehouse', 'WarehouseStock', 'StockBatch',
    'StockTransaction', 'PurchaseOrder', 'PurchaseOrderItem', 'ProductCosting',
    'PurchaseReturn', 'PurchaseReturnItem'
)


# ──────────────────────────────────────────────
#  MODULE 3 — MASTER DATA
# ──────────────────────────────────────────────

class Category(models.Model):
    name = models.CharField(max_length=100, verbose_name="اسم القسم")
    description = models.TextField(blank=True, null=True, verbose_name="وصف القسم")
    is_active = models.BooleanField(default=True, verbose_name="نشط")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="تاريخ الإنشاء")

    # Cafe: reserved SKU/code range for items in this category (e.g. عصاير = 1-1000),
    # used to auto-validate item codes and roll up sales-by-category reports.
    code_range_start = models.PositiveIntegerField(null=True, blank=True, verbose_name="من كود")
    code_range_end = models.PositiveIntegerField(null=True, blank=True, verbose_name="إلى كود")
    station = models.ForeignKey('restaurant.KitchenStation', on_delete=models.SET_NULL, null=True, blank=True,
                                related_name='categories', verbose_name="محطة التحضير")
    # Explicit, independent of `station` (which is only about *where* a prep ticket
    # prints and needs a KitchenStation record set up first). Products in a menu
    # category are made to order and never carry a meaningful stock count — this is
    # the single flag POS/waiter/reports/purchase-invoice all check for that.
    is_menu_category = models.BooleanField(
        default=False, verbose_name="قسم منيو (يُحضّر عند الطلب - بدون تتبع مخزون)")

    class Meta:
        verbose_name_plural = "الأقسام"
        verbose_name = "قسم"
        ordering = ['name']

    def __str__(self):
        return self.name

    @property
    def product_count(self):
        return self.products.filter(is_active=True).count()


class Kind(models.Model):
    """أنواع/تصنيفات داخل كل قسم — مثال: قسم القمصان → نوع رجالي / نسائي"""
    category = models.ForeignKey(
        Category, on_delete=models.CASCADE,
        related_name='kinds', verbose_name="القسم"
    )
    name = models.CharField(max_length=100, verbose_name="اسم النوع")
    is_active = models.BooleanField(default=True, verbose_name="نشط")

    class Meta:
        unique_together = ('category', 'name')
        verbose_name = "نوع"
        verbose_name_plural = "الأنواع"
        ordering = ['category__name', 'name']

    def __str__(self):
        return f"{self.category.name} ← {self.name}"


class Size(models.Model):
    """مقاسات المنتجات — حروف أو أرقام أو مخصص"""
    SIZE_TYPES = [
        ('alpha', 'حروف (S/M/L)'),
        ('numeric', 'أرقام'),
        ('custom', 'مخصص'),
    ]
    name = models.CharField(max_length=20, unique=True, verbose_name="المقاس")
    size_type = models.CharField(
        max_length=10, choices=SIZE_TYPES, default='alpha', verbose_name="نوع المقاس"
    )
    sort_order = models.PositiveIntegerField(default=0, verbose_name="ترتيب العرض")
    is_active = models.BooleanField(default=True, verbose_name="نشط")

    class Meta:
        verbose_name = "مقاس"
        verbose_name_plural = "المقاسات"
        ordering = ['sort_order', 'name']

    def __str__(self):
        return self.name


class UnitOfMeasure(models.Model):
    """وحدات القياس — قائمة مرجعية فقط (الحقل unit_measure في Product يبقى CharField)"""
    name = models.CharField(max_length=50, verbose_name="اسم الوحدة")          # متر
    abbreviation = models.CharField(max_length=10, verbose_name="الاختصار")     # MTR
    is_active = models.BooleanField(default=True, verbose_name="نشط")

    class Meta:
        verbose_name = "وحدة قياس"
        verbose_name_plural = "وحدات القياس"
        ordering = ['name']

    def __str__(self):
        return f"{self.name} ({self.abbreviation})"


# ──────────────────────────────────────────────
#  SUPPLIERS
# ──────────────────────────────────────────────

class Supplier(models.Model):
    SUPPLIER_TYPES = [
        ('RAW', 'مورد خامات'),
        ('FINISHED', 'مورد منتج نهائي'),
        ('BOTH', 'كلاهما'),
    ]

    name = models.CharField(max_length=200, verbose_name="اسم الشركة/المورد")
    contact_name = models.CharField(max_length=100, blank=True, verbose_name="اسم المسؤول")
    phone = models.CharField(max_length=20, verbose_name="رقم الهاتف")
    email = models.EmailField(blank=True, verbose_name="البريد الإلكتروني")
    address = models.TextField(blank=True, verbose_name="العنوان")
    supplier_type = models.CharField(
        max_length=10, choices=SUPPLIER_TYPES, default='FINISHED',
        verbose_name="نوع المورد"
    )
    opening_balance = models.DecimalField(
        max_digits=12, decimal_places=2, default=Decimal('0.00'),
        verbose_name="رصيد افتتاحي"
    )
    credit_limit = models.DecimalField(
        max_digits=12, decimal_places=2, default=Decimal('0.00'),
        verbose_name="حد الائتمان"
    )
    notes = models.TextField(blank=True, verbose_name="ملاحظات")
    is_active = models.BooleanField(default=True, verbose_name="نشط")

    class Meta:
        verbose_name_plural = "الموردين"
        verbose_name = "مورد"
        ordering = ['name']

    def __str__(self):
        return self.name

    @property
    def total_purchases(self):
        return self.invoices.filter(status='CONFIRMED').aggregate(total=Sum('net_amount'))['total'] or Decimal('0.00')

    @property
    def total_returns(self):
        return self.returns.aggregate(total=Sum('total_amount'))['total'] or Decimal('0.00')

    @property
    def total_payments(self):
        """إجمالي ما تم دفعه للمورد.

        مصدران منفصلان لا يتداخلان (تم التحقق — Phase 7.6):
          • السندات المستقلة (SupplierPayment) — تُسجَّل عند سداد دين المورد لاحقاً.
          • المبالغ المدفوعة مباشرة داخل الفواتير (PurchaseInvoice.paid_amount) — وقت إنشاء الفاتورة.
        لا يوجد تكرار: مسار إنشاء الفاتورة لا يُنشئ SupplierPayment، ومسار سداد المورد المستقل
        لا يلمس paid_amount، لذا الجمع بينهما صحيح.
        """
        payments = self.payments.aggregate(total=Sum('amount'))['total'] or Decimal('0.00')
        invoice_paid = self.invoices.filter(status='CONFIRMED').aggregate(total=Sum('paid_amount'))['total'] or Decimal('0.00')
        return payments + invoice_paid

    @property
    def outstanding_balance(self):
        # Outstanding = Opening Balance + (Purchases - Credit Returns) - Payments
        # Only returns that are NOT cash refunds (refund_method='debt') should reduce the outstanding debt balance.
        credit_returns = self.returns.filter(refund_method='debt').aggregate(total=Sum('total_amount'))['total'] or Decimal('0.00')
        return self.opening_balance + (self.total_purchases - credit_returns) - self.total_payments


class SupplierProduct(models.Model):
    """يربط المورد بالمنتجات التي يوفرها لتتبع الأسعار والبونص لكل مورد بشكل منفصل"""
    supplier = models.ForeignKey(Supplier, on_delete=models.CASCADE, related_name='product_links', verbose_name="المورد")
    product = models.ForeignKey('Product', on_delete=models.CASCADE, related_name='supplier_links', verbose_name="المنتج")
    
    last_purchase_price = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'), verbose_name="آخر سعر شراء")
    last_bonus_details = models.TextField(blank=True, verbose_name="تفاصيل آخر بونص")
    
    supplier_sku = models.CharField(max_length=100, blank=True, verbose_name="كود المنتج عند المورد")
    is_primary = models.BooleanField(default=False, verbose_name="مورد أساسي")
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('supplier', 'product')
        verbose_name = "سعر مورد لمنتج"
        verbose_name_plural = "أسعار الموردين للمنتجات"

    def __str__(self):
        return f"{self.supplier.name} - {self.product.name}"


class PurchaseInvoice(models.Model):
    STATUS_CHOICES = [
        ('DRAFT',     'مسودة — لم يُضاف المخزون بعد'),
        ('CONFIRMED', 'مؤكد — تم إضافة المخزون'),
        ('RETURNED',  'مرتجع — تم إرجاع الفاتورة'),
        ('CANCELLED', 'ملغي'),
    ]

    supplier = models.ForeignKey(Supplier, on_delete=models.CASCADE, related_name='invoices', verbose_name="المورد")
    user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, verbose_name="المسؤول")
    warehouse = models.ForeignKey('Warehouse', on_delete=models.PROTECT, null=True, related_name='purchase_invoices', verbose_name="مخزن الاستلام")
    
    invoice_number = models.CharField(max_length=50, blank=True, verbose_name="رقم الفاتورة (الورقية)")
    total_amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'), verbose_name="إجمالي الفاتورة")
    discount = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'), verbose_name="الخصم")
    # Phase 7.2: freight/customs/handling charges allocated across lines into item cost.
    landed_cost = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'), verbose_name="مصاريف إضافية (شحن/جمارك)")
    net_amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'), verbose_name="الصافي")
    paid_amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'), verbose_name="المدفوع من الفاتورة مباشرة")
    
    PAYMENT_CHOICES = [
        ('cash', 'نقدي'),
        ('credit', 'آجل'),
        ('bank', 'تحويل بنكي'),
    ]
    payment_method = models.CharField(max_length=20, choices=PAYMENT_CHOICES, default='cash', verbose_name="طريقة الدفع")
    account = models.ForeignKey('financial.Account', on_delete=models.SET_NULL, null=True, blank=True, verbose_name="الحساب المالي")

    # Invoice Lifecycle — DRAFT: no stock. CONFIRMED: stock applied. CANCELLED: reversed.
    status = models.CharField(
        max_length=10, choices=STATUS_CHOICES,
        default='CONFIRMED',  # backward compat — existing invoices are confirmed
        verbose_name="حالة الفاتورة"
    )
    # Idempotency guard: prevents double-applying stock on duplicate submissions
    is_stock_applied = models.BooleanField(
        default=True,  # backward compat — existing invoices have stock applied
        verbose_name="تم تطبيق المخزون"
    )
    
    notes = models.TextField(blank=True, verbose_name="ملاحظات")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="تاريخ الفاتورة")

    class Meta:
        verbose_name = "فاتورة مشتريات"
        verbose_name_plural = "فواتير المشتريات"

    def __str__(self):
        return f"فاتورة شراء #{self.id} - {self.supplier.name}"

    def save(self, *args, **kwargs):
        self.net_amount = self.total_amount - self.discount
        super().save(*args, **kwargs)


class PurchaseInvoiceItem(models.Model):
    """أصناف فاتورة المشتريات"""
    invoice = models.ForeignKey(PurchaseInvoice, on_delete=models.CASCADE, related_name='items', verbose_name="الفاتورة")
    product = models.ForeignKey('Product', on_delete=models.PROTECT, verbose_name="المنتج")
    
    quantity = models.DecimalField(max_digits=10, decimal_places=2, verbose_name="الكمية")
    bonus_quantity = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'), verbose_name="كمية البونص")
    
    unit_price = models.DecimalField(max_digits=12, decimal_places=2, verbose_name="سعر الوحدة")
    subtotal = models.DecimalField(max_digits=12, decimal_places=2, verbose_name="الإجمالي الفرعي")
    
    batch_number = models.CharField(max_length=100, blank=True, verbose_name="رقم التشغيلة / الباتش")
    expiry_date = models.DateField(null=True, blank=True, verbose_name="تاريخ الانتهاء")
    
    discount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'), verbose_name="الخصم")
    tax_rate = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal('0.00'), verbose_name="نسبة الضريبة (%)")
    tax_amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'), verbose_name="قيمة الضريبة")

    # Clothes market: how this item's total quantity splits across sizes, e.g.
    # {"3": "10", "4": "5"} (Size.id -> qty received for that size). Purely a receiving
    # record — the actual stock lives on ProductVariant, incremented when the invoice
    # is confirmed (see api_purchase_invoice_submit).
    size_breakdown = models.JSONField(default=dict, blank=True, verbose_name="تقسيم الكمية حسب المقاس")

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "صنف فاتورة مشتريات"
        verbose_name_plural = "أصناف فواتير المشتريات"

    def __str__(self):
        return f"{self.product.name} ({self.quantity})"

    def save(self, *args, **kwargs):
        taxable = max(Decimal('0.00'), (self.quantity * self.unit_price) - self.discount)
        self.tax_amount = taxable * (self.tax_rate / Decimal('100.00'))
        self.subtotal = taxable + self.tax_amount
        super().save(*args, **kwargs)


class PurchaseReturn(models.Model):
    """فاتورة مرتجع مشتريات لمورد"""
    supplier = models.ForeignKey(Supplier, on_delete=models.CASCADE, related_name='returns', verbose_name="المورد")
    user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, verbose_name="المسؤول")
    warehouse = models.ForeignKey('Warehouse', on_delete=models.PROTECT, related_name='purchase_returns', verbose_name="المخزن")
    
    original_invoice = models.ForeignKey(PurchaseInvoice, on_delete=models.SET_NULL, null=True, blank=True, related_name='returns', verbose_name="الفاتورة الأصلية")
    
    total_amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'), verbose_name="إجمالي المرتجع")
    refund_method = models.CharField(max_length=20, default='cash', verbose_name="طريقة استرداد المبلغ")
    account = models.ForeignKey('financial.Account', on_delete=models.SET_NULL, null=True, blank=True, verbose_name="الحساب المالي (للإيداع)")
    
    notes = models.TextField(blank=True, verbose_name="ملاحظات")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="تاريخ المرتجع")

    class Meta:
        verbose_name = "مرتجع مشتريات"
        verbose_name_plural = "مرتجع المشتريات"

    def __str__(self):
        return f"مرتجع شراء #{self.id} - {self.supplier.name}"


class PurchaseReturnItem(models.Model):
    """أصناف مرتجع المشتريات"""
    purchase_return = models.ForeignKey(PurchaseReturn, on_delete=models.CASCADE, related_name='items', verbose_name="فاتورة المرتجع")
    product = models.ForeignKey('Product', on_delete=models.PROTECT, verbose_name="المنتج")
    
    quantity = models.DecimalField(max_digits=10, decimal_places=2, verbose_name="الكمية المرتجعة")
    unit_price = models.DecimalField(max_digits=12, decimal_places=2, verbose_name="سعر الوحدة")
    
    batch = models.ForeignKey('StockBatch', on_delete=models.SET_NULL, null=True, blank=True, verbose_name="الباتش المتأثر")
    
    subtotal = models.DecimalField(max_digits=12, decimal_places=2, verbose_name="الإجمالي الفرعي")

    class Meta:
        verbose_name = "صنف مرتجع مشتريات"
        verbose_name_plural = "أصناف مرتجع المشتريات"

    def __str__(self):
        return f"{self.product.name} ({self.quantity})"

    def save(self, *args, **kwargs):
        self.subtotal = self.quantity * self.unit_price
        super().save(*args, **kwargs)


class SupplierPayment(models.Model):
    supplier = models.ForeignKey(Supplier, on_delete=models.CASCADE, related_name='payments', verbose_name="المورد")
    user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, verbose_name="المسؤول")
    amount = models.DecimalField(max_digits=12, decimal_places=2, verbose_name="المبلغ")
    payment_method = models.CharField(max_length=50, default='cash', verbose_name="طريقة الدفع")
    notes = models.TextField(blank=True, verbose_name="ملاحظات/رقم الإيصال")
    # Phase 4.4: payment voucher number (سند صرف) — PV-YYYY-NNNNN.
    voucher_number = models.CharField(max_length=30, null=True, blank=True, unique=True, db_index=True, verbose_name="رقم السند")
    date = models.DateTimeField(auto_now_add=True, verbose_name="التاريخ")

    @property
    def display_voucher(self):
        return self.voucher_number or f"#{self.id}"

    class Meta:
        verbose_name = "سند دفع لمورد"
        verbose_name_plural = "سندات الموردين"

    def __str__(self):
        return f"سند صرف لـ {self.supplier.name} - {self.amount}"


# ──────────────────────────────────────────────
#  MODULE 4 — PRODUCTS & INVENTORY
# ──────────────────────────────────────────────

class Product(models.Model):
    UNIT_CHOICES = [
        # Grocery units
        ('KG', 'كيلوغرام'),
        ('G', 'غرام'),
        ('L', 'لتر'),
        ('ML', 'مليلتر'),
        ('PCS', 'قطعة'),
        ('PACK', 'علبة'),
        ('DOZEN', 'دزينة'),
        ('BOTTLE', 'قنينة'),
        ('CAN', 'علبة معدنية'),
        ('BAG', 'كيس'),
        ('CRATE', 'قفص'),
        # Clothes units
        ('MTR', 'متر'),
        ('YRD', 'ياردة'),
        ('ROLL', 'رول / توب'),
        # Pharmacy units
        ('BOX', 'علبة'),
        ('STRIP', 'شريط'),
        ('AMP', 'أمبول'),
    ]

    name = models.CharField(max_length=200, verbose_name="اسم المنتج")
    sku = models.CharField(
        max_length=50, unique=True,
        help_text="كود المنتج الفريد",
        verbose_name="كود المنتج / SKU"
    )
    barcode = models.CharField(
        max_length=20, blank=True,
        verbose_name="الباركود (EAN-13)",
        help_text="يُولَّد تلقائياً من SKU إن تُرك فارغاً"
    )
    # Phase 10.2: Egyptian e-invoice (ETA) item code — GS1 (barcode) or EGS goods code.
    egs_code = models.CharField(
        max_length=30, blank=True, default='',
        verbose_name="كود الصنف الضريبي (EGS/GS1)",
        help_text="كود الصنف لمنظومة الفاتورة الإلكترونية. يُستخدم الباركود إن تُرك فارغاً."
    )
    category = models.ForeignKey(
        Category, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='products', verbose_name="القسم"
    )
    kind = models.ForeignKey(
        Kind, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='products', verbose_name="النوع"
    )
    sizes = models.ManyToManyField(
        Size, blank=True,
        related_name='products', verbose_name="المقاسات"
    )
    supplier = models.ForeignKey(
        Supplier, on_delete=models.SET_NULL, null=True, blank=True,
        verbose_name="المورد"
    )

    # Textile-specific fields
    material = models.CharField(max_length=100, blank=True, help_text="مثال: قطن، حرير، بوليستر", verbose_name="الخامة")
    pattern = models.CharField(max_length=100, blank=True, help_text="مثال: كاروهات، مشجر، سادة", verbose_name="النقشة")
    color = models.CharField(max_length=50, blank=True, verbose_name="اللون")

    # Pharmacy / Non-clothing packaging type
    PACKAGING_CHOICES = [
        ('', 'لا يوجد'),
        ('علبة', 'علبة'),
        ('شريط', 'شريط'),
        ('أمبول', 'أمبول'),
        ('كرتون', 'كرتون'),
        ('زجاجة', 'زجاجة'),
        ('كيس', 'كيس'),
        ('وحدة', 'وحدة'),
    ]
    packaging_type = models.CharField(
        max_length=20, blank=True, default='',
        choices=PACKAGING_CHOICES,
        verbose_name="نوع التعبئة (صيدلية/بقالة)",
        help_text="علبة، شريط، أمبول... للصيدليات والبقالات"
    )
    
    # Pharmacy-specific fields
    strips_per_box = models.PositiveIntegerField(default=1, verbose_name="عدد الأشرطة في العلبة", help_text="لخدمة نظام الصيدليات")
    scientific_name = models.CharField(max_length=200, blank=True, verbose_name="الاسم العلمي")
    
    # Grocery-specific fields
    is_weighted = models.BooleanField(default=False, verbose_name="منتج موزون (بالكيلو/غرام)", help_text="للبقالة: منتجات تُباع بالوزن")
    has_variants = models.BooleanField(default=False, verbose_name="له خيارات/أنواع مختلفة", help_text="مثل: حليب 1 لتر، حليب 500 مل")
    variant_parent = models.ForeignKey('self', on_delete=models.SET_NULL, null=True, blank=True, related_name='child_products', verbose_name="المنتج الأصلي (للخيارات)")
    variant_name = models.CharField(max_length=100, blank=True, verbose_name="اسم الخيار", help_text="مثل: 1 لتر، 500 مل، كبير، صغير")
    net_weight = models.DecimalField(max_digits=10, decimal_places=3, null=True, blank=True, verbose_name="الوزن الصافي", help_text="بالكيلوغرام أو اللتر")
    weight_unit = models.CharField(max_length=10, blank=True, choices=[('kg', 'كجم'), ('g', 'جرام'), ('l', 'لتر'), ('ml', 'ملليلتر')], verbose_name="وحدة الوزن/الحجم")
    shelf_life_days = models.PositiveIntegerField(null=True, blank=True, verbose_name="مدة الصلاحية (أيام)")
    requires_refrigeration = models.BooleanField(default=False, verbose_name="يحتاج تبريد")
    
    # Electronics-specific fields
    brand = models.CharField(max_length=100, blank=True, verbose_name="الماركة/العلامة التجارية")
    model_number = models.CharField(max_length=100, blank=True, verbose_name="رقم الموديل")
    serial_number = models.CharField(max_length=100, blank=True, verbose_name="الرقم التسلسلي")
    warranty_months = models.PositiveIntegerField(null=True, blank=True, verbose_name="مدة الضمان (أشهر)")
    is_serialized = models.BooleanField(default=False, verbose_name="يتطلب رقم تسلسلي عند البيع", help_text="للإلكترونيات: يُطلب الرقم التسلسلي للقطعة عند البيع")
    is_refurbished = models.BooleanField(default=False, verbose_name="مستخدم أو مُجدد")
    specifications = models.TextField(blank=True, verbose_name="المواصفات الفنية")

    # Clothes-specific fields
    SEASON_CHOICES = [
        ('', 'غير محدد'),
        ('spring_summer', 'ربيع / صيف'),
        ('fall_winter', 'خريف / شتاء'),
        ('all_season', 'كل المواسم'),
    ]
    season = models.CharField(
        max_length=20, blank=True, default='',
        choices=SEASON_CHOICES,
        verbose_name="الموسم",
        help_text="للملابس: صيفي، شتوي، أو كل المواسم"
    )

    # Multi-Unit / Sub-Unit Pricing (Generalized)
    has_sub_unit = models.BooleanField(default=False, verbose_name="يباع بالتجزئة (الوحدة الفرعية)")
    sub_unit = models.ForeignKey(UnitOfMeasure, on_delete=models.SET_NULL, null=True, blank=True, related_name='sub_products', verbose_name="الوحدة الفرعية")
    sub_units_per_main_unit = models.DecimalField(max_digits=10, decimal_places=3, default=Decimal('1.000'), verbose_name="عدد الوحدات الفرعية داخل الوحدة الأساسية")
    sub_unit_price = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'), verbose_name="سعر الوحدة الفرعية")

    @property
    def box_count(self):
        import math
        # stock_quantity is boxes. We want the full boxes only.
        return int(math.floor(float(self.stock_quantity)))

    @property
    def remaining_strips(self):
        if self.strips_per_box and self.strips_per_box > 1:
            from decimal import Decimal
            # Use Decimal for precision
            qty = Decimal(str(self.stock_quantity))
            strips = Decimal(str(self.strips_per_box))
            fractional_part = qty % Decimal('1')
            return int(round(float(fractional_part * strips)))
        return 0

    @property
    def total_strips(self):
        if self.strips_per_box and self.strips_per_box > 1:
            from decimal import Decimal
            qty = Decimal(str(self.stock_quantity))
            strips = Decimal(str(self.strips_per_box))
            return int(round(float(qty * strips)))
        return int(self.stock_quantity)

    # Pricing Tiers
    cost_price = models.DecimalField(max_digits=10, decimal_places=2, help_text="سعر الشراء من المورد", verbose_name="سعر التكلفة")
    price_retail = models.DecimalField(max_digits=10, decimal_places=2, verbose_name="سعر قطاعي")
    # 0 = not sold at this tier (falls back to price_retail — see products.pricing.tier_price).
    price_semi_wholesale = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'), blank=True, verbose_name="سعر نص جملة")
    price_wholesale = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'), blank=True, verbose_name="سعر جملة")

    # Stock
    stock_quantity = models.DecimalField(
        max_digits=10, decimal_places=2, default=Decimal('0.00'),
        verbose_name="إجمالي الكمية (كل المخازن)"
    )

    unit_measure = models.CharField(max_length=10, choices=UNIT_CHOICES, default='PCS', verbose_name="وحدة القياس")

    low_stock_threshold = models.DecimalField(
        max_digits=10, decimal_places=2, default=Decimal('10.00'),
        verbose_name="حد التنبيه للنواقص"
    )
    pieces_per_package = models.PositiveIntegerField(
        default=48, verbose_name="قطع في الشيكارة",
        help_text="عدد القطع في كل شيكارة (الافتراضي: 48)"
    )
    is_active = models.BooleanField(default=True, verbose_name="نشط (متاح للبيع)")
    # A raw material (milk, sugar, coffee beans...) consumed via a Recipe — never sold
    # directly, so it's added through its own simple screen instead of "إضافة منتج"
    # (which is built for sellable final products) and is hidden from POS/waiter menus.
    is_raw_material = models.BooleanField(default=False, verbose_name="مادة خام (غير مباعة مباشرة)")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="تاريخ الإضافة")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="آخر تحديث")

    class Meta:
        verbose_name_plural = "المنتجات"
        verbose_name = "منتج"

    def __str__(self):
        return f"{self.name} ({self.sku})"

    def save(self, *args, **kwargs):
        # A product saved without picking a قسم would otherwise sit invisible: the
        # waiter menu only ever loops through Category.products, and the POS grid
        # treats an uncategorized item as stock-tracked, hiding it at 0 stock behind
        # a "منتهي" badge. Falling back to a dedicated, non-stock-tracked category
        # makes it appear immediately everywhere, exactly like a menu item does.
        if not self.category_id:
            fallback, _ = Category.objects.get_or_create(
                name='بدون قسم', defaults={'is_menu_category': True, 'is_active': True}
            )
            self.category_id = fallback.id
        super().save(*args, **kwargs)

    @property
    def is_low_stock(self):
        return self.stock_quantity <= self.low_stock_threshold

    def calculate_total_stock(self):
        """Recalculates total stock from all warehouses"""
        total = self.warehouse_stocks.aggregate(total=Sum('quantity'))['total'] or 0
        self.stock_quantity = total
        self.save(update_fields=['stock_quantity'])

    def update_cost_price(self):
        """
        Calculates the weighted average cost based on current batches.
        Formula: Sum(current_quantity * purchase_price) / Sum(current_quantity)
        """
        active_batches = self.batches.filter(current_quantity__gt=0)
        if active_batches.exists():
            total_value = active_batches.aggregate(
                value=Sum(F('current_quantity') * F('purchase_price'))
            )['value'] or Decimal('0.00')
            total_qty = active_batches.aggregate(total=Sum('current_quantity'))['total'] or Decimal('1.00')
            
            if total_qty > 0:
                self.cost_price = total_value / total_qty
                self.save(update_fields=['cost_price'])

    @property
    def profit_margin_retail(self):
        """Profit margin % for retail price"""
        if self.cost_price and self.cost_price > 0:
            return round(((self.price_retail - self.cost_price) / self.cost_price) * 100, 1)
        return 0

    @property
    def thumbnail(self):
        """Returns first image data for thumbnail display"""
        first_img = self.images.first()
        return first_img.image_data if first_img else None

    @classmethod
    def get_combined_unit_choices(cls):
        choices = list(cls.UNIT_CHOICES)
        existing = {k for k, v in choices}
        try:
            from products.models import UnitOfMeasure
            for u in UnitOfMeasure.objects.filter(is_active=True):
                if u.abbreviation not in existing:
                    choices.append((u.abbreviation, u.name))
                    existing.add(u.abbreviation)
        except Exception:
            pass
        return choices

    def get_unit_measure_display(self):
        choices_dict = dict(self.UNIT_CHOICES)
        val = self.unit_measure
        if val in choices_dict:
            return choices_dict[val]
            
        from django.core.cache import cache
        db_units = cache.get('db_unit_measure_choices')
        if db_units is None:
            try:
                from products.models import UnitOfMeasure
                db_units = {u.abbreviation: u.name for u in UnitOfMeasure.objects.all()}
                cache.set('db_unit_measure_choices', db_units, 60)
            except Exception:
                db_units = {}
                
        return db_units.get(val, val)


class ProductVariant(models.Model):
    """A specific sellable variant of a `has_variants` product — size × color (fashion ERP).

    Each variant carries its OWN stock, optional price override and barcode. Kept deliberately
    simple (one stock number per variant, no batch/FEFO) — that's what clothing shops need.
    Falls back to the parent product's price when no override is set.
    """
    product = models.ForeignKey('Product', on_delete=models.CASCADE, related_name='variants', verbose_name="المنتج الأساسي")
    size = models.ForeignKey('Size', on_delete=models.SET_NULL, null=True, blank=True, related_name='variants', verbose_name="المقاس")
    color = models.CharField(max_length=50, blank=True, default='', verbose_name="اللون")
    barcode = models.CharField(max_length=64, blank=True, default='', verbose_name="الباركود")
    price_override = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True, verbose_name="سعر خاص (اختياري)")
    stock_quantity = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'), verbose_name="الكمية")
    is_active = models.BooleanField(default=True, verbose_name="نشط")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "خيار منتج (مقاس/لون)"
        verbose_name_plural = "خيارات المنتجات"
        ordering = ['product', 'size__sort_order', 'color']
        unique_together = ('product', 'size', 'color')

    def __str__(self):
        return f"{self.product.name} - {self.label}"

    @property
    def label(self):
        parts = [p for p in [(self.size.name if self.size else ''), self.color] if p]
        return ' / '.join(parts) if parts else 'افتراضي'

    @property
    def price(self):
        """Selling price: the variant override, else the parent product's retail price."""
        if self.price_override is not None:
            return self.price_override
        return self.product.price_retail


class ProductImage(models.Model):
    """Stores up to 5 product images as compressed base64 strings"""
    product = models.ForeignKey(
        Product, on_delete=models.CASCADE,
        related_name='images', verbose_name="المنتج"
    )
    image_data = models.TextField(verbose_name="بيانات الصورة (base64)")
    order = models.PositiveIntegerField(default=0, verbose_name="الترتيب")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['order', 'created_at']
        verbose_name = "صورة منتج"
        verbose_name_plural = "صور المنتجات"

    def __str__(self):
        return f"صورة #{self.order} - {self.product.name}"


# ──────────────────────────────────────────────
#  WAREHOUSES
# ──────────────────────────────────────────────

class Warehouse(models.Model):
    WAREHOUSE_TYPES = [
        ('RAW', 'مخزن خامات'),
        ('FINISHED', 'مخزن منتج نهائي'),
        ('BOTH', 'كلاهما'),
    ]

    name = models.CharField(max_length=100, verbose_name="اسم المخزن")
    address = models.TextField(blank=True, verbose_name="العنوان")
    is_active = models.BooleanField(default=True, verbose_name="نشط")
    is_sales_point = models.BooleanField(default=False, verbose_name="منفذ بيع (يظهر في الكاشير)")
    warehouse_type = models.CharField(
        max_length=10, choices=WAREHOUSE_TYPES, default='FINISHED',
        verbose_name="نوع المخزن"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "مخزن"
        verbose_name_plural = "المخازن"

    def __str__(self):
        return self.name


class WarehouseStock(models.Model):
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name='warehouse_stocks', verbose_name="المنتج")
    warehouse = models.ForeignKey(Warehouse, on_delete=models.CASCADE, related_name='stocks', verbose_name="المخزن")
    quantity = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'), verbose_name="الكمية")

    class Meta:
        unique_together = ('product', 'warehouse')
        verbose_name = "رصيد منتج في مخزن"
        verbose_name_plural = "أرصدة المخازن"

    def __str__(self):
        return f"{self.product.name} - {self.warehouse.name}: {self.quantity}"


class StockBatch(models.Model):
    """تتبع كميات المنتج بنظام الدفعات (الباتشات) لتطبيق FIFO وتتبع تواريخ الانتهاء"""
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name='batches', verbose_name="المنتج")
    warehouse = models.ForeignKey(Warehouse, on_delete=models.CASCADE, related_name='batches', verbose_name="المخزن")
    supplier = models.ForeignKey(Supplier, on_delete=models.SET_NULL, null=True, blank=True, verbose_name="المورد")
    purchase_invoice = models.ForeignKey(PurchaseInvoice, on_delete=models.SET_NULL, null=True, blank=True, related_name='batches', verbose_name="فاتورة الشراء")
    
    batch_number = models.CharField(max_length=100, blank=True, verbose_name="رقم الباتش")
    expiry_date = models.DateField(null=True, blank=True, verbose_name="تاريخ الانتهاء")
    
    purchase_price = models.DecimalField(max_digits=12, decimal_places=2, verbose_name="سعر الشراء")
    
    initial_quantity = models.DecimalField(max_digits=10, decimal_places=2, verbose_name="الكمية الأصلية")
    current_quantity = models.DecimalField(max_digits=10, decimal_places=2, verbose_name="الكمية الحالية")
    
    is_exhausted = models.BooleanField(default=False, verbose_name="منتهي (كمية صفر)")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="تاريخ الإضافة")

    class Meta:
        verbose_name = "باتش مخزني"
        verbose_name_plural = "باتشات المخزن"
        ordering = ['expiry_date', 'created_at'] # FIFO by expiry then entry

    def __str__(self):
        return f"{self.product.name} - Batch: {self.batch_number} - Qty: {self.current_quantity}"

    def save(self, *args, **kwargs):
        if self.current_quantity <= 0:
            self.is_exhausted = True
        else:
            self.is_exhausted = False
        super().save(*args, **kwargs)


# ──────────────────────────────────────────────
#  MODULE 5 — STOCK TRANSACTIONS
# ──────────────────────────────────────────────

class ProductPriceBreak(models.Model):
    """Quantity-break pricing (Phase 6.6): at/above `min_quantity` units, sell at
    `unit_price`. Optionally scoped to a customer tier (blank = all tiers).
    """
    TIER_CHOICES = [
        ('', 'كل الفئات'),
        ('retail', 'قطاعي'),
        ('semi_wholesale', 'نص جملة'),
        ('wholesale', 'جملة'),
    ]
    product = models.ForeignKey('Product', on_delete=models.CASCADE, related_name='price_breaks', verbose_name="الصنف")
    customer_type = models.CharField(max_length=20, blank=True, default='', choices=TIER_CHOICES, verbose_name="فئة العميل")
    min_quantity = models.DecimalField(max_digits=10, decimal_places=2, verbose_name="من كمية")
    unit_price = models.DecimalField(max_digits=10, decimal_places=2, verbose_name="سعر الوحدة")

    class Meta:
        ordering = ['product', 'customer_type', 'min_quantity']
        verbose_name = "شريحة سعر كمية"
        verbose_name_plural = "شرائح أسعار الكميات"

    def __str__(self):
        return f"{self.product_id}: {self.min_quantity}+ @ {self.unit_price}"


class StockCount(models.Model):
    """A stocktake / جرد session for a warehouse (Phase 5.1).

    Flow: create (snapshots system quantities) -> enter counted quantities -> review
    variances -> apply (posts ADJ adjustments via the inventory service). Applying is a
    deliberate, one-time action; a session can't be applied twice.
    """
    STATUS_DRAFT = 'draft'
    STATUS_APPLIED = 'applied'
    STATUS_CHOICES = [(STATUS_DRAFT, 'قيد الجرد'), (STATUS_APPLIED, 'تم الاعتماد')]

    warehouse = models.ForeignKey('Warehouse', on_delete=models.PROTECT, related_name='stock_counts', verbose_name="المخزن")
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=STATUS_DRAFT, verbose_name="الحالة")
    note = models.CharField(max_length=255, blank=True, default='', verbose_name="ملاحظات")
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='stock_counts_created', verbose_name="أنشأها")
    applied_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='stock_counts_applied', verbose_name="اعتمدها")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="تاريخ الإنشاء")
    applied_at = models.DateTimeField(null=True, blank=True, verbose_name="تاريخ الاعتماد")

    class Meta:
        ordering = ['-created_at']
        verbose_name = "جرد مخزون"
        verbose_name_plural = "عمليات الجرد"

    def __str__(self):
        return f"جرد #{self.id} - {self.warehouse.name}"

    @property
    def is_applied(self):
        return self.status == self.STATUS_APPLIED


class StockCountItem(models.Model):
    count = models.ForeignKey(StockCount, on_delete=models.CASCADE, related_name='items')
    product = models.ForeignKey('Product', on_delete=models.CASCADE, verbose_name="الصنف")
    system_qty = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'), verbose_name="رصيد النظام")
    counted_qty = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True, verbose_name="الجرد الفعلي")

    class Meta:
        unique_together = ('count', 'product')

    @property
    def variance(self):
        if self.counted_qty is None:
            return None
        return self.counted_qty - self.system_qty


class StockTransaction(models.Model):
    TRANSACTION_TYPES = [
        ('IN', 'استلام شراء'),
        ('OUT', 'بيع'),
        ('RET', 'مرتجع من عميل (قديم)'),
        ('RET_IN', 'مرتجع من عميل'),
        ('RET_OUT', 'مرتجع لمورد'),
        ('TRN', 'تحويل بين مخازن'),
        ('MFG', 'تصنيع — خصم خامات'),
        ('MFG_OUT', 'تصنيع — إضافة منتج نهائي'),
        ('ADJ', 'تسوية جرد'),
    ]

    product = models.ForeignKey(
        Product, on_delete=models.CASCADE,
        related_name='transactions', verbose_name="المنتج"
    )
    warehouse = models.ForeignKey(
        Warehouse, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='transactions', verbose_name="المخزن"
    )
    destination_warehouse = models.ForeignKey(
        Warehouse, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='incoming_transactions',
        verbose_name="المخزن الوجهة (للتحويل/التصنيع)"
    )
    linked_transaction = models.ForeignKey(
        'self', on_delete=models.SET_NULL, null=True, blank=True,
        verbose_name="الحركة المرتبطة (تصنيع)"
    )
    size = models.ForeignKey(
        Size, on_delete=models.SET_NULL, null=True, blank=True,
        verbose_name="المقاس"
    )
    # Audit: who made this transaction
    created_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='stock_transactions',
        verbose_name="بواسطة"
    )

    transaction_type = models.CharField(max_length=10, choices=TRANSACTION_TYPES, verbose_name="نوع الحركة")
    quantity = models.DecimalField(max_digits=10, decimal_places=2, verbose_name="الكمية")

    unit_price = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'), verbose_name="سعر الوحدة (بيع)")
    # Cost at time of transaction — critical for accurate COGS on OUT transactions
    cost_price = models.DecimalField(
        max_digits=10, decimal_places=2, default=Decimal('0.00'),
        verbose_name="تكلفة الوحدة (COGS)",
        help_text="سعر شراء الدفعة الفعلية وقت البيع — للحسابات الدقيقة للأرباح"
    )
    discount = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'), verbose_name="قيمة الخصم")
    reference_number = models.CharField(max_length=50, blank=True, verbose_name="رقم الفاتورة/المرجع")

    note = models.TextField(blank=True, verbose_name="ملاحظات")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="تاريخ الحركة")

    class Meta:
        verbose_name_plural = "حركات المخزن"
        verbose_name = "حركة مخزنية"

    def save(self, *args, **kwargs):
        if not self.pk and self.transaction_type == 'OUT' and self.unit_price == 0:
            if self.product:
                self.unit_price = self.product.price_retail
        super().save(*args, **kwargs)

    @property
    def total_price(self):
        return (self.quantity * self.unit_price) - self.discount

    @property
    def gross_profit(self):
        """Realized profit for OUT transactions: (selling_price - purchase_cost) * qty"""
        if self.transaction_type == 'OUT' and self.cost_price:
            return (self.unit_price - self.cost_price) * self.quantity
        return Decimal('0.00')

    def __str__(self):
        return f"{self.get_transaction_type_display()} - {self.product.name} ({self.quantity})"


# ──────────────────────────────────────────────
#  MODULE 6 — PURCHASE ORDERS
# ──────────────────────────────────────────────

class PurchaseOrder(models.Model):
    STATUS_CHOICES = [
        ('DRAFT', 'مسودة'),
        ('CONFIRMED', 'مؤكد'),
        ('PARTIAL', 'مستلم جزئياً'),
        ('RECEIVED', 'مستلم كلياً'),
        ('CANCELLED', 'ملغي'),
    ]

    po_number = models.CharField(max_length=50, unique=True, blank=True, verbose_name="رقم أمر الشراء")
    supplier = models.ForeignKey(
        Supplier, on_delete=models.PROTECT,
        related_name='purchase_orders', verbose_name="المورد"
    )
    destination_warehouse = models.ForeignKey(
        Warehouse, on_delete=models.PROTECT,
        verbose_name="مخزن الاستلام"
    )
    status = models.CharField(
        max_length=10, choices=STATUS_CHOICES, default='DRAFT',
        verbose_name="الحالة"
    )
    order_date = models.DateField(auto_now_add=True, verbose_name="تاريخ الطلب")
    expected_date = models.DateField(null=True, blank=True, verbose_name="تاريخ الاستلام المتوقع")
    total_amount = models.DecimalField(
        max_digits=12, decimal_places=2, default=Decimal('0.00'),
        verbose_name="إجمالي أمر الشراء"
    )
    notes = models.TextField(blank=True, verbose_name="ملاحظات")
    created_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True,
        verbose_name="أنشأه"
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="تاريخ الإنشاء")

    class Meta:
        verbose_name = "أمر شراء"
        verbose_name_plural = "أوامر الشراء"
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.po_number} - {self.supplier.name}"

    def recalculate_total(self):
        total = sum(item.line_total for item in self.items.all())
        self.total_amount = total
        self.save(update_fields=['total_amount'])


class PurchaseOrderItem(models.Model):
    purchase_order = models.ForeignKey(
        PurchaseOrder, on_delete=models.CASCADE,
        related_name='items', verbose_name="أمر الشراء"
    )
    product = models.ForeignKey(
        Product, on_delete=models.PROTECT,
        verbose_name="المنتج"
    )
    size = models.ForeignKey(
        Size, on_delete=models.SET_NULL, null=True, blank=True,
        verbose_name="المقاس"
    )
    ordered_quantity = models.DecimalField(max_digits=10, decimal_places=2, verbose_name="الكمية المطلوبة")
    received_quantity = models.DecimalField(
        max_digits=10, decimal_places=2, default=Decimal('0.00'),
        verbose_name="الكمية المستلمة"
    )
    unit_price = models.DecimalField(max_digits=10, decimal_places=2, verbose_name="سعر الوحدة")
    discount = models.DecimalField(
        max_digits=10, decimal_places=2, default=Decimal('0.00'),
        verbose_name="الخصم"
    )

    class Meta:
        verbose_name = "صنف في أمر الشراء"
        verbose_name_plural = "أصناف أوامر الشراء"

    def __str__(self):
        return f"{self.product.name} × {self.ordered_quantity}"

    @property
    def line_total(self):
        return (self.ordered_quantity * self.unit_price) - self.discount

    @property
    def is_fully_received(self):
        return self.received_quantity >= self.ordered_quantity

    @property
    def remaining_quantity(self):
        return max(self.ordered_quantity - self.received_quantity, Decimal('0.00'))


# ──────────────────────────────────────────────
#  PRODUCT COSTING (unchanged)
# ──────────────────────────────────────────────

class ProductCosting(models.Model):
    COSTING_MODES = [
        ('RETAIL', 'شراء (قطاعي)'),
        ('MANUFACTURE', 'تصنيع (إنتاج)'),
    ]

    name = models.CharField(max_length=200, verbose_name="اسم المنتج (في الحساب)")
    linked_product = models.ForeignKey(
        Product, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='costings', verbose_name="المنتج المرتبط"
    )
    mode = models.CharField(max_length=20, choices=COSTING_MODES, verbose_name="نوع الحساب")

    total_cost = models.DecimalField(max_digits=12, decimal_places=2, verbose_name="إجمالي التكلفة")
    selling_price = models.DecimalField(max_digits=12, decimal_places=2, verbose_name="سعر البيع المقترح")

    config_json = models.JSONField(verbose_name="بيانات الحساب التفصيلية")

    notes = models.TextField(blank=True, verbose_name="ملاحظات")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="تاريخ الحساب")

    class Meta:
        verbose_name = "حسبة تكاليف"
        verbose_name_plural = "سجل حساب التكاليف"
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.name} - {self.total_cost} ({self.created_at.strftime('%Y-%m-%d')})"
