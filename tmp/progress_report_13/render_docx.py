import argparse
import shutil
from pathlib import Path

import fitz
import win32com.client


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("input_docx")
    parser.add_argument("--output_dir", required=True)
    parser.add_argument("--emit_pdf", action="store_true")
    parser.add_argument("--dpi", type=int, default=144)
    args = parser.parse_args()

    source = Path(args.input_docx).resolve()
    out_dir = Path(args.output_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    working_pdf = out_dir / f".{source.stem}-render.pdf"

    word = win32com.client.DispatchEx("Word.Application")
    word.Visible = False
    word.DisplayAlerts = 0
    doc = None
    try:
        doc = word.Documents.Open(str(source), ReadOnly=True, AddToRecentFiles=False)
        doc.ExportAsFixedFormat(str(working_pdf), 17)
    finally:
        if doc is not None:
            doc.Close(False)
        word.Quit()

    pdf = fitz.open(working_pdf)
    scale = args.dpi / 72.0
    matrix = fitz.Matrix(scale, scale)
    for index, page in enumerate(pdf, start=1):
        pix = page.get_pixmap(matrix=matrix, alpha=False)
        pix.save(out_dir / f"page-{index}.png")
    pdf.close()

    if args.emit_pdf:
        shutil.copy2(working_pdf, out_dir / f"{source.stem}.pdf")
    working_pdf.unlink(missing_ok=True)
    print(f"Rendered {index} page(s) to {out_dir}")


if __name__ == "__main__":
    main()
