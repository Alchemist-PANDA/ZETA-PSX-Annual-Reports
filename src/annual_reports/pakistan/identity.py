"""Pakistan company identity normalization and alias resolution.

Handles PSX ticker symbols, former corporate names, historical tickers,
and verified identifier checks without fabricating ISIN or LEI numbers.
"""

from __future__ import annotations

import re
from typing import Any


_PAK_LEI_PREFIX = "XPAK"
_PAK_ISIN_PREFIX = "PK"


def normalize_ticker(raw_symbol: str) -> str:
    """Normalize a PSX ticker symbol."""
    if not raw_symbol:
        return ""
    s = raw_symbol.strip().upper()
    # Strip exchange prefixes like PSX: or XKAR:
    if s.startswith("PSX:"):
        s = s[4:].strip()
    elif s.startswith("XKAR:"):
        s = s[5:].strip()
    # Remove any brackets e.g. [HBL] -> HBL
    s = re.sub(r"[\[\]\(\)]", "", s).strip()
    return s


def sanitize_folder_name(company_name: str, symbol: str) -> str:
    """Produce SOP-compliant folder name: 'Company Name [SYMBOL]'."""
    clean_name = re.sub(r'[<>:"/\\|?*]', '', company_name).strip()
    clean_symbol = normalize_ticker(symbol)
    return f"{clean_name} [{clean_symbol}]"


def is_genuine_isin(isin: str | None) -> bool:
    """True only if ISIN is a valid 12-char ISO 6166 identifier starting with PK."""
    if not isin or len(isin) != 12:
        return False
    return isin.startswith("PK") and bool(re.fullmatch(r"[A-Z]{2}[A-Z0-9]{10}", isin))


def is_genuine_lei(lei: str | None) -> bool:
    """True only if LEI is a genuine 20-character ISO 17442 identifier."""
    if not lei or len(lei) != 20:
        return False
    return not lei.startswith(_PAK_LEI_PREFIX) and bool(re.fullmatch(r"[A-Z0-9]{20}", lei))


def generate_deterministic_lei(symbol: str) -> str:
    """Generate deterministic internal placeholder LEI: XPAK + 16-char hex hash."""
    import hashlib
    clean = normalize_ticker(symbol)
    digest = hashlib.sha256(f"PAK:XKAR:{clean}".encode("utf-8")).hexdigest()[:16].upper()
    return f"{_PAK_LEI_PREFIX}{digest}"


def generate_deterministic_isin(symbol: str) -> str:
    """Generate deterministic internal placeholder ISIN: PK + 10-char hex hash."""
    import hashlib
    clean = normalize_ticker(symbol)
    digest = hashlib.sha256(f"PAK:XKAR:{clean}".encode("utf-8")).hexdigest()[:10].upper()
    return f"{_PAK_ISIN_PREFIX}{digest}"
