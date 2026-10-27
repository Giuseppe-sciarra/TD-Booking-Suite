from django.db import migrations
def install(apps,schema_editor):
    if schema_editor.connection.vendor!="postgresql":return
    models=[m for m in apps.get_app_config("core").get_models() if m._meta.model_name!="membership" and any(f.name=="tenant" for f in m._meta.fields)]
    tables={m._meta.db_table for m in models};q=schema_editor.quote_name
    for m in models:
        table=m._meta.db_table;t=q(table)
        schema_editor.execute(f"ALTER TABLE {t} ADD CONSTRAINT {q(table+'_id_tenant')} UNIQUE (id,tenant_id)")
        schema_editor.execute(f"ALTER TABLE {t} ENABLE ROW LEVEL SECURITY")
        schema_editor.execute(f"ALTER TABLE {t} FORCE ROW LEVEL SECURITY")
        schema_editor.execute(f"CREATE POLICY tenant_boundary ON {t} USING (tenant_id::text=current_setting('app.tenant_id',true)) WITH CHECK (tenant_id::text=current_setting('app.tenant_id',true))")
    for m in models:
        for f in m._meta.fields:
            r=getattr(f,"remote_field",None)
            if r and r.model._meta.db_table in tables:
                name=(m._meta.db_table+"_"+f.name+"_same_tenant")[:63]
                schema_editor.execute(f"ALTER TABLE {q(m._meta.db_table)} ADD CONSTRAINT {q(name)} FOREIGN KEY ({q(f.column)},tenant_id) REFERENCES {q(r.model._meta.db_table)} (id,tenant_id) DEFERRABLE INITIALLY DEFERRED")
def remove(apps,schema_editor):
    if schema_editor.connection.vendor!="postgresql":return
    models=[m for m in apps.get_app_config("core").get_models() if m._meta.model_name!="membership" and any(f.name=="tenant" for f in m._meta.fields)]
    tables={m._meta.db_table for m in models};q=schema_editor.quote_name
    for m in models:
        for f in m._meta.fields:
            r=getattr(f,"remote_field",None)
            if r and r.model._meta.db_table in tables:schema_editor.execute(f"ALTER TABLE {q(m._meta.db_table)} DROP CONSTRAINT IF EXISTS {q((m._meta.db_table+'_'+f.name+'_same_tenant')[:63])}")
    for m in models:
        t=q(m._meta.db_table)
        schema_editor.execute(f"DROP POLICY tenant_boundary ON {t}")
        schema_editor.execute(f"ALTER TABLE {t} DISABLE ROW LEVEL SECURITY")
        schema_editor.execute(f"ALTER TABLE {t} DROP CONSTRAINT {q(m._meta.db_table+'_id_tenant')}")
class Migration(migrations.Migration):
    dependencies=[("core","0001_initial")]
    operations=[migrations.RunPython(install,remove)]
