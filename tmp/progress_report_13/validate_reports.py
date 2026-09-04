from hashlib import sha256
from pathlib import Path
from zipfile import ZipFile

import fitz


source = Path(r"C:\Users\griff\OneDrive\Documents\Progress Reports CAPSTONE\CAPSTONE 2\Integrative-Course-Progress-Report-Group-007-CAPSTONE 2 WEEK 1 TEMPLATE.docx")
output = Path(r"C:\Users\griff\Downloads\OJT REQUIREMENTS FOLDER\For Showing stuff\Capstone-encryption\output\reports\SealGuard_Integrative_Course_Progress_Reports_Weeks_1_to_13.docx")
rendered_pdf = Path(r"C:\Users\griff\Downloads\OJT REQUIREMENTS FOLDER\For Showing stuff\Capstone-encryption\tmp\progress_report_13\final-render-3\SealGuard_Integrative_Course_Progress_Reports_Weeks_1_to_13.pdf")

with ZipFile(source) as original, ZipFile(output) as final:
    original_parts = set(original.namelist())
    final_parts = set(final.namelist())
    print("zip_parts_equal", original_parts == final_parts, "part_count", len(original_parts))
    changed = []
    for name in sorted(original_parts & final_parts):
        if name == "word/document.xml":
            continue
        if sha256(original.read(name)).digest() != sha256(final.read(name)).digest():
            changed.append(name)
    print("changed_preserve_parts", changed)

pdf = fitz.open(rendered_pdf)
print("pdf_pages", len(pdf))
for index, page in enumerate(pdf, start=1):
    text = " ".join(page.get_text().split())
    print(
        index,
        "week=" + str(f"WEEK #: {index}" in text),
        "title=" + str("INTEGRATIVE COURSE PROGRESS REPORT" in text),
        "footer=" + str("OVPAA-039-09" in text),
        "chars=" + str(len(text)),
    )

print("source_sha256", sha256(source.read_bytes()).hexdigest().upper())
print("output_sha256", sha256(output.read_bytes()).hexdigest().upper())
