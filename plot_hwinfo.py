#!/usr/bin/env python3
"""
plot_hwinfo.py

Fixes:
- Your crash: pandas 'python' engine doesn't support low_memory with chunks -> use C engine.
- Keeps raw header names + our make_unique (#1/#2) naming (no pandas .1/.2 mangling).
- Chunked window slicing (low RAM).
- Exact-name selection first (case-insensitive), regex fallback.
- Never plots individual SPD Hub Temperature columns; plots only SPD Hub Max if SPD requested.
- run_window.csv contains only Date/Time + plotted series.
"""

import argparse
import csv
import re
from pathlib import Path
from typing import Optional, Tuple, List

import pandas as pd
import matplotlib.pyplot as plt


# ----------------- helpers -----------------
def make_unique(cols: List[str]) -> List[str]:
    """Make duplicate column names unique by appending ' #n'."""
    seen = {}
    out = []
    for c in cols:
        c = str(c).strip()
        if c not in seen:
            seen[c] = 0
            out.append(c)
        else:
            seen[c] += 1
            out.append(f"{c} #{seen[c]}")
    return out


def normalize_time(t: str) -> str:
    """Fix HWiNFO time strings like '13:23:1.975' -> '13:23:01.975'."""
    m = re.match(r"^(\d{1,2}):(\d{1,2}):(\d{1,2})(\.\d+)?$", str(t))
    if not m:
        return str(t)
    hh, mm, ss, ms = m.group(1), m.group(2), m.group(3), m.group(4) or ""
    return f"{int(hh):02d}:{int(mm):02d}:{int(ss):02d}{ms}"


def sanitize(name: str) -> str:
    return re.sub(r"[^\w\-. ]+", "_", name).strip().replace(" ", "_")[:180]


def sniff_encoding(path: Path) -> str:
    for enc in ("utf-8-sig", "utf-8", "cp1252"):
        try:
            with open(path, "r", encoding=enc, newline="") as f:
                f.readline()
            return enc
        except Exception:
            continue
    return "utf-8-sig"


def read_raw_header(path: Path, encoding: str) -> List[str]:
    with open(path, "r", encoding=encoding, newline="") as f:
        r = csv.reader(f)
        header = next(r, None)
    if not header:
        raise SystemExit("CSV header is empty.")

    cleaned = []
    for i, h in enumerate(header):
        h = str(h).strip().strip('"')
        if h == "":
            h = f"__EMPTY_{i}__"
        cleaned.append(h)
    return cleaned


def parse_dt_series(date_s: pd.Series, time_s: pd.Series) -> pd.Series:
    """
    Fast, consistent parsing for HWiNFO:
      Date: dd.mm.yyyy
      Time: hh:mm:ss.mmm (sometimes ss without leading zero)
    """
    time_fixed = time_s.map(normalize_time)
    s = date_s.astype(str) + " " + time_fixed.astype(str)

    dt = pd.to_datetime(s, format="%d.%m.%Y %H:%M:%S.%f", errors="coerce")
    if dt.notna().any():
        return dt

    dt = pd.to_datetime(s, format="%d.%m.%Y %H:%M:%S", errors="coerce")
    if dt.notna().any():
        return dt

    return pd.to_datetime(s, dayfirst=True, errors="coerce")


def add_spd_hub_max(df: pd.DataFrame) -> None:
    """Add derived: 'SPD Hub Max [°C]' = row-wise max of all SPD Hub Temperature columns."""
    spd_cols = [
        c for c in df.columns
        if re.match(r"^SPD Hub Temperature \[°C\](\s#\d+)?$", str(c))
    ]
    if not spd_cols:
        return

    spd_numeric = df[spd_cols].apply(pd.to_numeric, errors="coerce")
    df["SPD Hub Max [°C]"] = spd_numeric.max(axis=1)


def is_spd_individual(col: str) -> bool:
    return bool(re.match(r"^SPD Hub Temperature \[°C\](\s#\d+)?$", str(col)))


def select_series(df: pd.DataFrame, patterns: List[str]) -> List[str]:
    """
    - Prefer exact column name match (case-insensitive).
    - Regex fallback.
    - Never plot individual SPD Hub Temperature columns; use SPD Hub Max if SPD requested.
    """
    cols = [c for c in df.columns if c not in ("Date", "Time") and not str(c).startswith("__EMPTY_")]

    # case-insensitive exact map
    exact_map = {str(c).lower(): c for c in cols}

    selected: List[str] = []
    patterns = patterns or []

    for p in patterns:
        p = str(p)
        key = p.lower()

        if key in exact_map:
            selected.append(exact_map[key])
            continue

        try:
            rx = re.compile(p, re.I)
        except re.error:
            continue

        for c in cols:
            if rx.search(str(c)):
                selected.append(c)

    # dedupe preserving order
    seen = set()
    selected = [c for c in selected if not (c in seen or seen.add(c))]

    # SPD logic
    spd_requested = any("spd hub" in str(p).lower() for p in patterns) or any(is_spd_individual(c) for c in selected)
    selected = [c for c in selected if not is_spd_individual(c)]
    if spd_requested and "SPD Hub Max [°C]" in df.columns and "SPD Hub Max [°C]" not in selected:
        selected.append("SPD Hub Max [°C]")

    return selected


def build_time_axis(df: pd.DataFrame) -> pd.Series:
    if "Date" in df.columns and "Time" in df.columns:
        dt = parse_dt_series(df["Date"], df["Time"])
        if dt.notna().any():
            t0 = dt.dropna().iloc[0]
            return (dt - t0).dt.total_seconds()
    return pd.Series(range(len(df)), index=df.index, dtype="float64")


def load_hwinfo_window_df(
    csv_path: Path,
    names: List[str],
    encoding: str,
    window_start: Optional[str],
    window_end: Optional[str],
    chunksize: int = 25000,
) -> Tuple[pd.DataFrame, Optional[pd.Timestamp], Optional[pd.Timestamp], int]:
    """
    Stream-read CSV in chunks (low RAM) and keep only rows within [window_start, window_end].
    Uses pandas C engine (default) so low_memory+chunksize works.
    """
    if (window_start is None) ^ (window_end is None):
        raise SystemExit("Provide both --window-start and --window-end, or neither.")

    # common kwargs (C engine)
    base_kwargs = dict(
        header=None,
        names=names,
        skiprows=1,
        encoding=encoding,
        low_memory=True,
    )

    if window_start is None and window_end is None:
        df = pd.read_csv(csv_path, **base_kwargs)
        df = df.loc[:, ~df.columns.astype(str).str.startswith("__EMPTY_")]
        return df, None, None, int(df.shape[0])

    ws = pd.to_datetime(window_start, errors="coerce")
    we = pd.to_datetime(window_end, errors="coerce")
    if pd.isna(ws) or pd.isna(we):
        raise SystemExit("Could not parse window-start/end. Use: YYYY-MM-DD HH:MM:SS[.mmm]")
    if we < ws:
        raise SystemExit("window-end is earlier than window-start.")

    kept = []
    first_ts = None
    last_ts = None
    total_rows = 0
    done = False

    for chunk in pd.read_csv(csv_path, chunksize=chunksize, **base_kwargs):
        if done:
            break

        chunk = chunk.loc[:, ~chunk.columns.astype(str).str.startswith("__EMPTY_")]

        if "Date" not in chunk.columns or "Time" not in chunk.columns:
            raise SystemExit("CSV missing Date/Time columns; cannot window-filter.")

        dt = parse_dt_series(chunk["Date"], chunk["Time"])
        mask = (dt >= ws) & (dt <= we)

        if mask.any():
            sub = chunk.loc[mask].copy()
            kept.append(sub)
            dt_sub = dt.loc[mask].dropna()
            total_rows += int(sub.shape[0])

            if not dt_sub.empty:
                if first_ts is None:
                    first_ts = dt_sub.iloc[0]
                last_ts = dt_sub.iloc[-1]

        dt_valid = dt.dropna()
        if not dt_valid.empty and dt_valid.max() > we:
            done = True

    if not kept:
        raise SystemExit("Geen rijen gevonden binnen het opgegeven tijdvenster.")

    df_out = pd.concat(kept, ignore_index=True)
    return df_out, first_ts, last_ts, total_rows


# ----------------- main -----------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--patterns", nargs="*", default=[])

    ap.add_argument("--window-start", default=None)
    ap.add_argument("--window-end", default=None)
    ap.add_argument("--export-window-csv", action="store_true")

    args = ap.parse_args()

    csv_path = Path(args.csv)
    outdir = Path(args.out)
    outdir.mkdir(parents=True, exist_ok=True)

    if not csv_path.exists():
        raise SystemExit(f"CSV not found: {csv_path}")

    encoding = sniff_encoding(csv_path)
    raw_header = read_raw_header(csv_path, encoding)
    names = make_unique(raw_header)

    df, first_ts, last_ts, rowcount = load_hwinfo_window_df(
        csv_path=csv_path,
        names=names,
        encoding=encoding,
        window_start=args.window_start,
        window_end=args.window_end,
    )

    add_spd_hub_max(df)

    selected = select_series(df, args.patterns)
    if not selected:
        raise SystemExit("Geen kolommen geselecteerd. Check --patterns against your CSV headers.")

    # export small run_window.csv
    if args.export_window_csv:
        cols = []
        if "Date" in df.columns:
            cols.append("Date")
        if "Time" in df.columns:
            cols.append("Time")
        cols += [c for c in selected if c in df.columns and c not in cols]

        (outdir / "run_window.csv").write_text(df[cols].to_csv(index=False), encoding="utf-8")

        if args.window_start and args.window_end:
            (outdir / "window_check.txt").write_text(
                "\n".join(
                    [
                        f"window_start_requested={args.window_start}",
                        f"window_end_requested={args.window_end}",
                        f"rows_in_slice={rowcount}",
                        f"first_timestamp_in_slice={first_ts}",
                        f"last_timestamp_in_slice={last_ts}",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

    x = build_time_axis(df)

    summary = []
    for c in selected:
        y = pd.to_numeric(df[c], errors="coerce")
        if y.notna().sum() < 2:
            continue

        plt.figure()
        plt.plot(x, y)
        plt.xlabel("Time (s)")
        plt.ylabel("Value")
        plt.title(str(c))
        plt.tight_layout()
        plt.savefig(outdir / f"{sanitize(str(c))}.png", dpi=160)
        plt.close()

        summary.append({"sensor": str(c), "min": float(y.min()), "max": float(y.max()), "avg": float(y.mean())})

    plt.figure()
    for c in selected:
        y = pd.to_numeric(df[c], errors="coerce")
        if y.notna().sum() < 2:
            continue
        plt.plot(x, y, label=str(c))
    plt.xlabel("Time (s)")
    plt.ylabel("Value")
    plt.title("Selected sensors")
    plt.legend(fontsize=7)
    plt.tight_layout()
    plt.savefig(outdir / "ALL_SELECTED.png", dpi=160)
    plt.close()

    pd.DataFrame(summary).to_csv(outdir / "summary.csv", index=False)


if __name__ == "__main__":
    main()
