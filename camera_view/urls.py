from django.urls import path
from . import views

urlpatterns = [
    # Dashboard
    path('', views.camera_dashboard, name='camera_dashboard'),
    
    # Live Feed URL
    # Changed <str:camera_name> to <int:camera_id> to link to the database
    path('feed/<int:camera_id>/<int:stream_id>/', views.live_feed, name='live_feed'),
]