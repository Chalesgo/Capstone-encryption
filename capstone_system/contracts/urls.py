from django.urls import path
from . import views

urlpatterns = [
    path('', views.contract_list, name='contract_list'),
    path('upload/', views.upload_contract, name='upload_contract'),
]