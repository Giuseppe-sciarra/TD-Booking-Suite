# 2.14.0: the rooms and tables of a restaurant and the floor plan (tables.py), each in a table of its own, isolated per business

import django.db.models.deletion
import uuid
from django.db import migrations, models

# an area belongs to a location, a table to an area, a booking's area to its business (the pattern of 0028)
LINKS={"core_area":[("location","core_location")],"core_table":[("area","core_area")],"core_booking":[("area","core_area")]}
NEW=("core_area","core_table")

def protect(apps,schema_editor):
    if schema_editor.connection.vendor!="postgresql":return
    q=schema_editor.quote_name;x=schema_editor.execute
    for table in NEW:
        t=q(table)
        x(f"ALTER TABLE {t} ADD CONSTRAINT {q(table+'_id_tenant')} UNIQUE (id,tenant_id)")
        x(f"ALTER TABLE {t} ENABLE ROW LEVEL SECURITY")
        x(f"ALTER TABLE {t} FORCE ROW LEVEL SECURITY")
        x(f"CREATE POLICY tenant_boundary ON {t} USING (tenant_id::text=current_setting('app.tenant_id',true)) WITH CHECK (tenant_id::text=current_setting('app.tenant_id',true))")
    for table,links in LINKS.items():
        for field,target in links:
            name=(table+"_"+field+"_same_tenant")[:63]
            x(f"ALTER TABLE {q(table)} ADD CONSTRAINT {q(name)} FOREIGN KEY ({q(field+'_id')},tenant_id) REFERENCES {q(target)} (id,tenant_id) DEFERRABLE INITIALLY DEFERRED")

def unprotect(apps,schema_editor):
    if schema_editor.connection.vendor!="postgresql":return
    q=schema_editor.quote_name;x=schema_editor.execute
    for table,links in LINKS.items():
        for field,_ in links:x(f"ALTER TABLE {q(table)} DROP CONSTRAINT IF EXISTS {q((table+'_'+field+'_same_tenant')[:63])}")
    for table in NEW:
        t=q(table)
        x(f"DROP POLICY IF EXISTS tenant_boundary ON {t}")
        x(f"ALTER TABLE {t} DISABLE ROW LEVEL SECURITY")
        x(f"ALTER TABLE {t} DROP CONSTRAINT IF EXISTS {q(table+'_id_tenant')}")

class Migration(migrations.Migration):

    dependencies = [
        ("core", "0038_google_calendar"),
    ]

    operations = [
        migrations.AddField(
            model_name="booking",
            name="tables",
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.AddField(
            model_name="location",
            name="tables",
            field=models.CharField(
                choices=[
                    ("off", "Nessuna gestione dei tavoli"),
                    ("manual", "Li assegnate voi"),
                    ("suggest", "Il programma propone il tavolo più adatto"),
                ],
                default="off",
                max_length=7,
            ),
        ),
        migrations.AddField(
            model_name="location",
            name="tables_online",
            field=models.BooleanField(default=False),
        ),
        migrations.CreateModel(
            name="Area",
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
                ("name", models.CharField(max_length=60)),
                ("capacity", models.PositiveIntegerField(default=0)),
                (
                    "location",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE, to="core.location"
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
        migrations.AddField(
            model_name="booking",
            name="area",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                to="core.area",
            ),
        ),
        migrations.CreateModel(
            name="Table",
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
                ("name", models.CharField(max_length=30)),
                ("min_covers", models.PositiveSmallIntegerField(default=1)),
                ("max_covers", models.PositiveSmallIntegerField(default=4)),
                ("priority", models.PositiveSmallIntegerField(default=0)),
                ("joins", models.JSONField(blank=True, default=list)),
                ("x", models.FloatField(default=5)),
                ("y", models.FloatField(default=5)),
                (
                    "area",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="tables",
                        to="core.area",
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
        migrations.RunPython(protect,unprotect),
    ]
