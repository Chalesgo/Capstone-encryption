# PERF-06 Locust Report

Source: recorded Locust mixed-workload output. No native CSV/HTML export was saved.

- Initial 15-minute run: 15,674 requests, 732 failures, 4.67% failure rate.
- Failures were concentrated in concurrent uploads using duplicate titles and filenames, which entered the duplicate/revision workflow.
- After the Locust upload harness generated unique titles and filenames, a focused 20-user upload run completed with 0 failures; upload latency remained high.

This report preserves the observed Locust result and the follow-up validation; it is not a newly rerun measurement.
