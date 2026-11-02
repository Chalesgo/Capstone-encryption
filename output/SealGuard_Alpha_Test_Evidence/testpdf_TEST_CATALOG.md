# SEALGUARD PDF Test Corpus

These files are synthetic and contain no real procurement or personal data.

| File | Primary test | Expected use |
|---|---|---|
| `01_basic_contract.pdf` | Normal workflow | Upload, encrypt, download, verify, rename, delete, restore |
| `02_multipage_contract.pdf` | Multi-page handling | Confirm every page survives processing and version history remains valid |
| `03_long_text_stress.pdf` | Boundary values | Check long titles, table wrapping, search, and UI truncation |
| `04_unicode_filipino_names.pdf` | Character handling | Confirm `Niño`, `Biñan`, and Filipino text survive upload and extraction |
| `05_image_heavy_contract.pdf` | Embedded images | Exercise image hashing, file size, rendering, and stamping |
| `06_blank_page_contract.pdf` | Blank page | Confirm all three pages survive, including the blank second page |
| `07_metadata_rich_contract.pdf` | Metadata | Inspect metadata before and after SEALGUARD processing |
| `08_tampered_basic_contract.pdf` | Negative verification | Compare against QA-01; it must not be accepted as QA-01 |

## Suggested Integration Sequence

1. Upload QA-01 and verify the processed download.
2. Upload QA-01 as a new revision only when testing revision behavior.
3. Attempt to verify QA-08 against QA-01 and expect a tampered result.
4. Upload QA-02 and confirm its page count before and after processing.
5. Exercise QA-03 and QA-04 through list search, rename, tags, and folders.
6. Upload QA-05 and confirm its image is visible in the processed file.
7. Upload QA-06 and confirm the blank second page remains present.
8. Compare QA-07 metadata before and after processing.

## What This Corpus Does Not Prove

PDF samples support unit and integration tests for document processing. They do not complete browser compatibility, device compatibility, SQL injection, or brute-force testing. Those require automated HTTP/UI test suites and tests against a production-like deployment.
