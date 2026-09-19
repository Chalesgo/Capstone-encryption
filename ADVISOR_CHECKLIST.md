# Advisor checklist — agreed interpretation

Updated following the user's clarification: activation password change and forgot
password satisfy the password requirement; staff may view other staff documents
but need document rights to edit; console email is accepted temporarily; an OTP
attempt cap is not required for this checklist; authorized PDF extraction is
accepted. These are acceptance decisions, not additional security guarantees.

| Requirement | Status under the agreed scope |
| --- | --- |
| Remove remove-encryption option from upload | Fulfilled in the upload UI. Storage encryption still applies to legacy skip-sealing requests. |
| Encrypted exported PDFs, opened through SealGuard | Fulfilled. Downloads use .sgpdf; authorized viewer extraction is an accepted limitation. |
| Integration tests | Fulfilled: integration coverage exists. The prior audit identified outdated regression expectations that still need maintenance. |
| Maximum PDF page count and sourced justification | Outstanding. A size limit is not a page-count limit. |
| Change password | Fulfilled by activation password change and forgot-password recovery. No additional account-menu screen required. |
| Account creation email OTP | Fulfilled for the current demo scope; console delivery temporarily accepted. |
| Login security | Fulfilled for the agreed scope. OTP attempt limiting is not an acceptance requirement. |
| Drafts, revisions, and side-by-side comparison | Implemented: select two saved versions, view both PDFs, inspect version metadata and extracted-text changes. |
| Owner-controlled editing and shared rights | Fulfilled. Other staff may view; editing remains permission checked. |
| Folders | Fulfilled. |
| Draft and Final copy statuses | Fulfilled. |
| Me label | Outstanding. |
| Automatic publication after document approval | Outstanding. Guest access approval must remain distinct from document publication. |
| Revision attribution (revision recipient) | Fulfilled under the accepted attribution interpretation: Added by identifies the revision uploader. |

## Using comparison

Choose **Compare revisions** from a document's action menu, or open **Versions**
in the SealGuard viewer and choose **Compare revisions side by side**. At least
two saved versions are required. The most recent two are selected initially.
Use the left/right selectors to compare any pair.

Each panel shows version number, uploader, upload date/time, source, total pages,
and decrypted PDF size. The current document status is displayed separately;
it is not presented as historical approval status for each version.

The summary compares extractable text, including seals and footers. It does not
perform OCR, identify image/layout changes automatically, or replace integrity
verification. Text analysis is bounded to 100 pages and 60,000 characters per
version, with a visible warning if truncated; this is not an upload page limit.
The complete documents remain viewable. Small screens stack the two panels.
