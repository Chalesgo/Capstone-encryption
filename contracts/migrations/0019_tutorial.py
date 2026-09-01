from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


def create_default_tutorials(apps, schema_editor):
    Tutorial = apps.get_model('contracts', 'Tutorial')
    defaults = [
        {
            'title': 'How to upload and encrypt a contract',
            'summary': 'Upload a PDF and let SealGuard create its protected fingerprint, seal, and first version.',
            'icon': 'lock',
            'sort_order': 0,
            'content': (
                '<p><strong>1. Open Add Document.</strong> Select the plus icon in the navigation.</p>'
                '<p><strong>2. Enter the contract title.</strong> Use a clear title that staff can recognize.</p>'
                '<p><strong>3. Select a PDF.</strong> The system accepts valid PDF procurement documents.</p>'
                '<p><strong>4. Submit the upload.</strong> SealGuard generates the canonical fingerprint, '
                'encrypts it, embeds the protected marker in the seal, and creates version 1.</p>'
                '<p><strong>5. Confirm the result.</strong> Return to Contracts and check that the document '
                'shows the encrypted status.</p>'
            ),
        },
        {
            'title': 'How to verify a PDF contract',
            'summary': 'Check whether a processed PDF matches an official contract record or shows signs of modification.',
            'icon': 'shield',
            'sort_order': 1,
            'content': (
                '<p><strong>1. Open Verify Document.</strong> Select the shield icon.</p>'
                '<p><strong>2. Choose the PDF.</strong> Upload the copy that you want to authenticate.</p>'
                '<p><strong>3. Start verification.</strong> The system checks the document structure, marker, '
                'canonical fingerprint, and matching database record.</p>'
                '<p><strong>4. Read the status.</strong></p>'
                '<ul><li><strong>Authentic Document</strong> means the fingerprint matches an active record.</li>'
                '<li><strong>Document Not Authentic</strong> means the document does not match or was modified.</li>'
                '<li><strong>Could Not Verify</strong> means the file may not have been processed by this system.</li></ul>'
            ),
        },
        {
            'title': 'How to organize contracts into folders',
            'summary': 'Create folders, assign documents, and filter the contracts list.',
            'icon': 'folder',
            'sort_order': 2,
            'content': (
                '<p><strong>1. Open Contracts.</strong> Locate the Folders section in the Questions-style side panel.</p>'
                '<p><strong>2. Select the plus icon beside Folders.</strong> Enter a folder name.</p>'
                '<p><strong>3. Assign a contract.</strong> Open the contract action menu and select Add to Folder.</p>'
                '<p><strong>4. Filter the list.</strong> Select the folder name to show only its contracts.</p>'
            ),
        },
    ]
    for item in defaults:
        Tutorial.objects.get_or_create(title=item['title'], defaults=item)


class Migration(migrations.Migration):
    dependencies = [
        ('contracts', '0018_folder_sort_order'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='Tutorial',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('title', models.CharField(max_length=160)),
                ('summary', models.CharField(max_length=280)),
                ('content', models.TextField()),
                ('icon', models.CharField(choices=[('help', 'Help'), ('shield', 'Shield'), ('file', 'Document'), ('upload', 'Upload'), ('lock', 'Encryption'), ('search', 'Search'), ('folder', 'Folder'), ('dashboard', 'Dashboard'), ('eye', 'View'), ('download', 'Download'), ('history', 'History'), ('warning', 'Warning'), ('check', 'Check'), ('user', 'User')], default='help', max_length=20)),
                ('sort_order', models.PositiveIntegerField(default=0)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('created_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='tutorials', to=settings.AUTH_USER_MODEL)),
            ],
            options={'ordering': ['sort_order', 'title']},
        ),
        migrations.RunPython(create_default_tutorials, migrations.RunPython.noop),
    ]
