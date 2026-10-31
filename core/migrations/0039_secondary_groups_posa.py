from django.db import migrations,models
class Migration(migrations.Migration):
    dependencies=[("core","0037_visits")]
    operations=[
        migrations.AddField(model_name="resource",name="group",field=models.CharField(blank=True,max_length=40)),
        migrations.AddField(model_name="serviceresource",name="pause_start",field=models.PositiveIntegerField(blank=True,null=True)),
        migrations.AddField(model_name="serviceresource",name="pause_minutes",field=models.PositiveIntegerField(blank=True,null=True)),
        migrations.AddField(model_name="booking",name="more",field=models.JSONField(blank=True,default=list)),
    ]
