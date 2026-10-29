import json,re
from urllib.parse import urlparse
from zoneinfo import ZoneInfo,ZoneInfoNotFoundError
from django import forms
from django.core.exceptions import ValidationError
from .models import Location,Resource,Service,ServiceResource,Option,Schedule,ExceptionDay,Occurrence,Tenant,Campaign,ShiftRule

LABELS={"turnover":"A turni: il posto si libera dopo un tempo fisso","name":"Nome","location":"Sede","active":"Attivo","kind":"Tipo di prenotazione","description":"Descrizione","address":"Indirizzo","phone":"Telefono",
 "duration":"Durata (minuti)","interval":"Ogni quanti minuti si può prenotare","capacity":"Posti disponibili","max_party":"Persone massime per prenotazione",
 "buffer_before":"Pausa prima (minuti)","buffer_after":"Pausa dopo (minuti)","lead_minutes":"Preavviso minimo (minuti)","horizon_days":"Prenotabile fino a quanti giorni avanti",
 "checkin":"Ora di arrivo (check-in)","checkout":"Ora di partenza (check-out)","min_days":"Notti minime","max_days":"Notti massime",
 "cancel_cutoff_minutes":"Annullamento online fino a (minuti prima)","resource_label":"Come si chiamano le risorse",
 "service":"Servizio","resource":"Risorsa","purpose":"A cosa si applica","weekday":"Giorno","start":"Inizio","end":"Fine","valid_from":"Valido dal","valid_until":"Valido fino al",
 "date":"Data","closed":"Chiuso tutto il giorno","label":"Motivo","subject":"Oggetto","body":"Messaggio","scheduled":"Invio programmato","price":"Prezzo (€)"}
# 2.6.0: one plain line under every field, with an example
HELP={"interval":"Con 15 la pagina propone 19:00, 19:15, 19:30. Con 30 propone 19:00, 19:30",
 "buffer_before":"Minuti tenuti liberi prima di ogni prenotazione, per esempio per preparare la cabina. Di solito 0",
 "buffer_after":"Minuti tenuti liberi dopo ogni prenotazione, per esempio per riordinare. Di solito 0",
 "lead_minutes":"Quanto prima si deve prenotare online: 60 vuol dire almeno un'ora prima",
 "horizon_days":"Per esempio 60: si prenota al massimo due mesi avanti",
 "cancel_cutoff_minutes":"Fino a quando il cliente annulla da solo dal link nell'email. 0 vuol dire fino all'inizio, 1440 fino al giorno prima",
 "resource_label":"La parola che vede il cliente: Operatore, Cabina, Camera, Campo",
 "description":"Facoltativa: compare sotto il nome nella pagina prenotazioni",
 "min_days":"Per esempio 2 se non accetti soggiorni di una notte sola","max_days":"Il soggiorno più lungo prenotabile online",
 "checkin":"Da che ora arriva il cliente","checkout":"Entro che ora libera la camera o riconsegna",
 "address":"Compare nelle email di conferma","phone":"Compare nelle email di conferma",
 "price":"Facoltativo, per esempio 25 o 25,50. Compare in agenda, nelle prenotazioni e nella scheda del cliente, con il suo sconto se ne ha uno"}
SERVICE_KINDS=[("capacity","Posti per fascia oraria: ristorante, sala, lezione di gruppo"),("shift","Turni fissi, per esempio pranzo e cena, con i coperti di ciascuno"),
 ("resource","Appuntamento con una persona o una risorsa: parrucchiere, studio, campo"),("day","A notti o giorni: camere, appartamenti, noleggio"),("event","Evento o corso in date precise")]
# which fields each kind of booking uses; the others are not shown at all
FIELD_KINDS={"duration":{"resource","capacity"},"interval":{"resource","capacity"},"capacity":{"capacity","shift","day"},"max_party":{"capacity","shift","event","day"},
 "buffer_before":{"resource"},"buffer_after":{"resource","capacity"},"turnover":{"capacity"},"checkin":{"day"},"checkout":{"day"},"min_days":{"day"},"max_days":{"day"},
 "resource_label":{"resource","day"},"resources":{"resource","day"},"price":{"resource"}}
KIND_LABELS={"duration":{"capacity":"Quanto resta occupato il posto (minuti)","resource":"Durata dell'appuntamento (minuti)"},
 "capacity":{"capacity":"Posti (coperti)","shift":"Posti totali del locale","day":"Unità disponibili"},
 "max_party":{"capacity":"Persone massime per prenotazione","shift":"Persone massime per prenotazione","event":"Posti massimi per prenotazione","day":"Ospiti massimi per prenotazione"}}
KIND_HELP={"turnover":{"capacity":"Spento, di solito al ristorante: il cliente sceglie solo l'ora di arrivo e i coperti valgono per tutto il pranzo o tutta la cena. Acceso, per i locali a turni: ogni prenotazione tiene il posto per i minuti indicati qui sotto, poi si libera e si riprenota"},
 "buffer_after":{"capacity":"Minuti in cui il posto resta occupato dopo l'uscita, per esempio per riapparecchiare il tavolo. Di solito 0",
  "resource":"Minuti tenuti liberi dopo ogni prenotazione, per esempio per riordinare. Di solito 0"},
 "duration":{"capacity":"Al ristorante quanto dura un tavolo, per esempio 90: serve a sapere quando il posto si libera","resource":"Per esempio 45 per un taglio"},
 "capacity":{"capacity":"Il tetto massimo, per esempio 40. Senza turni sono i coperti di ogni pranzo e di ogni cena; a turni sono i posti occupati nello stesso momento",
  "shift":"Il tetto massimo anche se due turni si sovrappongono. I coperti di ogni turno si scrivono in Turni del ristorante",
  "day":"Conta solo se non usi camere o mezzi con il loro nome: in quel caso conta la disponibilità di ciascuno"},
 "max_party":{"capacity":"Per esempio 12: per gruppi più grandi il cliente ti chiama","shift":"Per esempio 12: per gruppi più grandi il cliente ti chiama","event":"Quanti posti può prendere una persona sola","day":"Ospiti massimi in una prenotazione"}}

def only_one(field,queryset):
    """With a single choice (one location, one service) the field is filled in and not shown."""
    items=list(queryset[:2])
    if len(items)==1:
        field.initial=items[0].pk;field.widget=forms.HiddenInput();field.required=True
        return items[0]
    return None

class ScopedForm(forms.ModelForm):
    confirm_conflicts=forms.BooleanField(required=False,label="Confermo la gestione delle prenotazioni in conflitto")
    single_location=True
    def __init__(self,*a,tenant=None,**kw):
        self.tenant=tenant
        kw.setdefault("label_suffix","")
        super().__init__(*a,**kw)
        for name,field in self.fields.items():
            field.label=LABELS.get(name,field.label)
            if not field.help_text and name in HELP:field.help_text=HELP[name]
            if isinstance(field,forms.ModelChoiceField):
                field.queryset=field.queryset.model.objects.all()
                if getattr(field,"empty_label",None):field.empty_label="Scegli"
            if isinstance(field,forms.DateTimeField):field.widget=forms.DateTimeInput(attrs={"type":"datetime-local"},format="%Y-%m-%dT%H:%M")
            elif isinstance(field,forms.DateField):field.widget=forms.DateInput(attrs={"type":"date"},format="%Y-%m-%d")
            elif isinstance(field,forms.TimeField):field.widget=forms.TimeInput(attrs={"type":"time"},format="%H:%M")
        if self.single_location and "location" in self.fields and self.fields["location"].required:only_one(self.fields["location"],Location.objects.filter(active=True).order_by("name"))
        self.instance.tenant=tenant
    def clean(self):
        d=super().clean()
        if "name" in d and len(d["name"])>120:raise ValidationError("Nome troppo lungo.")
        return d
class LocationForm(ScopedForm):
    class Meta:
        model=Location;fields=["name","address","phone","active"]
        labels={"name":"Nome della sede","active":"Attiva"}
        help_texts={"name":"Per esempio Centro o Via degli Angeli"}
WEEKDAYS=["Lunedì","Martedì","Mercoledì","Giovedì","Venerdì","Sabato","Domenica"]
def hour_field():return forms.TimeField(required=False,widget=forms.TimeInput(attrs={"type":"time"},format="%H:%M"))
class ResourceForm(ScopedForm):
    """An operator, a cabin, a room: what it does, and for people their own working days. A person takes one client
    at a time, so places only appear for rooms and vehicles (stays)."""
    services=forms.ModelMultipleChoiceField(queryset=Service.objects.none(),required=False,widget=forms.CheckboxSelectMultiple)
    own_hours=forms.BooleanField(required=False,label="Ha giorni e orari suoi",help_text="Spento: lavora negli orari di apertura dell'attività. Le ferie e i giorni liberi si segnano in Chiusure e giorni speciali")
    class Meta:
        model=Resource;fields=["name","location","capacity","description","photo_url","active"]
        labels={"capacity":"Persone massime per questa risorsa (0 = nessun limite)","photo_url":"Foto (indirizzo HTTPS)","active":"Attiva"}
        help_texts={"name":"Per esempio Marco, Cabina 1, Camera Blu, Campo A","capacity":"Serve per camere e mezzi: quante persone ci stanno"}
        widgets={"description":forms.Textarea(attrs={"rows":3})}
    def __init__(self,*a,**kw):
        super().__init__(*a,**kw)
        from .presets import units
        bookable=Service.objects.filter(active=True,kind__in=("resource","day"),location__active=True).select_related("location").order_by("location__name","name")
        f=self.fields["services"];f.queryset=bookable
        many=Location.objects.filter(active=True).count()>1
        f.label_from_instance=lambda s:f"{s.name} ({s.location.name})" if many else s.name
        one=units(getattr(self.tenant,"business_type",""))[0].lower()
        f.label="Cosa fa";f.help_text=f"Si prenota solo per i servizi spuntati. Un {one} nuovo parte con tutti"
        new=self.is_new=self.instance._state.adding
        self.initial["services"]=[s.pk for s in bookable] if new else list(ServiceResource.objects.filter(resource=self.instance).values_list("service_id",flat=True))
        if not Service.objects.filter(kind="day").exists():del self.fields["capacity"]
        # working days: one span a day here; with two spans a day (a lunch break) they stay in Orari di apertura
        rows=[] if new else list(Schedule.objects.filter(resource=self.instance,purpose="service"))
        self.split_hours=len({r.weekday for r in rows})<len(rows)
        self.grid_fields=["own_hours"]
        for d in range(7):
            mine=[r for r in rows if r.weekday==d]
            self.fields[f"day_{d}"]=forms.BooleanField(required=False,label=WEEKDAYS[d])
            self.fields[f"start_{d}"]=hour_field();self.fields[f"end_{d}"]=hour_field()
            self.initial[f"day_{d}"]=bool(mine) if rows else d<5
            self.initial[f"start_{d}"]=mine[0].start if mine else "09:00";self.initial[f"end_{d}"]=mine[0].end if mine else "18:00"
            self.grid_fields+=[f"day_{d}",f"start_{d}",f"end_{d}"]
        self.initial["own_hours"]=bool(rows)
        for name in self.grid_fields:
            self.fields[name].in_grid=True  # drawn as a week grid, not in the field list
            if self.split_hours:self.fields[name].disabled=True
    @property
    def hours_rows(self):return [{"on":self[f"day_{d}"],"start":self[f"start_{d}"],"end":self[f"end_{d}"]} for d in range(7)]
    def clean_photo_url(self):
        value=self.cleaned_data.get("photo_url","")
        if value and not value.startswith("https://"):raise ValidationError("Usa un indirizzo HTTPS.")
        return value
    def clean(self):
        d=super().clean()
        loc=d.get("location")
        if loc and any(s.location_id!=loc.pk for s in d.get("services",[])):raise ValidationError("Spunta solo servizi della sua sede.")
        if d.get("own_hours") and not self.split_hours:
            days=[n for n in range(7) if d.get(f"day_{n}")]
            if not days:raise ValidationError("Spunta almeno un giorno di lavoro, o spegni Ha giorni e orari suoi.")
            for n in days:
                if not d.get(f"start_{n}") or not d.get(f"end_{n}") or d[f"end_{n}"]<=d[f"start_{n}"]:raise ValidationError(f"{WEEKDAYS[n]}: l'orario di fine viene dopo quello di inizio.")
        return d
    def save(self,commit=True):
        obj=super().save(commit)
        d=self.cleaned_data
        ServiceResource.objects.filter(resource=obj).exclude(service__in=d.get("services",[])).delete()
        for s in d.get("services",[]):ServiceResource.objects.get_or_create(tenant=self.tenant,service=s,resource=obj)
        if not self.split_hours:
            Schedule.objects.filter(resource=obj,purpose="service").delete()
            if d.get("own_hours"):
                for n in range(7):
                    if d.get(f"day_{n}"):Schedule.objects.create(tenant=self.tenant,resource=obj,weekday=n,start=d[f"start_{n}"],end=d[f"end_{n}"])
        return obj
def default_kind(tenant):
    from .presets import PRESETS
    return PRESETS.get(getattr(tenant,"business_type",""),{}).get("kind","resource")
class ServiceForm(ScopedForm):
    resources=forms.ModelMultipleChoiceField(queryset=Resource.objects.none(),required=False,label="Risorse abilitate",widget=forms.CheckboxSelectMultiple)
    class Meta:
        model=Service
        fields=["name","location","kind","description","price","capacity","max_party","turnover","duration","interval","checkin","checkout","min_days","max_days","buffer_before","buffer_after","lead_minutes","horizon_days","cancel_cutoff_minutes","resource_label","fields","active"]
        labels={"name":"Nome del servizio","active":"Attivo: prenotabile online"}
        help_texts={"name":"Per esempio Cena, Taglio donna, Camera doppia"}
        widgets={"fields":forms.HiddenInput(),"description":forms.Textarea(attrs={"rows":2})}
    def __init__(self,*a,agency=False,**kw):
        super().__init__(*a,**kw)
        self.agency=agency
        # UUID primary keys exist before saving: a new service is told apart by _state.adding
        new=self.instance._state.adding
        kind=(self.initial.get("kind") or default_kind(self.tenant)) if new else self.instance.kind
        if new:self.instance.kind=kind
        self.kind=kind
        self.fields["resources"].queryset=Resource.objects.filter(active=True)
        from .presets import units
        one,many,_=units(getattr(self.tenant,"business_type",""))
        self.fields["resources"].label=many
        self.fields["resources"].help_text=f"Quali {many.lower()} si possono prenotare con questo servizio"
        if not new:self.initial["resources"]=ServiceResource.objects.filter(service=self.instance).values_list("resource_id",flat=True)
        # 2.8.0: with one location a new service starts with every operator ticked, instead of an error on saving
        elif not self.is_bound and Location.objects.filter(active=True).count()==1:self.initial["resources"]=list(Resource.objects.filter(active=True).values_list("pk",flat=True))
        if agency:
            # the agency chooses the kind: every field says which kinds use it and the page shows only those
            self.fields["kind"].choices=SERVICE_KINDS
            self.fields["kind"].help_text="Lo decide l'agenzia: l'attività non lo vede e non lo cambia"
            self.fields["kind"].widget.attrs["data-field-kinds"]=json.dumps({k:sorted(v) for k,v in FIELD_KINDS.items()})
            for name,kinds in FIELD_KINDS.items():
                f=self.fields[name];f.widget.attrs["data-kinds"]=" ".join(sorted(kinds))
                if name in KIND_LABELS:f.widget.attrs["data-labels"]=json.dumps(KIND_LABELS[name],ensure_ascii=False)
                if name in KIND_HELP:f.widget.attrs["data-helps"]=json.dumps(KIND_HELP[name],ensure_ascii=False)
        else:
            self.fields.pop("kind")
            for name,kinds in FIELD_KINDS.items():
                if kind not in kinds:self.fields.pop(name)
        for name in KIND_LABELS:
            if name in self.fields and kind in KIND_LABELS[name]:self.fields[name].label=KIND_LABELS[name][kind]
        for name in KIND_HELP:
            if name in self.fields and kind in KIND_HELP[name]:self.fields[name].help_text=KIND_HELP[name][kind]
    def clean_fields(self):
        schema=self.cleaned_data.get("fields") or []
        if not isinstance(schema,list) or len(schema)>12:raise ValidationError("Massimo 12 campi aggiuntivi.")
        keys=set()
        for item in schema:
            if not isinstance(item,dict) or not re.fullmatch(r"[a-z][a-z0-9_]{0,30}",str(item.get("key",""))) or item["key"] in keys:raise ValidationError("Identificativo campo non valido.")
            keys.add(item["key"])
            if item.get("type") not in ("text","number","checkbox","select") or not isinstance(item.get("label"),str) or not 1<=len(item["label"])<=80:raise ValidationError("Tipo o etichetta non validi.")
            if type(item.get("required",False)) is not bool:raise ValidationError("Obbligatorietà non valida.")
            if item["type"]=="select" and (not isinstance(item.get("choices"),list) or not 1<=len(item["choices"])<=30 or any(not isinstance(c,str) or not 1<=len(c)<=80 for c in item["choices"])):raise ValidationError("Inserisci le opzioni del campo.")
        return schema
    def clean(self):
        d=super().clean()
        kind=d.get("kind") or self.kind
        if d.get("price") is not None and not 0<=d["price"]<=100000:self.add_error("price","Prezzo tra 0 e 100.000 €.")
        bounds={"duration":(5,1440),"interval":(5,240),"capacity":(1,10000),"max_party":(1,1000),"buffer_before":(0,1440),"buffer_after":(0,1440),"lead_minutes":(0,525600),"horizon_days":(1,730),"max_days":(1,366),"min_days":(1,366),"cancel_cutoff_minutes":(0,525600)}
        for key,(low,high) in bounds.items():
            if key in d and d[key] is not None and not low<=d[key]<=high:self.add_error(key,f"Valore tra {low} e {high}.")
        if kind=="resource" and "resources" in self.fields and not d.get("resources"):self.add_error("resources","Seleziona almeno una voce: se l'elenco è vuoto, creala prima nella pagina accanto a Servizi.")
        if kind=="day" and d.get("min_days") and d.get("max_days") and d["min_days"]>d["max_days"]:self.add_error("min_days","Le notti minime superano le massime.")
        location=d.get("location") or getattr(self.instance,"location",None)
        if d.get("resources") and any(r.location_id!=getattr(location,"pk",None) for r in d["resources"]):self.add_error("resources","Le risorse devono appartenere alla sede del servizio.")
        return d
    def save(self,commit=True):
        service=super().save(commit)
        if commit and "resources" in self.fields:
            ServiceResource.objects.filter(service=service).delete()
            if service.kind in ("resource","day"):  # day: the linked resources are the named units
                for r in self.cleaned_data["resources"]:ServiceResource.objects.create(tenant=self.tenant,service=service,resource=r)
        return service
class OptionForm(ScopedForm):
    class Meta:
        model=Option;fields=["service","name","duration","price","active"]
        labels={"name":"Nome della variante","active":"Attiva"}
        help_texts={"name":"Per esempio Taglio corto o Taglio lungo","duration":"La durata di questa variante, al posto di quella del servizio","price":"Facoltativo: vuoto vuol dire il prezzo del servizio"}
    def __init__(self,*a,**kw):
        super().__init__(*a,**kw)
        only_one(self.fields["service"],Service.objects.filter(active=True).order_by("name"))
    def clean_duration(self):
        val=self.cleaned_data["duration"]
        if not 5<=val<=1440:raise ValidationError("Durata tra 5 e 1440 minuti.")
        return val
    def clean_price(self):
        val=self.cleaned_data.get("price")
        if val is not None and not 0<=val<=100000:raise ValidationError("Prezzo tra 0 e 100.000 €.")
        return val
class CalendarForm(ScopedForm):
    confirm_conflicts=forms.BooleanField(required=False,label="Confermo la modifica anche se esistono prenotazioni da ricontattare")
    single_location=False
    def __init__(self,*a,**kw):
        super().__init__(*a,**kw)
        # an optional narrowing: shown only when there is something to choose from
        for name,qs,empty in (("location",Location.objects.all(),"Tutte le sedi"),("service",Service.objects.all(),"Tutti i servizi"),("resource",Resource.objects.all(),"Tutte le risorse")):
            if name in self.fields:
                if qs.count()<2:self.fields.pop(name)
                else:self.fields[name].empty_label=empty;self.fields[name].label="Solo per "+{"location":"la sede","service":"il servizio","resource":"la risorsa"}[name];self.fields[name].help_text="Lascia "+empty.lower()+" se vale per tutta l'attività"
        if "purpose" in self.fields:
            if not Service.objects.filter(kind="day").exists():self.fields.pop("purpose")
            else:self.fields["purpose"].help_text="Apertura vale per le prenotazioni normali; arrivo e partenza per soggiorni e noleggi"
    def clean(self):
        d=super().clean()
        if sum(bool(d.get(k)) for k in ("location","service","resource"))>1:raise ValidationError("Scegli un solo livello: sede, servizio oppure risorsa. Nessuno = tutta l'attività.")
        if not d.get("closed"):
            if not d.get("start") or not d.get("end") or d["start"]==d["end"]:raise ValidationError("Indica una fascia con inizio e fine differenti.")
        if d.get("valid_from") and d.get("valid_until") and d["valid_from"]>d["valid_until"]:raise ValidationError("Intervallo date non valido.")
        return d
class ScheduleForm(CalendarForm):
    class Meta:
        model=Schedule;fields=["purpose","location","service","resource","weekday","start","end","valid_from","valid_until"]
        help_texts={"start":"Per esempio 12:00","end":"Per esempio 15:00. Per pranzo e cena crea due fasce","valid_from":"Vuoto: da subito","valid_until":"Vuoto: sempre"}
class ExceptionForm(CalendarForm):
    class Meta:
        model=ExceptionDay;fields=["purpose","location","service","resource","date","closed","start","end","label"]
        help_texts={"closed":"Togli la spunta per un orario speciale e scrivilo in Inizio e Fine","label":"Per esempio Ferie o Natale: lo vedi solo tu"}
class OccurrenceForm(ScopedForm):
    class Meta:
        model=Occurrence;fields=["service","start","end","capacity","active"]
        labels={"capacity":"Posti per questa data","active":"Attiva"}
    def __init__(self,*a,**kw):
        super().__init__(*a,**kw)
        self.fields["service"].queryset=Service.objects.filter(kind="event")
        only_one(self.fields["service"],Service.objects.filter(kind="event"))
    def clean(self):
        d=super().clean()
        if d.get("service") and d["service"].kind not in ("event","shift"):raise ValidationError("Seleziona un servizio evento.")
        if d.get("end") and d.get("start") and (d["end"]<=d["start"] or (d["end"]-d["start"]).days>7):raise ValidationError("Durata evento non valida.")
        if d.get("capacity",0)<1:raise ValidationError("Capienza positiva richiesta.")
        return d
class CampaignForm(ScopedForm):
    class Meta:model=Campaign;fields=["name","subject","body","scheduled"]
    def clean_subject(self):
        value=self.cleaned_data["subject"]
        if "\n" in value or "\r" in value:raise ValidationError("Oggetto non valido.")
        return value
    def clean_body(self):
        value=self.cleaned_data["body"]
        if len(value)>20000:raise ValidationError("Messaggio troppo lungo.")
        return value
class TenantForm(forms.ModelForm):
    logo_file=forms.FileField(required=False,label="Logo PNG trasparente, JPEG o WebP, massimo 1 MB, anche orizzontale")
    embed_origins_text=forms.CharField(required=False,label="Siti autorizzati a mostrare il widget (una origine HTTPS per riga)",widget=forms.Textarea(attrs={"rows":3}))
    class Meta:
        model=Tenant
        fields=["name","slug","timezone","status","trial_until","maintenance","marketing_enabled","smtp_daily_limit","smtp_hourly_limit","brand_color","tagline","website","privacy_text","consent_text","retention_days","receive_schedule"]
        labels={"name":"Nome attività","slug":"Indirizzo breve","timezone":"Fuso orario","status":"Stato","trial_until":"Fine prova","maintenance":"Manutenzione tecnica: sospendi temporaneamente prenotazioni e portale","marketing_enabled":"Abilita marketing email","smtp_daily_limit":"Limite email giornaliero","smtp_hourly_limit":"Limite email orario","brand_color":"Colore del marchio","tagline":"Sottotitolo","website":"Sito web","privacy_text":"Informativa prenotazioni","consent_text":"Testo consenso marketing","retention_days":"Conservazione prenotazioni (giorni)","receive_schedule":"Limita anche l'orario di invio delle richieste online"}
        widgets={"trial_until":forms.DateInput(attrs={"type":"date"}),"brand_color":forms.TextInput(attrs={"type":"color"}),"privacy_text":forms.Textarea(attrs={"rows":4}),"consent_text":forms.Textarea(attrs={"rows":3})}
    def __init__(self,*a,**kw):
        kw.setdefault("label_suffix","")
        super().__init__(*a,**kw)
        self.initial["embed_origins_text"]="\n".join(self.instance.embed_origins)
        if self.instance._state.adding is False:self.fields["slug"].disabled=True
        # 2.6.0: the type of business is chosen in Configura l'attività, together with the locations
        from django.conf import settings
        if settings.EDITION=="private":
            # Private has no plan, trial or subscription state.
            for name in ("status","trial_until"):self.fields.pop(name,None)
    def clean_logo_file(self):
        value=self.cleaned_data.get("logo_file")
        if value:
            from .site import clean_brand
            return clean_brand(value)  # 2.8.0: (png, width/height), wide logos included
        return None
    def clean_timezone(self):
        val=self.cleaned_data["timezone"]
        try:ZoneInfo(val)
        except ZoneInfoNotFoundError:raise ValidationError("Fuso orario non valido.")
        return val
    def clean_brand_color(self):
        val=self.cleaned_data["brand_color"]
        if not re.fullmatch(r"#[0-9a-fA-F]{6}",val):raise ValidationError("Colore non valido.")
        return val
    def clean_embed_origins_text(self):
        values=self.cleaned_data["embed_origins_text"].split()
        if len(values)>10:raise ValidationError("Massimo 10 siti.")
        from .wpconnect import valid_origin
        for v in values:
            if not valid_origin(v) or urlparse(v).path:raise ValidationError("Usa solo origini HTTPS, senza percorsi o caratteri jolly.")
        return [valid_origin(v) for v in values]
    def clean(self):
        d=super().clean()
        for k in ("smtp_daily_limit","smtp_hourly_limit"):
            if k in d and not 1<=d[k]<=10000:self.add_error(k,"Limite tra 1 e 10.000.")
        if "retention_days" in d and not 30<=d["retention_days"]<=3650:self.add_error("retention_days","Valore tra 30 e 3650 giorni.")
        if "status" in self.fields and d.get("status")=="trial" and not d.get("trial_until"):self.add_error("trial_until","Indica la fine della prova.")
        return d
    def save(self,commit=True):
        self.instance.embed_origins=self.cleaned_data["embed_origins_text"]
        if self.cleaned_data.get("logo_file"):self.instance.logo,self.instance.logo_ratio=self.cleaned_data["logo_file"]
        return super().save(commit)
# 2.6.0: credentials of a mailbox, not of the person signed in: password managers must not fill or save them
NO_AUTOFILL={"autocomplete":"new-password","data-1p-ignore":"","data-lpignore":"true","data-bwignore":"","data-form-type":"other"}
class SMTPForm(forms.Form):
    def __init__(self,*a,**kw):
        kw.setdefault("label_suffix","");super().__init__(*a,**kw)
    host=forms.CharField(label="Server SMTP",max_length=253)
    port=forms.ChoiceField(label="Porta",choices=[("587","587 — STARTTLS"),("465","465 — TLS")])
    username=forms.CharField(label="Utente SMTP",max_length=254,required=False,help_text="La casella con cui il server ti fa entrare, per esempio smtp@tuodominio.it")
    password=forms.CharField(label="Password SMTP (vuoto = mantieni)",widget=forms.PasswordInput(attrs=NO_AUTOFILL),required=False,max_length=1024)
    sender_name=forms.CharField(label="Nome del mittente, per esempio Booking Suite TD",max_length=80,required=False)
    sender=forms.EmailField(label="Email del mittente, quella che vedono i clienti",help_text="Per esempio info@tuodominio.it. L'email parte dalla casella dell'utente SMTP e mostra questo indirizzo: il server deve accettarlo, di solito basta che sia dello stesso dominio")
    reply_to=forms.EmailField(label="Rispondi a",required=False,help_text="Facoltativo: dove arrivano le risposte dei clienti, se diverso dal mittente")
    verified=forms.BooleanField(label="Ho verificato il dominio mittente e la configurazione SPF/DKIM/DMARC")
    field_order=["host","port","username","password","sender_name","sender","reply_to","verified"]
    def clean_sender_name(self):
        value=self.cleaned_data["sender_name"].strip()
        if any(c in value for c in "\r\n<>\"@"):raise forms.ValidationError("Solo il nome, senza a capo, virgolette o indirizzi.")
        return value

class ShiftForm(ScopedForm):
    class Meta:
        model=ShiftRule
        fields=["name","service","weekday","start","end","capacity","valid_from","valid_until","active"]
        labels={"name":"Nome del turno","capacity":"Coperti del turno","active":"Attivo"}
        help_texts={"name":"Per esempio Pranzo o Cena","capacity":"Quanti coperti si possono prenotare in questo turno, al massimo i posti totali del servizio","valid_from":"Vuoto: da subito","valid_until":"Vuoto: sempre"}
    def __init__(self,*a,**kw):
        super().__init__(*a,**kw)
        self.fields["service"].queryset=Service.objects.filter(kind="shift")
        only_one(self.fields["service"],Service.objects.filter(kind="shift"))
    def clean(self):
        d=super().clean()
        if d.get("service") and d["service"].kind!="shift":raise ValidationError("Seleziona un servizio Ristorante / pizzeria a turni.")
        if d.get("start")==d.get("end"):raise ValidationError("Inizio e fine devono essere differenti.")
        if d.get("capacity",0)<1 or (d.get("service") and d["capacity"]>d["service"].capacity):raise ValidationError("I coperti del turno devono essere tra 1 e la capienza del servizio.")
        if d.get("valid_from") and d.get("valid_until") and d["valid_from"]>d["valid_until"]:raise ValidationError("Periodo non valido.")
        return d
