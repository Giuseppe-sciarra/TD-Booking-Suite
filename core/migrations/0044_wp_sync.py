# 2.17.0: the WordPress site linked to the PC, also when the PC is off (wpsync.py)

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0043_cassa"),
    ]

    operations = [
        migrations.AddField(
            model_name="tenant",
            name="wp_sync",
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name="tenant",
            name="wp_state",
            field=models.JSONField(blank=True, default=dict),
        ),
    ]
