from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('contracts', '0048_add_workspace_tutorials')]

    operations = [
        migrations.CreateModel(
            name='NotificationClearState',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('cleared_at', models.DateTimeField()),
                ('user', models.OneToOneField(on_delete=models.deletion.CASCADE, related_name='notification_clear_state', to=settings.AUTH_USER_MODEL)),
            ],
        ),
    ]
