from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('contracts', '0030_auditlog_verification_debug_log'),
    ]

    operations = [
        migrations.AddField(
            model_name='contract',
            name='vector_fingerprint',
            field=models.CharField(blank=True, max_length=64),
        ),
        migrations.AddField(
            model_name='contractversion',
            name='vector_fingerprint',
            field=models.CharField(blank=True, max_length=64),
        ),
    ]
