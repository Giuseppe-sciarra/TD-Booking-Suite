"""2.22.0: gift cards. One is sold at the Cassa as a line of the bill (its own department, «GIFT CARD» and its code on the
receipt) and is active once the register has emitted that receipt; it is in a client's name, the buyer's or anybody's
later. A bill paid with it prints a receipt with the credit taken off as «GIFT CARD»: what is left is paid as usual.
The credit is taken when the bill goes to the register and given back if the register does not emit it, or on its annullo."""
import re,secrets
from datetime import date
from decimal import Decimal
from django.contrib import messages
from django.http import JsonResponse
from django.shortcuts import render,redirect,get_object_or_404
from django.urls import reverse
from django.utils import timezone
from zoneinfo import ZoneInfo
from .models import Contact,GiftCard,GiftMove,Sale,audit
from .permissions import can,require
from .prices import euro

ALPHABET="ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0/O, 1/I: read aloud at the desk
PRESETS=(25,50,100,150)
MONTHS=12  # the expiry proposed at the sale
def clean(code):return re.sub(r"[^A-Z0-9]","",str(code or "").upper())[:12]
def new_code():
    while True:
        code="".join(secrets.choice(ALPHABET) for _ in range(8))
        if not GiftCard.objects.filter(code=code).exists():return code
def proposed(today):
    """Today and MONTHS months, on the last day of that month when it is shorter (31 March, 30 April... next year)."""
    import calendar
    m=today.month-1+MONTHS;y,m=today.year+m//12,m%12+1
    return date(y,m,min(today.day,calendar.monthrange(y,m)[1]))
def cents(card):return int(card.balance*100)
def usable(code,today):
    """The card of a code, if it can pay now: active, not expired, with credit."""
    card=GiftCard.objects.select_for_update().filter(code=clean(code)).first()
    if not card:raise ValueError("Gift card non trovata: controlla il codice.")
    if card.status=="pending":raise ValueError("Questa gift card non è ancora attiva: lo scontrino della sua vendita non risulta emesso.")
    if card.status=="void":raise ValueError("Questa gift card è annullata.")
    if card.expires and card.expires<today:raise ValueError(f"Questa gift card è scaduta il {card.expires:%d/%m/%Y}.")
    if card.balance<=0:raise ValueError("Questa gift card non ha più credito.")
    return card
def taken(sale):return -sum(GiftMove.objects.filter(sale=sale).values_list("cents",flat=True))
def reserve(tenant,sale):
    """The credit of the bill taken off its card (once: a retry after a give-back takes it again)."""
    g=sale.data.get("gift")
    if not g or taken(sale)>=g["cents"]:return
    card=GiftCard.objects.select_for_update().get(pk=g["id"])
    today=timezone.now().astimezone(ZoneInfo(tenant.timezone)).date()
    if card.status!="active" or card.expires and card.expires<today or cents(card)<g["cents"]:
        raise ValueError(f"La gift card {card.code} non ha più {euro(Decimal(g['cents'])/100)} di credito: rifai il conto.")
    GiftMove.objects.create(tenant=tenant,card=card,sale=sale,cents=-g["cents"])
    card.balance-=Decimal(g["cents"])/100;card.save(update_fields=["balance"])
def give_back(tenant,sale):
    """A receipt not emitted, or voided: its credit goes back on the card."""
    n=taken(sale)
    if n<=0:return
    g=sale.data.get("gift") or {}
    card=GiftCard.objects.select_for_update().filter(pk=g.get("id")).first()
    if not card:return
    GiftMove.objects.create(tenant=tenant,card=card,sale=sale,cents=n)
    card.balance+=Decimal(n)/100;card.save(update_fields=["balance"])
def sold(tenant,sale,state):
    """The cards sold with this receipt: active when it is emitted, void with its annullo."""
    GiftCard.objects.filter(sale=sale,status="pending" if state=="done" else "active").update(status="active" if state=="done" else "void")
def holder(card,names_ok):
    if not card.contact_id:return ""
    if not names_ok:return "Cliente"
    d=card.contact.data;return " ".join(x for x in (d.get("first_name",""),d.get("last_name","")) if x)
def of(contact):
    """The cards of a client still with credit, for the Cassa and his card."""
    return list(GiftCard.objects.filter(contact=contact).exclude(status="void").order_by("-created"))

def page(request,slug):
    """Gift card: every card with its credit and moves; a card given to a client; the card to print for the client."""
    require(request,"sale.write")
    t=request.tenant;names_ok=can(request,"pii.read") and can(request,"contacts.read")
    if request.method=="POST":
        card=get_object_or_404(GiftCard,pk=request.POST.get("card"))
        if request.POST.get("action")=="assign" and names_ok:
            c=get_object_or_404(Contact,pk=request.POST.get("contact")) if request.POST.get("contact") else None
            if not c:messages.error(request,"Scegli il cliente dall'elenco che compare mentre scrivi.")
            else:
                card.contact=c;card.save(update_fields=["contact"]);audit(t,request.user,"gift.assigned",card.pk,contact=str(c.pk))
                messages.success(request,f"Gift card {card.code} intestata a {holder(card,True)}.")
        elif request.POST.get("action")=="expires":
            v=request.POST.get("expires","").strip()
            try:day=date.fromisoformat(v) if v else None
            except ValueError:messages.error(request,"Data non valida.");return redirect(reverse("gift_cards",args=[slug])+f"?q={card.code}")
            card.expires=day;card.save(update_fields=["expires"]);audit(t,request.user,"gift.expires",card.pk,expires=str(day or ""))
            messages.success(request,"Scadenza salvata." if day else "Scadenza tolta: la gift card non scade.")
        return redirect(reverse("gift_cards",args=[slug])+f"?q={card.code}")
    if request.GET.get("stampa"):
        card=get_object_or_404(GiftCard.objects.select_related("contact"),pk=request.GET["stampa"])
        return render(request,"gift_print.html",{"card":card,"who":holder(card,names_ok)})
    q=clean(request.GET.get("q",""));cards=GiftCard.objects.select_related("contact","sale").order_by("-created")
    if q:cards=cards.filter(code__startswith=q)
    rows=list(cards[:200])
    for c in rows:
        c.who=holder(c,names_ok);c.used=list(c.moves.order_by("created"))
        for m in c.used:m.euro=Decimal(abs(m.cents))/100
    today=timezone.now().astimezone(ZoneInfo(t.timezone)).date()
    live=GiftCard.objects.filter(status="active").exclude(expires__lt=today)
    return render(request,"gift_cards.html",{"cards":rows,"q":q,"today":today,"names_ok":names_ok,"open_total":sum(c.balance for c in live),"open_n":live.filter(balance__gt=0).count(),
        "presets":PRESETS,"expires":proposed(today)})
def lookup(request,slug):
    """The Cassa asks for a code: its credit, or why it cannot pay."""
    require(request,"sale.write")
    try:
        card=usable(request.GET.get("code",""),timezone.now().astimezone(ZoneInfo(request.tenant.timezone)).date())
        return JsonResponse({"code":card.code,"balance":str(card.balance).replace(".",","),"who":holder(card,can(request,"pii.read")),"expires":card.expires.strftime("%d/%m/%Y") if card.expires else ""})
    except ValueError as ex:return JsonResponse({"error":str(ex)})
