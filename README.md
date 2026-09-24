# ZETA-PSX: Autonomous Pakistan Stock Exchange Annual-Report Acquisition Engine

Production-grade autonomous annual report acquisition system for the Pakistan Stock Exchange (PSX / `XKAR`), powered by the hardened **ZETA Core Architecture**.

---

## 1. System Overview

- **WHAT**: Fully autonomous acquisition, validation, deduplication, and Google Drive publishing engine for Pakistan Stock Exchange annual reports.
- **PERIOD**: Fiscal Years **FY2017 – FY2025**.
- **INPUT**: Company names, PSX symbols/tickers, or cohort text files (e.g. `companies.txt`).
- **COMMAND**: `zeta-pk harvest companies.txt`
- **OUTPUT**: Automatically detected Google Drive mount: `Google Drive\Pakistan stock\<Company Name> [<TICKER>]\FY<YYYY>\<Canonical_Filename>.pdf`

```text
USER: "Scrape these 100 companies."
           │
           ▼
┌────────────────────────────────────────────────────────────────────────┐
│                        ZETA-PSX AUTONOMOUS CORE                        │
│                                                                        │
│  1. Parse & Normalize Tickers / Names                                  │
│  2. Resolve Exact Identity against PSX Historical Universe             │
│  3. Determine Historical Eligibility (FY2017–FY2025 Matrix)            │
│  4. Load Cached Company Source Profiles (Tier 0)                       │
│  5. Fingerprint & Select Website Adapter (Deterministic Discovery)    │
│  6. Classify Annual Reports vs Quarterlies/Half-Year/AGMs (Pass 1)     │
│  7. High-Speed Host-Sharded Parallel Transfer (Bounded Concurrency)    │
│  8. Multi-Stage Structural & Semantic Quality Validation               │
│  9. Atomic Rename & Direct Publish to Google Drive                     │
│ 10. Gap Matrix Computation & Targeted Pass 2 Recovery                 │
│ 11. Comprehensive Audit Generation (_AUDITS, _MANIFESTS, _SYSTEM)     │
└────────────────────────────────────────────────────────────────────────┘
           │
           ▼
Google Drive: Verified Annual Report PDFs with Complete Auditable Provenance
```

---

## 2. Quickstart & Autonomous CLI

### Installation

Requires Python 3.11+:

```powershell
# Create and activate virtual environment
python -m venv E:\ZETA-PSX-RUNTIME\.venv
E:\ZETA-PSX-RUNTIME\.venv\Scripts\Activate.ps1

# Install package with dependencies
pip install -e ".[test]"
```

### Health Check (`zeta-pk doctor`)
Verify runtime environment, local storage, Google Drive mount, and external connectivity:

```powershell
zeta-pk doctor
```

### Harvest a Cohort (`zeta-pk harvest`)

Single company or list of tickers:
```powershell
zeta-pk harvest --companies "HBL,SYS,LUCK,MCB,ENGRO"
```

Cohort file containing tickers or company names:
```powershell
zeta-pk harvest companies.txt
```

Default behavior:
- **Country**: `PAK`
- **Exchange MIC**: `XKAR`
- **Fiscal Years**: `2017:2025`
- **Resumability**: Enabled (existing verified files skipped automatically)
- **Source Hierarchy**: Official corporate issuer websites first
- **Multi-Stage Validation**: Enabled
- **Dataset Destination**: Automatically detected Google Drive desktop mount (`\Pakistan stock\`)
- **Hot Runtime**: High-speed local SSD (`E:\ZETA-PSX-RUNTIME\local\`)

---

## 3. Architecture & Operational Integrity

### A. Two-Pass Pipeline
To protect the high-speed transfer queue from being starved by web discovery:
1. **Pass 1 (Deterministic Discovery & Primary Download)**:
   - Resolves identities, evaluates listing eligibility for FY2017–FY2025.
   - Queries cached company source profiles (`company_source_profiles`).
   - For unprofiled companies, fingerprints the issuer site and extracts candidates via deterministic adapters.
   - Builds primary manifest and launches high-speed host-sharded concurrent transfer.
2. **Pass 2 (Targeted Gap Recovery)**:
   - Computes Company × Fiscal Year gap matrix (`company_year_matrix.csv`).
   - Targets only unresolved cells (`MISSING`, `REVIEW`, `FAILED`).
   - Leverages sitemaps, historical corporate aliases, and domain fallbacks.
   - Transfers and validates gap recoveries into the dataset.

### B. Two-Tier Storage Architecture
- **GitHub Repository**: Stores **SOFTWARE ONLY** (source code, tests, configuration, fixtures, documentation, CI workflows). No bulk PDFs or runtime databases are tracked by Git.
- **Google Drive**: Permanent destination for **DATA ONLY** (`\Pakistan stock\`). Verified reports are streamed directly to the Google Drive mount.
- **Local SSD Runtime**: Hot SQLite database with Write-Ahead Logging (WAL), `.part` files, locks, and cache reside locally (e.g. `E:\ZETA-PSX-RUNTIME\local\harvest.sqlite3`) to eliminate cloud filesystem latency and lock contention. State backups are periodically checkpointed to Google Drive.

```text
Google Drive\Pakistan stock\
│
├── _SYSTEM/                  # Safe SQLite backups, source profiles, golden benchmarks
├── _MANIFESTS/               # Primary run manifests and gap manifests
├── _AUDITS/                  # Full provenance audits, failure records, run summaries
├── _FAILURES/                # Quarantined non-compliant candidates
├── _LOGS/                    # Execution logs
│
├── Habib Bank Limited [HBL]/
│   ├── FY2017/               # Canonical annual report PDF
│   ├── FY2018/
│   └── ...
└── Systems Limited [SYS]/
    └── ...
```

---

## 4. Multi-Stage Validation & Quality Engine

A report is never considered verified merely because an HTTP request succeeded or a PDF file opened. Candidates progress through a strict 10-stage pipeline:

```text
DISCOVERED ──► SOURCE_TRUSTED ──► IDENTITY_VALID ──► PERIOD_VALID ──► ANNUAL_REPORT_VALID
                                                                             │
PUBLISHED ◄── CONTENT_VALID ◄── HASH_VALID ◄── PDF_STRUCTURE_VALID ◄── DOWNLOADED
```

### Explicit Failure Categories
Non-compliant candidates are rejected with explicit audit tags:
- `WRONG_ISSUER`: Content does not match requested corporate identity.
- `WRONG_FISCAL_YEAR`: Publication date misidentified as accounting period.
- `QUARTERLY_NOT_ANNUAL`: 1st, 2nd, 3rd quarter report detected.
- `HALF_YEAR_NOT_ANNUAL`: Interim half-yearly report detected.
- `NINE_MONTH_NOT_ANNUAL`: 9-month financial accounts detected.
- `AGM_DOCUMENT` / `PROXY_DOCUMENT`: AGM notices or proxy voting forms.
- `CORRUPT_PDF` / `TRUNCATED_PDF`: Damaged PDF trailer or incomplete stream.
- `HTML_INSTEAD_OF_PDF`: Captive portal, 404 HTML, or Cloudflare challenge.
- `DUPLICATE`: Identical SHA-256 already recorded for another slot.

### Fiscal-Year Intelligence
Accounting periods rarely match calendar upload dates (e.g., Year ended June 30, 2024 = `FY2024`, even if uploaded in October 2024). The engine extracts accounting period end-dates and anchors strictly to the issuer's fiscal year cycle.

---

## 5. Source Hierarchy & Adaptive Learning

1. **TIER 0**: Previously verified company source profile (`company_source_profiles` cache).
2. **TIER 1**: Official corporate Annual Report / Investor Relations archive page.
3. **TIER 2**: Other official financial pages on corporate website.
4. **TIER 3**: Official corporate CDN / subdomains.
5. **TIER 4**: Authorized exchange / regulatory disclosures.
6. **TIER 5**: Domain-restricted search discovery (`site:company.com`).
7. **TIER 6**: Legitimate digital web archives of official sources.
8. **TIER 7**: Reliable secondary repositories.

### Reusable Deterministic Adapters
Websites are parsed using dedicated adapters rather than generic scraping:
- `StaticYearArchiveAdapter`: Multi-year tables and card layouts.
- `InvestorRelationsAdapter`: Financial reports and statutory portals.
- `WordPressMediaAdapter`: Dynamic media library uploads (`/wp-content/uploads/YYYY/MM/`).
- `SitemapAdapter`: XML sitemaps filtered for report PDF patterns.
- `GenericAnchorAdapter`: Heuristic anchor parsing with positive/negative weighting.
- `DirectPdfAdapter`: Direct PDF endpoint verification.
- `JavascriptDiscoveryAdapter`: Headless browser fallback for SPA-driven portals.

---

## 6. Benchmarking & Concurrency Optimization

The system implements the **Performance Champion Rule**: a concurrency configuration becomes the production default *only* if 100% of correctness tests pass, 100% of golden reports match, and median throughput improves.

```powershell
# Run concurrency sweep across 32, 48, 64, 80 workers
zeta-pk optimize

# Run golden set verification benchmark
zeta-pk benchmark
```

Host performance is tracked continuously in the `host_stats` SQLite ledger to dynamically adjust per-host concurrency and back off upon receiving HTTP 429 or 5xx responses.

---

## 7. Upstream US & UK Core Architecture

This repository preserves the upstream **ZETA Core Engine** for statutory filings across US (SEC EDGAR) and UK (FCA NSM) capital markets:

- **US Ingestion**: SEC EDGAR Form 10-K, 20-F, and Form ARS via bulk submissions API and headless Chromium layout rendering.
- **UK Ingestion**: FCA National Storage Mechanism (NSM) structured XHTML, PDF, and ZIP filing ingest.
- **Upstream CLI**: `ar-harvest` commands remain fully accessible and functional.

---

## 8. Development & Continuous Integration

Run the test suite across unit, validation, fiscal-year, identity, and resume tests:

```powershell
pytest -v
```

GitHub Actions automatically executes the comprehensive test matrix on push and pull request via `.github/workflows/tests.yml`.
