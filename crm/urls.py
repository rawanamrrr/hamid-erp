from django.urls import path
from . import views

urlpatterns = [
    path('', views.CustomerListView.as_view(), name='customer_list'),
    path('customers/', views.CustomerListView.as_view(), name='customer_list_alias'),
    path('customers/bulk-add/', views.CustomerBulkAddView.as_view(), name='customer_bulk_add'),
    path('customers/import/', views.CustomerImportView.as_view(), name='customer_import'),
    path('customers/template/', views.CustomerTemplateDownloadView.as_view(), name='customer_template_download'),
    path('print-all/', views.customer_list_print, name='customer_list_print'), # رابط جديد
    path('ar-aging/', views.ar_aging_report, name='ar_aging'),
    path('add/', views.customer_create, name='customer_create'),
    path('<int:pk>/', views.customer_detail, name='customer_detail'),
    path('<int:pk>/edit/', views.customer_update, name='customer_update'),
    path('<int:pk>/delete/', views.customer_delete, name='customer_delete'),
    path('<int:pk>/pay/', views.customer_pay_debt, name='customer_pay_debt'),
    path('payment-receipt/<int:payment_id>/', views.payment_receipt, name='payment_receipt'),
    path('<int:pk>/report/', views.customer_report, name='customer_report'),
    path('<int:pk>/statement/', views.customer_statement, name='customer_statement'),

    # POS delivery-screen quick client lookup/create/edit (phone-first)
    path('api/customer-lookup/', views.customer_lookup_by_phone, name='customer_lookup_by_phone'),
    path('api/customer-quick-create/', views.customer_quick_create, name='customer_quick_create'),
    path('api/customer/<int:pk>/quick-update/', views.customer_quick_update, name='customer_quick_update'),
]