from django.db import migrations, models


def remove_scan_permission(apps, schema_editor):
    ContentType = apps.get_model('contenttypes', 'ContentType')
    Permission = apps.get_model('auth', 'Permission')
    contract_type = ContentType.objects.filter(app_label='contracts', model='contract').first()
    if contract_type:
        Permission.objects.filter(
            content_type=contract_type,
            codename='run_integrity_scan',
        ).delete()
    Tutorial = apps.get_model('contracts', 'Tutorial')
    Tutorial.objects.filter(title='How to run and follow an integrity scan').delete()


class Migration(migrations.Migration):
    dependencies = [
        ('contracts', '0053_contractversion_integrity_result'),
        ('auth', '0012_alter_user_first_name_max_length'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='contractversion',
            name='integrity_status',
        ),
        migrations.RemoveField(
            model_name='contractversion',
            name='integrity_checked_at',
        ),
        migrations.AlterField(
            model_name='auditlog',
            name='action',
            field=models.CharField(
                choices=[
                    ('viewed', 'Viewed Document'),
                    ('downloaded', 'Downloaded Document'),
                    ('added', 'Added Document'),
                    ('encrypted', 'Encrypted Document'),
                    ('edited', 'Edited Document'),
                    ('approved', 'Approved Document'),
                    ('rejected', 'Rejected Document'),
                    ('deleted', 'Deleted Document'),
                    ('reported_tampering', 'Reported Tampering'),
                    ('verification', 'Verification Attempt'),
                    ('failed_login', 'Failed Login Attempt'),
                    ('login', 'Successful Login'),
                    ('logout', 'Logout'),
                    ('locked_out', 'Account Lockout'),
                ],
                max_length=30,
            ),
        ),
        migrations.RunPython(remove_scan_permission, migrations.RunPython.noop),
    ]
