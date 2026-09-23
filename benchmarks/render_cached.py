"""Compare Chromium configurations on identical cached real SEC filings.

Offline rendering benchmark, NOT an internet download or semantic accuracy test.
Each trial has fresh output/state. Production state and cache are read only.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import shutil
import sqlite3
import statistics
import time
import uuid
from pathlib import Path
from unittest.mock import patch

import fitz

from annual_reports.conversion import _jobs, render_sec_html
from annual_reports.discovery import DiscoveryStore
from annual_reports.engine import native_path, verify_store


def backup(source: Path, target: Path) -> None:
    with sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True) as src:
        with sqlite3.connect(target) as dst:
            src.backup(dst)


def fingerprint(path: Path) -> dict:
    """Every page: extracted text and low-resolution appearance, ignoring PDF timestamps."""
    text = hashlib.sha256()
    visual = hashlib.sha256()
    with fitz.open(native_path(path)) as doc:
        for page in doc:
            text.update(page.get_text().encode("utf-8"))
            text.update(b"\0")
            pix = page.get_pixmap(matrix=fitz.Matrix(0.5, 0.5), alpha=False)
            visual.update(str((tuple(page.rect), pix.width, pix.height)).encode())
            visual.update(pix.samples)
        return {"pages": len(doc), "text_sha256": text.hexdigest(),
                "visual_36dpi_sha256": visual.hexdigest()}


async def no_network(*args, **kwargs):
    raise RuntimeError("Offline benchmark: missing/invalid cached HTML; network disabled")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--cache", type=Path, default=Path("cache"))
    parser.add_argument("--chrome", type=Path, default=Path("C:/Program Files/Google/Chrome/Application/chrome.exe"))
    parser.add_argument("--count", type=int, default=24)
    parser.add_argument("--repeats", type=int, default=2)
    parser.add_argument("--configs", default="1:6,3:6,6:6", help="browser processes:total page workers")
    parser.add_argument("--target-rate", type=float, default=3.0)
    parser.add_argument("--results", type=Path, default=Path("benchmarks/render-runs"))
    args = parser.parse_args()
    configs = [tuple(map(int, part.split(":"))) for part in args.configs.split(",")]
    if args.count < 2 or args.repeats < 2 or args.target_rate <= 0:
        parser.error("count/repeats must be >=2 and target-rate must be positive")
    if any(len(c) != 2 or not 1 <= c[0] <= c[1] <= 32 for c in configs):
        parser.error("configs require 1 <= browsers <= workers <= 32")
    if len(set(configs)) != len(configs):
        parser.error("duplicate configurations")
    root = args.results / (time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6])
    root.mkdir(parents=True)
    seed = root / "seed.sqlite3"
    backup(args.state, seed)
    store = DiscoveryStore(seed)
    try:
        available = []
        for job in _jobs(store, None):
            path = args.cache / "sec/html" / f"{job.candidate_id}.html"
            if path.is_file():
                available.append((path.stat().st_size, job.candidate_id, job, path))
        available.sort(key=lambda item: item[:2])
        if len(available) < args.count:
            raise ValueError(f"only {len(available)} cached eligible filings; requested {args.count}")
        # Evenly sample size quantiles, including the smallest and largest filings.
        sample = [available[round(i * (len(available) - 1) / (args.count - 1))]
                  for i in range(args.count)]
        ids = [item[1] for item in sample]
        store.connection.execute(f"DELETE FROM candidates WHERE id NOT IN ({','.join('?' for _ in ids)})", ids)
        for table in ("reports", "conversions"):
            exists = store.connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()
            if exists:
                store.connection.execute(f"DELETE FROM {table}")
        store.commit()
    finally:
        store.close()
    cached = root / "cache/sec/html"
    cached.mkdir(parents=True)
    manifest = []
    for size, candidate_id, job, path in sample:
        shutil.copyfile(path, cached / path.name)
        manifest.append({"candidate_id": candidate_id, "relative_path": job.report.relative_path.as_posix(),
                         "url": job.report.pdf_url, "html_bytes": size,
                         "html_sha256": hashlib.sha256((cached / path.name).read_bytes()).hexdigest()})
    (root / "sample.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(json.dumps({"results": str(root.resolve()), "sample": len(sample),
                      "html_mib": round(sum(x[0] for x in sample) / 2**20, 2)}), flush=True)
    reference = None
    trials = []
    for repeat in range(args.repeats):
        # Reverse order on alternate rounds to reduce cache/thermal order bias.
        for browsers, workers in (configs if repeat % 2 == 0 else configs[::-1]):
            trial = root / f"b{browsers}-w{workers}-r{repeat + 1}"
            trial.mkdir()
            state = trial / "state.sqlite3"
            backup(seed, state)
            started = time.monotonic()
            with patch("annual_reports.conversion._fetch_html", no_network):
                result = asyncio.run(render_sec_html(
                    state, trial / "data", root / "cache", "Offline benchmark offline@example.org",
                    chrome_path=args.chrome, render_workers=workers, browser_processes=browsers))
            errors = list(result["failures"])
            print(json.dumps({"phase": "render_complete", "trial": trial.name, **result}), flush=True)
            verified = verify_store(trial / "data", state)
            errors.extend(verified["errors"])
            if result["pdf_rendered"] != len(sample) or result["skipped"] or result["html_downloaded"] or verified["checked"] != len(sample):
                errors.append("incorrect fresh-render or verified count")
            actual = {}
            for item in manifest:
                path = trial / "data" / item["relative_path"]
                if not path.is_file():
                    errors.append(f"missing {item['relative_path']}")
                    continue
                actual[item["relative_path"]] = fingerprint(path)
            if reference is None:
                if errors:
                    raise RuntimeError(f"baseline failed: {errors}")
                reference = actual
                (root / "reference.json").write_text(json.dumps(reference, indent=2), encoding="utf-8")
            elif actual != reference:
                errors.append("page count, extracted text or page appearance differs from baseline")
            wall = time.monotonic() - started
            row = {**result, "repeat": repeat + 1, "regression_checks_passed": not errors,
                   "audit_errors": errors, "including_regression_audit_s": round(wall, 3),
                   "including_regression_audit_docs_s": round(len(sample) / wall, 3),
                   "target_met": not errors and len(sample) / result["elapsed_s"] >= args.target_rate}
            trials.append(row)
            with (root / "trials.jsonl").open("a", encoding="utf-8") as log:
                log.write(json.dumps(row) + "\n")
            print(json.dumps(row), flush=True)
    medians = []
    for browsers, workers in configs:
        group = [r for r in trials if (r["browser_processes"], r["render_workers"]) == (browsers, workers)]
        seconds = statistics.median(r["elapsed_s"] for r in group)
        valid = all(r["regression_checks_passed"] for r in group)
        medians.append({"browsers": browsers, "workers": workers, "median_seconds": seconds,
                        "docs_s": round(len(sample) / seconds, 3), "valid": valid})
    baseline = medians[0]
    valid = [r for r in medians if r["valid"]]
    fastest = min(valid, key=lambda r: r["median_seconds"]) if valid else None
    promoted = bool(fastest and fastest["median_seconds"] <= baseline["median_seconds"] * .95)
    summary = {"scope": "cached SEC HTML rendering; no network; regression checks are not semantic certification",
               "count": len(sample), "repeats": args.repeats, "configs": medians,
               "improved_at_least_5_percent": promoted, "recommended": fastest if promoted else baseline,
               "target_rate": args.target_rate,
               "target_met": bool(fastest and fastest["docs_s"] >= args.target_rate)}
    (root / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2), flush=True)
    if not all(r["regression_checks_passed"] for r in trials):
        return 1
    return 0 if summary["target_met"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
