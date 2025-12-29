import csv
from pathlib import Path


def read_hwinfo_headers(csv_path: str) -> list[str]:
    path = Path(csv_path)
    if not path.exists():
        raise FileNotFoundError(f"CSV not found: {csv_path}")

    last_err = None
    for enc in ("utf-8-sig", "utf-8", "cp1252"):
        try:
            with open(path, "r", encoding=enc, newline="") as f:
                reader = csv.reader(f)
                header = next(reader, None)
                if not header:
                    raise ValueError("CSV header is empty.")
                header = [str(h).strip().strip('"') for h in header if h is not None]
                while header and header[-1] == "":
                    header.pop()
                return header
        except Exception as e:
            last_err = e

    raise last_err or RuntimeError("Failed to read CSV header.")


def make_unique(cols: list[str]) -> list[str]:
    seen: dict[str, int] = {}
    out: list[str] = []
    for c in cols:
        c = str(c).strip()
        if c not in seen:
            seen[c] = 0
            out.append(c)
        else:
            seen[c] += 1
            out.append(f"{c} #{seen[c]}")
    return out


def sensor_leafs_from_header(header: list[str]) -> tuple[list[str], bool]:
    leafs: list[str] = []
    has_spd = False
    for hs in header:
        if not hs:
            continue
        lo = hs.lower()
        if lo in ("date", "time"):
            continue
        if lo.startswith("unnamed"):
            continue
        if "spd hub temperature" in lo:
            has_spd = True
        leafs.append(hs)
    return leafs, has_spd
