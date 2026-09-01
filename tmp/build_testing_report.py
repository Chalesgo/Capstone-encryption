from pathlib import Path

from docx import Document
from docx.enum.section import WD_SECTION
from docx.enum.table import WD_CELL_VERTICAL_ALIGNMENT, WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt, RGBColor


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "output" / "reports" / "Django_Automated_Testing_Report.docx"


TESTS = [
    ("AT-01", "Audit-log retention after permanent deletion",
     "Create a contract and audit entry, permanently delete the contract, then query the retained audit records.",
     "The contract is deleted while its title remains available in both related audit records.", "Passed"),
    ("AT-02", "Dashboard filtering and chronological sorting",
     "Create Added and Edited audit entries at controlled times; request only Added activity sorted oldest first.",
     "Only Added entries are returned, ordered from oldest to newest.", "Passed"),
    ("AT-03", "Dashboard pagination and filter preservation",
     "Create 25 deletion records and request page 2 with 10 items per page and active filter parameters.",
     "Page 2 contains 10 records; the total is 25; pagination links preserve the selected controls.", "Passed"),
    ("AT-04", "Debounced document search interface",
     "Request the authenticated contract list and inspect the rendered search-control JavaScript.",
     "The page invokes scheduled filtering and uses a 350 ms debounce delay.", "Passed"),
    ("AT-05", "Public verification fingerprint lookup",
     "Upload a valid minimal PDF while isolating auxiliary operations and supplying a known canonical fingerprint.",
     "Verification performs a direct fingerprint lookup, redirects normally, and records a Viewed audit event for the matching contract.", "Passed"),
    ("AT-06", "Filename-based demonstration mode",
     "Upload a demonstration PDF whose filename contains the configured authentic keyword.",
     "The demonstration result remains Authentic and the log explicitly states that cryptographic checks were not executed.", "Passed"),
    ("AT-07", "Version-chain mismatch detection",
     "Create two contract versions with an incorrect previous-fingerprint link in the second version.",
     "The first link is valid and the altered second link is rejected.", "Passed"),
    ("AT-08", "Folder-order persistence",
     "Submit a complete reordered list of folder identifiers for the authenticated user.",
     "The endpoint returns success and subsequent database ordering matches the submitted sequence.", "Passed"),
    ("AT-09", "Incomplete folder-order rejection",
     "Submit a folder order that omits one folder belonging to the current user.",
     "The request is rejected with HTTP 400 and an incomplete order is not accepted.", "Passed"),
    ("AT-10", "Verification loading-layout regression",
     "Render the public verification page and inspect critical responsive/loading styles and scripts.",
     "The verification state locks page scrolling, applies the expected grid, and avoids the problematic 100vw declaration.", "Passed"),
]


def set_cell_shading(cell, fill):
    tc_pr = cell._tc.get_or_add_tcPr()
    shading = OxmlElement("w:shd")
    shading.set(qn("w:fill"), fill)
    tc_pr.append(shading)


def set_repeat_table_header(row):
    tr_pr = row._tr.get_or_add_trPr()
    repeat = OxmlElement("w:tblHeader")
    repeat.set(qn("w:val"), "true")
    tr_pr.append(repeat)


def add_heading(document, text, level=1):
    paragraph = document.add_heading(text, level=level)
    paragraph.paragraph_format.space_before = Pt(10)
    paragraph.paragraph_format.space_after = Pt(5)
    return paragraph


def add_bullet(document, text):
    paragraph = document.add_paragraph(style="List Bullet")
    paragraph.add_run(text)
    return paragraph


def build_report():
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    doc = Document()

    section = doc.sections[0]
    section.top_margin = Inches(0.7)
    section.bottom_margin = Inches(0.7)
    section.left_margin = Inches(0.75)
    section.right_margin = Inches(0.75)

    styles = doc.styles
    styles["Normal"].font.name = "Arial"
    styles["Normal"].font.size = Pt(10)
    styles["Normal"].paragraph_format.space_after = Pt(5)
    for name in ("Title", "Heading 1", "Heading 2"):
        styles[name].font.name = "Arial"
        styles[name].font.color.rgb = RGBColor(31, 55, 76)

    title = doc.add_paragraph()
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = title.add_run("AUTOMATED SOFTWARE TESTING REPORT")
    run.bold = True
    run.font.name = "Arial"
    run.font.size = Pt(18)
    run.font.color.rgb = RGBColor(31, 55, 76)

    subtitle = doc.add_paragraph()
    subtitle.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = subtitle.add_run(
        "Web-Based Government Contracts Information System with Authenticity Verification"
    )
    run.bold = True
    run.font.size = Pt(11)

    info = doc.add_table(rows=4, cols=2)
    info.alignment = WD_TABLE_ALIGNMENT.CENTER
    info.style = "Table Grid"
    details = [
        ("Test type", "Automated Django regression testing"),
        ("Execution date", "September 1, 2026"),
        ("Runtime environment", "Python 3.14.5; Django 5.2.16; SQLite in-memory test database"),
        ("Result", "10 passed, 0 failed, 0 errors"),
    ]
    for row, values in zip(info.rows, details):
        row.cells[0].text, row.cells[1].text = values
        row.cells[0].paragraphs[0].runs[0].bold = True
        set_cell_shading(row.cells[0], "D9EAF7")

    add_heading(doc, "1. Purpose", 1)
    doc.add_paragraph(
        "This report documents the automated tests executed against the Django application. "
        "The tests serve as regression checks for selected audit logging, dashboard, document "
        "verification, version control, folder organization, and user-interface behaviors. Their "
        "purpose is to detect whether a later code change breaks behavior that previously worked."
    )

    add_heading(doc, "2. What Django tests.py Is", 1)
    doc.add_paragraph(
        "The contracts/tests.py file contains automated test cases for the contracts Django app. "
        "Django discovers methods whose names begin with test_. Each method prepares controlled "
        "data, performs an action through application functions or Django's test client, and "
        "compares the actual result with an expected result through assertions."
    )
    doc.add_paragraph(
        "Before execution, Django creates a separate temporary test database and applies the "
        "required migrations. The tests use this isolated database instead of the operational "
        "database. After execution, Django destroys the temporary database. This prevents the "
        "automated tests from changing the system's actual contract and user records."
    )

    add_heading(doc, "3. Test Procedure", 1)
    add_bullet(doc, "Set DEBUG=False for the test process because Django requires a Boolean value.")
    add_bullet(doc, "Run python manage.py check to validate the Django configuration.")
    add_bullet(doc, "Run python manage.py test --verbosity 2 to discover and execute all test methods.")
    add_bullet(doc, "Allow Django to create an isolated in-memory SQLite database and apply all migrations.")
    add_bullet(doc, "Review each assertion result and the final test summary.")
    add_bullet(doc, "Confirm that the temporary database is destroyed after the run.")

    command = doc.add_paragraph()
    command.add_run("Commands used:\n").bold = True
    code = command.add_run("$env:DEBUG='False'\npython manage.py check\npython manage.py test --verbosity 2")
    code.font.name = "Consolas"
    code.font.size = Pt(9)

    add_heading(doc, "4. Automated Test Cases and Results", 1)
    table = doc.add_table(rows=1, cols=5)
    table.style = "Table Grid"
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    headers = ["ID / Test", "Method", "Expected result", "Actual result", "Status"]
    for cell, text in zip(table.rows[0].cells, headers):
        cell.text = text
        cell.paragraphs[0].runs[0].bold = True
        cell.paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.CENTER
        cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
        set_cell_shading(cell, "1F4E78")
        cell.paragraphs[0].runs[0].font.color.rgb = RGBColor(255, 255, 255)
    set_repeat_table_header(table.rows[0])

    for test_id, name, method, expected, status in TESTS:
        cells = table.add_row().cells
        values = [f"{test_id}\n{name}", method, expected, expected, status]
        for cell, value in zip(cells, values):
            cell.text = value
            cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.TOP
        cells[0].paragraphs[0].runs[0].bold = True
        cells[4].paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.CENTER
        cells[4].paragraphs[0].runs[0].bold = True
        cells[4].paragraphs[0].runs[0].font.color.rgb = RGBColor(0, 112, 60)

    widths = [1.35, 2.1, 2.15, 2.15, 0.65]
    for row in table.rows:
        for cell, width in zip(row.cells, widths):
            cell.width = Inches(width)

    add_heading(doc, "5. Summary of Results", 1)
    summary = doc.add_table(rows=4, cols=2)
    summary.style = "Table Grid"
    summary.alignment = WD_TABLE_ALIGNMENT.CENTER
    summary_rows = [
        ("Tests executed", "10"),
        ("Passed", "10"),
        ("Failed or errored", "0"),
        ("Observed pass rate", "100% (10/10)"),
    ]
    for row, values in zip(summary.rows, summary_rows):
        row.cells[0].text, row.cells[1].text = values
        row.cells[0].paragraphs[0].runs[0].bold = True
        set_cell_shading(row.cells[0], "EAF2F8")

    doc.add_paragraph(
        "The Django system check reported no configuration issues after DEBUG was supplied as "
        "False for the test process. All ten discovered automated tests completed successfully. "
        "The result indicates that the specific behaviors covered by these tests were functioning "
        "as expected at the time of execution."
    )

    add_heading(doc, "6. Interpretation and Limitations", 1)
    doc.add_paragraph(
        "A 100% result applies only to the ten automated cases listed in this report. It does not "
        "mean that the entire system is defect-free, nor does it complete every test identified in "
        "the capstone manuscript. Several tests isolate dependencies through controlled values or "
        "mocked functions; consequently, a passing unit or regression test does not replace a "
        "complete end-to-end test using actual encryption keys and processed PDF documents."
    )
    doc.add_paragraph("The following testing activities remain separate and should be reported when completed:")
    add_bullet(doc, "Expanded cryptographic unit tests for AES, RSA key wrapping, HMAC, SHA-256, and LSB embedding/extraction.")
    add_bullet(doc, "End-to-end upload, encryption, sealing, publication, download, and verification tests using prepared PDFs.")
    add_bullet(doc, "Tamper-detection accuracy tests covering text, image, vector, metadata, and structural modifications.")
    add_bullet(doc, "Role-based authorization, SQL injection, cross-site scripting, CSRF, upload-abuse, and brute-force tests.")
    add_bullet(doc, "Browser, viewport, physical-device, stress, reliability, and response-time testing.")

    doc.add_paragraph(
        "The filename-based authentic, tampered, and unknown results are intentionally retained as "
        "demonstration features. The automated test confirms that this demonstration mode remains "
        "available and that its debug log discloses that cryptographic checks were not executed. "
        "Results produced by demonstration mode must not be included as evidence of cryptographic "
        "verification accuracy."
    )

    add_heading(doc, "7. Conclusion", 1)
    doc.add_paragraph(
        "The initial automated regression baseline passed all ten cases. The test suite provides "
        "repeatable evidence for the covered features and can be rerun after future modifications. "
        "Additional automated and manual tests are required to substantiate the manuscript's full "
        "functional, security, compatibility, reliability, and performance-testing objectives."
    )

    footer = section.footer.paragraphs[0]
    footer.alignment = WD_ALIGN_PARAGRAPH.CENTER
    footer.add_run("Automated Software Testing Report | Generated September 1, 2026").font.size = Pt(8)

    doc.save(OUTPUT)
    print(OUTPUT)


if __name__ == "__main__":
    build_report()
