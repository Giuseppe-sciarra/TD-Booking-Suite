"""2.6.0: the business profile, type of activity and locations, applied in one go.

Choosing "Pizzeria with two locations" rebuilds the catalog for that type: services with realistic
durations and seats, the operators or rooms it needs, opening hours and, for events, the first dates.
Bookings are never touched: anything they point to is switched off instead of deleted, so the
history stays readable. The booking page and the WordPress widget read the catalog live, so they
change at the same moment.
"""
from datetime import datetime,time,timedelta
from zoneinfo import ZoneInfo
from django.db import transaction,IntegrityError
from django.db.models import ProtectedError
from django.utils import timezone
from .models import Location,Resource,Service,ServiceResource,Option,Schedule,ExceptionDay,Occurrence,ShiftRule,audit
from .presets import PRESETS,initial
from .tenancy import scope

MAX_LOCATIONS=50
DEFAULT_NAMES=["Sede principale","Seconda sede","Terza sede","Quarta sede","Quinta sede"]
def default_name(i):
    """Name proposed for the location in position i (0-based): Sede 6, Sede 7... after the fifth."""
    return DEFAULT_NAMES[i] if i<len(DEFAULT_NAMES) else f"Sede {i+1}"
WEEK=range(7);TUE_SUN=range(1,7);MON_FRI=range(5);MON_SAT=range(6);TUE_SAT=range(1,6)

# services (name, changes to the preset), units for appointments and stays, opening hours, event dates
PROFILES={
 "ristorante":{"services":[("Tavolo",{"capacity":40})],"hours":[(TUE_SUN,"12:00","15:00"),(TUE_SUN,"19:00","23:30")]},
 "parrucchiere":{"services":[("Taglio",{"duration":30}),("Piega",{"duration":45}),("Colore",{"duration":90})],"units":2,"hours":[(TUE_SAT,"09:00","19:00")]},
 "medico":{"services":[("Prima visita",{"duration":60}),("Visita di controllo",{"duration":30})],"units":2,"hours":[(MON_FRI,"09:00","13:00"),(MON_FRI,"15:00","19:00")]},
 "consulenza":{"services":[("Consulenza",{"duration":60}),("Prima chiamata conoscitiva",{"duration":30})],"units":2,"hours":[(MON_FRI,"09:00","18:00")]},
 "centro_estetico":{"services":[("Manicure",{"duration":45}),("Pulizia del viso",{"duration":60}),("Ceretta",{"duration":30})],"units":2,"hours":[(TUE_SAT,"09:30","19:30")]},
 "spa":{"services":[("Massaggio 50 minuti",{"duration":50}),("Trattamento viso",{"duration":60})],"units":2,"hours":[(WEEK,"10:00","20:00")]},
 "tattoo":{"services":[("Consulenza per il tatuaggio",{"duration":30}),("Seduta",{"duration":180})],"units":2,"hours":[(TUE_SAT,"11:00","19:00")]},
 "autoscuola":{"services":[("Lezione di guida",{"duration":60})],"units":2,"hours":[(MON_SAT,"08:00","19:00")]},
 "campo":{"services":[("Campo per un'ora",{"duration":60}),("Campo per un'ora e mezza",{"duration":90})],"units":2,"hours":[(WEEK,"08:00","23:00")]},
 "corso":{"services":[("Lezione",{})],"hours":[(MON_FRI,"09:00","10:00"),(MON_FRI,"18:00","21:00"),(range(5,6),"10:00","12:00")]},
 "officina":{"services":[("Tagliando",{"duration":60}),("Cambio gomme",{"duration":30})],"hours":[(MON_FRI,"08:00","12:30"),(MON_FRI,"14:00","18:00")]},
 "hotel":{"services":[("Camera",{})],"units":3,"day":True},
 "casa_vacanze":{"services":[("Appartamento",{})],"units":2,"day":True},
 "noleggio":{"services":[("Noleggio bici",{})],"units":4,"day":True},
 "evento":{"services":[("Serata",{})],"events":{"weekdays":[5],"start":"21:00","end":"23:30","weeks":8}},
 "tour":{"services":[("Visita guidata",{})],"events":{"weekdays":[5,6],"start":"10:00","end":"12:00","weeks":6}},
}

def hhmm(v):h,m=v.split(":");return time(int(h),int(m))

def retire(queryset):
    """Delete what no booking points to; switch off the rest. Returns how many were kept switched off."""
    kept=0
    for obj in list(queryset):
        try:
            with transaction.atomic():obj.delete()
        except (ProtectedError,IntegrityError):
            if hasattr(obj,"active") and obj.active:
                type(obj).objects.filter(pk=obj.pk).update(active=False)
            kept+=1
    return kept

def remove(kind,obj):
    """2.6.0: Elimina from the catalog lists. What bookings point to is switched off instead, with the things
    that only exist for it: a location takes its services and resources along. Returns how many were kept off."""
    kept=0
    if kind=="services":
        Schedule.objects.filter(service=obj).delete();ExceptionDay.objects.filter(service=obj).delete()
        for rule in ShiftRule.objects.filter(service=obj):kept+=retire(Occurrence.objects.filter(rule=rule))
        kept+=retire(Occurrence.objects.filter(service=obj))+retire(ShiftRule.objects.filter(service=obj))+retire(Option.objects.filter(service=obj))
        ServiceResource.objects.filter(service=obj).delete()
    elif kind=="resources":
        Schedule.objects.filter(resource=obj).delete();ExceptionDay.objects.filter(resource=obj).delete()
        ServiceResource.objects.filter(resource=obj).delete()
    elif kind=="locations":
        for s in Service.objects.filter(location=obj):kept+=remove("services",s)
        for r in Resource.objects.filter(location=obj):kept+=remove("resources",r)
        Schedule.objects.filter(location=obj).delete();ExceptionDay.objects.filter(location=obj).delete()
    elif kind=="turns":
        kept+=retire(Occurrence.objects.filter(rule=obj))
    return kept+retire(type(obj).objects.filter(pk=obj.pk))

def describe(tenant):
    """The current profile, for the setup page and the client card."""
    with scope(tenant):
        locations=list(Location.objects.filter(active=True).order_by("name"))
        services=Service.objects.filter(active=True).count()
    p=PRESETS.get(tenant.business_type)
    return {"type":tenant.business_type,"label":p["label"] if p else "Personalizzato","locations":locations,"count":len(locations),"services":services}

def clean_names(names,count):
    count=max(1,min(MAX_LOCATIONS,int(count)))
    out=[]
    for i in range(count):
        n=(names[i] if i<len(names) else "").strip()[:100] or default_name(i)
        out.append(n)
    if len({n.casefold() for n in out})<len(out):raise ValueError("Dai un nome diverso a ogni sede.")
    return out

@transaction.atomic
def apply_profile(tenant,preset,names,actor=None):
    if preset not in PRESETS:raise ValueError("Scegli un tipo di attività.")
    p=PRESETS[preset];spec=PROFILES[preset];tz=ZoneInfo(tenant.timezone)
    with scope(tenant):
        # 1. the old catalog: hours and anything tied to services and resources
        Schedule.objects.all().delete()
        ExceptionDay.objects.exclude(service=None,resource=None).delete()
        archived=retire(Occurrence.objects.all())+retire(ShiftRule.objects.all())+retire(Option.objects.all())
        ServiceResource.objects.all().delete()
        archived+=retire(Service.objects.all())+retire(Resource.objects.all())
        # 2. locations: the existing ones are kept and renamed, in order; the extra ones go
        current=list(Location.objects.order_by("-active","name"))
        locations=[]
        for i,name in enumerate(names):
            if i<len(current):
                loc=current[i];loc.name=name;loc.active=True;loc.save(update_fields=["name","active"])
            else:loc=Location.objects.create(tenant=tenant,name=name)
            locations.append(loc)
        extra=Location.objects.exclude(pk__in=[l.pk for l in locations])
        ExceptionDay.objects.filter(location__in=extra).delete()
        archived+=retire(extra)
        # 3. the new catalog, the same in every location
        base=initial(preset);base.pop("preset",None)
        label=p["resource_label"] or "Risorsa"
        created={"services":0,"resources":0,"dates":0}
        for loc in locations:
            units=[]
            for n in range(1,spec.get("units",0)+1):
                units.append(Resource.objects.create(tenant=tenant,location=loc,name=f"{label} {n}",capacity=p["defaults"].get("max_party",0) if spec.get("day") else 0))
            created["resources"]+=len(units)
            for name,changes in spec["services"]:
                s=Service.objects.create(tenant=tenant,location=loc,name=name,preset=preset,lead_minutes=0,**{**base,**changes})
                created["services"]+=1
                for u in units:ServiceResource.objects.create(tenant=tenant,service=s,resource=u)
                ev=spec.get("events")
                if ev:
                    today=timezone.now().astimezone(tz).date()
                    for d in range(1,ev["weeks"]*7+1):
                        day=today+timedelta(days=d)
                        if day.weekday() in ev["weekdays"]:
                            start=datetime.combine(day,hhmm(ev["start"]),tz);end=datetime.combine(day,hhmm(ev["end"]),tz)
                            Occurrence.objects.create(tenant=tenant,service=s,start=start,end=end,capacity=s.capacity);created["dates"]+=1
        # 4. opening hours for the whole business; stays and events are open every day
        hours=spec.get("hours") or [(WEEK,"00:00",None)]
        for days,start,end in hours:
            for d in days:
                Schedule.objects.create(tenant=tenant,weekday=d,start=hhmm(start),end=hhmm(end) if end else time(23,59,59,999999))
        tenant.business_type=preset;tenant.save(update_fields=["business_type"])
        audit(tenant,actor,"tenant.profile",tenant.pk,type=preset,locations=len(locations),archived=archived)
    return {**created,"locations":len(locations),"archived":archived,"label":p["label"],"type":preset}
