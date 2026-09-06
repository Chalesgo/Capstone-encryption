from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('contracts', '0029_auditlog_version_number'),
    ]

    operations = [
        migrations.AddField(
            model_name='auditlog',
            name='verification_debug_log',
            field=models.TextField(blank=True),
        ),
    ]
