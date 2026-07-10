from django.urls import path
from . import views, api_views

urlpatterns = [
    path('', views.dashboard, name='dashboard'),
    path('margin-report/', views.margin_report, name='margin_report'),
    path('sales-profit-report/', views.sales_profit_report, name='sales_profit_report'),
    
    # Intelligence APIs
    path('api/product-health/', api_views.product_health_api, name='product_health_api'),
    path('api/suggestions/', api_views.smart_suggestions_api, name='smart_suggestions_api'),
    path('api/price-suggestions/', api_views.price_suggestions_api, name='price_suggestions_api'),
    path('api/crm-suggestions/', api_views.crm_suggestions_api, name='crm_suggestions_api'),
]