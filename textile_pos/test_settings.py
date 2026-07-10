"""
Test settings — fast, isolated runs against an in-memory SQLite DB.

As of Phase 0.3a the real migration chain replays cleanly on a fresh database, so
tests now exercise the ACTUAL migrations (the previous MIGRATION_MODULES bypass was
removed). This also means the test run is a continuous check that the chain stays
fresh-replayable — which is the prerequisite for the PostgreSQL cutover.

Run:  python manage.py test --settings=textile_pos.test_settings
  (or just use run_tests.bat)
"""
from textile_pos.settings import *  # noqa: F401,F403

# Faster, isolated test runs.
PASSWORD_HASHERS = ['django.contrib.auth.hashers.MD5PasswordHasher']
DATABASES['default'] = {  # noqa: F405
    'ENGINE': 'django.db.backends.sqlite3',
    'NAME': ':memory:',
}
