from django.db import migrations, models


def add_journal_entry_column(apps, schema_editor):
    # Idempotent: only add the column if it isn't already present. Building a fresh
    # database (e.g. the test DB or a Postgres migration) would otherwise fail with
    # "duplicate column name" because a later regular migration also defines it.
    conn = schema_editor.connection
    existing = [c.name for c in conn.introspection.get_table_description(conn.cursor(), 'financial_transaction')]
    if 'journal_entry_id' in existing:
        return
    schema_editor.execute(
        "ALTER TABLE financial_transaction ADD COLUMN journal_entry_id integer REFERENCES financial_journalentry(id)"
    )


def remove_journal_entry_column(apps, schema_editor):
    # SQLite does not support dropping columns directly; this is a no-op for rollback.
    pass

class Migration(migrations.Migration):
    dependencies = [
        ('financial', '0007_initialize_coa'),
    ]

    operations = [
        migrations.RunPython(add_journal_entry_column, remove_journal_entry_column),
    ]
