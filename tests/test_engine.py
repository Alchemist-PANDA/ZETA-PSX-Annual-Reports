import asyncio
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import fitz
import pytest

from annual_reports.catalog import Report
from annual_reports.engine import RunLock, Settings, StateStore, native_path, run, verify_store


def run_coro(coro):
    if os.name == "nt":
        with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
            return runner.run(coro)
    return asyncio.run(coro)


def make_pdf() -> bytes:
    with fitz.open() as document:
        document.new_page().insert_text((72, 72), "Annual report FY2024")
        return document.tobytes()


class PdfHandler(BaseHTTPRequestHandler):
    payload = make_pdf()
    active = 0
    peak = 0
    lock = threading.Lock()
    calls = {}

    def do_GET(self):
        with self.lock:
            type(self).active += 1
            type(self).peak = max(type(self).peak, type(self).active)
            type(self).calls[self.path] = type(self).calls.get(self.path, 0) + 1
            call = type(self).calls[self.path]
        try:
            time.sleep(0.01)
            if self.path == "/retry.pdf" and call == 1:
                self.send_response(503)
                self.end_headers()
                return
            if self.path == "/redirect.pdf":
                self.send_response(302)
                self.send_header("Location", "/0.pdf")
                self.end_headers()
                return
            payload = b"<html>not pdf</html>" if self.path == "/bad.pdf" else self.payload
            self.send_response(200)
            self.send_header("Content-Type", "application/pdf")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
        finally:
            with self.lock:
                type(self).active -= 1

    def log_message(self, *_):
        pass


def report(url: str, ticker: str) -> Report:
    return Report.from_row({
        "country": "USA", "exchange": "XNAS", "lei": "2138007ZFQYRUSLU3J98",
        "isin": "US0000000001", "ticker": ticker, "fiscal_year": "FY2024",
        "report_type": "AR", "language": "EN", "pdf_url": url,
        "source_page": "", "verified": "true",
    }, allow_http=True)


def test_run_lock_excludes_second_writer(tmp_path: Path):
    first = RunLock(tmp_path / "state.sqlite3")
    second = RunLock(tmp_path / "state.sqlite3")
    first.acquire()
    try:
        with pytest.raises(ValueError, match="another harvest"):
            second.acquire()
    finally:
        first.release()


def test_batch_retry_resume_and_integrity(tmp_path: Path):
    PdfHandler.active = PdfHandler.peak = 0
    PdfHandler.calls = {}
    server = ThreadingHTTPServer(("127.0.0.1", 0), PdfHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = f"http://127.0.0.1:{server.server_port}"
        reports = [report(f"{base}/{i}.pdf", f"T{i}") for i in range(100)]
        reports.append(report(f"{base}/retry.pdf", "RETRY"))
        reports.append(report(f"{base}/bad.pdf", "BAD"))
        reports.append(report(f"{base}/redirect.pdf", "REDIRECT"))
        settings = Settings(tmp_path / "data", tmp_path / "state.sqlite3", workers=24,
                            per_host=2, attempts=2, allow_http=True)
        settings.output_root.mkdir()
        (settings.output_root / ("old.pdf." + "a" * 32 + ".part")).write_bytes(b"interrupted")
        summary = run_coro(run(reports, settings))
        assert summary["downloaded"] == 102, summary["failures"][:2]
        assert summary["failed"] == 1
        assert summary["orphan_parts_removed"] == 1
        assert "not a PDF" in summary["failures"][0]["error"]
        assert PdfHandler.calls["/retry.pdf"] == 2
        assert PdfHandler.calls["/bad.pdf"] == 1
        assert PdfHandler.peak <= 2
        assert verify_store(settings.output_root, settings.state_path) == {
            "checked": 102, "errors": [], "ok": True,
        }
        assert not list(settings.output_root.rglob("*.part"))
        second = run_coro(run([r for r in reports if r.ticker != "BAD"], settings))
        assert second["skipped"] == 102
        assert second["downloaded"] == 0
        interrupted = report(f"{base}/never-requested.pdf", "RECOVER")
        target = settings.output_root / interrupted.relative_path
        os.makedirs(native_path(target.parent), exist_ok=True)
        with open(native_path(target), "wb") as stream:
            stream.write(PdfHandler.payload)
        state = StateStore(settings.state_path)
        state.schedule([interrupted])
        state.close()
        recovery = run_coro(run([interrupted], settings))
        assert recovery["recovered"] == 1
        assert recovery["downloaded"] == 0
        assert "/never-requested.pdf" not in PdfHandler.calls
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
