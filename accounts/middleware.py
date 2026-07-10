import traceback
import sys
from django.core.exceptions import PermissionDenied
from django.http import Http404
from django.shortcuts import redirect
from .models import SystemError

class RequireOnboardingMiddleware:
    """
    Middleware that forces users to complete their profile (onboarding)
    before they can access the rest of the application.
    """
    def __init__(self, get_response):
        self.get_response = get_response
        
        # Paths that are allowed to be accessed without completing onboarding
        self.allowed_paths = [
            '/accounts/onboarding/',
            '/accounts/logout/',
            '/admin/',
            '/static/',
            '/media/',
        ]

    def __call__(self, request):
        if request.user.is_authenticated:
            try:
                profile = request.user.profile
            except (AttributeError, Exception):
                # Should not happen because of the post_save signal
                return self.get_response(request)

            # Bypass for superuser ONLY IF they don't want to see onboarding 
            # or if we are not in a strict mode. However, if they are already completed, just let them through.
            if request.user.is_superuser and profile.onboarding_completed:
                return self.get_response(request)
                
            # Check if user needs onboarding (ONLY for Master)
            if profile.is_master and not profile.onboarding_completed:
                # Ensure the current request path is not in the allowed exemptions
                if not any(request.path.startswith(p) for p in self.allowed_paths):
                    return redirect('onboarding')
                    
        return self.get_response(request)

class SystemErrorCaptureMiddleware:
    """
    Middleware that captures all 500 errors (unhandled exceptions) and
    logs them into the SystemError model for admin review.
    """
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        return self.get_response(request)

    def process_exception(self, request, exception):
        # PermissionDenied (403) and Http404 (404) are normal, expected control flow —
        # not bugs — so don't log them as system errors or alert admins about them. Only
        # genuine unhandled exceptions (that become 500s) belong here.
        if isinstance(exception, (PermissionDenied, Http404)):
            return None

        # Capture critical details of the exception
        exc_type, exc_value, exc_traceback = sys.exc_info()
        tb_str = "".join(traceback.format_exception(exc_type, exc_value, exc_traceback))
        
        try:
            err = SystemError.objects.create(
                user=request.user if request.user.is_authenticated else None,
                path=request.path,
                exception_type=str(exc_type.__name__),
                message=str(exception),
                traceback=tb_str,
                ip_address=request.META.get('REMOTE_ADDR'),
                user_agent=request.META.get('HTTP_USER_AGENT')
            )
            # Phase 0.6: alert admins on new 500s — throttled so a crash loop can't spam.
            self._alert_admins(request, err)
        except Exception as e:
            # Failsafe if logging itself fails
            print(f"FAILED TO LOG SYSTEM ERROR: {e}")

        return None # Let standard Django exception handling continue (show 500 page)

    def _alert_admins(self, request, err):
        """Notify superusers (in-app + email) about a new server error, throttled per
        exception-type+path so a repeating crash doesn't flood. All best-effort."""
        from django.core.cache import cache
        throttle_key = f"err_alert:{err.exception_type}:{err.path}"
        if cache.get(throttle_key):
            return
        cache.set(throttle_key, 1, 600)  # at most one alert per 10 minutes per signature

        # In-app notifications to superusers
        try:
            from django.contrib.auth.models import User
            from notifications.models import Notification
            sus = User.objects.filter(is_superuser=True, is_active=True)
            Notification.objects.bulk_create([
                Notification(
                    recipient=su,
                    title=f"⚠️ خطأ في النظام: {err.exception_type}",
                    message=f"المسار: {err.path} — {err.message[:180]}",
                    link="/admin/accounts/systemerror/",
                ) for su in sus
            ])
        except Exception:
            pass

        # Email alert (async, no-ops if email isn't configured)
        try:
            from sales.email_utils import send_alert_email
            html = (
                f"<h3>خطأ في النظام (500)</h3>"
                f"<p><b>النوع:</b> {err.exception_type}</p>"
                f"<p><b>المسار:</b> {err.path}</p>"
                f"<p><b>الرسالة:</b> {err.message}</p>"
                f"<pre style='font-size:11px;direction:ltr'>{(err.traceback or '')[:3000]}</pre>"
            )
            send_alert_email(f"[POS] خطأ 500: {err.exception_type}", html)
        except Exception:
            pass
