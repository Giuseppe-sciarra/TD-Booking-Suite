from django.conf import settings
def presentation(request):
 from .models import Tenant
 brand=getattr(request,"tenant",None) or (Tenant.objects.first() if settings.EDITION=="private" else None)
 from .permissions import effective
 can=effective(request) if getattr(request,"tenant",None) else frozenset()
 from .site import current
 user=getattr(request,"user",None);mfa_ok=request.session.get("mfa",False) if hasattr(request,"session") else False
 # 2.4.0: the second factor is optional; until it is set up a reminder sits on top of every page
 missing=bool(user and user.is_authenticated and mfa_ok and not request.path.startswith(("/security/","/book/","/action/")) and not user.mfa_ready)
 trials=0
 if user and user.is_authenticated and user.is_staff and settings.EDITION=="cloud":
  from .trial import new_count
  trials=new_count()
 return {"trial_new":trials,"mfa_missing":missing,"site":current(request),"edition":settings.EDITION,"tenant":brand,"member":getattr(request,"member",None),"support":getattr(request,"support",None),"can":can,"mfa_ok":mfa_ok,"app_version":settings.VERSION,"source_url":settings.SOURCE_URL}
