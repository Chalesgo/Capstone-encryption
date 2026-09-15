from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ('contracts', '0034_contractversion_integrity_status'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='contractversion',
            name='integrity_status',
        ),
        migrations.RemoveField(
            model_name='contractversion',
            name='integrity_checked_at',
        ),
    ]
