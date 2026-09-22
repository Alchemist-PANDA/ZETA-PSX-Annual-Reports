# Agent runbook: US/UK annual and sustainability reports

Read this file before changing code or downloading reports. The user works in plain English. Translate each request into a company universe, report category, fiscal years, and an auditable run; do not ask the user to write CSV or commands when you can prepare them.

## The invariant

Correct report identity and complete, readable English PDFs are required. Optimize elapsed time only among runs that pass every correctness check. Never claim 100% semantic accuracy from a PDF header or page count alone. Keep unresolved company-years explicit; never fill them with a different report type merely to increase coverage.

## Source and classification decisions

1. Identify company by country, LEI, ISIN, ticker, MIC, and US CIK where applicable. Use `examples/universe.csv`; do not guess identifiers. Record fiscal year separately from publication year.
2. For annual reports, use SEC bulk submissions for US (`load-universe`, then `discover-us`) and FCA NSM CSV exports for UK (`import-fca-csv`). Use `render-sec` and `render-fca` for original filings that are HTML/XHTML/ZIP. Follow the README commands.
   If the user explicitly chooses AnnualReports.com, run `annualreports-discover` against the company universe, inspect its unresolved CSV, then run the generated manifest with `--authorized-hosted` when access permits automated hosted transfers. This resolves actual company-page PDF links before the concurrent download phase. Use `annualreports-import` and `annualreports-ingest-zip` when an authorized metadata export and bulk package are supplied. Do not assume a public bulk API exists or evade source blocks.
3. For sustainability/ESG/CSR, obtain authorized SustainabilityReports bulk metadata and ZIP when available. Never scrape its normal site for bulk files. For missing records, use official company reporting archives and direct PDF links. Convert either source to the `sr-import` metadata CSV described in README. The first-two-company CSV is an example, not the universe.
4. Classify standalone sustainability/ESG/CSR as `SR`; integrated business/annual reporting as `IR`; topic updates as `CLIMATE` or `OTHER`. A topic update is not a standalone SR. Multiple candidate files for the same company, fiscal year, and SOP type require review.
5. Resolve all URLs and ZIP member paths before downloading. Import metadata, inspect `sr-review.csv`, and finish a manifest before starting the transfer queue.

## Execution order

1. Run `py -m pytest -q` before a substantial code change and after it.
2. Build/verify the company universe and metadata for the requested set. Use `ar-harvest plan` on a direct manifest. Resolve conflicts and invalid identities before transferring files.
3. Use `ar-harvest run` for direct PDFs and `ar-harvest sr-ingest-zip` for authorized bulk ZIPs. Use a local SSD output directory. The engine already handles host interleaving, SEC rate limits, bounded retries, PDF validation, SHA-256, atomic writes, and SQLite state. Do not launch one browser per report or perform search while download workers are occupied.
4. Run `ar-harvest verify` for direct downloads. Check the ZIP ingestion summary and `bulk_reports` SQLite table for bulk files. Audit all exceptions and spot-check report title, issuer, fiscal year, language, and completeness. Check for orphaned `.part` files.
5. Report requested, resolved, verified, failed, and unresolved company-years separately. Give actual elapsed time and throughput, plus the source and output directory. Do not equate local synthetic speed with internet throughput.

## Continuous speed experiments

Use `py benchmarks/optimize.py fixture` to test downloader changes locally. The script gives every fixture PDF a known SHA-256 and page count, uses fresh output/state for each trial, verifies the ledger and every file, logs JSONL results, and keeps a champion only when all repetitions pass with no 429/5xx responses and the median time improves by at least 5%. Without `--configs`, it tests settings near the saved champion on the next run. Results are in ignored `benchmarks/results.jsonl` and `benchmarks/champions.json`.

For an actual representative sample, prepare `golden.csv` with columns `relative_path,sha256,pages`. Build this reference from independently checked, authoritative files before benchmarking; check each file's company, fiscal year, category, language, and completeness. Then run `py benchmarks/optimize.py real --manifest sample.csv --golden golden.csv --repeats 2`. Each trial downloads the same sample into a fresh temporary directory. Use a sample across different hosts, file sizes, and years. A faster run with any failed, wrong, missing, duplicate, or rate-limited report is rejected. Real-source comparisons can vary with server load, so repeat before changing production defaults.

Change one bottleneck at a time. Record the code change, baseline, candidate, sample, network conditions, median seconds, PDFs/second, MiB/second, retries, 429/5xx, and accuracy result in the task summary. Revert or revise changes that do not improve a valid benchmark. Never increase SEC access above its enforced gate or ignore a host's throttling to win a benchmark.

## Known limits

The repository does not contain a universal company roster or licensed bulk portal credentials. The portal export and ZIP must be obtained through authorized access. Official corporate archives have no universal API; agents must resolve missing links from each company's official archive. Structural PDF validation proves transfer integrity; semantic report identity needs trusted metadata and review.
