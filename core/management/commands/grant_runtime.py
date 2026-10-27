import os
from django.core.management.base import BaseCommand,CommandError
from django.db import connection
ROLE="tdbooking_app"
class Command(BaseCommand):
    help="Da ruolo migrazioni: crea se manca il ruolo runtime e gli assegna solo DML."
    def handle(self,*a,**kw):
        if connection.vendor!="postgresql":raise CommandError("Richiede PostgreSQL.")
        password=os.environ.get("APP_DB_PASSWORD","")
        with connection.cursor() as c:
            c.execute("SELECT rolsuper,rolbypassrls FROM pg_roles WHERE rolname=%s",[ROLE])
            row=c.fetchone()
            # deploy/init-db.sh normally creates the role; if it never ran (unreadable script, volume from an
            # interrupted first start) the migration role creates it here, with the same attributes.
            if row is None:
                if not password:raise CommandError(f"Il ruolo {ROLE} non esiste: imposta APP_DB_PASSWORD in .env e rilancia.")
                c.execute("SELECT format('CREATE ROLE %%I LOGIN PASSWORD %%L NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS',%s,%s)",[ROLE,password])
                c.execute(c.fetchone()[0])
                self.stdout.write(f"Ruolo {ROLE} creato senza privilegi.")
            elif any(row):raise CommandError(f"Il ruolo {ROLE} non deve essere superuser/BYPASSRLS.")
            elif password:
                # .env is the source of truth: a password changed there after the first start is applied here
                c.execute("SELECT format('ALTER ROLE %%I PASSWORD %%L',%s,%s)",[ROLE,password]);c.execute(c.fetchone()[0])
            c.execute("SELECT format('GRANT CONNECT ON DATABASE %%I TO %%I',current_database(),%s)",[ROLE]);c.execute(c.fetchone()[0])
            c.execute("GRANT USAGE ON SCHEMA public TO tdbooking_app")
            c.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO tdbooking_app")
            c.execute("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO tdbooking_app")
            c.execute("REVOKE UPDATE, DELETE ON core_audit FROM tdbooking_app")
            c.execute("REVOKE UPDATE, DELETE ON core_consentevent FROM tdbooking_app")
            c.execute("REVOKE INSERT, UPDATE, DELETE ON django_migrations FROM tdbooking_app")
        self.stdout.write("Permessi runtime configurati.")
