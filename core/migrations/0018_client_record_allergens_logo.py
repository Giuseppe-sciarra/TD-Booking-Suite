# 2.8.0: client record (treatments), allergies and intolerances asked by restaurants, shape of the business logo

import io
import django.db.models.deletion
import uuid
from django.db import migrations, models

TABLE="core_treatment"
# the record points to a contact and, optionally, to the booking it comes from: both of the same business
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

def logo_shapes(apps,schema_editor):
    """Logos uploaded before 2.8.0 keep their picture: only their shape is measured."""
    from PIL import Image
    Tenant=apps.get_model("core","Tenant")
    for t in Tenant.objects.exclude(logo=None).only("pk","logo"):
        try:
            im=Image.open(io.BytesIO(bytes(t.logo)))
            Tenant.objects.filter(pk=t.pk).update(logo_ratio=round(im.width/max(im.height,1),3))
        except Exception:pass


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0017_service_turnover"),
    ]

    operations = [
        migrations.AddField(
            model_name="tenant",
            name="allergens",
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.AddField(
            model_name="tenant",
            name="allergens_on",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="tenant",
            name="logo_ratio",
            field=models.FloatField(default=1),
        ),
        migrations.CreateModel(
            name="Treatment",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("date", models.DateField()),
                ("pii", models.TextField()),
                ("author", models.CharField(blank=True, max_length=36)),
                ("created", models.DateTimeField(auto_now_add=True)),
                ("booking", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="treatments", to="core.booking")),
                ("contact", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="treatments", to="core.contact")),
                ("tenant", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to="core.tenant")),
            ],
            options={
                "indexes": [models.Index(fields=["tenant", "contact", "date"], name="core_treatm_tenant__7f5be3_idx")],
            },
        ),
        migrations.RunPython(protect, unprotect),
        migrations.RunPython(logo_shapes, migrations.RunPython.noop),
    ]
