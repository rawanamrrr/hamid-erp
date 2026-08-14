"""Device-agnostic biometric/attendance-device integration layer.

Nothing in this app knows about late/absence/payroll rules — that logic stays entirely
in financial.payroll_models.AttendanceRecord / financial.views' deduction functions.
This app's only job is: talk to a physical device via its adapter, land the punches it
returns in a standard shape (DevicePunch), then fold them into the SAME arrival_time/
departure_time fields the existing manual-entry attendance form already writes to — see
process_punches_into_attendance() in sync.py. See attendance_devices/adapters/base.py
for the per-vendor adapter contract.
"""
from django.conf import settings
from django.db import models

from .crypto import decrypt_text, encrypt_text


class AttendanceDevice(models.Model):
    """One physical attendance terminal. `adapter_type` is a registry key (see
    adapters/registry.py) — NOT a hardcoded enum of specific vendors, so a brand-new
    protocol just registers a new key instead of requiring a migration/code change here.
    """
    CONNECTION_STATUS_CHOICES = [
        ('unknown', 'غير معروف'),
        ('online', 'متصل'),
        ('offline', 'غير متصل'),
        ('error', 'خطأ في الاتصال'),
    ]

    name = models.CharField(max_length=100, verbose_name="اسم الجهاز",
                            help_text="مثال: بصمة المدخل الرئيسي")
    manufacturer = models.CharField(max_length=100, blank=True, default='', verbose_name="الشركة المصنّعة")
    model = models.CharField(max_length=100, blank=True, default='', verbose_name="موديل الجهاز")

    # The registry key for adapters/registry.py — e.g. 'csv_import'. Deliberately a free
    # CharField (not `choices=`) so a new adapter never needs a migration to be selectable
    # once it's registered; the device-form view lists whatever's currently registered.
    adapter_type = models.CharField(max_length=50, verbose_name="بروتوكول/نوع الاتصال")

    ip_address = models.CharField(max_length=100, blank=True, default='', verbose_name="عنوان IP / المضيف")
    port = models.PositiveIntegerField(null=True, blank=True, verbose_name="المنفذ (Port)")
    protocol = models.CharField(max_length=30, blank=True, default='', verbose_name="بروتوكول الشبكة",
                                help_text="مثال: TCP, UDP, HTTP")
    device_serial = models.CharField(max_length=100, blank=True, default='', verbose_name="الرقم التسلسلي للجهاز")

    # Vendor username, stored encrypted — see crypto.py. The password/API-key/token is
    # kept in a SEPARATE model (AttendanceDeviceCredential) rather than inline here so a
    # casual `AttendanceDevice.objects.values(...)` dump (used all over this codebase for
    # list views/exports) can never accidentally include a secret column by omission.
    username = models.CharField(max_length=100, blank=True, default='', verbose_name="اسم المستخدم على الجهاز")

    enabled = models.BooleanField(default=True, verbose_name="مفعّل")
    clear_after_sync = models.BooleanField(
        default=False, verbose_name="مسح السجلات من الجهاز بعد المزامنة",
        help_text="فعّلها فقط إذا كنت تريد حذف البصمات من ذاكرة الجهاز بعد سحبها بنجاح — إجراء لا رجعة فيه.")

    # The TIMEZONE THE DEVICE'S OWN CLOCK IS SET TO — not necessarily the ERP server's
    # timezone (settings.TIME_ZONE). A device in a different branch/country, or simply
    # configured with a different local time than the server, would have every punch
    # silently shifted by the offset if the server's timezone were assumed instead. Every
    # adapter and the sync engine's own naive-timestamp normalization (see sync.py) must
    # localize raw device timestamps using THIS field, not settings.TIME_ZONE.
    timezone_name = models.CharField(max_length=64, default=settings.TIME_ZONE, verbose_name="المنطقة الزمنية للجهاز",
                                     help_text="مثال: Africa/Cairo — اضبطها على المنطقة الزمنية الفعلية لساعة الجهاز.")

    # Connection robustness — read by adapters/the sync engine, not hardcoded per-vendor.
    timeout_seconds = models.PositiveIntegerField(default=10, verbose_name="مهلة الاتصال (ثانية)",
                                                   help_text="أقصى وقت انتظار عند محاولة الاتصال بالجهاز قبل اعتبارها فشلت.")
    max_retries = models.PositiveIntegerField(default=2, verbose_name="عدد محاولات إعادة الاتصال",
                                              help_text="عدد محاولات إعادة المزامنة تلقائياً بعد فشل الاتصال، قبل تسجيلها كفشل نهائي.")
    retry_delay_seconds = models.PositiveIntegerField(default=5, verbose_name="الانتظار بين المحاولات (ثانية)")

    last_sync_at = models.DateTimeField(null=True, blank=True, verbose_name="آخر مزامنة ناجحة")
    connection_status = models.CharField(max_length=10, choices=CONNECTION_STATUS_CHOICES,
                                         default='unknown', verbose_name="حالة الاتصال")
    last_error = models.TextField(blank=True, default='', verbose_name="آخر خطأ")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "جهاز حضور"
        verbose_name_plural = "أجهزة الحضور"
        ordering = ['name']

    def __str__(self):
        return f"{self.name} ({self.get_adapter_type_display() if hasattr(self, 'get_adapter_type_display') else self.adapter_type})"

    def get_timezone(self):
        """The tzinfo the device's own clock uses — falls back to the server's default
        timezone only if `timezone_name` is somehow blank/invalid, never silently."""
        from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
        try:
            return ZoneInfo(self.timezone_name or settings.TIME_ZONE)
        except (ZoneInfoNotFoundError, ValueError):
            return ZoneInfo(settings.TIME_ZONE)


class AttendanceDeviceCredential(models.Model):
    """The device's secret (password/API key/token), encrypted at rest — kept off
    AttendanceDevice itself (see that model's `username` field comment). One-to-one:
    a device either has a secret or it doesn't (many protocols are IP-only / no-auth).
    """
    device = models.OneToOneField(AttendanceDevice, on_delete=models.CASCADE, related_name='credential')
    _secret_encrypted = models.TextField(blank=True, default='', db_column='secret_encrypted')
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "بيانات اعتماد جهاز"
        verbose_name_plural = "بيانات اعتماد الأجهزة"

    @property
    def secret(self) -> str:
        return decrypt_text(self._secret_encrypted)

    @secret.setter
    def secret(self, value: str):
        self._secret_encrypted = encrypt_text(value or '')


class DeviceEmployeeMapping(models.Model):
    """Device-side user id ↔ ERP employee — the explicit mapping the spec calls out
    ("device user ID = 1025" must never be assumed to equal "ERP employee ID = 57").
    """
    device = models.ForeignKey(AttendanceDevice, on_delete=models.CASCADE, related_name='employee_mappings')
    device_user_id = models.CharField(max_length=100, verbose_name="رقم المستخدم على الجهاز")
    device_user_name = models.CharField(max_length=150, blank=True, default='', verbose_name="اسم المستخدم على الجهاز")
    employee = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='device_mappings',
                                 verbose_name="الموظف في النظام")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "ربط موظف بجهاز"
        verbose_name_plural = "ربط الموظفين بالأجهزة"
        unique_together = ('device', 'device_user_id')
        indexes = [models.Index(fields=['device', 'device_user_id'])]

    def __str__(self):
        return f"{self.device_user_id} → {self.employee}"


class DeviceSyncLog(models.Model):
    STATUS_CHOICES = [
        ('running', 'جارٍ التنفيذ'),
        ('success', 'نجحت'),
        ('partial', 'نجحت جزئياً'),
        ('failed', 'فشلت'),
    ]
    device = models.ForeignKey(AttendanceDevice, on_delete=models.CASCADE, related_name='sync_logs')
    started_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='running')
    records_fetched = models.PositiveIntegerField(default=0)
    records_created = models.PositiveIntegerField(default=0)
    records_skipped_duplicate = models.PositiveIntegerField(default=0)
    records_unmapped = models.PositiveIntegerField(default=0)
    error_message = models.TextField(blank=True, default='')
    triggered_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                     on_delete=models.SET_NULL, related_name='+',
                                     verbose_name="بدأها")

    class Meta:
        verbose_name = "سجل مزامنة"
        verbose_name_plural = "سجلات المزامنة"
        ordering = ['-started_at']

    def __str__(self):
        return f"{self.device.name} — {self.started_at:%Y-%m-%d %H:%M} ({self.status})"


class DevicePunch(models.Model):
    """One raw punch, in the ERP's standard attendance format — the ONLY shape the rest
    of the system (process_punches_into_attendance in sync.py) ever reads. Immutable
    once created; re-syncing can never duplicate a punch because of `dedup_key`.
    """
    PUNCH_TYPE_CHOICES = [
        ('in', 'حضور'),
        ('out', 'انصراف'),
        ('unknown', 'غير محدد'),
    ]
    VERIFICATION_CHOICES = [
        ('fingerprint', 'بصمة إصبع'),
        ('face', 'بصمة وجه'),
        ('card', 'كارت'),
        ('pin', 'رقم سري'),
        ('unknown', 'غير محدد'),
    ]

    device = models.ForeignKey(AttendanceDevice, on_delete=models.CASCADE, related_name='punches')
    device_user_id = models.CharField(max_length=100, verbose_name="رقم المستخدم على الجهاز")
    employee = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                 related_name='device_punches', verbose_name="الموظف",
                                 help_text="فارغ إذا لم يتم ربط رقم المستخدم على الجهاز بموظف بعد")

    punch_timestamp = models.DateTimeField(verbose_name="وقت البصمة")
    punch_type = models.CharField(max_length=10, choices=PUNCH_TYPE_CHOICES, default='unknown', verbose_name="نوع البصمة")
    verification_method = models.CharField(max_length=15, choices=VERIFICATION_CHOICES, default='unknown',
                                           verbose_name="طريقة التحقق")

    raw_reference = models.CharField(max_length=200, blank=True, default='', verbose_name="مرجع السجل الخام بالجهاز")
    # Idempotency guarantee at the DB-constraint level, not just app logic — built from
    # every piece of identifying info the device gave us, so re-running a sync (or two
    # overlapping syncs) can never insert the same punch twice regardless of adapter.
    dedup_key = models.CharField(max_length=64, unique=True, verbose_name="مفتاح منع التكرار")

    sync_log = models.ForeignKey(DeviceSyncLog, null=True, blank=True, on_delete=models.SET_NULL,
                                 related_name='punches')
    synced_at = models.DateTimeField(auto_now_add=True)

    # Whether process_punches_into_attendance() has already folded this punch into an
    # AttendanceRecord — lets that step also be safely re-run without reprocessing.
    processed = models.BooleanField(default=False, verbose_name="تمت معالجته")

    class Meta:
        verbose_name = "بصمة حضور"
        verbose_name_plural = "بصمات الحضور"
        ordering = ['-punch_timestamp']
        indexes = [
            models.Index(fields=['employee', 'punch_timestamp']),
            models.Index(fields=['device', 'device_user_id']),
            models.Index(fields=['processed']),
        ]

    def __str__(self):
        return f"{self.device_user_id} @ {self.punch_timestamp} ({self.device.name})"
