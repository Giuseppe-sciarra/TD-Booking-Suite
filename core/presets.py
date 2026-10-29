"""Activity presets: they pre-fill an editable service and, in profile.py, a whole catalog.

kind is the booking engine: capacity, resource, day (stays and rentals), event (dated occurrences).
"""
from datetime import time

PRESETS={
 "ristorante":{"label":"Ristorante / Pizzeria","kind":"capacity","resource_label":"","hint":"Posti per fascia oraria, il tavolo occupa una durata.","defaults":{"duration":90,"interval":15,"capacity":30,"max_party":12,"turnover":False}},
 "parrucchiere":{"label":"Parrucchiere / Estetista","kind":"resource","resource_label":"Operatore","hint":"Operatori con agenda propria, prestazioni con durata.","defaults":{"interval":15,"max_party":1}},
 "medico":{"label":"Studio medico / Fisioterapia","kind":"resource","resource_label":"Professionista","hint":"Professionisti con agenda propria, visite con durata.","defaults":{"interval":15,"max_party":1}},
 "consulenza":{"label":"Consulenze professionali","kind":"resource","resource_label":"Consulente","hint":"Appuntamenti con professionisti, in studio o in videochiamata.","defaults":{"interval":30,"max_party":1}},
 "centro_estetico":{"label":"Centro estetico / Nails","kind":"resource","resource_label":"Operatrice","hint":"Trattamenti con operatrici e prestazioni con durata.","defaults":{"interval":15,"max_party":1}},
 "spa":{"label":"SPA / Massaggi","kind":"resource","resource_label":"Operatore o cabina","hint":"Massaggi e trattamenti: operatori o cabine con agenda propria.","defaults":{"interval":15,"max_party":2}},
 "tattoo":{"label":"Tattoo / Piercing","kind":"resource","resource_label":"Artista","hint":"Artisti con agenda propria, sedute anche lunghe.","defaults":{"interval":30,"max_party":1}},
 "autoscuola":{"label":"Autoscuola","kind":"resource","resource_label":"Istruttore","hint":"Lezioni di guida con istruttori e tipi di lezione.","defaults":{"interval":30,"max_party":1}},
 "campo":{"label":"Campi sportivi / Sale","kind":"resource","resource_label":"Campo o sala","hint":"Padel, tennis, calcetto, sale riunioni con calendario proprio.","defaults":{"interval":30,"max_party":10}},
 "corso":{"label":"Corsi / Lezioni","kind":"capacity","resource_label":"","hint":"Lezioni a orari fissi con capienza, per esempio yoga massimo 12.","defaults":{"duration":60,"interval":60,"capacity":12,"max_party":4}},
 "officina":{"label":"Officina / Gommista","kind":"capacity","resource_label":"","hint":"Capienza uguale ai ponti o alle postazioni disponibili.","defaults":{"duration":60,"interval":30,"capacity":2,"max_party":1}},
 "hotel":{"label":"Hotel / B&B","kind":"day","resource_label":"Camera","hint":"Camere con ospiti massimi, notti, check-in e check-out.","defaults":{"min_days":1,"max_days":30,"checkin":time(15),"checkout":time(10),"max_party":6}},
 "casa_vacanze":{"label":"Casa vacanze / Appartamenti","kind":"day","resource_label":"Alloggio","hint":"Alloggi con ospiti massimi e soggiorno minimo.","defaults":{"min_days":2,"max_days":30,"checkin":time(16),"checkout":time(10),"max_party":8}},
 "noleggio":{"label":"Noleggio bici, mezzi, attrezzatura","kind":"day","resource_label":"Mezzo","hint":"Mezzi noleggiabili a giorni con ritiro e riconsegna.","defaults":{"min_days":1,"max_days":366,"checkin":time(9),"checkout":time(19),"max_party":1}},
 "evento":{"label":"Evento / Serata","kind":"event","resource_label":"","hint":"Posti per ogni data dell'evento.","defaults":{"capacity":100,"max_party":10}},
 "tour":{"label":"Tour / Esperienze","kind":"event","resource_label":"","hint":"Partenze a orari fissi con posti per ogni data.","defaults":{"capacity":20,"max_party":10}},
}
CHOICES=[("","Personalizzato")]+[(k,v["label"]) for k,v in PRESETS.items()]

def initial(key):
    p=PRESETS.get(key)
    if not p:return {}
    return {"preset":key,"kind":p["kind"],"resource_label":p["resource_label"],**p["defaults"],"fields":questions(key)}

# 2.8.0: the questions each kind of business usually asks when someone books. They start in every new
# service of that type and stay editable in the service (Richieste aggiuntive alla prenotazione).
# Checked against the main booking software of each sector (Phorest, Fresha, Resy, Tablein, Doctolib, Tekmetric,
# Booking.com, Viator, Playtomic...): health questions stay out of the online form (they are asked in person),
# except the allergies a restaurant needs to cook safely.
def _q(key,label,type="checkbox",choices=None,required=False):
    return {"key":key,"label":label,"type":type,"required":required,**({"choices":choices} if choices else {})}
FIRST=_q("prima_volta","È la prima volta da noi")
ARRIVAL=_q("arrivo","Orario di arrivo previsto","select",["Nel pomeriggio, entro le 18","In serata, entro le 21","Dopo le 21"])
# a holiday home hands over the keys and identifies the guests in person: the arrival time is needed
ARRIVAL_NEEDED={**ARRIVAL,"required":True}
ACCESS=_q("accessibilita","Esigenze di accessibilità","text")
QUESTIONS={
 "ristorante":[_q("occasione","È un'occasione speciale?","select",["Compleanno","Anniversario","Cena di lavoro","Altro"]),_q("seggioloni","Seggioloni per bambini, quanti","number"),
               _q("spazio","Ci serve spazio per un passeggino o una sedia a rotelle"),_q("cane","Veniamo con un cane")],
 "parrucchiere":[FIRST],
 "centro_estetico":[FIRST],
 "spa":[FIRST,_q("pressione","Pressione del massaggio","select",["Leggera","Media","Forte"])],
 # minors: the age limits and the parents' signed consent change from region to region, so they book in person
 "tattoo":[_q("zona","Zona del corpo","text"),_q("dimensione","Dimensione indicativa","select",["Piccolo, fino a 5 cm","Medio, fino a 15 cm","Grande, oltre 15 cm","Copertura di un tatuaggio esistente"]),
           _q("stile","Colore o bianco e nero","select",["Bianco e nero","Colore","Da decidere insieme"]),
           _q("maggiorenne","Sono maggiorenne. Se hai meno di 18 anni chiamaci: serve il genitore",required=True)],
 "medico":[_q("per_chi","Per chi è la visita","select",["Per me","Per un familiare"]),_q("prima_visita","È la prima visita da questo professionista")],
 "consulenza":[_q("modalita","Come preferisci incontrarci","select",["In studio","Videochiamata","Telefono"]),_q("argomento","Di cosa vuoi parlare","text")],
 "autoscuola":[_q("tipo_guida","Tipo di guida","select",["Guida obbligatoria: veicolo e manovre","Guida obbligatoria: urbana","Guida obbligatoria: extraurbana e autostrada","Guida obbligatoria: notturna","Guida di esercitazione","Simulazione d'esame"]),_q("foglio_rosa","Ho il foglio rosa")],
 "campo":[_q("attrezzatura","Mi serve attrezzatura a noleggio, per esempio racchette o pallone")],
 "corso":[_q("livello","Il tuo livello","select",["Principiante","Intermedio","Avanzato"]),_q("prova","È una lezione di prova")],
 "officina":[_q("targa","Targa","text",required=True),_q("veicolo","Marca e modello","text"),_q("km","Chilometri attuali","number"),
             _q("attesa","Aspetti o lasci il veicolo","select",["Aspetto in officina","Lascio il veicolo"]),_q("auto_cortesia","Mi serve l'auto di cortesia")],
 "hotel":[ARRIVAL,_q("letto","Preferenza per il letto","select",["Matrimoniale","Due letti singoli","Indifferente"]),_q("culla","Mi serve una culla"),_q("animali","Viaggio con un animale")],
 "casa_vacanze":[ARRIVAL_NEEDED,_q("culla","Mi serve una culla"),_q("animali","Viaggio con un animale")],
 # one height per person: the frame size depends on it
 "noleggio":[_q("altezza","Altezza di chi pedala in cm, una per persona: per esempio 175, 160","text"),_q("casco","Mi serve il casco"),_q("seggiolino","Mi serve il seggiolino per bambini")],
 "evento":[ACCESS],
 "tour":[_q("lingua","Lingua della visita","select",["Italiano","Inglese","Francese","Tedesco","Spagnolo"]),_q("bambini","Età dei bambini, se ce ne sono","text"),ACCESS],
}
def questions(key):return [{**q,**({"choices":list(q["choices"])} if "choices" in q else {})} for q in QUESTIONS.get(key,[])]

# 2.8.0: the 14 allergens of Reg. UE 1169/2011, with lactose for the intolerance people ask about most.
# Restaurants start with them; the business adds or removes items in Configura l'attività
ALLERGENS=["Glutine","Lattosio","Latte e derivati","Uova","Frutta a guscio","Arachidi","Pesce","Crostacei","Molluschi","Soia","Sedano","Senape","Sesamo","Solfiti","Lupini"]

def first_settings(tenant,preset):
    """The type of business and what it brings along: restaurants ask about allergies from the first day."""
    tenant.business_type=preset
    if preset=="ristorante" and not tenant.allergens:tenant.allergens=list(ALLERGENS);tenant.allergens_on=True
    return ["business_type","allergens","allergens_on"]

# 2.8.0: the client record of businesses that work on the person: what was done, when and by whom.
# title, singular, label of "what", hint for the details, quick picks besides the services
RECORDS={
 "parrucchiere":("Trattamenti","trattamento","Trattamento","Per esempio: colore 6.3 con ossigeno 20 vol, posa 35 minuti, shampoo antigiallo",["Test di tollerabilità"]),
 "centro_estetico":("Trattamenti","trattamento","Trattamento","Per esempio: semipermanente rosso 112 su base rinforzante, pelle sensibile: usato il lenitivo",["Test di tollerabilità"]),
 "spa":("Trattamenti","trattamento","Trattamento","Per esempio: olio di mandorle, pressione media, insistere sulla cervicale",[]),
 "tattoo":("Sedute","seduta","Lavoro","Per esempio: avambraccio sinistro, linee e ombre, aghi 7RL, inchiostri e lotti usati, prossima seduta tra 4 settimane",["Consulenza","Seduta","Ritocco"]),
 # administrative only: the clinical record belongs to the clinic's own software
 "medico":("Visite e sedute","visita o seduta","Prestazione","Per esempio: seduta 3 di 10, esercizi consegnati, prossimo controllo tra un mese",["Prima visita","Visita di controllo"]),
 "consulenza":("Incontri","incontro","Argomento","Per esempio: cosa avete visto, documenti da preparare per la prossima volta",[]),
 "autoscuola":("Lezioni svolte","lezione","Tipo di guida","Per esempio: rotatorie e parcheggio in parallelo, da rivedere le precedenze",QUESTIONS["autoscuola"][0]["choices"]),
 "officina":("Interventi","intervento","Intervento","Per esempio: tagliando a 60.000 km, olio 5W30 e filtri, pastiglie anteriori al 30%",["Tagliando","Cambio gomme","Revisione"]),
}
GENERIC_RECORD=("Servizi svolti","servizio svolto","Servizio","Cosa hai fatto, i prodotti usati, cosa ricordare per la prossima volta",[])
def record(tenant):
    """What the business calls its client record, or None where there is none (restaurants, hotels, events).
    A custom business gets the generic one when it works by appointment."""
    t=getattr(tenant,"business_type","")
    if t in RECORDS:r=RECORDS[t]
    elif not t:
        from .models import Service
        if not Service.objects.filter(kind="resource").exists():return None
        r=GENERIC_RECORD
    else:return None
    return {"title":r[0],"one":r[1],"what":r[2],"hint":r[3],"suggest":r[4]}

# 2.6.0: what a business calls the people or things booked one at a time, instead of "risorse"
UNITS={
 "parrucchiere":("Operatore","Operatori","Chi lavora nel salone: ogni operatore ha la sua agenda e due clienti non finiscono mai sulla stessa persona alla stessa ora"),
 "medico":("Professionista","Professionisti","Medici e terapisti: ognuno ha la sua agenda e il paziente può scegliere da chi andare"),
 "consulenza":("Consulente","Consulenti","Chi riceve gli appuntamenti: ognuno ha la sua agenda"),
 "centro_estetico":("Operatrice","Operatrici","Chi fa i trattamenti: ognuna ha la sua agenda e la cliente può sceglierla"),
 "spa":("Cabina o operatore","Cabine e operatori","Le cabine e chi fa i massaggi: ognuno ha la sua agenda"),
 "tattoo":("Artista","Artisti","Chi tatua: ognuno ha la sua agenda e il cliente può sceglierlo"),
 "autoscuola":("Istruttore","Istruttori","Chi fa lezione: ognuno ha la sua agenda"),
 "campo":("Campo o sala","Campi e sale","I campi o le sale che si prenotano: ognuno ha il suo calendario"),
 "hotel":("Camera","Camere","Le camere che si prenotano, con quanti ospiti ci stanno, descrizione e foto"),
 "casa_vacanze":("Alloggio","Alloggi","Gli appartamenti o le case che si prenotano, con quanti ospiti ci stanno"),
 "noleggio":("Mezzo","Mezzi","Bici, auto o attrezzature che si noleggiano, una per una"),
}
DEFAULT_UNITS=("Operatore o postazione","Operatori e postazioni","Chi o cosa si prenota uno alla volta, con la sua agenda: un operatore, una cabina, una camera, un campo")
# 2.10.0: what the client books, in the words of the business: a salon books treatments
SERVICE_WORDS={"parrucchiere":"Trattamento","centro_estetico":"Trattamento","spa":"Trattamento","medico":"Visita","tattoo":"Seduta"}
def service_word(business_type):return SERVICE_WORDS.get(business_type,"Servizio")
def units(business_type):
    """(singular, plural, help) of the things booked one at a time."""
    return UNITS.get(business_type,DEFAULT_UNITS)
