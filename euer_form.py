"""Ausfüllhilfe Anlage EÜR: ordnet die Buchungen den Zeilen des amtlichen Vordrucks zu.

Quellen: BMF-Schreiben vom 29.08.2025 (Anlage EÜR 2025) und vom 01.09.2026 (Anlage EÜR 2026).
Zeilennummern ändern sich von Jahr zu Jahr – für ein neues Jahr eine Spalte in FIELDS ergänzen
und gegen den Vordruck prüfen.
"""
from models import Transaction
from sqlalchemy import extract

FORM_YEARS = (2025, 2026)

# Schlüssel: (Bezeichnung im Vordruck, Kennzahl, {Jahr: Zeile})
FIELDS = {
    "ku":          ("Betriebseinnahmen als umsatzsteuerlicher Kleinunternehmer (§ 19 Abs. 1 UStG)", "111", {2025: 12, 2026: 12}),
    "taxable":     ("Umsatzsteuerpflichtige Betriebseinnahmen (netto)", "112", {2025: 15, 2026: 15}),
    "exempt":      ("Umsatzsteuerfreie oder nicht umsatzsteuerbare Betriebseinnahmen", "103", {2025: 16, 2026: 16}),
    "vat_in":      ("Vereinnahmte Umsatzsteuer", "140", {2025: 17, 2026: 17}),
    "vat_refund":  ("Vom Finanzamt erstattete Umsatzsteuer", "141", {2025: 18, 2026: 18}),
    "sum_in":      ("Summe Betriebseinnahmen", "159", {2025: 23, 2026: 23}),
    "goods":       ("Waren, Rohstoffe und Hilfsstoffe einschließlich der Nebenkosten", "100", {2025: 27, 2026: 29}),
    "services":    ("Bezogene Fremdleistungen", "110", {2025: 29, 2026: 30}),
    "afa":         ("AfA auf bewegliche Wirtschaftsgüter (aus Anlage AVEÜR)", "130", {2025: 33, 2026: 34}),
    "gwg":         ("Aufwendungen für geringwertige Wirtschaftsgüter (§ 6 Abs. 2 EStG)", "132", {2025: 36, 2026: 37}),
    "rent":        ("Miete/Pacht für Geschäftsräume", "150", {2025: 39, 2026: 40}),
    "telecom":     ("Aufwendungen für Telekommunikation (Telefon, Internet)", "280", {2025: 43, 2026: 44}),
    "travel":      ("Übernachtungs- und Reisenebenkosten bei Geschäftsreisen", "221", {2025: 44, 2026: 45}),
    "training":    ("Fortbildungskosten (ohne Reisekosten)", "281", {2025: 45, 2026: 46}),
    "advice":      ("Kosten für Rechts- und Steuerberatung, Buchführung", "194", {2025: 46, 2026: 47}),
    "insurance":   ("Beiträge, Gebühren, Abgaben und Versicherungen (ohne Gebäude und Kfz)", "223", {2025: 49, 2026: 50}),
    "edv":         ("Laufende EDV-Kosten (Beratung, Wartung, Reparatur, Software)", "228", {2025: 50, 2026: 51}),
    "material":    ("Arbeitsmittel (Bürobedarf, Porto, Fachliteratur)", "229", {2025: 51, 2026: 52}),
    "ads":         ("Werbekosten", "224", {2025: 54, 2026: 55}),
    "vat_input":   ("Gezahlte und nach § 15 UStG abziehbare Vorsteuerbeträge", "185", {2025: 57, 2026: 58}),
    "vat_paid":    ("An das Finanzamt gezahlte Umsatzsteuer", "186", {2025: 58, 2026: 59}),
    "other":       ("Übrige unbeschränkt abziehbare Betriebsausgaben", "183", {2025: 60, 2026: 61}),
    "hospitality": ("Bewirtungsaufwendungen – abziehbar (70 %)", "175", {2025: 63, 2026: 64}),
    "hosp_nd":     ("Bewirtungsaufwendungen – nicht abziehbar (30 %)", "165", {2025: 63, 2026: 64}),
    "car":         ("Sonstige tatsächliche Fahrtkosten (Reparatur, Treibstoff, ÖPNV …)", "146", {2025: 70, 2026: 71}),
    "car_private": ("Fahrtkosten für nicht zum Betriebsvermögen gehörende Fahrzeuge (Nutzungseinlage)", "147", {2025: 71, 2026: 72}),
    "sum_out":     ("Summe Betriebsausgaben", "199", {2025: 75, 2026: 76}),
    "profit":      ("Steuerpflichtiger Gewinn/Verlust", "219", {2025: 97, 2026: 98}),
}

# Kategorie der App → Feld im Vordruck (Ausgaben)
EXPENSE_MAP = {
    "Waren / Material": "goods",
    "Fremdleistungen": "services",
    "Büromaterial": "material",
    "Software / Lizenzen": "edv",
    "Telefon / Internet": "telecom",
    "Porto": "material",
    "Raumkosten / Miete": "rent",
    "Fahrtkosten": "car",
    "Fahrten mit Privat-Pkw (0,30 €/km)": "car_private",
    "Reisekosten": "travel",
    "Bewirtung": "hospitality",
    "Werbung / Marketing": "ads",
    "Fortbildung": "training",
    "Versicherungen / Beiträge": "insurance",
    "Kontoführung / Gebühren": "other",
    "Beratung / Steuerberatung": "advice",
    "Abschreibungen (AfA)": "afa",
    "Geringwertige Wirtschaftsgüter (GWG)": "gwg",
    "Gezahlte Vorsteuer": "vat_input",
    "An das Finanzamt gezahlte USt": "vat_paid",
    "Sonstige Ausgaben": "other",
}

HINTS = {
    "ku": "Als Kleinunternehmer den tatsächlich vereinnahmten Gesamtbetrag eintragen. Die Zeilen 15 bis 17 bleiben leer.",
    "afa": "Abschreibungen müssen zusätzlich in der Anlage AVEÜR aufgeführt werden.",
    "hospitality": "Bewirtungen sind nur zu 70 % abziehbar. Beide Beträge stehen in derselben Zeile: links der nicht abziehbare, rechts der abziehbare Anteil.",
    "car_private": "Fahrten mit dem privaten Pkw: 0,30 € je gefahrenem Kilometer.",
    "gwg": "Wirtschaftsgüter bis 800 € netto, die im Jahr der Anschaffung voll abgeschrieben werden.",
}


def line(key, year):
    lines = FIELDS[key][2]
    return lines.get(year) or lines[max(lines)]


def form_year(year):
    """Vordruckjahr, dessen Zeilennummern verwendet werden (nächstälteres, sonst ältestes)."""
    older = [y for y in FORM_YEARS if y <= year]
    return max(older) if older else min(FORM_YEARS)


def compute(year, small_business_default):
    """Summen je Vordruckzeile für ein Kalenderjahr (Zuflussprinzip)."""
    fy = form_year(year)
    amounts = {k: 0 for k in FIELDS}
    sources = {k: set() for k in FIELDS}
    unmapped = {}

    def add(key, cents, source):
        amounts[key] += cents
        sources[key].add(source)

    items = Transaction.query.filter(extract("year", Transaction.date) == year).all()
    for t in items:
        if t.kind == "income":
            if t.category == "Vereinnahmte Umsatzsteuer":
                add("vat_in", t.gross_amount, t.category)
            elif t.category == "Umsatzsteuer-Erstattung":
                add("vat_refund", t.gross_amount, t.category)
            elif t.vat_rate:
                add("taxable", t.net_amount, t.category)
                add("vat_in", t.vat_amount, f"USt aus {t.category}")
            else:
                small = t.document.small_business if t.document else small_business_default
                add("ku" if small else "exempt", t.gross_amount, t.category)
        else:
            key = EXPENSE_MAP.get(t.category)
            if key is None:
                unmapped[t.category] = unmapped.get(t.category, 0) + t.net_amount
                key = "other"
            if key == "hospitality":
                deductible = round(t.net_amount * 0.7)
                add("hospitality", deductible, t.category)
                add("hosp_nd", t.net_amount - deductible, t.category)
            elif key in ("vat_input", "vat_paid"):
                add(key, t.gross_amount, t.category)
            else:
                add(key, t.net_amount, t.category)
            if t.vat_rate:
                add("vat_input", t.vat_amount, f"Vorsteuer aus {t.category}")

    income_keys = ["ku", "taxable", "exempt", "vat_in", "vat_refund"]
    expense_keys = [k for k in FIELDS if k not in income_keys + ["sum_in", "sum_out", "profit", "hosp_nd"]]
    amounts["sum_in"] = sum(amounts[k] for k in income_keys)
    # Nicht abziehbarer Bewirtungsanteil zählt nicht zur Summe der Betriebsausgaben
    amounts["sum_out"] = sum(amounts[k] for k in expense_keys)
    amounts["profit"] = amounts["sum_in"] - amounts["sum_out"]

    def rows(keys):
        out = []
        for k in keys:
            if not amounts[k] and k not in ("sum_in", "sum_out", "profit"):
                continue
            label, kz, _ = FIELDS[k]
            out.append({"key": k, "line": line(k, fy), "kz": kz, "label": label, "amount": amounts[k],
                        "sources": sorted(sources[k]), "hint": HINTS.get(k, "")})
        return sorted(out, key=lambda r: (r["line"], r["key"] == "hospitality"))

    return {
        "form_year": fy,
        "income": rows(income_keys),
        "expenses": rows([k for k in expense_keys] + ["hosp_nd"]),
        "sum_in": {"line": line("sum_in", fy), "kz": FIELDS["sum_in"][1], "amount": amounts["sum_in"]},
        "sum_out": {"line": line("sum_out", fy), "kz": FIELDS["sum_out"][1], "amount": amounts["sum_out"]},
        "profit": {"line": line("profit", fy), "kz": FIELDS["profit"][1], "amount": amounts["profit"]},
        "unmapped": unmapped,
        "count": len(items),
    }
