"""Datensicherung: konsistente Kopie der SQLite-Datenbank (+ Logo) als ZIP mit Rotation."""
import os
import shutil
import sqlite3
import tempfile
import zipfile
from datetime import datetime

PREFIX = "buchhaltung-"


def backup_dir(data_dir):
    path = os.path.join(data_dir, "backups")
    os.makedirs(path, mode=0o700, exist_ok=True)
    return path


def list_backups(directory):
    if not directory or not os.path.isdir(directory):
        return []
    files = [f for f in os.listdir(directory) if f.startswith(PREFIX) and f.endswith(".zip")]
    return sorted(files, reverse=True)


def _rotate(directory, keep):
    """Behält die `keep` neuesten Sicherungen plus je Monat die erste (für 24 Monate)."""
    files = list_backups(directory)
    keep_set = set(files[:max(keep, 1)])
    monthly = {}
    for f in sorted(files):                       # älteste zuerst → erste des Monats
        month = f[len(PREFIX):len(PREFIX) + 7]   # JJJJ-MM
        monthly.setdefault(month, f)
    keep_set.update(sorted(monthly.values(), reverse=True)[:24])
    for f in files:
        if f not in keep_set:
            os.remove(os.path.join(directory, f))


def create_backup(data_dir, db_path, logo_path="", extra_dir="", keep=30):
    """Erstellt eine Sicherung. Gibt (Pfad der ZIP-Datei, Warnung oder None) zurück."""
    target = backup_dir(data_dir)
    stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    zip_path = os.path.join(target, f"{PREFIX}{stamp}.zip")
    with tempfile.TemporaryDirectory() as tmp:
        snapshot = os.path.join(tmp, "buchhaltung.db")
        src = sqlite3.connect(db_path)
        dst = sqlite3.connect(snapshot)
        with dst:
            src.backup(dst)          # konsistent, auch während die App schreibt
        src.close()
        dst.close()
        check = sqlite3.connect(snapshot)
        ok = check.execute("PRAGMA integrity_check").fetchone()[0]
        check.close()
        if ok != "ok":
            raise RuntimeError(f"Integritätsprüfung der Datenbank fehlgeschlagen: {ok}")
        with zipfile.ZipFile(zip_path + ".tmp", "w", zipfile.ZIP_DEFLATED) as z:
            z.write(snapshot, "buchhaltung.db")
            if logo_path and os.path.exists(logo_path):
                z.write(logo_path, os.path.basename(logo_path))
            z.writestr("LIESMICH.txt",
                       "Sicherung der Buchhaltung vom " + datetime.now().strftime("%d.%m.%Y %H:%M") + "\n\n"
                       "Wiederherstellen: App stoppen, buchhaltung.db (und ggf. Logo) in den Ordner data/ "
                       "kopieren, App starten.\nAchtung: Die Datenbank enthält auch Zugangsdaten "
                       "(Mail-Passwort, Paperless-Token) – sicher aufbewahren.\n")
    os.replace(zip_path + ".tmp", zip_path)
    os.chmod(zip_path, 0o600)
    _rotate(target, keep)
    if extra_dir:
        try:
            if not os.path.isdir(extra_dir):
                raise OSError("Ordner nicht gefunden")
            shutil.copy2(zip_path, os.path.join(extra_dir, os.path.basename(zip_path)))
            _rotate(extra_dir, keep)
        except OSError as e:
            return zip_path, (f"Kopie nach {extra_dir} fehlgeschlagen ({e}) – "
                              f"die lokale Sicherung wurde trotzdem erstellt.")
    return zip_path, None
