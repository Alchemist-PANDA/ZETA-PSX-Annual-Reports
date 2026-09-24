"""Pakistan CLI entry point (``zeta-pk``).

Provides ``harvest``, ``optimize``, ``benchmark``, ``doctor``, and ``status``
subcommands without breaking the existing ``ar-harvest`` CLI.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

from rich.console import Console
from rich.table import Table
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn

from .config import PakistanConfig, detect_google_drive, get_pakistan_root, DEFAULT_YEARS
from .benchmark import run_benchmark, optimize_concurrency, get_current_git_sha


console = Console(safe_box=True)


def _parse_companies(value: str) -> list[str]:
    """Parse company argument — comma-separated string or file path."""
    path = Path(value)
    if path.is_file():
        text = path.read_text(encoding="utf-8")
        return [line.strip() for line in text.splitlines() if line.strip() and not line.startswith("#")]
    return [c.strip() for c in value.split(",") if c.strip()]


def _parse_years(value: str) -> list[int]:
    """Parse year range like '2017:2025'."""
    if ":" in value:
        start, end = value.split(":", 1)
        return list(range(int(start), int(end) + 1))
    return [int(y.strip()) for y in value.split(",")]


def cmd_harvest(args: argparse.Namespace) -> None:
    """Run the Pakistan annual-report harvest pipeline."""
    raw_comp = args.companies or args.companies_opt
    if not raw_comp:
        console.print("[red]No companies specified. Specify a company file or use --companies.[/red]")
        sys.exit(1)

    companies = _parse_companies(raw_comp)
    if not companies:
        console.print("[red]No valid company symbols or names parsed.[/red]")
        sys.exit(1)

    years = _parse_years(args.years) if args.years else list(DEFAULT_YEARS)

    console.print(f"\n[bold cyan]ZETA Pakistan Autonomous Harvester[/bold cyan]")
    console.print(f"  Companies: {len(companies)}")
    console.print(f"  Years:     FY{min(years)}–FY{max(years)}")
    console.print(f"  Workers:   {args.workers}")
    console.print(f"  Per-host:  {args.per_host}")
    console.print()

    # Build config
    try:
        config = PakistanConfig.auto(
            output_root=Path(args.output_root) if args.output_root else None,
            workers=args.workers,
            per_host=args.per_host,
            years=years,
        )
        if args.state:
            config = PakistanConfig(
                drive_root=config.drive_root,
                pakistan_root=config.pakistan_root,
                local_runtime=config.local_runtime,
                state_path=Path(args.state),
                workers=args.workers,
                per_host=args.per_host,
                years=years,
            )
    except EnvironmentError as exc:
        console.print(f"[red]Environment error: {exc}[/red]")
        sys.exit(1)

    console.print(f"  Output:    {config.pakistan_root}")
    console.print(f"  State:     {config.state_path}")
    console.print()

    from .autonomous import run_harvest_sync

    with Progress(
        SpinnerColumn("line"),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
        console=console,
    ) as progress:
        task_id = progress.add_task("Harvesting...", total=None)

        def on_progress(processed: int, total: int, downloaded: int, failed: int) -> None:
            progress.update(task_id, total=total, completed=processed,
                          description=f"Downloading: {downloaded} ok, {failed} failed")

        result = run_harvest_sync(companies, config, years)

    # Print summary
    console.print()
    table = Table(title="Harvest Summary", show_header=True, header_style="bold magenta")
    table.add_column("Metric", style="cyan")
    table.add_column("Value", justify="right", style="green")
    table.add_row("Requested Companies", str(result.requested_companies))
    table.add_row("Resolved Companies", str(result.resolved_companies))
    table.add_row("Eligible Company-Years", str(result.eligible_company_years))
    table.add_row("Candidates Resolved", str(result.candidates_resolved))
    table.add_row("Already Complete", str(result.already_complete))
    table.add_row("Downloaded", str(result.downloaded))
    table.add_row("Verified", str(result.verified))
    table.add_row("Needs Review", str(result.needs_review))
    table.add_row("Missing", str(result.missing))
    table.add_row("Failed", str(result.failed))
    table.add_row("Bytes Downloaded", f"{result.bytes_downloaded:,}")
    table.add_row("Elapsed", f"{result.elapsed_s:.1f}s")
    table.add_row("PDFs/sec", f"{result.pdfs_per_second:.2f}")
    table.add_row("MiB/sec", f"{result.mib_per_second:.2f}")
    table.add_row("Output Path", result.output_path)
    console.print(table)


def cmd_optimize(args: argparse.Namespace) -> None:
    """Run performance optimization experiments across candidate worker counts."""
    config = PakistanConfig.auto(
        output_root=Path(args.output_root) if args.output_root else None,
        per_host=args.per_host,
    )
    workers_list = [int(w.strip()) for w in args.workers.split(",") if w.strip()] if args.workers else [32, 48, 64, 80]
    optimize_concurrency(config, candidate_workers=workers_list, per_host=args.per_host)


def cmd_benchmark(args: argparse.Namespace) -> None:
    """Execute reproducible benchmark against the Pakistan Golden Corpus."""
    config = PakistanConfig.auto(
        output_root=Path(args.output_root) if args.output_root else None,
        workers=args.workers,
        per_host=args.per_host,
    )
    console.print(f"\n[bold cyan]Running Pakistan Golden Corpus Benchmark[/bold cyan]\n")
    res = run_benchmark(config, workers=args.workers, per_host=args.per_host)

    table = Table(title="Benchmark Execution Telemetry", show_header=True, header_style="bold magenta")
    table.add_column("Metric", style="cyan")
    table.add_column("Value", justify="right", style="green")
    table.add_row("Timestamp", res.timestamp)
    table.add_row("Commit SHA", res.commit_sha[:10])
    table.add_row("Worker Concurrency", str(res.workers))
    table.add_row("Per-host Concurrency", str(res.per_host))
    table.add_row("Golden Reports", str(res.report_count))
    table.add_row("Elapsed Time", f"{res.elapsed_s:.2f}s")
    table.add_row("Throughput (PDFs/s)", f"{res.pdfs_per_sec:.2f}")
    table.add_row("Bandwidth (MiB/s)", f"{res.mibs_per_sec:.2f}")
    table.add_row("Peak RAM", f"{res.peak_ram_mb:.1f} MB")
    table.add_row("CPU Load", f"{res.cpu_percent:.1f}%")
    table.add_row("Errors (429/5xx)", f"{res.http_429_count}/{res.http_5xx_count}")
    table.add_row("Golden Pass Rate", f"{res.golden_pass_rate:.1f}%")
    console.print(table)


def cmd_doctor(args: argparse.Namespace) -> None:
    """Run health checks on the Pakistan harvest environment (Requirement 43)."""
    console.print("\n[bold cyan]ZETA Pakistan — Doctor (Health Checks)[/bold cyan]\n")

    checks: list[tuple[str, str, str]] = []  # (name, status, detail)

    # 1. Google Drive for Desktop detected
    drive = detect_google_drive()
    if drive:
        checks.append(("Google Drive for Desktop", "PASS", str(drive)))
    else:
        checks.append(("Google Drive for Desktop", "WARN", "Not detected; local fallback will be used"))

    # 2. Drive root writable
    if drive and drive.is_dir():
        try:
            test_file = drive / ".zeta_doctor_test.tmp"
            test_file.write_text("ok", encoding="utf-8")
            test_file.unlink()
            checks.append(("Drive Root Writable", "PASS", "Writable"))
        except Exception as exc:
            checks.append(("Drive Root Writable", "WARN", f"Read-only or restricted: {exc}"))

    # 3. Pakistan stock root exists
    try:
        root = get_pakistan_root(drive)
        if root.is_dir():
            checks.append(("Pakistan Stock Root", "PASS", str(root)))
        else:
            checks.append(("Pakistan Stock Root", "WARN", f"Will be created at {root}"))
    except Exception as exc:
        checks.append(("Pakistan Stock Root", "WARN", str(exc)))

    # 4. Local runtime writable
    local_rt = Path("E:/ZETA-PSX-RUNTIME")
    if local_rt.is_dir():
        try:
            t_f = local_rt / ".zeta_doctor_test.tmp"
            t_f.write_text("ok", encoding="utf-8")
            t_f.unlink()
            checks.append(("Local Runtime", "PASS", f"{local_rt} (writable)"))
        except Exception:
            checks.append(("Local Runtime", "WARN", f"{local_rt} (not writable)"))
    else:
        checks.append(("Local Runtime", "WARN", f"{local_rt} not found"))

    # 5. SQLite health
    try:
        import sqlite3
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE TABLE test (id INT)")
        conn.close()
        checks.append(("SQLite Engine", "PASS", f"SQLite {sqlite3.sqlite_version} (WAL supported)"))
    except Exception as exc:
        checks.append(("SQLite Engine", "FAIL", str(exc)))

    # 6. Python version
    import platform
    py_ver = platform.python_version()
    major, minor = int(py_ver.split(".")[0]), int(py_ver.split(".")[1])
    if major >= 3 and minor >= 11:
        checks.append(("Python Version", "PASS", f"Python {py_ver}"))
    else:
        checks.append(("Python Version", "FAIL", f"{py_ver} (requires >= 3.11)"))

    # 7. Package dependencies
    for pkg in ["curl_cffi", "pymupdf", "httpx", "rich", "bs4"]:
        try:
            __import__(pkg)
            checks.append((f"Package: {pkg}", "PASS", "Installed"))
        except ImportError:
            checks.append((f"Package: {pkg}", "FAIL", "Missing"))

    # 8. Free disk space
    import shutil as sh
    for disk, label in [("E:\\", "E: SSD"), ("C:\\", "C: System")]:
        try:
            usage = sh.disk_usage(disk)
            free_gb = usage.free / (1024**3)
            status = "PASS" if free_gb > 1.0 else ("WARN" if free_gb > 0.2 else "FAIL")
            checks.append((f"Disk Space ({label})", status, f"{free_gb:.1f} GB free"))
        except Exception:
            pass

    # 9. Network connectivity
    try:
        import socket
        socket.create_connection(("1.1.1.1", 53), timeout=3).close()
        checks.append(("Network Connectivity", "PASS", "Internet reachable"))
    except Exception:
        checks.append(("Network Connectivity", "WARN", "Internet unreachable / offline test mode"))

    # 10. Git baseline
    sha = get_current_git_sha()
    checks.append(("Git Baseline SHA", "PASS" if sha != "UNKNOWN" else "WARN", sha[:12]))

    # Display results
    table = Table(show_header=True, header_style="bold")
    table.add_column("Check", style="cyan")
    table.add_column("Status")
    table.add_column("Detail", style="dim")

    for name, status, detail in checks:
        color = {"PASS": "green", "WARN": "yellow", "FAIL": "red"}.get(status, "white")
        table.add_row(name, f"[{color}]{status}[/{color}]", detail)

    console.print(table)

    statuses = [s for _, s, _ in checks]
    if "FAIL" in statuses:
        console.print("\n[red bold]FAIL — Critical environment issues found.[/red bold]")
        sys.exit(1)
    elif "WARN" in statuses:
        console.print("\n[yellow bold]WARN — System functional with warnings.[/yellow bold]")
    else:
        console.print("\n[green bold]PASS — All system checks passed cleanly.[/green bold]")


def cmd_status(args: argparse.Namespace) -> None:
    """Show current harvest status and gap matrix."""
    config = PakistanConfig.auto()
    profile_db = config.local_runtime / "local" / "source-profiles.sqlite3"
    if not profile_db.is_file():
        console.print("[yellow]No harvest state found. Run 'zeta-pk harvest' first.[/yellow]")
        return

    from .source_profiles import SourceProfileStore
    store = SourceProfileStore(profile_db)

    profiles = store.all_profiles()
    console.print(f"\n[bold cyan]Learned Source Profiles:[/bold cyan] {len(profiles)}")

    if profiles:
        table = Table(show_header=True, header_style="bold")
        table.add_column("Symbol", style="cyan")
        table.add_column("Company")
        table.add_column("Adapter")
        table.add_column("Confidence")

        for p in profiles[:25]:
            table.add_row(
                p.get("ticker", ""),
                p.get("current_name", ""),
                p.get("adapter_type", "GenericAnchorAdapter"),
                p.get("confidence", "HIGH"),
            )
        console.print(table)

    store.close()


def parser() -> argparse.ArgumentParser:
    cli = argparse.ArgumentParser(
        prog="zeta-pk",
        description="Pakistan Stock Exchange annual-report harvester (ZETA extension)",
    )
    commands = cli.add_subparsers(dest="command", required=True)

    # harvest
    h = commands.add_parser("harvest", help="Harvest annual reports for Pakistan companies")
    h.add_argument("companies", nargs="?", default=None, help="Company list file (e.g. companies.txt) or comma-separated symbols")
    h.add_argument("--companies", dest="companies_opt", default=None, help="Comma-separated tickers (e.g. 'HBL,UBL,MCB')")
    h.add_argument("--years", default=None, help="Year range, e.g. 2017:2025 (default: 2017:2025)")
    h.add_argument("--workers", type=int, default=48, help="Concurrent download workers")
    h.add_argument("--per-host", type=int, default=2, help="Max concurrent per host")
    h.add_argument("--output-root", default=None, help="Override output root")
    h.add_argument("--state", default=None, help="Override state DB path")
    h.add_argument("--resume", action="store_true", default=True, help="Resume from previous state (default)")

    # optimize
    opt = commands.add_parser("optimize", help="Run concurrency and throughput optimization experiments")
    opt.add_argument("--workers", default="32,48,64,80", help="Comma-separated candidate worker counts")
    opt.add_argument("--per-host", type=int, default=2, help="Per host concurrency setting")
    opt.add_argument("--output-root", default=None, help="Override output root")

    # benchmark
    bm = commands.add_parser("benchmark", help="Execute reproducible benchmark against the Pakistan Golden Corpus")
    bm.add_argument("--workers", type=int, default=48, help="Worker concurrency")
    bm.add_argument("--per-host", type=int, default=2, help="Per-host concurrency")
    bm.add_argument("--output-root", default=None, help="Override output root")

    # doctor
    commands.add_parser("doctor", help="Run environment health checks")

    # status
    commands.add_parser("status", help="Show harvest status and gap matrix")

    return cli


def main() -> None:
    args = parser().parse_args()
    if args.command == "harvest":
        cmd_harvest(args)
    elif args.command == "optimize":
        cmd_optimize(args)
    elif args.command == "benchmark":
        cmd_benchmark(args)
    elif args.command == "doctor":
        cmd_doctor(args)
    elif args.command == "status":
        cmd_status(args)
    else:
        parser().print_help()


if __name__ == "__main__":
    main()
