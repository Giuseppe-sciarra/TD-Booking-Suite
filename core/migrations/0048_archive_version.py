# 2.20.0: the moment of the last change of what the site archives, its version (wpsync.ver); the rows made before keep
# NULL and count from when they were created

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0047_sale_close"),
    ]

    operations = [
        migrations.AddField(model_name=m, name="updated", field=models.DateTimeField(blank=True, null=True))
        for m in ("contact", "booking", "treatment", "consentevent", "sale")
    ]
