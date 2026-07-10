from .settings import *
import os

# ---------------------------------------------------------
# PRODUCTION OVERRIDES
# ---------------------------------------------------------

# 1. Disable Debug mode for security and performance
DEBUG = False

# 2. Allow all hosts so you can access it from other PCs on the WiFi
ALLOWED_HOSTS = ['*']

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