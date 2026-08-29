from django.db import migrations, models


def repair_fingerprints(apps, schema_editor):
    Contract = apps.get_model('contracts', 'Contract')
    ContractVersion = apps.get_model('contracts', 'ContractVersion')

    from contracts.utils import generate_canonical_fingerprint

    for contract in Contract.objects.exclude(file='').iterator():
        try:
            contract_path = contract.file.path
            contract.fingerprint = generate_canonical_fingerprint(contract_path)
            contract.save(update_fields=['fingerprint'])
        except Exception:
            continue

        previous = ''
        versions = ContractVersion.objects.filter(contract_id=contract.id).order_by('version_number')
        for version in versions.iterator():
            try:
                fingerprint = generate_canonical_fingerprint(
                    version.file.path,
                    previous_cf=previous or None,
                )
            except Exception:
                continue
            version.previous_fingerprint = previous
            version.fingerprint = fingerprint
            version.save(update_fields=['previous_fingerprint', 'fingerprint'])
            previous = fingerprint


class Migration(migrations.Migration):
    dependencies = [
        ('contracts', '0016_auditlog_query_indexes'),
    ]

    operations = [
        migrations.AlterField(
            model_name='contract',
            name='fingerprint',
            field=models.CharField(blank=True, db_index=True, max_length=64),
        ),
        migrations.RunPython(repair_fingerprints, migrations.RunPython.noop),
    ]
