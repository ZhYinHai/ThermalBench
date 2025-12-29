import json
import ctypes
from ctypes import wintypes
from pathlib import Path
from typing import Any


# ----------------------------
# Cache helpers
# ----------------------------
def load_sensor_map(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def save_sensor_map(path: Path, header_unique: list[str], mapping: dict[str, str]) -> None:
    payload = {"schema": 1, "header_unique": header_unique, "mapping": mapping}
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


# ----------------------------
# HWiNFO Shared Memory (SM2)
# ----------------------------
HWiNFO_MAP_NAME = "Global\\HWiNFO_SENS_SM2"
HWiNFO_MUTEX_NAME = "Global\\HWiNFO_SM2_MUTEX"


def _cstr(b: bytes) -> str:
    raw = b.split(b"\x00", 1)[0]
    if not raw:
        return ""
    for enc in ("utf-8", "cp1252", "latin-1"):
        try:
            return raw.decode(enc)
        except Exception:
            pass
    return raw.decode("latin-1", errors="replace")


class HWiNFOHeader(ctypes.Structure):
    _pack_ = 1
    _fields_ = [
        ("magic", ctypes.c_uint32),
        ("version", ctypes.c_uint32),
        ("revision", ctypes.c_uint32),
        ("poll_time", ctypes.c_uint32),
        ("sensor_section_offset", ctypes.c_uint32),
        ("sensor_section_size", ctypes.c_uint32),
        ("sensor_element_size", ctypes.c_uint32),
        ("sensor_element_count", ctypes.c_uint32),
        ("reading_section_offset", ctypes.c_uint32),
        ("reading_section_size", ctypes.c_uint32),
        ("reading_element_size", ctypes.c_uint32),
        ("reading_element_count", ctypes.c_uint32),
    ]


class HWiNFOSensor(ctypes.Structure):
    _pack_ = 1
    _fields_ = [
        ("id", ctypes.c_uint32),
        ("instance", ctypes.c_uint32),
        ("name_original", ctypes.c_char * 128),
        ("name_user", ctypes.c_char * 128),
    ]


class HWiNFOReading(ctypes.Structure):
    _pack_ = 1
    _fields_ = [
        ("type", ctypes.c_uint32),
        ("sensor_index", ctypes.c_uint32),
        ("id", ctypes.c_uint32),
        ("name_original", ctypes.c_char * 128),
        ("name_user", ctypes.c_char * 128),
        ("unit", ctypes.c_char * 16),
        ("value", ctypes.c_double),
        ("value_min", ctypes.c_double),
        ("value_max", ctypes.c_double),
        ("value_avg", ctypes.c_double),
    ]


def _win_handle(func, *args):
    h = func(*args)
    if not h:
        raise OSError(ctypes.get_last_error())
    return h


def _read_sm2_entries() -> list[tuple[str, str]]:
    """
    Returns list of (csv_label, group_title) in the order HWiNFO exposes them.
    csv_label matches HWiNFO CSV format: "<Reading Name> [<Unit>]" (unit optional).
    group_title is the Sensor name (the group header like 'CPU [#0]: ... Enhanced').
    """
    kernel32 = ctypes.windll.kernel32

    OpenFileMappingW = kernel32.OpenFileMappingW
    OpenFileMappingW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
    OpenFileMappingW.restype = wintypes.HANDLE

    MapViewOfFile = kernel32.MapViewOfFile
    MapViewOfFile.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, ctypes.c_size_t]
    MapViewOfFile.restype = wintypes.LPVOID

    UnmapViewOfFile = kernel32.UnmapViewOfFile
    UnmapViewOfFile.argtypes = [wintypes.LPCVOID]
    UnmapViewOfFile.restype = wintypes.BOOL

    CloseHandle = kernel32.CloseHandle
    CloseHandle.argtypes = [wintypes.HANDLE]
    CloseHandle.restype = wintypes.BOOL

    FILE_MAP_READ = 0x0004

    # Open mapping
    hmap = _win_handle(OpenFileMappingW, FILE_MAP_READ, False, HWiNFO_MAP_NAME)
    try:
        base_ptr = _win_handle(MapViewOfFile, hmap, FILE_MAP_READ, 0, 0, 0)

        try:
            hdr = HWiNFOHeader.from_address(base_ptr)

            magic_expected = int.from_bytes(b"SiWH", "little")
            if hdr.magic != magic_expected:
                raise RuntimeError("HWiNFO shared memory signature mismatch. Is Shared Memory Support enabled?")

            # Read sensors
            sensors: list[str] = []
            for i in range(hdr.sensor_element_count):
                addr = base_ptr + hdr.sensor_section_offset + i * hdr.sensor_element_size
                s = HWiNFOSensor.from_address(addr)
                name = _cstr(bytes(s.name_user)) or _cstr(bytes(s.name_original))
                sensors.append(name or f"Sensor {i}")

            # Read readings
            out: list[tuple[str, str]] = []
            for i in range(hdr.reading_element_count):
                addr = base_ptr + hdr.reading_section_offset + i * hdr.reading_element_size
                r = HWiNFOReading.from_address(addr)
                sensor_idx = int(r.sensor_index)
                group = sensors[sensor_idx] if 0 <= sensor_idx < len(sensors) else "Other"

                rname = _cstr(bytes(r.name_user)) or _cstr(bytes(r.name_original))
                unit = _cstr(bytes(r.unit)).strip()

                if not rname:
                    continue

                label = f"{rname} [{unit}]" if unit else rname
                out.append((label, group))

            return out
        finally:
            UnmapViewOfFile(base_ptr)
    finally:
        CloseHandle(hmap)


def build_precise_group_map(csv_leafs: list[str], csv_unique_leafs: list[str]) -> dict[str, str]:
    """
    Map each UNIQUE CSV column name to its precise HWiNFO group title.
    Handles duplicates by matching the Nth occurrence of a label.

    Example:
      csv_leafs:        ["CPU Package [°C]", "CPU Package [°C]", ...]
      csv_unique_leafs: ["CPU Package [°C]", "CPU Package [°C] #1", ...]
      -> assigns each to the correct group, based on HWiNFO SM2 order.
    """
    entries = _read_sm2_entries()

    label_to_groups: dict[str, list[str]] = {}
    for label, group in entries:
        label_to_groups.setdefault(label, []).append(group)

    occ: dict[str, int] = {}
    mapping: dict[str, str] = {}

    for base, uniq in zip(csv_leafs, csv_unique_leafs):
        k = occ.get(base, 0)
        occ[base] = k + 1
        groups = label_to_groups.get(base, [])
        if k < len(groups):
            mapping[uniq] = groups[k]

    return mapping
