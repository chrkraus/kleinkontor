"""Formatierung und Parsing für deutsche Zahlen- und Datumsformate."""
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from models import NumberSequence, db


def fmt_money(cents, currency=True):
    if cents is None:
        cents = 0
    neg = cents < 0
    cents = abs(int(cents))
    euros, rest = divmod(cents, 100)
    s = f"{euros:,}".replace(",", ".") + f",{rest:02d}"
    if neg:
        s = "-" + s
    return f"{s} €" if currency else s


def fmt_date(d):
    if not d:
        return ""
    return d.strftime("%d.%m.%Y")


def fmt_qty(q):
    q = Decimal(str(q or 0)).normalize()
    s = f"{q:f}"
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return s.replace(".", ",")


def parse_money(value):
    """'1.234,56' / '1234.56' / '12' -> Cent."""
    if value is None:
        return 0
    s = str(value).strip().replace("€", "").replace(" ", "")
    if not s:
        return 0
    if "," in s:
        s = s.replace(".", "").replace(",", ".")
    try:
        return int((Decimal(s) * 100).quantize(Decimal("1")))
    except InvalidOperation:
        raise ValueError(f"Ungültiger Betrag: {value}")


def parse_decimal(value, default="1"):
    s = str(value or "").strip().replace(" ", "")
    if not s:
        s = default
    if "," in s:
        s = s.replace(".", "").replace(",", ".")
    try:
        return Decimal(s)
    except InvalidOperation:
        raise ValueError(f"Ungültige Zahl: {value}")


def parse_date(value):
    if not value:
        return None
    if isinstance(value, date):
        return value
    for fmt in ("%Y-%m-%d", "%d.%m.%Y"):
        try:
            return datetime.strptime(value.strip(), fmt).date()
        except ValueError:
            pass
    raise ValueError(f"Ungültiges Datum: {value}")


def next_number(kind, prefix, year):
    """Vergibt die nächste lückenlose Nummer, z. B. RE-2026-0001."""
    seq = NumberSequence.query.filter_by(kind=kind, year=year).first()
    if seq is None:
        seq = NumberSequence(kind=kind, year=year, last=0)
        db.session.add(seq)
    seq.last += 1
    db.session.flush()
    return f"{prefix}-{year}-{seq.last:04d}" if prefix else f"{year}-{seq.last:04d}"


def next_customer_number():
    seq = NumberSequence.query.filter_by(kind="customer", year=0).first()
    if seq is None:
        seq = NumberSequence(kind="customer", year=0, last=10000)
        db.session.add(seq)
    seq.last += 1
    db.session.flush()
    return f"K{seq.last}"
