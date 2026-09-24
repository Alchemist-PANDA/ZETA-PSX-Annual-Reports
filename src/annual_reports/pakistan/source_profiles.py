"""Permanent company source profile and host performance memory store.

Provides durable SQLite persistence for learned website adapters, investor
relations pages, report URL patterns, and host performance statistics.
Supports safe WAL checkpointing, online SQLite backups, and exports to
Google Drive ``_SYSTEM/source-profiles/`` (SQLite, JSON, CSV).
"""

from __future__ import annotations

import csv
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class SourceProfileStore:
    """Persistent per-company source profile and host stats database."""

    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(str(self.db_path))
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA synchronous=NORMAL")
        self.connection.execute("PRAGMA busy_timeout=5000")
        self._init_schema()

    def _init_schema(self) -> None:
        """Create tables and indexes matching production requirements."""
        self.connection.executescript("""
            CREATE TABLE IF NOT EXISTS company_source_profiles (
                company_id TEXT PRIMARY KEY,
                current_name TEXT NOT NULL,
                ticker TEXT NOT NULL,
                official_domain TEXT NOT NULL DEFAULT '',
                annual_report_page TEXT NOT NULL DEFAULT '',
                investor_relations_page TEXT NOT NULL DEFAULT '',
                financial_reports_page TEXT NOT NULL DEFAULT '',
                sitemap_url TEXT NOT NULL DEFAULT '',
                adapter_type TEXT NOT NULL DEFAULT 'GenericAnchorAdapter',
                working_url_pattern TEXT NOT NULL DEFAULT '',
                pdf_url_pattern TEXT NOT NULL DEFAULT '',
                preferred_host TEXT NOT NULL DEFAULT '',
                requires_javascript INTEGER NOT NULL DEFAULT 0,
                historical_coverage_start INTEGER NOT NULL DEFAULT 2017,
                historical_coverage_end INTEGER NOT NULL DEFAULT 2025,
                last_verified TEXT NOT NULL DEFAULT '',
                success_rate REAL NOT NULL DEFAULT 1.0,
                failure_count INTEGER NOT NULL DEFAULT 0,
                last_error TEXT NOT NULL DEFAULT '',
                confidence TEXT NOT NULL DEFAULT 'HIGH',
                updated_at TEXT NOT NULL DEFAULT ''
            );

            CREATE VIEW IF NOT EXISTS source_profiles AS SELECT * FROM company_source_profiles;

            CREATE INDEX IF NOT EXISTS idx_csp_ticker ON company_source_profiles(ticker);
            CREATE INDEX IF NOT EXISTS idx_csp_host ON company_source_profiles(preferred_host);

            CREATE TABLE IF NOT EXISTS host_stats (
                hostname TEXT PRIMARY KEY,
                avg_connect_ms REAL NOT NULL DEFAULT 0.0,
                avg_ttfb_ms REAL NOT NULL DEFAULT 0.0,
                avg_mbps REAL NOT NULL DEFAULT 0.0,
                success_rate REAL NOT NULL DEFAULT 1.0,
                timeout_rate REAL NOT NULL DEFAULT 0.0,
                rate_limit_rate REAL NOT NULL DEFAULT 0.0,
                server_error_rate REAL NOT NULL DEFAULT 0.0,
                recommended_concurrency INTEGER NOT NULL DEFAULT 2,
                last_tested TEXT NOT NULL DEFAULT ''
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
                stage TEXT NOT NULL DEFAULT 'DISCOVERED',
                failure_category TEXT NOT NULL DEFAULT 'NONE',
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

            CREATE INDEX IF NOT EXISTS idx_pr_company_fy ON pak_reports(company_id, fiscal_year);
            CREATE INDEX IF NOT EXISTS idx_pr_status ON pak_reports(status);
            CREATE INDEX IF NOT EXISTS idx_pr_sha256 ON pak_reports(sha256);
            CREATE INDEX IF NOT EXISTS idx_pr_url ON pak_reports(pdf_url);

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

        # Ensure migration columns exist on pak_reports if table previously created
        for col, col_type, default in [
            ("stage", "TEXT", "'DISCOVERED'"),
            ("failure_category", "TEXT", "'NONE'"),
        ]:
            try:
                self.connection.execute(f"ALTER TABLE pak_reports ADD COLUMN {col} {col_type} NOT NULL DEFAULT {default}")
            except sqlite3.OperationalError:
                pass

        self.connection.commit()

    def close(self) -> None:
        """Close SQLite database connection safely."""
        try:
            self.connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            self.connection.close()
        except Exception:
            pass

    # ── Company Operations ────────────────────────────────────────────

    def get_company(self, company_id: str) -> dict[str, Any] | None:
        """Retrieve registered company metadata."""
        row = self.connection.execute(
            "SELECT * FROM pak_companies WHERE company_id = ?",
            (company_id,),
        ).fetchone()
        return dict(row) if row else None

    def upsert_company(self, company_id: str, **fields: Any) -> None:
        """Insert or update registered company metadata."""
        fields["company_id"] = company_id
        cols = list(fields.keys())
        placeholders = ", ".join("?" for _ in cols)
        col_names = ", ".join(cols)
        updates = ", ".join(f"{c} = excluded.{c}" for c in cols if c != "company_id")
        sql = f"""
            INSERT INTO pak_companies ({col_names})
            VALUES ({placeholders})
            ON CONFLICT(company_id) DO UPDATE SET
            {updates}
        """
        self.connection.execute(sql, list(fields.values()))
        self.connection.commit()

    # ── Source Profile Operations ─────────────────────────────────────

    def get_profile(self, company_id: str) -> dict[str, Any] | None:
        """Retrieve cached source profile for a company."""
        row = self.connection.execute(
            "SELECT * FROM company_source_profiles WHERE company_id = ?",
            (company_id,),
        ).fetchone()
        return dict(row) if row else None

    def upsert_profile(self, company_id: str, **fields: Any) -> None:
        """Insert or update a company source profile."""
        now = datetime.now(timezone.utc).isoformat()
        fields["updated_at"] = now

        # Backwards compatible alias mapping
        if "company_name" in fields and "current_name" not in fields:
            fields["current_name"] = fields.pop("company_name")
        elif "company_name" in fields:
            fields.pop("company_name")

        if "psx_symbol" in fields and "ticker" not in fields:
            fields["ticker"] = fields.pop("psx_symbol")
        elif "psx_symbol" in fields:
            fields.pop("psx_symbol")

        existing = self.get_profile(company_id)

        if existing:
            sets = ", ".join(f"{k} = ?" for k in fields)
            vals = list(fields.values())
            vals.append(company_id)
            self.connection.execute(
                f"UPDATE company_source_profiles SET {sets} WHERE company_id = ?",
                vals,
            )
        else:
            fields.setdefault("current_name", "")
            fields.setdefault("ticker", "")
            fields["company_id"] = company_id
            cols = ", ".join(fields.keys())
            placeholders = ", ".join("?" for _ in fields)
            self.connection.execute(
                f"INSERT INTO company_source_profiles ({cols}) VALUES ({placeholders})",
                list(fields.values()),
            )
        self.connection.commit()

    def all_profiles(self) -> list[dict[str, Any]]:
        """Return all persisted source profiles."""
        rows = self.connection.execute("SELECT * FROM company_source_profiles ORDER BY ticker").fetchall()
        return [dict(r) for r in rows]

    # ── Host Stats Operations ─────────────────────────────────────────

    def get_host_stat(self, hostname: str) -> dict[str, Any] | None:
        """Get performance memory for a host."""
        row = self.connection.execute(
            "SELECT * FROM host_stats WHERE hostname = ?", (hostname,)
        ).fetchone()
        return dict(row) if row else None

    def record_host_result(
        self,
        hostname: str,
        success: bool,
        connect_ms: float = 0.0,
        ttfb_ms: float = 0.0,
        is_429: bool = False,
        is_5xx: bool = False,
        is_timeout: bool = False,
    ) -> None:
        """Update host performance memory based on observed request outcome."""
        now = datetime.now(timezone.utc).isoformat()
        existing = self.get_host_stat(hostname)

        if not existing:
            concurrency = 1 if (is_429 or is_5xx or is_timeout) else 2
            self.connection.execute("""
                INSERT INTO host_stats
                (hostname, avg_connect_ms, avg_ttfb_ms, success_rate, timeout_rate,
                 rate_limit_rate, server_error_rate, recommended_concurrency, last_tested)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                hostname, connect_ms, ttfb_ms,
                1.0 if success else 0.0,
                1.0 if is_timeout else 0.0,
                1.0 if is_429 else 0.0,
                1.0 if is_5xx else 0.0,
                concurrency, now
            ))
        else:
            # Exponential moving average / decay
            decay = 0.8
            new_success = decay * existing["success_rate"] + (1 - decay) * (1.0 if success else 0.0)
            new_timeout = decay * existing["timeout_rate"] + (1 - decay) * (1.0 if is_timeout else 0.0)
            new_429 = decay * existing["rate_limit_rate"] + (1 - decay) * (1.0 if is_429 else 0.0)
            new_5xx = decay * existing["server_error_rate"] + (1 - decay) * (1.0 if is_5xx else 0.0)

            # Never increase concurrency after throttling or errors
            if is_429 or is_5xx or is_timeout or new_429 > 0.05:
                concurrency = 1
            elif new_success > 0.95 and existing["avg_ttfb_ms"] < 800 and existing["recommended_concurrency"] < 3:
                concurrency = existing["recommended_concurrency"]  # maintain or cautious 2-3
            else:
                concurrency = max(1, min(2, existing["recommended_concurrency"]))

            self.connection.execute("""
                UPDATE host_stats SET
                    avg_connect_ms = ?,
                    avg_ttfb_ms = ?,
                    success_rate = ?,
                    timeout_rate = ?,
                    rate_limit_rate = ?,
                    server_error_rate = ?,
                    recommended_concurrency = ?,
                    last_tested = ?
                WHERE hostname = ?
            """, (
                connect_ms or existing["avg_connect_ms"],
                ttfb_ms or existing["avg_ttfb_ms"],
                new_success, new_timeout, new_429, new_5xx,
                concurrency, now, hostname
            ))
        self.connection.commit()

    # ── Report Tracking & Multi-Stage Validation ──────────────────────

    def upsert_report(self, company_id: str, fiscal_year: int,
                      pdf_url: str, **fields: Any) -> None:
        """Record or update a candidate report."""
        now = datetime.now(timezone.utc).isoformat()
        fields["company_id"] = company_id
        fields["fiscal_year"] = fiscal_year
        fields["pdf_url"] = pdf_url

        row = self.connection.execute(
            "SELECT id FROM pak_reports WHERE company_id = ? AND fiscal_year = ? AND pdf_url = ?",
            (company_id, fiscal_year, pdf_url),
        ).fetchone()

        if row:
            sets = ", ".join(f"{k} = ?" for k in fields)
            vals = list(fields.values())
            vals.append(row["id"])
            self.connection.execute(f"UPDATE pak_reports SET {sets} WHERE id = ?", vals)
        else:
            fields["discovered_at"] = now
            cols = ", ".join(fields.keys())
            placeholders = ", ".join("?" for _ in fields)
            self.connection.execute(
                f"INSERT INTO pak_reports ({cols}) VALUES ({placeholders})",
                list(fields.values()),
            )
        self.connection.commit()

    def mark_downloaded(
        self, company_id: str, fiscal_year: int, pdf_url: str,
        sha256: str = "", file_size: int = 0, page_count: int = 0,
        relative_path: str = "", stage: str = "PUBLISHED",
        failure_category: str = "NONE",
    ) -> None:
        """Mark a report downloaded and validated."""
        now = datetime.now(timezone.utc).isoformat()
        self.connection.execute("""
            UPDATE pak_reports SET
                status = 'PUBLISHED',
                stage = ?,
                failure_category = ?,
                sha256 = ?,
                file_size = ?,
                page_count = ?,
                relative_path = ?,
                downloaded_at = ?,
                verified_at = ?
            WHERE company_id = ? AND fiscal_year = ? AND pdf_url = ?
        """, (stage, failure_category, sha256, file_size, page_count, relative_path, now, now,
              company_id, fiscal_year, pdf_url))
        self.connection.commit()

    def mark_failure(
        self, company_id: str, fiscal_year: int, pdf_url: str,
        failure_category: str, reason: str,
    ) -> None:
        """Mark a report candidate as failed with explicit category."""
        self.connection.execute("""
            UPDATE pak_reports SET
                status = 'FAILED',
                failure_category = ?,
                notes = ?
            WHERE company_id = ? AND fiscal_year = ? AND pdf_url = ?
        """, (failure_category, reason, company_id, fiscal_year, pdf_url))
        self.connection.commit()

    def get_report(self, company_id: str, fiscal_year: int) -> dict[str, Any] | None:
        """Lookup a report by company_id and fiscal_year."""
        row = self.connection.execute(
            "SELECT * FROM pak_reports WHERE company_id = ? AND fiscal_year = ?",
            (company_id, fiscal_year),
        ).fetchone()
        return dict(row) if row else None

    def get_published_reports(self, company_id: str) -> list[dict[str, Any]]:
        """Return all published reports for a company."""
        rows = self.connection.execute(
            "SELECT * FROM pak_reports WHERE company_id = ? AND status = 'PUBLISHED' ORDER BY fiscal_year",
            (company_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    # ── Gap Matrix Generation ─────────────────────────────────────────

    def gap_matrix(self, company_ids: list[str], years: list[int]) -> dict[str, dict[int, str]]:
        """Produce company × fiscal year status matrix.

        States:
          VERIFIED / PUBLISHED
          NOT_LISTED
          NOT_ELIGIBLE / DELISTED
          MISSING
          REVIEW
          FAILED
        """
        matrix: dict[str, dict[int, str]] = {}
        for cid in company_ids:
            matrix[cid] = {}
            # Check company listing eligibility from pak_companies
            comp_row = self.connection.execute(
                "SELECT listing_date, delisting_date FROM pak_companies WHERE company_id = ?",
                (cid,),
            ).fetchone()
            listing_year = int(comp_row["listing_date"][:4]) if comp_row and comp_row["listing_date"] else None
            delist_year = int(comp_row["delisting_date"][:4]) if comp_row and comp_row["delisting_date"] else None

            # Get all reports for this company
            reports = self.connection.execute(
                "SELECT fiscal_year, status, failure_category FROM pak_reports WHERE company_id = ?",
                (cid,),
            ).fetchall()
            by_year: dict[int, list[sqlite3.Row]] = {}
            for r in reports:
                by_year.setdefault(r["fiscal_year"], []).append(r)

            for y in years:
                # Check eligibility
                if listing_year and y < listing_year:
                    matrix[cid][y] = "NOT_LISTED"
                    continue
                if delist_year and y > delist_year:
                    matrix[cid][y] = "NOT_ELIGIBLE"
                    continue

                year_reps = by_year.get(y, [])
                if any(r["status"] == "PUBLISHED" for r in year_reps):
                    matrix[cid][y] = "VERIFIED"
                elif any(r["status"] == "FAILED" for r in year_reps):
                    matrix[cid][y] = "FAILED"
                elif any(r["status"] == "DISCOVERED" for r in year_reps):
                    matrix[cid][y] = "REVIEW"
                else:
                    matrix[cid][y] = "MISSING"

        return matrix

    def export_gap_csv(self, gap: dict[str, dict[int, str]], path: Path, years: list[int]) -> None:
        """Export gap matrix to CSV."""
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["company_id"] + [str(y) for y in years])
            for cid, row in sorted(gap.items()):
                writer.writerow([cid] + [row.get(y, "MISSING") for y in years])

    # ── Safe Backup to Google Drive (Requirement 10 & 14) ─────────────

    def snapshot_to_drive(self, drive_backup_dir: Path) -> Path:
        """Safe SQLite online backup & JSON/CSV export to Google Drive.

        Uses SQLite's safe online backup API so no locks are held and no
        corrupt database copy can ever occur during active transactions.
        """
        drive_backup_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

        # 1. Safe SQLite online backup
        backup_file = drive_backup_dir / f"source-profiles-{ts}.sqlite3"
        dest_conn = sqlite3.connect(str(backup_file))
        with dest_conn:
            self.connection.backup(dest_conn)
        dest_conn.close()

        # 2. JSON snapshot
        json_file = drive_backup_dir / f"source-profiles-{ts}.json"
        profiles = self.all_profiles()
        json_file.write_text(json.dumps(profiles, indent=2), encoding="utf-8")

        # 3. CSV snapshot
        csv_file = drive_backup_dir / f"source-profiles-{ts}.csv"
        if profiles:
            with csv_file.open("w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=list(profiles[0].keys()))
                writer.writeheader()
                for p in profiles:
                    writer.writerow(p)

        return backup_file
