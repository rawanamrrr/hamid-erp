from decimal import Decimal

from django.contrib.auth.models import User
from django.db import models


# Branch = products.Warehouse (is_sales_point=True). Reused instead of a new model so
# stock, POS warehouse-restrictions and reporting all stay scoped the same way.


class Section(models.Model):
    """A floor area of a branch (indoor/outdoor/terrace…) that tables belong to."""
    branch = models.ForeignKey('products.Warehouse', on_delete=models.CASCADE,
                               related_name='restaurant_sections', verbose_name="الفرع")
    name = models.CharField(max_length=100, verbose_name="اسم الصالة")
    is_active = models.BooleanField(default=True, verbose_name="نشط")

    class Meta:
        verbose_name = "صالة"
        verbose_name_plural = "الصالات"
        ordering = ['branch', 'name']

    def __str__(self):
        return f"{self.branch.name} - {self.name}"


class Table(models.Model):
    STATUS_FREE = 'free'
    STATUS_BUSY = 'busy'
    STATUS_RESERVED = 'reserved'
    STATUS_CHOICES = [
        (STATUS_FREE, 'متاحة'),
        (STATUS_BUSY, 'مشغولة'),
        (STATUS_RESERVED, 'محجوزة'),
    ]

    branch = models.ForeignKey('products.Warehouse', on_delete=models.CASCADE,
                               related_name='tables', verbose_name="الفرع")
    section = models.ForeignKey(Section, on_delete=models.SET_NULL, null=True, blank=True,
                                related_name='tables', verbose_name="الصالة")
    number = models.CharField(max_length=20, verbose_name="رقم الترابيزة")
    seats = models.PositiveIntegerField(default=4, verbose_name="عدد الكراسي")
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=STATUS_FREE,
                              db_index=True, verbose_name="الحالة")
    is_active = models.BooleanField(default=True, verbose_name="نشطة")
    reserved_name = models.CharField(max_length=100, blank=True, default='', verbose_name="اسم صاحب الحجز")
    reserved_phone = models.CharField(max_length=30, blank=True, default='', verbose_name="رقم الهاتف")
    reserved_party_size = models.PositiveIntegerField(null=True, blank=True, verbose_name="عدد الأفراد")
    reserved_time = models.DateTimeField(null=True, blank=True, verbose_name="موعد الحجز")
    reserved_notes = models.CharField(max_length=200, blank=True, default='', verbose_name="ملاحظات")

    class Meta:
        verbose_name = "ترابيزة"
        verbose_name_plural = "الترابيزات"
        unique_together = ('branch', 'number')
        ordering = ['branch', 'section', 'number']

    def __str__(self):
        return f"ترابيزة {self.number} ({self.branch.name})"

    @property
    def open_order(self):
        return self.orders.filter(is_open=True).exclude(status='void').first()


class KitchenStation(models.Model):
    """A prep station (مطبخ رئيسي / بار العصائر …). Categories route their items here."""
    branch = models.ForeignKey('products.Warehouse', on_delete=models.CASCADE,
                               related_name='kitchen_stations', verbose_name="الفرع")
    name = models.CharField(max_length=100, verbose_name="اسم المحطة")
    printer_target = models.CharField(max_length=200, blank=True, default='',
                                      verbose_name="اسم/عنوان الطابعة")
    is_active = models.BooleanField(default=True, verbose_name="نشطة")

    class Meta:
        verbose_name = "محطة تحضير"
        verbose_name_plural = "محطات التحضير"
        ordering = ['branch', 'name']

    def __str__(self):
        return f"{self.name} ({self.branch.name})"


class MenuModifierGroup(models.Model):
    """A set of choices for a menu item, e.g. 'مستوى السكر' or 'الحجم'."""
    name = models.CharField(max_length=100, verbose_name="اسم المجموعة")
    products = models.ManyToManyField('products.Product', blank=True, related_name='modifier_groups',
                                      verbose_name="الأصناف")
    categories = models.ManyToManyField('products.Category', blank=True, related_name='modifier_groups',
                                        verbose_name="الأقسام")
    is_required = models.BooleanField(default=False, verbose_name="إجباري")
    show_on_receipt = models.BooleanField(default=True, verbose_name="يُطبع على فاتورة العميل")
    min_select = models.PositiveIntegerField(default=0, verbose_name="أقل عدد اختيارات")
    max_select = models.PositiveIntegerField(default=1, verbose_name="أقصى عدد اختيارات")
    sort_order = models.PositiveIntegerField(default=0, verbose_name="الترتيب")

    class Meta:
        verbose_name = "مجموعة اختيارات"
        verbose_name_plural = "مجموعات الاختيارات"
        ordering = ['sort_order', 'name']

    def __str__(self):
        return self.name


class MenuModifier(models.Model):
    """A single option within a modifier group, e.g. 'سكر زيادة' (+0), 'شوت إضافي' (+5)."""
    group = models.ForeignKey(MenuModifierGroup, on_delete=models.CASCADE, related_name='options',
                              verbose_name="المجموعة")
    name = models.CharField(max_length=100, verbose_name="اسم الاختيار")
    price_delta = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'),
                                      verbose_name="فرق السعر")
    is_active = models.BooleanField(default=True, verbose_name="نشط")
    sort_order = models.PositiveIntegerField(default=0, verbose_name="الترتيب")

    class Meta:
        verbose_name = "اختيار"
        verbose_name_plural = "الاختيارات"
        ordering = ['sort_order', 'name']

    def __str__(self):
        return f"{self.group.name}: {self.name}"


class Recipe(models.Model):
    """Optional bill-of-materials for a menu item (hybrid: not every item needs one).

    When present, selling the linked product deducts each RecipeItem's ingredient
    quantity from stock via the existing inventory service.
    """
    product = models.OneToOneField('products.Product', on_delete=models.CASCADE,
                                   related_name='recipe', verbose_name="الصنف")
    notes = models.TextField(blank=True, verbose_name="ملاحظات التحضير")
    is_active = models.BooleanField(default=True, verbose_name="نشطة")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "وصفة تحضير"
        verbose_name_plural = "وصفات التحضير"

    def __str__(self):
        return f"وصفة: {self.product.name}"


# Conversion factor FROM the key unit TO the value unit's family base, e.g. 1 G = 0.001 KG.
# Only unit *pairs within the same physical family* are convertible — you can't convert
# PCS to KG. Families: weight (KG/G), volume (L/ML). Anything else must match exactly.
UNIT_CONVERSION = {
    ('G', 'KG'): Decimal('0.001'),
    ('KG', 'G'): Decimal('1000'),
    ('ML', 'L'): Decimal('0.001'),
    ('L', 'ML'): Decimal('1000'),
}
UNIT_FAMILIES = {
    'KG': {'KG', 'G'}, 'G': {'KG', 'G'},
    'L': {'L', 'ML'}, 'ML': {'L', 'ML'},
}


def compatible_units(base_unit):
    """Units a recipe line may be entered in for an ingredient whose stock is tracked in
    `base_unit` — its own unit, plus any unit convertible to it (e.g. base KG -> KG or G).
    Falls back to just the base unit for families with no defined conversion (PCS, BOX...).
    """
    from products.models import Product
    choices = dict(Product.UNIT_CHOICES)
    allowed = UNIT_FAMILIES.get(base_unit, {base_unit})
    return [(u, choices[u]) for u in choices if u in allowed]


class RecipeItem(models.Model):
    recipe = models.ForeignKey(Recipe, on_delete=models.CASCADE, related_name='items', verbose_name="الوصفة")
    ingredient = models.ForeignKey('products.Product', on_delete=models.PROTECT,
                                   related_name='used_in_recipes', verbose_name="الخامة")
    quantity = models.DecimalField(max_digits=10, decimal_places=3, verbose_name="الكمية")
    # The unit `quantity` was actually entered in — may differ from the ingredient's own
    # tracked unit (e.g. a recipe calling for "30 G" of an ingredient whose stock is kept
    # in KG). Blank/unset means "same as the ingredient's unit", so old rows keep working.
    unit = models.CharField(max_length=10, blank=True, default='', verbose_name="وحدة الكمية")

    class Meta:
        verbose_name = "مكوّن وصفة"
        verbose_name_plural = "مكوّنات الوصفات"
        unique_together = ('recipe', 'ingredient')

    def __str__(self):
        return f"{self.ingredient.name} x{self.quantity}"

    @property
    def base_quantity(self):
        """`quantity` converted into the ingredient's own unit — this is what actually
        gets deducted from stock (issue_stock always works in the ingredient's unit).
        """
        base_unit = self.ingredient.unit_measure
        entered_unit = self.unit or base_unit
        if entered_unit == base_unit:
            return self.quantity
        factor = UNIT_CONVERSION.get((entered_unit, base_unit))
        if factor is None:
            # Incompatible/unknown pair — safest fallback is to treat it as already in
            # the base unit rather than silently deducting a wildly wrong amount.
            return self.quantity
        return self.quantity * factor


class Driver(models.Model):
    """A delivery driver, tied to one branch."""
    branch = models.ForeignKey('products.Warehouse', on_delete=models.CASCADE,
                               related_name='drivers', verbose_name="الفرع")
    user = models.OneToOneField(User, on_delete=models.SET_NULL, null=True, blank=True,
                                related_name='driver_profile', verbose_name="حساب الدخول")
    name = models.CharField(max_length=100, verbose_name="اسم الطيار")
    phone = models.CharField(max_length=20, blank=True, verbose_name="رقم الهاتف")
    is_active = models.BooleanField(default=True, verbose_name="نشط")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "طيار / مندوب دليفري"
        verbose_name_plural = "الطيارين"
        ordering = ['branch', 'name']

    def __str__(self):
        return f"{self.name} ({self.branch.name})"

    def owed_today(self):
        """Cash this driver is currently holding (unsettled custody) today."""
        from django.utils import timezone
        return self.custodies.filter(
            status=CashCustody.STATUS_HELD,
            created_at__date=timezone.localdate(),
        ).aggregate(total=models.Sum('amount'))['total'] or Decimal('0.00')


class CashCustody(models.Model):
    """Money held by a person (waiter or driver) before it's handed to the cashier.

    وديعة: "اسم الشخص معاه كام". Settling posts a DEPOSIT transaction into the cashier's
    CASH_DRAWER account so the shift/جرد reconciliation report picks it up.
    """
    KIND_WAITER = 'waiter'
    KIND_DRIVER = 'driver'
    KIND_OTHER = 'other'
    KIND_CHOICES = [
        (KIND_WAITER, 'ويتر'),
        (KIND_DRIVER, 'طيار دليفري'),
        (KIND_OTHER, 'أخرى'),
    ]

    STATUS_HELD = 'held'
    STATUS_SETTLED = 'settled'
    STATUS_CHOICES = [
        (STATUS_HELD, 'معه (لم يُسلَّم)'),
        (STATUS_SETTLED, 'تم التسليم'),
    ]

    branch = models.ForeignKey('products.Warehouse', on_delete=models.CASCADE,
                               related_name='cash_custodies', verbose_name="الفرع")
    kind = models.CharField(max_length=10, choices=KIND_CHOICES, verbose_name="النوع")
    holder_user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True,
                                    related_name='cash_custodies', verbose_name="المستخدم")
    holder_name = models.CharField(max_length=100, verbose_name="اسم الشخص")
    driver = models.ForeignKey(Driver, on_delete=models.SET_NULL, null=True, blank=True,
                               related_name='custodies', verbose_name="الطيار")
    amount = models.DecimalField(max_digits=12, decimal_places=2, verbose_name="المبلغ")
    orders = models.ManyToManyField('sales.Order', blank=True, related_name='cash_custodies',
                                    verbose_name="الفواتير المرتبطة")
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=STATUS_HELD,
                              db_index=True, verbose_name="الحالة")
    shift = models.ForeignKey('financial.DailyShift', on_delete=models.SET_NULL, null=True, blank=True,
                              related_name='cash_custodies', verbose_name="الشيفت")
    settlement_transaction = models.ForeignKey('financial.Transaction', on_delete=models.SET_NULL,
                                               null=True, blank=True, related_name='+',
                                               verbose_name="حركة التسليم")
    note = models.TextField(blank=True, verbose_name="ملاحظات")
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True,
                                   related_name='created_custodies', verbose_name="أنشأها")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="تاريخ الفتح")
    settled_at = models.DateTimeField(null=True, blank=True, verbose_name="تاريخ التسليم")

    class Meta:
        verbose_name = "وديعة"
        verbose_name_plural = "الودائع"
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.get_kind_display()} - {self.holder_name}: {self.amount} ({self.get_status_display()})"

    def settle(self, settled_by, cash_drawer_account=None, shift=None):
        """Deposit the held amount into the cashier's cash drawer for جرد purposes."""
        from django.db import transaction as db_transaction
        from django.utils import timezone
        from financial.models import Account, Transaction

        if self.status == self.STATUS_SETTLED:
            return self.settlement_transaction

        with db_transaction.atomic():
            account = cash_drawer_account or Account.objects.filter(account_type='CASH_DRAWER').first()
            if account is None:
                raise ValueError("لا يوجد حساب درج كاشير لإتمام التسليم")
            txn = Transaction.objects.create(
                shift=shift,
                account=account,
                transaction_type='DEPOSIT',
                amount=self.amount,
                description=f"تسليم وديعة - {self.get_kind_display()} {self.holder_name}",
                created_by=settled_by,
            )
            self.status = self.STATUS_SETTLED
            self.settlement_transaction = txn
            self.settled_at = timezone.now()
            self.shift = shift or self.shift
            self.save(update_fields=['status', 'settlement_transaction', 'settled_at', 'shift'])
        return txn
