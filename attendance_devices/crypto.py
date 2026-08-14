"""Symmetric encryption for device credentials (passwords/API keys/tokens).

Credentials must never sit in source code or in plaintext in the database. The key is
derived from Django's own SECRET_KEY (already required to be a real secret, sourced from
an environment variable — see textile_pos/settings.py) via SHA-256, so there is no new
secret to provision/rotate/leak separately. Rotating DJANGO_SECRET_KEY would make
previously-encrypted credentials unreadable — acceptable here since a device credential
can simply be re-entered from the device management UI, unlike e.g. session data.
"""
import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings


def _fernet():
    digest = hashlib.sha256(settings.SECRET_KEY.encode('utf-8')).digest()
    key = base64.urlsafe_b64encode(digest)
    return Fernet(key)


def encrypt_text(plain: str) -> str:
    if not plain:
        return ''
    return _fernet().encrypt(plain.encode('utf-8')).decode('utf-8')


def decrypt_text(token: str) -> str:
    if not token:
        return ''
    try:
        return _fernet().decrypt(token.encode('utf-8')).decode('utf-8')
    except InvalidToken:
        return ''
