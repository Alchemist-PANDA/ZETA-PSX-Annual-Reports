"""Separate SEC HTML acquisition and local PDF rendering pools."""

from __future__ import annotations

import asyncio
import hashlib
import os
import sqlite3
import time
from contextlib import AsyncExitStack
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import httpx

from .catalog import Report
from .discovery import DiscoveryStore
from .engine import (RateGate, Result, RunLock, SEC_REQUEST_INTERVAL_S,
                     StateStore, native_path, validate_and_store)

CHROMIUM_RENDER_ARGS: list[str] = [
    "--no-sandbox",
    "--disable-gpu",
    "--disable-software-rasterizer",
    "--disable-dev-shm-usage",
    "--disable-extensions",
    "--disable-background-networking",
    "--disable-background-timer-throttling",
    "--disable-renderer-backgrounding",
    "--disable-backgrounding-occluded-windows",
    "--disable-breakpad",
    "--disable-component-update",
    "--disable-ipc-flooding-protection",
    "--metrics-recording-only",
    "--renderer-process-limit=16",
    "--js-flags=--max-old-space-size=512",
]


@dataclass(frozen=True, slots=True)
class HtmlJob:
    candidate_id: int
    report: Report


def _jobs(store: DiscoveryStore, limit: int | None,
          company_keys: set[str] | None = None) -> list[HtmlJob]:
    query = """SELECT c.*, u.country, u.exchange, u.lei, u.isin, u.ticker
               FROM candidates c JOIN companies u USING(company_key)
               WHERE c.source='SEC' AND c.source_format='html'
                 AND c.status IN ('DISCOVERED','VERIFIED') AND c.verified=1
                 AND c.report_year IS NOT NULL
                 AND NOT EXISTS (
                   SELECT 1 FROM candidates p WHERE p.company_key=c.company_key
                     AND p.report_year=c.report_year AND p.source_format='pdf'
                     AND p.status IN ('DISCOVERED','VERIFIED') AND p.verified=1)
               ORDER BY c.company_key, c.report_year, c.filing_date, c.id"""
    seen: set[tuple[str, int]] = set()
    jobs: list[HtmlJob] = []
    for item in store.connection.execute(query):
        if company_keys is not None and item["company_key"] not in company_keys:
            continue
        identity = (item["company_key"], item["report_year"])
        if identity in seen:
            continue
        seen.add(identity)
        report = Report.from_row({
            "country": item["country"], "exchange": item["exchange"],
            "lei": item["lei"], "isin": item["isin"], "ticker": item["ticker"],
            "fiscal_year": f"FY{item['report_year']}",
            "report_type": {"10-K": "10K", "20-F": "20F"}.get(item["form_type"], "AR"),
            "language": "EN", "pdf_url": item["source_url"],
            "source_page": "", "verified": "true",
        })
        jobs.append(HtmlJob(item["id"], report))
        if limit and len(jobs) >= limit:
            break
    return jobs


def _html_valid(raw: bytes) -> bool:
    start = raw[:4096].lower()
    return len(raw) >= 300 and (b"<html" in start or b"<!doctype html" in start or b"<body" in start)


async def _fetch_html(client: httpx.AsyncClient, url: str, gate: RateGate, max_bytes: int) -> bytes:
    current = url
    for _ in range(6):
        host = urlsplit(current).hostname or ""
        if not (host == "sec.gov" or host.endswith(".sec.gov")) or urlsplit(current).scheme != "https":
            raise ValueError("SEC HTML redirect left the official SEC host")
        await gate.wait()
        async with client.stream("GET", current, follow_redirects=False) as response:
            if response.status_code in {301, 302, 303, 307, 308}:
                location = response.headers.get("location")
                if not location:
                    raise ValueError("SEC redirect lacks Location")
                current = urljoin(current, location)
                continue
            response.raise_for_status()
            data = bytearray()
            async for chunk in response.aiter_bytes(chunk_size=256 * 1024):
                data.extend(chunk)
                if len(data) > max_bytes:
                    raise ValueError("SEC HTML exceeds size limit")
            raw = bytes(data)
            if not _html_valid(raw):
                raise ValueError("SEC response is not a substantial HTML filing")
            return raw
    raise ValueError("too many SEC redirects")


def _write_html(cache_path: Path, raw: bytes) -> None:
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    staged = cache_path.with_suffix(".html.part")
    try:
        with staged.open("wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(staged, cache_path)
    finally:
        staged.unlink(missing_ok=True)


def record_conversion(connection: sqlite3.Connection, candidate_id: int,
                      cache_path: Path, source_sha: str, source_format: str,
                      result: Result, engine: str, error: str = "") -> None:
    connection.execute(
        """INSERT INTO conversions VALUES (?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(candidate_id) DO UPDATE SET
        source_path=excluded.source_path,
        source_sha256=excluded.source_sha256,
        source_format=excluded.source_format,
        output_relative_path=excluded.output_relative_path,
        pdf_sha256=excluded.pdf_sha256,
        engine=excluded.engine, status=excluded.status, error=excluded.error,
        updated_at=excluded.updated_at""",
        (candidate_id, str(cache_path), source_sha, source_format,
         str(result.report.relative_path), result.sha256, engine, result.status,
         error, datetime.now(timezone.utc).isoformat()),
    )
    connection.commit()


def ensure_conversion_table(connection: sqlite3.Connection) -> None:
    connection.execute(
        """CREATE TABLE IF NOT EXISTS conversions (
            candidate_id INTEGER PRIMARY KEY,
            source_path TEXT NOT NULL, source_sha256 TEXT NOT NULL,
            source_format TEXT NOT NULL, output_relative_path TEXT NOT NULL,
            pdf_sha256 TEXT NOT NULL, engine TEXT NOT NULL,
            status TEXT NOT NULL, error TEXT NOT NULL,
            updated_at TEXT NOT NULL)"""
    )
    connection.commit()


async def render_sec_html(
    state_path: Path, output_root: Path, cache_root: Path, user_agent: str,
    *, chrome_path: Path | None = None, limit: int | None = None,
    network_workers: int = 8, render_workers: int = 6,
    browser_processes: int = 1,
    company_keys: set[str] | None = None,
    max_mib: int = 64,
) -> dict:
    if not user_agent or "@" not in user_agent:
        raise ValueError("SEC_USER_AGENT must contain an organization and contact email")
    if network_workers < 1 or render_workers < 1 or max_mib < 1:
        raise ValueError("workers and max_mib must be positive")
    if not 1 <= browser_processes <= render_workers:
        raise ValueError("browser_processes must be between 1 and render_workers")
    try:
        from playwright.async_api import async_playwright
    except ImportError as exc:
        raise ValueError("install the render extra: pip install -e '.[render]'") from exc
    with RunLock(state_path):
        discovery = DiscoveryStore(state_path)
        try:
            jobs = _jobs(discovery, limit, company_keys)
        finally:
            discovery.close()
        if not jobs:
            raise ValueError("no SEC HTML annual filings were discovered")
        ledger = StateStore(state_path)
        ensure_conversion_table(ledger.connection)
        started = time.monotonic()
        skipped = 0
        failed: list[dict] = []
        rendered = 0
        html_downloaded = 0
        stage_seconds = dict(html=0.0, navigation=0.0, print_pdf=0.0, validation=0.0)
        queue: asyncio.Queue[HtmlJob] = asyncio.Queue()
        render_queue: asyncio.Queue[tuple[HtmlJob, Path, bytes] | None] = asyncio.Queue(maxsize=render_workers * 2)
        for job in jobs:
            target = output_root / job.report.relative_path
            prior = ledger.prior(job.report)
            if os.path.isfile(native_path(target)) and prior and prior[0] == "downloaded" and prior[1] == job.report.pdf_url and prior[2] == os.path.getsize(native_path(target)):
                skipped += 1
            elif os.path.isfile(native_path(target)):
                failed.append({"path": str(job.report.relative_path),
                               "error": "canonical target already exists from another source"})
            else:
                queue.put_nowait(job)
        gate = RateGate(SEC_REQUEST_INTERVAL_S)
        timeout = httpx.Timeout(connect=10, read=90, write=30, pool=30)
        limits = httpx.Limits(max_connections=network_workers, max_keepalive_connections=network_workers)
        try:
            async with httpx.AsyncClient(timeout=timeout, limits=limits,
                                         headers={"User-Agent": user_agent, "Accept-Encoding": "gzip, deflate"}) as client:
                async with async_playwright() as playwright, AsyncExitStack() as browsers:
                    async def block_network(route):
                        if route.request.url.startswith("http://") or route.request.url.startswith("https://"):
                            await route.abort()
                        else:
                            await route.continue_()

                    async def make_context():
                        browser = await playwright.chromium.launch(
                            executable_path=str(chrome_path) if chrome_path else None,
                            headless=True, args=CHROMIUM_RENDER_ARGS,
                        )
                        browsers.push_async_callback(browser.close)
                        ctx = await browser.new_context(java_script_enabled=False)
                        ctx.set_default_timeout(120_000)
                        await ctx.route("**/*", block_network)
                        return ctx

                    contexts = [await make_context() for _ in range(browser_processes)]

                    async def network_worker() -> None:
                        nonlocal html_downloaded
                        while True:
                            try:
                                job = queue.get_nowait()
                            except asyncio.QueueEmpty:
                                return
                            cache_path = cache_root / "sec" / "html" / f"{job.candidate_id}.html"
                            try:
                                phase = time.monotonic()
                                cached = await asyncio.to_thread(cache_path.read_bytes) if cache_path.is_file() else b""
                                if _html_valid(cached):
                                    raw = cached
                                else:
                                    raw = await _fetch_html(client, job.report.pdf_url, gate, max_mib * 1024 * 1024)
                                    await asyncio.to_thread(_write_html, cache_path, raw)
                                    html_downloaded += 1
                                stage_seconds["html"] += time.monotonic() - phase
                                await render_queue.put((job, cache_path, raw))
                            except Exception as exc:
                                failed.append({"path": str(job.report.relative_path), "error": f"HTML fetch: {exc}"})
                            finally:
                                queue.task_done()

                    async def renderer(ctx_idx: int) -> None:
                        nonlocal rendered
                        context = contexts[ctx_idx]
                        page = await context.new_page()
                        docs_handled = 0
                        try:
                            while True:
                                item = await render_queue.get()
                                if item is None:
                                    render_queue.task_done()
                                    return
                                job, cache_path, html = item
                                try:
                                    if docs_handled >= 4:
                                        try:
                                            await page.close()
                                        except Exception:
                                            pass
                                        try:
                                            page = await context.new_page()
                                        except Exception:
                                            context = await make_context()
                                            contexts[ctx_idx] = context
                                            page = await context.new_page()
                                        docs_handled = 0
                                    phase = time.monotonic()
                                    if cache_path.is_file():
                                        await page.goto(cache_path.resolve().as_uri(), wait_until="domcontentloaded", timeout=45_000)
                                    else:
                                        await page.set_content(html.decode("utf-8", errors="replace"), wait_until="domcontentloaded", timeout=45_000)
                                    stage_seconds["navigation"] += time.monotonic() - phase
                                    phase = time.monotonic()
                                    pdf = await asyncio.wait_for(
                                        page.pdf(format="A4", print_background=True, prefer_css_page_size=True),
                                        timeout=90.0,
                                    )
                                    stage_seconds["print_pdf"] += time.monotonic() - phase
                                    phase = time.monotonic()
                                    pages, digest = await asyncio.to_thread(validate_and_store, pdf, job.report, output_root)
                                    stage_seconds["validation"] += time.monotonic() - phase
                                    result = Result(job.report, "downloaded", size=len(pdf), pages=pages, sha256=digest)
                                    ledger.save(result)
                                    record_conversion(ledger.connection, job.candidate_id, cache_path,
                                                      hashlib.sha256(html).hexdigest(), "html",
                                                      result, "chromium")
                                    ledger.connection.execute(
                                        "UPDATE candidates SET status='VERIFIED' WHERE id=?",
                                        (job.candidate_id,),
                                    )
                                    ledger.connection.commit()
                                    try:
                                        cache_path.unlink(missing_ok=True)
                                    except Exception:
                                        pass
                                    rendered += 1
                                    docs_handled += 1
                                except Exception as exc:
                                    failed.append({"path": str(job.report.relative_path), "error": f"PDF render: {exc}"})
                                    docs_handled = 999
                                finally:
                                    render_queue.task_done()
                        finally:
                            try:
                                await page.close()
                            except Exception:
                                pass


                    async def produce() -> None:
                        async with asyncio.TaskGroup() as producers:
                            for _ in range(min(network_workers, queue.qsize())):
                                producers.create_task(network_worker())
                        for _ in range(render_workers):
                            await render_queue.put(None)

                    # If a browser worker fails during startup/cleanup, cancel the
                    # producers too: a bounded queue must never hang without consumers.
                    async with asyncio.TaskGroup() as workers:
                        for index in range(render_workers):
                            workers.create_task(renderer(index % browser_processes))
                        workers.create_task(produce())
        finally:
            ledger.close()
        elapsed = round(time.monotonic() - started, 3)
        return {"selected": len(jobs), "html_downloaded": html_downloaded,
                "pdf_rendered": rendered, "skipped": skipped, "failed": len(failed),
                "elapsed_s": elapsed, "documents_per_second": round(rendered / max(elapsed, 0.001), 2),
                "browser_processes": browser_processes, "render_workers": render_workers,
                "stage_worker_seconds": {key: round(value, 3) for key, value in stage_seconds.items()},
                "validation_scope": "PDF structure and hash; report identity/completeness still require review",
                "external_resources": "blocked; externally referenced images/styles are not acquired",
                "failures": failed}
