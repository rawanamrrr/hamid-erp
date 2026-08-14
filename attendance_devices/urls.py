from django.urls import path

from . import views

app_name = 'attendance_devices'

urlpatterns = [
    path('', views.device_list, name='device_list'),
    path('add/', views.device_form, name='device_add'),
    path('<int:device_id>/edit/', views.device_form, name='device_form'),
    path('<int:device_id>/delete/', views.device_delete, name='device_delete'),
    path('<int:device_id>/test/', views.device_test_connection, name='device_test_connection'),
    path('<int:device_id>/sync/', views.device_sync_now, name='device_sync_now'),
    path('<int:device_id>/mapping/', views.mapping_view, name='device_mapping'),
]
