from django.db import migrations,models

class Migration(migrations.Migration):
    dependencies=[("core","0025_last_arrival_whatsapp_retention")]
    operations=[
        migrations.AddField(model_name="tenant",name="force_all",field=models.BooleanField(default=False)),
        # all off: every existing service keeps confirming at once
        migrations.AddField(model_name="service",name="request_all",field=models.BooleanField(default=False)),
        migrations.AddField(model_name="service",name="request_above",field=models.PositiveIntegerField(default=0)),
        migrations.AddField(model_name="service",name="request_allergies",field=models.BooleanField(default=False)),
        migrations.AlterField(model_name="booking",name="status",field=models.CharField(choices=[("pending","In attesa di conferma"),("confirmed","Confermata"),("arrived","Arrivato"),("in_progress","In corso"),("completed","Completata"),("cancelled","Annullata"),("no_show","Assente")],default="confirmed",max_length=15)),
        # empty for the bookings made before: where they came from cannot be known afterwards
        migrations.AddField(model_name="booking",name="origin",field=models.CharField(blank=True,choices=[("public","Pagina pubblica"),("wordpress","Sito WordPress"),("desk","Banco"),("walkin","Senza appuntamento"),("import","Importata")],max_length=10)),
        migrations.AddField(model_name="booking",name="source",field=models.CharField(blank=True,max_length=30)),
    ]
