"""Belegeingang: holt markierte Dokumente aus Paperless und erzeugt Buchungsvorschläge."""
import re
from collections import Counter
from datetime import date

from models import Document, ReceiptInbox, Transaction, db

# Schlüsselwörter für den Endbetrag – vorne = verlässlicher. OCR-Varianten (ü→u) sind berücksichtigt.
TOTAL_KEYWORDS = [
    "rechnungsbetrag", "gesamtbetrag", "endbetrag", "zahlbetrag", "zu zahlen", "zu zahlender betrag",
    "gesamtsumme", "summe brutto", "gesamt brutto", "bruttobetrag", "total", "gesamt", "summe", "betrag",
]
AMOUNT_RE = re.compile(r"(?<![\d,.])(\d{1,3}(?:[.\s]\d{3})+|\d+),(\d{2})(?!\d)")

# Kategorievorschläge anhand von Korrespondent/Text (erste Übereinstimmung gewinnt)
CATEGORY_RULES = [
    (r"ionos|telekom|vodafone|o2|1&1|congstar|strato|hetzner|all-inkl", "Telefon / Internet"),
    (r"adobe|microsoft|google|jetbrains|github|dropbox|apple|lizenz|abonnement|software", "Software / Lizenzen"),
    (r"steuerberat|lohnsteuerhilfe", "Beratung / Steuerberatung"),
    (r"versicherung|haftpflicht|ihk|handwerkskammer|gewerbe|gemeinde|stadt|verwaltung|landratsamt|gebühr|gebuhr|beitrag",
     "Versicherungen / Beiträge"),
    (r"deutsche post|dhl|hermes|dpd|gls|porto|briefmarke", "Porto"),
    (r"aral|shell|esso|jet tankstelle|tankstelle|total energies|kraftstoff|diesel|super e10", "Fahrtkosten"),
    (r"ubiquiti|ui\.com|reolink|tp-link|netgear|mikrotik|avm|reichelt|conrad|kabel|patch|switch|access point",
     "Waren / Material"),
    (r"amazon|otto|mediamarkt|saturn|cyberport|alternate|mindfactory", "Waren / Material"),
    (r"restaurant|gasthof|gaststätte|bewirtung|café|cafe|pizzeria", "Bewirtung"),
    (r"hotel|übernachtung|booking\.com|bahn|db fernverkehr", "Reisekosten"),
    (r"bank|kontoführung|kontofuhrung|sparkasse|volksbank|paypal gebühr", "Kontoführung / Gebühren"),
    (r"büro|buro|papier|toner|druckerpatrone|ordner", "Büromaterial"),
]


def parse_amount(text):
    """Erkennt den Endbetrag. Gibt (Cent oder None, Textstelle) zurück."""
    lines = (text or "").splitlines()
    best = None   # (Rang, Betrag, Zeile)
    for line in lines:
        low = line.lower()
        amounts = AMOUNT_RE.findall(line)
        if not amounts:
            continue
        for rank, kw in enumerate(TOTAL_KEYWORDS):
            if kw in low:
                euros, cents = amounts[-1]
                value = int(re.sub(r"[.\s]", "", euros)) * 100 + int(cents)
                if best is None or rank < best[0] or (rank == best[0] and value > best[1]):
                    best = (rank, value, line.strip())
                break
    if best:
        return best[1], best[2]
    # Fallback: größter Betrag im Dokument
    found = [(int(re.sub(r"[.\s]", "", e)) * 100 + int(c), l.strip())
             for l in lines for e, c in AMOUNT_RE.findall(l)]
    if found:
        value, line = max(found)
        return value, line
    return None, ""


def parse_vat(text):
    low = (text or "").lower()
    if re.search(r"19[,.]?0{0,2}\s?%", low):
        return 19
    if re.search(r"(?<!\d)7[,.]?0{0,2}\s?%", low):
        return 7
    return 0


def guess_category(correspondent, text, title=""):
    """1. aus früheren Buchungen lernen, 2. Regeln auf Absender/Titel, 3. Regeln auf den Belegtext."""
    if correspondent:
        previous = [t.category for t in Transaction.query.filter(
            Transaction.kind == "expense", Transaction.counterparty.ilike(correspondent)).all()]
        if previous:
            return Counter(previous).most_common(1)[0][0]
    head = f"{correspondent}\n{title}".lower()
    for pattern, category in CATEGORY_RULES:
        if re.search(pattern, head):
            return category
    # Im Fließtext nur am Wortanfang suchen („Bankingsoftware“ ist keine Software-Ausgabe)
    body = (text or "").lower()
    for pattern, category in CATEGORY_RULES:
        if re.search(rf"\b(?:{pattern})", body):
            return category
    return "Sonstige Ausgaben"


def _parse_date(value):
    try:
        return date.fromisoformat((value or "")[:10])
    except ValueError:
        return None


def sync(client, tag_name, small_business):
    """Holt neue Dokumente mit dem Tag aus Paperless. Gibt die Anzahl neuer Einträge zurück."""
    tags = client._request("GET", "/api/tags/", params={"name__iexact": tag_name}).json().get("results", [])
    if not tags:
        raise ValueError(f"Tag „{tag_name}“ gibt es in Paperless nicht.")
    tag_id = tags[0]["id"]
    out_tags = client._request("GET", "/api/tags/", params={"name__iexact": "Ausgangsrechnung"}).json().get("results", [])
    out_tag_id = out_tags[0]["id"] if out_tags else None

    known = {r.paperless_id for r in ReceiptInbox.query.all()}
    known |= {pid for (pid,) in db.session.query(Transaction.paperless_document_id)
              .filter(Transaction.paperless_document_id.isnot(None))}
    known |= {pid for (pid,) in db.session.query(Document.paperless_document_id)
              .filter(Document.paperless_document_id.isnot(None))}

    names = {}
    def name_of(endpoint, obj_id):
        if not obj_id:
            return ""
        key = (endpoint, obj_id)
        if key not in names:
            try:
                names[key] = client._request("GET", f"/api/{endpoint}/{obj_id}/").json().get("name", "")
            except Exception:
                names[key] = ""
        return names[key]

    new = 0
    url, params = "/api/documents/", {"tags__id__all": tag_id, "page_size": 100, "ordering": "-added"}
    while url:
        page = client._request("GET", url, params=params).json()
        for d in page.get("results", []):
            if d["id"] in known or (out_tag_id and out_tag_id in d.get("tags", [])):
                continue
            content = d.get("content") or ""
            correspondent = name_of("correspondents", d.get("correspondent"))
            amount, excerpt = parse_amount(content)
            db.session.add(ReceiptInbox(
                paperless_id=d["id"], title=d.get("title") or "", correspondent=correspondent,
                document_type=name_of("document_types", d.get("document_type")),
                doc_date=_parse_date(d.get("created")), amount=amount, excerpt=excerpt[:300],
                vat_rate=0 if small_business else parse_vat(content),
                category=guess_category(correspondent, content, d.get("title") or ""), kind="expense"))
            known.add(d["id"])
            new += 1
        nxt = page.get("next")
        url = nxt[nxt.index("/api/"):] if nxt else None
        params = None
    db.session.commit()
    return new
