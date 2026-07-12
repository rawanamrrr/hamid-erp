from django.db import models
from django.contrib.auth.models import User
from django.utils import timezone

class Role(models.Model):
    """
    يمثل دور أو وظيفة مخصصة، ويحتوي على صلاحيات مقسمة حسب الوحدات والأفعال
    مثال للصلاحيات: 
    {
        "sales": ["view", "create"],
        "financial": ["view"],
        ...
    }
    """
    name = models.CharField(max_length=100, unique=True, verbose_name="اسم الدور")
    description = models.TextField(blank=True, null=True, verbose_name="وصف الدور")
    permissions = models.JSONField(default=dict, verbose_name="الصلاحيات (JSON)")
    created_at = models.DateTimeField(auto_now_add=True)
    
    def __str__(self):
        return self.name

class UserProfile(models.Model):
    """
    بيانات إضافية للمستخدم (الملف الشخصي) وربط بالأدوار المخصصة (Roles)
    """
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='profile')
    roles = models.ManyToManyField(Role, blank=True, related_name='users', verbose_name="الأدوار")
    
    # Onboarding Data
    # Phone is the recovery handle: one phone ↔ one account (unique). Nullable so legacy/system
    # accounts without a phone don't collide (DBs treat NULLs as distinct); the registration
    # forms require it for new users.
    phone = models.CharField(max_length=20, blank=True, null=True, unique=True, verbose_name="رقم الهاتف")
    department = models.CharField(max_length=100, blank=True, null=True, verbose_name="القسم")
    job_title = models.CharField(max_length=100, blank=True, null=True, verbose_name="المسمى الوظيفي")
    branch = models.CharField(max_length=100, blank=True, null=True, verbose_name="الفرع")
    profile_photo = models.ImageField(upload_to='profiles/', blank=True, null=True, verbose_name="الصورة الشخصية")
    
    # Preferences & System tracking
    preferred_lang = models.CharField(max_length=10, default='ar', verbose_name="لغة الواجهة")
    timezone = models.CharField(max_length=50, default='Africa/Cairo', verbose_name="المنطقة الزمنية")
    
    onboarding_completed = models.BooleanField(default=False, verbose_name="أكمل التهيئة؟")
    is_master = models.BooleanField(default=False, verbose_name="رتبة المالك (Master)") # The ultimate global owner role
    last_known_ip = models.GenericIPAddressField(null=True, blank=True, verbose_name="آخر IP مسجل")
    
    # Direct per-user permissions to control sidebar visibility and quick overrides
    direct_permissions = models.JSONField(default=dict, blank=True, null=True, verbose_name="صلاحيات مباشرة")

    # Phase: per-user favorite shortcuts (list of url-name keys) — pinned in sidebar/home.
    favorites = models.JSONField(default=list, blank=True, verbose_name="الاختصارات المفضلة")
    
    # Store restrictions for cashier
    allowed_warehouses = models.ManyToManyField('products.Warehouse', blank=True, verbose_name="المخازن المسموحة")

    # --- Operational limits (Phase 3.2) ---
    from decimal import Decimal as _D
    max_discount_percent = models.DecimalField(max_digits=5, decimal_places=2, default=_D('100.00'),
                                               verbose_name="أقصى نسبة خصم مسموحة %")
    # 0 = unlimited (same convention as Customer.credit_limit) — caps a FIXED-amount discount
    # independently of the % cap above, since a percentage cap alone doesn't limit a fixed
    # discount on a very large invoice.
    max_discount_amount = models.DecimalField(max_digits=12, decimal_places=2, default=_D('0.00'),
                                               verbose_name="أقصى قيمة خصم ثابت (0 = غير محدود)")
    can_sell_below_cost = models.BooleanField(default=False, verbose_name="السماح بالبيع تحت سعر التكلفة")
    can_edit_price = models.BooleanField(default=True, verbose_name="السماح بتعديل السعر في نقطة البيع")
    # Phase 3.2 (expanded — SKY SOFT-style granular cashier flags)
    can_sell_below_sale_price = models.BooleanField(default=True, verbose_name="السماح بالبيع تحت سعر البيع (القطاعي)")
    can_sell_below_zero_stock = models.BooleanField(default=False, verbose_name="السماح بالبيع برصيد سالب (تحت الصفر)")
    can_change_unit = models.BooleanField(default=True, verbose_name="السماح بتغيير وحدة البيع في الكاشير")
    can_view_profit = models.BooleanField(default=False, verbose_name="إظهار الربح على الفواتير والتقارير")

    # Where this user lands right after login. Waiters/cashiers shouldn't see the
    # analytics dashboard (and often can't — 'dashboard'.'view' is usually denied for
    # them) — this sends them straight to their actual work screen instead of a 403.
    # 'auto' (the default) picks it automatically from the user's own صلاحيات via
    # get_best_landing_url() — an admin only needs 'waiter'/'cashier'/'dashboard' to
    # force a specific screen regardless of what their permissions would pick.
    LANDING_AUTO = 'auto'
    LANDING_DASHBOARD = 'dashboard'
    LANDING_WAITER = 'waiter'
    LANDING_CASHIER = 'cashier'
    LANDING_CHOICES = [
        (LANDING_AUTO, 'تلقائي (حسب صلاحيات المستخدم)'),
        (LANDING_DASHBOARD, 'لوحة التحكم'),
        (LANDING_WAITER, 'شاشة الويتر (POS)'),
        (LANDING_CASHIER, 'شاشة الكاشير'),
    ]
    default_landing = models.CharField(max_length=10, choices=LANDING_CHOICES, default=LANDING_AUTO,
                                       verbose_name="الشاشة الافتراضية بعد الدخول")

    def __str__(self):
        return f"ملف {self.user.username}"

    @property
    def _is_privileged(self):
        return self.user.is_superuser or self.is_master

    def discount_cap(self):
        """Maximum discount % this user may apply (privileged users uncapped)."""
        from decimal import Decimal
        if self._is_privileged:
            return Decimal('100.00')
        return self.max_discount_percent if self.max_discount_percent is not None else Decimal('100.00')

    def discount_amount_cap(self):
        """Maximum FIXED-amount discount this user may apply (0 = unlimited; privileged
        users always unlimited). Independent of discount_cap()'s percentage limit —
        a % cap alone doesn't bound a fixed discount on a large invoice."""
        from decimal import Decimal
        if self._is_privileged:
            return Decimal('0.00')
        return self.max_discount_amount if self.max_discount_amount is not None else Decimal('0.00')

    def allows_below_cost(self):
        return self._is_privileged or self.can_sell_below_cost

    def allows_price_edit(self):
        return self._is_privileged or self.can_edit_price

    def allows_below_sale_price(self):
        return self._is_privileged or self.can_sell_below_sale_price

    def allows_below_zero_stock(self):
        return self._is_privileged or self.can_sell_below_zero_stock

    def allows_change_unit(self):
        return self._is_privileged or self.can_change_unit

    def allows_view_profit(self):
        return self._is_privileged or self.can_view_profit
    
    def get_all_permissions(self):
        """
        تقوم بدمج كل الصلاحيات من جميع الأدوار المحددة للمستخدم.
        الصلاحيات المباشرة (direct_permissions) تملك الأولوية القصوى:
        - إذا كان الوحدة في direct_permissions وتحتوي على ['__denied__'], يتم منع الوصول بالكامل.
        - إذا كانت الوحدة تحتوي على أفعال محددة, تلك الأفعال تُضاف دون النظر للدور.
        - إذا لم تكن الوحدة في direct_permissions, يتم توارث صلاحيات الأدوار.
        """
        # Step 1: Collect all role-based permissions
        role_perms = {}
        for role in self.roles.all():
            for module, actions in role.permissions.items():
                if module not in role_perms:
                    role_perms[module] = set()
                role_perms[module].update(actions)
        
        if not self.direct_permissions:
            return role_perms

        # Step 2: Apply direct permissions with deny-override logic
        # If a module appears in direct_permissions, it takes FULL control for that module
        # (overrides whatever the role says for that module)
        final_perms = {}
        
        # Add role perms for modules NOT touched by direct_permissions
        for module, actions in role_perms.items():
            if module not in self.direct_permissions:
                final_perms[module] = actions
        
        # Apply direct_permissions (these override role perms for their modules)
        for module, actions in self.direct_permissions.items():
            if '__denied__' in actions:
                # Explicit deny: module is completely hidden - do NOT add to final_perms
                pass
            elif actions:
                # Explicit grant: use exactly these actions
                if module not in final_perms:
                    final_perms[module] = set()
                final_perms[module].update(a for a in actions if a != '__denied__')
        
        return final_perms

class ApprovalRequest(models.Model):
    """A manager-authorized override of a per-user/store limit (Phase 3.3).

    When a cashier hits a hard guard at checkout (over discount cap, below cost, below sale
    price, changed unit, negative stock, over credit limit), the operation is either rejected
    or escalated: a privileged user authorizes it inline (credentials in the checkout payload)
    and we persist one row per override for audit. Rows may also be created `pending` for an
    asynchronous approve-later flow from the approvals screen.
    """
    KIND_CHOICES = [
        ('over_discount',     'تجاوز حد الخصم'),
        ('below_cost',        'البيع تحت سعر التكلفة'),
        ('below_sale_price',  'البيع تحت سعر القطاعي'),
        ('change_unit',       'تغيير وحدة البيع'),
        ('below_zero_stock',  'البيع برصيد سالب'),
        ('over_credit_limit', 'تجاوز حد ائتمان العميل'),
    ]
    STATUS_CHOICES = [
        ('pending',  'بانتظار الموافقة'),
        ('approved', 'تمت الموافقة'),
        ('rejected', 'مرفوض'),
    ]

    requested_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True,
                                     related_name='approval_requests', verbose_name="مقدّم الطلب")
    approved_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True,
                                    related_name='approvals_given', verbose_name="المعتمِد")
    kind = models.CharField(max_length=30, choices=KIND_CHOICES, verbose_name="نوع التجاوز")
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='pending', verbose_name="الحالة")
    payload = models.JSONField(default=dict, blank=True, verbose_name="تفاصيل")
    note = models.TextField(blank=True, default='', verbose_name="سبب الطلب")
    decision_note = models.TextField(blank=True, default='', verbose_name="ملاحظة الاعتماد")
    created_at = models.DateTimeField(auto_now_add=True)
    decided_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = "طلب اعتماد"
        verbose_name_plural = "طلبات الاعتماد"

    def __str__(self):
        return f"{self.get_kind_display()} — {self.get_status_display()} ({self.created_at:%Y-%m-%d})"

class PasswordResetCode(models.Model):
    """A short-lived numeric code for resetting a password via the account's phone number.

    The code is delivered by the pluggable SMS sender (accounts/sms.py). Codes expire and are
    single-use; we keep only the latest active one per user. Rate-limited at the view level.
    """
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='reset_codes')
    code = models.CharField(max_length=6, verbose_name="رمز التحقق")
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    used = models.BooleanField(default=False)
    attempts = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ['-created_at']

    def is_valid(self):
        return (not self.used) and self.attempts < 5 and timezone.now() < self.expires_at

    @classmethod
    def issue(cls, user, ttl_minutes=15):
        """Invalidate prior codes and create a fresh 6-digit code for `user`."""
        import random
        from datetime import timedelta
        cls.objects.filter(user=user, used=False).update(used=True)
        code = f"{random.randint(0, 999999):06d}"
        return cls.objects.create(
            user=user, code=code,
            expires_at=timezone.now() + timedelta(minutes=ttl_minutes),
        )


class UserActivityLog(models.Model):
    """
    جدول لتسجيل (أوديت) جميع حركات المستخدمين في النظام
    """
    ACTION_CHOICES = [
        ('LOGIN', 'تسجيل دخول'),
        ('LOGOUT', 'تسجيل خروج'),
        ('CREATE', 'إنشاء'),
        ('UPDATE', 'تعديل'),
        ('DELETE', 'حذف'),
        ('EXPORT', 'تصدير'),
        ('OTHER', 'أخرى'),
    ]

    user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='activity_logs')
    action_type = models.CharField(max_length=20, choices=ACTION_CHOICES, verbose_name="نوع الحركة")
    module = models.CharField(max_length=50, verbose_name="الوحدة (Module)") # e.g., 'sales', 'financial', 'products'
    description = models.TextField(verbose_name="وصف الحركة") # e.g., 'أضاف منتج جديد: تفاح'
    
    ip_address = models.GenericIPAddressField(null=True, blank=True, verbose_name="عنوان الـ IP")
    user_agent = models.TextField(null=True, blank=True, verbose_name="المتصفح والجهاز")
    
    # Data Tracking
    before_data = models.JSONField(null=True, blank=True, verbose_name="البيانات قبل التعديل")
    after_data = models.JSONField(null=True, blank=True, verbose_name="البيانات بعد التعديل")
    
    timestamp = models.DateTimeField(default=timezone.now, verbose_name="وقت الحركة")

    def __str__(self):
        return f"{self.user} - {self.get_action_type_display()} - {self.timestamp}"
        
class UserIPHistory(models.Model):
    """
    تتبع عناوين الـ IP الخاصة بالمستخدم بمرور الوقت
    """
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='ip_history')
    ip_address = models.GenericIPAddressField(verbose_name="عنوان الـ IP")
    
    is_flagged = models.BooleanField(default=False, verbose_name="مشبوه (Flagged)")
    is_whitelisted = models.BooleanField(default=False, verbose_name="في القائمة البيضاء")
    is_blacklisted = models.BooleanField(default=False, verbose_name="في القائمة السوداء")
    
    first_seen = models.DateTimeField(default=timezone.now, verbose_name="أول ظهور")
    last_seen = models.DateTimeField(auto_now=True, verbose_name="آخر ظهور")

    class Meta:
        unique_together = ('user', 'ip_address')

    def __str__(self):
        return f"{self.ip_address} ({self.user.username})"

class SystemError(models.Model):
    """
    سجل الأخطاء التقنية (500 Errors) التي تحدث في النظام لتسهيل تتبعها وحلها
    """
    user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='system_errors')
    path = models.CharField(max_length=255, verbose_name="المسار (URL)")
    exception_type = models.CharField(max_length=255, verbose_name="نوع الخطأ")
    message = models.TextField(verbose_name="رسالة الخطأ")
    traceback = models.TextField(verbose_name="تفاصيل الخطأ (Traceback)")
    
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.TextField(null=True, blank=True)
    
    ERROR_SOURCE_CHOICES = [
        ('BACKEND', 'خلفية النظام (Django)'),
        ('FRONTEND', 'واجهة النظام (JavaScript)'),
    ]
    source = models.CharField(max_length=20, choices=ERROR_SOURCE_CHOICES, default='BACKEND', verbose_name="مصدر الخطأ")
    
    timestamp = models.DateTimeField(auto_now_add=True, verbose_name="وقت الحدوث")
    is_resolved = models.BooleanField(default=False, verbose_name="تم الحل؟")

    class Meta:
        ordering = ['-timestamp']

    def __str__(self):
        return f"{self.exception_type} at {self.path} ({self.timestamp})"
