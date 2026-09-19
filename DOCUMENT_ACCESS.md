# Document access

The current branch distinguishes Admin (superuser), Staff (`is_staff`), and read-only User accounts. Admin and Staff can view workspace documents. Editing is limited to Admin, the staff uploader, and authorized staff collaborators; read-only users can view assigned documents. Logging in as a User does not grant access to all PDFs. Public, non-trashed documents remain readable without logging in; publication does not grant editing rights.

In the document row menu, choose **Manage access**, enter an existing account's username, and choose **Grant access**. The same option is in the mobile Edit menu. Use **Revoke** to remove a collaborator. Only the uploader and administrators can manage access or publish the document. Staff collaborators can view, download, edit, revise, organize, and trash/restore the document. Read-only User collaborators receive viewing access only. Existing permanent-deletion actions are also limited to accessible documents. Grants and revocations are recorded in the activity log.

Migration 0038 fills ownership from version 1's creator, falling back to the existing recipient field (previously displayed as Uploaded by). Documents with neither record remain administrator-managed. Later revisions do not change the uploader.

The `/media/` route now checks the owning document before serving current PDFs, revisions, or seals. Evidence is restricted to its authorized audit audience; uploaded verification previews are session-bound. Unregistered and temporary files are denied, even to administrators. File responses disallow caching. Revocation prevents subsequent requests, not retention of bytes already delivered.

Deployment must route `/media/` through Django's protected endpoint. Do not configure nginx, IIS, a CDN, or object storage to expose `MEDIA_ROOT` directly. This repository's runserver route already uses the protected endpoint.

For advisor demonstrations, an integrity scan keeps a short-lived cache entry containing its active control state and per-document progress. Administrators can cancel a running scan from the dashboard. Cancellation stops at the next document boundary, preserves results already checked, removes the temporary cache, and leaves unreached PDFs unchecked. If the administrator does not cancel it, the scan completes normally, records real failures, and then removes the temporary cache. A cancellation or an incomplete scan never marks an unreached PDF as tampered.

## Whole-PDF encryption

PDF uploads, sealed outputs, revisions, audit evidence, and retained verification previews use authenticated AES-256-GCM encryption. Every file has a fresh key and nonce; the AES key is wrapped using RSA-OAEP/SHA-256 with the configured RSA public key. Permission-checked responses decrypt into memory, verify the authentication tag, and disable caching. The PDF content and existing authenticity fingerprints are preserved exactly. Public documents are encrypted in storage too, then decrypted for public viewing.

The existing AES-CBC fingerprint/seal pipeline remains separate. Skipping authenticity sealing still encrypts the PDF in storage. An unchanged exported copy remains authentic even if its possession was unauthorized.

Keep the configured RSA private key backed up securely, separately from PDF storage. Do not regenerate or replace the keys: existing encrypted files need the original private key. This implementation does not yet provide automatic key rotation. Existing media backups or files outside MEDIA_ROOT are not converted by the storage command.

For another checkout or deployment, pause uploads while performing the resumable conversion:

```powershell
$env:DEBUG='False'
python manage.py migrate --noinput
python manage.py encrypt_pdf_storage          # inspect and check existing envelopes
python manage.py encrypt_pdf_storage --apply  # atomic replacement and byte verification
```

Registered file paths (including older names without a .pdf suffix) and all media PDFs are included. A second run authenticates existing envelopes without double-encrypting them. File-serving routes reject unencrypted stored PDFs, so complete conversion before serving traffic.

## QR access requests

1. The uploader or Admin opens a document row menu and chooses **Download QR access sheet** (also in the mobile Edit menu).
2. The printable sheet contains a request URL and an opaque reference, with no private document title, PDF contents, credentials, or decryption key.
3. A recipient scans it and provides a name, email, optional organization, and reason. A six-digit code verifies email possession; it expires after 10 minutes and permits at most five attempts. Requests are rate-limited by email/IP.
4. Staff or Admin opens **PDF access requests** in navigation (`/access-requests/`) to approve or reject verified requests. Viewing can last 1 hour, 24 hours, or 3 days. Approved requests can be revoked.
5. The recipient refreshes the request page in the same verified browser and opens the approved version. The grant provides no account, editing permission, future-version access, or general workspace access. Reopening in a different browser requires a new verified request and approval. Email delivery errors are shown instead of claiming a code was delivered.

Set `PUBLIC_BASE_URL=https://your-stable-host` in the deployment environment for durable QR links. If unset, the sheet uses the current request origin; sheets created through localhost are local-only, and changing a temporary ngrok hostname invalidates old printed links. The configured email backend is used for codes. With the console backend, the code appears in the Django terminal for local testing; configure SMTP for real recipients. Approval does not send another email: the recipient checks the same page.

Revocation and expiry block subsequent PDF requests, but cannot erase bytes already delivered to a browser. Guest views and staff decisions are recorded in the audit log. This is access control, not copy prevention.

Implementation references: [PyCryptodome authenticated encryption](https://www.pycryptodome.org/src/cipher/modern) and [OWASP token expiry and brute-force protections](https://cheatsheetseries.owasp.org/cheatsheets/Forgot_Password_Cheat_Sheet.html).

Run focused checks with:

```powershell
$env:DEBUG='False'
python manage.py test contracts.pdf_approval_tests contracts.access_tests contracts.role_tests contracts.tests.DocumentManagementTests contracts.security_tests --noinput
python manage.py makemigrations --check --dry-run
```

## Validation (2026-09-17)

101 selected Django tests passed on a disposable test database, covering encrypted storage, guest OTP/approval/expiry/revocation, access and role boundaries, document management, cryptographic public verification, physical verification, end-to-end workflows, local security matrices, and integrity controls. Django system and migration-drift checks passed. The list, approval queue, and guest page rendered; four inline JavaScript blocks parsed successfully. A generated QR sheet was rendered and visually inspected, and its QR decoded to the exact request URL.

No browser was available for interactive desktop/phone validation. Email tests used the in-memory backend; this local checkout uses console delivery. Live SMTP, external HTTPS, and real-phone scanning remain deployment checks.

Local migration applied successfully: 6,876 files converted with byte-for-byte verification and zero conversion failures. Final inventory: 6787 media/registered PDF files, 0 unencrypted files, and 0 missing registered paths. Three authorized live previews returned HTTP 200 and matched the decrypted stored bytes. Unreferenced role-test fixtures were cleaned up after verifying their exact dummy payload; the role tests now isolate MEDIA_ROOT and their five tests passed again.
# Encrypted document downloads

Document and revision downloads, including documents inside bulk ZIPs, now use
`.sgpdf` packages. The packages contain encrypted PDF bytes and signed metadata
binding them to a particular document/file and ciphertext digest. Ordinary PDF
readers cannot open these packages. Keep both the RSA private key and Django
signing configuration when moving the installation.

Use **Open encrypted document (.sgpdf)** on the document list or visit
`/open-encrypted/`. SealGuard validates the uploaded package, checks current
document permission (or the same browser's unexpired, verified guest approval),
and authenticates/decrypts it in memory. It then opens the matching stored version
in SealGuard; the exact encrypted file must still be retained and unchanged.
Uploading does not create a new document or grant access. Revoked access also
blocks previously downloaded packages from reopening through this flow.

The canvas viewer is used on desktop and mobile. Its download action requests
an encrypted package from the server; it no longer exports decrypted PDF blobs.
Previews without a document download route do not offer a download button.
QR invitation sheets remain ordinary readable PDFs without document contents.

**Boundary:** this protects exported packages, not against extraction by an
authorized viewer. PDF.js receives decrypted PDF data to render inside the
browser. Authorized users can capture network responses or screenshots.
Previously exported plaintext PDFs are not retroactively encrypted. Strictly
preventing PDF bytes from reaching a browser would require a separate server-side
page-rendering design; even that cannot prevent screenshots.
