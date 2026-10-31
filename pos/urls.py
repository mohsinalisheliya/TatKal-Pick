from django.urls import path

from . import views

urlpatterns = [
    path('', views.pos_screen, name='pos'),
    path('api/products/', views.pos_products_api, name='pos_products_api'),
    path('checkout/', views.pos_checkout, name='pos_checkout'),
    path('sales/', views.pos_sales, name='pos_sales'),
    path('sales/<int:pk>/void/', views.pos_void, name='pos_void'),
    path('receipt/<int:pk>/', views.pos_receipt, name='pos_receipt'),
]
