import re

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models

APPROVAL_NOTE = re.compile(r'^PDF access approved; request=([0-9a-f-]{36})')


def backfill_approvers(apps, schema_editor):
    """Recover who approved earlier requests from their audit-log entries."""
    AuditLog = apps.get_model('contracts', 'AuditLog')
    DocumentAccessRequest = apps.get_model('contracts', 'DocumentAccessRequest')
    approvals = AuditLog.objects.filter(action='approved', note__startswith='PDF access approved; request=')
    for log in approvals.order_by('timestamp'):
        match = APPROVAL_NOTE.match(log.note)
        if match:
            DocumentAccessRequest.objects.filter(pk=match.group(1), approved_by__isnull=True).update(
                approved_by_id=log.user_id, approved_at=log.timestamp,
            )
    # A request that is still approved has not been reviewed since its
    # approval, so its last reviewer is the approver.
    for entry in DocumentAccessRequest.objects.filter(status='approved', approved_by__isnull=True,
                                                      reviewed_by__isnull=False):
        entry.approved_by_id = entry.reviewed_by_id
        entry.approved_at = entry.reviewed_at
        entry.save(update_fields=['approved_by', 'approved_at'])


class Migration(migrations.Migration):

    dependencies = [
        ('contracts', '0055_deleted_uploader_names'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name='documentaccessrequest',
            name='approved_by',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                                    related_name='approved_access_requests', to=settings.AUTH_USER_MODEL),
        ),
        migrations.AddField(
            model_name='documentaccessrequest',
            name='approved_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.RunPython(backfill_approvers, migrations.RunPython.noop),
    ]
