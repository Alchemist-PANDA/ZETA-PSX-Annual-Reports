"""AnnualReports.com authorized-export adapter for annual report manifests.

The public site does not document a bulk endpoint. This module consumes metadata
and ZIPs obtained with appropriate access; it does not crawl the public site.
"""

from __future__ import annotations

import csv
import re
from pathlib import Path
from urllib.parse import urlsplit

from .catalog import FIELDS, Report, _url
from .discovery import read_universe
from .sustainability import INDEX_FIELDS


FIELDS_IN = ("country", "isin", "report_year", "report_title", "pdf_url",
             "zip_member", "source_page")
REVIEW_FIELDS = (*FIELDS_IN, "reason", "line")
ANNUAL_TITLE = re.compile(r"annual report|form 10[- ]?k|report and accounts", re.I)


def _country(value: str) -> str:
    return {
        "US": "USA", "USA": "USA", "UNITED STATES": "USA",
        "UK": "GBR", "GB": "GBR", "GBR": "GBR", "UNITED KINGDOM": "GBR",
    }.get(value.upper(), value.upper())


def _hosted(url: str) -> bool:
    host = urlsplit(url).hostname or ""
    return host == "annualreports.com" or host.endswith(".annualreports.com")


def import_annualreports(universe: Path, metadata: Path, direct_output: Path,
                         bulk_output: Path, review_output: Path,
                         *, authorized_hosted: bool = False,
                         years: tuple[int, int] = (2017, 2025)) -> dict:
    """Join normalized vendor metadata to the SOP identity roster.

    Only URLs explicitly marked as authorized may be emitted for automated
    downloads from AnnualReports.com HostedData/Click endpoints.
    """
    companies = {(company.country, company.isin): company for company in read_universe(universe)}
    selected: dict[Path, dict[str, str]] = {}
    conflicts: set[Path] = set()
    review: list[dict[str, str]] = []
    with metadata.open("r", encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        headers = {re.sub(r"[^a-z0-9]", "", name.casefold()): name
                   for name in (reader.fieldnames or [])}
        columns = {field: headers.get(re.sub(r"[^a-z0-9]", "", field)) for field in FIELDS_IN}
        if any(name is None for name in columns.values()):
            raise ValueError(f"AnnualReports metadata requires: {', '.join(FIELDS_IN)}")
        for line, raw in enumerate(reader, 2):
            row = {field: (raw.get(columns[field]) or "").strip() for field in FIELDS_IN}
            row["country"] = _country(row["country"])
            row["isin"] = row["isin"].upper()
            company = companies.get((row["country"], row["isin"]))
            year_text = row["report_year"].removeprefix("FY")
            reason = ""
            if company is None:
                reason = "company not in universe"
            elif not year_text.isdigit() or not years[0] <= int(year_text) <= years[1]:
                reason = "year outside requested range"
            elif not ANNUAL_TITLE.search(row["report_title"]):
                reason = "title does not identify an annual report"
            elif not row["pdf_url"] and not row["zip_member"]:
                reason = "no direct URL or ZIP member"
            elif row["pdf_url"] and not _url(row["pdf_url"], allow_http=False):
                reason = "invalid HTTPS URL"
            elif row["source_page"] and not _url(row["source_page"], allow_http=False):
                reason = "invalid source page"
            elif row["zip_member"] and (row["zip_member"].startswith(("/", "\\")) or
                                        ".." in row["zip_member"].replace("\\", "/").split("/")):
                reason = "unsafe ZIP member"
            if reason:
                review.append({**row, "reason": reason, "line": str(line)})
                continue
            safe_url = row["pdf_url"] if row["pdf_url"] else "https://bulk.invalid/report.pdf"
            report = Report.from_row({
                "country": company.country, "exchange": company.exchange,
                "lei": company.lei, "isin": company.isin, "ticker": company.ticker,
                "fiscal_year": f"FY{year_text}", "report_type": "AR", "language": "EN",
                "pdf_url": safe_url, "source_page": row["source_page"], "verified": "true",
            })
            record = {field: str(getattr(report, field)) for field in FIELDS}
            record["verified"] = "true"
            record.update(zip_member=row["zip_member"], classification="ANNUAL_REPORT",
                          report_title=row["report_title"])
            path = report.relative_path
            prior = selected.get(path)
            if path in conflicts or (prior and (prior["pdf_url"], prior["zip_member"]) !=
                                     (record["pdf_url"], record["zip_member"])):
                if prior:
                    selected.pop(path)
                    review.append({**row, "reason": "conflicting annual reports for same SOP target", "line": "prior"})
                conflicts.add(path)
                review.append({**row, "reason": "conflicting annual reports for same SOP target", "line": str(line)})
            elif not prior:
                selected[path] = record
    direct = []
    bulk = []
    for record in selected.values():
        url = record["pdf_url"]
        if record["zip_member"]:
            bulk.append(record)
        if url != "https://bulk.invalid/report.pdf":
            if _hosted(url) and not authorized_hosted:
                review.append({"country": record["country"], "isin": record["isin"],
                               "report_year": record["fiscal_year"],
                               "report_title": record["report_title"], "pdf_url": url,
                               "zip_member": record["zip_member"],
                               "source_page": record["source_page"],
                               "reason": "hosted direct downloads require --authorized-hosted",
                               "line": ""})
            else:
                direct.append(record)
    for path, fields, rows in ((direct_output, FIELDS, direct),
                               (bulk_output, INDEX_FIELDS, bulk),
                               (review_output, REVIEW_FIELDS, review)):
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)
    return {"universe_companies": len(companies), "matched_slots": len(selected),
            "direct": len(direct), "bulk": len(bulk), "review": len(review)}
