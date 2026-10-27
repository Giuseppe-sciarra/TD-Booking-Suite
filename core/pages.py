"""2.5.0: public pages of the portal, both edited from the agency area.

Prezzi e versioni: a showcase of plans and editions, off by default. It is meant for the demo
portal and switched off on an installation that only serves clients.
Privacy: the policy of the portal. With no custom text it shows a standard one, filled in with
the company data of Impostazioni generali."""
from django import forms
from django.conf import settings
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.http import Http404
from django.shortcuts import render,redirect,get_object_or_404
from django.utils import timezone
from django.utils.html import escape
from django.utils.safestring import mark_safe
from .models import SiteSettings,PricingPlan

def pairs(text):
    """Lines like "Title | text" -> [(title, text)]."""
    out=[]
    for line in (text or "").splitlines():
        if not line.strip():continue
        title,_,body=line.partition("|")
        out.append((title.strip(),body.strip()))
    return out

def plan_link(plan,site):
    if plan.cta_url:return plan.cta_url
    if plan.edition=="cloud" and site.trial_enabled and settings.EDITION=="cloud":return "/prova/"
    if site.support_email:return "mailto:"+site.support_email
    return "/prova/" if site.trial_enabled else "/login/"

def flags(site):
    cloud=settings.EDITION=="cloud"
    return {"pricing_on":cloud and site.pricing_enabled,"why_on":cloud and site.why_enabled}

def why_view(request):
    """Perché sceglierci: reasons and a comparison by category, a page of its own, off by default."""
    site=SiteSettings.current()
    if settings.EDITION!="cloud" or not site.why_enabled:raise Http404
    columns=[c.strip() for c in site.why_columns.split("|") if c.strip()]
    rows=[]
    for line in site.why_rows.splitlines():
        cells=[c.strip() for c in line.split("|")]
        if len(cells)>=2 and cells[0]:rows.append((cells[0],(cells[1:]+[""]*len(columns))[:len(columns)]))
    return render(request,"why.html",{"points":pairs(site.why_points),"columns":columns,"rows":rows,"trial_available":site.trial_enabled,"showcase":True,**flags(site)})

def pricing_view(request):
    site=SiteSettings.current()
    if settings.EDITION!="cloud" or not site.pricing_enabled:raise Http404
    plans=list(PricingPlan.objects.filter(active=True))
    for p in plans:p.link=plan_link(p,site)
    # 2.6.0: every plan on one row, same width and height, the edition as a label on the card
    return render(request,"pricing.html",{"plans":plans,"columns":min(len(plans),4),"highlights":pairs(site.pricing_highlights),"faq":pairs(site.pricing_faq),
        "trial_available":site.trial_enabled,"showcase":True,**flags(site)})

# privacy policy: plain text with "## " headings and "- " lists, nothing else, always escaped
STANDARD="""## Chi tratta i tuoi dati
Il titolare del trattamento è {legal_name}{address_part}{vat_part}. Per qualsiasi domanda sui tuoi dati scrivi a {email}.

## Quali dati trattiamo
- Per chi usa il portale: email, password conservata in forma non leggibile, eventuali Authenticator e passkey, registro degli accessi e delle operazioni
- Per chi chiede una prova del servizio: nome, cognome, email, telefono, nome e tipo dell'attività, messaggio
- Per i clienti delle attività che prenotano: i dati che inseriscono nella prenotazione

## Le prenotazioni presso un'attività
Quando prenoti presso un'attività, il titolare dei tuoi dati è l'attività stessa: trovi la sua informativa nella pagina prenotazioni. {legal_name} gestisce il portale per conto dell'attività come responsabile del trattamento e usa quei dati solo per farlo funzionare.

## Perché li trattiamo
- Per fornire il portale alle attività che lo usano, in base al contratto
- Per rispondere alle richieste di prova, su tua richiesta e con il tuo consenso
- Per la sicurezza del servizio, per esempio bloccare accessi sospetti, in base al nostro legittimo interesse
- Per rispettare gli obblighi di legge

## Per quanto tempo
I dati degli account restano finché dura il servizio e poi per il tempo richiesto dalla legge. Le richieste di prova si possono cancellare in ogni momento e le eliminiamo quando non servono più. Le prenotazioni restano per il tempo deciso da ogni attività.

## Dove sono e chi li vede
I dati sono conservati sui server gestiti da {legal_name}{location_part}, cifrati e separati attività per attività. Li vedono solo le persone autorizzate. Ci aiutano solo i fornitori necessari al servizio, per esempio per l'invio delle email. Non vendiamo i dati e non li usiamo per pubblicità.

## Cookie
Il portale usa solo cookie tecnici, necessari a farlo funzionare: la sessione di chi entra nel portale e la protezione dei moduli. Nella memoria del browser restano anche le tue scelte di visualizzazione, come il tema chiaro o scuro. Non ci sono cookie di profilazione né di terze parti, per questo non serve un banner per il consenso.

## I tuoi diritti
Puoi chiedere di vedere, correggere, cancellare o esportare i tuoi dati, limitarne l'uso o opporti al trattamento, scrivendo a {email}. Puoi anche presentare reclamo al Garante per la protezione dei dati personali (garanteprivacy.it)."""

def standard_policy(site):
    name=site.legal_name or site.owner_name or site.name
    email=site.privacy_email or site.support_email or "l'indirizzo indicato dall'agenzia"
    return STANDARD.format(legal_name=name,email=email,address_part=", "+site.legal_address if site.legal_address else "",
        vat_part=", partita IVA "+site.vat_number if site.vat_number else "",location_part=", a "+site.data_location if site.data_location else "")

def render_text(text):
    html=[];items=[];para=[]
    def close():
        if para:html.append("<p>"+" ".join(para)+"</p>");para.clear()
        if items:html.append("<ul>"+"".join("<li>"+i+"</li>" for i in items)+"</ul>");items.clear()
    for raw in text.splitlines():
        line=raw.strip()
        if not line:close();continue
        if line.startswith("### "):close();html.append("<h3>"+escape(line[4:])+"</h3>")
        elif line.startswith("## "):close();html.append("<h2>"+escape(line[3:])+"</h2>")
        elif line.startswith("- "):
            if para:close()
            items.append(escape(line[2:]))
        else:
            if items:close()
            para.append(escape(line))
    close()
    return mark_safe("".join(html))

def privacy_view(request):
    site=SiteSettings.current()
    text=site.privacy_policy.strip() or standard_policy(site)
    return render(request,"privacy.html",{"policy":render_text(text),"custom":bool(site.privacy_policy.strip()),"updated":site.privacy_updated,"showcase":True,**flags(site)})

# agency area
class PricingForm(forms.ModelForm):
    class Meta:
        model=SiteSettings
        fields=["pricing_enabled","pricing_title","pricing_intro","pricing_highlights","pricing_faq","pricing_note"]
        labels={"pricing_enabled":"Mostra la pagina pubblica Prezzi e versioni","pricing_title":"Titolo","pricing_intro":"Testo sotto il titolo",
                "pricing_highlights":"Punti di forza, uno per riga: Titolo | testo","pricing_faq":"Domande frequenti, una per riga: Domanda | risposta","pricing_note":"Nota sotto i prezzi"}
        widgets={"pricing_intro":forms.Textarea(attrs={"rows":2}),"pricing_highlights":forms.Textarea(attrs={"rows":7}),"pricing_faq":forms.Textarea(attrs={"rows":6})}
    def __init__(self,*a,**kw):kw.setdefault("label_suffix","");super().__init__(*a,**kw)

class WhyForm(forms.ModelForm):
    class Meta:
        model=SiteSettings
        fields=["why_enabled","why_title","why_intro","why_points","why_columns","why_rows","why_note"]
        labels={"why_enabled":"Mostra la pagina pubblica Perché sceglierci","why_title":"Titolo","why_intro":"Testo sotto il titolo",
                "why_points":"Motivi, uno per riga: Titolo | testo","why_columns":"Colonne del confronto, separate da |: la prima è la tua",
                "why_rows":"Righe del confronto, una per riga: Voce | tua colonna | seconda colonna | terza colonna","why_note":"Nota sotto il confronto"}
        widgets={"why_intro":forms.Textarea(attrs={"rows":2}),"why_points":forms.Textarea(attrs={"rows":7}),"why_rows":forms.Textarea(attrs={"rows":8})}
    def __init__(self,*a,**kw):kw.setdefault("label_suffix","");super().__init__(*a,**kw)

def safe_link(value):
    value=value.strip()
    if not value:return value
    if value.startswith(("https://","http://","mailto:","tel:")) or (value.startswith("/") and not value.startswith("//")):
        if any(c in value for c in "\"'<> \\"):raise forms.ValidationError("Il collegamento contiene caratteri non validi.")
        return value
    raise forms.ValidationError("Usa un indirizzo che inizia con https://, mailto:, tel: oppure /")

class PlanForm(forms.ModelForm):
    class Meta:
        model=PricingPlan
        fields=["edition","name","badge","price","period","free_months","price_note","description","features","cta_label","cta_url","highlighted","active","order"]
        labels={"edition":"Versione","name":"Nome del piano","badge":"Etichetta in evidenza, per esempio Il più scelto","price":"Prezzo, per esempio 29 € oppure Su misura",
                "period":"Periodo, per esempio al mese","price_note":"Nota sotto il prezzo","description":"Descrizione breve","features":"Cosa comprende, una voce per riga",
                "cta_label":"Testo del pulsante","cta_url":"Collegamento del pulsante (vuoto: richiesta di prova o email di assistenza)","highlighted":"Metti in evidenza questo piano",
                "active":"Visibile nella pagina","order":"Ordine (i numeri più bassi vengono prima)","free_months":"Mesi gratuiti"}
        help_texts={"free_months":"Per esempio 2: sulla scheda compare I primi 2 mesi sono gratis. Lascia 0 se non ce ne sono"}
        widgets={"features":forms.Textarea(attrs={"rows":6}),"description":forms.Textarea(attrs={"rows":2})}
    def __init__(self,*a,**kw):
        kw.setdefault("label_suffix","");super().__init__(*a,**kw)
        self.fields["free_months"].required=False
    def clean_cta_url(self):return safe_link(self.cleaned_data["cta_url"])
    def clean_free_months(self):
        v=self.cleaned_data["free_months"] or 0
        if v>24:raise forms.ValidationError("Al massimo 24 mesi.")
        return v

def staff(request):
    from .identity import fresh
    if not request.user.is_staff:raise PermissionDenied
    if settings.EDITION!="cloud":raise Http404
    fresh(request)

def pricing_admin(request):
    staff(request);site=SiteSettings.current();which=request.POST.get("page")
    form=PricingForm(request.POST if which=="pricing" else None,instance=site,prefix="pricing")
    why=WhyForm(request.POST if which=="why" else None,instance=site,prefix="why")
    for name,f,label,flag in (("pricing",form,"Prezzi e versioni","pricing_enabled"),("why",why,"Perché sceglierci","why_enabled")):
        if which==name and f.is_valid():
            f.save();messages.success(request,"Pagina "+label+" salvata"+("" if getattr(site,flag) else ": è spenta, nessuno la vede")+".")
            return redirect("pricing_admin")
    return render(request,"pricing_admin.html",{"form":form,"why":why,"plans":PricingPlan.objects.all(),"enabled":site.pricing_enabled,"why_enabled":site.why_enabled})

def plan_edit(request,plan_id=None):
    staff(request)
    plan=get_object_or_404(PricingPlan,pk=plan_id) if plan_id else None
    if request.method=="POST" and request.POST.get("action")=="delete" and plan:
        plan.delete();messages.success(request,"Piano eliminato.");return redirect("pricing_admin")
    form=PlanForm(request.POST or None,instance=plan)
    if request.method=="POST" and form.is_valid():
        form.save();messages.success(request,"Piano salvato.");return redirect("pricing_admin")
    return render(request,"plan_form.html",{"form":form,"plan":plan})
