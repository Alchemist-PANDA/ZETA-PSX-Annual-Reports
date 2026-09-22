import asyncio
import csv

import pytest
import fitz

from annual_reports.annualreports_site import (PageClient, audit_reports, discover,
                                               parse_profile, search_links)
from annual_reports.catalog import Report, load_manifest
from annual_reports.engine import Settings, run


SEARCH = '''<span class="companyName"><a href="/Company/microsoft-corporation">Microsoft Corporation</a></span>'''
PROFILE = '''
<h1>Microsoft Corporation</h1>
<li class="top_content_list"><div class="left"><span class="ticker_name">MSFT</span></div>
<div class="right">Exchange NASDAQ</div></li>
<div class="most_recent_content_block"><span class="bold_txt">2025 Annual Report and Form 10K</span>
<div class="view_btn"><a href="/Click/37135">View PDF</a><a href="/Click/html">View Form 10K (HTML)</a></div></div>
<div class="archived_report_content_block"><ul><li><div class="text_block">
<span class="heading">Microsoft Corporation 2024 Annual Report - (PDF)</span>
<span class="btn_archived download"><a href="/HostedData/AnnualReportArchive/m/NASDAQ_MSFT_2024.pdf">Download</a></span>
</div></li></ul></div>
'''


def test_page_parsing_resolves_explicit_pdf_links():
    assert search_links(SEARCH) == [
        ("Microsoft Corporation", "https://www.annualreports.com/Company/microsoft-corporation")]
    listing = parse_profile(PROFILE)
    assert (listing.name, listing.ticker, listing.exchange) == ("Microsoft Corporation", "MSFT", "NASDAQ")
    assert listing.reports[2024].endswith("NASDAQ_MSFT_2024.pdf")
    assert listing.reports[2025].endswith("/Click/37135")


def test_discovery_writes_sop_manifest_and_missing_years(tmp_path, monkeypatch):
    universe = tmp_path / "universe.csv"
    with universe.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(("country", "company_name", "exchange", "lei", "isin", "ticker", "cik", "aliases"))
        writer.writerow(("USA", "Microsoft Corporation", "XNAS", "INR2EJN1ERAN0W5ZP974",
                         "US5949181045", "MSFT", "789019", ""))

    async def fake_get(self, url):
        return SEARCH if "/Companies?" in url else PROFILE

    monkeypatch.setattr(PageClient, "get", fake_get)
    manifest, unresolved = tmp_path / "manifest.csv", tmp_path / "unresolved.csv"
    result = asyncio.run(discover(universe, manifest, unresolved, tmp_path / "cache",
                                  years=(2023, 2025)))
    assert result["reports_discovered"] == 2
    assert result["unresolved_slots"] == 1
    reports = load_manifest(manifest)
    assert {r.fiscal_year for r in reports} == {"FY2024", "FY2025"}
    assert all(r.report_type == "AR" for r in reports)
    for report in reports:
        target = tmp_path / "output" / report.relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        with fitz.open() as pdf:
            pdf.new_page().insert_text((72, 72), "Microsoft Corporation 2025 Annual Report")
            target.write_bytes(pdf.tobytes())
    audit = tmp_path / "audit.csv"
    summary = audit_reports(universe, manifest, tmp_path / "output", audit)
    assert summary["passed"] == 1
    assert summary["review"] == 1
    assert "fiscal year not found" in audit.read_text(encoding="utf-8")


def test_hosted_transfer_requires_explicit_access(tmp_path):
    report = Report.from_row({
        "country": "USA", "exchange": "XNAS", "lei": "INR2EJN1ERAN0W5ZP974",
        "isin": "US5949181045", "ticker": "MSFT", "fiscal_year": "FY2024",
        "report_type": "AR", "language": "EN",
        "pdf_url": "https://www.annualreports.com/HostedData/AnnualReports/PDF/NASDAQ_MSFT_2024.pdf",
        "source_page": "", "verified": "true",
    })
    with pytest.raises(ValueError, match="authorized-hosted"):
        asyncio.run(run([report], Settings(tmp_path / "out", tmp_path / "state.sqlite3")))
