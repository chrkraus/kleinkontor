# Änderungen

## 1.0.3 – 11.10.2026
- **Neue Adresse:** Das Projekt liegt jetzt unter https://github.com/ck-networks/kleinkontor, das Container-Image heißt `ghcr.io/ck-networks/kleinkontor`. Bestehende Installationen bitte in der `docker-compose.yml` bzw. der Projektdatei im Container Manager auf den neuen Image-Namen umstellen.
- README: Abschnitt „Unterstützen“

## 1.0.2 – 11.10.2026
- Oberfläche heißt jetzt „Kleinkontor“ (Seitenleiste, Anmeldung, App-Name auf dem Handy)
- Layoutkorrekturen: Skonto-Felder, Einheitenfeld, Rechnungsnummern in der Übersicht
- README mit Screenshots (Demodaten)

## 1.0.1 – 11.10.2026
- Docker: Der Container läuft als Besitzer des Datenordners; `PUID`/`PGID` optional
- Verständliche Meldung, wenn der Datenordner nicht beschreibbar ist
- Synology-Anleitung als Schnellstart

## 1.0.0 – 11.10.2026
- Erste Veröffentlichung: Angebote, Rechnungen mit E-Rechnung (ZUGFeRD EN 16931), Einnahmen/Ausgaben, EÜR mit Ausfüllhilfe Anlage EÜR, Paperless-ngx-Anbindung, Benutzer mit Einzelrechten und Zwei-Faktor-Anmeldung, Datensicherung
- Container-Image `ghcr.io/ck-networks/kleinkontor` (amd64, arm64) und Synology-Anleitung
