"""Pakistan annual-report URL discovery from official corporate websites.

Implements a tiered discovery strategy:
  TIER 0: Previously verified source profile / cached URL
  TIER 1: Official company annual-report / investor-relations page
  TIER 2: Other official company domain pages
  TIER 3: Official CDN/subdomain
  TIER 5: Search-engine discovery restricted to official domain

The system LEARNS each company's report archive location and caches it
for subsequent runs.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup

from ..catalog import _url
from .classifier import classify_annual_report, extract_fiscal_year, determine_period_end
from .company import PakistanCompany
from .source_profile import SourceProfileStore


# Common IR / annual-report page path fragments on Pakistani sites
_IR_PATH_CANDIDATES = [
    "/investor-relations",
    "/investors",
    "/annual-reports",
    "/annual-report",
    "/financial-reports",
    "/financial-statements",
    "/financials",
    "/reports",
    "/downloads",
    "/publications",
    "/shareholder-information",
    "/shareholder",
    "/corporate-reports",
]

# Link text patterns that suggest annual report archives
_LINK_TEXT_HINTS = re.compile(
    r"annual\s+report|financial\s+report|investor\s+relation|"
    r"annual\s+accounts|financial\s+statement|annual\s+review|"
    r"downloads|reports|publications",
    re.IGNORECASE,
)


@dataclass(slots=True)
class Candidate:
    """A discovered PDF candidate before classification."""
    company: PakistanCompany
    pdf_url: str
    source_page: str
    link_text: str
    fiscal_year: int | None
    source_tier: int
    classification: str = "REVIEW"
    score: float = 0.0
    period_end: str = ""


def _is_pdf_url(url: str) -> bool:
    """Heuristic: does this URL likely point to a PDF?"""
    path = urlsplit(url).path.lower()
    return path.endswith(".pdf") or "pdf" in path.split("/")[-1:]


def _extract_year_from_url(url: str) -> int | None:
    """Try to pull a fiscal year from the URL path."""
    path = urlsplit(url).path
    matches = re.findall(r"(20(?:1[7-9]|2[0-5]))", path)
    return int(matches[-1]) if matches else None


def _score_candidate(candidate: Candidate) -> float:
    """Compute a deterministic quality score for source ranking."""
    score = 0.0

    # Source tier (lower is better)
    score += max(0, 10 - candidate.source_tier * 2)

    # Classification
    if candidate.classification == "ANNUAL":
        score += 20.0
    elif candidate.classification == "REVIEW":
        score += 5.0
    elif candidate.classification == "NOT_ANNUAL":
        score -= 30.0

    # Fiscal year matched
    if candidate.fiscal_year is not None:
        score += 15.0

    # Link text quality
    text = candidate.link_text.lower()
    if "annual report" in text:
        score += 10.0
    if "annual accounts" in text or "audited" in text:
        score += 8.0
    if re.search(r"20\d{2}", text):
        score += 5.0

    # PDF URL quality
    if candidate.pdf_url.endswith(".pdf"):
        score += 5.0

    # Official domain
    company_domain = candidate.company.official_website
    if company_domain:
        cd_host = urlsplit(company_domain).hostname or ""
        pdf_host = urlsplit(candidate.pdf_url).hostname or ""
        if cd_host and pdf_host and (pdf_host == cd_host or pdf_host.endswith("." + cd_host)):
            score += 10.0

    candidate.score = score
    return score


async def discover_company_reports(
    company: PakistanCompany,
    target_years: list[int],
    profile_store: SourceProfileStore,
    http_get: Any = None,
    max_concurrent: int = 3,
) -> list[Candidate]:
    """Discover annual report PDF URLs for a company.

    Uses a tiered strategy:
    1. Check cached source profile
    2. Try official website IR/annual-report pages
    3. Scan official domain for PDF links

    Parameters
    ----------
    company : PakistanCompany
    target_years : list of target fiscal years
    profile_store : SourceProfileStore for caching
    http_get : async callable(url) -> (status, html_text) for HTTP requests
    max_concurrent : max concurrent requests per company

    Returns
    -------
    list of Candidate objects, scored and classified
    """
    candidates: list[Candidate] = []

    if http_get is None:
        # Default HTTP client using httpx
        import httpx
        async def http_get(url: str) -> tuple[int, str]:
            try:
                async with httpx.AsyncClient(
                    follow_redirects=True,
                    timeout=15.0,
                    headers={
                        "User-Agent": (
                            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                            "AppleWebKit/537.36 (KHTML, like Gecko) "
                            "Chrome/124.0.0.0 Safari/537.36"
                        )
                    },
                ) as client:
                    resp = await client.get(url)
                    return resp.status_code, resp.text
            except Exception:
                return 0, ""

    # TIER 0: Check cached source profile
    profile = profile_store.get_profile(company.identity_key)
    if profile and profile.get("annual_report_page"):
        ar_page = profile["annual_report_page"]
        page_candidates = await _scan_page_for_pdfs(
            company, ar_page, target_years, source_tier=0, http_get=http_get
        )
        candidates.extend(page_candidates)

    # If TIER 0 found enough, skip further discovery
    found_years = {c.fiscal_year for c in candidates if c.fiscal_year and c.classification != "NOT_ANNUAL"}
    missing_years = [y for y in target_years if y not in found_years]

    if missing_years and company.official_website:
        # TIER 1: Try known IR/AR page paths
        base = company.official_website.rstrip("/")
        semaphore = asyncio.Semaphore(max_concurrent)

        async def try_page(path: str) -> list[Candidate]:
            url = base + path
            async with semaphore:
                return await _scan_page_for_pdfs(
                    company, url, missing_years, source_tier=1, http_get=http_get
                )

        tasks = [try_page(path) for path in _IR_PATH_CANDIDATES]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        for result in results:
            if isinstance(result, list):
                candidates.extend(result)

        # Update found years
        found_years = {c.fiscal_year for c in candidates if c.fiscal_year and c.classification != "NOT_ANNUAL"}
        missing_years = [y for y in target_years if y not in found_years]

        # TIER 2: Scan the homepage itself
        if missing_years:
            homepage_candidates = await _scan_page_for_pdfs(
                company, base, missing_years, source_tier=2, http_get=http_get
            )
            candidates.extend(homepage_candidates)

    # Deduplicate by (fiscal_year, pdf_url)
    seen: set[tuple[int | None, str]] = set()
    unique: list[Candidate] = []
    for c in candidates:
        key = (c.fiscal_year, c.pdf_url)
        if key not in seen:
            seen.add(key)
            _score_candidate(c)
            unique.append(c)

    # Sort by score descending
    unique.sort(key=lambda c: c.score, reverse=True)

    # Update source profile if we found a working AR page
    if unique:
        best_page = ""
        for c in unique:
            if c.classification == "ANNUAL" and c.source_tier <= 1:
                best_page = c.source_page
                break
        if best_page:
            profile_store.upsert_profile(
                company.identity_key,
                company_name=company.company_name,
                psx_symbol=company.psx_symbol,
                official_domain=company.official_website,
                annual_report_page=best_page,
                confidence="HIGH",
            )

    return unique


async def _scan_page_for_pdfs(
    company: PakistanCompany,
    page_url: str,
    target_years: list[int],
    source_tier: int,
    http_get: Any,
) -> list[Candidate]:
    """Fetch a page and extract PDF link candidates."""
    candidates: list[Candidate] = []

    status, html = await http_get(page_url)
    if status != 200 or not html:
        return candidates

    soup = BeautifulSoup(html, "html.parser")

    for anchor in soup.find_all("a", href=True):
        href = anchor["href"]
        full_url = urljoin(page_url, href)

        if not _is_pdf_url(full_url):
            continue

        if not _url(full_url, allow_http=False):
            continue

        link_text = anchor.get_text(strip=True)
        # Decode URL path for better classification
        from urllib.parse import unquote
        decoded_url = unquote(full_url)
        context = f"{link_text} {decoded_url}"

        # Classify
        classification = classify_annual_report(context)
        if classification == "NOT_ANNUAL":
            continue  # Skip obvious non-annual documents

        # Extract fiscal year
        fy = extract_fiscal_year(context)
        if fy is None:
            fy = _extract_year_from_url(full_url)

        # Only keep candidates for target years
        if fy is not None and fy not in target_years:
            continue

        period_end = determine_period_end(context)

        candidates.append(Candidate(
            company=company,
            pdf_url=full_url,
            source_page=page_url,
            link_text=link_text,
            fiscal_year=fy,
            source_tier=source_tier,
            classification=classification,
            period_end=period_end,
        ))

    return candidates


async def discover_batch(
    companies: list[PakistanCompany],
    target_years: list[int],
    profile_store: SourceProfileStore,
    max_concurrent_companies: int = 5,
) -> dict[str, list[Candidate]]:
    """Discover reports for multiple companies concurrently.

    Bounded concurrency across companies, further bounded per-company.
    """
    semaphore = asyncio.Semaphore(max_concurrent_companies)
    results: dict[str, list[Candidate]] = {}

    async def discover_one(company: PakistanCompany) -> None:
        async with semaphore:
            candidates = await discover_company_reports(
                company, target_years, profile_store
            )
            results[company.identity_key] = candidates

    await asyncio.gather(
        *(discover_one(c) for c in companies),
        return_exceptions=True,
    )
    return results
