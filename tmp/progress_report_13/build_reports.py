from copy import deepcopy
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from lxml import etree


W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
NS = {"w": W}
Q = lambda name: f"{{{W}}}{name}"

SOURCE = Path(r"C:\Users\griff\OneDrive\Documents\Progress Reports CAPSTONE\CAPSTONE 2\Integrative-Course-Progress-Report-Group-007-CAPSTONE 2 WEEK 1 TEMPLATE.docx")
OUTPUT = Path(r"C:\Users\griff\Downloads\OJT REQUIREMENTS FOLDER\For Showing stuff\Capstone-encryption\output\reports\SealGuard_Integrative_Course_Progress_Reports_Weeks_1_to_13.docx")


REPORTS = [
    {
        "week": 1,
        "date": "April 4 - 10, 2026",
        "lines": [
            "FOCUS: Establish the initial SealGuard web and security prototype.",
            "- Built the first HTML, JavaScript, and CSS interface and organized the Git repository.",
            "- Scaffolded the Django project with contract models, upload and list views, authentication pages, and migrations.",
            "- Added SHA-256 canonical fingerprinting, AES-256-CBC encryption, an LSB seal prototype, verification, and deletion flows.",
            "TECHNICAL OUTCOME: A working local prototype could enroll, secure, list, and verify PDF contracts.",
            "ISSUE AND RESPONSE: Rapid file uploads created an uneven structure, so the next sprint focused on consolidation.",
            "MANUSCRIPT ALIGNMENT: Planning, design, and the Encryption and Verification and Document Management modules.",
            "NEXT STEP: Consolidate the project and strengthen key handling.",
        ],
    },
    {
        "week": 2,
        "date": "April 11 - 18, 2026",
        "lines": [
            "FOCUS: Consolidate the Django prototype and strengthen cryptographic key protection.",
            "- Moved the functional application into the repository root so settings, routes, models, and templates shared one structure.",
            "- Added HMAC-SHA256 integrity data and RSA-wrapped AES keys through new contract fields and migrations.",
            "- Retained upload, encryption, verification, and authentication behavior while updating setup documentation.",
            "TECHNICAL OUTCOME: The prototype used layered hashing, symmetric encryption, integrity checking, and key wrapping.",
            "ISSUE AND RESPONSE: Duplicate project trees risked divergence; one primary application layout was established.",
            "MANUSCRIPT ALIGNMENT: Three-layer encryption design, hybrid cryptography, and secure Django implementation.",
            "NEXT STEP: Extend verification to printed or scanned copies.",
        ],
    },
    {
        "week": 3,
        "date": "May 18 - 23, 2026",
        "lines": [
            "FOCUS: Develop the first physical-document verification workflow.",
            "- Added a physical verification page and expanded PDF processing for seals, QR data, and embedded metadata.",
            "- Stored the original fingerprint separately so the system could distinguish source content from the sealed output.",
            "- Refined public verification logic and documented how to stop and restart the local development server.",
            "TECHNICAL OUTCOME: The system gained an early bridge between digital records and printed-document checking.",
            "ISSUE AND RESPONSE: Seal stamping changes PDF bytes; separate original and sealed fingerprints clarified comparison logic.",
            "MANUSCRIPT ALIGNMENT: Scanning interface, steganographic extraction, AES decryption, and result display.",
            "NEXT STEP: Integrate the security workflow with the final interface.",
        ],
    },
    {
        "week": 4,
        "date": "June 8 - 13, 2026",
        "lines": [
            "FOCUS: Integrate the SealGuard user interface with the Django application.",
            "- Built improved login, dashboard, and public verification screens from the earlier static interface.",
            "- Converted the visual design into reusable Django templates for upload, contract list, verification, and physical verification.",
            "- Expanded PDF encryption and verification utilities and documented setup, administrator access, and the known LSB concern.",
            "TECHNICAL OUTCOME: The security functions became accessible through a consistent branded web workflow.",
            "ISSUE AND RESPONSE: Static mockups and backend pages differed; shared templates aligned behavior and presentation.",
            "MANUSCRIPT ALIGNMENT: Web administrative system, public verification interface, and Agile prototype refinement.",
            "NEXT STEP: Clean the repository and make setup reproducible.",
        ],
    },
    {
        "week": 5,
        "date": "June 14 - 20, 2026",
        "lines": [
            "FOCUS: Reorganize the repository and document a reproducible development environment.",
            "- Revised the README to explain SealGuard features, installation, key generation, media folders, migrations, and local use.",
            "- Added the Python dependency list required to reproduce the Django, PDF, image, QR, and cryptographic environment.",
            "- Removed obsolete duplicate project directories and restored one canonical application tree.",
            "TECHNICAL OUTCOME: New developers could identify the required packages and the intended project entry point.",
            "ISSUE AND RESPONSE: Earlier uploads duplicated application folders; repository cleanup reduced maintenance risk.",
            "MANUSCRIPT ALIGNMENT: Development and coding practices, GitHub version control, and modular Django implementation.",
            "NEXT STEP: Address privacy, encryption, and document metadata defects.",
        ],
    },
    {
        "week": 6,
        "date": "June 22 - 28, 2026",
        "lines": [
            "FOCUS: Correct security and document-record defects discovered during integration.",
            "- Removed exposed private information from the repository and its history, then tightened sensitive-file handling.",
            "- Corrected encryption behavior and prevented duplicate document records from appearing unexpectedly.",
            "- Added contract creation date, modification date, recipient, visibility, status, and related metadata fields.",
            "TECHNICAL OUTCOME: Contract records became safer to handle and more useful for administrative tracking.",
            "ISSUE AND RESPONSE: Security leakage and duplicate records were treated as release-blocking defects and corrected.",
            "MANUSCRIPT ALIGNMENT: Confidentiality, data integrity, structured presentation, and secure document management.",
            "NEXT STEP: Improve list usability and add an accountable audit trail.",
        ],
    },
    {
        "week": 7,
        "date": "July 6 - 12, 2026",
        "lines": [
            "FOCUS: Improve contract retrieval and establish activity monitoring.",
            "- Redesigned the contracts list for clearer document status, metadata, actions, and day-to-day navigation.",
            "- Added the AuditLog model and dashboard activity records for document and user operations.",
            "- Connected interface actions to chronological records that support later filtering and reporting.",
            "TECHNICAL OUTCOME: Administrators gained a clearer working list and traceable records of system activity.",
            "ISSUE AND RESPONSE: Document actions were difficult to review after the fact; persistent activity logs added accountability.",
            "MANUSCRIPT ALIGNMENT: Report Dashboard, audit trail, accountability, search, and document retrieval.",
            "NEXT STEP: Refine navigation and visual consistency.",
        ],
    },
    {
        "week": 8,
        "date": "July 27 - August 2, 2026",
        "lines": [
            "FOCUS: Refine navigation, visual hierarchy, and theme consistency.",
            "- Reworked the top navigation so key document, dashboard, and verification destinations were easier to locate.",
            "- Improved page layouts, spacing, controls, and visual feedback across the administrative interface.",
            "- Added more consistent theme behavior so core screens presented the same SealGuard identity.",
            "TECHNICAL OUTCOME: The prototype became easier to navigate and more suitable for demonstration and evaluation.",
            "ISSUE AND RESPONSE: Screen-level styles had diverged; shared navigation and theme rules reduced inconsistency.",
            "MANUSCRIPT ALIGNMENT: Design phase mockups, administrative interface, usability, and compatibility objectives.",
            "NEXT STEP: Improve verification feedback during longer operations.",
        ],
    },
    {
        "week": 9,
        "date": "August 17 - 23, 2026",
        "lines": [
            "FOCUS: Make the verification process understandable while the system is working.",
            "- Added a loading workspace that displays progress while a submitted PDF is inspected.",
            "- Organized verification feedback into visible stages instead of leaving the user with an unresponsive screen.",
            "- Prepared the result area for clearer authentic, tampered, and error outcomes.",
            "TECHNICAL OUTCOME: Public verification gained better status feedback and a clearer transition to results.",
            "ISSUE AND RESPONSE: PDF analysis could appear stalled; staged loading feedback communicated ongoing work.",
            "MANUSCRIPT ALIGNMENT: Result Display Interface, public authenticity validation, and usability evaluation.",
            "NEXT STEP: Add document organization, version history, previews, and recovery actions.",
        ],
    },
    {
        "week": 10,
        "date": "August 24 - 30, 2026",
        "lines": [
            "FOCUS: Expand document lifecycle management and prepare repeatable test data.",
            "- Added user folders, folder ordering, contract assignment, version history, version-source tracking, and stable base filenames.",
            "- Improved PDF previews and encryption-footer layout, then added Trash, restore, permanent deletion, and quality-of-life controls.",
            "- Created authentic, tampered, malformed, and edge-case PDF fixtures plus automated checks and database repair migrations.",
            "TECHNICAL OUTCOME: SealGuard could organize, revise, preview, recover, and test contracts through a fuller lifecycle.",
            "ISSUE AND RESPONSE: Revisions and deletions lacked traceability; version chains and recoverable Trash reduced that risk.",
            "MANUSCRIPT ALIGNMENT: Document Management, version control, tamper detection, and alpha unit/integration testing.",
            "NEXT STEP: Strengthen mobile use, logs, tutorials, and regression coverage.",
        ],
    },
    {
        "week": 11,
        "date": "August 31 - September 1, 2026",
        "lines": [
            "FOCUS: Improve responsive use, verification evidence, tutorials, and automated regression checks.",
            "- Added responsive navigation and mobile-safe preview behavior across the main templates.",
            "- Expanded secret-safe verification and encryption process logs and retained supporting evidence for tampered results.",
            "- Added administrator-managed Help and Tutorials with sanitized content, starter topics, and permission-checked editing.",
            "TECHNICAL OUTCOME: Users received clearer guidance and verification evidence on desktop and smaller screens.",
            "ISSUE AND RESPONSE: Results and help were fragmented; the loading workspace, detailed log, and tutorial module unified guidance.",
            "MANUSCRIPT ALIGNMENT: Compatibility testing, user training, public verification, and deployment documentation.",
            "NEXT STEP: Harden navigation, pagination, physical checks, and access control.",
        ],
    },
    {
        "week": 12,
        "date": "September 2 - 3, 2026",
        "lines": [
            "FOCUS: Harden mobile operation, signed-document verification, authentication, and role permissions.",
            "- Replaced the mobile sidebar with top navigation and added scrolling, pagination, inline tutorial icons, and versioned filenames.",
            "- Retained tampered-PDF evidence and added signed physical-document manifests, per-page tokens, page-order checks, and manual review.",
            "- Logged authentication events, enforced five-attempt temporary lockout, and clarified Superuser, Clerk, and Public permissions.",
            "TECHNICAL OUTCOME: The system better protected privileged actions and verified both digital and signed physical workflows.",
            "ISSUE AND RESPONSE: Mobile access and role boundaries were inconsistent; responsive and server-side controls closed the gaps.",
            "MANUSCRIPT ALIGNMENT: RBAC, brute-force testing, mobile verification, scanning, tamper detection, and audit monitoring.",
            "NEXT STEP: Stabilize the suite and complete document-management reporting.",
        ],
    },
    {
        "week": 13,
        "date": "September 4 - 5, 2026",
        "lines": [
            "FOCUS: Stabilize automated tests and complete document-selection and bulk lifecycle actions.",
            "- Fixed regressions and expanded authentication, authorization, cryptographic, physical-verification, and document tests.",
            "- Added duplicate-aware upload selection, audited view/download behavior, checkbox selection, select-all, and permission-checked bulk Trash actions.",
            "- Added bulk permanent deletion, report filters, pagination persistence, CSV export checks, and document-management test reporting.",
            "TECHNICAL OUTCOME: The automated test report records 12 of 12 document-management methods and 73 of 73 contracts tests passing.",
            "ISSUE AND RESPONSE: Selection and reporting paths were under-tested; explicit DOC-01 to DOC-12 matrices made results repeatable.",
            "MANUSCRIPT ALIGNMENT: Alpha unit, integration, reliability, cybersecurity, compatibility, and deployment-readiness testing.",
            "NEXT STEP: Proceed to manual browser/mobile checks and formal user acceptance testing.",
        ],
    },
]


def replace_cell_text(cell, text):
    texts = cell.xpath(".//w:t", namespaces=NS)
    if not texts:
        paragraph = cell.find(Q("p"))
        if paragraph is None:
            paragraph = etree.SubElement(cell, Q("p"))
        run = etree.SubElement(paragraph, Q("r"))
        node = etree.SubElement(run, Q("t"))
        node.text = text
        return
    texts[0].text = text
    texts[0].set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
    for node in texts[1:]:
        node.text = ""


def build_description(cell, lines):
    paragraphs = cell.findall(Q("p"))
    base_p = deepcopy(paragraphs[0]) if paragraphs else etree.Element(Q("p"))
    for paragraph in paragraphs:
        cell.remove(paragraph)

    for index, line in enumerate(lines):
        paragraph = deepcopy(base_p)
        for child in list(paragraph):
            if child.tag != Q("pPr"):
                paragraph.remove(child)

        ppr = paragraph.find(Q("pPr"))
        if ppr is None:
            ppr = etree.Element(Q("pPr"))
            paragraph.insert(0, ppr)
        spacing = ppr.find(Q("spacing"))
        if spacing is None:
            spacing = etree.SubElement(ppr, Q("spacing"))
        spacing.set(Q("before"), "0")
        spacing.set(Q("after"), "12")
        spacing.set(Q("line"), "220")
        spacing.set(Q("lineRule"), "auto")

        run = etree.SubElement(paragraph, Q("r"))
        rpr = etree.SubElement(run, Q("rPr"))
        fonts = etree.SubElement(rpr, Q("rFonts"))
        fonts.set(Q("ascii"), "Arial")
        fonts.set(Q("hAnsi"), "Arial")
        size = etree.SubElement(rpr, Q("sz"))
        size.set(Q("val"), "19")
        size_cs = etree.SubElement(rpr, Q("szCs"))
        size_cs.set(Q("val"), "19")
        if index == 0:
            etree.SubElement(rpr, Q("b"))
        text = etree.SubElement(run, Q("t"))
        text.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
        text.text = line
        cell.append(paragraph)


def set_page_break_before(paragraph):
    ppr = paragraph.find(Q("pPr"))
    if ppr is None:
        ppr = etree.Element(Q("pPr"))
        paragraph.insert(0, ppr)
    if ppr.find(Q("pageBreakBefore")) is None:
        etree.SubElement(ppr, Q("pageBreakBefore"))


def make_page_break():
    paragraph = etree.Element(Q("p"))
    ppr = etree.SubElement(paragraph, Q("pPr"))
    spacing = etree.SubElement(ppr, Q("spacing"))
    spacing.set(Q("before"), "0")
    spacing.set(Q("after"), "0")
    spacing.set(Q("line"), "20")
    spacing.set(Q("lineRule"), "exact")
    run = etree.SubElement(paragraph, Q("r"))
    page_break = etree.SubElement(run, Q("br"))
    page_break.set(Q("type"), "page")
    return paragraph


def update_report_block(elements, report):
    tables = [element for element in elements if element.tag == Q("tbl")]
    period_table = tables[3]
    period_cells = period_table.xpath(".//w:tc", namespaces=NS)
    replace_cell_text(period_cells[1], f"WEEK #: {report['week']}")
    replace_cell_text(period_cells[2], f"COVERED DATE: {report['date']}")

    description_table = tables[4]
    description_cell = description_table.xpath(".//w:tc", namespaces=NS)[0]
    build_description(description_cell, report["lines"])


def main():
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(SOURCE, "r") as source_zip:
        document_xml = source_zip.read("word/document.xml")
        root = etree.fromstring(document_xml)
        body = root.find(Q("body"))
        children = list(body)
        primary_sect_pr = deepcopy(root.xpath("//w:sectPr", namespaces=NS)[0])

        # The final empty paragraph creates the blank second page in the source.
        # Keep the complete form through the attestation table and omit that overflow paragraph.
        pattern = [deepcopy(element) for element in children[:17]]
        # The source splits one physical form page into three continuous sections.
        # For a multi-page compilation, those breaks make alternate pages inherit
        # the source's blank overflow header. One consistent section preserves the
        # visible page-one header and footer on every report page.
        for element in pattern:
            for section_properties in element.xpath(".//w:sectPr", namespaces=NS):
                section_properties.getparent().remove(section_properties)
        for element in list(body):
            body.remove(element)

        for report_index, report in enumerate(REPORTS):
            block = [deepcopy(element) for element in pattern]
            update_report_block(block, report)
            if report_index > 0:
                body.append(make_page_break())
            for element in block:
                body.append(element)
        body.append(primary_sect_pr)

        new_document_xml = etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone="yes")
        with ZipFile(OUTPUT, "w", ZIP_DEFLATED) as output_zip:
            for item in source_zip.infolist():
                data = new_document_xml if item.filename == "word/document.xml" else source_zip.read(item.filename)
                output_zip.writestr(item, data)

    print(OUTPUT)


if __name__ == "__main__":
    main()
