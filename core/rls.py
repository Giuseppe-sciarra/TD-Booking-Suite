"""Expected tenant isolation, derived from the models rather than from the database.

The runtime check compares PostgreSQL's catalog with this list, so a tenant
table without RLS, FORCE, policy or tenant key is reported even when it was
added after the security migration.
"""
from django.apps import apps

GLOBAL_MODELS={"membership"}  # identity/control tables with explicit grants

def tenant_tables():
    tables=set()
    for model in apps.get_app_config("core").get_models():
        if model._meta.model_name in GLOBAL_MODELS:continue
        if any(f.name=="tenant" for f in model._meta.fields):tables.add(model._meta.db_table)
    return tables

def pending_migrations(connection):
    """Migrations of this version not yet applied to the database."""
    from django.db.migrations.executor import MigrationExecutor
    executor=MigrationExecutor(connection)
    return [m.app_label+"."+m.name for m,backwards in executor.migration_plan(executor.loader.graph.leaf_nodes()) if not backwards]

def problems(catalog,expected=None):
    """catalog: {table: {"rls":bool,"force":bool,"policy_using":str|None,"policy_check":str|None,"tenant_key":bool}}"""
    expected=tenant_tables() if expected is None else expected
    found=[]
    for table in sorted(expected):
        row=catalog.get(table)
        if row is None:found.append(table+": tabella assente");continue
        if not row.get("rls"):found.append(table+": ROW LEVEL SECURITY non attiva")
        if not row.get("force"):found.append(table+": FORCE ROW LEVEL SECURITY mancante")
        for key,label in (("policy_using","USING"),("policy_check","WITH CHECK")):
            if "app.tenant_id" not in (row.get(key) or ""):found.append(table+f": policy tenant_boundary {label} assente")
        if not row.get("tenant_key"):found.append(table+": vincolo UNIQUE (id, tenant_id) assente")
    return found

def read_catalog(cursor,tables):
    cursor.execute("""
        SELECT c.relname,c.relrowsecurity,c.relforcerowsecurity,
               pg_get_expr(p.polqual,p.polrelid),pg_get_expr(p.polwithcheck,p.polrelid),
               EXISTS(SELECT 1 FROM pg_constraint k WHERE k.conrelid=c.oid AND k.conname=c.relname||'_id_tenant')
        FROM pg_class c
        JOIN pg_namespace n ON n.oid=c.relnamespace AND n.nspname=current_schema()
        LEFT JOIN pg_policy p ON p.polrelid=c.oid AND p.polname='tenant_boundary'
        WHERE c.relkind='r' AND c.relname = ANY(%s)""",[list(tables)])
    return {r[0]:{"rls":r[1],"force":r[2],"policy_using":r[3],"policy_check":r[4],"tenant_key":r[5]} for r in cursor.fetchall()}
