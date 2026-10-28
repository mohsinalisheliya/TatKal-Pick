from django.urls import path

from . import views

urlpatterns = [
    path('login/', views.RoleLoginView.as_view(), name='login'),
    path('logout/', views.logout_view, name='logout'),
    path('account/password/', views.password_change, name='password_change'),
    path('home/', views.role_home, name='role_home'),
]
