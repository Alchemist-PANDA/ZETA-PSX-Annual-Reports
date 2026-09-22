"""Synthetic localhost throughput check; this is not an internet speed guarantee."""

import argparse
import asyncio
import json
import os
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import fitz

from annual_reports.catalog import Report
from annual_reports.engine import Settings, run


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--count", type=int, default=100)
    parser.add_argument("--delay-ms", type=float, default=10)
    parser.add_argument("--image-side", type=int, default=0,
                        help="embed random RGB image, e.g. 1024 makes a roughly 3 MiB PDF")
    args = parser.parse_args()
    with fitz.open() as document:
        page = document.new_page(width=1024, height=1024)
        if args.image_side:
            side = args.image_side
            pixmap = fitz.Pixmap(fitz.csRGB, side, side, os.urandom(side * side * 3), False)
            page.insert_image(page.rect, pixmap=pixmap)
        else:
            page.insert_text((72, 72), "Synthetic annual report")
        pdf = document.tobytes()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            time.sleep(args.delay_ms / 1000)
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
        base = f"http://127.0.0.1:{server.server_port}"
        reports = [Report.from_row({
            "country": "USA", "exchange": "XNAS", "lei": "2138007ZFQYRUSLU3J98",
            "isin": "US0000000001", "ticker": f"T{i}", "fiscal_year": "FY2024",
            "report_type": "AR", "language": "EN", "pdf_url": f"{base}/{i}.pdf",
            "source_page": "", "verified": "true",
        }, allow_http=True) for i in range(args.count)]
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            settings = Settings(root / "data", root / "state.sqlite3", allow_http=True)
            if os.name == "nt":
                with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
                    result = runner.run(run(reports, settings))
            else:
                result = asyncio.run(run(reports, settings))
            print(json.dumps({**result, "pdf_bytes_each": len(pdf),
                              "note": "localhost, synthetic PDFs, one host"}, indent=2))
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


if __name__ == "__main__":
    main()
