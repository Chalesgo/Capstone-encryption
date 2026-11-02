from pathlib import Path

import fitz
from PIL import Image, ImageDraw
from pypdf import PdfReader


ROOT = Path(__file__).resolve().parents[2]
PDF_DIR = ROOT / "output" / "pdf"
RENDER_DIR = ROOT / "tmp" / "pdfs" / "rendered"
RENDER_DIR.mkdir(parents=True, exist_ok=True)

expected_pages = {
    "01_basic_contract.pdf": 1,
    "02_multipage_contract.pdf": 2,
    "03_long_text_stress.pdf": 2,
    "04_unicode_filipino_names.pdf": 1,
    "05_image_heavy_contract.pdf": 1,
    "06_blank_page_contract.pdf": 3,
    "07_metadata_rich_contract.pdf": 1,
    "08_tampered_basic_contract.pdf": 1,
}

rendered = []
for filename, expected_count in expected_pages.items():
    path = PDF_DIR / filename
    reader = PdfReader(str(path))
    if len(reader.pages) != expected_count:
        raise AssertionError(f"{filename}: expected {expected_count} pages, found {len(reader.pages)}")

    document = fitz.open(path)
    for page_number, page in enumerate(document):
        pix = page.get_pixmap(matrix=fitz.Matrix(1.25, 1.25), alpha=False)
        output = RENDER_DIR / f"{path.stem}_page_{page_number + 1}.png"
        pix.save(output)
        rendered.append((filename, page_number + 1, output))
    document.close()

blank_reader = PdfReader(str(PDF_DIR / "06_blank_page_contract.pdf"))
if (blank_reader.pages[1].extract_text() or "").strip():
    raise AssertionError("QA-06 page 2 is not blank")

metadata = PdfReader(str(PDF_DIR / "07_metadata_rich_contract.pdf")).metadata
if metadata.get("/Author") != "Barangay QA Team":
    raise AssertionError("QA-07 author metadata is incorrect")
if "metadata" not in (metadata.get("/Keywords") or ""):
    raise AssertionError("QA-07 keywords metadata is incorrect")

unicode_text = "".join(
    page.extract_text() or ""
    for page in PdfReader(str(PDF_DIR / "04_unicode_filipino_names.pdf")).pages
)
for expected in ("Niño", "Biñan", "Peña"):
    if expected not in unicode_text:
        raise AssertionError(f"QA-04 missing extracted text: {expected}")

thumb_width = 260
label_height = 44
gap = 16
columns = 4
thumbs = []
for filename, page_number, image_path in rendered:
    source = Image.open(image_path).convert("RGB")
    ratio = thumb_width / source.width
    resized = source.resize((thumb_width, int(source.height * ratio)), Image.Resampling.LANCZOS)
    tile = Image.new("RGB", (thumb_width, label_height + resized.height), "white")
    draw = ImageDraw.Draw(tile)
    draw.text((8, 7), filename, fill="#183153")
    draw.text((8, 24), f"Page {page_number}", fill="#52606d")
    tile.paste(resized, (0, label_height))
    thumbs.append(tile)

rows = (len(thumbs) + columns - 1) // columns
tile_height = max(tile.height for tile in thumbs)
sheet = Image.new(
    "RGB",
    (columns * thumb_width + (columns - 1) * gap, rows * tile_height + (rows - 1) * gap),
    "#dfe5ec",
)
for index, tile in enumerate(thumbs):
    x = (index % columns) * (thumb_width + gap)
    y = (index // columns) * (tile_height + gap)
    sheet.paste(tile, (x, y))
sheet.save(RENDER_DIR / "all_pages_contact_sheet.png")

print(f"Validated {len(expected_pages)} PDFs and rendered {len(rendered)} pages")
