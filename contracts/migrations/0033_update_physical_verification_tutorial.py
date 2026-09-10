from django.db import migrations


def update_physical_verification_tutorial(apps, schema_editor):
    Tutorial = apps.get_model('contracts', 'Tutorial')
    Tutorial.objects.update_or_create(
        title='How to verify a physical document',
        defaults={
            'summary': 'Scan each page QR code, then photograph each complete physical page with the same camera.',
            'icon': 'shield',
            'sort_order': 8,
            'content': (
                '<p><strong>Before you begin:</strong> Place the document on a flat, well-lit surface and allow SealGuard to use the rear camera.</p>'
                '<p><strong>1. Scan every page QR code.</strong> Start with page 1 and continue in page order. Hold the phone steady until SealGuard confirms each page.</p>'
                '<p><strong>2. Photograph the complete pages.</strong> After the QR codes are confirmed, the camera changes to page-capture mode. Move the phone back until the whole page is inside the guide, then tap the floating camera button.</p>'
                '<p><strong>3. Capture multiple pages in order.</strong> SealGuard displays the page number currently required. Photograph page 1, page 2, and so on.</p>'
                '<p><strong>4. Use existing files when preferred.</strong> Tap the floating file button to choose complete page photographs or one scanned PDF instead of taking new photos.</p>'
                '<p><strong>5. Wait for the result.</strong> Verification begins automatically once SealGuard has the QR codes and complete page images.</p>'
                '<ul><li><strong>Verified</strong> means the submitted pages match the registered document.</li>'
                '<li><strong>Differences Detected</strong> means the page content differs from the registered copy.</li>'
                '<li><strong>Incomplete / Invalid Document</strong> means pages are missing, duplicated, out of order, or linked to another document.</li>'
                '<li><strong>Manual Review Required</strong> means the photo quality was insufficient for a confident automatic decision.</li></ul>'
            ),
        },
    )


class Migration(migrations.Migration):
    dependencies = [
        ('contracts', '0032_add_physical_verification_tutorial'),
    ]

    operations = [
        migrations.RunPython(update_physical_verification_tutorial, migrations.RunPython.noop),
    ]
