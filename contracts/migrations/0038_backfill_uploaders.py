from django.db import migrations


def backfill(apps, schema_editor):
    Contract = apps.get_model('contracts', 'Contract')
    Version = apps.get_model('contracts', 'ContractVersion')
    for contract in Contract.objects.using(schema_editor.connection.alias).all().iterator():
        first = Version.objects.using(schema_editor.connection.alias).filter(
            contract_id=contract.pk
        ).order_by('version_number', 'pk').first()
        # Only an initial upload establishes ownership, never a later editor.
        owner = first.created_by_id if first and first.version_number == 1 else None
        contract.uploaded_by_id = owner or contract.recipient_id
        contract.save(update_fields=['uploaded_by'])


class Migration(migrations.Migration):
    dependencies = [('contracts', '0037_contract_collaborators_contract_uploaded_by')]
    operations = [migrations.RunPython(backfill, migrations.RunPython.noop)]
