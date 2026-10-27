from django.core.management.base import BaseCommand,CommandError
from django.db import transaction
from core.models import User
from core.crypto import login_index
from core.platform import invitation
class Command(BaseCommand):
    help="Recupero locale amministratore dopo verifica identità fuori banda. Revoca sessioni e MFA."
    def add_arguments(self,p):
        p.add_argument("--email",required=True);p.add_argument("--confirm-reset-mfa",action="store_true")
    def handle(self,*a,**kw):
        if not kw["confirm_reset_mfa"]:raise CommandError("Richiesto --confirm-reset-mfa dopo verifica identità.")
        with transaction.atomic():
            u=User.objects.select_for_update().get(login_index=login_index(kw["email"]),is_staff=True)
            u.auth_version+=1;u.totp="";u.recovery=[];u.set_unusable_password();u.save();u.passkeys.all().delete()
            self.stdout.write(invitation(u))
