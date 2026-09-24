"""Autonomous Pakistan harvest orchestrator.

Implements the two-phase architecture mandated by ZETA:
  Phase 1: Pre-resolve the requested cohort (identity, eligibility, URLs, manifest)
  Phase 2: Bulk validated transfers using the ZETA engine

Bridges Pakistan-specific discovery and classification to the existing
download engine, PDF validation, state ledger, and verification pipeline.
"""

from __future__ import annotations

import asyncio
import csv
import hashlib
import json
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    import pymupdf as fitz
except ImportError:
    import fitz

from urllib.parse import urlsplit

from ..catalog import Report
from ..engine import (
    Settings, StateStore, RunLock, Result, native_path,
    interleave_by_host, validate_and_store, inspect_pdf, verify_store,
    cleanup_orphan_parts, run as engine_run,
)
from .company import PakistanCompany
from .config import PakistanConfig, COUNTRY, EXCHANGE_MIC
from .classifier import classify_annual_report, extract_fiscal_year
from .discovery import discover_batch, Candidate
from .report import PakistanReport
from .source_profile import SourceProfileStore
from .universe import resolve_companies
from .validation import validate_pdf_content, FailureCategory, ValidationStage
from .gap_recovery import GapRecoveryManager
from .audit import AuditManager


@dataclass
class HarvestResult:
    """Summary of a Pakistan harvest run."""
    requested_companies: int = 0
    resolved_companies: int = 0
    eligible_company_years: int = 0
    candidates_resolved: int = 0
    already_complete: int = 0
    downloaded: int = 0
    verified: int = 0
    needs_review: int = 0
    missing: int = 0
    failed: int = 0
    bytes_downloaded: int = 0
    elapsed_s: float = 0.0
    pdfs_per_second: float = 0.0
    mib_per_second: float = 0.0
    retry_count: int = 0
    http_429_count: int = 0
    http_5xx_count: int = 0
    output_path: str = ""
    errors: list[str] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items()}


def _make_engine_report(pak_report: PakistanReport) -> Report:
    """Create a catalog.Report compatible with the ZETA engine.

    We bypass the US/UK country check by constructing directly.
    """
    return Report(
        country=COUNTRY,
        exchange=EXCHANGE_MIC,
        lei=pak_report.company.effective_lei,
        isin=pak_report.company.effective_isin,
        ticker=pak_report.company.psx_symbol,
        fiscal_year=pak_report.fy_label,
        report_type=pak_report.report_type,
        language=pak_report.language,
        pdf_url=pak_report.pdf_url,
        source_page=pak_report.source_page,
        verified=pak_report.verified,
    )


def _create_company_folders(
    config: PakistanConfig,
    companies: list[PakistanCompany],
    years: list[int],
) -> None:
    """Create company/FY folder structure on Google Drive."""
    for company in companies:
        folder = config.pakistan_root / company.folder_name
        eligible_years = company.fiscal_year_range(min(years), max(years))
        for year in eligible_years:
            fy_dir = folder / f"FY{year}"
            fy_dir.mkdir(parents=True, exist_ok=True)


def _check_existing(
    config: PakistanConfig,
    pak_report: PakistanReport,
) -> bool:
    """Check if a report already exists on Google Drive."""
    target = config.pakistan_root / pak_report.relative_path
    return os.path.isfile(native_path(target))


async def harvest(
    company_queries: list[str],
    config: PakistanConfig | None = None,
    years: list[int] | None = None,
    progress_callback: Any = None,
) -> HarvestResult:
    """Run a complete Pakistan harvest.

    Phase 1: Resolve → Discover → Classify → Manifest
    Phase 2: Download → Validate → Publish → Gap Recovery
    """
    started = time.monotonic()
    result = HarvestResult()

    # Auto-detect config
    if config is None:
        config = PakistanConfig.auto()
    config.ensure_directories()

    if years is None:
        years = list(config.years)

    result.output_path = str(config.pakistan_root)

    # ── Phase 1: Resolution ───────────────────────────────────────────

    # Resolve companies
    companies = resolve_companies(company_queries)
    result.requested_companies = len(company_queries)
    result.resolved_companies = len([c for c in companies if c.identity_status == "RESOLVED"])

    # Initialize source profile store
    profile_db_path = config.local_runtime / "local" / "source-profiles.sqlite3"
    profile_store = SourceProfileStore(profile_db_path)

    # Register companies in DB
    for company in companies:
        profile_store.upsert_company(
            company.identity_key,
            company_name=company.company_name,
            psx_symbol=company.psx_symbol,
            isin=company.isin,
            lei=company.lei,
            effective_lei=company.effective_lei,
            effective_isin=company.effective_isin,
            official_website=company.official_website,
            fiscal_year_end=company.fiscal_year_end,
            sector=company.sector,
            identity_status=company.identity_status,
        )

    # Calculate eligible slots
    for company in companies:
        eligible = company.fiscal_year_range(min(years), max(years))
        result.eligible_company_years += len(eligible)

    # Create company folders
    _create_company_folders(config, companies, years)

    # Discover report URLs (async bounded concurrency)
    all_candidates = await discover_batch(
        companies, years, profile_store, max_concurrent_companies=5
    )

    # Build download manifest
    download_queue: list[PakistanReport] = []
    for company in companies:
        cid = company.identity_key
        candidates = all_candidates.get(cid, [])
        eligible_years = company.fiscal_year_range(min(years), max(years))

        for year in eligible_years:
            # Find best candidate for this year
            year_candidates = [
                c for c in candidates
                if c.fiscal_year == year and c.classification != "NOT_ANNUAL"
            ]
            if not year_candidates:
                continue

            best = max(year_candidates, key=lambda c: c.score)
            result.candidates_resolved += 1

            pak_report = PakistanReport(
                company=company,
                fiscal_year=year,
                pdf_url=best.pdf_url,
                source_page=best.source_page,
                source_tier=best.source_tier,
                candidate_score=best.score,
                period_end=best.period_end,
            )

            # Check if already exists
            if _check_existing(config, pak_report):
                result.already_complete += 1
                # Record in profile store
                profile_store.upsert_report(
                    cid, year, best.pdf_url,
                    source_page=best.source_page,
                    status="PUBLISHED",
                )
                continue

            # Record candidate
            profile_store.upsert_report(
                cid, year, best.pdf_url,
                source_page=best.source_page,
                source_tier=best.source_tier,
                candidate_score=best.score,
                classification=best.classification,
                status="DISCOVERED",
            )

            download_queue.append(pak_report)

    # Save manifest
    _save_manifest(config, download_queue)

    # ── Phase 2: Bulk Transfer ────────────────────────────────────────

    if download_queue:
        # Pass download_queue directly so relative_path matches Pakistan folder structure
        engine_reports = download_queue

        settings = Settings(
            output_root=config.pakistan_root,
            state_path=config.state_path,
            workers=min(config.workers, len(engine_reports)),
            per_host=config.per_host,
            timeout_s=config.timeout_s,
            max_mib=config.max_mib,
            attempts=config.attempts,
            allow_http=False,
        )

        summary = await engine_run(engine_reports, settings, progress=progress_callback)

        result.downloaded = summary.get("downloaded", 0)
        result.failed = summary.get("failed", 0)
        result.bytes_downloaded = summary.get("bytes", 0)
        result.retry_count = summary.get("retry_attempts", 0)
        result.http_429_count = summary.get("http_429_attempts", 0)
        result.http_5xx_count = summary.get("http_5xx_attempts", 0)

        # Multi-stage validation of downloaded reports
        for pak_report in download_queue:
            target = config.pakistan_root / pak_report.relative_path
            p_native = native_path(target)
            hostname = urlsplit(pak_report.pdf_url).hostname or ""

            if os.path.isfile(p_native):
                raw = open(p_native, "rb").read()
                val_res = validate_pdf_content(
                    raw,
                    expected_symbol=pak_report.company.psx_symbol,
                    expected_fy=pak_report.fiscal_year,
                    expected_company_name=pak_report.company.company_name,
                )

                if val_res.is_valid:
                    profile_store.mark_downloaded(
                        pak_report.company.identity_key,
                        pak_report.fiscal_year,
                        pak_report.pdf_url,
                        sha256=val_res.sha256,
                        file_size=val_res.file_size,
                        page_count=val_res.page_count,
                        relative_path=str(pak_report.relative_path),
                        stage=val_res.stage.value,
                    )
                    profile_store.record_host_result(hostname, success=True)
                    result.verified += 1
                else:
                    # Invalid report - remove corrupt/quarterly file from user folder
                    try:
                        os.remove(p_native)
                    except OSError:
                        pass
                    profile_store.mark_failure(
                        pak_report.company.identity_key,
                        pak_report.fiscal_year,
                        pak_report.pdf_url,
                        failure_category=val_res.failure_category.value,
                        reason=val_res.failure_reason,
                    )
                    profile_store.record_host_result(hostname, success=False)
                    result.failed += 1
            else:
                profile_store.record_host_result(hostname, success=False)

    # ── Pass 2: Targeted Gap Recovery ─────────────────────────────────
    gap_manager = GapRecoveryManager(config, profile_store)
    unresolved_gaps = gap_manager.identify_gaps(companies, years)

    if unresolved_gaps:
        recovered = await gap_manager.recover_gaps(unresolved_gaps)
        if recovered:
            gap_manifest_path = config.manifests_dir / f"gap-manifest-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.csv"
            gap_manager.export_gap_manifest(recovered, gap_manifest_path)

            gap_settings = Settings(
                output_root=config.pakistan_root,
                state_path=config.state_path,
                workers=min(config.workers, len(recovered)),
                per_host=config.per_host,
                timeout_s=config.timeout_s,
                max_mib=config.max_mib,
                attempts=config.attempts,
                allow_http=False,
            )
            gap_summary = await engine_run(recovered, gap_settings)
            result.downloaded += gap_summary.get("downloaded", 0)
            result.bytes_downloaded += gap_summary.get("bytes", 0)

            for pak_rep in recovered:
                target = config.pakistan_root / pak_rep.relative_path
                p_native = native_path(target)
                if os.path.isfile(p_native):
                    raw = open(p_native, "rb").read()
                    v_res = validate_pdf_content(raw)
                    if v_res.is_valid:
                        profile_store.mark_downloaded(
                            pak_rep.company.identity_key,
                            pak_rep.fiscal_year,
                            pak_rep.pdf_url,
                            sha256=v_res.sha256,
                            file_size=v_res.file_size,
                            page_count=v_res.page_count,
                            relative_path=str(pak_rep.relative_path),
                        )
                        result.verified += 1

    # ── Gap matrix ────────────────────────────────────────────────────
    company_ids = [c.identity_key for c in companies]
    gap = profile_store.gap_matrix(company_ids, years)
    result.missing = sum(
        1 for row in gap.values() for s in row.values() if s == "MISSING"
    )
    result.needs_review = sum(
        1 for row in gap.values() for s in row.values() if s in ("REVIEW", "IDENTIFIER_REVIEW")
    )

    # Export gap matrix
    profile_store.export_gap_csv(gap, config.audits_dir / "company-year-matrix.csv", years)

    # ── Finalize & Audits ─────────────────────────────────────────────
    result.elapsed_s = round(time.monotonic() - started, 3)
    if result.elapsed_s > 0:
        result.pdfs_per_second = round(result.downloaded / result.elapsed_s, 2)
        result.mib_per_second = round(
            result.bytes_downloaded / (1024 * 1024) / result.elapsed_s, 2
        )

    # Generate complete auditable provenance artifacts
    audit_mgr = AuditManager(config, profile_store)
    audit_mgr.generate_audits(result.to_dict(), companies, years)

    # Snapshot state and profiles safely to Drive
    try:
        profile_store.snapshot_to_drive(config.source_profiles_dir)
        profile_store.snapshot_to_drive(config.state_backups_dir)
    except Exception:
        pass  # Non-fatal; Drive may be temporarily syncing

    profile_store.close()
    return result


def _save_manifest(config: PakistanConfig, reports: list[PakistanReport]) -> None:
    """Write the resolved manifest CSV."""
    if not reports:
        return
    path = config.manifests_dir / f"manifest-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        fieldnames = list(reports[0].to_manifest_row().keys())
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for r in reports:
            writer.writerow(r.to_manifest_row())


def _save_run_summary(config: PakistanConfig, result: HarvestResult) -> None:
    """Write run-summary.json to Drive."""
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = config.audits_dir / f"run-summary-{ts}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result.to_dict(), indent=2), encoding="utf-8")


def run_harvest_sync(
    company_queries: list[str],
    config: PakistanConfig | None = None,
    years: list[int] | None = None,
) -> HarvestResult:
    """Synchronous wrapper for the async harvest."""
    if os.name == "nt":
        with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
            return runner.run(harvest(company_queries, config, years))
    return asyncio.run(harvest(company_queries, config, years))
