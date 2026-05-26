from __future__ import annotations

import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

from core.resources import app_root


_REG_KEY = r"Software\ThermalBench"


def _read_hkcu_string(value_name: str) -> str:
    if sys.platform != "win32":
        return ""

    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _REG_KEY) as key:
            value, _kind = winreg.QueryValueEx(key, value_name)
            return str(value or "").strip()
    except Exception:
        return ""


def _documents_dir() -> Path:
    # Good enough for this app; falls back safely if Documents is missing.
    docs = Path.home() / "Documents"
    if docs.exists():
        return docs

    userprofile = os.environ.get("USERPROFILE", "").strip()
    if userprofile:
        return Path(userprofile) / "Documents"

    return Path.home()


def data_root() -> Path:
    """
    Writable user-data root.

    Example:
      C:/Users/<user>/Documents/ThermalBench
    """
    docs = _documents_dir()
    root = docs / "ThermalBench"

    try:
        root.mkdir(parents=True, exist_ok=True)
    except Exception:
        fallback = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "ThermalBench"
        fallback.mkdir(parents=True, exist_ok=True)
        return fallback

    return root


def resolve_runs_root() -> Path:
    path = data_root() / "runs"
    try:
        path.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    return path


from core.user_paths import thermalbench_hwinfo_csv, thermalbench_runs_root, user_data_root


def data_root() -> Path:
    return user_data_root()


def resolve_runs_root() -> Path:
    return thermalbench_runs_root()


def resolve_hwinfo_csv_path() -> Path:
    return thermalbench_hwinfo_csv()


@dataclass
class RunsMigrationResult:
    destination: str
    copied: int = 0
    skipped: int = 0
    errors: list[str] | None = None


def _legacy_runs_roots() -> list[Path]:
    """
    Old locations that may contain historical results.

    Covers every default that was used in previous releases:
      1. Documents/ThermalBench/runs  — default before 2026 (OneDrive-unsafe)
      2. Inno Setup InstallDir/runs   — old bundled layout
      3. LOCALAPPDATA/Programs/ThermalBench/runs  — early installer layout
      4. <app_root>/runs              — dev / portable layout
    """
    candidates: list[Path] = []

    # Old default: Documents/ThermalBench/runs.
    # Use the proper shell-API resolver so OneDrive-redirected Documents is
    # found correctly on machines where Documents lives inside OneDrive.
    try:
        from core.user_paths import documents_dir as _docs_dir
        candidates.append(_docs_dir() / "ThermalBench" / "runs")
    except Exception:
        candidates.append(_documents_dir() / "ThermalBench" / "runs")

    old_install_dir = _read_hkcu_string("InstallDir")
    if old_install_dir:
        candidates.append(Path(old_install_dir) / "runs")

    localappdata = os.environ.get("LOCALAPPDATA", "").strip()
    if localappdata:
        candidates.append(Path(localappdata) / "Programs" / "ThermalBench" / "runs")

    # Dev / old bundled-layout fallback:
    candidates.append(app_root() / "runs")

    out: list[Path] = []
    seen: set[str] = set()

    for p in candidates:
        try:
            rp = p.resolve()
            key = os.path.normcase(str(rp))
            if key in seen:
                continue
            seen.add(key)
            out.append(rp)
        except Exception:
            continue

    return out


def migrate_legacy_runs_to_data_root() -> RunsMigrationResult:
    """
    One-time safe migration.

    Copies old:
      <old install>/runs/<case>/<run>

    To new:
      %LOCALAPPDATA%/ThermalBench/runs/<case>/<run>

    Existing destination folders are never overwritten.
    A sentinel file is written on completion so this never re-runs on future
    startups — preventing deleted runs from being re-imported from the old
    location.
    """
    from core.user_paths import local_data_root as _local_data_root

    dest_root = resolve_runs_root()

    # Sentinel lives next to the runs folder, not inside it, so it survives
    # the user deleting all runs.
    _sentinel = _local_data_root() / "migration_v1.done"
    if _sentinel.exists():
        return RunsMigrationResult(destination=str(dest_root), copied=0, skipped=0, errors=[])

    result = RunsMigrationResult(
        destination=str(dest_root),
        copied=0,
        skipped=0,
        errors=[],
    )

    try:
        dest_resolved = dest_root.resolve()
    except Exception:
        dest_resolved = dest_root

    for src_root in _legacy_runs_roots():
        try:
            if not src_root.exists() or not src_root.is_dir():
                continue

            src_resolved = src_root.resolve()

            # Do not copy from the new location into itself.
            if os.path.normcase(str(src_resolved)) == os.path.normcase(str(dest_resolved)):
                continue

            for case_dir in src_root.iterdir():
                if not case_dir.is_dir():
                    continue

                dest_case_dir = dest_root / case_dir.name
                dest_case_dir.mkdir(parents=True, exist_ok=True)

                for run_dir in case_dir.iterdir():
                    if not run_dir.is_dir():
                        continue

                    dest_run_dir = dest_case_dir / run_dir.name

                    if dest_run_dir.exists():
                        result.skipped += 1
                        continue

                    try:
                        shutil.copytree(run_dir, dest_run_dir)
                        result.copied += 1
                    except Exception as exc:
                        result.errors.append(f"{run_dir} -> {dest_run_dir}: {exc}")

        except Exception as exc:
            result.errors.append(f"{src_root}: {exc}")

    # Write sentinel so migration never re-runs.
    try:
        _sentinel.write_text("done", encoding="utf-8")
    except Exception as exc:
        result.errors.append(f"sentinel write failed: {exc}")

    return result