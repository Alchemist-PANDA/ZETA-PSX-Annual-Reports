import sqlite3
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import fitz

from annual_reports.discovery import Company, DiscoveryStore


def test_live_benchmark_command_with_local_pdf(tmp_path: Path):
    with fitz.open() as document:
        document.new_page().insert_text((72, 72), "Example annual report 2024")
        pdf = document.tobytes()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "application/pdf")
            self.send_header("Content-Length", str(len(pdf)))
            self.end_headers()
            self.wfile.write(pdf)

        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        state_path = tmp_path / "state.sqlite3"
        store = DiscoveryStore(state_path)
        try:
            company = Company("USA", "Example Inc", "XNAS", "2138007ZFQYRUSLU3J98",
                              "US0000000001", "EXM", "320193")
            store.add_universe([company], [2024])
            store.upsert_candidate(company_key=company.key, report_year=2024,
                                   source="SEC", source_record_id="fixture",
                                   source_url=f"http://127.0.0.1:{server.server_port}/annual.pdf",
                                   source_format="pdf", form_type="10-K",
                                   status="DISCOVERED", verified=True)
            store.commit()
        finally:
            store.close()
        result = subprocess.run(
            [sys.executable, "-m", "annual_reports.cli", "benchmark",
             "--state", str(state_path), "--output-root", str(tmp_path / "data"),
             "--limit", "1", "--allow-http"],
            cwd=tmp_path, capture_output=True, text=True, timeout=30,
        )
        assert result.returncode == 0, result.stderr + result.stdout
        with sqlite3.connect(state_path) as connection:
            count = connection.execute("SELECT COUNT(*) FROM benchmark_runs").fetchone()[0]
        assert count == 1
        assert (tmp_path / "benchmark.csv").is_file()
        assert len(list((tmp_path / "data").rglob("*.pdf"))) == 1
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
