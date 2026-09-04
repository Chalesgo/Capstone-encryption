# Template execution contract

## Reference

- Path: C:\Users\griff\OneDrive\Documents\Progress Reports CAPSTONE\CAPSTONE 2\Integrative-Course-Progress-Report-Group-007-CAPSTONE 2 WEEK 1 TEMPLATE.docx
- SHA-256: 5D9A998A29EA25C4A3F1D72E2019D2C02F960F3388971983083FCFF5E45BCF52
- Rendered pages: 2. Page 1 contains the complete form; page 2 is visually blank except for the recurring footer.
- Sections: 3 continuous portrait sections, Letter 8.50 x 11.00 inches, left and right margins 0.00 inches, top margin 1.26 inches, bottom margin 0.29 inches.
- Evidence: template-reference-render, template-style-evidence.json, section audit, heading audit, image audit, field audit, footnote audit.

## Page system and recurring components

- Preserve the Mapua Malayan Colleges Laguna header image, revision block, form identifier, copy distribution line, and Office of Vice President for Academic Affairs footer.
- Preserve the centered black Arial title, the course/project/group metadata tables, the Capstone Project checkbox, the student identification table, adviser scheduling area, and attestation blocks.
- The source contains 20 floating anchors, including the header logo and footer/form furniture. Preserve their relationships and geometry.
- The source has no Word fields, footnotes, endnotes, or Heading styles.
- The second rendered page is an empty overflow artifact rather than a content pattern. The compiled output may remove it so that each report occupies one complete form page.

## Typography and tables

- Primary typeface: Arial. Student identity uses Times New Roman in selected cells.
- Title: centered, bold, black, approximately 16 pt.
- Metadata and labels: black, bold where present, mostly 8 to 10 pt.
- Work-description text: black Arial, approximately 10 to 11 pt, left aligned.
- Retain the original light-gray header fills, thin gray/black table borders, and the original column proportions.
- Allow work-description rows to expand; do not use fixed heights that clip text.

## Content flow and slot map

- Repeat the complete form-page pattern 13 times, one report per page.
- Preserve course, course code, section, term, research title, group code, student number, program, signature field, adviser fields, and coordinator fields.
- Editable slots in word/document.xml:
  - Week number cell in the work-period table.
  - Covered-date cell in the work-period table.
  - Single-cell description-of-work table.
- Replace the source Week 1 sample text. Do not retain its unsupported Firebase claim unless supported by repository evidence.
- Each work-description slot may contain a focus line, evidence-based accomplishment bullets, manuscript alignment, issue/resolution note, and next-sprint direction.

## Package preservation

- Preserve-only: [Content_Types].xml, package relationships, docProps, word/styles.xml, word/numbering.xml, word/settings.xml, word/theme, word/fontTable.xml, word/webSettings.xml, headers, footers, media, and all drawing relationships.
- Editable: word/document.xml body content and section layout needed to remove the blank overflow page and repeat the source form pattern.
- No custom XML, comments, tracked changes, footnotes, endnotes, or fields are required for the output.

## Fidelity gates

- The reference file must remain byte-for-byte unchanged at the recorded hash.
- Every report page must visibly retain the original institutional form and recurring page furniture.
- Each report must fit on one page with no clipped text, overlapping anchors, broken tables, blank overflow pages, or split attestation blocks.
- Reports must be numbered 1 through 13 and use the intended covered dates.
