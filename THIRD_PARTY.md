# Komponenten Dritter

Kleinkontor steht unter der AGPL-3.0 (siehe `LICENSE`). Es nutzt folgende Komponenten mit eigenen, AGPL-kompatiblen Lizenzen:

| Komponente | Lizenz | Verwendung |
|---|---|---|
| Flask, Werkzeug, Jinja2, itsdangerous | BSD-3-Clause | Web-Framework |
| Flask-SQLAlchemy | BSD-3-Clause | Datenbankanbindung |
| SQLAlchemy | MIT | Datenbankzugriff |
| fpdf2 | LGPL-3.0 | PDF-Erzeugung (unverändert als Bibliothek genutzt) |
| drafthorse | Apache-2.0 | ZUGFeRD/Factur-X-XML und PDF/A-3 |
| pypdf | BSD-3-Clause | PDF-Verarbeitung |
| lxml | BSD-3-Clause | XML |
| Pillow | MIT-CMU (HPND) | Bilder, Farbprofil |
| pyotp | MIT | Zwei-Faktor-Codes |
| qrcode | BSD | QR-Code für die Zwei-Faktor-Einrichtung |
| requests | Apache-2.0 | Paperless-ngx-API |
| waitress | ZPL 2.1 | Webserver |
| DejaVu Sans (Ordner `fonts/`) | Bitstream-Vera-/DejaVu-Lizenz (siehe `fonts/LICENSE`) | Schrift in PDFs |

Optional, nicht Teil dieses Repositorys: Paperless-ngx, Caddy, cloudflared, acme.sh.
