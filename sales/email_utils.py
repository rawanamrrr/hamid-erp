import threading
import logging
from django.core.mail import EmailMessage
from django.core.mail.backends.smtp import EmailBackend
from settings.models import SystemSetting

logger = logging.getLogger(__name__)

def _send_email_thread(subject, html_body, sender, app_password, recipients):
    try:
        backend = EmailBackend(
            host='smtp.gmail.com',
            port=587,
            username=sender,
            password=app_password,
            use_tls=True,
            fail_silently=False
        )
        msg = EmailMessage(subject, html_body, sender, recipients, connection=backend)
        msg.content_subtype = 'html'
        msg.send()
    except Exception as exc:
        logger.exception("Failed to send alert email '%s': %s", subject, str(exc))

def send_alert_email(subject, html_body):
    """
    Sends an email to all configured recipients asynchronously.
    """
    import os
    settings_obj = SystemSetting.objects.first()
    # Phase 3.6: prefer the Gmail credentials from the environment so the secret can
    # live outside the database; fall back to the stored values for compatibility.
    sender = (os.environ.get('DJANGO_GMAIL_SENDER') or (settings_obj.gmail_sender_email if settings_obj else '') or '').strip()
    app_password = (os.environ.get('DJANGO_GMAIL_APP_PASSWORD') or (settings_obj.gmail_app_password if settings_obj else '') or '').strip()
    recipients = [e.strip() for e in ((settings_obj.email_recipients if settings_obj else '') or '').split(',') if e.strip()]

    if not sender or not app_password or not recipients:
        return

    # Run in a background thread so the HTTP response isn't blocked
    thread = threading.Thread(
        target=_send_email_thread,
        args=(subject, html_body, sender, app_password, recipients)
    )
    thread.start()
