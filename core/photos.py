"""2.22.9: the client's photo album. At the desk, after a cut or a colour, one to four photos of the work go on the
visit of that day (from the booking) or on today (from the client card): the browser makes them small before sending,
the server makes them a JPEG of at most 1600 px plus a small copy, both sealed with the business key. Only where there
is a client record (hair, beauty, tattoo...), only for who sees the clients' data; kept in this program, never sent to
the site. They go with the client's erasure and when the booking of that day loses its data (privacy.py)."""
import io
from datetime import timedelta
from zoneinfo import ZoneInfo
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.http import HttpResponse,JsonResponse
from django.shortcuts import get_object_or_404,redirect
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from PIL import Image,ImageOps,UnidentifiedImageError
from .crypto import encrypt_bytes,decrypt_bytes
from .models import Booking,ClientPhoto,Contact,audit
from .permissions import can,require,visible_bookings

def uid(v):
    import uuid
    try:return uuid.UUID(str(v))
    except ValueError:return None
PER_DAY=4  # more is a mess to look at later
MAX_UPLOAD=20*1024*1024  # a phone photo as it is, when the browser could not make it small
FULL,THUMB=1600,360

def compress(upload):
    """The photo as it is kept: upright as it was taken, at most 1600 px, JPEG; and its small copy. Raises ValueError."""
    data=upload.read(MAX_UPLOAD+1)
    if len(data)>MAX_UPLOAD:raise ValueError("Foto troppo grande: massimo 20 MB.")
    try:
        im=Image.open(io.BytesIO(data))
        if im.format not in ("JPEG","PNG","WEBP","MPO") or im.width*im.height>60_000_000:raise ValueError()
        im.load();im=ImageOps.exif_transpose(im)
        if im.mode!="RGB":
            rgba=im.convert("RGBA");flat=Image.new("RGB",rgba.size,(255,255,255));flat.paste(rgba,mask=rgba.getchannel("A"));im=flat
        out=[]
        for side,quality in ((FULL,80),(THUMB,72)):
            copy=im.copy();copy.thumbnail((side,side),Image.LANCZOS)
            buf=io.BytesIO();copy.save(buf,format="JPEG",quality=quality,optimize=True,progressive=True);out.append(buf.getvalue())
        return out
    except (ValueError,OSError,UnidentifiedImageError,Image.DecompressionBombError):
        raise ValueError("Usa una foto JPEG, PNG o WebP valida.")

def on(request):
    """Photos exist where the business keeps a client record, for who sees the clients' data."""
    from .presets import record
    return bool(record(request.tenant)) and can(request,"pii.read")

def album(contact,tz,day=None):
    """The client's photos by day, newest first: [{"date","label","photos"}]; label: the services of that day."""
    rows=ClientPhoto.objects.filter(contact=contact).defer("image","thumb").order_by("-date","created")
    if day:rows=rows.filter(date=day)
    days={}
    for p in rows:days.setdefault(p.date,[]).append(p)
    if not days:return []
    names={};first={}
    from .models import Treatment
    entries={}
    for e in Treatment.objects.filter(contact=contact,date__in=list(days)).order_by("created"):
        entries.setdefault(e.date,[]).append(e.data.get("service",""))
    for b in Booking.objects.filter(contact=contact,start__date__gte=min(days)-timedelta(days=1),start__date__lte=max(days)+timedelta(days=1)).exclude(status__in=("cancelled","rejected","expired")).select_related("service"):
        d=b.start.astimezone(tz).date();names.setdefault(d,[]).append(b.snapshot.get("service") or b.service.name);first.setdefault(d,b.pk)
    # 2.22.9: each day with its appointment and its record entry, to go from the photos to what was done
    return [{"date":d,"label":" · ".join(x for x in dict.fromkeys(entries.get(d,[])+names.get(d,[])) if x),"photos":ps,"left":max(0,PER_DAY-len(ps)),
        "booking":next((p.booking_id for p in ps if p.booking_id),None) or first.get(d),"entry":d in entries} for d,ps in days.items()]

def allowed(request,contact):
    """2.22.9: everybody who sees the clients' data sees every photo: who did what to whom is no secret among the staff."""
    return can(request,"pii.read")

def by_day(contact):
    """{day: [photos]} of a client, without the images: for the record entries and the earlier visits."""
    out={}
    for p in ClientPhoto.objects.filter(contact=contact).defer("image","thumb").order_by("created"):out.setdefault(p.date,[]).append(p)
    return out

def place(entries,days):
    """The photos of a day on the first record entry of that day (the newest), each entry with how many more fit."""
    seen=set()
    for t in entries:
        t.photos=None
        if t.date in seen:continue
        seen.add(t.date);t.photos=days.get(t.date,[]);t.photos_left=max(0,PER_DAY-len(t.photos))

def back(request,fallback):
    nxt=request.POST.get("next") or request.GET.get("next") or ""
    return nxt if url_has_allowed_host_and_scheme(nxt,allowed_hosts={request.get_host()},require_https=request.is_secure()) else fallback

def wants_json(request):return request.headers.get("X-Requested-With")=="fetch"

def upload(request,slug,contact_id):
    """POST one or more photos: on the visit of a booking (booking=), else on today. At most PER_DAY a day: the ones
    over are left out and said."""
    if request.method!="POST":return HttpResponse(status=405)
    require(request,"booking.write","pii.read")
    if not on(request):raise PermissionDenied("Le foto ci sono dove c'è la scheda cliente.")
    t=request.tenant;tz=ZoneInfo(t.timezone)
    contact=get_object_or_404(Contact.objects.exclude(email_index__startswith="erased:"),pk=contact_id)
    booking=None
    from .models import Treatment
    entry=Treatment.objects.filter(contact=contact,pk=uid(request.POST["treatment"])).select_related("booking").first() if request.POST.get("treatment") else None
    if request.POST.get("treatment") and not entry:return HttpResponse(status=404)
    if entry:  # 2.22.9: on the day of an entry of the record, and its visit if it came from one
        if not can(request,"contacts.read"):raise PermissionDenied("Il tuo ruolo non consente di vedere le schede dei clienti.")
        booking,day=entry.booking,entry.date
    elif request.POST.get("booking"):
        booking=get_object_or_404(visible_bookings(request,Booking.objects.filter(contact=contact)),pk=uid(request.POST["booking"]))
        day=booking.start.astimezone(tz).date()
    else:
        if not can(request,"contacts.read"):raise PermissionDenied("Il tuo ruolo non consente di vedere le schede dei clienti.")
        day=timezone.now().astimezone(tz).date()
    fallback=reverse("contact_detail",args=[slug,contact.pk])+"#scheda" if entry else reverse("booking_detail",args=[slug,booking.pk])+"#foto" if booking else reverse("contact_detail",args=[slug,contact.pk])+"#foto"
    files=request.FILES.getlist("photo")
    room=PER_DAY-ClientPhoto.objects.filter(contact=contact,date=day).count()
    error="";made=[]
    if not files:error="Scegli una foto."
    elif room<=0:error=f"Ci sono già {PER_DAY} foto per questo giorno: togline una per aggiungerne un'altra."
    else:
        for f in files[:room]:
            try:full,small=compress(f)
            except ValueError as ex:error=str(ex);continue
            p=ClientPhoto(tenant=t,contact=contact,booking=booking,date=day)
            p.image=encrypt_bytes(t,full,"photo:"+str(p.pk));p.thumb=encrypt_bytes(t,small,"photo-thumb:"+str(p.pk));p.save()
            made.append(p)
        if len(files)>room:error=f"Tenute le prime {room}: al massimo {PER_DAY} foto per giorno."
    if made:audit(t,request.user,"photo.added",contact.pk,count=len(made),day=day.isoformat())
    left=max(0,PER_DAY-ClientPhoto.objects.filter(contact=contact,date=day).count())
    if wants_json(request):
        return JsonResponse({"ok":bool(made),"error":error,"left":left,"photos":[{"id":str(p.pk),"thumb":reverse("photo_image",args=[slug,p.pk])+"?piccola=1","full":reverse("photo_image",args=[slug,p.pk])} for p in made]},status=200 if made or not error else 400)
    if made:messages.success(request,"Foto salvata." if len(made)==1 else f"{len(made)} foto salvate.")
    if error:messages.error(request,error)
    return redirect(back(request,fallback))

def remove(request,slug,photo_id):
    if request.method!="POST":return HttpResponse(status=405)
    require(request,"booking.write","pii.read")
    p=get_object_or_404(ClientPhoto.objects.select_related("contact"),pk=photo_id)
    if not allowed(request,p.contact):raise PermissionDenied("Non puoi vedere questo cliente.")
    contact,day=p.contact,p.date;p.delete()
    audit(request.tenant,request.user,"photo.removed",contact.pk,day=day.isoformat())
    if wants_json(request):return JsonResponse({"ok":True,"left":max(0,PER_DAY-ClientPhoto.objects.filter(contact=contact,date=day).count())})
    messages.success(request,"Foto tolta.")
    return redirect(back(request,reverse("contact_detail",args=[slug,contact.pk])+"#foto"))

def image(request,slug,photo_id):
    """The photo (or with ?piccola=1 its small copy), only to who may see that client; kept by the browser for a while,
    never by a shared cache."""
    require(request,"pii.read")
    small=bool(request.GET.get("piccola"))
    p=get_object_or_404(ClientPhoto.objects.select_related("contact").defer("image" if small else "thumb"),pk=photo_id)
    if not allowed(request,p.contact):raise PermissionDenied("Non puoi vedere questo cliente.")
    data=decrypt_bytes(request.tenant,p.thumb,"photo-thumb:"+str(p.pk)) if small else decrypt_bytes(request.tenant,p.image,"photo:"+str(p.pk))
    r=HttpResponse(data,content_type="image/jpeg")
    r["Cache-Control"]="private, max-age=86400";r["X-Content-Type-Options"]="nosniff"
    if request.GET.get("scarica"):r["Content-Disposition"]=f'attachment; filename="foto-{p.date.isoformat()}-{str(p.pk)[:6]}.jpg"'
    return r
