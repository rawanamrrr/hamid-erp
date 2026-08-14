from django.db import models, transaction as db_transaction
from django.contrib.auth.models import User
from django.utils import timezone
from decimal import Decimal


class DocumentSequence(models.Model):
    """Gap-free per-type, per-year counters (Phase 6.1).

    `next_number(doc_type)` hands out the next value atomically (row lock), so two
    concurrent cashiers never collide and numbers have no gaps. Format e.g.
    INV-2026-00001, RET-2026-00007.
    """
    doc_type = models.CharField(max_length=10, verbose_name="نوع المستند")
    year = models.PositiveIntegerField(verbose_name="السنة")
    last_number = models.PositiveIntegerField(default=0, verbose_name="آخر رقم")

    class Meta:
        unique_together = ('doc_type', 'year')
        verbose_name = "تسلسل المستندات"
        verbose_name_plural = "تسلسلات المستندات"

    def __str__(self):
        return f"{self.doc_type}-{self.year}: {self.last_number}"

    @classmethod
    def next_number(cls, doc_type, year=None, width=5):
        """Return the next formatted document number, e.g. 'INV-2026-00001'."""
        from django.utils import timezone
        if year is None:
            year = timezone.now().year
        with db_transaction.atomic():
            seq, _ = cls.objects.select_for_update().get_or_create(
                doc_type=doc_type, year=year, defaults={'last_number': 0})
            seq.last_number = models.F('last_number') + 1
            seq.save(update_fields=['last_number'])
            seq.refresh_from_db(fields=['last_number'])
        return f"{doc_type}-{year}-{seq.last_number:0{width}d}"


class OrderManager(models.Manager):
    def active(self):
        """Orders that count toward sales, stock and balances (excludes voided)."""
        return self.get_queryset().exclude(status=Order.STATUS_VOID)


class Order(models.Model):
    DISCOUNT_TYPE_CHOICES = [('fixed', 'مبلغ ثابت'), ('percent', 'نسبة مئوية')]

    # payment_method has no choices= (free-text, derived from the split-payment fields —
    # see submit_order_ajax), but shipping/dashboard.html's COD collection screen needs a
    # fixed list to populate its method <select>.
    PAYMENT_METHODS = [
        ('cash', 'نقدي'),
        ('wallet', 'محفظة'),
        ('instapay', 'إنستا باي'),
        ('visa', 'فيزا'),
    ]

    STATUS_ACTIVE = 'active'
    STATUS_VOID = 'void'
    STATUS_CHOICES = [
        (STATUS_ACTIVE, 'نشطة'),
        (STATUS_VOID, 'ملغاة (Void)'),
    ]

    user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, verbose_name="الموظف")
    shift = models.ForeignKey('financial.DailyShift', on_delete=models.SET_NULL, null=True, blank=True, verbose_name="الوردية")
    customer = models.ForeignKey('crm.Customer', on_delete=models.SET_NULL, null=True, blank=True, verbose_name="العميل")
    warehouse = models.ForeignKey('products.Warehouse', on_delete=models.SET_NULL, null=True, blank=True, verbose_name="المخزن")
    applied_deal = models.ForeignKey('financial.DealDiscount', on_delete=models.SET_NULL, null=True, blank=True, verbose_name="العرض المطبق")

    # --- Cafe/restaurant fields ---
    ORDER_TYPE_DINE_IN = 'dine_in'
    ORDER_TYPE_TAKEAWAY = 'takeaway'
    ORDER_TYPE_DELIVERY = 'delivery'
    ORDER_TYPE_CHOICES = [
        (ORDER_TYPE_DINE_IN, 'صالة'),
        (ORDER_TYPE_TAKEAWAY, 'تيك أواي'),
        (ORDER_TYPE_DELIVERY, 'دليفري'),
    ]
    order_type = models.CharField(max_length=10, choices=ORDER_TYPE_CHOICES, default=ORDER_TYPE_DINE_IN,
                                  db_index=True, verbose_name="نوع الطلب")
    table = models.ForeignKey('restaurant.Table', on_delete=models.SET_NULL, null=True, blank=True,
                              related_name='orders', verbose_name="الترابيزة")
    # Snapshot of Table.seats at the moment THIS order opened — Table.seats is a live,
    # editable count (see restaurant.views.set_table_seats' +/- stepper), so without this
    # every past order's "chairs" would silently change to whatever the table is set to
    # NOW instead of what it actually was when that order was taken (e.g. a 1-chair order
    # followed later by a 3-chair order on the same table must keep showing 1 and 3, not
    # both showing whatever the table happens to be set to today).
    table_seats_snapshot = models.PositiveIntegerField(null=True, blank=True,
                                                        verbose_name="عدد الكراسي وقت الطلب")
    waiter = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True,
                               related_name='waiter_orders', verbose_name="الويتر")
    driver = models.ForeignKey('restaurant.Driver', on_delete=models.SET_NULL, null=True, blank=True,
                               related_name='deliveries', verbose_name="الطيار")
    # Stamped the moment a flyer is assigned on the delivery dashboard — drives the
    # live elapsed-timer on the order card (and, once frozen by CashCustody.created_at
    # at settle time, the "total time out" shown on a closed delivery card).
    driver_assigned_at = models.DateTimeField(null=True, blank=True, verbose_name="وقت تعيين الطيار")
    # Stamped when the flyer returns and the order is settled (driver_return_settle) —
    # the actual "closed" marker for the delivery dashboard. Deliberately separate from
    # is_completed ("تم الدفع"/payment complete), which is already True at creation for
    # any delivery order paid upfront, long before a driver is even assigned.
    driver_settled_at = models.DateTimeField(null=True, blank=True, verbose_name="وقت استلام الطيار وتسليمه")
    # Running tab: True while the check is still open on a table and can receive more items.
    is_open = models.BooleanField(default=False, db_index=True, verbose_name="شيك مفتوح")
    service_charge = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'),
                                         verbose_name="نسبة/قيمة الخدمة")
    # The real VAT amount added at checkout (0 when settings.vat_included_in_price=True,
    # since nothing was added in that case) — stored rather than re-derived from
    # total_amount because VAT and service_charge are independent percentages of the same
    # pre-extras subtotal, not compounded, so total_amount alone can't be reverse-engineered
    # back into a tax portion once a service charge is also present.
    vat_amount = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'),
                                     verbose_name="قيمة ضريبة القيمة المضافة")
    # Snapshot of the store's VAT rate/pricing-mode AT CHECKOUT TIME (settings.policies
    # 'tax.vat_rate' / 'tax.vat_included_in_price') — vat_breakdown() reads these instead
    # of the live policy so a rate change later never silently rewrites the VAT on past
    # invoices, refunds of them, or historical reports. null = pre-migration order with no
    # snapshot; vat_breakdown() falls back to the live policy for those (old behavior).
    vat_rate_snapshot = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True,
                                            verbose_name="نسبة الضريبة وقت البيع")
    vat_included_snapshot = models.BooleanField(null=True, blank=True,
                                                 verbose_name="هل كانت الضريبة مضمنة وقت البيع")

    CLOSE_TYPE_CASH = 'cash'
    CLOSE_TYPE_VISA = 'visa'
    CLOSE_TYPE_WALLET = 'wallet'
    CLOSE_TYPE_INSTAPAY = 'instapay'
    CLOSE_TYPE_CL = 'cl'
    CLOSE_TYPE_CHOICES = [
        (CLOSE_TYPE_CASH, 'كاش'),
        (CLOSE_TYPE_VISA, 'فيزا'),
        (CLOSE_TYPE_WALLET, 'محفظة'),
        (CLOSE_TYPE_INSTAPAY, 'إنستا باي'),
        (CLOSE_TYPE_CL, 'آجل (CL)'),
    ]
    close_type = models.CharField(max_length=10, choices=CLOSE_TYPE_CHOICES, blank=True, default='',
                                  verbose_name="طريقة قفل الشيك")

    # --- Order-level kitchen workflow (Cashier Dashboard) ---
    # Distinct from OrderItem.kitchen_status (per-station KDS granularity): this is the
    # single ticket-wide status shown on the cashier's queue cards, advanced explicitly
    # via action buttons (Pending -> Preparing -> Ready -> Delivered).
    PREP_PENDING = 'pending'
    PREP_PREPARING = 'preparing'
    PREP_READY = 'ready'
    PREP_DELIVERED = 'delivered'
    PREP_STATUS_CHOICES = [
        (PREP_PENDING, 'قيد الانتظار'),
        (PREP_PREPARING, 'قيد التحضير'),
        (PREP_READY, 'جاهز'),
        (PREP_DELIVERED, 'تم التسليم'),
    ]
    kitchen_status = models.CharField(max_length=10, choices=PREP_STATUS_CHOICES, default=PREP_PENDING,
                                      db_index=True, verbose_name="حالة تحضير الطلب")
    prep_started_at = models.DateTimeField(null=True, blank=True, verbose_name="بدء التحضير")
    ready_at = models.DateTimeField(null=True, blank=True, verbose_name="وقت الجاهزية")
    delivered_at = models.DateTimeField(null=True, blank=True, verbose_name="وقت التسليم")

    subtotal_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0.00, verbose_name="المجموع الفرعي")
    discount = models.DecimalField(max_digits=10, decimal_places=2, default=0.00, verbose_name="الخصم")
    discount_type = models.CharField(max_length=10, choices=DISCOUNT_TYPE_CHOICES, default='fixed', verbose_name="نوع الخصم")
    delivery_cost = models.DecimalField(max_digits=10, decimal_places=2, default=0.00, verbose_name="تكلفة التوصيل")
    total_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0.00, verbose_name="الإجمالي النهائي")
    
    received_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0.00, verbose_name="المبلغ المدفوع")
    cash_paid = models.DecimalField(max_digits=10, decimal_places=2, default=0.00, verbose_name="مدفوع نقدي")
    wallet_paid = models.DecimalField(max_digits=10, decimal_places=2, default=0.00, verbose_name="مدفوع محفظة")
    instapay_paid = models.DecimalField(max_digits=10, decimal_places=2, default=0.00, verbose_name="مدفوع InstaPay")
    visa_paid = models.DecimalField(max_digits=10, decimal_places=2, default=0.00, verbose_name="مدفوع فيزا")
    credit_paid = models.DecimalField(max_digits=10, decimal_places=2, default=0.00, verbose_name="مدفوع من الرصيد")
    
    payment_method = models.CharField(max_length=20, default='cash', verbose_name="طريقة الدفع")
    # Salesman/rep credited with the sale, distinct from the cashier (Order.user) — Phase 6.8.
    salesman_name = models.CharField(max_length=100, blank=True, verbose_name="اسم البائع/المندوب")
    is_completed = models.BooleanField(default=True, verbose_name="تم الدفع")
    is_online_order = models.BooleanField(default=False, verbose_name="طلب أونلاين")
    notes = models.TextField(blank=True, verbose_name="ملاحظات / مقاسات")
    shipping_address = models.TextField(blank=True, verbose_name="عنوان التوصيل/التفصيل")
    
    # Tailoring fields
    TAILORING_STATUS_CHOICES = [
        ('pending', 'لم يرسل بعد'),
        ('sent', 'تم الإرسال للترزي'),
        ('arrived', 'وصل من الترزي'),
        ('delivered', 'تم التسليم للعميل'),
    ]
    is_tailoring = models.BooleanField(default=False, verbose_name="طلب تفصيل")
    tailoring_type = models.CharField(max_length=200, blank=True, verbose_name="نوع التفصيل")
    tailoring_cost = models.DecimalField(max_digits=10, decimal_places=2, default=0.00, verbose_name="تكلفة التفصيل")
    tailoring_status = models.CharField(max_length=20, choices=TAILORING_STATUS_CHOICES, default='delivered', verbose_name="حالة التفصيل")
    tailor_name = models.CharField(max_length=100, blank=True, verbose_name="اسم الترزي")
    
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="تاريخ الفاتورة")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="آخر تعديل")

    # Phase 6.1: human-facing gap-free invoice number (INV-YYYY-NNNNN). Falls back to
    # the DB id in templates until assigned.
    invoice_number = models.CharField(max_length=30, unique=True, null=True, blank=True,
                                      db_index=True, verbose_name="رقم الفاتورة")

    # Lifecycle / audit (Phase 1.10) — orders are never hard-deleted; they are voided.
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=STATUS_ACTIVE, db_index=True, verbose_name="حالة الفاتورة")
    revision_no = models.PositiveIntegerField(default=0, verbose_name="رقم المراجعة")
    voided_at = models.DateTimeField(null=True, blank=True, verbose_name="تاريخ الإلغاء")
    voided_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='voided_orders', verbose_name="ألغاها")
    void_reason = models.TextField(blank=True, verbose_name="سبب الإلغاء")

    # Receipt splitting (تقسيم الفاتورة) — paying for a subset of an open order's items
    # (e.g. the tea) while the rest (the coffee) stays open on the original check. Each
    # split-off subset becomes its OWN real Order (own invoice number, own VAT/service
    # breakdown computed only from its items, own receipt) rather than a sub-record of
    # the original — see sales.services.split_order_and_pay. split_from links a split
    # invoice back to the check it was carved out of; the original order's own items are
    # simply whatever's left after the split items were reassigned onto the new Order.
    split_from = models.ForeignKey('self', on_delete=models.SET_NULL, null=True, blank=True,
                                   related_name='split_invoices', verbose_name="مقسمة من فاتورة")

    objects = OrderManager()

    @property
    def remaining_amount(self):
        # Remaining is what's left after all payments (including old credit)
        return self.total_amount - (self.received_amount + self.credit_paid)

    @property
    def is_void(self):
        return self.status == self.STATUS_VOID

    @property
    def display_number(self):
        """Human-facing invoice number, falling back to the DB id for legacy orders."""
        return self.invoice_number or f"#{self.id}"

    @property
    def outstanding(self):
        """Unpaid credit portion of this invoice after later receipts (Phase 8.2)."""
        from crm.allocation import order_outstanding
        return order_outstanding(self)

    def vat_breakdown(self):
        """VAT split of this invoice's total — for display only, doesn't change what's
        charged.

        VAT and the service charge are each an independent percentage of the same
        pre-extras subtotal (never compounded on each other — see
        sales.services.compute_discount_and_total), so the tax portion can't be safely
        reverse-engineered from total_amount alone once a service charge is also present.

        - Not included in price (default): the real amount added at checkout, stored on
          `vat_amount` (sales.services.compute_vat_amount()).
        - Included in price: nothing was added: the amount is a purely informational split
          extracted from (total_amount - service_charge) — the service charge is excluded
          from that base since it's never part of the taxable subtotal either way.

        Returns None when no rate is configured, so receipts only show a tax line when
        VAT actually applies.

        Uses the rate/mode SNAPSHOTTED at checkout (vat_rate_snapshot/vat_included_snapshot)
        when present, so a later change to the store's VAT settings never silently rewrites
        the VAT on a past invoice, a refund of it, or a historical report. Falls back to the
        live policy only for orders created before this snapshot existed (both null).
        """
        from settings.policies import get_policy
        if self.vat_rate_snapshot is not None:
            rate = self.vat_rate_snapshot
            included = bool(self.vat_included_snapshot)
        else:
            rate = Decimal(str(get_policy('tax.vat_rate') or 0))
            included = get_policy('tax.vat_included_in_price')
        if rate <= 0:
            return None
        total = self.total_amount or Decimal('0')
        if included:
            base = total - (self.service_charge or Decimal('0'))
            tax = (base * rate / (Decimal('100') + rate)).quantize(Decimal('0.01'))
        else:
            tax = self.vat_amount or Decimal('0')
        net = total - tax
        return {'rate': rate, 'net': net, 'tax': tax, 'total': total, 'included': included,
                'vat_number': get_policy('tax.vat_number') or ''}

    def service_charge_breakdown(self):
        """Service-charge line for the receipt — dine-in only (None for takeaway/delivery,
        same restriction compute_dine_in_service_charge already enforces when the order was
        created/edited).

        When not included in price (the default), Order.service_charge was already added
        on top of the subtotal at checkout time, so this just surfaces that stored amount.
        When included in price, nothing was added — the amount here is purely an
        informational split extracted from the existing total_amount, mirroring
        vat_breakdown()'s included branch exactly.
        """
        if self.order_type != self.ORDER_TYPE_DINE_IN:
            return None
        from settings.policies import get_policy
        pct = Decimal(str(get_policy('tax.service_charge_percent') or 0))
        if pct <= 0:
            return None
        included = get_policy('tax.service_charge_included_in_price')
        if included:
            base = self.total_amount or Decimal('0')
            amount = (base * pct / (Decimal('100') + pct)).quantize(Decimal('0.01'))
        else:
            amount = self.service_charge or Decimal('0')
        return {'pct': pct, 'amount': amount, 'included': included}

    def gross_profit(self):
        """Total COGS-based profit of this invoice = Σ line gross_profit (Phase 1.9).

        Only ever shown to users with can_view_profit AND when the store enables
        sales.show_profit_on_invoice — both gated in the template.
        """
        return sum((it.gross_profit for it in self.items.all()), Decimal('0'))

    def delete(self, *args, **kwargs):
        """Hard-deleting an order (e.g. delete_order_ajax, factory_order_delete) used to
        leave its post_sale() journal entry (reference_number f"SALE-{id}") behind
        forever — no Order left to explain it, permanently corrupting the trial balance/
        income statement's revenue, COGS, AR and VAT-payable totals (found 56 of these
        already sitting in this database). Mirrors financial.models.Transaction.delete()'s
        identical fix for standalone cash-movement transactions.
        """
        from financial.posting import unpost
        with db_transaction.atomic():
            unpost(f"SALE-{self.pk}")
            super().delete(*args, **kwargs)

    def __str__(self):
        return f"Order #{self.id}"


class OrderRevision(models.Model):
    """Immutable snapshot of an order before it was edited (Phase 1.10).

    Lets you see exactly what an invoice looked like prior to each edit, instead of
    silently overwriting history.
    """
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name='revisions', verbose_name="الفاتورة")
    revision_no = models.PositiveIntegerField(verbose_name="رقم المراجعة")
    edited_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, verbose_name="عدّلها")
    reason = models.TextField(blank=True, verbose_name="سبب التعديل")
    before_data = models.JSONField(verbose_name="بيانات الفاتورة قبل التعديل")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="وقت التعديل")

    class Meta:
        ordering = ['-created_at']
        verbose_name = "مراجعة فاتورة"
        verbose_name_plural = "مراجعات الفواتير"

    def __str__(self):
        return f"Order #{self.order_id} rev {self.revision_no}"

class OrderItem(models.Model):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name='items')
    product = models.ForeignKey('products.Product', on_delete=models.SET_NULL, null=True)
    # Fashion: the specific size×color variant sold (its stock is deducted instead of the product's).
    variant = models.ForeignKey('products.ProductVariant', on_delete=models.SET_NULL, null=True, blank=True, related_name='order_items')
    quantity = models.DecimalField(max_digits=10, decimal_places=2, default=1)
    price = models.DecimalField(max_digits=10, decimal_places=2)
    sell_unit = models.CharField(max_length=20, default='box', verbose_name="وحدة البيع")
    # Electronics: the serial number of the specific unit sold (printed on the receipt).
    serial_number = models.CharField(max_length=100, blank=True, default='', verbose_name="الرقم التسلسلي")
    # COGS: actual purchase cost from the FIFO batch at time of sale
    cost_price = models.DecimalField(max_digits=10, decimal_places=2, default=0.00, verbose_name="تكلفة الوحدة (COGS)")
    # Layer 2 'sales.allow_negative_stock': portion of `quantity` (in box units) that exceeded
    # real available stock at the moment of sale — 0 for a normal, fully-covered sale.
    # Recorded here because neither StockTransaction nor the stock ledger itself ever shows
    # this: batches floor at 0 instead of going negative, so the shortfall is otherwise invisible.
    shortfall_qty = models.DecimalField(max_digits=10, decimal_places=2, default=0,
                                         verbose_name="نقص المخزون وقت البيع")
    # Which price tier the cashier sold this line at (POS tier picker on the product card, or
    # the global tier selector). Printed on the invoice so a قطاعي-priced customer can see a
    # نص جملة/جملة line was intentional, not a pricing mistake.
    PRICE_TIER_CHOICES = [('retail', 'قطاعي'), ('semi_wholesale', 'نص جملة'), ('wholesale', 'جملة')]
    price_tier = models.CharField(max_length=20, choices=PRICE_TIER_CHOICES, default='retail',
                                   verbose_name="شريحة السعر")

    # --- Cafe/restaurant fields ---
    is_void = models.BooleanField(default=False, verbose_name="ملغى")
    void_reason = models.TextField(blank=True, default='', verbose_name="سبب الإلغاء")
    voided_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True,
                                  related_name='voided_order_items', verbose_name="ألغاه")
    voided_at = models.DateTimeField(null=True, blank=True, verbose_name="تاريخ الإلغاء")
    # A void doesn't just drop the item off the kitchen screen — it had already been sent
    # to the kitchen, possibly mid-prep, so the KDS keeps showing it (as a red "ملغي"
    # notice) until the chef explicitly dismisses it, rather than it silently vanishing
    # the instant a waiter/cashier cancels it. See kds_view/kds_ack_void in restaurant/views.py.
    kitchen_void_acknowledged = models.BooleanField(default=False, verbose_name="تم استلام إلغاء المطبخ")

    KITCHEN_NEW = 'new'
    KITCHEN_PREPARING = 'preparing'
    KITCHEN_READY = 'ready'
    KITCHEN_SERVED = 'served'
    KITCHEN_STATUS_CHOICES = [
        (KITCHEN_NEW, 'جديد'),
        (KITCHEN_PREPARING, 'قيد التحضير'),
        (KITCHEN_READY, 'جاهز'),
        (KITCHEN_SERVED, 'تم التقديم'),
    ]
    kitchen_status = models.CharField(max_length=10, choices=KITCHEN_STATUS_CHOICES, default=KITCHEN_NEW,
                                      db_index=True, verbose_name="حالة التحضير")
    # A recipe's raw materials are deducted the moment the kitchen marks this item
    # "جاهز" (ready), not when the order is placed — this flag makes that a one-time
    # event (kds_set_status/kds_set_order_status can be called again, or an order can
    # be edited after reaching ready, without re-deducting the same ingredients twice).
    recipe_deducted = models.BooleanField(default=False, verbose_name="تم خصم مكونات الوصفة")
    # Snapshot of selected MenuModifier choices at sale time, e.g.
    # [{"group": "مستوى السكر", "option": "سكر زيادة", "price_delta": 0}]
    modifiers = models.JSONField(default=list, blank=True, verbose_name="الإضافات/الاختيارات")
    printed = models.BooleanField(default=False, verbose_name="طُبع للمطبخ")
    # Free-text kitchen note, e.g. "extra hot", "no foam", "on the aroma" — separate from
    # `modifiers` (structured price-affecting choices) since a note never changes the price.
    note = models.TextField(blank=True, default='', verbose_name="ملاحظة")

    @property
    def is_oversold(self):
        return self.shortfall_qty and self.shortfall_qty > 0

    @property
    def subtotal(self):
        return self.quantity * self.price

    @property
    def box_quantity(self):
        """Quantity expressed in stock (box) units. cost_price is per box, while
        `quantity` is in the sell unit, so strip sales must be divided down."""
        from decimal import Decimal
        spb = (self.product.strips_per_box if self.product else 1) or 1
        if self.sell_unit == 'strip' and spb and int(spb) > 1:
            return self.quantity / Decimal(str(spb))
        return self.quantity

    @property
    def cogs(self):
        """Cost of goods sold for this line = unit (box) cost × box quantity.
        Using box_quantity avoids the strip overstatement (cost_price is per box)."""
        return self.cost_price * self.box_quantity

    @property
    def gross_profit(self):
        """Profit from this line item: revenue (in sell units) − COGS (in box units)."""
        return self.subtotal - self.cogs

class Draft(models.Model):
    STATUS_OPEN = 'open'
    STATUS_CONVERTED = 'converted'
    STATUS_CLOSED = 'closed'
    STATUS_CHOICES = [(STATUS_OPEN, 'مفتوح'), (STATUS_CONVERTED, 'تم التحويل'), (STATUS_CLOSED, 'مغلق')]

    user = models.ForeignKey(User, on_delete=models.CASCADE, verbose_name="المستخدم")
    customer = models.ForeignKey('crm.Customer', on_delete=models.SET_NULL, null=True, blank=True, verbose_name="العميل")
    warehouse = models.ForeignKey('products.Warehouse', on_delete=models.SET_NULL, null=True, blank=True, verbose_name="المخزن")
    cart_data = models.JSONField(default=list, verbose_name="بيانات السلة")
    delivery_cost = models.DecimalField(max_digits=10, decimal_places=2, default=0.00, verbose_name="تكلفة التوصيل")
    discount = models.DecimalField(max_digits=10, decimal_places=2, default=0.00, verbose_name="الخصم")
    discount_type = models.CharField(max_length=10, choices=Order.DISCOUNT_TYPE_CHOICES, default='fixed', verbose_name="نوع الخصم")
    notes = models.TextField(blank=True, verbose_name="ملاحظات")
    
    # Split payment info for drafts
    cash_paid = models.DecimalField(max_digits=10, decimal_places=2, default=0.00, verbose_name="مدفوع نقدي")
    wallet_paid = models.DecimalField(max_digits=10, decimal_places=2, default=0.00, verbose_name="مدفوع محفظة")
    instapay_paid = models.DecimalField(max_digits=10, decimal_places=2, default=0.00, verbose_name="مدفوع InstaPay")
    visa_paid = models.DecimalField(max_digits=10, decimal_places=2, default=0.00, verbose_name="مدفوع فيزا")
    credit_paid = models.DecimalField(max_digits=10, decimal_places=2, default=0.00, verbose_name="مدفوع من الرصيد")
    
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=STATUS_OPEN, verbose_name="الحالة")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="تاريخ الإنشاء")

    @property
    def subtotal_amount(self):
        return sum(Decimal(str(item.get('price', 0))) * Decimal(str(item.get('quantity', 1))) for item in self.cart_data)

    @property
    def total_amount(self):
        applied_discount = Decimal('0')
        if self.discount_type == 'percent':
            applied_discount = (self.subtotal_amount * self.discount) / Decimal('100')
        else:
            applied_discount = self.discount
            
        total = self.subtotal_amount - applied_discount + self.delivery_cost
        if total < 0:
            return Decimal('0')
        return total

    def to_payload(self):
        # Full data for loading into POS
        return self.to_dict()

    def to_summary(self):
        # Minimal data for listing drafts
        return {
            'id': self.id,
            'customer_name': f"{self.customer.first_name} {self.customer.last_name}" if self.customer else 'عميل نقدي',
            'total': float(self.total_amount),
            'items_count': len(self.cart_data or []),
            'created_at': self.created_at.strftime('%Y-%m-%d %H:%M'),
        }

    def to_dict(self):
        return {
            'id': self.id,
            'customer_id': self.customer.id if self.customer else None,
            'customer_name': f"{self.customer.first_name} {self.customer.last_name}" if self.customer else 'عميل نقدي',
            'warehouse_id': self.warehouse.id if self.warehouse else None,
            'cart_data': self.cart_data or [],
            'delivery_cost': float(self.delivery_cost),
            'discount': float(self.discount),
            'discount_type': self.discount_type,
            'notes': self.notes or '',
            'cash_paid': float(self.cash_paid),
            'wallet_paid': float(self.wallet_paid),
            'instapay_paid': float(self.instapay_paid),
            'visa_paid': float(self.visa_paid),
            'credit_paid': float(self.credit_paid),
            'status': self.status,
            'created_at': self.created_at.isoformat(),
        }

class Quotation(models.Model):
    """Customer price quotation / عرض سعر (Phase 6.2).

    A non-committal document (no stock or financial impact). It has its own number
    (QUO-YYYY-NNNNN), line items, a validity date, and a status the seller advances
    manually. When accepted, the cashier rings it up in the POS.
    """
    STATUS_DRAFT = 'draft'
    STATUS_SENT = 'sent'
    STATUS_ACCEPTED = 'accepted'
    STATUS_REJECTED = 'rejected'
    STATUS_CONVERTED = 'converted'
    STATUS_CHOICES = [
        (STATUS_DRAFT, 'مسودة'), (STATUS_SENT, 'مُرسل'),
        (STATUS_ACCEPTED, 'مقبول'), (STATUS_REJECTED, 'مرفوض'),
        (STATUS_CONVERTED, 'تحوّل لفاتورة'),
    ]
    number = models.CharField(max_length=30, unique=True, null=True, blank=True, db_index=True, verbose_name="رقم العرض")
    customer = models.ForeignKey('crm.Customer', on_delete=models.SET_NULL, null=True, blank=True, verbose_name="العميل")
    customer_name = models.CharField(max_length=200, blank=True, default='', verbose_name="اسم العميل (نصي)")
    status = models.CharField(max_length=12, choices=STATUS_CHOICES, default=STATUS_DRAFT, verbose_name="الحالة")
    valid_until = models.DateField(null=True, blank=True, verbose_name="صالح حتى")
    notes = models.TextField(blank=True, default='', verbose_name="ملاحظات")
    converted_order = models.ForeignKey('Order', on_delete=models.SET_NULL, null=True, blank=True, related_name='from_quotation', verbose_name="الفاتورة الناتجة")
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, verbose_name="أنشأها")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="تاريخ الإنشاء")

    class Meta:
        ordering = ['-created_at']
        verbose_name = "عرض سعر"
        verbose_name_plural = "عروض الأسعار"

    @property
    def display_number(self):
        return self.number or f"QUO#{self.id}"

    @property
    def total(self):
        return sum((i.line_total for i in self.items.all()), Decimal('0.00'))

    @property
    def display_customer(self):
        if self.customer:
            return f"{self.customer.first_name} {self.customer.last_name}"
        return self.customer_name or 'عميل غير محدد'

    def __str__(self):
        return self.display_number


class QuotationItem(models.Model):
    quotation = models.ForeignKey(Quotation, on_delete=models.CASCADE, related_name='items')
    product = models.ForeignKey('products.Product', on_delete=models.SET_NULL, null=True, verbose_name="الصنف")
    description = models.CharField(max_length=200, blank=True, default='', verbose_name="وصف")
    quantity = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('1'))
    unit_price = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0'))

    @property
    def line_total(self):
        return (self.quantity or Decimal('0')) * (self.unit_price or Decimal('0'))


class Expense(models.Model):
    EXPENSE_CATEGORIES = [
        ('rent', 'إيجار'),
        ('salary', 'رواتب موظفين'),
        ('electricity', 'كهرباء/مياه/غاز'),
        ('internet', 'إنترنت وتليفون'),
        ('maintenance', 'صيانة وإصلاحات'),
        ('cleaning', 'تنظيف ومستلزمات'),
        ('goods', 'نقل ومشال'),
        ('hospitality', 'ضيافة وبوفيه'),
        ('marketing', 'دعاية وإعلان'),
        ('other', 'نثريات / أخرى')
    ]
    PAYMENT_METHODS = [
        ('cash', 'كاش'),
        ('bank', 'تحويل بنكي'),
        ('wallet', 'محفظة إلكترونية'),
        ('instapay', 'إنستاباي'),
    ]
    title = models.CharField(max_length=200, verbose_name="بند الصرف")
    category = models.CharField(max_length=50, choices=EXPENSE_CATEGORIES, verbose_name="التصنيف")
    amount = models.DecimalField(max_digits=10, decimal_places=2, verbose_name="المبلغ")
    description = models.TextField(blank=True, verbose_name="التفاصيل")
    # Was auto_now_add (never editable, always "right now") — a daily-expense entry
    # needs to be backdateable (e.g. logging yesterday's receipt today), so this is now
    # a plain editable field defaulting to today rather than silently overwritten on save.
    date = models.DateField(default=timezone.localdate, verbose_name="التاريخ")
    payment_method = models.CharField(max_length=20, choices=PAYMENT_METHODS, default='cash',
                                      verbose_name="طريقة الدفع")
    # Optional photo of the receipt — no existing attachment convention elsewhere in this
    # codebase to match beyond the plain ImageField pattern already used for profile
    # photos/logo (accounts.UserProfile.profile_photo / settings.SystemSetting).
    receipt = models.ImageField(upload_to='expenses/receipts/', blank=True, null=True,
                                verbose_name="صورة الإيصال")
    user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, verbose_name="المسؤول")
    # Set only when the amount exceeded settings.policies 'expenses.approval_threshold'
    # and a manager authorized it inline (see accounts.approvals) — null for every
    # expense under the threshold, which is the common case and needs no approver at all.
    approved_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True,
                                    related_name='approved_expenses', verbose_name="اعتمده")

class ReturnInvoice(models.Model):
    REFUND_METHOD_CHOICES = [
        ('cash', 'استرداد نقدي (من الدرج)'),
        ('customer_credit', 'إضافة لرصيد العميل (خصم من مديونيته)'),
    ]
    REASON_CHOICES = [
        ('defect', 'عيب صناعة / تالف'),
        ('wrong_item', 'صنف خاطئ'),
        ('size', 'مقاس غير مناسب'),
        ('changed_mind', 'العميل غيّر رأيه'),
        ('exchange', 'استبدال'),
        ('other', 'أخرى'),
    ]
    # Phase 6.4: own gap-free document number (RET-YYYY-NNNNN).
    return_number = models.CharField(max_length=30, unique=True, null=True, blank=True, db_index=True, verbose_name="رقم المرتجع")
    original_order = models.ForeignKey(Order, on_delete=models.SET_NULL, null=True, blank=True, verbose_name="الفاتورة الأصلية")
    customer = models.ForeignKey('crm.Customer', on_delete=models.SET_NULL, null=True, blank=True, verbose_name="العميل")
    user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, verbose_name="الموظف")
    total_refund_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0.00, verbose_name="إجمالي المبلغ المسترد")
    # VAT portion of total_refund_amount — the same proportion the original order's own
    # total was VAT (see refund_view: total_refund * original.vat_breakdown()['tax'] /
    # original.vat_breakdown()['total']). Lets post_refund() reverse Sales Returns and
    # VAT Payable separately instead of debiting the whole VAT-inclusive amount to
    # Sales Returns, which would understate net revenue by the VAT portion of every return.
    vat_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0.00, verbose_name="قيمة الضريبة في المرتجع")
    refund_method = models.CharField(max_length=20, choices=REFUND_METHOD_CHOICES, default='cash', verbose_name="طريقة الاسترداد")
    reason_category = models.CharField(max_length=20, choices=REASON_CHOICES, blank=True, verbose_name="تصنيف السبب")
    reason = models.TextField(blank=True, verbose_name="سبب الإرجاع")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="تاريخ الإرجاع")

    @property
    def display_number(self):
        return self.return_number or f"RET#{self.id}"

    def delete(self, *args, **kwargs):
        """Same fix as Order.delete()/Transaction.delete() — a hard-deleted return must
        not leave its post_refund() journal entry (reference_number f"RET-{id}") behind."""
        from financial.posting import unpost
        with db_transaction.atomic():
            unpost(f"RET-{self.pk}")
            super().delete(*args, **kwargs)

class ReturnItem(models.Model):
    return_invoice = models.ForeignKey(ReturnInvoice, on_delete=models.CASCADE, related_name='items')
    product = models.ForeignKey('products.Product', on_delete=models.SET_NULL, null=True)
    # Fashion: which size/color variant this return line is for, if the original sale
    # was a variant line. Needed so "already returned" tracking and stock restoration
    # don't lump different sizes of the same product together.
    variant = models.ForeignKey('products.ProductVariant', on_delete=models.SET_NULL, null=True, blank=True, related_name='return_items')
    quantity = models.DecimalField(max_digits=10, decimal_places=2)
    refund_price = models.DecimalField(max_digits=10, decimal_places=2)

class OtherIncome(models.Model):
    title = models.CharField(max_length=200, verbose_name="مصدر الدخل", help_text="مثال: سداد دين، إيجار، إضافة رصيد")
    amount = models.DecimalField(max_digits=10, decimal_places=2, verbose_name="المبلغ")
    description = models.TextField(blank=True, verbose_name="ملاحظات")
    date = models.DateField(auto_now_add=True, verbose_name="التاريخ")
    user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, verbose_name="المسؤول")

class CashSettlement(models.Model):
    date = models.DateTimeField(auto_now_add=True, verbose_name="تاريخ الجرد")
    user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, verbose_name="القائم بالجرد")
    expected_cash = models.DecimalField(max_digits=10, decimal_places=2, verbose_name="النقدية المتوقعة (بالنظام)")
    actual_cash = models.DecimalField(max_digits=10, decimal_places=2, verbose_name="النقدية الفعلية (بالدرج)")
    difference = models.DecimalField(max_digits=10, decimal_places=2, verbose_name="العجز / الزيادة")
    note = models.TextField(blank=True, verbose_name="ملاحظات التسوية")


class SavedOrder(models.Model):
    """A named, reusable order template for a specific customer (Phase 6.11).

    Captures a recurring basket (e.g. a customer's "طلب شهري"). Loading it in the POS
    auto-fills the cart with the saved products + quantities and flags any item whose
    available stock is below the saved quantity (نقص).
    """
    customer = models.ForeignKey('crm.Customer', on_delete=models.CASCADE,
                                 related_name='saved_orders', verbose_name="العميل")
    name = models.CharField(max_length=100, verbose_name="اسم الطلب")
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, verbose_name="أنشأه")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="تاريخ الإنشاء")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="آخر تعديل")

    class Meta:
        ordering = ['-updated_at']
        verbose_name = "طلب محفوظ"
        verbose_name_plural = "طلبات محفوظة"

    def __str__(self):
        return f"{self.name} ({self.customer_id})"


class SavedOrderItem(models.Model):
    saved_order = models.ForeignKey(SavedOrder, on_delete=models.CASCADE,
                                    related_name='items', verbose_name="الطلب المحفوظ")
    product = models.ForeignKey('products.Product', on_delete=models.CASCADE, verbose_name="الصنف")
    quantity = models.DecimalField(max_digits=10, decimal_places=2, default=1, verbose_name="الكمية")
    sell_unit = models.CharField(max_length=10, default='box', verbose_name="وحدة البيع")

    def __str__(self):
        return f"{self.product_id} x{self.quantity}"


class Reservation(models.Model):
    """A stock reservation / sale order for a customer (Phase 6.3).

    Holds stock (without deducting it) until converted to an invoice. While OPEN, the
    reserved quantities reduce the available stock shown/enforced in the POS. An optional
    deposit (عربون) is recorded as a customer credit. Converting creates a normal Order
    and marks the reservation CONVERTED; cancelling releases the hold.
    """
    STATUS_OPEN = 'OPEN'
    STATUS_CONVERTED = 'CONVERTED'
    STATUS_CANCELLED = 'CANCELLED'
    STATUS_CHOICES = [
        (STATUS_OPEN, 'محجوز (مفتوح)'),
        (STATUS_CONVERTED, 'تم التحويل لفاتورة'),
        (STATUS_CANCELLED, 'ملغي'),
    ]

    reservation_number = models.CharField(max_length=30, unique=True, null=True, blank=True,
                                          db_index=True, verbose_name="رقم الحجز")
    customer = models.ForeignKey('crm.Customer', on_delete=models.PROTECT,
                                 related_name='reservations', verbose_name="العميل")
    warehouse = models.ForeignKey('products.Warehouse', on_delete=models.PROTECT,
                                  related_name='reservations', verbose_name="المخزن")
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=STATUS_OPEN,
                              db_index=True, verbose_name="الحالة")
    deposit_amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'),
                                         verbose_name="العربون")
    notes = models.TextField(blank=True, verbose_name="ملاحظات")
    expiry_date = models.DateField(null=True, blank=True, verbose_name="تاريخ انتهاء الحجز")
    converted_order = models.ForeignKey('Order', on_delete=models.SET_NULL, null=True, blank=True,
                                        related_name='from_reservation', verbose_name="الفاتورة الناتجة")
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, verbose_name="أنشأه")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="تاريخ الحجز")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = "حجز"
        verbose_name_plural = "الحجوزات"

    def __str__(self):
        return f"{self.reservation_number or self.id} - {self.customer}"

    @property
    def total_amount(self):
        return sum((i.quantity * i.unit_price for i in self.items.all()), Decimal('0.00'))

    @property
    def display_number(self):
        return self.reservation_number or f"#{self.id}"


class ReservationItem(models.Model):
    reservation = models.ForeignKey(Reservation, on_delete=models.CASCADE,
                                    related_name='items', verbose_name="الحجز")
    product = models.ForeignKey('products.Product', on_delete=models.PROTECT, verbose_name="المنتج")
    quantity = models.DecimalField(max_digits=10, decimal_places=2, default=1, verbose_name="الكمية")
    unit_price = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'), verbose_name="سعر الوحدة")

    @property
    def line_total(self):
        return self.quantity * self.unit_price

    def __str__(self):
        return f"{self.product_id} x{self.quantity}"


def reserved_quantities(warehouse_id, exclude_reservation_id=None):
    """Map {product_id: reserved_qty} of OPEN reservations in a warehouse (Phase 6.3).

    Used by the POS to compute available stock = on-hand − reserved. `exclude_reservation_id`
    drops one reservation from the total (so converting it can consume its own held stock).
    """
    from django.db.models import Sum as _Sum
    qs = ReservationItem.objects.filter(
        reservation__warehouse_id=warehouse_id,
        reservation__status=Reservation.STATUS_OPEN,
    )
    if exclude_reservation_id:
        qs = qs.exclude(reservation_id=exclude_reservation_id)
    rows = qs.values('product_id').annotate(q=_Sum('quantity'))
    return {r['product_id']: (r['q'] or Decimal('0.00')) for r in rows}
