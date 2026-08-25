from decimal import Decimal
from django.db import models
from .market_profiles import MARKET_TYPE_CHOICES as _MARKET_TYPE_CHOICES

class SystemSetting(models.Model):
    # Single source of truth lives in settings/market_profiles.py (the MarketProfile engine).
    MARKET_TYPE_CHOICES = _MARKET_TYPE_CHOICES

    shop_name = models.CharField(max_length=200, default="DigiFlow", verbose_name="اسم المحل")
    # No placeholder default — an unset address must print as nothing on receipts, not
    # a literal "العنوان الافتراضي" line nobody actually typed in (see the receipt
    # templates, which now only render this paragraph when it's non-blank).
    address = models.TextField(blank=True, default="", verbose_name="العنوان")
    phone = models.CharField(max_length=50, blank=True, default="01000000000", verbose_name="أرقام الهاتف")
    
    # Market Type (NEW)
    market_type = models.CharField(
        max_length=20,
        choices=MARKET_TYPE_CHOICES,
        default='clothes',
        verbose_name="نوع المتجر / السوق"
    )
    is_market_type_locked = models.BooleanField(
        default=False,
        verbose_name="قفل نوع المتجر؟ (لا يمكن تغييره)"
    )

    # VAT / service-charge rate + included-vs-added toggles moved to the ثوابت النظام
    # policy engine (settings/policies.py, group 'tax') — a single global, DB-backed
    # setting like everything else here, just organized alongside the other store-wide
    # behavior toggles instead of the company-profile fields on this model.

    # Site-wide color palette — remaps the two "primary action" Tailwind color families
    # (teal/indigo, used interchangeably across screens as the brand/CTA color; see
    # UI_COLOR_PALETTES in settings/views.py) to the chosen hue via a runtime
    # tailwind.config override in base.html. Deliberately leaves semantic colors
    # (red=danger, green=success, amber=warning...) untouched.
    UI_COLOR_THEME_CHOICES = [
        ('default', 'الافتراضي (تركواز)'),
        ('indigo', 'نيلي'),
        ('emerald', 'زمردي'),
        ('rose', 'وردي غامق'),
        ('pink', 'زهري'),
        ('amber', 'كهرماني'),
        ('orange', 'برتقالي'),
        ('violet', 'بنفسجي'),
        ('fuchsia', 'فوشيا'),
        ('sky', 'سماوي'),
        ('cyan', 'سيان'),
        ('lime', 'ليموني'),
        ('slate', 'رمادي'),
        ('brown', 'بني'),
        ('beige', 'بيج'),
    ]
    ui_color_theme = models.CharField(max_length=20, choices=UI_COLOR_THEME_CHOICES, default='default',
                                      verbose_name="نظام ألوان الواجهة")

    # Logo stored as Base64 string
    logo_base64 = models.TextField(blank=True, null=True, verbose_name="كود اللوجو (Base64)")
    
    # Receipt Footer
    return_policy = models.TextField(blank=True, default="البضاعة المباعة ترد وتستبدل خلال 14 يوم", verbose_name="سياسة الاسترجاع")
    thank_you_text = models.CharField(max_length=200, default="شكراً لزيارتكم", verbose_name="رسالة الشكر")
    
    # QR Options
    show_qr = models.BooleanField(default=True, verbose_name="إظهار QR Code")
    qr_link = models.CharField(max_length=255, blank=True, verbose_name="رابط QR Code (اختياري)")

    # Printer Settings
    printer_name = models.CharField(max_length=255, blank=True, null=True, verbose_name="اسم الطابعة الأساسية (Direct Print)")
    # Kitchen tickets are printed SERVER-SIDE straight to this printer (see
    # restaurant/direct_print.py) instead of through a browser print dialog — that's the
    # only way to send them to a different printer than the customer invoice, since a
    # browser gives no way to choose the target printer. Leave empty to keep the old
    # behaviour (ticket opens in a print preview like before).
    kitchen_printer_name = models.CharField(
        max_length=255, blank=True, null=True,
        verbose_name="طابعة المطبخ (طباعة مباشرة للتذاكر)")
    
    # Notification Sound
    notification_sound = models.FileField(upload_to='sounds/', blank=True, null=True, verbose_name="صوت التنبيه (MP3)")

    # Email Settings
    gmail_sender_email = models.EmailField(blank=True, default='', verbose_name="بريد Gmail المرسل")
    gmail_app_password = models.CharField(max_length=64, blank=True, default='', verbose_name="كلمة مرور التطبيق")
    email_recipients = models.TextField(blank=True, default='', verbose_name="المستلمون (مفصولين بفواصل)")
    
    def save(self, *args, **kwargs):
        # ضمان وجود ID=1 دائماً (Singleton)
        self.pk = 1
        super(SystemSetting, self).save(*args, **kwargs)

    def __str__(self):
        return "إعدادات النظام"


class SystemPolicy(models.Model):
    """Singleton store for the Layer-2 policy engine (settings/policies.py).

    `values` holds only the policies the customer has overridden; everything else falls back
    to the registry default. Master/customer-configurable — distinct from per-user permissions
    and from dev-locked feature entitlements.
    """
    values = models.JSONField(default=dict, blank=True, verbose_name="قيم ثوابت النظام")
    updated_at = models.DateTimeField(auto_now=True)

    def save(self, *args, **kwargs):
        self.pk = 1  # Singleton
        super().save(*args, **kwargs)

    def __str__(self):
        return "ثوابت النظام"
