from django.conf import settings
def presentation(request):
 from .models import Tenant
 brand=getattr(request,"tenant",None) or (Tenant.objects.first() if settings.EDITION=="private" else None)
 from .permissions import effective
 can=effective(request) if getattr(request,"tenant",None) else frozenset()
 from .site import current
 user=getattr(request,"user",None);mfa_ok=request.session.get("mfa",False) if hasattr(request,"session") else False
 member=getattr(request,"member",None)
 if brand and not getattr(request,"tenant",None) and user and user.is_authenticated:
  # private edition, agency pages: the one business keeps the role of whoever also works in it, not "solo gestione tecnica"
  from types import SimpleNamespace
  from .models import Membership
  member=Membership.objects.filter(tenant=brand,user=user).first()
  if member:can=effective(SimpleNamespace(member=member,support=None,user=user))
 # 2.4.0: the second factor is optional; until it is set up a reminder sits on top of every page
 reminder=settings.MFA_ENABLED and (settings.MFA_REMINDER=="all" or settings.MFA_REMINDER=="staff" and user and user.is_staff)
 missing=bool(reminder and user and user.is_authenticated and mfa_ok and not request.path.startswith(("/security/","/book/","/action/")) and not user.mfa_ready)
 trials=0
 if user and user.is_authenticated and user.is_staff and settings.EDITION=="cloud":
  from .trial import new_count
  trials=new_count()
 return {"trial_new":trials,"mfa_missing":missing,"mfa_enabled":settings.MFA_ENABLED,"usernames":settings.LOGIN_USERNAMES,"site":current(request),"edition":settings.EDITION,"tenant":brand,"member":member,"support":getattr(request,"support",None),"can":can,"mfa_ok":mfa_ok,"app_version":settings.VERSION,"source_url":settings.SOURCE_URL,
         "extra_nav":settings.EXTRA_NAV if user and user.is_authenticated and user.is_staff else (),
         "edition_label":settings.EDITION_LABEL or ("Private" if settings.EDITION=="private" else "Cloud"),
         # how the platform staff are called: the agency here, the program's administrator in an installed edition
         "agency":settings.AGENCY_NAMES,
         # warnings an edition puts on top of the pages: [{"title","text","url","action"}]
         "extra_notices":getattr(request,"extra_notices",()) if mfa_ok else ()}
