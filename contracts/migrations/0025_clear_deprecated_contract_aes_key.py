from django.db import migrations


def clear_deprecated_aes_key(apps, schema_editor):
    Contract = apps.get_model('contracts', 'Contract')
    Contract.objects.exclude(aes_key='').update(aes_key='')


class Migration(migrations.Migration):
    dependencies = [
        ('contracts', '0024_alter_tutorial_icon'),
    ]

    operations = [
        migrations.RunPython(clear_deprecated_aes_key, migrations.RunPython.noop),
    ]
