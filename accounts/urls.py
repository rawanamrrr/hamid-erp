from django.urls import path
from . import views, views_onboarding
from django.contrib.auth import views as auth_views

urlpatterns = [
    # Dashboard Redirect
    path('', views.home_redirect, name='home_redirect'),

    # Auth
    path('login/', views.CustomLoginView.as_view(), name='login'),
    path('logout/', views.logout_view, name='logout'),
    path('forgot-password/', views.forgot_password_request, name='forgot_password'),
    path('forgot-password/verify/', views.forgot_password_verify, name='forgot_password_verify'),
    path('no-access/', views.no_access, name='no_access'),
    
    # Onboarding
    path('onboarding/', views_onboarding.onboarding_wizard, name='onboarding'),
    path('profile/', views.my_profile, name='my_profile'),

    # User Management
    path('users/', views.user_list, name='user_list'),
    path('audit-log/', views.audit_log, name='audit_log'),
    path('approvals/', views.approvals_log, name='approvals_log'),
    path('customize/', views.customize_shortcuts, name='customize_shortcuts'),
    path('users/add/', views.user_create, name='user_create'),
    path('users/<int:pk>/', views.user_detail, name='user_detail'),
    path('users/<int:pk>/edit/', views.user_edit, name='user_edit'),
    path('users/<int:pk>/reset-password/', views.user_reset_password, name='user_reset_password'),
    path('users/<int:pk>/sidebar/', views.user_sidebar_permissions, name='user_sidebar_permissions'),
    path('users/<int:pk>/delete/', views.user_delete, name='user_delete'),
    
    # Roles Management
    path('roles/', views.role_list, name='role_list'),
    path('roles/add/', views.role_create, name='role_create'),
    path('roles/<int:pk>/edit/', views.role_edit, name='role_edit'),
    
    # Global Activity Logs
    path('logs/', views.activity_logs, name='activity_logs'),
    
    # System Errors
    path('error-history/', views.system_error_history, name='error_history'),
    path('error-history/resolve/<int:pk>/', views.resolve_error, name='resolve_error'),
    path('error-history/restart-gunicorn/', views.restart_gunicorn, name='restart_gunicorn'),
    path('api/log-js-error/', views.log_js_error, name='log_js_error'),
]