import time
from django.core.management.base import BaseCommand
from core.mail import worker_once
class Command(BaseCommand):
    help="Elabora la coda email; --once per un solo ciclo."
    def add_arguments(self,p):p.add_argument("--once",action="store_true")
    def handle(self,*a,**kw):
        last_refresh=last_remind=0
        while True:
            if time.time()-last_refresh>3600:
                from django.core.management import call_command
                call_command("refresh_turns");last_refresh=time.time()
                from core.privacy import run_retention  # 2.14.0: every business once a day, without a cron
                if done:=run_retention():self.stdout.write(f"Conservazione dei dati applicata a {done} attività.")
                from core.stays import run as calendars  # 2.14.0: the portals' calendars of each unit (Airbnb reads ours every ~3 hours)
                if done:=calendars():self.stdout.write(f"Calendari dei portali letti: {done} alloggi.")
            if time.time()-last_remind>300:
                # 2.13.0: the reminders to the clients, every 5 minutes; 2.14.0: the other timed messages and the evening list
                from core.client_mails import remind
                from core.notify import evening
                if remind():self.stdout.write("Messaggi accodati ai clienti.")
                if evening():self.stdout.write("Riepiloghi serali accodati.")
                last_remind=time.time()
            from core.waitlist import run as waitlist  # 2.14.0: places freed, offered to the waiting list; kept places that expire
            if waitlist():self.stdout.write("Avvisi della lista d'attesa accodati.")
            result=worker_once()
            from core.trial import notify_pending
            if notify_pending():self.stdout.write("Richieste di prova inoltrate all'agenzia.")
            if any(result.values()):self.stdout.write(str(result))
            if kw["once"]:break
            time.sleep(10)
