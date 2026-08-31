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
import subprocess
import threading
import webbrowser
from datetime import datetime

PORT = 8085
WINDOW_TITLE = 'DigiFlow'


def _silence_child_console_windows():
    """Stop any child process from flashing a black console window on screen.

    This app is built windowed (PyInstaller `console=False`), so it owns no console of
    its own. When anything here spawns a console program, Windows helpfully creates a
    BRAND NEW console window for it — which appears as a black cmd box that pops up and
    vanishes a second later, in the middle of whatever the cashier was doing.

    Our own call sites already pass CREATE_NO_WINDOW, but third-party libraries don't and
    can't be expected to: pyzk shells out to ping.exe before connecting to the attendance
    device, and the standard library's own `platform` module runs `cmd /c ver` to read the
    Windows version. Both fired on a normal working day and both were reported as "why
    does this cmd keep showing?".

    Rather than chase each caller, default `creationflags` to CREATE_NO_WINDOW for every
    subprocess started anywhere in this process. Callers that pass their own flags are
    left alone, so nothing that already made a deliberate choice is overridden. Every
    subprocess helper (run/call/check_output/Popen) funnels through Popen.__init__, so
    patching that one place covers all of them.
    """
    if sys.platform != 'win32':
        return
    CREATE_NO_WINDOW = 0x08000000
    _original_init = subprocess.Popen.__init__

    def _patched_init(self, *args, **kwargs):
        if not kwargs.get('creationflags'):
            kwargs['creationflags'] = CREATE_NO_WINDOW
        return _original_init(self, *args, **kwargs)

    subprocess.Popen.__init__ = _patched_init


_silence_child_console_windows()


def _app_dir():
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def _data_dir():
    """Prefers a `data/` folder NEXT TO the .exe (simplest — one self-contained folder,
    fine for a portable/USB install or running dist/POS directly during dev/testing).
    Falls back to %ProgramData%\\DigiFlow when that's not writable — which is
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
    fallback = os.path.join(fallback_root, 'DigiFlow', 'data')
    os.makedirs(os.path.join(fallback, 'media'), exist_ok=True)
    return fallback


APP_DIR = _app_dir()
DATA_DIR = _data_dir()

DB_CONFIG_PATH = os.path.join(APP_DIR, 'db_config.json')


def _load_db_config():
    """Point Django at PostgreSQL when the installer wrote a db_config.json next to the
    .exe; otherwise stay on the built-in SQLite file (zero setup, the default).

    Lives in a file rather than environment variables because the app is launched by
    whatever user double-clicks the shortcut — machine-wide env vars would have to be set
    separately on every install, and per-user ones wouldn't apply to a different cashier
    logging into the same PC.

    setdefault(), not direct assignment: a real environment variable is an explicit
    admin/developer override and must still win over the file.
    """
    try:
        import json
        with open(DB_CONFIG_PATH, encoding='utf-8') as fh:
            cfg = json.load(fh)
    except (OSError, ValueError):
        return None   # no config (or unreadable/corrupt) -> SQLite

    if str(cfg.get('engine', '')).lower() not in ('postgres', 'postgresql'):
        return None

    os.environ.setdefault('DJANGO_DB_ENGINE', 'postgres')
    os.environ.setdefault('DJANGO_DB_NAME', cfg.get('name') or 'digiflow')
    os.environ.setdefault('DJANGO_DB_USER', cfg.get('user') or 'postgres')
    os.environ.setdefault('DJANGO_DB_PASSWORD', cfg.get('password') or '')
    os.environ.setdefault('DJANGO_DB_HOST', cfg.get('host') or '127.0.0.1')
    os.environ.setdefault('DJANGO_DB_PORT', str(cfg.get('port') or '5432'))
    return cfg


# Persistent paths must be set BEFORE Django settings are imported.
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'textile_pos.production_settings')
_DB_CONFIG = _load_db_config()
os.environ.setdefault('DJANGO_SQLITE_NAME', os.path.join(DATA_DIR, 'db.sqlite3'))
os.environ.setdefault('DJANGO_MEDIA_ROOT', os.path.join(DATA_DIR, 'media'))
os.environ.setdefault('DJANGO_LOG_DIR', os.path.join(DATA_DIR, 'logs'))
# Uploads (staff photos, expense receipts, a custom notification sound) are saved into
# DATA_DIR/media above, but with DEBUG=False Django serves /media/ only when this is set
# (see textile_pos/urls.py). It is off by default because a server deployment puts nginx
# in front to serve uploads directly — the desktop build has no nginx, Django IS the
# server, so without this every uploaded image 404s and renders as a broken thumbnail
# even though the file is sitting right there on disk.
os.environ.setdefault('DJANGO_SERVE_MEDIA', '1')
# Same reason as the media/log dirs: the backup command's own default is BASE_DIR/backups,
# which lives inside the program folder under Program Files and is not writable by the
# cashier running the app.
os.environ.setdefault('DJANGO_BACKUP_DIR', os.path.join(DATA_DIR, 'backups'))

LOG_PATH = os.path.join(DATA_DIR, 'pos.log')


def _log(msg):
    """Append a line to the log file (the EXE has no console window)."""
    try:
        with open(LOG_PATH, 'a', encoding='utf-8') as fh:
            fh.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')}  {msg}\n")
    except Exception:
        pass


_SEP = chr(92)   # backslash; registry paths are built with join() to keep them readable


def _webview2_status():
    """Why (if at all) pywebview would refuse to use WebView2 on this machine.

    Returns '' when WebView2 will be used, or a short Arabic reason when it won't.

    Deliberately mirrors pywebview's own `_is_chromium()`
    (webview/platforms/winforms.py) condition for condition, because that function is
    what actually decides. If this check is looser than that one, the app sails past this
    guard and pywebview still drops to the ancient MSHTML/Internet Explorer engine — which
    renders the UI as raw unstyled HTML with giant icons and no hint as to why. Matching
    it exactly means every case that would produce that screen is reported here instead.

    Two requirements, not one — missing the .NET check is precisely how a machine with
    WebView2 installed could still have fallen through:
      * .NET Framework >= 4.6.2 (release 394802)
      * a WebView2 runtime build >= 86.0.622.0, in any of the four channels pywebview
        accepts (stable/beta/dev/canary), per-user or machine-wide
    """
    if sys.platform != 'win32':
        return ''
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                            r'SOFTWARE\Microsoft\NET Framework Setup\NDP\v4\Full') as key:
            release, _ = winreg.QueryValueEx(key, 'Release')
        if release < 394802:
            return 'إصدار .NET Framework قديم (مطلوب 4.6.2 أو أحدث)'
    except OSError:
        return '.NET Framework 4.6.2 غير مثبَّت'

    def _at_least(minimum, found):
        """True when `found` is >= `minimum`, comparing version parts numerically."""
        try:
            want = [int(x) for x in minimum.split('.')]
            got = [int(x) for x in found.split('.')]
        except (TypeError, ValueError):
            return False
        for i, want_part in enumerate(want):
            got_part = got[i] if i < len(got) else 0
            if got_part != want_part:
                return got_part > want_part
        return True

    # The four channels pywebview accepts; any one of them satisfies it.
    channels = (
        '{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}',   # runtime (what the installer ships)
        '{2CD8A007-E189-409D-A2C8-9AF4EF3C72AA}',   # beta
        '{0D50BFEC-CD6A-4F9A-964C-C7416E3ACB10}',   # dev
        '{65C35B14-6C1D-4122-AC46-7148CC9D6497}',   # canary
    )
    for guid in channels:
        for hive, path in (
            (winreg.HKEY_LOCAL_MACHINE, _SEP.join(('SOFTWARE', 'WOW6432Node', 'Microsoft', 'EdgeUpdate', 'Clients', guid))),
            (winreg.HKEY_LOCAL_MACHINE, _SEP.join(('SOFTWARE', 'Microsoft', 'EdgeUpdate', 'Clients', guid))),
            (winreg.HKEY_CURRENT_USER, _SEP.join(('SOFTWARE', 'Microsoft', 'EdgeUpdate', 'Clients', guid))),
        ):
            try:
                with winreg.OpenKey(hive, path) as key:
                    build, _ = winreg.QueryValueEx(key, 'pv')
            except OSError:
                continue
            if build and _at_least('86.0.622.0', build):
                return ''
    return 'مكوّن Microsoft Edge WebView2 غير مثبَّت (أو إصداره قديم جداً)'


def _webview2_present():
    return _webview2_status() == ''


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

    def pick_folder(self, current=''):
        """Open Windows' own folder picker and return the chosen path.

        A web page cannot do this on its own — browsers deliberately never reveal a real
        filesystem path — so the backup-folder setting could only ever be typed by hand,
        which is easy to get wrong and impossible to verify while typing. The desktop
        window has no such restriction, so it offers the real picker and hands the path
        back to the page.

        Returns '' when the dialog is cancelled, so the caller can simply do nothing.
        """
        import webview
        try:
            windows = webview.windows
            if not windows:
                return ''
            chosen = windows[0].create_file_dialog(
                webview.FOLDER_DIALOG, directory=current or '')
            if not chosen:
                return ''
            return chosen[0] if isinstance(chosen, (list, tuple)) else str(chosen)
        except Exception as exc:
            _log(f'folder picker failed: {exc}')
            return ''

    def open_external(self, url):
        """Hand a link that leads outside the app to the system's real browser.

        Links to WhatsApp and the like used to load inside the app's own window, which
        has no address bar and no Back button — so following one replaced the POS with a
        web page the user could not get out of without killing the program.
        """
        if not isinstance(url, str) or not url.lower().startswith(('http://', 'https://')):
            return False
        webbrowser.open(url)
        return True


_SINGLE_INSTANCE_MUTEX_NAME = 'Global\\DigiFlow_SingleInstance'
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


def _wait_for_postgres_ready(conn_kwargs, timeout=40):
    """Retry connecting to PostgreSQL's own maintenance database until the SERVER is
    actually ready to accept connections, or `timeout` seconds pass. Raises the last
    connection error once the timeout is exhausted.

    PostgreSQL runs as its own Windows service, separate from this app, and after a
    reboot it can take anywhere from a couple of seconds to well over a minute to
    finish its own startup/recovery — during that window it refuses EVERY connection
    with "the database system is starting up", which looks identical to a real
    misconfiguration but is not one: it just isn't ready yet. On a till that boots
    straight into this app every morning (or has it in the Windows startup folder),
    launching before Postgres has finished starting is routine, not exceptional — and
    surfacing that as an immediate fatal error, with no retry at all, is exactly the
    "why does this show sometimes" report this fixes. Trying again for a while first is
    the difference between a real error message and a spurious one.
    """
    import psycopg2
    deadline = time.time() + timeout
    last_exc = None
    attempt = 0
    while time.time() < deadline:
        attempt += 1
        try:
            psycopg2.connect(dbname='postgres', connect_timeout=5, **conn_kwargs).close()
            if attempt > 1:
                _log(f'PostgreSQL became ready after {attempt} attempt(s)')
            return
        except psycopg2.OperationalError as exc:
            last_exc = exc
            _log(f'PostgreSQL not ready yet (attempt {attempt}): {exc}'.strip())
            time.sleep(2)
    raise last_exc


def _ensure_postgres_database():
    """Create the target PostgreSQL database on first run if it doesn't exist yet.

    `manage.py migrate` can create TABLES but never the DATABASE itself — it just fails
    with "database ... does not exist". Without this the customer would have to open
    pgAdmin/psql and create it by hand before the app would start even once, which is
    not something a cafe owner should ever have to do.

    Connects to the always-present 'postgres' maintenance database to issue CREATE
    DATABASE, since you cannot create a database from inside itself.
    """
    if os.environ.get('DJANGO_DB_ENGINE', '').lower() not in ('postgres', 'postgresql'):
        return

    import psycopg2
    from psycopg2 import sql
    from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT

    name = os.environ['DJANGO_DB_NAME']
    conn_kwargs = dict(
        user=os.environ.get('DJANGO_DB_USER', 'postgres'),
        password=os.environ.get('DJANGO_DB_PASSWORD', ''),
        host=os.environ.get('DJANGO_DB_HOST', '127.0.0.1'),
        port=os.environ.get('DJANGO_DB_PORT', '5432'),
    )

    # Wait for the SERVER itself to be reachable before trying anything else — both
    # connection attempts below would otherwise fail with the same "starting up" error
    # right after a reboot, and the second one (unlike this wait) is not retried.
    _wait_for_postgres_ready(conn_kwargs)

    # Already there? Then there is nothing to do — and we must NOT touch it.
    try:
        psycopg2.connect(dbname=name, connect_timeout=5, **conn_kwargs).close()
        return
    except psycopg2.OperationalError:
        pass

    conn = psycopg2.connect(dbname='postgres', connect_timeout=5, **conn_kwargs)
    try:
        conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)   # CREATE DATABASE can't run in a transaction
        with conn.cursor() as cur:
            cur.execute('SELECT 1 FROM pg_database WHERE datname = %s', (name,))
            if cur.fetchone() is None:
                # UTF8 so Arabic product/customer names store correctly.
                cur.execute(sql.SQL("CREATE DATABASE {} ENCODING 'UTF8' TEMPLATE template0")
                            .format(sql.Identifier(name)))
                _log(f'created PostgreSQL database "{name}"')
    finally:
        conn.close()


ATTENDANCE_SYNC_MINUTES = int(os.environ.get('DIGIFLOW_ATTENDANCE_SYNC_MINUTES', '5'))


def _backup_target_dir():
    """Where the daily backup should be written.

    An admin-chosen folder (ثوابت النظام ← النسخ الاحتياطي) wins, so backups can live on a
    different drive from the program — a copy sitting on the same disk as the original is
    no protection against that disk failing. Falls back to the app's own data folder when
    unset or unusable, because a backup somewhere is worth far more than no backup at all.
    """
    from settings.policies import get_policy

    fallback = os.path.join(DATA_DIR, 'backups')
    chosen = (get_policy('backups.folder') or '').strip()
    if not chosen:
        os.makedirs(fallback, exist_ok=True)
        return fallback

    try:
        os.makedirs(chosen, exist_ok=True)
        # Prove it is really writable now rather than discovering it isn't at 2am: a
        # disconnected USB drive or a permission-protected folder both look fine to
        # makedirs but fail on the first write.
        probe = os.path.join(chosen, '.digiflow-write-test')
        with open(probe, 'w') as fh:
            fh.write('ok')
        os.unlink(probe)
        return chosen
    except OSError as exc:
        _log(f'backup folder "{chosen}" is not usable ({exc}); using {fallback} instead')
        os.makedirs(fallback, exist_ok=True)
        return fallback


def _backup_already_taken_today(target_dir):
    """Has today's backup already been written?

    Read off the filenames rather than remembered in memory, so closing and reopening the
    app during the day cannot produce a second backup — or, worse, make it think one was
    taken when the app was actually shut at the scheduled moment.
    """
    import glob

    stamp = datetime.now().strftime('%Y%m%d')
    return bool(glob.glob(os.path.join(target_dir, f'backup_{stamp}_*')))


def _daily_backup_loop():
    """Take one full backup a day, at the time the admin picked.

    A shop that has never lost its data does not think about backups, which is exactly why
    this cannot be a button someone has to remember to press. The schedule, the folder and
    how many copies to keep all come from ثوابت النظام, and are re-read on every tick so a
    change takes effect without restarting the program.

    If the app was closed at the scheduled time, the backup is taken as soon as it opens
    later that day — a till that is switched off overnight would otherwise never back up
    at all with an early-hours schedule.
    """
    import time as _time

    # Let startup finish first: migrations and the first requests are already competing
    # for the database.
    _time.sleep(150)

    while True:
        try:
            from settings.policies import get_policy

            if get_policy('backups.daily_enabled'):
                scheduled = str(get_policy('backups.daily_time') or '02:00')
                try:
                    hh, mm = (int(p) for p in scheduled.split(':')[:2])
                except ValueError:
                    hh, mm = 2, 0

                now = datetime.now()
                due_at = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
                target_dir = _backup_target_dir()

                if now >= due_at and not _backup_already_taken_today(target_dir):
                    keep = int(get_policy('backups.keep_count') or 30)
                    from django.core.management import call_command
                    call_command('backup_db', out=target_dir, keep=keep, verbosity=0)
                    _log(f'daily backup written to {target_dir}')
        except Exception as exc:
            # A failed backup must never take the till down. Logged, then retried on the
            # next tick — and because the "already taken today" check reads the folder,
            # a failure now still leaves today's backup pending rather than skipped.
            _log(f'daily backup failed: {exc}')

        _time.sleep(60)


def _attendance_sync_loop():
    """Pull fingerprint punches from the attendance devices on a timer, forever.

    The same work the "مزامنة الآن" button does and the sync_attendance_devices
    management command does — but neither is reachable on a customer install: there is no
    manage.py in a packaged build to schedule, and expecting a cafe to remember to click
    a button every day means the attendance data is silently stale exactly when payroll
    needs it. So the app syncs itself.

    Deliberately forgiving: an unreachable device (switched off, moved, wrong IP) must
    never take down the app or spam the log, so every failure is swallowed and simply
    retried on the next tick. File-based (csv_import) devices are skipped — they have no
    live connection to poll.
    """
    import time as _time

    # Let the server finish coming up first; a sync during startup would fight the
    # migrate/first-request work for the same SQLite file.
    _time.sleep(90)

    while True:
        try:
            from attendance_devices.models import AttendanceDevice
            from attendance_devices.sync import process_punches_into_attendance, sync_device

            devices = list(AttendanceDevice.objects.filter(enabled=True)
                           .exclude(adapter_type='csv_import'))
            for device in devices:
                try:
                    sync_device(device)
                except Exception as exc:
                    _log(f'auto-sync: device "{device.name}" failed — {exc}')

            if devices:
                result = process_punches_into_attendance()
                if result.get('attendance_records_updated'):
                    _log(f"auto-sync: {result['attendance_records_updated']} سجل حضور محدّث")
        except Exception as exc:
            _log(f'auto-sync loop error: {exc}')

        _time.sleep(max(60, ATTENDANCE_SYNC_MINUTES * 60))


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

    if _DB_CONFIG:
        _log(f"database: PostgreSQL {os.environ['DJANGO_DB_NAME']} @ "
             f"{os.environ['DJANGO_DB_HOST']}:{os.environ['DJANGO_DB_PORT']}")
        try:
            _ensure_postgres_database()
        except Exception as exc:
            # Deliberately fatal instead of quietly continuing on SQLite: a silent
            # fallback would start writing today's sales into a DIFFERENT database from
            # yesterday's, and nobody would notice until the numbers didn't add up.
            _log(f'FATAL: cannot reach PostgreSQL — {exc}')
            _error_box(
                'تعذّر الاتصال بقاعدة بيانات PostgreSQL.\n\n'
                f'{exc}\n\n'
                'تأكد أن خدمة PostgreSQL شغّالة وأن بيانات الاتصال صحيحة في:\n'
                f'{DB_CONFIG_PATH}\n\n'
                'Could not connect to PostgreSQL — check the service is running and the '
                'connection details in the file above are correct.')
            sys.exit(1)
    else:
        _log(f'database: SQLite ({os.path.join(DATA_DIR, "db.sqlite3")})')

    import django
    django.setup()
    from django.core.management import call_command

    call_command('migrate', interactive=False, verbosity=0)   # first run / update upgrades the DB

    threading.Thread(target=_serve_forever, daemon=True).start()
    threading.Thread(target=_attendance_sync_loop, daemon=True).start()
    threading.Thread(target=_daily_backup_loop, daemon=True).start()

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

        # WebView2 missing → pywebview silently falls back to the ANCIENT Internet
        # Explorer engine (MSHTML) instead of refusing to start. IE cannot run the modern
        # JavaScript this app is built on: the whole UI is styled by tailwind.js at
        # runtime, and the POS/waiter/kitchen screens are Alpine + fetch + websockets.
        # The result is a window that opens and looks catastrophically broken — raw
        # unstyled HTML with giant icons — with nothing anywhere saying why. That is
        # exactly what happened on a customer's Windows 10 machine, which (unlike
        # Windows 11) does not ship the WebView2 runtime.
        #
        # The installer now bundles and installs it, so this should never trigger — but
        # if the runtime is ever removed or the install is copied to another machine by
        # hand, say so plainly instead of showing a broken screen.
        _wv_problem = _webview2_status()
        if _wv_problem:
            _log(f'FATAL: {_wv_problem} — refusing to fall back to MSHTML')
            _error_box(
                'لا يمكن تشغيل البرنامج على هذا الجهاز.'
                + chr(10) + chr(10)
                + f'السبب: {_wv_problem}'
                + chr(10) + chr(10)
                + 'الحل: أعد تشغيل ملف تثبيت البرنامج (DigiFlow-Setup) — سيقوم بتثبيت المكوّن الناقص تلقائياً.'
                + chr(10) + chr(10)
                + f'Cannot start: {_wv_problem}'
                + chr(10)
                + 'Re-run the DigiFlow installer, which installs the missing component automatically.')
            sys.exit(1)
        # window.open()/target="_blank" popups (invoice & draft printing, payment
        # receipts, etc.) must stay inside this window rather than going to the
        # system's default browser — that's a different browser profile with no
        # session cookie, so it always bounced to the login page.
        webview.settings['OPEN_EXTERNAL_LINKS_IN_BROWSER'] = False
        # pywebview blocks downloads by default, and WebView2 then cancels the navigation
        # without a word — so every "تصدير Excel / CSV / PDF" button in the app looked
        # completely dead: no file, no error, nothing. Turning this on lets WebView2 show
        # its normal save prompt for any response sent as an attachment.
        webview.settings['ALLOW_DOWNLOADS'] = True
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
