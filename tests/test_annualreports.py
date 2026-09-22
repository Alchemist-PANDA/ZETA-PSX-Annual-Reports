import csv
import zipfile

import fitz

from annual_reports.annualreports import FIELDS_IN, import_annualreports
from annual_reports.catalog import load_manifest
from annual_reports.sustainability import ingest_zip


def _csv(path, fields, rows):
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def test_annualreports_authorized_bulk_and_hosted_gate(tmp_path):
    universe = tmp_path / "companies.csv"
    _csv(universe, ("country", "company_name", "exchange", "lei", "isin", "ticker", "cik", "aliases"), [
        dict(country="USA", company_name="Microsoft", exchange="XNAS",
             lei="INR2EJN1ERAN0W5ZP974", isin="US5949181045",
             ticker="MSFT", cik="789019", aliases=""),
    ])
    metadata = tmp_path / "metadata.csv"
    _csv(metadata, FIELDS_IN, [
        dict(country="United States", isin="US5949181045", report_year="2024",
             report_title="2024 Annual Report", pdf_url="https://www.annualreports.com/HostedData/AnnualReports/PDF/NASDAQ_MSFT_2024.pdf",
             zip_member="reports/msft-2024.pdf", source_page="https://www.annualreports.com/Company/microsoft-corporation"),
    ])
    direct, bulk, review = (tmp_path / name for name in ("direct.csv", "bulk.csv", "review.csv"))
    restricted = import_annualreports(universe, metadata, direct, bulk, review)
    assert restricted["direct"] == 0
    assert restricted["bulk"] == 1
    assert restricted["review"] == 1
    allowed = import_annualreports(universe, metadata, direct, bulk, review,
                                   authorized_hosted=True)
    assert allowed["direct"] == 1
    assert load_manifest(direct)[0].report_type == "AR"

    doc = fitz.open()
    doc.new_page()
    payload = doc.tobytes()
    doc.close()
    archive = tmp_path / "bulk.zip"
    with zipfile.ZipFile(archive, "w") as zipped:
        zipped.writestr("reports/msft-2024.pdf", payload)
    result = ingest_zip(archive, bulk, tmp_path / "output", tmp_path / "state.sqlite3")
    assert result["verified"] == 1
    assert len(list((tmp_path / "output").rglob("*.pdf"))) == 1
