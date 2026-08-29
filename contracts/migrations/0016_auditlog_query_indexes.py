from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('contracts', '0015_auditlog_document_title'),
    ]

    operations = [
        migrations.AddIndex(
            model_name='auditlog',
            index=models.Index(fields=['timestamp', 'id'], name='audit_time_id_idx'),
        ),
        migrations.AddIndex(
            model_name='auditlog',
            index=models.Index(fields=['action', 'timestamp', 'id'], name='audit_action_time_id_idx'),
        ),
    ]
