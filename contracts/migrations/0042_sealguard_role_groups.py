from django.db import migrations


ROLE_GROUPS = ('SealGuard Admin', 'SealGuard Staff', 'SealGuard User')


def create_and_assign_role_groups(apps, schema_editor):
    Group = apps.get_model('auth', 'Group')
    User = apps.get_model('auth', 'User')
    groups = {name: Group.objects.get_or_create(name=name)[0] for name in ROLE_GROUPS}
    for user in User.objects.all().iterator():
        role = 'SealGuard Admin' if user.is_superuser else ('SealGuard Staff' if user.is_staff else 'SealGuard User')
        user.groups.remove(*[group for name, group in groups.items() if name != role])
        user.groups.add(groups[role])


def remove_role_groups(apps, schema_editor):
    Group = apps.get_model('auth', 'Group')
    Group.objects.filter(name__in=ROLE_GROUPS).delete()


class Migration(migrations.Migration):
    dependencies = [('contracts', '0041_staffinvitation')]
    operations = [migrations.RunPython(create_and_assign_role_groups, remove_role_groups)]
