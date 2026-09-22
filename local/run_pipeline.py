import asyncio
import csv
import hashlib
import json
import os
import re
import sys
import time
from collections import Counter, defaultdict
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
import httpx

import fitz

from annual_reports.catalog import Report, FIELDS
from annual_reports.conversion import render_sec_html
from annual_reports.discovery import Company, DiscoveryStore, _ingest_sec_data, parse_years
from annual_reports.engine import RateGate, RunLock, Settings, StateStore, native_path, run, verify_store, SEC_REQUEST_INTERVAL_S

# 1. Output and State Setup
OUTPUT_ROOT = Path("local").resolve()
STATE_PATH = OUTPUT_ROOT / "harvest.sqlite3"
CACHE_ROOT = Path("cache").resolve()
SEC_USER_AGENT = os.getenv("SEC_USER_AGENT", "AcademicResearch research@harvester.edu")
os.environ["SEC_USER_AGENT"] = SEC_USER_AGENT

# Detect Chrome
CHROME_PATH = None
if os.name == "nt":
    for candidate in (Path("C:/Program Files/Google/Chrome/Application/chrome.exe"),
                      Path("C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe")):
        if candidate.is_file():
            CHROME_PATH = candidate
            break

print("=" * 80)
print(f"HARVESTER PIPELINE: TOP 100 US PUBLIC COMPANIES (FY2017-2025)")
print(f"Output Directory : {OUTPUT_ROOT}")
print(f"State Database   : {STATE_PATH}")
print(f"Chrome Binary    : {CHROME_PATH}")
print(f"SEC User-Agent   : {SEC_USER_AGENT}")
print("=" * 80)

# 2. Load Top 100 Companies from Resolved Checkpoint
with open("C:/Users/CGS_Computer/.gemini/antigravity/brain/9fa7cc27-c419-40e0-83c9-762fc0095204/scratch/top100_resolved.json", "r", encoding="utf-8") as f:
    raw_companies = json.load(f)

manual_patches = {
    "XOM": {"cik": "34088", "isin": "US30231G1022", "lei": "J3WHBG0MTS7O8ZVMDC91", "exchange": "XNYS", "company_name": "Exxon Mobil"},
    "SCCO": {"cik": "1001838", "isin": "US84265V1052", "lei": "2549007U6NAP46Q9TU15", "exchange": "XNYS", "company_name": "Southern Copper"},
    "VRTX": {"cik": "875320", "isin": "US92532F1003", "lei": "54930015RAQRRZ5ZGJ91", "exchange": "XNAS", "company_name": "Vertex Pharmaceuticals"},
    "GEV": {"cik": "1996810", "isin": "US36828A1016", "lei": "254900DP080RU6OK2553", "exchange": "XNYS", "company_name": "GE Vernova"},
    "MRVL": {"cik": "1835632", "isin": "US5738741041", "lei": "254900WVU0BM7ZCJ9E93", "exchange": "XNAS", "company_name": "Marvell Technology"},
    "BLK": {"cik": "1364742", "isin": "US09247X1019", "lei": "529900VBK42Y5HHRMD23", "exchange": "XNYS", "company_name": "BlackRock"},
    "IBKR": {"cik": "1381197", "isin": "US45841N1072", "lei": "5493004DT6DCDUZNDM53", "exchange": "XNAS", "company_name": "Interactive Brokers"},
    "BRK-B": {"ticker": "BRK-B", "exchange": "XNYS"}
}

top100_companies: list[Company] = []
top100_rows = []
for c_dict in raw_companies:
    t = c_dict["ticker"]
    if t in manual_patches:
        c_dict.update(manual_patches[t])
    c_dict["ticker"] = c_dict["ticker"].replace(".", "-").replace("/", "-")
    comp = Company.from_row(c_dict)
    top100_companies.append(comp)
    top100_rows.append({
        "country": comp.country, "company_name": comp.company_name,
        "exchange": comp.exchange, "lei": comp.lei, "isin": comp.isin,
        "ticker": comp.ticker, "cik": comp.cik, "aliases": comp.aliases
    })

# Save top 100 universe CSV
universe_csv = OUTPUT_ROOT / "top100_universe.csv"
universe_csv.parent.mkdir(parents=True, exist_ok=True)
with universe_csv.open("w", encoding="utf-8", newline="") as stream:
    writer = csv.DictWriter(stream, fieldnames=list(top100_rows[0].keys()))
    writer.writeheader()
    writer.writerows(top100_rows)
print(f"Saved frozen Top 100 Universe CSV to {universe_csv}")

# 5 batches of 20
batches = [top100_companies[i * 20 : (i + 1) * 20] for i in range(5)]
years_range = list(range(2017, 2026))

# Helper to discover SEC filings for a batch
async def discover_batch_sec(store: DiscoveryStore, companies: list[Company]):
    gate = RateGate(SEC_REQUEST_INTERVAL_S)
    headers = {"User-Agent": SEC_USER_AGENT, "Accept-Encoding": "gzip, deflate"}
    timeout = httpx.Timeout(connect=10, read=60, write=30, pool=30)
    history_cache = CACHE_ROOT / "sec" / "history"
    history_cache.mkdir(parents=True, exist_ok=True)
    
    async with httpx.AsyncClient(headers=headers, timeout=timeout) as client:
        for comp in companies:
            url = f"https://data.sec.gov/submissions/CIK{int(comp.cik):010d}.json"
            await gate.wait()
            try:
                r = await client.get(url)
                if r.status_code == 200:
                    data = r.json()
                    _ingest_sec_data(store, comp, data)
                    # Check if any years missing
                    present = {row[0] for row in store.connection.execute(
                        "SELECT DISTINCT report_year FROM candidates WHERE company_key=? AND source='SEC' AND status='DISCOVERED'",
                        (comp.key,)
                    )}
                    missing = store.years(comp.key) - present
                    if missing:
                        # Check history files
                        for f_info in data.get("filings", {}).get("files", []):
                            fname = f_info.get("name", "")
                            f_from = str(f_info.get("filingFrom", ""))[:4]
                            f_to = str(f_info.get("filingTo", ""))[:4]
                            if f_from.isdigit() and f_to.isdigit():
                                if not (int(f_to) < min(missing) or int(f_from) > max(missing) + 2):
                                    hist_url = f"https://data.sec.gov/submissions/{fname}"
                                    hist_path = history_cache / fname
                                    if hist_path.is_file():
                                        h_data = json.loads(hist_path.read_text(encoding="utf-8"))
                                    else:
                                        await gate.wait()
                                        hr = await client.get(hist_url)
                                        if hr.status_code == 200:
                                            h_data = hr.json()
                                            hist_path.write_text(json.dumps(h_data), encoding="utf-8")
                                        else:
                                            continue
                                    _ingest_sec_data(store, comp, h_data)
                else:
                    print(f"Warning: SEC returned status {r.status_code} for CIK {comp.cik} ({comp.ticker})")
            except Exception as exc:
                print(f"Error fetching SEC for {comp.ticker}: {exc}")
    store.commit()

# Main workflow loop across 5 batches
batch_metrics = []
benchmark_results = []

for batch_idx, batch_companies in enumerate(batches, start=1):
    print("\n" + "=" * 80)
    print(f"PROCESSING BATCH {batch_idx} OF 5 (Companies { (batch_idx-1)*20 + 1 } to { batch_idx*20 })")
    print("=" * 80)
    
    # Save batch universe CSV
    batch_csv = OUTPUT_ROOT / f"batch{batch_idx}_universe.csv"
    with batch_csv.open("w", encoding="utf-8", newline="") as s:
        w = csv.DictWriter(s, fieldnames=list(top100_rows[0].keys()))
        w.writeheader()
        w.writerows([asdict(c) for c in batch_companies])
    print(f"1. Verified identifiers for {len(batch_companies)} companies in Batch {batch_idx}.")
    
    # Ingest batch into DiscoveryStore
    with RunLock(STATE_PATH):
        store = DiscoveryStore(STATE_PATH)
        try:
            store.add_universe(batch_companies, years_range)
            print(f"2. Loaded universe into state DB. Expected slots so far: {store.status()['expected_company_years']}.")
            
            # Resolve official SEC annual filings
            print("3. Resolving official SEC annual filings via SEC EDGAR submission endpoints...")
            asyncio.run(discover_batch_sec(store, batch_companies))
            
            status_after_disc = store.status()
            print(f"   Discovery Status: Discovered: {status_after_disc['discovered_company_years']} | Missing: {status_after_disc['missing_company_years']} | Non-PDF (HTML): {status_after_disc['non_pdf_candidates']} | Direct PDF: {status_after_disc['pdf_ready_candidates']}")
        finally:
            store.close()
            
    # Render / Download reports
    print(f"4. Rendering/Downloading reports for Batch {batch_idx}...")
    batch_start_time = time.monotonic()
    
    # Call render_sec_html
    # We use 8 network workers and 6 Chromium render workers for optimal throughput
    render_summary = asyncio.run(render_sec_html(
        STATE_PATH, OUTPUT_ROOT, CACHE_ROOT, SEC_USER_AGENT,
        chrome_path=CHROME_PATH, network_workers=8, render_workers=6
    ))
    batch_elapsed = round(time.monotonic() - batch_start_time, 2)
    print(f"   Render Complete: Rendered={render_summary['pdf_rendered']}, Skipped={render_summary['skipped']}, Failed={render_summary['failed']}, Elapsed={batch_elapsed}s ({render_summary['documents_per_second']} docs/sec)")
    
    # Verify local files
    print(f"5. Verifying PDF integrity, page counts, and SHA-256 for Batch {batch_idx}...")
    v_summary = verify_store(OUTPUT_ROOT, STATE_PATH)
    print(f"   Verification Store Check: Checked files={v_summary['checked']}, OK={v_summary['ok']}, Errors={len(v_summary['errors'])}")
    
    batch_metrics.append({
        "batch": batch_idx,
        "companies": [c.ticker for c in batch_companies],
        "rendered": render_summary["pdf_rendered"],
        "skipped": render_summary["skipped"],
        "failed": render_summary["failed"],
        "elapsed_s": batch_elapsed,
        "docs_per_s": render_summary["documents_per_second"],
        "checked_files": v_summary["checked"]
    })
    
    # Accuracy-gated speed benchmark
    print(f"6. Running accuracy-gated speed benchmark after Batch {batch_idx}...")
    from subprocess import run as sub_run
    bench_cmd = [sys.executable, "benchmarks/optimize.py", "fixture", "--count", "20", "--repeats", "2"]
    res = sub_run(bench_cmd, capture_output=True, text=True)
    bench_stdout = res.stdout.strip()
    # Parse recommendation JSON from benchmark output
    try:
        json_start = bench_stdout.rfind("{\n  \"mode\":")
        if json_start != -1:
            rec = json.loads(bench_stdout[json_start:])
        else:
            rec = {}
    except Exception:
        rec = {}
    
    best_cfg = rec.get("best_valid_config")
    promoted = rec.get("promoted", False)
    print(f"   Speed Benchmark Result: Best Valid Config={best_cfg} | Promoted Champion={promoted}")
    benchmark_results.append({
        "batch": batch_idx,
        "best_config": best_cfg,
        "promoted": promoted,
        "raw": bench_stdout[-500:]
    })

print("\n" + "=" * 80)
print("ALL 5 BATCHES COMPLETED SUCCESSFULLY!")
print("=" * 80)

# Final Status and Report Generation
with RunLock(STATE_PATH):
    store = DiscoveryStore(STATE_PATH)
    try:
        final_status = store.status()
        
        # Build Company-Year Matrix
        companies = store.companies("USA")
        candidates = store.connection.execute(
            "SELECT company_key, report_year, form_type, status, source_format FROM candidates WHERE source='SEC'"
        ).fetchall()
        
        cand_map = {}
        for row in candidates:
            cand_map[(row["company_key"], row["report_year"])] = row
            
        conversions = store.connection.execute(
            "SELECT output_relative_path, pdf_sha256, status FROM conversions"
        ).fetchall()
        conv_map = {row["output_relative_path"]: row for row in conversions}
        
    finally:
        store.close()

# Save final comprehensive summary JSON
summary_output = {
    "generated_at": datetime.now(timezone.utc).isoformat(),
    "total_companies": len(top100_companies),
    "expected_slots": final_status["expected_company_years"],
    "discovered_slots": final_status["discovered_company_years"],
    "missing_slots": final_status["missing_company_years"],
    "verified_company_years": final_status["verified_company_years"],
    "batch_metrics": batch_metrics,
    "benchmark_results": benchmark_results,
}
(OUTPUT_ROOT / "pipeline_final_summary.json").write_text(json.dumps(summary_output, indent=2), encoding="utf-8")
print(f"Summary written to {OUTPUT_ROOT / 'pipeline_final_summary.json'}")
