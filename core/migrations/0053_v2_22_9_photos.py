# 2.22.9: the client's photo album (a table of its own, isolated per business)

import django.db.models.deletion
import uuid
from django.db import migrations, models

TABLE="core_clientphoto"
# the photo belongs to a client, and maybe a booking, of the same business
LINKS=[("contact","core_contact"),("booking","core_booking")]

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
        ("core", "0052_v2_22_1_thanks"),
    ]

    operations = [
        migrations.CreateModel(
            name="ClientPhoto",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("date", models.DateField()),
                ("image", models.BinaryField()),
                ("thumb", models.BinaryField()),
                ("created", models.DateTimeField(auto_now_add=True)),
                ("booking", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="photos", to="core.booking")),
                ("contact", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="photos", to="core.contact")),
                ("tenant", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to="core.tenant")),
            ],
            options={"indexes": [models.Index(fields=["tenant", "contact", "date"], name="core_client_tenant__7754a1_idx")]},
        ),
        migrations.RunPython(protect, unprotect),
    ]
