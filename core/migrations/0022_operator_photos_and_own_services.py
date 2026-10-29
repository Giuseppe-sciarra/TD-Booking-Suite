# 2.12.0: operators' photos (in a table of their own, isolated per business) and each operator's own duration and
# price for a service

import django.db.models.deletion
import uuid
from django.db import migrations, models

TABLE="core_resourcephoto"
# the photo belongs to a resource of the same business
LINKS=[("resource","core_resource")]

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
        ("core", "0021_prices_and_discounts"),
    ]

    operations = [
        migrations.AddField(
            model_name="resource",
            name="photo_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="serviceresource",
            name="duration",
            field=models.PositiveIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="serviceresource",
            name="price",
            field=models.DecimalField(
                blank=True, decimal_places=2, max_digits=8, null=True
            ),
        ),
        migrations.CreateModel(
            name="ResourcePhoto",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ("data", models.BinaryField()),
                (
                    "resource",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="photo",
                        to="core.resource",
                    ),
                ),
                (
                    "tenant",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT, to="core.tenant"
                    ),
                ),
            ],
            options={
                "abstract": False,
            },
        ),
        migrations.RunPython(protect, unprotect),
    ]
