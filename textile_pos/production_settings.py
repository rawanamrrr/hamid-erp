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

# A CSRF failure in the desktop window is a dead end — no address bar, no Back button, so
# Django's bare 403 page traps the cashier with no way out but killing the app. And the
# usual cause is innocent: a login screen left open long enough for its CSRF cookie to be
# replaced. This view sends them back to reload the form with a fresh token instead.
CSRF_FAILURE_VIEW = 'textile_pos.csrf.csrf_failure'
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
# Errors MUST go to a file here, not just the console. The packaged desktop app runs with
# no console window at all (pos_launcher.py, pywebview), so a console-only handler sent
# every 500 traceback into nowhere — leaving a customer-reported error with literally no
# evidence anywhere on their machine to diagnose it from. Same rotating file the dev
# settings use, inside the app's own writable data folder.
_log_dir = Path(os.environ.get('DJANGO_LOG_DIR', str(BASE_DIR / 'logs')))
try:
    _log_dir.mkdir(parents=True, exist_ok=True)
except OSError:
    _log_dir = Path(BASE_DIR)

LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'verbose': {'format': '{asctime} [{levelname}] {name}: {message}', 'style': '{'},
    },
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
            'formatter': 'verbose',
        },
        'file': {
            'class': 'logging.handlers.RotatingFileHandler',
            'filename': str(_log_dir / 'app.log'),
            'maxBytes': 5 * 1024 * 1024,
            'backupCount': 5,
            'encoding': 'utf-8',
            'formatter': 'verbose',
        },
    },
    'root': {
        'handlers': ['console', 'file'],
        'level': 'WARNING',
    },
    'loggers': {
        # Unhandled view exceptions (the 500 pages) are logged by django.request —
        # without naming it explicitly its propagation could be silenced, which is
        # exactly the traceback we need most.
        'django.request': {
            'handlers': ['console', 'file'],
            'level': 'ERROR',
            'propagate': False,
        },
    },
}