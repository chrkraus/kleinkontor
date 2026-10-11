"""E-Rechnung: ZUGFeRD 2.x / Factur-X im Profil EN 16931 (XML eingebettet in PDF/A-3)."""
import calendar
import re
from datetime import date, datetime
from decimal import Decimal

from drafthorse.models.accounting import ApplicableTradeTax, BillingSpecifiedPeriod
from drafthorse.models.document import Document as XDocument
from drafthorse.models.note import IncludedNote
from drafthorse.models.party import TaxRegistration
from drafthorse.models.payment import PaymentMeans, PaymentTerms
from drafthorse.models.references import InvoiceReferencedDocument
from drafthorse.models.tradelines import LineItem
from drafthorse.pdf import attach_xml

from pdf import render_document

PROFILE = "EN 16931"
GUIDELINE = "urn:cen.eu:en16931:2017"
SMALL_BUSINESS_REASON = "Kleinunternehmer gemäß § 19 UStG"

# Einheiten nach UN/ECE Recommendation 20
UNIT_CODES = {
    "std": "HUR", "std.": "HUR", "stunde": "HUR", "stunden": "HUR", "h": "HUR",
    "stk": "H87", "stk.": "H87", "stück": "H87",
    "tag": "DAY", "tage": "DAY", "tag(e)": "DAY",
    "monat": "MON", "monate": "MON", "monat(e)": "MON",
    "km": "KMT", "kg": "KGM", "m": "MTR", "m²": "MTK", "l": "LTR",
    "pauschal": "LS", "psch.": "LS", "pausch.": "LS",
}

COUNTRY_CODES = {
    "deutschland": "DE", "germany": "DE", "österreich": "AT", "austria": "AT",
    "schweiz": "CH", "switzerland": "CH", "frankreich": "FR", "france": "FR",
    "niederlande": "NL", "netherlands": "NL", "belgien": "BE", "luxemburg": "LU",
    "italien": "IT", "spanien": "ES", "polen": "PL", "tschechien": "CZ",
    "dänemark": "DK", "schweden": "SE", "vereinigtes königreich": "GB", "großbritannien": "GB",
    "usa": "US", "vereinigte staaten": "US",
}


def country_code(name):
    n = (name or "").strip()
    if not n:
        return "DE"
    if len(n) == 2 and n.isalpha():
        return n.upper()
    return COUNTRY_CODES.get(n.lower(), "DE")


def unit_code(unit):
    return UNIT_CODES.get((unit or "").strip().lower(), "C62")  # C62 = Einheit/Stück


def money(cents):
    return (Decimal(int(cents)) / 100).quantize(Decimal("0.01"))


def _parse_de_date(s, default_year=None):
    m = re.fullmatch(r"(\d{1,2})\.(\d{1,2})\.(\d{2,4})?", s.strip())
    if not m:
        return None
    year = m.group(3) or default_year
    if not year:
        return None
    year = int(year)
    if year < 100:
        year += 2000
    try:
        return date(year, int(m.group(2)), int(m.group(1)))
    except ValueError:
        return None


MONTHS = {name: i for i, name in enumerate(
    ["januar", "februar", "märz", "april", "mai", "juni", "juli", "august",
     "september", "oktober", "november", "dezember"], start=1)}


def parse_service_period(text):
    """Leistungsdatum als (start, end) – bei einem Einzeldatum ist start == end."""
    t = (text or "").strip().lower()
    if not t:
        return None
    d = _parse_de_date(t)
    if d:
        return d, d
    m = re.fullmatch(r"(\S+)\s*(?:-|–|—|bis)\s*(\S+)", t)
    if m:
        end = _parse_de_date(m.group(2))
        if end:
            start = _parse_de_date(m.group(1)) or _parse_de_date(m.group(1).rstrip(".") + ".", end.year)
            if start and start <= end:
                return start, end
    m = re.fullmatch(r"([a-zä]+)\.?\s+(\d{4})", t) or re.fullmatch(r"(\d{1,2})[./](\d{4})", t)
    if m:
        name = m.group(1)
        month = (int(name) if name.isdigit()
                 else MONTHS.get(name) or next((i for n, i in MONTHS.items() if len(name) >= 3 and n.startswith(name)), None))
        if month and 1 <= month <= 12:
            year = int(m.group(2))
            return date(year, month, 1), date(year, month, calendar.monthrange(year, month)[1])
    return None


def missing_fields(settings, doc=None):
    """Pflichtangaben, ohne die keine gültige E-Rechnung entsteht."""
    s = settings
    missing = []
    if not (s.company_name or s.owner_name):
        missing.append("Firmenname oder Inhaber")
    if not (s.street and s.zip_code and s.city):
        missing.append("Anschrift (Straße, PLZ, Ort)")
    if not (s.tax_number or s.vat_id):
        missing.append("Steuernummer oder USt-IdNr.")
    if doc is not None:
        if not (doc.customer.company or doc.customer.name):
            missing.append("Name des Kunden")
        if not doc.customer.city:
            missing.append("Ort des Kunden")
    return missing


def _tax_category(doc, rate):
    if doc.small_business:
        return "E"
    return "S" if rate else "Z"


def build_xml(doc, settings):
    """Erzeugt das ZUGFeRD-XML (CII) und prüft es gegen das XSD."""
    s = settings
    credit_note = bool(doc.cancels_id)
    sign = -1 if credit_note else 1   # Stornos werden als Gutschrift mit positiven Beträgen ausgegeben
    totals = doc.totals()

    x = XDocument()
    x.context.guideline_parameter.id = GUIDELINE
    x.header.id = doc.number
    x.header.type_code = "381" if credit_note else "380"
    x.header.issue_date_time = doc.issue_date
    if doc.small_business:
        x.header.notes.add(IncludedNote(content="Gemäß § 19 UStG wird keine Umsatzsteuer berechnet."))
    if doc.service_date:
        x.header.notes.add(IncludedNote(content=f"Leistungsdatum: {doc.service_date}"))
    if doc.title:
        x.header.notes.add(IncludedNote(content=doc.title))

    # Verkäufer
    seller = x.trade.agreement.seller
    if not s.vat_id:
        # BR-CO-26: ohne USt-IdNr. braucht der Verkäufer eine Kennung – die Steuernummer
        seller.id = s.tax_number
    seller.name = s.company_name or s.owner_name
    if s.owner_name:
        seller.contact.person_name = s.owner_name
    if s.phone:
        seller.contact.telephone.number = s.phone
    if s.email:
        seller.contact.email.address = s.email
        seller.electronic_address.uri_ID = ("EM", s.email)
    seller.address.line_one = s.street
    seller.address.postcode = s.zip_code
    seller.address.city_name = s.city
    seller.address.country_id = country_code(s.country)
    if s.vat_id:
        seller.tax_registrations.add(TaxRegistration(id=("VA", s.vat_id)))
    if s.tax_number:
        seller.tax_registrations.add(TaxRegistration(id=("FC", s.tax_number)))

    # Käufer
    c = doc.customer
    buyer = x.trade.agreement.buyer
    buyer.name = c.company or c.name
    if c.customer_number:
        buyer.id = c.customer_number
    if c.company and c.name:
        buyer.contact.person_name = c.name
    if c.email:
        buyer.electronic_address.uri_ID = ("EM", c.email)
    buyer.address.line_one = c.street or ""
    buyer.address.postcode = c.zip_code or ""
    buyer.address.city_name = c.city or ""
    buyer.address.country_id = country_code(c.country)
    if c.vat_id:
        buyer.tax_registrations.add(TaxRegistration(id=("VA", c.vat_id)))
    if getattr(c, "buyer_reference", None):
        x.trade.agreement.buyer_reference = c.buyer_reference

    # Leistungsdatum / -zeitraum
    period = parse_service_period(doc.service_date)
    if period and period[0] == period[1]:
        x.trade.delivery.event.occurrence = period[0]
    elif period:
        x.trade.settlement.period.start = period[0]
        x.trade.settlement.period.end = period[1]

    # Positionen
    for n, it in enumerate(doc.items, start=1):
        rate = 0 if doc.small_business else (it.vat_rate or 0)
        li = LineItem()
        li.document.line_id = str(n)
        li.product.name = (it.description or "Position").split("\n")[0][:200]
        if "\n" in (it.description or ""):
            li.product.description = it.description
        li.agreement.net.amount = money(it.unit_price)
        li.delivery.billed_quantity = (Decimal(str(it.quantity)) * sign, unit_code(it.unit))
        li.settlement.trade_tax.type_code = "VAT"
        li.settlement.trade_tax.category_code = _tax_category(doc, rate)
        li.settlement.trade_tax.rate_applicable_percent = Decimal(rate)
        li.settlement.monetary_summation.total_amount = money(it.line_total * sign)
        x.trade.items.add(li)

    # Steueraufschlüsselung je Kategorie/Satz
    bases = {}
    for it in doc.items:
        rate = 0 if doc.small_business else (it.vat_rate or 0)
        bases[rate] = bases.get(rate, 0) + it.line_total
    tax_sum = 0
    for rate, base in sorted(bases.items()):
        vat = totals["vat"].get(rate, 0) if rate else 0
        tax_sum += vat
        t = ApplicableTradeTax()
        t.calculated_amount = money(vat * sign)
        t.basis_amount = money(base * sign)
        t.type_code = "VAT"
        category = _tax_category(doc, rate)
        t.category_code = category
        if category == "E":
            t.exemption_reason = SMALL_BUSINESS_REASON
        t.rate_applicable_percent = Decimal(rate)
        x.trade.settlement.trade_tax.add(t)

    # Zahlung
    x.trade.settlement.currency_code = "EUR"
    x.trade.settlement.payment_reference = doc.number
    if s.iban:
        pm = PaymentMeans()
        pm.type_code = "58"  # SEPA-Überweisung
        pm.payee_account.iban = s.iban.replace(" ", "")
        pm.payee_account.account_name = s.company_name or s.owner_name
        if s.bic:
            pm.payee_institution.bic = s.bic.replace(" ", "")
        x.trade.settlement.payment_means.add(pm)
    if not credit_note:
        terms = PaymentTerms()
        if doc.due_date:
            terms.description = f"Zahlbar bis {doc.due_date.strftime('%d.%m.%Y')} ohne Abzug."
            terms.due = doc.due_date
        else:
            terms.description = "Zahlbar sofort ohne Abzug."
        if doc.has_skonto:
            # Deutsche Konvention (XRechnung, auch von ZUGFeRD-Empfängern ausgewertet):
            # je Skontostufe eine Zeile „#SKONTO#TAGE=n#PROZENT=x.xx#“, abgeschlossen mit Zeilenumbruch
            terms.description = (f"{terms.description} Bei Zahlung bis {doc.skonto_date.strftime('%d.%m.%Y')} "
                                 f"{doc.skonto_percent_label} Skonto.\n"
                                 f"#SKONTO#TAGE={doc.skonto_days}#PROZENT={float(doc.skonto_percent):.2f}#\n")
        x.trade.settlement.terms.add(terms)
    else:
        ref = doc.cancels
        x.trade.settlement.invoice_referenced_document.issuer_assigned_id = ref.number
        x.trade.settlement.invoice_referenced_document.issue_date_time = ref.issue_date

    net = sum(bases.values())
    ms = x.trade.settlement.monetary_summation
    ms.line_total = money(net * sign)
    ms.charge_total = money(0)
    ms.allowance_total = money(0)
    ms.tax_basis_total = (money(net * sign), None)
    ms.tax_total = (money(tax_sum * sign), "EUR")
    ms.grand_total = (money((net + tax_sum) * sign), None)
    ms.prepaid_total = money(0)
    ms.due_amount = money((net + tax_sum) * sign)

    return x.serialize(schema="FACTUR-X_EN16931")


def build_invoice_pdf(doc, settings):
    """PDF für ein Dokument. Festgeschriebene Rechnungen werden als ZUGFeRD-PDF ausgegeben,
    sofern aktiviert und alle Pflichtangaben vorhanden sind. Gibt (pdf_bytes, ist_zugferd) zurück."""
    use_zugferd = (doc.is_invoice and doc.number and doc.status != "draft"
                   and settings.einvoice_enabled and not missing_fields(settings, doc))
    pdf = render_document(doc, settings, pdfa=use_zugferd)
    if not use_zugferd:
        return pdf, False
    xml = build_xml(doc, settings)
    seller = settings.company_name or settings.owner_name
    meta = {
        "author": seller,
        "title": f"{doc.kind_label} {doc.number}",
        "subject": f"{doc.kind_label} {doc.number} vom {doc.issue_date.strftime('%d.%m.%Y')} – {seller}",
        "keywords": f"{doc.kind_label}, ZUGFeRD, Factur-X",
        "creator": "Kleinkontor",
        "producer": "Kleinkontor",
    }
    return attach_xml(pdf, xml, level=PROFILE, metadata=meta, lang="de-DE"), True
