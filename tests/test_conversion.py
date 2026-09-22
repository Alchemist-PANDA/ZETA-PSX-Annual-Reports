import asyncio
import io
import os
import shutil
import zipfile
from pathlib import Path

import pytest

from annual_reports.conversion import render_sec_html
from annual_reports.discovery import Company, DiscoveryStore
from annual_reports.engine import verify_store
from annual_reports.fca_conversion import _safe_extract, render_fca_originals


def _chrome() -> Path | None:
    paths = [Path("C:/Program Files/Google/Chrome/Application/chrome.exe"),
             Path("C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe")]
    for path in paths:
        if path.is_file():
            return path
    binary = shutil.which("google-chrome") or shutil.which("chromium")
    return Path(binary) if binary else None


def test_cached_sec_html_renders_to_sop_pdf(tmp_path: Path):
    pytest.importorskip("playwright")
    chrome = _chrome()
    if chrome is None:
        pytest.skip("Chrome/Chromium is unavailable")
    state_path = tmp_path / "state.sqlite3"
    store = DiscoveryStore(state_path)
    company = Company("USA", "Example Inc", "XNAS", "2138007ZFQYRUSLU3J98",
                      "US0000000001", "EXM", "320193")
    try:
        store.add_universe([company], [2024])
        store.upsert_candidate(
            company_key=company.key, report_year=2024, source="SEC",
            source_record_id="0000320193-25-000001",
            source_url="https://www.sec.gov/Archives/edgar/data/320193/000032019325000001/annual.htm",
            source_format="html", form_type="10-K", filing_date="2025-01-01",
            report_date="2024-12-31", status="DISCOVERED", verified=True,
        )
        store.commit()
        candidate_id = store.connection.execute("SELECT id FROM candidates").fetchone()[0]
    finally:
        store.close()
    cache = tmp_path / "cache" / "sec" / "html"
    cache.mkdir(parents=True)
    html = "<html><body><h1>Example Inc Annual Report 2024</h1>" + "<p>Financial statements and auditor report.</p>" * 20 + "</body></html>"
    (cache / f"{candidate_id}.html").write_text(html, encoding="utf-8")
    coroutine = render_sec_html(state_path, tmp_path / "data", tmp_path / "cache",
                                "Example Research contact@example.org", chrome_path=chrome)
    summary = asyncio.run(coroutine)
    assert summary["pdf_rendered"] == 1, summary
    assert summary["html_downloaded"] == 0
    assert verify_store(tmp_path / "data", state_path)["ok"]


def test_cached_fca_zip_renders_to_sop_pdf(tmp_path: Path):
    pytest.importorskip("playwright")
    chrome = _chrome()
    if chrome is None:
        pytest.skip("Chrome/Chromium is unavailable")
    state_path = tmp_path / "state.sqlite3"
    store = DiscoveryStore(state_path)
    company = Company("GBR", "Example PLC", "XLON", "2138007ZFQYRUSLU3J98",
                      "GB00BHJYC057", "EXM", "")
    try:
        store.add_universe([company], [2024])
        store.upsert_candidate(company_key=company.key, report_year=2024,
                               source="FCA_NSM", source_record_id="fixture",
                               source_url="https://data.fca.org.uk/artefacts/NSM/example.zip",
                               source_format="zip", form_type="AFR",
                               status="MANUAL_REVIEW", verified=False)
        store.commit()
        candidate_id = store.connection.execute("SELECT id FROM candidates").fetchone()[0]
    finally:
        store.close()
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("report/annual.xhtml",
                             "<html><head><title>Annual report 2024</title></head><body>"
                             "<h1>Example PLC Annual Report 2024</h1><p>Auditor and financial statements</p>"
                             "</body></html>")
    cache = tmp_path / "cache" / "fca" / "originals"
    cache.mkdir(parents=True)
    (cache / f"{candidate_id}.bin").write_bytes(buffer.getvalue())
    result = asyncio.run(render_fca_originals(state_path, tmp_path / "data", tmp_path / "cache",
                                               chrome_path=chrome))
    assert result["pdf_completed"] == 1, result
    assert result["originals_downloaded"] == 0
    assert verify_store(tmp_path / "data", state_path)["ok"]


def test_fca_zip_rejects_path_traversal(tmp_path: Path):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("../outside.xhtml", "<html>bad</html>")
    with zipfile.ZipFile(io.BytesIO(buffer.getvalue())) as archive:
        with pytest.raises(ValueError, match="unsafe path"):
            _safe_extract(archive, tmp_path)


def test_render_sec_html_skips_when_ars_pdf_present(tmp_path: Path):
    state_path = tmp_path / "state.sqlite3"
    store = DiscoveryStore(state_path)
    company = Company("USA", "Example Inc", "XNAS", "2138007ZFQYRUSLU3J98",
                      "US0000000001", "EXM", "320193")
    try:
        store.add_universe([company], [2024])
        # Add 10-K HTML candidate
        store.upsert_candidate(
            company_key=company.key, report_year=2024, source="SEC",
            source_record_id="0000320193-25-000001",
            source_url="https://www.sec.gov/Archives/edgar/data/320193/000032019325000001/annual.htm",
            source_format="html", form_type="10-K", filing_date="2025-01-01",
            report_date="2024-12-31", status="DISCOVERED", verified=True,
        )
        # Add ARS PDF candidate for the same fiscal year
        store.upsert_candidate(
            company_key=company.key, report_year=2024, source="SEC",
            source_record_id="0000320193-25-000002",
            source_url="https://www.sec.gov/Archives/edgar/data/320193/000032019325000002/glossy_ar.pdf",
            source_format="pdf", form_type="ARS", filing_date="2025-02-01",
            report_date="2024-12-31", status="DISCOVERED", verified=True,
        )
        store.commit()
        from annual_reports.conversion import _jobs
        jobs = _jobs(store, limit=None)
        # Because an authentic PDF candidate exists, Chromium rendering jobs must be empty!
        assert len(jobs) == 0
    finally:
        store.close()

