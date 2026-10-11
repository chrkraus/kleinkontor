"""E-Rechnung (ZUGFeRD EN 16931). drafthorse prüft beim Serialisieren gegen das XSD."""
import io

from pypdf import PdfReader

from conftest import make_invoice
from einvoice import build_invoice_pdf, build_xml, parse_service_period
from models import Settings


def test_small_business_invoice_xml(admin, ctx):
    d = make_invoice(admin, [("Montage", "1", "100")], skonto=("2", "5"))
    xml = build_xml(d, Settings.get()).decode()
    assert "<ram:CategoryCode>E</ram:CategoryCode>" in xml
    assert "§ 19 UStG" in xml
    assert "#SKONTO#TAGE=5#PROZENT=2.00#" in xml
    assert "urn:cen.eu:en16931:2017" in xml


def test_invoice_pdf_embeds_xml(admin, ctx):
    d = make_invoice(admin, [("Montage", "1", "100")])
    pdf, zugferd = build_invoice_pdf(d, Settings.get())
    assert zugferd
    assert "factur-x.xml" in PdfReader(io.BytesIO(pdf)).attachments


def test_service_period_parsing():
    assert str(parse_service_period("01.09.–30.09.2026")[1]) == "2026-09-30"
    assert str(parse_service_period("Okt 2026")[0]) == "2026-10-01"
    assert parse_service_period("irgendwann") is None
