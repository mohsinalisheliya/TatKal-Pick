from django.urls import path

from . import views

urlpatterns = [
    path('', views.import_center, name='import_center'),
    path('new/<str:kind>/', views.import_start, name='import_start'),
    path('sample/<str:kind>/', views.import_sample, name='import_sample'),
    path('job/<int:pk>/map/', views.import_map, name='import_map'),
    path('job/<int:pk>/preview/', views.import_preview, name='import_preview'),
    path('job/<int:pk>/commit/', views.import_commit, name='import_commit'),
    path('job/<int:pk>/result/', views.import_result, name='import_result'),
    path('job/<int:pk>/errors.csv', views.import_errors_csv, name='import_errors_csv'),
    path('mapping/<int:pk>/delete/', views.import_mapping_delete, name='import_mapping_delete'),
]
