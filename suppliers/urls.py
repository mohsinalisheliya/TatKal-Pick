from django.urls import path

from . import views

urlpatterns = [
    path('', views.supplier_list, name='supplier_list'),
    path('new/', views.supplier_form, name='supplier_new'),
    path('<int:pk>/', views.supplier_detail, name='supplier_detail'),
    path('<int:pk>/edit/', views.supplier_form, name='supplier_edit'),
    path('<int:pk>/toggle/', views.supplier_toggle, name='supplier_toggle'),
    path('<int:pk>/delete/', views.supplier_delete, name='supplier_delete'),
    path('api/search/', views.supplier_search_api, name='supplier_search_api'),
    path('api/quick-add/', views.supplier_quick_add_api, name='supplier_quick_add_api'),
]
