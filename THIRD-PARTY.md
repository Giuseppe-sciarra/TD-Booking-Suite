# Third-party components

TD Booking Suite is released under AGPL-3.0-or-later. The WordPress plugin in
`wordpress/td-booking-suite/` is released under GPL-2.0-or-later, like WordPress itself. They use
the following third-party components, each under its own licence.

## Portal (Python, installed by pip from `requirements.lock`)

| Component | Licence |
|-----------|---------|
| [Django](https://www.djangoproject.com) / [asgiref](https://github.com/django/asgiref) / [sqlparse](https://github.com/andialbrecht/sqlparse) | BSD-3-Clause |
| [dj-database-url](https://github.com/jazzband/dj-database-url) | BSD-3-Clause |
| [Gunicorn](https://gunicorn.org) | MIT |
| [WhiteNoise](https://whitenoise.readthedocs.io) | MIT |
| [Psycopg 3](https://www.psycopg.org) (PostgreSQL driver) | LGPL-3.0 |
| [cryptography](https://cryptography.io) | Apache-2.0 OR BSD-3-Clause |
| [cffi](https://cffi.readthedocs.io) / [pycparser](https://github.com/eliben/pycparser) | MIT-0 / BSD-3-Clause |
| [argon2-cffi](https://argon2-cffi.readthedocs.io) (password hashing) | MIT |
| [py_webauthn](https://github.com/duo-labs/py_webauthn) (passkeys) / [cbor2](https://github.com/agronholm/cbor2) / [pyOpenSSL](https://www.pyopenssl.org) / [pyasn1](https://github.com/pyasn1/pyasn1) | BSD-3-Clause / MIT / Apache-2.0 / BSD-2-Clause |
| [PyOTP](https://github.com/pyauth/pyotp) (authenticator apps) / [qrcode](https://github.com/lincolnloop/python-qrcode) | MIT / BSD-3-Clause |
| [Pillow](https://python-pillow.org) (logos) | MIT-CMU |
| [tzdata](https://github.com/python/tzdata) / [typing_extensions](https://github.com/python/typing_extensions) / [colorama](https://github.com/tartley/colorama) | Apache-2.0 / PSF-2.0 / BSD-3-Clause |

## Bundled assets

| Asset | Licence |
|-------|---------|
| [Figtree](https://github.com/erikdkennedy/figtree) and [Poppins](https://github.com/itfoundry/Poppins) fonts (`static/fonts`) | SIL Open Font License 1.1 (`static/fonts/OFL-*.txt`) |
| Interface, scripts, stylesheets, icons and favicon | part of TD Booking Suite, AGPL-3.0 |

The interface is served by the application itself: the portal and the booking page make no
requests to external CDNs.

## Services in `compose.yaml`

These run as separate containers from their official images.

| Service | Role | Licence |
|---------|------|---------|
| [PostgreSQL](https://www.postgresql.org) 17 (`postgres:17`) | database | PostgreSQL License |
| [Python](https://www.python.org) 3.12 (`python:3.12-slim`) | base image of the portal and the mail worker | PSF License |

## External services, optional

Used only when the agency or a business connects them; TD Booking Suite talks to their APIs and
does not redistribute any of their code.

| Service | Role |
|---------|------|
| SMTP server of the agency or of the business | confirmation, invitation and campaign emails |
| Google (Gmail API, OAuth) / Microsoft 365 (Graph) | sending emails from a Google or Microsoft mailbox |
| Telegram Bot API | notifications to the staff |
