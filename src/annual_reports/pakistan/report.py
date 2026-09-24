"""Pakistan report model — bridges Pakistan identity to the ZETA engine.

Creates ``Report``-compatible objects that the existing download engine,
state store, and verification pipeline can consume without modification.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .company import PakistanCompany


@dataclass(frozen=True, slots=True)
class PakistanReport:
    """A resolved Pakistan annual-report download candidate."""
    company: PakistanCompany
    fiscal_year: int
    pdf_url: str
    source_page: str = ""
    report_type: str = "AR"
    language: str = "EN"
    verified: bool = True
    source_tier: int = 1
    candidate_score: float = 0.0
    period_end: str = ""
    notes: str = ""

    @property
    def fy_label(self) -> str:
        return f"FY{self.fiscal_year}"

    @property
    def relative_path(self) -> Path:
        """SOP-compliant relative path for Pakistan reports.

        Uses the Pakistan-specific folder layout:
        ``Company Name [SYMBOL]/FY<year>/<filename>.pdf``

        The filename includes the effective LEI/ISIN for structural
        compatibility with the ZETA engine while clearly indicating
        when identifiers are placeholders.
        """
        lei = self.company.effective_lei
        isin = self.company.effective_isin
        ticker = self.company.psx_symbol
        folder = self.company.folder_name
        filename = (
            f"{lei}_PAK_XKAR_{ticker}_{isin}_{self.fy_label}"
            f"_{self.report_type}_{self.language}.pdf"
        )
        return Path(folder, self.fy_label, filename)

    def to_engine_row(self) -> dict[str, str]:
        """Convert to a dict compatible with ``catalog.Report.from_row()``
        via the Pakistan-aware adapter (bypasses US/UK country check)."""
        return {
            "country": "PAK",
            "exchange": "XKAR",
            "lei": self.company.effective_lei,
            "isin": self.company.effective_isin,
            "ticker": self.company.psx_symbol,
            "fiscal_year": self.fy_label,
            "report_type": self.report_type,
            "language": self.language,
            "pdf_url": self.pdf_url,
            "source_page": self.source_page,
            "verified": "true" if self.verified else "false",
        }

    def to_manifest_row(self) -> dict[str, str]:
        """Full manifest row with Pakistan-specific metadata."""
        return {
            "company_name": self.company.company_name,
            "ticker": self.company.psx_symbol,
            "country": "PAK",
            "MIC": "XKAR",
            "lei": self.company.lei,
            "isin": self.company.isin,
            "effective_lei": self.company.effective_lei,
            "effective_isin": self.company.effective_isin,
            "secp_registration": self.company.secp_registration,
            "fiscal_year": self.fy_label,
            "period_end": self.period_end,
            "report_type": self.report_type,
            "language": self.language,
            "pdf_url": self.pdf_url,
            "source_page": self.source_page,
            "official_domain": self.company.official_website,
            "source_tier": str(self.source_tier),
            "verified": str(self.verified).lower(),
            "candidate_score": str(self.candidate_score),
            "identity_status": self.company.identity_status,
            "notes": self.notes,
        }
