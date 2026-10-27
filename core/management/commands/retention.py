from django.core.management.base import BaseCommand
from django.utils import timezone
from core.models import Tenant,RateBucket,Ceremony
from core.tenancy import scope
from core.privacy import apply_retention
class Command(BaseCommand):
    help=("Applica la conservazione: le prenotazioni oltre il periodo perdono i dati personali e il legame col contatto; "
          "i contatti senza prenotazioni recenti e senza consenso attivo vengono cancellati. Default: sola simulazione.")
    def add_arguments(self,p):
        p.add_argument("--tenant",required=True);p.add_argument("--apply",action="store_true")
    def handle(self,*a,**kw):
        tenant=Tenant.objects.get(slug=kw["tenant"])
        with scope(tenant):report=apply_retention(tenant,apply=kw["apply"])
        if kw["apply"]:
            RateBucket.objects.filter(ends__lt=timezone.now()).delete();Ceremony.objects.filter(expires__lt=timezone.now()).delete()
        verb="Applicato" if kw["apply"] else "Simulazione"
        self.stdout.write(f"{verb}: {report['bookings']} prenotazioni, {report['contacts']} contatti, {report['treatments']} voci della scheda cliente, {report['deliveries']} email.")
