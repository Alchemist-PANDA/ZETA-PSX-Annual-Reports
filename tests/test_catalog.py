import csv
from pathlib import Path

import pytest

from annual_reports.catalog import FIELDS, Report, load_manifest


def row(**changes):
    base = {
        "country": "GBR", "exchange": "XLON", "lei": "2138007ZFQYRUSLU3J98",
        "isin": "GB00BHJYC057", "ticker": "IHG", "fiscal_year": "FY2024",
        "report_type": "AR", "language": "EN", "pdf_url": "https://example.org/ihg.pdf",
        "source_page": "https://example.org/reports", "verified": "true",
    }
    return {**base, **changes}


def test_sop_path_is_exact():
    report = Report.from_row(row())
    assert str(report.relative_path).replace("\\", "/") == (
        "GBR/XLON/2138007ZFQYRUSLU3J98_GB00BHJYC057_IHG/FY2024/"
        "2138007ZFQYRUSLU3J98_GBR_XLON_IHG_GB00BHJYC057_FY2024_AR_EN.pdf"
    )


@pytest.mark.parametrize("changes", [
    {"country": "UK"}, {"fiscal_year": "2024"}, {"language": "FR"},
    {"report_type": "Annual Report"}, {"ticker": "a/b"},
    {"pdf_url": "http://example.org/file.pdf"},
])
def test_invalid_rows_fail(changes):
    with pytest.raises(ValueError):
        Report.from_row(row(**changes))


def test_duplicate_target_fails(tmp_path: Path):
    manifest = tmp_path / "reports.csv"
    with manifest.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerow(row())
        writer.writerow(row(pdf_url="https://example.org/other.pdf"))
    with pytest.raises(ValueError, match="duplicate canonical target"):
        load_manifest(manifest)
