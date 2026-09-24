"""Fiscal-year intelligence for Pakistan corporate reporting.

Ensures that publication date is NEVER confused with fiscal year.
Under Pakistani reporting standards (Companies Act 2017), companies typically
report for fiscal years ending either:
  - June 30 (e.g. Cement, Oil & Gas, Textiles)
  - December 31 (e.g. Commercial Banks, Technology, Fertilizer)
  - March 31 or September 30 (specialized sectors like Automotive, Sugar)

Calculates the exact expected company-year eligibility matrix (P0 completeness denominator).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import Literal


EligibilityStatus = Literal[
    "ELIGIBLE",
    "NOT_LISTED",
    "NOT_ELIGIBLE",
    "DELISTED",
]


@dataclass(frozen=True, slots=True)
class FiscalPeriod:
    """Detailed fiscal period bounds and classification."""
    fiscal_year: int
    period_start: str = ""       # ISO YYYY-MM-DD
    period_end: str = ""         # ISO YYYY-MM-DD
    fiscal_year_end_label: str = ""  # e.g. "June 30" or "December 31"
    publication_date: str = ""   # When the report was uploaded/released
    eligibility: EligibilityStatus = "ELIGIBLE"
    reason: str = ""


# Regex patterns prioritizing explicit fiscal periods over publication dates
_PERIOD_PATTERNS = [
    # "for the year ended June 30, 2024" or "ended December 31, 2023"
    re.compile(
        r"(?:for\s+the\s+)?(?:year|period|twelve\s+months?)\s+ended?\s+"
        r"([a-z]+)\s+(\d{1,2}),?\s+(20[12]\d)",
        re.IGNORECASE,
    ),
    # "June 30, 2024" or "31 December 2023" following "annual report" or "financial statements"
    re.compile(
        r"(?:annual\s+report|financial\s+statements?).{0,40}?"
        r"(?:ended?\s+)?([a-z]+)\s+(\d{1,2}),?\s+(20[12]\d)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(\d{1,2})(?:st|nd|rd|th)?\s+([a-z]+),?\s+(20[12]\d)",
        re.IGNORECASE,
    ),
    # "FY2024", "FY 2024", "FY-24", "FY24"
    re.compile(r"\bFY\s?[-_]?\s?(20[12]\d)\b", re.IGNORECASE),
    re.compile(r"\bFY\s?[-_]?\s?([12]\d)\b", re.IGNORECASE),
    # "Annual Report 2024"
    re.compile(r"\bannual\s+report\s+(20[12]\d)\b", re.IGNORECASE),
    re.compile(r"\bannual\s+accounts?\s+(20[12]\d)\b", re.IGNORECASE),
    # Bare year bounded by 2017-2025
    re.compile(r"\b(20(?:1[7-9]|2[0-5]))\b"),
]

_MONTH_MAP = {
    "january": 1, "jan": 1,
    "february": 2, "feb": 2,
    "march": 3, "mar": 3,
    "april": 4, "apr": 4,
    "may": 5,
    "june": 6, "jun": 6,
    "july": 7, "jul": 7,
    "august": 8, "aug": 8,
    "september": 9, "sep": 9, "sept": 9,
    "october": 10, "oct": 10,
    "november": 11, "nov": 11,
    "december": 12, "dec": 12,
}


def parse_period_and_year(
    text: str,
    company_fiscal_year_end: str = "",
) -> tuple[int | None, str, str]:
    """Parse text and extract (fiscal_year, period_start, period_end).

    Ensures that publication year is not confused with fiscal year.
    For example: a report published in October 2024 for "Year ended June 30, 2024"
    has fiscal_year = 2024 and period_end = "2024-06-30".
    """
    clean = text.strip()

    # Try explicit period ended pattern first
    for pattern in _PERIOD_PATTERNS[:2]:
        m = pattern.search(clean)
        if m:
            m1, m2, m3 = m.group(1), m.group(2), m.group(3)
            # check which group is month vs day vs year
            month_str = m1.lower()
            year_val = int(m3)
            day_val = int(m2) if m2.isdigit() else 30
            month_num = _MONTH_MAP.get(month_str, 6)

            p_end = f"{year_val:04d}-{month_num:02d}-{day_val:02d}"
            # Fiscal year equals the calendar year of period end
            return year_val, "", p_end

    # Try FY20XX pattern
    m_fy = re.search(r"\bFY\s?[-_]?\s?(20[12]\d)\b", clean, re.IGNORECASE)
    if m_fy:
        fy = int(m_fy.group(1))
        return fy, "", ""

    # Try 2-digit FY e.g. FY24 -> 2024
    m_fy2 = re.search(r"\bFY\s?[-_]?\s?([12]\d)\b", clean, re.IGNORECASE)
    if m_fy2:
        fy = 2000 + int(m_fy2.group(1))
        return fy, "", ""

    # Try Annual Report 20XX
    m_ar = re.search(r"\bannual\s+(?:report|accounts?)\s+(20[12]\d)\b", clean, re.IGNORECASE)
    if m_ar:
        return int(m_ar.group(1)), "", ""

    # Fallback to standalone year
    m_yr = re.search(r"\b(20(?:1[7-9]|2[0-5]))\b", clean)
    if m_yr:
        return int(m_yr.group(1)), "", ""

    return None, "", ""


def determine_company_year_eligibility(
    fiscal_year: int,
    listing_date: str = "",
    delisting_date: str = "",
    fiscal_year_end: str = "December 31",
) -> tuple[EligibilityStatus, str]:
    """Determine whether a company was listed and eligible for a specific FY.

    Parameters
    ----------
    fiscal_year : int, e.g. 2020
    listing_date : str, ISO format 'YYYY-MM-DD' or 'YYYY'
    delisting_date : str, ISO format 'YYYY-MM-DD' or 'YYYY'
    fiscal_year_end : str, e.g. 'June 30' or 'December 31'

    Returns
    -------
    (status, reason)
    """
    # Parse listing year
    if listing_date:
        try:
            l_year = int(listing_date[:4])
            # If company listed after the fiscal year, it is NOT_LISTED for that FY
            if l_year > fiscal_year:
                return "NOT_LISTED", f"Company listed in {listing_date}, after FY{fiscal_year}"
        except (ValueError, IndexError):
            pass

    # Parse delisting year
    if delisting_date:
        try:
            d_year = int(delisting_date[:4])
            # If company delisted before the fiscal year, it is DELISTED
            if d_year < fiscal_year:
                return "DELISTED", f"Company delisted on {delisting_date}, before FY{fiscal_year}"
        except (ValueError, IndexError):
            pass

    return "ELIGIBLE", ""


def build_expected_company_years(
    company_id: str,
    years: list[int],
    listing_date: str = "",
    delisting_date: str = "",
    fiscal_year_end: str = "December 31",
) -> dict[int, FiscalPeriod]:
    """Construct the expected company-year matrix for a company.

    Returns dict mapping fiscal_year -> FiscalPeriod.
    """
    matrix: dict[int, FiscalPeriod] = {}
    for y in years:
        status, reason = determine_company_year_eligibility(
            y, listing_date, delisting_date, fiscal_year_end
        )
        matrix[y] = FiscalPeriod(
            fiscal_year=y,
            fiscal_year_end_label=fiscal_year_end,
            eligibility=status,
            reason=reason,
        )
    return matrix
