"""Pakistan-specific configuration constants and runtime settings."""

from __future__ import annotations

import json
import os
import platform
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


COUNTRY = "PAK"
EXCHANGE_MIC = "XKAR"
DEFAULT_YEARS = list(range(2017, 2026))  # FY2017–FY2025

# Google Drive detection
_DRIVE_CANDIDATES = [
    Path("G:/My Drive"),
    Path("H:/My Drive"),
    Path("I:/My Drive"),
    Path("D:/My Drive"),
    Path("E:/My Drive"),
    Path("F:/My Drive"),
]

PAKISTAN_STOCK_DIR = "Pakistan stock"


def detect_google_drive() -> Path | None:
    """Find the mounted Google Drive My Drive path on Windows."""
    for candidate in _DRIVE_CANDIDATES:
        if candidate.is_dir():
            return candidate
    return None


def get_pakistan_root(drive_root: Path | None = None) -> Path:
    """Return *Pakistan stock* folder inside Google Drive."""
    if drive_root is None:
        drive_root = detect_google_drive()
    if drive_root is None:
        raise EnvironmentError(
            "Google Drive for Desktop is not detected.  "
            "Ensure it is installed and signed in."
        )
    return drive_root / PAKISTAN_STOCK_DIR


@dataclass(frozen=True, slots=True)
class PakistanConfig:
    """Immutable runtime configuration for a Pakistan harvest run."""
    drive_root: Path
    pakistan_root: Path
    local_runtime: Path
    state_path: Path
    workers: int = 48
    per_host: int = 2
    timeout_s: float = 15.0
    max_mib: int = 64
    attempts: int = 2
    years: list[int] = field(default_factory=lambda: list(DEFAULT_YEARS))
    psx_bulk_authorized: bool = False
    official_sources_first: bool = True
    semantic_validation: bool = True

    # Management subdirectories
    @property
    def system_dir(self) -> Path:
        return self.pakistan_root / "_SYSTEM"

    @property
    def manifests_dir(self) -> Path:
        return self.pakistan_root / "_MANIFESTS"

    @property
    def audits_dir(self) -> Path:
        return self.pakistan_root / "_AUDITS"

    @property
    def logs_dir(self) -> Path:
        return self.pakistan_root / "_LOGS"

    @property
    def failures_dir(self) -> Path:
        return self.pakistan_root / "_FAILURES"

    @property
    def source_profiles_dir(self) -> Path:
        return self.system_dir / "source-profiles"

    @property
    def benchmarks_dir(self) -> Path:
        return self.system_dir / "benchmarks"

    @property
    def state_backups_dir(self) -> Path:
        return self.system_dir / "state-backups"

    def ensure_directories(self) -> None:
        """Create all management directories if missing."""
        for d in (
            self.system_dir,
            self.system_dir / "source",
            self.system_dir / "baseline",
            self.system_dir / "config",
            self.state_backups_dir,
            self.system_dir / "installers",
            self.source_profiles_dir,
            self.benchmarks_dir,
            self.manifests_dir,
            self.audits_dir,
            self.logs_dir,
            self.failures_dir,
        ):
            d.mkdir(parents=True, exist_ok=True)

    @classmethod
    def auto(cls, local_runtime: Path | None = None,
             output_root: Path | None = None,
             workers: int = 48, per_host: int = 2,
             years: list[int] | None = None,
             psx_bulk_authorized: bool = False) -> "PakistanConfig":
        """Auto-detect environment and build config."""
        drive_root = detect_google_drive()
        if local_runtime is None:
            # Prefer E: SSD, then local appdata
            if Path("E:/").is_dir():
                local_runtime = Path("E:/ZETA-PSX-RUNTIME")
            else:
                local_runtime = Path(os.environ.get("LOCALAPPDATA", "C:/Users")) / "ZETA-PSX-RUNTIME"

        if output_root is not None:
            pakistan_root = Path(output_root)
        elif drive_root is not None:
            import shutil
            try:
                free_space = shutil.disk_usage(str(drive_root)).free
            except Exception:
                free_space = 0
            # If Google Drive has at least 500 MB free quota, use it;
            # otherwise fall back to local SSD to prevent WinError 112 / ENOSPC.
            if free_space >= 500 * 1024 * 1024:
                pakistan_root = drive_root / PAKISTAN_STOCK_DIR
            else:
                pakistan_root = local_runtime / "Pakistan-stock"
        else:
            pakistan_root = local_runtime / "Pakistan-stock"

        state_path = local_runtime / "local" / "harvest-pak.sqlite3"
        return cls(
            drive_root=drive_root or local_runtime,
            pakistan_root=pakistan_root,
            local_runtime=local_runtime,
            state_path=state_path,
            workers=workers,
            per_host=per_host,
            years=years or list(DEFAULT_YEARS),
            psx_bulk_authorized=psx_bulk_authorized,
        )


def build_environment_manifest(config: PakistanConfig) -> dict[str, Any]:
    """Collect environment information for the manifest."""
    git_version = ""
    try:
        git_version = subprocess.check_output(
            ["git", "--version"], text=True, timeout=5
        ).strip()
    except Exception:
        pass

    try:
        import annual_reports
        pkg_version = annual_reports.__version__
    except Exception:
        pkg_version = "unknown"

    # Get installed package versions
    packages = {}
    for pkg_name in ["curl_cffi", "PyMuPDF", "httpx", "rich",
                     "beautifulsoup4", "pytest", "playwright"]:
        try:
            from importlib.metadata import version
            packages[pkg_name] = version(pkg_name)
        except Exception:
            packages[pkg_name] = "not installed"

    return {
        "python_version": platform.python_version(),
        "package_version": pkg_version,
        "packages": packages,
        "git_version": git_version,
        "os_version": f"{platform.system()} {platform.version()}",
        "drive_mount": str(config.drive_root),
        "pakistan_root": str(config.pakistan_root),
        "local_runtime": str(config.local_runtime),
        "state_path": str(config.state_path),
        "workers": config.workers,
        "per_host": config.per_host,
    }


def save_environment_manifest(config: PakistanConfig) -> Path:
    """Write environment.json to _SYSTEM/config/."""
    manifest = build_environment_manifest(config)
    path = config.system_dir / "config" / "environment.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return path
