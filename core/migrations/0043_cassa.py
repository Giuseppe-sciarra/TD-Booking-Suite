# 2.16.0: the fiscal register of each location and the receipts it emitted, isolated per business

import django.db.models.deletion
import uuid
from django.db import migrations, models

def protect(apps,schema_editor):
    if schema_editor.connection.vendor!="postgresql":return
    q=schema_editor.quote_name;x=schema_editor.execute;t=q("core_sale")
    x(f"ALTER TABLE {t} ADD CONSTRAINT {q('core_sale_id_tenant')} UNIQUE (id,tenant_id)")
    x(f"ALTER TABLE {t} ENABLE ROW LEVEL SECURITY")
    x(f"ALTER TABLE {t} FORCE ROW LEVEL SECURITY")
    x(f"CREATE POLICY tenant_boundary ON {t} USING (tenant_id::text=current_setting('app.tenant_id',true)) WITH CHECK (tenant_id::text=current_setting('app.tenant_id',true))")

def unprotect(apps,schema_editor):
    if schema_editor.connection.vendor!="postgresql":return
    t=schema_editor.quote_name("core_sale")
    schema_editor.execute(f"DROP POLICY IF EXISTS tenant_boundary ON {t}")
    schema_editor.execute(f"ALTER TABLE {t} DISABLE ROW LEVEL SECURITY")
    schema_editor.execute(f"ALTER TABLE {t} DROP CONSTRAINT IF EXISTS {schema_editor.quote_name('core_sale_id_tenant')}")


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0042_calendly_features"),
    ]

    operations = [
        migrations.AddField(
            model_name="location",
            name="till",
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AddField(
            model_name="location",
            name="till_seen",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="location",
            name="till_token",
            field=models.CharField(blank=True, db_index=True, max_length=64),
        ),
        migrations.AddField(
            model_name="tenant",
            name="till_on",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="tenant",
            name="till_operators",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="tenant",
            name="till_self",
            field=models.BooleanField(default=False),
        ),
        migrations.CreateModel(
            name="Sale",
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
                (
                    "kind",
                    models.CharField(
                        choices=[
                            ("sale", "Scontrino"),
                            ("void", "Annullo"),
                            ("probe", "Prova collegamento"),
                            ("test", "Stampa di prova"),
                        ],
                        default="sale",
                        max_length=6,
                    ),
                ),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("queued", "In coda"),
                            ("sending", "In corso"),
                            ("done", "Emesso"),
                            ("failed", "Non emesso"),
                            ("unknown", "Da verificare"),
                            ("void", "Annullato"),
                        ],
                        default="queued",
                        max_length=8,
                    ),
                ),
                (
                    "total",
                    models.DecimalField(decimal_places=2, default=0, max_digits=9),
                ),
                ("number", models.CharField(blank=True, max_length=20)),
                ("z", models.CharField(blank=True, max_length=10)),
                ("serial", models.CharField(blank=True, max_length=20)),
                ("printed", models.DateTimeField(blank=True, null=True)),
                ("error", models.CharField(blank=True, max_length=200)),
                ("claimed", models.DateTimeField(blank=True, null=True)),
                ("mailed", models.DateTimeField(blank=True, null=True)),
                ("key", models.CharField(max_length=64)),
                ("actor", models.CharField(blank=True, max_length=36)),
                ("pii", models.TextField()),
                ("created", models.DateTimeField(auto_now_add=True)),
                (
                    "booking",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="sales",
                        to="core.booking",
                    ),
                ),
                (
                    "contact",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="sales",
                        to="core.contact",
                    ),
                ),
                (
                    "location",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT, to="core.location"
                    ),
                ),
                (
                    "tenant",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT, to="core.tenant"
                    ),
                ),
                (
                    "voids",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="voided_by",
                        to="core.sale",
                    ),
                ),
            ],
            options={
                "indexes": [
                    models.Index(
                        fields=["location", "status", "created"],
                        name="core_sale_locatio_7e1303_idx",
                    )
                ],
                "constraints": [
                    models.UniqueConstraint(fields=("tenant", "key"), name="sale_key")
                ],
            },
        ),
        migrations.RunPython(protect,unprotect),
    ]
