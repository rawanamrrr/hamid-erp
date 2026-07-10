from django.urls import path
from . import views

urlpatterns = [
    path('', views.shipping_dashboard, name='shipping_dashboard'),
    path('create/<int:order_id>/', views.create_shipment_for_order, name='create_shipment'),
    path('companies/', views.company_list, name='company_list'),
    path('companies/delete/<int:pk>/', views.company_delete, name='company_delete'),
    path('print/<int:shipment_id>/', views.print_shipping_label, name='print_shipping_label'),
    path('update-address/<int:customer_id>/', views.update_customer_address, name='update_customer_address'),
    
    # API
    path('api/update-status/', views.update_shipment_status, name='api_update_shipping_status'),
    path('api/update-payment/', views.update_payment_info, name='api_update_payment_info'),
]