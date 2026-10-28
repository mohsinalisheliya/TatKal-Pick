from django.urls import path

from . import views

urlpatterns = [
    path('', views.home, name='master_home'),
    path('users/', views.users, name='master_users'),
    path('users/new/', views.user_form, name='master_user_new'),
    path('users/<int:pk>/', views.user_form, name='master_user_edit'),
    path('users/<int:pk>/reset-password/', views.user_reset_password, name='master_user_reset'),
    path('users/<int:pk>/toggle/', views.user_toggle, name='master_user_toggle'),
    path('logs/', views.logs, name='master_logs'),
]
