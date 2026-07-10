from django.shortcuts import redirect
from django.urls import reverse
from django.utils import timezone
from datetime import timedelta
from .models import SystemLicense


class LicenseMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        # Skip middleware for certain paths (activation, login, logout, static files, dev tools)
        excluded_paths = [
            reverse('license_activation'),
            reverse('license_status_api'),
            reverse('login'),
            reverse('logout'),
            '/licensing/dev-licensing-84761234Aa/',  # Dev admin path
            reverse('licensing_dev_login'),
            reverse('licensing_dev_logout'),
            '/accounts/onboarding/',
            '/media/',
            '/static/',
        ]
        
        path = request.path
        if any(path.startswith(p) for p in excluded_paths):
            return self.get_response(request)

        # Check license status
        try:
            license = SystemLicense.objects.first()
            if license:
                # If there's no signature yet (for existing records), auto-set one
                if not license.license_signature and license.subscription_expires_at:
                    license.save()  # Triggers signature generation in save method!
                
                # Check if tampered
                if not license.is_signature_valid():
                    return redirect(reverse('license_activation') + '?status=tampered')
                
                # Check if completely expired (after grace period)
                if license.is_expired:
                    # Mark grace period as used if not already
                    if not license.grace_period_used and license.grace_period_started_at:
                        if timezone.now() > (license.grace_period_started_at + timedelta(days=5)):
                            license.grace_period_used = True
                            license.system_locked = True
                            license.save()
                    return redirect(reverse('license_activation') + '?status=expired')
        except Exception as e:
            print(f"License middleware error: {e}")
            pass

        return self.get_response(request)