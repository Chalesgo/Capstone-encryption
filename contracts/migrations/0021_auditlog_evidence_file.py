from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('contracts', '0020_add_more_tutorials'),
    ]

    operations = [
        migrations.AddField(
            model_name='auditlog',
            name='evidence_file',
            field=models.FileField(blank=True, null=True, upload_to='verification_evidence/'),
        ),
    ]
