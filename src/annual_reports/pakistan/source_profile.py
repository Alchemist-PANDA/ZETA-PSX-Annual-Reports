"""Pakistan source-profile persistence.

Stores per-company discovered source URLs and adapter configurations so
subsequent runs skip research for already-resolved companies.  Profiles
are persisted both in the local SQLite state DB and snapshotted to
Google Drive ``_SYSTEM/source-profiles/``.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class SourceProfileStore:
    """Persistent per-company source profile database."""

    def __init__(self, db_path: Path):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(db_path)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA synchronous=NORMAL")
        self.connection.executescript("""
            CREATE TABLE IF NOT EXISTS source_profiles (
                company_id TEXT PRIMARY KEY,
                company_name TEXT NOT NULL,
                psx_symbol TEXT NOT NULL,
                official_domain TEXT NOT NULL DEFAULT '',
                annual_report_page TEXT NOT NULL DEFAULT '',
                investor_relations_page TEXT NOT NULL DEFAULT '',
                adapter_type TEXT NOT NULL DEFAULT 'generic',
                working_path_pattern TEXT NOT NULL DEFAULT '',
                pdf_path_pattern TEXT NOT NULL DEFAULT '',
                sitemap_url TEXT NOT NULL DEFAULT '',
                requires_js INTEGER NOT NULL DEFAULT 0,
                preferred_host TEXT NOT NULL DEFAULT '',
                last_verified TEXT NOT NULL DEFAULT '',
                historical_coverage_start INTEGER NOT NULL DEFAULT 0,
                historical_coverage_end INTEGER NOT NULL DEFAULT 0,
                success_rate REAL NOT NULL DEFAULT 0.0,
                confidence TEXT NOT NULL DEFAULT 'UNKNOWN',
                last_error TEXT NOT NULL DEFAULT '',
                last_refresh TEXT NOT NULL DEFAULT '',
                metadata_json TEXT NOT NULL DEFAULT '{}'
            );

            CREATE TABLE IF NOT EXISTS pak_reports (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                company_id TEXT NOT NULL,
                fiscal_year INTEGER NOT NULL,
                report_type TEXT NOT NULL DEFAULT 'AR',
                language TEXT NOT NULL DEFAULT 'EN',
                pdf_url TEXT NOT NULL,
                source_page TEXT NOT NULL DEFAULT '',
                source_tier INTEGER NOT NULL DEFAULT 1,
                candidate_score REAL NOT NULL DEFAULT 0.0,
                period_end TEXT NOT NULL DEFAULT '',
                classification TEXT NOT NULL DEFAULT 'ANNUAL',
                status TEXT NOT NULL DEFAULT 'DISCOVERED',
                sha256 TEXT NOT NULL DEFAULT '',
                file_size INTEGER NOT NULL DEFAULT 0,
                page_count INTEGER NOT NULL DEFAULT 0,
                relative_path TEXT NOT NULL DEFAULT '',
                notes TEXT NOT NULL DEFAULT '',
                discovered_at TEXT NOT NULL,
                downloaded_at TEXT NOT NULL DEFAULT '',
                verified_at TEXT NOT NULL DEFAULT '',
                UNIQUE(company_id, fiscal_year, report_type, pdf_url)
            );

            CREATE INDEX IF NOT EXISTS idx_pak_reports_company
                ON pak_reports(company_id, fiscal_year);
            CREATE INDEX IF NOT EXISTS idx_pak_reports_status
                ON pak_reports(status);

            CREATE TABLE IF NOT EXISTS pak_companies (
                company_id TEXT PRIMARY KEY,
                company_name TEXT NOT NULL,
                psx_symbol TEXT NOT NULL,
                country TEXT NOT NULL DEFAULT 'PAK',
                exchange TEXT NOT NULL DEFAULT 'XKAR',
                isin TEXT NOT NULL DEFAULT '',
                lei TEXT NOT NULL DEFAULT '',
                effective_lei TEXT NOT NULL DEFAULT '',
                effective_isin TEXT NOT NULL DEFAULT '',
                official_website TEXT NOT NULL DEFAULT '',
                secp_registration TEXT NOT NULL DEFAULT '',
                listing_date TEXT NOT NULL DEFAULT '',
                delisting_date TEXT NOT NULL DEFAULT '',
                fiscal_year_end TEXT NOT NULL DEFAULT '',
                sector TEXT NOT NULL DEFAULT '',
                identity_status TEXT NOT NULL DEFAULT 'RESOLVED',
                source_confidence TEXT NOT NULL DEFAULT 'HIGH',
                metadata_json TEXT NOT NULL DEFAULT '{}'
            );
        """)
        self.connection.commit()

    def close(self) -> None:
        self.connection.close()

    # ── Source Profile operations ─────────────────────────────────────

    def get_profile(self, company_id: str) -> dict[str, Any] | None:
        row = self.connection.execute(
            "SELECT * FROM source_profiles WHERE company_id = ?",
            (company_id,),
        ).fetchone()
        return dict(row) if row else None

    def upsert_profile(self, company_id: str, **fields: Any) -> None:
        now = datetime.now(timezone.utc).isoformat()
        existing = self.get_profile(company_id)
        if existing:
            sets = ", ".join(f"{k} = ?" for k in fields)
            vals = list(fields.values())
            vals.append(now)
            vals.append(company_id)
            self.connection.execute(
                f"UPDATE source_profiles SET {sets}, last_refresh = ? WHERE company_id = ?",
                vals,
            )
        else:
            fields.setdefault("company_name", "")
            fields.setdefault("psx_symbol", "")
            fields["company_id"] = company_id
            fields["last_refresh"] = now
            cols = ", ".join(fields.keys())
            placeholders = ", ".join("?" for _ in fields)
            self.connection.execute(
                f"INSERT INTO source_profiles ({cols}) VALUES ({placeholders})",
                list(fields.values()),
            )
        self.connection.commit()

    def all_profiles(self) -> list[dict[str, Any]]:
        return [dict(row) for row in self.connection.execute("SELECT * FROM source_profiles")]

    # ── Pakistan report tracking ──────────────────────────────────────

    def upsert_report(self, company_id: str, fiscal_year: int,
                      pdf_url: str, **fields: Any) -> None:
        now = datetime.now(timezone.utc).isoformat()
        fields["company_id"] = company_id
        fields["fiscal_year"] = fiscal_year
        fields["pdf_url"] = pdf_url
        fields.setdefault("discovered_at", now)
        fields.setdefault("report_type", "AR")
        fields.setdefault("language", "EN")

        self.connection.execute("""
            INSERT INTO pak_reports (company_id, fiscal_year, pdf_url,
                report_type, language, source_page, source_tier,
                candidate_score, period_end, classification, status,
                notes, discovered_at)
            VALUES (:company_id, :fiscal_year, :pdf_url,
                :report_type, :language,
                :source_page, :source_tier,
                :candidate_score, :period_end, :classification, :status,
                :notes, :discovered_at)
            ON CONFLICT(company_id, fiscal_year, report_type, pdf_url)
            DO UPDATE SET
                source_page = excluded.source_page,
                source_tier = excluded.source_tier,
                candidate_score = excluded.candidate_score,
                status = excluded.status,
                notes = excluded.notes
        """, {
            "source_page": "",
            "source_tier": 1,
            "candidate_score": 0.0,
            "period_end": "",
            "classification": "ANNUAL",
            "status": "DISCOVERED",
            "notes": "",
            **fields,
        })
        self.connection.commit()

    def get_report(self, company_id: str, fiscal_year: int,
                   report_type: str = "AR") -> dict[str, Any] | None:
        row = self.connection.execute(
            "SELECT * FROM pak_reports WHERE company_id = ? AND fiscal_year = ? AND report_type = ? ORDER BY candidate_score DESC LIMIT 1",
            (company_id, fiscal_year, report_type),
        ).fetchone()
        return dict(row) if row else None

    def completed_reports(self, company_id: str) -> list[dict[str, Any]]:
        return [dict(row) for row in self.connection.execute(
            "SELECT * FROM pak_reports WHERE company_id = ? AND status = 'PUBLISHED'",
            (company_id,),
        )]

    def mark_downloaded(self, company_id: str, fiscal_year: int,
                        pdf_url: str, sha256: str, file_size: int,
                        page_count: int, relative_path: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        self.connection.execute("""
            UPDATE pak_reports SET
                status = 'PUBLISHED', sha256 = ?, file_size = ?,
                page_count = ?, relative_path = ?, downloaded_at = ?
            WHERE company_id = ? AND fiscal_year = ? AND pdf_url = ?
        """, (sha256, file_size, page_count, relative_path, now,
              company_id, fiscal_year, pdf_url))
        self.connection.commit()

    # ── Company tracking ──────────────────────────────────────────────

    def upsert_company(self, company_id: str, **fields: Any) -> None:
        fields["company_id"] = company_id
        cols = ", ".join(fields.keys())
        placeholders = ", ".join("?" for _ in fields)
        updates = ", ".join(f"{k} = excluded.{k}" for k in fields if k != "company_id")
        self.connection.execute(
            f"""INSERT INTO pak_companies ({cols}) VALUES ({placeholders})
            ON CONFLICT(company_id) DO UPDATE SET {updates}""",
            list(fields.values()),
        )
        self.connection.commit()

    # ── Gap matrix ────────────────────────────────────────────────────

    def gap_matrix(self, company_ids: list[str],
                   years: list[int]) -> dict[str, dict[int, str]]:
        """Build company × FY status matrix."""
        matrix: dict[str, dict[int, str]] = {}
        for cid in company_ids:
            matrix[cid] = {}
            for year in years:
                report = self.get_report(cid, year)
                if report and report["status"] == "PUBLISHED":
                    matrix[cid][year] = "VERIFIED"
                elif report and report["status"] in ("DISCOVERED", "DOWNLOADING"):
                    matrix[cid][year] = "FOUND_REVIEW"
                elif report and report["status"] == "FAILED":
                    matrix[cid][year] = "FAILED"
                else:
                    matrix[cid][year] = "MISSING"
        return matrix

    def export_gap_csv(self, matrix: dict[str, dict[int, str]],
                       path: Path, years: list[int]) -> None:
        """Write company×FY gap matrix to CSV."""
        import csv
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["company_id"] + [str(y) for y in years])
            for cid, row in sorted(matrix.items()):
                writer.writerow([cid] + [row.get(y, "MISSING") for y in years])

    def snapshot_to_drive(self, drive_dir: Path) -> Path:
        """Safe SQLite backup to Google Drive."""
        import shutil
        drive_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        dest = drive_dir / f"harvest-pak-{ts}.sqlite3"
        # Use SQLite backup API for safety
        backup_conn = sqlite3.connect(str(dest))
        self.connection.backup(backup_conn)
        backup_conn.close()
        return dest
