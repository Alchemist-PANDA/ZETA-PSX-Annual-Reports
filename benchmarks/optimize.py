"""Accuracy-gated throughput experiments for agent-driven report harvesting.

Fixture mode is local and repeatable. Real mode requires an independently checked
golden CSV so a fast transfer cannot be mistaken for the correct report.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import hashlib
import json
import os
import statistics
import tempfile
import threading
import time
import uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

import fitz

from annual_reports.catalog import Report, load_manifest
from annual_reports.engine import Settings, run, verify_store


GOLDEN_FIELDS = ("relative_path", "sha256", "pages")


def read_golden(path: Path, reports: list[Report]) -> dict[str, tuple[str, int]]:
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        if not reader.fieldnames or not set(GOLDEN_FIELDS).issubset(reader.fieldnames):
            raise ValueError("golden CSV requires relative_path,sha256,pages")
        expected = {}
        for row in reader:
            relative = row["relative_path"].replace("\\", "/")
            digest = row["sha256"].strip().lower()
            pages = int(row["pages"])
            if relative in expected or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest) or pages < 1:
                raise ValueError(f"invalid or duplicate golden row: {relative}")
            expected[relative] = (digest, pages)
    targets = {report.relative_path.as_posix() for report in reports}
    if set(expected) != targets:
        raise ValueError(f"golden must cover every target exactly; missing={len(targets - set(expected))}, extra={len(set(expected) - targets)}")
    return expected


def make_fixture(count: int, image_side: int, delay_ms: float):
    if count < 1 or image_side < 0 or image_side > 2048 or delay_ms < 0:
        raise ValueError("invalid fixture size or delay")
    image = None
    if image_side:
        image = fitz.Pixmap(fitz.csRGB, image_side, image_side,
                            os.urandom(image_side * image_side * 3), False)
    payloads = {}
    for index in range(count):
        with fitz.open() as doc:
            page = doc.new_page()
            page.insert_text((72, 72), f"Fixture report {index} FY2024")
            if image is not None:
                page.insert_image(fitz.Rect(72, 100, 500, 528), pixmap=image)
            payloads[f"/{index}.pdf"] = doc.tobytes()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            payload = payloads.get(urlsplit(self.path).path)
            if payload is None:
                self.send_error(404)
                return
            time.sleep(delay_ms / 1000)
            self.send_response(200)
            self.send_header("Content-Type", "application/pdf")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    reports = [Report.from_row({
        "country": "USA", "exchange": "XNAS", "lei": "INR2EJN1ERAN0W5ZP974",
        "isin": "US5949181045", "ticker": f"F{i}", "fiscal_year": "FY2024",
        "report_type": "SR", "language": "EN", "pdf_url": f"{base}/{i}.pdf",
        "source_page": "", "verified": "true",
    }, allow_http=True) for i in range(count)]
    golden = {r.relative_path.as_posix(): (hashlib.sha256(payloads[f"/{i}.pdf"]).hexdigest(), 1)
              for i, r in enumerate(reports)}
    return server, thread, reports, golden


def check_outputs(root: Path, reports: list[Report], golden: dict[str, tuple[str, int]],
                  summary: dict) -> list[str]:
    errors = []
    if summary["failed"] or summary["downloaded"] != len(reports) or summary["skipped"]:
        errors.append("download count, failure count or fresh-run count differs from manifest")
    verified = verify_store(root / "data", root / "state.sqlite3")
    errors.extend(verified["errors"])
    if verified["checked"] != len(reports):
        errors.append(f"ledger verified {verified['checked']} of {len(reports)} targets")
    expected_paths = set(golden)
    actual_paths = {p.relative_to(root / "data").as_posix() for p in (root / "data").rglob("*.pdf")}
    if actual_paths != expected_paths:
        errors.append(f"output set differs: missing={len(expected_paths - actual_paths)}, extra={len(actual_paths - expected_paths)}")
    for relative, (digest, pages) in golden.items():
        path = root / "data" / relative
        if not path.is_file():
            continue
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != digest:
            errors.append(f"SHA-256 mismatch: {relative}")
        try:
            with fitz.open(stream=raw, filetype="pdf") as document:
                if document.page_count != pages:
                    errors.append(f"page count mismatch: {relative}")
        except fitz.FileDataError:
            errors.append(f"invalid PDF: {relative}")
    if list((root / "data").rglob("*.part")):
        errors.append("orphaned .part files")
    return errors


def parse_configs(value: str) -> list[tuple[int, int]]:
    configs = []
    for item in value.split(","):
        try:
            workers, per_host = (int(part) for part in item.split(":"))
        except (ValueError, TypeError):
            raise ValueError("configs must look like 16:2,32:2") from None
        if not 1 <= workers <= 128 or not 1 <= per_host <= min(workers, 5):
            raise ValueError("workers must be 1-128 and per-host 1-5, no greater than workers")
        if (workers, per_host) not in configs:
            configs.append((workers, per_host))
    return configs


def nearby_configs(champion: dict | None, mode: str) -> list[tuple[int, int]]:
    if not champion:
        return parse_configs("16:2,32:2,64:2" if mode == "fixture" else "16:2,32:2")
    workers, per_host = champion["workers"], champion["per_host"]
    cap = 5 if mode == "fixture" else 2
    candidates = [(workers, per_host), (max(1, workers // 2), per_host),
                  (min(128, workers * 2), per_host),
                  (workers, max(1, per_host - 1)), (workers, min(cap, per_host + 1))]
    return list(dict.fromkeys(c for c in candidates if c[1] <= c[0]))


def execute(reports: list[Report], golden: dict[str, tuple[str, int]],
            config: tuple[int, int], allow_http: bool) -> dict:
    with tempfile.TemporaryDirectory(prefix="ar-speed-") as folder:
        root = Path(folder)
        settings = Settings(root / "data", root / "state.sqlite3", workers=config[0],
                            per_host=config[1], allow_http=allow_http,
                            sec_user_agent=os.getenv("SEC_USER_AGENT", ""),
                            companies_house_key=os.getenv("COMPANIES_HOUSE_API_KEY", ""))
        if os.name == "nt":
            with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
                summary = runner.run(run(reports, settings))
        else:
            summary = asyncio.run(run(reports, settings))
        errors = check_outputs(root, reports, golden, summary)
        return {
            "workers": config[0], "per_host": config[1],
            "elapsed_s": summary["elapsed_s"], "pdfs_per_second": summary["pdfs_per_second"],
            "mib_per_second": summary["mib_per_second"], "failed": summary["failed"],
            "http_429_attempts": summary["http_429_attempts"],
            "http_5xx_attempts": summary["http_5xx_attempts"],
            "accuracy_ok": not errors, "errors": errors[:20],
        }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Accuracy-gated speed experiments")
    parser.add_argument("mode", choices=("fixture", "real"))
    parser.add_argument("--manifest", type=Path, help="required in real mode")
    parser.add_argument("--golden", type=Path, help="independently verified CSV, required in real mode")
    parser.add_argument("--configs", help="comma-separated workers:per-host candidates")
    parser.add_argument("--repeats", type=int, help="fixture default 2; real default 1")
    parser.add_argument("--count", type=int, default=100)
    parser.add_argument("--image-side", type=int, default=256)
    parser.add_argument("--delay-ms", type=float, default=10)
    parser.add_argument("--history", type=Path, default=Path("benchmarks/results.jsonl"))
    parser.add_argument("--champion", type=Path, default=Path("benchmarks/champions.json"))
    args = parser.parse_args(argv)
    if args.repeats is not None and not 1 <= args.repeats <= 5:
        parser.error("repeats must be 1-5")
    server = thread = None
    if args.mode == "real":
        if not args.manifest or not args.golden:
            parser.error("real mode requires --manifest and independently verified --golden")
        reports = load_manifest(args.manifest)
        if not reports or any(not r.verified for r in reports):
            parser.error("real manifest must have verified reports")
        golden = read_golden(args.golden, reports)
        fingerprint = hashlib.sha256(args.manifest.read_bytes() + args.golden.read_bytes()).hexdigest()[:16]
    else:
        server, thread, reports, golden = make_fixture(args.count, args.image_side, args.delay_ms)
        fingerprint = f"fixture-{args.count}-{args.image_side}-{args.delay_ms}"
    champions = json.loads(args.champion.read_text(encoding="utf-8")) if args.champion.is_file() else {}
    previous = champions.get(fingerprint)
    configs = parse_configs(args.configs) if args.configs else nearby_configs(previous, args.mode)
    repeats = args.repeats or 2
    trials = []
    try:
        for config in configs:
            for repeat in range(repeats):
                trial = execute(reports, golden, config, args.mode == "fixture")
                trial.update(mode=args.mode, fingerprint=fingerprint, repeat=repeat + 1,
                             timestamp=datetime.now(timezone.utc).isoformat(),
                             reports=len(reports))
                trials.append(trial)
                print(json.dumps(trial), flush=True)
    finally:
        if server is not None:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
    args.history.parent.mkdir(parents=True, exist_ok=True)
    with args.history.open("a", encoding="utf-8") as stream:
        for trial in trials:
            stream.write(json.dumps(trial) + "\n")
    ranked = []
    for config in configs:
        group = [t for t in trials if (t["workers"], t["per_host"]) == config]
        if all(t["accuracy_ok"] and t["http_429_attempts"] == 0 and t["http_5xx_attempts"] == 0 for t in group):
            ranked.append((statistics.median(t["elapsed_s"] for t in group), config))
    winner = min(ranked) if ranked else None
    promoted = False
    if winner and repeats >= 2 and (previous is None or winner[0] <= previous["median_elapsed_s"] * 0.95):
        champions[fingerprint] = {"workers": winner[1][0], "per_host": winner[1][1],
                                  "median_elapsed_s": winner[0],
                                  "updated_at": datetime.now(timezone.utc).isoformat()}
        args.champion.parent.mkdir(parents=True, exist_ok=True)
        staged = args.champion.with_name(f"{args.champion.name}.{uuid.uuid4().hex}.part")
        staged.write_text(json.dumps(champions, indent=2) + "\n", encoding="utf-8")
        os.replace(staged, args.champion)
        promoted = True
    recommendation = {"mode": args.mode, "fingerprint": fingerprint,
                      "accuracy_gate": "all reports must match independent hashes and page counts",
                      "best_valid_config": {"workers": winner[1][0], "per_host": winner[1][1],
                                            "median_elapsed_s": winner[0]} if winner else None,
                      "valid_configs": len(ranked), "tested_configs": len(configs),
                      "previous_champion": previous, "promoted": promoted,
                      "next_candidates": nearby_configs(champions.get(fingerprint), args.mode),
                      "note": "Real-source results depend on server and network conditions; repeat before changing defaults."}
    print(json.dumps(recommendation, indent=2))
    return 0 if winner else 1


if __name__ == "__main__":
    raise SystemExit(main())
