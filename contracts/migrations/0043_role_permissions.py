from django.db import migrations


CUSTOM_PERMISSIONS = {
    'view_sealguard_workspace': 'Can view the SealGuard workspace',
    'download_contract_file': 'Can download viewable contract files',
    'manage_contract_documents': 'Can manage authorized contract documents',
    'manage_contract_access': 'Can grant and revoke contract access',
    'export_dashboard': 'Can export dashboard reports',
    'run_integrity_scan': 'Can run and cancel integrity scans',
}


def configure_role_permissions(apps, schema_editor):
    ContentType = apps.get_model('contenttypes', 'ContentType')
    Permission = apps.get_model('auth', 'Permission')
    Group = apps.get_model('auth', 'Group')
    Contract = apps.get_model('contracts', 'Contract')
    Tutorial = apps.get_model('contracts', 'Tutorial')
    Folder = apps.get_model('contracts', 'Folder')

    contract_type = ContentType.objects.get_for_model(Contract, for_concrete_model=False)
    tutorial_type = ContentType.objects.get_for_model(Tutorial, for_concrete_model=False)
    folder_type = ContentType.objects.get_for_model(Folder, for_concrete_model=False)
    custom = {
        codename: Permission.objects.get_or_create(
            content_type=contract_type, codename=codename,
            defaults={'name': name},
        )[0]
        for codename, name in CUSTOM_PERMISSIONS.items()
    }

    def builtin(content_type, *codenames):
        return list(Permission.objects.filter(content_type=content_type, codename__in=codenames))

    admin_perms = list(custom.values()) + builtin(contract_type, 'add_contract', 'change_contract', 'delete_contract', 'view_contract')
    admin_perms += builtin(tutorial_type, 'add_tutorial', 'change_tutorial', 'delete_tutorial', 'view_tutorial')
    admin_perms += builtin(folder_type, 'add_folder', 'change_folder', 'delete_folder', 'view_folder')
    staff_perms = [custom[name] for name in CUSTOM_PERMISSIONS if name != 'run_integrity_scan']
    staff_perms += builtin(contract_type, 'add_contract', 'change_contract', 'delete_contract', 'view_contract')
    staff_perms += builtin(tutorial_type, 'add_tutorial', 'change_tutorial', 'delete_tutorial', 'view_tutorial')
    staff_perms += builtin(folder_type, 'add_folder', 'change_folder', 'delete_folder', 'view_folder')
    user_perms = [custom['view_sealguard_workspace']] + builtin(contract_type, 'view_contract') + builtin(tutorial_type, 'view_tutorial')

    for group_name, permissions in (
        ('SealGuard Admin', admin_perms),
        ('SealGuard Staff', staff_perms),
        ('SealGuard User', user_perms),
    ):
        group, _ = Group.objects.get_or_create(name=group_name)
        group.permissions.set(permissions)


class Migration(migrations.Migration):
    dependencies = [('contracts', '0042_sealguard_role_groups')]
    operations = [migrations.RunPython(configure_role_permissions, migrations.RunPython.noop)]
