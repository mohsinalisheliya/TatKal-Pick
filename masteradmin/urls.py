from django.urls import path

from . import views

urlpatterns = [
    path('', views.home, name='master_home'),
    path('users/', views.users, name='master_users'),
    path('users/new/', views.user_form, name='master_user_new'),
    path('users/<int:pk>/', views.user_form, name='master_user_edit'),
    path('users/<int:pk>/password/', views.user_set_password, name='master_user_password'),
    path('users/<int:pk>/toggle/', views.user_toggle, name='master_user_toggle'),
    path('logs/', views.logs, name='master_logs'),
    path('sales/', views.sales, name='master_sales'),
    path('sales/<int:pk>/', views.sale_detail, name='master_sale'),
    path('stock/', views.stock, name='master_stock'),
    path('settings/', views.app_settings, name='app_settings'),
]
