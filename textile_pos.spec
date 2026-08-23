# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the POS desktop build (onedir).

Build with:  pyinstaller textile_pos.spec --noconfirm
Output:      dist/POS/POS.exe  (ship the whole dist/POS folder)
"""
import os
from PyInstaller.utils.hooks import collect_submodules, collect_data_files, collect_all

# Python 3.10.0 has a stdlib `dis` bug (fixed in 3.10.1) that raises IndexError while
# disassembling some modules (e.g. bottle, pulled in by pywebview), crashing PyInstaller's
# import scan. Guard `_get_const_info` so a broken const yields a placeholder instead of
# crashing the whole build. Self-contained — affects only this build run.
import dis as _dis
_orig_const_info = _dis._get_const_info
def _safe_get_const_info(*args, **kwargs):
    try:
        return _orig_const_info(*args, **kwargs)
    except IndexError:
        return ('<unknown>', '<unknown>')
_dis._get_const_info = _safe_get_const_info

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'textile_pos.production_settings')

LOCAL_APPS = [
    'textile_pos', 'products', 'crm', 'accounts', 'sales', 'settings',
    'search_system', 'dashboard', 'camera_view', 'shipping', 'financial',
    'notifications', 'licensing', 'widget_tweaks',
    # Added after this spec was first written — the cafe/restaurant module (waiter,
    # kitchen KDS, cashier queue, tables) and fingerprint-device attendance sync. Missing
    # these means PyInstaller's static import scan never sees their view/model/signal
    # modules, so the compiled EXE would ImportError or silently 500 on nearly every
    # restaurant page the moment it's opened.
    'restaurant', 'attendance_devices',
]

hiddenimports = []
for app in LOCAL_APPS:
    hiddenimports += collect_submodules(app)
# Dynamically-imported infrastructure PyInstaller can't see by following imports.
hiddenimports += [
    'django.contrib.admin', 'django.contrib.auth', 'django.contrib.contenttypes',
    'django.contrib.sessions', 'django.contrib.messages', 'django.contrib.staticfiles',
    'django.contrib.humanize', 'django.db.backends.sqlite3',
    # Postgres is a documented supported path for multi-cashier customer setups
    # (docs/BUILD_EXE.md -> POSTGRES_CUTOVER.md) via DJANGO_DB_ENGINE=postgres — without
    # bundling the driver, any install that sets that env var crashes immediately with
    # "Error loading psycopg2 or psycopg module" instead of connecting.
    'django.db.backends.postgresql', 'psycopg2',
    'whitenoise', 'whitenoise.middleware', 'whitenoise.storage',
    'waitress', 'openpyxl', 'PIL', 'PIL.Image',
    # ASGI + websockets (realtime KDS/waiter/cashier screens) — see the collect_all loop
    # below for the rest of the twisted stack these pull in dynamically.
    'daphne', 'daphne.server', 'daphne.endpoints', 'channels', 'channels.layers',
    'channels.auth', 'channels.routing', 'channels.generic.websocket',
    'textile_pos.asgi', 'restaurant.routing', 'restaurant.consumers',
    'twisted.internet.reactor', 'twisted.internet.asyncioreactor',
    # asymmetric token verification
    'ecdsa',
    # pywebview (native window) + WebView2/.NET backend
    'webview', 'webview.platforms.edgechromium', 'webview.platforms.winforms',
    'clr', 'proxy_tools', 'bottle',
]

# Non-Python files Django reads at runtime.
datas = [
    ('templates', 'templates'),
    ('staticfiles', 'staticfiles'),
]
datas += collect_data_files('django.contrib.admin')      # admin templates/static
datas += collect_data_files('widget_tweaks')

# pywebview + pythonnet ship native DLLs (WebBrowserInterop, Python.Runtime) — bundle them all.
# psycopg2-binary similarly ships its own libpq/openssl DLLs (psycopg2_binary.libs) that a
# plain hiddenimport doesn't pull in — collect_all grabs those too, not just the .py files.
#
# daphne/twisted/autobahn/channels are the ASGI + websocket stack the app now serves with
# (see pos_launcher._serve_forever). twisted in particular loads big chunks of itself
# dynamically (its plugin system + reactor selection), so PyInstaller's static import scan
# alone misses them — collect_all is what makes websockets work at all in the frozen build.
binaries = []
for _pkg in ('webview', 'pythonnet', 'clr_loader', 'psycopg2',
             'daphne', 'twisted', 'autobahn', 'channels', 'txaio',
             'constantly', 'incremental', 'hyperlink', 'zope.interface'):
    try:
        _d, _b, _h = collect_all(_pkg)
        datas += _d
        binaries += _b
        hiddenimports += _h
    except Exception:
        pass

a = Analysis(
    ['pos_launcher.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=['weasyprint', 'tkinter', 'matplotlib', 'pytest'],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name='DigiFlow',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    icon='app_icon.ico',
)

coll = COLLECT(
    exe, a.binaries, a.datas,
    strip=False, upx=False, name='DigiFlow',
)
