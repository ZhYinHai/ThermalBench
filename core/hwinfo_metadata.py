import json
import ctypes
from ctypes import wintypes
from pathlib import Path
from typing import Any

# ----------------------------
# Cache helpers (unchanged)
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
HWiNFO_MAP_CANDIDATES = [
    "Global\\HWiNFO_SENS_SM2",
    "Local\\HWiNFO_SENS_SM2",
    "HWiNFO_SENS_SM2",
]


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


def _win_handle(func, *args):
    h = func(*args)
    if not h:
        raise OSError(ctypes.get_last_error())
    return h


# --- Legacy structs (your original) ---
# Kept so the work PC keeps working if it happens to match the older layout.
class _LegacyHeader(ctypes.Structure):
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


class _LegacySensor(ctypes.Structure):
    _pack_ = 1
    _fields_ = [
        ("id", ctypes.c_uint32),
        ("instance", ctypes.c_uint32),
        ("name_original", ctypes.c_char * 128),
        ("name_user", ctypes.c_char * 128),
    ]


class _LegacyReading(ctypes.Structure):
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


# --- New dynamic SM2 header (works on your personal PC) ---
class _SM2HeaderV2(ctypes.Structure):
    _pack_ = 1
    _fields_ = [
        ("dwSignature", ctypes.c_uint32),
        ("dwVersion", ctypes.c_uint32),
        ("dwRevision", ctypes.c_uint32),
        ("poll_time", ctypes.c_int64),  # 64-bit in this layout
        ("dwOffsetOfSensorSection", ctypes.c_uint32),
        ("dwSizeOfSensorElement", ctypes.c_uint32),
        ("dwNumSensorElements", ctypes.c_uint32),
        ("dwOffsetOfReadingSection", ctypes.c_uint32),
        ("dwSizeOfReadingElement", ctypes.c_uint32),
        ("dwNumReadingElements", ctypes.c_uint32),
    ]


# Minimal “prefix” structures that read the first bytes of each element.
# The element can be bigger; we read the known prefix and ignore padding/extra fields.
class _SM2SensorPrefix(ctypes.Structure):
    _pack_ = 1
    _fields_ = [
        ("dwSensorID", ctypes.c_uint32),
        ("dwSensorInst", ctypes.c_uint32),
        ("szSensorNameOrig", ctypes.c_char * 128),
        ("szSensorNameUser", ctypes.c_char * 128),
    ]


class _SM2ReadingPrefix(ctypes.Structure):
    _pack_ = 1
    _fields_ = [
        ("tReading", ctypes.c_uint32),
        ("dwSensorIndex", ctypes.c_uint32),
        ("dwReadingID", ctypes.c_uint32),
        ("szLabelOrig", ctypes.c_char * 128),
        ("szLabelUser", ctypes.c_char * 128),
        ("szUnit", ctypes.c_char * 16),
        ("Value", ctypes.c_double),
        ("ValueMin", ctypes.c_double),
        ("ValueMax", ctypes.c_double),
        ("ValueAvg", ctypes.c_double),
    ]


def _open_sm2_mapping():
    kernel32 = ctypes.windll.kernel32
    OpenFileMappingW = kernel32.OpenFileMappingW
    OpenFileMappingW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
    OpenFileMappingW.restype = wintypes.HANDLE

    FILE_MAP_READ = 0x0004

    last_err = None
    for nm in HWiNFO_MAP_CANDIDATES:
        h = OpenFileMappingW(FILE_MAP_READ, False, nm)
        if h:
            return h, nm
        last_err = ctypes.get_last_error()

    raise FileNotFoundError(
        "Could not open HWiNFO SM2 mapping.\n"
        f"Tried: {', '.join(HWiNFO_MAP_CANDIDATES)} (last winerr={last_err}).\n"
        "Make sure HWiNFO is running, Sensors window is open, and Shared Memory Support is enabled."
    )


def _read_entries_v2(base_ptr: int) -> list[tuple[str, str]]:
    """
    Dynamic reader using SM2 'V2' header layout:
    uses header-provided offsets + element sizes (works when elements are 392/460 etc).
    """
    hdr = _SM2HeaderV2.from_address(base_ptr)

    sig_ok = {
        int.from_bytes(b"HWiS", "little"),
        int.from_bytes(b"SiWH", "little"),
    }
    if hdr.dwSignature not in sig_ok:
        raise RuntimeError(f"SM2 signature mismatch (0x{hdr.dwSignature:08X})")

    sensor_off = int(hdr.dwOffsetOfSensorSection)
    sensor_sz = int(hdr.dwSizeOfSensorElement)
    sensor_n = int(hdr.dwNumSensorElements)

    read_off = int(hdr.dwOffsetOfReadingSection)
    read_sz = int(hdr.dwSizeOfReadingElement)
    read_n = int(hdr.dwNumReadingElements)

    # sanity: element sizes must at least fit our prefix structs
    if sensor_sz < ctypes.sizeof(_SM2SensorPrefix):
        raise RuntimeError(f"SM2 sensor element too small: {sensor_sz}")
    if read_sz < ctypes.sizeof(_SM2ReadingPrefix):
        raise RuntimeError(f"SM2 reading element too small: {read_sz}")

    # Sensors (group titles)
    sensors: list[str] = []
    for i in range(sensor_n):
        addr = base_ptr + sensor_off + i * sensor_sz
        s = _SM2SensorPrefix.from_address(addr)
        name = _cstr(bytes(s.szSensorNameUser)) or _cstr(bytes(s.szSensorNameOrig))
        sensors.append(name or f"Sensor {i}")

    # Readings (label -> group)
    out: list[tuple[str, str]] = []
    for i in range(read_n):
        addr = base_ptr + read_off + i * read_sz
        r = _SM2ReadingPrefix.from_address(addr)

        si = int(r.dwSensorIndex)
        group = sensors[si] if 0 <= si < len(sensors) else "Other"

        rname = _cstr(bytes(r.szLabelUser)) or _cstr(bytes(r.szLabelOrig))
        unit = _cstr(bytes(r.szUnit)).strip()

        if not rname:
            continue

        label = f"{rname} [{unit}]" if unit else rname
        out.append((label, group))

    return out


def _read_entries_legacy(base_ptr: int) -> list[tuple[str, str]]:
    """
    Your original reader (works on machines where the original layout matches).
    """
    hdr = _LegacyHeader.from_address(base_ptr)

    magic_ok = {
        int.from_bytes(b"HWiS", "little"),
        int.from_bytes(b"SiWH", "little"),
    }
    if hdr.magic not in magic_ok:
        raise RuntimeError(f"Legacy SM2 signature mismatch (0x{hdr.magic:08X})")

    sensors: list[str] = []
    for i in range(hdr.sensor_element_count):
        addr = base_ptr + hdr.sensor_section_offset + i * hdr.sensor_element_size
        s = _LegacySensor.from_address(addr)
        name = _cstr(bytes(s.name_user)) or _cstr(bytes(s.name_original))
        sensors.append(name or f"Sensor {i}")

    out: list[tuple[str, str]] = []
    for i in range(hdr.reading_element_count):
        addr = base_ptr + hdr.reading_section_offset + i * hdr.reading_element_size
        r = _LegacyReading.from_address(addr)
        si = int(r.sensor_index)
        group = sensors[si] if 0 <= si < len(sensors) else "Other"

        rname = _cstr(bytes(r.name_user)) or _cstr(bytes(r.name_original))
        unit = _cstr(bytes(r.unit)).strip()
        if not rname:
            continue
        label = f"{rname} [{unit}]" if unit else rname
        out.append((label, group))
    return out


def _read_sm2_entries() -> list[tuple[str, str]]:
    """
    Returns list of (csv_label, group_title) in HWiNFO order.

    Strategy:
      1) Try NEW dynamic V2 layout (works with sensor elem_size=392, reading elem_size=460, etc.)
      2) If that fails, fall back to your legacy layout (keeps work PC behavior)
    """
    kernel32 = ctypes.windll.kernel32

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

    hmap, _ = _open_sm2_mapping()
    try:
        base_ptr = _win_handle(MapViewOfFile, hmap, FILE_MAP_READ, 0, 0, 0)
        try:
            # Prefer the new logic; fallback to legacy
            try:
                return _read_entries_v2(base_ptr)
            except Exception:
                return _read_entries_legacy(base_ptr)
        finally:
            UnmapViewOfFile(base_ptr)
    finally:
        CloseHandle(hmap)

def build_precise_group_map(csv_leafs: list[str], csv_unique_leafs: list[str]) -> dict[str, str]:
    """
    Map each UNIQUE CSV column name to its HWiNFO sensor group title.

    - Reads SM2 entries via _read_sm2_entries() (now supports both layouts).
    - Handles duplicates by matching the Nth occurrence of a label.
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
