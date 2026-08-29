from pathlib import Path

from PIL import Image, ImageDraw
from pypdf import PdfReader, PdfWriter
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas
from reportlab.platypus import (
    Image as RLImage,
    KeepTogether,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)


ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / "output" / "pdf"
TMP = ROOT / "tmp" / "pdfs"
OUTPUT.mkdir(parents=True, exist_ok=True)
TMP.mkdir(parents=True, exist_ok=True)

font_path = Path("C:/Windows/Fonts/arial.ttf")
bold_font_path = Path("C:/Windows/Fonts/arialbd.ttf")
BODY_FONT = "Helvetica"
BOLD_FONT = "Helvetica-Bold"
if font_path.exists() and bold_font_path.exists():
    pdfmetrics.registerFont(TTFont("ArialTest", str(font_path)))
    pdfmetrics.registerFont(TTFont("ArialTest-Bold", str(bold_font_path)))
    BODY_FONT = "ArialTest"
    BOLD_FONT = "ArialTest-Bold"

styles = getSampleStyleSheet()
styles.add(ParagraphStyle(
    name="TestTitle", parent=styles["Title"], fontName=BOLD_FONT,
    fontSize=17, leading=21, textColor=colors.HexColor("#183153"), spaceAfter=10,
))
styles.add(ParagraphStyle(
    name="TestHeading", parent=styles["Heading2"], fontName=BOLD_FONT,
    fontSize=11, leading=14, textColor=colors.HexColor("#1f5d55"), spaceBefore=8, spaceAfter=5,
))
styles.add(ParagraphStyle(
    name="TestBody", parent=styles["BodyText"], fontName=BODY_FONT,
    fontSize=9.5, leading=14, textColor=colors.HexColor("#263238"), spaceAfter=7,
))
styles.add(ParagraphStyle(
    name="TestSmall", parent=styles["BodyText"], fontName=BODY_FONT,
    fontSize=7.5, leading=10, textColor=colors.HexColor("#52606d"),
))
styles.add(ParagraphStyle(
    name="CoverLabel", parent=styles["BodyText"], fontName=BOLD_FONT,
    fontSize=8, leading=10, alignment=TA_CENTER, textColor=colors.HexColor("#ffffff"),
))


def page_header_footer(pdf_canvas, doc):
    width, height = A4
    pdf_canvas.saveState()
    pdf_canvas.setFillColor(colors.HexColor("#183153"))
    pdf_canvas.rect(0, height - 18 * mm, width, 18 * mm, stroke=0, fill=1)
    pdf_canvas.setFillColor(colors.white)
    pdf_canvas.setFont(BOLD_FONT, 10)
    pdf_canvas.drawString(20 * mm, height - 11 * mm, "BARANGAY STO. NINO - TEST DOCUMENT")
    pdf_canvas.setFillColor(colors.HexColor("#52606d"))
    pdf_canvas.setFont(BODY_FONT, 7.5)
    pdf_canvas.drawString(20 * mm, 11 * mm, "SEALGUARD QA SAMPLE - NOT AN OFFICIAL CONTRACT")
    pdf_canvas.drawRightString(width - 20 * mm, 11 * mm, f"Page {doc.page}")
    pdf_canvas.setStrokeColor(colors.HexColor("#d8dee8"))
    pdf_canvas.line(20 * mm, 15 * mm, width - 20 * mm, 15 * mm)
    pdf_canvas.restoreState()


def make_doc(filename, title, story, metadata=None):
    path = OUTPUT / filename
    doc = SimpleDocTemplate(
        str(path), pagesize=A4,
        rightMargin=20 * mm, leftMargin=20 * mm,
        topMargin=26 * mm, bottomMargin=21 * mm,
        title=title, author="SEALGUARD QA Corpus",
        subject="System testing sample document",
    )
    doc.build(story, onFirstPage=page_header_footer, onLaterPages=page_header_footer)
    if metadata:
        reader = PdfReader(str(path))
        writer = PdfWriter()
        writer.clone_document_from_reader(reader)
        current = dict(reader.metadata or {})
        current.update(metadata)
        writer.add_metadata(current)
        with path.open("wb") as stream:
            writer.write(stream)
    return path


def title_block(title, test_id, purpose):
    return [
        Spacer(1, 5 * mm),
        Table(
            [[Paragraph(test_id, styles["CoverLabel"])]],
            colWidths=[42 * mm], rowHeights=[8 * mm],
            style=TableStyle([
                ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#1f5d55")),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#174a44")),
            ]),
        ),
        Spacer(1, 5 * mm),
        Paragraph(title, styles["TestTitle"]),
        Paragraph(f"<b>Testing purpose:</b> {purpose}", styles["TestBody"]),
        Spacer(1, 3 * mm),
    ]


def details_table(rows):
    data = [[Paragraph("Field", styles["TestSmall"]), Paragraph("Value", styles["TestSmall"])]]
    data.extend([[Paragraph(str(key), styles["TestSmall"]), Paragraph(str(value), styles["TestSmall"])] for key, value in rows])
    return Table(data, colWidths=[42 * mm, 118 * mm], repeatRows=1, style=TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e8eef6")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor("#183153")),
        ("FONTNAME", (0, 0), (-1, 0), BOLD_FONT),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#cbd5e1")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))


def clauses(count, prefix="Clause"):
    result = []
    for number in range(1, count + 1):
        result.extend([
            Paragraph(f"{prefix} {number}", styles["TestHeading"]),
            Paragraph(
                "The supplier shall provide the listed goods according to the approved schedule, "
                "technical specifications, inspection requirements, and applicable barangay procurement rules. "
                "Any variation must be documented and approved in writing before implementation.",
                styles["TestBody"],
            ),
        ])
    return result


basic_story = title_block(
    "Office Supplies Procurement Agreement",
    "QA-01 BASIC",
    "Normal one-page upload, encryption, download, and verification.",
)
basic_story.extend([
    details_table([
        ("Contract ID", "QA-2026-0001"),
        ("Supplier", "Laguna Community Office Supplies"),
        ("Amount", "PHP 48,750.00"),
        ("Effective date", "August 28, 2026"),
        ("Status", "For system testing only"),
    ]),
    Spacer(1, 5 * mm),
    Paragraph("Agreement", styles["TestHeading"]),
    Paragraph(
        "This sample agreement covers paper, folders, printer ink, and administrative supplies for a thirty-day delivery period.",
        styles["TestBody"],
    ),
    Spacer(1, 12 * mm),
    details_table([
        ("Prepared by", "Maria L. Santos"),
        ("Reviewed by", "Roberto D. Cruz"),
        ("Signature", "____________________________"),
    ]),
])
make_doc("01_basic_contract.pdf", "QA-01 Basic Contract", basic_story)


multi_story = title_block(
    "Barangay Road Lighting Maintenance Contract",
    "QA-02 MULTI-PAGE",
    "Page counting, navigation, stamping on every page, and version-chain testing.",
)
multi_story.extend([details_table([
    ("Contract ID", "QA-2026-0002"),
    ("Project", "Streetlight inspection and maintenance"),
    ("Coverage", "Six service zones"),
])])
multi_story.extend(clauses(15, "Service Condition"))
make_doc("02_multipage_contract.pdf", "QA-02 Multi-page Contract", multi_story)


long_value = (
    "Emergency procurement and scheduled delivery of disaster-response equipment, rechargeable lamps, "
    "portable communication units, first-aid supplies, water containers, temporary shelter materials, "
    "inventory labels, protective equipment, and associated documentation for multiple response sites"
)
long_story = title_block(
    "Long Text and Field Boundary Stress Contract",
    "QA-03 LONG TEXT",
    "Long titles, table wrapping, search, filename handling, and layout boundaries.",
)
long_story.extend([
    details_table([
        ("Extremely long project title", long_value),
        ("Supplier legal name", "South Luzon Emergency Logistics and Community Resilience Equipment Distribution Cooperative"),
        ("Reference", "QA-LONG-ABCDEFGHIJKLMNOPQRSTUVWXYZ-0123456789-2026"),
        ("Notes", long_value + ". " + long_value + "."),
    ]),
])
long_story.extend(clauses(7, "Extended Provision"))
make_doc("03_long_text_stress.pdf", "QA-03 Long Text Stress", long_story)


unicode_story = title_block(
    "Kasunduan sa Pagkakaloob ng Serbisyo",
    "QA-04 FILIPINO NAMES",
    "Filipino names, accented characters, punctuation, and text extraction.",
)
unicode_story.extend([
    details_table([
        ("Barangay", "Barangay Sto. Niño, Biñan City"),
        ("Kinatawan", "Ma. Peña Villanueva"),
        ("Tagapagtustos", "Niño José Dela Cruz Trading"),
        ("Lugar", "Lungsod ng Biñan, Laguna"),
        ("Paksa", "Pagkukumpuni, paghahatid, at taunang inspeksiyon"),
    ]),
    Spacer(1, 5 * mm),
    Paragraph("Mga Tuntunin", styles["TestHeading"]),
    Paragraph(
        "Ang dokumentong ito ay sample lamang para sa pagsusuri ng pag-upload, paghahanap, pag-encrypt, at pagpapatunay ng sistema.",
        styles["TestBody"],
    ),
])
make_doc("04_unicode_filipino_names.pdf", "QA-04 Filipino Names", unicode_story)


image_path = TMP / "inspection_photo.png"
image = Image.new("RGB", (1400, 850), "#edf2f7")
draw = ImageDraw.Draw(image)
draw.rectangle((80, 80, 1320, 770), fill="#ffffff", outline="#183153", width=8)
draw.rectangle((150, 170, 650, 650), fill="#4f8ef7")
draw.rectangle((750, 170, 1250, 650), fill="#1f5d55")
draw.text((170, 690), "QA IMAGE PANEL A", fill="#183153")
draw.text((770, 690), "QA IMAGE PANEL B", fill="#183153")
image.save(image_path)

image_story = title_block(
    "Equipment Inspection Record with Embedded Image",
    "QA-05 IMAGE HEAVY",
    "Embedded-image hashing, larger file handling, stamping, and visual verification.",
)
image_story.extend([
    details_table([
        ("Inspection ID", "QA-IMG-2026-05"),
        ("Asset", "Emergency response equipment cabinets"),
        ("Inspector", "QA Test Operator"),
    ]),
    Spacer(1, 7 * mm),
    RLImage(str(image_path), width=160 * mm, height=97 * mm),
    Spacer(1, 5 * mm),
    Paragraph("The large embedded raster image is intentional and should remain visible after processing.", styles["TestBody"]),
])
make_doc("05_image_heavy_contract.pdf", "QA-05 Image-heavy Contract", image_story)


blank_path = OUTPUT / "06_blank_page_contract.pdf"
blank_canvas = canvas.Canvas(str(blank_path), pagesize=A4)
blank_canvas.setTitle("QA-06 Intentional Blank Page")
blank_canvas.setAuthor("SEALGUARD QA Corpus")
width, height = A4
blank_canvas.setFont(BOLD_FONT, 17)
blank_canvas.setFillColor(colors.HexColor("#183153"))
blank_canvas.drawString(25 * mm, height - 35 * mm, "QA-06 Blank Page Boundary Test")
blank_canvas.setFont(BODY_FONT, 10)
blank_canvas.setFillColor(colors.HexColor("#263238"))
blank_canvas.drawString(25 * mm, height - 48 * mm, "Page 2 is intentionally completely blank.")
blank_canvas.drawString(25 * mm, height - 56 * mm, "The system should preserve a total of three pages.")
blank_canvas.showPage()
blank_canvas.showPage()
blank_canvas.setFont(BOLD_FONT, 15)
blank_canvas.setFillColor(colors.HexColor("#1f5d55"))
blank_canvas.drawString(25 * mm, height - 35 * mm, "Page After Intentional Blank Page")
blank_canvas.setFont(BODY_FONT, 10)
blank_canvas.setFillColor(colors.HexColor("#263238"))
blank_canvas.drawString(25 * mm, height - 48 * mm, "If this is page 3, the blank page was preserved correctly.")
blank_canvas.save()


metadata_story = title_block(
    "Metadata Preservation and Replacement Test",
    "QA-07 METADATA",
    "Metadata extraction, replacement by SEALGUARD, and verification behavior.",
)
metadata_story.extend([
    details_table([
        ("Expected title metadata", "QA Metadata Contract"),
        ("Expected author metadata", "Barangay QA Team"),
        ("Expected subject metadata", "Metadata and verification testing"),
        ("Expected keywords", "qa, sealguard, metadata, procurement"),
    ]),
    Paragraph("Inspect the original metadata before upload and compare it with the processed document.", styles["TestBody"]),
])
make_doc(
    "07_metadata_rich_contract.pdf",
    "QA Metadata Contract",
    metadata_story,
    metadata={
        "/Title": "QA Metadata Contract",
        "/Author": "Barangay QA Team",
        "/Subject": "Metadata and verification testing",
        "/Keywords": "qa, sealguard, metadata, procurement",
    },
)


tampered_story = title_block(
    "Office Supplies Procurement Agreement - Altered Copy",
    "QA-08 TAMPERED",
    "Negative verification test paired with QA-01; content and amount are deliberately changed.",
)
tampered_story.extend([
    details_table([
        ("Contract ID", "QA-2026-0001"),
        ("Supplier", "Laguna Community Office Supplies"),
        ("Amount", "PHP 948,750.00 - DELIBERATELY ALTERED"),
        ("Effective date", "August 28, 2026"),
        ("Status", "Negative test sample"),
    ]),
    Spacer(1, 5 * mm),
    Paragraph("Deliberate Modification", styles["TestHeading"]),
    Paragraph(
        "This file resembles QA-01 but contains a materially different amount. It must never verify as the processed QA-01 document.",
        styles["TestBody"],
    ),
])
make_doc("08_tampered_basic_contract.pdf", "QA-08 Tampered Basic Contract", tampered_story)

print(f"Created 8 PDFs in {OUTPUT}")
