# Bundled PDF.js

Source: the official `pdfjs-dist` npm package, version **5.4.624**.
https://www.npmjs.com/package/pdfjs-dist/v/5.4.624

The legacy build, matching worker, cmaps, standard fonts, wasm assets, and
Apache-2.0 LICENSE are kept together. Update them together when upgrading.
No PDF content is sent to an external viewer service.

`../mobile-pdf.html` and `../mobile-pdf.mjs` render one page at a time.
`../mobile-pdf-host.js` exposes the shared mobile overlay and embedded URL helper
used by Verify, Contracts, and Dashboard. Desktop URLs pass through unchanged.
The viewer requires same-origin static hosting with JavaScript MIME types for
`.mjs` files. The existing ngrok demo launcher enables Django static serving;
production must publish these assets using its normal static-file deployment.

PDFs are fetched using the current session; authorization stays at the existing
document endpoint. Download saves the loaded PDF through a blob link. Rendering
has no native PDF toolbar, annotations, or Google Drive integration. This is a
viewer UI, not a mechanism for preventing copying or screenshots.

Validation during implementation: JavaScript syntax, host routing/lifecycle
checks, Django static responses, and PDF.js canvas rendering of the repository's
basic, multipage, image-heavy, and Unicode fixtures. Browser/phone visual QA
must cover page navigation, zoom, download, close, and reopening a different
document, including uploaded blob and retained session previews on Verify.
