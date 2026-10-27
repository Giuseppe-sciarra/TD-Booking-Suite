"""Durable outbox. Each recipient rechecked at dispatch; no automatic retry after uncertain DATA."""
import socket,ssl,smtplib,ipaddress,secrets
from datetime import timedelta
from email.message import EmailMessage
from email.utils import formataddr
from django.conf import settings
from django.db import transaction
from django.db.models import Q, Case, When, IntegerField
from django.utils import timezone
from .models import Tenant,Campaign,Contact,Delivery,audit
from .crypto import decrypt,encrypt
from .tenancy import scope
from .booking import action_token,queue,BookingError
from .middleware import operational

def public_addresses(host,port):
    if not host or any(c in host for c in "/@ \r\n"):raise ValueError("Host SMTP non valido.")
    found=socket.getaddrinfo(host,port,type=socket.SOCK_STREAM)
    addresses=[]
    for family,kind,proto,_,addr in found:
        ip=ipaddress.ip_address(addr[0])
        if not ip.is_global:raise ValueError("SMTP verso reti locali o riservate non consentito.")
        addresses.append((family,kind,proto,addr))
    if not addresses:raise ValueError("SMTP non risolvibile.")
    return addresses
class PinnedSMTP(smtplib.SMTP):
    def _get_socket(self,host,port,timeout):
        family,kind,proto,addr=public_addresses(host,port)[0]
        sock=socket.socket(family,kind,proto);sock.settimeout(timeout)
        try:sock.connect(addr);return sock
        except Exception:sock.close();raise
class PinnedSMTPSSL(smtplib.SMTP_SSL):
    def _get_socket(self,host,port,timeout):
        family,kind,proto,addr=public_addresses(host,port)[0]
        sock=socket.socket(family,kind,proto);sock.settimeout(timeout)
        try:
            sock.connect(addr)
            return self.context.wrap_socket(sock,server_hostname=host)
        except Exception:sock.close();raise
def smtp_config(tenant,marketing=False):
    if tenant.smtp:return decrypt(tenant,tenant.smtp,"smtp")
    if marketing:return None
    # service emails of a business without its own SMTP: the agency's, from the portal or from .env
    from .site import agency_smtp
    return agency_smtp()
def send_message(config,payload,message_id):
    cls=PinnedSMTPSSL if config.get("mode")=="ssl" else PinnedSMTP
    kwargs={"timeout":15}
    if cls is PinnedSMTPSSL:kwargs["context"]=ssl.create_default_context()
    message=EmailMessage()
    # 2.4.0: a display name, "Booking Suite TD <info@...>" instead of the bare address
    message["From"]=formataddr((config.get("sender_name") or "",config["sender"]));message["To"]=payload["to"]
    message["Subject"]=payload["subject"];message["Reply-To"]=config.get("reply_to") or config["sender"]
    message["Message-ID"]=message_id
    if payload.get("unsubscribe"):
        message["List-Unsubscribe"]="<"+payload["unsubscribe"]+">"
        message["List-Unsubscribe-Post"]="List-Unsubscribe=One-Click"
    message.set_content(payload["body"])
    if config.get("method","smtp")!="smtp":
        from .mailapi import send
        return send(config,message)
    with cls(config["host"],config["port"],**kwargs) as smtp:
        smtp.ehlo()
        if config["mode"]=="starttls":
            smtp.starttls(context=ssl.create_default_context());smtp.ehlo()
        if config["username"]:smtp.login(config["username"],config["password"])
        # 2.6.0: the envelope comes from the authenticated mailbox (smtp@...), the From header shows the
        # sender the customers see (info@...): servers such as Register.it and Aruba accept any address
        # of the domain this way, and SPF and DMARC stay aligned on the same domain
        user=config.get("username") or ""
        refused=smtp.send_message(message,from_addr=user if "@" in user else config["sender"])
        if refused:raise smtplib.SMTPRecipientsRefused(refused)
def send_test(config,to,name):
    """2.4.0: one email straight away, to check an SMTP configuration. Returns an error text or ""."""
    if not config:return "Nessun SMTP configurato: salvalo prima di provarlo."
    body=("Questa è un'email di prova di "+name+".\n\nSe la leggi, l'invio funziona: "+describe(config)+
          ", mittente "+formataddr((config.get("sender_name") or "",config["sender"]))+".\n")
    from .mailapi import ApiError
    try:
        if config.get("method","smtp")=="smtp":public_addresses(config["host"],int(config["port"]))
        send_message(config,{"to":to,"subject":"Email di prova da "+name,"body":body},"<test-"+secrets.token_hex(8)+"@"+settings.PUBLIC_ORIGIN.split("//")[-1]+">")
        return ""
    except ApiError as ex:return str(ex)
    except smtplib.SMTPAuthenticationError:return "Il server ha rifiutato utente o password."
    except smtplib.SMTPRecipientsRefused:return "Il server ha rifiutato il destinatario."
    except smtplib.SMTPSenderRefused:return "Il server non accetta questo mittente: deve essere un indirizzo autorizzato sull'account SMTP."
    except (smtplib.SMTPException,ssl.SSLError) as ex:return "Il server ha risposto con un errore: "+str(ex)[:200]
    except (OSError,ValueError) as ex:return "Server non raggiungibile: "+str(ex)[:200]
METHODS={"smtp":"SMTP","gmail":"Google / Gmail","graph":"Microsoft 365 / Exchange"}
def describe(config):
    """One line for pages and test emails: how this configuration sends."""
    m=config.get("method","smtp")
    if m=="gmail":return "Google / Gmail, account "+(config.get("account") or config["sender"])
    if m=="graph":return "Microsoft 365, casella "+config.get("mailbox","")
    return "server "+str(config.get("host",""))+", porta "+str(config.get("port",""))
def campaign_contacts():
    return Contact.objects.filter(consent="yes",suppressed=False).order_by("created")
def enqueue_campaign(tenant,campaign,actor):
    with transaction.atomic():
        tenant=Tenant.objects.select_for_update().get(pk=tenant.pk)
        campaign=Campaign.objects.select_for_update().get(pk=campaign.pk)
        if not tenant.marketing_enabled or not tenant.smtp or not operational(tenant):raise BookingError("Invio marketing non abilitato o SMTP non configurato.")
        if campaign.status!="draft":raise BookingError("Campagna già accodata.")
        recipients=campaign_contacts()
        if recipients.count()>5000:raise BookingError("Massimo 5.000 destinatari per campagna; suddividi gli invii.")
        if not recipients.exists():raise BookingError("Nessun destinatario con consenso confermato.")
        for contact in recipients.iterator(chunk_size=100):
            token=action_token(tenant,"unsubscribe",contact=contact,days=3650)
            link=f"{settings.PUBLIC_ORIGIN}/action/{tenant.slug}/{token}/"
            payload={"to":contact.data["email"],"subject":campaign.subject,"body":campaign.body+f"\n\n{tenant.name}\nDisiscriviti: {link}","unsubscribe":link+"one-click/"}
            delivery=queue(tenant,payload,contact,campaign)
            if campaign.scheduled and campaign.scheduled>timezone.now():delivery.next_attempt=campaign.scheduled;delivery.save(update_fields=["next_attempt"])
        campaign.status="queued";campaign.save(update_fields=["status"])
        audit(tenant,actor,"campaign.queued",campaign.pk)
def worker_once():
    counts={"accepted":0,"failed":0,"skipped":0,"unknown":0}
    for tenant in Tenant.objects.all().iterator():
        # A crashed worker after SMTP DATA has uncertain delivery: never retry automatically.
        with scope(tenant):
            Delivery.objects.filter(status="sending",claimed_at__lt=timezone.now()-timedelta(minutes=10)).update(status="unknown",last_code="worker_interrupted")
            eligible=Q(campaign__isnull=True)
            if tenant.marketing_enabled and operational(tenant):
                eligible|=Q(campaign__status="queued")
            candidates=list(Delivery.objects.filter(eligible,status="pending",next_attempt__lte=timezone.now())
                .annotate(priority=Case(When(campaign__isnull=True,then=0),default=1,output_field=IntegerField()))
                .order_by("priority","next_attempt","pk").values_list("pk",flat=True)[:50])
        for delivery_id in candidates:
            config=None;payload=None
            with scope(tenant):
                Tenant.objects.select_for_update().get(pk=tenant.pk)
                tenant.refresh_from_db()
                delivery=Delivery.objects.select_for_update().get(pk=delivery_id)
                if delivery.status!="pending":continue
                if delivery.campaign_id:
                    contact=delivery.contact
                    if not tenant.marketing_enabled or not operational(tenant) or delivery.campaign.status!="queued":continue
                    if contact.consent!="yes" or contact.suppressed:
                        delivery.status="skipped";delivery.last_code="not_eligible";delivery.save();counts["skipped"]+=1;continue
                channel=delivery.channel
                if channel=="telegram":
                    delivery.status="sending";delivery.claimed_at=timezone.now();delivery.attempts+=1;delivery.save()
                    payload=decrypt(tenant,delivery.payload,"delivery:"+str(delivery.pk))
                else:
                    recent=Delivery.objects.filter(channel="email",status__in=["accepted","sending","unknown"],claimed_at__gte=timezone.now()-timedelta(days=1))
                    # Separate budgets reserve notification capacity even after a campaign.
                    recent=recent.filter(campaign__isnull=not bool(delivery.campaign_id))
                    if recent.count()>=tenant.smtp_daily_limit or recent.filter(claimed_at__gte=timezone.now()-timedelta(hours=1)).count()>=tenant.smtp_hourly_limit:continue
                    config=smtp_config(tenant,marketing=bool(delivery.campaign_id))
                    if not config:
                        delivery.last_code="smtp_missing";delivery.next_attempt=timezone.now()+timedelta(minutes=10);delivery.save();continue
                    delivery.status="sending";delivery.claimed_at=timezone.now();delivery.attempts+=1;delivery.save()
                    payload=decrypt(tenant,delivery.payload,"delivery:"+str(delivery.pk))
            if channel=="telegram":
                from .notify import send_telegram,TelegramError
                status="accepted";code="";retry=False
                try:send_telegram(tenant,payload)
                except TelegramError as ex:
                    code=ex.code;retry=ex.temporary and delivery.attempts<5;status="pending" if retry else "failed"
                except Exception:status="unknown";code="delivery_uncertain"
                with scope(tenant):
                    d=Delivery.objects.select_for_update().get(pk=delivery_id)
                    d.status=status;d.last_code=code
                    if status=="accepted":d.sent_at=timezone.now()
                    if retry:d.next_attempt=timezone.now()+timedelta(minutes=2**d.attempts)
                    d.save()
                    if status in counts:counts[status]+=1
                continue
            status="accepted";code="";retry=False
            try:
                send_message(config,payload,f"<{delivery_id}@{settings.PUBLIC_ORIGIN.split('://',1)[1].split(':')[0]}>")
            except smtplib.SMTPRecipientsRefused as ex:
                codes=[int(value[0]) for value in ex.recipients.values()]
                temporary=any(400<=value<500 for value in codes)
                retry=temporary and delivery.attempts<5
                status="pending" if retry else "failed"
                # Only permanent recipient failures suppress future marketing.
                code="recipient_temporary" if temporary else "recipient_rejected"
            except smtplib.SMTPResponseException as ex:
                code=str(ex.smtp_code)
                retry=400<=ex.smtp_code<500 and delivery.attempts<5
                status="pending" if retry else "failed"
            except (ValueError,ssl.SSLCertVerificationError):
                status="failed";code="smtp_config"
            except Exception:
                status="unknown";code="delivery_uncertain"
            with scope(tenant):
                d=Delivery.objects.select_for_update().get(pk=delivery_id)
                d.status=status;d.last_code=code
                if status=="accepted":d.sent_at=timezone.now()
                if retry:d.next_attempt=timezone.now()+timedelta(minutes=2**d.attempts)
                d.save()
                if code=="recipient_rejected" and d.contact_id:
                    c=d.contact
                    if not c.suppressed:
                        c.suppressed=True;c.save(update_fields=["suppressed"])
                        from .privacy import record
                        record(tenant,c,"suppressed","smtp",ref=d.pk)
                if status in counts:counts[status]+=1
    return counts
