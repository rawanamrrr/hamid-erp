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


class AttendanceRecord(models.Model):
    """One row of FACT per employee per day — 'late' is not its own status (an employee
    is present-and-late, not a third thing), and this never stores a computed deduction:
    that's derived fresh from Payslip.late_minutes/days_absent at payslip generation time,
    since salary or the store's deduction policy can change after the fact. One record per
    (employee, date) — editing today's status again updates the same row, it doesn't stack.
    """
    STATUS_PRESENT = 'present'
    STATUS_ABSENT = 'absent'
    STATUS_EXCUSED = 'excused'
    STATUS_LEAVE = 'leave'
    STATUS_HOLIDAY = 'holiday'
    STATUS_DAY_OFF = 'day_off'
    STATUS_CHOICES = [
        (STATUS_PRESENT, 'حاضر'),
        (STATUS_ABSENT, 'غياب'),
        (STATUS_EXCUSED, 'غياب بعذر'),
        (STATUS_LEAVE, 'إجازة'),
        (STATUS_HOLIDAY, 'عطلة رسمية'),
        (STATUS_DAY_OFF, 'يوم راحة'),
    ]
    # Statuses that deduct salary by default when the record is created — 'absent' only.
    # 'deduct_salary' below is still a real per-row field (not derived) so an admin can
    # override either direction: an unpaid leave, or a no-questions-asked forgiven absence.
    DEFAULT_DEDUCT_STATUSES = {STATUS_ABSENT}

    employee = models.ForeignKey(User, on_delete=models.CASCADE, related_name='attendance_records', verbose_name="الموظف")
    date = models.DateField(verbose_name="التاريخ")
    # Which configured payroll shift (1-5, financial.views._match_shift_and_compute) this
    # row belongs to. Lets one calendar day hold multiple attendance rows for the same
    # employee — e.g. shift 1 in the morning and shift 2 in the evening — instead of a
    # second shift's clock-in punch getting mislabeled as shift 1's missed clock-out. The
    # manual attendance_daily form only ever writes/edits shift_index=1 (it has no shift
    # picker); shift_index 2+ rows are created exclusively by the device-sync path
    # (attendance_devices/sync.py) when it detects punches for more than one shift on the
    # same day.
    shift_index = models.PositiveSmallIntegerField(default=1, verbose_name="رقم الشيفت")
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=STATUS_PRESENT, verbose_name="الحالة")

    # Optional real clock times — if given, late/early-departure minutes are derived from
    # them (against payroll.work_start_time/work_end_time + the grace period) instead of
    # being typed by hand. Kept even when unused today so a future time-clock/biometric
    # integration can just start writing these two fields without a schema change.
    arrival_time = models.TimeField(null=True, blank=True, verbose_name="وقت الحضور")
    departure_time = models.TimeField(null=True, blank=True, verbose_name="وقت الانصراف")

    late_minutes = models.PositiveIntegerField(default=0, verbose_name="دقائق التأخير")
    early_departure_minutes = models.PositiveIntegerField(default=0, verbose_name="دقائق الانصراف المبكر")

    # Only meaningful when status='absent' or 'leave' — whether this day's absence should
    # actually reduce salary. Sensible default per status, but a real per-row override
    # (an "unpaid leave" or a forgiven absence both need to flip the default).
    deduct_salary = models.BooleanField(default=False, verbose_name="يخصم من الراتب")

    note = models.CharField(max_length=255, blank=True, default='', verbose_name="ملاحظات")
    recorded_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL,
                                     related_name='attendance_recorded', verbose_name="سجّله")

    # Set whenever a human explicitly edits this day via the manual attendance_daily
    # form (financial/views.py). Once set, attendance_devices.sync.
    # process_punches_into_attendance() will never silently overwrite this row from a
    # later device sync — a device punch that would otherwise land here is left
    # unprocessed instead (see DevicePunch.processed) so nothing is lost if the lock is
    # ever cleared. Exists specifically so "device syncs, then a manager hand-corrects a
    # mistake, then the device syncs again" can never quietly clobber that correction.
    locked_by_manual_edit = models.BooleanField(default=False, verbose_name="مقفل بتعديل يدوي")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "سجل حضور"
        verbose_name_plural = "سجلات الحضور والغياب"
        unique_together = ('employee', 'date', 'shift_index')
        ordering = ['-date', 'employee_id', 'shift_index']

    def __str__(self):
        suffix = f" — شيفت {self.shift_index}" if self.shift_index != 1 else ""
        return f"{self.employee.username} — {self.date} ({self.get_status_display()}){suffix}"

    def save(self, *args, **kwargs):
        # Only auto-default on first save — never silently flip an admin's explicit
        # override back on a later edit.
        if not self.pk:
            self.deduct_salary = self.status in self.DEFAULT_DEDUCT_STATUSES
        super().save(*args, **kwargs)

    @property
    def total_late_minutes(self):
        """Late arrival + early departure combined — both cost the same per minute."""
        return (self.late_minutes or 0) + (self.early_departure_minutes or 0)


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

    # Null on advances created before this existed, and on any advance an admin recorded
    # after the fact (money already handed over in cash, nothing left to withdraw from an
    # account here). When set, financial.views.advance_create posted a WITHDRAWAL
    # Transaction against this account for `amount` at creation time — before that, giving
    # an advance never touched any account balance at all, so the drawer's reported cash
    # stayed overstated from the moment the cash physically left until the advance was
    # eventually deducted from a payslip.
    source_account = models.ForeignKey(Account, on_delete=models.SET_NULL, null=True, blank=True,
                                       related_name='employee_advances', verbose_name="صُرفت من حساب")

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
    # Snapshotted from AttendanceRecord at generation time (see payslip_generate) — the
    # attendance records themselves are the source of truth and can keep changing after
    # this payslip exists; this is "what payroll actually used", same convention as
    # days_absent above. Minutes, not hours, to avoid rounding drift before deduction math.
    late_minutes = models.PositiveIntegerField(default=0, verbose_name="دقائق التأخير (متضمنة الانصراف المبكر)")
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
    def lateness_deduction(self):
        """Admin-configurable: either a flat rate per hour late (payroll.late_deduction_
        per_hour), or this employee's own derived hourly wage (basic_salary / working_days
        / working_hours) — see settings.policies 'payroll.late_deduction_method'. Covers
        late arrival AND early departure minutes together (both cost the same per minute)."""
        if not self.late_minutes or not self.basic_salary:
            return Decimal('0.00')
        from settings.policies import get_policy
        method = get_policy('payroll.late_deduction_method') or 'fixed_rate'
        if method == 'employee_hourly':
            working_days = get_policy('payroll.working_days_per_month') or 26
            working_hours = get_policy('payroll.working_hours_per_day') or Decimal('8')
            divisor = Decimal(str(working_days)) * Decimal(str(working_hours))
            hourly_rate = (self.basic_salary / divisor) if divisor > 0 else Decimal('0.00')
        else:
            hourly_rate = get_policy('payroll.late_deduction_per_hour') or Decimal('0.00')
        minutes = Decimal(str(self.late_minutes))
        return (hourly_rate * minutes / Decimal('60')).quantize(Decimal('0.01'))

    @property
    def gross(self):
        return self.basic_salary + self.allowances + self.total_additions

    @property
    def net(self):
        n = (self.gross - self.total_deductions - self.absence_deduction - self.lateness_deduction
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

