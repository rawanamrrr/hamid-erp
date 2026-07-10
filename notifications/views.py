from django.shortcuts import render, get_object_or_404, redirect
from django.http import JsonResponse
from django.contrib.auth.decorators import login_required, user_passes_test
from django.views.decorators.csrf import csrf_exempt
from django.contrib import messages
from django.contrib.auth.models import User
from .models import Notification
from .forms import BroadcastNotificationForm

def is_admin(user):
    return user.is_superuser

# ... (existing views)

@login_required
@user_passes_test(is_admin)
def broadcast_notification(request):
    """
    Interface for administrators to send notifications to large groups of users.
    """
    if request.method == 'POST':
        form = BroadcastNotificationForm(request.POST)
        if form.is_valid():
            target_type = form.cleaned_data['target_type']
            title = form.cleaned_data['title']
            message = form.cleaned_data['message']
            link = form.cleaned_data['link']
            
            recipients = []
            
            if target_type == 'all':
                recipients = User.objects.filter(is_active=True)
            elif target_type == 'role':
                role = form.cleaned_data['target_role']
                recipients = User.objects.filter(profile__roles=role, is_active=True)
            elif target_type == 'user':
                recipients = form.cleaned_data['target_users']
            
            # Create notifications efficiently
            notifications = []
            for recipient in recipients:
                notifications.append(Notification(
                    recipient=recipient,
                    title=title,
                    message=message,
                    link=link,
                    created_by=request.user
                ))
            
            if notifications:
                Notification.objects.bulk_create(notifications)
                messages.success(request, f"تم إرسال الإشعار بنجاح إلى {len(notifications)} مستخدم.")
            else:
                messages.warning(request, "لم يتم العثور على أي مستخدمين لإرسال الإشعار لهم.")
                
            return redirect('broadcast_notification')
    else:
        form = BroadcastNotificationForm()
        
    recent_broadcasts = Notification.objects.filter(created_by=request.user).order_by('-created_at')[:20]
    
    return render(request, 'notifications/broadcast.html', {
        'form': form,
        'recent_broadcasts': recent_broadcasts,
        'title': 'بث الإشعارات للجماهير'
    })

@login_required
def check_notifications(request):
    """
    Lightweight API to check for new notifications.
    Returns unread count and latest notification ID.
    Used for optimized polling.
    """
    unread_count = Notification.objects.filter(recipient=request.user, is_read=False).count()
    latest_notif = Notification.objects.filter(recipient=request.user).first()
    latest_id = latest_notif.id if latest_notif else 0
    
    return JsonResponse({
        'unread_count': unread_count,
        'latest_id': latest_id
    })

@login_required
def get_notifications(request):
    """
    API to fetch unread notifications for the current user via AJAX.
    """
    # Fetch unread notifications
    unread_notifications = Notification.objects.filter(recipient=request.user, is_read=False)[:10]
    unread_count = Notification.objects.filter(recipient=request.user, is_read=False).count()
    
    data = []
    for notif in unread_notifications:
        data.append({
            'id': notif.id,
            'title': notif.title,
            'message': notif.message,
            'link': notif.link,
            'time': notif.created_at.strftime('%H:%M'),
            'sender': notif.created_by.first_name if notif.created_by else 'النظام'
        })
    
    return JsonResponse({
        'unread_count': unread_count,
        'notifications': data
    })

@csrf_exempt
@login_required
def mark_as_read(request, pk):
    """
    API to mark a notification as read when clicked.
    """
    if request.method == 'POST':
        notification = get_object_or_404(Notification, pk=pk, recipient=request.user)
        notification.is_read = True
        notification.save()
        return JsonResponse({'status': 'success'})
    return JsonResponse({'status': 'error'}, status=400)

@csrf_exempt
@login_required
def mark_all_read(request):
    """
    API to mark all notifications as read.
    """
    if request.method == 'POST':
        Notification.objects.filter(recipient=request.user, is_read=False).update(is_read=True)
        return JsonResponse({'status': 'success'})
    return JsonResponse({'status': 'error'}, status=400)