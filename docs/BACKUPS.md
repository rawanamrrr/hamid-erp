# Database Backups (Phase 0.5) & Error Alerting (Phase 0.6)

## Backups

`python manage.py backup_db` writes a timestamped, consistent snapshot to `backups/`
(or `--out <dir>`) and prunes to the most recent `--keep` (default 30).

- **SQLite**: uses the Online Backup API (safe during concurrent writes).
- **PostgreSQL**: `pg_dump -Fc` custom-format `.dump` (restore with `pg_restore`).

```bash
python manage.py backup_db                 # -> backups/backup_YYYYMMDD_HHMMSS.sqlite3
python manage.py backup_db --out D:/pos_backups --keep 60
```

### Schedule it

**Windows (Task Scheduler)** — daily at 02:00:
```
Program/script:  C:\path\to\python.exe
Arguments:       manage.py backup_db --out D:\pos_backups --keep 60
Start in:        E:\Users\Admin\Desktop\wholesale-pos-system\v4
```

**Linux (cron)**:
```
0 2 * * * cd /srv/pos && /srv/pos/venv/bin/python manage.py backup_db --keep 60 >> logs/backup.log 2>&1
```

### Restore

- **SQLite**: stop the app, copy a `backup_*.sqlite3` over `db.sqlite3`.
- **PostgreSQL**: `pg_restore -d wholesale_pos --clean backups/backup_*.dump`

> Keep at least one copy **off the server** (cloud sync / another disk). A backup on the
> same disk as the DB does not survive disk failure.

## Error alerting

`SystemErrorCaptureMiddleware` records every unhandled 500 into the `SystemError` model
(viewable at `/admin/accounts/systemerror/`). On a **new** error it now also:

- creates an in-app notification for every superuser, and
- sends an email alert (async; no-op unless Gmail is configured in System Settings).

Alerts are **throttled** to one per `exception_type + path` per 10 minutes, so a crash
loop cannot flood inboxes.
