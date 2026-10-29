from django.db import migrations,models

def keep_export(apps,schema_editor):
    # the businesses that already send campaigns keep the CSV they had
    apps.get_model("core","Tenant").objects.filter(marketing_enabled=True).update(export_enabled=True)

class Migration(migrations.Migration):
    dependencies=[("core","0022_operator_photos_and_own_services")]
    operations=[
        migrations.AddField(model_name="tenant",name="export_enabled",field=models.BooleanField(default=False)),
        migrations.AddField(model_name="tenant",name="client_mails_enabled",field=models.BooleanField(default=False)),
        migrations.AddField(model_name="tenant",name="client_mails",field=models.JSONField(blank=True,default=dict)),
        migrations.AddField(model_name="contact",name="notices",field=models.BooleanField(default=True)),
        migrations.RunPython(keep_export,migrations.RunPython.noop),
    ]
