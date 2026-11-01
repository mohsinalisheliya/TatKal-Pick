from django.urls import path

from . import views

urlpatterns = [
    path('', views.price_list, name='price_list'),
    path('save/', views.price_save, name='price_save'),
]
