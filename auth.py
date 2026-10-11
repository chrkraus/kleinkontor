"""Benutzer, Einzelrechte und Protokoll."""
from flask import g, has_request_context, request

from models import AuditLog, db

# Schlüssel → Beschriftung (Reihenfolge = Anzeige in der Benutzerverwaltung)
PERMISSIONS = {
    "documents_view": "Angebote & Rechnungen ansehen (inkl. PDF)",
    "documents_edit": "Angebote & Rechnungen erstellen, festschreiben, stornieren, als bezahlt markieren",
    "documents_send": "Angebote & Rechnungen per E-Mail senden, Zahlungserinnerungen",
    "customers_edit": "Kunden anlegen und bearbeiten",
    "catalog_edit": "Produkte & Leistungen bearbeiten",
    "bookkeeping_view": "Einnahmen, Ausgaben und EÜR ansehen",
    "bookkeeping_edit": "Buchungen erfassen und Belegeingang buchen",
    "settings": "Einstellungen (Firma, E-Mail, Paperless, Datensicherung)",
}

PUBLIC = "public"     # ohne Anmeldung
LOGIN = "login"       # jeder angemeldete Benutzer
ADMIN = "admin"       # nur Admins

# Endpunkt → (Recht für GET, Recht für POST). Ein Tupel bedeutet „eines davon genügt“.
# Nicht aufgeführte Endpunkte sind nur für Admins erreichbar (sicherer Standard).
ENDPOINTS = {
    "static": (PUBLIC, PUBLIC),
    "app_icon": (PUBLIC, PUBLIC),
    "login": (PUBLIC, PUBLIC),
    "setup_password": (PUBLIC, PUBLIC),
    "password_forgot": (PUBLIC, PUBLIC),
    "password_reset": (PUBLIC, PUBLIC),
    "logout": (LOGIN, LOGIN),
    "dashboard": (LOGIN, LOGIN),
    "logo": (LOGIN, LOGIN),
    "account": (LOGIN, LOGIN),
    "login_2fa": (PUBLIC, PUBLIC),
    "twofa_setup": (LOGIN, LOGIN),
    "password_change_required": (LOGIN, LOGIN),

    "documents": ("documents_view", None),
    "document_view": ("documents_view", None),
    "document_pdf": ("documents_view", None),
    "document_xml": ("documents_view", None),
    "document_new": ("documents_edit", None),
    "document_edit": ("documents_edit", None),
    "document_save": (None, "documents_edit"),
    "document_finalize": (None, "documents_edit"),
    "document_delete": (None, "documents_edit"),
    "document_duplicate": (None, "documents_edit"),
    "document_status": (None, "documents_edit"),
    "offer_to_invoice": (None, "documents_edit"),
    "invoice_cancel": (None, "documents_edit"),
    "invoice_paid": (None, "documents_edit"),
    "invoice_unpaid": (None, "documents_edit"),
    "document_to_paperless": (None, "documents_edit"),
    "document_send": ("documents_send", "documents_send"),
    "reminders": (("documents_view", "documents_send"), None),

    "customers": (("documents_view", "customers_edit"), None),
    "customer_edit": (("documents_view", "customers_edit"), "customers_edit"),
    "customer_delete": (None, "customers_edit"),

    "products": (("catalog_edit", "documents_edit"), None),
    "product_edit": (("catalog_edit", "documents_edit"), "catalog_edit"),
    "product_delete": (None, "catalog_edit"),
    "products_starter": (None, "catalog_edit"),

    "transactions": ("bookkeeping_view", None),
    "transaction_edit": ("bookkeeping_view", "bookkeeping_edit"),
    "transaction_delete": (None, "bookkeeping_edit"),
    "euer": ("bookkeeping_view", None),
    "euer_csv": ("bookkeeping_view", None),
    "inbox": ("bookkeeping_view", None),
    "inbox_book": (None, "bookkeeping_edit"),
    "inbox_ignore": (None, "bookkeeping_edit"),
    "inbox_sync": (None, "bookkeeping_edit"),
    "paperless_document": (("bookkeeping_view", "documents_view"), None),
    "paperless_link": (("bookkeeping_view", "documents_view"), None),
    "paperless_search": ("bookkeeping_edit", None),

    "settings_view": ("settings", "settings"),
    "backup_now": (None, "settings"),
    "backup_download": ("settings", None),

    "users": (ADMIN, ADMIN),
    "user_edit": (ADMIN, ADMIN),
    "user_delete": (ADMIN, ADMIN),
    "audit": (ADMIN, ADMIN),
}


def required(endpoint, method):
    """Benötigtes Recht für einen Aufruf: PUBLIC, LOGIN, ADMIN, ein Schlüssel oder ein Tupel."""
    get_perm, post_perm = ENDPOINTS.get(endpoint, (ADMIN, ADMIN))
    perm = get_perm if method in ("GET", "HEAD") else post_perm
    return ADMIN if perm is None else perm


def allowed(user, perm):
    if perm == PUBLIC:
        return True
    if user is None:
        return False
    if perm == LOGIN:
        return True
    if perm == ADMIN:
        return user.is_admin
    if isinstance(perm, tuple):
        return any(user.can(p) for p in perm)
    return user.can(perm)


def current_username():
    user = getattr(g, "user", None) if has_request_context() else None
    return user.username if user else "system"


def audit(action, obj_type="", obj_id=None, details="", username=None):
    """Protokolleintrag – wird mit der nächsten Transaktion gespeichert."""
    db.session.add(AuditLog(
        username=username or current_username(), action=action, object_type=obj_type, object_id=obj_id,
        details=(details or "")[:500], ip=(request.remote_addr if has_request_context() else "") or ""))
