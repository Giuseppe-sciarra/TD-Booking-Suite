"""2.13.0: Entra senza password. An owner the agency allows (Persone e permessi) signs in with one click, only from a
device the edition trusts: on Windows the program's own PC and the registered workstations (TRUSTED_DEVICE).
Anywhere else, and in the Docker edition, which trusts no device, the password as always."""
from django.conf import settings
from django.utils.module_loading import import_string

def trusted(request):
    path=getattr(settings,"TRUSTED_DEVICE","")
    return bool(path) and bool(import_string(path)(request))

def available():
    """The edition has trusted devices: the switch is worth showing."""
    return bool(getattr(settings,"TRUSTED_DEVICE",""))

def owners():
    """Who may come in without password: active owners, never an agency account."""
    from .models import User
    return User.objects.filter(quick_login=True,is_active=True,is_staff=False,membership__role="owner").distinct()

def accounts(request):
    """The buttons of the login page: name and businesses of each, only on a trusted device."""
    if not trusted(request):return []
    from .models import Membership
    out=[]
    for u in owners():
        out.append({"id":str(u.pk),"name":u.login_name,"where":", ".join(m.tenant.name for m in Membership.objects.filter(user=u,role="owner").select_related("tenant").order_by("tenant__name"))})
    return sorted(out,key=lambda a:a["name"])
