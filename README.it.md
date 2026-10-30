# TD Booking Suite

**Prenotazioni online, agenda e scheda clienti per le piccole attività** — ristoranti,
parrucchieri e centri estetici, studi di tatuaggi, studi medici, consulenti, autoscuole,
officine, hotel, case vacanza, noleggi, corsi, eventi e tour. Un portale per l'agenzia che segue
molte attività, una pagina prenotazioni per ogni attività, un plugin WordPress per mostrarla nel
sito dell'attività. Da installare sul proprio server con Docker, ogni attività chiusa con la sua
chiave.

🇬🇧 [Read in English](README.md)

![Scheda cliente](docs/screenshots/01-client-record.png)

---

## Schermate

| | |
|---|---|
| **Prenotazione** — la nota sul cliente in cima, i trattamenti precedenti prima di cominciare, *Registra trattamento* a lavoro finito.<br>![Dettaglio prenotazione](docs/screenshots/02-booking-detail.png) | **Ristorante, stasera** — i tavoli del giorno, le intolleranze in arancione.<br>![Dashboard del ristorante](docs/screenshots/03-restaurant-dashboard.png) |
| **Pagina prenotazioni** — i 14 allergeni UE più il lattosio da spuntare, e le domande del ristorante.<br>![Modulo del ristorante](docs/screenshots/04-booking-form-restaurant.png) | **Intolleranze e allergie** — accese o spente, voci da aggiungere e togliere, l'anteprima di cosa vede il cliente.<br>![Intolleranze e allergie](docs/screenshots/05-allergens.png) |
| **Studio di tatuaggi** — zona, dimensione, colore o bianco e nero, online solo maggiorenni.<br>![Modulo del tatuatore](docs/screenshots/07-booking-form-tattoo.png) | **Domande** — ogni servizio ha le sue: testo, numero, sì/no, elenco; facoltative o obbligatorie.<br>![Domande del modulo](docs/screenshots/08-booking-questions.png) |
| **Agenda del giorno** — una colonna per ogni operatore che lavora, con la sua foto; un clic su uno spazio libero e la prenotazione parte con operatore, giorno e ora.<br>![Agenda](docs/screenshots/06-agenda.png) | **Prenotazione al banco** — il cliente si trova per nome, telefono o email, oppure se ne scrive uno nuovo.<br>![Nuova prenotazione](docs/screenshots/09-new-booking-client-search.png) |
| **Clienti** — la rubrica con le visite e la prossima prenotazione.<br>![Clienti](docs/screenshots/10-clients.png) | **Dashboard del salone** — oggi e i prossimi 7 giorni da cliccare, le prenotazioni ricevute, come sono andate.<br>![Dashboard del salone](docs/screenshots/11-salon-dashboard.png) |
| **Agenzia** — tutte le attività a colpo d'occhio, e cosa chiede attenzione.<br>![Dashboard dell'agenzia](docs/screenshots/12-agency-dashboard.png) | **Clienti dell'agenzia** — logo, settore, referente, numeri.<br>![Clienti dell'agenzia](docs/screenshots/13-agency-clients.png) |
| **Scheda del cliente dell'agenzia** — anagrafica, logo, referenti, configurazione e utilizzo. Solo numeri: i clienti dell'attività restano nel suo portale.<br>![Scheda del cliente](docs/screenshots/14-agency-client.png) | **Accesso** — password, app Authenticator o passkey; *Resta collegato* finché non premi Esci.<br>![Accesso](docs/screenshots/18-login.png) |
| **Settimana** — i sette giorni ora per ora, un colore per operatore, per tutti o per uno solo.<br>![Settimana](docs/screenshots/19-agenda-week.png) | **Mese** — quanti appuntamenti ha ogni giorno e i primi; un clic apre il giorno.<br>![Mese](docs/screenshots/20-agenda-month.png) |
| **Operatore** — la foto, cosa fa con la sua durata e il suo prezzo, i suoi giorni e orari.<br>![Operatore](docs/screenshots/21-operator.png) | **Prossimi 7 giorni** — dalla dashboard, chi viene e a che ora, giorno per giorno.<br>![Prossimi 7 giorni](docs/screenshots/22-next-7-days.png) |
| **Email ai clienti** — quali partono, il promemoria, i testi con i campi e l'anteprima.<br>![Email ai clienti](docs/screenshots/23-client-emails.png) | **Logo e aspetto** — logo, colore e frase dell'attività, con l'anteprima del menu e della pagina prenotazioni.<br>![Logo e aspetto](docs/screenshots/24-logo-look.png) |
| **Persone e permessi** — solo per l'agenzia: ruoli, sedi, risorse, password.<br>![Persone e permessi](docs/screenshots/25-people.png) | **Tema scuro** — automatico, chiaro o scuro.<br>![Tema scuro](docs/screenshots/15-dark-theme.png) |
| **Dal telefono** — la scheda cliente e la pagina prenotazioni.<br><img src="docs/screenshots/16-mobile-client.png" alt="Scheda cliente dal telefono" width="49%"> <img src="docs/screenshots/17-mobile-booking.png" alt="Pagina prenotazioni dal telefono" width="49%"> | |

I dati sono una demo con nomi inventati e indirizzi `example.com`.

## Cosa fa

**Prenotazioni**
- Cinque modi di prenotare: **posti per fascia oraria** (ristoranti, corsi, officine), **agenda di
  una persona o di una risorsa** (parrucchieri, professionisti, tatuatori, istruttori, campi),
  **date con posti** (eventi, tour), **turni**, **giorni e notti** (camere, appartamenti, noleggi).
- Sedi, orari, chiusure e giorni speciali (per tutta l'attività, una sede, un servizio o una
  risorsa), servizi con durata, passo, pause prima e dopo, preavviso minimo, fino a quanti giorni
  avanti, termine per annullare online, varianti, persone per prenotazione.
- La disponibilità si ricontrolla quando il cliente conferma; ogni scrittura prende lo stesso
  blocco, così due clienti non prendono mai lo stesso orario. Una richiesta inviata due volte è una
  prenotazione sola.
- **16 tipi di attività** pronti: scegli il tipo e quante sedi, e servizi, risorse, orari e domande
  sono pronti, da ritoccare. Si può riapplicare anche dopo: le prenotazioni restano.

**Scheda cliente**
- **Trattamenti, sedute, visite, lezioni, interventi** — il nome segue l'attività: data, cosa è
  stato fatto, con chi e i dettagli (la formula del colore, gli inchiostri e i lotti, i
  chilometri). Linea del tempo per anno, dalla più recente, modificabile. *Registra trattamento*
  dalla scheda o da una prenotazione passata, già compilato con giorno, servizio, variante e
  operatore; nella prenotazione l'operatore vede le volte precedenti prima di cominciare.
- **Nota sul cliente** in ogni prenotazione ("preferisce Anna", "allergica al nichel"): si scrive
  una volta nella scheda e compare in cima a ogni sua prenotazione. Il cliente non la vede.
- La scheda tiene sempre in vista chi è il cliente (recapiti, allergie, sconto, note) e mette il
  resto in schede: panoramica con quanto ha speso, visite, prossimi appuntamenti, servizio e
  operatore preferiti, disdette e assenze; prenotazioni con quanto ha pagato; trattamenti; consensi
  e privacy.
- **Sconto del cliente**, in percentuale o fisso in euro, da quel momento, prenotazioni in
  programma comprese; ogni prenotazione tiene il suo prezzo, come uno scontrino.
- **Lo stesso cliente, una scheda**: si riconosce dall'email, poi dal telefono. Chi è stato
  inserito al banco con il solo numero ritrova la sua scheda quando prenota online; due email
  diverse restano due persone, anche con lo stesso telefono, e un nome diverso sullo stesso numero
  (una madre e la figlia) ha la sua scheda.
- **Importa dal programma di prima** — anagrafica e storico dei trattamenti da Excel o CSV:
  si sceglie cosa contiene ogni colonna, si guarda l'anteprima, si importa. Le schede che ci sono
  già prendono solo i dati mancanti; i consensi marketing restano da confermare.

**Pagina prenotazioni**
- Una pagina per attività (`/book/<attività>/`), con il suo logo, anche dal telefono.
- **Domande per tipo di attività**, confrontate con i gestionali di settore (Phorest, Fresha, Resy,
  Tablein, Doctolib, Tekmetric, Booking.com, Viator, Playtomic): occasione, seggioloni, passeggino
  o sedia a rotelle, cane al ristorante; prima volta dal parrucchiere; pressione del massaggio
  nella SPA; zona, dimensione, stile e solo maggiorenni dal tatuatore; targa, modello,
  chilometri, attesa o consegna e auto di cortesia in officina; orario di arrivo, letto, culla e
  animali in hotel… Nessuna domanda sulla salute nel modulo online, tranne gli allergeni del
  ristorante.
- **Intolleranze e allergie** per i ristoranti, gestite dall'attività; dati sulla salute visibili
  solo a chi vede le note, mai nelle notifiche, cancellati con i dati del cliente.
- Email di conferma con il link per gestire o annullare la prenotazione.
- **Plugin WordPress**: si collega il sito con un codice monouso e nasce la pagina *Prenota* con
  `[td_booking_suite]`; shortcode per una sola sede o un solo servizio; il riquadro segue l'altezza
  del modulo.

**Portale dell'attività**
- **Agenda del giorno, della settimana e del mese**: nel giorno una colonna per ogni operatore che
  lavora, la settimana ora per ora con gli appuntamenti affiancati, il mese con quanti ne ha ogni
  giorno; ogni operatore ha il suo colore e la sua foto, e si guarda l'agenda di uno solo. I
  soggiorni compaiono in ogni giorno, con arrivo, "in casa" e partenza.
- **Operatori con foto** e cosa fa ciascuno, con la sua durata e il suo prezzo se diversi da quelli
  del trattamento; i suoi giorni e orari, le ferie.
- **Il listino lo tiene l'attività**: servizi e varianti con i loro prezzi; il tipo di prenotazione
  resta dell'agenzia.
- Dashboard, agenda, prenotazioni al banco, clienti, export CSV, campagne marketing con consenso
  confermato e disiscrizione con un clic, notifiche al personale via email e Telegram (senza note,
  risposte né allergie), registro attività.
- **Email ai clienti con promemoria**: conferma, modifica e annullamento partono da sole; quando
  l'agenzia lo attiva l'attività sceglie quali inviare, aggiunge il promemoria (da 2 ore a 3 giorni
  prima) e ne scrive il testo, con l'anteprima. Il cliente sceglie se riceverle.
- **Logo e aspetto** tenuti dall'attività, con l'anteprima; la dashboard mostra chi viene oggi e nei
  prossimi 7 giorni. Campagne e CSV degli iscritti li accende l'agenzia: senza, la voce Marketing
  non compare.
- Persone e ruoli, dati dall'agenzia: amministratore, amministratore sede, reception, operatore
  sede (solo la sua agenda e le sue sedi), marketing, consultazione.
- Email con l'SMTP dell'attività, Gmail o Microsoft 365, oppure quello dell'agenzia; coda di
  invio con nuovi tentativi.

**Area agenzia**
- Clienti con logo, anagrafica, referenti e numeri di utilizzo — mai i clienti dell'attività.
- Nome del portale, loghi per sfondi chiari e scuri, colore, testi della pagina di accesso, email
  dell'agenzia.
- Richieste di prova dalla pagina di accesso, pagine pubbliche prezzi e "perché sceglierci"
  (spente finché non le accendi), download del plugin WordPress.
- **Accesso di assistenza** concesso dall'attività, a tempo e registrato: senza, l'agenzia gestisce
  solo la parte tecnica.
- Due edizioni nello stesso codice: **Cloud** (molte attività) e **Private** (una sola attività).
  Si passa dall'una all'altra in entrambi i sensi.

**Sicurezza e privacy**
- Dati dei clienti cifrati con **AES-GCM e una chiave per attività**; email e telefono cercati con
  indici a chiave.
- **Row-level security** di PostgreSQL su ogni tabella delle attività (forzata, anche per il
  proprietario delle tabelle) e chiavi esterne che non possono puntare ai dati di un'altra
  attività.
- Verifica in due passaggi con **app Authenticator o passkey**, codici di recupero, conferma della
  password per le pagine sensibili, limiti ai tentativi, registro delle attività. *Resta collegato*
  tiene dentro un dispositivo finché non preme Esci (o i giorni di `TDBS_SESSION_REMEMBER_DAYS`).
- Export e cancellazione GDPR per cliente, regole di conservazione per attività, rotazione delle
  chiavi, backup cifrati.
- **Aggiornamento automatico ogni notte** di Python, Debian, librerie e PostgreSQL 17.x,
  con il backup prima e il ritorno automatico alla versione di prima se quella nuova non passa le prove.

## Cosa serve

- Un server Linux con Docker e Docker Compose (va bene un container LXC su Proxmox con *nesting* e
  *keyctl* attivi).
- `python3` con `python3-cryptography` sul server, per creare la configurazione.
- Un reverse proxy con HTTPS davanti (NPMplus, Nginx Proxy Manager, Caddy, Traefik…).

| Porta | Protocollo | Cosa |
|---|---|---|
| `TDBS_PORT` (predefinita 8085) | TCP | il portale, dietro il reverse proxy |

Il database e il worker delle email restano sulla rete interna di Docker. Apri la porta 8085 solo
all'indirizzo del proxy.

## Installazione

```bash
git clone https://github.com/Giuseppe-sciarra/TD-Booking-Suite.git /opt/td-booking-suite
cd /opt/td-booking-suite

# 1. Configurazione: crea .env con tutte le chiavi (copialo nel gestore di password)
python3 scripts/configure.py --edition cloud --origin https://prenota.example.com

# 2. Build, database, avvio
docker compose build
docker compose up -d db
docker compose --profile tools run --rm migrate
docker compose up -d

# 3. Primo amministratore dell'agenzia
docker compose run --rm web python manage.py bootstrap --email admin@example.com

# 4. Chiave dei backup e aggiornamento automatico ogni notte
python3 scripts/backup_crypto.py keygen /root/tdbs-backup.key
bash scripts/installa-aggiornamento.sh
```

Copia `/root/tdbs-backup.key` nel gestore di password, separata da `.env`: senza, i backup non si
leggono e l'aggiornamento automatico non parte.

Nel proxy punta il dominio a `http://<server>:8085`, con HTTPS. `--origin` è l'indirizzo che gli
utenti aprono nel browser. `--edition private` installa l'edizione per una sola attività.

**`.env` contiene le chiavi che cifrano i dati dei clienti**: se lo perdi quei dati non si leggono
più, nemmeno dal backup.

La guida completa è in [INSTALLAZIONE.md](INSTALLAZIONE.md): reverse proxy, prima attività e sito
WordPress, backup cifrati ogni notte e ripristino, conservazione dei dati, aggiornamenti, cambio di
edizione, cosa fare se qualcosa non va.

## Aggiornare

```bash
cd /opt/td-booking-suite
git pull
docker compose build
docker compose --profile tools run --rm migrate
docker compose up -d --remove-orphans
```

Prima fai il backup e leggi il [CHANGELOG](CHANGELOG.md) della versione nuova.

**Oppure ogni notte, da solo**: `bash scripts/installa-aggiornamento.sh` pianifica `scripts/aggiorna-tutto.sh`
(timer systemd, o cron). Prende l'ultima immagine di Python 3.12 con i suoi pacchetti Debian, tutte le
librerie Python all'ultima versione (nei limiti di `requirements.in`) e l'ultimo PostgreSQL 17.x. Prima fa il
backup cifrato, poi prova la versione nuova dentro il suo container; se qualcosa non va torna da solo a quella
di prima, database compreso, e manda un'email di riepilogo. Il codice di TD Booking Suite non lo tocca e GitHub
non lo guarda: la versione nuova la installi tu, coi comandi qui sopra. I dettagli sono in
[INSTALLAZIONE.md](INSTALLAZIONE.md), passo 13.

## Tecnologie

Python 3.12, Django 5.2, PostgreSQL 17, Gunicorn, WhiteNoise; JavaScript e CSS semplici, senza
framework né bundler. Plugin WordPress in PHP. Test con pytest (`pip install -r
requirements-dev.txt`, poi `pytest`; i test della row-level security girano su PostgreSQL).

## Documentazione

- [`INSTALLAZIONE.md`](INSTALLAZIONE.md) — installazione e gestione, passo per passo.
- [`CHANGELOG.md`](CHANGELOG.md) — cosa cambia in ogni versione.
- [`SECURITY.md`](SECURITY.md) — come segnalare una vulnerabilità.
- [`THIRD-PARTY.md`](THIRD-PARTY.md) — componenti di terze parti e le loro licenze.
- La **Guida** dentro il portale — ogni pagina spiegata, per l'agenzia e per le attività.
- `wordpress/td-booking-suite/` — il plugin WordPress.

## Licenza

TD Booking Suite è software libero rilasciato sotto la [GNU Affero General Public License v3.0 o
successiva](LICENSE). Se fai girare una versione modificata per altre persone attraverso la rete,
devi offrire loro il suo codice sorgente. Il plugin WordPress in `wordpress/td-booking-suite/` è
rilasciato sotto la GNU General Public License v2.0 o successiva, come WordPress. I componenti di
terze parti mantengono le loro licenze (`THIRD-PARTY.md`).

Copyright © Giuseppe Sciarra — [Tastiere Digitali](https://tastieredigitali.it)

---

## Sostieni il progetto

Se TD Booking Suite ti è utile, puoi sostenerne lo sviluppo con una donazione:

**[paypal.me/raxiel87](https://paypal.me/raxiel87)**

Segnalazioni di bug e pull request sono benvenute.
