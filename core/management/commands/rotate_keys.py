from django.core.management.base import BaseCommand
from django.db import transaction
from core.models import User,Tenant
from core.crypto import protect,reveal
class Command(BaseCommand):
    help="Ricifra chiavi tenant e identità con TDBS_ACTIVE_KEY."
    def handle(self,*a,**kw):
        count=0
        for tenant in Tenant.objects.all().iterator():
            with transaction.atomic():
                t=Tenant.objects.select_for_update().get(pk=tenant.pk)
                t.wrapped_key=protect(reveal(t.wrapped_key,"tenant-key:"+str(t.pk)),"tenant-key:"+str(t.pk));t.save(update_fields=["wrapped_key"]);count+=1
        for user in User.objects.all().iterator():
            with transaction.atomic():
                u=User.objects.select_for_update().get(pk=user.pk)
                u.pii=protect(reveal(u.pii,"user:"+str(u.pk)),"user:"+str(u.pk))
                if u.totp:u.totp=protect(reveal(u.totp,"totp:"+str(u.pk)),"totp:"+str(u.pk))
                u.save(update_fields=["pii","totp"])
        self.stdout.write(f"Rotazione applicata a {count} attività. Conservare vecchie chiavi per sessioni e backup.")
