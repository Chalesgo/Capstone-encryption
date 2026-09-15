from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('contracts', '0033_update_physical_verification_tutorial'),
    ]

    operations = [
        migrations.AddField(
            model_name='contractversion',
            name='integrity_status',
            field=models.CharField(blank=True, max_length=20),
        ),
        migrations.AddField(
            model_name='contractversion',
            name='integrity_checked_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
