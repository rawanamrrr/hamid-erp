"""Pluggable SMS sender for password-reset codes.

Automated SMS is never free — it needs a paid gateway. This module isolates the delivery
mechanism so the rest of the app doesn't care how a message is sent:

- Default ('log'): writes the message to the server log / console. Works out of the box for
  testing and demos; the code is visible to whoever runs the server (the owner).
- 'twilio': real SMS via Twilio. Set env vars and it activates — no code change needed:
      POS_SMS_BACKEND=twilio
      TWILIO_ACCOUNT_SID=...
      TWILIO_AUTH_TOKEN=...
      TWILIO_FROM_NUMBER=+1...

Add other gateways (a local provider, etc.) by writing one more `_send_*` function and a
branch in `send_sms`. The reset flow already calls `send_sms(phone, message)`.
"""
import logging
import os

logger = logging.getLogger('pos.sms')


def _send_log(phone, message):
    # Visible to the operator running the app — fine for single-store/desktop installs.
    logger.warning("[SMS→%s] %s", phone, message)
    print(f"[SMS→{phone}] {message}")
    return True


def _send_twilio(phone, message):
    sid = os.environ.get('TWILIO_ACCOUNT_SID')
    token = os.environ.get('TWILIO_AUTH_TOKEN')
    sender = os.environ.get('TWILIO_FROM_NUMBER')
    if not (sid and token and sender):
        logger.error("Twilio backend selected but TWILIO_* env vars are missing.")
        return False
    try:
        from twilio.rest import Client  # pip install twilio (only needed for this backend)
        Client(sid, token).messages.create(body=message, from_=sender, to=phone)
        return True
    except Exception as exc:
        logger.error("Twilio send failed: %s", exc)
        return False


def send_sms(phone, message):
    """Send `message` to `phone`. Returns True on success. Never raises."""
    backend = os.environ.get('POS_SMS_BACKEND', 'log').lower()
    try:
        if backend == 'twilio':
            return _send_twilio(phone, message)
        return _send_log(phone, message)
    except Exception as exc:
        logger.error("send_sms error: %s", exc)
        return False
