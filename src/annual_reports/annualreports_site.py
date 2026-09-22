"""Opt-in AnnualReports.com company-page discovery.

This resolves explicit PDF links from company pages. It never guesses archive
filenames, bypasses blocks, or starts PDF transfers during discovery.
"""

from __future__ import annotations

import asyncio
import csv
import difflib
import hashlib
import os
import re
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote, urljoin, urlsplit

import httpx
import fitz
from bs4 import BeautifulSoup

from .catalog import FIELDS, Report, _url, load_manifest
from .discovery import Company, read_universe
from .engine import RateGate


BASE = "https://www.annualreports.com"
YEAR = re.compile(r"(?<!\d)(20\d{2})(?!\d)")
ANNUAL_TITLE = re.compile(r"annual report|form 10[- ]?k|report and accounts", re.I)
EXCHANGES = {"XNAS": "NASDAQ", "XNYS": "NYSE", "XASE": "AMEX", "XLON": "LSE"}
MAX_HTML_BYTES = 2 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class Listing:
    name: str
    ticker: str
    exchange: str
    reports: dict[int, str]


def _normalize(value: str) -> str:
    value = re.sub(r"[^a-z0-9]+", " ", value.casefold()).strip()
    return re.sub(r"\b(the|inc|incorporated|corp|corporation|company|co|plc|ltd|limited)\b", "", value).strip()


def search_links(html: str) -> list[tuple[str, str]]:
    soup = BeautifulSoup(html, "html.parser")
    links = []
    for anchor in soup.select(".companyName a[href]"):
        url = urljoin(BASE, anchor["href"])
        if urlsplit(url).hostname == "www.annualreports.com" and urlsplit(url).path.startswith("/Company/"):
            links.append((anchor.get_text(" ", strip=True), url))
    return list(dict.fromkeys(links))


def parse_profile(html: str) -> Listing:
    soup = BeautifulSoup(html, "html.parser")
    heading = soup.select_one("h1")
    ticker = soup.select_one(".ticker_name")
    top = soup.select_one(".top_content_list .right")
    if heading is None or ticker is None or top is None:
        raise ValueError("company page lacks identity fields")
    exchange_text = top.get_text(" ", strip=True).upper()
    exchange = next((name for name in ("NASDAQ", "NYSE", "AMEX", "LSE")
                     if re.search(rf"\b{name}\b", exchange_text)), "")
    reports: dict[int, str] = {}

    def add(title: str, href: str) -> None:
        match = YEAR.search(title)
        if not match or not ANNUAL_TITLE.search(title):
            return
        url = urljoin(BASE, href)
        if not _url(url, allow_http=False):
            return
        year = int(match.group(1))
        # Explicit PDF archive URLs take precedence over Click redirects.
        if year not in reports or urlsplit(url).path.lower().endswith(".pdf"):
            reports[year] = url

    recent = soup.select_one(".most_recent_content_block")
    if recent:
        title = recent.select_one(".bold_txt")
        for anchor in recent.select("a[href]"):
            label = anchor.get_text(" ", strip=True).casefold()
            if "pdf" in label and "html" not in label and title:
                add(title.get_text(" ", strip=True), anchor["href"])
                break
    for item in soup.select(".archived_report_content_block li"):
        heading = item.select_one(".heading")
        if not heading:
            continue
        title = heading.get_text(" ", strip=True)
        anchors = item.select(".btn_archived.download a[href], .btn_archived.view_annual_report a[href]")
        for anchor in anchors:
            href = anchor["href"]
            if href.lower().split("?", 1)[0].endswith(".pdf") or "/Click/" in href:
                add(title, href)
                break
    return Listing(name=soup.select_one("h1").get_text(" ", strip=True),
                   ticker=ticker.get_text(" ", strip=True), exchange=exchange,
                   reports=reports)


class PageClient:
    def __init__(self, cache: Path, *, refresh: bool = False):
        self.cache = cache
        self.refresh = refresh
        self.gate = RateGate(0.5)
        self.semaphore = asyncio.Semaphore(2)
        self.blocked = False
        self.client = httpx.AsyncClient(timeout=15, follow_redirects=False,
                                        limits=httpx.Limits(max_connections=2),
                                        headers={"User-Agent": "AnnualReportHarvester/0.1 (+local research)"})

    async def close(self) -> None:
        await self.client.aclose()

    async def get(self, url: str) -> str:
        parts = urlsplit(url)
        if (parts.hostname != "www.annualreports.com" or parts.scheme != "https" or
                (parts.path != "/Companies" and not parts.path.startswith("/Company/"))):
            raise ValueError("unexpected AnnualReports.com page URL")
        cache_path = self.cache / (hashlib.sha256(url.encode()).hexdigest() + ".html")
        if not self.refresh and cache_path.is_file() and time.time() - cache_path.stat().st_mtime < 7 * 86400:
            return cache_path.read_text(encoding="utf-8")
        if self.blocked:
            raise ValueError("AnnualReports.com returned 403/429; discovery stopped")
        async with self.semaphore:
            for attempt in range(3):
                await self.gate.wait()
                response = await self.client.get(url)
                if response.status_code in {403, 429}:
                    self.blocked = True
                    raise ValueError(f"AnnualReports.com HTTP {response.status_code}; discovery stopped")
                if response.status_code in {500, 502, 503, 504} and attempt < 2:
                    await asyncio.sleep(2 ** attempt)
                    continue
                response.raise_for_status()
                if len(response.content) > MAX_HTML_BYTES or "text/html" not in response.headers.get("content-type", ""):
                    raise ValueError("unexpected company page response")
                text = response.text
                self.cache.mkdir(parents=True, exist_ok=True)
                staged = cache_path.with_name(cache_path.name + f".{uuid.uuid4().hex}.part")
                staged.write_text(text, encoding="utf-8")
                os.replace(staged, cache_path)
                return text
            raise ValueError("company page failed after retries")


async def _resolve(company: Company, client: PageClient) -> tuple[str, Listing] | None:
    candidates: dict[str, str] = {}
    for query in (company.ticker, company.company_name):
        search = f"{BASE}/Companies?search={quote(query, safe='')}"
        matches = search_links(await client.get(search))
        candidates.update((url, name) for name, url in matches[:20])
        best = max((difflib.SequenceMatcher(None, _normalize(company.company_name),
                                           _normalize(name)).ratio() for name in candidates.values()),
                   default=0.0)
        if best >= 0.9:
            break
    urls = sorted(candidates, key=lambda url: difflib.SequenceMatcher(
        None, _normalize(company.company_name), _normalize(candidates[url])).ratio(), reverse=True)
    for url in urls[:8]:
        listing = parse_profile(await client.get(url))
        if re.sub(r"[^A-Z0-9]", "", listing.ticker.upper()) != re.sub(r"[^A-Z0-9]", "", company.ticker.upper()):
            continue
        expected_exchange = EXCHANGES.get(company.exchange)
        if expected_exchange and listing.exchange != expected_exchange:
            continue
        similarity = difflib.SequenceMatcher(None, _normalize(company.company_name),
                                             _normalize(listing.name)).ratio()
        if similarity < 0.55:
            continue
        return url, listing
    return None


async def discover(universe: Path, manifest: Path, unresolved: Path, cache: Path,
                   *, years: tuple[int, int] = (2017, 2025),
                   refresh: bool = False, limit_companies: int | None = None) -> dict:
    companies = read_universe(universe)
    if limit_companies is not None:
        if limit_companies < 1:
            raise ValueError("limit-companies must be positive")
        companies = companies[:limit_companies]
    client = PageClient(cache, refresh=refresh)
    found: list[Report] = []
    missing: list[dict[str, str]] = []
    processed = 0
    try:
        for company in companies:
            processed += 1
            try:
                resolved = await _resolve(company, client)
                reason = "company not matched" if resolved is None else "annual PDF link missing"
            except (httpx.HTTPError, ValueError) as exc:
                resolved = None
                reason = str(exc)
            for year in range(years[0], years[1] + 1):
                url = resolved[1].reports.get(year) if resolved else None
                if not url:
                    missing.append({"company_name": company.company_name,
                                    "ticker": company.ticker, "isin": company.isin,
                                    "report_year": str(year), "reason": reason})
                    continue
                found.append(Report.from_row({
                    "country": company.country, "exchange": company.exchange,
                    "lei": company.lei, "isin": company.isin, "ticker": company.ticker,
                    "fiscal_year": f"FY{year}", "report_type": "AR",
                    "language": "EN", "pdf_url": url, "source_page": resolved[0],
                    "verified": "true",
                }))
            if client.blocked:
                break
    finally:
        await client.close()
    if client.blocked:
        for company in companies[processed:]:
            for year in range(years[0], years[1] + 1):
                missing.append({"company_name": company.company_name,
                                "ticker": company.ticker, "isin": company.isin,
                                "report_year": str(year),
                                "reason": "source blocked; no further requests attempted"})
    for path in (manifest, unresolved):
        path.parent.mkdir(parents=True, exist_ok=True)
    with manifest.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=FIELDS)
        writer.writeheader()
        for report in found:
            writer.writerow({field: getattr(report, field) for field in FIELDS})
    with unresolved.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=("company_name", "ticker", "isin", "report_year", "reason"))
        writer.writeheader()
        writer.writerows(missing)
    return {"companies_requested": len(companies), "reports_discovered": len(found),
            "unresolved_slots": len(missing), "source_blocked": client.blocked,
            "manifest": str(manifest), "unresolved": str(unresolved)}


def audit_reports(universe: Path, manifest: Path, output_root: Path,
                  review_output: Path) -> dict:
    """Check semantic clues without treating OCR or absence of text as success."""
    companies = {(company.country, company.isin): company for company in read_universe(universe)}
    reports = load_manifest(manifest)
    rows = []
    passed = 0
    for report in reports:
        company = companies.get((report.country, report.isin))
        target = output_root / report.relative_path
        reasons = []
        pages = 0
        if company is None:
            reasons.append("company absent from universe")
        if not target.is_file():
            reasons.append("PDF missing")
        else:
            try:
                with fitz.open(target) as pdf:
                    pages = pdf.page_count
                    if not pdf.is_pdf or pdf.needs_pass or pdf.is_repaired or pages < 1:
                        reasons.append("invalid PDF")
                    else:
                        sample = sorted(set(range(min(5, pages))) |
                                        set(range(max(0, pages - 5), pages)))
                        content = _normalize(" ".join(pdf[index].get_text() for index in sample))
                        if not content:
                            reasons.append("no extractable text in sampled pages")
                        if str(int(report.fiscal_year[2:])) not in content:
                            reasons.append("fiscal year not found in sampled pages")
                        if company:
                            tokens = [word for word in _normalize(company.company_name).split()
                                      if len(word) >= 4]
                            if tokens and sum(word in content for word in tokens) < min(2, len(tokens)):
                                reasons.append("company name not found in sampled pages")
            except (fitz.FileDataError, OSError) as exc:
                reasons.append(f"PDF read error: {exc}")
        status = "REVIEW" if reasons else "PASS"
        passed += status == "PASS"
        rows.append({"relative_path": report.relative_path.as_posix(),
                     "company_name": company.company_name if company else "",
                     "fiscal_year": report.fiscal_year,
                     "status": status, "reason": "; ".join(reasons),
                     "pages": pages, "source_page": report.source_page})
    review_output.parent.mkdir(parents=True, exist_ok=True)
    with review_output.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=("relative_path", "company_name",
                                                  "fiscal_year", "status", "reason",
                                                  "pages", "source_page"))
        writer.writeheader()
        writer.writerows(rows)
    return {"checked": len(reports), "passed": passed, "review": len(reports) - passed,
            "output": str(review_output)}
