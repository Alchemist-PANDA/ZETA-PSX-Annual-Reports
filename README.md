# US and UK annual and sustainability report harvester

## Sustainability pilot: first two companies

`examples/sustainability-first-two.csv` contains Microsoft and Coca-Cola official PDF examples. They illustrate report-family changes; they are not the company limit. The general workflow below accepts any supplied US/UK universe and 2017–2025 metadata export. The full source strategy is in `docs/sustainability-pipeline.md`.

Create a company identity CSV using `examples/universe.csv`. Obtain an **authorized** bulk portal export, or create the same metadata format from official company archive links. Required columns are `company_name,ticker,isin,country,report_year,report_title,report_type,language,source_url,filename,page_count`. `source_url` is an HTTPS direct PDF URL when available; `filename` is an exact member path in a supplied bulk ZIP. Rows may have either or both. Country uses `USA` or `GBR`, and `report_year` uses `2017` through `2025`.

```powershell
ar-harvest sr-import companies.csv authorized-metadata.csv --years 2017:2025
ar-harvest plan sr-direct.csv
ar-harvest run sr-direct.csv --output-root "D:\GLOBAL_SUSTAINABILITY_DATABASE"
ar-harvest sr-ingest-zip authorized-bulk.zip --index sr-bulk-index.csv --output-root "D:\GLOBAL_SUSTAINABILITY_DATABASE"
```

`sr-import` joins by exact country and ISIN, classifies standalone sustainability/ESG/CSR, integrated reports, and topic updates, and writes ambiguous or unmatched rows to `sr-review.csv`. Conflicting reports for the same SOP path go to review. It creates the full URL manifest before `run` starts. `sr-ingest-zip` reads only listed members, caps member size, verifies PDF structure, hashes the bytes, writes atomically to the SOP path, and records results in SQLite. The portal itself does not expose an authorized automation API in this repository; obtain its metadata and ZIP through your licensed workflow. Official archive URLs can use the same metadata import format. Report availability and access depend on the supplied universe and source exports.

A local, resumable discovery and PDF ingestion system for US and UK annual reports, fiscal years 2017–2025. It follows the supplied **Phase-1 SOP: Folder & PDF File Naming Standard** and uses the two authoritative sources specified in `AR sources.txt`: SEC EDGAR and FCA NSM.

## Current scope

- Countries: `USA` and `GBR`.
- Input: a company-level identity roster containing LEI, ISIN, exchange and ticker, plus CIK for US companies. `AR sources.txt` and `SR sources.txt` are execution guides. The supplied country workbook has aggregate counts, not the individual companies. `file naming.txt` is empty.
- US discovery: cached SEC bulk submissions ZIP, followed by only the historical submission JSON files needed for missing fiscal years. The SEC's `reportDate` determines the fiscal year; amendments stay separate.
- UK discovery: import the CSV exported by the FCA NSM search interface, join by LEI, and leave ambiguous fiscal years for review. PDF, XHTML and ZIP originals are handled separately.
- PDF acquisition: reviewed direct PDF links download concurrently. Official SEC HTML filings can be saved and locally rendered to PDF with a separate Chromium pool; the ledger records that conversion.
- Output: PDF only, complete English reports, using the SOP's exact path and filename:

  `GLOBAL_SUSTAINABILITY_DATABASE/GBR/XLON/2138007ZFQYRUSLU3J98_GB00BHJYC057_IHG/FY2024/2138007ZFQYRUSLU3J98_GBR_XLON_IHG_GB00BHJYC057_FY2024_AR_EN.pdf`

The source catalog must identify the **fiscal year**, not publication year. `verified=true` in an imported PDF manifest means a person or trusted upstream process confirmed the identifiers, period, report type and complete English content. The downloader checks PDF structure and location; arbitrary PDF content can still need human review.

The supplied self-improvement checklist mentions a `CATEGORY` subfolder, and `AR sources.txt` proposes `US_AAPL_2024_AR.html`. The updated V2 folder-and-file SOP explicitly requires the LEI/ISIN path and PDF name shown above. This repository follows the updated V2 SOP for every final PDF. Original SEC HTML is retained separately under `cache/`, not mislabeled as a source PDF.

## Setup

Python 3.11+:

```powershell
py -m venv .venv
.venv\Scripts\python -m pip install -e ".[test,render]"
```

The `render` extra is needed for SEC HTML conversion. On Windows, the CLI detects installed Chrome or Edge. On other systems, pass `--chrome` or install a Playwright Chromium browser.

## Company universe and discovery

Copy `examples/universe.csv` and add one listing per row. Its columns are `country,company_name,exchange,lei,isin,ticker,cik,aliases`. Use `USA` and `GBR`, the four-character exchange MIC, and the exact LEI/ISIN/ticker supplied by your data source. US rows require a numeric SEC CIK. `aliases` is optional text; leave it blank when unavailable.

```powershell
$env:SEC_USER_AGENT = "Your Organization research@example.org"
ar-harvest load-universe companies.csv --years 2017:2025 --state harvest.sqlite3
ar-harvest discover-us --state harvest.sqlite3
ar-harvest import-fca-csv fca-nsm-export.csv --state harvest.sqlite3
ar-harvest import-fca-map cache/fca/mapping.zip --state harvest.sqlite3
ar-harvest status --state harvest.sqlite3
ar-harvest export-manifest --state harvest.sqlite3
```

`discover-us` downloads the SEC bulk ZIP once to `cache/sec/submissions.zip`, reuses a fresh cached copy, and fetches only referenced history files for missing years. For offline testing, use `--bulk-zip path/to/submissions.zip --no-history`. Every SEC network request needs `SEC_USER_AGENT` with a real contact address. [SEC bulk API documentation](https://www.sec.gov/search-filings/edgar-application-programming-interfaces).

Export UK NSM search results as CSV from the [FCA search interface](https://www.fca.org.uk/markets/primary-markets/regulatory-disclosures/national-storage-mechanism); the current [investor guide](https://www.fca.org.uk/publication/primary-market/nsm-investor-user-guide.pdf) says the export includes a document download link and permits up to 4,000 rows per export. Import multiple date/LEI shards by repeating `import-fca-csv`. The older NSM search API named in `AR sources.txt` returned `Invalid index` in a live check, and the FCA's [current FAQ](https://data.fca.org.uk/artefacts/PUBLISHING_HUB_FAQs_v0.1.pdf) says direct programmatic NSM access is not permitted. This repository uses the CSV export route.

For historic Morningstar NSM links, download the [FCA migration mapping ZIP](https://data.fca.org.uk/artefacts/NSM/data-migration/MS_to_FCA_NSM_Document_URL_Mapping.zip) to `cache/fca/mapping.zip` and run `import-fca-map`. The default streams the 2017–2020 CSVs and stores only mappings needed by your candidate catalog; `--all` stores every mapping and needs much more disk space.

SEC HTML filings are discovered but omitted from the direct-PDF manifest. Render those separately:

```powershell
ar-harvest render-sec --state harvest.sqlite3 --output-root "D:\GLOBAL_SUSTAINABILITY_DATABASE"
ar-harvest render-fca --state harvest.sqlite3 --output-root "D:\GLOBAL_SUSTAINABILITY_DATABASE"
```

`render-sec` downloads official SEC HTML with the SEC request limit, keeps the original source in `cache/sec/html/`, and produces a local PDF in the SOP path. `render-fca` preserves each FCA original in `cache/fca/originals/`; it passes through genuine PDFs and renders HTML/XHTML or a ZIP package's largest report page. Its FCA network concurrency starts at 4 and decreases on 429/503 responses. Both renderers block external page resources, so inspect a sample of converted reports for layout and missing images. Conversion provenance is kept in SQLite. Records with unresolved fiscal years remain in `status` for review.

## Direct PDF downloads

`export-manifest` writes only verified direct PDF candidates. You can also create a reviewed CSV from `examples/reports.csv`. Its columns are:

| Column | Example | Meaning |
| --- | --- | --- |
| `country` | `GBR` | `GBR` or `USA` |
| `exchange` | `XLON` | Four-character exchange MIC, supplied with the listing |
| `lei` | `2138007ZFQYRUSLU3J98` | 20-character LEI |
| `isin` | `GB00BHJYC057` | 12-character ISIN |
| `ticker` | `IHG` | Supplied ticker |
| `fiscal_year` | `FY2024` | Reporting year |
| `report_type` | `AR` | SOP code, including `AR`, `10K`, `20F`, `IR` |
| `language` | `EN` | Complete English report |
| `pdf_url` | `https://.../report.pdf` | Direct PDF endpoint |
| `source_page` | `https://.../reports` | Public page or filing where the link was found; may be blank |
| `verified` | `true` | Confirmation of report identity and scope |

Keep credentials in environment variables. For SEC-hosted URLs, set `SEC_USER_AGENT` to an organization name and contact email. Never put credentials into a CSV.

```powershell
ar-harvest plan resolved-reports.csv
ar-harvest run resolved-reports.csv --output-root "D:\GLOBAL_SUSTAINABILITY_DATABASE" --state harvest.sqlite3
ar-harvest verify --output-root "D:\GLOBAL_SUSTAINABILITY_DATABASE" --state harvest.sqlite3
ar-harvest benchmark --limit 200 --output-root "D:\GLOBAL_SUSTAINABILITY_DATABASE" --state harvest.sqlite3
```

The SOP recommends storing the database on a non-system drive and keeping a backup. Pick an output path suited to your machine. `run` writes `run-summary.json` and `failures.csv` in the current directory. Re-running skips successful files whose recorded URL and file size match. If a process stopped after an atomic write but before its database update, the next run validates and recovers that file. `verify` performs full hash and page-tree checks. Use `--replace` only when intentionally replacing existing files.

`benchmark` downloads up to 200 pending discovered direct PDFs, displays live counts, records the run in SQLite, and appends `benchmark.csv`. It performs real downloads into the chosen final output path.

## Performance model

The default has 32 concurrent transfers and at most 2 per host. It interleaves hosts, caps each request at 15 seconds, sends retryable failures to a second pass, validates PDFs in memory, and writes each completed PDF once through a `.part` file and atomic rename. The default PDF size cap is 64 MiB per transfer. Adjust `--workers` to the available RAM and bandwidth; maximum possible in-flight PDF memory is roughly `workers × max-mib`, plus overhead. At 32 × 64 MiB that upper bound is high, so use a lower cap or worker count for memory-limited machines.

Throughput is constrained by source bandwidth, file size and source access policies. The downloader does not promise a fixed number of PDFs per second. It respects the SEC's [10 requests/second fair access limit](https://www.sec.gov/about/webmaster-frequently-asked-questions) with a margin (8.8/second). An individual corporate host gets at most two concurrent transfers.

## Source strategy

The [SEC submissions API](https://www.sec.gov/search-filings/edgar-application-programming-interfaces) provides filing metadata, but many 10-K filings are HTML. The [FCA NSM](https://www.fca.org.uk/markets/primary-markets/regulatory-disclosures/national-storage-mechanism) holds UK annual financial reports, but many recent filings are structured XHTML or ZIP packages. [FCA guidance](https://www.fca.org.uk/markets/filing-structured-annual-financial-reports) distinguishes structured reports from PDF filings. These source formats determine the number of annual reports that can be ingested as original PDFs without local conversion.

The supplied workbook lists approximately 9,548 US and 3,191 UK companies, but contains no individual records. Supply the company roster before a full 2017–2025 run. Discovery status reports expected, found, missing, PDF-ready, non-PDF and manual-review counts; it never invents absent documents.

## Development

```powershell
.venv\Scripts\python -m pytest -q
.venv\Scripts\python benchmarks\local_batch.py --count 100
.venv\Scripts\python benchmarks\local_batch.py --count 100 --image-side 1024
```

`--allow-http` exists only to run local HTTP integration tests. Production manifests should use HTTPS.
The benchmark serves synthetic PDFs from localhost. The image option creates roughly 3 MiB files. Its throughput is a regression check for the local pipeline, not a prediction for remote hosts.
