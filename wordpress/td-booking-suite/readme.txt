=== TD Booking Suite — Prenotazioni ===
Requires at least: 6.2
Requires PHP: 8.0
Stable tag: 2.17.1
License: GPL-2.0-or-later

Mostra nel sito il modulo di prenotazione di TD Booking Suite, edizione Cloud o Private, in HTTPS.

Collegamento in un passo: nel portale, Configura l'attività > Sito WordPress, genera il codice di collegamento.
In WordPress, Impostazioni > TD Booking Suite > Collegati al portale: indirizzo del portale e codice.
Il plugin autorizza il sito, legge sedi e servizi e crea la pagina Prenota con [td_booking_suite].
Shortcode per una sola sede o un solo servizio: [td_booking_suite location="..."] e [td_booking_suite service="..."], elencati nelle impostazioni.
Tema facoltativo: [td_booking_suite theme="dark"] o theme="light".
Impostazioni manuali sempre disponibili: indirizzo del portale e indirizzo breve, con il sito autorizzato nel portale.
Non memorizza dati personali nel database WordPress. Link alternativo per aprire la pagina fuori dal riquadro.

Modalità PC (2.17.0), per il programma installato su un PC spesso spento e non raggiungibile da Internet (edizione Windows, Un PC solo).
Nel programma, Configura l'attività > Sito WordPress · modalità PC: indirizzo del sito e "Genera la chiave". In WordPress,
Impostazioni > TD Booking Suite > Modalità PC: spunta la modalità e incolla la chiave. Il PC chiama il sito ogni 2 minuti
quando è acceso (nessuna porta da aprire), manda servizi, operatori e orari liberi e prende le richieste arrivate.
[td_booking_suite] mostra allora il modulo dai dati del PC, con "Disponibilità aggiornata alle ..."; ogni richiesta toglie subito
l'orario dal sito, il cliente riceve "Richiesta ricevuta" e la conferma vera arriva dal programma. Le richieste restano nel
database cifrate con una chiave ricavata dalla chiave condivisa e si cancellano appena il PC le ha prese. Se il PC non si fa
sentire da più dei giorni indicati, il modulo dice di contattare l'attività. Richiede i permalink attivi (/wp-json/).

Agenda riservata (2.17.1). Nel programma, nella stessa card, l'agenzia crea gli accessi: nome, password e cosa si vede
(tutta l'attività o alcuni operatori). In una pagina del sito metti [tdbs_agenda]: con la password si apre l'agenda del giorno
o della settimana, una colonna per operatore, con il dettaglio di ogni prenotazione e le richieste del sito non ancora prese
dal PC. Il sito riceve e dà solo dati cifrati (PBKDF2-HMAC-SHA256 + AES-256-GCM, chiave ricavata dalla password): si decifrano
solo nel browser, la password non viene mai inviata. Sola lettura.
