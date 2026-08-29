from django.db import migrations, models


DELETE_NOTE_PREFIXES = (
    'Moved to trash: ',
    'Permanently deleted from trash: ',
    'Permanently deleted: ',
    'Trash emptied: ',
    'Auto-purged after 15 days: ',
)


def backfill_document_titles(apps, schema_editor):
    AuditLog = apps.get_model('contracts', 'AuditLog')
    logs = AuditLog.objects.select_related('contract').all()
    for log in logs.iterator():
        title = log.contract.title if log.contract_id else ''
        if not title and log.action == 'deleted':
            for prefix in DELETE_NOTE_PREFIXES:
                if log.note.startswith(prefix):
                    title = log.note[len(prefix):].strip()
                    break
        if title:
            log.document_title = title[:255]
            log.save(update_fields=['document_title'])


class Migration(migrations.Migration):
    dependencies = [
        ('contracts', '0014_contract_is_trashed_contract_trashed_at'),
    ]

    operations = [
        migrations.AddField(
            model_name='auditlog',
            name='document_title',
            field=models.CharField(blank=True, max_length=255),
        ),
        migrations.RunPython(backfill_document_titles, migrations.RunPython.noop),
    ]
