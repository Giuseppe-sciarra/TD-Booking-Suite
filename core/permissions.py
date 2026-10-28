"""Server-side permissions: role -> capabilities, plus location/resource scope.

Platform staff no longer bypass tenant checks: without a support grant they
reach only technical pages; with a grant they act as "support", every request
is audited and recent MFA is required.
"""
import time
from django.core.exceptions import PermissionDenied
from django.utils import timezone

PERMS={
 "booking.read":"Vedere l'agenda",
 "booking.write":"Creare e spostare prenotazioni",
 "booking.cancel":"Annullare e chiudere prenotazioni",
 "pii.read":"Vedere nomi e recapiti",
 "notes.read":"Vedere note del cliente e note interne",
 "contacts.read":"Vedere l'anagrafica clienti",
 "catalog.write":"Modificare servizi e disponibilità",
 "export.operational":"Esportare le prenotazioni",
 "export.marketing":"Esportare i contatti",
 "campaign.manage":"Preparare e inviare campagne",
 "users.manage":"Gestire utenti e accessi di assistenza",
 "audit.read":"Leggere il registro attività",
 "privacy.manage":"Gestire richieste privacy (accesso e cancellazione dei dati)",
}
ALL=frozenset(PERMS)
ROLES={
 "owner":("Amministratore",ALL),
 "manager":("Amministratore sede",frozenset({"booking.read","booking.write","booking.cancel","pii.read","notes.read","contacts.read","export.operational"})),
 "reception":("Reception",frozenset({"booking.read","booking.write","booking.cancel","pii.read","notes.read","contacts.read"})),
 "operator":("Operatore sede",frozenset({"booking.read","booking.write","pii.read","notes.read"})),
 "marketing":("Marketing",frozenset({"contacts.read","pii.read","campaign.manage","export.marketing"})),
 "viewer":("Consultazione",frozenset({"booking.read"})),
}
def role_label(role):
    """The role's name; an edition can rename some (ROLE_LABELS), the Windows one calls the owner Titolare."""
    from django.conf import settings
    return getattr(settings,"ROLE_LABELS",{}).get(role) or ROLES.get(role,(role,))[0]
ROLE_CHOICES=[(k,role_label(k)) for k in ROLES]
SUPPORT=frozenset({"booking.read","booking.write","booking.cancel","pii.read","notes.read","contacts.read","catalog.write","audit.read"})
SUPPORT_MFA_SECONDS=900

def effective(request):
    cached=getattr(request,"_perms",None)
    if cached is not None:return cached
    m=getattr(request,"member",None);grant=getattr(request,"support",None)
    if m:
        perms=set(ROLES.get(m.role,("",frozenset()))[1])
        # Catalog editing stays an explicit flag, as before, for any role that writes.
        if m.can_schedule and m.role not in ("viewer","marketing"):perms.add("catalog.write")
        # the agency configures every business, also one it works in with a lower role
        if getattr(getattr(request,"user",None),"is_staff",False):perms.add("catalog.write")
    elif grant:perms=set(SUPPORT)
    elif getattr(getattr(request,"user",None),"is_staff",False):
        # 2.6.0: the agency configures every business (locations, hours, services): no customer data involved
        perms={"catalog.write"}
    else:perms=set()
    request._perms=frozenset(perms)
    return request._perms

def can(request,perm):return perm in effective(request)

def require(request,*perms):
    missing=[p for p in perms if not can(request,p)]
    if missing:raise PermissionDenied("Il tuo ruolo non consente: "+", ".join(PERMS[p].lower() for p in missing)+".")

def scoped_to_assigned(request):
    m=getattr(request,"member",None)
    return bool(m and m.role=="operator")

def visible_bookings(request,qs):
    """Bookings the current user may see, before any field-level masking."""
    if not can(request,"booking.read"):return qs.none()
    m=getattr(request,"member",None)
    if not m:return qs
    if m.role=="operator":
        # an operator works on his resources, or on his location when the business has no personal resources
        if m.resources:return qs.filter(resource_id__in=m.resources)
        return qs.filter(location_id__in=m.locations or [])
    if m.locations:qs=qs.filter(location_id__in=m.locations)
    if m.resources:qs=qs.filter(resource_id__in=m.resources)
    return qs

def check_new_booking(request,service,resource_id):
    """Manual bookings stay inside the creator's own scope."""
    m=getattr(request,"member",None)
    if not m:return
    if m.locations and str(service.location_id) not in m.locations:raise PermissionDenied("Sede fuori dal tuo ambito.")
    if m.resources:
        if not resource_id or str(resource_id) not in (m.resources or []):raise PermissionDenied("Puoi prenotare solo sulle risorse assegnate.")

def initials(value):
    value=(value or "").strip()
    return value[:1].upper()+"." if value else ""

def shown(request,data):
    """Field-level view of booking/contact data for the current role."""
    data=dict(data or {})
    if not can(request,"pii.read"):
        for key in ("first_name","last_name"):data[key]=initials(data.get(key))
        for key in ("email","phone"):data[key]="•••" if data.get(key) else ""
        data["answers"]={};data["masked"]=True
    if not can(request,"notes.read"):
        # allergies are health data: they go with the notes (2.8.0)
        data.pop("notes",None);data.pop("internal_notes",None);data.pop("allergies",None);data["notes_hidden"]=True
    data.pop("cancel_token",None)
    return data

def active_grant(tenant,user):
    from .models import SupportGrant
    return SupportGrant.objects.filter(tenant=tenant,user=user,revoked=False,expires__gt=timezone.now()).order_by("-expires").first()

def support_mfa_fresh(request):
    return request.session.get("mfa") and time.time()-request.session.get("verified_at",0)<=SUPPORT_MFA_SECONDS
