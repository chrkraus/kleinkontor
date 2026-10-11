"""PDF-Erzeugung für Angebote und Rechnungen (Layout angelehnt an DIN 5008)."""
import os

from fpdf import FPDF
from fpdf.enums import OutputIntentSubType
from fpdf.fonts import FontFace
from fpdf.output import PDFICCProfile
from fpdf.svg import SVGObject
from PIL import Image, ImageCms

import environment
from helpers import fmt_date, fmt_money, fmt_qty

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
FONT_DIR = os.path.join(BASE_DIR, "fonts")

GREY = (110, 110, 110)
ACCENT = (40, 70, 110)
LEFT = 25
RIGHT_MARGIN = 20


class DocPDF(FPDF):
    def __init__(self, settings):
        super().__init__(format="A4", unit="mm")
        self.s = settings
        self.add_font("DejaVu", "", os.path.join(FONT_DIR, "DejaVuSans.ttf"))
        self.add_font("DejaVu", "B", os.path.join(FONT_DIR, "DejaVuSans-Bold.ttf"))
        self.add_font("DejaVu", "I", os.path.join(FONT_DIR, "DejaVuSans-Oblique.ttf"))
        self.set_margins(LEFT, 20, RIGHT_MARGIN)
        self.set_auto_page_break(True, margin=38)
        self.alias_nb_pages()

    def header(self):
        if environment.IS_TEST:
            # Großes Wasserzeichen „TESTUMGEBUNG“ diagonal über die Seite
            self.set_font("DejaVu", "B", 54)
            self.set_text_color(235, 200, 200)
            with self.rotation(35, x=self.w / 2, y=self.h / 2):
                self.text(self.w / 2 - 95, self.h / 2, "TESTUMGEBUNG")
            self.set_text_color(0, 0, 0)

    def footer(self):
        s = self.s
        self.set_y(-30)
        self.set_draw_color(200, 200, 200)
        self.line(LEFT, self.get_y(), self.w - RIGHT_MARGIN, self.get_y())
        self.ln(2)
        self.set_font("DejaVu", "", 7)
        self.set_text_color(*GREY)
        col_w = (self.w - LEFT - RIGHT_MARGIN) / 3
        cols = [
            [s.company_name or s.owner_name,
             s.owner_name if s.company_name else "",
             s.street, f"{s.zip_code} {s.city}".strip()],
            [f"Tel.: {s.phone}" if s.phone else "",
             f"E-Mail: {s.email}" if s.email else "",
             s.website,
             ],
            [s.bank_name,
             f"IBAN: {s.iban}" if s.iban else "",
             f"BIC: {s.bic}" if s.bic else "",
             f"Steuernr.: {s.tax_number}" if s.tax_number else "",
             f"USt-IdNr.: {s.vat_id}" if s.vat_id else ""],
        ]
        y0 = self.get_y()
        for i, lines in enumerate(cols):
            self.set_xy(LEFT + i * col_w, y0)
            self.multi_cell(col_w, 3.4, "\n".join(l for l in lines if l))
        self.set_xy(LEFT, -10)
        self.cell(0, 4, f"Seite {self.page_no()} von {{nb}}", align="R")
        self.set_text_color(0, 0, 0)


def _logo_box(path, max_w, max_h):
    """Breite/Höhe in mm, damit das Logo seitenverhältnistreu in max_w × max_h passt."""
    if path.lower().endswith(".svg"):
        svg = SVGObject.from_file(path)
        iw, ih = svg.width or svg.viewbox[2], svg.height or svg.viewbox[3]
    else:
        with Image.open(path) as im:
            iw, ih = im.size
    scale = min(max_w / iw, max_h / ih)
    return iw * scale, ih * scale


def _add_srgb_output_intent(pdf):
    """sRGB-Farbprofil als OutputIntent – Voraussetzung für PDF/A-3 (ZUGFeRD)."""
    icc = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
    pdf.add_output_intent(
        OutputIntentSubType.PDFA, output_condition_identifier="sRGB",
        registry_name="http://www.color.org", info="sRGB IEC61966-2.1",
        dest_output_profile=PDFICCProfile(contents=icc, n=3, alternate="DeviceRGB"))


def render_document(doc, settings, pdfa=False):
    s = settings
    pdf = DocPDF(s)
    if pdfa:
        _add_srgb_output_intent(pdf)
    pdf.set_lang("de-DE")
    pdf.set_title(f"{doc.kind_label} {doc.display_number}")
    pdf.set_author(s.company_name or s.owner_name)
    pdf.add_page()
    content_w = pdf.w - LEFT - RIGHT_MARGIN

    # Logo
    logo_drawn = False
    if s.logo_path and os.path.exists(s.logo_path):
        try:
            # Logo rechtsbündig in einem Feld von max. 55 × 25 mm
            w, h = _logo_box(s.logo_path, 55, 25)
            pdf.image(s.logo_path, x=pdf.w - RIGHT_MARGIN - w, y=12, w=w, h=h)
            logo_drawn = True
        except Exception:
            pass
    if not logo_drawn and (s.company_name or s.owner_name):
        pdf.set_font("DejaVu", "B", 14)
        pdf.set_text_color(*ACCENT)
        pdf.set_xy(LEFT, 18)
        pdf.cell(content_w, 7, s.company_name or s.owner_name, align="R")
        pdf.set_text_color(0, 0, 0)

    # Absenderzeile + Anschrift (Anschriftfeld ab 45 mm)
    pdf.set_xy(LEFT, 45)
    pdf.set_font("DejaVu", "", 7)
    pdf.set_text_color(*GREY)
    sender = " · ".join(p for p in (s.company_name or s.owner_name, s.street,
                                    f"{s.zip_code} {s.city}".strip()) if p)
    pdf.cell(85, 4, sender)
    pdf.set_text_color(0, 0, 0)
    pdf.set_xy(LEFT, 51)
    pdf.set_font("DejaVu", "", 10)
    pdf.multi_cell(85, 5, "\n".join(doc.customer.address_lines()))

    # Infoblock rechts
    info = [(f"{doc.kind_label}snr." if doc.is_invoice else "Angebotsnr.", doc.display_number),
            ("Datum", fmt_date(doc.issue_date))]
    if doc.customer.customer_number:
        info.append(("Kundennr.", doc.customer.customer_number))
    if doc.is_invoice and doc.service_date:
        info.append(("Leistungsdatum", doc.service_date))
    if doc.due_date:
        info.append(("Fällig am" if doc.is_invoice else "Gültig bis", fmt_date(doc.due_date)))
    if doc.customer.vat_id:
        info.append(("USt-IdNr. Kunde", doc.customer.vat_id))
    y = 51
    pdf.set_font("DejaVu", "", 9)
    for label, value in info:
        pdf.set_xy(125, y)
        pdf.set_text_color(*GREY)
        pdf.cell(30, 5, label)
        pdf.set_text_color(0, 0, 0)
        pdf.cell(0, 5, value, align="R")
        y += 5

    # Betreff
    pdf.set_xy(LEFT, max(pdf.get_y(), 98))
    pdf.set_font("DejaVu", "B", 13)
    headline = f"{doc.kind_label} {doc.display_number}"
    pdf.cell(0, 8, headline, new_x="LMARGIN", new_y="NEXT")
    if doc.title:
        pdf.set_font("DejaVu", "", 10)
        pdf.multi_cell(0, 5, doc.title, new_x="LMARGIN", new_y="NEXT")
    pdf.ln(4)

    pdf.set_font("DejaVu", "", 10)
    if doc.cancels:
        pdf.multi_cell(0, 5, f"Diese Stornorechnung storniert die Rechnung {doc.cancels.number} "
                             f"vom {fmt_date(doc.cancels.issue_date)}.", new_x="LMARGIN", new_y="NEXT")
        pdf.ln(3)
    if doc.intro:
        greeting = "Sehr geehrte Damen und Herren,"
        pdf.multi_cell(0, 5, f"{greeting}\n\n{doc.intro}", new_x="LMARGIN", new_y="NEXT")
        pdf.ln(4)

    # Positionstabelle
    show_vat = not doc.small_business and len({i.vat_rate for i in doc.items}) > 1
    if show_vat:
        widths = (12, 66, 16, 20, 26, 13, 27)
        header = ("Pos", "Beschreibung", "Menge", "Einheit", "Einzelpreis", "USt", "Gesamt")
        align = ("C", "L", "R", "L", "R", "R", "R")
    else:
        widths = (12, 79, 16, 20, 26, 27)
        header = ("Pos", "Beschreibung", "Menge", "Einheit", "Einzelpreis", "Gesamt")
        align = ("C", "L", "R", "L", "R", "R")
    scale = content_w / sum(widths)
    widths = tuple(w * scale for w in widths)

    pdf.set_font("DejaVu", "", 9)
    head_style = FontFace(emphasis="BOLD", color=(255, 255, 255), fill_color=ACCENT)
    with pdf.table(col_widths=widths, text_align=align, headings_style=head_style,
                   borders_layout="HORIZONTAL_LINES", line_height=5, padding=1.2,
                   first_row_as_headings=True, width=content_w) as table:
        table.row(header)
        for n, it in enumerate(doc.items, start=1):
            cells = [str(n), it.description, fmt_qty(it.quantity), it.unit or "",
                     fmt_money(it.unit_price)]
            if show_vat:
                cells.append(f"{it.vat_rate} %")
            cells.append(fmt_money(it.line_total))
            table.row(cells)

    # Summen
    t = doc.totals()
    pdf.ln(3)
    x_label = LEFT + content_w - 80

    def total_line(label, value, bold=False):
        pdf.set_x(x_label)
        pdf.set_font("DejaVu", "B" if bold else "", 10 if bold else 9)
        pdf.cell(50, 6, label)
        pdf.cell(30, 6, value, align="R", new_x="LMARGIN", new_y="NEXT")

    if doc.small_business:
        total_line("Gesamtbetrag", fmt_money(t["gross"]), bold=True)
    else:
        total_line("Summe netto", fmt_money(t["net"]))
        for rate, amount in t["vat"].items():
            total_line(f"zzgl. {rate} % USt", fmt_money(amount))
        pdf.set_draw_color(0, 0, 0)
        pdf.line(x_label, pdf.get_y(), LEFT + content_w, pdf.get_y())
        total_line("Gesamtbetrag", fmt_money(t["gross"]), bold=True)

    pdf.ln(5)
    pdf.set_font("DejaVu", "", 9)
    if doc.small_business:
        pdf.multi_cell(0, 4.5, "Gemäß § 19 UStG wird keine Umsatzsteuer berechnet.",
                       new_x="LMARGIN", new_y="NEXT")
        pdf.ln(3)

    pdf.set_font("DejaVu", "", 10)
    footer = (doc.footer or "").replace("{faellig}", fmt_date(doc.due_date))
    if footer:
        pdf.multi_cell(0, 5, footer, new_x="LMARGIN", new_y="NEXT")
        pdf.ln(2)
    if doc.has_skonto:
        pdf.set_font("DejaVu", "B", 10)
        pdf.multi_cell(0, 5, skonto_text(doc), new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("DejaVu", "", 10)
    pdf.ln(4)
    pdf.multi_cell(0, 5, "Mit freundlichen Grüßen\n\n" + (s.owner_name or s.company_name),
                   new_x="LMARGIN", new_y="NEXT")

    return bytes(pdf.output())


def skonto_text(doc):
    """Einheitlicher Skonto-Hinweis für PDF und E-Mail."""
    return (f"Bei Zahlung bis zum {fmt_date(doc.skonto_date)} können {doc.skonto_percent_label} Skonto "
            f"({fmt_money(doc.skonto_amount)}) abgezogen werden – der Zahlbetrag beträgt dann "
            f"{fmt_money(doc.gross - doc.skonto_amount)}.")
