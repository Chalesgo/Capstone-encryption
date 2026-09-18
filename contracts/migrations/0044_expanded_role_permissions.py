from django.db import migrations


PERMISSIONS = {
    'view_sealguard_workspace': 'Can view the SealGuard workspace',
    'view_contract_pdf': 'Can view contract PDFs',
    'view_contract_history': 'Can view contract revision history',
    'view_activity_logs': 'Can view authorized activity logs',
    'download_contract_file': 'Can download viewable contract files',
    'upload_contract': 'Can upload new contracts',
    'add_contract_revision': 'Can add contract revisions',
    'encrypt_contract': 'Can encrypt contract documents',
    'manage_contract_documents': 'Can manage authorized contract documents',
    'manage_contract_access': 'Can grant and revoke contract access',
    'publish_contract': 'Can publish and unpublish contracts',
    'manage_contract_folders': 'Can create and manage contract folders',
    'manage_trash': 'Can move, restore, and permanently delete contracts',
    'export_dashboard': 'Can export dashboard reports',
    'view_verification_records': 'Can view verification records and evidence',
    'perform_public_verification': 'Can perform public document verification',
    'perform_physical_verification': 'Can perform physical QR verification',
    'review_physical_verification': 'Can review physical verification results',
    'manage_tutorials': 'Can create, edit, and delete help tutorials',
    'run_integrity_scan': 'Can run and cancel integrity scans',
    'invite_staff': 'Can create staff invitations',
    'approve_password_resets': 'Can approve password reset requests',
}


def configure_permissions(apps, schema_editor):
    ContentType = apps.get_model('contenttypes', 'ContentType')
    Permission = apps.get_model('auth', 'Permission')
    Group = apps.get_model('auth', 'Group')
    Contract = apps.get_model('contracts', 'Contract')
    contract_type = ContentType.objects.get_for_model(Contract, for_concrete_model=False)
    permission_map = {
        codename: Permission.objects.get_or_create(
            content_type=contract_type, codename=codename,
            defaults={'name': label},
        )[0]
        for codename, label in PERMISSIONS.items()
    }
    builtin_contract = list(Permission.objects.filter(
        content_type=contract_type,
        codename__in=('add_contract', 'change_contract', 'delete_contract', 'view_contract'),
    ))
    builtin_tutorial = list(Permission.objects.filter(
        content_type__app_label='contracts', content_type__model='tutorial',
        codename__in=('add_tutorial', 'change_tutorial', 'delete_tutorial', 'view_tutorial'),
    ))
    builtin_folder = list(Permission.objects.filter(
        content_type__app_label='contracts', content_type__model='folder',
        codename__in=('add_folder', 'change_folder', 'delete_folder', 'view_folder'),
    ))
    admin_names = set(PERMISSIONS)
    staff_names = admin_names - {'run_integrity_scan', 'invite_staff', 'approve_password_resets'}
    user_names = {
        'view_sealguard_workspace', 'view_contract_pdf', 'view_contract_history',
        'view_activity_logs', 'view_verification_records',
        'perform_public_verification', 'perform_physical_verification',
    }
    group_permissions = {
        'SealGuard Admin': [permission_map[name] for name in admin_names] + builtin_contract + builtin_tutorial + builtin_folder,
        'SealGuard Staff': [permission_map[name] for name in staff_names] + builtin_contract + builtin_tutorial + builtin_folder,
        'SealGuard User': [permission_map[name] for name in user_names] + [
            permission for permission in builtin_contract + builtin_tutorial
            if permission.codename in ('view_contract', 'view_tutorial')
        ],
    }
    for group_name, permissions in group_permissions.items():
        group, _ = Group.objects.get_or_create(name=group_name)
        group.permissions.set(permissions)


class Migration(migrations.Migration):
    dependencies = [('contracts', '0043_role_permissions')]
    operations = [migrations.RunPython(configure_permissions, migrations.RunPython.noop)]
