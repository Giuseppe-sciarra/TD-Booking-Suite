from django import template
register=template.Library()
@register.filter
def status_name(v):
 return {"confirmed":"Confermata","completed":"Completata","cancelled":"Annullata","no_show":"Assente","none":"Non richiesto","pending":"In attesa","yes":"Confermato","revoked":"Revocato","draft":"Bozza","queued":"In coda","paused":"In pausa","accepted":"Accettata da SMTP","failed":"Fallita","unknown":"Esito incerto","skipped":"Esclusa","sending":"Invio in corso","active":"Attivo","trial":"Prova","suspended":"Sospeso"}.get(v,v)
@register.filter
def row_label(o):
 if hasattr(o,"name"):return o.name
 hm=lambda t:t.strftime("%H:%M") if t else ""
 if hasattr(o,"weekday"):return o.get_weekday_display()+", "+hm(o.start)+"–"+hm(o.end)
 if hasattr(o,"closed"):return o.date.strftime("%d/%m/%Y")+", "+("chiuso" if o.closed else hm(o.start)+"–"+hm(o.end))+(", "+o.label if o.label else "")
 if hasattr(o,"start"):return o.service.name+", "+o.start.strftime("%d/%m/%Y %H:%M")
 return str(o.pk)

@register.filter
def service_summary(s):
 """2.6.0: what a service is, in words, under its name in the list."""
 if s.kind=="capacity" and not s.turnover:return f"{s.capacity} coperti per ogni pranzo e cena, il cliente sceglie l'ora di arrivo"
 if s.kind=="capacity":return f"A turni: {s.capacity} posti, ogni posto si libera dopo {s.duration} minuti"
 if s.kind=="shift":return f"Turni: {s.capacity} posti totali, i coperti di ogni turno sono in Turni del ristorante"
 if s.kind=="resource":return f"Appuntamento di {s.duration} minuti"+(" con "+s.resource_label.lower() if s.resource_label else "")
 if s.kind=="day":return f"A notti: da {s.min_days} a {s.max_days}, arrivo alle {s.checkin.strftime('%H:%M') if hasattr(s.checkin,'strftime') else s.checkin}"
 if s.kind=="event":return "Evento con date: i posti sono in Eventi con data"
 return ""

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
