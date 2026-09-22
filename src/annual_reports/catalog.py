"""Strict input parsing and the project SOP's canonical naming contract."""

from __future__ import annotations

import csv
import ipaddress
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit


REPORT_TYPES = frozenset({
    "AR", "SR", "ESG", "IR", "CLIMATE", "TCFD", "CDP", "STR", "BRR",
    "ASSUR", "GHG", "10K", "20F", "ASR", "MODS", "GGR", "OTHER",
})
FIELDS = (
    "country", "exchange", "lei", "isin", "ticker", "fiscal_year",
    "report_type", "language", "pdf_url", "source_page", "verified",
)
_TOKEN = re.compile(r"^[A-Z0-9.-]+$")


def _url(value: str, *, allow_http: bool) -> bool:
    parsed = urlsplit(value)
    if parsed.scheme not in ({"https", "http"} if allow_http else {"https"}):
        return False
    if not parsed.hostname or parsed.username or parsed.password:
        return False
    if parsed.hostname == "localhost" and not allow_http:
        return False
    try:
        if not ipaddress.ip_address(parsed.hostname).is_global and not allow_http:
            return False
    except ValueError:
        pass
    return True


@dataclass(frozen=True, slots=True)
class Report:
    country: str
    exchange: str
    lei: str
    isin: str
    ticker: str
    fiscal_year: str
    report_type: str
    language: str
    pdf_url: str
    source_page: str
    verified: bool

    @classmethod
    def from_row(cls, row: dict[str, str], *, allow_http: bool = False) -> "Report":
        value = {key: (row.get(key) or "").strip() for key in FIELDS}
        if value["country"] not in {"USA", "GBR"}:
            raise ValueError("country must be USA or GBR")
        if not re.fullmatch(r"[A-Z0-9]{4}", value["exchange"]):
            raise ValueError("exchange must be a four-character MIC")
        if not re.fullmatch(r"[A-Z0-9]{20}", value["lei"]):
            raise ValueError("LEI must contain 20 uppercase letters/digits")
        if not re.fullmatch(r"[A-Z]{2}[A-Z0-9]{10}", value["isin"]):
            raise ValueError("ISIN must contain 12 uppercase letters/digits")
        if not _TOKEN.fullmatch(value["ticker"]) or len(value["ticker"]) > 16:
            raise ValueError("ticker must be 1-16 uppercase letters, digits, dots or hyphens")
        if not re.fullmatch(r"FY(?:19|20|21)\d{2}", value["fiscal_year"]):
            raise ValueError("fiscal_year must look like FY2024")
        if value["report_type"] not in REPORT_TYPES:
            raise ValueError("unknown SOP report_type")
        if value["language"] != "EN":
            raise ValueError("only complete English PDFs (EN) are in scope")
        if not _url(value["pdf_url"], allow_http=allow_http):
            raise ValueError("pdf_url must be an absolute HTTPS URL")
        if value["source_page"] and not _url(value["source_page"], allow_http=allow_http):
            raise ValueError("source_page must be an absolute HTTPS URL")
        if value["verified"].lower() not in {"true", "false"}:
            raise ValueError("verified must be true or false")
        return cls(**{**value, "verified": value["verified"].lower() == "true"})

    @property
    def relative_path(self) -> Path:
        company = f"{self.lei}_{self.isin}_{self.ticker}"
        filename = (
            f"{self.lei}_{self.country}_{self.exchange}_{self.ticker}_"
            f"{self.isin}_{self.fiscal_year}_{self.report_type}_{self.language}.pdf"
        )
        return Path(self.country, self.exchange, company, self.fiscal_year, filename)


def load_manifest(path: Path, *, allow_http: bool = False) -> list[Report]:
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        if reader.fieldnames is None or set(reader.fieldnames) != set(FIELDS):
            raise ValueError(f"CSV columns must be exactly: {', '.join(FIELDS)}")
        reports: list[Report] = []
        seen: dict[Path, str] = {}
        for line, row in enumerate(reader, start=2):
            try:
                report = Report.from_row(row, allow_http=allow_http)
            except ValueError as exc:
                raise ValueError(f"line {line}: {exc}") from exc
            if report.relative_path in seen:
                raise ValueError(
                    f"line {line}: duplicate canonical target also listed at {seen[report.relative_path]}"
                )
            seen[report.relative_path] = f"line {line}"
            reports.append(report)
    return reports
