# F. Cybersecurity and Penetration Testing Report

## 1. Report Information

**System:** SealGuard contract-authentication system  
**Environment:** Local authorized staging-capable checkout  
**Date performed:** 5 September 2026  
**Tester:** SealGuard development/testing team  
**Reference:** [OWASP Web Security Testing Guide](https://owasp.org/www-project-web-security-testing-guide/stable/)

Testing was performed only against the local project environment. No unauthorized external system was tested. Existing working-tree changes were preserved.

## 2. Objective and Scope

The objective was to evaluate the application against common web-application security risks involving authentication, authorization, object access, CSRF, uploads, direct file access, error handling, and session protection. The requested test plan also included SQL injection, XSS, and automated staging scans.

The assessment used Django’s test client, the project’s automated test suite, framework deployment checks, source inspection, and controlled malformed requests. Tests that require a real browser, HTTPS deployment, an intercepting proxy, or an external scanner are identified as incomplete rather than being reported as passed.

## 3. Executive Summary

The current automated evidence is generally positive for authentication, role-based authorization, invalid-upload validation, direct media-file access, malformed-request handling, and session invalidation. The complete Django test suite passed with **101/101 tests**.

However, this is not a complete penetration test. The required 100-request SQL injection matrix, 60-submission XSS matrix, full 40-attempt IDOR matrix, and three automated staging scans were not completed. The deployment security check also identified five configuration warnings, including insecure cookie settings for an HTTPS deployment.

The system should not be described as having zero security findings until the incomplete tests are performed and the deployment configuration is hardened.

## 4. Test Results

| ID | Test area | Automated evidence and result | Status |
|---|---|---|---|
| SEC-01 | SQL injection | The complete required matrix of at least 20 database-related parameters × 5 payload categories, or 100 requests, was not completed. | Incomplete |
| SEC-02 | Cross-site scripting | Existing tests cover tutorial HTML sanitization and template escaping. The required 20 text inputs × reflected, stored, and client-rendered contexts, or 60 submissions, was not completed. | Partial |
| SEC-03 | CSRF | Thirty controlled requests were sent to 10 state-changing endpoints under missing-token, invalid-token, and cross-origin conditions. All 30 were rejected with HTTP 400. | Pass with note |
| SEC-04 | Authorization bypass | The existing authentication matrix tested protected actions as Administrator, Clerk, and unauthenticated users. The 24-attempt role matrix passed. | Pass |
| SEC-05 | IDOR | Existing object-level authorization tests verified that another user could not view, edit, delete, or download a private contract. The required 10 private contracts × 4 actions, or 40 attempts, was not completed. | Partial |
| SEC-06 | Upload abuse | The project’s DOC-02 invalid-upload matrix tested 30 invalid files, including wrong extensions, empty files, malformed PDFs, renamed non-PDF files, oversized files, and password-protected PDFs. All 30 were rejected by validation. | Pass for validation |
| SEC-07 | Direct encrypted-file access | Ten guessed direct media URLs and ten copied-storage-path traversal-style URLs were requested. All 20 returned HTTP 404 and disclosed no file. | Pass |
| SEC-08 | Error disclosure | Thirty malformed requests covered login, upload, search, verification, and folder-related paths. No traceback, secret, sensitive configuration, or filesystem path was found in the responses. | Pass |
| SEC-09 | Session protection | Existing tests passed login/logout and session-reuse invalidation checks. Current settings show `HttpOnly=True` and `SameSite=Lax`, but `SESSION_COOKIE_SECURE=False` and `CSRF_COOKIE_SECURE=False`. | Partial / finding |
| SEC-10 | Automated staging scan | Three complete authorized scans using an automated scanner were not performed because no ZAP, Nikto, Nuclei, Semgrep, or Bandit scanner was available in the environment. | Incomplete |

## 5. Detailed Automated Evidence

### 5.1 Regression and application tests

The following commands were run with `DEBUG=False` using the project virtual environment:

```text
python manage.py test --verbosity 1
Result: 101 tests passed.

python manage.py test contracts.tests.AuthenticationMatrixTests --verbosity 1
Result: 10 tests passed.

python manage.py test contracts.tests.AuthenticationMatrixTests contracts.tests.DocumentManagementTests --verbosity 1
Result: 27 tests passed.

python manage.py migrate --check
Result: No unapplied migrations.
```

These tests provide evidence for authentication behavior, login lockout, logout invalidation, role permissions, object-level authorization, invalid uploads, document downloads, and related document-management controls.

### 5.2 CSRF testing

Thirty requests were issued as follows:

- 10 state-changing endpoints;
- missing CSRF token;
- invalid CSRF token;
- cross-origin request with an invalid token.

All 30 requests were rejected with HTTP 400. The application therefore rejected the requests, although its CSRF failure response is HTTP 400 rather than the HTTP 403 response stated in the requested test table. If the manuscript requires the exact status code 403, the application’s CSRF failure handler and corresponding tests should be aligned with that requirement.

### 5.3 Direct file access

Twenty unauthenticated requests were made against guessed encrypted-document URLs and copied storage-path variants. All responses were HTTP 404. No encrypted document or private storage file was returned.

### 5.4 Error disclosure

Thirty malformed requests were sent across login, upload, search, verification, and folder paths. Response bodies were checked for tracebacks, private keys, secret configuration, database paths, media-root paths, and filesystem locations. No such sensitive disclosure was detected.

## 6. Deployment Security Check

`python manage.py check --deploy` reported the following warnings:

1. `SECURE_HSTS_SECONDS` is not configured.
2. `SECURE_SSL_REDIRECT` is not enabled.
3. `SESSION_COOKIE_SECURE` is disabled.
4. `CSRF_COOKIE_SECURE` is disabled.
5. `X_FRAME_OPTIONS` is `SAMEORIGIN` rather than `DENY`.

These settings may be acceptable for local HTTP development, but they should be reviewed and hardened before production or HTTPS-only deployment. HSTS should be enabled only after confirming that the complete deployed site and required subdomains work correctly over HTTPS.

## 7. Limitations and Outstanding Work

The following work remains before claiming complete penetration-test coverage:

- Execute 100 authorized SQL injection requests across all database-related parameters and five safe payload categories.
- Execute 60 XSS submissions covering reflected, stored, and client-rendered contexts, then inspect responses in a real browser.
- Repeat IDOR testing against 10 separately owned private contracts using another authenticated user.
- Test invalid upload files through the actual HTTP upload endpoint and confirm that no file executes or creates a contract record.
- Run three complete authorized scans using OWASP ZAP or an equivalent scanner after manual testing.
- Verify session cookie flags through the actual HTTPS staging deployment and review session rotation with browser developer tools.
- Perform browser-based testing for JavaScript DOM sinks that use `innerHTML`, especially dynamically rendered dashboard, contract-list, and verification content.

## 8. Conclusion

Based on the evidence completed on 5 September 2026, SealGuard’s automated security regression coverage passed for the tested authentication, authorization, invalid-upload validation, direct-file-access, malformed-request, and session-invalidation scenarios. The full application test suite also passed 101 of 101 tests.

The assessment remains **partially complete** because several requested penetration-testing matrices and the automated staging scans were not performed. The most immediate configuration finding is that session and CSRF cookies are not marked Secure, and HTTPS redirect/HSTS protection is not enabled. These settings should be addressed or explicitly documented as development-only exceptions before deployment.

Therefore, the appropriate manuscript conclusion is: **“The implemented automated security checks passed for the tested scenarios, while the complete penetration-testing scope remains in progress pending the outstanding SQL injection, XSS, expanded IDOR, browser-based upload, HTTPS session, and automated scanner tests.”**

## 9. Follow-up automated matrix (12 September 2026)

The previously incomplete application-boundary checks were automated in
`contracts/security_tests.py` and run against Django's isolated test database
with `DEBUG=False`:

```text
python manage.py test contracts.security_tests --verbosity 1
Result: 4 test groups passed.
```

The run covered:

| Area | Automated coverage | Result |
|---|---:|---|
| SQL injection safety | 20 database-related inputs × 5 harmless payloads = 100 requests | Pass; no server error, traceback, or database error disclosed |
| XSS safety | 20 payloads × reflected, stored, and client-data contexts = 60 checks | Pass in server-side tests; dangerous HTML was not returned as executable markup |
| Public object boundary | 10 private contracts × 4 unauthenticated actions = 40 requests | Pass; private objects were not exposed to the public client |
| HTTP upload abuse | 30 invalid upload requests | Pass; no contract records were created |

The IDOR test follows SealGuard's stated policy that authenticated staff may
see shared system documents; it therefore verifies that unauthenticated users
cannot access private objects. Browser execution of JavaScript, HTTPS cookie
flags, and third-party scanner findings still require separate browser,
HTTPS-staging, or scanner runs. No destructive SQL or external target testing
was performed.
