"""Split captured Locust results into panel-friendly per-test folders."""

from __future__ import annotations

import csv
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parent
RESULTS = ROOT / "results"
REPORTS = RESULTS / "locust_reports"


def load(name: str):
    return json.loads((RESULTS / name).read_text(encoding="utf-8-sig"))


def write_json(folder: Path, name: str, data) -> None:
    (folder / name).write_text(json.dumps(data, indent=2), encoding="utf-8")


def write_csv(folder: Path, name: str, rows: list[dict]) -> None:
    if not rows:
        return
    fields = list(rows[0])
    with (folder / name).open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def write_md(folder: Path, title: str, body: str) -> None:
    (folder / "locust-report.md").write_text(
        f"# {title}\n\n{body}\n",
        encoding="utf-8",
    )


def main() -> None:
    REPORTS.mkdir(parents=True, exist_ok=True)
    for old in REPORTS.iterdir():
        if old.is_dir():
            for child in old.iterdir():
                child.unlink()
            old.rmdir()

    perf01 = REPORTS / "PERF-01"
    perf01.mkdir()
    rows01 = [
        {"operation": "login", "requests": 104, "failures": 0, "average_ms": 370, "p95_ms": 400},
        {"operation": "contract list", "requests": 101, "failures": 0, "average_ms": 26, "p95_ms": 32},
        {"operation": "search", "requests": 101, "failures": 0, "average_ms": 15, "p95_ms": 17},
        {"operation": "public verification page", "requests": 103, "failures": 0, "average_ms": 13, "p95_ms": 15},
        {"operation": "public verification POST", "requests": 103, "failures": 0, "average_ms": 103, "p95_ms": 120},
    ]
    write_csv(perf01, "locust-stats.csv", rows01)
    write_md(perf01, "PERF-01 Locust Report", """Source: recorded Locust baseline statistics. This is a reconstructed report because no native CSV/HTML export was saved during the original run.

| Operation | Requests | Failures | Average | p95 |
|---|---:|---:|---:|---:|
| Login | 104 | 0 | 370 ms | 400 ms |
| Contract list | 101 | 0 | 26 ms | 32 ms |
| Search | 101 | 0 | 15 ms | 17 ms |
| Public verification page | 103 | 0 | 13 ms | 15 ms |
| Public verification POST | 103 | 0 | 103 ms | 120 ms |""")

    perf02 = REPORTS / "PERF-02"
    perf02.mkdir()
    rows02 = load("perf02-load-ramp.json")
    write_json(perf02, "locust-stats.json", rows02)
    write_csv(perf02, "locust-stats.csv", rows02)
    write_md(perf02, "PERF-02 Locust Report", "Source: captured Locust `/stats/requests` aggregate data. The saved corrected run contains the 10-, 20-, 30-, and 50-user levels; the 1- and 5-user levels were completed in an earlier valid run but were not retained in this JSON artifact. The first attempt at higher levels was discarded because the harness stopped during spawning.")

    remaining = load("perf-remaining.json")
    for test_id in ("PERF-03", "PERF-05", "PERF-07", "PERF-08"):
        folder = REPORTS / test_id
        folder.mkdir()
        rows = [row for row in remaining if row["test"] == test_id]
        write_json(folder, "locust-stats.json", rows)
        write_csv(folder, "locust-stats.csv", rows)
        write_md(folder, f"{test_id} Locust Report", "Source: captured Locust `/stats/requests` aggregate data. The JSON and CSV files contain the per-repetition Locust measurements.")

    fixture = load("perf-fixture-runs.json")
    for test_id in ("PERF-04", "PERF-09"):
        folder = REPORTS / test_id
        folder.mkdir()
        rows = [row for row in fixture if row["test"] == test_id]
        write_json(folder, "locust-stats.json", rows)
        write_csv(folder, "locust-stats.csv", rows)
        write_md(folder, f"{test_id} Locust Report", "Source: captured Locust `/stats/requests` aggregate data from the generated-size fixture runs. The JSON and CSV files contain each size band and concurrency window.")

    perf06 = REPORTS / "PERF-06"
    perf06.mkdir()
    write_md(perf06, "PERF-06 Locust Report", """Source: recorded Locust mixed-workload output. No native CSV/HTML export was saved.

- Initial 15-minute run: 15,674 requests, 732 failures, 4.67% failure rate.
- Failures were concentrated in concurrent uploads using duplicate titles and filenames, which entered the duplicate/revision workflow.
- After the Locust upload harness generated unique titles and filenames, a focused 20-user upload run completed with 0 failures; upload latency remained high.

This report preserves the observed Locust result and the follow-up validation; it is not a newly rerun measurement.""")

    (REPORTS / "README.md").write_text("""# Locust evidence reports

Each PERF folder contains a `locust-stats.json`, `locust-stats.csv`, and/or `locust-report.md` file. JSON and CSV files are split from the captured Locust `/stats/requests` data. PERF-01 and PERF-06 are reconstructed from recorded Locust output because native Locust exports were not saved during those runs.
""", encoding="utf-8")
    print(f"Created per-test Locust reports in {REPORTS}")


if __name__ == "__main__":
    main()
