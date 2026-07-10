from django.db import migrations


# Default cafe roles (docs/CAFE_ERP_PLAN.md Phase 1 promised these but they were never
# seeded) — a fresh install otherwise starts with zero roles and an admin has to build
# the entire permission matrix by hand before the system is usable at all.
ROLES = [
    {
        'name': 'كاشير',
        'description': 'يشغّل نقطة البيع، يستقبل طلبات المطبخ الواردة، يقفل الشيفت.',
        'permissions': {
            'pos': ['view', 'create', 'edit'],
            'sales': ['view'],
            'financial': ['view'],
            'products': ['view'],
            'crm': ['view', 'create'],
        },
    },
    {
        'name': 'ويتر',
        'description': 'يفتح شيكات الترابيزات، يضيف أصناف، يرسل للمطبخ.',
        'permissions': {
            'pos': ['view', 'create', 'edit'],
        },
    },
    {
        'name': 'مطبخ',
        'description': 'يتابع شاشة المطبخ (KDS) ويحدّث حالة تحضير الطلبات.',
        'permissions': {
            'pos': ['view', 'edit'],
        },
    },
    {
        'name': 'طيار',
        'description': 'يتابع طلبات الدليفري المسندة له.',
        'permissions': {
            'pos': ['view'],
        },
    },
    {
        'name': 'مدير',
        'description': 'صلاحية كاملة على تشغيل الفرع (بدون إعدادات النظام العامة).',
        'permissions': {
            'dashboard': ['all'],
            'pos': ['all'],
            'financial': ['all'],
            'sales': ['all'],
            'products': ['all'],
            'inventory': ['all'],
            'master_data': ['all'],
            'crm': ['all'],
        },
    },
]


def seed_roles(apps, schema_editor):
    Role = apps.get_model('accounts', 'Role')
    for r in ROLES:
        Role.objects.get_or_create(
            name=r['name'],
            defaults={'description': r['description'], 'permissions': r['permissions']},
        )


def unseed_roles(apps, schema_editor):
    Role = apps.get_model('accounts', 'Role')
    Role.objects.filter(name__in=[r['name'] for r in ROLES]).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0015_userprofile_default_landing'),
    ]

    operations = [
        migrations.RunPython(seed_roles, unseed_roles),
    ]
