from django.db import migrations,models

class Migration(migrations.Migration):
    dependencies=[("core","0023_client_mails_and_marketing_switches")]
    operations=[migrations.AddField(model_name="user",name="quick_login",field=models.BooleanField(default=False))]
