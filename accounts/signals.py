from django.db.models.signals import post_save
from django.dispatch import receiver
from django.contrib.auth.models import User
from django.contrib.auth.signals import user_logged_in, user_logged_out
from django.utils import timezone
from .models import UserProfile, UserActivityLog, UserIPHistory

@receiver(post_save, sender=User)
def create_or_update_user_profile(sender, instance, created, **kwargs):
    if created:
        UserProfile.objects.create(user=instance)

def get_client_ip(request):
    x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
    if x_forwarded_for:
        ip = x_forwarded_for.split(',')[0]
    else:
        ip = request.META.get('REMOTE_ADDR')
    return ip

@receiver(user_logged_in)
def log_user_login(sender, request, user, **kwargs):
    ip = get_client_ip(request)
    user_agent = request.META.get('HTTP_USER_AGENT', '')
    
    # Update latest IP in profile
    if hasattr(user, 'profile'):
        user.profile.last_known_ip = ip
        user.profile.save(update_fields=['last_known_ip'])
    
    # Update IP History Tracker
    if ip:
        ip_record, created = UserIPHistory.objects.get_or_create(
            user=user, ip_address=ip
        )
        if not created:
            ip_record.last_seen = timezone.now()
            ip_record.save(update_fields=['last_seen'])
            
    # Record Activity Log
    UserActivityLog.objects.create(
        user=user,
        action_type='LOGIN',
        module='system',
        description='تسجيل دخول للنظام',
        ip_address=ip,
        user_agent=user_agent
    )

@receiver(user_logged_out)
def log_user_logout(sender, request, user, **kwargs):
    if user is not None:
        ip = get_client_ip(request)
        UserActivityLog.objects.create(
            user=user,
            action_type='LOGOUT',
            module='system',
            description='تسجيل خروج من النظام',
            ip_address=ip,
            user_agent=request.META.get('HTTP_USER_AGENT', '')
        )

