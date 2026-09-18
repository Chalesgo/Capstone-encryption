from django.db import migrations, models


def migrate_legacy_statuses(apps, schema_editor):
    Contract = apps.get_model('contracts', 'Contract')
    # A previously Signed document is the closest equivalent to the new
    # Final copy state. Sent documents remain drafts until an Admin confirms
    # that the current PDF is final.
    Contract.objects.filter(status='approved').update(status='final')
    Contract.objects.filter(status='sent').update(status='pending')


class Migration(migrations.Migration):
    dependencies = [
        ('contracts', '0045_documentaccesslink_alter_auditlog_evidence_file_and_more'),
        ('contracts', '0045_emailmessagelog'),
    ]

    operations = [
        migrations.RunPython(migrate_legacy_statuses, migrations.RunPython.noop),
        migrations.AlterField(
            model_name='contract',
            name='status',
            field=models.CharField(
                choices=[('pending', 'Draft'), ('final', 'Final copy')],
                default='pending',
                max_length=20,
            ),
        ),
    ]
