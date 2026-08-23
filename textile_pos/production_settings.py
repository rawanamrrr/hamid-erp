from .settings import *
import os

# ---------------------------------------------------------
# PRODUCTION OVERRIDES
# ---------------------------------------------------------

# 1. Disable Debug mode for security and performance
DEBUG = False

# 2. Allow all hosts so you can access it from other PCs on the WiFi
ALLOWED_HOSTS = ['*']

# 2c. CSRF — settings.py's own CSRF_TRUSTED_ORIGINS defaults to the real production HTTPS
# domain (mekawyerp.shop), which obviously never matches http://localhost:8085 — that
# mismatch alone was enough to make every POST (login included) fail CSRF verification
# with a raw 403 page. Trusting the actual local origins fixes it.
#
# Deliberately NOT touching SESSION_COOKIE_SECURE/CSRF_COOKIE_SECURE/SAMESITE here (they
# stay at Django's plain defaults: Secure=False, SameSite='Lax') — a `Secure` cookie is
# ONLY ever sent back over HTTPS, with a narrow Chromium exception for the literal
# `localhost` origin. That exception does NOT cover a phone/tablet on the same WiFi
# connecting over plain http://192.168.x.x:8085 (a real LAN IP is not "localhost" to any
# browser) — Secure=True was tried once and it silently broke every other device on the
# network from ever getting a session cookie at all, while only "fixing" the desktop app's
# own window. If the desktop app's CSRF 403 comes back, that needs a narrower fix scoped to
# just the pywebview window (e.g. a middleware keyed off the request's Host header) rather
# than a blanket Secure=True that collateral-damages every LAN client.
CSRF_TRUSTED_ORIGINS = [
    f'http://localhost:{os.environ.get("POS_PORT", "8085")}',
    f'http://127.0.0.1:{os.environ.get("POS_PORT", "8085")}',
]
# See textile_pos/middleware.py — rewrites the session/CSRF cookies to Secure+SameSite=None
# ONLY for requests whose Host is literally localhost/127.0.0.1 (the desktop app's own
# window), which is what actually fixes the WebView2 CSRF-cookie-drop quirk without
# breaking every phone/tablet on the LAN (which never uses that hostname). Inserted FIRST
# so its post-processing runs LAST — after SessionMiddleware/CsrfViewMiddleware have
# already set their cookies on the response for it to rewrite.
MIDDLEWARE.insert(0, 'textile_pos.middleware.LocalhostSecureCookieMiddleware')

# 2b. Writable media path (uploads) — set by the desktop launcher to a folder next to the
#     .exe so customer uploads persist across program updates. Falls back to the dev path.
_media = os.environ.get('DJANGO_MEDIA_ROOT')
if _media:
    MEDIA_ROOT = _media

# 3. Static Files Configuration (WhiteNoise)
#    This allows the server to serve CSS/Images without a separate web server like Nginx.

STATIC_ROOT = BASE_DIR / 'staticfiles'

# Insert WhiteNoise middleware after SecurityMiddleware
# We check first to avoid duplication
if 'whitenoise.middleware.WhiteNoiseMiddleware' not in MIDDLEWARE:
    MIDDLEWARE.insert(1, 'whitenoise.middleware.WhiteNoiseMiddleware')

# Use WhiteNoise storage for static files
STATICFILES_STORAGE = 'whitenoise.storage.CompressedManifestStaticFilesStorage'

# ---------------------------------------------------------
# LOGGING (Optional - prints errors to console)
# ---------------------------------------------------------
LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
        },
    },
    'root': {
        'handlers': ['console'],
        'level': 'WARNING',
    },
}