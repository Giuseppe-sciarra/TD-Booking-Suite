from django.core.management.base import BaseCommand
from django.db import transaction
from core.models import Tenant
from core.tenancy import scope
from core.shifts import extend_rules
class Command(BaseCommand):
    help="Estende l'orizzonte dei turni ricorrenti senza modificare quelli esistenti."
    def handle(self,*a,**kw):
        for tenant in Tenant.objects.all():
            with scope(tenant):
                Tenant.objects.select_for_update().get(pk=tenant.pk)
                extend_rules(tenant)
        self.stdout.write("Turni aggiornati.")
