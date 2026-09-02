from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('contracts', '0021_auditlog_evidence_file'),
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
                ],
                max_length=30,
            ),
        ),
        migrations.AddField(
            model_name='auditlog',
            name='document_size',
            field=models.PositiveBigIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='auditlog',
            name='integrity_check',
            field=models.CharField(blank=True, max_length=30),
        ),
        migrations.AddField(
            model_name='auditlog',
            name='verification_result',
            field=models.CharField(blank=True, max_length=50),
        ),
        migrations.AddField(
            model_name='auditlog',
            name='verification_source',
            field=models.CharField(blank=True, max_length=100),
        ),
    ]
