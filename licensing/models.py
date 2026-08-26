import uuid
import hashlib
import json
from datetime import timedelta, datetime
from django.db import models
from django.utils import timezone
from django.conf import settings
from django.contrib.auth.hashers import make_password, check_password


def get_license_signature(expires_at, store_id):
    """Generate a secure signature to prevent tampering"""
    data = f"{expires_at.isoformat()}-{store_id}-{settings.SECRET_KEY}"
    return hashlib.sha256(data.encode()).hexdigest()


class DeveloperAccount(models.Model):
    """Single developer account for secure authentication"""
    username = models.CharField(max_length=50, unique=True, primary_key=True)
    password_hash = models.CharField(max_length=256)  # Store hashed password only!
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Developer Account"
        verbose_name_plural = "Developer Account"

    def __str__(self):
        return f"Developer: {self.username}"

    def set_password(self, raw_password):
        """Set and store hashed password"""
        self.password_hash = make_password(raw_password)

    def check_password(self, raw_password):
        """Check if password matches"""
        return check_password(raw_password, self.password_hash)


class MasterStore(models.Model):
    # Single source of truth: settings/market_profiles.py (the MarketProfile engine).
    from settings.market_profiles import MARKET_TYPE_CHOICES

    store_id = models.CharField(max_length=50, unique=True, primary_key=True, verbose_name="Store ID")
    public_id = models.CharField(max_length=100, blank=True, null=True, verbose_name="Public ID")
    store_name = models.CharField(max_length=200, verbose_name="Store Name")
    store_type = models.CharField(max_length=20, choices=MARKET_TYPE_CHOICES, default='general', verbose_name="Store Type")
    license_status = models.CharField(max_length=20, choices=[('active', 'Active'), ('inactive', 'Inactive'), ('trial', 'Trial')], default='active', verbose_name="License Status")
    subscription_expires_at = models.DateTimeField(blank=True, null=True, verbose_name="Subscription Expires At")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Created At")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="Updated At")

    class Meta:
        verbose_name = "Master Store"
        verbose_name_plural = "Master Stores"
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.store_name} ({self.store_id})"

    def save(self, *args, **kwargs):
        if not self.store_id:
            self.store_id = f"STORE-{uuid.uuid4().hex[:8].upper()}"
        super().save(*args, **kwargs)


class Device(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, verbose_name="Device ID")
    store = models.ForeignKey(MasterStore, on_delete=models.CASCADE, related_name='devices', verbose_name="Store")
    device_name = models.CharField(max_length=200, blank=True, null=True, verbose_name="Device Name")
    device_info = models.TextField(blank=True, null=True, verbose_name="Device Info")
    is_authorized = models.BooleanField(default=True, verbose_name="Authorized")
    last_used_at = models.DateTimeField(auto_now=True, verbose_name="Last Used At")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Created At")

    class Meta:
        verbose_name = "Device"
        verbose_name_plural = "Devices"
        ordering = ['-last_used_at']

    def __str__(self):
        return f"{self.device_name or 'Unknown'} - {self.store.store_name}"


class TokenLog(models.Model):
    ACTION_CHOICES = [
        ('CHANGE_STORE_TYPE', 'Change Store Type'),
        ('DEVICE_AUTHORIZATION', 'Device Authorization'),
        ('EXTEND_SUBSCRIPTION', 'Extend Subscription'),
        ('GENERAL_SYSTEM_OVERRIDE', 'General System Override'),
        ('TOGGLE_DARK_MODE', 'Toggle Dark Mode'),
        ('ENABLE_MODULE', 'Enable Module'),
        ('DISABLE_MODULE', 'Disable Module'),
        ('CREATE_MASTER_USER', 'Create Master User'),
        ('RESET_USER_PASSWORD', 'Reset User Password'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, verbose_name="Token Log ID")
    store = models.ForeignKey(MasterStore, on_delete=models.CASCADE, related_name='token_logs', verbose_name="Store")
    action = models.CharField(max_length=50, choices=ACTION_CHOICES, verbose_name="Action")
    value = models.TextField(verbose_name="Value")
    token = models.TextField(verbose_name="Activation Token")
    generated_at = models.DateTimeField(default=timezone.now, verbose_name="Generated At")
    expires_at = models.DateTimeField(verbose_name="Expires At")
    used_at = models.DateTimeField(blank=True, null=True, verbose_name="Used At")
    used_by = models.CharField(max_length=200, blank=True, null=True, verbose_name="Used By")
    is_used = models.BooleanField(default=False, verbose_name="Used")

    class Meta:
        verbose_name = "Token Log"
        verbose_name_plural = "Token Logs"
        ordering = ['-generated_at']

    def __str__(self):
        return f"{self.action} - {self.store.store_name}"


class SystemLicense(models.Model):
    store_id = models.CharField(max_length=50, unique=True, primary_key=True, verbose_name="Store ID")
    store_type = models.CharField(max_length=20, choices=MasterStore.MARKET_TYPE_CHOICES, default='general', verbose_name="Store Type")
    subscription_expires_at = models.DateTimeField(blank=True, null=True, verbose_name="Subscription Expires At")
    license_signature = models.CharField(max_length=256, blank=True, null=True, verbose_name="License Signature")
    is_locked = models.BooleanField(default=True, verbose_name="Settings Locked")
    last_token_used = models.TextField(blank=True, null=True, verbose_name="Last Token Used")
    # Replay protection: SHA-256 fingerprints of every token already consumed on THIS install.
    # A token is single-use; re-submitting one (e.g. to stack EXTEND_SUBSCRIPTION) is rejected.
    used_token_hashes = models.JSONField(default=list, blank=True, verbose_name="Used Token Fingerprints")
    last_updated_at = models.DateTimeField(auto_now=True, verbose_name="Last Updated At")
    device_id = models.UUIDField(blank=True, null=True, verbose_name="Local Device ID")
    
    # Grace period tracking
    grace_period_used = models.BooleanField(default=False, verbose_name="Has Used Grace Period")
    grace_period_started_at = models.DateTimeField(blank=True, null=True, verbose_name="Grace Period Started At")
    
    # System lock
    system_locked = models.BooleanField(default=False, verbose_name="System Completely Locked")
    
    # New features
    dark_mode_enabled = models.BooleanField(default=False, verbose_name="Dark Mode Enabled")
    enabled_modules = models.JSONField(default=list, blank=True, verbose_name="Enabled Modules")

    # Phase ①: per-store token signing key, distinct from Django SECRET_KEY. Generated once
    # per install; the dev reads it from the (master/admin-only) activation page to sign that
    # store's tokens. Empty on legacy installs → token validation falls back to SECRET_KEY.
    license_signing_key = models.CharField(max_length=128, blank=True, default='', verbose_name="Per-Store Signing Key")

    class Meta:
        verbose_name = "System License"
        verbose_name_plural = "System Licenses"

    def __str__(self):
        return f"License for {self.store_id}"
        
    def get_full_signature(self):
        """Generate a SECURE signature for ALL critical fields - prevents ANY tampering!"""
        # Include ALL critical fields that could be modified to bypass the system
        fields_to_sign = [
            str(self.store_id),
            str(self.subscription_expires_at.isoformat() if self.subscription_expires_at else "none"),
            str(self.grace_period_used),
            str(self.grace_period_started_at.isoformat() if self.grace_period_started_at else "none"),
            str(self.system_locked),
            str(self.store_type),
            str(self.device_id),
            str(sorted(self.enabled_modules or [])),  # Phase ②: sign entitlements (anti-DB-tamper)
            str(settings.SECRET_KEY),  # The most important part!
        ]
        data = "||".join(fields_to_sign)
        return hashlib.sha3_512(data.encode('utf-8')).hexdigest()

    def save(self, *args, **kwargs):
        if not self.device_id:
            self.device_id = uuid.uuid4()

        # Phase ①: generate a unique per-store signing key once (used to sign/verify tokens).
        if not self.license_signing_key:
            import secrets
            self.license_signing_key = secrets.token_hex(32)

        # Handle subscription changes and grace period
        if self.subscription_expires_at:
            from django.utils import timezone
            
            # Get original subscription date from DB if this is an update
            original = None
            if self.pk:
                try:
                    original = SystemLicense.objects.get(pk=self.pk)
                except SystemLicense.DoesNotExist:
                    pass
            
            if self.subscription_expires_at > timezone.now():
                # Subscription is in future: reset grace period
                self.grace_period_used = False
                self.grace_period_started_at = None
                self.system_locked = False
            elif not self.grace_period_used:
                # Only set grace_period_started_at ONCE — never overwrite it.
                # Overwriting it on every save() was the bug that reset the 5-day clock.
                if not self.grace_period_started_at:
                    self.grace_period_started_at = self.subscription_expires_at
        
        # Update license signature (covers ALL critical fields!)
        if self.store_id:
            self.license_signature = self.get_full_signature()
            
        super().save(*args, **kwargs)
        
    def is_signature_valid(self):
        """Verify NO part of license has been tampered with"""
        if not self.store_id:
            return False
        if not self.license_signature:
            return True  # For existing records

        expected_signature = self.get_full_signature()
        return expected_signature == self.license_signature
        
    @property
    def days_remaining(self):
        """Calculate numeric days remaining (for internal use)"""
        if not self.is_signature_valid():
            return -100  # Tampered, big negative number
        if not self.subscription_expires_at:
            return 0
            
        now = timezone.now()
        delta = self.subscription_expires_at - now
        total_seconds = delta.total_seconds()
        
        # Check if grace period is active (only if grace not already used)
        if total_seconds < 0 and not self.grace_period_used:
            if not self.grace_period_started_at:
                # Use update() to avoid triggering save() override and recursive loops
                SystemLicense.objects.filter(pk=self.pk).update(grace_period_started_at=now)
                self.grace_period_started_at = now
                
            grace_end = self.grace_period_started_at + timedelta(days=5)
            grace_delta = grace_end - now
            grace_total_seconds = grace_delta.total_seconds()
            
            return max(0, grace_total_seconds / (24 * 3600))  # Return decimal days for calculations
            
        return total_seconds / (24 * 3600)
    
    @property
    def formatted_remaining(self):
        """Return formatted string with days, hours, minutes as needed in Arabic"""
        days_decimal = self.days_remaining
        if days_decimal < 0:
            return "منتهي"
            
        now = timezone.now()
        if self.is_grace_period_active:
            end_date = self.grace_period_started_at + timedelta(days=5)
        else:
            end_date = self.subscription_expires_at

        # A license row can legitimately exist with no expiry date at all (the onboarding
        # flow used to create one that way), and this property is read straight from the
        # activation template — so an unguarded `None - now` here is not a quiet bug, it is
        # a hard 500 on the plain GET of /licensing/activate/ before the user types
        # anything. Report it as undetermined rather than crashing the page.
        if end_date is None:
            return "غير محدد"

        delta = end_date - now
        total_seconds = delta.total_seconds()
        
        if total_seconds <= 0:
            return "منتهي"
            
        days = int(total_seconds // (24 * 3600))
        remaining_seconds = total_seconds % (24 * 3600)
        hours = int(remaining_seconds // 3600)
        minutes = int((remaining_seconds % 3600) // 60)
        
        parts = []
        if days > 0:
            if days == 1:
                parts.append(f"{days} يوم")
            else:
                parts.append(f"{days} أيام")
        if hours > 0:
            if hours == 1:
                parts.append(f"{hours} ساعة")
            else:
                parts.append(f"{hours} ساعات")
        if minutes > 0 or (days == 0 and hours == 0):
            if minutes == 1:
                parts.append(f"{minutes} دقيقة")
            else:
                parts.append(f"{minutes} دقائق")
            
        return " و ".join(parts)
        
    @property
    def is_grace_period_active(self):
        """Check if we are currently in the grace period"""
        if not self.is_signature_valid():
            return False
        if self.grace_period_used:
            return False
        if not self.subscription_expires_at:
            return False
            
        now = timezone.now()
        if now <= self.subscription_expires_at:
            return False
            
        if not self.grace_period_started_at:
            return True
            
        grace_end = self.grace_period_started_at + timedelta(days=5)
        return now <= grace_end
        
    @property
    def is_expired(self):
        """Check if subscription is completely expired (after grace period or tampered)"""
        if not self.is_signature_valid():
            return True
        if self.system_locked:
            return True
            
        if not self.subscription_expires_at:
            return False
            
        now = timezone.now()
        
        if now <= self.subscription_expires_at:
            return False
            
        # Past expiry date
        if self.grace_period_used:
            return True

        if not self.grace_period_started_at:
            # Grace hasn't started yet (save() will set it on next request)
            return False

        grace_end = self.grace_period_started_at + timedelta(days=5)
        if now > grace_end:
            # Grace period over — use update() to avoid recursive save() loop
            SystemLicense.objects.filter(pk=self.pk).update(
                grace_period_used=True,
                system_locked=True
            )
            self.grace_period_used = True
            self.system_locked = True
            return True

        # Still inside grace window
        return False
