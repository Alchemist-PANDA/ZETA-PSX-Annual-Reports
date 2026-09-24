"""Targeted Pass 2 gap recovery for unresolved company-year cells.

Preserves ZETA's two-pass architecture:
  Pass 1: Fast deterministic archive discovery -> high-speed bulk transfer.
  Pass 2: Targeted gap recovery focusing strictly on unresolved cells
          (MISSING, REVIEW, FAILED), avoiding expensive search during the
          primary transfer queue.
"""

from __future__ import annotations

import asyncio
import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urljoin

from .company import PakistanCompany
from .config import PakistanConfig
from .discovery import Candidate, discover_company_reports, _score_candidate
from .report import PakistanReport
from .source_profiles import SourceProfileStore
from .fiscal_year import determine_company_year_eligibility


@dataclass(slots=True)
class GapCell:
    """An unresolved company-year coordinate requiring targeted recovery."""
    company: PakistanCompany
    fiscal_year: int
    current_status: str  # MISSING, REVIEW, FAILED
    reason: str = ""


class GapRecoveryManager:
    """Manages Pass 2 targeted gap research and gap manifest execution."""

    def __init__(self, config: PakistanConfig, profile_store: SourceProfileStore):
        self.config = config
        self.profile_store = profile_store

    def identify_gaps(
        self,
        companies: list[PakistanCompany],
        years: list[int],
    ) -> list[GapCell]:
        """Examine the gap matrix and return only cells requiring recovery."""
        cid_map = {c.identity_key: c for c in companies}
        gap_matrix = self.profile_store.gap_matrix(list(cid_map.keys()), years)
        unresolved: list[GapCell] = []

        for cid, row in gap_matrix.items():
            company = cid_map.get(cid)
            if not company:
                continue

            for y in years:
                st = row.get(y, "MISSING")
                # Do NOT attempt recovery for non-eligible or already verified years
                if st in ("VERIFIED", "PUBLISHED", "NOT_LISTED", "NOT_ELIGIBLE", "DELISTED"):
                    continue

                unresolved.append(GapCell(
                    company=company,
                    fiscal_year=y,
                    current_status=st,
                ))

        return unresolved

    async def recover_gaps(
        self,
        gaps: list[GapCell],
        max_concurrent: int = 3,
    ) -> list[PakistanReport]:
        """Perform targeted recovery on unresolved cells using historical aliases and sitemaps."""
        if not gaps:
            return []

        # Group gaps by company
        by_company: dict[str, list[int]] = {}
        comp_lookup: dict[str, PakistanCompany] = {}
        for g in gaps:
            by_company.setdefault(g.company.identity_key, []).append(g.fiscal_year)
            comp_lookup[g.company.identity_key] = g.company

        recovered_reports: list[PakistanReport] = []
        semaphore = asyncio.Semaphore(max_concurrent)

        async def recover_company(company: PakistanCompany, missing_years: list[int]) -> None:
            async with semaphore:
                # 1. Try sitemap discovery if domain available
                candidates = await self._discover_via_sitemap_or_aliases(company, missing_years)
                for year in missing_years:
                    matching = [c for c in candidates if c.fiscal_year == year and c.classification != "NOT_ANNUAL"]
                    if matching:
                        best = max(matching, key=lambda c: c.score)
                        rep = PakistanReport(
                            company=company,
                            fiscal_year=year,
                            pdf_url=best.pdf_url,
                            source_page=best.source_page,
                            source_tier=best.source_tier,
                            candidate_score=best.score,
                            period_end=best.period_end,
                            notes="Pass 2 gap recovery",
                        )
                        recovered_reports.append(rep)
                        self.profile_store.upsert_report(
                            company.identity_key, year, best.pdf_url,
                            source_page=best.source_page,
                            source_tier=best.source_tier,
                            candidate_score=best.score,
                            status="DISCOVERED",
                        )

        tasks = [recover_company(comp_lookup[cid], yrs) for cid, yrs in by_company.items()]
        await asyncio.gather(*tasks, return_exceptions=True)

        return recovered_reports

    async def _discover_via_sitemap_or_aliases(
        self,
        company: PakistanCompany,
        target_years: list[int],
    ) -> list[Candidate]:
        """Attempt secondary recovery using sitemap.xml and historical aliases."""
        candidates: list[Candidate] = []
        if not company.official_website:
            return candidates

        import httpx
        from .adapters import SitemapAdapter

        base = company.official_website.rstrip("/")
        sitemap_urls = [
            f"{base}/sitemap.xml",
            f"{base}/sitemap_index.xml",
            f"{base}/wp-sitemap.xml",
        ]

        async with httpx.AsyncClient(
            follow_redirects=True,
            timeout=15.0,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
        ) as client:
            for s_url in sitemap_urls:
                try:
                    resp = await client.get(s_url)
                    if resp.status_code == 200 and resp.text:
                        s_candidates = SitemapAdapter().extract_candidates(resp.text, s_url, target_years)
                        for sc in s_candidates:
                            c = Candidate(
                                company=company,
                                pdf_url=sc.pdf_url,
                                source_page=sc.source_page,
                                link_text=sc.link_text,
                                fiscal_year=sc.fiscal_year,
                                source_tier=3,
                                classification=sc.classification,
                                period_end=sc.period_end,
                            )
                            _score_candidate(c)
                            candidates.append(c)
                        if candidates:
                            break
                except Exception:
                    pass

        return candidates

    def export_gap_manifest(
        self,
        recovered_reports: list[PakistanReport],
        path: Path,
    ) -> None:
        """Export gap_manifest.csv for Pass 2 execution."""
        if not recovered_reports:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", newline="", encoding="utf-8") as f:
            fieldnames = list(recovered_reports[0].to_manifest_row().keys())
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for r in recovered_reports:
                writer.writerow(r.to_manifest_row())
