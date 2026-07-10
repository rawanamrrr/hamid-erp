"""POS desktop launcher — the entry point compiled into the .exe (PyInstaller).

Runs the Django app under waitress on 127.0.0.1 and shows it inside a **native Windows
window** via pywebview (WebView2). It's a real desktop app: own window, own icon, own
taskbar entry — no browser, no tabs, no address bar. Closing the window quits the program.
The database, media and logs live in a writable `data/` folder NEXT TO the .exe, so customer
data survives every program update.
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


APP_DIR = _app_dir()
DATA_DIR = os.path.join(APP_DIR, 'data')
os.makedirs(os.path.join(DATA_DIR, 'media'), exist_ok=True)

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


def main():
    _log('starting…')
    import django
    django.setup()
    from django.core.management import call_command
    from django.core.wsgi import get_wsgi_application

    call_command('migrate', interactive=False, verbosity=0)   # first run / update upgrades the DB
    application = get_wsgi_application()

    from waitress import serve
    threading.Thread(
        target=lambda: serve(application, host='0.0.0.0', port=PORT, threads=8),
        daemon=True,
    ).start()

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
