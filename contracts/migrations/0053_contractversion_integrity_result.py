from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('contracts', '0052_sgpdf_decryption_requests'),
    ]

    operations = [
        migrations.AddField(
            model_name='contractversion',
            name='integrity_status',
            field=models.CharField(blank=True, choices=[('valid', 'Passed'), ('invalid', 'Failed')], max_length=20),
        ),
        migrations.AddField(
            model_name='contractversion',
            name='integrity_checked_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
