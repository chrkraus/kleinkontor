# Kleinkontor – Hinweise für die Entwicklung

Web-App für Selbständige und Kleinunternehmer in Deutschland (Angebote, Rechnungen, E-Rechnung, Einnahmen/Ausgaben, EÜR). Oberfläche, Texte und Kommentare sind auf Deutsch.

Installationsspezifische Details (Server, Domains, NAS, Zugänge) gehören in eine lokale, nicht versionierte Datei (`*.local.md`).

## Arbeitsweise
- Vor jedem Commit: `.venv/bin/python -m pytest -q`.
- Nicht im git: `data/`, `instance.env`, `.venv/`, `bin/`, `Caddyfile`, `*.local.md`.
- Neustart einer Instanz mit `./restart.sh` (über `data/app.pid`), nicht per `pkill`.

## Testmodus (`environment.IS_TEST`)
Mails gehen an `BUCHHALTUNG_TEST_MAIL` (`mailer.send`), Paperless-Uploads sind gesperrt (`PaperlessClient.upload`), kein Hintergrundplaner, PDF mit Wasserzeichen. Neue Funktionen mit Außenwirkung (Mail, Upload, externe APIs) **müssen** diese Sperre ebenfalls beachten.

## Architektur
- `app.py`: alle Routen (Flask, `create_app`), Hintergrundjobs `run_*_job`, `migrate_columns()` (ergänzt neue Spalten per `ALTER TABLE ADD COLUMN`; Umbenennen/Löschen manuell).
- `models.py`: SQLAlchemy-Modelle. Geldbeträge immer als Integer in Cent (`parse_money`, Filter `|money`).
- `auth.py`: Rechte je Endpunkt (`ENDPOINTS`, GET/POST getrennt) und Protokoll (`audit`). **Neue Routen immer in `auth.ENDPOINTS` eintragen**, sonst nur für Admins erreichbar. Templates prüfen mit `can('recht')`.
- `security.py`: `TrustLocalProxy` übernimmt Client-IP/Protokoll nur von 127.0.0.1 (lokaler Proxy/Tunnel); Secure-Cookie nur bei HTTPS; POST mit fremdem Origin/Referer → 403; CSP nur `'self'` (keine CDNs). waitress läuft mit `--no-clear-untrusted-proxy-headers`. Der Flask-Testclient umgeht waitress – Proxy-Verhalten gegen den laufenden Server prüfen.
- `twofa.py`: TOTP (±1 Zeitfenster, `totp_last_step` gegen Wiederverwendung), Wiederherstellungscodes nur als sha256.
- Passwort vergessen / Einladungen: signierter Link (itsdangerous, `pw_fingerprint`), Basis `Settings.app_url`, nie der Host-Header. Einladungen `inv=1`, 3 Tage gültig.
- `einvoice.py`: ZUGFeRD/Factur-X (EN 16931) mit drafthorse, PDF/A-3. Kleinunternehmer → Steuerkategorie E mit §19-Grund; Storno → Gutschrift 381 mit positiven Beträgen; ohne USt-IdNr. Steuernummer als BT-29 (BR-CO-26); Skonto im PaymentTerms-Text `#SKONTO#TAGE=n#PROZENT=x.xx#\n`.
- `euer_form.py`: Zeilen/Kennzahlen der Anlage EÜR je Vordruckjahr (`FIELDS`), Kategorie-Zuordnung (`EXPENSE_MAP`). Zeilen ändern sich jährlich: BMF-Schreiben „Anlage EÜR <Jahr>“ laden und jede Zeile gegen das PDF prüfen, nie raten. Neue Ausgabekategorien auch in `EXPENSE_MAP`.
- `receipts.py`: Belegeingang aus Paperless (Tag in den Einstellungen), Betragserkennung über Schlüsselwörter, Kategorie aus früheren Buchungen bzw. Regeln (im Fließtext nur am Wortanfang).
- `mailer.py`: SMTP, Vorlagen mit Platzhaltern (`format_map` + `_Keep`), BCC nur im Envelope, Versandprotokoll `EmailLog`.
- `backup.py`: SQLite-Online-Backup als ZIP mit `integrity_check` und Rotation; `nas_sync.sh` verschlüsselt (openssl AES-256, PBKDF2) und spiegelt per rsync nur nach `NAS_TARGET` (aus `instance.env`).
- `scheduler.py`: Hintergrundjobs nur mit `BUCHHALTUNG_SCHEDULER=1` (setzt `run.sh`), nie im Testmodus.
- Mehrzeilige Formularfelder: `\r\n` beim Speichern zu `\n` normalisieren.

## Fachliche Regeln (nicht aufweichen)
- GoBD: Festgeschriebene Dokumente (`status != "draft"`) nie ändern oder löschen; Korrekturen nur per Stornorechnung.
- Nummern erst beim Festschreiben, lückenlos pro Jahr (`NumberSequence`).
- Rechnungen brauchen ein Leistungsdatum (§ 14 UStG).
- Steuermodus (§ 19 UStG / Regelbesteuerung) wird pro Dokument in `small_business` gespeichert.
- EÜR nach Zuflussprinzip (Zahlungsdatum).
- Belege liegen in Paperless; lokal nur `paperless_document_id`.

## Testen
- `pytest` (Ordner `tests/`) mit temporärem Datenordner – nie gegen die echte `data/` testen.
- E-Rechnungen nach Änderungen an `einvoice.py` zusätzlich gegen die offiziellen Regeln prüfen (Schematron-XSLT der EN 16931, github.com/ConnectingEurope/eInvoicing-EN16931, z. B. mit saxonche) oder mit Mustang-CLI, das auch PDF/A prüft.
- Mailtests mit aiosmtpd als Sink; Fehler 535 per Mock simulieren (aiosmtpd hängt bei falschem Passwort).
