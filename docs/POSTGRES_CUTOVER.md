# PostgreSQL Cutover (Phase 0.3)

The migration chain now replays cleanly on a fresh database (Phase 0.3a repaired the
duplicated `financial` migrations). That was the only blocker. SQLite remains the
default for development; production should move to PostgreSQL for concurrent-write
safety (multiple cashiers) — SQLite serializes writes and throws `database is locked`.

## Why move

- SQLite locks the whole DB on every write → POS stalls/errors under 2+ cashiers.
- PostgreSQL gives row-level locking (the `select_for_update()` in the inventory and
  financial services then actually does what it promises).

## One-time cutover

1. **Install & create the database**
   ```bash
   sudo -u postgres createuser pos_user -P          # set a password
   sudo -u postgres createdb wholesale_pos -O pos_user
   pip install psycopg2-binary
   ```

2. **Set environment** (see `.env.example`)
   ```
   DJANGO_DB_ENGINE=postgres
   DJANGO_DB_NAME=wholesale_pos
   DJANGO_DB_USER=pos_user
   DJANGO_DB_PASSWORD=********
   DJANGO_DB_HOST=127.0.0.1
   DJANGO_DB_PORT=5432
   ```

3. **Build the schema on Postgres** (chain is fresh-replay clean)
   ```bash
   python manage.py migrate
   ```

4. **Move the data** from the existing SQLite DB. Use a natural-key dump to avoid
   content-type / permission PK clashes:
   ```bash
   # with SQLite still active (unset DJANGO_DB_ENGINE)
   python manage.py dumpdata --natural-foreign --natural-primary \
       --exclude contenttypes --exclude auth.permission \
       --exclude admin.logentry --exclude sessions.session \
       -o /tmp/data.json
   # switch env to Postgres, then:
   python manage.py loaddata /tmp/data.json
   ```

5. **Verify integrity after load**
   ```bash
   run_tests.bat                       # invariants still hold
   python manage.py migrate --check    # no pending migrations
   ```
   Then spot-check: a few customer balances, drawer balance, and
   `Σ batches == WarehouseStock` for a handful of products.

## Rollback

Keep the SQLite file. To revert, unset `DJANGO_DB_ENGINE` (defaults back to SQLite).
Nothing in code is Postgres-specific.

## Notes

- `CONN_MAX_AGE=60` is already set for Postgres (persistent connections).
- After cutover, schedule `pg_dump` backups (Phase 0.5) instead of copying the SQLite file.
