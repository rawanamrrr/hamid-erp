from django.urls import path
from . import views

urlpatterns = [
    path('user-guide/', views.user_guide_view, name='user_guide'),
    path('settings/', views.settings_view, name='settings_view'),
    path('settings/policies/', views.policies_view, name='policies_view'),
    path('database/download/', views.download_database, name='download_database'),
    path('database/import/', views.import_database, name='import_database'),
    path('favicon.ico', views.favicon_view, name='favicon'),
]