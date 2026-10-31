# 2.14.0: the waiting list (in a table of its own, isolated per business), the client card (VIP, not bookable online,
# keyed indexes of phone, name, tags and birthday that find every card without opening it, merged cards), the rules of
# the business (absences that make a request, waiting list mode) and the filters of a campaign

import django.db.models.deletion
import uuid
from django.db import migrations, models

TABLE="core_waitentry"
LINKS=[("contact","core_contact"),("resource","core_resource"),("service","core_service")]
MERGED=("core_contact_merged_into_same_tenant","core_contact")

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
    # a card is merged only into a card of the same business
    x(f"ALTER TABLE {q(MERGED[1])} ADD CONSTRAINT {q(MERGED[0])} FOREIGN KEY (merged_into_id,tenant_id) REFERENCES {q(MERGED[1])} (id,tenant_id) DEFERRABLE INITIALLY DEFERRED")

def unprotect(apps,schema_editor):
    if schema_editor.connection.vendor!="postgresql":return
    q=schema_editor.quote_name;t=q(TABLE);x=schema_editor.execute
    x(f"ALTER TABLE {q(MERGED[1])} DROP CONSTRAINT IF EXISTS {q(MERGED[0])}")
    for field,_ in LINKS:x(f"ALTER TABLE {t} DROP CONSTRAINT IF EXISTS {q((TABLE+'_'+field+'_same_tenant')[:63])}")
    x(f"DROP POLICY IF EXISTS tenant_boundary ON {t}")
    x(f"ALTER TABLE {t} DISABLE ROW LEVEL SECURITY")
    x(f"ALTER TABLE {t} DROP CONSTRAINT IF EXISTS {q(TABLE+'_id_tenant')}")

def indexes(apps,schema_editor):
    """The keyed indexes of the cards that exist, each business with its own key (and its own RLS context)."""
    from core.crypto import tenant_key,open_seal
    from core.clients import stamp,search_keys,INDEXES
    Tenant=apps.get_model("core","Tenant");Contact=apps.get_model("core","Contact")
    for t in Tenant.objects.all():
        if schema_editor.connection.vendor=="postgresql":
            with schema_editor.connection.cursor() as c:c.execute("SELECT set_config('app.tenant_id',%s,true)",[str(t.pk)])
        key=tenant_key(t);h=search_keys(key);rows=[]
        for c in Contact.objects.filter(tenant_id=t.pk).exclude(email_index__startswith="erased:").iterator(chunk_size=500):
            stamp(c,open_seal(c.pii,key,f"{t.pk}:contact:{c.pk}"),key,h);rows.append(c)
        Contact.objects.bulk_update(rows,INDEXES,batch_size=500)

class Migration(migrations.Migration):

    dependencies = [
        ("core", "0028_closures_holidays_blocks_series"),
    ]

    operations = [
        migrations.AddField(
            model_name="campaign",
            name="filters",
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AddField(
            model_name="contact",
            name="birthday_index",
            field=models.CharField(blank=True, db_index=True, max_length=16),
        ),
        migrations.AddField(
            model_name="contact",
            name="merged_into",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="merged",
                to="core.contact",
            ),
        ),
        migrations.AddField(
            model_name="contact",
            name="name_index",
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name="contact",
            name="online_blocked",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="contact",
            name="phone_index",
            field=models.CharField(blank=True, db_index=True, max_length=64),
        ),
        migrations.AddField(
            model_name="contact",
            name="tag_index",
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name="contact",
            name="vip",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="tenant",
            name="absences_request",
            field=models.PositiveSmallIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="tenant",
            name="wait_check",
            field=models.DateTimeField(blank=True, db_index=True, null=True),
        ),
        migrations.AddField(
            model_name="tenant",
            name="wait_minutes",
            field=models.PositiveSmallIntegerField(default=60),
        ),
        migrations.AddField(
            model_name="tenant",
            name="wait_mode",
            field=models.CharField(
                choices=[
                    ("all", "Avvisa tutti, vince chi prenota prima"),
                    ("queue", "Uno alla volta, in ordine di iscrizione"),
                ],
                default="all",
                max_length=5,
            ),
        ),
        migrations.AlterField(
            model_name="consentevent",
            name="kind",
            field=models.CharField(
                choices=[
                    ("requested", "Richiesto"),
                    ("confirmed", "Confermato"),
                    ("revoked", "Revocato"),
                    ("suppressed", "Escluso dagli invii"),
                    ("erased", "Dati cancellati"),
                    ("imported", "Importato"),
                    ("merged", "Schede unite"),
                ],
                max_length=12,
            ),
        ),
        migrations.CreateModel(
            name="WaitEntry",
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
                ("first", models.DateField()),
                ("last", models.DateField()),
                ("time_from", models.TimeField(blank=True, null=True)),
                ("time_to", models.TimeField(blank=True, null=True)),
                ("party", models.PositiveIntegerField(default=1)),
                (
                    "status",
                    models.CharField(
                        choices=[("waiting", "In attesa"), ("done", "Fatta")],
                        default="waiting",
                        max_length=8,
                    ),
                ),
                ("due", models.BooleanField(default=False)),
                (
                    "held_until",
                    models.DateTimeField(blank=True, db_index=True, null=True),
                ),
                ("pii", models.TextField()),
                ("snapshot", models.JSONField(default=dict)),
                ("created", models.DateTimeField(auto_now_add=True)),
                (
                    "contact",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="waits",
                        to="core.contact",
                    ),
                ),
                (
                    "resource",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        to="core.resource",
                    ),
                ),
                (
                    "service",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE, to="core.service"
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
                "indexes": [
                    models.Index(
                        fields=["tenant", "status", "last"],
                        name="core_waiten_tenant__74b558_idx",
                    )
                ],
            },
        ),
        migrations.RunPython(protect,unprotect),
        migrations.RunPython(indexes,migrations.RunPython.noop),
    ]
