# 2.14.0: the tourist tax by the town's rules, Alloggiati Web by its web service and its receipts, isolated per business

import django.db.models.deletion
import uuid
from django.db import migrations, models

def protect(apps,schema_editor):
    if schema_editor.connection.vendor!="postgresql":return
    q=schema_editor.quote_name;x=schema_editor.execute;t=q("core_receipt")
    x(f"ALTER TABLE {t} ENABLE ROW LEVEL SECURITY")
    x(f"ALTER TABLE {t} FORCE ROW LEVEL SECURITY")
    x(f"CREATE POLICY tenant_boundary ON {t} USING (tenant_id::text=current_setting('app.tenant_id',true)) WITH CHECK (tenant_id::text=current_setting('app.tenant_id',true))")

def unprotect(apps,schema_editor):
    if schema_editor.connection.vendor!="postgresql":return
    t=schema_editor.quote_name("core_receipt")
    schema_editor.execute(f"DROP POLICY IF EXISTS tenant_boundary ON {t}")
    schema_editor.execute(f"ALTER TABLE {t} DISABLE ROW LEVEL SECURITY")


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0037_visits"),
    ]

    operations = [
        migrations.AddField(
            model_name="location",
            name="tax_category",
            field=models.CharField(blank=True, max_length=20),
        ),
        migrations.AddField(
            model_name="location",
            name="tax_escort",
            field=models.PositiveSmallIntegerField(default=25),
        ),
        migrations.AddField(
            model_name="location",
            name="tax_reduction",
            field=models.PositiveSmallIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="location",
            name="tax_rules",
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.AddField(
            model_name="location",
            name="tax_year_nights",
            field=models.PositiveSmallIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="tenant",
            name="alloggiati",
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name="tenant",
            name="alloggiati_self",
            field=models.BooleanField(default=False),
        ),
        migrations.CreateModel(
            name="Receipt",
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
                ("day", models.DateField()),
                ("rows", models.PositiveIntegerField(default=0)),
                ("pdf", models.BinaryField(blank=True, null=True)),
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
                        fields=("tenant", "day"), name="receipt_day"
                    )
                ],
            },
        ),
        migrations.RunPython(protect,unprotect),
    ]
