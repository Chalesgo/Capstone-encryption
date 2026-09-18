from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('contracts', '0044_expanded_role_permissions')]
    operations = [
        migrations.CreateModel(
            name='EmailMessageLog',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('recipient', models.EmailField(max_length=254)),
                ('subject', models.CharField(max_length=255)),
                ('body', models.TextField()),
                ('sent_at', models.DateTimeField(auto_now_add=True)),
                ('delivered', models.BooleanField(default=False)),
            ],
            options={
                'verbose_name': 'Outgoing email',
                'verbose_name_plural': 'Outgoing email inbox',
                'ordering': ['-sent_at'],
            },
        ),
    ]
