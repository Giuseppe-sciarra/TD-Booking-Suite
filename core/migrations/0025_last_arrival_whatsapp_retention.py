from django.db import migrations,models

class Migration(migrations.Migration):
    dependencies=[("core","0024_user_quick_login")]
    operations=[
        # 0 = arrivals until closing: every existing service keeps what it did
        migrations.AddField(model_name="service",name="last_arrival",field=models.PositiveIntegerField(default=0)),
        migrations.AddField(model_name="tenant",name="whatsapp_text",field=models.TextField(blank=True)),
        migrations.AddField(model_name="tenant",name="retention_at",field=models.DateTimeField(blank=True,null=True)),
        migrations.AddField(model_name="tenant",name="retention_error",field=models.CharField(blank=True,max_length=200)),
    ]
