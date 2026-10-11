"""Buchhaltung für Selbständige: Angebote, Rechnungen, Einnahmen/Ausgaben, EÜR."""
import csv
import hashlib
import io
import mimetypes
import os
import secrets
import time
from datetime import date, datetime, timedelta

from flask import (Flask, Response, abort, flash, g, jsonify, redirect, render_template,
                   request, send_file, session, url_for)
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from werkzeug.security import check_password_hash, generate_password_hash
from sqlalchemy import extract, func, text
from sqlalchemy import inspect as sa_inspect

from helpers import (fmt_date, fmt_money, fmt_qty, next_customer_number, next_number,
                     parse_date, parse_decimal, parse_money)
from models import (DOC_INVOICE, DOC_OFFER, EXPENSE_CATEGORIES, INCOME_CATEGORIES,
                    PRODUCT_KINDS, STATUS_LABELS, Customer, Document, DocumentItem, EmailLog, Product,
                    AuditLog, ReceiptInbox, Settings, Transaction, User, db)
from paperless import PaperlessClient, PaperlessError
import auth
import environment
import backup
import euer_form
import mailer
import receipts
import scheduler
import security
import twofa
from einvoice import build_invoice_pdf, build_xml, missing_fields

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.environ.get("BUCHHALTUNG_DATA", os.path.join(BASE_DIR, "data"))

# Umsatzgrenzen der Kleinunternehmerregelung ab 2025 (§ 19 Abs. 1 UStG)
SMALL_BUSINESS_PREV_YEAR_LIMIT = 25_000_00
SMALL_BUSINESS_CURRENT_YEAR_LIMIT = 100_000_00


def migrate_columns():
    """Ergänzt neue Spalten in bestehenden Tabellen (SQLite kennt nur ADD COLUMN)."""
    insp = sa_inspect(db.engine)
    with db.engine.begin() as conn:
        for table in db.metadata.sorted_tables:
            existing = {c["name"] for c in insp.get_columns(table.name)}
            for col in table.columns:
                if col.name in existing:
                    continue
                ddl = f'ALTER TABLE "{table.name}" ADD COLUMN "{col.name}" {col.type.compile(db.engine.dialect)}'
                default = col.default.arg if col.default is not None and not callable(col.default.arg) else None
                if isinstance(default, bool):
                    ddl += f" DEFAULT {int(default)}"
                elif isinstance(default, (int, float)):
                    ddl += f" DEFAULT {default}"
                elif isinstance(default, str):
                    ddl += " DEFAULT '" + default.replace("'", "''") + "'"
                conn.execute(text(ddl))


def _check_data_dir():
    """Bricht mit verständlicher Meldung ab, wenn der Datenordner nicht beschreibbar ist."""
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        probe = os.path.join(DATA_DIR, ".schreibtest")
        with open(probe, "w") as f:
            f.write("ok")
        os.remove(probe)
    except OSError as e:
        uid = os.getuid() if hasattr(os, "getuid") else "?"
        gid = os.getgid() if hasattr(os, "getgid") else "?"
        raise SystemExit(
            f"FEHLER: Datenordner {DATA_DIR} ist nicht beschreibbar ({e.strerror}).\n"
            f"Der Prozess läuft als uid={uid} gid={gid}. Bitte prüfen, ob dieser Benutzer "
            f"Schreibrechte auf den Ordner hat (Docker: Angabe 'user:' und Ordnerrechte)."
        )


def create_app():
    _check_data_dir()
    app = Flask(__name__)
    key_file = os.path.join(DATA_DIR, "secret_key")
    if not os.path.exists(key_file):
        with open(key_file, "w") as f:
            f.write(secrets.token_hex(32))
        os.chmod(key_file, 0o600)
    with open(key_file) as f:
        app.secret_key = f.read().strip()
    app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///" + os.path.join(DATA_DIR, "buchhaltung.db")
    app.config["MAX_CONTENT_LENGTH"] = 50 * 1024 * 1024
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"      # schützt Formulare vor fremden Webseiten (CSRF)
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(days=30)
    if environment.IS_TEST:
        app.config["SESSION_COOKIE_NAME"] = "buchhaltung_test"   # getrennt von der produktiven Anmeldung
    app.wsgi_app = security.TrustLocalProxy(app.wsgi_app)
    app.session_interface = security.SchemeAwareSessionInterface()
    db.init_app(app)
    with app.app_context():
        db.create_all()
        migrate_columns()
        s = Settings.get()
        if not User.query.first() and s.password_hash:
            db.session.add(User(username="admin", display_name=s.owner_name or "Admin",
                                password_hash=s.password_hash, is_admin=True, active=True))
            s.password_hash = ""
            db.session.commit()
    register(app)
    if os.environ.get("BUCHHALTUNG_SCHEDULER") == "1" and not environment.IS_TEST:
        scheduler.start(app, {"backup": run_backup_job, "paperless": run_paperless_job,
                              "reminders": run_reminders_job})
    return app


# ---------------------------------------------------------------- Hintergrundaufgaben
def run_backup_job():
    s = Settings.get()
    try:
        path, warning = backup.create_backup(
            DATA_DIR, os.path.join(DATA_DIR, "buchhaltung.db"), s.logo_path,
            s.backup_dir.strip(), s.backup_keep or 30)
        s.last_backup_at = datetime.now()
        s.last_backup_file = os.path.basename(path)
        s.last_backup_error = warning or ""
    except Exception as e:
        s.last_backup_error = f"{datetime.now():%d.%m.%Y %H:%M}: {e}"
        db.session.commit()
        raise
    db.session.commit()
    return path, warning


def run_paperless_job():
    s = Settings.get()
    try:
        # Laufende Uploads zuerst zuordnen, damit sie nicht doppelt im Belegeingang landen
        for t in Transaction.query.filter(Transaction.paperless_task_id.isnot(None)):
            resolve_paperless_task(t)
        for d in Document.query.filter(Document.paperless_task_id.isnot(None)):
            resolve_paperless_task(d)
        new = receipts.sync(paperless_client(), s.paperless_inbox_tag or "Buchhaltung", s.small_business)
        s.last_paperless_sync_at = datetime.now()
        s.paperless_sync_error = ""
        db.session.commit()
        return new
    except Exception as e:
        db.session.rollback()
        s = Settings.get()
        s.paperless_sync_error = f"{datetime.now():%d.%m.%Y %H:%M}: {e}"
        s.last_paperless_sync_at = datetime.now()
        db.session.commit()
        raise


def pw_fingerprint(user):
    """Kurzer Fingerabdruck des Passwort-Hashs – ändert sich mit jedem neuen Passwort (beendet Sitzungen/Links)."""
    return hashlib.sha256(user.password_hash.encode()).hexdigest()[:16]


def nas_sync_status():
    """Status der Übertragung auf die NAS (nas_sync.sh) – None, wenn nicht eingerichtet."""
    if not os.path.exists(os.path.join(DATA_DIR, "nas-rsync.pass")):
        return None
    try:
        with open(os.path.join(DATA_DIR, "nas-sync.status")) as fh:
            stamp, state, *rest = fh.read().strip().split(" ", 2)
        at = datetime.fromisoformat(stamp)
    except (OSError, ValueError):
        return {"at": None, "ok": False, "info": "noch keine Übertragung", "stale": True}
    ok = state == "OK"
    return {"at": at, "ok": ok, "info": " ".join(rest),
            "stale": (datetime.now() - at) > timedelta(days=2)}


def apply_skonto_defaults(doc, s):
    if s.skonto_enabled:
        doc.skonto_percent, doc.skonto_days = s.skonto_percent, s.skonto_days
    else:
        doc.skonto_percent, doc.skonto_days = 0, 0


def overdue_invoices():
    today = date.today()
    return [d for d in Document.query.filter(Document.kind == DOC_INVOICE, Document.status == "sent",
                                             Document.cancels_id.is_(None), Document.due_date < today)
            .order_by(Document.due_date) if d.gross > 0]


def reminder_due(d, s):
    """Ist für diese Rechnung (automatisch) eine Erinnerung fällig?"""
    today = date.today()
    if (d.reminder_count or 0) >= (s.reminder_max or 2) or not d.customer.email:
        return False
    if (today - d.due_date).days < (s.reminder_days or 7):
        return False
    if d.last_reminder_at and (datetime.now() - d.last_reminder_at).days < (s.reminder_interval_days or 14):
        return False
    return True


def send_reminder(d, s, to, cc, subject, body, automatic=False):
    pdf, _ = build_invoice_pdf(d, s)
    name = f"Rechnung_{d.number}.pdf"
    log = EmailLog(document_id=d.id, subject=subject, kind="reminder", automatic=automatic,
                   recipients=", ".join(to + cc), attachments=name, sent_by=auth.current_username())
    try:
        mailer.send(s, to, cc, subject, body, [(name, pdf, "application/pdf")])
    except mailer.MailError as e:
        log.success = False
        log.error = str(e)
        db.session.add(log)
        db.session.commit()
        raise
    d.reminder_count = (d.reminder_count or 0) + 1
    d.last_reminder_at = datetime.now()
    auth.audit("Zahlungserinnerung versendet" + (" (automatisch)" if automatic else ""), "document", d.id,
               f"{d.number} · Stufe {d.reminder_count} an {', '.join(to + cc)}")
    db.session.add(log)
    db.session.commit()


def run_reminders_job():
    s = Settings.get()
    s.last_reminder_run = date.today()
    db.session.commit()
    if not s.reminder_auto or mailer.missing_settings(s):
        return 0
    sent = 0
    for d in overdue_invoices():
        if reminder_due(d, s):
            subject, body = mailer.default_mail(d, s, reminder=True)
            try:
                send_reminder(d, s, [d.customer.email], [], subject, body, automatic=True)
                sent += 1
            except mailer.MailError:
                pass   # im Versandprotokoll vermerkt
    return sent


def paperless_client():
    s = Settings.get()
    return PaperlessClient(s.paperless_url, s.paperless_token)


def paperless_configured():
    s = Settings.get()
    return bool(s.paperless_url and s.paperless_token)


def resolve_paperless_task(obj):
    """Ordnet nachträglich die Paperless-Dokument-ID zu, falls der Upload noch lief."""
    if obj.paperless_document_id or not obj.paperless_task_id or not paperless_configured():
        return
    try:
        status, doc_id, _ = paperless_client().task_result(obj.paperless_task_id)
    except PaperlessError:
        return
    if status == "SUCCESS" and doc_id:
        obj.paperless_document_id = doc_id
        obj.paperless_task_id = None
        db.session.commit()
    elif status in ("FAILURE", "REVOKED", "SUCCESS"):
        obj.paperless_task_id = None
        db.session.commit()


def upload_to_paperless(obj, filename, content, mimetype, **meta):
    """Lädt hoch und verknüpft das Ergebnis mit obj (Transaction oder Document)."""
    client = paperless_client()
    task_id = client.upload(filename, content, mimetype=mimetype, **meta)
    status, doc_id, msg = client.wait_for_task(task_id, timeout=12)
    if status == "SUCCESS" and doc_id:
        obj.paperless_document_id = doc_id
        obj.paperless_task_id = None
        return f"In Paperless gespeichert (Dokument #{doc_id})."
    if status in ("FAILURE", "REVOKED"):
        # Häufigster Fall: Duplikat – Paperless meldet dann die vorhandene ID im Text
        raise PaperlessError(f"Paperless konnte das Dokument nicht verarbeiten: {msg}")
    if status == "SUCCESS":
        return "In Paperless gespeichert. Die Dokument-ID wurde nicht gemeldet – bitte über „In Paperless suchen“ verknüpfen."
    obj.paperless_task_id = task_id
    return "Upload an Paperless läuft noch – die Verknüpfung wird automatisch ergänzt, sobald das Dokument verarbeitet ist."


def year_choices():
    years = {date.today().year}
    for model, col in ((Transaction, Transaction.date), (Document, Document.issue_date)):
        for (y,) in db.session.query(extract("year", col)).distinct():
            if y:
                years.add(int(y))
    return sorted(years, reverse=True)


def revenue_for_year(year):
    """Umsatz (netto) eines Jahres aus Einnahmen der Kategorie Umsatzerlöse."""
    total = 0
    for t in Transaction.query.filter(Transaction.kind == "income",
                                      Transaction.category == "Umsatzerlöse",
                                      extract("year", Transaction.date) == year):
        total += t.net_amount
    return total


def catalog_json():
    """Aktive Katalogeinträge für die Positionsauswahl im Editor."""
    return [{"id": p.id, "name": p.name, "sku": p.sku, "category": p.category, "kind": p.kind,
             "manufacturer": p.manufacturer, "text": p.document_text, "unit": p.unit,
             "price": fmt_money(p.unit_price, False), "vat": p.vat_rate}
            for p in Product.query.filter_by(active=True).order_by(Product.category, Product.name)]


STARTER_PRODUCTS = [
    # (Art, Kategorie, Name, Beschreibung, Einheit)
    ("service", "Arbeitszeit", "Arbeitszeit Netzwerktechnik", "Installation, Konfiguration und Inbetriebnahme", "Std."),
    ("service", "Anfahrt", "Anfahrtspauschale", "", "pauschal"),
    ("service", "Anfahrt", "Fahrtkosten", "je gefahrenem Kilometer", "km"),
    ("service", "Installation", "Montage und Einrichtung Access Point",
     "Montage an Decke/Wand, Adoption im Controller, WLAN-Konfiguration und Funktionstest", "Stk."),
    ("service", "Installation", "Montage und Einrichtung IP-Kamera",
     "Montage, Ausrichtung, Einbindung ins Netzwerk/NVR, App-Einrichtung und Funktionstest", "Stk."),
    ("service", "Installation", "Netzwerkdose setzen inkl. Patchen", "", "Stk."),
    ("service", "Installation", "Netzwerkkabel verlegen", "Cat.6/Cat.7, Aufputz im Kabelkanal", "m"),
    ("service", "Beratung", "Netzwerkplanung und WLAN-Ausleuchtung", "", "Std."),
    ("product", "Access Points", "UniFi Access Point", "", "Stk."),
    ("product", "Kameras", "Reolink PoE-Kamera", "", "Stk."),
    ("product", "Zubehör", "PoE-Injektor", "", "Stk."),
    ("product", "Zubehör", "Patchkabel Cat.6", "", "Stk."),
]


def register(app):
    app.jinja_env.filters["money"] = fmt_money
    app.jinja_env.filters["date"] = fmt_date
    app.jinja_env.filters["qty"] = fmt_qty

    @app.context_processor
    def inject():
        user = getattr(g, "user", None)
        can = (lambda perm: bool(user) and user.can(perm))
        return {"settings": Settings.get(), "today": date.today(),
                "paperless_enabled": paperless_configured(), "STATUS_LABELS": STATUS_LABELS,
                "inbox_count": ReceiptInbox.query.filter_by(status="pending").count() if can("bookkeeping_view") else 0,
                "overdue_count": len(overdue_invoices()) if can("documents_view") else 0,
                "current_user": user, "can": can, "is_test": environment.IS_TEST,
                "nas_status": nas_sync_status() if can("settings") else None}

    # ------------------------------------------------------------------ Anmeldung & Rechte
    failed_logins = {}   # (IP, Benutzer) → (Anzahl, Zeitpunkt der letzten Fehlanmeldung)
    failed_by_user = {}  # Benutzer → Zeitpunkte der Fehlversuche (gegen verteilte Angriffe von vielen IPs)
    failed_2fa = {}      # Benutzer-ID → (Anzahl, Zeitpunkt)

    def user_locked(username):
        recent = [t for t in failed_by_user.get(username, []) if time.time() - t < 900]
        failed_by_user[username] = recent
        return len(recent) >= 10

    @app.after_request
    def security_headers(response):
        return security.add_headers(response)

    @app.before_request
    def check_access():
        security.check_same_origin()
        g.user = None
        uid = session.get("user_id")
        if uid:
            u = db.session.get(User, uid)
            # Deaktivierte Benutzer bzw. geänderte Passwörter beenden bestehende Sitzungen
            if u and u.active and session.get("pw") == pw_fingerprint(u):
                g.user = u
            else:
                session.clear()
        perm = auth.required(request.endpoint, request.method)
        if perm == auth.PUBLIC:
            return None
        if not User.query.first():
            return redirect(url_for("setup_password"))
        if g.user is None:
            return redirect(url_for("login", next=request.full_path if request.method == "GET" else None))
        if g.user.must_change_password and request.endpoint not in ("password_change_required", "logout"):
            return redirect(url_for("password_change_required"))
        if (Settings.get().require_2fa and not g.user.totp_enabled
                and request.endpoint not in ("twofa_setup", "logout", "account")):
            flash("Bitte richte zuerst die Zwei-Faktor-Anmeldung ein – sie ist für alle Benutzer vorgeschrieben.", "info")
            return redirect(url_for("twofa_setup"))
        if not auth.allowed(g.user, perm):
            if request.method == "GET" and request.endpoint != "dashboard":
                flash("Dafür fehlt dir die Berechtigung.", "error")
                return redirect(url_for("dashboard"))
            abort(403)
        return None

    def safe_next(target):
        # nur relative Pfade dieser App – kein Weiterleiten auf fremde Seiten
        if target and target.startswith("/") and not target.startswith("//"):
            return target.rstrip("?")
        return url_for("dashboard")

    def start_session(user, remember):
        session.clear()
        session["user_id"] = user.id
        session["pw"] = pw_fingerprint(user)
        session.permanent = remember
        user.last_login_at = datetime.now()

    def validate_password(pw, pw2):
        if len(pw) < 8:
            return "Das Passwort muss mindestens 8 Zeichen lang sein."
        if pw != pw2:
            return "Die Passwörter stimmen nicht überein."
        return None

    @app.route("/einrichten", methods=["GET", "POST"])
    def setup_password():
        """Ersteinrichtung: legt den ersten Admin an (nur solange es keinen Benutzer gibt)."""
        if User.query.first():
            return redirect(url_for("login"))
        if request.method == "POST":
            f = request.form
            username = f.get("username", "").strip().lower()
            error = (validate_password(f.get("password", ""), f.get("password2", ""))
                     or ("Bitte einen Benutzernamen angeben." if not username else None))
            if error:
                flash(error, "error")
            else:
                u = User(username=username, display_name=f.get("display_name", "").strip(),
                         password_hash=generate_password_hash(f["password"]), is_admin=True, active=True)
                db.session.add(u)
                db.session.flush()
                start_session(u, True)
                g.user = u
                auth.audit("Admin eingerichtet", "user", u.id, u.username)
                db.session.commit()
                flash("Admin-Konto angelegt – die App ist jetzt geschützt.", "success")
                return redirect(url_for("dashboard"))
        return render_template("login.html", setup=True)

    @app.route("/anmelden", methods=["GET", "POST"])
    def login():
        if not User.query.first():
            return redirect(url_for("setup_password"))
        if request.method == "POST":
            username = request.form.get("username", "").strip().lower()
            key = (request.remote_addr or "?", username)
            count, last = failed_logins.get(key, (0, 0))
            if (count >= 5 and time.time() - last < 300) or user_locked(username):
                flash("Zu viele Fehlversuche – bitte einige Minuten warten.", "error")
                return render_template("login.html", setup=False, username=username), 429
            u = User.query.filter_by(username=username).first()
            if u and u.active and check_password_hash(u.password_hash, request.form.get("password", "")):
                failed_logins.pop(key, None)
                if u.totp_enabled:
                    session.clear()
                    session["2fa_uid"], session["2fa_at"] = u.id, time.time()
                    session["2fa_remember"] = bool(request.form.get("remember"))
                    session["2fa_next"] = request.form.get("next", "")
                    return redirect(url_for("login_2fa"))
                start_session(u, bool(request.form.get("remember")))
                g.user = u
                auth.audit("Anmeldung", "user", u.id)
                db.session.commit()
                return redirect(safe_next(request.form.get("next")))
            failed_logins[key] = (count + 1 if time.time() - last < 300 else 1, time.time())
            failed_by_user.setdefault(username, []).append(time.time())
            time.sleep(1)
            flash("Benutzername oder Passwort falsch.", "error")
            return render_template("login.html", setup=False, username=username,
                                   next=request.form.get("next", ""))
        return render_template("login.html", setup=False, next=request.args.get("next", ""))

    # ---------- Eigenes Passwort bei erster Anmeldung
    @app.route("/passwort-festlegen", methods=["GET", "POST"])
    def password_change_required():
        u = g.user
        if not u.must_change_password:
            return redirect(url_for("dashboard"))
        if request.method == "POST":
            pw = request.form.get("password", "")
            error = validate_password(pw, request.form.get("password2", ""))
            if not error and check_password_hash(u.password_hash, pw):
                error = "Bitte ein neues Passwort wählen – nicht das vom Admin vergebene."
            if error:
                flash(error, "error")
                return render_template("password_reset.html", u=u, token=None, first_login=True)
            u.password_hash = generate_password_hash(pw)
            u.must_change_password = False
            session["pw"] = pw_fingerprint(u)
            auth.audit("Eigenes Passwort bei erster Anmeldung festgelegt", "user", u.id)
            db.session.commit()
            flash("Dein persönliches Passwort ist gespeichert. Willkommen!", "success")
            return redirect(url_for("dashboard"))
        return render_template("password_reset.html", u=u, token=None, first_login=True)

    # ---------- Zwei-Faktor-Anmeldung
    @app.route("/anmelden/code", methods=["GET", "POST"])
    def login_2fa():
        uid = session.get("2fa_uid")
        if not uid or time.time() - session.get("2fa_at", 0) > 300:
            session.clear()
            flash("Bitte erneut anmelden.", "info")
            return redirect(url_for("login"))
        u = db.session.get(User, uid)
        if not u or not u.active or not u.totp_enabled:
            session.clear()
            return redirect(url_for("login"))
        if request.method == "POST":
            count, last = failed_2fa.get(uid, (0, 0))
            if count >= 5 and time.time() - last < 300:
                flash("Zu viele falsche Codes – bitte 5 Minuten warten.", "error")
                return render_template("login_2fa.html"), 429
            code = request.form.get("code", "").strip()
            ok, via_recovery = False, False
            step = twofa.check_code(u.totp_secret, code, u.totp_last_step)
            if step:
                u.totp_last_step, ok = step, True
            elif len(code.replace("-", "").replace(" ", "")) == 12:
                ok, u.recovery_codes = twofa.use_recovery_code(u.recovery_codes, code)
                via_recovery = ok
            if ok:
                failed_2fa.pop(uid, None)
                remember, nxt = session.get("2fa_remember", False), session.get("2fa_next", "")
                start_session(u, remember)
                g.user = u
                auth.audit("Anmeldung (2FA" + (", Wiederherstellungscode)" if via_recovery else ")"), "user", u.id)
                db.session.commit()
                if via_recovery:
                    left = twofa.remaining_recovery_codes(u.recovery_codes)
                    flash(f"Mit Wiederherstellungscode angemeldet – noch {left} Codes übrig. "
                          "Richte die Authenticator-App unter „Mein Konto“ neu ein, falls das Handy weg ist.", "info")
                return redirect(safe_next(nxt))
            failed_2fa[uid] = (count + 1 if time.time() - last < 300 else 1, time.time())
            time.sleep(1)
            flash("Code falsch oder abgelaufen.", "error")
        return render_template("login_2fa.html")

    @app.route("/konto/zwei-faktor", methods=["GET", "POST"])
    def twofa_setup():
        u = g.user
        s = Settings.get()
        if request.method == "POST":
            action = request.form.get("action")
            if action == "enable" and not u.totp_enabled:
                secret = session.get("2fa_new_secret")
                step = twofa.check_code(secret, request.form.get("code"))
                if not step:
                    flash("Der Code passt nicht – bitte den aktuellen Code aus der App eingeben.", "error")
                    return redirect(url_for("twofa_setup"))
                codes, stored = twofa.new_recovery_codes()
                u.totp_secret, u.totp_enabled, u.totp_last_step, u.recovery_codes = secret, True, step, stored
                session.pop("2fa_new_secret", None)
                auth.audit("Zwei-Faktor aktiviert", "user", u.id)
                db.session.commit()
                return render_template("twofa_setup.html", u=u, codes=codes)
            if action == "new_codes" and u.totp_enabled:
                if not check_password_hash(u.password_hash, request.form.get("password", "")):
                    flash("Passwort falsch.", "error")
                    return redirect(url_for("twofa_setup"))
                codes, u.recovery_codes = twofa.new_recovery_codes()
                auth.audit("Neue Wiederherstellungscodes", "user", u.id)
                db.session.commit()
                return render_template("twofa_setup.html", u=u, codes=codes)
            if action == "disable" and u.totp_enabled:
                if s.require_2fa:
                    flash("Zwei-Faktor ist vorgeschrieben und kann nicht abgeschaltet werden.", "error")
                elif not check_password_hash(u.password_hash, request.form.get("password", "")):
                    flash("Passwort falsch.", "error")
                else:
                    u.totp_enabled, u.totp_secret, u.recovery_codes = False, "", ""
                    auth.audit("Zwei-Faktor deaktiviert", "user", u.id)
                    db.session.commit()
                    flash("Zwei-Faktor-Anmeldung abgeschaltet.", "success")
                return redirect(url_for("twofa_setup"))
            return redirect(url_for("twofa_setup"))
        qr = secret = None
        if not u.totp_enabled:
            secret = session.get("2fa_new_secret") or twofa.new_secret()
            session["2fa_new_secret"] = secret
            qr = twofa.qr_svg(twofa.provisioning_uri(secret, u.username, s.company_name or s.owner_name or ""))
        return render_template("twofa_setup.html", u=u, qr=qr, secret=secret, codes=None,
                               remaining=twofa.remaining_recovery_codes(u.recovery_codes))

    # ---------- Passwort per E-Mail zurücksetzen
    RESET_MAX_AGE = 3600          # Link gilt 1 Stunde
    reset_requests = {}           # IP → Zeitpunkte der letzten Anfragen

    def reset_serializer():
        return URLSafeTimedSerializer(app.secret_key, salt="passwort-zuruecksetzen")

    INVITE_MAX_AGE = 3 * 24 * 3600   # Einladungslinks gelten 3 Tage

    def reset_token(user, invite=False):
        # Enthält einen Fingerabdruck des aktuellen Hashs → Link wird nach der Passwortänderung ungültig (einmalig)
        return reset_serializer().dumps({"uid": user.id, "pw": pw_fingerprint(user), "inv": 1 if invite else 0})

    def user_from_token(token):
        try:
            data, issued = reset_serializer().loads(token, max_age=INVITE_MAX_AGE, return_timestamp=True)
            if not data.get("inv") and (datetime.now(issued.tzinfo) - issued).total_seconds() > RESET_MAX_AGE:
                raise SignatureExpired("abgelaufen")
        except SignatureExpired:
            return None, "Der Link ist abgelaufen – bitte einen neuen anfordern."
        except BadSignature:
            return None, "Der Link ist ungültig."
        u = db.session.get(User, data.get("uid"))
        if not u or not u.active or pw_fingerprint(u) != data.get("pw"):
            return None, "Der Link wurde bereits verwendet oder ist nicht mehr gültig."
        return u, None

    def reset_available():
        s = Settings.get()
        return bool(s.app_url and not mailer.missing_settings(s))

    @app.route("/passwort-vergessen", methods=["GET", "POST"])
    def password_forgot():
        if request.method == "POST":
            ip = request.remote_addr or "?"
            recent = [t for t in reset_requests.get(ip, []) if time.time() - t < 900]
            if len(recent) >= 5:
                flash("Zu viele Anfragen – bitte 15 Minuten warten.", "error")
                return render_template("password_forgot.html", available=reset_available()), 429
            reset_requests[ip] = recent + [time.time()]
            ident = request.form.get("ident", "").strip().lower()
            s = Settings.get()
            u = None
            if ident:
                u = (User.query.filter(db.func.lower(User.username) == ident).first()
                     or User.query.filter(db.func.lower(User.email) == ident).first())
            if u and u.active and u.email and reset_available():
                link = s.app_url.rstrip("/") + url_for("password_reset", token=reset_token(u))
                body = (f"Hallo {u.name},\n\nfür dein Konto „{u.username}“ in der Buchhaltung von "
                        f"{s.company_name or s.owner_name} wurde ein neues Passwort angefordert.\n\n"
                        f"Über diesen Link kannst du innerhalb der nächsten Stunde ein neues Passwort festlegen:\n"
                        f"{link}\n\nDer Link funktioniert nur einmal. Wenn du das nicht angefordert hast, "
                        f"ignoriere diese E-Mail einfach – dein Passwort bleibt unverändert.\n")
                try:
                    mailer.send(s, [u.email], [], "Passwort zurücksetzen – Buchhaltung", body, [],
                                bcc_self=False)
                    auth.audit("Passwort-Link angefordert", "user", u.id, f"an {u.email}", username=u.username)
                except mailer.MailError as e:
                    app.logger.error("Passwort-Mail an %s fehlgeschlagen: %s", u.email, e)
                    auth.audit("Passwort-Link fehlgeschlagen", "user", u.id, str(e)[:200], username=u.username)
                db.session.commit()
            else:
                time.sleep(1)   # gleiche Antwortzeit wie mit Versand
            # Immer dieselbe Antwort – verrät nicht, ob es das Konto gibt
            flash("Falls ein aktives Konto mit hinterlegter E-Mail-Adresse existiert, wurde ein Link zum "
                  "Zurücksetzen verschickt. Bitte Posteingang (und Spam-Ordner) prüfen.", "success")
            return redirect(url_for("login"))
        return render_template("password_forgot.html", available=reset_available())

    @app.route("/passwort-zuruecksetzen/<token>", methods=["GET", "POST"])
    def password_reset(token):
        u, error = user_from_token(token)
        if error:
            flash(error, "error")
            return redirect(url_for("password_forgot"))
        if request.method == "POST":
            error = validate_password(request.form.get("password", ""), request.form.get("password2", ""))
            if error:
                flash(error, "error")
                return render_template("password_reset.html", u=u, token=token)
            u.password_hash = generate_password_hash(request.form["password"])
            u.must_change_password = False
            auth.audit("Passwort per E-Mail-Link gesetzt", "user", u.id, username=u.username)
            db.session.commit()
            session.clear()
            flash("Neues Passwort gespeichert – bitte jetzt anmelden.", "success")
            return redirect(url_for("login"))
        return render_template("password_reset.html", u=u, token=token)

    @app.route("/abmelden")
    def logout():
        session.clear()
        flash("Abgemeldet.", "success")
        return redirect(url_for("login"))

    @app.route("/konto", methods=["GET", "POST"])
    def account():
        """Eigenes Konto: Anzeigename und Passwort ändern."""
        u = g.user
        if request.method == "POST":
            f = request.form
            u.display_name = f.get("display_name", "").strip()
            u.email = f.get("email", "").strip()
            if f.get("new_password"):
                if not check_password_hash(u.password_hash, f.get("current_password", "")):
                    db.session.rollback()
                    flash("Aktuelles Passwort ist falsch – nichts gespeichert.", "error")
                    return redirect(url_for("account"))
                error = validate_password(f["new_password"], f.get("new_password2", ""))
                if error:
                    db.session.rollback()
                    flash(error + " Nichts gespeichert.", "error")
                    return redirect(url_for("account"))
                u.password_hash = generate_password_hash(f["new_password"])
                session["pw"] = pw_fingerprint(u)
                auth.audit("Passwort geändert", "user", u.id)
            db.session.commit()
            flash("Gespeichert.", "success")
            return redirect(url_for("account"))
        return render_template("account.html", u=u)

    # ------------------------------------------------------------------ Benutzerverwaltung (Admin)
    @app.route("/benutzer")
    def users():
        return render_template("users.html", users=User.query.order_by(User.is_admin.desc(), User.username).all(),
                               permissions=auth.PERMISSIONS)

    @app.route("/benutzer/neu", methods=["GET", "POST"])
    @app.route("/benutzer/<int:uid>", methods=["GET", "POST"])
    def user_edit(uid=None):
        u = db.get_or_404(User, uid) if uid else User(active=True, is_admin=False,
                                                       permissions="documents_view,bookkeeping_view")
        if request.method == "POST":
            f = request.form
            username = f.get("username", "").strip().lower()
            if not username or not username.replace(".", "").replace("-", "").replace("_", "").isalnum():
                flash("Benutzername: nur Buchstaben, Ziffern, Punkt, Binde- und Unterstrich.", "error")
                return render_template("user_edit.html", u=u, permissions=auth.PERMISSIONS)
            other = User.query.filter_by(username=username).first()
            if other and other.id != u.id:
                flash(f"Benutzername „{username}“ ist schon vergeben.", "error")
                return render_template("user_edit.html", u=u, permissions=auth.PERMISSIONS)
            was_admin = bool(u.id and u.is_admin and u.active)
            u.username = username
            u.display_name = f.get("display_name", "").strip()
            u.email = f.get("email", "").strip()
            if f.get("reset_2fa") and u.totp_enabled:
                u.totp_enabled, u.totp_secret, u.recovery_codes = False, "", ""
                auth.audit("Zwei-Faktor durch Admin zurückgesetzt", "user", u.id, u.username)
            u.is_admin = bool(f.get("is_admin"))
            u.active = bool(f.get("active"))
            u.permissions = ",".join(p for p in auth.PERMISSIONS if f.get("perm_" + p))
            # Der letzte aktive Admin darf sich nicht selbst aussperren
            if was_admin and not (u.is_admin and u.active):
                remaining = User.query.filter(User.is_admin.is_(True), User.active.is_(True), User.id != u.id).count()
                if not remaining:
                    db.session.rollback()
                    flash("Es muss mindestens ein aktiver Admin bleiben.", "error")
                    return redirect(url_for("user_edit", uid=uid))
            pw = f.get("password", "")
            invite = bool(f.get("invite")) and not u.id
            if invite:
                if not u.email:
                    db.session.rollback()
                    flash("Für eine Einladung bitte eine E-Mail-Adresse angeben.", "error")
                    return render_template("user_edit.html", u=u, permissions=auth.PERMISSIONS, invite_ok=reset_available())
                if not reset_available():
                    db.session.rollback()
                    flash("Einladungen brauchen den E-Mail-Versand und die „Adresse der App“ (Einstellungen).", "error")
                    return render_template("user_edit.html", u=u, permissions=auth.PERMISSIONS, invite_ok=False)
                # Unbenutzbares Zufallspasswort – der Benutzer legt sein eigenes über den Link fest
                u.password_hash = generate_password_hash(secrets.token_urlsafe(32))
                u.must_change_password = False
            elif pw or not u.id:
                error = validate_password(pw, f.get("password2", ""))
                if error:
                    db.session.rollback()
                    flash(error, "error")
                    return render_template("user_edit.html", u=u, permissions=auth.PERMISSIONS)
                u.password_hash = generate_password_hash(pw)
                # Vom Admin vergebenes Passwort muss beim nächsten Login ersetzt werden (nicht beim eigenen Konto)
                u.must_change_password = bool(f.get("must_change")) and not (u.id and u.id == g.user.id)
            new = not u.id
            if new:
                db.session.add(u)
            db.session.flush()
            rights = "Admin" if u.is_admin else (", ".join(sorted(u.permission_set)) or "keine Rechte")
            auth.audit("Benutzer angelegt" if new else "Benutzer geändert", "user", u.id,
                       f"{u.username} · {'aktiv' if u.active else 'deaktiviert'} · {rights}"
                       + (" · Passwort gesetzt" if pw else ""))
            if u.id == g.user.id and pw:
                session["pw"] = pw_fingerprint(u)
            db.session.commit()
            flash(f"Benutzer „{u.name}“ gespeichert.", "success")
            if invite:
                s = Settings.get()
                link = s.app_url.rstrip("/") + url_for("password_reset", token=reset_token(u, invite=True))
                body = (f"Hallo {u.name},\n\n{g.user.name} hat dir ein Konto für die Buchhaltung von "
                        f"{s.company_name or s.owner_name} eingerichtet.\n\nDein Benutzername: {u.username}\n\n"
                        f"Über diesen Link legst du dein persönliches Passwort fest (3 Tage gültig, nur einmal nutzbar):\n"
                        f"{link}\n\nViele Grüße\n{s.owner_name or s.company_name}\n")
                try:
                    mailer.send(s, [u.email], [], "Einladung zur Buchhaltung", body, [], bcc_self=False)
                    auth.audit("Einladung verschickt", "user", u.id, f"an {u.email}")
                    db.session.commit()
                    flash(f"Einladung an {u.email} verschickt.", "success")
                except mailer.MailError as e:
                    flash(f"Benutzer angelegt, aber die Einladung konnte nicht verschickt werden: {e}", "error")
            return redirect(url_for("users"))
        return render_template("user_edit.html", u=u, permissions=auth.PERMISSIONS, invite_ok=reset_available(),
                               delete_blockers=user_delete_blockers(u) if u.id else None)

    # Aktionen, die nur das eigene Konto betreffen – sie verhindern das Löschen nicht
    ACCOUNT_ONLY_ACTIONS = ("Anmeldung", "Passwort", "Eigenes Passwort", "Zwei-Faktor", "Neue Wiederherstellungscodes")

    def user_delete_blockers(u):
        """Gründe, warum ein Benutzer nicht gelöscht werden darf (leer = löschbar)."""
        reasons = []
        if g.user and u.id == g.user.id:
            reasons.append("Du kannst dein eigenes Konto nicht löschen.")
        if u.is_admin and u.active and not User.query.filter(
                User.is_admin.is_(True), User.active.is_(True), User.id != u.id).count():
            reasons.append("Der letzte aktive Admin kann nicht gelöscht werden.")
        n = Document.query.filter(db.or_(Document.created_by == u.username, Document.finalized_by == u.username)).count()
        if n:
            reasons.append(f"hat {n} Angebot(e)/Rechnung(en) erstellt oder festgeschrieben")
        n = Transaction.query.filter_by(created_by=u.username).count()
        if n:
            reasons.append(f"hat {n} Buchung(en) angelegt")
        n = EmailLog.query.filter_by(sent_by=u.username).count()
        if n:
            reasons.append(f"hat {n} E-Mail(s) versendet")
        own = AuditLog.query.filter_by(username=u.username).all()
        business = [a for a in own if not a.action.startswith(ACCOUNT_ONLY_ACTIONS)]
        if business:
            reasons.append(f"hat {len(business)} weitere protokollierte Aktion(en), z. B. „{business[0].action}“")
        return reasons

    @app.post("/benutzer/<int:uid>/loeschen")
    def user_delete(uid):
        u = db.get_or_404(User, uid)
        reasons = user_delete_blockers(u)
        if reasons:
            flash("Löschen nicht möglich: " + "; ".join(reasons) + ". Bitte stattdessen deaktivieren.", "error")
            return redirect(url_for("user_edit", uid=uid))
        name = f"{u.username} ({u.name})"
        auth.audit("Benutzer gelöscht", "user", u.id, name)
        db.session.delete(u)
        db.session.commit()
        flash(f"Benutzer {name} gelöscht.", "success")
        return redirect(url_for("users"))

    @app.route("/protokoll")
    def audit():
        q = AuditLog.query
        user = request.args.get("benutzer", "")
        if user:
            q = q.filter_by(username=user)
        page = max(request.args.get("seite", 1, type=int), 1)
        entries = q.order_by(AuditLog.at.desc(), AuditLog.id.desc()).offset((page - 1) * 100).limit(101).all()
        names = [n for (n,) in db.session.query(AuditLog.username).distinct().order_by(AuditLog.username)]
        return render_template("audit.html", entries=entries[:100], more=len(entries) > 100, page=page,
                               user=user, names=names)

    @app.errorhandler(ValueError)
    def value_error(e):
        flash(str(e), "error")
        return redirect(request.referrer or url_for("dashboard"))

    # ------------------------------------------------------------------ Dashboard
    @app.route("/")
    def dashboard():
        year = date.today().year
        income = expense = 0
        for t in Transaction.query.filter(extract("year", Transaction.date) == year):
            if t.kind == "income":
                income += t.net_amount
            else:
                expense += t.net_amount
        open_invoices = Document.query.filter_by(kind=DOC_INVOICE, status="sent").order_by(Document.due_date).all()
        open_offers = Document.query.filter_by(kind=DOC_OFFER, status="sent").order_by(Document.issue_date.desc()).all()
        recent = Transaction.query.order_by(Transaction.date.desc(), Transaction.id.desc()).limit(8).all()
        s = Settings.get()
        sb = None
        if s.small_business:
            sb = {"current": revenue_for_year(year), "prev": revenue_for_year(year - 1),
                  "limit_current": SMALL_BUSINESS_CURRENT_YEAR_LIMIT,
                  "limit_prev": SMALL_BUSINESS_PREV_YEAR_LIMIT}
        return render_template("dashboard.html", year=year, income=income, expense=expense,
                               open_invoices=open_invoices, open_offers=open_offers,
                               open_sum=sum(d.gross for d in open_invoices), recent=recent, sb=sb,
                               setup_needed=not (s.company_name or s.owner_name))

    # ------------------------------------------------------------------ Kunden
    @app.route("/kunden")
    def customers():
        q = request.args.get("q", "").strip()
        query = Customer.query
        if q:
            like = f"%{q}%"
            query = query.filter(db.or_(Customer.company.ilike(like), Customer.name.ilike(like),
                                        Customer.city.ilike(like), Customer.email.ilike(like),
                                        Customer.customer_number.ilike(like)))
        return render_template("customers.html",
                               customers=query.order_by(Customer.company, Customer.name).all(), q=q)

    @app.route("/kunden/neu", methods=["GET", "POST"])
    @app.route("/kunden/<int:cid>", methods=["GET", "POST"])
    def customer_edit(cid=None):
        c = db.get_or_404(Customer, cid) if cid else Customer(country="Deutschland")
        if request.method == "POST":
            for field in ("company", "name", "street", "zip_code", "city", "country",
                          "email", "phone", "vat_id", "buyer_reference", "notes"):
                setattr(c, field, request.form.get(field, "").strip())
            if not (c.company or c.name):
                flash("Bitte Firma oder Name angeben.", "error")
                return render_template("customer_edit.html", c=c)
            new = not c.id
            if new:
                c.customer_number = next_customer_number()
                db.session.add(c)
            db.session.flush()
            auth.audit("Kunde angelegt" if new else "Kunde geändert", "customer", c.id, f"{c.customer_number} · {c.display_name}")
            db.session.commit()
            flash("Kunde gespeichert.", "success")
            if request.args.get("next") == "document":
                return redirect(url_for("document_new", kind=request.args.get("kind", DOC_INVOICE), customer_id=c.id))
            return redirect(url_for("customer_edit", cid=c.id))
        docs = []
        if c.id:
            docs = Document.query.filter_by(customer_id=c.id).order_by(Document.issue_date.desc()).all()
        return render_template("customer_edit.html", c=c, docs=docs)

    @app.post("/kunden/<int:cid>/loeschen")
    def customer_delete(cid):
        c = db.get_or_404(Customer, cid)
        if c.documents:
            flash("Kunde hat Angebote/Rechnungen und kann nicht gelöscht werden.", "error")
            return redirect(url_for("customer_edit", cid=cid))
        auth.audit("Kunde gelöscht", "customer", None, f"{c.customer_number} · {c.display_name}")
        db.session.delete(c)
        db.session.commit()
        flash("Kunde gelöscht.", "success")
        return redirect(url_for("customers"))

    # ------------------------------------------------------------------ Angebote & Rechnungen
    @app.route("/angebote", defaults={"kind": DOC_OFFER})
    @app.route("/rechnungen", defaults={"kind": DOC_INVOICE})
    def documents(kind):
        status = request.args.get("status", "")
        year = request.args.get("jahr", type=int)
        query = Document.query.filter_by(kind=kind)
        if status:
            query = query.filter_by(status=status)
        if year:
            query = query.filter(extract("year", Document.issue_date) == year)
        docs = query.order_by(Document.issue_date.desc(), Document.id.desc()).all()
        statuses = (["draft", "sent", "accepted", "rejected", "invoiced"] if kind == DOC_OFFER
                    else ["draft", "sent", "paid", "cancelled"])
        return render_template("documents.html", kind=kind, docs=docs, status=status, year=year,
                               statuses=statuses, years=year_choices(),
                               total=sum(d.gross for d in docs if d.status != "cancelled"))

    @app.route("/dokument/neu")
    def document_new():
        kind = request.args.get("kind", DOC_INVOICE)
        if kind not in (DOC_OFFER, DOC_INVOICE):
            abort(400)
        customers_ = Customer.query.order_by(Customer.company, Customer.name).all()
        if not customers_:
            flash("Lege zuerst einen Kunden an.", "info")
            return redirect(url_for("customer_edit", next="document", kind=kind))
        s = Settings.get()
        d = Document(kind=kind, issue_date=date.today(), small_business=s.small_business,
                     customer_id=request.args.get("customer_id", type=int))
        if kind == DOC_INVOICE:
            d.due_date = date.today() + timedelta(days=s.payment_terms_days)
            d.intro, d.footer = s.invoice_intro, s.invoice_footer
            apply_skonto_defaults(d, s)
        else:
            d.due_date = date.today() + timedelta(days=s.offer_valid_days)
            d.intro, d.footer = s.offer_intro, s.offer_footer
        d.items = [DocumentItem(position=0, quantity=1, unit="Std.",
                                vat_rate=0 if s.small_business else s.default_vat_rate)]
        return render_template("document_edit.html", d=d, customers=customers_, products=catalog_json())

    @app.route("/dokument/<int:did>/bearbeiten")
    def document_edit(did):
        d = db.get_or_404(Document, did)
        if not d.editable:
            flash("Nur Entwürfe können bearbeitet werden.", "error")
            return redirect(url_for("document_view", did=did))
        customers_ = Customer.query.order_by(Customer.company, Customer.name).all()
        return render_template("document_edit.html", d=d, customers=customers_, products=catalog_json())

    @app.post("/dokument/speichern")
    @app.post("/dokument/<int:did>/speichern")
    def document_save(did=None):
        f = request.form
        if did:
            d = db.get_or_404(Document, did)
            if not d.editable:
                abort(400)
        else:
            d = Document(kind=f["kind"], status="draft", created_by=auth.current_username())
            db.session.add(d)
        d.customer_id = int(f["customer_id"])
        d.title = f.get("title", "").strip()
        d.issue_date = parse_date(f.get("issue_date")) or date.today()
        d.due_date = parse_date(f.get("due_date"))
        d.service_date = f.get("service_date", "").strip()
        d.intro = f.get("intro", "").replace("\r\n", "\n")
        d.footer = f.get("footer", "").replace("\r\n", "\n")
        d.small_business = f.get("small_business") == "1"
        if d.kind == DOC_INVOICE and f.get("skonto_on"):
            d.skonto_percent = parse_decimal(f.get("skonto_percent"), "0")
            d.skonto_days = int(f.get("skonto_days") or 0)
            if not (0 < d.skonto_percent < 100) or d.skonto_days <= 0:
                raise ValueError("Skonto: Prozentsatz zwischen 0 und 100 und mindestens 1 Tag angeben.")
        else:
            d.skonto_percent, d.skonto_days = 0, 0

        d.items.clear()
        descs = f.getlist("item_description")
        for pos, desc in enumerate(descs):
            qty = f.getlist("item_quantity")[pos]
            price = f.getlist("item_price")[pos]
            if not desc.strip() and not price.strip():
                continue
            pids = f.getlist("item_product_id")
            pid = pids[pos] if pos < len(pids) else ""
            d.items.append(DocumentItem(
                position=pos, description=desc.replace("\r\n", "\n").strip(), quantity=parse_decimal(qty),
                unit=f.getlist("item_unit")[pos].strip(), unit_price=parse_money(price),
                vat_rate=0 if d.small_business else int(f.getlist("item_vat")[pos] or 0),
                product_id=int(pid) if pid.isdigit() else None))
        if not d.items:
            db.session.rollback()
            flash("Mindestens eine Position angeben.", "error")
            return redirect(request.referrer)
        db.session.flush()
        auth.audit("Entwurf gespeichert", "document", d.id, f"{d.kind_label} · {d.customer.display_name} · {fmt_money(d.gross)}")
        db.session.commit()
        if f.get("action") == "finalize":
            return finalize(d)
        flash("Entwurf gespeichert.", "success")
        return redirect(url_for("document_view", did=d.id))

    def finalize(d):
        s = Settings.get()
        if d.kind == DOC_INVOICE and not d.service_date:
            flash("Für Rechnungen ist ein Leistungsdatum Pflicht (§ 14 UStG).", "error")
            return redirect(url_for("document_edit", did=d.id))
        prefix = s.invoice_prefix if d.is_invoice else s.offer_prefix
        d.number = next_number(d.kind, prefix, d.issue_date.year)
        d.status = "sent"
        d.finalized_by, d.finalized_at = auth.current_username(), datetime.now()
        auth.audit("Festgeschrieben", "document", d.id, f"{d.kind_label} {d.number} · {d.customer.display_name} · {fmt_money(d.gross)}")
        db.session.commit()
        flash(f"{d.kind_label} {d.number} wurde festgeschrieben.", "success")
        if d.is_invoice and s.paperless_archive_invoices and paperless_configured():
            archive_document(d)
        return redirect(url_for("document_view", did=d.id))

    def archive_document(d):
        s = Settings.get()
        try:
            msg = upload_to_paperless(
                d, f"{d.number}.pdf", build_invoice_pdf(d, s)[0], "application/pdf",
                title=f"{d.kind_label} {d.number} – {d.customer.display_name}",
                created=d.issue_date, tags=["Buchhaltung", "Ausgangsrechnung"],
                correspondent=d.customer.display_name, document_type=d.kind_label)
            auth.audit("An Paperless übergeben", "document", d.id, f"{d.kind_label} {d.number}")
            db.session.commit()
            flash(msg, "success")
        except PaperlessError as e:
            flash(str(e), "error")

    @app.post("/dokument/<int:did>/festschreiben")
    def document_finalize(did):
        d = db.get_or_404(Document, did)
        if not d.editable:
            abort(400)
        return finalize(d)

    @app.route("/dokument/<int:did>")
    def document_view(did):
        d = db.get_or_404(Document, did)
        resolve_paperless_task(d)
        invoices = Document.query.filter_by(offer_id=d.id).all() if not d.is_invoice else []
        storno = Document.query.filter_by(cancels_id=d.id).first()
        payments = Transaction.query.filter_by(document_id=d.id).all()
        s = Settings.get()
        einvoice_missing = missing_fields(s, d) if d.is_invoice else []
        return render_template("document_view.html", d=d, invoices=invoices, storno=storno,
                               payments=payments, einvoice_missing=einvoice_missing)

    @app.route("/dokument/<int:did>/senden", methods=["GET", "POST"])
    def document_send(did):
        d = db.get_or_404(Document, did)
        s = Settings.get()
        if d.status == "draft":
            flash("Bitte das Dokument zuerst festschreiben – Entwürfe haben noch keine Nummer.", "error")
            return redirect(url_for("document_view", did=did))
        missing = mailer.missing_settings(s)
        if missing:
            flash("E-Mail-Versand ist noch nicht eingerichtet (" + ", ".join(missing) + "). "
                  "Bitte unter Einstellungen → E-Mail-Versand ergänzen.", "error")
            return redirect(url_for("settings_view") + "#mail")
        pdf_name = f"{d.kind_label}_{d.number}.pdf".replace(" ", "_")
        reminder = request.values.get("art") == "erinnerung"
        if reminder and not (d.is_invoice and d.status == "sent"):
            flash("Erinnerungen sind nur für offene Rechnungen möglich.", "error")
            return redirect(url_for("document_view", did=did))
        if request.method == "POST" and reminder:
            f = request.form
            subject, body = f.get("subject", "").strip(), f.get("body", "").replace("\r\n", "\n")
            try:
                to, cc = mailer.parse_addresses(f.get("to")), mailer.parse_addresses(f.get("cc"))
                if not to:
                    raise mailer.MailError("Bitte mindestens einen Empfänger angeben.")
                label = mailer.reminder_label(d)
                send_reminder(d, s, to, cc, subject, body)
                flash(f"{label} zu {d.number} an {', '.join(to)} versendet.", "success")
                return redirect(url_for("document_view", did=did))
            except mailer.MailError as e:
                flash(str(e), "error")
                return render_template("document_send.html", d=d, to=f.get("to", ""), cc=f.get("cc", ""),
                                       subject=subject, body=body, pdf_name=pdf_name, attach_xml=False,
                                       einvoice=False, reminder=True)
        if request.method == "POST":
            f = request.form
            subject = f.get("subject", "").strip()
            body = f.get("body", "").replace("\r\n", "\n")
            log = EmailLog(document_id=d.id, subject=subject)
            try:
                to = mailer.parse_addresses(f.get("to"))
                cc = mailer.parse_addresses(f.get("cc"))
                pdf, _ = build_invoice_pdf(d, s)
                attachments = [(pdf_name, pdf, "application/pdf")]
                if f.get("attach_xml") and d.is_invoice and not missing_fields(s, d):
                    attachments.append((f"{d.number}_zugferd.xml", build_xml(d, s), "application/xml"))
                log.attachments = ", ".join(a[0] for a in attachments)
                log.recipients = ", ".join(to + cc)
                mailer.send(s, to, cc, subject, body, attachments)
                log.sent_by = auth.current_username()
                auth.audit("Per E-Mail versendet", "document", d.id, f"{d.number} an {', '.join(to + cc)}")
                if f.get("save_email") and to and not d.customer.email:
                    d.customer.email = mailer.bare(to[:1])[0]
                db.session.add(log)
                db.session.commit()
                flash(f"{d.kind_label} {d.number} an {', '.join(to)} versendet.", "success")
                return redirect(url_for("document_view", did=did))
            except mailer.MailError as e:
                if log.attachments:   # nur echte Versandversuche protokollieren, keine Eingabefehler
                    log.success = False
                    log.error = str(e)
                    db.session.add(log)
                    db.session.commit()
                flash(str(e), "error")
                return render_template("document_send.html", d=d, to=f.get("to", ""), cc=f.get("cc", ""),
                                       subject=subject, body=body, pdf_name=pdf_name,
                                       attach_xml=bool(f.get("attach_xml")),
                                       einvoice=d.is_invoice and s.einvoice_enabled and not missing_fields(s, d))
        subject, body = mailer.default_mail(d, s, reminder=reminder)
        return render_template("document_send.html", d=d, to=d.customer.email or "", cc="",
                               subject=subject, body=body, pdf_name=pdf_name, attach_xml=False,
                               einvoice=d.is_invoice and s.einvoice_enabled and not missing_fields(s, d),
                               reminder=reminder)

    @app.route("/dokument/<int:did>/xml")
    def document_xml(did):
        """Nur das ZUGFeRD-XML (CII) – z. B. für Empfänger, die die XML-Datei separat wünschen."""
        d = db.get_or_404(Document, did)
        s = Settings.get()
        if not d.is_invoice or d.status == "draft":
            abort(400)
        missing = missing_fields(s, d)
        if missing:
            flash("E-Rechnung nicht möglich, es fehlen: " + ", ".join(missing), "error")
            return redirect(url_for("document_view", did=did))
        return send_file(io.BytesIO(build_xml(d, s)), mimetype="application/xml",
                         as_attachment=True, download_name=f"{d.number}_zugferd.xml")

    @app.route("/dokument/<int:did>/pdf")
    def document_pdf(did):
        d = db.get_or_404(Document, did)
        data, _ = build_invoice_pdf(d, Settings.get())
        name = f"{d.kind_label}_{d.number or 'Entwurf-' + str(d.id)}.pdf".replace(" ", "_")
        return send_file(io.BytesIO(data), mimetype="application/pdf",
                         as_attachment=bool(request.args.get("download")), download_name=name)

    @app.post("/dokument/<int:did>/status")
    def document_status(did):
        d = db.get_or_404(Document, did)
        new = request.form["status"]
        allowed = {DOC_OFFER: {"sent", "accepted", "rejected"}}.get(d.kind, set())
        if d.status == "draft" or new not in allowed:
            abort(400)
        d.status = new
        auth.audit("Status geändert", "document", d.id, f"{d.kind_label} {d.number} → {d.status_label}")
        db.session.commit()
        flash(f"Status: {d.status_label}", "success")
        return redirect(url_for("document_view", did=did))

    @app.post("/dokument/<int:did>/in-rechnung")
    def offer_to_invoice(did):
        o = db.get_or_404(Document, did)
        if o.is_invoice or o.status == "draft":
            abort(400)
        s = Settings.get()
        inv = Document(kind=DOC_INVOICE, customer_id=o.customer_id, title=o.title,
                       issue_date=date.today(), due_date=date.today() + timedelta(days=s.payment_terms_days),
                       intro=s.invoice_intro, footer=s.invoice_footer,
                       small_business=s.small_business, offer_id=o.id, created_by=auth.current_username())
        inv.items = [DocumentItem(position=i.position, description=i.description, quantity=i.quantity,
                                  unit=i.unit, unit_price=i.unit_price, product_id=i.product_id,
                                  vat_rate=0 if s.small_business else (i.vat_rate or s.default_vat_rate))
                     for i in o.items]
        apply_skonto_defaults(inv, s)
        o.status = "invoiced"
        db.session.add(inv)
        db.session.flush()
        auth.audit("Rechnung aus Angebot erstellt", "document", inv.id, f"aus {o.number}")
        db.session.commit()
        flash("Rechnungsentwurf aus Angebot erstellt – bitte Leistungsdatum ergänzen.", "success")
        return redirect(url_for("document_edit", did=inv.id))

    @app.post("/dokument/<int:did>/duplizieren")
    def document_duplicate(did):
        o = db.get_or_404(Document, did)
        s = Settings.get()
        days = s.payment_terms_days if o.is_invoice else s.offer_valid_days
        d = Document(kind=o.kind, customer_id=o.customer_id, title=o.title, issue_date=date.today(),
                     due_date=date.today() + timedelta(days=days), intro=o.intro, footer=o.footer,
                     small_business=s.small_business, created_by=auth.current_username(),
                     skonto_percent=o.skonto_percent if not o.cancels_id else 0,
                     skonto_days=o.skonto_days if not o.cancels_id else 0)
        d.items = [DocumentItem(position=i.position, description=i.description, quantity=abs(i.quantity),
                                unit=i.unit, unit_price=abs(i.unit_price), product_id=i.product_id,
                                vat_rate=0 if s.small_business else (i.vat_rate or s.default_vat_rate))
                   for i in o.items]
        db.session.add(d)
        db.session.flush()
        auth.audit("Dupliziert", "document", d.id, f"Kopie von {o.display_number}")
        db.session.commit()
        flash("Kopie als Entwurf angelegt.", "success")
        return redirect(url_for("document_edit", did=d.id))

    @app.post("/dokument/<int:did>/loeschen")
    def document_delete(did):
        d = db.get_or_404(Document, did)
        if not d.editable:
            flash("Festgeschriebene Dokumente können nicht gelöscht werden (GoBD). Rechnungen bitte stornieren.", "error")
            return redirect(url_for("document_view", did=did))
        kind = d.kind
        if d.offer_id and d.offer and d.offer.status == "invoiced":
            d.offer.status = "accepted"
        auth.audit("Entwurf gelöscht", "document", None, f"{d.kind_label} Entwurf #{d.id} · {d.customer.display_name}")
        db.session.delete(d)
        db.session.commit()
        flash("Entwurf gelöscht.", "success")
        return redirect(url_for("documents", kind=kind))

    @app.post("/dokument/<int:did>/bezahlt")
    def invoice_paid(did):
        d = db.get_or_404(Document, did)
        if not d.is_invoice or d.status != "sent":
            abort(400)
        paid = parse_date(request.form.get("paid_date")) or date.today()
        d.status = "paid"
        d.paid_date = paid
        t = d.totals()
        storno = bool(d.cancels_id)
        label = f"{'Storno ' if storno else ''}Rechnung {d.number}"
        if d.small_business or not t["vat"]:
            rates = {0: t["gross"]}
        else:
            # Je Steuersatz eine Buchung (brutto); steuerfreie Positionen separat
            rates = {}
            for it in d.items:
                rates.setdefault(it.vat_rate or 0, 0)
                rates[it.vat_rate or 0] += it.line_total
            rates = {r: net + (t["vat"].get(r, 0) if r else 0) for r, net in rates.items()}
        with_skonto = bool(request.form.get("with_skonto")) and d.has_skonto
        if with_skonto:
            # Skonto mindert das Entgelt anteilig je Steuersatz; Rundungsrest auf den größten Posten
            total = sum(rates.values())
            target = total - d.skonto_amount
            scaled = {r: round(g * target / total) for r, g in rates.items()}
            biggest = max(scaled, key=lambda r: abs(scaled[r]))
            scaled[biggest] += target - sum(scaled.values())
            rates = scaled
            label += f" (abzgl. {d.skonto_percent_label} Skonto)"
        for rate, gross in rates.items():
            db.session.add(Transaction(
                kind="income", date=paid, category="Umsatzerlöse", description=label,
                counterparty=d.customer.display_name, gross_amount=gross, vat_rate=rate,
                payment_method=request.form.get("payment_method", "Überweisung"), document_id=d.id,
                paperless_document_id=d.paperless_document_id, created_by=auth.current_username()))
        auth.audit("Als bezahlt markiert", "document", d.id, f"{d.number} · {fmt_date(paid)} · {fmt_money(sum(rates.values()))}")
        db.session.commit()
        flash(f"Rechnung {d.number} als bezahlt markiert und als Einnahme gebucht.", "success")
        return redirect(url_for("document_view", did=did))

    @app.post("/dokument/<int:did>/offen")
    def invoice_unpaid(did):
        d = db.get_or_404(Document, did)
        if d.status != "paid":
            abort(400)
        Transaction.query.filter_by(document_id=d.id).delete()
        d.status = "sent"
        d.paid_date = None
        auth.audit("Zahlung zurückgenommen", "document", d.id, d.number)
        db.session.commit()
        flash("Zahlung zurückgenommen, zugehörige Einnahme gelöscht.", "success")
        return redirect(url_for("document_view", did=did))

    @app.post("/dokument/<int:did>/stornieren")
    def invoice_cancel(did):
        d = db.get_or_404(Document, did)
        if not d.is_invoice or d.status not in ("sent", "paid") or d.cancels_id:
            abort(400)
        s = Settings.get()
        storno = Document(kind=DOC_INVOICE, customer_id=d.customer_id, title=d.title,
                          issue_date=date.today(), service_date=d.service_date,
                          intro="", footer="", small_business=d.small_business, cancels_id=d.id,
                          status="sent", created_by=auth.current_username(), finalized_by=auth.current_username(),
                          finalized_at=datetime.now())
        storno.items = [DocumentItem(position=i.position, description=i.description,
                                     quantity=-i.quantity, unit=i.unit, unit_price=i.unit_price,
                                     vat_rate=i.vat_rate, product_id=i.product_id) for i in d.items]
        storno.number = next_number(DOC_INVOICE, s.invoice_prefix, storno.issue_date.year)
        db.session.add(storno)
        if d.status == "paid":
            # Bereits bezahlt: Storno wird als Rückzahlung erwartet (offen)
            flash("Die Rechnung war bereits bezahlt. Markiere die Stornorechnung als bezahlt, "
                  "sobald die Erstattung erfolgt ist.", "info")
        else:
            storno.status = "cancelled"
        d.status = "cancelled"
        db.session.flush()
        auth.audit("Storniert", "document", d.id, f"{d.number} → Stornorechnung {storno.number}")
        db.session.commit()
        flash(f"Rechnung {d.number} storniert, Stornorechnung {storno.number} erstellt.", "success")
        if s.paperless_archive_invoices and paperless_configured():
            archive_document(storno)
        return redirect(url_for("document_view", did=storno.id))

    @app.post("/dokument/<int:did>/paperless")
    def document_to_paperless(did):
        d = db.get_or_404(Document, did)
        if d.status == "draft":
            abort(400)
        archive_document(d)
        return redirect(url_for("document_view", did=did))

    # ------------------------------------------------------------------ Belegeingang (Paperless)
    @app.route("/belege")
    def inbox():
        status = request.args.get("status", "pending")
        items = (ReceiptInbox.query.filter_by(status=status)
                 .order_by(ReceiptInbox.doc_date.desc(), ReceiptInbox.id.desc()).all())
        return render_template("inbox.html", items=items, status=status,
                               expense_categories=EXPENSE_CATEGORIES, income_categories=INCOME_CATEGORIES)

    @app.post("/belege/abrufen")
    def inbox_sync():
        try:
            new = run_paperless_job()
            flash(f"{new} neue Belege aus Paperless übernommen." if new else "Keine neuen Belege in Paperless.",
                  "success")
        except Exception as e:
            flash(f"Abruf fehlgeschlagen: {e}", "error")
        return redirect(url_for("inbox"))

    @app.post("/belege/<int:rid>/buchen")
    def inbox_book(rid):
        r = db.get_or_404(ReceiptInbox, rid)
        if r.status != "pending":
            abort(400)
        f = request.form
        amount = parse_money(f.get("amount"))
        if amount <= 0:
            flash("Bitte den Betrag prüfen – er muss größer als 0 sein.", "error")
            return redirect(url_for("inbox") + f"#beleg-{rid}")
        t = Transaction(kind=f.get("kind", "expense"), date=parse_date(f.get("date")) or r.doc_date or date.today(),
                        category=f.get("category") or "Sonstige Ausgaben",
                        description=f.get("description", "").strip() or r.title,
                        counterparty=f.get("counterparty", "").strip(), gross_amount=amount,
                        vat_rate=int(f.get("vat_rate") or 0), payment_method=f.get("payment_method", "Überweisung"),
                        paperless_document_id=r.paperless_id, created_by=auth.current_username())
        db.session.add(t)
        db.session.flush()
        r.status, r.transaction_id = "booked", t.id
        auth.audit("Beleg gebucht", "transaction", t.id,
                   f"Paperless #{r.paperless_id} · {t.category} · {fmt_date(t.date)} · {fmt_money(t.gross_amount)}")
        db.session.commit()
        flash(f"Gebucht: {t.counterparty or t.description} – {fmt_money(t.gross_amount)}", "success")
        return redirect(url_for("inbox"))

    @app.post("/belege/<int:rid>/ignorieren")
    def inbox_ignore(rid):
        r = db.get_or_404(ReceiptInbox, rid)
        r.status = "ignored" if r.status == "pending" else "pending"
        auth.audit("Beleg ignoriert" if r.status == "ignored" else "Beleg zurück in Eingang", "receipt", r.id,
                   f"Paperless #{r.paperless_id} · {r.correspondent or r.title}")
        db.session.commit()
        flash("Beleg ignoriert." if r.status == "ignored" else "Beleg wieder in den Eingang gelegt.", "success")
        return redirect(url_for("inbox", status="ignored" if r.status == "pending" else None))

    # ------------------------------------------------------------------ Zahlungserinnerungen
    @app.route("/erinnerungen")
    def reminders():
        s = Settings.get()
        items = overdue_invoices()
        return render_template("reminders.html", items=items, due={d.id: reminder_due(d, s) for d in items})

    # ------------------------------------------------------------------ Datensicherung
    @app.post("/sicherung/jetzt")
    def backup_now():
        try:
            path, warning = run_backup_job()
            flash(f"Sicherung erstellt: {os.path.basename(path)}", "success")
            if warning:
                flash(warning, "error")
        except Exception as e:
            flash(f"Sicherung fehlgeschlagen: {e}", "error")
        return redirect(url_for("settings_view") + "#backup")

    @app.route("/sicherung/<name>")
    def backup_download(name):
        if name not in backup.list_backups(backup.backup_dir(DATA_DIR)):
            abort(404)
        return send_file(os.path.join(backup.backup_dir(DATA_DIR), name), as_attachment=True)

    # ------------------------------------------------------------------ Produkte & Leistungen
    @app.route("/katalog")
    def products():
        q = request.args.get("q", "").strip()
        kind = request.args.get("art", "")
        category = request.args.get("kategorie", "")
        show_inactive = bool(request.args.get("inaktiv"))
        query = Product.query
        if not show_inactive:
            query = query.filter_by(active=True)
        if kind:
            query = query.filter_by(kind=kind)
        if category:
            query = query.filter_by(category=category)
        if q:
            like = f"%{q}%"
            query = query.filter(db.or_(Product.name.ilike(like), Product.sku.ilike(like),
                                        Product.manufacturer.ilike(like), Product.description.ilike(like)))
        items = query.order_by(Product.kind.desc(), Product.category, Product.name).all()
        # Verkaufsstatistik aus festgeschriebenen, nicht stornierten Rechnungen
        stats = {}
        rows = (db.session.query(DocumentItem.product_id, func.count(DocumentItem.id),
                                 func.sum(DocumentItem.quantity))
                .join(Document).filter(Document.kind == DOC_INVOICE, Document.status.in_(("sent", "paid")),
                                       Document.cancels_id.is_(None), DocumentItem.product_id.isnot(None))
                .group_by(DocumentItem.product_id))
        for pid, count, qty in rows:
            stats[pid] = {"count": count, "qty": qty}
        categories = [c for (c,) in db.session.query(Product.category).distinct().order_by(Product.category) if c]
        return render_template("products.html", items=items, q=q, kind=kind, category=category,
                               categories=categories, stats=stats, show_inactive=show_inactive,
                               total=Product.query.count(), kinds=PRODUCT_KINDS)

    @app.route("/katalog/neu", methods=["GET", "POST"])
    @app.route("/katalog/<int:pid>", methods=["GET", "POST"])
    def product_edit(pid=None):
        s = Settings.get()
        p = db.get_or_404(Product, pid) if pid else Product(
            kind=request.args.get("art", "service"), active=True,
            unit="Std." if request.args.get("art", "service") == "service" else "Stk.",
            vat_rate=s.default_vat_rate)
        if request.method == "POST":
            f = request.form
            p.kind = f.get("kind", "service")
            for field in ("sku", "name", "description", "category", "manufacturer", "unit", "notes"):
                setattr(p, field, f.get(field, "").replace("\r\n", "\n").strip())
            p.unit_price = parse_money(f.get("unit_price"))
            p.purchase_price = parse_money(f.get("purchase_price"))
            p.vat_rate = int(f.get("vat_rate") or 19)
            p.active = bool(f.get("active"))
            if not p.name:
                flash("Bitte eine Bezeichnung angeben.", "error")
                return render_template("product_edit.html", p=p, categories=_categories(), kinds=PRODUCT_KINDS)
            if not p.id:
                db.session.add(p)
            db.session.flush()
            auth.audit("Katalogeintrag gespeichert", "product", p.id, f"{p.name} · {fmt_money(p.unit_price)}")
            db.session.commit()
            flash(f"„{p.name}“ gespeichert.", "success")
            if f.get("action") == "new":
                return redirect(url_for("product_edit", art=p.kind))
            return redirect(url_for("products"))
        usage = []
        if p.id:
            usage = (Document.query.join(DocumentItem).filter(DocumentItem.product_id == p.id)
                     .distinct().order_by(Document.issue_date.desc()).limit(20).all())
        return render_template("product_edit.html", p=p, categories=_categories(), kinds=PRODUCT_KINDS,
                               usage=usage)

    def _categories():
        return [c for (c,) in db.session.query(Product.category).distinct().order_by(Product.category) if c]

    @app.post("/katalog/<int:pid>/loeschen")
    def product_delete(pid):
        p = db.get_or_404(Product, pid)
        if DocumentItem.query.filter_by(product_id=p.id).first():
            p.active = False
            flash(f"„{p.name}“ wird in Dokumenten verwendet und wurde deshalb nur deaktiviert.", "info")
        else:
            db.session.delete(p)
            flash(f"„{p.name}“ gelöscht.", "success")
        auth.audit("Katalogeintrag gelöscht/deaktiviert", "product", p.id, p.name)
        db.session.commit()
        return redirect(url_for("products"))

    @app.post("/katalog/beispiele")
    def products_starter():
        s = Settings.get()
        if Product.query.count():
            abort(400)
        for kind, cat, name, desc, unit in STARTER_PRODUCTS:
            db.session.add(Product(kind=kind, category=cat, name=name, description=desc, unit=unit,
                                   unit_price=0, vat_rate=s.default_vat_rate, active=True))
        db.session.commit()
        flash("Beispiel-Einträge angelegt. Bitte Preise ergänzen und Hardware mit genauem Modell versehen.", "success")
        return redirect(url_for("products"))

    # ------------------------------------------------------------------ Einnahmen / Ausgaben
    @app.route("/buchungen")
    def transactions():
        year = request.args.get("jahr", type=int) or date.today().year
        kind = request.args.get("art", "")
        category = request.args.get("kategorie", "")
        query = Transaction.query.filter(extract("year", Transaction.date) == year)
        if kind:
            query = query.filter_by(kind=kind)
        if category:
            query = query.filter_by(category=category)
        items = query.order_by(Transaction.date.desc(), Transaction.id.desc()).all()
        for t in items:
            if t.paperless_task_id:
                resolve_paperless_task(t)
        income = sum(t.gross_amount for t in items if t.kind == "income")
        expense = sum(t.gross_amount for t in items if t.kind == "expense")
        return render_template("transactions.html", items=items, year=year, kind=kind,
                               category=category, years=year_choices(), income=income, expense=expense,
                               categories=INCOME_CATEGORIES + EXPENSE_CATEGORIES)

    @app.route("/buchungen/neu", methods=["GET", "POST"])
    @app.route("/buchungen/<int:tid>", methods=["GET", "POST"])
    def transaction_edit(tid=None):
        s = Settings.get()
        if tid:
            t = db.get_or_404(Transaction, tid)
        else:
            kind = request.args.get("art", "expense")
            t = Transaction(kind=kind, date=date.today(),
                            category="Umsatzerlöse" if kind == "income" else "",
                            vat_rate=0 if s.small_business else s.default_vat_rate)
        if request.method == "POST":
            f = request.form
            t.kind = f["kind"]
            t.date = parse_date(f.get("date")) or date.today()
            t.category = f.get("category", "").strip() or "Sonstige Ausgaben"
            t.description = f.get("description", "").strip()
            t.counterparty = f.get("counterparty", "").strip()
            t.gross_amount = parse_money(f.get("gross_amount"))
            t.vat_rate = int(f.get("vat_rate") or 0)
            t.payment_method = f.get("payment_method", "")
            t.notes = f.get("notes", "")
            pid = f.get("paperless_document_id", "").strip()
            t.paperless_document_id = int(pid) if pid.isdigit() else None
            if t.gross_amount <= 0:
                flash("Bitte einen Betrag größer 0 angeben.", "error")
                return render_template("transaction_edit.html", t=t,
                                       income_categories=INCOME_CATEGORIES,
                                       expense_categories=EXPENSE_CATEGORIES)
            new = not t.id
            if new:
                t.created_by = auth.current_username()
                db.session.add(t)
            db.session.flush()
            auth.audit("Buchung angelegt" if new else "Buchung geändert", "transaction", t.id,
                       f"{'Einnahme' if t.kind == 'income' else 'Ausgabe'} · {t.category} · {fmt_date(t.date)} · {fmt_money(t.gross_amount)}")
            db.session.commit()
            upload = request.files.get("receipt")
            if upload and upload.filename:
                try:
                    content = upload.read()
                    mimetype = upload.mimetype or mimetypes.guess_type(upload.filename)[0] or "application/octet-stream"
                    msg = upload_to_paperless(
                        t, upload.filename, content, mimetype,
                        title=f"{'Beleg' if t.kind == 'expense' else 'Einnahme'} {fmt_date(t.date)} – "
                              f"{t.counterparty or t.description or t.category}",
                        created=t.date,
                        tags=["Buchhaltung", "Ausgabe" if t.kind == "expense" else "Einnahme"],
                        correspondent=t.counterparty or None)
                    db.session.commit()
                    flash(msg, "success")
                except PaperlessError as e:
                    flash(f"Buchung gespeichert, aber Beleg-Upload fehlgeschlagen: {e}", "error")
            flash("Buchung gespeichert.", "success")
            if f.get("action") == "new":
                return redirect(url_for("transaction_edit", art=t.kind))
            return redirect(url_for("transactions", jahr=t.date.year))
        if t.id:
            resolve_paperless_task(t)
        return render_template("transaction_edit.html", t=t, income_categories=INCOME_CATEGORIES,
                               expense_categories=EXPENSE_CATEGORIES)

    @app.post("/buchungen/<int:tid>/loeschen")
    def transaction_delete(tid):
        t = db.get_or_404(Transaction, tid)
        if t.document_id and t.document and t.document.status == "paid":
            flash("Diese Einnahme gehört zu einer bezahlten Rechnung – bitte dort die Zahlung zurücknehmen.", "error")
            return redirect(url_for("transaction_edit", tid=tid))
        year = t.date.year
        auth.audit("Buchung gelöscht", "transaction", None,
                   f"{t.category} · {fmt_date(t.date)} · {fmt_money(t.gross_amount)} · {t.description}")
        db.session.delete(t)
        db.session.commit()
        flash("Buchung gelöscht.", "success")
        return redirect(url_for("transactions", jahr=year))

    # ------------------------------------------------------------------ Paperless
    @app.route("/paperless/suche")
    def paperless_search():
        try:
            client = paperless_client()
            results = client.search(request.args.get("q", ""))
        except PaperlessError as e:
            return jsonify({"error": str(e)}), 400
        return jsonify([{"id": r["id"], "title": r.get("title", ""),
                         "created": (r.get("created") or "")[:10],
                         "correspondent": r.get("correspondent")} for r in results])

    @app.route("/paperless/dokument/<int:pid>")
    def paperless_document(pid):
        """Liefert den Beleg aus Paperless durch (auch wenn Paperless nur intern erreichbar ist)."""
        try:
            content, ctype, disposition = paperless_client().download(pid)
        except PaperlessError as e:
            flash(str(e), "error")
            return redirect(request.referrer or url_for("transactions"))
        headers = {}
        if disposition:
            headers["Content-Disposition"] = disposition.replace("attachment", "inline")
        return Response(content, mimetype=ctype, headers=headers)

    @app.route("/paperless/link/<int:pid>")
    def paperless_link(pid):
        return redirect(paperless_client().document_url(pid))

    # ------------------------------------------------------------------ EÜR
    def euer_data(year):
        items = Transaction.query.filter(extract("year", Transaction.date) == year).order_by(Transaction.date).all()
        income_cats, expense_cats = {}, {}
        vat_collected = vat_paid = 0
        quarters = {q: {"vat": 0, "input": 0} for q in range(1, 5)}
        for t in items:
            q = (t.date.month - 1) // 3 + 1
            if t.kind == "income":
                income_cats[t.category] = income_cats.get(t.category, 0) + t.net_amount
                vat_collected += t.vat_amount
                quarters[q]["vat"] += t.vat_amount
            else:
                expense_cats[t.category] = expense_cats.get(t.category, 0) + t.net_amount
                vat_paid += t.vat_amount
                quarters[q]["input"] += t.vat_amount
        if vat_collected:
            income_cats["Vereinnahmte Umsatzsteuer"] = income_cats.get("Vereinnahmte Umsatzsteuer", 0) + vat_collected
        if vat_paid:
            expense_cats["Gezahlte Vorsteuer"] = expense_cats.get("Gezahlte Vorsteuer", 0) + vat_paid
        total_income = sum(income_cats.values())
        total_expense = sum(expense_cats.values())
        months = []
        for m in range(1, 13):
            inc = sum(t.gross_amount for t in items if t.kind == "income" and t.date.month == m)
            exp = sum(t.gross_amount for t in items if t.kind == "expense" and t.date.month == m)
            months.append({"month": m, "income": inc, "expense": exp})
        return {"items": items, "income_cats": sorted(income_cats.items()),
                "expense_cats": sorted(expense_cats.items(), key=lambda kv: -kv[1]),
                "total_income": total_income, "total_expense": total_expense,
                "profit": total_income - total_expense, "vat_collected": vat_collected,
                "vat_paid": vat_paid, "quarters": quarters, "months": months,
                "has_vat": bool(vat_collected or vat_paid)}

    @app.route("/euer")
    def euer():
        year = request.args.get("jahr", type=int) or date.today().year
        anlage = euer_form.compute(year, Settings.get().small_business)
        return render_template("euer.html", year=year, years=year_choices(), anlage=anlage, **euer_data(year))

    @app.route("/euer/<int:year>.csv")
    def euer_csv(year):
        data = euer_data(year)
        out = io.StringIO()
        w = csv.writer(out, delimiter=";")
        w.writerow(["Datum", "Art", "Kategorie", "Beschreibung", "Geschäftspartner", "Brutto",
                    "USt-Satz", "USt-Betrag", "Netto", "Zahlungsart", "Rechnung", "Paperless-ID"])
        for t in data["items"]:
            w.writerow([fmt_date(t.date), "Einnahme" if t.kind == "income" else "Ausgabe", t.category,
                        t.description, t.counterparty, fmt_money(t.gross_amount, False), f"{t.vat_rate} %",
                        fmt_money(t.vat_amount, False), fmt_money(t.net_amount, False), t.payment_method,
                        t.document.number if t.document else "", t.paperless_document_id or ""])
        w.writerow([])
        w.writerow(["Zusammenfassung EÜR", year])
        w.writerow(["Betriebseinnahmen"])
        for cat, amount in data["income_cats"]:
            w.writerow(["", cat, fmt_money(amount, False)])
        w.writerow(["", "Summe Betriebseinnahmen", fmt_money(data["total_income"], False)])
        w.writerow(["Betriebsausgaben"])
        for cat, amount in data["expense_cats"]:
            w.writerow(["", cat, fmt_money(amount, False)])
        w.writerow(["", "Summe Betriebsausgaben", fmt_money(data["total_expense"], False)])
        w.writerow(["", "Gewinn / Verlust", fmt_money(data["profit"], False)])
        # BOM, damit Excel Umlaute korrekt erkennt
        return Response("﻿" + out.getvalue(), mimetype="text/csv",
                        headers={"Content-Disposition": f"attachment; filename=EUER_{year}.csv"})

    # ------------------------------------------------------------------ Einstellungen
    @app.route("/einstellungen", methods=["GET", "POST"])
    def settings_view():
        s = Settings.get()
        if request.method == "POST":
            f = request.form
            for field in ("company_name", "owner_name", "street", "zip_code", "city", "country", "email",
                          "phone", "website", "tax_number", "vat_id", "bank_name", "iban", "bic",
                          "invoice_prefix", "offer_prefix", "offer_intro", "offer_footer",
                          "invoice_intro", "invoice_footer", "paperless_url",
                          "smtp_host", "smtp_user", "mail_from_name", "mail_from_address",
                          "mail_offer_subject", "mail_invoice_subject"):
                setattr(s, field, f.get(field, "").strip())
            s.mail_offer_body = f.get("mail_offer_body", "").replace("\r\n", "\n")
            s.mail_invoice_body = f.get("mail_invoice_body", "").replace("\r\n", "\n")
            s.smtp_port = int(f.get("smtp_port") or 587)
            s.smtp_security = f.get("smtp_security", "starttls")
            s.mail_bcc_self = bool(f.get("mail_bcc_self"))
            if f.get("smtp_password"):
                s.smtp_password = f["smtp_password"]
            if f.get("smtp_password_clear"):
                s.smtp_password = ""
            # Datensicherung, Belegeingang, Erinnerungen
            s.backup_dir = f.get("backup_dir", "").strip()
            s.backup_keep = max(int(f.get("backup_keep") or 30), 3)
            s.paperless_inbox_enabled = bool(f.get("paperless_inbox_enabled"))
            s.paperless_inbox_tag = f.get("paperless_inbox_tag", "").strip() or "Buchhaltung"
            s.reminder_auto = bool(f.get("reminder_auto"))
            s.reminder_days = max(int(f.get("reminder_days") or 7), 1)
            s.reminder_interval_days = max(int(f.get("reminder_interval_days") or 14), 1)
            s.reminder_max = max(int(f.get("reminder_max") or 2), 1)
            s.mail_reminder_subject = f.get("mail_reminder_subject", "").strip() or s.mail_reminder_subject
            if f.get("mail_reminder_body", "").strip():
                s.mail_reminder_body = f["mail_reminder_body"].replace("\r\n", "\n")
            if f.get("paperless_token", "").strip():
                s.paperless_token = f["paperless_token"].strip()
            if f.get("paperless_token_clear"):
                s.paperless_token = ""
            s.invoice_prefix = s.invoice_prefix or "RE"
            app_url = f.get("app_url", "").strip().rstrip("/")
            if app_url and not app_url.startswith(("http://", "https://")):
                app_url = "http://" + app_url
            s.app_url = app_url
            s.require_2fa = bool(f.get("require_2fa"))
            s.skonto_enabled = bool(f.get("skonto_enabled"))
            s.skonto_percent = parse_decimal(f.get("skonto_percent"), "2")
            s.skonto_days = max(int(f.get("skonto_days") or 5), 1)
            s.offer_prefix = s.offer_prefix or "AN"
            s.small_business = f.get("small_business") == "1"
            s.paperless_archive_invoices = bool(f.get("paperless_archive_invoices"))
            s.einvoice_enabled = bool(f.get("einvoice_enabled"))
            s.default_vat_rate = int(f.get("default_vat_rate") or 19)
            s.payment_terms_days = int(f.get("payment_terms_days") or 14)
            s.offer_valid_days = int(f.get("offer_valid_days") or 30)
            logo = request.files.get("logo")
            if logo and logo.filename:
                ext = os.path.splitext(logo.filename)[1].lower()
                content = logo.read()
                if ext not in (".png", ".jpg", ".jpeg", ".svg"):
                    flash("Logo bitte als PNG, JPG oder SVG hochladen.", "error")
                elif ext == ".svg" and b"<text" in content:
                    # Text in SVGs wird mit nicht eingebetteter Schrift gesetzt → kein gültiges PDF/A (ZUGFeRD)
                    flash("Dieses SVG enthält Text. Bitte den Text im Grafikprogramm in Pfade umwandeln "
                          "oder das Logo als PNG hochladen.", "error")
                else:
                    path = os.path.join(DATA_DIR, "logo" + ext)
                    with open(path, "wb") as fh:
                        fh.write(content)
                    s.logo_path = path
            if f.get("logo_remove"):
                s.logo_path = ""
            auth.audit("Einstellungen geändert", "settings", None)
            db.session.commit()
            flash("Einstellungen gespeichert.", "success")
            if f.get("action") == "test_mail":
                try:
                    to = mailer.send_test(s)
                    flash(f"Testnachricht an {to} versendet – bitte Posteingang prüfen.", "success")
                except mailer.MailError as e:
                    flash(str(e), "error")
            if f.get("action") == "test_paperless":
                try:
                    count = paperless_client().test()
                    flash(f"Verbindung zu Paperless erfolgreich – {count} Dokumente gefunden.", "success")
                except PaperlessError as e:
                    flash(str(e), "error")
            return redirect(url_for("settings_view"))
        backups = backup.list_backups(backup.backup_dir(DATA_DIR))
        return render_template("settings.html", s=s, backups=backups[:10], backup_count=len(backups))

    @app.route("/icon-<int:size>.png")
    def app_icon(size):
        """App-Symbol: eigenes aus data/icons/ (z. B. Firmenlogo), sonst das neutrale aus static/."""
        name = {192: "icon-192.png", 512: "icon-512.png", 180: "apple-touch-icon.png"}.get(size)
        if not name:
            abort(404)
        own = os.path.join(DATA_DIR, "icons", name)
        return send_file(own if os.path.exists(own) else os.path.join(app.static_folder, name), mimetype="image/png",
                         max_age=86400)

    @app.route("/logo")
    def logo():
        s = Settings.get()
        if not s.logo_path or not os.path.exists(s.logo_path):
            abort(404)
        return send_file(s.logo_path)


app = create_app()

if __name__ == "__main__":
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "5000"))
    app.run(host=host, port=port, debug=os.environ.get("DEBUG") == "1")
