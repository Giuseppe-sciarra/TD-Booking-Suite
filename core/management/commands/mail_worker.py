import time
from django.core.management.base import BaseCommand
from core.mail import worker_once
class Command(BaseCommand):
    help="Elabora la coda email; --once per un solo ciclo."
    def add_arguments(self,p):p.add_argument("--once",action="store_true")
    def handle(self,*a,**kw):
        last_refresh=0
        while True:
            if time.time()-last_refresh>3600:
                from django.core.management import call_command
                call_command("refresh_turns");last_refresh=time.time()
            result=worker_once()
            from core.trial import notify_pending
            if notify_pending():self.stdout.write("Richieste di prova inoltrate all'agenzia.")
            if any(result.values()):self.stdout.write(str(result))
            if kw["once"]:break
            time.sleep(10)
