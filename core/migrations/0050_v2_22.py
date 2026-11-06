# 2.22.0: products, gift cards, Preventivo pagamenti cassa and quotes, each isolated per business

import django.db.models.deletion
import uuid
from django.db import migrations, models

# each table, and the rows of the same business its links point to
TABLES={"core_product":[],"core_giftcard":[("contact","core_contact"),("sale","core_sale")],"core_giftmove":[("card","core_giftcard"),("sale","core_sale")],
    "core_parked":[("location","core_location"),("contact","core_contact"),("booking","core_booking")],"core_quote":[("contact","core_contact")]}

def protect(apps,schema_editor):
    if schema_editor.connection.vendor!="postgresql":return
    q=schema_editor.quote_name;x=schema_editor.execute
    for table,links in TABLES.items():
        t=q(table)
        x(f"ALTER TABLE {t} ADD CONSTRAINT {q(table+'_id_tenant')} UNIQUE (id,tenant_id)")
        x(f"ALTER TABLE {t} ENABLE ROW LEVEL SECURITY")
        x(f"ALTER TABLE {t} FORCE ROW LEVEL SECURITY")
        x(f"CREATE POLICY tenant_boundary ON {t} USING (tenant_id::text=current_setting('app.tenant_id',true)) WITH CHECK (tenant_id::text=current_setting('app.tenant_id',true))")
    for table,links in TABLES.items():
        for field,target in links:
            name=(table+"_"+field+"_same_tenant")[:63]
            x(f"ALTER TABLE {q(table)} ADD CONSTRAINT {q(name)} FOREIGN KEY ({q(field+'_id')},tenant_id) REFERENCES {q(target)} (id,tenant_id) DEFERRABLE INITIALLY DEFERRED")

def unprotect(apps,schema_editor):
    if schema_editor.connection.vendor!="postgresql":return
    q=schema_editor.quote_name;x=schema_editor.execute
    for table,links in reversed(list(TABLES.items())):
        t=q(table)
        for field,_ in links:x(f"ALTER TABLE {t} DROP CONSTRAINT IF EXISTS {q((table+'_'+field+'_same_tenant')[:63])}")
        x(f"DROP POLICY IF EXISTS tenant_boundary ON {t}")
        x(f"ALTER TABLE {t} DISABLE ROW LEVEL SECURITY")
    for table in reversed(list(TABLES)):x(f"ALTER TABLE {q(table)} DROP CONSTRAINT IF EXISTS {q(table+'_id_tenant')}")


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0049_sale_due"),
    ]

    operations = [
        migrations.AddField(
            model_name="tenant",
            name="wa_when",
            field=models.CharField(blank=True, max_length=4),
        ),
        migrations.AddField(
            model_name="tenant",
            name="whatsapp_reminder",
            field=models.TextField(blank=True),
        ),
        migrations.CreateModel(
            name="GiftCard",
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
                ("code", models.CharField(max_length=12)),
                ("amount", models.DecimalField(decimal_places=2, max_digits=8)),
                ("balance", models.DecimalField(decimal_places=2, max_digits=8)),
                ("expires", models.DateField(blank=True, null=True)),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("pending", "Da emettere"),
                            ("active", "Attiva"),
                            ("void", "Annullata"),
                        ],
                        default="pending",
                        max_length=8,
                    ),
                ),
                ("created", models.DateTimeField(auto_now_add=True)),
                (
                    "contact",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="gift_cards",
                        to="core.contact",
                    ),
                ),
                (
                    "sale",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="gift_cards",
                        to="core.sale",
                    ),
                ),
                (
                    "tenant",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT, to="core.tenant"
                    ),
                ),
            ],
        ),
        migrations.CreateModel(
            name="GiftMove",
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
                ("cents", models.IntegerField()),
                ("created", models.DateTimeField(auto_now_add=True)),
                (
                    "card",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="moves",
                        to="core.giftcard",
                    ),
                ),
                (
                    "sale",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="gift_moves",
                        to="core.sale",
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
        migrations.CreateModel(
            name="Parked",
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
                    "total",
                    models.DecimalField(decimal_places=2, default=0, max_digits=9),
                ),
                ("key", models.CharField(blank=True, max_length=66)),
                ("actor", models.CharField(blank=True, max_length=36)),
                ("pii", models.TextField()),
                ("created", models.DateTimeField(auto_now_add=True)),
                (
                    "booking",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="parked",
                        to="core.booking",
                    ),
                ),
                (
                    "contact",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="parked",
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
            ],
            options={
                "abstract": False,
            },
        ),
        migrations.CreateModel(
            name="Product",
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
                ("name", models.CharField(max_length=80)),
                (
                    "price",
                    models.DecimalField(
                        blank=True, decimal_places=2, max_digits=8, null=True
                    ),
                ),
                ("active", models.BooleanField(default=True)),
                ("created", models.DateTimeField(auto_now_add=True)),
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
        migrations.CreateModel(
            name="Quote",
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
                ("year", models.PositiveSmallIntegerField()),
                ("number", models.PositiveIntegerField()),
                (
                    "total",
                    models.DecimalField(decimal_places=2, default=0, max_digits=9),
                ),
                ("valid_until", models.DateField(blank=True, null=True)),
                ("actor", models.CharField(blank=True, max_length=36)),
                ("pii", models.TextField()),
                ("created", models.DateTimeField(auto_now_add=True)),
                ("updated", models.DateTimeField(auto_now=True)),
                (
                    "contact",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="quotes",
                        to="core.contact",
                    ),
                ),
                (
                    "tenant",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT, to="core.tenant"
                    ),
                ),
            ],
        ),
        migrations.AddConstraint(
            model_name="giftcard",
            constraint=models.UniqueConstraint(
                fields=("tenant", "code"), name="giftcard_code"
            ),
        ),
        migrations.AddConstraint(
            model_name="quote",
            constraint=models.UniqueConstraint(
                fields=("tenant", "year", "number"), name="quote_number"
            ),
        ),
        migrations.AddConstraint(
            model_name="parked",
            constraint=models.UniqueConstraint(fields=("tenant", "key"), name="parked_key"),
        ),
        migrations.RunPython(protect,unprotect),
    ]
