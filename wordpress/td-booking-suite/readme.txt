=== TD Booking Suite — Prenotazioni ===
Requires at least: 6.2
Requires PHP: 8.0
Stable tag: 2.22.3
License: GPL-2.0-or-later

Mostra nel sito il modulo di prenotazione di TD Booking Suite, edizione Cloud o Private, in HTTPS.

Collegamento in un passo: nel portale, Impostazioni e sito > Sito WordPress, genera il codice di collegamento.
In WordPress, Impostazioni > TD Booking Suite > Collegati al portale: indirizzo del portale e codice.
Il plugin autorizza il sito, legge sedi e servizi e crea la pagina Prenota con [td_booking_suite].
Shortcode per una sola sede o un solo servizio: [td_booking_suite location="..."] e [td_booking_suite service="..."], elencati nelle impostazioni.
Tema facoltativo: [td_booking_suite theme="dark"] o theme="light".
Impostazioni manuali sempre disponibili: indirizzo del portale e indirizzo breve, con il sito autorizzato nel portale.
Non memorizza dati personali nel database WordPress. Link alternativo per aprire la pagina fuori dal riquadro.

Modalità PC, per il programma installato su un PC spesso spento e non raggiungibile da Internet (edizione Windows, Un PC solo).
Nel programma, Impostazioni e sito > Sito WordPress · modalità PC: indirizzo del sito e "Genera la chiave". In WordPress,
Impostazioni > TD Booking Suite > Modalità PC: spunta la modalità e incolla la chiave. Il PC chiama il sito ogni 2 minuti
quando è acceso (nessuna porta da aprire), manda servizi, operatori e orari liberi e prende le richieste arrivate.
[td_booking_suite] mostra allora il modulo dai dati del PC, con "Disponibilità aggiornata alle ..."; ogni richiesta toglie subito
l'orario dal sito, il cliente riceve "Richiesta ricevuta" e la conferma vera arriva dal programma. Le richieste restano nel
database cifrate con una chiave ricavata dalla chiave condivisa e si cancellano appena il PC le ha prese. Se il PC non si fa
sentire da più dei giorni indicati, il modulo dice di contattare l'attività. Richiede i permalink attivi (/wp-json/).

Agenda riservata. In Modalità PC imposta la password dell'agenda e metti [tdbs_agenda] in una pagina: con la
password si apre l'agenda del giorno o della settimana, una colonna per operatore, con il dettaglio di ogni prenotazione e le
richieste del sito non ancora prese dal PC. Il PC la manda cifrata con la chiave condivisa e il sito la decifra per mostrarla:
chi amministra il sito può tecnicamente leggerla. Blocco dopo 5 password sbagliate, sessione HttpOnly di 8 ore, Esci, pagina
fuori dalla cache dei plugin e non indicizzata; la cache del server (per esempio la FastCGI cache di aaPanel) va
esclusa a mano per quella pagina.

Agenda come un'agenda (2.22.1). [tdbs_agenda] è una griglia come quella del programma: le ore a sinistra, una colonna per
operatore con la sua foto o le iniziali in cima (nella settimana una colonna per giorno), i blocchi all'ora giusta col colore
dell'operatore, quelli sovrapposti affiancati, la riga rossa dell'ora, un tocco apre il dettaglio (telefono cliccabile). Gli
operatori si scelgono da una tendina con le foto: uno solo o tutti. Le foto si scelgono in Impostazioni > TD Booking Suite >
Foto degli operatori (libreria media); senza, valgono quelle messe nel programma in Operatori, che arrivano con la
sincronizzazione. Da telefono tre operatori stanno nello schermo, di più scorrono di lato.

Agenda come app sul telefono (2.22.1). L'agenda ha anche una pagina sua senza il tema, <sito>/?tdbs_app=1 (il tasto "Apri come
app" nella pagina), con il nome, il logo e l'icona del sito e il colore dell'attività, un manifest e un service worker: Chrome
propone "Installa l'app", su iPhone si usa Condividi > Aggiungi alla schermata Home, e l'agenda si apre a tutto schermo come
un'app, con la stessa password. Senza connessione mostra l'ultima agenda vista; con Esci la copia sul telefono viene tolta.
Serve l'icona del sito (Aspetto > Personalizza > Identità del sito): senza, Chrome non propone l'installazione (vale anche il
logo del tema se è almeno 144 px). Nient'altro del sito passa dal service worker.

Nuovo appuntamento nell'agenda riservata (2.19.0). Con la password dell'agenda si aggiungono anche appuntamenti: premi
"Nuovo appuntamento", scegli servizio, prestazione (se ce n'è più di una), operatore o "Chiunque", giorno e uno degli orari
liberi, oppure scrivi "Altro orario" (lo controlla il PC); poi nome, cognome e telefono del cliente, email facoltativa, note,
e premi "Salva l'appuntamento". L'orario sparisce subito dal modulo pubblico e l'appuntamento compare in agenda come
"Da sincronizzare" finché il PC non lo prende: se l'orario è ancora libero diventa una prenotazione confermata con origine
"Agenda del sito" e il cliente con l'email riceve la conferma dal programma; se non lo è più va tra le Richieste da
confermare. Dal sito non si spostano né si annullano appuntamenti: serve il programma. I dati del cliente restano cifrati
come le richieste dei visitatori, fino al PC. Nell'agenda gli operatori e le prenotazioni scorrono insieme anche sul telefono.

Il sito come archivio centrale (2.18.0). In Modalità PC spunta "Il sito come archivio centrale" e accendilo anche nel programma:
a ogni sincronizzazione il PC manda le modifiche di prenotazioni, clienti con i dati fiscali, schede di lavoro, consensi e
riepilogo degli scontrini, ogni record cifrato con la chiave condivisa (AES-256-GCM). Un cliente cancellato nel programma
sparisce anche qui. Un PC nuovo riprende tutto con "Ripristina dal sito". [tdbs_agenda] legge allora dall'archivio, anche i
giorni passati. Spento qui, l'archivio nel sito viene cancellato.

Il sito e il PC si allineano sulla modifica più recente (2.20.0). Di ogni prenotazione, cliente e scheda il sito tiene la
versione modificata per ultima: il PC manda con ogni record il momento esatto della modifica, contato in microsecondi dal
1970 in tempo universale, lo stesso numero qualunque sia il fuso del PC o del server del sito. Una copia più vecchia che
arriva dopo viene confermata al PC ma non sostituisce quella più nuova. Se il sito è giù, le modifiche fatte sul PC restano in
coda e partono appena torna, con gli orari liberi aggiornati. Nel programma "Sincronizza con il sito" confronta tutto e tiene
la versione più recente da tutte e due le parti; lo fa da solo anche quando l'archivio si accende su un PC già usato da solo.
L'archivio di prima resta: i suoi record contano come i più vecchi, finché il PC non li manda di nuovo.

Aggiornamenti dalla vetrina pubblica (2.22.2). Lo zip del plugin (td-booking-suite-<versione>.zip) sta nei rilasci di
github.com/Giuseppe-sciarra/TD-Booking-Suite-per-Windows, accanto all'MSI: ce lo mette il rilascio di Windows. Il plugin
controlla ogni 12 ore l'ultimo rilascio (senza chiavi: sono pubblici) e, se c'è una versione più nuova con lo zip, WordPress la
mostra in Plugin e la installa come ogni altro aggiornamento; "Controlla di nuovo" in Aggiornamenti forza il controllo. Senza
licenza il plugin resta inutile: lo zip pubblico non dà niente a chi non ha il programma.

Prima dell'arrivo, Dopo la visita, Richiamo e Si è liberato un posto (2.22.1). Con il sito che gestisce le email, anche queste
quattro le decide e le manda il sito da solo, ogni 5 minuti con gli altri promemoria, dai dati che il PC sincronizza: le
prenotazioni e le schede dell'archivio, le istruzioni e i giorni di richiamo dei servizi (arrivano con gli orari liberi) e la
lista d'attesa (arriva con l'archivio). Le regole sono quelle del programma: Prima dell'arrivo alle prenotazioni confermate con
le istruzioni scritte nel servizio, ore prima; Dopo la visita ore dopo una visita segnata Fatta, solo a chi ha dato il consenso
alle comunicazioni e al massimo una volta a settimana; Richiamo, una volta al giorno, all'ultima visita di un servizio con il
richiamo quando arriva il suo giorno, con il consenso e senza un altro appuntamento già preso; Si è liberato un posto a chi è in
lista quando per il suo servizio e i suoi giorni compare un orario libero nuovo (una volta per orario, "lo prende chi prenota per
primo"), con la pagina Prenota. Interruttori, ore e link della recensione stanno in Email ai clienti: Prima dell'arrivo spenta di
serie, Dopo la visita accesa (parte solo con il link), Richiamo spento, Si è liberato un posto acceso. Il PC spento non ferma
niente, se non l'arrivo di dati nuovi.

Più servizi nella stessa visita (2.22.0). Nel modulo, scegliendo ancora dalla lista Servizio (2.22.2: una lista sola, niente "Aggiungi un altro servizio"), il cliente mette altri servizi della
stessa sede, uno dopo l'altro: il sito mostra solo gli orari in cui ci stanno tutti, con lo stesso operatore o divisi come
deciso nel programma (Sedi), li tiene occupati finché il PC non li prende e scrive un'email sola con tutti i servizi. Il PC
li riceve come una visita, come al banco. Serve il programma 2.22.0, che manda anche gli orari liberi senza variante.

Il sito gestisce prenotazioni ed email (2.21.0). Si accende dal programma, in Sito WordPress · modalità PC, con "Sito come
archivio centrale" e "Il sito gestisce prenotazioni ed email". Da allora chi prenota dal sito è confermato subito (o riceve
"Richiesta ricevuta" per i servizi che l'attività conferma), e tutte le email partono da qui con la posta del sito (wp_mail, con
il plugin SMTP del sito): conferma, richiesta ricevuta, modifica, annullamento, promemoria e secondo promemoria con i tasti
"Confermo" e "Annulla", e gli avvisi al titolare, anche con il PC spento per giorni. Valgono anche per gli appuntamenti presi al
banco, che arrivano con l'archivio. Il programma non manda più le email delle prenotazioni: riceve le prenotazioni e quello che i clienti fanno dai link
(annullata, confermata, "Non vengo" oltre il termine) alla sincronizzazione dopo, anche il giorno dopo. I testi si scrivono in
Impostazioni > TD Booking Suite > Email ai clienti, con {nome}, {servizio}, {quando}, {data}, {ora}, {sede}, {operatore},
{attivita}, {link}; c'è un'email di prova. Le email partono solo per quello che cambia dopo l'accensione, mai per lo storico. I
promemoria partono da WP-Cron: per l'ora giusta anche senza visite al sito, un cron di sistema ogni 5 minuti su wp-cron.php.
I nomi scritti tutti minuscoli o tutti maiuscoli diventano "Mario Rossi"; il programma lega la prenotazione alla scheda con lo
stesso nome e cognome. I link delle email aprono una pagina del sito, firmata con la chiave condivisa, valida fino a 30 giorni
dopo l'appuntamento.

Per il firewall del sito. Il PC chiama solo /wp-json/tdbs/v1/sync e /wp-json/tdbs/v1/archive/push e /archive/pull, sempre
in POST con JSON, intestazioni X-TDBS-Time e X-TDBS-Signature, corpo fino a 512 KB per l'archivio (/sync di solito
meno, fino a 5 MB con tanti servizi e 90 giorni: se il proxy risponde 413, alza il limite). Nel WAF: whitelist, tipo "path", due
voci con nota "TD Booking Suite – PC": /wp-json/tdbs/v1/sync e /wp-json/tdbs/v1/archive/ (per un solo sito:
sitocliente.it|/wp-json/tdbs/v1/sync, host esatto, il www vuole una voce sua). Non mettere in whitelist tutto
/wp-json/tdbs/v1/: /slots (GET), /book (POST) e /agenda/book (POST, il Nuovo appuntamento dell'agenda) li usa il browser
di chi apre il sito e restano sotto il firewall.
