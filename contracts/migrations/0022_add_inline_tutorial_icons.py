from django.db import migrations


def icon(name, label):
    return (
        f'<span class="tutorial-inline-icon" data-icon="{name}" role="img" '
        f'aria-label="{label}" contenteditable="false">'
        f'<svg aria-hidden="true" viewBox="0 0 24 24" fill="none" '
        f'stroke="currentColor" stroke-width="2" stroke-linecap="round" '
        f'stroke-linejoin="round"><use href="#tutorial-icon-{name}"></use></svg></span>'
    )


def add_icons(apps, schema_editor):
    Tutorial = apps.get_model('contracts', 'Tutorial')
    replacements = {
        'How to upload and encrypt a contract': [
            ('<strong>1. Open Add Document.</strong>', f'<strong>1. {icon("upload", "Upload")} Open Add Document.</strong>'),
            ('<strong>5. Confirm the result.</strong> Return to Contracts', f'<strong>5. Confirm the result.</strong> Return to {icon("file", "Contracts")} Contracts'),
        ],
        'How to verify a PDF contract': [
            ('<strong>1. Open Verify Document.</strong>', f'<strong>1. {icon("shield", "Verify")} Open Verify Document.</strong>'),
            ('<strong>2. Choose the PDF.</strong>', f'<strong>2. {icon("file", "Document")} Choose the PDF.</strong>'),
        ],
        'How to organize contracts into folders': [
            ('<strong>1. Open Contracts.</strong>', f'<strong>1. {icon("file", "Contracts")} Open Contracts.</strong>'),
            ('<strong>2. Select the plus icon beside Folders.</strong>', f'<strong>2. Select the {icon("plus", "Add")} icon beside {icon("folder", "Folder")} Folders.</strong>'),
            ('<strong>3. Assign a contract.</strong>', f'<strong>3. {icon("file", "Document")} Assign a contract.</strong>'),
        ],
        'How to publish a contract for public viewing': [
            ('<strong>1. Open Contracts.</strong>', f'<strong>1. {icon("file", "Contracts")} Open Contracts.</strong>'),
            ('<strong>2. Select the eye icon.</strong>', f'<strong>2. Select the {icon("eye", "Public visibility")} eye icon.</strong>'),
            ('public document list on the Verify page.', f'public document list on the {icon("shield", "Verify")} Verify page.'),
        ],
        'How to add a new contract revision': [
            ('<strong>1. Open Contracts.</strong>', f'<strong>1. {icon("file", "Contracts")} Open Contracts.</strong>'),
            ('<strong>5. Review Version History.</strong>', f'<strong>5. Review {icon("history", "History")} Version History.</strong>'),
        ],
        'How to restore or permanently delete a contract': [
            ('<strong>2. Move the contract to Trash.</strong>', f'<strong>2. Move the contract to {icon("trash", "Trash")} Trash.</strong>'),
            ('<strong>3. Open Trash.</strong>', f'<strong>3. {icon("trash", "Trash")} Open Trash.</strong>'),
            ('Select <strong>Restore</strong>', f'Select {icon("restore", "Restore")} <strong>Restore</strong>'),
        ],
        'How to read verification results': [
            ('<strong>Authentic Document</strong>', f'{icon("check", "Authentic")} <strong>Authentic Document</strong>'),
            ('<strong>Document Not Authentic</strong>', f'{icon("warning", "Warning")} <strong>Document Not Authentic</strong>'),
            ('Expand the <strong>Verification Log</strong>', f'Expand the {icon("search", "Search")} <strong>Verification Log</strong>'),
        ],
        'How to review the audit dashboard': [
            ('<strong>1. Open Dashboard.</strong>', f'<strong>1. {icon("dashboard", "Dashboard")} Open Dashboard.</strong>'),
        ],
    }

    for title, pairs in replacements.items():
        tutorial = Tutorial.objects.filter(title=title).first()
        if not tutorial:
            continue
        content = tutorial.content
        for old, new in pairs:
            content = content.replace(old, new)
        if content != tutorial.content:
            tutorial.content = content
            tutorial.save(update_fields=['content', 'updated_at'])


def remove_icons(apps, schema_editor):
    # Keep the migration reversible without deleting tutorial content.
    pass


class Migration(migrations.Migration):
    dependencies = [
        ('contracts', '0021_auditlog_evidence_file'),
    ]

    operations = [
        migrations.RunPython(add_icons, remove_icons),
    ]
