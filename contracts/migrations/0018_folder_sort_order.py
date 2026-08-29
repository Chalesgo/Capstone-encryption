from django.db import migrations, models


def set_initial_folder_order(apps, schema_editor):
    Folder = apps.get_model('contracts', 'Folder')
    owner_ids = Folder.objects.values_list('owner_id', flat=True).distinct()
    for owner_id in owner_ids.iterator():
        folders = Folder.objects.filter(owner_id=owner_id).order_by('name', 'id')
        for position, folder in enumerate(folders.iterator()):
            folder.sort_order = position
            folder.save(update_fields=['sort_order'])


class Migration(migrations.Migration):
    dependencies = [
        ('contracts', '0017_contract_fingerprint_index_and_repair'),
    ]

    operations = [
        migrations.AddField(
            model_name='folder',
            name='sort_order',
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.RunPython(set_initial_folder_order, migrations.RunPython.noop),
        migrations.AlterModelOptions(
            name='folder',
            options={'ordering': ['sort_order', 'name']},
        ),
    ]
