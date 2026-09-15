from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('contracts', '0035_remove_contractversion_integrity_cache'),
    ]

    operations = [
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
                    ('integrity_scan', 'Integrity Scan'),
                    ('verification', 'Verification Attempt'),
                    ('failed_login', 'Failed Login Attempt'),
                    ('login', 'Successful Login'),
                    ('logout', 'Logout'),
                    ('locked_out', 'Account Lockout'),
                ],
                max_length=30,
            ),
        ),
    ]
