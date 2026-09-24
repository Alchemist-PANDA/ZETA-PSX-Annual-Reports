# Pakistan Architecture

## Overview

The Pakistan extension adds PSX (Pakistan Stock Exchange, MIC: XKAR) annual-report
harvesting to the ZETA system.  It reuses the **existing ZETA core** for:

- Download engine (`engine.py`) — curl_cffi, connection pooling, bounded workers
- PDF validation (`inspect_pdf`) — PyMuPDF in-memory validation
- SHA-256 integrity hashing
- Atomic `.part` staging and rename
- SQLite WAL state ledger
- Retry/resume logic
- Benchmark framework
- Verification pipeline

## Module Structure

```
src/annual_reports/pakistan/
├── __init__.py          # Package init, public API exports
├── config.py            # Google Drive detection, runtime configuration
├── company.py           # PakistanCompany identity model
├── report.py            # PakistanReport — bridges Pakistan identity to ZETA engine
├── classifier.py        # Annual-report vs quarterly/half-year classifier
├── universe.py          # Seed universe of ~35 major PSX companies
├── source_profile.py    # Persistent per-company source profile database
├── discovery.py         # Tiered URL discovery from official websites
├── autonomous.py        # Two-phase harvest orchestrator
└── cli.py               # zeta-pk CLI entry point
```

## Two-Phase Architecture

### Phase 1: Pre-Resolution
1. Resolve company identities from user queries (tickers, names)
2. Determine eligible fiscal years per company
3. Check cached source profiles for known annual-report pages
4. Discover report URLs from official corporate websites
5. Classify candidates (ANNUAL / NOT_ANNUAL / REVIEW)
6. Build structured download manifest

### Phase 2: Bulk Transfer
1. Convert Pakistan reports to ZETA engine-compatible format
2. Execute bounded async downloads via existing engine
3. In-memory PDF validation (PyMuPDF)
4. SHA-256 hashing
5. Atomic `.part` → final write to Google Drive
6. Update state ledger
7. Generate gap matrix for missing reports

## Identity Model

Pakistani companies may lack globally-resolved LEI (ISO 17442) or ISIN (ISO 6166).
The system uses **deterministic fallback identifiers** that are:
- Clearly marked as non-genuine (LEI starts with `XPAK`, not a valid LEI prefix)
- Deterministic (same company always gets same fallback)
- Never confused with real identifiers

## Source Discovery Tiers

| Tier | Source | Priority |
|------|--------|----------|
| 0 | Cached source profile | Highest |
| 1 | Official company IR/annual-report page | High |
| 2 | Other official company domain pages | Medium |
| 3 | Official CDN/subdomain | Medium |
| 5 | Search-engine discovery (official domain only) | Low |

## Storage Layout

```
G:\My Drive\Pakistan stock\
├── _SYSTEM\              # System metadata
├── _MANIFESTS\           # Resolved download manifests
├── _AUDITS\              # Gap matrices, run summaries
├── _LOGS\                # Runtime logs
├── _FAILURES\            # Failure reports
├── Habib Bank Limited [HBL]\
│   ├── FY2017\
│   │   └── <LEI>_PAK_XKAR_HBL_<ISIN>_FY2017_AR_EN.pdf
│   ├── FY2018\
│   └── ...
└── Systems Limited [SYS]\
    └── ...
```
