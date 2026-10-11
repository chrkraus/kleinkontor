"""E-Mail-Versand von Angeboten und Rechnungen per SMTP."""
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr, formatdate, getaddresses, make_msgid

from datetime import date

import environment
from helpers import fmt_date, fmt_money


class MailError(Exception):
    pass


class _Keep(dict):
    """Unbekannte Platzhalter bleiben unverändert stehen statt einen Fehler auszulösen."""
    def __missing__(self, key):
        return "{" + key + "}"


def placeholders(doc, settings):
    c = doc.customer
    contact = c.name if c.name else ""
    return {
        "nummer": doc.number or "",
        "datum": fmt_date(doc.issue_date),
        "betrag": fmt_money(doc.gross),
        "faellig": fmt_date(doc.due_date),
        "gueltig_bis": fmt_date(doc.due_date),
        "kunde": c.display_name,
        "ansprechpartner": contact,
        "anrede": f"Guten Tag {contact}" if contact else "Guten Tag",
        "betreff": doc.title or "",
        "firma": settings.company_name or settings.owner_name,
        "name": settings.owner_name or settings.company_name,
        "tage_ueberfaellig": str(max((date.today() - doc.due_date).days, 0)) if doc.due_date else "0",
        "stufe": str((doc.reminder_count or 0) + 1),
        "erinnerung": reminder_label(doc),
        "skonto": _skonto_sentence(doc),
    }


def _skonto_sentence(doc):
    if not doc.has_skonto:
        return ""
    from pdf import skonto_text
    return skonto_text(doc)


def reminder_label(doc):
    level = (doc.reminder_count or 0) + 1
    return "Zahlungserinnerung" if level == 1 else f"{level}. Zahlungserinnerung"


def render_template_text(template, doc, settings):
    return (template or "").format_map(_Keep(placeholders(doc, settings)))


def default_mail(doc, settings, reminder=False):
    if reminder:
        return (render_template_text(settings.mail_reminder_subject, doc, settings),
                render_template_text(settings.mail_reminder_body, doc, settings))
    if doc.is_invoice:
        subject, body = settings.mail_invoice_subject, settings.mail_invoice_body
        if doc.has_skonto and "{skonto}" not in body:
            # Skonto-Hinweis vor den Gruß setzen, auch wenn die Vorlage den Platzhalter nicht enthält
            marker = "\n\nViele Grüße"
            body = body.replace(marker, "\n\n{skonto}" + marker, 1) if marker in body else body + "\n\n{skonto}"
    else:
        subject, body = settings.mail_offer_subject, settings.mail_offer_body
    if doc.cancels_id:
        subject = "Stornorechnung {nummer} – {firma}"
        body = ("{anrede},\n\nim Anhang finden Sie die Stornorechnung {nummer} zur Rechnung "
                f"{doc.cancels.number}.\n\nViele Grüße\n{{name}}\n{{firma}}")
    return render_template_text(subject, doc, settings), render_template_text(body, doc, settings)


def missing_settings(settings):
    missing = []
    if not settings.smtp_host:
        missing.append("SMTP-Server")
    if not (settings.mail_from_address or settings.smtp_user):
        missing.append("Absender-Adresse")
    return missing


def parse_addresses(value):
    """'a@b.de, Name <c@d.de>' → ['a@b.de', 'Name <c@d.de>'] (Anzeigenamen bleiben erhalten)."""
    pairs = [(n, a) for n, a in getaddresses([value or ""]) if a]
    bad = [a for _, a in pairs if "@" not in a or "." not in a.split("@")[-1] or " " in a]
    if bad or (value or "").strip() and not pairs:
        raise MailError("Ungültige E-Mail-Adresse: " + (", ".join(bad) or value))
    return [formataddr((n, a)) for n, a in pairs]


def bare(addresses):
    """Nur die reinen Adressen (für den SMTP-Envelope)."""
    return [a for _, a in getaddresses(addresses)]


def _connect(settings, timeout=20):
    host, port = settings.smtp_host.strip(), int(settings.smtp_port or 587)
    ctx = ssl.create_default_context()
    try:
        if settings.smtp_security == "ssl":
            server = smtplib.SMTP_SSL(host, port, timeout=timeout, context=ctx)
        else:
            server = smtplib.SMTP(host, port, timeout=timeout)
            server.ehlo()
            if settings.smtp_security == "starttls":
                server.starttls(context=ctx)
                server.ehlo()
        if settings.smtp_user:
            server.login(settings.smtp_user, settings.smtp_password or "")
        return server
    except smtplib.SMTPAuthenticationError as e:
        raise MailError("Anmeldung am Mailserver fehlgeschlagen – Benutzername/Passwort prüfen "
                        "(bei manchen Anbietern ist ein App-Passwort nötig).") from e
    except (smtplib.SMTPException, OSError) as e:
        raise MailError(f"Verbindung zum Mailserver fehlgeschlagen: {e}") from e


def build_message(settings, to, cc, subject, body, attachments):
    sender = settings.mail_from_address or settings.smtp_user
    msg = EmailMessage()
    msg["From"] = formataddr((settings.mail_from_name or settings.company_name or settings.owner_name or "", sender))
    msg["To"] = ", ".join(to)
    if cc:
        msg["Cc"] = ", ".join(cc)
    msg["Subject"] = subject
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain=sender.split("@")[-1])
    msg.set_content(body)
    for filename, data, mimetype in attachments:
        maintype, subtype = mimetype.split("/", 1)
        msg.add_attachment(data, maintype=maintype, subtype=subtype, filename=filename)
    return msg, sender


def send(settings, to, cc, subject, body, attachments, bcc_self=None):
    """Versendet die Mail. Gibt die Liste aller Empfänger zurück (inkl. BCC an sich selbst)."""
    if not to:
        raise MailError("Bitte mindestens einen Empfänger angeben.")
    if environment.IS_TEST:
        # Testumgebung: niemals an echte Empfänger – alles an die Testadresse umleiten
        target = environment.TEST_MAIL or settings.mail_from_address or settings.smtp_user
        body = (f"*** TESTUMGEBUNG – diese Mail wäre gegangen an: {', '.join(list(to) + list(cc))} ***\n\n" + body)
        subject, to, cc, bcc_self = f"[TEST] {subject}", [target], [], False
    msg, sender = build_message(settings, to, cc, subject, body, attachments)
    recipients = bare(list(to) + list(cc))
    if (settings.mail_bcc_self if bcc_self is None else bcc_self) and sender not in recipients:
        recipients.append(sender)          # BCC: nur im Envelope, nicht im Header
    server = _connect(settings)
    try:
        refused = server.send_message(msg, from_addr=sender, to_addrs=recipients)
    except smtplib.SMTPException as e:
        raise MailError(f"Versand fehlgeschlagen: {e}") from e
    finally:
        try:
            server.quit()
        except Exception:
            pass
    if refused:
        raise MailError("Folgende Empfänger wurden abgelehnt: " + ", ".join(refused))
    return recipients


def send_test(settings):
    sender = settings.mail_from_address or settings.smtp_user
    send(settings, [sender], [], "Testnachricht aus der Buchhaltung",
         "Diese Testnachricht bestätigt, dass der E-Mail-Versand funktioniert.", [])
    return sender
