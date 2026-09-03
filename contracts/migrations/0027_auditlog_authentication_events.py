from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('contracts', '0026_physicalverificationmanifest'),
    ]

    operations = [
        migrations.AlterField(
            model_name='auditlog',
            name='action',
            field=models.CharField(
                choices=[
                    ('viewed', 'Viewed Document'),
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
                ],
                max_length=30,
            ),
        ),
    ]
