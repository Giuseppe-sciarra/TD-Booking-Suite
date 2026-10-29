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
    smtp=models.TextField(blank=True)
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
    created=models.DateTimeField(auto_now_add=True)
    def save(self,*a,**kw):
        if not self.wrapped_key:self.wrapped_key=protect(b64(secrets.token_bytes(32)),"tenant-key:"+str(self.pk))
        super().save(*a,**kw)
    def __str__(self):return self.name
    @property
    def logo_wide(self):return bool(self.logo) and self.logo_ratio>1.4
    @property
    def asked_allergens(self):return list(self.allergens) if self.allergens_on else []
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
    preset=models.CharField(max_length=40,blank=True)
    resource_label=models.CharField(max_length=60,blank=True)  # "Operatore", "Stanza", "Campo"...
    fields=models.JSONField(default=list,blank=True)
    active=models.BooleanField(default=True)
    # 2.11.0: the price list, kept by the business itself; empty = no price shown
    price=models.DecimalField(max_digits=8,decimal_places=2,null=True,blank=True)
    def __str__(self):return self.name
class ServiceResource(Scoped):
    service=models.ForeignKey(Service,on_delete=models.CASCADE,related_name="resource_links")
    resource=models.ForeignKey(Resource,on_delete=models.PROTECT)
    # 2.11.0: what this operator takes and asks for this service; empty = the service's. A variant keeps its own
    duration=models.PositiveIntegerField(null=True,blank=True)
    price=models.DecimalField(max_digits=8,decimal_places=2,null=True,blank=True)
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
class ExceptionDay(Scoped):
    location=models.ForeignKey(Location,null=True,blank=True,on_delete=models.PROTECT)
    service=models.ForeignKey(Service,null=True,blank=True,on_delete=models.PROTECT)
    resource=models.ForeignKey(Resource,null=True,blank=True,on_delete=models.PROTECT)
    purpose=models.CharField(max_length=12,default="service",choices=Schedule._meta.get_field("purpose").choices)
    date=models.DateField()
    closed=models.BooleanField(default=True)
    start=models.TimeField(null=True,blank=True)
    end=models.TimeField(null=True,blank=True)
    label=models.CharField(max_length=100,blank=True)
class Occurrence(Scoped):
    rule=models.ForeignKey("ShiftRule",null=True,blank=True,on_delete=models.PROTECT)
    service=models.ForeignKey(Service,on_delete=models.PROTECT)
    start=models.DateTimeField()
    end=models.DateTimeField()
    capacity=models.PositiveIntegerField(default=1)
    active=models.BooleanField(default=True)
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
    created=models.DateTimeField(auto_now_add=True)
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
    party=models.PositiveIntegerField(default=1)
    status=models.CharField(max_length=15,default="confirmed",choices=[("confirmed","Confermata"),("completed","Completata"),("cancelled","Annullata"),("no_show","Assente")])
    pii=models.TextField()
    snapshot=models.JSONField(default=dict)
    request_key=models.CharField(max_length=64)
    request_fingerprint=models.CharField(max_length=64)
    created=models.DateTimeField(auto_now_add=True)
    class Meta:
        constraints=[models.UniqueConstraint(fields=["tenant","request_key"],name="booking_idempotency")]
        indexes=[models.Index(fields=["tenant","start","end"]),models.Index(fields=["resource","busy_start","busy_end"])]
    @property
    def data(self):return decrypt(self.tenant,self.pii,"booking:"+str(self.pk))
class Treatment(Scoped):
    """2.8.0: one entry of the client record: when, what was done, by whom and the details (colour formula,
    products, inks). Sealed with the business key, never shown to the client, erased with the contact."""
    contact=models.ForeignKey(Contact,on_delete=models.PROTECT,related_name="treatments")
    booking=models.ForeignKey(Booking,null=True,blank=True,on_delete=models.SET_NULL,related_name="treatments")
    date=models.DateField()
    pii=models.TextField()
    author=models.CharField(max_length=36,blank=True)
    created=models.DateTimeField(auto_now_add=True)
    class Meta:indexes=[models.Index(fields=["tenant","contact","date"])]
    @property
    def data(self):return decrypt(self.tenant,self.pii,"treatment:"+str(self.pk))
class ConsentEvent(Scoped):
    """Append-only consent history. No personal data: who and what live in Contact."""
    contact=models.ForeignKey(Contact,on_delete=models.PROTECT,related_name="consent_events")
    kind=models.CharField(max_length=12,choices=[("requested","Richiesto"),("confirmed","Confermato"),("revoked","Revocato"),("suppressed","Escluso dagli invii"),("erased","Dati cancellati"),("imported","Importato")])
    purpose=models.CharField(max_length=30,default="email_marketing")
    channel=models.CharField(max_length=20)
    text_hash=models.CharField(max_length=64,blank=True)
    ref=models.CharField(max_length=64,blank=True)
    actor=models.CharField(max_length=36,blank=True)
    at=models.DateTimeField(auto_now_add=True)
    def save(self,*a,**kw):
        if not self._state.adding:raise ValueError("Lo storico dei consensi non si modifica.")
        super().save(*a,**kw)
    def delete(self,*a,**kw):raise ValueError("Lo storico dei consensi non si cancella.")
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
    created=models.DateTimeField(auto_now_add=True)
class Delivery(Scoped):
    channel=models.CharField(max_length=10,default="email")  # email | telegram
    campaign=models.ForeignKey(Campaign,null=True,on_delete=models.PROTECT)
    contact=models.ForeignKey(Contact,null=True,on_delete=models.PROTECT)
    payload=models.TextField()
    status=models.CharField(max_length=15,default="pending")
    attempts=models.PositiveIntegerField(default=0)
    next_attempt=models.DateTimeField()
    sent_at=models.DateTimeField(null=True)
    claimed_at=models.DateTimeField(null=True)
    last_code=models.CharField(max_length=40,blank=True)
    created=models.DateTimeField(auto_now_add=True)
    class Meta:constraints=[models.UniqueConstraint(fields=["campaign","contact"],name="campaign_recipient_unique")]
class ClientProfile(Scoped):
    """What the agency keeps about its client: company data, contacts and internal notes.
    Sealed with the client's key and read only by the agency area, never by the client's portal."""
    data=models.TextField(blank=True)
    updated=models.DateTimeField(auto_now=True)
    class Meta:constraints=[models.UniqueConstraint(fields=["tenant"],name="client_profile_unique")]
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
