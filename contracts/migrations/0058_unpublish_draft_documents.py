from django.db import migrations


# Returning a Final copy to Draft used to leave it in Public Verified
# Documents. Only Final copies may be public, so hide any Draft left visible.
# The Final copy tutorial also wrongly said that finalizing does not publish.
OLD_TUTORIAL_TEXT = (
    '<p><strong>Final copy does not publish the PDF or grant public access.</strong> '
    'Use the QR access request process when someone needs permission to view a private document.</p>'
)
NEW_TUTORIAL_TEXT = (
    '<p><strong>Marking a document as Final copy publishes it in Public Verified Documents, '
    'and returning it to Draft removes it.</strong> Use the QR access request process when '
    'someone needs permission to view a document that is not public.</p>'
)


def unpublish_drafts(apps, schema_editor):
    Contract = apps.get_model('contracts', 'Contract')
    Contract.objects.filter(is_public=True).exclude(status='final').update(is_public=False)
    Tutorial = apps.get_model('contracts', 'Tutorial')
    for tutorial in Tutorial.objects.filter(content__contains=OLD_TUTORIAL_TEXT):
        tutorial.content = tutorial.content.replace(OLD_TUTORIAL_TEXT, NEW_TUTORIAL_TEXT)
        tutorial.save(update_fields=['content'])


def restore_tutorial(apps, schema_editor):
    Tutorial = apps.get_model('contracts', 'Tutorial')
    for tutorial in Tutorial.objects.filter(content__contains=NEW_TUTORIAL_TEXT):
        tutorial.content = tutorial.content.replace(NEW_TUTORIAL_TEXT, OLD_TUTORIAL_TEXT)
        tutorial.save(update_fields=['content'])


class Migration(migrations.Migration):

    dependencies = [
        ('contracts', '0057_update_access_request_tutorials'),
    ]

    operations = [
        migrations.RunPython(unpublish_drafts, restore_tutorial),
    ]
