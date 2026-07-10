import base64
import hmac
import hashlib
import json
from django.conf import settings
from django.utils import timezone
from datetime import timedelta, datetime, timezone as dt_timezone
from functools import wraps
from django.shortcuts import redirect, render


def get_secret_key():
    return settings.SECRET_KEY.encode('utf-8')


def _signing_key_for_store(store_id):
    """Phase ①: per-store token signing key, falling back to Django SECRET_KEY for legacy
    installs that don't have a per-store key yet (and for legacy SECRET_KEY-signed tokens)."""
    if store_id:
        try:
            from licensing.models import SystemLicense
            lic = SystemLicense.objects.filter(store_id=store_id).first()
            if lic and lic.license_signing_key:
                return lic.license_signing_key.encode('utf-8')
        except Exception:
            pass
    return settings.SECRET_KEY.encode('utf-8')


def generate_token(store_id, action, value, expires_in_minutes=60):
    """Mint a signed activation token. Signs with the dev PRIVATE key (asymmetric) — so this
    only works on the dev's machine; customer EXEs can verify but never create tokens."""
    from . import signing
    payload = {
        "store_id": store_id,
        "action": action,
        "value": value,
        "expires_at": (timezone.now() + timedelta(minutes=expires_in_minutes)).isoformat()
    }

    payload_json = json.dumps(payload, sort_keys=True)
    signature = signing.sign(payload_json.encode('utf-8'))

    token_data = {"payload": payload, "signature": signature}
    token_json = json.dumps(token_data)
    encoded_token = base64.urlsafe_b64encode(token_json.encode('utf-8')).decode('utf-8').rstrip('=')
    return f"ACT-{encoded_token}"


def token_fingerprint(token):
    """Stable single-use key for a token (SHA-256 of the full token string).

    Two identical tokens share a fingerprint, so storing it lets us reject replays
    without parsing the token internals. Used by the activation view for replay protection.
    """
    return hashlib.sha256((token or '').encode('utf-8')).hexdigest()


def validate_token(token):
    try:
        if not token.startswith('ACT-'):
            return False, "Invalid token format"

        token_part = token[4:]
        padding_needed = 4 - len(token_part) % 4
        if padding_needed != 4:
            token_part += '=' * padding_needed

        token_json = base64.urlsafe_b64decode(token_part.encode('utf-8')).decode('utf-8')
        token_data = json.loads(token_json)

        payload = token_data['payload']
        signature = token_data['signature']

        payload_json = json.dumps(payload, sort_keys=True)

        # Asymmetric verification with the embedded PUBLIC key — works on every install, and
        # cannot be used to forge tokens (only the dev's private key can sign).
        from . import signing
        if not signing.verify(payload_json.encode('utf-8'), signature):
            return False, "Invalid token signature"

        expires_at = datetime.fromisoformat(payload['expires_at'])
        # If datetime is naive, assume UTC (use datetime.timezone.utc — django.utils.timezone
        # has no UTC attribute in modern Django, which previously crashed all validation).
        if timezone.is_naive(expires_at):
            expires_at = expires_at.replace(tzinfo=dt_timezone.utc)
        else:
            # Convert to UTC if it's in another timezone
            expires_at = expires_at.astimezone(dt_timezone.utc)

        if expires_at < timezone.now():
            return False, "Token has expired"

        return True, payload

    except Exception as e:
        return False, f"Token validation failed: {str(e)}"


def parse_token_value(action, value):
    if action == "CHANGE_STORE_TYPE":
        return value
    elif action == "DEVICE_AUTHORIZATION":
        return value
    elif action == "EXTEND_SUBSCRIPTION":
        try:
            return float(value)
        except ValueError:
            return 30
    elif action in ["TOGGLE_DARK_MODE"]:
        try:
            return bool(value)
        except:
            return True
    elif action in ["ENABLE_MODULE", "DISABLE_MODULE"]:
        return value
    elif action == "GENERAL_SYSTEM_OVERRIDE":
        try:
            return json.loads(value)
        except:
            return value
    return value


def dev_login_required(view_func):
    """Decorator to require developer login for licensing admin views"""
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if not request.session.get('licensing_dev_logged_in', False):
            return redirect('licensing_dev_login')
        return view_func(request, *args, **kwargs)
    return wrapper
