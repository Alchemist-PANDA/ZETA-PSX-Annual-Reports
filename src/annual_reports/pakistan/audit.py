"""Auditable provenance and execution audit generation for Pakistan annual reports.

Produces structured audit files:
  - _AUDITS/company-audit.csv
  - _AUDITS/failure-audit.csv
  - _AUDITS/run-summary-<timestamp>.json
  - _FAILURES/failures-<timestamp>.csv

Enforces output folder cleanliness: company folders must contain ONLY
verified annual-report PDFs; all management artifacts belong in _SYSTEM,
_MANIFESTS, _AUDITS, _LOGS, _FAILURES.
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import PakistanConfig
from .source_profiles import SourceProfileStore


class AuditManager:
    """Generates execution audits and provenance records."""

    def __init__(self, config: PakistanConfig, profile_store: SourceProfileStore):
        self.config = config
        self.profile_store = profile_store

    def generate_audits(
        self,
        summary_dict: dict[str, Any],
        companies: list[Any],
        years: list[int],
    ) -> dict[str, Path]:
        """Generate all execution audit artifacts."""
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        self.config.audits_dir.mkdir(parents=True, exist_ok=True)
        self.config.failures_dir.mkdir(parents=True, exist_ok=True)

        audit_paths: dict[str, Path] = {}

        # 1. Run summary JSON
        summary_path = self.config.audits_dir / f"run-summary-{ts}.json"
        summary_path.write_text(json.dumps(summary_dict, indent=2), encoding="utf-8")
        audit_paths["summary"] = summary_path

        # 2. Company level audit CSV
        company_audit_path = self.config.audits_dir / f"company-audit-{ts}.csv"
        cids = [c.identity_key for c in companies]
        gap = self.profile_store.gap_matrix(cids, years)

        with company_audit_path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["company_id", "company_name", "ticker", "eligible_years", "verified_count", "missing_count", "review_count", "failed_count"])
            for comp in companies:
                row = gap.get(comp.identity_key, {})
                v_count = sum(1 for s in row.values() if s in ("VERIFIED", "PUBLISHED"))
                m_count = sum(1 for s in row.values() if s == "MISSING")
                r_count = sum(1 for s in row.values() if s == "REVIEW")
                f_count = sum(1 for s in row.values() if s == "FAILED")
                eligible_count = len(comp.fiscal_year_range(min(years), max(years)))
                writer.writerow([
                    comp.identity_key,
                    comp.company_name,
                    comp.psx_symbol,
                    eligible_count,
                    v_count,
                    m_count,
                    r_count,
                    f_count,
                ])
        audit_paths["company_audit"] = company_audit_path

        # 3. Failure audit CSV
        failures_path = self.config.failures_dir / f"failures-{ts}.csv"
        cursor = self.profile_store.connection.execute("""
            SELECT company_id, fiscal_year, pdf_url, stage, failure_category, notes, discovered_at
            FROM pak_reports
            WHERE status = 'FAILED' OR failure_category != 'NONE'
        """)
        failed_rows = [dict(r) for r in cursor.fetchall()]
        if failed_rows:
            with failures_path.open("w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=list(failed_rows[0].keys()))
                writer.writeheader()
                for r in failed_rows:
                    writer.writerow(r)
            audit_paths["failures"] = failures_path

        return audit_paths

    def verify_folder_cleanliness(self, root: Path) -> list[str]:
        """Ensure company folders contain ONLY final PDF reports."""
        violations: list[str] = []
        if not root.is_dir():
            return violations

        for item in root.iterdir():
            # Skip management folders
            if item.name.startswith("_") or item.name.startswith("."):
                continue
            if item.is_dir():
                for sub in item.rglob("*"):
                    if sub.is_file():
                        # Must be .pdf or .gitkeep
                        if not (sub.suffix.lower() == ".pdf" or sub.name == ".gitkeep"):
                            violations.append(str(sub))
        return violations
