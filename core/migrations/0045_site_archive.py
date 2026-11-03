# 2.18.0: the site as the central archive: the switch, the numbers of the changes and the outbox, isolated per business


import django.db.models.deletion
import uuid
from django.db import migrations, models


def protect(apps,schema_editor):
    if schema_editor.connection.vendor!="postgresql":return
    q=schema_editor.quote_name;x=schema_editor.execute;t=q("core_siteoutbox")
    x(f"ALTER TABLE {t} ADD CONSTRAINT {q('core_siteoutbox_id_tenant')} UNIQUE (id,tenant_id)")
    x(f"ALTER TABLE {t} ENABLE ROW LEVEL SECURITY")
    x(f"ALTER TABLE {t} FORCE ROW LEVEL SECURITY")
    x(f"CREATE POLICY tenant_boundary ON {t} USING (tenant_id::text=current_setting('app.tenant_id',true)) WITH CHECK (tenant_id::text=current_setting('app.tenant_id',true))")

def unprotect(apps,schema_editor):
    if schema_editor.connection.vendor!="postgresql":return
    t=schema_editor.quote_name("core_siteoutbox")
    schema_editor.execute(f"DROP POLICY IF EXISTS tenant_boundary ON {t}")
    schema_editor.execute(f"ALTER TABLE {t} DISABLE ROW LEVEL SECURITY")
    schema_editor.execute(f"ALTER TABLE {t} DROP CONSTRAINT IF EXISTS {schema_editor.quote_name('core_siteoutbox_id_tenant')}")


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0044_wp_sync"),
    ]

    operations = [
        migrations.AddField(
            model_name="tenant",
            name="wp_archive",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="tenant",
            name="wp_seq",
            field=models.BigIntegerField(default=0),
        ),
        migrations.CreateModel(
            name="SiteOutbox",
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
                ("kind", models.CharField(max_length=10)),
                ("ref", models.CharField(max_length=36)),
                ("seq", models.BigIntegerField(db_index=True)),
                (
                    "tenant",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT, to="core.tenant"
                    ),
                ),
            ],
            options={
                "constraints": [
                    models.UniqueConstraint(
                        fields=("tenant", "kind", "ref"), name="outbox_record"
                    )
                ],
            },
        ),
        migrations.RunPython(protect,unprotect),
    ]
