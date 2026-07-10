from django.db import models
from django.contrib.auth.models import User
from django.utils import timezone
from decimal import Decimal
from financial.models import Account, Transaction

class EmployeeSalary(models.Model):
    """
    إعدادات رواتب الموظفين المستمرة (Continuous Salary Config)
    """
    employee = models.OneToOneField(User, on_delete=models.CASCADE, related_name='salary_config', verbose_name="الموظف")
    basic_salary = models.DecimalField(max_digits=10, decimal_places=2, verbose_name="الراتب الأساسي")
    allowances = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'), verbose_name="الحوافز والبدلات")
    deductions = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'), verbose_name="الخصومات والجزاءات")
    net_salary = models.DecimalField(max_digits=10, decimal_places=2, editable=False, verbose_name="صافي المرتب")
    
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="تاريخ الإنشاء")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="تاريخ التحديث")
    notes = models.TextField(blank=True, null=True, verbose_name="ملاحظات")

    class Meta:
        verbose_name = "إعداد راتب موظف"
        verbose_name_plural = "إعدادات رواتب الموظفين"
        ordering = ['-created_at']

    def __str__(self):
        return f"مرتب {self.employee.username} ({self.net_salary} ج.م)"

    def save(self, *args, **kwargs):
        self.net_salary = self.basic_salary + self.allowances - self.deductions
        super().save(*args, **kwargs)


class DealDiscount(models.Model):
    """
    الخصومات والعروض الترويجية الذكية (Smart Discounts & Promotions)
    """
    DISCOUNT_TYPES = (
        ('PERCENTAGE', 'نسبة مئوية (%)'),
        ('FIXED', 'مبلغ ثابت (ج.م)'),
    )

    PROMO_TYPES = (
        ('discount', 'خصم عادي (% أو مبلغ ثابت)'),
        ('buy_x_get_y', 'اشتري X واحصل على Y مجاناً'),
        ('buy_n_for_price', 'اشتري N بسعر X (مثل: 3 بـ 50)'),
    )

    name = models.CharField(max_length=150, verbose_name="اسم العرض / الخصم")
    promo_type = models.CharField(
        max_length=20, choices=PROMO_TYPES, default='discount',
        verbose_name="نوع العرض"
    )
    discount_type = models.CharField(max_length=15, choices=DISCOUNT_TYPES, default='PERCENTAGE', verbose_name="نوع الخصم")
    value = models.DecimalField(max_digits=10, decimal_places=2, verbose_name="قيمة الخصم")
    
    # For buy_x_get_y
    buy_x_qty = models.PositiveIntegerField(default=0, verbose_name="اشتري كمية (X)", help_text="لعرض اشتري X واحصل على Y")
    get_y_qty = models.PositiveIntegerField(default=0, verbose_name="احصل على كمية مجاناً (Y)", help_text="الكمية المجانية عند الشراء")

    # For buy_n_for_price
    buy_n_qty = models.PositiveIntegerField(default=0, verbose_name="كمية N للعرض", help_text="لعرض اشتري N بسعر واحد")
    for_price = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'), verbose_name="السعر الإجمالي لـ N قطعة")
    
    minimum_order_value = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'), verbose_name="الحد الأدنى لقيمة الفاتورة لتطبيق العرض")
    
    start_date = models.DateTimeField(verbose_name="تاريخ بداية العرض")
    end_date = models.DateTimeField(verbose_name="تاريخ نهاية العرض")
    
    coupon_code = models.CharField(max_length=50, unique=True, blank=True, null=True, verbose_name="كود كوبون الخصم (اختياري)")
    is_active = models.BooleanField(default=True, verbose_name="نشط")

    # Product scoping fields
    apply_to_all = models.BooleanField(default=True, verbose_name="تطبيق على كل المنتجات")
    categories = models.ManyToManyField('products.Category', blank=True, related_name='promotions', verbose_name="الأقسام المشمولة بالعرض")
    products = models.ManyToManyField('products.Product', blank=True, related_name='promotions', verbose_name="منتجات العرض المحددة (الشراء)")
    get_products = models.ManyToManyField('products.Product', blank=True, related_name='free_promotions', verbose_name="المنتجات المجانية (للعرض اشتري X واحصل على Y)")

    created_at = models.DateTimeField(auto_now_add=True, verbose_name="تاريخ الإنشاء")

    class Meta:
        verbose_name = "عرض وخصم"
        verbose_name_plural = "العروض والخصومات"
        ordering = ['-created_at']

    def __str__(self):
        val_suffix = "%" if self.discount_type == 'PERCENTAGE' else " ج.م"
        return f"{self.name} ({self.value}{val_suffix})"

    def is_valid_for_order(self, order_subtotal):
        import django.utils.timezone
        now = django.utils.timezone.now()
        if not self.is_active:
            return False
        if now < self.start_date or now > self.end_date:
            return False
        if order_subtotal < self.minimum_order_value:
            return False
        return True

    def get_scoped_product_ids(self):
        """Resolve which products this deal applies to: explicit `products` ∪ every
        product in the selected `categories`. Returns None when apply_to_all is set
        (caller should treat None as "every product qualifies")."""
        if self.apply_to_all:
            return None
        ids = set(self.products.values_list('id', flat=True))
        category_ids = list(self.categories.values_list('id', flat=True))
        if category_ids:
            from products.models import Product
            ids |= set(Product.objects.filter(category_id__in=category_ids).values_list('id', flat=True))
        return ids


class EmployeeAdvance(models.Model):
    """سلفة/قرض موظف — auto-deducted from monthly payslips until settled."""
    employee = models.ForeignKey(User, on_delete=models.CASCADE, related_name='advances', verbose_name="الموظف")
    amount = models.DecimalField(max_digits=10, decimal_places=2, verbose_name="قيمة السلفة")
    per_period_deduction = models.DecimalField(
        max_digits=10, decimal_places=2, default=Decimal('0.00'),
        verbose_name="القسط الشهري", help_text="0 = خصم كامل المبلغ دفعة واحدة")
    remaining = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'), verbose_name="المتبقي")
    date_taken = models.DateField(default=timezone.now, verbose_name="تاريخ السلفة")
    is_settled = models.BooleanField(default=False, verbose_name="تم السداد")
    notes = models.TextField(blank=True, default='', verbose_name="ملاحظات")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "سلفة موظف"
        verbose_name_plural = "سلف الموظفين"
        ordering = ['-date_taken']

    def __str__(self):
        return f"سلفة {self.employee.username}: {self.amount} (متبقي {self.remaining})"

    def save(self, *args, **kwargs):
        if not self.pk and (self.remaining is None or self.remaining == 0):
            self.remaining = self.amount
        if self.remaining <= 0:
            self.is_settled = True
        super().save(*args, **kwargs)

    def due_amount(self):
        """Amount to deduct this period (the installment, capped at the remaining balance)."""
        if self.is_settled or self.remaining <= 0:
            return Decimal('0.00')
        inst = self.per_period_deduction if self.per_period_deduction > 0 else self.remaining
        return min(inst, self.remaining)

    def apply(self, amount):
        """Reduce the remaining balance by `amount` (called when a payslip is paid)."""
        self.remaining = max(Decimal('0.00'), self.remaining - Decimal(str(amount)))
        if self.remaining <= 0:
            self.is_settled = True
        self.save(update_fields=['remaining', 'is_settled'])


class Payslip(models.Model):
    """قسيمة راتب — one persisted payslip per employee per month (Phase: payroll register)."""
    STATUS_CHOICES = [('draft', 'مسودة'), ('paid', 'مدفوع')]

    employee = models.ForeignKey(User, on_delete=models.CASCADE, related_name='payslips', verbose_name="الموظف")
    period_month = models.CharField(max_length=7, verbose_name="الشهر", help_text="YYYY-MM")
    basic_salary = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'), verbose_name="الراتب الأساسي")
    allowances = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'), verbose_name="البدلات الثابتة")
    days_absent = models.DecimalField(max_digits=5, decimal_places=1, default=Decimal('0.0'), verbose_name="أيام الغياب")
    advance_deducted = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'), verbose_name="خصم السلف")
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='draft', verbose_name="الحالة")
    paid_at = models.DateTimeField(null=True, blank=True, verbose_name="تاريخ الصرف")
    paid_account = models.ForeignKey(Account, null=True, blank=True, on_delete=models.SET_NULL, related_name='payslips', verbose_name="حساب الصرف")
    net_paid = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True, verbose_name="الصافي المدفوع")
    notes = models.TextField(blank=True, default='', verbose_name="ملاحظات")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "قسيمة راتب"
        verbose_name_plural = "قسائم الرواتب"
        unique_together = ('employee', 'period_month')
        ordering = ['-period_month', '-created_at']

    def __str__(self):
        return f"قسيمة {self.employee.username} - {self.period_month}"

    @property
    def total_additions(self):
        return sum((a.amount for a in self.adjustments.all() if a.is_addition), Decimal('0.00'))

    @property
    def total_deductions(self):
        return sum((a.amount for a in self.adjustments.all() if not a.is_addition), Decimal('0.00'))

    @property
    def absence_deduction(self):
        """Admin-configurable: (basic_salary * absence_deduction_percent / 100) per absent day
        — see settings.policies 'payroll.absence_deduction_percent' (default 3.33% ≈ 1/30)."""
        if self.days_absent and self.basic_salary:
            from settings.policies import get_policy
            percent = get_policy('payroll.absence_deduction_percent') or Decimal('3.33')
            per_day = self.basic_salary * percent / Decimal('100')
            return (per_day * Decimal(str(self.days_absent))).quantize(Decimal('0.01'))
        return Decimal('0.00')

    @property
    def gross(self):
        return self.basic_salary + self.allowances + self.total_additions

    @property
    def net(self):
        n = (self.gross - self.total_deductions - self.absence_deduction
             - (self.advance_deducted or Decimal('0.00')))
        return n if n > Decimal('0') else Decimal('0.00')


class PayslipAdjustment(models.Model):
    """A bonus/deduction/overtime/penalty line on a payslip — the 'easier add خصم/بونص' entry."""
    KIND_CHOICES = [
        ('bonus', 'حافز / بونص'),
        ('overtime', 'أوفرتايم'),
        ('deduction', 'خصم'),
        ('penalty', 'جزاء'),
    ]
    ADDITION_KINDS = {'bonus', 'overtime'}

    payslip = models.ForeignKey(Payslip, on_delete=models.CASCADE, related_name='adjustments', verbose_name="القسيمة")
    kind = models.CharField(max_length=12, choices=KIND_CHOICES, verbose_name="النوع")
    label = models.CharField(max_length=150, blank=True, default='', verbose_name="البيان")
    amount = models.DecimalField(max_digits=10, decimal_places=2, verbose_name="المبلغ")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['created_at']

    def __str__(self):
        return f"{self.get_kind_display()} {self.amount}"

    @property
    def is_addition(self):
        return self.kind in self.ADDITION_KINDS

