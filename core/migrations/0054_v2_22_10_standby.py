# 2.22.10: Parcheggio, the bills in stand-by at the Cassa (a table of its own, isolated per business)

import django.db.models.deletion
import uuid
from django.db import migrations, models

TABLE="core_standby"
# the bill belongs to a location, maybe a client and a booking, of the same business
LINKS=[("location","core_location"),("contact","core_contact"),("booking","core_booking")]

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

    dependencies = [
        ("core", "0053_v2_22_9_photos"),
    ]

    operations = [
        migrations.CreateModel(
            name="Standby",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("total", models.DecimalField(decimal_places=2, default=0, max_digits=9)),
                ("actor", models.CharField(blank=True, max_length=36)),
                ("pii", models.TextField()),
                ("created", models.DateTimeField(auto_now_add=True)),
                ("booking", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="standby", to="core.booking")),
                ("contact", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="standby", to="core.contact")),
                ("location", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to="core.location")),
                ("tenant", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to="core.tenant")),
            ],
        ),
        migrations.RunPython(protect, unprotect),
    ]
