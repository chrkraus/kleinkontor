"""Rechnungen: Nummernkreis, Unveränderbarkeit (GoBD), Storno, Zahlungseingang, Skonto."""
from conftest import make_invoice
from models import Document, Transaction


def test_finalize_assigns_gapless_numbers(admin, ctx):
    a = make_invoice(admin, [("Montage", "1", "100")])
    b = make_invoice(admin, [("Montage", "1", "50")])
    na, nb = int(a.number.split("-")[-1]), int(b.number.split("-")[-1])
    assert a.number.startswith("RE-2026-") and nb == na + 1
    assert a.status == "sent" and a.finalized_by == "admin"


def test_draft_has_no_number_and_can_be_deleted(admin, ctx):
    d = make_invoice(admin, [("Entwurf", "1", "10")], finalize=False)
    assert d.number is None and d.editable
    assert admin.post(f"/dokument/{d.id}/loeschen").status_code == 302
    assert Document.query.get(d.id) is None


def test_finalized_invoice_is_immutable(admin, ctx):
    d = make_invoice(admin, [("Montage", "1", "100")])
    assert admin.post(f"/dokument/{d.id}/speichern", data={"kind": "invoice"}).status_code == 400
    admin.post(f"/dokument/{d.id}/loeschen")
    assert Document.query.get(d.id) is not None


def test_invoice_requires_service_date(admin, ctx):
    d = make_invoice(admin, [("X", "1", "10")], service_date="")
    assert d.number is None


def test_cancel_creates_credit_note(admin, ctx):
    d = make_invoice(admin, [("Montage", "2", "100")])
    admin.post(f"/dokument/{d.id}/stornieren")
    storno = Document.query.filter_by(cancels_id=d.id).one()
    assert Document.query.get(d.id).status == "cancelled"
    assert storno.number and storno.gross == -d.gross


def test_payment_with_skonto_books_reduced_amount(admin, ctx):
    d = make_invoice(admin, [("Beratung", "2", "100", "19"), ("Buch", "1", "20", "7")],
                     small_business=False, skonto=("2", "5"))
    assert d.gross == 25940 and d.skonto_amount == 519
    admin.post(f"/dokument/{d.id}/bezahlt", data={"paid_date": "2026-10-03", "with_skonto": "1"})
    booked = Transaction.query.filter_by(document_id=d.id).all()
    assert sum(t.gross_amount for t in booked) == 25940 - 519
    assert {t.vat_rate for t in booked} == {19, 7}
