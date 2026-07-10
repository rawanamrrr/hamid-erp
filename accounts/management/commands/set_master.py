from django.core.management.base import BaseCommand
from django.contrib.auth.models import User
from accounts.models import UserProfile

class Command(BaseCommand):
    help = 'Assigns the Master role to a specific user'

    def add_arguments(self, parser):
        parser.add_argument('username', type=str, help='The username to promote to Master')

    def handle(self, *args, **options):
        username = options['username']
        try:
            user = User.objects.get(username=username)
            profile, created = UserProfile.objects.get_or_create(user=user)
            
            profile.is_master = True
            profile.save()
            
            self.stdout.write(self.style.SUCCESS(f'Successfully promoted "{username}" to Master role!'))
            self.stdout.write(self.style.NOTICE(f'This user now has global system oversight and exclusive onboarding access.'))
            
        except User.DoesNotExist:
            self.stdout.write(self.style.ERROR(f'Error: User "{username}" does not exist.'))
