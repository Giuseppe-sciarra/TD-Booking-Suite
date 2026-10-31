from django import template
register=template.Library()
@register.filter
def role_label(role):
 from core.permissions import role_label as label
 return label(role)
@register.filter
def status_name(v):
 return {"confirmed":"Confermata","completed":"Completata","cancelled":"Annullata","no_show":"Assente","arrived":"Arrivato","in_progress":"In corso","none":"Non richiesto","pending":"In attesa","yes":"Confermato","revoked":"Revocato","draft":"Bozza","queued":"In coda","paused":"In pausa","accepted":"Accettata da SMTP","failed":"Fallita","unknown":"Esito incerto","skipped":"Esclusa","sending":"Invio in corso","active":"Attivo","trial":"Prova","suspended":"Sospeso"}.get(v,v)
@register.filter
def row_label(o):
 if hasattr(o,"name"):return o.name
 hm=lambda t:t.strftime("%H:%M") if t else ""
 # 2.14.0: alternate weeks, and closures from one day to another, said as such
 if hasattr(o,"weekday"):return o.get_weekday_display()+", "+hm(o.start)+"–"+hm(o.end)+(f", ogni {o.every_weeks} settimane dal {o.valid_from:%d/%m/%Y}" if o.every_weeks>1 and o.valid_from else "")
 if hasattr(o,"closed"):return (f"Dal {o.date:%d/%m/%Y} al {o.until:%d/%m/%Y}" if o.until and o.until>o.date else o.date.strftime("%d/%m/%Y"))+", "+("chiuso" if o.closed else hm(o.start)+"–"+hm(o.end))+(", "+o.label if o.label else "")
 if hasattr(o,"start"):return o.service.name+", "+o.start.strftime("%d/%m/%Y %H:%M")
 return str(o.pk)

@register.filter
def service_summary(s):
 """2.6.0: what a service is, in words, under its name in the list."""
 last=f", ultimo arrivo {s.last_arrival} minuti prima della chiusura" if s.kind=="capacity" and s.last_arrival else ""
 if s.kind=="capacity" and not s.turnover:return f"{s.capacity} coperti per ogni pranzo e cena, il cliente sceglie l'ora di arrivo"+last
 if s.kind=="capacity":return f"A turni: {s.capacity} posti, ogni posto si libera dopo {s.duration} minuti"+last
 if s.kind=="shift":return f"Turni: {s.capacity} posti totali, i coperti di ogni turno sono in Turni del ristorante"
 if s.kind=="resource":return f"Appuntamento di {s.duration} minuti"+(" con "+s.resource_label.lower() if s.resource_label else "")+(" · "+euro(s.price) if s.price is not None else "")
 if s.kind=="day":return f"A notti: {f'da {s.min_days} a' if s.min_days else 'anche in giornata, fino a'} {s.max_days}, arrivo alle {s.checkin.strftime('%H:%M') if hasattr(s.checkin,'strftime') else s.checkin}"
 if s.kind=="event":return "Evento con date: i posti sono in Eventi con data"
 return ""

@register.filter
def nights(booking):
    """2.12.0: how long a stay is, "3 notti" or "1 notte", counted in the business's time zone."""
    from django.utils import timezone
    n=(timezone.localtime(booking.end).date()-timezone.localtime(booking.start).date()).days
    return f"{n} nott{'e' if n==1 else 'i'}" if n else "in giornata"  # 2.14.0: a rental picked up and returned the same day

@register.filter
def photo_link(resource,slug):
    """2.12.0: an operator's photo, uploaded or by address; empty without one."""
    return resource.photo_link(slug) if resource else ""

@register.filter
def euro(value):
    """2.11.0: 25 -> 25,00 €, empty for no price."""
    from core.prices import euro as fmt
    return fmt(value)

@register.filter
def uses_resources(rows):
    """2.6.1: the column of operators or rooms only where the business has them; its name is the business's word."""
    from core.models import Resource
    from core.presets import units
    rows=list(rows)
    if not any(r.resource_id for r in rows) and not Resource.objects.filter(active=True).exists():return ""
    tenant=rows[0].tenant if rows else None
    return units(getattr(tenant,"business_type",""))[0] if tenant else "Operatore"

@register.filter
def question_kind(q):
    """2.8.0: how a booking question is answered, in words: Sì o no, Testo, Scelta tra..."""
    kind={"checkbox":"Casella sì o no","text":"Testo libero","number":"Numero"}.get(q.get("type"),"Scelta tra "+", ".join(q.get("choices") or []))
    return kind+(", obbligatoria" if q.get("required") else ", facoltativa")

# 2.10.0: one small line icon for each group of the menu, drawn here so the page needs no icon font or file
ICONS={
 "day":'<rect x="3" y="4.5" width="18" height="16" rx="2.5"/><path d="M3 9.5h18M8 2.5v4M16 2.5v4"/>',
 "shop":'<path d="M4 9.5 5.6 4h12.8L20 9.5"/><path d="M4.5 9.5V20h15V9.5"/><path d="M10 20v-5h4v5"/>',
 "mail":'<rect x="3" y="5" width="18" height="14" rx="2.5"/><path d="m3.5 7 8.5 6 8.5-6"/>',
 "shield":'<path d="M12 3 20 6v6c0 4.4-3.3 8.2-8 9-4.7-.8-8-4.6-8-9V6z"/><path d="m9 12 2 2 4-4"/>',
 "wrench":'<path d="M14.6 6.4a4 4 0 0 0-5.3 5.3L3.5 17.5V20.5h3l5.8-5.8a4 4 0 0 0 5.3-5.3l-2.6 2.6-2.3-.6-.6-2.3z"/>',
 "sliders":'<path d="M4 6h9M17 6h3M4 12h3M11 12h9M4 18h11M19 18h1"/><circle cx="15" cy="6" r="2"/><circle cx="9" cy="12" r="2"/><circle cx="17" cy="18" r="2"/>',
 "help":'<circle cx="12" cy="12" r="9"/><path d="M9.6 9.2a2.5 2.5 0 0 1 4.9.6c0 1.7-2.5 2.1-2.5 3.6"/><path d="M12 17h.01"/>',
 "home":'<path d="M3.5 11 12 4l8.5 7"/><path d="M5.5 9.5V20h13V9.5"/><path d="M10 20v-5h4v5"/>'}
ICONS.update({
 "phone":'<path d="M6.6 3.5h2.6l1.4 4-2 1.3a12 12 0 0 0 6.6 6.6l1.3-2 4 1.4v2.6a2 2 0 0 1-2.2 2A17 17 0 0 1 4.6 5.7a2 2 0 0 1 2-2.2z"/>',
 "grid":'<rect x="3.5" y="3.5" width="7" height="7" rx="1.5"/><rect x="13.5" y="3.5" width="7" height="7" rx="1.5"/><rect x="3.5" y="13.5" width="7" height="7" rx="1.5"/><rect x="13.5" y="13.5" width="7" height="7" rx="1.5"/>',
 "users":'<circle cx="9" cy="8" r="3.5"/><path d="M2.5 20c.6-3.6 3.2-5.5 6.5-5.5s5.9 1.9 6.5 5.5"/><path d="M15.5 4.8a3.3 3.3 0 0 1 0 6.4M17.5 14.8c2 .7 3.4 2.4 3.9 5.2"/>',
 "badge":'<rect x="4" y="3" width="16" height="18" rx="2.5"/><circle cx="12" cy="10" r="3"/><path d="M7.5 17.5c.8-2.2 2.4-3.3 4.5-3.3s3.7 1.1 4.5 3.3"/>',
 "megaphone":'<path d="M3.5 10v4h3l7 4V6l-7 4z"/><path d="M17 9.5a3.5 3.5 0 0 1 0 5"/><path d="m6.5 14 1.2 5h2.3l-1-5"/>',
 "bell":'<path d="M6 16v-5a6 6 0 0 1 12 0v5l1.5 2h-15z"/><path d="M10 20.5a2.2 2.2 0 0 0 4 0"/>',
 "list":'<path d="M8.5 6H20M8.5 12H20M8.5 18H20"/><path d="M4 6h.01M4 12h.01M4 18h.01"/>',
 "key":'<circle cx="8" cy="15" r="4"/><path d="m11 12 8.5-8.5M16 7l2.5 2.5M13.8 9.2l2 2"/>',
 "globe":'<circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3c2.5 2.6 3.7 5.6 3.7 9s-1.2 6.4-3.7 9c-2.5-2.6-3.7-5.6-3.7-9S9.5 5.6 12 3z"/>',
 "compass":'<circle cx="12" cy="12" r="9"/><path d="m15.5 8.5-2 5-5 2 2-5z"/>',
 "monitor":'<rect x="3" y="4" width="18" height="12" rx="2"/><path d="M8.5 20h7M12 16v4"/>',
 "archive":'<rect x="3" y="4" width="18" height="5" rx="1.5"/><path d="M5 9v10a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1V9"/><path d="M10 13h4"/>',
 "briefcase":'<rect x="3" y="7" width="18" height="13" rx="2"/><path d="M9 7V5.5A1.5 1.5 0 0 1 10.5 4h3A1.5 1.5 0 0 1 15 5.5V7M3 12.5h18"/>',
 "inbox":'<path d="M3.5 13.5 6 5h12l2.5 8.5V19a1 1 0 0 1-1 1h-15a1 1 0 0 1-1-1z"/><path d="M3.5 13.5h5l1 2.5h5l1-2.5h5"/>',
 "tag":'<path d="M3.5 12.5V4H12l8.5 8.5-8 8z"/><circle cx="8" cy="8.5" r="1.3"/>',
 "plug":'<path d="M9 3v4M15 3v4M6.5 7h11v3a5.5 5.5 0 0 1-11 0z"/><path d="M12 15.5V21"/>',
 "chat":'<path d="M5 19.5 6.2 16A7.5 7.5 0 1 1 9 18.6z"/><path d="M9 11h.01M12 11h.01M15 11h.01"/>',
 "user":'<circle cx="12" cy="8" r="4"/><path d="M4.5 20.5c.9-4 3.8-6 7.5-6s6.6 2 7.5 6"/>',
 # 2.14.0: a booking forced at the desk; Nascondi nomi
 "bolt":'<path d="M13 2.5 4.5 13.5H11L10 21.5l8.5-11H13z"/>',
 "eye":'<path d="M2.5 12S6 5.5 12 5.5 21.5 12 21.5 12 18 18.5 12 18.5 2.5 12 2.5 12z"/><circle cx="12" cy="12" r="3"/>',
 # 2.14.0: the client card on a booking: VIP, absences, cancelled at the last moment, not bookable online; the waiting list; birthdays
 "star":'<path d="m12 3.5 2.6 5.3 5.9.9-4.3 4.1 1 5.8-5.2-2.8-5.2 2.8 1-5.8-4.3-4.1 5.9-.9z"/>',
 "absent":'<circle cx="12" cy="12" r="8.5"/><path d="m6 6 12 12"/>',
 "late":'<circle cx="12" cy="13" r="8"/><path d="M12 9v4l2.5 2.5M9.5 2.5h5"/>',
 "hourglass":'<path d="M6.5 3h11M6.5 21h11M7.5 3c0 5 9 5 9 9s-9 4-9 9M16.5 3c0 5-9 5-9 9s9 4 9 9"/>',
 "gift":'<rect x="3.5" y="8.5" width="17" height="4" rx="1"/><path d="M5 12.5V20h14v-7.5M12 8.5V20M12 8.5C10 4 6.5 4.5 7.5 7c.5 1.2 2.5 1.5 4.5 1.5zm0 0c2-4.5 5.5-4 4.5-1.5-.5 1.2-2.5 1.5-4.5 1.5z"/>'})
@register.simple_tag
def icon(name):
    from django.utils.safestring import mark_safe
    return mark_safe(f'<svg class="nav-ico" viewBox="0 0 24 24" aria-hidden="true">{ICONS.get(name) or ICONS["sliders"]}</svg>')

# 2.14.0: where a booking came from, as a small icon with its name (and the tag of the link, ?src=instagram)
ORIGIN_ICONS={"public":"globe","wordpress":"plug","desk":"shop","walkin":"user","import":"archive"}
@register.filter
def origin_icon(booking,full=""):
    """full: the name written next to the icon (the booking's page), otherwise only for screen readers and on hover."""
    from django.utils.html import format_html
    from django.utils.safestring import mark_safe
    if not booking.origin:return ""
    name=booking.get_origin_display()+(" · "+booking.source if booking.source else "")
    return format_html('<span class="origin" title="{}"><svg class="nav-ico" viewBox="0 0 24 24" aria-hidden="true">{}</svg><span class="{}">{}</span></span>',name,mark_safe(ICONS[ORIGIN_ICONS[booking.origin]]),"" if full else "sr-only",name)

@register.filter
def client_ok(booking,full=""):
    """2.14.0: the client answered Confermo to the reminder: a green tick, with the words on the booking's page."""
    from django.utils.html import format_html
    if not booking.snapshot.get("client_confirmed"):return ""
    return format_html('<span class="client-ok{}" title="Confermata dal cliente"><span aria-hidden="true">✓</span><span class="{}">Confermata dal cliente</span></span>'," full" if full else "","" if full else "sr-only")

@register.filter
def reason_label(code):
    """2.14.0: why a booking was cancelled, in words."""
    from core.booking import REASON_LABELS
    return REASON_LABELS.get(code,"")

@register.filter
def client_marks(booking,full=""):
    """2.14.0: VIP, absences, cancellations at the last moment and "not bookable online" of the booking's client
    (clients.mark, only for who sees the client cards); full: in words, with the tags, on the booking's page."""
    from django.utils.html import format_html,format_html_join
    from django.utils.safestring import mark_safe
    m=getattr(booking,"marks",None)
    if not m:return ""
    items=[("vip","star","","Cliente VIP")] if m["vip"] else []
    if m["no_show"]:items.append(("absent","absent",m["no_show"],f"{m['no_show']} assenz{'a' if m['no_show']==1 else 'e'}"))
    if m["late"]:items.append(("late","late",m["late"],f"{m['late']} disdett{'a' if m['late']==1 else 'e'} all'ultimo momento"))
    if m["blocked"]:items.append(("blocked","absent","","Non prenotabile online"))
    html=format_html_join("",'<span class="cmark {}{}" title="{}"><svg class="nav-ico" viewBox="0 0 24 24" aria-hidden="true">{}</svg>{}<span class="{}">{}</span></span>',
        ((cls," full" if full else "",label,mark_safe(ICONS[icon]),"" if full else n,"" if full else "sr-only",label) for cls,icon,n,label in items))
    if full and m["tags"]:html+=format_html_join("",'<span class="tag-chip">{}</span>',((t,) for t in m["tags"]))
    return html

@register.filter
def birthday(iso):
    """2.14.0: "1985-03-12" -> "12 marzo"."""
    from datetime import date
    from django.utils.formats import date_format
    try:return date_format(date.fromisoformat(iso),"j F").lower()
    except (TypeError,ValueError):return ""
