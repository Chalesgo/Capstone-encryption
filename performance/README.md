# SealGuard performance testing

This directory contains the Locust workload used for PERF-01 through PERF-09.
It measures HTTP behavior against a running SealGuard instance; it is not a
replacement for Django functional or cryptographic tests.

## Preparation

Use a disposable staging/demo database with prepared contracts and a test
account. Do not use production credentials or the production database. The
default fixture is `output/pdf/01_basic_contract.pdf`; provide larger 1 MB,
5 MB, and near-limit PDFs with `SEALGUARD_PDF` when testing upload sizes.

Install the optional dependency from the project root:

```powershell
python -m pip install -r performance/requirements.txt
```

Because this checkout currently has `DEBUG=release` in `.env` while Django
expects a boolean, start the test server with an explicit override:

```powershell
$env:DEBUG='True'
$env:SEALGUARD_USERNAME='performance-user'
$env:SEALGUARD_PASSWORD='use-a-test-secret'
python manage.py runserver 127.0.0.1:8000 --noreload
```

Run Locust from the project root. Credentials are read only from environment
variables and are not written to results:

```powershell
python -m locust -f performance/locustfile.py --host http://127.0.0.1:8000
```

The default `MixedWorkloadUser` task weights implement PERF-06: 40% list/search,
25% verification, 15% login, 10% upload, and 10% dashboard. Select a focused
workload with `--user-classes`:

```powershell
python -m locust -f performance/locustfile.py --user-classes ViewSearchUser --users 50 --spawn-rate 1 --run-time 15m --headless
python -m locust -f performance/locustfile.py --user-classes VerificationUser --users 50 --spawn-rate 1 --run-time 15m --headless
python -m locust -f performance/locustfile.py --user-classes UploadUser --users 20 --spawn-rate 1 --run-time 5m --headless
python -m locust -f performance/locustfile.py --user-classes LoginUser --users 30 --spawn-rate 1 --run-time 30s --headless
```

For PERF-02, repeat the same command at 1, 5, 10, 20, 30, and 50 users for
five minutes each. For PERF-07, use `--users 50 --spawn-rate 5 --run-time 2m`
and repeat three times. For PERF-08, use 10 users for two hours. Record the
Locust CSV/HTML output for each run.

PERF-01's exact 100-request-per-operation sequence is easiest to run as five
focused 100-request runs, one each for login, list, search, public view, and
verification. Locust reports request count, failures, average, and p95; the
normal-page p95 acceptance threshold is 3 seconds.

## Evidence and limitations

After each run, independently query the database and audit records for record
counts, duplicate/incomplete contracts, verification classifications, and
unexpected server errors. Locust alone cannot prove cryptographic correctness,
database isolation, absence of cross-user leakage, or no data corruption.

The standard Django test suite uses an isolated test database. Locust runs use
the configured staging database and therefore require explicit cleanup and
backup procedures. Do not report any PERF result until the server environment,
database, storage, network, file sizes, Locust configuration, and output files
have been recorded.
