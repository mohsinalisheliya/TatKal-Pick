# sku_manager/urls.py
from django.urls import path
from . import views

urlpatterns = [
    # Auth
    path('login/', views.user_login, name='login'),
    path('logout/', views.user_logout, name='logout'),

    # Part 1: Product Master & Listing
    path('products/', views.product_master, name='product_master'),
    path('products/edit/<int:pk>/', views.product_master, name='product_edit_details'),
    path('products/list/', views.product_list_view, name='product_list'),
    path('products/delete/<int:pk>/', views.product_delete_secure, name='product_delete_secure'),

    # Part 2: Inventory Dashboard (Matches view_inventory.html)
    path('', views.inventory_list, name='inventory_list'),
    path('inventory/', views.inventory_list, name='sku_inventory'),
    path('inventory/threshold/', views.update_universal_threshold, name='update_universal_threshold'),
    path('inventory/product/<int:pk>/', views.product_detail, name='product_detail'),

    # Stock Management (Add / Edit Batch - Matches manage_inventory.html)
    path('inventory/stock/add/<int:pk>/', views.stock_action, name='stock_action_add'),
    path('inventory/stock/edit/<int:pk>/<int:batch_id>/', views.stock_action, name='stock_action_edit'),
    path('inventory/stock/<int:pk>/', views.stock_action, name='stock_action'),

    # Product Batches Timeline (Matches product_stock_list.html)
    path('inventory/product/<int:pk>/batches/', views.product_batches_view, name='product_batches'),
    path('inventory/product/toggle-status/<int:pk>/', views.toggle_product_status, name='toggle_product_status'),
    path('batch/<int:batch_id>/toggle-sales/', views.toggle_batch_sales, name='toggle_batch_sales'),
    path('inventory/batch/delete/<int:batch_id>/', views.delete_batch, name='delete_batch'),
    path('inventory/api/last-stock/<int:pk>/', views.last_stock_api, name='last_stock_api'),

    # Storage & Platforms
    path('boxes/', views.box_manager, name='box_manager'),
    path('boxes/delete/<int:pk>/', views.box_delete, name='box_delete'),
    path('platforms/', views.platform_manager, name='platform_manager'),
    path('platforms/delete/<int:pk>/', views.platform_delete, name='platform_delete'),

    # Settings & Export
    path('settings/', views.app_settings_view, name='app_settings'),
    path('export-csv/', views.export_csv, name='sku_export_csv'),

    # Scanner & Dispatch
    path('scanner/', views.sku_scanner, name='sku_scanner'),
    path('print-label/<int:pk>/', views.sku_print_label, name='sku_print_label'),
    path('qr/<int:pk>/download/', views.sku_qr_download, name='sku_qr_download'),
    path('dispatch/<str:sku>/', views.scan_dispatch, name='scan_dispatch'),
    path('dispatch-logs/', views.dispatch_logs, name='dispatch_logs'),
    path('orders/scan-pdf/', views.pdf_order_scan, name='pdf_order_scan'),
    path('orders/batch/', views.pdf_order_batch, name='pdf_order_batch'),
    path('orders/<int:pk>/review/', views.pdf_order_review, name='pdf_order_review'),
]