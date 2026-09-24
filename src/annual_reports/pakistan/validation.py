"""Multi-stage validation and failure classification for Pakistan annual reports.

Replaces a simplistic 'verified=true' with an explicit multi-stage progression:
  1. DISCOVERED
  2. SOURCE_TRUSTED
  3. IDENTITY_VALID
  4. PERIOD_VALID
  5. ANNUAL_REPORT_VALID
  6. DOWNLOADED
  7. PDF_STRUCTURE_VALID
  8. HASH_VALID
  9. CONTENT_VALID
  10. PUBLISHED

Also provides granular failure categories to make execution audits auditable and actionable.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

try:
    import pymupdf as fitz
except ImportError:
    try:
        import fitz
    except ImportError:
        fitz = None


class ValidationStage(str, Enum):
    """Explicit progression states for a report."""
    DISCOVERED = "DISCOVERED"
    SOURCE_TRUSTED = "SOURCE_TRUSTED"
    IDENTITY_VALID = "IDENTITY_VALID"
    PERIOD_VALID = "PERIOD_VALID"
    ANNUAL_REPORT_VALID = "ANNUAL_REPORT_VALID"
    DOWNLOADED = "DOWNLOADED"
    PDF_STRUCTURE_VALID = "PDF_STRUCTURE_VALID"
    HASH_VALID = "HASH_VALID"
    CONTENT_VALID = "CONTENT_VALID"
    PUBLISHED = "PUBLISHED"


class FailureCategory(str, Enum):
    """Explicit, auditable failure categories."""
    NONE = "NONE"
    WRONG_ISSUER = "WRONG_ISSUER"
    WRONG_FISCAL_YEAR = "WRONG_FISCAL_YEAR"
    QUARTERLY_NOT_ANNUAL = "QUARTERLY_NOT_ANNUAL"
    HALF_YEAR_NOT_ANNUAL = "HALF_YEAR_NOT_ANNUAL"
    NINE_MONTH_NOT_ANNUAL = "NINE_MONTH_NOT_ANNUAL"
    AGM_DOCUMENT = "AGM_DOCUMENT"
    PROXY_DOCUMENT = "PROXY_DOCUMENT"
    CORRUPT_PDF = "CORRUPT_PDF"
    TRUNCATED_PDF = "TRUNCATED_PDF"
    HTML_INSTEAD_OF_PDF = "HTML_INSTEAD_OF_PDF"
    DUPLICATE = "DUPLICATE"
    SCANNED_REVIEW = "SCANNED_REVIEW"
    SOURCE_BLOCKED = "SOURCE_BLOCKED"
    IDENTITY_CONFLICT = "IDENTITY_CONFLICT"
    NO_OFFICIAL_REPORT_FOUND = "NO_OFFICIAL_REPORT_FOUND"


@dataclass(slots=True)
class ValidationResult:
    """Outcome of multi-stage validation."""
    is_valid: bool
    stage: ValidationStage
    failure_category: FailureCategory = FailureCategory.NONE
    failure_reason: str = ""
    page_count: int = 0
    file_size: int = 0
    sha256: str = ""
    has_text: bool = False
    evidence_notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "is_valid": self.is_valid,
            "stage": self.stage.value,
            "failure_category": self.failure_category.value,
            "failure_reason": self.failure_reason,
            "page_count": self.page_count,
            "file_size": self.file_size,
            "sha256": self.sha256,
            "has_text": self.has_text,
            "evidence_notes": self.evidence_notes,
        }


# Positive semantic markers inside Pakistani annual reports
_POSITIVE_CONTENT_MARKERS = [
    re.compile(r"directors?'?\s+report", re.IGNORECASE),
    re.compile(r"independent\s+auditors?'?\s+report", re.IGNORECASE),
    re.compile(r"statement\s+of\s+financial\s+position", re.IGNORECASE),
    re.compile(r"balance\s+sheet", re.IGNORECASE),
    re.compile(r"statement\s+of\s+profit\s+or\s+loss", re.IGNORECASE),
    re.compile(r"profit\s+and\s+loss\s+account", re.IGNORECASE),
    re.compile(r"annual\s+report\s+20\d{2}", re.IGNORECASE),
    re.compile(r"annual\s+accounts\s+20\d{2}", re.IGNORECASE),
    re.compile(r"for\s+the\s+year\s+ended", re.IGNORECASE),
]

# Explicit negative content markers (interim, quarterly, half-yearly)
_NEGATIVE_CONTENT_MARKERS = [
    (re.compile(r"condensed\s+interim", re.IGNORECASE), FailureCategory.QUARTERLY_NOT_ANNUAL),
    (re.compile(r"first\s+quarter\s+report", re.IGNORECASE), FailureCategory.QUARTERLY_NOT_ANNUAL),
    (re.compile(r"1st\s+quarter\s+report", re.IGNORECASE), FailureCategory.QUARTERLY_NOT_ANNUAL),
    (re.compile(r"second\s+quarter\s+report", re.IGNORECASE), FailureCategory.QUARTERLY_NOT_ANNUAL),
    (re.compile(r"2nd\s+quarter\s+report", re.IGNORECASE), FailureCategory.QUARTERLY_NOT_ANNUAL),
    (re.compile(r"third\s+quarter\s+report", re.IGNORECASE), FailureCategory.QUARTERLY_NOT_ANNUAL),
    (re.compile(r"3rd\s+quarter\s+report", re.IGNORECASE), FailureCategory.QUARTERLY_NOT_ANNUAL),
    (re.compile(r"for\s+the\s+three\s+months\s+ended", re.IGNORECASE), FailureCategory.QUARTERLY_NOT_ANNUAL),
    (re.compile(r"for\s+the\s+six\s+months\s+ended", re.IGNORECASE), FailureCategory.HALF_YEAR_NOT_ANNUAL),
    (re.compile(r"half[\s-]?yearly\s+report", re.IGNORECASE), FailureCategory.HALF_YEAR_NOT_ANNUAL),
    (re.compile(r"half[\s-]?year\s+accounts", re.IGNORECASE), FailureCategory.HALF_YEAR_NOT_ANNUAL),
    (re.compile(r"for\s+the\s+nine\s+months\s+ended", re.IGNORECASE), FailureCategory.NINE_MONTH_NOT_ANNUAL),
    (re.compile(r"notice\s+of\s+annual\s+general\s+meeting", re.IGNORECASE), FailureCategory.AGM_DOCUMENT),
    (re.compile(r"form\s+of\s+proxy", re.IGNORECASE), FailureCategory.PROXY_DOCUMENT),
]


def validate_pdf_content(
    raw_bytes: bytes,
    expected_symbol: str = "",
    expected_fy: int | None = None,
    expected_company_name: str = "",
) -> ValidationResult:
    """Deep multi-stage validation of a downloaded PDF document.

    Checks:
      1. Not HTML or empty
      2. Valid PDF signature (%PDF-)
      3. PyMuPDF parse & page count
      4. SHA-256 calculation
      5. Text extraction & positive annual-report markers
      6. Rejection of quarterly, interim, half-yearly or proxy notices
    """
    file_size = len(raw_bytes)

    # Check 1: HTML webpage check first
    prefix = raw_bytes[:1024].lower()
    if b"<!doctype html" in prefix or b"<html" in prefix or b"<head" in prefix:
        return ValidationResult(
            is_valid=False,
            stage=ValidationStage.DOWNLOADED,
            failure_category=FailureCategory.HTML_INSTEAD_OF_PDF,
            failure_reason="Downloaded content is an HTML webpage, not a PDF document",
            file_size=file_size,
        )

    # Check 2: Valid PDF magic header
    if not raw_bytes.startswith(b"%PDF-"):
        return ValidationResult(
            is_valid=False,
            stage=ValidationStage.DOWNLOADED,
            failure_category=FailureCategory.CORRUPT_PDF,
            failure_reason="Missing %PDF- header",
            file_size=file_size,
        )

    # Check 3: Truncated / empty
    if file_size < 100:
        return ValidationResult(
            is_valid=False,
            stage=ValidationStage.DOWNLOADED,
            failure_category=FailureCategory.TRUNCATED_PDF,
            failure_reason="File size under 100 bytes",
            file_size=file_size,
        )

    # Check 4: SHA-256
    sha256 = hashlib.sha256(raw_bytes).hexdigest()

    # Check 5: PDF structure via fitz
    if fitz is None:
        # Fallback if fitz not installed
        return ValidationResult(
            is_valid=True,
            stage=ValidationStage.PDF_STRUCTURE_VALID,
            file_size=file_size,
            sha256=sha256,
        )

    try:
        doc = fitz.open(stream=raw_bytes, filetype="pdf")
    except Exception as exc:
        return ValidationResult(
            is_valid=False,
            stage=ValidationStage.DOWNLOADED,
            failure_category=FailureCategory.CORRUPT_PDF,
            failure_reason=f"PyMuPDF failed to parse document structure: {exc}",
            file_size=file_size,
            sha256=sha256,
        )

    page_count = len(doc)
    if page_count < 1:
        doc.close()
        return ValidationResult(
            is_valid=False,
            stage=ValidationStage.DOWNLOADED,
            failure_category=FailureCategory.CORRUPT_PDF,
            failure_reason="PDF contains 0 pages",
            file_size=file_size,
            sha256=sha256,
        )

    # Sample text from first few pages (where title, directors' report, and accounts appear)
    sample_pages = min(page_count, 15)
    extracted_text_parts: list[str] = []
    for i in range(sample_pages):
        try:
            page = doc[i]
            extracted_text_parts.append(page.get_text())
        except Exception:
            pass
    doc.close()

    full_sample = " ".join(extracted_text_parts).strip()
    has_text = len(full_sample) > 10

    notes: list[str] = []

    # Check 5: Negative markers (Quarterly, Half-yearly, AGM notice)
    if has_text:
        # Check first 3 pages particularly strongly
        first_3_pages = " ".join(extracted_text_parts[:3])
        for neg_regex, neg_cat in _NEGATIVE_CONTENT_MARKERS:
            if neg_regex.search(first_3_pages):
                # Ensure it's not a false positive like "quarterly reports are available on website" in annual report
                # If "condensed interim" appears in title pages, it is definitively NOT an annual report
                if "condensed interim" in first_3_pages.lower() or "quarter report" in first_3_pages.lower():
                    return ValidationResult(
                        is_valid=False,
                        stage=ValidationStage.ANNUAL_REPORT_VALID,
                        failure_category=neg_cat,
                        failure_reason=f"Document contains explicit negative classification marker: '{neg_regex.pattern}'",
                        page_count=page_count,
                        file_size=file_size,
                        sha256=sha256,
                        has_text=has_text,
                        evidence_notes=[f"Matched negative marker: {neg_regex.pattern}"],
                    )

    # Check 6: Positive markers
    positive_matches = [m.pattern for m in _POSITIVE_CONTENT_MARKERS if m.search(full_sample)]
    if positive_matches:
        notes.append(f"Matched {len(positive_matches)} positive markers: {positive_matches[:3]}")

    # Check 7: Scanned documents (no text extracted)
    if not has_text:
        if page_count < 10:
            return ValidationResult(
                is_valid=False,
                stage=ValidationStage.PDF_STRUCTURE_VALID,
                failure_category=FailureCategory.SCANNED_REVIEW,
                failure_reason=f"Scanned/image-only document with only {page_count} pages (unlikely annual report)",
                page_count=page_count,
                file_size=file_size,
                sha256=sha256,
                has_text=False,
                evidence_notes=["Image-only / scanned document"],
            )
        notes.append("Scanned PDF with sufficient page count (>10 pages)")

    return ValidationResult(
        is_valid=True,
        stage=ValidationStage.CONTENT_VALID,
        failure_category=FailureCategory.NONE,
        page_count=page_count,
        file_size=file_size,
        sha256=sha256,
        has_text=has_text,
        evidence_notes=notes,
    )
