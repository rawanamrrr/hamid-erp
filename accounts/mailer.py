"""Password-reset email delivery (free, via the shop's configured Gmail SMTP).

Reuses the same credentials as the alert emails: SystemSetting.gmail_sender_email +
gmail_app_password (or the DJANGO_GMAIL_* env vars). If no credentials are configured, the
code is written to the server log instead of crashing — so testing/demos work, and the owner
can still read the code. Configuring one Gmail app-password makes real reset emails work for
free (no per-message cost like SMS).
"""
import logging
import os

logger = logging.getLogger('pos.mail')


def _gmail_creds():
    try:
        from settings.models import SystemSetting
        s = SystemSetting.objects.first()
    except Exception:
        s = None
    sender = (os.environ.get('DJANGO_GMAIL_SENDER')
              or (s.gmail_sender_email if s else '') or '').strip()
    pwd = (os.environ.get('DJANGO_GMAIL_APP_PASSWORD')
           or (s.gmail_app_password if s else '') or '').strip()
    return sender, pwd


def send_reset_email(to_email, code):
    """Email a reset code. Returns True if actually sent, False if logged as a fallback."""
    sender, pwd = _gmail_creds()
    subject = "رمز استعادة كلمة المرور"
    body = (f"رمز استعادة كلمة المرور الخاص بك هو: {code}\n"
            f"الرمز صالح لمدة 15 دقيقة. إذا لم تطلب ذلك، تجاهل هذه الرسالة.")
    if not sender or not pwd or not to_email:
        logger.warning("[RESET EMAIL -> %s] code=%s (no SMTP configured - shown for the operator)",
                       to_email, code)
        print(f"[RESET EMAIL -> {to_email}] code={code}")
        return False
    try:
        from django.core.mail.backends.smtp import EmailBackend
        from django.core.mail import EmailMessage
        backend = EmailBackend(host='smtp.gmail.com', port=587, username=sender,
                               password=pwd, use_tls=True, fail_silently=False)
        EmailMessage(subject, body, sender, [to_email], connection=backend).send()
        return True
    except Exception as exc:
        logger.error("reset email send failed: %s", exc)
        print(f"[RESET EMAIL -> {to_email}] code={code} (send failed - shown for testing)")
        return False
