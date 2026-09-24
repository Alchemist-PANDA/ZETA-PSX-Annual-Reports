"""Pakistan annual-report classifier.

Strong positive/negative evidence lists for distinguishing annual reports
from quarterly, half-year, AGM notices, and other document types commonly
found on Pakistani corporate websites.
"""

from __future__ import annotations

import re


# ── Strong positive evidence (annual report / annual accounts) ────────────
ANNUAL_POSITIVE = [
    r"annual\s+report",
    r"annual\s+accounts",
    r"annual\s+financial\s+statements?",
    r"audited\s+financial\s+statements?",
    r"report\s+and\s+accounts",
    r"annual\s+report\s+\d{4}",
    r"year\s+ended",
    r"twelve\s+months?\s+ended",
    r"for\s+the\s+year",
    r"yearly\s+report",
]

# ── Strong negative evidence (not an annual report) ───────────────────────
ANNUAL_NEGATIVE = [
    r"quarterly\s+report",
    r"quarterly\s+financial",
    r"first\s+quarter",
    r"second\s+quarter",
    r"third\s+quarter",
    r"half[\s-]?year",
    r"half[\s-]?yearly",
    r"nine\s+months?",
    r"six\s+months?",
    r"three\s+months?",
    r"interim\s+report",
    r"interim\s+financial",
    r"financial\s+results",
    r"board\s+meeting",
    r"agm\s+notice",
    r"notice\s+of\s+meeting",
    r"proxy\s+form",
    r"corporate\s+briefing",
    r"credit\s+rating",
    r"dividend\s+announcement",
    r"analyst\s+briefing",
    r"investor\s+presentation",
    r"factsheet",
    r"accounts?\s+condensed",
    r"un-?audited",
    # URL/filename pattern markers common in Pakistani sites
    r"\bQ[1-4]\b",                 # Q1, Q2, Q3, Q4
    r"\b(?:1st|2nd|3rd)\s+quarter",
    r"\bHY\s+(?:report|20)",       # HY Report, HY 2024
    r"\bH[12]\s+20\d{2}",          # H1 2024, H2 2024
    r"quarter[_\s]+report",
    r"investors?\s*brief",
    r"(?:gender|pay)\s+gap",
]

_POS_RE = [re.compile(p, re.IGNORECASE) for p in ANNUAL_POSITIVE]
_NEG_RE = [re.compile(p, re.IGNORECASE) for p in ANNUAL_NEGATIVE]


def classify_annual_report(text: str) -> str:
    """Classify text as ANNUAL, NOT_ANNUAL, or REVIEW.

    Parameters
    ----------
    text : str
        The combined title, link text, URL path, and first-page text
        of a candidate document.

    Returns
    -------
    str
        ``ANNUAL``   — strong positive evidence, no negative evidence
        ``NOT_ANNUAL`` — strong negative evidence found
        ``REVIEW``   — ambiguous; needs human review
    """
    has_positive = any(p.search(text) for p in _POS_RE)
    has_negative = any(p.search(text) for p in _NEG_RE)

    if has_negative:
        return "NOT_ANNUAL"
    if has_positive:
        return "ANNUAL"
    return "REVIEW"


# ── Fiscal year extraction ────────────────────────────────────────────────

_FY_PATTERNS = [
    # "year ended June 30, 2025" or "period ended December 31, 2024"
    re.compile(
        r"(?:year|period|twelve\s+months?)\s+ended?\s+"
        r"(?:January|February|March|April|May|June|July|August|September|"
        r"October|November|December)\s+\d{1,2},?\s+(20[12]\d)",
        re.IGNORECASE,
    ),
    # "FY2024" or "FY 2024"
    re.compile(r"FY\s?(20[12]\d)", re.IGNORECASE),
    # "Annual Report 2024"
    re.compile(r"annual\s+report\s+(20[12]\d)", re.IGNORECASE),
    # "Annual Accounts 2024"
    re.compile(r"annual\s+accounts?\s+(20[12]\d)", re.IGNORECASE),
    # Bare year in title like "2024" (weakest, only if standalone)
    re.compile(r"\b(20(?:1[7-9]|2[0-5]))\b"),
]


def extract_fiscal_year(text: str) -> int | None:
    """Extract the fiscal year from document text/title.

    Tries patterns from most specific to least.  Returns ``None``
    if no year can be determined (the report should then be marked REVIEW).
    """
    for pattern in _FY_PATTERNS:
        match = pattern.search(text)
        if match:
            return int(match.group(1))
    return None


def determine_period_end(text: str) -> str:
    """Extract period-end date string like 'June 30, 2025'."""
    match = re.search(
        r"(?:year|period)\s+ended?\s+"
        r"((?:January|February|March|April|May|June|July|August|September|"
        r"October|November|December)\s+\d{1,2},?\s+20[12]\d)",
        text,
        re.IGNORECASE,
    )
    return match.group(1).strip() if match else ""
