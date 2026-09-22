"""Provider-neutral sustainability metadata and authorized bulk ZIP ingestion."""

from __future__ import annotations

import csv
import hashlib
import os
import re
import sqlite3
import uuid
import zipfile
from collections import Counter
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit

import fitz

from .catalog import FIELDS, Report, _url
from .discovery import Company, read_universe
from .engine import RunLock, native_path


METADATA_FIELDS = (
    "company_name", "ticker", "isin", "country", "report_year",
    "report_title", "report_type", "language", "source_url", "filename",
    "page_count",
)
INDEX_FIELDS = (*FIELDS, "zip_member", "classification", "report_title")
TOPIC = re.compile(r"environmental update|portfolio update|people.{0,8}communities|climate update|progress update", re.I)
INTEGRATED = re.compile(r"integrated|business\s*(?:&|and)\s*(?:sustainability|esg)|annual report (?:and|&) accounts", re.I)
STANDALONE = re.compile(r"sustainab|\besg\b|corporate (?:social )?responsibility|\bcsr\b|impact (?:report|summary)", re.I)
YEAR = re.compile(r"(?:FY)?(20\d{2})$")
MAX_PDF_BYTES = 256 * 1024 * 1024
COUNTRIES = {
    "US": "USA", "USA": "USA", "UNITED STATES": "USA", "UNITED STATES OF AMERICA": "USA",
    "UK": "GBR", "GB": "GBR", "GBR": "GBR", "UNITED KINGDOM": "GBR", "GREAT BRITAIN": "GBR",
}


def classify(title: str, kind: str) -> tuple[str, str]:
    description = f"{title} {kind}"
    if TOPIC.search(description):
        return "TOPIC_UPDATE", "CLIMATE" if "environment" in description.lower() or "climate" in description.lower() else "OTHER"
    if INTEGRATED.search(description):
        return "INTEGRATED_REPORT", "IR"
    if STANDALONE.search(description):
        return "STANDALONE_SR", "SR"
    return "MANUAL_REVIEW", "OTHER"


def _report(company: Company, year: int, code: str, url: str) -> Report:
    return Report.from_row({
        "country": company.country, "exchange": company.exchange,
        "lei": company.lei, "isin": company.isin, "ticker": company.ticker,
        "fiscal_year": f"FY{year}", "report_type": code, "language": "EN",
        "pdf_url": url, "source_page": "", "verified": "true",
    })


def import_metadata(universe: Path, metadata: Path, direct_output: Path,
                    bulk_output: Path, review_output: Path,
                    years: tuple[int, int] = (2017, 2025)) -> dict:
    """Join an authorized portal or official archive CSV to the identity universe.

    Exact country+ISIN matching prevents similarly named issuers being conflated.
    Conflicting records for one canonical SOP slot go to review.
    """
    companies = read_universe(universe)
    by_identity = {(c.country, c.isin): c for c in companies}
    chosen: dict[Path, dict[str, str]] = {}
    conflicts: set[Path] = set()
    rejected: list[dict[str, str]] = []
    with metadata.open("r", encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        headers = {re.sub(r"[^a-z0-9]", "", name.casefold()): name for name in (reader.fieldnames or [])}
        columns = {field: headers.get(re.sub(r"[^a-z0-9]", "", field)) for field in METADATA_FIELDS}
        if any(name is None for name in columns.values()):
            raise ValueError(f"metadata requires columns: {', '.join(METADATA_FIELDS)}")
        for line, raw in enumerate(reader, 2):
            row = {field: (raw.get(columns[field]) or "").strip() for field in METADATA_FIELDS}
            row["country"] = COUNTRIES.get(row["country"].upper(), row["country"])
            row["isin"] = row["isin"].upper()
            reason = ""
            company = by_identity.get((row["country"], row["isin"]))
            match = YEAR.fullmatch(row["report_year"])
            year = int(match.group(1)) if match else None
            if company is None:
                reason = "company not in universe"
            elif row["ticker"] and row["ticker"].upper() != company.ticker:
                reason = "ticker conflicts with universe"
            elif year is None or not years[0] <= year <= years[1]:
                reason = "year outside requested range"
            elif row["language"].casefold() not in {"en", "english"}:
                reason = "not English"
            classification, code = classify(row["report_title"], row["report_type"])
            if not reason and classification == "MANUAL_REVIEW":
                reason = "report type needs review"
            url = row["source_url"]
            filename = row["filename"].replace("\\", "/")
            if not reason and not url and not filename:
                reason = "no direct URL or ZIP member"
            if not reason and url and not _url(url, allow_http=False):
                reason = "invalid HTTPS source URL"
            if not reason and filename and (filename.startswith("/") or ".." in PurePosixPath(filename).parts):
                reason = "unsafe ZIP member"
            if not reason:
                report = _report(company, year, code, url or "https://bulk.invalid/report.pdf")
                record = {key: str(getattr(report, key)) for key in FIELDS}
                record["verified"] = "true"
                record.update(zip_member=filename, classification=classification,
                              report_title=row["report_title"])
                prior = chosen.get(report.relative_path)
                if report.relative_path in conflicts:
                    reason = "duplicate canonical slot; review competing reports"
                elif prior and (prior["pdf_url"], prior["zip_member"]) != (record["pdf_url"], filename):
                    reason = "duplicate canonical slot; review competing reports"
                    rejected.append({**prior, "reason": reason, "line": "prior"})
                    chosen.pop(report.relative_path)
                    conflicts.add(report.relative_path)
                elif prior:
                    continue
                else:
                    chosen[report.relative_path] = record
            if reason:
                rejected.append({**row, "reason": reason, "line": str(line)})
    direct = [r for r in chosen.values() if r["pdf_url"] != "https://bulk.invalid/report.pdf"]
    bulk = [r for r in chosen.values() if r["zip_member"]]
    for path, fields, rows in (
        (direct_output, FIELDS, direct),
        (bulk_output, INDEX_FIELDS, bulk),
        (review_output, (*METADATA_FIELDS, "reason", "line"), rejected),
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)
    return {"universe_companies": len(companies), "metadata_rows": len(chosen) + len(rejected),
            "direct": len(direct), "bulk": len(bulk), "review": len(rejected),
            "classifications": dict(Counter(r["classification"] for r in chosen.values()))}


def ingest_zip(archive: Path, index: Path, output_root: Path, state_path: Path) -> dict:
    """Extract named members only; hash, validate and atomically place SOP PDFs."""
    with index.open("r", encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        if not reader.fieldnames or not set(INDEX_FIELDS).issubset(reader.fieldnames):
            raise ValueError("bulk index has missing columns")
        entries = [(Report.from_row(row), row["zip_member"]) for row in reader]
    results = Counter()
    with RunLock(state_path), zipfile.ZipFile(archive) as zipped:
        state_path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(state_path)
        try:
            db.execute("""CREATE TABLE IF NOT EXISTS sustainability_bulk (
                relative_path TEXT PRIMARY KEY, archive TEXT NOT NULL,
                zip_member TEXT NOT NULL, bytes INTEGER NOT NULL,
                pages INTEGER NOT NULL, sha256 TEXT NOT NULL,
                status TEXT NOT NULL, error TEXT NOT NULL)""")
            for report, member in entries:
                target = output_root / report.relative_path
                staged = target.with_name(f"{target.name}.{uuid.uuid4().hex}.part")
                size = 0
                digest = hashlib.sha256()
                try:
                    if target.is_file():
                        try:
                            with fitz.open(native_path(target)) as existing:
                                if existing.is_pdf and not existing.needs_pass and existing.page_count > 0:
                                    results["skipped_existing"] += 1
                                    continue
                        except fitz.FileDataError:
                            pass
                    info = zipped.getinfo(member)
                    if info.file_size > MAX_PDF_BYTES:
                        raise ValueError("PDF exceeds 256 MiB")
                    os.makedirs(native_path(target.parent), exist_ok=True)
                    with zipped.open(info) as source, open(native_path(staged), "wb") as sink:
                        for chunk in iter(lambda: source.read(1024 * 1024), b""):
                            size += len(chunk)
                            if size > MAX_PDF_BYTES:
                                raise ValueError("PDF exceeds 256 MiB")
                            digest.update(chunk)
                            sink.write(chunk)
                        sink.flush()
                        os.fsync(sink.fileno())
                    with fitz.open(native_path(staged)) as pdf:
                        if pdf.needs_pass or pdf.page_count < 1 or pdf.is_repaired or not pdf.is_pdf:
                            raise ValueError("invalid PDF")
                        pages = pdf.page_count
                    with open(native_path(staged), "rb") as check:
                        if check.read(5) != b"%PDF-":
                            raise ValueError("missing PDF signature")
                    os.replace(native_path(staged), native_path(target))
                    db.execute("INSERT OR REPLACE INTO sustainability_bulk VALUES (?,?,?,?,?,?,?,?)",
                               (str(report.relative_path), str(archive), member, size, pages,
                                digest.hexdigest(), "VERIFIED", ""))
                    results["verified"] += 1
                    results["bytes"] += size
                except (KeyError, ValueError, OSError, zipfile.BadZipFile, fitz.FileDataError) as exc:
                    db.execute("INSERT OR REPLACE INTO sustainability_bulk VALUES (?,?,?,?,?,?,?,?)",
                               (str(report.relative_path), str(archive), member, 0, 0, "", "FAILED", str(exc)))
                    results["failed"] += 1
                finally:
                    if staged.exists():
                        staged.unlink()
            db.commit()
        finally:
            db.close()
    return {"requested": len(entries), **dict(results)}
