from django.db import migrations,models
class Migration(migrations.Migration):
    dependencies=[("core","0019_site_default_theme")]
    operations=[migrations.AddField(model_name="user",name="name_index",field=models.CharField(blank=True,max_length=64,null=True,unique=True))]
