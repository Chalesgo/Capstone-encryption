from django.db import migrations


def add_physical_verification_tutorial(apps, schema_editor):
    Tutorial = apps.get_model('contracts', 'Tutorial')
    Tutorial.objects.update_or_create(
        title='How to verify a physical document',
        defaults={
            'summary': 'Scan each page QR code, then submit clear images of the complete physical pages.',
            'icon': 'shield',
            'sort_order': 8,
            'content': (
                '<p><strong>Before you begin:</strong> Place the physical document on a flat, well-lit surface. '
                'The phone must be able to see both the page QR code and the complete page content.</p>'
                '<p><strong>1. Open Physical Verification.</strong> Allow SealGuard to use the rear camera when your browser asks.</p>'
                '<p><strong>2. Scan the QR code on page 1.</strong> Hold the phone steady until SealGuard confirms the document title, version, and page number.</p>'
                '<p><strong>3. Scan every remaining page in order.</strong> For a one-page document, continue as soon as page 1 is confirmed.</p>'
                '<p><strong>4. Upload the complete page.</strong> The upload panel opens automatically. Take a clear photo of the whole page, not a close-up of only the QR code. You may also choose existing page photos or one scanned PDF.</p>'
                '<p><strong>5. Keep pages in order.</strong> If you upload several photographs, select them in the same order in which you scanned their QR codes.</p>'
                '<p><strong>6. Wait for the result.</strong> Verification starts after the files are selected. SealGuard checks the registered QR identity and compares the submitted page content with the official version.</p>'
                '<ul><li><strong>Verified</strong> means the pages match the registered document.</li>'
                '<li><strong>Differences Detected</strong> means the submitted page content differs from the registered copy.</li>'
                '<li><strong>Incomplete / Invalid Document</strong> means pages are missing, duplicated, out of order, or linked to another document.</li>'
                '<li><strong>Manual Review Required</strong> means the scan quality was not sufficient for a confident automatic decision.</li></ul>'
            ),
        },
    )


def remove_physical_verification_tutorial(apps, schema_editor):
    Tutorial = apps.get_model('contracts', 'Tutorial')
    Tutorial.objects.filter(title='How to verify a physical document').delete()


class Migration(migrations.Migration):
    dependencies = [
        ('contracts', '0031_vector_fingerprints'),
    ]

    operations = [
        migrations.RunPython(
            add_physical_verification_tutorial,
            remove_physical_verification_tutorial,
        ),
    ]
