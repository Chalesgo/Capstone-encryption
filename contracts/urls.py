from django.urls import path
from . import views

urlpatterns = [
    path('verify/', views.public_verify, name='public_verify'),
    path('contracts/', views.contract_list, name='contract_list'),
    path('upload/', views.upload_contract, name='upload_contract'),
    path('encrypt/<int:contract_id>/', views.encrypt_contract, name='encrypt_contract'),
    path('delete/<int:contract_id>/', views.delete_contract, name='delete_contract'),
    path('register/', views.register, name='register'),
    path('verify/physical/', views.verify_physical, name='verify_physical'),
]