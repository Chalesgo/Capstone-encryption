from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('contracts', '0054_remove_revision_integrity_scan'),
    ]

    operations = [
        migrations.AddField(
            model_name='contract',
            name='uploaded_by_name',
            field=models.CharField(blank=True, editable=False, max_length=150),
        ),
        migrations.AddField(
            model_name='contractversion',
            name='created_by_name',
            field=models.CharField(blank=True, editable=False, max_length=150),
        ),
    ]
