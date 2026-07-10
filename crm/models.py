from django.db import models
from django.db.models import Sum, Max, Min, Count
from django.contrib.auth.models import User
from decimal import Decimal

class Customer(models.Model):
    CUSTOMER_TYPES = [
        ('retail', 'قطاعي'),
        ('semi_wholesale', 'نص جملة'),
        ('wholesale', 'جملة'),
    ]

    first_name = models.CharField(max_length=100, verbose_name="الاسم الأول")
    last_name = models.CharField(max_length=100, verbose_name="الاسم الثاني / العائلة")
    phone = models.CharField(max_length=20, unique=True, verbose_name="رقم الهاتف")
    # Phase 8.6: richer customer data (B2B / e-invoice readiness + collections control)
    phone2 = models.CharField(max_length=20, blank=True, default='', verbose_name="رقم هاتف إضافي")
    tax_number = models.CharField(max_length=50, blank=True, default='', verbose_name="الرقم الضريبي (للشركات)")
    is_blacklisted = models.BooleanField(default=False, verbose_name="قائمة سوداء (إيقاف البيع الآجل)")
    address = models.TextField(blank=True, null=True, verbose_name="العنوان")
    customer_type = models.CharField(max_length=20, choices=CUSTOMER_TYPES, default='retail', verbose_name="نوع العميل")
    
    # حقل جديد للرصيد الافتتاحي (ما قبل النظام)
    opening_balance = models.DecimalField(
        max_digits=12, 
        decimal_places=2, 
        default=0.00, 
        verbose_name="رصيد افتتاحي (مديونية سابقة)",
        help_text="أدخل قيمة موجبة (+) إذا كان عليه فلوس، وقيمة سالبة (-) إذا كان له رصيد"
    )
    
    credit_limit = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal('0.00'),
        verbose_name="حد الائتمان (الدين المسموح)",
        help_text="أقصى مديونية مسموح بها. 0 يعني غير محدود."
    )
    
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="تاريخ الإضافة")

    def whatsapp_phone(self):
        """Best-effort international phone for wa.me (assumes Egypt for local numbers)."""
        digits = ''.join(ch for ch in (self.phone or '') if ch.isdigit())
        if not digits:
            return ''
        if digits.startswith('00'):
            digits = digits[2:]
        elif digits.startswith('0'):
            digits = '20' + digits[1:]   # Egypt country code
        elif not digits.startswith('20'):
            digits = '20' + digits
        return digits

    def whatsapp_url(self, message=None):
        """A wa.me link with a prefilled balance-reminder message (Phase 8.5)."""
        from urllib.parse import quote
        phone = self.whatsapp_phone()
        if not phone:
            return ''
        if message is None:
            bal = self.get_balance()
            name = f"{self.first_name} {self.last_name}".strip()
            message = (f"مرحباً {name}،\nنحيطكم علماً بأن المبلغ المستحق على حضرتكم هو "
                       f"{bal:.2f} ج.م. نرجو السداد في أقرب وقت. شكراً لتعاملكم معنا.")
        return f"https://wa.me/{phone}?text={quote(message)}"

    def credit_available(self):
        """Remaining credit headroom; None means unlimited (credit_limit 0)."""
        limit = self.credit_limit or Decimal('0.00')
        if limit <= 0:
            return None
        return limit - self.get_balance()

    def would_exceed_credit(self, additional_debt):
        """True if taking on `additional_debt` would push the customer past their limit (Phase 6.9)."""
        avail = self.credit_available()
        if avail is None:
            return False
        return Decimal(str(additional_debt or 0)) > avail

    class Meta:
        verbose_name = "عميل"
        verbose_name_plural = "العملاء"
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.first_name} {self.last_name} ({self.get_customer_type_display()})"

    # --- التحليلات المالية ---

    def _real_orders(self):
        """Orders that count toward this customer's spend/balance: have line items
        and are NOT voided (Phase 1.10)."""
        return self.order_set.filter(items__isnull=False).exclude(status='void').distinct()

    def get_total_spent(self):
        """إجمالي مشتريات العميل"""
        return self._real_orders().aggregate(total=Sum('total_amount'))['total'] or 0

    def get_balance(self):
        """رصيد العميل (ما له أو ما عليه)"""
        # Exclude dummy orders that don't have items, and voided invoices.
        real_orders = self._real_orders()
        
        total_due = real_orders.aggregate(total=Sum('total_amount'))['total'] or Decimal('0.00')
        total_paid = real_orders.aggregate(total=Sum('received_amount'))['total'] or Decimal('0.00')
        
        # Sum transactions from new CustomerPayment model
        payments_sum = self.payments_log.filter(transaction_type='payment').aggregate(total=Sum('amount'))['total'] or Decimal('0.00')
        manual_debt_sum = self.payments_log.filter(transaction_type='debt').aggregate(total=Sum('amount'))['total'] or Decimal('0.00')

        # Coerce opening_balance: a freshly-created (not-yet-reloaded) instance can hold
        # the float field-default, which would break Decimal arithmetic.
        opening = Decimal(str(self.opening_balance or '0.00'))
        return (total_due + opening + manual_debt_sum) - (total_paid + payments_sum)

    # --- نظام الشرائح (Tier System) ---

    @property
    def tier(self):
        total = self.get_total_spent()
        if total >= 80000:
            return 'gold'
        elif total >= 10000:
            return 'silver'
        return 'bronze'

    def get_tier_display(self):
        t = self.tier
        if t == 'gold': return 'ذهبي'
        if t == 'silver': return 'فضي'
        return 'برونزي'

    # --- إحصائيات سلوك الشراء ---

    def most_purchased_product(self):
        from sales.models import OrderItem
        # نجمع الكميات لكل منتج في فواتير هذا العميل
        items = OrderItem.objects.filter(order__customer=self)
        if not items.exists():
            return "لا يوجد"
        
        top_product = items.values('product__name').annotate(total_qty=Sum('quantity')).order_by('-total_qty').first()
        return top_product['product__name'] if top_product else "لا يوجد"

    def last_visit_date(self):
        last_order = self.order_set.order_by('-created_at').first()
        return last_order.created_at if last_order else None

    def highest_invoice_amount(self):
        return self.order_set.filter(items__isnull=False).distinct().aggregate(max_val=Max('total_amount'))['max_val'] or 0

    def average_purchase_frequency(self):
        """متوسط الأيام بين الزيارات"""
        # 1. نحسب فقط الفواتير اللي فيها شراء (بدون مديونيات أو سداد)
        orders = self.order_set.filter(items__isnull=False).distinct()
        
        # 2. نستخدم Aggregation للأداء والدقة
        stats = orders.aggregate(
            first_date=Min('created_at'), 
            last_date=Max('created_at'),
            count=Count('id')
        )
        
        count = stats['count']
        
        if count < 2:
            return None # لا يمكن الحساب لزيارة واحدة أو صفر
        
        first = stats['first_date']
        last = stats['last_date']
        
        if not first or not last:
            return None

        total_days = (last - first).days
        
        if total_days < 1:
            return 0 # زيارات متعددة في نفس اليوم (شراء يومي/فوري)
            
        # 3. نقسم المدة الكلية على عدد الفترات
        return int(total_days / (count - 1))


class CustomerPayment(models.Model):
    TRANSACTION_TYPES = [
        ('payment', 'سداد مديونية / إيداع'),
        ('debt', 'سحب مديونية / دين يدوي'),
    ]
    customer = models.ForeignKey(Customer, on_delete=models.CASCADE, related_name='payments_log', verbose_name="العميل")
    user = models.ForeignKey(User, on_delete=models.PROTECT, verbose_name="الموظف")
    amount = models.DecimalField(max_digits=12, decimal_places=2, verbose_name="المبلغ")
    transaction_type = models.CharField(max_length=20, choices=TRANSACTION_TYPES, default='payment', verbose_name="نوع العملية")
    payment_method = models.CharField(max_length=20, default='cash', verbose_name="طريقة الدفع")
    notes = models.TextField(blank=True, verbose_name="ملاحظات")
    # Phase 4.4: receipt voucher number (سند قبض) for genuine cash receipts (RV-YYYY-NNNNN).
    voucher_number = models.CharField(max_length=30, null=True, blank=True, unique=True, db_index=True, verbose_name="رقم السند")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="التاريخ")

    @property
    def display_voucher(self):
        return self.voucher_number or f"#{self.id}"

    class Meta:
        verbose_name = "دفعة عميل"
        verbose_name_plural = "دفعات العملاء"
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.customer.first_name} - {self.get_transaction_type_display()} - {self.amount}"


class PaymentAllocation(models.Model):
    """Links a customer payment to the specific invoice(s) it settles (Phase 8.2).

    Lets each invoice report how much of it has been paid by later receipts, and powers
    an accurate aging/collection view that isn't just whole-account FIFO guesswork.
    """
    payment = models.ForeignKey(CustomerPayment, on_delete=models.CASCADE, related_name='allocations', verbose_name="الدفعة")
    order = models.ForeignKey('sales.Order', on_delete=models.CASCADE, related_name='payment_allocations', verbose_name="الفاتورة")
    amount = models.DecimalField(max_digits=12, decimal_places=2, verbose_name="المبلغ المخصص")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "تخصيص دفعة"
        verbose_name_plural = "تخصيصات الدفعات"

    def __str__(self):
        return f"{self.payment_id} → {self.order_id}: {self.amount}"