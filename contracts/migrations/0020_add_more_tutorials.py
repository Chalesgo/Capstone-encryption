from django.db import migrations


def add_more_tutorials(apps, schema_editor):
    Tutorial = apps.get_model('contracts', 'Tutorial')
    tutorials = [
        {
            'title': 'How to publish a contract for public viewing',
            'summary': 'Make an authenticated contract visible in the public verified-contracts list.',
            'icon': 'eye',
            'sort_order': 3,
            'content': (
                '<p><strong>1. Open Contracts.</strong> Find the authenticated document that you want to publish.</p>'
                '<p><strong>2. Select the eye icon.</strong> A confirmation message explains that the document '
                'will become visible without requiring a staff login.</p>'
                '<p><strong>3. Review the document.</strong> Make sure it does not contain information that '
                'should remain private.</p>'
                '<p><strong>4. Confirm publication.</strong> The contract will appear in the public document '
                'list on the Verify page.</p>'
                '<p><strong>5. Remove it when necessary.</strong> Select the active eye icon again to stop '
                'showing the contract publicly.</p>'
            ),
        },
        {
            'title': 'How to add a new contract revision',
            'summary': 'Create a new protected version while retaining the contract’s fingerprint history.',
            'icon': 'history',
            'sort_order': 4,
            'content': (
                '<p><strong>1. Open Contracts.</strong> Locate the document that received an authorized revision.</p>'
                '<p><strong>2. Open its action menu.</strong> Select <strong>Add Revision</strong>.</p>'
                '<p><strong>3. Upload the revised PDF.</strong> Confirm that the file represents an authorized '
                'revision rather than an altered copy of the finalized contract.</p>'
                '<p><strong>4. Submit the revision.</strong> SealGuard creates the next version and links its '
                'fingerprint to the previous version.</p>'
                '<p><strong>5. Review Version History.</strong> Open the document preview and confirm that every '
                'version displays a valid chain indicator.</p>'
            ),
        },
        {
            'title': 'How to restore or permanently delete a contract',
            'summary': 'Use the Trash safely and understand when deletion can no longer be reversed.',
            'icon': 'warning',
            'sort_order': 5,
            'content': (
                '<p><strong>Administrator access is required.</strong></p>'
                '<p><strong>1. Remove public access first.</strong> A public contract cannot be moved to Trash '
                'until it has been unpublished.</p>'
                '<p><strong>2. Move the contract to Trash.</strong> Use the contract delete action and confirm.</p>'
                '<p><strong>3. Open Trash.</strong> Select Trash from the Contracts side panel.</p>'
                '<ul><li>Select <strong>Restore</strong> to return the contract to the active list.</li>'
                '<li>Select <strong>Delete Forever</strong> only when the contract and its stored files are no '
                'longer required.</li></ul>'
                '<p>Permanent deletion cannot be undone, although the document title remains in the audit history.</p>'
            ),
        },
        {
            'title': 'How to read verification results',
            'summary': 'Understand Authentic, Not Authentic, Unknown, and Could Not Verify outcomes.',
            'icon': 'check',
            'sort_order': 6,
            'content': (
                '<p><strong>Authentic Document</strong> means the uploaded PDF fingerprint matches an active '
                'official record in SealGuard.</p>'
                '<p><strong>Document Not Authentic</strong> means the fingerprint did not match. The file may '
                'have been modified after it was processed.</p>'
                '<p><strong>Unknown Document</strong> means the document could not be associated with a known '
                'SealGuard record.</p>'
                '<p><strong>Could Not Verify</strong> means processing could not be completed. Confirm that the '
                'file is a readable PDF produced by the system and try again.</p>'
                '<p>Expand the <strong>Verification Log</strong> to see which validation stage produced the result. '
                'Cryptographic values are intentionally hidden from this log.</p>'
            ),
        },
        {
            'title': 'How to review the audit dashboard',
            'summary': 'Use activity filters and document history to review important system events.',
            'icon': 'dashboard',
            'sort_order': 7,
            'content': (
                '<p><strong>1. Open Dashboard.</strong> Select the dashboard icon in the navigation.</p>'
                '<p><strong>2. Review recent activity.</strong> Entries identify the document, user, IP address, '
                'time, and recorded action.</p>'
                '<p><strong>3. Apply an activity filter.</strong> Show events such as added, encrypted, edited, '
                'deleted, viewed, or reported tampering.</p>'
                '<p><strong>4. Choose the order and rows per page.</strong> Use these controls when reviewing a '
                'long activity history.</p>'
                '<p><strong>5. Investigate unusual events.</strong> Repeated failed verification or unauthorized '
                'activity should be documented and reviewed by the administrator.</p>'
            ),
        },
    ]
    for item in tutorials:
        Tutorial.objects.get_or_create(title=item['title'], defaults=item)


class Migration(migrations.Migration):
    dependencies = [
        ('contracts', '0019_tutorial'),
    ]

    operations = [
        migrations.RunPython(add_more_tutorials, migrations.RunPython.noop),
    ]
