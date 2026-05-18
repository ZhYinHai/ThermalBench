from __future__ import annotations

import ctypes
import subprocess
import sys
from pathlib import Path

import shutil

from core.user_paths import (
    thermalbench_hwinfo_csv,
    thermalbench_hwinfo_dir,
    thermalbench_hwinfo_runtime_dir,
    thermalbench_furmark_runtime_dir,
    thermalbench_prime95_runtime_dir,
)

from core.resources import app_root


_REG_KEY = r"Software\ThermalBench"

def _bundled_hwinfo_dir() -> Path:
    return tools_root() / "HWiNFO"


def _ignore_hwinfo_runtime_files(_dir: str, names: list[str]) -> set[str]:
    ignored: set[str] = set()

    for name in names:
        lower = name.lower()

        # Never copy old logs/user config into runtime.
        if lower.endswith(".csv"):
            ignored.add(name)
        elif lower.endswith(".log"):
            ignored.add(name)
        elif lower.endswith(".ini"):
            ignored.add(name)

    return ignored


def ensure_hwinfo_runtime_dir() -> Path:
    """
    Copy bundled HWiNFO to a writable runtime folder.

    Source:
      Program Files/ThermalBench/tools/HWiNFO

    Runtime:
      LOCALAPPDATA/ThermalBench/tools/HWiNFO
    """
    src = _bundled_hwinfo_dir()
    dst = thermalbench_hwinfo_runtime_dir()

    if not src.exists():
        return dst

    try:
        shutil.copytree(
            src,
            dst,
            dirs_exist_ok=True,
            ignore=_ignore_hwinfo_runtime_files,
        )
    except Exception:
        pass

    return dst

def _hwinfo_ini_path(exe_path: Path) -> Path:
    # HWiNFO64.exe -> HWiNFO64.INI
    # HWiNFO.exe   -> HWiNFO.INI
    return exe_path.with_name(f"{exe_path.stem}.INI")


def _upsert_ini_settings(ini_path: Path, updates: dict[str, str]) -> None:
    """
    Preserve existing HWiNFO INI as much as possible, but force the settings
    ThermalBench needs for reliable bundled startup.
    """
    try:
        if ini_path.exists():
            text = ini_path.read_text(encoding="utf-8", errors="ignore")
        else:
            text = ""
    except Exception:
        text = ""

    lines = text.splitlines()
    out: list[str] = []

    in_settings = False
    settings_found = False
    written_keys: set[str] = set()

    def flush_missing_settings():
        for key, value in updates.items():
            if key.lower() not in written_keys:
                out.append(f"{key}={value}")
                written_keys.add(key.lower())

    for line in lines:
        stripped = line.strip()

        if stripped.startswith("[") and stripped.endswith("]"):
            if in_settings:
                flush_missing_settings()

            in_settings = stripped.lower() == "[settings]"
            if in_settings:
                settings_found = True

            out.append(line)
            continue

        if in_settings and "=" in line and not stripped.startswith(";"):
            key = line.split("=", 1)[0].strip()
            match_key = next((k for k in updates if k.lower() == key.lower()), None)

            if match_key is not None:
                out.append(f"{match_key}={updates[match_key]}")
                written_keys.add(match_key.lower())
                continue

        out.append(line)

    if in_settings:
        flush_missing_settings()

    if not settings_found:
        if out and out[-1].strip():
            out.append("")
        out.append("[Settings]")
        for key, value in updates.items():
            out.append(f"{key}={value}")

    try:
        ini_path.parent.mkdir(parents=True, exist_ok=True)
        ini_path.write_text("\n".join(out) + "\n", encoding="utf-8")
    except Exception as e:
        raise RuntimeError(f"Could not write HWiNFO settings file: {ini_path} ({e})")


def prepare_hwinfo_portable_settings(exe_path: Path) -> None:
    """
    Force bundled HWiNFO to open directly in Sensors-only mode.

    This avoids the full 'Examining system requirements' hardware scan,
    which can freeze on some systems.
    """
    ini_path = _hwinfo_ini_path(exe_path)

    _upsert_ini_settings(
        ini_path,
        {
            # Main fix: skip full system inventory scan.
            "SensorsOnly": "1",

            # Do not show startup/welcome/progress screen.
            "ShowWelcomeAndProgress": "0",

            # Open sensors, not system summary.
            "OpenSensors": "1",
            "OpenSystemSummary": "0",

            # Keep main window out of the way.
            "MinimalizeMainWnd": "1",

            # Enable shared memory support for your SM2 check.
            "SensorsSM": "1",

            # Avoid updater prompts in bundled app.
            "AutoUpdateBetaDisable": "1",

            # Good default polling interval for ThermalBench.
            "SensorInterval": "1000",
        },
    )


def _launch_elevated_if_needed(exe_path: Path) -> bool:
    if sys.platform != "win32":
        return False

    try:
        rc = ctypes.windll.shell32.ShellExecuteW(
            None,
            "runas",
            str(exe_path),
            None,
            str(exe_path.parent),
            1,
        )
        return int(rc) > 32
    except Exception:
        return False


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


def _existing_file(path: str | Path | None) -> str:
    if not path:
        return ""

    try:
        p = Path(path)
        if p.is_file():
            return str(p.resolve())
    except Exception:
        pass

    return ""


def _first_existing(candidates: list[str | Path | None]) -> str:
    for candidate in candidates:
        found = _existing_file(candidate)
        if found:
            return found
    return ""


def tools_root() -> Path:
    """
    Runtime:
      <AppRoot>/tools

    Dev fallback:
      <RepoRoot>/vendor/tools
    """
    primary = app_root() / "tools"
    if primary.exists():
        return primary

    dev = app_root() / "vendor" / "tools"
    if dev.exists():
        return dev

    return primary


def _bundled_furmark_dir() -> Path:
    return tools_root() / "FurMark"


def _bundled_prime95_dir() -> Path:
    return tools_root() / "Prime95"


def _ignore_furmark_runtime_files(_dir: str, names: list[str]) -> set[str]:
    """Don't overwrite FurMark's runtime-generated files on re-copy."""
    ignored: set[str] = set()
    for name in names:
        lower = name.lower()
        if lower in ("_geexlab_log.txt", "_furmark_log.txt", "_scores.csv"):
            ignored.add(name)
    return ignored


def _ignore_prime95_runtime_files(_dir: str, names: list[str]) -> set[str]:
    """Don't overwrite Prime95 runtime-generated files on re-copy."""
    ignored: set[str] = set()
    for name in names:
        lower = name.lower()
        if lower in ("results.txt", "local.txt", "prime.txt"):
            ignored.add(name)
    return ignored


def ensure_furmark_runtime_dir() -> Path:
    """
    Copy bundled FurMark to a writable runtime folder so GeeXLab can write
    its log and scores files next to the executable.
    """
    src = _bundled_furmark_dir()
    dst = thermalbench_furmark_runtime_dir()

    if not src.exists():
        return dst

    try:
        shutil.copytree(
            src,
            dst,
            dirs_exist_ok=True,
            ignore=_ignore_furmark_runtime_files,
        )
    except Exception:
        pass

    return dst


def ensure_prime95_runtime_dir() -> Path:
    """
    Copy bundled Prime95 to a writable runtime folder so it can write
    results.txt and local.txt next to the executable.
    """
    src = _bundled_prime95_dir()
    dst = thermalbench_prime95_runtime_dir()

    if not src.exists():
        return dst

    try:
        shutil.copytree(
            src,
            dst,
            dirs_exist_ok=True,
            ignore=_ignore_prime95_runtime_files,
        )
    except Exception:
        pass

    return dst


def resolve_furmark_exe(configured: str = "") -> str:
    runtime = ensure_furmark_runtime_dir()
    bundled = _bundled_furmark_dir()

    return _first_existing(
        [
            # Prefer writable runtime copy so GeeXLab can write its log files.
            runtime / "furmark.exe",
            runtime / "FurMark_win64" / "furmark.exe",
            # Configured / registry overrides (user-supplied path, kept as-is).
            configured,
            _read_hkcu_string("FurMarkExe"),
            # Bundled fallback (dev: vendor/tools, prod: Program Files — last
            # resort; may fail if Program Files is read-only for log writes).
            bundled / "furmark.exe",
            bundled / "FurMark_win64" / "furmark.exe",
        ]
    )


def resolve_prime95_exe(configured: str = "") -> str:
    runtime = ensure_prime95_runtime_dir()
    bundled = _bundled_prime95_dir()

    return _first_existing(
        [
            runtime / "prime95.exe",
            runtime / "prime95" / "prime95.exe",
            configured,
            _read_hkcu_string("PrimeExe"),
            bundled / "prime95.exe",
            bundled / "prime95" / "prime95.exe",
        ]
    )


def hwinfo_dir() -> Path:
    return ensure_hwinfo_runtime_dir()


def resolve_hwinfo_exe(configured: str = "") -> str:
    runtime = ensure_hwinfo_runtime_dir()
    bundled = _bundled_hwinfo_dir()

    # Prefer writable runtime copy.
    return _first_existing(
        [
            runtime / "HWiNFO64.exe",
            runtime / "HWiNFO.exe",
            bundled / "HWiNFO64.exe",
            bundled / "HWiNFO.exe",
            configured,
            _read_hkcu_string("HwinfoExe"),
        ]
    )


def resolve_hwinfo_csv() -> str:
    """
    Fixed writable HWiNFO CSV path.

    HWiNFO must log to:
      Documents/ThermalBench/HWiNFO/hwinfo.csv
    """
    return str(thermalbench_hwinfo_csv().resolve(strict=False))


def launch_hwinfo(configured: str = "") -> str:
    exe = resolve_hwinfo_exe(configured)
    if not exe:
        return ""

    exe_path = Path(exe)

    try:
        prepare_hwinfo_portable_settings(exe_path)
    except Exception:
        # Continue launching, but this should be logged/shown somewhere during testing.
        # If this fails on the other PC, that explains why HWiNFO still freezes.
        pass

    kwargs = {}

    if sys.platform == "win32":
        kwargs["creationflags"] = (
            getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            | getattr(subprocess, "DETACHED_PROCESS", 0)
        )

    try:
        subprocess.Popen(
            [str(exe_path)],
            cwd=str(exe_path.parent),
            close_fds=True,
            **kwargs,
        )
    except OSError as e:
        # Fix for [WinError 740] requested operation requires elevation.
        if getattr(e, "winerror", None) == 740:
            if _launch_elevated_if_needed(exe_path):
                return str(exe_path)
        raise

    return str(exe_path)