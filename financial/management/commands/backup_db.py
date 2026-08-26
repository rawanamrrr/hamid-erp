"""
backup_db — timestamped database backup with retention pruning (Phase 0.5).

Works for both engines:
  * sqlite3    -> safe file copy using the SQLite Online Backup API
  * postgresql -> pg_dump custom format (.dump)

Usage:
    python manage.py backup_db
    python manage.py backup_db --out D:/backups --keep 30

Schedule it (see docs/BACKUPS.md):
  * Windows: Task Scheduler -> daily -> `python manage.py backup_db`
  * Linux:   cron -> `0 2 * * * /path/venv/bin/python manage.py backup_db`
"""
import os
import shutil
import sqlite3
import subprocess
from datetime import datetime
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Create a timestamped database backup and prune old ones."

    def add_arguments(self, parser):
        parser.add_argument('--out', default=None, help="Backup directory (default: <BASE_DIR>/backups)")
        parser.add_argument('--keep', type=int, default=30, help="How many recent backups to retain (default: 30)")

    def handle(self, *args, **opts):
        db = settings.DATABASES['default']
        # BASE_DIR sits inside the program folder, which in the packaged desktop build is
        # under Program Files and is not writable by the cashier running the app — the
        # launcher points DJANGO_BACKUP_DIR at the same writable data folder it uses for
        # the database and logs. Falls back to BASE_DIR for a plain source checkout.
        default_out = os.environ.get('DJANGO_BACKUP_DIR') or (Path(settings.BASE_DIR) / 'backups')
        out_dir = Path(opts['out']) if opts['out'] else Path(default_out)
        out_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        engine = db['ENGINE']

        if 'sqlite3' in engine:
            target = out_dir / f"backup_{stamp}.sqlite3"
            self._backup_sqlite(db['NAME'], target)
            prefix = 'backup_'
            suffix = '.sqlite3'
        elif 'postgresql' in engine:
            target = out_dir / f"backup_{stamp}.dump"
            self._backup_postgres(db, target)
            prefix = 'backup_'
            suffix = '.dump'
        else:
            raise CommandError(f"Unsupported engine for backup: {engine}")

        size_kb = target.stat().st_size / 1024
        self.stdout.write(self.style.SUCCESS(f"Backup written: {target} ({size_kb:.0f} KB)"))
        self._prune(out_dir, prefix, suffix, opts['keep'])

    def _backup_sqlite(self, src_path, target):
        # Online Backup API: consistent even if the app is writing concurrently.
        src = sqlite3.connect(src_path)
        try:
            dst = sqlite3.connect(str(target))
            try:
                with dst:
                    src.backup(dst)
            finally:
                dst.close()
        finally:
            src.close()

    def _resolve_pg_dump(self):
        """Locate pg_dump — shared with the settings-page backup/restore buttons."""
        from textile_pos.pg_tools import find_pg_tool, PgToolNotFound
        try:
            return find_pg_tool('pg_dump')
        except PgToolNotFound as exc:
            raise CommandError(str(exc))

    def _backup_postgres(self, db, target):
        env = os.environ.copy()
        if db.get('PASSWORD'):
            env['PGPASSWORD'] = db['PASSWORD']
        cmd = [
            self._resolve_pg_dump(), '-Fc',
            '-h', db.get('HOST') or '127.0.0.1',
            '-p', str(db.get('PORT') or '5432'),
            '-U', db.get('USER') or 'postgres',
            '-d', db['NAME'],
            '-f', str(target),
        ]
        try:
            subprocess.run(cmd, env=env, check=True, capture_output=True, text=True)
        except FileNotFoundError:
            raise CommandError(f"Could not run {cmd[0]} — the file disappeared or is not executable.")
        except subprocess.CalledProcessError as e:
            raise CommandError(f"pg_dump failed: {e.stderr}")

    def _prune(self, out_dir, prefix, suffix, keep):
        backups = sorted(
            [p for p in out_dir.iterdir() if p.name.startswith(prefix) and p.name.endswith(suffix)],
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        for old in backups[keep:]:
            try:
                old.unlink()
                self.stdout.write(f"Pruned old backup: {old.name}")
            except OSError:
                pass
