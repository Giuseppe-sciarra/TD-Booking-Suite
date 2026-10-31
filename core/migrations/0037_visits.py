from django.db import migrations,models
class Migration(migrations.Migration):
    dependencies=[("core","0035_api_webhook")]
    operations=[
        migrations.AddField(model_name="location",name="visit_split",field=models.CharField(choices=[("same","Stesso operatore"),("split","Divisibile tra operatori"),("need","Divisibile solo se serve")],default="need",max_length=5)),
        migrations.AddField(model_name="location",name="visit_wait",field=models.PositiveSmallIntegerField(default=15)),
        migrations.AddField(model_name="booking",name="visit",field=models.UUIDField(blank=True,db_index=True,null=True)),
    ]
