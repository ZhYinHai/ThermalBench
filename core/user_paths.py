from __future__ import annotations

import ctypes
import os
import sys
from pathlib import Path


def _windows_documents_dir() -> Path | None:
    """
    Return the real Windows Documents folder, including OneDrive/redirected Documents.
    Falls back safely if the Windows shell API is unavailable.
    """
    if sys.platform != "win32":
        return None

    try:
        from ctypes import wintypes

        class GUID(ctypes.Structure):
            _fields_ = [
                ("Data1", ctypes.c_ulong),
                ("Data2", ctypes.c_ushort),
                ("Data3", ctypes.c_ushort),
                ("Data4", ctypes.c_ubyte * 8),
            ]

        def make_guid(text: str) -> GUID:
            import uuid

            u = uuid.UUID(text)
            data4 = (ctypes.c_ubyte * 8).from_buffer_copy(u.bytes[8:])
            return GUID(u.fields[0], u.fields[1], u.fields[2], data4)

        # FOLDERID_Documents
        folder_id_documents = make_guid("FDD39AD0-238F-46AF-ADB4-6C85480369C7")

        path_ptr = ctypes.c_wchar_p()
        result = ctypes.windll.shell32.SHGetKnownFolderPath(
            ctypes.byref(folder_id_documents),
            0,
            None,
            ctypes.byref(path_ptr),
        )

        if result != 0 or not path_ptr.value:
            return None

        path = Path(path_ptr.value)

        try:
            ctypes.windll.ole32.CoTaskMemFree(path_ptr)
        except Exception:
            pass

        return path
    except Exception:
        return None


def documents_dir() -> Path:
    win_docs = _windows_documents_dir()
    if win_docs is not None:
        return win_docs

    fallback = Path.home() / "Documents"
    if fallback.exists():
        return fallback

    return Path.home()


def user_data_root() -> Path:
    """
    Visible, user-writable ThermalBench data folder.

    Preferred:
      C:/Users/<user>/Documents/ThermalBench

    Fallback:
      LOCALAPPDATA/ThermalBench
    """
    try:
        root = documents_dir() / "ThermalBench"
        root.mkdir(parents=True, exist_ok=True)
        return root
    except Exception:
        pass

    fallback_base = os.environ.get("LOCALAPPDATA") or str(Path.home())
    root = Path(fallback_base) / "ThermalBench"
    root.mkdir(parents=True, exist_ok=True)
    return root


def thermalbench_runs_root() -> Path:
    """
    Canonical location for all benchmark run data.

    Uses AppData\\Local\\ThermalBench\\runs so the folder is never inside
    OneDrive (or any other cloud-sync path), which avoids file-locking
    conflicts during and after runs.
    """
    path = local_data_root() / "runs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def thermalbench_hwinfo_dir() -> Path:
    path = user_data_root() / "HWiNFO"
    path.mkdir(parents=True, exist_ok=True)
    return path


def thermalbench_hwinfo_csv() -> Path:
    """
    HWiNFO CSV path.

    Important:
    Do NOT touch/create this file here.
    HWiNFO must create/write the file, otherwise ThermalBench may see
    an empty file and think CSV exists but has no header.
    """
    path = thermalbench_hwinfo_dir() / "hwinfo.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path

def local_data_root() -> Path:
    """
    Hidden writable app-data root.

    Used for writable bundled tool runtime files.
    """
    base = os.environ.get("LOCALAPPDATA") or str(Path.home())
    root = Path(base) / "ThermalBench"
    root.mkdir(parents=True, exist_ok=True)
    return root


def sensor_map_cache_path() -> Path:
    """Writable cache for HWiNFO CSV-column to device-group mapping."""
    return local_data_root() / "sensor_map.json"


def thermalbench_hwinfo_runtime_dir() -> Path:
    """
    Writable runtime copy of HWiNFO.

    HWiNFO executable + INI live here, not in Program Files.
    CSV still lives in Documents/ThermalBench/HWiNFO/hwinfo.csv.
    """
    path = local_data_root() / "tools" / "HWiNFO"
    path.mkdir(parents=True, exist_ok=True)
    return path


def thermalbench_furmark_runtime_dir() -> Path:
    """
    Writable runtime copy of FurMark.

    GeeXLab writes _geexlab_log.txt, _furmark_log.txt and _scores.csv next to
    the executable.  Program Files blocks those writes, so we run from here.
    """
    path = local_data_root() / "tools" / "FurMark"
    path.mkdir(parents=True, exist_ok=True)
    return path


def thermalbench_prime95_runtime_dir() -> Path:
    """
    Writable runtime copy of Prime95.

    Prime95 writes results.txt and local.txt next to its executable.
    Program Files blocks those writes, so we run from here.
    """
    path = local_data_root() / "tools" / "Prime95"
    path.mkdir(parents=True, exist_ok=True)
    return path


def thermalbench_afterburner_runtime_dir() -> Path:
    """
    Writable runtime copy of MSI Afterburner.

    MSI Afterburner writes profile/config/runtime files next to its executable.
    Program Files blocks those writes, so we run from here.
    """
    path = local_data_root() / "tools" / "MSI Afterburner"
    path.mkdir(parents=True, exist_ok=True)
    return path
