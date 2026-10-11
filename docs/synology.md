# Kleinkontor auf einer Synology-NAS installieren

Diese Anleitung richtet Kleinkontor mit dem **Container Manager** von DSM ein. Du brauchst dafür weder SSH noch einen eigenen Server.

**Voraussetzungen**

- DSM 7.2 oder neuer
- Ein Modell, auf dem der **Container Manager** läuft (die meisten Plus-Modelle mit Intel/AMD-Prozessor sowie viele ARM-Modelle; das Image gibt es für `amd64` und `arm64`)
- Etwa 300 MB freier Speicher

> Das Programm ist eine Arbeitshilfe und ersetzt keine Steuerberatung – siehe Haftungsausschluss in der [README](../README.md).

---

## 1. Container Manager installieren

**Paket-Zentrum** → nach „Container Manager“ suchen → **Installieren**.

## 2. Ordner anlegen

In der **File Station** im gemeinsamen Ordner `docker` (legt der Container Manager an) einen Ordner **`kleinkontor`** erstellen und darin einen Unterordner **`data`**:

```
docker/
└── kleinkontor/
    └── data/        ← Datenbank, Sicherungen, Logo
```

## 3. Benutzer für den Container festlegen

Der Container soll nicht als Administrator laufen, sondern als eigener Benutzer, der nur in `docker/kleinkontor` schreiben darf.

1. **Systemsteuerung → Benutzer und Gruppe → Erstellen**: Name z. B. `kleinkontor`, sicheres Passwort, keine Gruppen außer `users`. Bei den gemeinsamen Ordnern überall *Kein Zugriff* lassen, bei den Anwendungen alles verweigern – der Benutzer meldet sich nie selbst an.
2. Nur den Ordner `kleinkontor` freigeben: In der **File Station** mit Rechtsklick auf `docker/kleinkontor` → **Eigenschaften → Berechtigung → Erstellen** → Benutzer `kleinkontor`, Typ *Zulassen*, **Lesen** und **Schreiben** anhaken, *Anwenden auf: Dieser Ordner, untergeordnete Ordner und Dateien* → OK. So kommt der Container nicht an die Daten anderer Container (z. B. Paperless).
3. **Benutzer-ID und Gruppen-ID herausfinden** – der Container braucht die Zahlen, nicht den Namen:
   - **Systemsteuerung → Aufgabenplaner → Erstellen → Geplante Aufgabe → Benutzerdefiniertes Skript**
   - Allgemein: Name `id ermitteln`, Benutzer **root**, Häkchen bei *Aktiviert* entfernen
   - Aufgabeneinstellungen → Skript:
     ```
     id kleinkontor > /volume1/docker/kleinkontor/id.txt
     ```
   - Speichern, die Aufgabe markieren und **Ausführen**. In der File Station `docker/kleinkontor/id.txt` öffnen, z. B.:
     ```
     uid=1027(kleinkontor) gid=100(users) groups=100(users)
     ```
     Merke dir **uid** (hier `1027`) und **gid** (hier `100`). Die Aufgabe kannst du danach löschen.

## 4. Projekt im Container Manager anlegen

**Container Manager → Projekt → Erstellen**

- Projektname: `kleinkontor`
- Pfad: `/docker/kleinkontor`
- Quelle: **docker-compose.yml erstellen**

Füge eine der beiden Varianten ein und ersetze `1027:100` durch deine uid und gid aus Schritt 3.

### Variante A – nur im Heimnetz, ohne HTTPS (zum Ausprobieren)

```yaml
services:
  kleinkontor:
    image: ghcr.io/chrkraus/kleinkontor:latest
    container_name: kleinkontor
    user: "1027:100"
    ports:
      - "8085:5000"            # DSM belegt selbst 5000/5001 – deshalb 8085
    volumes:
      - ./data:/data
    environment:
      TZ: Europe/Berlin
    restart: unless-stopped
```

Aufruf danach: `http://<IP-der-NAS>:8085`. Passwörter laufen dabei **unverschlüsselt** durchs Heimnetz – für den Dauerbetrieb Variante B nehmen.

### Variante B – mit HTTPS über den Reverse Proxy von DSM (empfohlen)

```yaml
services:
  kleinkontor:
    image: ghcr.io/chrkraus/kleinkontor:latest
    container_name: kleinkontor
    user: "1027:100"
    ports:
      - "127.0.0.1:8085:5000"  # nur für DSM selbst erreichbar, Zugriff von außen nur per HTTPS
    volumes:
      - ./data:/data
    environment:
      TZ: Europe/Berlin
      BUCHHALTUNG_TRUSTED_PROXIES: 172.30.0.1   # DSM-Reverse-Proxy (Gateway des Netzes unten)
    networks:
      - kleinkontor
    restart: unless-stopped

networks:
  kleinkontor:
    ipam:
      config:
        - subnet: 172.30.0.0/24   # falls schon belegt: anderes freies Netz wählen und die Gateway-Adresse oben anpassen
```

Weiter mit **Weiter → Webportal nicht einrichten → Fertig**. Der Container Manager lädt das Image und startet das Projekt; der Status sollte nach kurzer Zeit **Wird ausgeführt** (bzw. *healthy*) zeigen.

## 5. HTTPS einrichten (nur Variante B)

1. **Adresse wählen**, z. B. `kleinkontor.deine-domain.de` oder eine Synology-DDNS-Adresse (`…synology.me`), und dafür sorgen, dass sie im Heimnetz auf die NAS zeigt.
2. **Zertifikat:** Systemsteuerung → **Sicherheit → Zertifikat → Hinzufügen** → Let's Encrypt (oder ein vorhandenes Zertifikat für die Adresse verwenden).
3. **Reverse Proxy:** Systemsteuerung → **Anmeldeportal → Erweitert → Reverse Proxy → Erstellen**
   - Quelle: Protokoll **HTTPS**, Hostname `kleinkontor.deine-domain.de`, Port **443**, HSTS aktivieren
   - Ziel: Protokoll **HTTP**, Hostname **localhost**, Port **8085**
   - Reiter **Benutzerdefinierte Kopfzeile → Erstellen**:
     | Kopfzeilenname | Wert |
     |---|---|
     | `X-Forwarded-For` | `$proxy_add_x_forwarded_for` |
     | `X-Forwarded-Proto` | `$scheme` |
4. Unter **Sicherheit → Zertifikat → Einstellungen** dem neuen Reverse-Proxy-Eintrag das Zertifikat zuweisen.
5. Aufruf: `https://kleinkontor.deine-domain.de`

## 6. Ersteinrichtung in Kleinkontor

1. Beim ersten Aufruf das **Admin-Konto** anlegen.
2. **Einstellungen**: Firmendaten, Steuernummer bzw. USt-IdNr., Bankverbindung, Kleinunternehmer ja/nein.
3. Bei Variante B unter *Anmeldung & Benutzer* die **Adresse der App** eintragen (`https://kleinkontor.deine-domain.de`) – sie wird für Links in E-Mails gebraucht.
4. **Mein Konto → Zwei-Faktor** einrichten.
5. Optional: **E-Mail-Versand** (SMTP deines Mail-Anbieters) und **Paperless-ngx**. Läuft Paperless auf derselben NAS, als URL `http://<IP-der-NAS>:8000` eintragen (nicht `localhost` – das wäre der Kleinkontor-Container selbst).

## 7. Datensicherung

- Kleinkontor sichert sich **alle 24 Stunden** selbst nach `docker/kleinkontor/data/backups` (letzte 30 Tage plus je Monat eine Sicherung).
- Zusätzlich den Ordner **`docker/kleinkontor`** mit **Hyper Backup** auf ein externes Laufwerk oder einen Cloud-Speicher sichern.
- Empfohlen: **Snapshot Replication** für den gemeinsamen Ordner `docker` (schützt auch vor versehentlichem Löschen und Verschlüsselungstrojanern).
- Die Datenbank enthält auch Zugangsdaten (Mail-Passwort, Paperless-Token) – Sicherungen entsprechend schützen.

## 8. Aktualisieren

1. In Kleinkontor **Einstellungen → Datensicherung → Jetzt sichern**.
2. **Container Manager → Projekt → kleinkontor → Aktion → Stoppen**, danach **Aktion → Erstellen** (lädt das neueste Image und startet neu). Alternativ unter **Image** bei „Aktualisierung verfügbar“ aktualisieren.
3. Neue Versionen und Änderungen stehen auf der [Release-Seite](https://github.com/chrkraus/kleinkontor/releases).

## 9. Zugriff von unterwegs

Kleinkontor **nicht** per Portfreigabe im Router direkt ins Internet stellen. Besser:

- **VPN**: Paket *VPN Server* oder *Tailscale* auf der NAS bzw. WireGuard im Router (z. B. FRITZ!Box) – dann von unterwegs wie zu Hause.
- **Tunnel mit Zugangskontrolle** (z. B. Cloudflare Tunnel + Access), wenn kein VPN möglich ist.

## Fehlerbehebung

| Problem | Ursache und Lösung |
|---|---|
| Container stoppt sofort, im Protokoll *Permission denied* / *unable to open database file* | Benutzer aus Schritt 3 darf nicht in `docker/kleinkontor/data` schreiben → Berechtigung prüfen, uid/gid in `user:` kontrollieren |
| Port 8085 ist belegt | Anderen freien Port verwenden (an beiden Stellen: Projekt und Reverse Proxy) |
| Uhrzeiten im Protokoll sind falsch | `TZ: Europe/Berlin` in der Projektdatei prüfen |
| „Netzwerk-Subnetz überschneidet sich“ beim Erstellen | In Variante B ein anderes freies Subnetz wählen, z. B. `172.31.0.0/24` und Gateway `172.31.0.1` |
| Login klappt über HTTPS nicht dauerhaft | Benutzerdefinierte Kopfzeilen aus Schritt 5 prüfen und `BUCHHALTUNG_TRUSTED_PROXIES` muss zum Gateway des Netzes passen |
| Image lässt sich nicht laden | Internetverbindung der NAS prüfen; das Image heißt `ghcr.io/chrkraus/kleinkontor:latest` |
