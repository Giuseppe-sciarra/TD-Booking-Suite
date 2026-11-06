"""2.22.0: Preventivi. A quote for a client (from the client list or written by hand): services, products and free
lines with their price, notes and validity; printed or saved as PDF with the business's header (logo, name, address,
phone, email, partita IVA), opened in Cassa when the client accepts. A free line can be saved among the products."""
import re
from datetime import date,timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo
from django.conf import settings
from django.contrib import messages
from django.db import IntegrityError,transaction
from django.db.models import Max
from django.shortcuts import render,redirect,get_object_or_404
from django.utils import timezone
from .crypto import encrypt
from .models import Contact,Location,Option,Product,Quote,Service,audit
from .permissions import can,require
from .prices import euro

VALID_DAYS=30
def who(q):
    d=q.data;return " ".join(x for x in (d.get("first_name",""),d.get("last_name","")) if x)
def header(t):
    """The business on top of the quote: the data the agency keeps for it, the first location where they are missing."""
    from .agency import load_profile
    p=load_profile(t);loc=Location.objects.filter(active=True).order_by("name").first()
    address=", ".join(x for x in (p.get("address",""),p.get("city","")) if x) or (loc.address if loc else "")
    return {"name":p.get("company") or t.name,"brand":t.name,"address":address,"phone":p.get("phone") or (loc.phone if loc else ""),
        "email":p.get("email",""),"vat":p.get("vat",""),"tax_code":p.get("tax_code",""),"logo":bool(t.logo)}
def catalog():
    """Services (and their variants) and products, to add a line with a tap."""
    out=[]
    for s in Service.objects.filter(active=True).order_by("name"):
        opts=list(Option.objects.filter(service=s,active=True).order_by("name"))
        out+=[("Servizi",f"{s.name} {o.name}",o.price if o.price is not None else s.price,f"s:{s.pk}") for o in opts] or [("Servizi",s.name,s.price,f"s:{s.pk}")]
    out+=[("Prodotti",p.name,p.price,f"p:{p.pk}") for p in Product.objects.filter(active=True).order_by("name")]
    seen=set();rows=[]
    for g,n,p,r in out:
        if (g,n) in seen:continue  # the same service in two locations, once
        seen.add((g,n));rows.append({"group":g,"name":n,"price":str(p if p is not None else "").replace(".",","),"ref":r})
    return rows
def lines(post):
    """The rows of the form: description, quantity, price; a free row marked to save becomes a product."""
    from .cassa import cents
    out=[];flags=post.getlist("save");refs=post.getlist("ref")
    for i,(desc,qty,price) in enumerate(zip(post.getlist("desc"),post.getlist("qty"),post.getlist("price"))):
        desc=" ".join(desc.split())[:120]
        if not desc and not price.strip():continue
        try:qty=int(qty)
        except ValueError:qty=0
        if not desc or not 1<=qty<=99:raise ValueError("Ogni riga vuole una descrizione e una quantità da 1 a 99.")
        ref=refs[i] if i<len(refs) and re.fullmatch(r"[sp]:[0-9a-f-]{36}",refs[i] or "") else ""
        out.append({"desc":desc,"qty":qty,"cents":cents(price),"ref":ref,"save":not ref and i<len(flags) and flags[i]=="1"})
    if not out:raise ValueError("Aggiungi almeno una riga al preventivo.")
    if len(out)>60:raise ValueError("Al massimo 60 righe per preventivo.")
    return out
def index(request,slug):
    require(request,"booking.write","pii.read")
    rows=list(Quote.objects.select_related("contact").order_by("-year","-number")[:300])
    q=" ".join(request.GET.get("q","").split()).casefold()
    for r in rows:r.who=who(r)
    if q:rows=[r for r in rows if q in r.who.casefold() or q in r.ref]
    return render(request,"quotes.html",{"rows":rows,"q":request.GET.get("q","")})
def edit(request,slug,quote_id=None):
    require(request,"booking.write","pii.read")
    t=request.tenant;quote=get_object_or_404(Quote,pk=quote_id) if quote_id else None
    today=timezone.now().astimezone(ZoneInfo(t.timezone)).date()
    d=quote.data if quote else {}
    if request.method=="POST" and request.POST.get("action")=="delete" and quote:
        audit(t,request.user,"quote.deleted",quote.pk,ref=quote.ref);quote.delete()
        messages.success(request,f"Preventivo {quote.ref} eliminato.");return redirect("quotes",slug)
    if request.method=="POST":
        p=request.POST
        d={"first_name":" ".join(p.get("first_name","").split())[:80],"last_name":" ".join(p.get("last_name","").split())[:80],
           "phone":" ".join(p.get("phone","").split())[:40],"email":p.get("email","").strip()[:160],"notes":p.get("notes","").strip()[:2000]}
        try:
            rows=lines(p)
            if not (d["first_name"] or d["last_name"]):raise ValueError("Scrivi il nome del cliente.")
            if d["email"] and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+",d["email"]):raise ValueError("Email non valida.")
            try:valid=date.fromisoformat(p.get("valid_until","")) if p.get("valid_until") else None
            except ValueError:raise ValueError("Data di validità non valida.")
            with transaction.atomic():
                contact=get_object_or_404(Contact,pk=p["contact"]) if re.fullmatch(r"[0-9a-f-]{36}",p.get("contact","")) else None
                if not contact and p.get("new_client")=="on":
                    from .clients import find_or_create
                    contact,_=find_or_create(t,{**{k:d[k] for k in ("first_name","last_name","phone","email")}},named=True)
                made=0
                for r in rows:
                    if r.pop("save"):
                        # 2.22.0: the products come in a little at a time, from the quotes
                        prod=Product.objects.filter(name__iexact=r["desc"][:80],active=True).first()
                        if not prod:prod=Product.objects.create(tenant=t,name=r["desc"][:80],price=Decimal(r["cents"])/100 if r["cents"] else None);made+=1
                        r["ref"]=f"p:{prod.pk}"
                d["lines"]=rows;total=Decimal(sum(r["qty"]*r["cents"] for r in rows))/100
                if not quote:
                    quote=Quote(tenant=t,year=today.year,actor=str(request.user.pk))
                    quote.number=(Quote.objects.filter(year=today.year).aggregate(n=Max("number"))["n"] or 0)+1
                quote.contact=contact;quote.total=total;quote.valid_until=valid
                quote.pii=encrypt(t,d,"quote:"+str(quote.pk));quote.save()
                audit(t,request.user,"quote.saved",quote.pk,ref=quote.ref,total=str(total))
            messages.success(request,f"Preventivo {quote.ref} salvato."+(f" {made} nuov{'o prodotto' if made==1 else 'i prodotti'} in Prodotti." if made else ""))
            return redirect("quote_sheet",slug,quote.pk)
        except IntegrityError:messages.error(request,"Un altro preventivo ha preso lo stesso numero: salva di nuovo.")
        except ValueError as ex:messages.error(request,str(ex))
        d["lines"]=[{"desc":a,"qty":b,"cents":None,"price":c,"ref":r} for a,b,c,r in zip(p.getlist("desc"),p.getlist("qty"),p.getlist("price"),p.getlist("ref")+[""]*len(p.getlist("desc")))]
        d["contact"]=p.get("contact","");d["valid_until"]=p.get("valid_until","")
    rows=[{**l,"price":l["price"] if l.get("cents") is None else str(Decimal(l["cents"])/100).replace(".",",")} for l in d.get("lines",[])]
    contact=quote.contact if quote and quote.contact_id else (Contact.objects.filter(pk=request.GET["cliente"]).first() if re.fullmatch(r"[0-9a-f-]{36}",request.GET.get("cliente","")) else None)
    if contact and not quote and request.method!="POST":
        c=contact.data;d.update({k:c.get(k,"") for k in ("first_name","last_name","phone","email")})
    valid=d.get("valid_until") if request.method=="POST" else (quote.valid_until if quote else today+timedelta(days=VALID_DAYS))
    return render(request,"quote_form.html",{"quote":quote,"d":d,"rows":rows,"catalog":catalog(),"contact":contact,"valid":valid})
def sheet(request,slug,quote_id):
    require(request,"booking.write","pii.read")
    q=get_object_or_404(Quote.objects.select_related("contact"),pk=quote_id);d=q.data;h=header(request.tenant)
    rows=[{**l,"price":Decimal(l["cents"])/100,"amount":Decimal(l["qty"]*l["cents"])/100} for l in d.get("lines",[])]
    missing=[n for k,n in (("address","indirizzo"),("phone","telefono"),("vat","partita IVA")) if not h[k]]
    return render(request,"quote_sheet.html",{"q":q,"d":d,"h":h,"rows":rows,"missing":missing,"name":who(q)})
