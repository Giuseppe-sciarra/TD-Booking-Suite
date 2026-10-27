# Security policy

🇮🇹 [Versione italiana più sotto](#italiano)

## Supported versions

Only the latest release receives security fixes. If you run an older version, update first and
check whether the problem is still there.

| Version | Supported |
|---------|-----------|
| 2.8.x   | ✅        |
| < 2.8   | ❌        |

## Reporting a vulnerability

**Please do not open a public issue, discussion or pull request for a security problem.**
Report it privately instead:

👉 **[Report a vulnerability](https://github.com/Giuseppe-sciarra/TD-Booking-Suite/security/advisories/new)**
(GitHub private vulnerability reporting — only the maintainer can see it)

A useful report contains the version (`VERSION`), what an attacker can do and what they need first
(nothing, a client's booking link, a staff account of one business, an agency account…), the steps
to reproduce and, if relevant, your deployment details (reverse proxy, database, edition).

You get an acknowledgement as soon as possible. The problem is fixed in a new release, listed in
the [CHANGELOG](CHANGELOG.md). Please give a reasonable time to ship the fix before disclosing
details publicly.

## Scope

**In scope** — the code in this repository:

- sign-in, sessions, two-step verification, roles and permissions
- the isolation between businesses (row-level security, per-business keys) and between the agency
  and the businesses (support access)
- the public booking page, its API and the client action links (cancel, consent, unsubscribe)
- uploads (logos), mail sending, the WordPress connection and the WordPress plugin

**Out of scope** — the security of your server, Docker, the reverse proxy and its certificates,
the SMTP / Gmail / Microsoft 365 accounts you connect, and anything that needs the server's `.env`.

---

## Italiano

Solo l'ultima versione riceve le correzioni di sicurezza: se usi una versione vecchia, aggiorna e
controlla che il problema ci sia ancora.

**Non aprire una issue, una discussione o una pull request pubblica per un problema di
sicurezza.** Segnalalo in privato con
**[Segnala una vulnerabilità](https://github.com/Giuseppe-sciarra/TD-Booking-Suite/security/advisories/new)**:
la vede solo il manutentore.

Indica la versione (`VERSION`), cosa può fare un attaccante e cosa gli serve prima (niente, il link
di una prenotazione, un account di un'attività, un account dell'agenzia…), i passi per riprodurlo e,
se serve, come è installato (reverse proxy, database, edizione). La correzione esce in una versione
nuova, descritta nel [CHANGELOG](CHANGELOG.md): lascia il tempo di pubblicarla prima di rendere
pubblici i dettagli.

Rientrano il codice di questo repository: accesso, sessioni, verifica in due passaggi, ruoli e
permessi, separazione tra attività e tra agenzia e attività, pagina prenotazioni pubblica e link
inviati ai clienti, caricamento dei loghi, invio email, collegamento e plugin WordPress. Non
rientrano il server, Docker, il reverse proxy, gli account email collegati e tutto ciò che richiede
il file `.env` del server.
