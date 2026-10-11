"""Datenmodell. Geldbeträge werden als Integer in Cent gespeichert."""
from datetime import date, datetime

from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()


class Settings(db.Model):
    """Firmendaten und Programmeinstellungen (genau eine Zeile)."""
    id = db.Column(db.Integer, primary_key=True)
    company_name = db.Column(db.String(200), default="")
    owner_name = db.Column(db.String(200), default="")
    street = db.Column(db.String(200), default="")
    zip_code = db.Column(db.String(20), default="")
    city = db.Column(db.String(100), default="")
    country = db.Column(db.String(100), default="Deutschland")
    email = db.Column(db.String(200), default="")
    phone = db.Column(db.String(100), default="")
    website = db.Column(db.String(200), default="")
    tax_number = db.Column(db.String(100), default="")      # Steuernummer
    vat_id = db.Column(db.String(100), default="")          # USt-IdNr.
    bank_name = db.Column(db.String(200), default="")
    iban = db.Column(db.String(50), default="")
    bic = db.Column(db.String(20), default="")
    logo_path = db.Column(db.String(300), default="")

    small_business = db.Column(db.Boolean, default=True)     # §19 UStG
    default_vat_rate = db.Column(db.Integer, default=19)
    payment_terms_days = db.Column(db.Integer, default=14)
    offer_valid_days = db.Column(db.Integer, default=30)
    invoice_prefix = db.Column(db.String(20), default="RE")
    skonto_enabled = db.Column(db.Boolean, default=False)          # Skonto für neue Rechnungen vorschlagen
    skonto_percent = db.Column(db.Numeric(5, 2), default=2)
    skonto_days = db.Column(db.Integer, default=5)
    offer_prefix = db.Column(db.String(20), default="AN")

    offer_intro = db.Column(db.Text, default="vielen Dank für Ihre Anfrage. Gerne unterbreite ich Ihnen folgendes Angebot:")
    offer_footer = db.Column(db.Text, default="Ich freue mich auf Ihre Rückmeldung.")
    invoice_intro = db.Column(db.Text, default="für meine Leistungen erlaube ich mir, Ihnen Folgendes in Rechnung zu stellen:")
    invoice_footer = db.Column(db.Text, default="Bitte überweisen Sie den Rechnungsbetrag unter Angabe der Rechnungsnummer bis zum {faellig} auf das unten angegebene Konto.")

    paperless_url = db.Column(db.String(300), default="")
    paperless_token = db.Column(db.String(300), default="")
    paperless_archive_invoices = db.Column(db.Boolean, default=False)

    einvoice_enabled = db.Column(db.Boolean, default=True)    # ZUGFeRD-PDF für Rechnungen

    # Anmeldung
    password_hash = db.Column(db.String(300), default="")

    app_url = db.Column(db.String(300), default="")             # Adresse der App für Links in E-Mails
    require_2fa = db.Column(db.Boolean, default=False)           # Zwei-Faktor für alle Benutzer verpflichtend

    # Datensicherung
    backup_dir = db.Column(db.String(300), default="")          # zusätzliches Ziel, z. B. NAS-Freigabe
    backup_keep = db.Column(db.Integer, default=30)              # Anzahl täglicher Sicherungen
    last_backup_at = db.Column(db.DateTime)
    last_backup_file = db.Column(db.String(300), default="")
    last_backup_error = db.Column(db.Text, default="")

    # Belegeingang aus Paperless
    paperless_inbox_enabled = db.Column(db.Boolean, default=True)
    paperless_inbox_tag = db.Column(db.String(100), default="Buchhaltung")
    last_paperless_sync_at = db.Column(db.DateTime)
    paperless_sync_error = db.Column(db.Text, default="")

    # Zahlungserinnerungen
    reminder_auto = db.Column(db.Boolean, default=False)
    reminder_days = db.Column(db.Integer, default=7)              # Tage nach Fälligkeit
    reminder_interval_days = db.Column(db.Integer, default=14)    # Abstand zwischen Erinnerungen
    reminder_max = db.Column(db.Integer, default=2)
    last_reminder_run = db.Column(db.Date)
    mail_reminder_subject = db.Column(db.String(300), default="{erinnerung}: Rechnung {nummer}")
    mail_reminder_body = db.Column(db.Text, default=(
        "{anrede},\n\nsicher ist es im Alltag untergegangen: Die Rechnung {nummer} vom {datum} über {betrag} "
        "war am {faellig} fällig. Bis heute konnte ich leider keinen Zahlungseingang feststellen.\n\n"
        "Bitte überweisen Sie den Betrag innerhalb der nächsten 7 Tage unter Angabe der Rechnungsnummer. "
        "Die Rechnung finden Sie zur Sicherheit noch einmal im Anhang.\n\n"
        "Sollte sich Ihre Zahlung mit dieser Nachricht überschnitten haben, betrachten Sie diese Erinnerung "
        "bitte als gegenstandslos.\n\nViele Grüße\n{name}\n{firma}"))

    # E-Mail-Versand (SMTP)
    smtp_host = db.Column(db.String(200), default="")
    smtp_port = db.Column(db.Integer, default=587)
    smtp_security = db.Column(db.String(20), default="starttls")   # starttls | ssl | none
    smtp_user = db.Column(db.String(200), default="")
    smtp_password = db.Column(db.String(300), default="")
    mail_from_name = db.Column(db.String(200), default="")
    mail_from_address = db.Column(db.String(200), default="")
    mail_bcc_self = db.Column(db.Boolean, default=True)
    mail_offer_subject = db.Column(db.String(300), default="Angebot {nummer} – {firma}")
    mail_offer_body = db.Column(db.Text, default=(
        "{anrede},\n\nvielen Dank für Ihre Anfrage. Im Anhang finden Sie mein Angebot {nummer} "
        "vom {datum} über {betrag}.\n\nDas Angebot ist gültig bis zum {gueltig_bis}. "
        "Bei Fragen melden Sie sich gerne jederzeit.\n\nViele Grüße\n{name}\n{firma}"))
    mail_invoice_subject = db.Column(db.String(300), default="Rechnung {nummer} – {firma}")
    mail_invoice_body = db.Column(db.Text, default=(
        "{anrede},\n\nvielen Dank für Ihren Auftrag. Im Anhang finden Sie die Rechnung {nummer} "
        "vom {datum} über {betrag}.\n\nBitte überweisen Sie den Betrag bis zum {faellig} unter Angabe "
        "der Rechnungsnummer auf das in der Rechnung genannte Konto.\n\nViele Grüße\n{name}\n{firma}"))

    @staticmethod
    def get():
        s = db.session.get(Settings, 1)
        if s is None:
            s = Settings(id=1)
            db.session.add(s)
            db.session.commit()
        return s


class NumberSequence(db.Model):
    """Fortlaufende Nummernkreise je Dokumenttyp und Jahr."""
    id = db.Column(db.Integer, primary_key=True)
    kind = db.Column(db.String(20), nullable=False)
    year = db.Column(db.Integer, nullable=False)
    last = db.Column(db.Integer, default=0, nullable=False)
    __table_args__ = (db.UniqueConstraint("kind", "year"),)


class Customer(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    customer_number = db.Column(db.String(30), unique=True)
    company = db.Column(db.String(200), default="")
    name = db.Column(db.String(200), default="")
    street = db.Column(db.String(200), default="")
    zip_code = db.Column(db.String(20), default="")
    city = db.Column(db.String(100), default="")
    country = db.Column(db.String(100), default="")
    email = db.Column(db.String(200), default="")
    phone = db.Column(db.String(100), default="")
    vat_id = db.Column(db.String(100), default="")
    buyer_reference = db.Column(db.String(100), default="")  # Käuferreferenz / Leitweg-ID (BT-10)
    notes = db.Column(db.Text, default="")
    created_at = db.Column(db.DateTime, default=datetime.now)

    documents = db.relationship("Document", back_populates="customer")

    @property
    def display_name(self):
        if self.company and self.name:
            return f"{self.company} ({self.name})"
        return self.company or self.name or f"Kunde #{self.id}"

    def address_lines(self):
        lines = [l for l in (self.company, self.name, self.street) if l]
        city = " ".join(p for p in (self.zip_code, self.city) if p)
        if city:
            lines.append(city)
        if self.country and self.country.lower() not in ("deutschland", "germany", "de"):
            lines.append(self.country)
        return lines


DOC_OFFER = "offer"
DOC_INVOICE = "invoice"

STATUS_LABELS = {
    "draft": "Entwurf",
    "sent": "Versendet",
    "accepted": "Angenommen",
    "rejected": "Abgelehnt",
    "invoiced": "Abgerechnet",
    "paid": "Bezahlt",
    "cancelled": "Storniert",
}


class Document(db.Model):
    """Angebot oder Rechnung."""
    id = db.Column(db.Integer, primary_key=True)
    kind = db.Column(db.String(20), nullable=False)          # offer | invoice
    number = db.Column(db.String(50), unique=True)           # wird beim Finalisieren vergeben
    status = db.Column(db.String(20), default="draft", nullable=False)
    customer_id = db.Column(db.Integer, db.ForeignKey("customer.id"), nullable=False)
    title = db.Column(db.String(200), default="")
    issue_date = db.Column(db.Date, default=date.today)
    service_date = db.Column(db.String(100), default="")     # Leistungsdatum/-zeitraum
    due_date = db.Column(db.Date)                            # Rechnung: fällig / Angebot: gültig bis
    intro = db.Column(db.Text, default="")
    footer = db.Column(db.Text, default="")
    small_business = db.Column(db.Boolean, default=True)     # Steuermodus zum Zeitpunkt der Erstellung
    paid_date = db.Column(db.Date)
    offer_id = db.Column(db.Integer, db.ForeignKey("document.id"))      # Rechnung aus Angebot
    cancels_id = db.Column(db.Integer, db.ForeignKey("document.id"))    # Stornorechnung zu ...
    paperless_document_id = db.Column(db.Integer)
    paperless_task_id = db.Column(db.String(100))
    reminder_count = db.Column(db.Integer, default=0)
    last_reminder_at = db.Column(db.DateTime)
    skonto_percent = db.Column(db.Numeric(5, 2), default=0)        # 0 = kein Skonto
    skonto_days = db.Column(db.Integer, default=0)
    created_by = db.Column(db.String(80), default="")
    finalized_by = db.Column(db.String(80), default="")
    finalized_at = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, default=datetime.now)

    customer = db.relationship("Customer", back_populates="documents")
    items = db.relationship("DocumentItem", back_populates="document",
                            order_by="DocumentItem.position", cascade="all, delete-orphan")
    offer = db.relationship("Document", remote_side=[id], foreign_keys=[offer_id])
    cancels = db.relationship("Document", remote_side=[id], foreign_keys=[cancels_id])

    @property
    def is_invoice(self):
        return self.kind == DOC_INVOICE

    @property
    def kind_label(self):
        if self.cancels_id:
            return "Stornorechnung"
        return "Rechnung" if self.is_invoice else "Angebot"

    @property
    def status_label(self):
        return STATUS_LABELS.get(self.status, self.status)

    @property
    def editable(self):
        return self.status == "draft"

    @property
    def display_number(self):
        return self.number or f"Entwurf #{self.id}"

    def totals(self):
        """Netto, Steuer je Satz und Brutto in Cent."""
        net = 0
        vat_by_rate = {}
        for it in self.items:
            line = it.line_total
            net += line
            if not self.small_business and it.vat_rate:
                vat_by_rate[it.vat_rate] = vat_by_rate.get(it.vat_rate, 0) + line
        vat = {rate: round(base * rate / 100) for rate, base in sorted(vat_by_rate.items())}
        return {"net": net, "vat": vat, "gross": net + sum(vat.values())}

    @property
    def gross(self):
        return self.totals()["gross"]

    @property
    def has_skonto(self):
        return bool(self.is_invoice and not self.cancels_id and self.skonto_percent and self.skonto_days
                    and self.gross > 0)

    @property
    def skonto_date(self):
        from datetime import timedelta
        return self.issue_date + timedelta(days=self.skonto_days or 0) if self.has_skonto else None

    @property
    def skonto_amount(self):
        """Skontobetrag in Cent (vom Bruttobetrag)."""
        return round(self.gross * float(self.skonto_percent or 0) / 100) if self.has_skonto else 0

    @property
    def skonto_percent_label(self):
        p = f"{float(self.skonto_percent or 0):.2f}".rstrip("0").rstrip(".")
        return p.replace(".", ",") + " %"

    @property
    def overdue(self):
        return (self.is_invoice and self.status == "sent" and self.due_date
                and self.due_date < date.today())


class DocumentItem(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    document_id = db.Column(db.Integer, db.ForeignKey("document.id"), nullable=False)
    position = db.Column(db.Integer, default=0)
    description = db.Column(db.Text, default="")
    quantity = db.Column(db.Numeric(12, 3), default=1)
    unit = db.Column(db.String(30), default="Stk.")
    unit_price = db.Column(db.Integer, default=0)            # Cent, netto
    vat_rate = db.Column(db.Integer, default=19)
    product_id = db.Column(db.Integer, db.ForeignKey("product.id"))   # Herkunft aus dem Katalog

    document = db.relationship("Document", back_populates="items")
    product = db.relationship("Product")

    @property
    def line_total(self):
        return round(float(self.quantity or 0) * (self.unit_price or 0))


PRODUCT_KINDS = {"product": "Produkt", "service": "Dienstleistung"}


class Product(db.Model):
    """Katalogeintrag: Hardware/Ware oder Dienstleistung mit Standardpreis."""
    id = db.Column(db.Integer, primary_key=True)
    kind = db.Column(db.String(20), default="service", nullable=False)   # product | service
    sku = db.Column(db.String(50), default="")                           # Artikelnummer
    name = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text, default="")                         # Zusatztext für Angebot/Rechnung
    category = db.Column(db.String(100), default="")
    manufacturer = db.Column(db.String(100), default="")
    unit = db.Column(db.String(30), default="Stk.")
    unit_price = db.Column(db.Integer, default=0)                        # Cent, netto (Kleinunternehmer: Endpreis)
    purchase_price = db.Column(db.Integer, default=0)                    # Cent, Einkaufspreis netto (optional)
    vat_rate = db.Column(db.Integer, default=19)
    active = db.Column(db.Boolean, default=True)
    notes = db.Column(db.Text, default="")                               # intern, erscheint nicht auf Dokumenten
    created_at = db.Column(db.DateTime, default=datetime.now)

    @property
    def kind_label(self):
        return PRODUCT_KINDS.get(self.kind, self.kind)

    @property
    def document_text(self):
        """Positionstext für Angebote/Rechnungen."""
        title = f"{self.manufacturer} {self.name}".strip() if self.manufacturer and not self.name.lower().startswith(self.manufacturer.lower()) else self.name
        return f"{title}\n{self.description}".strip() if self.description else title

    @property
    def margin(self):
        """Rohertrag je Einheit in Cent (nur wenn EK hinterlegt)."""
        return self.unit_price - self.purchase_price if self.purchase_price else None

    @property
    def margin_percent(self):
        if not self.purchase_price or not self.unit_price:
            return None
        return (self.unit_price - self.purchase_price) / self.unit_price * 100


INCOME_CATEGORIES = [
    "Umsatzerlöse",
    "Sonstige Einnahmen",
    "Vereinnahmte Umsatzsteuer",
    "Umsatzsteuer-Erstattung",
]

EXPENSE_CATEGORIES = [
    "Waren / Material",
    "Fremdleistungen",
    "Büromaterial",
    "Software / Lizenzen",
    "Telefon / Internet",
    "Porto",
    "Raumkosten / Miete",
    "Fahrtkosten",
    "Fahrten mit Privat-Pkw (0,30 €/km)",
    "Reisekosten",
    "Bewirtung",
    "Werbung / Marketing",
    "Fortbildung",
    "Versicherungen / Beiträge",
    "Kontoführung / Gebühren",
    "Beratung / Steuerberatung",
    "Abschreibungen (AfA)",
    "Geringwertige Wirtschaftsgüter (GWG)",
    "Gezahlte Vorsteuer",
    "An das Finanzamt gezahlte USt",
    "Sonstige Ausgaben",
]


class Transaction(db.Model):
    """Einnahme oder Ausgabe (Zufluss-/Abflussprinzip für die EÜR)."""
    id = db.Column(db.Integer, primary_key=True)
    kind = db.Column(db.String(10), nullable=False)          # income | expense
    date = db.Column(db.Date, nullable=False, default=date.today)
    category = db.Column(db.String(100), nullable=False)
    description = db.Column(db.String(300), default="")
    counterparty = db.Column(db.String(200), default="")
    gross_amount = db.Column(db.Integer, nullable=False)     # Cent
    vat_rate = db.Column(db.Integer, default=0)
    payment_method = db.Column(db.String(50), default="Überweisung")
    document_id = db.Column(db.Integer, db.ForeignKey("document.id"))
    paperless_document_id = db.Column(db.Integer)
    paperless_task_id = db.Column(db.String(100))
    created_by = db.Column(db.String(80), default="")
    notes = db.Column(db.Text, default="")
    created_at = db.Column(db.DateTime, default=datetime.now)

    document = db.relationship("Document")

    @property
    def vat_amount(self):
        if not self.vat_rate:
            return 0
        return round(self.gross_amount * self.vat_rate / (100 + self.vat_rate))

    @property
    def net_amount(self):
        return self.gross_amount - self.vat_amount


class EmailLog(db.Model):
    """Versandprotokoll für Angebote und Rechnungen."""
    id = db.Column(db.Integer, primary_key=True)
    document_id = db.Column(db.Integer, db.ForeignKey("document.id"), nullable=False)
    sent_at = db.Column(db.DateTime, default=datetime.now)
    recipients = db.Column(db.String(500), default="")
    subject = db.Column(db.String(300), default="")
    attachments = db.Column(db.String(500), default="")
    kind = db.Column(db.String(20), default="document")          # document | reminder
    sent_by = db.Column(db.String(80), default="")
    automatic = db.Column(db.Boolean, default=False)
    success = db.Column(db.Boolean, default=True)
    error = db.Column(db.Text, default="")

    document = db.relationship("Document", backref=db.backref("emails", order_by="EmailLog.sent_at.desc()"))


class ReceiptInbox(db.Model):
    """Beleg aus Paperless, der noch gebucht werden muss."""
    id = db.Column(db.Integer, primary_key=True)
    paperless_id = db.Column(db.Integer, unique=True, nullable=False)
    title = db.Column(db.String(300), default="")
    correspondent = db.Column(db.String(200), default="")
    document_type = db.Column(db.String(100), default="")
    doc_date = db.Column(db.Date)
    amount = db.Column(db.Integer)                 # erkannter Bruttobetrag in Cent (None = nicht erkannt)
    vat_rate = db.Column(db.Integer, default=0)
    category = db.Column(db.String(100), default="")
    kind = db.Column(db.String(10), default="expense")
    excerpt = db.Column(db.Text, default="")       # Textstelle, aus der der Betrag stammt
    status = db.Column(db.String(20), default="pending")   # pending | booked | ignored
    transaction_id = db.Column(db.Integer, db.ForeignKey("transaction.id"))
    created_at = db.Column(db.DateTime, default=datetime.now)

    transaction = db.relationship("Transaction")


class User(db.Model):
    """Benutzer mit Einzelrechten. Admins dürfen alles."""
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)     # klein geschrieben
    display_name = db.Column(db.String(120), default="")
    email = db.Column(db.String(200), default="")                       # für „Passwort vergessen“
    password_hash = db.Column(db.String(300), nullable=False)
    is_admin = db.Column(db.Boolean, default=False)
    active = db.Column(db.Boolean, default=True)
    permissions = db.Column(db.String(500), default="")                  # kommagetrennte Schlüssel
    created_at = db.Column(db.DateTime, default=datetime.now)
    last_login_at = db.Column(db.DateTime)
    # Zwei-Faktor-Anmeldung (TOTP)
    must_change_password = db.Column(db.Boolean, default=False)   # bei nächster Anmeldung eigenes Passwort setzen
    totp_secret = db.Column(db.String(64), default="")
    totp_enabled = db.Column(db.Boolean, default=False)
    totp_last_step = db.Column(db.Integer, default=0)          # verhindert Wiederverwendung eines Codes
    recovery_codes = db.Column(db.Text, default="")            # gehashte Einmal-Codes, durch \n getrennt

    @property
    def name(self):
        return self.display_name or self.username

    @property
    def permission_set(self):
        return {p for p in (self.permissions or "").split(",") if p}

    def can(self, perm):
        return self.is_admin or perm in self.permission_set


class AuditLog(db.Model):
    """Protokoll: wer hat wann was getan (nur anhängen, nie ändern)."""
    id = db.Column(db.Integer, primary_key=True)
    at = db.Column(db.DateTime, default=datetime.now, index=True)
    username = db.Column(db.String(80), default="")
    action = db.Column(db.String(60), nullable=False)
    object_type = db.Column(db.String(30), default="")
    object_id = db.Column(db.Integer)
    details = db.Column(db.String(500), default="")
    ip = db.Column(db.String(60), default="")
