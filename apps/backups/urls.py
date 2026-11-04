from django.urls import path

from . import views

urlpatterns = [
    path('', views.backups_home, name='backups_home'),
    path('run/', views.backups_now, name='backups_now'),
    path('download/', views.backups_download, name='backups_download'),
    path('test/', views.backups_test, name='backups_test'),
]
