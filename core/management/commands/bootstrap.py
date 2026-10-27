import getpass
from django.core.management.base import BaseCommand,CommandError
from django.contrib.auth.password_validation import validate_password
from django.core.validators import validate_email
from core.models import User
from core.crypto import login_index
class Command(BaseCommand):
    help="Crea l'amministratore iniziale; MFA obbligatorio al primo accesso."
    def add_arguments(self,p):p.add_argument("--email",required=True)
    def handle(self,*a,**kw):
        email=kw["email"];validate_email(email)
        if User.objects.filter(login_index=login_index(email)).exists():raise CommandError("Utente esistente.")
        password=getpass.getpass("Password amministratore: ")
        if password!=getpass.getpass("Ripeti password: "):raise CommandError("Le password non coincidono.")
        validate_password(password)
        User.objects.create_superuser(email,password)
        self.stdout.write("Amministratore creato. Accedi e registra Authenticator o passkey.")
