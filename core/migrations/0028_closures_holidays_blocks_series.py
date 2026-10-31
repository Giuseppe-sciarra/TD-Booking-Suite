# 2.14.0: closures "dal … al …", alternate weeks, series of event dates, the patron saint, and the time blocks of the
# agenda (in a table of their own, isolated per business)
import django.db.models.deletion
import uuid
from django.db import migrations,models

TABLE="core_timeblock"
# a block belongs to an operator or a service of the same business
LINKS=[("resource","core_resource"),("service","core_service")]

def protect(apps,schema_editor):
    if schema_editor.connection.vendor!="postgresql":return
    q=schema_editor.quote_name;t=q(TABLE);x=schema_editor.execute
    x(f"ALTER TABLE {t} ADD CONSTRAINT {q(TABLE+'_id_tenant')} UNIQUE (id,tenant_id)")
    x(f"ALTER TABLE {t} ENABLE ROW LEVEL SECURITY")
    x(f"ALTER TABLE {t} FORCE ROW LEVEL SECURITY")
    x(f"CREATE POLICY tenant_boundary ON {t} USING (tenant_id::text=current_setting('app.tenant_id',true)) WITH CHECK (tenant_id::text=current_setting('app.tenant_id',true))")
    for field,target in LINKS:
        name=(TABLE+"_"+field+"_same_tenant")[:63]
        x(f"ALTER TABLE {t} ADD CONSTRAINT {q(name)} FOREIGN KEY ({q(field+'_id')},tenant_id) REFERENCES {q(target)} (id,tenant_id) DEFERRABLE INITIALLY DEFERRED")

def unprotect(apps,schema_editor):
    if schema_editor.connection.vendor!="postgresql":return
    q=schema_editor.quote_name;t=q(TABLE);x=schema_editor.execute
    for field,_ in LINKS:x(f"ALTER TABLE {t} DROP CONSTRAINT IF EXISTS {q((TABLE+'_'+field+'_same_tenant')[:63])}")
    x(f"DROP POLICY IF EXISTS tenant_boundary ON {t}")
    x(f"ALTER TABLE {t} DISABLE ROW LEVEL SECURITY")
    x(f"ALTER TABLE {t} DROP CONSTRAINT IF EXISTS {q(TABLE+'_id_tenant')}")

class Migration(migrations.Migration):
    dependencies=[("core","0027_reminders_and_notices")]
    operations=[
        # empty: every closure that exists stays a single date
        migrations.AddField(model_name="exceptionday",name="until",field=models.DateField(blank=True,null=True)),
        migrations.AddField(model_name="occurrence",name="series",field=models.UUIDField(blank=True,db_index=True,null=True)),
        migrations.AddField(model_name="schedule",name="every_weeks",field=models.PositiveSmallIntegerField(default=1)),
        migrations.AddField(model_name="tenant",name="patron_day",field=models.CharField(blank=True,max_length=5)),
        migrations.AddField(model_name="tenant",name="patron_name",field=models.CharField(blank=True,max_length=60)),
        migrations.CreateModel(name="TimeBlock",fields=[
            ("id",models.UUIDField(default=uuid.uuid4,editable=False,primary_key=True,serialize=False)),
            ("day",models.DateField()),
            ("start",models.TimeField()),
            ("end",models.TimeField()),
            ("weekly",models.BooleanField(default=False)),
            ("until",models.DateField(blank=True,null=True)),
            ("reason",models.CharField(choices=[("break","Pausa"),("training","Formazione"),("meeting","Riunione"),("other","Altro"),("closed","Turno chiuso")],default="break",max_length=10)),
            ("pii",models.TextField(blank=True)),
            ("created",models.DateTimeField(auto_now_add=True)),
            ("resource",models.ForeignKey(blank=True,null=True,on_delete=django.db.models.deletion.CASCADE,to="core.resource")),
            ("service",models.ForeignKey(blank=True,null=True,on_delete=django.db.models.deletion.CASCADE,to="core.service")),
            ("tenant",models.ForeignKey(on_delete=django.db.models.deletion.PROTECT,to="core.tenant")),
        ],options={"indexes":[models.Index(fields=["tenant","day"],name="core_timebl_tenant__399e0f_idx")]}),
        migrations.RunPython(protect,unprotect),
    ]
