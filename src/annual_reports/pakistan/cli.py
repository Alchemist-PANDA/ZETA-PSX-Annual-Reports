"""Pakistan CLI entry point (``zeta-pk``).

Provides ``harvest``, ``doctor``, ``status`` subcommands without breaking
the existing ``ar-harvest`` CLI.
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
from .config import build_environment_manifest, save_environment_manifest


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
    companies = _parse_companies(args.companies)
    if not companies:
        console.print("[red]No companies specified.[/red]")
        sys.exit(1)

    years = _parse_years(args.years) if args.years else list(DEFAULT_YEARS)

    console.print(f"\n[bold cyan]ZETA Pakistan Harvester[/bold cyan]")
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


def cmd_doctor(args: argparse.Namespace) -> None:
    """Run health checks on the Pakistan harvest environment."""
    console.print("\n[bold cyan]ZETA Pakistan — Doctor[/bold cyan]\n")

    checks: list[tuple[str, str, str]] = []  # (name, status, detail)

    # Google Drive
    drive = detect_google_drive()
    if drive:
        checks.append(("Google Drive", "PASS", str(drive)))
    else:
        checks.append(("Google Drive", "FAIL", "Not detected"))

    # Pakistan stock root
    try:
        root = get_pakistan_root(drive)
        if root.is_dir():
            checks.append(("Pakistan Stock Root", "PASS", str(root)))
        else:
            checks.append(("Pakistan Stock Root", "WARN", "Directory doesn't exist yet"))
    except Exception as exc:
        checks.append(("Pakistan Stock Root", "FAIL", str(exc)))

    # Local runtime
    local_rt = Path("E:/ZETA-PSX-RUNTIME")
    if local_rt.is_dir():
        checks.append(("Local Runtime", "PASS", str(local_rt)))
    else:
        local_rt = Path(os.environ.get("LOCALAPPDATA", "C:/Users")) / "ZETA-PSX-RUNTIME"
        checks.append(("Local Runtime", "WARN", f"Using {local_rt}"))

    # Python version
    import platform
    py_ver = platform.python_version()
    major, minor = int(py_ver.split(".")[0]), int(py_ver.split(".")[1])
    if major >= 3 and minor >= 11:
        checks.append(("Python", "PASS", py_ver))
    else:
        checks.append(("Python", "FAIL", f"{py_ver} (need 3.11+)"))

    # Dependencies
    for pkg in ["curl_cffi", "pymupdf", "httpx", "rich", "bs4"]:
        try:
            __import__(pkg)
            checks.append((f"Package: {pkg}", "PASS", "installed"))
        except ImportError:
            checks.append((f"Package: {pkg}", "FAIL", "missing"))

    # Browser (optional)
    import shutil
    chrome = shutil.which("chrome") or shutil.which("google-chrome")
    edge = shutil.which("msedge")
    if chrome or edge:
        checks.append(("Browser", "PASS", chrome or edge or ""))
    else:
        checks.append(("Browser", "WARN", "No Chrome/Edge found (optional)"))

    # Disk space
    import shutil as sh
    for disk, label in [("E:\\", "E: SSD"), ("C:\\", "C: System")]:
        try:
            usage = sh.disk_usage(disk)
            free_gb = usage.free / (1024**3)
            status = "PASS" if free_gb > 1 else ("WARN" if free_gb > 0.2 else "FAIL")
            checks.append((f"Disk {label}", status, f"{free_gb:.1f} GB free"))
        except Exception:
            checks.append((f"Disk {label}", "WARN", "Cannot check"))

    # Network
    try:
        import socket
        socket.create_connection(("dns.google", 443), timeout=5).close()
        checks.append(("Network", "PASS", "Internet reachable"))
    except Exception:
        checks.append(("Network", "FAIL", "Cannot reach internet"))

    # Display results
    table = Table(show_header=True, header_style="bold")
    table.add_column("Check", style="cyan")
    table.add_column("Status")
    table.add_column("Detail", style="dim")

    for name, status, detail in checks:
        color = {"PASS": "green", "WARN": "yellow", "FAIL": "red"}.get(status, "white")
        table.add_row(name, f"[{color}]{status}[/{color}]", detail)

    console.print(table)

    # Overall
    statuses = [s for _, s, _ in checks]
    if "FAIL" in statuses:
        console.print("\n[red bold]FAIL — Critical issues found.[/red bold]")
        sys.exit(1)
    elif "WARN" in statuses:
        console.print("\n[yellow bold]WARN — Some warnings, but functional.[/yellow bold]")
    else:
        console.print("\n[green bold]PASS — All checks passed.[/green bold]")


def cmd_status(args: argparse.Namespace) -> None:
    """Show current harvest status and gap matrix."""
    try:
        config = PakistanConfig.auto()
    except EnvironmentError as exc:
        console.print(f"[red]{exc}[/red]")
        sys.exit(1)

    profile_db = config.local_runtime / "local" / "source-profiles.sqlite3"
    if not profile_db.is_file():
        console.print("[yellow]No harvest state found. Run 'zeta-pk harvest' first.[/yellow]")
        return

    from .source_profile import SourceProfileStore
    store = SourceProfileStore(profile_db)

    profiles = store.all_profiles()
    console.print(f"\n[bold cyan]Source Profiles:[/bold cyan] {len(profiles)}")

    if profiles:
        table = Table(show_header=True, header_style="bold")
        table.add_column("Symbol", style="cyan")
        table.add_column("Company")
        table.add_column("AR Page")
        table.add_column("Confidence")

        for p in profiles[:20]:
            table.add_row(
                p.get("psx_symbol", ""),
                p.get("company_name", ""),
                p.get("annual_report_page", "")[:60],
                p.get("confidence", ""),
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
    h.add_argument("companies", help="Comma-separated tickers/names or path to text/CSV file")
    h.add_argument("--years", default=None, help="Year range, e.g. 2017:2025 (default: 2017:2025)")
    h.add_argument("--workers", type=int, default=48, help="Concurrent download workers")
    h.add_argument("--per-host", type=int, default=2, help="Max concurrent per host")
    h.add_argument("--output-root", default=None, help="Override output root")
    h.add_argument("--state", default=None, help="Override state DB path")
    h.add_argument("--resume", action="store_true", default=True, help="Resume from previous state (default)")

    # doctor
    commands.add_parser("doctor", help="Run environment health checks")

    # status
    commands.add_parser("status", help="Show harvest status and gap matrix")

    return cli


def main() -> None:
    args = parser().parse_args()
    if args.command == "harvest":
        cmd_harvest(args)
    elif args.command == "doctor":
        cmd_doctor(args)
    elif args.command == "status":
        cmd_status(args)
    else:
        parser().print_help()


if __name__ == "__main__":
    main()
