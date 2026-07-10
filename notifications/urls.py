from django.urls import path
from . import views

urlpatterns = [
    path('api/get/', views.get_notifications, name='get_notifications'),
    path('api/check/', views.check_notifications, name='check_notifications'),
    path('api/read/<int:pk>/', views.mark_as_read, name='mark_notification_read'),
    path('api/read-all/', views.mark_all_read, name='mark_all_notifications_read'),
    path('broadcast/', views.broadcast_notification, name='broadcast_notification'),
]