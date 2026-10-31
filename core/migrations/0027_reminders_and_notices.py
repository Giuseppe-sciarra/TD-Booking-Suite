from django.db import migrations,models

def keep_reminders(apps,schema_editor):
    """The reminder is now on by default and the owner switches it without the agency: the businesses that exist keep
    the one they had, which went only with the agency's switch on and the owner's own "on"."""
    for t in apps.get_model("core","Tenant").objects.all():
        mails=dict(t.client_mails or {});reminder=dict(mails.get("reminder") or {})
        reminder["on"]=bool(t.client_mails_enabled and reminder.get("on"))
        mails["reminder"]=reminder;t.client_mails=mails;t.save(update_fields=["client_mails"])

class Migration(migrations.Migration):
    dependencies=[("core","0026_statuses_desk_origin")]
    operations=[
        migrations.AddField(model_name="tenant",name="notify_operators",field=models.BooleanField(default=False)),
        migrations.AddField(model_name="tenant",name="summary_hour",field=models.PositiveSmallIntegerField(blank=True,null=True)),
        migrations.AddField(model_name="tenant",name="summary_sent",field=models.DateField(blank=True,null=True)),
        migrations.AddField(model_name="service",name="instructions",field=models.TextField(blank=True,max_length=2000)),
        migrations.RunPython(keep_reminders,migrations.RunPython.noop),
    ]
