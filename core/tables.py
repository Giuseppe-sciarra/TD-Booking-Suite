"""2.14.0: the rooms and tables of a restaurant. The client may ask for a room (Sala, Dehors) or none, never a table;
a room may have its own covers. Tables are given by the staff or suggested: the smallest free one that fits, then the
priority; two tables that can be joined when none is enough. On the floor plan the staff place the tables and see, at
a time, who sits where; a booking or a client walking in goes to a table with a click."""
import secrets
from datetime import date,datetime,time,timedelta
from zoneinfo import ZoneInfo
from django import forms
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db import transaction
from django.http import JsonResponse,Http404
from django.shortcuts import render,redirect,get_object_or_404
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext
from django.views.decorators.http import require_POST
from .models import Area,Table,Booking,Location,Service,Tenant,audit
from .permissions import require,can,shown,visible_bookings

KINDS=("capacity","shift")  # the restaurant services

def pick_area(service,value):
    """The room asked for, of the service's location; empty = Indifferente."""
    if not value:return None
    try:area=Area.objects.filter(pk=value,location_id=service.location_id).first()
    except (ValidationError,ValueError):area=None
    if not area:
        from .booking import BookingError
        raise BookingError(gettext("Sala non valida: ricarica la pagina."))
    return area
def taken(location,start,end,exclude=None):
    """The tables held by the bookings of the place overlapping start-end."""
    from .booking import HOLDING
    rows=Booking.objects.filter(location=location,status__in=HOLDING,busy_start__lt=end,busy_end__gt=start).exclude(pk=exclude).values_list("tables",flat=True)
    return {t for row in rows for t in row or []}
def fit(location,party,start,end,area=None,exclude=None):
    """The best free tables for party: one whose covers fit, the smallest, then by priority; else two that join, the
    smallest together. [] when none."""
    rows=Table.objects.filter(area__location=location).select_related("area")
    if area:rows=rows.filter(area=area)
    busy=taken(location,start,end,exclude)
    free={str(t.pk):t for t in rows if str(t.pk) not in busy}
    one=sorted((t for t in free.values() if t.min_covers<=party<=t.max_covers),key=lambda t:(t.max_covers,t.priority,t.name))
    if one:return [one[0]]
    pairs={frozenset((k,j)) for k,t in free.items() for j in t.joins if j in free and j!=k}
    pairs=sorted(([free[k] for k in sorted(p)] for p in pairs if party<=sum(free[k].max_covers for k in p)),key=lambda p:(sum(t.max_covers for t in p),sum(t.priority for t in p)))
    return pairs[0] if pairs else []
def area_over(area,start,end,party,exclude=None):
    """The covers of a room, for every booking in it: the words of the limit passed, empty when it fits."""
    if not area or not area.capacity:return ""
    from .booking import HOLDING,over,spans
    rows=Booking.objects.filter(area=area,status__in=HOLDING,busy_start__lt=end,busy_end__gt=start).exclude(pk=exclude)
    return f"oltre i coperti di {area.name} ({area.capacity})" if over(spans(rows,start,end),party,area.capacity) else ""
def needs_table(service,desk):
    """Online, a time is offered only with a table free for it, when the location says so."""
    place=service.location
    return not desk and service.kind in KINDS and place.tables!="off" and place.tables_online
def seat(service,party,bs,be,area=None,chosen=None,exclude=None,online=False):
    """The room and the tables of a booking: the ones the staff chose (free, of the place), otherwise the best fit
    where the location suggests it. Online with "solo con un tavolo adatto" no table means no booking."""
    from .booking import BookingError
    place=service.location
    if place.tables=="off" or service.kind not in KINDS:return area,[]
    if chosen:
        if not isinstance(chosen,list) or len(chosen)>8:raise BookingError("Tavolo non valido: ricarica la pagina.")
        try:tables=list(Table.objects.filter(area__location=place,pk__in=chosen).select_related("area"))
        except (ValidationError,ValueError):tables=[]
        if not tables or len(tables)!=len(set(chosen)):raise BookingError("Tavolo non valido: ricarica la pagina.")
        if {str(t.pk) for t in tables}&taken(place,bs,be,exclude):raise BookingError("Il tavolo è già occupato a quell'ora.")
    else:tables=fit(place,party,bs,be,area,exclude) if place.tables=="suggest" or online and place.tables_online else []
    if online and place.tables_online and not tables:raise BookingError(gettext("Nessun tavolo libero per quest'ora: scegline un'altra."))
    if online and place.tables=="manual":tables=[]  # checked that one is there; the staff choose which
    return (tables[0].area if tables else area),[str(t.pk) for t in tables]
def names(booking):
    """The tables of a booking, as the staff call them: T1 + T2."""
    if not booking.tables:return ""
    found={str(t.pk):t.name for t in Table.objects.filter(pk__in=booking.tables)}
    return " + ".join(found[k] for k in booking.tables if k in found)

class AreaForm(forms.ModelForm):
    class Meta:
        model=Area;fields=["location","name","capacity"]
        labels={"location":"Sede","name":"Nome della sala","capacity":"Coperti della sala"}
        help_texts={"name":"Per esempio Sala, Dehors, Veranda: il cliente può chiederla quando prenota","capacity":"Le persone sedute insieme al massimo in questa sala. 0 vuol dire nessun limite oltre ai posti del servizio"}
    def __init__(self,*a,tenant=None,**kw):
        super().__init__(*a,**kw);self.instance.tenant=tenant
        self.fields["location"].queryset=Location.objects.filter(active=True).order_by("name")
    def clean_capacity(self):
        v=self.cleaned_data.get("capacity") or 0
        if v>10000:raise ValidationError("Valore tra 0 e 10000.")
        return v
class TableForm(forms.ModelForm):
    joins=forms.MultipleChoiceField(required=False,label="Si unisce a",widget=forms.CheckboxSelectMultiple,help_text="I tavoli che si avvicinano a questo per un gruppo più grande")
    class Meta:
        model=Table;fields=["area","name","min_covers","max_covers","priority","joins"]
        labels={"area":"Sala","name":"Nome del tavolo","min_covers":"Persone minime","max_covers":"Persone massime","priority":"Priorità"}
        help_texts={"name":"Per esempio 1, 12 o Finestra","priority":"Tra due tavoli adatti si propone prima quello col numero più basso"}
    def __init__(self,*a,tenant=None,**kw):
        super().__init__(*a,**kw);self.instance.tenant=tenant
        self.fields["area"].queryset=Area.objects.select_related("location").order_by("location__name","name")
        self.fields["joins"].choices=[(str(t.pk),f"{t.name} ({t.area.name})") for t in Table.objects.select_related("area").order_by("area__name","name") if t.pk!=self.instance.pk]
    def clean(self):
        d=super().clean()
        lo,hi=d.get("min_covers") or 0,d.get("max_covers") or 0
        if not 1<=lo<=hi<=100:raise ValidationError("Persone minime e massime tra 1 e 100, le minime non oltre le massime.")
        if d.get("area"):d["joins"]=[str(k) for k in Table.objects.filter(pk__in=d.get("joins") or [],area__location_id=d["area"].location_id).values_list("pk",flat=True)]
        return d
    def save(self,commit=True):
        table=super().save();me=str(table.pk)
        # joining goes both ways
        for t in Table.objects.exclude(pk=table.pk):
            want=str(t.pk) in table.joins
            if want!=(me in t.joins):t.joins=[j for j in t.joins if j!=me]+([me] if want else []);t.save(update_fields=["joins"])
        return table

def edit(request,slug,what,item_id=None):
    """A room or a table, new or changed, and its removal."""
    require(request,"catalog.write")
    if what not in ("sala","tavolo"):raise Http404
    model,form_class,title={"sala":(Area,AreaForm,"sala"),"tavolo":(Table,TableForm,"tavolo")}[what]
    obj=get_object_or_404(model,pk=item_id) if item_id else None
    back=reverse("tables",args=[slug])
    if obj and request.method=="POST" and request.POST.get("remove"):
        with transaction.atomic():
            ids=[str(obj.pk)] if what=="tavolo" else [str(k) for k in obj.tables.values_list("pk",flat=True)]
            for t in Table.objects.exclude(pk__in=ids):
                if set(t.joins)&set(ids):t.joins=[j for j in t.joins if j not in ids];t.save(update_fields=["joins"])
            obj.delete();audit(request.tenant,request.user,f"tables.{what}_removed",item_id)
        messages.success(request,"Tolto. Le prenotazioni restano, senza quel tavolo.");return redirect(back)
    form=form_class(request.POST or None,instance=obj,tenant=request.tenant,label_suffix="")
    if what=="tavolo" and not request.POST and not obj and request.GET.get("sala"):form.initial["area"]=request.GET["sala"]
    if request.method=="POST" and form.is_valid():
        with transaction.atomic():
            item=form.save();audit(request.tenant,request.user,f"tables.{what}_saved",item.pk)
        messages.success(request,"Salvato.");return redirect(back+(f"?sala={item.pk if what=='sala' else item.area_id}"))
    return render(request,"form.html",{"form":form,"title":("Nuova sala" if what=="sala" else "Nuovo tavolo") if not obj else f"{title.capitalize()} {obj.name}","back":back,"back_label":"Torna a Sale e tavoli",
        "description":"Il cliente sceglie al massimo la sala, mai il tavolo" if what=="sala" else "I coperti che il tavolo accoglie e con quali altri si unisce. Sulla piantina lo spostate trascinandolo",
        "remove_label":("Togli la sala e i suoi tavoli" if what=="sala" else "Togli il tavolo") if obj else ""})

def floor(request,slug):
    """The floor plan of a room at a chosen day and time: each table free, booked or with people seated, with the name of
    the booking as this role may see it; the bookings of that time still without a table, to give one with a click."""
    require(request,"booking.read")
    t=request.tenant;tz=ZoneInfo(t.timezone);now=timezone.now().astimezone(tz)
    areas=list(Area.objects.select_related("location").order_by("location__name","name"))
    m=getattr(request,"member",None)
    if m and m.locations:areas=[a for a in areas if str(a.location_id) in m.locations]
    area=next((a for a in areas if str(a.pk)==request.GET.get("sala")),areas[0] if areas else None)
    try:day=date.fromisoformat(request.GET.get("data",""))
    except ValueError:day=now.date()
    try:clock=time.fromisoformat(request.GET.get("ora",""))
    except ValueError:clock=now.time().replace(second=0,microsecond=0)
    at=datetime.combine(day,clock,tz);tables=[];loose=[];walkin=[]
    if area:
        rows=list(visible_bookings(request,Booking.objects.filter(location=area.location,status__in=("pending","confirmed","arrived","in_progress"),busy_start__lte=at,busy_end__gt=at)).select_related("tenant").order_by("start"))
        by_table={}
        for r in rows:
            d=shown(request,r.data);x={"booking":r,"name":f"{d.get('first_name','')} {d.get('last_name','')}".strip() or "Cliente","from":r.start.astimezone(tz).strftime("%H:%M")}
            for k in r.tables:by_table.setdefault(k,x)
            if not r.tables and (r.area_id in (None,area.pk)):loose.append(x)
        for tb in area.tables.order_by("name"):
            x=by_table.get(str(tb.pk))
            state="free" if not x else "seated" if x["booking"].status in ("arrived","in_progress") else "booked"
            tables.append({"table":tb,"x":f"{tb.x:.1f}","y":f"{tb.y:.1f}","state":state,"who":x})
        walkin=list(Service.objects.filter(active=True,kind="capacity",location=area.location).order_by("name"))
    return render(request,"tables.html",{"areas":areas,"area":area,"tables":tables,"loose":loose,"day":day,"clock":clock.strftime("%H:%M"),
        "can_edit":can(request,"catalog.write"),"can_seat":can(request,"booking.write"),"walkin_services":walkin,"location":area.location if area else None})

def _json(request):
    import json
    try:return json.loads(request.body or b"{}")
    except ValueError:return {}
@require_POST
def place(request,slug):
    """A table dragged on the floor plan: where it stands, in percent of the plan."""
    require(request,"catalog.write")
    data=_json(request)
    table=get_object_or_404(Table,pk=data.get("table"))
    try:table.x,table.y=(min(95.0,max(0.0,float(data[k]))) for k in ("x","y"))
    except (KeyError,TypeError,ValueError):return JsonResponse({"error":"Posizione non valida."},status=400)
    table.save(update_fields=["x","y"]);return JsonResponse({"ok":True})
@require_POST
def assign(request,slug):
    """A booking given a table on the floor plan (or none: tables empty); the table must be free for its whole time."""
    from .booking import BookingError,OPEN,choice
    require(request,"booking.write")
    data=_json(request)
    try:
        with transaction.atomic():
            Tenant.objects.select_for_update().get(pk=request.tenant.pk)
            b=choice(visible_bookings(request,Booking.objects.all()),data.get("booking"),status__in=OPEN)  # seated ones too
            chosen=[str(x) for x in data.get("tables") or []]
            if chosen:
                area,ids=seat(b.service,b.party,b.busy_start,b.busy_end,chosen=chosen,exclude=b.pk)
                if not ids:raise BookingError("La sede non gestisce i tavoli: si accende in Sedi.")
                b.area,b.tables=area,ids
            else:b.tables=[]
            b.save(update_fields=["area","tables"]);audit(request.tenant,request.user,"booking.tables",b.pk,tables=b.tables)
        return JsonResponse({"ok":True})
    except BookingError as ex:return JsonResponse({"error":str(ex)},status=400)
@require_POST
def seat_walkin(request,slug):
    """A client walking in, seated now on a table of the floor plan."""
    from .booking import BookingError,ForceWarning,create_booking
    require(request,"booking.write")
    back=reverse("tables",args=[slug])+"?sala="+request.POST.get("sala","")
    try:
        from .booking import choice
        from .permissions import check_new_booking
        check_new_booking(request,choice(Service,request.POST.get("service"),active=True),None)
        create_booking(request.tenant,{"service":request.POST.get("service"),"party":request.POST.get("party") or 1,"walkin":True,"tables":[request.POST.get("table")],
            "request_key":secrets.token_urlsafe(24)},request.user)
        messages.success(request,"Cliente seduto al tavolo.")
    except (BookingError,ValidationError) as ex:messages.error(request,getattr(ex,"message",None) or str(ex))
    return redirect(back)
