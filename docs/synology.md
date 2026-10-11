# Kleinkontor auf einer Synology-NAS installieren

Diese Anleitung richtet Kleinkontor mit dem **Container Manager** von DSM ein – ohne SSH und ohne eigenen Server.

**Voraussetzungen**

- DSM 7.2 oder neuer
- Ein Modell, auf dem der **Container Manager** läuft (die meisten Plus-Modelle sowie viele ARM-Modelle; das Image gibt es für `amd64` und `arm64`)
- Etwa 300 MB freier Speicher

> Das Programm ist eine Arbeitshilfe und ersetzt keine Steuerberatung – siehe Haftungsausschluss in der [README](../README.md).

---

## Schnellstart (etwa 5 Minuten)

### 1. Container Manager installieren

**Paket-Zentrum** → nach „Container Manager“ suchen → **Installieren**.

### 2. Ordner anlegen

In der **File Station** im gemeinsamen Ordner `docker` einen Ordner **`kleinkontor`** erstellen und darin einen Unterordner **`data`**:

```
docker/
└── kleinkontor/
    └── data/        ← Datenbank, Sicherungen, Logo
```

Den Ordner `data` **selbst in der File Station anlegen**. Kleinkontor arbeitet später mit den Rechten des Benutzers, dem dieser Ordner gehört (also deinem Konto), kommt aber nur an diesen einen Ordner heran.

### 3. Projekt anlegen

**Container Manager → Projekt → Erstellen**

- Projektname: `kleinkontor`
- Pfad: `/docker/kleinkontor`
- Quelle: **docker-compose.yml erstellen** und Folgendes unverändert einfügen:

```yaml
services:
  kleinkontor:
    image: ghcr.io/ck-networks/kleinkontor:latest
    container_name: kleinkontor
    ports:
      - "8085:5000"            # DSM belegt selbst 5000/5001 – deshalb 8085
    volumes:
      - ./data:/data
    environment:
      TZ: Europe/Berlin
    restart: unless-stopped
```

**Weiter → Webportal nicht einrichten → Fertig.** Der Container Manager lädt das Image und startet das Projekt.

### 4. Öffnen und einrichten

`http://<IP-der-NAS>:8085` im Browser öffnen, dann:

1. **Admin-Konto** anlegen.
2. **Einstellungen**: Firmendaten, Steuernummer bzw. USt-IdNr., Bankverbindung, Kleinunternehmer ja/nein.
3. **Mein Konto → Zwei-Faktor** einrichten.
4. Optional: **E-Mail-Versand** (SMTP deines Mail-Anbieters) und **Paperless-ngx**. Läuft Paperless auf derselben NAS, als URL `http://<IP-der-NAS>:8000` eintragen (nicht `localhost` – das wäre der Kleinkontor-Container selbst).

Für den Alltag im Heimnetz reicht das. Ohne HTTPS laufen Passwörter allerdings **unverschlüsselt** durchs Heimnetz – für den Dauerbetrieb den Abschnitt „HTTPS“ unten einrichten.

---

## Datensicherung

- Kleinkontor sichert sich **alle 24 Stunden** selbst nach `docker/kleinkontor/data/backups` (letzte 30 Tage plus je Monat eine Sicherung).
- Zusätzlich den Ordner **`docker/kleinkontor`** mit **Hyper Backup** auf ein externes Laufwerk oder einen Cloud-Speicher sichern.
- Empfohlen: **Snapshot Replication** für den gemeinsamen Ordner `docker` (schützt auch vor versehentlichem Löschen und Verschlüsselungstrojanern).
- Die Datenbank enthält auch Zugangsdaten (Mail-Passwort, Paperless-Token) – Sicherungen entsprechend schützen.

## Aktualisieren

1. In Kleinkontor **Einstellungen → Datensicherung → Jetzt sichern**.
2. **Container Manager → Projekt → kleinkontor → Aktion → Stoppen**, danach **Aktion → Erstellen** (lädt das neueste Image und startet neu).
3. Neue Versionen und Änderungen stehen auf der [Release-Seite](https://github.com/ck-networks/kleinkontor/releases).

Auch nach jeder Änderung an der Projektdatei gilt: **Stoppen** und **Erstellen** – erst dann wird sie übernommen.

---

## Für den Dauerbetrieb: HTTPS über den Reverse Proxy von DSM

Damit ist Kleinkontor im Heimnetz nur noch verschlüsselt erreichbar.

1. **Projektdatei ersetzen** (Projekt stoppen, YAML bearbeiten, Erstellen):

   ```yaml
   services:
     kleinkontor:
       image: ghcr.io/ck-networks/kleinkontor:latest
       container_name: kleinkontor
       ports:
         - "127.0.0.1:8085:5000"  # nur noch für DSM selbst erreichbar
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
           - subnet: 172.30.0.0/24   # falls belegt: anderes freies Netz und Gateway oben anpassen
   ```

   `BUCHHALTUNG_TRUSTED_PROXIES` nur zusammen mit `127.0.0.1:8085` verwenden – sonst könnte jedes Gerät im Heimnetz eine falsche Absender-Adresse vorgeben.

2. **Adresse wählen**, z. B. `kleinkontor.deine-domain.de` oder eine Synology-DDNS-Adresse (`…synology.me`), und dafür sorgen, dass sie im Heimnetz auf die NAS zeigt.
3. **Zertifikat:** Systemsteuerung → **Sicherheit → Zertifikat → Hinzufügen** → Let's Encrypt (oder ein vorhandenes Zertifikat verwenden).
4. **Reverse Proxy:** Systemsteuerung → **Anmeldeportal → Erweitert → Reverse Proxy → Erstellen**
   - Quelle: Protokoll **HTTPS**, Hostname `kleinkontor.deine-domain.de`, Port **443**, HSTS aktivieren
   - Ziel: Protokoll **HTTP**, Hostname **localhost**, Port **8085**
   - Reiter **Benutzerdefinierte Kopfzeile → Erstellen**:
     | Kopfzeilenname | Wert |
     |---|---|
     | `X-Forwarded-For` | `$proxy_add_x_forwarded_for` |
     | `X-Forwarded-Proto` | `$scheme` |
5. Unter **Sicherheit → Zertifikat → Einstellungen** dem Reverse-Proxy-Eintrag das Zertifikat zuweisen.
6. In Kleinkontor unter **Einstellungen → Anmeldung & Benutzer** die **Adresse der App** eintragen (`https://kleinkontor.deine-domain.de`) – sie wird für Links in E-Mails gebraucht.

## Zugriff von unterwegs

Kleinkontor **nicht** per Portfreigabe im Router direkt ins Internet stellen. Besser:

- **VPN**: Paket *VPN Server* oder *Tailscale* auf der NAS bzw. WireGuard im Router (z. B. FRITZ!Box) – dann von unterwegs wie zu Hause.
- **Tunnel mit Zugangskontrolle** (z. B. Cloudflare Tunnel + Access), wenn kein VPN möglich ist.

## Optional: eigener Benutzer statt deines Kontos

Standardmäßig läuft Kleinkontor mit der Kennung des Benutzers, dem `data` gehört. Wer es strenger möchte, legt einen eigenen Benutzer an:

1. **Systemsteuerung → Benutzer und Gruppe → Erstellen**: Name z. B. `kleinkontor`, keine Gruppen außer `users`, bei gemeinsamen Ordnern **keine Häkchen** (auch nicht *Kein Zugriff* – das wäre ein ausdrückliches Verbot), Anwendungen alle verweigern.
2. In der **File Station** Rechtsklick auf `docker/kleinkontor` → **Eigenschaften → Berechtigung → Erstellen** → Benutzer `kleinkontor`, *Zulassen*, **Lesen** und **Schreiben**, *Anwenden auf: Dieser Ordner, untergeordnete Ordner und Dateien*.
3. uid und gid ermitteln: **Aufgabenplaner → Erstellen → Geplante Aufgabe → Benutzerdefiniertes Skript**, Benutzer **root**, *Aktiviert* aus, Skript `id kleinkontor > /volume1/docker/kleinkontor/id.txt` → speichern → **Ausführen**. In `id.txt` steht z. B. `uid=1033(kleinkontor) gid=100(users)`.
4. In der Projektdatei unter `environment:` ergänzen (mit **deinen** Zahlen) und neu erstellen:
   ```yaml
         PUID: "1033"
         PGID: "100"
   ```

## Fehlerbehebung

Im **Container Manager → Container → kleinkontor → Protokoll** steht beim Start, als welcher Benutzer Kleinkontor läuft (`Kleinkontor läuft als uid=… gid=…`).

| Problem | Ursache und Lösung |
|---|---|
| Seite nicht erreichbar, *Verbindung abgelehnt* | Container läuft nicht (Status prüfen, Protokoll ansehen) – oder die HTTPS-Variante ist aktiv: dann nur über die HTTPS-Adresse erreichbar |
| Im Protokoll *Datenordner /data ist nicht beschreibbar* | Den Ordner `data` selbst in der File Station anlegen (nicht vom Container anlegen lassen); bei PUID/PGID die Zahlen und die Ordnerberechtigung prüfen |
| Port 8085 ist belegt | Anderen freien Port verwenden (bei HTTPS auch im Reverse Proxy) |
| Uhrzeiten sind falsch | `TZ: Europe/Berlin` in der Projektdatei prüfen |
| „Netzwerk-Subnetz überschneidet sich“ | Anderes freies Subnetz wählen, z. B. `172.31.0.0/24` mit Gateway `172.31.0.1` |
| Login klappt über HTTPS nicht dauerhaft | Benutzerdefinierte Kopfzeilen im Reverse Proxy prüfen; `BUCHHALTUNG_TRUSTED_PROXIES` muss zum Gateway des Netzes passen |
| Änderung an der Projektdatei wirkt nicht | Projekt **Stoppen** und **Erstellen** |
| Image lässt sich nicht laden | Internetverbindung der NAS prüfen; das Image heißt `ghcr.io/ck-networks/kleinkontor:latest` (bis Version 1.0.2: `ghcr.io/chrkraus/kleinkontor`) |
