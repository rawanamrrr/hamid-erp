from django.urls import path

from . import views

app_name = 'restaurant'

urlpatterns = [
    # Setup (sections/tables/drivers)
    path('setup/', views.restaurant_setup, name='setup'),

    # Waiter
    path('waiter/tables/', views.waiter_table_map, name='waiter_tables'),
    path('waiter/tables/<int:table_id>/reserve/', views.reserve_table, name='reserve_table'),
    path('waiter/tables/<int:table_id>/unreserve/', views.unreserve_table, name='unreserve_table'),
    path('waiter/tables/<int:table_id>/', views.waiter_order_screen, name='waiter_order'),
    path('waiter/tables/<int:table_id>/seats/', views.set_table_seats, name='set_table_seats'),
    path('waiter/tables/<int:table_id>/add-items/', views.waiter_open_or_append, name='waiter_add_items'),
    path('waiter/tables/<int:table_id>/ensure-order/', views.waiter_ensure_order, name='waiter_ensure_order'),
    path('waiter/order/new/', views.waiter_new_order_no_table, name='waiter_new_order_no_table'),
    path('waiter/order/<int:order_id>/', views.waiter_order_screen_no_table, name='waiter_order_no_table'),
    path('waiter/order/<int:order_id>/add-items/', views.waiter_add_items_no_table, name='waiter_add_items_no_table'),
    path('waiter/order/<int:order_id>/set-customer/', views.waiter_set_order_customer, name='waiter_set_order_customer'),
    path('waiter/order/<int:order_id>/set-note/', views.waiter_set_order_note, name='waiter_set_order_note'),
    path('order-item/<int:item_id>/void/', views.void_order_item, name='void_order_item'),
    path('order/<int:order_id>/void/', views.void_order, name='void_order'),
    path('order/<int:order_id>/close/', views.close_check, name='close_check'),
    path('order/<int:order_id>/mark-delivered/', views.mark_order_delivered, name='mark_order_delivered'),
    path('waiter/tables/<int:table_id>/free/', views.free_table, name='free_table'),
    path('order/<int:order_id>/split-pay/', views.split_order_pay, name='split_order_pay'),

    # Cashier dashboard (order-level workflow queue)
    path('cashier/', views.cashier_dashboard, name='cashier_dashboard'),
    path('cashier/order/<int:order_id>/status/', views.cashier_set_order_status, name='cashier_set_order_status'),

    # Kitchen Display
    # Realtime fallback fingerprint polled by base.html's window.LiveRefresh — see
    # restaurant.views.live_token for why the websocket layer alone isn't enough.
    path('live-token/', views.live_token, name='live_token'),
    path('kds/', views.kds_view, name='kds'),
    path('kds/item/<int:item_id>/status/', views.kds_set_status, name='kds_set_status'),
    path('kds/order/<int:order_id>/status/', views.kds_set_order_status, name='kds_set_order_status'),
    path('kds/order/<int:order_id>/ack-void/', views.kds_ack_void, name='kds_ack_void'),
    path('order/<int:order_id>/ticket/', views.kitchen_ticket_preview, name='kitchen_ticket_preview'),

    # Delivery
    path('delivery/', views.delivery_dashboard, name='delivery_dashboard'),
    path('delivery/feed/', views.delivery_orders_feed, name='delivery_orders_feed'),
    path('delivery/order/<int:order_id>/assign/', views.assign_driver, name='assign_driver'),
    path('delivery/order/<int:order_id>/return/', views.driver_return_settle, name='driver_return_settle'),
    path('delivery/order/<int:order_id>/check/', views.delivery_check_preview, name='delivery_check_preview'),
    path('delivery/driver/<int:driver_id>/settle/', views.driver_account_settle, name='driver_account_settle'),

    # Cash custody (وديعة)
    path('custody/', views.custody_list, name='custody_list'),
    path('custody/<int:custody_id>/settle/', views.custody_settle, name='custody_settle'),
    path('reports/reconciliation/', views.reconciliation_report, name='reconciliation_report'),

    # Reports
    path('reports/waiter-sales/', views.waiter_sales_report, name='waiter_sales_report'),
    path('reports/waiter-sales/<int:waiter_id>/', views.waiter_sales_detail, name='waiter_sales_detail'),
    path('reports/waiter-sales/<int:waiter_id>/orders/', views.waiter_orders_detail, name='waiter_orders_detail'),
    path('reports/driver-owed/', views.driver_owed_report, name='driver_owed_report'),
    path('reports/category-sales/', views.category_sales_report, name='category_sales_report'),
    path('reports/product-sales/', views.product_sales_report, name='product_sales_report'),
    path('reports/raw-material-usage/', views.raw_material_usage_report, name='raw_material_usage_report'),

    # Recipe (optional BOM per product, optionally per-size)
    path('menu/recipes/', views.menu_recipe_list, name='menu_recipe_list'),
    path('product/<int:product_id>/recipe/', views.recipe_edit, name='recipe_edit'),

    # Sub-recipes (وصفات فرعية) — reusable batch recipes pulled into product recipes
    path('menu/subrecipes/', views.subrecipe_list, name='subrecipe_list'),
    path('menu/subrecipes/add/', views.subrecipe_edit, name='subrecipe_create'),
    path('menu/subrecipes/<int:pk>/edit/', views.subrecipe_edit, name='subrecipe_edit'),

    # Modifier groups (مستوى السكر، إضافات...) — fixed-choice options for a menu item
    path('modifiers/', views.modifier_group_list, name='modifier_group_list'),
    path('modifiers/add/', views.modifier_group_create, name='modifier_group_create'),
    path('modifiers/<int:pk>/edit/', views.modifier_group_update, name='modifier_group_update'),
    path('modifiers/<int:pk>/delete/', views.modifier_group_delete, name='modifier_group_delete'),
    path('modifiers/option/<int:modifier_id>/recipe/', views.modifier_recipe_edit, name='modifier_recipe_edit'),
]
