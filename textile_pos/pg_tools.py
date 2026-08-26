"""Locating the PostgreSQL command-line tools on Windows.

pg_dump and pg_restore ship with PostgreSQL, but the Windows installer puts them in
`Program Files\\PostgreSQL\\<major>\\bin` and never adds that to PATH. So a bare
`pg_dump` resolves only on a machine where someone installed the client tools by hand —
on every customer install it fails with "not found", which is how database backup and
restore came to be quietly broken there.
"""
import os
import shutil
from pathlib import Path


class PgToolNotFound(Exception):
    """Raised with a message meant to be shown to the person who clicked the button."""


def find_pg_tool(name):
    """Full path to `name` ('pg_dump', 'pg_restore', 'psql'). Raises PgToolNotFound."""
    env_key = 'POS_' + name.upper()          # POS_PG_DUMP / POS_PG_RESTORE
    override = os.environ.get(env_key)
    if override:
        if Path(override).is_file():
            return override
        raise PgToolNotFound(f'{env_key} يشير إلى مسار غير موجود: {override}')

    found = shutil.which(name)
    if found:
        return found

    candidates = []
    roots = {os.environ.get('ProgramW6432'), os.environ.get('ProgramFiles'),
             os.environ.get('ProgramFiles(x86)')}
    for root in filter(None, roots):
        base = Path(root) / 'PostgreSQL'
        if not base.is_dir():
            continue
        for version_dir in base.iterdir():
            exe = version_dir / 'bin' / f'{name}.exe'
            if exe.is_file():
                candidates.append(exe)

    if candidates:
        # Newest major first: an old leftover 12 sitting beside a current 18 refuses to
        # work against the newer server ("server version mismatch").
        def major(path):
            try:
                return int(str(path.parent.parent.name).split('.')[0])
            except (ValueError, IndexError):
                return -1
        candidates.sort(key=major, reverse=True)
        return str(candidates[0])

    raise PgToolNotFound(
        f'لم يتم العثور على {name}. يأتي مع PostgreSQL — تم البحث في PATH وفي '
        f'Program Files\\PostgreSQL\\<الإصدار>\\bin. إذا كان مثبتاً في مكان آخر '
        f'فاضبط المتغير {env_key} على مساره الكامل.'
    )


def pg_env(db):
    """Environment for a pg_* subprocess, carrying the password out of band."""
    env = os.environ.copy()
    if db.get('PASSWORD'):
        env['PGPASSWORD'] = db['PASSWORD']
    return env


def pg_conn_args(db):
    return [
        '-h', db.get('HOST') or '127.0.0.1',
        '-p', str(db.get('PORT') or '5432'),
        '-U', db.get('USER') or 'postgres',
    ]
