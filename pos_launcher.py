"""POS desktop launcher — the entry point compiled into the .exe (PyInstaller).

Runs the Django app under waitress on 127.0.0.1 and shows it inside a **native Windows
window** via pywebview (WebView2). It's a real desktop app: own window, own icon, own
taskbar entry — no browser, no tabs, no address bar. Closing the window quits the program.
The database, media and logs live in a writable `data/` folder, so customer data survives
every program update.
"""
import os
import sys
import time
import socket
import threading
import webbrowser

PORT = 8085
WINDOW_TITLE = 'Wholesale POS System'


def _app_dir():
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def _data_dir():
    """Prefers a `data/` folder NEXT TO the .exe (simplest — one self-contained folder,
    fine for a portable/USB install or running dist/POS directly during dev/testing).
    Falls back to %ProgramData%\\Wholesale POS System when that's not writable — which is
    exactly the installer's default (Program Files requires admin elevation to write into,
    but the app runs as whatever regular Windows user double-clicks the Start Menu icon
    afterward — this used to hard-crash with PermissionError on every single launch for
    anyone who installed to the default Program Files location).
    """
    app_dir = _app_dir()
    candidate = os.path.join(app_dir, 'data')
    try:
        os.makedirs(os.path.join(candidate, 'media'), exist_ok=True)
        return candidate
    except PermissionError:
        pass
    fallback_root = os.environ.get('ProgramData') or os.environ.get('APPDATA') or app_dir
    fallback = os.path.join(fallback_root, 'Wholesale POS System', 'data')
    os.makedirs(os.path.join(fallback, 'media'), exist_ok=True)
    return fallback


APP_DIR = _app_dir()
DATA_DIR = _data_dir()

# Persistent paths must be set BEFORE Django settings are imported.
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'textile_pos.production_settings')
os.environ.setdefault('DJANGO_SQLITE_NAME', os.path.join(DATA_DIR, 'db.sqlite3'))
os.environ.setdefault('DJANGO_MEDIA_ROOT', os.path.join(DATA_DIR, 'media'))
os.environ.setdefault('DJANGO_LOG_DIR', os.path.join(DATA_DIR, 'logs'))

LOG_PATH = os.path.join(DATA_DIR, 'pos.log')


def _log(msg):
    """Append a line to the log file (the EXE has no console window)."""
    try:
        with open(LOG_PATH, 'a', encoding='utf-8') as fh:
            fh.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')}  {msg}\n")
    except Exception:
        pass


def _error_box(text):
    """Show a native Windows message box (no console needed)."""
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(0, str(text), 'POS System', 0x10)  # MB_ICONERROR
    except Exception:
        pass


def _wait_until_up(timeout=45):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection(('127.0.0.1', PORT), timeout=1):
                return True
        except OSError:
            time.sleep(0.3)
    return False


class _PrintAPI:
    """JS-callable API exposed via window.pywebview.api in the desktop window."""
    def open_print_window(self, url):
        import webview
        webview.create_window('طباعة', url, width=950, height=780)


_SINGLE_INSTANCE_MUTEX_NAME = 'Global\\WholesalePOSSystem_SingleInstance'
_ERROR_ALREADY_EXISTS = 183


def _acquire_single_instance_lock():
    """A Windows named mutex — the OS itself guarantees only one process can ever hold it,
    and it's automatically released the instant this process exits or crashes (unlike a
    lock *file*, which could be left behind after a crash and then wrongly block every
    future launch). Returns the mutex handle to keep it alive (must NOT be garbage
    collected / closed while the app runs), or None if another instance already holds it.

    Without this: a double-click while the first launch is still starting up (a very easy
    mistake — there's no window yet, nothing visibly happening for several seconds) starts
    a SECOND process that runs its own `migrate` against the exact same db.sqlite3
    concurrently. SQLite has no protection against two independent processes racing the
    same schema migration — one adds a column and commits, the other (mid-flight, unaware)
    tries to add the same column a moment later and crashes with "duplicate column name"
    before either ever gets to show a window. That's exactly what happened here.
    """
    import ctypes
    handle = ctypes.windll.kernel32.CreateMutexW(None, False, _SINGLE_INSTANCE_MUTEX_NAME)
    if ctypes.windll.kernel32.GetLastError() == _ERROR_ALREADY_EXISTS:
        if handle:
            ctypes.windll.kernel32.CloseHandle(handle)
        return None
    return handle


def _serve_forever():
    """Serve the app over ASGI (daphne) so WEBSOCKETS actually work.

    This used to be `waitress.serve(get_wsgi_application(), ...)`. waitress is a
    WSGI-only server, and WSGI has no concept of a websocket — so inside the packaged
    desktop app EVERY `new WebSocket(...)` the frontend opens (kds.html,
    waiter_tables.html, waiter_order.html, cashier_dashboard.html, delivery.html) was
    refused at the protocol level, and every server-side push_event() broadcast went
    into a void with nobody connected to receive it. That is precisely why nothing on
    the kitchen/waiter/cashier screens ever updated until the page was manually
    refreshed: the app's entire realtime layer was silently dead in the .exe build,
    even though the channels consumers/routing were all present and correct.

    Falls back to the old waitress/WSGI behavior if daphne can't start for any reason,
    so a failure here degrades to "works but needs refresh" (the previous behavior)
    rather than "app won't start at all".
    """
    try:
        from daphne.endpoints import build_endpoint_description_strings
        from daphne.server import Server

        from textile_pos.asgi import application as asgi_app

        # signal_handlers=False is REQUIRED: daphne runs the twisted reactor, and only
        # the main thread may install signal handlers — this runs on a worker thread so
        # the pywebview GUI loop can own the main thread.
        Server(
            application=asgi_app,
            endpoints=build_endpoint_description_strings(host='0.0.0.0', port=PORT),
            signal_handlers=False,
            verbosity=0,
        ).run()
        return
    except Exception as exc:
        _log(f'daphne/ASGI failed to start ({exc}); falling back to waitress (no websockets)')

    from django.core.wsgi import get_wsgi_application
    from waitress import serve
    serve(get_wsgi_application(), host='0.0.0.0', port=PORT, threads=8)


def main():
    _log('starting…')

    _mutex = _acquire_single_instance_lock()
    if _mutex is None:
        _log('another instance is already running — exiting without touching the database')
        _error_box('النظام شغّال بالفعل — دوّر على النافذة المفتوحة (قد تكون خلف نوافذ أخرى)، '
                    'أو في شريط المهام.\n\nThe system is already running — check for its window '
                    '(it may be behind other windows) or the taskbar.')
        sys.exit(0)

    import django
    django.setup()
    from django.core.management import call_command

    call_command('migrate', interactive=False, verbosity=0)   # first run / update upgrades the DB

    threading.Thread(target=_serve_forever, daemon=True).start()

    if not _wait_until_up():
        raise RuntimeError('الخادم لم يبدأ في الوقت المتوقع / server did not start')
    _log(f'serving on http://localhost:{PORT}')

    url = f'http://localhost:{PORT}/'
    # Headless/server-only mode (used for tests/servers): keep running, no window.
    if os.environ.get('POS_NO_WINDOW') == '1':
        while True:
            time.sleep(3600)

    # Native desktop window via pywebview (WebView2). webview.start() runs the GUI loop on
    # the main thread and returns when the user closes the window → the process then exits.
    try:
        import webview
        # window.open()/target="_blank" popups (invoice & draft printing, payment
        # receipts, etc.) must stay inside this window rather than going to the
        # system's default browser — that's a different browser profile with no
        # session cookie, so it always bounced to the login page.
        webview.settings['OPEN_EXTERNAL_LINKS_IN_BROWSER'] = False
        webview.create_window(WINDOW_TITLE, url, width=1300, height=840, min_size=(1024, 700), js_api=_PrintAPI())
        _log('opening native window (pywebview)')
        webview.start()
        _log('window closed — shutting down')
    except Exception as exc:
        _log(f'pywebview failed ({exc}); falling back to default browser')
        webbrowser.open(url)
        while True:
            time.sleep(3600)


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        _log(f'FATAL: {exc}')
        import traceback
        _log(traceback.format_exc())
        _error_box(f'تعذّر تشغيل النظام:\n{exc}\n\nراجع: {LOG_PATH}')
        sys.exit(1)
