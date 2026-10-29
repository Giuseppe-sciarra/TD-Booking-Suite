import re,time
from datetime import date
from django.contrib.auth import logout
from django.http import HttpResponse
from django.shortcuts import redirect
from django.core.exceptions import PermissionDenied
from .models import Tenant,Membership
from .tenancy import scope

def denied(request,message):
    from django.shortcuts import render
    return render(request,"403.html",{"exception":message},status=403)

TECHNICAL_SECTIONS={"settings","smtp","members","guida","setup","catalog"}

class GuardMiddleware:
    def __init__(self,get_response):self.get_response=get_response
    def process_exception(self,request,exception):
        from .identity import ConfirmNeeded
        if isinstance(exception,ConfirmNeeded):
            from urllib.parse import urlencode
            return redirect("/verify/confirm/?"+urlencode({"next":request.get_full_path()}))
        return None
    def __call__(self,request):
        path=request.path
        try:
            # the client file of the old program can reach 20 MB, every other request stays small
            if int(request.META.get("CONTENT_LENGTH") or 0)>(21000000 if path.endswith("/contacts/import/") else 2200000):return HttpResponse("Richiesta troppo grande.",status=413)
        except ValueError:return HttpResponse(status=400)
        protected=path=="/" or path.startswith(("/t/","/platform/","/security/","/guida/"))
        if request.user.is_authenticated:
            from django.conf import settings
            # 2.6.0: a remembered device stays signed in for SESSION_REMEMBER_DAYS of inactivity (2.13.0: 0 means until
            # Esci), the others for 30 minutes
            idle=settings.SESSION_REMEMBER_DAYS*86400 if request.session.get("remember") else 1800
            if request.session.get("auth_version")!=request.user.auth_version or idle and time.time()-request.session.get("last_seen",time.time())>idle:
                logout(request)
            else:request.session["last_seen"]=time.time()
        if protected:
            if not request.user.is_authenticated:return redirect("login")
            if not request.session.get("mfa") and not request.user.mfa_ready:
                # sessions opened before 2.4.0 by a password-only account: the second factor is now optional
                request.session["mfa"]=True
            if not path.startswith("/security/") and not request.session.get("mfa"):return redirect("security")
        match=re.match(r"^/(t|book|action)/([a-zA-Z0-9_-]+)/",path)
        request.tenant=None;request.member=None;request.support=None
        if match:
            tenant=Tenant.objects.filter(slug=match[2]).first()
            if not tenant:return HttpResponse("Attività non disponibile.",status=404)
            request.tenant=tenant
            if match[1]=="t":
                request.member=Membership.objects.filter(tenant=tenant,user=request.user).first()
                if not request.member and not request.user.is_staff:return denied(request,"Non hai un accesso a questa attività.")
                if not request.member:
                    # Platform staff: technical pages only, content needs the owner's grant.
                    section=path[len(match[0]):].split("/",1)[0]
                    if section not in TECHNICAL_SECTIONS:
                        from .permissions import active_grant,support_mfa_fresh
                        with scope(tenant):grant=active_grant(tenant,request.user)
                        if not grant:return denied(request,"Prenotazioni e clienti di questa attività sono riservati: serve un accesso di assistenza concesso dall'amministratore dell'attività, dalla sua pagina Accesso dell'agenzia.")
                        if not support_mfa_fresh(request):
                            from urllib.parse import urlencode
                            return redirect("/verify/confirm/?"+urlencode({"next":request.get_full_path()}))
                        request.support=grant
                export=path.endswith("/export/")
                if not request.user.is_staff and not export and not operational(tenant):
                    if tenant.maintenance:return HttpResponse("Attività in manutenzione tecnica. Puoi esportare i tuoi dati dal collegamento Esporta.",status=503)
                    return HttpResponse("Servizio sospeso. Puoi esportare i tuoi dati dal collegamento Esporta.",status=402)
            elif match[1]=="book" and not operational(tenant):
                return HttpResponse("Le prenotazioni online non sono disponibili.",status=503 if tenant.maintenance else 402)
            from django.utils import timezone
            with scope(tenant), timezone.override(tenant.timezone):
                if request.support:
                    from .models import audit
                    audit(tenant,request.user,"support.access",request.support.pk,method=request.method,path=path[:200])
                response=self.get_response(request)
        else:response=self.get_response(request)
        public=path.startswith("/book/")
        ancestors="'self'"
        if public and request.tenant:
            ancestors+=" "+" ".join(request.tenant.embed_origins)
            # XFrameOptionsMiddleware is inner; allow only origins in CSP.
            response.headers.pop("X-Frame-Options",None)
        response["Content-Security-Policy"]="default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; font-src 'self'; object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors "+(ancestors if public else "'none'")
        response["Cache-Control"]="no-store, private"
        response["Referrer-Policy"]="same-origin"
        response["Permissions-Policy"]="camera=(), microphone=(), geolocation=()"
        return response

def operational(tenant):
    from django.conf import settings
    from zoneinfo import ZoneInfo
    from django.utils import timezone
    if tenant.maintenance:return False
    if settings.EDITION=="private":return True
    today=timezone.now().astimezone(ZoneInfo(tenant.timezone)).date()
    return tenant.status=="active" or (tenant.status=="trial" and tenant.trial_until is not None and tenant.trial_until>=today)
def require_role(request,write=False,owner=False,schedule=False):
    """Compatibility wrapper over core.permissions; staff no longer bypass it."""
    from .permissions import require
    perms=["booking.write"] if write else []
    if owner:perms.append("users.manage")
    if schedule:perms.append("catalog.write")
    require(request,*(perms or ["booking.read"]))
