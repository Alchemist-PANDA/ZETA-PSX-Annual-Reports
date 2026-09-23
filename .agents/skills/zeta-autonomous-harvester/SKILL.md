---
name: zeta-autonomous-harvester
description: Zero-latency autonomous execution skill for harvesting US/UK corporate annual and sustainability reports across the wide stock universe directly into Google Drive (GLOBAL_SUSTAINABILITY_DATABASE) using the Two-Tier SEC/FCA Engine.
---

# ZETA Autonomous Corporate Report Harvester Skill

## When to Activate
Activate immediately whenever the user asks to:
- Download the next $N$ companies or any country/exchange cohort across the wide stock universe (e.g., "download next 500", "extract UK/US reports", "do this folder")
- Check harvesting status ("status")
- Sync or verify reports in Google Drive (`GLOBAL_SUSTAINABILITY_DATABASE`)

## Core Rules (Zero Deliberation)
1. **Act Autonomously**: Never pause after creating universe CSVs to ask "Ready to proceed?". Build the universe CSVs and immediately launch the batch pipeline.
2. **Wide Stock Universe Support**: Never assume a fixed company count cap. Always query `SELECT cik, lei FROM companies` in `local/harvest.sqlite3` to dynamically exclude already-harvested companies and expand across the full active SEC (`company_tickers_exchange.json`), FCA NSM, Wikidata, GLEIF, and OpenFIGI stock universe (~9,548 US, ~3,191 UK, plus global dual-listed issuers).
3. **Direct Google Drive Output**: All final PDFs go directly to `GLOBAL_SUSTAINABILITY_DATABASE` (`C:\Users\CGS_Computer\Videos\annaual reportsssssss\GLOBAL_SUSTAINABILITY_DATABASE`), which is an NTFS junction (`mklink /J`) to `G:\My Drive\GLOBAL_SUSTAINABILITY_DATABASE`. Keep `local/harvest.sqlite3` and `cache/` on local SSD.
4. **Never Use `AnnualReports.com` as Primary**: `AnnualReports.com` is deprecated and must always be the absolute last resort. Always use the **Two-Tier SEC EDGAR Engine** (`Method #1: Direct Graphic ARS PDF` + `Method #2: Self-Healing Chromium 10-K/20-F Layout`) and **FCA NSM** for UK domestic companies.

## Exact 2-Step Playbook for Any New Cohort

### Step 1: Build Next Cohort Universe CSVs (100 companies per batch)
- Query `SELECT cik, lei FROM companies` from `local/harvest.sqlite3` to skip all previously harvested companies.
- Match active SEC issuers (`https://www.sec.gov/files/company_tickers_exchange.json`) against Wikidata datasets, the GLEIF API (`https://api.gleif.org/api/v1/lei-records/{lei}/isins`), and the OpenFIGI API (`POST https://api.openfigi.com/v3/mapping`).
- Validate every row with `Company.from_row()` and write `local/universe_batch_<name>_100.csv`.

### Step 2: Execute & Monitor Autonomously via `ar-harvest harvest-batch`
- Run the built-in CLI batch pipeline for each cohort CSV:
  ```powershell
  $env:SEC_USER_AGENT="blackswan capital khanholdings127@gmail.com"
  ar-harvest harvest-batch local/universe_batch_<name>_100.csv --state local/harvest.sqlite3 --output-root GLOBAL_SUSTAINABILITY_DATABASE
  ```
- Schedule a 5-minute watchdog timer (`schedule` tool, `DurationSeconds=300, TimerCondition="any"`) to monitor until 100% completion.
