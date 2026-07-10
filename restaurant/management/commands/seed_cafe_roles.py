from django.core.management.base import BaseCommand

from accounts.models import Role


ROLE_DEFINITIONS = {
    'كاشير': {
        'pos': ['view', 'create', 'edit', 'void'],
        'financial': ['view', 'edit'],
        'sales': ['view'],
    },
    'ويتر': {
        'pos': ['view', 'create'],
    },
    'مطبخ': {
        'pos': ['view', 'edit'],
    },
    'دليفري': {
        'pos': ['view', 'edit'],
    },
    'مدير فرع': {
        'pos': ['view', 'create', 'edit', 'void'],
        'financial': ['view', 'edit'],
        'sales': ['view', 'create', 'edit'],
        'products': ['view', 'edit'],
    },
}


class Command(BaseCommand):
    help = "Seed the standard cafe roles (كاشير/ويتر/مطبخ/دليفري/مدير فرع) with sane default permissions. Safe to re-run — updates existing roles instead of duplicating them."

    def handle(self, *args, **options):
        for name, permissions in ROLE_DEFINITIONS.items():
            role, created = Role.objects.update_or_create(
                name=name, defaults={'permissions': permissions},
            )
            action = 'Created' if created else 'Updated'
            self.stdout.write(self.style.SUCCESS(f"{action} role: {name}"))
        self.stdout.write(self.style.SUCCESS("Done. Assign these roles to users from Accounts > Users."))
