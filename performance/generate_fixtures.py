"""Create valid disposable PDF fixtures at controlled file sizes.

The source PDF remains intact. Extra bytes are inserted as PDF comments before
the final EOF marker, so the files remain parseable while exercising upload
size and processing-time limits.
"""

from pathlib import Path
from pypdf import PdfReader


ROOT = Path(__file__).resolve().parent
SOURCE = ROOT.parent / "output" / "pdf" / "01_basic_contract.pdf"
DEST = ROOT / "fixtures"
TARGETS = {
    "1mb": 1_000_000,
    "5mb": 5_000_000,
    "near_limit": 14_500_000,
}


def make_fixture(source: bytes, target: int, path: Path) -> None:
    startxref = source.rfind(b"startxref")
    if startxref < 0 or source.rfind(b"%%EOF") < 0:
        raise ValueError(f"Source is missing PDF trailer markers: {SOURCE}")
    # Insert comments before the trailer's startxref keyword. Inserting them
    # between startxref and %%EOF would corrupt the trailer integer.
    header = source[:startxref]
    footer = source[startxref:]
    if len(header) >= target:
        raise ValueError(f"Source is already larger than target {target}: {path}")
    filler_size = target - len(header) - len(footer)
    # PDF comments may contain arbitrary bytes except line-ending concerns;
    # use printable ASCII and keep each comment line bounded.
    line = b"% SealGuard performance fixture padding 0123456789abcdef\n"
    repeats, remainder = divmod(filler_size, len(line))
    padding = line * repeats + line[:remainder]
    if padding and not padding.endswith(b"\n"):
        padding = padding[:-1] + b"\n"
    path.write_bytes(header + padding + footer)


def main() -> None:
    if not SOURCE.is_file():
        raise FileNotFoundError(SOURCE)
    source = SOURCE.read_bytes()
    DEST.mkdir(parents=True, exist_ok=True)
    manifest = []
    for band, target in TARGETS.items():
        for index in range(1, 11):
            path = DEST / f"perf_{band}_{index:02d}.pdf"
            make_fixture(source, target, path)
            reader = PdfReader(str(path), strict=False)
            if len(reader.pages) < 1:
                raise ValueError(f"Generated PDF has no pages: {path}")
            manifest.append({"file": path.name, "bytes": path.stat().st_size, "pages": len(reader.pages)})
    (DEST / "manifest.json").write_text(__import__("json").dumps(manifest, indent=2), encoding="utf-8")
    print(f"Created {len(manifest)} valid PDFs in {DEST}")
    for band, target in TARGETS.items():
        sizes = [item["bytes"] for item in manifest if item["file"].startswith(f"perf_{band}_")]
        print(f"{band}: {len(sizes)} files, {min(sizes):,}-{max(sizes):,} bytes")


if __name__ == "__main__":
    main()
