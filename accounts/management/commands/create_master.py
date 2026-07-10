from django.core.management.base import BaseCommand
from django.contrib.auth.models import User
from accounts.models import UserProfile
import getpass

class Command(BaseCommand):
    help = 'Creates a brand new Master account from scratch'

    def handle(self, *args, **options):
        self.stdout.write(self.style.MIGRATE_HEADING('--- Create Fresh Master Account ---'))
        
        username = input('Username: ').strip()
        if not username:
            self.stdout.write(self.style.ERROR('Error: Username cannot be empty.'))
            return

        if User.objects.filter(username=username).exists():
            self.stdout.write(self.style.ERROR(f'Error: User "{username}" already exists. Use set_master to promote existing users.'))
            return

        email = input('Email (optional): ').strip()
        self.stdout.write(self.style.WARNING('\n[NOTE] Security Hidden Input: You will NOT see characters while typing your password.'))
        password = getpass.getpass('Password: ')
        confirm_password = getpass.getpass('Confirm Password: ')

        if password != confirm_password:
            self.stdout.write(self.style.ERROR('Error: Passwords do not match.'))
            return

        try:
            # Create the User
            user = User.objects.create_user(username=username, email=email, password=password)
            user.is_staff = True  # Masters should have staff/superuser access typically
            user.is_superuser = True
            user.save()

            # Ensure Profile exists and set Master
            profile, created = UserProfile.objects.get_or_create(user=user)
            profile.is_master = True
            profile.save()

            self.stdout.write(self.style.SUCCESS(f'\nSuccessfully created Master account: "{username}"'))
            self.stdout.write(self.style.NOTICE('This account has global system ownership, superuser rights, and onboarding access.'))

        except Exception as e:
            self.stdout.write(self.style.ERROR(f'Error: {str(e)}'))
