# SealGuard Alpha Test Evidence

This folder holds the evidence behind the written Alpha Test reports in
`output/SealGuard_Alpha_Test_Reports/`. Those reports describe the findings;
this folder holds the test runs, result files, and test data they rest on.

## Layout

- **Files in this folder** are the evidence itself: the full automated test run,
  performance results, the test PDFs, and files restored from earlier commits.
- **Sections A–J** hold the test code for each Alpha Test section, plus that
  section's results from the 2026-11-02 run. The test files are copies. The
  runnable originals are in `contracts/`, `scripts/` and `performance/`.

| File prefix | What it is |
|---|---|
| `django_test_run_2026-11-02.txt` | Full verbose output of every Django test in `contracts/` |
| `perf_*` | Locust and fixture performance results, copied from `performance/results/` |
| `testpdf_*` | Test PDFs used for upload, accuracy and tamper testing, copied from `output/pdf/` |
| `restored_*` | Files deleted in commit `d46001b` (2026-09-05), restored from git history |

## Automated test run — 2026-11-02

Command (run from the repository root at commit `8834b1c`):

```
python manage.py test contracts --pattern="*test*.py" -v 2
```

Environment: Python 3.11.9, Django 4.2, SQLite in-memory test database.
A temporary `SECRET_KEY` and a throwaway RSA keypair were supplied for the run,
because the production `.env` and `keys/` are not stored in the repository.
Run time was 13:15–13:19, about 4 minutes.

**Result: 278 tests, 261 passed, 17 failed.** The log's last line reads
`FAILED (failures=46)` because Django counts each failing *subtest* separately.
The 17 failing tests contain 46 failing subtests between them.

| Section | Tests | Passed | Failed |
|---|---:|---:|---:|
| A Authentication and Access Control | 116 | 104 | 12 |
| B Document Management | 38 | 35 | 3 |
| C Cryptographic Unit Testing | 65 | 65 | 0 |
| D End-to-End Workflow | 9 | 8 | 1 |
| E System Accuracy and Tamper Detection | 44 | 44 | 0 |
| F Cybersecurity and Penetration | 4 | 3 | 1 |
| G Performance and Stress | Locust load tests, see `perf_*` files | | |
| H Reliability and Fault | 2 | 2 | 0 |
| I Browser and Viewport Compatibility | JavaScript test only, see below | | |
| J Mobile Authentication Application | No tests (section is conditional) | | |

### Failing tests

These failures are recorded as they are, not hidden. The "Likely cause" column
comes from reading each failure message and has not been confirmed test by test.
Most failures look like tests that were not updated after the interface,
routing or download format changed. Each section's `test_results_2026-11-02.txt`
has the full failure output.

| Test | Failure | Likely cause |
|---|---|---|
| A: `AuthenticationMatrixTests.test_auth07_rbac_role_permission_matrix` | Got 404, expected 403 or redirect | Access is still denied, but through a "not found" response instead of "forbidden" |
| A: `AuthenticationMatrixTests.test_auth07_clerk_granted_contract_delete_permission` | Got 404, expected 302 | Same as above |
| A: `AuthenticationMatrixTests.test_auth08_object_level_authorization_matrix` | Status codes differ from expected | Same as above |
| A: `AuditLogTests` (9 tests) | Expected page text, styles or routes not found | Login form, document list, verification layout and folder routes were redesigned after these tests were written |
| B: `test_bulk_staff_actions_move_status_download_and_encrypt` | 403, expected 200 | Bulk actions were restricted to administrators |
| B: `test_bulk_toolbar_allows_document_administrators` | Bulk status control not found on page | Bulk toolbar was redesigned |
| B: `test_download_names_use_system_title_and_version_number` | Download is `.sgpdf`, test expects `.pdf` | Downloads are now encrypted `.sgpdf` packages |
| D: `test_e2e04_persistence_after_new_client_session` | Got encrypted package bytes, test expects plain PDF bytes | Same `.sgpdf` change |
| F: `test_sec05_public_object_access_matrix_40_requests` | Got 404, expected 401, 403 or redirect | Private objects return "not found" to anonymous users; access is still denied |

## JavaScript tests — 2026-11-02 (Node 24.18.0)

| Script | Section | Result |
|---|---|---|
| `test_dashboard_verification.cjs` | E | Passed |
| `test_upload_preflight.cjs` | B | Assertions printed PASS, then the script crashed (`response.arrayBuffer is not a function`): the test's fake network response is out of date |
| `test_mobile_pdf.cjs` | I | Crashed (`sidebarViewport.matchMedia is not a function`): the test's fake browser window is out of date |

## Performance evidence (Section G)

The `perf_PERF-0N_*` files are Locust exports for PERF-01 to PERF-09.
As `perf_locust_README.md` explains, PERF-01 and PERF-06 were reconstructed
from recorded Locust output because native exports were not saved during
those runs. PERF-06 has a report only, with no CSV or JSON.

## Restored files

Commit `d46001b` (2026-09-05) removed these from the repository. They were
recovered from its parent commit `3541704`:

- `restored_Django_Automated_Testing_Report_2026-09-01.docx`: the earlier
  automated testing report (10 of 10 tests passed on 2026-09-01).
- `restored_Progress_Reports_Weeks_1_to_13.docx`: weekly progress reports,
  which record the dates when test suites were added and passed.
- `restored_render_*.png`: page renders of each test PDF, including
  `restored_render_all_pages_contact_sheet.png` showing all pages at once.
- `restored_testpdf_inspection_photo.png`: the generated image used when building
  the image-heavy test PDF (05).
- `E_System_Accuracy_and_Tamper_Detection/restored_verify_test_pdfs.py`: the
  script that checked each test PDF's page count and produced the renders.

## Not included

- Screenshots of manually confirmed checks, such as lockout messages, role
  views and phone or tablet layouts. These need to be captured from the
  running system.
- Section J evidence. The section stays conditional until it is decided
  whether a separate mobile application is in scope.
