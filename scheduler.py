"""Hintergrundaufgaben im App-Prozess: Backup, Belegeingang, Zahlungserinnerungen."""
import logging
import threading
import time
from datetime import date, datetime, timedelta

log = logging.getLogger("buchhaltung.scheduler")

BACKUP_INTERVAL = timedelta(hours=24)
PAPERLESS_INTERVAL = timedelta(minutes=15)
REMINDER_HOUR = 9          # automatische Erinnerungen frühestens ab 9 Uhr

_started = False


def start(app, jobs):
    """jobs: dict mit Funktionen backup(), paperless(), reminders() – laufen im App-Kontext."""
    global _started
    if _started:
        return
    _started = True

    def loop():
        time.sleep(20)   # App erst vollständig starten lassen
        while True:
            with app.app_context():
                from models import Settings, db
                try:
                    s = Settings.get()
                    now = datetime.now()
                    if not s.last_backup_at or now - s.last_backup_at >= BACKUP_INTERVAL:
                        _run("backup", jobs["backup"])
                    if (s.paperless_inbox_enabled and s.paperless_url and s.paperless_token
                            and (not s.last_paperless_sync_at or now - s.last_paperless_sync_at >= PAPERLESS_INTERVAL)):
                        _run("paperless", jobs["paperless"])
                    if s.reminder_auto and now.hour >= REMINDER_HOUR and s.last_reminder_run != date.today():
                        _run("reminders", jobs["reminders"])
                except Exception:
                    log.exception("Fehler im Hintergrundplaner")
                finally:
                    db.session.remove()
            time.sleep(60)

    threading.Thread(target=loop, name="buchhaltung-scheduler", daemon=True).start()
    log.info("Hintergrundplaner gestartet")


_last_attempt = {}
RETRY_AFTER = timedelta(minutes=10)


def _run(name, fn):
    """Führt eine Aufgabe aus – nach einem Fehlversuch frühestens wieder nach RETRY_AFTER."""
    now = datetime.now()
    if name in _last_attempt and now - _last_attempt[name] < RETRY_AFTER:
        return
    _last_attempt[name] = now
    try:
        fn()
    except Exception:
        log.exception("Aufgabe %s fehlgeschlagen", name)
