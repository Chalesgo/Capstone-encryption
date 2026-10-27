from django.db import migrations


# Access requests are now decided by administrators only, need a written
# reason, and use an adjustable duration instead of three fixed options.
REPLACEMENTS = [
    (
        '<li>A staff member or administrator opens <strong>PDF access requests</strong>, reviews the request, and chooses <strong>Approve viewing</strong> or <strong>Reject</strong>.</li>',
        '<li>An administrator opens <strong>PDF access requests</strong>, reviews the request, writes the reason for the decision, and chooses <strong>Approve viewing</strong> or <strong>Reject</strong>. Staff can view the queue but cannot decide requests.</li>',
    ),
    (
        '<li>When approving, select 1 hour, 24 hours, or 3 days. The requester returns to the request page in the same browser to view the approved version.</li>',
        '<li>When approving, enter how long access should last, from 15 minutes to 3 days, or pick a common duration. The page shows when access will end. The requester returns to the request page in the same browser to view the approved version and the reason given.</li>',
    ),
    (
        'Use the Approved tab and <strong>Revoke access</strong> to end permission early.</p>',
        'Use the Approved tab and <strong>Revoke access</strong>, with a reason, to end permission early.</p>',
    ),
    (
        'Staff and administrators can review document access requests.</li>',
        'Staff can view document access requests; only administrators can approve, reject, or revoke them.</li>',
    ),
]


def update_tutorials(apps, schema_editor):
    Tutorial = apps.get_model('contracts', 'Tutorial')
    for tutorial in Tutorial.objects.all():
        content = tutorial.content
        for old, new in REPLACEMENTS:
            content = content.replace(old, new)
        if content != tutorial.content:
            tutorial.content = content
            tutorial.save(update_fields=['content'])


def restore_tutorials(apps, schema_editor):
    Tutorial = apps.get_model('contracts', 'Tutorial')
    for tutorial in Tutorial.objects.all():
        content = tutorial.content
        for old, new in REPLACEMENTS:
            content = content.replace(new, old)
        if content != tutorial.content:
            tutorial.content = content
            tutorial.save(update_fields=['content'])


class Migration(migrations.Migration):

    dependencies = [
        ('contracts', '0056_access_request_approved_by'),
    ]

    operations = [
        migrations.RunPython(update_tutorials, restore_tutorials),
    ]
