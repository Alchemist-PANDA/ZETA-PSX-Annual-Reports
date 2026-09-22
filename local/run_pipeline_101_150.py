import asyncio
import csv
import json
import os
import re
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
import httpx
import fitz

from annual_reports.catalog import Report, load_manifest
from annual_reports.conversion import render_sec_html
from annual_reports.discovery import Company, DiscoveryStore, _ingest_sec_data, parse_years, export_pdf_manifest
from annual_reports.engine import RateGate, RunLock, StateStore, Settings, native_path, verify_store, run as engine_run, SEC_REQUEST_INTERVAL_S

OUTPUT_ROOT = Path("local").resolve()
STATE_PATH = OUTPUT_ROOT / "harvest.sqlite3"
CACHE_ROOT = Path("cache").resolve()
SEC_USER_AGENT = os.getenv("SEC_USER_AGENT", "AcademicResearch research@harvester.edu")
os.environ["SEC_USER_AGENT"] = SEC_USER_AGENT

CHROME_PATH = Path("C:/Program Files/Google/Chrome/Application/chrome.exe")
UNIVERSE_CSV = Path("local/universe_101_150.csv")

def main():
    print("=" * 80)
    print("HARVESTER PIPELINE: US COMPANIES 101-150 (FY2017-2025) HIGH-THROUGHPUT RUN")
    print(f"Universe File    : {UNIVERSE_CSV}")
    print(f"Output Directory : {OUTPUT_ROOT}")
    print(f"State Database   : {STATE_PATH}")
    print(f"Chrome Binary    : {CHROME_PATH}")
    print(f"SEC User-Agent   : {SEC_USER_AGENT}")
    print("=" * 80, flush=True)

    # 1. Load Companies
    with open(UNIVERSE_CSV, "r", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    
    companies = []
    for r in rows:
        companies.append(Company(
            country=r["country"],
            company_name=r["company_name"],
            exchange=r["exchange"],
            lei=r["lei"],
            isin=r["isin"],
            ticker=r["ticker"],
            cik=r["cik"],
            aliases=r["aliases"]
        ))
    
    years = list(range(2017, 2026))
    print(f"Loaded {len(companies)} companies, {len(years)} years ({len(companies) * len(years)} total slots).", flush=True)

    # 2. Ingest Universe and Run Discovery
    print("\n--- Phase 1: SEC EDGAR Discovery (Submissions API) ---", flush=True)
    t_disc0 = time.monotonic()
    history_cache = CACHE_ROOT / "sec" / "history"
    history_cache.mkdir(parents=True, exist_ok=True)
    with RunLock(STATE_PATH):
        store = DiscoveryStore(STATE_PATH)
        try:
            store.add_universe(companies, years)
            
            headers = {"User-Agent": SEC_USER_AGENT, "Accept-Encoding": "gzip, deflate"}
            with httpx.Client(timeout=20.0, headers=headers) as client:
                for idx, comp in enumerate(companies, 1):
                    t_c0 = time.monotonic()
                    time.sleep(SEC_REQUEST_INTERVAL_S)
                    url = f"https://data.sec.gov/submissions/CIK{int(comp.cik):010d}.json"
                    try:
                        r = client.get(url)
                        if r.status_code == 200:
                            data = r.json()
                            _ingest_sec_data(store, comp, data)
                            
                            # Check missing years to ingest history files if needed
                            present = {row[0] for row in store.connection.execute(
                                "SELECT DISTINCT report_year FROM candidates WHERE company_key=? AND source='SEC' AND status='DISCOVERED'",
                                (comp.key,)
                            )}
                            missing = store.years(comp.key) - present
                            if missing:
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
                                                time.sleep(SEC_REQUEST_INTERVAL_S)
                                                hr = client.get(hist_url)
                                                if hr.status_code == 200:
                                                    h_data = hr.json()
                                                    hist_path.write_text(json.dumps(h_data), encoding="utf-8")
                                                else:
                                                    continue
                                            _ingest_sec_data(store, comp, h_data)
                            store.commit()
                            discovered_count = store.connection.execute(
                                "SELECT count(*) FROM candidates WHERE company_key=? AND source='SEC'",
                                (comp.key,)
                            ).fetchone()[0]
                            print(f"[{idx:2d}/50] Discovered {discovered_count:2d} filings for {comp.ticker:6s} ({comp.company_name}) in {time.monotonic()-t_c0:.2f}s", flush=True)
                    except Exception as e:
                        print(f"Error on {comp.ticker}: {e}", flush=True)
        finally:
            store.close()
    t_disc = time.monotonic() - t_disc0
    print(f"Discovery completed in {t_disc:.2f}s.\n", flush=True)

    # 3. Analyze Discovered vs Missing Slots
    store = DiscoveryStore(STATE_PATH)
    try:
        discovered_slots = []
        missing_slots = []
        for c in companies:
            for y in years:
                row = store.connection.execute(
                    "SELECT id, source_format, form_type, filing_date, source_url FROM candidates WHERE company_key=? AND report_year=? AND status IN ('DISCOVERED','VERIFIED') AND verified=1",
                    (c.key, y)
                ).fetchone()
                if row:
                    discovered_slots.append({"company": c.ticker, "year": y, "id": row[0], "format": row[1], "form": row[2], "url": row[4]})
                else:
                    missing_slots.append({"company": c.ticker, "year": y})
    finally:
        store.close()

    print(f"Slots Summary: Total Expected={len(companies)*len(years)}, Discovered={len(discovered_slots)}, Missing={len(missing_slots)}", flush=True)
    if missing_slots:
        print(f"Missing Slots Breakdown by Company:")
        missing_by_comp = Counter(m["company"] for m in missing_slots)
        for comp, cnt in missing_by_comp.most_common():
            comp_years = [str(m["year"]) for m in missing_slots if m["company"] == comp]
            print(f"  {comp:6s}: {cnt} missing ({', '.join(comp_years)})", flush=True)

    # 4. Phase 2: METHOD 1 (Direct Graphic PDF Passthrough - PRIMARY)
    print("\n--- Phase 2: Method #1 (Direct Graphic PDF Passthrough - PRIMARY) ---", flush=True)
    manifest_path = OUTPUT_ROOT / "pipeline_pdf_manifest.csv"
    with RunLock(STATE_PATH):
        store = DiscoveryStore(STATE_PATH)
        try:
            exported = export_pdf_manifest(store, manifest_path)
            print(f"Discovered {exported['pdf_rows']} official graphic ARS PDF candidates for direct passthrough.", flush=True)
        finally:
            store.close()
    
    t_pdf0 = time.monotonic()
    pdf_downloaded = 0
    if exported["pdf_rows"] > 0:
        pdf_reports = load_manifest(manifest_path)
        pdf_settings = Settings(
            output_root=OUTPUT_ROOT,
            state_path=STATE_PATH,
            workers=8,
            per_host=4,
            sec_user_agent=SEC_USER_AGENT,
        )
        pdf_summary = asyncio.run(engine_run(pdf_reports, pdf_settings))
        pdf_downloaded = pdf_summary["downloaded"]
        t_pdf = time.monotonic() - t_pdf0
        print(f"Method #1 Execution Complete: Downloaded {pdf_downloaded} official graphic PDFs in {t_pdf:.2f}s ({pdf_summary['bytes']/(1024*1024):.2f} MB, {pdf_summary['pdfs_per_second']:.2f} docs/sec).", flush=True)
    else:
        print("No direct ARS PDFs discovered; falling back to Chromium HTML rendering for all slots.", flush=True)

    # 5. Phase 3: METHOD 2 (Headless Chromium Layout & Rendering - AUTOMATIC FALLBACK)
    print("\n--- Phase 3: Method #2 (Chromium Layout & Rendering - AUTOMATIC FALLBACK) ---", flush=True)
    t_net0 = time.monotonic()
    
    # Query pending HTML jobs that do NOT have a direct PDF candidate
    with RunLock(STATE_PATH):
        store = DiscoveryStore(STATE_PATH)
        try:
            from annual_reports.conversion import _jobs
            pending_html_jobs = _jobs(store, limit=None)
            print(f"Pending HTML filings requiring Chromium layout: {len(pending_html_jobs)}", flush=True)
        finally:
            store.close()

    if pending_html_jobs:
        print(f"Pre-caching {len(pending_html_jobs)} HTML filings to local SSD...", flush=True)
        cached_count = 0
        downloaded_count = 0
        with httpx.Client(timeout=45.0, headers={"User-Agent": SEC_USER_AGENT, "Accept-Encoding": "gzip, deflate"}) as client:
            for idx, job in enumerate(pending_html_jobs, 1):
                cache_file = CACHE_ROOT / "sec" / "html" / f"{job.candidate_id}.html"
                if cache_file.is_file() and cache_file.stat().st_size >= 300:
                    cached_count += 1
                    continue
                time.sleep(SEC_REQUEST_INTERVAL_S)
                try:
                    r = client.get(job.report.pdf_url)
                    if r.status_code == 200 and len(r.content) >= 300:
                        cache_file.parent.mkdir(parents=True, exist_ok=True)
                        cache_file.write_bytes(r.content)
                        downloaded_count += 1
                        if downloaded_count % 10 == 0:
                            print(f"  Downloaded {downloaded_count} new filings...", flush=True)
                except Exception as e:
                    print(f"  Error downloading {job.report.ticker} {job.report.fiscal_year}: {e}", flush=True)

        t_net = time.monotonic() - t_net0
        print(f"Pre-cache complete in {t_net:.2f}s (Already cached: {cached_count}, Downloaded: {downloaded_count}).", flush=True)

    t_render0 = time.monotonic()
    
    # We call render_sec_html with 6 workers (matching 6 physical cores)
    render_summary = asyncio.run(render_sec_html(
        STATE_PATH,
        OUTPUT_ROOT,
        CACHE_ROOT,
        SEC_USER_AGENT,
        chrome_path=CHROME_PATH,
        network_workers=8,
        render_workers=6
    ))
    t_render = time.monotonic() - t_render0
    
    print("\n" + "=" * 80)
    print("RENDER EXECUTION COMPLETE:")
    print(f"  PDFs Rendered  : {render_summary['pdf_rendered']}")
    print(f"  Skipped (prior): {render_summary['skipped']}")
    print(f"  Failed         : {render_summary['failed']}")
    print(f"  Elapsed Time   : {t_render:.2f}s")
    if render_summary['pdf_rendered'] > 0:
        docs_per_s = render_summary['pdf_rendered'] / t_render
        s_per_doc = t_render / render_summary['pdf_rendered']
        print(f"  Throughput     : {docs_per_s:.2f} docs/sec ({s_per_doc:.2f} seconds / doc)")
    print("=" * 80, flush=True)

    # 6. Phase 4: Local Store Verification
    print("\n--- Phase 4: PDF Integrity, Page Count, and Ledger Audit ---", flush=True)
    v_summary = verify_store(OUTPUT_ROOT, STATE_PATH)
    print(f"Verification Check:")
    print(f"  Total Checked PDFs : {v_summary['checked']}")
    print(f"  Store Integrity OK : {v_summary['ok']}")
    print(f"  Errors             : {len(v_summary['errors'])}")
    if v_summary['errors']:
        for err in v_summary['errors'][:10]:
            print(f"    - {err}")

    # Count total pages for 101-150 filings
    total_pages_101_150 = 0
    ledger = StateStore(STATE_PATH)
    try:
        # Check newly created reports
        cursor = ledger.connection.execute(
            "SELECT count(*), sum(pages), sum(size) FROM conversions c JOIN candidates cd ON c.candidate_id=cd.id WHERE cd.company_key IN ({})".format(
                ",".join(f"'{comp.key}'" for comp in companies)
            )
        )
        c_count, c_pages, c_size = cursor.fetchone()
        print(f"\nAudit for Ranks 101-150:")
        print(f"  Total Verified PDFs : {c_count}")
        print(f"  Total Printed Pages : {c_pages}")
        print(f"  Total Storage Size  : {(c_size or 0)/(1024*1024):.2f} MB")
        if c_pages and t_render > 0:
            print(f"  Print Page Rate     : {c_pages / t_render:.1f} pages / sec")
    finally:
        ledger.close()

    # Save summary report
    summary_data = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "universe": "US Companies Ranked 101-150",
        "total_companies": len(companies),
        "expected_slots": len(companies) * len(years),
        "discovered_slots": len(discovered_slots),
        "missing_slots": len(missing_slots),
        "missing_details": [{"company": m["company"], "year": m["year"]} for m in missing_slots],
        "render_metrics": {
            "rendered": render_summary["pdf_rendered"],
            "skipped": render_summary["skipped"],
            "failed": render_summary["failed"],
            "elapsed_s": t_render,
            "docs_per_s": render_summary["pdf_rendered"] / max(t_render, 0.001),
            "total_pages": c_pages,
            "pages_per_s": (c_pages or 0) / max(t_render, 0.001),
        },
        "verification": {
            "checked": v_summary["checked"],
            "ok": v_summary["ok"],
            "errors": v_summary["errors"]
        }
    }
    with open("local/pipeline_101_150_summary.json", "w", encoding="utf-8") as f:
        json.dump(summary_data, f, indent=2)
    print("\nSaved pipeline summary to local/pipeline_101_150_summary.json", flush=True)

if __name__ == "__main__":
    main()
