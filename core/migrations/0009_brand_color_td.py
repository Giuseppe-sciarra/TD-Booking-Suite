from django.db import migrations,models

OLD,NEW="#2563eb","#7a55ad"

def forwards(apps,schema_editor):
    # only businesses still on the old untouched default move to the Tastiere Digitali violet
    apps.get_model("core","Tenant").objects.filter(brand_color=OLD).update(brand_color=NEW)

def backwards(apps,schema_editor):
    apps.get_model("core","Tenant").objects.filter(brand_color=NEW).update(brand_color=OLD)

class Migration(migrations.Migration):
    dependencies=[("core","0008_presets_notifications")]
    operations=[
        migrations.AlterField(model_name="tenant",name="brand_color",field=models.CharField(default=NEW,max_length=7)),
        migrations.RunPython(forwards,backwards),
    ]
