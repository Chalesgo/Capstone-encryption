from django.db import migrations


RESIDENT_GROUP = 'SealGuard Resident'


def create_group(apps, schema_editor):
    apps.get_model('auth', 'Group').objects.get_or_create(name=RESIDENT_GROUP)


def delete_group(apps, schema_editor):
    apps.get_model('auth', 'Group').objects.filter(name=RESIDENT_GROUP).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('auth', '0012_alter_user_first_name_max_length'),
        ('contracts', '0058_unpublish_draft_documents'),
    ]

    operations = [
        migrations.RunPython(create_group, delete_group),
    ]
