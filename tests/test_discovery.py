import asyncio
import csv
import json
import zipfile
from pathlib import Path

from annual_reports.catalog import load_manifest
from annual_reports.discovery import (Company, DiscoveryStore, discover_sec_bulk,
                                      discover_sec_history, export_pdf_manifest, import_fca_csv,
                                      import_fca_historic_map, parse_years, sec_archive_url)


def test_sec_bulk_uses_report_date_and_keeps_non_pdf(tmp_path: Path):
    archive = tmp_path / "submissions.zip"
    filings = {
        "form": ["10-K", "10-K", "10-K/A", "10-Q"],
        "reportDate": ["2024-09-28", "2023-09-30", "2024-09-28", "2024-06-30"],
        "filingDate": ["2025-01-01", "2024-01-01", "2025-02-01", "2024-07-01"],
        "accessionNumber": ["0000320193-25-000001", "0000320193-24-000001",
                            "0000320193-25-000002", "0000320193-24-000003"],
        "primaryDocument": ["annual.htm", "annual-2023.pdf", "amend.htm", "quarter.htm"],
    }
    with zipfile.ZipFile(archive, "w") as stream:
        stream.writestr("CIK0000320193.json", json.dumps({"filings": {
            "recent": filings,
            "files": [{"name": "CIK0000320193-submissions-001.json",
                       "filingFrom": "2022-01-01", "filingTo": "2023-01-01"}],
        }}))
    store = DiscoveryStore(tmp_path / "state.sqlite3")
    try:
        company = Company("USA", "Example Inc", "XNAS", "2138007ZFQYRUSLU3J98",
                          "US0000000001", "EXM", "320193")
        store.add_universe([company], [2022, 2023, 2024])
        result = discover_sec_bulk(store, archive)
        assert result["candidate_filings"] == 3
        history_cache = tmp_path / "history"
        history_cache.mkdir()
        (history_cache / "CIK0000320193-submissions-001.json").write_text(json.dumps({
            "form": ["10-K"], "reportDate": ["2022-09-30"],
            "filingDate": ["2023-01-01"],
            "accessionNumber": ["0000320193-23-000001"],
            "primaryDocument": ["annual-2022.htm"],
        }), encoding="utf-8")
        history = asyncio.run(discover_sec_history(store, archive, history_cache, ""))
        assert history["candidate_filings"] == 1
        status = store.status()
        assert status["expected_company_years"] == 3
        assert status["discovered_company_years"] == 3
        manifest = tmp_path / "resolved.csv"
        exported = export_pdf_manifest(store, manifest)
        assert exported["pdf_rows"] == 1
        report = load_manifest(manifest)[0]
        assert report.fiscal_year == "FY2023"
        assert report.report_type == "10K"
        assert report.pdf_url == sec_archive_url("320193", "0000320193-24-000001", "annual-2023.pdf")
    finally:
        store.close()


def test_fca_csv_joins_lei_and_does_not_guess_year(tmp_path: Path):
    store = DiscoveryStore(tmp_path / "state.sqlite3")
    try:
        company = Company("GBR", "Example PLC", "XLON", "2138007ZFQYRUSLU3J98",
                          "GB00BHJYC057", "EXM", "")
        store.add_universe([company], [2023, 2024])
        source = tmp_path / "nsm.csv"
        with source.open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=["Disclosing Organisation LEI", "Description",
                                                      "Category", "Download Link", "Publication Date"])
            writer.writeheader()
            writer.writerow({"Disclosing Organisation LEI": company.lei,
                             "Description": "Annual report and accounts 2023",
                             "Category": "Annual Financial Report",
                             "Download Link": "/artefacts/NSM/2023-report.pdf",
                             "Publication Date": "2024-03-01"})
            writer.writerow({"Disclosing Organisation LEI": company.lei,
                             "Description": "Annual report and accounts",
                             "Category": "Annual Financial Report",
                             "Download Link": "/artefacts/NSM/unknown.pdf",
                             "Publication Date": "2025-03-01"})
        result = import_fca_csv(store, source)
        assert result == {"candidate_records": 2, "unmatched_lei": 0, "manual_review": 1}
        exported = export_pdf_manifest(store, tmp_path / "resolved.csv")
        assert exported["pdf_rows"] == 1
        report = load_manifest(tmp_path / "resolved.csv")[0]
        assert report.fiscal_year == "FY2023"
        assert report.pdf_url == "https://data.fca.org.uk/artefacts/NSM/2023-report.pdf"
    finally:
        store.close()


def test_year_range_rejects_reversed():
    try:
        parse_years("2025:2017")
    except ValueError:
        pass
    else:
        raise AssertionError("reversed year range accepted")


def test_fca_historic_map_resolves_only_needed_links(tmp_path: Path):
    store = DiscoveryStore(tmp_path / "state.sqlite3")
    try:
        company = Company("GBR", "Example PLC", "XLON", "2138007ZFQYRUSLU3J98",
                          "GB00BHJYC057", "EXM", "")
        store.add_universe([company], [2018])
        old = "https://tools.morningstar.co.uk/documentHandler.ashx?DocumentId=123"
        new = "https://data.fca.org.uk/artefacts/NSM/data-migration/123.pdf"
        store.upsert_candidate(company_key=company.key, report_year=2018,
                               source="FCA_NSM", source_record_id="123",
                               source_url=old, source_format="unknown",
                               form_type="AFR", status="MANUAL_REVIEW")
        store.commit()
        archive = tmp_path / "mapping.zip"
        with zipfile.ZipFile(archive, "w") as stream:
            stream.writestr("Mapping 20170101 to 20181231.csv",
                            '"Company Name","Title","Published Date","Morningstar URL","FCA URL"\n'
                            f'"Example PLC","Annual Report 2018","1/1/2019","{old}","{new}"\n'
                            '"Other","Annual Report 2018","1/1/2019","https://old/other","https://data.fca.org.uk/other.pdf"\n')
        result = import_fca_historic_map(store, archive)
        assert result == {"mappings_ingested": 1, "candidates_resolved": 1}
        row = store.connection.execute("SELECT source_url,source_format,status,verified FROM candidates").fetchone()
        assert tuple(row) == (new, "pdf", "DISCOVERED", 1)
    finally:
        store.close()
