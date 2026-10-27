from django.core.management.base import BaseCommand,CommandError
from django.db import connection
from django.conf import settings
from core.rls import tenant_tables,read_catalog,problems,pending_migrations
def private_problem():
    """2.4.0: switching a Cloud portal to Private keeps every business but would show only one of them."""
    if settings.EDITION!="private":return
    from core.models import Tenant
    n=Tenant.objects.count()
    if n>1:raise CommandError(f"TDBS_EDITION=private gestisce una sola attività, ma questo database ne ha {n}. "
        "Rimetti TDBS_EDITION=cloud nel file .env e lancia docker compose up -d: nessun dato è stato toccato.")
class Command(BaseCommand):
    help="Rifiuta runtime PostgreSQL privilegiato o protezioni tenant mancanti."
    def handle(self,*a,**kw):
        if settings.DEV:return
        if connection.vendor!="postgresql":raise CommandError("In produzione serve PostgreSQL.")
        pending=pending_migrations(connection)
        if pending:raise CommandError("Il database non è aggiornato a questa versione: mancano le migrazioni "+", ".join(pending)+". Lancia: docker compose --profile tools run --rm migrate")
        private_problem()
        with connection.cursor() as c:
            c.execute("SELECT rolsuper,rolbypassrls FROM pg_roles WHERE rolname=current_user")
            if any(c.fetchone()):raise CommandError("Il ruolo runtime non deve essere superuser/BYPASSRLS.")
            expected=tenant_tables()
            found=problems(read_catalog(c,expected),expected)
            c.execute("SELECT current_setting('app.tenant_id',true)")
            if (c.fetchone()[0] or ""):found.append("La connessione di avvio ha già un tenant impostato.")
        if found:raise CommandError("Isolamento tenant incompleto:\n- "+"\n- ".join(found))
        self.stdout.write(f"Controlli runtime superati su {len(expected)} tabelle tenant.")
