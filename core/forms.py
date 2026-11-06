import json,re,uuid,secrets
from django.conf import settings
from datetime import timedelta
from urllib.parse import urlparse
from zoneinfo import ZoneInfo,ZoneInfoNotFoundError
from django import forms
from django.core.exceptions import ValidationError
from .models import Location,Product,Resource,Service,ServiceResource,Option,Schedule,ExceptionDay,Occurrence,Tenant,Campaign,ShiftRule,TimeBlock,BLOCK_REASONS
from .calendar import local_datetime

LABELS={"turnover":"A turni: il posto si libera dopo un tempo fisso","name":"Nome","location":"Sede","active":"Attivo","kind":"Tipo di prenotazione","description":"Descrizione","address":"Indirizzo","phone":"Telefono",
 "duration":"Durata (minuti)","interval":"Ogni quanti minuti si può prenotare","capacity":"Posti disponibili","max_party":"Persone massime per prenotazione",
 "buffer_before":"Pausa prima (minuti)","buffer_after":"Pausa dopo (minuti)","lead_minutes":"Preavviso minimo (minuti)","horizon_days":"Prenotabile fino a quanti giorni avanti",
 "checkin":"Ora di arrivo (check-in)","checkout":"Ora di partenza (check-out)","min_days":"Notti minime","max_days":"Notti massime",
 "cancel_cutoff_minutes":"Annullamento online fino a (minuti prima)","resource_label":"Come si chiamano le risorse",
 "service":"Servizio","resource":"Risorsa","purpose":"A cosa si applica","weekday":"Giorno","start":"Inizio","end":"Fine","valid_from":"Valido dal","valid_until":"Valido fino al",
 "last_arrival":"Ultimo arrivo (minuti prima della chiusura)",
 "request_all":"Su richiesta: confermate voi ogni prenotazione online","request_above":"Su richiesta oltre quante persone","request_allergies":"Su richiesta se il cliente segnala intolleranze o allergie",
 "instructions":"Istruzioni prima dell'arrivo",
 "pace_covers":"Persone massime per ogni orario di arrivo","pace_bookings":"Prenotazioni massime per ogni orario di arrivo","pace_desk":"Il ritmo degli arrivi vale anche al banco",
 "online_capacity":"Posti prenotabili online","party_durations":"Durata per numero di persone",
 "remote_url":"A distanza: link della videochiamata","recall_days":"Richiamo dopo quanti giorni","test_days":"Richiede un test fatto da non più di quanti giorni",
 "date":"Data","closed":"Chiuso tutto il giorno","label":"Motivo","subject":"Oggetto","body":"Messaggio","scheduled":"Invio programmato","price":"Prezzo (€)"}
# 2.6.0: one plain line under every field, with an example
HELP={"interval":"Con 15 la pagina propone 19:00, 19:15, 19:30. Con 30 propone 19:00, 19:30",
 "buffer_before":"Minuti tenuti liberi prima di ogni prenotazione, per esempio per preparare la cabina. Di solito 0",
 "buffer_after":"Minuti tenuti liberi dopo ogni prenotazione, per esempio per riordinare. Di solito 0",
 "lead_minutes":"Quanto prima si deve prenotare online: 60 vuol dire almeno un'ora prima. Al banco non conta",
 "horizon_days":"Per esempio 60: online si prenota al massimo due mesi avanti. Al banco non conta",
 "request_all":"Spento, di solito: la prenotazione online è confermata subito. Acceso: aspetta la vostra conferma, tiene intanto il posto e la accettate o rifiutate dalla Dashboard; il cliente riceve un'email a ogni passo",
 "request_above":"Per esempio 8: i gruppi più grandi aspettano la vostra conferma, gli altri sono confermati subito. 0 vuol dire mai",
 "request_allergies":"La richiesta aspetta la vostra conferma, così la cucina può dire se è in grado di accogliere il cliente",
 "cancel_cutoff_minutes":"Fino a quando il cliente annulla da solo dal link nell'email. 0 vuol dire fino all'inizio, 1440 fino al giorno prima",
 "resource_label":"La parola che vede il cliente: Operatore, Cabina, Camera, Campo",
 "description":"Facoltativa: compare sotto il nome nella pagina prenotazioni",
 "min_days":"Per esempio 2 se non accetti soggiorni di una notte sola. 0 per il noleggio in giornata: ritiro e riconsegna lo stesso giorno","max_days":"Il soggiorno più lungo prenotabile online",
 "checkin":"Da che ora arriva il cliente","checkout":"Entro che ora libera la camera o riconsegna",
 "last_arrival":"Per esempio 60: con il pranzo dalle 12 alle 15 l'ultimo arrivo è alle 14, con la cena fino alle 23:30 alle 22:30. 0 vuol dire fino alla chiusura",
 "address":"Compare nelle email di conferma","phone":"Compare nelle email di conferma",
 "instructions":"Facoltative: come ritirare le chiavi, dove trovarsi, cosa portare. Le riceve il cliente con il messaggio Prima dell'arrivo, se lo accendete in Notifiche",
 "pace_covers":"Il ritmo degli arrivi, per la cucina: con un orario ogni 15 minuti e 10, alle 20:00 arrivano al massimo 10 persone, alle 20:15 altre 10. Vale per la pagina online. 0 vuol dire nessun limite",
 "pace_bookings":"Per esempio 3: allo stesso orario arrivano al massimo tre prenotazioni. 0 vuol dire nessun limite",
 "pace_desk":"Spento, di solito: al banco decidete voi. Acceso: anche al banco, e si supera solo con Forza",
 "online_capacity":"Per esempio 30 con 40 posti: gli ultimi 10 restano per il telefono e per chi entra senza prenotare. 0 vuol dire tutti",
 "party_durations":"Quando il posto si libera dopo un tempo: per esempio 2:90, 4:105 vuol dire fino a 2 persone 90 minuti, fino a 4 persone 105, i gruppi più grandi la durata qui sopra. Vuoto: la stessa durata per tutti",
 "remote_url":"Facoltativo, per esempio la stanza di TD Meet: il cliente lo riceve nella conferma e nei promemoria. Vuoto per i servizi in sede",
 "recall_days":"Per esempio 180 per un controllo dopo sei mesi, 35 per il colore: il cliente compare in Richiami se non ha già un nuovo appuntamento. 0 vuol dire nessun richiamo",
 "test_days":"Per esempio 180 per la tinta: se il test segnato nella scheda del cliente manca o è più vecchio, la prenotazione lo dice al banco, senza bloccare. 0 vuol dire nessun test",
 "price":"Facoltativo, per esempio 25 o 25,50. Compare in agenda, nelle prenotazioni e nella scheda del cliente, con il suo sconto se ne ha uno"}
SERVICE_KINDS=[("capacity","Posti per fascia oraria: ristorante, sala, lezione di gruppo"),("shift","Turni fissi, per esempio pranzo e cena, con i coperti di ciascuno"),
 ("resource","Appuntamento con una persona o una risorsa: parrucchiere, studio, campo"),("day","A notti o giorni: camere, appartamenti, noleggio"),("event","Evento o corso in date precise")]
# which fields each kind of booking uses; the others are not shown at all
FIELD_KINDS={"duration":{"resource","capacity"},"interval":{"resource","capacity"},"capacity":{"capacity","shift","day"},"max_party":{"capacity","shift","event","day"},
 "buffer_before":{"resource"},"buffer_after":{"resource","capacity"},"turnover":{"capacity"},"last_arrival":{"capacity"},"checkin":{"day"},"checkout":{"day"},"min_days":{"day"},"max_days":{"day"},
 "resource_label":{"resource","day"},"resources":{"resource","day"},"price":{"resource"},"instructions":{"day","event"},
 "online_capacity":{"capacity","shift"},"pace_covers":{"capacity"},"pace_bookings":{"capacity"},"pace_desk":{"capacity"},"party_durations":{"capacity"},
 "remote_url":{"resource","capacity","event"},"recall_days":{"resource","capacity"},"test_days":{"resource"},
 "pause_start":{"resource"},"pause_minutes":{"resource"},"client_picks":{"resource"}}
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
 "max_party":{"capacity":"Per esempio 12: per gruppi più grandi il cliente ti chiama, e al banco li prenoti tu","shift":"Per esempio 12: per gruppi più grandi il cliente ti chiama, e al banco li prenoti tu","event":"Quanti posti può prendere una persona sola","day":"Ospiti massimi in una prenotazione"}}

MODES=[("service","Un turno per servizio"),("fixed","Turni fissi"),("rotation","Rotazione con durata")]
def only_one(field,queryset):
    """With a single choice (one location, one service) the field is filled in and not shown."""
    items=list(queryset[:2])
    if len(items)==1:
        field.initial=items[0].pk;field.widget=forms.HiddenInput();field.required=True
        return items[0]
    return None

LIMIT_WORDS=(("day","al giorno"),("week","a settimana"),("month","al mese"))
def add_limits(form,who):
    """2.14.0: bookings and booked minutes at most per day, week, month (booking.limited); empty = no limit."""
    for p,words in LIMIT_WORDS:
        for k,what in (("n","Prenotazioni"),("m","Minuti prenotati")):
            name=f"lim_{p}_{k}";form.fields[name]=forms.IntegerField(required=False,min_value=0,max_value=100000,label=f"{what} al massimo {words}",
                help_text=f"Online, per {who}. Vuoto o 0: nessun limite. Al banco si supera con Forza" if (p,k)==("day","n") else "")
            form.initial[name]=(form.instance.limits or {}).get(f"{p}_{k}") or None
def read_limits(d):return {k[4:]:d[k] for k in d if k.startswith("lim_") and d[k]}
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
        model=Location;fields=["name","address","phone","capacity","active","cin","cir","ross_code","tax_rate","tax_nights","tax_age","assign","min_gap","tables","tables_online","visit_split","visit_wait"]
        labels={"visit_split":"Più servizi nella stessa visita","visit_wait":"Attesa massima tra due servizi (minuti)","tables":"Tavoli","tables_online":"Online accetta solo se c'è un tavolo adatto libero","name":"Nome della sede","active":"Attiva","assign":"«Il primo disponibile» va a","min_gap":"Online, niente buchi più corti di (minuti)","capacity":"Coperti di tutto il locale","cin":"CIN","cir":"CIR o codice regionale","ross_code":"Codice struttura Ross1000","tax_rate":"Imposta di soggiorno, € a persona a notte","tax_nights":"Notti tassate al massimo","tax_age":"Esenti sotto i … anni"}
        help_texts={"visit_split":"Quando il cliente prenota per esempio colore, taglio e piega insieme: sempre con lo stesso operatore, con chi è libero, o con un altro solo se uno da solo non riesce","visit_wait":"Quanto il cliente può aspettare tra la fine di un servizio e l'inizio del successivo. 0 = subito dopo","tables":"Ristoranti: sale e tavoli si preparano in Sale e tavoli. Il cliente sceglie al massimo la sala, mai il tavolo","tables_online":"Acceso: online un orario compare solo se c'è un tavolo libero con i posti giusti (o due da unire)","name":"Nome della sede","assign":"Chi riceve la prenotazione quando il cliente non sceglie con chi. Chi è segnato «Fuori da Il primo disponibile» non la riceve mai",
            "min_gap":"0 = tutti gli orari liberi. Con 30, online non compaiono gli orari che lascerebbero meno di 30 minuti tra due appuntamenti dello stesso operatore; al banco si vedono tutti","capacity":"Per i ristoranti: il tetto che tutti i servizi della sede dividono, per esempio pranzo alla carta e menu di gruppo nella stessa sala. 0 vuol dire nessun limite","cin":"Strutture ricettive e case vacanze: compare nella pagina di prenotazione e nelle email","ross_code":"Per il file ISTAT dei movimenti, te lo dà l'ufficio regionale del turismo","tax_rate":"Vuoto se il comune non la chiede. Roma, B&B: 6","tax_nights":"0 = tutte. Roma: 10","tax_age":"0 = nessuna esenzione per età. Roma: 10"}
    def __init__(self,*a,**kw):
        super().__init__(*a,**kw)
        self.fields["min_gap"].required=False;self.fields["visit_split"].required=False;self.fields["visit_wait"].required=False
        if getattr(self.tenant,"business_type","")!="ristorante":
            for name in ("tables","tables_online"):self.fields.pop(name)
        else:self.fields["tables"].required=False  # a form without it (older pages) keeps the tables off
    def clean_tables(self):return self.cleaned_data.get("tables") or "off"
    def clean_visit_split(self):return self.cleaned_data.get("visit_split") or "need"
    def clean_visit_wait(self):
        v=self.cleaned_data.get("visit_wait") or 0
        if v>240:raise ValidationError("Valore tra 0 e 240.")
        return v
    def clean_min_gap(self):
        v=self.cleaned_data["min_gap"] or 0
        if v>240:raise ValidationError("Valore tra 0 e 240.")
        return v
    def clean_capacity(self):
        val=self.cleaned_data.get("capacity") or 0
        if val>10000:raise ValidationError("Valore tra 0 e 10000.")
        return val
WEEKDAYS=["Lunedì","Martedì","Mercoledì","Giovedì","Venerdì","Sabato","Domenica"]
def hour_field():return forms.TimeField(required=False,widget=forms.TimeInput(attrs={"type":"time"},format="%H:%M"))
class ResourceForm(ScopedForm):
    """An operator, a cabin, a room: its photo, what it does (2.11.0: with its own duration and price for each
    service, as the days with their hours), and for people their own working days. A person takes one client at a
    time, so places only appear for rooms and vehicles (stays)."""
    photo_file=forms.FileField(required=False,label="Foto",help_text="PNG, JPEG o WebP fino a 5 MB: compare in agenda e quando il cliente sceglie con chi prenotare")
    photo_remove=forms.BooleanField(required=False,label="Togli la foto")
    services=forms.ModelMultipleChoiceField(queryset=Service.objects.none(),required=False,widget=forms.CheckboxSelectMultiple)
    own_hours=forms.BooleanField(required=False,label="Ha giorni e orari suoi",help_text="Spento: lavora negli orari di apertura dell'attività. Le ferie e i giorni liberi si segnano in Chiusure e giorni speciali")
    # 2.14.0: the calendars (feeds.py): its bookings to subscribe from a phone, an outside calendar read as Occupato
    feed_on=forms.BooleanField(required=False,label="Calendario da aggiungere al telefono",help_text="Un link privato con le sue prenotazioni, servizio e orario, da aggiungere a Google Calendar, Outlook o iPhone come calendario a cui iscriversi")
    feed_new=forms.BooleanField(required=False,label="Cambia il link del calendario",help_text="Il link di prima smette di funzionare: serve se è finito in mani sbagliate")
    busy_url=forms.CharField(required=False,max_length=2000,label="Calendario esterno da segnare come occupato",help_text="L'indirizzo segreto .ics del suo calendario Google, Outlook o iCloud: ogni 15 minuti i suoi impegni diventano orari occupati, senza titoli. Vuoto: nessuno")
    class Meta:
        model=Resource;fields=["name","location","capacity","description","photo_url","active","feed_names","secondary","group","auto_excluded","priority","away_from","away_until","away_by"]
        labels={"capacity":"Persone massime per questa risorsa (0 = nessun limite)","photo_url":"Oppure l'indirizzo HTTPS di una foto","active":"Attiva","feed_names":"Nel calendario anche il nome del cliente",
            "secondary":"Si usa insieme all'operatore: cabina, lettino, macchinario, auto","group":"Gruppo","auto_excluded":"Fuori da «Il primo disponibile»","priority":"Ordine in «Il primo disponibile» (1 = per primo)","away_from":"Fuori ufficio dal","away_until":"Fuori ufficio fino al","away_by":"Lo sostituisce"}
        help_texts={"name":"Per esempio Marco, Cabina 1, Camera Blu, Campo A","capacity":"Serve per camere e mezzi: quante persone ci stanno",
            "secondary":"Il cliente non la sceglie e non ha una colonna in agenda: i servizi che la spuntano si prenotano solo se è libera lei (o un'altra spuntata) insieme all'operatore",
            "group":"Solo per cabine e macchinari, per esempio Cabina o Laser: un servizio che spunta voci di gruppi diversi ne vuole una libera per ogni gruppo. Vuoto: tutte nello stesso gruppo",
            "auto_excluded":"Si prenota solo se il cliente o il banco la scelgono","priority":"Conta solo se la sede dà «Il primo disponibile» secondo l'ordine scelto",
            "away_from":"Vuoto: in servizio. Nei giorni fuori ufficio non si prenota con lui","away_by":"Facoltativo: in quei giorni prende «Il primo disponibile» e online lo proponiamo al suo posto"}
        widgets={"description":forms.Textarea(attrs={"rows":3}),"away_from":forms.DateInput(attrs={"type":"date"},format="%Y-%m-%d"),"away_until":forms.DateInput(attrs={"type":"date"},format="%Y-%m-%d")}
    def __init__(self,*a,**kw):
        super().__init__(*a,**kw)
        from .presets import units,service_word
        from .prices import euro
        self.order_fields(["name","location","capacity","description","photo_file","photo_remove","photo_url","active","secondary","group","auto_excluded","priority"])
        self.fields["priority"].required=False
        new=self.is_new=self.instance._state.adding
        self.fields["away_by"].queryset=Resource.objects.filter(active=True,secondary=False).exclude(pk=self.instance.pk).order_by("name");self.fields["away_by"].empty_label="Nessuno"
        add_limits(self,"questa persona")
        slug=getattr(self.tenant,"slug","")
        self.photo_preview=self.instance.photo_link(slug) if not new and self.instance.photo_at else ""
        from .feeds import link,busy_url
        r=self.instance;self.initial["feed_on"]=bool(r.feed);self.initial["busy_url"]=busy_url(r) if not new else ""
        if r.feed:self.fields["feed_on"].help_text="Link privato da aggiungere al calendario del telefono: "+link(r,slug)
        else:del self.fields["feed_new"]
        if r.busy_error:self.fields["busy_url"].help_text="Ultima lettura non riuscita: "+r.busy_error
        elif r.busy_at:
            from zoneinfo import ZoneInfo
            self.fields["busy_url"].help_text+=f". Letto il {r.busy_at.astimezone(ZoneInfo(self.tenant.timezone)):%d/%m alle %H:%M}"
        from . import gcal  # 2.14.0: Google Calendar, both ways, once the agency set the OAuth client (its own box under the form)
        ready=bool(self.tenant and gcal.config(self.tenant))
        self.google=None if new or not (ready or r.google or r.google_error) else {"ready":ready,"on":bool(r.google),"error":r.google_error,"at":r.google_at,"account":(st:=gcal.state(r)).get("account",""),"calendars":st.get("calendars",[]),"read":st.get("read","primary"),"write":st.get("write","primary")}
        from . import mscal  # 2.14.0: Outlook / Microsoft 365, the same way
        ready=bool(self.tenant and mscal.config(self.tenant))
        self.outlook=None if new or not (ready or r.microsoft or r.microsoft_error) else {"ready":ready,"on":bool(r.microsoft),"error":r.microsoft_error,"at":r.microsoft_at,"account":mscal.state(r).get("account","")}
        if not self.photo_preview:del self.fields["photo_remove"]
        bookable=Service.objects.filter(active=True,kind__in=("resource","day"),location__active=True).select_related("location").order_by("location__name","name")
        f=self.fields["services"];f.queryset=bookable
        many=Location.objects.filter(active=True).count()>1
        f.label_from_instance=lambda s:f"{s.name} ({s.location.name})" if many else s.name
        one=units(getattr(self.tenant,"business_type",""))[0].lower()
        word=service_word(getattr(self.tenant,"business_type","")).lower()
        # 2.12.0: an operator does things and works hours; a room, a flat, a bike is booked for something and
        # follows the arrivals and departures of its service, so it shows neither hours nor durations
        self.appointments=any(s.kind=="resource" for s in bookable)
        f.label="Cosa fa" if self.appointments else "Si prenota per";f.in_grid=True
        # said without "un/una nuovo/a", as the word changes gender with the business (operatore, camera, cabina)
        self.treatments_help="Si prenota solo per quelli spuntati; all'inizio sono spuntati tutti."+(
            f" Durata e prezzo vuoti vogliono dire quelli del {word}; con una variante valgono quelli della variante." if self.appointments else "")
        if not self.appointments:self.fields["photo_file"].help_text="PNG, JPEG o WebP fino a 5 MB: compare nell'elenco e nella prenotazione"
        if "capacity" in self.fields:self.fields["capacity"].label="Persone massime (0 = nessun limite)"
        links={} if new else {l.service_id:l for l in ServiceResource.objects.filter(resource=self.instance)}
        self.initial["services"]=[s.pk for s in bookable] if new else list(links)
        self.rows=[]
        for s in bookable:
            row={"service":s}
            if s.kind=="resource":
                dur,price=f"dur_{s.pk}",f"price_{s.pk}"
                self.fields[dur]=forms.IntegerField(required=False,min_value=5,max_value=1440,label=f"Durata di {s.name}",
                    widget=forms.NumberInput(attrs={"placeholder":str(s.duration),"inputmode":"numeric","step":"5"}))
                # written as in Italy, 35,50: the same as the price list shows it
                self.fields[price]=forms.DecimalField(required=False,min_value=0,max_value=100000,max_digits=8,decimal_places=2,label=f"Prezzo di {s.name}",localize=True,
                    widget=forms.TextInput(attrs={"placeholder":euro(s.price).replace(" €","") if s.price is not None else "—","inputmode":"decimal","maxlength":"10"}))
                # 2.14.0: his own posa (work before it, posa), as his duration; empty = the service's
                at,pause=f"pa_{s.pk}",f"pm_{s.pk}"
                for name,label,ph in ((at,"Lavoro prima della posa",s.pause_start),(pause,"Posa",s.pause_minutes)):
                    self.fields[name]=forms.IntegerField(required=False,min_value=0,max_value=1440,label=f"{label} di {s.name}",widget=forms.NumberInput(attrs={"placeholder":str(ph),"inputmode":"numeric","step":"5"}))
                for name in (dur,price,at,pause):self.fields[name].in_grid=True
                link=links.get(s.pk)
                if link:self.initial[dur]=link.duration;self.initial[price]=link.price;self.initial[at]=link.pause_start;self.initial[pause]=link.pause_minutes
                row.update(dur=dur,price=price,at=at,pause=pause)
            self.rows.append(row)
        if not Service.objects.filter(kind="day").exists():del self.fields["capacity"]
        # working days: up to two spans a day here (2.14.0: the lunch break); more, or alternate weeks, stay in Orari di apertura
        rows=[] if new else list(Schedule.objects.filter(resource=self.instance,purpose="service").order_by("weekday","start"))
        self.split_hours=any(sum(r.weekday==d for r in rows)>2 for d in range(7)) or any(r.every_weeks>1 for r in rows)
        # working days only where someone works; hours already set stay visible, never deleted unseen
        self.hours=self.appointments or bool(rows)
        self.grid_fields=["own_hours"]
        for d in range(7):
            mine=[r for r in rows if r.weekday==d]
            self.fields[f"day_{d}"]=forms.BooleanField(required=False,label=WEEKDAYS[d])
            for name in ("start","end","start2","end2"):self.fields[f"{name}_{d}"]=hour_field()
            self.initial[f"day_{d}"]=bool(mine) if rows else d<5
            self.initial[f"start_{d}"]=mine[0].start if mine else "09:00";self.initial[f"end_{d}"]=mine[0].end if mine else "18:00"
            if len(mine)>1:self.initial[f"start2_{d}"]=mine[1].start;self.initial[f"end2_{d}"]=mine[1].end
            self.grid_fields+=[f"day_{d}",f"start_{d}",f"end_{d}",f"start2_{d}",f"end2_{d}"]
        self.initial["own_hours"]=bool(rows)
        for name in self.grid_fields:
            self.fields[name].in_grid=True  # drawn as a week grid, not in the field list
            if self.split_hours:self.fields[name].disabled=True
    @property
    def hours_rows(self):return [{"on":self[f"day_{d}"],"start":self[f"start_{d}"],"end":self[f"end_{d}"],"start2":self[f"start2_{d}"],"end2":self[f"end2_{d}"]} for d in range(7)] if self.hours else []
    @property
    def treatment_rows(self):
        """One row a service, as the week grid: the tick, and for appointments the operator's duration and price."""
        boxes={str(w.data["value"]):w for w in self["services"].subwidgets}
        return [{"box":boxes.get(str(r["service"].pk)),"name":self.fields["services"].label_from_instance(r["service"]),
            "dur":self[r["dur"]] if "dur" in r else None,"price":self[r["price"]] if "price" in r else None,
            "at":self[r["at"]] if "at" in r else None,"pause":self[r["pause"]] if "pause" in r else None} for r in self.rows]
    def clean_priority(self):return self.cleaned_data["priority"] or 0
    def clean_photo_url(self):
        value=self.cleaned_data.get("photo_url","")
        if value and not value.startswith("https://"):raise ValidationError("Usa un indirizzo HTTPS.")
        return value
    def clean_busy_url(self):
        value=self.cleaned_data.get("busy_url","").strip()
        if value and not re.match(r"(https|webcal)://[^\s/]+",value,re.I):raise ValidationError("Usa l'indirizzo che inizia con https:// o webcal://")
        return value
    def clean_photo_file(self):
        upload=self.cleaned_data.get("photo_file")
        if not upload:return None
        from .branding import clean_photo
        return clean_photo(upload)
    def clean(self):
        d=super().clean()
        loc=d.get("location")
        if loc and any(s.location_id!=loc.pk for s in d.get("services",[])):raise ValidationError("Spunta solo servizi della sua sede.")
        for row in self.rows:
            s=row["service"]
            if "pause" not in row or d.get(row["pause"]) is None:continue
            at=d.get(row["at"]) if d.get(row["at"]) is not None else s.pause_start;n=d[row["pause"]];whole=d.get(row["dur"]) or s.duration
            if n and not (0<at and at+n<whole):self.add_error(row["pause"],f"{s.name}: la posa sta dentro la durata, dopo un po' di lavoro e prima della finitura.")
        if bool(d.get("away_from"))!=bool(d.get("away_until")) or (d.get("away_from") and d["away_until"]<d["away_from"]):self.add_error("away_until","Scrivi dal e fino al, con la fine non prima dell'inizio.")
        if d.get("away_by") and d.get("location") and d["away_by"].location_id!=d["location"].pk:self.add_error("away_by","Scegli qualcuno della stessa sede.")
        if d.get("own_hours") and self.hours and not self.split_hours:
            days=[n for n in range(7) if d.get(f"day_{n}")]
            if not days:raise ValidationError("Spunta almeno un giorno di lavoro, o spegni Ha giorni e orari suoi.")
            for n in days:
                if not d.get(f"start_{n}") or not d.get(f"end_{n}") or d[f"end_{n}"]<=d[f"start_{n}"]:raise ValidationError(f"{WEEKDAYS[n]}: l'orario di fine viene dopo quello di inizio.")
                a,b=d.get(f"start2_{n}"),d.get(f"end2_{n}")
                if (a or b) and (not a or not b or b<=a):raise ValidationError(f"{WEEKDAYS[n]}: la seconda fascia vuole inizio e fine, con la fine dopo l'inizio.")
                if a and a<d[f"end_{n}"] and d[f"start_{n}"]<b:raise ValidationError(f"{WEEKDAYS[n]}: le due fasce si sovrappongono.")
        return d
    def save(self,commit=True):
        from .crypto import encrypt
        from .models import TimeBlock
        from .feeds import busy_url
        d=self.cleaned_data;obj=self.instance;old=busy_url(obj) if obj.busy else ""
        # a new random version: the links given before stop working, also those of a feed switched off and on again
        if d.get("feed_on") and (not obj.feed or d.get("feed_new")):obj.feed=secrets.randbelow(2**31-1)+1
        if not d.get("feed_on"):obj.feed=0
        obj.limits=read_limits(d)
        obj=super().save(commit)
        if d.get("busy_url")!=old:
            obj.busy=encrypt(self.tenant,d["busy_url"],"busy:"+str(obj.pk)) if d.get("busy_url") else "";obj.busy_at=None;obj.busy_error=""
            obj.save(update_fields=["busy","busy_at","busy_error"])
            TimeBlock.objects.filter(resource=obj,reason="busy").delete()
        # the links are kept, with their own duration and price: only the unticked go, the ticked are added
        ServiceResource.objects.filter(resource=obj).exclude(service__in=d.get("services",[])).delete()
        for s in d.get("services",[]):
            link,_=ServiceResource.objects.get_or_create(tenant=self.tenant,service=s,resource=obj)
            if s.kind=="resource":
                link.duration=d.get(f"dur_{s.pk}");link.price=d.get(f"price_{s.pk}")
                link.pause_minutes=d.get(f"pm_{s.pk}");link.pause_start=d.get(f"pa_{s.pk}") if link.pause_minutes is not None else None
                link.save(update_fields=["duration","price","pause_start","pause_minutes"])
        if d.get("photo_remove") or d.get("photo_file"):
            from django.utils import timezone
            from .models import ResourcePhoto
            if d.get("photo_file"):
                ResourcePhoto.objects.update_or_create(resource=obj,defaults={"tenant":self.tenant,"data":d["photo_file"]});obj.photo_at=timezone.now()
            else:
                ResourcePhoto.objects.filter(resource=obj).delete();obj.photo_at=None
            obj.save(update_fields=["photo_at"])
        if self.hours and not self.split_hours:
            Schedule.objects.filter(resource=obj,purpose="service").delete()
            if d.get("own_hours"):
                for n in range(7):
                    if d.get(f"day_{n}"):
                        Schedule.objects.create(tenant=self.tenant,resource=obj,weekday=n,start=d[f"start_{n}"],end=d[f"end_{n}"])
                        if d.get(f"start2_{n}"):Schedule.objects.create(tenant=self.tenant,resource=obj,weekday=n,start=d[f"start2_{n}"],end=d[f"end2_{n}"])
        return obj
def default_kind(tenant):
    from .presets import PRESETS
    return PRESETS.get(getattr(tenant,"business_type",""),{}).get("kind","resource")
class ServiceForm(ScopedForm):
    resources=forms.ModelMultipleChoiceField(queryset=Resource.objects.none(),required=False,label="Risorse abilitate",widget=forms.CheckboxSelectMultiple)
    party_durations=forms.CharField(required=False,max_length=200)  # 2.14.0: "2:90, 4:105", kept as [[2,90],[4,105]]
    # 2.14.0: a private service, booked only from its link (Service.secret)
    private_on=forms.BooleanField(required=False,label="Riservato: si prenota solo con il suo link",help_text="Non compare nella pagina prenotazioni: lo prenota solo chi riceve il link")
    private_new=forms.BooleanField(required=False,label="Cambia il link riservato",help_text="Il link di prima smette di funzionare")
    class Meta:
        model=Service
        fields=["name","name_en","location","kind","description","description_en","price","capacity","online_capacity","max_party","turnover","last_arrival","duration","party_durations","interval","pace_covers","pace_bookings","pace_desk","checkin","checkout","min_days","max_days","buffer_before","buffer_after","lead_minutes","horizon_days","cancel_cutoff_minutes","request_all","request_above","request_allergies","pause_start","pause_minutes","client_picks","instructions","remote_url","recall_days","test_days","resource_label","thanks_url","fields","active"]
        labels={"name":"Nome del servizio","thanks_url":"Pagina di ringraziamento dopo la prenotazione","active":"Attivo: prenotabile online","pause_start":"Lavoro prima della posa (minuti)","pause_minutes":"Tempo di posa (minuti)","client_picks":"Il cliente sceglie con chi"}
        help_texts={"name":"Per esempio Cena, Taglio donna, Camera doppia","thanks_url":"Facoltativa, per esempio https://www.ilvostrosito.it/grazie: dopo la prenotazione online il cliente va lì. Vuota: quella dell'attività, se c'è","pause_start":"Per esempio 30 per l'applicazione del colore. Dopo la posa viene la finitura, fino alla fine della durata",
            "pause_minutes":"0 = nessuna posa. Durante la posa il cliente resta, l'operatore è libero e si può prenotare con lui un servizio breve",
            "client_picks":"Spento: online il cliente sceglie solo l'orario e il programma assegna la persona o il posto libero, per esempio il ponte dell'officina"}
        localized_fields=["price"]  # 2.12.0: 25,50 as in Italy
        widgets={"fields":forms.HiddenInput(),"description":forms.Textarea(attrs={"rows":2}),"description_en":forms.Textarea(attrs={"rows":2}),"instructions":forms.Textarea(attrs={"rows":3})}
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
        self.fields["resources"].help_text=f"Quali {many.lower()} si possono prenotare con questo servizio"+(": le voci usate insieme all'operatore (cabine, macchinari) sono richieste, ne serve una libera per ogni gruppo" if Resource.objects.filter(active=True,secondary=True).exists() else "")
        if not new:self.initial["resources"]=ServiceResource.objects.filter(service=self.instance).values_list("resource_id",flat=True)
        # 2.8.0: with one location a new service starts with every operator ticked, instead of an error on saving
        elif not self.is_bound and Location.objects.filter(active=True).count()==1:self.initial["resources"]=list(Resource.objects.filter(active=True).values_list("pk",flat=True))
        if agency:
            # the agency chooses the kind: every field says which kinds use it and the page shows only those
            self.fields["kind"].choices=SERVICE_KINDS
            self.fields["kind"].help_text="Lo decide l'"+settings.AGENCY_NAMES["who"]+": l'attività non lo vede e non lo cambia"
            self.fields["kind"].widget.attrs["data-field-kinds"]=json.dumps({k:sorted(v) for k,v in FIELD_KINDS.items()})
            for name,kinds in FIELD_KINDS.items():
                f=self.fields[name];f.widget.attrs["data-kinds"]=" ".join(sorted(kinds))
                if name in KIND_LABELS:f.widget.attrs["data-labels"]=json.dumps(KIND_LABELS[name],ensure_ascii=False)
                if name in KIND_HELP:f.widget.attrs["data-helps"]=json.dumps(KIND_HELP[name],ensure_ascii=False)
        else:
            self.fields.pop("kind")
            for name,kinds in FIELD_KINDS.items():
                if kind not in kinds:self.fields.pop(name)
            # 2.14.0: a restaurant chooses how its covers turn, and changes it when it wants
            if getattr(self.tenant,"business_type","")=="ristorante" and kind in ("capacity","shift"):
                self.fields["mode"]=forms.ChoiceField(choices=MODES,label="Come girano i tavoli",help_text="Un turno per servizio: i coperti valgono per tutto il pranzo o tutta la cena. Turni fissi: per esempio 19:30 e 21:30, i turni si scrivono in Turni del ristorante. Rotazione: il tavolo si libera dopo la durata e si riprenota")
                self.initial.setdefault("mode","fixed" if kind=="shift" else "rotation" if self.instance.turnover else "service")
                self.fields.pop("turnover",None)
        # 2.14.0: allergies make a request only where the booking form asks about them
        if not getattr(self.tenant,"allergens_on",False):self.fields.pop("request_allergies")
        for name in ("request_above","online_capacity","pace_covers","pace_bookings","pause_start","pause_minutes"):
            if name in self.fields:self.fields[name].required=False  # 2.14.0: empty = 0, no limit
        add_limits(self,"questo servizio")
        self.initial["private_on"]=bool(self.instance.secret)
        if self.instance.secret:
            self.fields["private_on"].help_text="Il link da mandare: "+f"{settings.PUBLIC_ORIGIN}/book/{getattr(self.tenant,'slug','')}/?riservato={self.instance.secret}"
        else:del self.fields["private_new"]
        if "party_durations" in self.fields and not self.is_bound:self.initial["party_durations"]=", ".join(f"{n}:{m}" for n,m in self.instance.party_durations or [])
        for name in ("recall_days","test_days"):
            if name in self.fields:self.fields[name].required=False
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
            # 2.14.0: shown only when an earlier question has that answer (sì / no for a box)
            if (cond:=item.get("show_if")) is not None and (not isinstance(cond,dict) or cond.get("key") not in keys-{item["key"]} or not isinstance(cond.get("value"),str) or not 1<=len(cond["value"])<=80):raise ValidationError("Condizione non valida: scegli una domanda che viene prima e la risposta.")
            if item["type"]=="select" and (not isinstance(item.get("choices"),list) or not 1<=len(item["choices"])<=30 or any(not isinstance(c,str) or not 1<=len(c)<=80 for c in item["choices"])):raise ValidationError("Inserisci le opzioni del campo.")
        return schema
    def clean_party_durations(self):
        out=[]
        for part in filter(None,(p.strip() for p in re.split(r"[,;\n]",self.cleaned_data.get("party_durations") or ""))):
            m=re.fullmatch(r"(\d{1,4})\s*[:=]\s*(\d{1,4})",part)
            if not m:raise ValidationError("Scrivi persone e minuti così: 2:90, 4:105.")
            if not (1<=int(m[1])<=1000 and 5<=int(m[2])<=1440):raise ValidationError("Persone da 1 a 1000, minuti da 5 a 1440.")
            out.append([int(m[1]),int(m[2])])
        if len({n for n,_ in out})<len(out):raise ValidationError("Ogni numero di persone una volta sola.")
        return sorted(out)[:8]
    def clean_remote_url(self):
        value=self.cleaned_data.get("remote_url","")
        if value and not value.startswith("https://"):raise ValidationError("Usa un indirizzo HTTPS.")
        return value
    def clean_thanks_url(self):
        value=self.cleaned_data.get("thanks_url","")
        if value and not value.startswith("https://"):raise ValidationError("Usa un indirizzo HTTPS.")
        return value
    def clean(self):
        d=super().clean()
        kind=d.get("kind") or self.kind
        if d.get("price") is not None and not 0<=d["price"]<=100000:self.add_error("price","Prezzo tra 0 e 100.000 €.")
        for name in ("request_above","online_capacity","pace_covers","pace_bookings","recall_days","test_days","pause_start","pause_minutes"):
            if name in self.fields and d.get(name) is None:d[name]=0
        bounds={"pause_start":(0,1440),"pause_minutes":(0,1440),"recall_days":(0,1100),"test_days":(0,1100),"duration":(5,1440),"interval":(5,240),"capacity":(1,10000),"max_party":(1,1000),"buffer_before":(0,1440),"buffer_after":(0,1440),"lead_minutes":(0,525600),"horizon_days":(1,730),"max_days":(1,366),"min_days":(0,366),"last_arrival":(0,1440),"cancel_cutoff_minutes":(0,525600),"request_above":(0,1000),"online_capacity":(0,10000),"pace_covers":(0,10000),"pace_bookings":(0,1000)}
        for key,(low,high) in bounds.items():
            if key in d and d[key] is not None and not low<=d[key]<=high:self.add_error(key,f"Valore tra {low} e {high}.")
        if d.get("online_capacity") and d.get("capacity") and d["online_capacity"]>d["capacity"]:self.add_error("online_capacity","Non può superare i posti.")
        # 2.14.0: the groups above the threshold are requests, up to the most people online: above it no request can start
        if d.get("request_above") and d.get("max_party") and d["request_above"]>=d["max_party"]:self.add_error("request_above","Deve essere meno delle persone massime per prenotazione: sopra quel numero online non si prenota.")
        if kind=="resource" and "resources" in self.fields and not d.get("resources"):self.add_error("resources","Seleziona almeno una voce: se l'elenco è vuoto, creala prima nella pagina accanto a Servizi.")
        elif kind=="resource" and d.get("resources") and all(r.secondary for r in d["resources"]):self.add_error("resources","Serve almeno una persona: cabine e macchinari si prenotano insieme a lei.")
        if kind=="resource" and d.get("pause_minutes") and not (0<d.get("pause_start",0) and d["pause_start"]+d["pause_minutes"]<d.get("duration",0)):
            self.add_error("pause_minutes","La posa sta dentro la durata: dopo un po' di lavoro e prima della finitura.")
        if kind=="day" and d.get("min_days") and d.get("max_days") and d["min_days"]>d["max_days"]:self.add_error("min_days","Le notti minime superano le massime.")
        if kind=="day" and d.get("min_days")==0 and d.get("checkin") and d.get("checkout") and d["checkout"]<=d["checkin"]:self.add_error("min_days","Con 0 notti la riconsegna è lo stesso giorno: l'ora di partenza deve venire dopo quella di arrivo.")
        location=d.get("location") or getattr(self.instance,"location",None)
        if d.get("resources") and any(r.location_id!=getattr(location,"pk",None) for r in d["resources"]):self.add_error("resources","Le risorse devono appartenere alla sede del servizio.")
        return d
    def save(self,commit=True):
        mode=self.cleaned_data.get("mode")
        if mode:self.instance.kind,self.instance.turnover={"service":("capacity",False),"fixed":("shift",False),"rotation":("capacity",True)}[mode]
        d=self.cleaned_data;self.instance.limits=read_limits(d)
        if not d.get("private_on"):self.instance.secret=""
        elif not self.instance.secret or d.get("private_new"):self.instance.secret=secrets.token_urlsafe(18)
        service=super().save(commit)
        if commit and "resources" in self.fields:
            # 2.11.0: an operator ticked again keeps his own duration and price for this service
            chosen=self.cleaned_data["resources"] if service.kind in ("resource","day") else []  # day: the named units
            ServiceResource.objects.filter(service=service).exclude(resource__in=chosen).delete()
            for r in chosen:ServiceResource.objects.get_or_create(tenant=self.tenant,service=service,resource=r)
        return service
class OptionForm(ScopedForm):
    class Meta:
        model=Option;fields=["service","name","duration","price","active"];localized_fields=["price"]
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
class ProductForm(ScopedForm):
    """2.22.0: a product of the shop, sold at the Cassa (on the products' department of Registratore di cassa)."""
    class Meta:
        model=Product;fields=["name","price","active"];localized_fields=["price"]
        labels={"name":"Nome del prodotto","price":"Prezzo (€)","active":"In vendita"}
        help_texts={"name":"Come compare in Cassa, nei preventivi e sullo scontrino (sullo scontrino al massimo 32 caratteri)","price":"Facoltativo: vuoto, lo scrivi ogni volta in Cassa"}
    def clean_name(self):
        v=" ".join(self.cleaned_data["name"].split())
        if Product.objects.filter(name__iexact=v,active=True).exclude(pk=self.instance.pk).exists():raise ValidationError("C'è già un prodotto con questo nome.")
        return v
    def clean_price(self):
        val=self.cleaned_data.get("price")
        if val is not None and not 0<val<=99999:raise ValidationError("Prezzo tra 0,01 e 99.999 €.")
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
        model=Schedule;fields=["purpose","location","service","resource","weekday","start","end","valid_from","valid_until","every_weeks"]
        labels={"every_weeks":"Ogni quante settimane"}
        help_texts={"start":"Per esempio 12:00","end":"Per esempio 15:00. Per pranzo e cena crea due fasce","valid_from":"Vuoto: da subito","valid_until":"Vuoto: sempre",
            "every_weeks":"1 ogni settimana. 2 per le settimane alterne: conta dalla settimana di Valido dal (settimana A); per la settimana B crea le fasce con Valido dal una settimana dopo"}
    def __init__(self,*a,**kw):
        super().__init__(*a,**kw)
        self.fields["every_weeks"].required=False  # empty: every week, as before
    def clean(self):
        d=super().clean()
        d["every_weeks"]=d.get("every_weeks") or 1
        if not 1<=d["every_weeks"]<=4:raise ValidationError("Ogni quante settimane: da 1 a 4.")
        if d["every_weeks"]>1 and not d.get("valid_from"):raise ValidationError("Per le settimane alterne scrivi in Valido dal il giorno da cui contare.")
        return d
class ExceptionForm(CalendarForm):
    class Meta:
        model=ExceptionDay;fields=["purpose","location","service","resource","date","until","closed","start","end","label"]
        help_texts={"until":"Vuoto: solo quel giorno. Due settimane di ferie sono una riga sola","closed":"Togli la spunta per un orario speciale, uguale per ogni giorno, e scrivilo in Inizio e Fine","label":"Per esempio Ferie o Natale: lo vedi solo tu"}
    def __init__(self,*a,**kw):
        super().__init__(*a,**kw)
        self.fields["date"].label="Dal giorno";self.fields["until"].label="Al giorno"
    def clean(self):
        d=super().clean()
        if d.get("until") and d.get("date"):
            if d["until"]<d["date"]:raise ValidationError("Il giorno di fine viene prima di quello di inizio.")
            if (d["until"]-d["date"]).days>366:raise ValidationError("Al massimo un anno su una riga.")
            if d["until"]==d["date"]:d["until"]=None  # a range of one day is a single date
        return d
REPEAT=[(0,"Non si ripete"),(1,"Ogni settimana"),(2,"Ogni 2 settimane"),(3,"Ogni 3 settimane"),(4,"Ogni 4 settimane")]
class OccurrenceForm(ScopedForm):
    """2.14.0: a date can repeat every N weeks until a day, on its weekday or on the ones ticked (a series); a date of a
    series changes alone or with the next ones."""
    repeat=forms.TypedChoiceField(choices=REPEAT,coerce=int,required=False,empty_value=0,label="Ripeti")
    repeat_until=forms.DateField(required=False,label="Fino al",widget=forms.DateInput(attrs={"type":"date"},format="%Y-%m-%d"),help_text="L'ultimo giorno della serie, al massimo due anni dopo la prima data")
    repeat_days=forms.TypedMultipleChoiceField(choices=list(enumerate(WEEKDAYS)),coerce=int,required=False,widget=forms.CheckboxSelectMultiple,label="Nei giorni",help_text="Vuoto: lo stesso giorno della settimana della prima data")
    scope=forms.ChoiceField(choices=[("one","Solo questa data"),("series","Questa e le successive della serie")],initial="one",required=False,widget=forms.RadioSelect,label="Cosa cambia")
    class Meta:
        model=Occurrence;fields=["service","start","end","capacity","active"]
        labels={"capacity":"Posti per questa data","active":"Attiva"}
    def __init__(self,*a,**kw):
        super().__init__(*a,**kw)
        self.fields["service"].queryset=Service.objects.filter(kind="event")
        only_one(self.fields["service"],Service.objects.filter(kind="event"))
        self.is_new=self.instance._state.adding;self.was=self.instance.start
        if self.is_new:self.fields.pop("scope")
        else:
            for name in ("repeat","repeat_until","repeat_days"):self.fields.pop(name)
            if not self.instance.series:self.fields.pop("scope")
    def clean(self):
        d=super().clean()
        if d.get("service") and d["service"].kind not in ("event","shift"):raise ValidationError("Seleziona un servizio evento.")
        if d.get("end") and d.get("start") and (d["end"]<=d["start"] or (d["end"]-d["start"]).days>7):raise ValidationError("Durata evento non valida.")
        if d.get("capacity",0)<1:raise ValidationError("Capienza positiva richiesta.")
        if d.get("repeat") and d.get("start"):
            first=d["start"].astimezone(ZoneInfo(self.tenant.timezone)).date()
            if not d.get("repeat_until") or d["repeat_until"]<=first:raise ValidationError("Scrivi in Fino al l'ultimo giorno della serie, dopo la prima data.")
            if (d["repeat_until"]-first).days>730:raise ValidationError("Una serie arriva al massimo a due anni dalla prima data.")
        return d
    def targets(self):
        """The dates a change reaches: this one, and with "the next ones" the rest of its series."""
        later=Occurrence.objects.filter(series=self.instance.series,start__gt=self.was).exclude(pk=self.instance.pk) if not self.is_new and self.cleaned_data.get("scope")=="series" else Occurrence.objects.none()
        return [self.instance,*later.order_by("start")]
    def save(self,commit=True):
        d=self.cleaned_data;tz=ZoneInfo(self.tenant.timezone)
        dates=self.targets()[1:]
        obj=super().save(commit)
        a,b=obj.start.astimezone(tz),obj.end.astimezone(tz)
        def place(o,day):
            o.start=local_datetime(day,a.time(),tz);o.end=local_datetime(day+(b.date()-a.date()),b.time(),tz,fold=1)
            o.capacity=obj.capacity;o.active=obj.active
            if o.start and o.end:o.save()  # an hour that does not exist that day (summer time) is skipped
        if d.get("repeat"):
            obj.series=uuid.uuid4();obj.save(update_fields=["series"])
            monday=a.date()-timedelta(days=a.date().weekday())
            while monday<=d["repeat_until"]:
                for wd in sorted(set(d.get("repeat_days") or [a.date().weekday()])):
                    day=monday+timedelta(days=wd)
                    if a.date()<day<=d["repeat_until"]:place(Occurrence(tenant=self.tenant,service=obj.service,series=obj.series),day)
                monday+=timedelta(weeks=d["repeat"])
        elif dates:
            # the next dates of the series move as this one: the same days later or earlier, the new hours and places
            shift=a.date()-self.was.astimezone(tz).date()
            for o in dates:place(o,o.start.astimezone(tz).date()+shift)
        return obj
class CampaignForm(ScopedForm):
    # 2.14.0: to whom among the subscribers; all empty = all of them, as before
    tag=forms.ChoiceField(required=False,label="Solo i clienti con l'etichetta")
    vip=forms.BooleanField(required=False,label="Solo i clienti VIP")
    after=forms.DateField(required=False,label="Ultima visita dal",widget=forms.DateInput(attrs={"type":"date"},format="%Y-%m-%d"))
    before=forms.DateField(required=False,label="Nessuna visita dal",help_text="Per esempio per chi non torna da sei mesi",widget=forms.DateInput(attrs={"type":"date"},format="%Y-%m-%d"))
    visits=forms.IntegerField(required=False,min_value=1,max_value=1000,label="Almeno quante visite")
    class Meta:model=Campaign;fields=["name","subject","body","scheduled"]
    def __init__(self,*a,**kw):
        super().__init__(*a,**kw)
        from .clients import known_tags
        self.fields["tag"].choices=[("","Tutti")]+[(t,t) for t in known_tags(self.tenant)]
        for k,v in (self.instance.filters or {}).items():self.initial[k]=v
    def save(self,commit=True):
        d=self.cleaned_data
        self.instance.filters={k:(d[k].isoformat() if hasattr(d[k],"isoformat") else d[k]) for k in ("tag","vip","after","before","visits") if d.get(k)}
        return super().save(commit)
    def clean_subject(self):
        value=self.cleaned_data["subject"]
        if "\n" in value or "\r" in value:raise ValidationError("Oggetto non valido.")
        return value
    def clean_body(self):
        value=self.cleaned_data["body"]
        if len(value)>20000:raise ValidationError("Messaggio troppo lungo.")
        return value
class BrandForm(forms.ModelForm):
    """2.13.0: the look the business sets itself, in Configura l'attività: logo, colour, the line under the name, site."""
    logo_file=forms.FileField(required=False)
    logo_remove=forms.BooleanField(required=False)
    class Meta:
        model=Tenant
        fields=["brand_color","tagline","website","thanks_url"]
    def clean_brand_color(self):return TenantForm.clean_brand_color(self)
    def clean_thanks_url(self):return ServiceForm.clean_thanks_url(self)
    def clean_logo_file(self):
        f=self.cleaned_data.get("logo_file")
        if not f:return None
        from .site import clean_brand
        return clean_brand(f)
    def save(self):
        t=super().save(commit=False)
        if self.cleaned_data.get("logo_file"):t.logo,t.logo_ratio=self.cleaned_data["logo_file"]
        elif self.cleaned_data.get("logo_remove"):t.logo=None;t.logo_ratio=1
        t.save(update_fields=["brand_color","tagline","website","thanks_url","logo","logo_ratio"]);return t
class TenantForm(forms.ModelForm):
    logo_file=forms.FileField(required=False,label="Logo PNG trasparente, JPEG o WebP, massimo 1 MB, anche orizzontale")
    embed_origins_text=forms.CharField(required=False,label="Siti autorizzati a mostrare il widget (una origine HTTPS per riga)",widget=forms.Textarea(attrs={"rows":3}))
    class Meta:
        model=Tenant
        fields=["name","slug","timezone","status","trial_until","maintenance","marketing_enabled","export_enabled","client_mails_enabled","alloggiati_self","till_self","smtp_daily_limit","smtp_hourly_limit","brand_color","tagline","website","privacy_text","privacy_text_en","consent_text","consent_text_en","retention_days","receive_schedule","api_enabled"]
        labels={"name":"Nome attività","slug":"Indirizzo breve","timezone":"Fuso orario","status":"Stato","trial_until":"Fine prova","maintenance":"Manutenzione tecnica: sospendi temporaneamente prenotazioni e portale","marketing_enabled":"Marketing: l'attività invia campagne email ai clienti iscritti","export_enabled":"Marketing: l'attività scarica il CSV dei clienti iscritti (Brevo, Mailchimp, fogli)","client_mails_enabled":"Email ai clienti: l'attività sceglie quali inviare e ne scrive oggetto e testo (in Notifiche). Promemoria, arrivo e recensione, e quando partono, li impostate voi in Notifiche","alloggiati_self":"Alloggiati Web: l'attività inserisce da sola utente, password e chiave del servizio web (in Alloggi)","till_self":"Registratore di cassa: l'attività lo collega da sola (tipo, indirizzo, reparti e pagamenti)","smtp_daily_limit":"Limite email giornaliero","smtp_hourly_limit":"Limite email orario","brand_color":"Colore del marchio","tagline":"Sottotitolo","website":"Sito web","privacy_text":"Informativa prenotazioni","consent_text":"Testo consenso marketing","retention_days":"Conservazione prenotazioni (giorni)","receive_schedule":"Limita anche l'orario di invio delle richieste online"}
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

class TimeBlockForm(ScopedForm):
    """2.14.0: Blocca orario from the agenda: an operator is not bookable for a while (pause, training, meeting), once or
    every week. The note is sealed with the business key, as the notes of a booking."""
    note=forms.CharField(required=False,max_length=200,label="Nota",help_text="Facoltativa, per esempio il nome del corso: la vede solo lo staff")
    class Meta:
        model=TimeBlock;fields=["resource","day","start","end","reason","weekly","until"]
        labels={"day":"Giorno","reason":"Motivo","weekly":"Ogni settimana, lo stesso giorno alla stessa ora","until":"Fino al"}
        help_texts={"end":"Se viene prima dell'inizio, il blocco arriva al giorno dopo","until":"Solo se si ripete. Vuoto: ogni settimana, senza fine"}
    def __init__(self,*a,resources=None,**kw):
        super().__init__(*a,**kw)
        f=self.fields["resource"];f.queryset=resources;f.required=True;f.empty_label=None
        from .presets import units
        f.label=units(getattr(self.tenant,"business_type",""))[0]
        self.fields["reason"].choices=[c for c in BLOCK_REASONS if c[0] not in ("closed","busy","google","outlook")]  # busy, google: only from an outside calendar (feeds.py, gcal.py)
        if self.instance.pii:self.initial["note"]=self.instance.note
        self.order_fields(["resource","day","start","end","reason","note","weekly","until"])
    def clean(self):
        d=super().clean()
        if d.get("start") and d.get("start")==d.get("end"):raise ValidationError("Inizio e fine devono essere differenti.")
        if d.get("weekly") and d.get("until") and d.get("day") and d["until"]<d["day"]:raise ValidationError("Fino al viene prima del giorno del blocco.")
        return d
    def save(self,commit=True):
        from .crypto import encrypt
        obj=super().save(commit=False)
        if not obj.weekly:obj.until=None
        note=" ".join(self.cleaned_data.get("note","").split())
        obj.pii=encrypt(self.tenant,note,"block:"+str(obj.pk)) if note else ""
        obj.save();return obj
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
