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
    path('rename/<int:pk>/', views.rename_contract, name='rename_contract'),
    path('status/<int:pk>/',  views.update_status,   name='update_status'),
    path('dashboard/', views.dashboard, name='dashboard'),
    path('tag/<int:pk>/', views.tag_contract, name='tag_contract'),
    path('folder/create/', views.create_folder, name='create_folder'),
    path('folder/reorder/', views.reorder_folders, name='reorder_folders'),
    path('folder/rename/<int:pk>/', views.rename_folder, name='rename_folder'),
    path('folder/assign/<int:contract_id>/', views.assign_folder, name='assign_folder'),
    path('folder/delete/<int:pk>/', views.delete_folder, name='delete_folder'),
    path('contract/<int:contract_id>/upload-signed/', views.upload_signed_scan, name='upload_signed_scan'),
    path('contract/<int:contract_id>/add-revision/', views.add_revision, name='add_revision'),
    path('contract/<int:pk>/publish/', views.publish_contract, name='publish_contract'),
    path('contract/<int:contract_id>/version-history/', views.contract_version_history, name='contract_version_history'),
    path('trash/', views.trash_list, name='trash_list'),
    path('trash/restore/<int:contract_id>/', views.restore_contract, name='restore_contract'),
    path('trash/delete-forever/<int:contract_id>/', views.permanently_delete_contract, name='permanently_delete_contract'),
    path('trash/empty/', views.empty_trash, name='empty_trash'),
]

