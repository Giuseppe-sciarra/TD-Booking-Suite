from django.db import migrations,models
class Migration(migrations.Migration):
    dependencies=[("core","0029_waitlist_client_card")]
    operations=[
        migrations.AddField(model_name="tenant",name="api_key",field=models.CharField(blank=True,db_index=True,max_length=64)),
        migrations.AddField(model_name="tenant",name="webhook",field=models.TextField(blank=True)),
    ]
