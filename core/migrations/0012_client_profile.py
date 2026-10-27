import django.db.models.deletion
import uuid
from django.db import migrations, models

TABLE="core_clientprofile"

def protect(apps,schema_editor):
    if schema_editor.connection.vendor!="postgresql":return
    q=schema_editor.quote_name;t=q(TABLE);x=schema_editor.execute
    x(f"ALTER TABLE {t} ADD CONSTRAINT {q(TABLE+'_id_tenant')} UNIQUE (id,tenant_id)")
    x(f"ALTER TABLE {t} ENABLE ROW LEVEL SECURITY")
    x(f"ALTER TABLE {t} FORCE ROW LEVEL SECURITY")
    x(f"CREATE POLICY tenant_boundary ON {t} USING (tenant_id::text=current_setting('app.tenant_id',true)) WITH CHECK (tenant_id::text=current_setting('app.tenant_id',true))")

def unprotect(apps,schema_editor):
    if schema_editor.connection.vendor!="postgresql":return
    q=schema_editor.quote_name;t=q(TABLE);x=schema_editor.execute
    x(f"DROP POLICY IF EXISTS tenant_boundary ON {t}")
    x(f"ALTER TABLE {t} DISABLE ROW LEVEL SECURITY")
    x(f"ALTER TABLE {t} DROP CONSTRAINT IF EXISTS {q(TABLE+'_id_tenant')}")


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0011_role_names"),
    ]

    operations = [
        migrations.CreateModel(
            name="ClientProfile",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("data", models.TextField(blank=True)),
                ("updated", models.DateTimeField(auto_now=True)),
                ("tenant", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to="core.tenant")),
            ],
            options={"abstract": False},
        ),
        migrations.AddConstraint(
            model_name="clientprofile",
            constraint=models.UniqueConstraint(fields=("tenant",), name="client_profile_unique"),
        ),
        migrations.RunPython(protect, unprotect),
    ]
