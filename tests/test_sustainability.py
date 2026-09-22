import csv
import zipfile

import fitz

from annual_reports.catalog import load_manifest
from annual_reports.sustainability import (METADATA_FIELDS, classify,
                                           import_metadata, ingest_zip)


def _csv(path, fields, rows):
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def test_classification():
    assert classify("Microsoft 2019 CSR Annual Report", "CSR") == ("STANDALONE_SR", "SR")
    assert classify("Business & Sustainability Report", "Integrated") == ("INTEGRATED_REPORT", "IR")
    assert classify("2024 Environmental Update", "Sustainability") == ("TOPIC_UPDATE", "CLIMATE")


def test_general_metadata_and_bulk_zip(tmp_path):
    universe = tmp_path / "universe.csv"
    _csv(universe, ("country", "company_name", "exchange", "lei", "isin", "ticker", "cik", "aliases"), [
        dict(country="USA", company_name="Example US", exchange="XNAS", lei="INR2EJN1ERAN0W5ZP974",
             isin="US5949181045", ticker="MSFT", cik="789019", aliases=""),
        dict(country="GBR", company_name="Example UK", exchange="XLON", lei="2138007ZFQYRUSLU3J98",
             isin="GB00BHJYC057", ticker="IHG", cik="", aliases=""),
    ])
    metadata = tmp_path / "metadata.csv"
    _csv(metadata, METADATA_FIELDS, [
        dict(company_name="Example US", ticker="MSFT", isin="US5949181045", country="USA",
             report_year="2019", report_title="2019 CSR Annual Report", report_type="CSR",
             language="English", source_url="https://example.org/microsoft.pdf", filename="", page_count="2"),
        dict(company_name="Example UK", ticker="IHG", isin="GB00BHJYC057", country="GBR",
             report_year="2024", report_title="2024 Sustainability Report", report_type="Sustainability",
             language="EN", source_url="", filename="reports/ihg-2024.pdf", page_count="3"),
    ])
    direct, bulk, review = (tmp_path / name for name in ("direct.csv", "bulk.csv", "review.csv"))
    summary = import_metadata(universe, metadata, direct, bulk, review)
    assert summary["direct"] == 1
    assert summary["bulk"] == 1
    assert summary["review"] == 0
    assert load_manifest(direct)[0].report_type == "SR"

    doc = fitz.open()
    doc.new_page()
    payload = doc.tobytes()
    doc.close()
    archive = tmp_path / "reports.zip"
    with zipfile.ZipFile(archive, "w") as zipped:
        zipped.writestr("reports/ihg-2024.pdf", payload)
    result = ingest_zip(archive, bulk, tmp_path / "output", tmp_path / "state.sqlite3")
    assert result["verified"] == 1
    assert len(list((tmp_path / "output").rglob("*.pdf"))) == 1
