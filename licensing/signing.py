"""Asymmetric token signing (ECDSA / NIST256p).

The DEV holds the PRIVATE key and signs activation tokens with it. The app (incl. every
customer EXE) holds only the PUBLIC key below and can *verify* tokens but **cannot create
them** — so extracting the EXE never lets a customer forge tokens.

- Public key: the constant `PUBLIC_KEY_HEX` (ships everywhere; safe to expose).
- Private key: loaded ONLY from `LICENSE_PRIVATE_KEY` (hex) env var, or a `license_private_key.pem`
  file next to the app / project. It is gitignored and excluded from the EXE build, so it lives
  only on YOUR token-generating machine.
"""
import os
import sys

from ecdsa import SigningKey, VerifyingKey, NIST256p, BadSignatureError

# Public verifying key — embedded in the app. (Generated once; safe to publish.)
PUBLIC_KEY_HEX = "746abca3eac707b1d1fd37884b4062fd154f6469137946f1895af498d019ac1b38ee783c713c06d6dfeaa179d4ccd09ccdc21315264e609f77b9e86757464656"

def _private_key_paths():
    name = 'license_private_key.pem'
    paths = [name, os.path.join(os.path.dirname(__file__), '..', name)]
    # Next to the running .exe (so the dev can token-generate from an installed copy too).
    if getattr(sys, 'frozen', False):
        paths.append(os.path.join(os.path.dirname(sys.executable), name))
    return paths


def _verifying_key():
    return VerifyingKey.from_string(bytes.fromhex(PUBLIC_KEY_HEX), curve=NIST256p)


def _signing_key():
    """Load the dev private key (env first, then a local file). None if not present."""
    hexkey = (os.environ.get('LICENSE_PRIVATE_KEY') or '').strip()
    if hexkey:
        try:
            return SigningKey.from_string(bytes.fromhex(hexkey), curve=NIST256p)
        except Exception:
            return None
    for path in _private_key_paths():
        try:
            if os.path.exists(path):
                with open(path, 'r', encoding='utf-8') as fh:
                    return SigningKey.from_string(bytes.fromhex(fh.read().strip()), curve=NIST256p)
        except Exception:
            continue
    return None


def can_sign():
    return _signing_key() is not None


def sign(message: bytes) -> str:
    """Sign bytes with the dev private key → hex signature. Raises if no private key (i.e. on a
    customer machine), which is exactly what we want — only the dev can mint tokens."""
    sk = _signing_key()
    if sk is None:
        raise RuntimeError("private signing key not available on this machine")
    return sk.sign(message).hex()


def verify(message: bytes, signature_hex: str) -> bool:
    """Verify a signature with the embedded public key. Works on every install."""
    try:
        return _verifying_key().verify(bytes.fromhex(signature_hex), message)
    except (BadSignatureError, ValueError, Exception):
        return False
