from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0003_tenant_logo'),
    ]

    operations = [
        migrations.AddField(
            model_name='tenant',
            name='maintenance',
            field=models.BooleanField(default=False),
        ),
    ]
