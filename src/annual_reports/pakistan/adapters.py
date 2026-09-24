"""Deterministic website adapters for Pakistani corporate websites.

Replaces ad-hoc web exploration with reusable, fingerprinted extraction adapters:
  1. GenericAnchorAdapter
  2. StaticYearArchiveAdapter
  3. InvestorRelationsAdapter
  4. WordPressMediaAdapter
  5. SitemapAdapter
  6. DirectPdfAdapter
  7. JavascriptDiscoveryAdapter

Pipeline:
  company source profile -> known adapter?
    YES -> use it
    NO  -> fingerprint website -> select adapter -> discover reports -> save adapter choice.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin, urlsplit, unquote
from bs4 import BeautifulSoup

from .classifier import classify_annual_report
from .fiscal_year import parse_period_and_year


@dataclass(slots=True)
class DiscoveredCandidate:
    """Discovered PDF link candidate produced by an adapter."""
    pdf_url: str
    source_page: str
    link_text: str
    fiscal_year: int | None
    classification: str
    period_end: str
    adapter_name: str
    confidence: float = 1.0


class BaseAdapter:
    """Base class for deterministic website adapters."""
    name: str = "BaseAdapter"

    def extract_candidates(
        self,
        html: str,
        page_url: str,
        target_years: list[int],
    ) -> list[DiscoveredCandidate]:
        raise NotImplementedError


class GenericAnchorAdapter(BaseAdapter):
    """Scans anchor tags with href ending in .pdf and extracts fiscal year and title."""
    name = "GenericAnchorAdapter"

    def extract_candidates(
        self,
        html: str,
        page_url: str,
        target_years: list[int],
    ) -> list[DiscoveredCandidate]:
        candidates: list[DiscoveredCandidate] = []
        soup = BeautifulSoup(html, "html.parser")

        for a in soup.find_all("a", href=True):
            href = a["href"].strip()
            if not href or href.startswith("javascript:") or href.startswith("#"):
                continue
            full_url = urljoin(page_url, href)
            path_lower = urlsplit(full_url).path.lower()
            if not (path_lower.endswith(".pdf") or ".pdf?" in full_url.lower() or "download" in path_lower):
                continue

            text = a.get_text(separator=" ", strip=True)
            context = f"{text} {unquote(full_url)}"

            classification = classify_annual_report(context)
            if classification == "NOT_ANNUAL":
                continue

            fy, _, period_end = parse_period_and_year(context)
            if fy and fy not in target_years:
                continue

            candidates.append(DiscoveredCandidate(
                pdf_url=full_url,
                source_page=page_url,
                link_text=text,
                fiscal_year=fy,
                classification=classification,
                period_end=period_end,
                adapter_name=self.name,
                confidence=0.85,
            ))
        return candidates


class StaticYearArchiveAdapter(BaseAdapter):
    """Extracts from structured year lists/accordions (e.g. 2024, 2023, 2022 headers)."""
    name = "StaticYearArchiveAdapter"

    def extract_candidates(
        self,
        html: str,
        page_url: str,
        target_years: list[int],
    ) -> list[DiscoveredCandidate]:
        candidates: list[DiscoveredCandidate] = []
        soup = BeautifulSoup(html, "html.parser")

        # Find container sections that have year headings (h2, h3, h4, div.year, accordion-item)
        year_headings = soup.find_all(re.compile(r"^(h[1-6]|div|span|button|a)$"), string=re.compile(r"\b20[12]\d\b"))
        for heading in year_headings:
            m = re.search(r"\b(20[12]\d)\b", heading.get_text())
            if not m:
                continue
            heading_year = int(m.group(1))
            if heading_year not in target_years:
                continue

            # Look for closest PDF links inside same container or following sibling
            container = heading.find_parent(["div", "section", "li", "tr"]) or heading
            for a in container.find_all("a", href=True):
                href = a["href"].strip()
                full_url = urljoin(page_url, href)
                if not (urlsplit(full_url).path.lower().endswith(".pdf") or "download" in href.lower()):
                    continue
                text = f"{heading.get_text()} {a.get_text()}"
                classification = classify_annual_report(f"{text} {unquote(full_url)}")
                if classification == "NOT_ANNUAL":
                    continue

                candidates.append(DiscoveredCandidate(
                    pdf_url=full_url,
                    source_page=page_url,
                    link_text=text,
                    fiscal_year=heading_year,
                    classification=classification,
                    period_end="",
                    adapter_name=self.name,
                    confidence=0.95,
                ))

        # Also fallback to generic anchor extraction if no structured headers matched
        if not candidates:
            return GenericAnchorAdapter().extract_candidates(html, page_url, target_years)
        return candidates


class InvestorRelationsAdapter(BaseAdapter):
    """Navigates specialized financial statement tables, tab panes, and download grids."""
    name = "InvestorRelationsAdapter"

    def extract_candidates(
        self,
        html: str,
        page_url: str,
        target_years: list[int],
    ) -> list[DiscoveredCandidate]:
        candidates: list[DiscoveredCandidate] = []
        soup = BeautifulSoup(html, "html.parser")

        # Check for table rows (common in Pakistani IR pages: Year | Report Type | Download)
        for row in soup.find_all("tr"):
            row_text = row.get_text(separator=" ", strip=True)
            if not ("annual" in row_text.lower() or re.search(r"20[12]\d", row_text)):
                continue

            classification = classify_annual_report(row_text)
            if classification == "NOT_ANNUAL":
                continue

            fy, _, p_end = parse_period_and_year(row_text)
            if fy and fy not in target_years:
                continue

            for a in row.find_all("a", href=True):
                full_url = urljoin(page_url, a["href"].strip())
                if urlsplit(full_url).path.lower().endswith(".pdf") or "download" in a["href"].lower():
                    candidates.append(DiscoveredCandidate(
                        pdf_url=full_url,
                        source_page=page_url,
                        link_text=row_text[:120],
                        fiscal_year=fy,
                        classification=classification,
                        period_end=p_end,
                        adapter_name=self.name,
                        confidence=0.92,
                    ))

        if not candidates:
            return StaticYearArchiveAdapter().extract_candidates(html, page_url, target_years)
        return candidates


class WordPressMediaAdapter(BaseAdapter):
    """Specialized for WordPress CMS sites (uploads/YYYY/MM/*.pdf)."""
    name = "WordPressMediaAdapter"

    def extract_candidates(
        self,
        html: str,
        page_url: str,
        target_years: list[int],
    ) -> list[DiscoveredCandidate]:
        candidates: list[DiscoveredCandidate] = []
        soup = BeautifulSoup(html, "html.parser")

        for a in soup.find_all("a", href=True):
            href = a["href"].strip()
            full_url = urljoin(page_url, href)
            path = urlsplit(full_url).path

            if not path.lower().endswith(".pdf"):
                continue

            text = a.get_text(separator=" ", strip=True)
            context = f"{text} {unquote(full_url)}"

            classification = classify_annual_report(context)
            if classification == "NOT_ANNUAL":
                continue

            fy, _, p_end = parse_period_and_year(context)
            # If not in text, extract from upload path or filename e.g. /wp-content/uploads/2024/10/Annual-Report-2024.pdf
            if fy is None:
                m = re.search(r"/uploads/(20[12]\d)/", path)
                if m:
                    fy = int(m.group(1))

            if fy and fy not in target_years:
                continue

            candidates.append(DiscoveredCandidate(
                pdf_url=full_url,
                source_page=page_url,
                link_text=text,
                fiscal_year=fy,
                classification=classification,
                period_end=p_end,
                adapter_name=self.name,
                confidence=0.90,
            ))
        return candidates


class SitemapAdapter(BaseAdapter):
    """Parses sitemap XML files directly for annual report PDF URLs."""
    name = "SitemapAdapter"

    def extract_candidates(
        self,
        html: str,
        page_url: str,
        target_years: list[int],
    ) -> list[DiscoveredCandidate]:
        candidates: list[DiscoveredCandidate] = []
        # Find all <loc> tags in XML
        locs = re.findall(r"<loc>(.*?)</loc>", html, re.IGNORECASE)
        for loc in locs:
            clean_url = loc.strip()
            if not clean_url.lower().endswith(".pdf"):
                continue
            context = unquote(clean_url)
            classification = classify_annual_report(context)
            if classification == "NOT_ANNUAL":
                continue

            fy, _, p_end = parse_period_and_year(context)
            if fy and fy not in target_years:
                continue

            candidates.append(DiscoveredCandidate(
                pdf_url=clean_url,
                source_page=page_url,
                link_text="sitemap.xml",
                fiscal_year=fy,
                classification=classification,
                period_end=p_end,
                adapter_name=self.name,
                confidence=0.88,
            ))
        return candidates


class DirectPdfAdapter(BaseAdapter):
    """Direct PDF extraction for pages where report links are embedded in data attributes."""
    name = "DirectPdfAdapter"

    def extract_candidates(
        self,
        html: str,
        page_url: str,
        target_years: list[int],
    ) -> list[DiscoveredCandidate]:
        candidates: list[DiscoveredCandidate] = []
        # Regex search for all http(s)://...pdf strings inside quotes
        matches = set(re.findall(r'["\'](https?://[^"\']+\.pdf[^"\']*)["\']', html, re.IGNORECASE))
        for url in matches:
            full_url = urljoin(page_url, url)
            context = unquote(full_url)
            classification = classify_annual_report(context)
            if classification == "NOT_ANNUAL":
                continue
            fy, _, p_end = parse_period_and_year(context)
            if fy and fy not in target_years:
                continue

            candidates.append(DiscoveredCandidate(
                pdf_url=full_url,
                source_page=page_url,
                link_text="Direct Embed/Data",
                fiscal_year=fy,
                classification=classification,
                period_end=p_end,
                adapter_name=self.name,
                confidence=0.80,
            ))
        return candidates


class JavascriptDiscoveryAdapter(BaseAdapter):
    """Fallback adapter for dynamic websites with client-rendered elements."""
    name = "JavascriptDiscoveryAdapter"

    def extract_candidates(
        self,
        html: str,
        page_url: str,
        target_years: list[int],
    ) -> list[DiscoveredCandidate]:
        # Combines direct PDF regex matching and general anchor scan
        direct = DirectPdfAdapter().extract_candidates(html, page_url, target_years)
        anchors = GenericAnchorAdapter().extract_candidates(html, page_url, target_years)
        seen = {c.pdf_url for c in direct}
        for a in anchors:
            if a.pdf_url not in seen:
                direct.append(a)
                seen.add(a.pdf_url)
        return direct


ADAPTER_REGISTRY: dict[str, type[BaseAdapter]] = {
    "GenericAnchorAdapter": GenericAnchorAdapter,
    "StaticYearArchiveAdapter": StaticYearArchiveAdapter,
    "InvestorRelationsAdapter": InvestorRelationsAdapter,
    "WordPressMediaAdapter": WordPressMediaAdapter,
    "SitemapAdapter": SitemapAdapter,
    "DirectPdfAdapter": DirectPdfAdapter,
    "JavascriptDiscoveryAdapter": JavascriptDiscoveryAdapter,
}


def fingerprint_website(html: str, url: str) -> str:
    """Analyze page HTML and URL to select the best adapter."""
    url_lower = url.lower()
    html_lower = html.lower()

    if "sitemap" in url_lower and (".xml" in url_lower or "<urlset" in html_lower):
        return "SitemapAdapter"

    if "wp-content" in html_lower or "wp-includes" in html_lower:
        return "WordPressMediaAdapter"

    if "<table" in html_lower and ("financial statement" in html_lower or "annual report" in html_lower):
        return "InvestorRelationsAdapter"

    if re.search(r"accordion|tab-content|year-list|archive", html_lower):
        return "StaticYearArchiveAdapter"

    return "GenericAnchorAdapter"


def get_adapter(adapter_name: str) -> BaseAdapter:
    """Instantiate adapter by name, defaulting to GenericAnchorAdapter."""
    cls = ADAPTER_REGISTRY.get(adapter_name, GenericAnchorAdapter)
    return cls()
