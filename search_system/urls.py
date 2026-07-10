from django.urls import path
from . import views

urlpatterns = [
    path('results/', views.search_view, name='global_search'),
]