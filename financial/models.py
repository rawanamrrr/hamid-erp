from django.db import models, transaction as db_transaction
from django.contrib.auth.models import User
from django.utils import timezone
from django.db.models import Sum, F
from decimal import Decimal
from django.core.exceptions import ValidationError

class Account(models.Model):
    """
    يمثل وعاء للأموال أو حساب في الدليل المحاسبي (ASSET, LIABILITY, EQUITY, REVENUE, EXPENSE)
    """
    ACCOUNT_TYPES = (
        ('ASSET', 'الأصول'),
        ('LIABILITY', 'الخصوم / الالتزامات'),
        ('EQUITY', 'حقوق الملكية'),
        ('REVENUE', 'الإيرادات'),
        ('EXPENSE', 'المصروفات'),
        # Backward compatibility
        ('CASH_DRAWER', 'درج الكاشير'),
        ('SAFE', 'الخزنة الرئيسية'),
        ('BANK', 'حساب بنكي'),
        ('VODAFONE_CASH', 'فودافون كاش'),
        ('INSTAPAY', 'إنستا باي'),
    )
    
    name = models.CharField(max_length=100, verbose_name="اسم الحساب")
    account_type = models.CharField(max_length=20, choices=ACCOUNT_TYPES, verbose_name="نوع الحساب")
    code = models.CharField(max_length=50, null=True, blank=True, unique=True, verbose_name="كود الحساب")
    parent = models.ForeignKey('self', on_delete=models.PROTECT, null=True, blank=True, related_name='children', verbose_name="الحساب الأب")
    balance = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'), verbose_name="الرصيد الحالي")
    is_active = models.BooleanField(default=True, verbose_name="نشط")

    # The initial coded chart-of-accounts seed created generic ASSET rows (codes
    # 1110/1120/1130/1140/1150) for "درج الكاشير"/"الخزنة الرئيسية"/"حساب بنكي"/
    # "فودافون كاش"/"إنستا باي" — duplicates of the separate operational accounts
    # (account_type CASH_DRAWER/SAFE/BANK/VODAFONE_CASH/INSTAPAY) the rest of the app
    # actually posts to. Two accounts with the identical display name is worse than
    # clutter — it's a live money-routing trap: whoever picks the wrong one from a
    # dropdown silently sends cash to a dead account (this happened for real, twice —
    # once for 1150/إنستا باي, once for 1120/الخزنة الرئيسية). Use
    # exclude_dead_duplicates() on every account-selection queryset, not just display
    # lists — an account only counts as "dead" while its balance is zero, so real
    # money already sitting on one is never hidden.
    DEAD_DUPLICATE_CODES = ['1110', '1120', '1130', '1140', '1150']

    @classmethod
    def exclude_dead_duplicates(cls, queryset=None):
        if queryset is None:
            queryset = cls.objects.all()
        dead_codes = [c for c in cls.DEAD_DUPLICATE_CODES if cls.objects.filter(code=c, balance=0).exists()]
        return queryset.exclude(code__in=dead_codes) if dead_codes else queryset

    def __str__(self):
        type_display = self.get_account_type_display()
        code_str = f"[{self.code}] " if self.code else ""
        return f"{code_str}{self.name} ({type_display}) - {self.balance} EGP"

class DailyShift(models.Model):
    """
    يمثل شيفت العمل اليومي. 
    يتم ربط كل المعاملات المالية بهذا الشيفت.
    """
    employee = models.ForeignKey(User, on_delete=models.PROTECT, verbose_name="الموظف المسؤول")
    start_time = models.DateTimeField(default=timezone.now, verbose_name="وقت البداية")
    end_time = models.DateTimeField(null=True, blank=True, verbose_name="وقت النهاية")
    
    start_balance = models.DecimalField(max_digits=10, decimal_places=2, default=0, verbose_name="رصيد البداية (في الدرج)")
    opening_notes = models.TextField(blank=True, null=True, verbose_name="ملاحظات الفتح")

    # الأرقام عند الإغلاق
    total_sales_cash = models.DecimalField(max_digits=10, decimal_places=2, default=0, verbose_name="مبيعات الكاش (نظام)")
    total_sales_visa = models.DecimalField(max_digits=10, decimal_places=2, default=0, verbose_name="مبيعات فيزا (نظام)")
    total_expenses = models.DecimalField(max_digits=10, decimal_places=2, default=0, verbose_name="إجمالي المصروفات")
    total_withdrawals = models.DecimalField(max_digits=10, decimal_places=2, default=0, verbose_name="إجمالي المسحوبات")
    
    expected_closing_balance = models.DecimalField(max_digits=10, decimal_places=2, default=0, verbose_name="المفروض يكون في الدرج")
    actual_closing_balance = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True, verbose_name="العد الفعلي (الجرد)")
    difference = models.DecimalField(max_digits=10, decimal_places=2, default=0, verbose_name="العجز / الزيادة")
    
    notes = models.TextField(blank=True, null=True, verbose_name="ملاحظات الإغلاق")
    is_closed = models.BooleanField(default=False, verbose_name="تم الإغلاق")

    def __str__(self):
        return f"Shift #{self.id} - {self.employee.username} - {self.start_time.date()}"

    def calculate_expected_balance(self):
        """
        دالة لحساب الرصيد المتوقع بناءً على:
        1. رصيد البداية
        2. مبيعات الأوردرات (الكاش فقط)
        3. المعاملات المالية (مصروفات، إيداعات، مسحوبات)
        """
        # استيراد Order هنا لتجنب Circular Import
        try:
            from sales.models import Order
        except ImportError:
            Order = None

        end_t = self.end_time if self.end_time else timezone.now()
        
        # 1. Transactions: المعاملات المالية المسجلة (إيداع/سحب/مصروف)
        transactions = self.transactions.all()
        
        # نجمع فقط ما يؤثر على الكاش (CASH_DRAWER)
        # نستثني 'SALE' من هنا لأننا سنحسبها بدقة من جدول الأوردرات
        # إلا لو كنت تسجل مبيعات يدوية كـ Transaction
        
        # Exclude _open_shift()'s own opening reconciliation entry (DEPOSIT "تسوية
        # افتتاحية للشيفت" / EXPENSE "عجز افتتاحي في الشيفت") — that transaction's
        # entire purpose is to catch the CASH_DRAWER ledger balance UP TO start_balance,
        # which `start_bal` below already counts. Including it here double-counts the
        # same money (once as start_bal, once as an "added"/"removed" transaction).
        added_money_trans = transactions.filter(
            transaction_type__in=['DEPOSIT', 'INCOME'],
            account__account_type='CASH_DRAWER'
        ).exclude(description__startswith='تسوية افتتاحية للشيفت').aggregate(Sum('amount'))['amount__sum'] or Decimal('0.00')

        # Cash leaving the drawer — includes REFUND (cash returns are money out, and were
        # previously missing from the expected-balance calc, overstating the drawer).
        removed_money_trans = transactions.filter(
            transaction_type__in=['EXPENSE', 'WITHDRAWAL', 'SUPPLIER_PAYMENT', 'REFUND'],
            account__account_type='CASH_DRAWER'
        ).exclude(description__startswith='عجز افتتاحي في الشيفت').aggregate(Sum('amount'))['amount__sum'] or Decimal('0.00')
        
        # 2. Orders: مبيعات الكاش من جدول الأوردرات
        #
        # Delivery COD orders are excluded here (driver_settled_at is set) because their
        # cash isn't in the drawer yet when the driver leaves with it — restaurant.views.
        # driver_return_settle() deliberately posts NO Transaction at that point, and sets
        # order.cash_paid to the FULL amount the driver collected (which includes the
        # delivery fee the driver keeps as their own earning, never entering the drawer).
        # The only money that actually reaches CASH_DRAWER for such an order is posted
        # later, once, by CashCustody.settle() (restaurant/models.py) when the driver
        # hands the remainder over — and that DEPOSIT Transaction is already counted below
        # in added_money_trans. Counting order.cash_paid here TOO double-counted that same
        # cash (once via cash_paid, once via the settle() deposit) and also wrongly
        # counted the driver's own kept delivery fee as drawer cash.
        sales_cash_orders = Decimal('0.00')
        if Order:
            sales_cash_orders = Order.objects.filter(
                user=self.employee, # أو يمكن إزالتها لحساب كل مبيعات الفترة
                created_at__range=(self.start_time, end_t)
            ).exclude(status='void').exclude(driver_settled_at__isnull=False).aggregate(
                Sum('cash_paid'))['cash_paid__sum'] or Decimal('0.00')

        # رصيد البداية
        start_bal = self.start_balance if self.start_balance is not None else Decimal('0.00')
        
        # المعادلة النهائية:
        # المتوقع = رصيد البداية + مبيعات الكاش (أوردرات) + إيرادات خارجية (معاملات) - مصروفات ومسحوبات (معاملات)
        expected = start_bal + sales_cash_orders + added_money_trans - removed_money_trans
        
        return expected, sales_cash_orders

class Transaction(models.Model):
    """
    سجل لكل حركة مالية (مصروف، سحب، إيداع، تحويل، مبيعات)
    """
    TRANSACTION_TYPES = (
        ('SALE', 'إيراد مبيعات (أوردر)'),
        ('INCOME', 'إيراد خارجي (غير مبيعات)'),
        ('EXPENSE', 'مصروفات تشغيل'),
        ('WITHDRAWAL', 'سحب مالك / مسحوبات'),
        ('REFUND', 'مرتجع بيع'),
        ('DEPOSIT', 'إيداع في الخزنة'),
        ('TRANSFER', 'تحويل بين الحسابات'),
        ('SUPPLIER_PAYMENT', 'سند صرف مورد'),
    )

    shift = models.ForeignKey(DailyShift, related_name='transactions', on_delete=models.SET_NULL, null=True, blank=True, verbose_name="الشيفت")
    account = models.ForeignKey(Account, on_delete=models.PROTECT, verbose_name="الحساب المتأثر")
    transaction_type = models.CharField(max_length=20, choices=TRANSACTION_TYPES, verbose_name="نوع الحركة")
    amount = models.DecimalField(max_digits=10, decimal_places=2, verbose_name="المبلغ")
    description = models.CharField(max_length=255, verbose_name="الوصف")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="وقت الحركة")
    created_by = models.ForeignKey(User, on_delete=models.PROTECT, verbose_name="قام بالحركة", null=True, blank=True)

    # في حالة التحويل فقط
    to_account = models.ForeignKey(Account, related_name='incoming_transfers', on_delete=models.PROTECT, null=True, blank=True, verbose_name="إلى حساب (في حالة التحويل)")
    journal_entry = models.ForeignKey('JournalEntry', on_delete=models.SET_NULL, null=True, blank=True, related_name='transactions', verbose_name="قيد اليومية")

    # --- Source-document links (Phase 1.2) ---
    # These replace fragile description-substring matching when reversing/looking up
    # the transactions that belong to a specific business document. Always set the
    # relevant FK when creating a transaction for an order/return/payment/expense.
    order = models.ForeignKey('sales.Order', on_delete=models.SET_NULL, null=True, blank=True, related_name='financial_transactions', verbose_name="فاتورة البيع المصدر")
    return_invoice = models.ForeignKey('sales.ReturnInvoice', on_delete=models.SET_NULL, null=True, blank=True, related_name='financial_transactions', verbose_name="مرتجع البيع المصدر")
    expense = models.ForeignKey('sales.Expense', on_delete=models.SET_NULL, null=True, blank=True, related_name='financial_transactions', verbose_name="المصروف المصدر")
    customer_payment = models.ForeignKey('crm.CustomerPayment', on_delete=models.SET_NULL, null=True, blank=True, related_name='financial_transactions', verbose_name="دفعة العميل المصدر")
    supplier_payment = models.ForeignKey('products.SupplierPayment', on_delete=models.SET_NULL, null=True, blank=True, related_name='financial_transactions', verbose_name="سند المورد المصدر")

    def save(self, *args, **kwargs):
        is_new = self.pk is None
        with db_transaction.atomic():
            if not is_new:
                # Fetch original transaction values from DB to reverse their adjustments
                old = Transaction.objects.select_for_update().get(pk=self.pk)
                
                # Reverse old balance adjustments
                if old.transaction_type in ['EXPENSE', 'WITHDRAWAL', 'SUPPLIER_PAYMENT', 'REFUND']:
                    Account.objects.select_for_update().filter(pk=old.account_id).update(
                        balance=F('balance') + old.amount
                    )
                    if old.transaction_type == 'WITHDRAWAL':
                        from .posting import nominal
                        Account.objects.select_for_update().filter(pk=nominal('OWNER_DRAWINGS').pk).update(
                            balance=F('balance') - old.amount
                        )
                elif old.transaction_type in ['INCOME', 'DEPOSIT', 'SALE']:
                    Account.objects.select_for_update().filter(pk=old.account_id).update(
                        balance=F('balance') - old.amount
                    )
                elif old.transaction_type == 'TRANSFER' and old.to_account_id:
                    Account.objects.select_for_update().filter(pk=old.account_id).update(
                        balance=F('balance') + old.amount
                    )
                    Account.objects.select_for_update().filter(pk=old.to_account_id).update(
                        balance=F('balance') - old.amount
                    )

            # Save the new state
            super().save(*args, **kwargs)
            
            # Apply new/updated balance adjustments
            if self.transaction_type in ['EXPENSE', 'WITHDRAWAL', 'SUPPLIER_PAYMENT', 'REFUND']:
                Account.objects.select_for_update().filter(pk=self.account_id).update(
                    balance=F('balance') - self.amount
                )
                # A withdrawal (سحب مالك) only ever touched the drawer/bank side above —
                # the dashboard's "مسحوبات المالك" card reads a real Account.balance (see
                # financial/views.py financial_dashboard), but the OWNER_DRAWINGS nominal
                # account only ever got a JournalLine from post_cash_transaction() below
                # (for the trial balance/statements), never this operational balance
                # field — so it stayed frozen at 0 no matter how many withdrawals were
                # recorded. Mirror the cash-side update onto it too.
                if self.transaction_type == 'WITHDRAWAL':
                    from .posting import nominal
                    Account.objects.select_for_update().filter(pk=nominal('OWNER_DRAWINGS').pk).update(
                        balance=F('balance') + self.amount
                    )
            elif self.transaction_type in ['INCOME', 'DEPOSIT', 'SALE']:
                Account.objects.select_for_update().filter(pk=self.account_id).update(
                    balance=F('balance') + self.amount
                )
            elif self.transaction_type == 'TRANSFER' and self.to_account_id:
                Account.objects.select_for_update().filter(pk=self.account_id).update(
                    balance=F('balance') - self.amount
                )
                Account.objects.select_for_update().filter(pk=self.to_account_id).update(
                    balance=F('balance') + self.amount
                )
            
            # Double-entry posting (Phase 4.2). SALE/REFUND are posted at document
            # level (post_sale/post_refund); standalone cash movements post here.
            # Self-guarded: a journal failure never blocks the operational save.
            from .posting import post_cash_transaction
            post_cash_transaction(self)

    def delete(self, *args, **kwargs):
        with db_transaction.atomic():
            # Reverse the balance update before deletion
            if self.transaction_type in ['EXPENSE', 'WITHDRAWAL', 'SUPPLIER_PAYMENT', 'REFUND']:
                Account.objects.select_for_update().filter(pk=self.account_id).update(
                    balance=F('balance') + self.amount
                )
                if self.transaction_type == 'WITHDRAWAL':
                    from .posting import nominal
                    Account.objects.select_for_update().filter(pk=nominal('OWNER_DRAWINGS').pk).update(
                        balance=F('balance') - self.amount
                    )
            elif self.transaction_type in ['INCOME', 'DEPOSIT', 'SALE']:
                Account.objects.select_for_update().filter(pk=self.account_id).update(
                    balance=F('balance') - self.amount
                )
            elif self.transaction_type == 'TRANSFER' and self.to_account_id:
                Account.objects.select_for_update().filter(pk=self.account_id).update(
                    balance=F('balance') + self.amount
                )
                Account.objects.select_for_update().filter(pk=self.to_account_id).update(
                    balance=F('balance') - self.amount
                )
            # Mirror save()'s post_cash_transaction: reverses the operational
            # Account.balance above, but a deleted Transaction also needs its journal
            # entry (posted at reference_number f"TXN-{id}") removed — otherwise the
            # double-entry ledger (trial balance / income statement) keeps a stale
            # debit/credit pair forever with no corresponding Transaction, silently
            # drifting away from Account.balance every time a transaction is deleted.
            from .posting import unpost
            unpost(f"TXN-{self.pk}")
            super().delete(*args, **kwargs)

    def __str__(self):
        return f"{self.get_transaction_type_display()} - {self.amount}"


class PeriodLock(models.Model):
    """Accounting period lock (Phase 4.5) — a singleton 'books are closed up to' date.

    Documents dated on or before `locked_up_to` cannot be created, edited or voided
    unless the user holds the override permission ('financial', 'manage'). This protects
    finalised periods (day-close / month-close) from after-the-fact tampering.
    """
    locked_up_to = models.DateField(null=True, blank=True, verbose_name="مغلق حتى تاريخ")
    locked_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, verbose_name="أغلقها")
    locked_at = models.DateTimeField(null=True, blank=True, verbose_name="وقت الإغلاق")
    note = models.TextField(blank=True, verbose_name="ملاحظات")

    class Meta:
        verbose_name = "إغلاق الفترة المحاسبية"
        verbose_name_plural = "إغلاق الفترات المحاسبية"

    def __str__(self):
        return f"مغلق حتى {self.locked_up_to}" if self.locked_up_to else "لا يوجد إغلاق"

    @classmethod
    def get_solo(cls):
        obj = cls.objects.first()
        if not obj:
            obj = cls.objects.create()
        return obj

    @classmethod
    def is_locked(cls, the_date):
        """True if `the_date` falls within a closed period."""
        if the_date is None:
            return False
        lock = cls.objects.first()
        if not lock or not lock.locked_up_to:
            return False
        if hasattr(the_date, 'date'):
            the_date = the_date.date()
        return the_date <= lock.locked_up_to


class ShiftEmailLog(models.Model):
    shift = models.ForeignKey(DailyShift, on_delete=models.CASCADE, related_name='email_logs', verbose_name="الشيفت")
    sent_at = models.DateTimeField(auto_now_add=True, verbose_name="وقت الإرسال")
    success = models.BooleanField(default=False, verbose_name="تم الإرسال بنجاح")
    error_message = models.TextField(blank=True, default='', verbose_name="رسالة الخطأ")

    class Meta:
        verbose_name = "سجل إرسال إيميل الشيفت"
        verbose_name_plural = "سجلات إرسال إيميلات الشيفت"
        ordering = ['-sent_at']

    def __str__(self):
        status = "نجاح" if self.success else "فشل"
        return f"Shift #{self.shift_id} - {status} - {self.sent_at:%Y-%m-%d %H:%M}"


class JournalEntry(models.Model):
    """
    رأس قيد اليومية (الدفتر العام)
    """
    STATUS_CHOICES = (
        ('DRAFT', 'مسودة'),
        ('POSTED', 'مرحل / معتمد'),
        ('REVERSED', 'ملغى / معكس'),
    )
    
    reference_number = models.CharField(max_length=100, unique=True, db_index=True, verbose_name="رقم القيد / المرجع")
    description = models.TextField(verbose_name="البيان / الوصف")
    posted_at = models.DateTimeField(default=timezone.now, db_index=True, verbose_name="تاريخ الترحيل")
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='DRAFT', verbose_name="حالة القيد")
    
    created_by = models.ForeignKey(User, on_delete=models.PROTECT, verbose_name="منشئ القيد")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="تاريخ الإنشاء")
    
    class Meta:
        verbose_name = "قيد يومية"
        verbose_name_plural = "قيود اليومية"
        ordering = ['-posted_at', '-id']

    def __str__(self):
        return f"قيد #{self.reference_number} - {self.get_status_display()}"

    def clean(self):
        # Validate balance on posted entries
        if self.status == 'POSTED':
            lines = self.lines.all()
            total_debit = sum(line.debit for line in lines)
            total_credit = sum(line.credit for line in lines)
            if total_debit != total_credit:
                raise ValidationError("قيد اليومية غير متوازن: إجمالي المدين يجب أن يساوي الدائن.")

    def post(self):
        with db_transaction.atomic():
            self.status = 'POSTED'
            self.full_clean()
            self.save()


class JournalLine(models.Model):
    """
    بنود قيد اليومية (المدين والدائن)
    """
    entry = models.ForeignKey(JournalEntry, on_delete=models.CASCADE, related_name='lines', verbose_name="القيد")
    account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name='ledger_lines', verbose_name="الحساب")
    debit = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'), verbose_name="مدين")
    credit = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'), verbose_name="دائن")
    
    class Meta:
        verbose_name = "بند قيد"
        verbose_name_plural = "بنود القيود"

    def __str__(self):
        return f"{self.account.name} - مدين: {self.debit} | دائن: {self.credit}"

# Import payroll and discount models to expose them to Django App
from .payroll_models import EmployeeSalary, DealDiscount
