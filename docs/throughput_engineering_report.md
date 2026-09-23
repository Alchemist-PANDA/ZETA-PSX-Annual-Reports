# Throughput engineering: measured results and the 24-hour deadline

Updated 2026-09-23. Target: 150,000 distinct, correct reports in one day.
**3 reports/second has NOT been achieved.** The previous guarantees were predictions,
not benchmark results. The original report is preserved locally in
`benchmarks/render-runs/original_throughput_engineering_report.md`.

## Deadline

- At 0.75 reports/second: 55 hours 33 minutes.
- Minimum average for 24 hours: 1.7362 reports/second, including all work.
- At 3 reports/second: 13 hours 53 minutes 20 seconds for processing alone.

Discovery, retries, identity/completeness review and confirmed Drive uploads must
fit the same deadline. The user confirms the previous company batches are already
downloaded; the 150,000-report URL list is not currently available. This optimization
run did not start or complete a 150,000-report harvest.

## Measured cached-HTML results

Hardware: Ryzen 5 PRO 4650U, **6 physical cores / 12 threads**, approximately 24 GB RAM.
Sample: 18 real cached SEC filings evenly across HTML size ranks, including extremes;
109.85 MiB of HTML and 2,352 generated pages per trial. The largest HTML is 35.3 MiB.
This is a stress sample, not a representative forecast of all 150,000 reports.

| Browser processes | Total page workers | Trial 1 seconds | Trial 2 seconds | Median seconds | PDFs/second |
| --- | --- | --- | --- | --- | --- |
| 1 | 6 | 53.673 | 48.298 | 50.986 | 0.353 |
| 3 | 6 | 43.693 | 43.604 | 43.648 | 0.412 |
| 6 | 6 | 53.813 | 54.881 | 54.347 | 0.331 |

Three browsers reduced median elapsed time by **14.4%**, increasing throughput by
**16.8%**. The first baseline overlapped a short test run; comparing only the second
baseline still gives approximately 10.8% higher throughput. Six browsers were slower.

All six trials used fresh state/output and disabled source downloads. All **108 PDFs**
passed SHA-256/structure checks and matched the baseline's page counts, full extracted
text and every page's 36-dpi appearance. This is regression checking, not semantic
certification. Including the extra visual/text audit, the three-browser trials took
81.9–83.4 seconds, about 0.22 reports/second. Rendering timings include normal
validation/writes but exclude that extra audit and all network/discovery work.

Printing was the dominant measured stage: 117–126 accumulated worker-seconds versus
33–35 navigating and under one validating/storing in the three-browser trials.
Worker timings overlap; summing them does not give wall time.

Evidence: `benchmarks/render_evidence_20260923.json`.
Raw local outputs: `benchmarks/render-runs/20260923-020817-6f00d2/`.

## Implemented changes

1. Independent Chromium instances with a bounded shared queue. `--render-workers`
   is the TOTAL across `--browser-processes`. General CLI default remains one browser;
   this laptop's batch script uses the measured 3-browser/6-worker setting.
2. Stage timings, asynchronous cache reads and cancellation on worker startup failure
   to prevent the queue hanging without consumers.
3. `render-sec --universe` limits rendering to the requested company cohort.
4. Removed serial HTML pre-caching from `local/run_pipeline_201_250.py`. The library
   already overlaps downloads and rendering. Cold-network improvement is unmeasured.
5. Removed forced re-downloads in that batch script. Fixed direct-phase timing and
   excluded existing/skipped reports from fresh throughput.
6. Added a repeatable cached-render benchmark and `--target-rate` to the direct-PDF
   benchmark. Exit 2 means valid outputs missed the target; 1 means checks failed.

## Storage: the user's 2 TB Google Drive

The ledger records 2,161 downloaded PDFs, **5,471,612,737 bytes**, and 292,972 pages.
The earlier report's 7.07 GB does not match this ledger snapshot. At the actual PDF
average, 150,000 reports would occupy approximately **380 GB**, before HTML caches,
temporary files and backups. This estimate depends on the report mix.

C: currently has about 22 GB free. Mounted G:, H: and I: each report about 21 GB;
that is not evidence of the account's available cloud quota. The user's 2 TB plan
provides storage, not rendering CPU. Available cloud space has not been verified.

Use bounded local staging, resumable uploads, verified remote checksums/file IDs,
and local cleanup only after remote verification. A write to a synced folder is not
proof of upload completion. Automated archival/eviction is **not implemented here**.

At the existing PDF-store average, 3 reports/second needs about 7.6 MB/s upload
capacity. The larger direct-ARS subset averaged 9.84 MiB/report, requiring about
29.5 MiB/s for 3/second. Measure the actual workload's bandwidth in both directions.
Google documents 750 GB/day upload/copy limits for Workspace users; confirm account
type and applicable limits before planning a full-day upload. A 2 TB storage plan
is not a throughput guarantee.
Source: https://developers.google.com/workspace/drive/api/guides/limits

## Instructions for the next agent

1. Establish the actual roster, countries, report categories and fiscal years. Resolve
   and review URLs in batches. Keep missing company-years explicit; do not rerun the
   completed 250-company batch as new work.
2. Use SEC bulk metadata and supplied FCA metadata for discovery. `submissions.zip`
   contains filing-history JSON, **not the full PDFs/HTML**. Daily filing archives
   are separate and need independent selection and size planning.
3. Prefer complete original PDFs or authorized bulk packages. Use official company
   archives for unresolved files. Never substitute topic updates for annual reports.
4. Run a representative live sample with independently checked golden files:

   ```powershell
   py benchmarks/optimize.py real --manifest sample.csv --golden golden.csv --repeats 2 --target-rate 3
   ```

   A real SEC organization/contact User-Agent is required. It is not configured in
   this task's environment; do not invent contact details to run a live SEC sample.
5. For pending SEC HTML, invoke the streaming renderer directly:

   ```powershell
   ar-harvest render-sec --state local/harvest.sqlite3 --output-root local --cache cache --universe prepared-universe.csv --network-workers 8 --render-workers 6 --browser-processes 3
   ```

   `prepared-universe.csv` is a placeholder for the next verified cohort, not an
   existing 150,000-report roster. Do not add a serial pre-cache step.
6. If rendering stays below the required aggregate rate, use additional CPU capacity
   with disjoint cached batches, separate state/output, and a measured combined rate.
   Keep SEC acquisition centralized. Current separate CLI runs have separate rate
   gates: **do not multiply the user's SEC allowance with concurrent invocations**.
7. Track requested, resolved, newly transferred, rendered, structurally verified,
   semantically reviewed and confirmed-uploaded counts separately. Record end-to-end
   elapsed time, including discovery and upload. No claim of completion while any
   requested company-year remains failed, unresolved or unreviewed.

For local tuning:

```powershell
py benchmarks/render_cached.py --state local/harvest.sqlite3 --cache cache --count 24 --configs 1:6,3:6,6:6 --repeats 2 --target-rate 3
```

## Accuracy limitations and corrections

The existing renderer blocks remote images/styles. Externally linked assets or
incorporated financial statements may be absent. Matching the baseline does not
prove complete annual reports. Original PDFs or source-level review are still
needed. No outputs were newly declared semantically approved in this experiment.

- Overlap alone changes 259 seconds downloading plus 342 rendering toward a lower
  bound near 342 seconds, not 85 seconds.
- One slow AnnualReports.com trial does not establish a universal bandwidth cap.
- Browser counts do not scale throughput linearly; six were slower than three here.
- SEC guidance allows each user at most 10 requests/second across machines. The
  repository's 8.8/second local gate is lower; more machines do not multiply it.

Official sources:
- https://www.sec.gov/search-filings/edgar-application-programming-interfaces
- https://www.sec.gov/about/developer-resources
