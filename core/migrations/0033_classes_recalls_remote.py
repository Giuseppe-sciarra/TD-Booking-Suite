# 2.14.0: corsi e studi: serie ripetute dal banco, richiamo, test richiesto e link della videochiamata per servizio,
# categorie di clienti con i loro limiti online
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0029_waitlist_client_card"),
    ]

    operations = [
        migrations.AddField(
            model_name="booking",
            name="series",
            field=models.UUIDField(blank=True, db_index=True, null=True),
        ),
        migrations.AddField(
            model_name="service",
            name="recall_days",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="service",
            name="remote_url",
            field=models.URLField(blank=True, max_length=500),
        ),
        migrations.AddField(
            model_name="service",
            name="test_days",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="tenant",
            name="client_categories",
            field=models.JSONField(blank=True, default=list),
        ),
    ]
