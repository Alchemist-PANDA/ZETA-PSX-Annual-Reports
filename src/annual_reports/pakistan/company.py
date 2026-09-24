"""Pakistan-specific company identity model.

Extends the upstream ``discovery.Company`` with PSX-specific fields while
preserving the existing US/UK validation untouched.  Pakistani companies may
lack globally-resolved LEI/ISIN; this module provides a deterministic
fallback identity that is *never* confused with a real LEI/ISIN.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


# Deterministic placeholder patterns — visually distinct from real identifiers
_PAK_LEI_PREFIX = "XPAK"  # 4 chars, then 16 hex chars = 20 total
_PAK_ISIN_PREFIX = "PK"   # already correct for Pakistan ISINs


@dataclass(frozen=True, slots=True)
class PakistanCompany:
    """Identity record for a PSX-listed company."""
    company_name: str
    psx_symbol: str
    official_website: str = ""
    isin: str = ""
    lei: str = ""
    secp_registration: str = ""
    listing_date: str = ""
    delisting_date: str = ""
    fiscal_year_end: str = ""          # e.g. "June 30", "December 31"
    historical_names: list[str] = field(default_factory=list)
    historical_symbols: list[str] = field(default_factory=list)
    aliases: list[str] = field(default_factory=list)
    sector: str = ""
    source_confidence: str = "HIGH"    # HIGH, MEDIUM, LOW
    identity_status: str = "RESOLVED"  # RESOLVED, UNRESOLVED, REVIEW

    @property
    def country(self) -> str:
        return "PAK"

    @property
    def exchange(self) -> str:
        return "XKAR"

    @property
    def has_genuine_lei(self) -> bool:
        """True only if LEI appears to be a real 20-char ISO 17442 identifier."""
        if not self.lei or len(self.lei) != 20:
            return False
        return not self.lei.startswith(_PAK_LEI_PREFIX)

    @property
    def has_genuine_isin(self) -> bool:
        """True only if ISIN appears to be a real 12-char ISO 6166 identifier."""
        if not self.isin or len(self.isin) != 12:
            return False
        return self.isin.startswith("PK") and re.fullmatch(r"[A-Z]{2}[A-Z0-9]{10}", self.isin) is not None

    @property
    def effective_lei(self) -> str:
        """Return genuine LEI or deterministic fallback placeholder."""
        if self.has_genuine_lei:
            return self.lei
        # Deterministic fallback: XPAK + SHA-based hex from symbol (16 chars)
        import hashlib
        digest = hashlib.sha256(f"PAK:XKAR:{self.psx_symbol}".encode()).hexdigest()[:16].upper()
        return f"{_PAK_LEI_PREFIX}{digest}"

    @property
    def effective_isin(self) -> str:
        """Return genuine ISIN or deterministic fallback placeholder."""
        if self.has_genuine_isin:
            return self.isin
        # Deterministic fallback: PK + SHA-based hex (10 chars)
        import hashlib
        digest = hashlib.sha256(f"PAK:XKAR:{self.psx_symbol}".encode()).hexdigest()[:10].upper()
        return f"PK{digest}"

    @property
    def identity_key(self) -> str:
        """Unique identity string for deduplication and DB keying."""
        return f"PAK|XKAR|{self.psx_symbol}"

    @property
    def folder_name(self) -> str:
        """Clean deterministic folder name: ``Company Name [SYMBOL]``."""
        name = self.company_name.strip()
        # Remove characters illegal in Windows filenames
        clean = re.sub(r'[<>:"/\\|?*]', '', name).strip()
        return f"{clean} [{self.psx_symbol}]"

    def fiscal_year_range(self, start: int = 2017, end: int = 2025) -> list[int]:
        """Return eligible fiscal years based on listing/delisting dates."""
        years = list(range(start, end + 1))
        # If listing_date is available, filter early years
        if self.listing_date:
            try:
                listing_year = int(self.listing_date[:4])
                years = [y for y in years if y >= listing_year]
            except (ValueError, IndexError):
                pass
        if self.delisting_date:
            try:
                delist_year = int(self.delisting_date[:4])
                years = [y for y in years if y <= delist_year]
            except (ValueError, IndexError):
                pass
        return years

    def matches_query(self, query: str) -> bool:
        """Check if a user query string matches this company."""
        q = query.strip().upper()
        if q == self.psx_symbol.upper():
            return True
        if q == self.company_name.upper():
            return True

        # Strip common corporate suffixes for robust matching (e.g. 'Lucky Cement' -> 'Lucky Cement Limited')
        def clean_name(n: str) -> str:
            return re.sub(r"\b(LIMITED|LTD|COMPANY|CO)\b", "", n, flags=re.IGNORECASE).strip().upper()

        q_clean = clean_name(q)
        if q_clean and q_clean == clean_name(self.company_name):
            return True

        for alias in self.aliases + self.historical_names + self.historical_symbols:
            if q == alias.upper() or (q_clean and q_clean == clean_name(alias)):
                return True
        return False

    def to_dict(self) -> dict[str, Any]:
        return {
            "country": self.country,
            "exchange": self.exchange,
            "company_name": self.company_name,
            "psx_symbol": self.psx_symbol,
            "official_website": self.official_website,
            "isin": self.isin,
            "lei": self.lei,
            "effective_lei": self.effective_lei,
            "effective_isin": self.effective_isin,
            "secp_registration": self.secp_registration,
            "listing_date": self.listing_date,
            "delisting_date": self.delisting_date,
            "fiscal_year_end": self.fiscal_year_end,
            "historical_names": self.historical_names,
            "historical_symbols": self.historical_symbols,
            "aliases": self.aliases,
            "sector": self.sector,
            "source_confidence": self.source_confidence,
            "identity_status": self.identity_status,
            "has_genuine_lei": self.has_genuine_lei,
            "has_genuine_isin": self.has_genuine_isin,
        }
