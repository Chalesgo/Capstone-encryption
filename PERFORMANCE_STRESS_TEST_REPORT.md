# SealGuard Performance and Stress-Test Report

## Purpose

This report consolidates the Section G performance and stress testing performed against the SealGuard Django application. The tests used a disposable local database and media directory, a local Django development server, and Locust workloads. They were not run against production data.

The test environment used Windows, Django at `http://127.0.0.1:8000`, Locust at port 8089, SQLite, local filesystem media storage, and a disposable Clerk account. During the runs, the database contained approximately 3,000 disposable document records and the C: drive had approximately 60 GB of free space.

## Test implementation

The Locust workload was configured for browser-like login, contract-list and search requests, dashboard requests, public verification, and uploads. Upload requests were changed to use unique titles and filenames so normal concurrent-upload performance was measured instead of duplicate/revision collision behavior.

Thirty valid disposable PDF fixtures were also generated for the size-based tests:

- 10 files at exactly 1,000,000 bytes;
- 10 files at exactly 5,000,000 bytes; and
- 10 files at exactly 14,500,000 bytes, below the 15 MB upload limit.

Each fixture reopened successfully with `pypdf` and contained PDF pages. The fixtures were intentionally disposable and excluded from Git.

## Consolidated results

| ID | Scenario | Result | Key evidence |
|---|---|---|---|
| PERF-01 | Sequential baseline | Passed | Login, list, search, public view, and verification each completed with zero failures. Normal-page p95 values were within 3 seconds. |
| PERF-02 | Load ramp | Completed with findings | 1, 5, 10, 20, 30, and 50-user levels were exercised. The corrected run recorded zero failures at 20, 30, and 50 users; the 10-user level had one failure in its completed run. |
| PERF-03 | Concurrent login | Passed | Three 30-user repetitions completed with 332, 337, and 336 requests and zero failures. Average response times were 865 ms, 945 ms, and 897 ms. |
| PERF-04 | Concurrent upload | Passed at HTTP level | Exactly 105 fixture uploads were executed across 5-, 10-, and 20-user levels and the three size bands. All recorded fixture windows had zero HTTP failures. |
| PERF-05 | Concurrent verification | Completed with findings | 15,302 requests completed with 2 failures, a 0.013% failure rate. Average response time was 2.55 seconds; p95 was 10 seconds and p99 was 17 seconds. |
| PERF-06 | Mixed workload | Completed with findings and follow-up validation | The initial 15-minute mixed run produced 15,674 requests and 732 failures, a 4.67% failure rate. Failures were concentrated in concurrent uploads caused by duplicate titles/filenames and revision races. Unique-title and unique-filename upload follow-up testing completed with zero failures, although upload latency remained high. |
| PERF-07 | Sudden spike | Passed at HTTP level | Three 50-user, 5-users/second repetitions completed with zero failures. Requests per repetition were 1,476, 1,609, and 1,351. |
| PERF-08 | Endurance | Completed with findings | Ten users ran for two hours, generating 82,259 requests. There were 23 failures, a 0.028% failure rate. Average response time was 555 ms, p95 was 1.8 seconds, and p99 was 2.6 seconds. No crash or restart occurred. |
| PERF-09 | PDF response-time workload | Passed at aggregate-window level | Thirty fixture uploads were exercised: 10 in each size band. Recorded aggregate p95 values were 1.2 seconds for 1 MB, 1.2 seconds for 5 MB, and 2.7 seconds for 14.5 MB. All windows had zero HTTP failures. |

## PERF-01 baseline measurements

The original 100-request-per-operation baseline recorded the following results:

| Operation | Requests | Failures | Average | p95 |
|---|---:|---:|---:|---:|
| Login | 104 | 0 | 370 ms | 400 ms |
| Contract list | 101 | 0 | 26 ms | 32 ms |
| Search | 101 | 0 | 15 ms | 17 ms |
| Public verification page | 103 | 0 | 13 ms | 15 ms |
| Public verification POST | 103 | 0 | 103 ms | 120 ms |

All normal-page p95 values were below the proposed 3-second threshold.

## Findings and corrective work

The first mixed-workload run exposed a test-harness and workload-design problem rather than a simple application availability failure. Concurrent users were submitting the same title and filename, which intentionally entered the duplicate/revision path and caused race-related upload failures. The Locust harness was corrected to generate unique titles and filenames for independent performance uploads. A focused follow-up upload run then completed with zero failures, though encryption/upload processing was slow under concurrency.

The media-serving configuration was also corrected for the `DEBUG=False` test environment so generated seal and logo assets continued to load during stress testing.

## Limitations

Locust establishes HTTP-level throughput, latency, and failure behavior. It does not by itself prove cryptographic correctness, database consistency, absence of cross-user data leakage, audit completeness, or absence of incomplete records. Those properties require separate database, audit-log, and security assertions.

The saved PERF-02 artifact from the earlier run did not retain every percentile field because the Locust JSON property name required PowerShell quoting. The live Locust statistics did expose percentile data, including an observed 50-user aggregate p95 of approximately 8.4 seconds. The corrected runner now quotes those fields correctly for future runs.

PERF-09 reports aggregate fixture-window timing, which includes the login and upload-page requests in addition to the upload POST. It should therefore be described as an aggregate performance measurement unless a separate upload-only timing extraction is required.

## Overall conclusion

The SealGuard application completed the full planned performance-test set without a crash or restart. Sequential operation, concurrent login, sudden-spike HTTP reliability, fixture upload windows, and fixture response windows met their stated HTTP-level outcomes. The principal findings were increased latency under concurrency, two failures during the focused verification run, 23 failures during the two-hour endurance run, and the initial mixed-workload upload collision behavior. These findings should remain documented in the manuscript rather than being reported as a completely failure-free stress evaluation.

Evidence files:

- `performance/results/perf02-load-ramp.json`
- `performance/results/perf-remaining.json`
- `performance/results/perf-fixture-runs.json`
- `performance/fixtures/manifest.json`
