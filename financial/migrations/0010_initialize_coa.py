from django.db import migrations
from decimal import Decimal

def create_coa(apps, schema_editor):
    Account = apps.get_model('financial', 'Account')
    # Helper to create or get account
    def get_or_create(code, name, account_type, parent=None):
        obj, created = Account.objects.get_or_create(
            code=code,
            defaults={
                'name': name,
                'account_type': account_type,
                'balance': Decimal('0.00'),
                'is_active': True,
                'parent': parent,
            },
        )
        return obj

    # Top‑level categories
    assets = get_or_create('1000', 'الأصول', 'ASSET')
    liabilities = get_or_create('2000', 'الخصوم', 'LIABILITY')
    equity = get_or_create('3000', 'حقوق الملكية', 'EQUITY')
    revenue = get_or_create('4000', 'الإيرادات', 'REVENUE')
    expenses = get_or_create('5000', 'المصروفات', 'EXPENSE')

    # Asset sub‑accounts
    cash = get_or_create('1100', 'النقدية وما في حكمها', 'ASSET', parent=assets)
    get_or_create('1110', 'درج الكاشير', 'ASSET', parent=cash)
    get_or_create('1120', 'الخزنة الرئيسية', 'ASSET', parent=cash)
    get_or_create('1130', 'حساب بنكي', 'ASSET', parent=cash)
    get_or_create('1140', 'فودافون كاش', 'ASSET', parent=cash)
    get_or_create('1150', 'إنستا باي', 'ASSET', parent=cash)
    get_or_create('1200', 'العملاء / ذمم مدينة', 'ASSET', parent=assets)
    get_or_create('1300', 'تقييم المخزون', 'ASSET', parent=assets)

    # Liability sub‑accounts
    get_or_create('2100', 'الموردين / ذمم دائنة', 'LIABILITY', parent=liabilities)

    # Revenue sub‑accounts
    get_or_create('4100', 'إيرادات المبيعات', 'REVENUE', parent=revenue)

    # Expense sub‑accounts
    get_or_create('5100', 'تكلفة البضاعة المباعة - COGS', 'EXPENSE', parent=expenses)
    get_or_create('5200', 'مصروفات تشغيلية', 'EXPENSE', parent=expenses)

def delete_coa(apps, schema_editor):
    Account = apps.get_model('financial', 'Account')
    Account.objects.all().delete()

class Migration(migrations.Migration):
    dependencies = [
        ('financial', '0009_create_journal_models'),
    ]

    operations = [
        migrations.RunPython(create_coa, delete_coa),
    ]
