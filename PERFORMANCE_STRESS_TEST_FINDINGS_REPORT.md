# SealGuard Performance and Stress-Testing Findings Report

## Purpose

This report summarizes the performance and stress-testing work performed on SealGuard, with emphasis on the initial problems discovered, the corrective actions taken, and the final evidence available for manuscript documentation.

Testing was performed against a disposable local Django environment using SQLite, local filesystem media storage, a disposable Clerk account, and Locust. The test database and media directory were not production data.

## Initial problems

### 1. Repetitive performance testing was too manual

The original performance plan required repeated concurrency levels, timed runs, upload workloads, verification workloads, and endurance testing. Running these manually through the Locust interface was slow and made it difficult to preserve consistent evidence.

### 2. The first load-ramp automation stopped during user spawning

The initial PERF-02 PowerShell runner treated every state other than `running` as completion. Locust reports `spawning` before it reports `running`, so the 10-, 20-, 30-, and 50-user levels in the first attempt stopped prematurely.

### 3. Percentile values were saved as null

Locust returned percentile fields whose names contained decimal points. PowerShell interpreted those names as nested properties, so p95 and p99 values were not written correctly to the JSON output.

### 4. Concurrent uploads produced artificial failures

The mixed workload initially reused the same title and filename across concurrent upload users. This drove the duplicate-document and revision workflow simultaneously, causing title/filename collisions and revision races. The result was a 4.67% failure rate in the first PERF-06 run.

### 5. Media assets disappeared in the release-style test environment

With `DEBUG=False`, the existing static-media behavior did not serve the seal and logo assets correctly. Contract-row logos and the site logo appeared broken during testing.

### 6. Size-specific PDF fixtures were unavailable

PERF-04 and PERF-09 required controlled 1 MB, 5 MB, and near-limit PDFs. The project did not initially contain a complete fixture set for those measurements.

### 7. Native Locust exports were not retained

The original runs were started through the Locust web API without native `--csv` or `--html` output options. Therefore, the original raw Locust export format was not preserved for every test.

## Corrective actions

### Automated Locust runners

PowerShell runners were added for the load ramp and remaining timed workloads. The runners reset Locust statistics, start each scenario, wait until Locust genuinely reaches the `stopped` state, capture aggregate statistics, and save JSON results.

The spawning-state bug was fixed by waiting until the state was exactly `stopped`, ensuring that a run could not be recorded while users were still being created.

### Correct percentile extraction

The percentile fields were changed to use quoted PowerShell property access. The corrected runners now retain Locust p95 and p99 values.

### Independent upload identities

The Locust upload harness now appends UUID fragments to titles and filenames. This separates normal concurrent-upload performance from intentional duplicate/revision testing. Follow-up focused upload testing completed with zero HTTP failures.

### Media-serving correction

An explicit media-serving route was added for the release-style local test configuration. After the correction, the seal and site logo assets loaded normally during the performance environment.

### Controlled PDF fixtures

Thirty valid disposable PDF fixtures were generated:

- 10 files at exactly 1,000,000 bytes;
- 10 files at exactly 5,000,000 bytes; and
- 10 files at exactly 14,500,000 bytes, below the 15 MB limit.

Each generated file was reopened successfully with `pypdf` and confirmed to contain PDF pages. The fixtures were excluded from Git.

### Evidence organization

Captured Locust statistics were split into separate folders under `performance/results/locust_reports/PERF-01` through `PERF-09`. Each folder contains JSON and/or CSV statistics and a report note. PERF-01 and PERF-06 are labeled as reconstructed from recorded Locust output because native exports were not retained during those runs.

## Final test findings

| Test | Finding |
|---|---|
| PERF-01 | Sequential baseline passed. Login, list, search, public verification, and verification POST had zero failures; normal-page p95 values were below 3 seconds. |
| PERF-02 | All planned levels were exercised. The corrected 10-, 20-, 30-, and 50-user run recorded one failure at 10 users and zero failures at 20, 30, and 50 users. |
| PERF-03 | Three 30-user login repetitions completed with zero failures. |
| PERF-04 | Exactly 105 controlled-size uploads completed with zero recorded HTTP failures. |
| PERF-05 | 15,302 verification requests completed with 2 failures, or approximately 0.013%. Average response time was 2.55 seconds, p95 was 10 seconds, and p99 was 17 seconds. |
| PERF-06 | The initial mixed run produced 4.67% failures because of duplicate upload identities. After unique titles and filenames were introduced, focused uploads completed with zero failures. |
| PERF-07 | Three sudden-spike repetitions completed with zero failures. |
| PERF-08 | The two-hour, 10-user endurance run completed 82,259 requests with 23 failures, or approximately 0.028%. Average response time was 555 ms, p95 was 1.8 seconds, and p99 was 2.6 seconds. No crash or restart occurred. |
| PERF-09 | Thirty controlled-size fixture uploads completed with zero recorded HTTP failures. Aggregate-window p95 values were 1.2 seconds for 1 MB, 1.2 seconds for 5 MB, and 2.7 seconds for 14.5 MB. |

## Interpretation

The corrective work improved test reliability and removed artificial upload failures caused by shared document identities. The application remained available through the spike and endurance tests, and no crash or restart was observed.

The results are not completely failure-free. PERF-05 and PERF-08 recorded small numbers of failed requests, and PERF-06 demonstrated that concurrent duplicate/revision operations require separate treatment from independent upload performance. These findings should remain in the manuscript as observed limitations rather than being omitted.

Locust measures HTTP-level behavior. It does not independently prove cryptographic correctness, database consistency, audit completeness, absence of cross-user data leakage, or absence of incomplete records. Those claims require separate database, audit, security, and functional checks.

## Evidence locations

- `performance/results/locust_reports/` - per-test Locust-derived evidence;
- `performance/results/perf02-load-ramp.json` - load-ramp capture;
- `performance/results/perf-remaining.json` - login, verification, spike, and endurance captures;
- `performance/results/perf-fixture-runs.json` - controlled-size upload captures;
- `performance/fixtures/manifest.json` - generated PDF sizes and page validation;
- `PERFORMANCE_STRESS_TEST_REPORT.md` - consolidated performance report.

## Conclusion

SealGuard completed the planned performance and stress-test set in the disposable environment. The main initial issues were manual test execution, premature load-ramp termination, missing percentile export, artificial concurrent-upload collisions, release-style media serving, missing size-controlled fixtures, and incomplete evidence retention. These were addressed through automated runners, corrected Locust-state handling, percentile extraction, unique upload identities, explicit media serving, controlled PDF fixtures, and organized per-test evidence reports.
