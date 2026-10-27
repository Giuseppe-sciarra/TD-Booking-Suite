"""2.8.0: the summary of the nightly update (scripts/aggiorna-tutto.sh), emailed to the agency with the
portal's own mail settings. The text arrives on stdin; the answer is one JSON line for the script."""
import json,secrets,smtplib,ssl,sys
from django.conf import settings
from django.core.management import BaseCommand
OUTCOMES={"ok":"aggiornamento notturno riuscito","rollback":"aggiornamento annullato, resta la versione di prima",
          "errore":"aggiornamento notturno non riuscito","nessuno":"era già tutto aggiornato"}

class Command(BaseCommand):
    help="Manda all'agenzia il riepilogo dell'aggiornamento notturno, letto dallo standard input."
    def add_arguments(self,p):
        p.add_argument("--esito",choices=sorted(OUTCOMES),default="ok")
        p.add_argument("--a",default="",help="destinatario; vuoto = email delle richieste di prova o di assistenza")
    def handle(self,*a,**kw):
        from core.models import SiteSettings
        from core.site import agency_smtp
        from core.trial import recipient
        from core.mail import send_message
        from core.mailapi import ApiError
        site=SiteSettings.current();to=kw["a"].strip() or recipient(site);config=agency_smtp(site)
        if not to:return self.answer(False,"",msg="nessun destinatario: imposta l'email in Impostazioni generali o TDBS_AGG_EMAIL")
        if not config:return self.answer(False,to,msg="invio email dell'agenzia non configurato")
        body=sys.stdin.read()[:20000]
        try:
            send_message(config,{"to":to,"subject":f"{site.name}: {OUTCOMES[kw['esito']]}","body":body},
                         f"<update-{secrets.token_hex(8)}@{settings.PUBLIC_ORIGIN.split('//')[-1]}>")
        except (OSError,smtplib.SMTPException,ssl.SSLError,ValueError,ApiError) as ex:
            return self.answer(False,to,msg=str(ex)[:200])
        self.answer(True,to)
    def answer(self,ok,to,msg=""):
        self.stdout.write(json.dumps({"ok":ok,"a":to,"msg":msg}))
