import re,uuid,secrets
from django.db import models
from django.contrib.auth.models import AbstractBaseUser,BaseUserManager,PermissionsMixin
from django.core.exceptions import ValidationError
from .crypto import protect,reveal,login_index,b64,encrypt,decrypt
from .tenancy import TenantManager,current_tenant

USERNAME=re.compile(r"[a-z0-9][a-z0-9._-]{2,39}")
def clean_username(value):
    """An edition with LOGIN_USERNAMES: a name to sign in with, never an "@", so it cannot be taken for an email."""
    value=(value or "").strip().casefold()
    if value and not USERNAME.fullmatch(value):
        raise ValidationError("Username non valido: da 3 a 40 caratteri tra lettere, numeri, punto, trattino e trattino basso, senza spazi né accenti.")
    return value
class UserManager(BaseUserManager):
    def create_user(self,email,password=None,username="",**extra):
        email=(email or "").strip().casefold();username=clean_username(username)
        if not email and not username:raise ValidationError("Serve un'email o uno username.")
        # usernames and emails share the index space without clashing (no "@" in a username); without an email the username is the login
        user=self.model(login_index=login_index(email or username),name_index=login_index(username) if username else None,**extra)
        user.pii=protect({"email":email,**({"username":username} if username else {})},"user:"+str(user.pk))
        user.set_password(password);user.save();return user
    def create_superuser(self,email,password,**extra):
        return self.create_user(email,password,is_staff=True,is_superuser=True,**extra)
    def by_login(self,value):
        """The account an email or a username signs in to."""
        key=login_index(value or "")
        return self.filter(models.Q(login_index=key)|models.Q(name_index=key))
class User(AbstractBaseUser,PermissionsMixin):
    id=models.UUIDField(primary_key=True,default=uuid.uuid4,editable=False)
    login_index=models.CharField(max_length=64,unique=True)
    name_index=models.CharField(max_length=64,unique=True,null=True,blank=True)  # 2.9.0: the username, where an edition uses them
    pii=models.TextField()
    is_active=models.BooleanField(default=True)
    is_staff=models.BooleanField(default=False)
    auth_version=models.PositiveIntegerField(default=1)
    totp=models.TextField(blank=True)
    last_totp=models.BigIntegerField(default=-1)
    recovery=models.JSONField(default=list)
    # 2.13.0: an owner the agency lets in without password, only from a device the edition trusts (quick.py)
    quick_login=models.BooleanField(default=False)
    USERNAME_FIELD="login_index"
    objects=UserManager()
    @property
    def email(self): return reveal(self.pii,"user:"+str(self.pk))["email"]
    @property
    def username(self): return reveal(self.pii,"user:"+str(self.pk)).get("username","")
    @property
    def login_name(self):
        """What the person signs in with and is shown as: the username, or the email."""
        data=reveal(self.pii,"user:"+str(self.pk))
        return data.get("username") or data["email"]
    @property
    def mfa_ready(self): return bool(self.totp) or self.passkeys.exists()
class Passkey(models.Model):
    id=models.UUIDField(primary_key=True,default=uuid.uuid4,editable=False)
    user=models.ForeignKey(User,on_delete=models.CASCADE,related_name="passkeys")
    credential_id=models.TextField(unique=True)
    public_key=models.TextField()
    sign_count=models.BigIntegerField(default=0)
    name=models.CharField(max_length=80,default="Passkey")
    created=models.DateTimeField(auto_now_add=True)
class Ceremony(models.Model):
    id=models.UUIDField(primary_key=True,default=uuid.uuid4,editable=False)
    user=models.ForeignKey(User,null=True,on_delete=models.CASCADE)
    kind=models.CharField(max_length=20)
    challenge=models.TextField()
    expires=models.DateTimeField()
    used=models.BooleanField(default=False)
class Invitation(models.Model):
    token=models.CharField(max_length=64,primary_key=True)
    user=models.ForeignKey(User,on_delete=models.CASCADE)
    expires=models.DateTimeField()
    used=models.BooleanField(default=False)
class RateBucket(models.Model):
    key=models.CharField(max_length=64,primary_key=True)
    count=models.PositiveIntegerField(default=0)
    ends=models.DateTimeField()

class Tenant(models.Model):
    id=models.UUIDField(primary_key=True,default=uuid.uuid4,editable=False)
    slug=models.SlugField(unique=True,max_length=60)
    name=models.CharField(max_length=120)
    wrapped_key=models.TextField()
    timezone=models.CharField(max_length=64,default="Europe/Rome")
    status=models.CharField(max_length=12,choices=[("active","Attivo"),("trial","Prova"),("suspended","Sospeso")],default="active")
    trial_until=models.DateField(null=True,blank=True)
    # Technical pause, independent of any commercial plan (also in Private).
    maintenance=models.BooleanField(default=False)
    # Staff notifications: minimal content, never customer contacts or notes.
    notify_email=models.EmailField(blank=True)
    telegram=models.TextField(blank=True)  # encrypted {"token","chat"}
    marketing_enabled=models.BooleanField(default=False)
    marketing_requested=models.BooleanField(default=False)
    # 2.13.0: the agency turns on the CSV of the subscribers apart from the campaigns; with neither, no Marketing
    export_enabled=models.BooleanField(default=False)
    # 2.13.0: the agency lets the business choose which emails go to its clients and write them (client_mails.py)
    client_mails_enabled=models.BooleanField(default=False)
    client_mails=models.JSONField(default=dict,blank=True)
    # 2.14.0: the text of the WhatsApp button on a booking and on the client card (client_mails.whatsapp); empty = standard
    whatsapp_text=models.TextField(blank=True)
    whatsapp_reminder=models.TextField(blank=True)  # 2.22.0: the text of Promemoria WhatsApp, the day before
    whatsapp_thanks=models.TextField(blank=True)  # 2.22.2: the thanks of the day after, with the review link
    wa_when=models.CharField(max_length=4,blank=True)  # 2.22.0: when the banner asks to remind on WhatsApp (client_mails.WA_WHEN); empty: never
    # 2.14.0: the retention the worker runs once a day (privacy.run_retention): when it last went through, and why not
    retention_at=models.DateTimeField(null=True,blank=True)
    retention_error=models.CharField(max_length=200,blank=True)
    # 2.14.0: Forza at the desk also for reception and operators, when the owner says so (permissions.effective)
    force_all=models.BooleanField(default=False)
    # 2.14.0: the notices also to the operator of each booking (Persone tied to him, with an email), and the list of
    # tomorrow at this hour of the business (empty = none) with the day it last went (notify.evening)
    notify_operators=models.BooleanField(default=False)
    summary_hour=models.PositiveSmallIntegerField(null=True,blank=True)
    summary_sent=models.DateField(null=True,blank=True)
    # 2.14.0: the patron saint of the town, added to the public holidays (calendar.holidays): "06-29" and its name
    patron_day=models.CharField(max_length=5,blank=True)
    patron_name=models.CharField(max_length=60,blank=True)
    # 2.14.0: the waiting list (waitlist.py): "all" tells every waiting client at once, the first who books wins; "queue"
    # holds the place for the first in line for wait_minutes, then for the next. wait_check: when the worker looks again
    wait_mode=models.CharField(max_length=5,default="all",choices=[("all","Avvisa tutti, vince chi prenota prima"),("queue","Uno alla volta, in ordine di iscrizione")])
    wait_minutes=models.PositiveSmallIntegerField(default=60)
    wait_check=models.DateTimeField(null=True,blank=True,db_index=True)
    # 2.14.0: after this many absences the client's online bookings wait for the business's yes (booking.needs_request); 0 = never
    absences_request=models.PositiveSmallIntegerField(default=0)
    # 2.14.0: the categories of client (Socio, Ospite) with their limits on the online page: [{"name","ahead","max"}],
    # ahead = days ahead it may book (0 = the service's), max = bookings still to come at once (0 = no limit)
    client_categories=models.JSONField(default=list,blank=True)
    smtp=models.TextField(blank=True)
    # 2.14.0: the reminders also by SMS (Twilio, Skebby or Aruba) or WhatsApp Cloud API, set by the agency (texts.py); encrypted,
    # with its own daily budget as the emails
    texts=models.TextField(blank=True)
    texts_daily_limit=models.PositiveIntegerField(default=100)
    # 2.14.0: the agency's Google OAuth client (ID and secret, encrypted) for Google Calendar on the operators (gcal.py)
    google=models.TextField(blank=True)
    # 2.14.0: the agency's Microsoft (Entra) app, ID and secret encrypted, for Outlook / Microsoft 365 on the operators (mscal.py)
    microsoft=models.TextField(blank=True)
    # 2.14.0: the thank-you page after an online booking (a service's own wins)
    thanks_url=models.URLField(max_length=500,blank=True)
    # 2.14.0: Alloggiati Web through the Polizia's web service (stays.ws_send): {"user","password","wskey","auto","error"},
    # encrypted; set by the agency, or by the owner when the agency allows it
    alloggiati=models.TextField(blank=True)
    alloggiati_self=models.BooleanField(default=False)
    # 2.16.0: the fiscal register (cassa.py): the agency lets the owner set it up himself; the owner lets the operators close
    # bills too; till_on: some location has one, so Cassa shows (permissions.effective)
    till_self=models.BooleanField(default=False)
    till_operators=models.BooleanField(default=False)
    till_on=models.BooleanField(default=False)
    smtp_daily_limit=models.PositiveIntegerField(default=500)
    smtp_hourly_limit=models.PositiveIntegerField(default=100)
    logo=models.BinaryField(null=True,blank=True)
    # 2.8.0: width / height of the logo, so a wide wordmark is shown whole in the portal and on the booking page
    logo_ratio=models.FloatField(default=1)
    # 2.8.0: allergies and intolerances the booking form asks about (restaurants), edited by the business
    allergens=models.JSONField(default=list,blank=True)
    allergens_on=models.BooleanField(default=False)
    brand_color=models.CharField(max_length=7,default="#7a55ad")
    tagline=models.CharField(max_length=180,blank=True)
    website=models.URLField(blank=True)
    embed_origins=models.JSONField(default=list)
    privacy_text=models.TextField(default="I dati sono utilizzati per gestire la prenotazione. Contatta l'attività per l'informativa completa.")
    consent_text=models.TextField(default="Acconsento a ricevere novità e offerte via email. Posso revocare il consenso in qualsiasi momento.")
    retention_days=models.PositiveIntegerField(default=730)
    receive_schedule=models.BooleanField(default=False)
    # 2.6.0: chosen by the agency; new services start from it and business users never change it
    business_type=models.CharField(max_length=40,blank=True)
    # 2.6.0: one-time code that connects a WordPress site (sha256 of the code, valid one day)
    connect_hash=models.CharField(max_length=64,blank=True,db_index=True)
    connect_until=models.DateTimeField(null=True,blank=True)
    # 2.14.0: the read-only API (api.py): the key's hash, never the key; the webhook, sealed {"url","secret"}
    api_key=models.CharField(max_length=64,blank=True,db_index=True)
    webhook=models.TextField(blank=True)
    api_enabled=models.BooleanField("Permetti all'attività di gestire API e webhook",default=False)  # 2.14.0: the agency lets the owner set API key and webhook himself
    # 2.14.0: the English page and emails; empty = the Italian text
    privacy_text_en=models.TextField("Informativa in inglese (facoltativa)",blank=True)
    consent_text_en=models.TextField("Consenso in inglese (facoltativo)",blank=True)
    # 2.17.0: the WordPress site fed by this PC even when it is off (wpsync.py): sealed {"url","secret","days","on"},
    # and how the last sync went {"at","running","new","ids","ack","error"}
    wp_sync=models.TextField(blank=True)
    wp_state=models.JSONField(default=dict,blank=True)
    # 2.18.0: the site keeps the whole archive (wpsync.touch): set by the agency; wp_seq the last number given to a change
    wp_archive=models.BooleanField(default=False)
    wp_seq=models.BigIntegerField(default=0)
    created=models.DateTimeField(auto_now_add=True)
    def save(self,*a,**kw):
        if not self.wrapped_key:self.wrapped_key=protect(b64(secrets.token_bytes(32)),"tenant-key:"+str(self.pk))
        super().save(*a,**kw)
    def __str__(self):return self.name
    @property
    def logo_wide(self):return bool(self.logo) and self.logo_ratio>1.4
    @property
    def stays(self):return self.business_type in ("hotel","casa_vacanze")
    @property
    def asked_allergens(self):
        from .presets import serves_food
        return list(self.allergens) if self.allergens_on and serves_food(self.business_type) else []
class Membership(models.Model):
    tenant=models.ForeignKey(Tenant,on_delete=models.CASCADE)
    user=models.ForeignKey(User,on_delete=models.CASCADE)
    role=models.CharField(max_length=15,choices=[("owner","Amministratore"),("manager","Amministratore sede"),("reception","Reception"),("operator","Operatore sede"),("marketing","Marketing"),("viewer","Consultazione")],default="operator")
    can_schedule=models.BooleanField(default=False)
    # Optional scope: location/resource UUIDs of this tenant. Operators see only their resources.
    locations=models.JSONField(default=list,blank=True)
    resources=models.JSONField(default=list,blank=True)
    class Meta: constraints=[models.UniqueConstraint(fields=["tenant","user"],name="membership_unique")]
class Scoped(models.Model):
    id=models.UUIDField(primary_key=True,default=uuid.uuid4,editable=False)
    tenant=models.ForeignKey(Tenant,on_delete=models.PROTECT)
    objects=TenantManager()
    raw=models.Manager()
    class Meta: abstract=True
    def save(self,*a,**kw):
        if str(self.tenant_id)!=current_tenant.get():raise ValidationError("Contesto attività non valido.")
        for field in self._meta.fields:
            if isinstance(field,models.ForeignKey) and field.name!="tenant" and getattr(self,field.attname):
                related=getattr(self,field.name)
                if hasattr(related,"tenant_id") and related.tenant_id!=self.tenant_id:raise ValidationError("Relazione tra attività diverse.")
        super().save(*a,**kw)
class Location(Scoped):
    name=models.CharField(max_length=100)
    address=models.CharField(max_length=255,blank=True)
    phone=models.CharField(max_length=60,blank=True)
    active=models.BooleanField(default=True)
    # 2.14.0: the covers of the whole place, shared by its restaurant services (booking.shared); 0 = no such limit
    capacity=models.PositiveIntegerField(default=0)
    # 2.14.0: a hotel, B&B or holiday home (stays.py): the national and regional codes shown on the booking page and in
    # the emails, and the tourist tax of its town: euro a person a night, nights taxed at most (0 = all), exempt under this age
    cin=models.CharField(max_length=40,blank=True)
    cir=models.CharField(max_length=40,blank=True)
    ross_code=models.CharField(max_length=20,blank=True)  # the code the regional office gives for Ross1000 (ISTAT)
    tax_rate=models.DecimalField(max_digits=6,decimal_places=2,null=True,blank=True)
    tax_nights=models.PositiveSmallIntegerField(default=0)
    tax_age=models.PositiveSmallIntegerField(default=0)
    # 2.14.0: the town's rules (stays.tax): the category of the structure, the rates by period or season and category
    # [{"category","from","until","yearly","rate","nights"}], the nights a year taxed to workers and students (0 = no cap),
    # one tour escort exempt every tax_escort guests, the reduction in percent for the guests marked reduced
    tax_category=models.CharField(max_length=20,blank=True)
    tax_rules=models.JSONField(default=list,blank=True)
    tax_year_nights=models.PositiveSmallIntegerField(default=0)
    tax_escort=models.PositiveSmallIntegerField(default=25)
    tax_reduction=models.PositiveSmallIntegerField(default=0)
    # 2.14.0: who gets "Il primo disponibile" (booking.ranked), and online no hole shorter than min_gap minutes
    assign=models.CharField(max_length=8,default="alpha",choices=[("alpha","Il primo libero, in ordine di nome"),("last","L'operatore dell'ultima visita del cliente"),("even","Chi ha meno appuntamenti quel giorno"),("priority","L'ordine scelto per ogni operatore")])
    min_gap=models.PositiveIntegerField(default=0)
    # 2.14.0: a visit of several services (booking.visit_slots): who does them, and the longest wait between two
    visit_split=models.CharField(max_length=5,default="need",choices=[("same","Stesso operatore"),("split","Divisibile tra operatori"),("need","Divisibile solo se serve")])
    visit_wait=models.PositiveSmallIntegerField(default=15)
    # 2.14.0: restaurants (tables.py): tables given by hand or the best fit suggested, and online only with a table free
    tables=models.CharField(max_length=7,default="off",choices=[("off","Nessuna gestione dei tavoli"),("manual","Li assegnate voi"),("suggest","Il programma propone il tavolo più adatto")])
    tables_online=models.BooleanField(default=False)
    # 2.16.0: its fiscal register (cassa.py): {"device","address","serial","link","departments":{service id:{"dept","vat"}},
    # "default_dept","payments":{"cash","card","other"}}; the hash of the bridge's token and when the bridge last asked
    till=models.JSONField(default=dict,blank=True)
    till_token=models.CharField(max_length=64,blank=True,db_index=True)
    till_seen=models.DateTimeField(null=True,blank=True)
    def __str__(self):return self.name
class Receipt(Scoped):
    """2.14.0: the Alloggiati Web receipt of a day of sending (stays.receipts): how many guests went, the Polizia's PDF
    once downloaded. Kept five years, as the law asks."""
    day=models.DateField()
    rows=models.PositiveIntegerField(default=0)
    pdf=models.BinaryField(null=True,blank=True)
    class Meta: constraints=[models.UniqueConstraint(fields=["tenant","day"],name="receipt_day")]
class Area(Scoped):
    """2.14.0: a room of a restaurant (Sala, Dehors): the client may ask for it; capacity its own covers at once, 0 = none."""
    location=models.ForeignKey(Location,on_delete=models.CASCADE)
    name=models.CharField(max_length=60)
    capacity=models.PositiveIntegerField(default=0)
    def __str__(self):return self.name
class Table(Scoped):
    """2.14.0: a table of an area: covers it takes, its place in the order of the suggestion, the tables it can be put
    together with (joins: ids), where it stands on the floor plan (x, y: percent of the plan)."""
    area=models.ForeignKey(Area,on_delete=models.CASCADE,related_name="tables")
    name=models.CharField(max_length=30)
    min_covers=models.PositiveSmallIntegerField(default=1)
    max_covers=models.PositiveSmallIntegerField(default=4)
    priority=models.PositiveSmallIntegerField(default=0)
    joins=models.JSONField(default=list,blank=True)
    x=models.FloatField(default=5)
    y=models.FloatField(default=5)
    def __str__(self):return self.name
class Resource(Scoped):
    location=models.ForeignKey(Location,on_delete=models.PROTECT)
    name=models.CharField(max_length=100)
    # Named units (rooms, vehicles): guests per unit (0 = no limit), description and photo.
    capacity=models.PositiveIntegerField(default=0)
    description=models.TextField(blank=True)
    photo_url=models.URLField(max_length=500,blank=True)
    active=models.BooleanField(default=True)
    # 2.11.0: when its uploaded photo (ResourcePhoto) last changed, also the version in its address
    photo_at=models.DateTimeField(null=True,blank=True)
    # 2.14.0: the private calendar feed of its bookings (feeds.py): the version in the signed link, 0 = off; with the
    # client's name only when chosen. busy: an outside calendar (.ics address, encrypted) read as Occupato blocks
    feed=models.PositiveIntegerField(default=0)
    feed_names=models.BooleanField(default=False)
    busy=models.TextField(blank=True)
    busy_at=models.DateTimeField(null=True,blank=True)
    busy_error=models.CharField(max_length=200,blank=True)
    # 2.14.0: its Google Calendar (gcal.py): tokens and sync state encrypted, when last synced and what went wrong
    google=models.TextField(blank=True)
    google_at=models.DateTimeField(null=True,blank=True)
    google_error=models.CharField(max_length=200,blank=True)
    microsoft=models.TextField(blank=True)  # 2.14.0: the same with Outlook / Microsoft 365 (mscal.py)
    microsoft_at=models.DateTimeField(null=True,blank=True)
    microsoft_error=models.CharField(max_length=200,blank=True)
    # 2.14.0: fuori ufficio from day to day (both included): not bookable, and his stand-in takes "Il primo disponibile"
    # and is offered online instead of him. limits: bookings and booked minutes at most online (booking.limited)
    away_from=models.DateField(null=True,blank=True)
    away_until=models.DateField(null=True,blank=True)
    away_by=models.ForeignKey("self",null=True,blank=True,on_delete=models.SET_NULL,related_name="+")
    limits=models.JSONField(default=dict,blank=True)
    # 2.14.0: a room or a holiday home (stays.py): its cleaning, and the calendars of the portals read as closures
    # (addresses sealed: they hold the portal's secret), when last read and what went wrong
    housekeeping=models.CharField(max_length=5,default="clean",choices=[("clean","Pulita"),("dirty","Da pulire"),("out","Fuori servizio")])
    ical_in=models.TextField(blank=True)
    ical_at=models.DateTimeField(null=True,blank=True)
    ical_error=models.CharField(max_length=200,blank=True)
    # 2.14.0: a cabin, a machine, a car taken together with the operator (never chosen alone); out of "Il primo
    # disponibile"; its place in it when the location assigns by priority
    secondary=models.BooleanField(default=False)
    group=models.CharField(max_length=40,blank=True)  # cabins and machines: a service needs one free of each group it ticks
    auto_excluded=models.BooleanField(default=False)
    priority=models.PositiveIntegerField(default=0)
    def __str__(self):return self.name
    def photo_link(self,slug):
        """The uploaded photo, served by the program itself; otherwise the HTTPS address typed in, if any."""
        if self.photo_at:return f"/brand/{slug}/foto/{self.pk}.jpg?v={int(self.photo_at.timestamp())}"
        return self.photo_url
class ResourcePhoto(Scoped):
    """2.11.0: the photo of an operator, a room or a court, kept apart so that lists of resources do not load it."""
    resource=models.OneToOneField(Resource,on_delete=models.CASCADE,related_name="photo")
    data=models.BinaryField()
class Service(Scoped):
    location=models.ForeignKey(Location,on_delete=models.PROTECT)
    name=models.CharField(max_length=120)
    name_en=models.CharField("Nome in inglese (facoltativo)",max_length=120,blank=True)  # 2.14.0: on the English page and emails; empty = name
    description_en=models.TextField("Descrizione in inglese (facoltativa)",blank=True)
    kind=models.CharField(max_length=16,choices=[("resource","Appuntamento / risorsa"),("capacity","Capienza"),("event","Evento / tour"),("shift","Ristorante / pizzeria a turni"),("day","Soggiorno / noleggio")],default="resource")
    description=models.TextField(blank=True)
    duration=models.PositiveIntegerField(default=60)
    interval=models.PositiveIntegerField(default=30)
    capacity=models.PositiveIntegerField(default=1)
    max_party=models.PositiveIntegerField(default=1)
    buffer_before=models.PositiveIntegerField(default=0)
    buffer_after=models.PositiveIntegerField(default=0)
    lead_minutes=models.PositiveIntegerField(default=60)
    horizon_days=models.PositiveIntegerField(default=180)
    checkin=models.TimeField(default="15:00")
    checkout=models.TimeField(default="10:00")
    max_days=models.PositiveIntegerField(default=30)
    min_days=models.PositiveIntegerField(default=1)
    cancel_cutoff_minutes=models.PositiveIntegerField(default=0)  # 0 = online cancellation until start
    # 2.6.1: places by capacity. True: a place is free again after `duration` (time slots, table turnover).
    # False: the customer picks the arrival time and the places count for the whole opening window (lunch, dinner)
    turnover=models.BooleanField(default=True)
    # 2.14.0: places by capacity: the last arrival, minutes before each opening window closes (lunch 12-15 with 60:
    # last at 14:00); 0 = until closing, as before
    last_arrival=models.PositiveIntegerField(default=0)
    # 2.14.0: an online booking that waits for the business's yes (booking.needs_request); all off = confirmed at once
    request_all=models.BooleanField(default=False)
    request_above=models.PositiveIntegerField(default=0)  # more people than this; 0 = never
    request_allergies=models.BooleanField(default=False)
    # 2.14.0: restaurants. The arrivals of each time of the page (every `interval` minutes): covers and bookings at most,
    # online only unless pace_desk (booking.paced); the places sold online, the rest kept for the phone and who walks in
    # (0 = all); minutes by people when a place frees after a time: [[up to people, minutes], ...], bigger groups `duration`
    pace_covers=models.PositiveIntegerField(default=0)
    pace_bookings=models.PositiveIntegerField(default=0)
    pace_desk=models.BooleanField(default=False)
    online_capacity=models.PositiveIntegerField(default=0)
    party_durations=models.JSONField(default=list,blank=True)
    # 2.14.0: work, posa, finish: the posa starts pause_start minutes in and lasts pause_minutes (0 = none); the
    # operator is free during it (booking.posa). client_picks off: online the client does not choose with whom
    pause_start=models.PositiveIntegerField(default=0)
    pause_minutes=models.PositiveIntegerField(default=0)
    client_picks=models.BooleanField(default=True)
    # 2.14.0: what the client needs before arriving (keys, meeting point), sent by the message Prima dell'arrivo
    instructions=models.TextField(blank=True,max_length=2000)
    # 2.14.0: a distanza: the video-call link put in confirmation and reminders (a TD Meet room or any HTTPS address)
    remote_url=models.URLField(max_length=500,blank=True)
    # 2.14.0: the recall after the visit (desk list Richiami, email only with marketing consent) and the test that must be
    # done within so many days before the service (a warning at the desk, never a refusal); 0 = none
    recall_days=models.PositiveIntegerField(default=0)
    test_days=models.PositiveIntegerField(default=0)
    preset=models.CharField(max_length=40,blank=True)
    resource_label=models.CharField(max_length=60,blank=True)  # "Operatore", "Stanza", "Campo"...
    fields=models.JSONField(default=list,blank=True)
    active=models.BooleanField(default=True)
    # 2.11.0: the price list, kept by the business itself; empty = no price shown
    price=models.DecimalField(max_digits=8,decimal_places=2,null=True,blank=True)
    # 2.14.0: stays: rules for a period, [{"from","until","min","no_arrival":[weekdays],"no_departure":[...]}] (stays.restricted)
    stay_rules=models.JSONField(default=list,blank=True)
    # 2.14.0: limits online per day, week and month: {"day_n","day_m","week_n",...} bookings and minutes (booking.limited);
    # secret: not on the public page, booked only from its link /book/<slug>/?riservato=<secret>; empty = public.
    # thanks_url: the thank-you page after the online booking, instead of the business's
    limits=models.JSONField(default=dict,blank=True)
    secret=models.CharField(max_length=40,blank=True,db_index=True)
    thanks_url=models.URLField(max_length=500,blank=True)
    def __str__(self):return self.name
class ServiceResource(Scoped):
    service=models.ForeignKey(Service,on_delete=models.CASCADE,related_name="resource_links")
    resource=models.ForeignKey(Resource,on_delete=models.PROTECT)
    # 2.11.0: what this operator takes and asks for this service; empty = the service's. A variant keeps its own
    duration=models.PositiveIntegerField(null=True,blank=True)
    price=models.DecimalField(max_digits=8,decimal_places=2,null=True,blank=True)
    pause_start=models.PositiveIntegerField(null=True,blank=True)  # 2.14.0: his own posa; empty = the service's
    pause_minutes=models.PositiveIntegerField(null=True,blank=True)
    class Meta: constraints=[models.UniqueConstraint(fields=["service","resource"],name="service_resource_unique")]
class Option(Scoped):
    service=models.ForeignKey(Service,on_delete=models.PROTECT)
    name=models.CharField(max_length=100)
    duration=models.PositiveIntegerField(default=60)
    active=models.BooleanField(default=True)
    price=models.DecimalField(max_digits=8,decimal_places=2,null=True,blank=True)  # 2.11.0: empty = the service's
    def __str__(self):return self.name
class Schedule(Scoped):
    location=models.ForeignKey(Location,null=True,blank=True,on_delete=models.PROTECT)
    service=models.ForeignKey(Service,null=True,blank=True,on_delete=models.PROTECT)
    resource=models.ForeignKey(Resource,null=True,blank=True,on_delete=models.PROTECT)
    purpose=models.CharField(max_length=12,choices=[("service","Apertura"),("receive","Invio online"),("arrival","Arrivo / ritiro"),("departure","Partenza / consegna")],default="service")
    weekday=models.PositiveSmallIntegerField(choices=list(enumerate(["Lunedì","Martedì","Mercoledì","Giovedì","Venerdì","Sabato","Domenica"])))
    start=models.TimeField()
    end=models.TimeField()
    valid_from=models.DateField(null=True,blank=True)
    valid_until=models.DateField(null=True,blank=True)
    # 2.14.0: 2 = alternate weeks, counted from the week of valid_from (week A; week B starts a week later)
    every_weeks=models.PositiveSmallIntegerField(default=1)
class ExceptionDay(Scoped):
    location=models.ForeignKey(Location,null=True,blank=True,on_delete=models.PROTECT)
    service=models.ForeignKey(Service,null=True,blank=True,on_delete=models.PROTECT)
    resource=models.ForeignKey(Resource,null=True,blank=True,on_delete=models.PROTECT)
    purpose=models.CharField(max_length=12,default="service",choices=Schedule._meta.get_field("purpose").choices)
    date=models.DateField()
    until=models.DateField(null=True,blank=True)  # 2.14.0: a closure "dal … al …" on one row; empty = that day only
    closed=models.BooleanField(default=True)
    start=models.TimeField(null=True,blank=True)
    end=models.TimeField(null=True,blank=True)
    label=models.CharField(max_length=100,blank=True)
    # 2.14.0: nights taken by another portal (stays.refresh), replaced at every reading: the key of its calendar
    feed=models.CharField(max_length=16,blank=True)
class Occurrence(Scoped):
    rule=models.ForeignKey("ShiftRule",null=True,blank=True,on_delete=models.PROTECT)
    service=models.ForeignKey(Service,on_delete=models.PROTECT)
    start=models.DateTimeField()
    end=models.DateTimeField()
    capacity=models.PositiveIntegerField(default=1)
    active=models.BooleanField(default=True)
    series=models.UUIDField(null=True,blank=True,db_index=True)  # 2.14.0: the dates made together by "ripeti ogni N settimane"
class ShiftRule(Scoped):
    service=models.ForeignKey(Service,on_delete=models.PROTECT)
    name=models.CharField(max_length=100)
    weekday=models.PositiveSmallIntegerField(choices=Schedule._meta.get_field("weekday").choices)
    start=models.TimeField()
    end=models.TimeField()
    capacity=models.PositiveIntegerField(default=30)
    valid_from=models.DateField(null=True,blank=True)
    valid_until=models.DateField(null=True,blank=True)
    active=models.BooleanField(default=True)
    def __str__(self):return self.name
BLOCK_REASONS=[("break","Pausa"),("training","Formazione"),("meeting","Riunione"),("other","Altro"),("closed","Turno chiuso"),("busy","Occupato"),("google","Occupato su Google"),("outlook","Occupato su Outlook")]
class TimeBlock(Scoped):
    """2.14.0: time taken off the agenda without a booking: an operator's pause, training or meeting (Blocca orario),
    or a restaurant turn closed for one day (service). It takes the time as a booking does and is counted nowhere."""
    resource=models.ForeignKey(Resource,null=True,blank=True,on_delete=models.CASCADE)
    service=models.ForeignKey(Service,null=True,blank=True,on_delete=models.CASCADE)
    day=models.DateField()
    start=models.TimeField()
    end=models.TimeField()  # before start: until the next day
    weekly=models.BooleanField(default=False)
    until=models.DateField(null=True,blank=True)  # the last week of a weekly block; empty = always
    reason=models.CharField(max_length=10,choices=BLOCK_REASONS,default="break")
    pii=models.TextField(blank=True)  # the note, sealed: it may say why someone is away
    created=models.DateTimeField(auto_now_add=True)
    class Meta:indexes=[models.Index(fields=["tenant","day"])]
    def on(self,d):
        return d==self.day or self.weekly and d>self.day and (d-self.day).days%7==0 and (self.until is None or d<=self.until)
    @property
    def note(self):return decrypt(self.tenant,self.pii,"block:"+str(self.pk)) if self.pii else ""
class Contact(Scoped):
    email_index=models.CharField(max_length=64)
    pii=models.TextField()
    consent=models.CharField(max_length=12,default="none",choices=[("none","Non richiesto"),("pending","Da confermare"),("yes","Confermato"),("revoked","Revocato")])
    consent_at=models.DateTimeField(null=True)
    consent_proof=models.TextField(blank=True)
    suppressed=models.BooleanField(default=False)
    # 2.11.0: the client's own discount, on every booking from when it is set: a percentage or euro off the price
    discount_kind=models.CharField(max_length=8,blank=True,choices=[("","Nessuno"),("percent","Percentuale"),("amount","Importo fisso")])
    discount=models.DecimalField(max_digits=8,decimal_places=2,default=0)
    # 2.13.0: the client wants the emails about his bookings (confirmation, reminder, changes)
    notices=models.BooleanField(default=True)
    # 2.14.0: how he wants the reminders; empty = the business's channel (texts.py)
    channel=models.CharField(max_length=10,blank=True,choices=[("","Come imposta l'attività"),("email","Solo email"),("sms","SMS"),("whatsapp","WhatsApp")])
    # 2.14.0: the card of a client: VIP, refused on the online page (booking.create_booking), and the keyed indexes that find
    # it without opening it (clients.stamp): its phone, the starts of the words of its name, its tags, its birthday
    vip=models.BooleanField(default=False)
    online_blocked=models.BooleanField(default=False)
    phone_index=models.CharField(max_length=64,blank=True,db_index=True)
    name_index=models.TextField(blank=True)
    tag_index=models.TextField(blank=True)
    birthday_index=models.CharField(max_length=16,blank=True,db_index=True)
    # 2.14.0: a card merged into another one stays, without data, for its consent history (append-only)
    merged_into=models.ForeignKey("self",null=True,blank=True,on_delete=models.PROTECT,related_name="merged")
    created=models.DateTimeField(auto_now_add=True)
    updated=models.DateTimeField(null=True,blank=True)  # 2.20.0: the last change, its version for the site's archive (wpsync.ver); empty: created
    class Meta:constraints=[models.UniqueConstraint(fields=["tenant","email_index"],name="contact_email_unique")]
    @property
    def data(self):return decrypt(self.tenant,self.pii,"contact:"+str(self.pk))
class Booking(Scoped):
    service=models.ForeignKey(Service,on_delete=models.PROTECT)
    location=models.ForeignKey(Location,on_delete=models.PROTECT)
    resource=models.ForeignKey(Resource,null=True,blank=True,on_delete=models.PROTECT)
    option=models.ForeignKey(Option,null=True,blank=True,on_delete=models.PROTECT)
    occurrence=models.ForeignKey(Occurrence,null=True,blank=True,on_delete=models.PROTECT)
    contact=models.ForeignKey(Contact,null=True,blank=True,on_delete=models.PROTECT)  # unlinked by retention
    start=models.DateTimeField()
    end=models.DateTimeField()
    busy_start=models.DateTimeField()
    busy_end=models.DateTimeField()
    # 2.14.0: the posa, when the operator is free for another client; the cabin or machine taken with the operator
    gap_start=models.DateTimeField(null=True,blank=True)
    gap_end=models.DateTimeField(null=True,blank=True)
    secondary=models.ForeignKey(Resource,null=True,blank=True,on_delete=models.PROTECT,related_name="+")
    more=models.JSONField(default=list,blank=True)  # the ids of the cabins or machines of the other groups taken with it
    party=models.PositiveIntegerField(default=1)
    status=models.CharField(max_length=15,default="confirmed",choices=[("pending","In attesa di conferma"),("confirmed","Confermata"),("arrived","Arrivato"),("in_progress","In corso"),("completed","Completata"),("cancelled","Annullata"),("no_show","Assente")])
    # 2.14.0: where it came from, and the tag of the link (?src=instagram); empty for the bookings made before
    origin=models.CharField(max_length=10,blank=True,choices=[("public","Pagina pubblica"),("wordpress","Sito WordPress"),("desk","Banco"),("walkin","Senza appuntamento"),("import","Importata"),("agenda","Agenda del sito")])
    source=models.CharField(max_length=30,blank=True)
    series=models.UUIDField(null=True,blank=True,db_index=True)  # 2.14.0: the dates repeated together from the desk (Ripeti)
    visit=models.UUIDField(null=True,blank=True,db_index=True)  # 2.14.0: the services of one visit, one after the other
    # 2.14.0: the room the client asked for (empty = Indifferente) and the tables given to it (ids, tables.py)
    area=models.ForeignKey(Area,null=True,blank=True,on_delete=models.SET_NULL)
    tables=models.JSONField(default=list,blank=True)
    pii=models.TextField()
    snapshot=models.JSONField(default=dict)
    request_key=models.CharField(max_length=64)
    request_fingerprint=models.CharField(max_length=64)
    created=models.DateTimeField(auto_now_add=True)
    updated=models.DateTimeField(null=True,blank=True)  # 2.20.0: the last change, its version for the site's archive (wpsync.ver); empty: created
    class Meta:
        constraints=[models.UniqueConstraint(fields=["tenant","request_key"],name="booking_idempotency")]
        indexes=[models.Index(fields=["tenant","start","end"]),models.Index(fields=["resource","busy_start","busy_end"])]
    @property
    def data(self):return decrypt(self.tenant,self.pii,"booking:"+str(self.pk))
class WaitEntry(Scoped):
    """2.14.0: a client waiting for a place (waitlist.py): a service, an operator or anyone, a day or some days, a time
    window and how many. Name and contacts sealed like a booking's; snapshot: the consent to the notice, the free times
    already told (seen), who added it. Entries go away once their last day has passed (privacy.apply_retention, daily from the worker)."""
    service=models.ForeignKey(Service,on_delete=models.CASCADE)
    resource=models.ForeignKey(Resource,null=True,blank=True,on_delete=models.SET_NULL)
    contact=models.ForeignKey(Contact,on_delete=models.PROTECT,related_name="waits")
    first=models.DateField()
    last=models.DateField()  # a stay: the departure
    time_from=models.TimeField(null=True,blank=True)
    time_to=models.TimeField(null=True,blank=True)
    party=models.PositiveIntegerField(default=1)
    status=models.CharField(max_length=8,default="waiting",choices=[("waiting","In attesa"),("done","Fatta")])
    due=models.BooleanField(default=False)  # something freed on its days: the worker looks at it
    # queue mode: the place kept for this client until then (snapshot["hold"]: when, with whom, how many); it counts as
    # taken for everyone else (booking.capacity_available)
    held_until=models.DateTimeField(null=True,blank=True,db_index=True)
    pii=models.TextField()
    snapshot=models.JSONField(default=dict)
    created=models.DateTimeField(auto_now_add=True)
    updated=models.DateTimeField(null=True,blank=True)  # 2.22.0: the last change, its version for the site's archive (wpsync.ver): the site in charge tells the waiting list itself
    class Meta:indexes=[models.Index(fields=["tenant","status","last"])]
    @property
    def data(self):return decrypt(self.tenant,self.pii,"wait:"+str(self.pk))
class Treatment(Scoped):
    """2.8.0: one entry of the client record: when, what was done, by whom and the details (colour formula,
    products, inks). Sealed with the business key, never shown to the client, erased with the contact."""
    contact=models.ForeignKey(Contact,on_delete=models.PROTECT,related_name="treatments")
    booking=models.ForeignKey(Booking,null=True,blank=True,on_delete=models.SET_NULL,related_name="treatments")
    date=models.DateField()
    pii=models.TextField()
    author=models.CharField(max_length=36,blank=True)
    created=models.DateTimeField(auto_now_add=True)
    updated=models.DateTimeField(null=True,blank=True)  # 2.20.0: the last change, its version for the site's archive (wpsync.ver); empty: created
    class Meta:indexes=[models.Index(fields=["tenant","contact","date"])]
    @property
    def data(self):return decrypt(self.tenant,self.pii,"treatment:"+str(self.pk))
class ClientPhoto(Scoped):
    """2.22.9: a photo of the work done (a cut, a colour, a nail art), in the client's album by day, at most 4 a day: the
    JPEG compressed on the way in and sealed with the business key, a small copy for the lists. Kept in this program only,
    never sent to the site; it goes with the client's erasure and when the visit of that day loses its data."""
    contact=models.ForeignKey(Contact,on_delete=models.PROTECT,related_name="photos")
    booking=models.ForeignKey(Booking,null=True,blank=True,on_delete=models.SET_NULL,related_name="photos")
    date=models.DateField()
    image=models.BinaryField()
    thumb=models.BinaryField()
    created=models.DateTimeField(auto_now_add=True)
    class Meta:indexes=[models.Index(fields=["tenant","contact","date"])]
class ConsentEvent(Scoped):
    """Append-only consent history. No personal data: who and what live in Contact."""
    contact=models.ForeignKey(Contact,on_delete=models.PROTECT,related_name="consent_events")
    kind=models.CharField(max_length=12,choices=[("requested","Richiesto"),("confirmed","Confermato"),("revoked","Revocato"),("suppressed","Escluso dagli invii"),("erased","Dati cancellati"),("imported","Importato"),("merged","Schede unite")])
    purpose=models.CharField(max_length=30,default="email_marketing")
    channel=models.CharField(max_length=20)
    text_hash=models.CharField(max_length=64,blank=True)
    ref=models.CharField(max_length=64,blank=True)
    actor=models.CharField(max_length=36,blank=True)
    at=models.DateTimeField(auto_now_add=True)
    updated=models.DateTimeField(null=True,blank=True)  # 2.20.0: only the archive's version (erased with the card); the history itself never changes
    def save(self,*a,**kw):
        if not self._state.adding:raise ValueError("Lo storico dei consensi non si modifica.")
        super().save(*a,**kw)
    def delete(self,*a,**kw):raise ValueError("Lo storico dei consensi non si cancella.")
class SiteOutbox(Scoped):
    """2.18.0: a record changed (or gone) that the site's archive has not acked yet; one row per record, its number moved
    up at each change: what is sent is the record as it is when sent (wpsync.push)."""
    kind=models.CharField(max_length=10)
    ref=models.CharField(max_length=36)
    seq=models.BigIntegerField(db_index=True)
    class Meta:constraints=[models.UniqueConstraint(fields=["tenant","kind","ref"],name="outbox_record")]
class ActionToken(Scoped):
    digest=models.CharField(max_length=64,unique=True)
    kind=models.CharField(max_length=15)
    contact=models.ForeignKey(Contact,null=True,on_delete=models.CASCADE)
    booking=models.ForeignKey(Booking,null=True,on_delete=models.CASCADE)
    expires=models.DateTimeField()
    used=models.BooleanField(default=False)
class SupportGrant(Scoped):
    """Time-boxed access to tenant content for one platform staff user, granted by the owner."""
    user=models.ForeignKey(User,on_delete=models.CASCADE,related_name="support_grants")
    reason=models.CharField(max_length=300)
    expires=models.DateTimeField()
    created_by=models.CharField(max_length=36)
    revoked=models.BooleanField(default=False)
    created=models.DateTimeField(auto_now_add=True)
class Campaign(Scoped):
    name=models.CharField(max_length=100)
    subject=models.CharField(max_length=180)
    body=models.TextField()
    status=models.CharField(max_length=15,default="draft")
    scheduled=models.DateTimeField(null=True,blank=True)
    # 2.14.0: who among the subscribers: tag, VIP, last visit before or after, at least N bookings (mail.campaign_contacts)
    filters=models.JSONField(default=dict,blank=True)
    created=models.DateTimeField(auto_now_add=True)
class Delivery(Scoped):
    channel=models.CharField(max_length=10,default="email")  # email | telegram | webhook
    campaign=models.ForeignKey(Campaign,null=True,on_delete=models.PROTECT)
    contact=models.ForeignKey(Contact,null=True,on_delete=models.PROTECT)
    payload=models.TextField()
    status=models.CharField(max_length=15,default="pending")
    attempts=models.PositiveIntegerField(default=0)
    next_attempt=models.DateTimeField()
    sent_at=models.DateTimeField(null=True)
    claimed_at=models.DateTimeField(null=True)
    last_code=models.CharField(max_length=40,blank=True)
    # 2.14.0: a text about a booking, the provider's id of it and what the provider says of it later (texts.receipts)
    booking=models.ForeignKey(Booking,null=True,blank=True,on_delete=models.SET_NULL,related_name="+")
    ref=models.CharField(max_length=80,blank=True)
    receipt=models.CharField(max_length=10,blank=True,choices=[("","Non ancora"),("delivered","Consegnato"),("failed","Non consegnato")])
    created=models.DateTimeField(auto_now_add=True)
    class Meta:constraints=[models.UniqueConstraint(fields=["campaign","contact"],name="campaign_recipient_unique")]
class ClientProfile(Scoped):
    """What the agency keeps about its client: company data, contacts and internal notes.
    Sealed with the client's key and read only by the agency area, never by the client's portal."""
    data=models.TextField(blank=True)
    updated=models.DateTimeField(auto_now=True)
    class Meta:constraints=[models.UniqueConstraint(fields=["tenant"],name="client_profile_unique")]
class Sale(Scoped):
    """2.16.0: a documento commerciale asked of the location's fiscal register (cassa.py), or its annullo (voids), or a
    check of the connection (probe, test). The register emits and transmits it; this is what it answered. Lines,
    payments, codice fiscale, lotteria and the email of the copy sealed in pii; key: one bill sent once."""
    location=models.ForeignKey(Location,on_delete=models.PROTECT)
    booking=models.ForeignKey(Booking,null=True,blank=True,on_delete=models.SET_NULL,related_name="sales")
    contact=models.ForeignKey(Contact,null=True,blank=True,on_delete=models.SET_NULL,related_name="sales")
    voids=models.ForeignKey("self",null=True,blank=True,on_delete=models.PROTECT,related_name="voided_by")
    kind=models.CharField(max_length=6,default="sale",choices=[("sale","Scontrino"),("void","Annullo"),("probe","Prova collegamento"),("test","Stampa di prova"),("close","Chiusura giornaliera")])
    status=models.CharField(max_length=8,default="queued",choices=[("queued","In coda"),("sending","In corso"),("done","Emesso"),("failed","Non emesso"),("unknown","Da verificare"),("void","Annullato")])
    total=models.DecimalField(max_digits=9,decimal_places=2,default=0)
    number=models.CharField(max_length=20,blank=True)
    z=models.CharField(max_length=10,blank=True)
    serial=models.CharField(max_length=20,blank=True)
    printed=models.DateTimeField(null=True,blank=True)
    error=models.CharField(max_length=200,blank=True)
    claimed=models.DateTimeField(null=True,blank=True)
    mailed=models.DateTimeField(null=True,blank=True)
    # 2.20.9: what the client still owes (the «Da pagare» of the bill, non riscosso on the receipt), when and how he paid it
    due=models.DecimalField(max_digits=9,decimal_places=2,default=0)
    settled=models.DateTimeField(null=True,blank=True)
    settled_with=models.CharField(max_length=6,blank=True)
    key=models.CharField(max_length=64)
    actor=models.CharField(max_length=36,blank=True)
    pii=models.TextField()
    created=models.DateTimeField(auto_now_add=True)
    updated=models.DateTimeField(null=True,blank=True)  # 2.20.0: the last change, its version for the site's archive (wpsync.ver); empty: created
    class Meta:
        constraints=[models.UniqueConstraint(fields=["tenant","key"],name="sale_key")]
        indexes=[models.Index(fields=["location","status","created"])]
    @property
    def data(self):return decrypt(self.tenant,self.pii,"sale:"+str(self.pk))
    @property
    def doc(self):
        """2.22.2: the «Documento n.» as printed at the foot of the receipt: closure-number, four digits each (2039-0013)."""
        if self.z.isdigit() and self.number.isdigit():return f"{int(self.z):04d}-{int(self.number):04d}"
        return f"{self.z}-{self.number}" if self.z and self.number else self.number
class Product(Scoped):
    """2.22.0: what the shop sells besides its services (a shampoo, a brush): sold at the Cassa, written in the quotes."""
    name=models.CharField(max_length=80)
    price=models.DecimalField(max_digits=8,decimal_places=2,null=True,blank=True)
    active=models.BooleanField(default=True)
    created=models.DateTimeField(auto_now_add=True)
    def __str__(self):return self.name
class GiftCard(Scoped):
    """2.22.0: a prepaid credit in a client's name, sold at the Cassa (pending until the register emits that receipt),
    spent on the next bills; balance in euro, every spend and give-back in moves."""
    code=models.CharField(max_length=12)
    contact=models.ForeignKey(Contact,null=True,blank=True,on_delete=models.SET_NULL,related_name="gift_cards")
    sale=models.ForeignKey(Sale,null=True,blank=True,on_delete=models.SET_NULL,related_name="gift_cards")
    amount=models.DecimalField(max_digits=8,decimal_places=2)
    balance=models.DecimalField(max_digits=8,decimal_places=2)
    expires=models.DateField(null=True,blank=True)
    status=models.CharField(max_length=8,default="pending",choices=[("pending","Da emettere"),("active","Attiva"),("void","Annullata")])
    created=models.DateTimeField(auto_now_add=True)
    class Meta:constraints=[models.UniqueConstraint(fields=["tenant","code"],name="giftcard_code")]
class GiftMove(Scoped):
    card=models.ForeignKey(GiftCard,on_delete=models.CASCADE,related_name="moves")
    sale=models.ForeignKey(Sale,null=True,blank=True,on_delete=models.SET_NULL,related_name="gift_moves")
    cents=models.IntegerField()  # less than 0: spent on that receipt; more than 0: given back (receipt not emitted or voided)
    created=models.DateTimeField(auto_now_add=True)
class Parked(Scoped):
    """2.22.0: Preventivo pagamenti cassa: lines of a bill left out of the receipt (done, paid another day), on the
    client's card until they go in a bill. Lines and note sealed in pii."""
    location=models.ForeignKey(Location,on_delete=models.PROTECT)
    contact=models.ForeignKey(Contact,null=True,blank=True,on_delete=models.SET_NULL,related_name="parked")
    booking=models.ForeignKey(Booking,null=True,blank=True,on_delete=models.SET_NULL,related_name="parked")
    total=models.DecimalField(max_digits=9,decimal_places=2,default=0)
    key=models.CharField(max_length=66,blank=True)  # the bill's key: the same form sent twice parks once
    actor=models.CharField(max_length=36,blank=True)
    pii=models.TextField()
    created=models.DateTimeField(auto_now_add=True)
    class Meta:constraints=[models.UniqueConstraint(fields=["tenant","key"],name="parked_key")]
    @property
    def data(self):return decrypt(self.tenant,self.pii,"parked:"+str(self.pk))
class Quote(Scoped):
    """2.22.0: a quote for a client: who, lines of services, products or free text, notes, sealed in pii."""
    year=models.PositiveSmallIntegerField()
    number=models.PositiveIntegerField()
    contact=models.ForeignKey(Contact,null=True,blank=True,on_delete=models.SET_NULL,related_name="quotes")
    total=models.DecimalField(max_digits=9,decimal_places=2,default=0)
    valid_until=models.DateField(null=True,blank=True)
    actor=models.CharField(max_length=36,blank=True)
    pii=models.TextField()
    created=models.DateTimeField(auto_now_add=True)
    updated=models.DateTimeField(auto_now=True)
    class Meta:constraints=[models.UniqueConstraint(fields=["tenant","year","number"],name="quote_number")]
    @property
    def data(self):return decrypt(self.tenant,self.pii,"quote:"+str(self.pk))
    @property
    def ref(self):return f"{self.year}-{self.number:04d}"
class Audit(Scoped):
    actor=models.CharField(max_length=36,blank=True)
    action=models.CharField(max_length=80)
    target=models.CharField(max_length=80,blank=True)
    detail=models.JSONField(default=dict)
    created=models.DateTimeField(auto_now_add=True)
def audit(tenant,actor,action,target="",**detail):
    Audit.objects.create(tenant=tenant,actor=str(getattr(actor,"pk","") or ""),action=action,target=str(target),detail=detail)

class SiteSettings(models.Model):
    """One row for the whole portal: what the agency sets once instead of on every business."""
    name=models.CharField(max_length=60,default="Booking Suite")
    owner_name=models.CharField(max_length=80,default="Tastiere Digitali")
    owner_url=models.URLField(max_length=200,blank=True,default="https://www.tastieredigitali.it")
    brand_color=models.CharField(max_length=7,default="#7a55ad")
    # 2.8.1: the theme of the agency and business pages until each person picks one from the top bar
    default_theme=models.CharField(max_length=5,default="auto",choices=[("auto","Automatico, come il sistema"),("light","Chiaro"),("dark","Scuro")])
    logo=models.BinaryField(null=True,blank=True)
    # 2.5.0: a second logo for dark backgrounds, and the shape of each one (width / height)
    logo_dark=models.BinaryField(null=True,blank=True)
    logo_ratio=models.FloatField(default=1)
    logo_dark_ratio=models.FloatField(default=1)
    favicon=models.BinaryField(null=True,blank=True)
    login_title=models.CharField(max_length=140,default="Agenda, clienti e prenotazioni della tua attività, in un posto solo")
    login_text=models.CharField(max_length=240,default="Il portale di Tastiere Digitali per le attività che ricevono prenotazioni online")
    support_email=models.EmailField(blank=True)
    support_phone=models.CharField(max_length=40,blank=True)
    # 2.4.0: "try the service" on the sign-in page (Cloud edition)
    trial_enabled=models.BooleanField(default=True)
    trial_email=models.EmailField(blank=True)
    trial_days=models.PositiveSmallIntegerField(default=14)
    # 2.5.0: public "Prezzi e versioni" page (a showcase: off by default) and the privacy policy
    pricing_enabled=models.BooleanField(default=False)
    pricing_title=models.CharField(max_length=120,default="Prezzi e versioni")
    pricing_intro=models.CharField(max_length=300,default="Un prezzo chiaro, senza commissioni sulle prenotazioni. Il portale lo configuriamo noi, collegato al tuo sito")
    pricing_highlights=models.TextField(blank=True,default="Nessuna commissione | Paghi un canone fisso: più prenotazioni non vogliono dire più costi\nIl cliente resta tuo | Si prenota sul tuo sito, con il tuo logo, senza un portale che mostra i concorrenti\nDati protetti | Ogni attività ha i suoi dati cifrati e separati dalle altre\nTutti i tipi di prenotazione | Tavoli a turni, appuntamenti, camere, eventi, più sedi\nTe lo configuriamo noi | Sedi, orari, servizi e plugin sul sito: parti già pronto\nAssistenza vera | Persone che rispondono, in italiano")
    pricing_faq=models.TextField(blank=True,default="Ci sono commissioni sulle prenotazioni? | No. Paghi solo il piano scelto, qualunque sia il numero di prenotazioni\nPosso provarlo prima? | Sì: compila la richiesta di prova e prepariamo il portale per il tuo tipo di attività\nFunziona con il mio sito? | Sì: con WordPress usi il nostro plugin, con gli altri siti basta un link o un pulsante\nQual è la differenza tra Cloud e Private? | Cloud gira sui nostri server e pensiamo a tutto noi. Private si installa su un server tuo, per chi deve tenere i dati in casa")
    pricing_note=models.CharField(max_length=200,blank=True,default="Prezzi IVA esclusa")
    why_enabled=models.BooleanField(default=False)
    why_title=models.CharField(max_length=120,default="Perché sceglierci")
    why_intro=models.CharField(max_length=300,default="Ci sono tanti modi di ricevere prenotazioni online. Ecco cosa cambia con il nostro, punto per punto")
    why_points=models.TextField(blank=True,default="Più prenotazioni, stessi costi | Con un canone fisso il successo non ti costa di più: niente percentuali sui nuovi clienti, niente euro a coperto\nIl tuo sito lavora per te | Il cliente prenota dove ti ha cercato, con il tuo logo e i tuoi colori. Nessun elenco di concorrenti a due righe di distanza\nI clienti restano tuoi | Nomi, contatti e consensi sono dell'attività. Puoi scrivergli quando vuoi, e solo tu\nUn partner, non solo un software | Configuriamo tutto noi, colleghiamo il sito e rispondiamo quando serve. Le stesse persone che seguono il tuo sito e i tuoi social\nDati al sicuro | Ogni attività ha i suoi dati cifrati e separati. E se devi tenerli in casa, c'è la versione Private\nUn solo strumento per tutto | Tavoli a turni, appuntamenti con l'operatore, camere, eventi, più sedi: non serve cambiare programma se l'attività cresce")
    why_columns=models.CharField(max_length=200,default="TD Booking Suite | Portali con commissione | Agende online fai da te")
    why_rows=models.TextField(blank=True,default="Costo | Canone fisso | Commissione su prenotazioni, coperti o nuovi clienti | Canone, spesso con un limite di prenotazioni al mese\nDove prenota il cliente | Sul tuo sito, con il tuo marchio | Sul portale, accanto ad altre attività | Su una pagina del fornitore o nel tuo sito\nDi chi sono i contatti | Tuoi | Condivisi con il portale | Tuoi\nConfigurazione | La facciamo noi | Fai da te, con il supporto del portale | Fai da te\nTipi di prenotazione | Tavoli, appuntamenti, camere, eventi, più sedi | Di solito un solo settore | Soprattutto appuntamenti\nAssistenza | Persone che conosci, in italiano | Centro assistenza del portale | Email o chat del fornitore\nSul tuo server | Sì, con la versione Private | No | Di solito no")
    why_note=models.CharField(max_length=300,blank=True,default="Il confronto è per categorie di servizi e descrive le condizioni più comuni: i singoli servizi possono avere piani e condizioni diverse")
    legal_name=models.CharField(max_length=160,blank=True)
    vat_number=models.CharField(max_length=40,blank=True)
    legal_address=models.CharField(max_length=200,blank=True)
    privacy_email=models.EmailField(blank=True)
    data_location=models.CharField(max_length=120,blank=True)
    privacy_policy=models.TextField(blank=True)
    privacy_updated=models.DateField(null=True,blank=True)
    # agency SMTP for service emails (never for campaigns), sealed with the master key
    smtp=models.TextField(blank=True)
    # what a new business starts with
    default_timezone=models.CharField(max_length=64,default="Europe/Rome")
    default_brand_color=models.CharField(max_length=7,default="#7a55ad")
    default_privacy_text=models.TextField(default="I dati sono utilizzati per gestire la prenotazione. Contatta l'attività per l'informativa completa.")
    default_consent_text=models.TextField(default="Acconsento a ricevere novità e offerte via email. Posso revocare il consenso in qualsiasi momento.")
    default_retention_days=models.PositiveIntegerField(default=730)
    default_daily_limit=models.PositiveIntegerField(default=500)
    default_hourly_limit=models.PositiveIntegerField(default=100)
    updated=models.DateTimeField(auto_now=True)
    @classmethod
    def current(cls):
        from django.db import DatabaseError,transaction
        try:
            with transaction.atomic():
                obj,_=cls.objects.get_or_create(pk=1)
            return obj
        except DatabaseError:
            # database not migrated to this version yet: pages keep working with the defaults
            # (check_runtime stops web and worker and says which command to run)
            return cls(pk=1)
    @property
    def version(self):return str(int(self.updated.timestamp())) if self.updated else "0"
    # 2.5.0: which logo goes where, and whether it is a wide wordmark (shown alone, without the name)
    @property
    def light_wide(self):return (self.logo_ratio if self.logo else self.logo_dark_ratio if self.logo_dark else 1)>1.4
    @property
    def dark_wide(self):return (self.logo_dark_ratio if self.logo_dark else self.logo_ratio if self.logo else 1)>1.4

class TrialRequest(models.Model):
    """A business asking to try the portal from the sign-in page. Who asked is sealed with the master key;
    the agency turns it into a client (with the business type already chosen) or archives it."""
    id=models.UUIDField(primary_key=True,default=uuid.uuid4,editable=False)
    pii=models.TextField()
    preset=models.CharField(max_length=40,blank=True)
    status=models.CharField(max_length=10,choices=[("new","Da gestire"),("done","Attivata"),("archived","Archiviata")],default="new")
    client=models.ForeignKey(Tenant,null=True,blank=True,on_delete=models.SET_NULL,related_name="+")
    notified=models.DateTimeField(null=True,blank=True)
    attempts=models.PositiveSmallIntegerField(default=0)
    created=models.DateTimeField(auto_now_add=True)
    class Meta:indexes=[models.Index(fields=["status","created"])]
    @property
    def data(self):
        from .crypto import reveal
        return reveal(self.pii,"trial:"+str(self.pk))

class PricingPlan(models.Model):
    """2.5.0: one card of the public "Prezzi e versioni" page, edited from the agency area."""
    edition=models.CharField(max_length=8,choices=[("cloud","Cloud, in abbonamento"),("private","Private, sul server del cliente")],default="cloud")
    name=models.CharField(max_length=60)
    badge=models.CharField(max_length=40,blank=True)
    price=models.CharField(max_length=30)
    period=models.CharField(max_length=40,blank=True)
    price_note=models.CharField(max_length=120,blank=True)
    description=models.CharField(max_length=240,blank=True)
    features=models.TextField(blank=True)
    cta_label=models.CharField(max_length=40,blank=True)
    cta_url=models.CharField(max_length=300,blank=True)
    highlighted=models.BooleanField(default=False)
    active=models.BooleanField(default=True)
    order=models.PositiveSmallIntegerField(default=10)
    free_months=models.PositiveSmallIntegerField(default=0)
    class Meta:ordering=["edition","order","name"]
    @property
    def free_label(self):
        """2.6.0: "1 mese gratis", "3 mesi gratis", empty when there are none."""
        if not self.free_months:return ""
        return ("Il primo mese è gratis" if self.free_months==1 else "I primi "+str(self.free_months)+" mesi sono gratis")
    def feature_list(self):return [x.strip() for x in self.features.splitlines() if x.strip()]

# 2.18.0: every change of what the site archives goes in its outbox, when the site is the archive (wpsync.changed)
def _archived(sender,instance,**kw):
    from .wpsync import changed
    changed(sender,instance)
for _m in (Booking,Contact,Sale,Treatment,ConsentEvent,WaitEntry):
    models.signals.post_save.connect(_archived,sender=_m,dispatch_uid="archive-save-"+_m.__name__)
    models.signals.post_delete.connect(_archived,sender=_m,dispatch_uid="archive-delete-"+_m.__name__)
