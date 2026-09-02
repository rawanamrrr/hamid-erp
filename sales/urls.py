from django.urls import path
from . import views

urlpatterns = [
    # Main POS & Dashboard (Desktop)
    path('pos/', views.pos_view, name='pos_view'),
    path('pos/last-price/', views.last_customer_price, name='last_customer_price'),
    path('pos/saved-orders/save/', views.save_saved_order, name='save_saved_order'),
    path('pos/saved-orders/list/', views.list_saved_orders, name='list_saved_orders'),
    path('pos/saved-orders/<int:pk>/', views.get_saved_order, name='get_saved_order'),
    path('pos/saved-orders/<int:pk>/delete/', views.delete_saved_order, name='delete_saved_order'),
    # Phase 6.3 reservations
    path('pos/reservations/create/', views.create_reservation, name='create_reservation'),
    path('pos/reservations/<int:pk>/json/', views.get_reservation, name='get_reservation'),
    path('pos/reservations/<int:pk>/cancel/', views.cancel_reservation, name='cancel_reservation'),
    path('reservations/', views.reservation_list, name='reservation_list'),
    path('reservations/<int:pk>/', views.reservation_detail, name='reservation_detail'),
    path('pos/drafts/save/', views.save_draft_ajax, name='save_draft_ajax'),
    path('pos/drafts/list/', views.list_drafts_ajax, name='list_drafts_ajax'),
    path('pos/drafts/<int:pk>/', views.get_draft_ajax, name='get_draft_ajax'),
    path('pos/drafts/<int:pk>/delete/', views.delete_draft_ajax, name='delete_draft_ajax'),
    path('pos/drafts/<int:pk>/print/', views.draft_invoice_view, name='draft_invoice_view'),

    # Mobile POS
    path('pos/mobile/', views.pos_mobile, name='pos_mobile'),
    
    # Orders Management
    path('orders/', views.order_list, name='order_list'),
    path('orders/voided/', views.voided_orders_register, name='voided_register'),
    path('orders/report/', views.order_report, name='order_report'),
    
    # Invoice & Details
    path('invoice/<int:pk>/', views.order_invoice, name='order_invoice'),
    path('orders/<int:pk>/', views.order_invoice, name='order_detail'),
    path('orders/<int:pk>/invoice/', views.order_invoice, name='order_invoice_specific'),
    path('invoice/pdf/<int:pk>/', views.download_invoice_pdf, name='download_invoice_pdf'),
    
    # Direct Print (AJAX) - NEW
    path('invoice/print-direct/<int:pk>/', views.print_receipt_backend, name='print_receipt_backend'),

    
    # Factory/Tailoring
    path('factory/', views.factory_list, name='factory_list'),
    path('factory/create/', views.factory_order_create, name='factory_order_create'),
    path('factory/<int:pk>/edit/', views.factory_order_edit, name='factory_order_edit'),
    path('factory/<int:pk>/delete/', views.factory_order_delete, name='factory_order_delete'),
    path('factory/invoice/<int:pk>/', views.factory_invoice, name='factory_invoice'),
    
    # Refunds & Returns (UPDATED)
    path('refund/', views.refund_view, name='refund_view'),
    path('returns/', views.returns_register, name='returns_register'),
    path('oversold/', views.oversold_register, name='oversold_register'),
    path('orders/<int:pk>/einvoice.json', views.order_einvoice_json, name='order_einvoice_json'),
    path('quotations/', views.quotation_list, name='quotation_list'),
    path('quotations/create/', views.quotation_create, name='quotation_create'),
    path('quotations/<int:pk>/', views.quotation_detail, name='quotation_detail'),
    path('quotations/<int:pk>/add-item/', views.quotation_add_item, name='quotation_add_item'),
    path('quotation-item/<int:pk>/delete/', views.quotation_delete_item, name='quotation_delete_item'),
    path('quotations/<int:pk>/status/', views.quotation_set_status, name='quotation_set_status'),
    path('api/get-order-details/', views.get_order_details_ajax, name='get_order_details_ajax'), # New API
    
    # Expenses
    path('expenses/', views.expense_list, name='expense_list'),
    path('expenses/<int:pk>/edit/', views.expense_edit, name='expense_edit'),
    path('expenses/invoice/<int:pk>/', views.expense_invoice, name='expense_invoice'),
    path('expenses/report/', views.expense_report, name='expense_report'),
    
    # Financial Statement
    path('financial/', views.financial_statement, name='financial_statement'),

    # AJAX APIs
    path('api/create-customer/', views.create_customer_ajax, name='create_customer_ajax'),
    path('api/submit-order/', views.submit_order_ajax, name='submit_order_ajax'),
    path('api/check-recipe-stock/', views.api_check_recipe_stock, name='api_check_recipe_stock'),
    path('api/update-tailoring-status/', views.update_tailoring_status_ajax, name='update_tailoring_status_ajax'),
    path('api/delete-order/', views.delete_order_ajax, name='delete_order_ajax'),
    path('api/edit-order/', views.edit_order_ajax, name='edit_order_ajax'),
]   