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
    return {"preset":key,"kind":p["kind"],"resource_label":p["resource_label"],**p["defaults"]}

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
def units(business_type):
    """(singular, plural, help) of the things booked one at a time."""
    return UNITS.get(business_type,DEFAULT_UNITS)
