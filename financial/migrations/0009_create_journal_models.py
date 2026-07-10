from django.db import migrations, models
import django.utils.timezone
from decimal import Decimal

class Migration(migrations.Migration):
    # NOTE: JournalEntry/JournalLine were ALREADY created in 0006 (the migration
    # history was edited so this migration became a duplicate). On the live DB the
    # tables exist; replaying this on a fresh DB used to fail with "table already
    # exists". We therefore keep the model declarations as STATE-only operations and
    # perform no database changes here, so the chain replays cleanly everywhere.
    dependencies = [
        ('financial', '0008_add_journal_entry_column_sql'),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(database_operations=[], state_operations=[
        migrations.CreateModel(
            name='JournalEntry',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('reference_number', models.CharField(max_length=100, unique=True, db_index=True, verbose_name='رقم القيد / المرجع')),
                ('description', models.TextField(verbose_name='البيان / الوصف')),
                ('posted_at', models.DateTimeField(default=django.utils.timezone.now, db_index=True, verbose_name='تاريخ الترحيل')),
                ('status', models.CharField(max_length=10, choices=[('DRAFT', 'مسودة'), ('POSTED', 'مرحل / معتمد'), ('REVERSED', 'ملغى / معكس')], default='DRAFT', verbose_name='حالة القيد')),
                ('created_by', models.ForeignKey(on_delete=models.PROTECT, to='auth.User', verbose_name='منشئ القيد')),
                ('created_at', models.DateTimeField(auto_now_add=True, verbose_name='تاريخ الإنشاء')),
            ],
            options={
                'verbose_name': 'قيد يومية',
                'verbose_name_plural': 'قيود اليومية',
                'ordering': ['-posted_at', '-id'],
            },
        ),
        migrations.CreateModel(
            name='JournalLine',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('debit', models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'), verbose_name='مدين')),
                ('credit', models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'), verbose_name='دائن')),
                ('entry', models.ForeignKey(on_delete=models.CASCADE, related_name='lines', to='financial.JournalEntry', verbose_name='القيد')),
                ('account', models.ForeignKey(on_delete=models.PROTECT, related_name='ledger_lines', to='financial.Account', verbose_name='الحساب')),
            ],
            options={
                'verbose_name': 'بند قيد',
                'verbose_name_plural': 'بنود القيود',
            },
        ),
        ]),
    ]
