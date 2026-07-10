from django.db import migrations, models

class Migration(migrations.Migration):
    dependencies = [
        ('financial', '0009_create_journal_models'),
        ('financial', '0010_initialize_coa'),
    ]

    # The transaction.journal_entry column was already added in 0006 (AddField) and
    # 0008 (raw SQL). This duplicate AddField is therefore STATE-only: it keeps the
    # field in the migration state but performs no database change, so a fresh replay
    # does not fail with "duplicate column name".
    operations = [
        migrations.SeparateDatabaseAndState(database_operations=[], state_operations=[
            migrations.AddField(
                model_name='transaction',
                name='journal_entry',
                field=models.ForeignKey(null=True, blank=True, on_delete=models.SET_NULL, to='financial.JournalEntry', related_name='transactions', verbose_name='قيد اليومية'),
            ),
        ]),
    ]
