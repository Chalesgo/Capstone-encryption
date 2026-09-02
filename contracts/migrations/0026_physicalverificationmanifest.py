from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [('contracts', '0025_clear_deprecated_contract_aes_key')]
    operations = [
        migrations.CreateModel(
            name='PhysicalVerificationManifest',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('manifest_id', models.CharField(db_index=True, max_length=64, unique=True)),
                ('manifest', models.JSONField()),
                ('signature', models.TextField()),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('version', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='physical_manifest', to='contracts.contractversion')),
            ],
        ),
    ]
