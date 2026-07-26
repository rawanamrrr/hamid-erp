from django.urls import path
from . import views

urlpatterns = [
    # --- PUBLIC ROUTE ---
    path('p/<str:sku>/', views.public_product_detail, name='public_product_detail'),

    # --- Products Management ---
    path('products/', views.product_list, name='product_list'),
    path('products/low-stock/', views.low_stock_report, name='low_stock_report'),

    # --- Raw materials (ingredients — separate from sellable products) ---
    path('raw-materials/', views.raw_material_list, name='raw_material_list'),
    path('raw-materials/add/', views.raw_material_create, name='raw_material_create'),
    path('raw-materials/<int:pk>/edit/', views.raw_material_update, name='raw_material_update'),
    path('products/<int:pk>/movement/', views.item_movement_card, name='item_movement_card'),
    path('products/<int:pk>/price-break/add/', views.price_break_add, name='price_break_add'),
    path('price-break/<int:pk>/delete/', views.price_break_delete, name='price_break_delete'),
    path('reports/stock-valuation/', views.stock_valuation, name='stock_valuation'),
    path('reports/expiry/', views.expiry_report, name='expiry_report'),
    path('reports/ap-aging/', views.ap_aging_report, name='ap_aging'),
    path('reports/insights/', views.inventory_insights_view, name='inventory_insights'),
    path('suppliers/price-comparison/', views.supplier_price_comparison, name='supplier_price_comparison'),
    path('stocktake/', views.stocktake_list, name='stocktake_list'),
    path('stocktake/create/', views.stocktake_create, name='stocktake_create'),
    path('stocktake/<int:pk>/', views.stocktake_detail, name='stocktake_detail'),
    path('stocktake/<int:pk>/apply/', views.stocktake_apply, name='stocktake_apply'),

    # EXPORT & IMPORT
    path('products/export/excel/', views.export_products_excel, name='export_products_excel'),
    path('products/export/pdf/', views.export_products_pdf, name='export_products_pdf'),
    path('products/export/barcode/', views.export_products_barcode, name='export_products_barcode'),
    path('products/barcode-generator/', views.barcode_generator_view, name='barcode_generator'),
    path('products/print/qrs/', views.print_product_qrs, name='print_product_qrs'),
    path('products/import/excel/', views.import_products_excel, name='import_products_excel'),
    path('products/import/template/', views.download_import_template, name='download_import_template'),

    path('products/add/', views.product_create, name='product_create'),
    path('products/quick-edit/', views.quick_edit_products_page, name='quick_edit_products_page'),
    path('products/bulk-add/', views.bulk_product_add_view, name='bulk_product_add'),
    path('api/products/quick-create/', views.api_quick_create_product, name='api_quick_create_product'),
    path('api/products/bulk-save/', views.bulk_product_save_ajax, name='bulk_product_save_ajax'),
    path('api/products/bulk/quick-add/category/', views.bulk_quick_add_category_api, name='bulk_quick_add_category_api'),
    path('api/products/bulk/quick-add/kind/', views.bulk_quick_add_kind_api, name='bulk_quick_add_kind_api'),
    path('api/products/bulk/quick-add/size/', views.bulk_quick_add_size_api, name='bulk_quick_add_size_api'),
    path('api/products/bulk-edit/', views.bulk_edit_products, name='bulk_edit_products'),

    # BULK PRICE MANAGEMENT
    path('products/bulk-price/', views.bulk_price_manage, name='bulk_price_manage'),
    path('api/products/bulk-price/save/', views.bulk_price_save_api, name='bulk_price_save_api'),

    # BULK COST PRICE MANAGEMENT
    path('products/bulk-cost-price/', views.bulk_cost_price_manage, name='bulk_cost_price_manage'),
    path('api/products/bulk-cost-price/save/', views.bulk_cost_price_save_api, name='bulk_cost_price_save_api'),

    path('products/<int:pk>/', views.product_detail, name='product_detail'),
    path('products/<int:pk>/report/pdf/', views.product_report_pdf, name='product_report_pdf'),
    path('products/<int:pk>/report/excel/', views.product_report_excel, name='product_report_excel'),
    path('products/<int:pk>/edit/', views.product_update, name='product_update'),
    path('products/<int:pk>/variants/', views.product_variants, name='product_variants'),
    path('api/products/<int:pk>/variants/', views.product_variants_api, name='product_variants_api'),
    path('products/<int:pk>/delete/', views.product_delete, name='product_delete'),
    path('api/products/quick-update/', views.quick_update_product_ajax, name='quick_update_product_ajax'),

    # Product Images API
    path('api/products/<int:pk>/images/', views.product_images_list, name='product_images_list'),
    path('api/products/<int:pk>/images/upload/', views.product_image_upload, name='product_image_upload'),
    path('api/products/<int:pk>/images/<int:img_pk>/delete/', views.product_image_delete, name='product_image_delete'),

    # --- Stock Alerts ---
    path('inventory/alerts/', views.stock_alerts, name='stock_alerts'),
    path('api/inventory/alerts-count/', views.api_stock_alerts_count, name='api_stock_alerts_count'),

    # --- Warehouses Management ---
    path('warehouses/', views.warehouse_list, name='warehouse_list'),
    path('warehouses/add/', views.warehouse_create, name='warehouse_create'),
    path('warehouses/transfer/', views.bulk_transfer_view, name='stock_transfer'),
    path('warehouses/transfer/single/', views.stock_transfer, name='stock_transfer_single'),
    path('api/warehouses/transfer/save/', views.bulk_transfer_save_api, name='bulk_transfer_save_api'),
    path('warehouses/<int:pk>/', views.warehouse_detail, name='warehouse_detail'),
    path('warehouses/<int:pk>/edit/', views.warehouse_update, name='warehouse_update'),
    path('warehouses/<int:pk>/delete/', views.warehouse_delete, name='warehouse_delete'),
    path('warehouses/<int:pk>/export/excel/', views.warehouse_export_excel, name='warehouse_export_excel'),
    path('warehouses/<int:pk>/print/<str:print_format>/', views.warehouse_print, name='warehouse_print'),

    # --- Suppliers Management ---
    path('suppliers/', views.supplier_list, name='supplier_list'),
    path('suppliers/add/', views.supplier_create, name='supplier_create'),
    path('suppliers/<int:pk>/edit/', views.supplier_update, name='supplier_update'),
    path('suppliers/<int:pk>/delete/', views.supplier_delete, name='supplier_delete'),
    path('suppliers/<int:pk>/profile/', views.supplier_profile, name='supplier_profile'),
    path('suppliers/<int:pk>/statement/', views.supplier_statement, name='supplier_statement'),
    path('suppliers/purchase-invoice/add/', views.purchase_invoice_create, name='purchase_invoice_create'),
    path('suppliers/purchase-invoices/<int:pk>/edit/', views.purchase_invoice_create, name='purchase_invoice_edit'),
    path('suppliers/purchase-invoices/', views.purchase_invoice_list, name='purchase_invoice_list'),
    path('suppliers/purchase-invoices/<int:pk>/', views.purchase_invoice_detail, name='purchase_invoice_detail'),
    path('suppliers/purchase-invoices/<int:pk>/print-barcodes/', views.purchase_invoice_print_barcodes, name='purchase_invoice_print_barcodes'),
    path('suppliers/purchase-return/add/', views.purchase_return_create, name='purchase_return_create'),
    path('suppliers/purchase-return/add/<int:pk>/', views.purchase_return_create, name='purchase_return_from_invoice'),
    path('api/purchase-return/submit/', views.api_purchase_return_submit, name='api_purchase_return_submit'),
    path('api/products/search/', views.api_products_search, name='api_products_search'),
    path('api/purchase-invoice/submit/', views.api_purchase_invoice_submit, name='api_purchase_invoice_submit'),
    path('api/purchase-invoice/confirm/', views.api_purchase_invoice_confirm, name='api_purchase_invoice_confirm'),
    path('api/purchase-invoice/cancel/', views.api_purchase_invoice_cancel, name='api_purchase_invoice_cancel'),
    path('suppliers/<int:pk>/add-purchase/', views.supplier_add_purchase, name='supplier_add_purchase'),
    path('suppliers/<int:pk>/add-payment/', views.supplier_add_payment, name='supplier_add_payment'),
    path('suppliers/<int:pk>/account/', views.supplier_account, name='supplier_account'),
    path('suppliers/<int:pk>/statement/pdf/', views.supplier_statement_pdf, name='supplier_statement_pdf'),

    # --- Purchase Orders ---
    path('purchase-orders/', views.purchase_order_list, name='purchase_order_list'),
    path('purchase-orders/add/', views.purchase_order_create, name='purchase_order_create'),
    path('purchase-orders/<int:pk>/', views.purchase_order_detail, name='purchase_order_detail'),
    path('purchase-orders/<int:pk>/confirm/', views.purchase_order_confirm, name='purchase_order_confirm'),
    path('purchase-orders/<int:pk>/cancel/', views.purchase_order_cancel, name='purchase_order_cancel'),
    path('purchase-orders/<int:pk>/receive/', views.purchase_order_receive, name='purchase_order_receive'),
    path('purchase-orders/<int:pk>/print/', views.purchase_order_print, name='purchase_order_print'),

    # --- Categories Management ---
    path('categories/', views.category_list, name='category_list'),
    path('categories/add/', views.category_create, name='category_create'),
    path('categories/<int:pk>/edit/', views.category_update, name='category_update'),
    path('categories/<int:pk>/delete/', views.category_delete, name='category_delete'),

    # --- Kinds Management ---
    path('kinds/', views.kind_list, name='kind_list'),
    path('kinds/add/', views.kind_create, name='kind_create'),
    path('kinds/<int:pk>/edit/', views.kind_update, name='kind_update'),
    path('kinds/<int:pk>/delete/', views.kind_delete, name='kind_delete'),
    path('api/kinds/', views.api_kinds_by_category, name='api_kinds_by_category'),

    # --- Sizes Management ---
    path('sizes/', views.size_list, name='size_list'),
    path('sizes/add/', views.size_create, name='size_create'),
    path('sizes/<int:pk>/delete/', views.size_delete, name='size_delete'),
    path('api/sizes/reorder/', views.size_reorder, name='size_reorder'),

    # --- Units of Measure ---
    path('units/', views.unit_list, name='unit_list'),
    path('units/add/', views.unit_create, name='unit_create'),
    path('units/<int:pk>/delete/', views.unit_delete, name='unit_delete'),
    path('api/units/quick-create/', views.quick_create_unit_ajax, name='quick_create_unit_ajax'),

    # --- Stock Transactions ---
    path('transactions/', views.transaction_list, name='transaction_list'),
    path('transactions/add/', views.transaction_create, name='transaction_create'),
    path('transactions/bulk/add/', views.bulk_transaction_create, name='bulk_transaction_create'),
    path('transactions/pos-bulk-transfer/', views.pos_bulk_transfer_api, name='pos_bulk_transfer_api'),
    path('transactions/pos-stock-movement/', views.pos_stock_movement_api, name='pos_stock_movement_api'),
    path('api/transactions/manufacturing/', views.create_manufacturing_transaction, name='manufacturing_transaction'),

    # --- Sales Reports ---
    path('sales/list/', views.sold_items_list, name='sold_items_list'),
    path('sales/report/', views.sold_items_report, name='sold_items_report'),

    # --- Audit System ---
    path('audit/', views.bulk_audit_view, name='bulk_audit'),
    path('audit/get-stocks/<int:pk>/', views.get_warehouse_stocks_ajax, name='get_warehouse_stocks_ajax'),
    path('audit/update/', views.update_product_stock_ajax, name='audit_update_ajax'),

    # --- Costing Tool ---
    path('costing/', views.costing_view, name='costing_view'),
    path('api/costing/unlock/', views.api_costing_unlock, name='api_costing_unlock'),
    path('costing/history/', views.costing_list_view, name='costing_list'),
    path('costing/<int:pk>/print/', views.costing_print_view, name='costing_print'),
    path('api/products/update-cost/', views.api_update_product_cost, name='api_update_product_cost'),
    path('api/products/search-costing/', views.api_search_products, name='api_search_products'),
    path('api/costing/recipe/', views.api_recipe_cost, name='api_recipe_cost'),
    path('api/costing/create-recipe-product/', views.api_create_recipe_product, name='api_create_recipe_product'),
    path('api/products/save-costing/', views.api_save_costing, name='api_save_costing'),
    path('api/update-price/', views.update_price_api, name='update_price_api'),
    path('api/products/fetch-image/', views.fetch_product_image_api, name='fetch_product_image_api'),
]
