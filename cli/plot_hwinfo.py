# plot_hwinfo.py
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
import json
import re
import sys
import unicodedata
from pathlib import Path
from typing import Optional, Tuple, List

import pandas as pd
import matplotlib.pyplot as plt

# Apply dark plotting theme similar to provided image
plt.rcParams.update({
    "figure.facecolor": "#121212",
    "axes.facecolor": "#121212",
    "axes.edgecolor": "#2A2A2A",
    "axes.labelcolor": "#EAEAEA",
    "xtick.color": "#BDBDBD",
    "ytick.color": "#BDBDBD",
    "text.color": "#EAEAEA",
    "grid.color": "#2A2A2A",
    "grid.linestyle": ":",
    "grid.linewidth": 0.6,
    "axes.grid": True,
    "legend.frameon": False,
})

# default line color palette (teal-ish)
LINE_COLOR = "#1BE7C7"


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
    """Best-effort encoding detection for HWiNFO CSV.

    HWiNFO CSV exports are typically ANSI/Windows-1252 or UTF-8, but some
    systems/tools may produce UTF-16 with BOM.
    """
    try:
        head = path.read_bytes()[:4]
    except Exception:
        head = b""

    if head.startswith(b"\xff\xfe") or head.startswith(b"\xfe\xff"):
        return "utf-16"
    if head.startswith(b"\xef\xbb\xbf"):
        return "utf-8-sig"

    for enc in ("utf-8", "utf-8-sig", "cp1252"):
        try:
            with open(path, "r", encoding=enc, newline="") as f:
                f.readline()
            return enc
        except Exception:
            continue
    return "cp1252"


def norm_hwinfo_text(s: str) -> str:
    """Normalize common HWiNFO header/pattern text issues.

    - Fix broken degree symbol renderings ("\ufffdC", "\u00c2\u00b0C").
    - Apply Unicode normalization and whitespace cleanup.
    """
    s = str(s)

    # Common mojibake / replacement-char variants
    s = s.replace("[\ufffdC]", "[\u00b0C]").replace("\ufffdC", "\u00b0C")
    s = s.replace("\u00c2\u00b0", "\u00b0")  # "Â°" -> "°"
    s = s.replace("[\u00c2\u00b0C]", "[\u00b0C]")

    s = unicodedata.normalize("NFKC", s)
    s = s.replace("\u00a0", " ")
    s = re.sub(r"\s+", " ", s).strip()
    s = s.strip().strip('"')
    return s


def read_raw_header(path: Path, encoding: str) -> List[str]:
    with open(path, "r", encoding=encoding, newline="") as f:
        r = csv.reader(f)
        header = next(r, None)
    if not header:
        raise SystemExit("CSV header is empty.")

    cleaned = []
    for i, h in enumerate(header):
        h = norm_hwinfo_text(h)
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
    """Add derived: 'SPD Hub Max [°C]' from SPD Hub sensors.

    Definition:
    - Find all columns matching "SPD Hub Temperature [°C]".
    - Compute the average of each hub over the window.
    - Pick the single hub with the highest average, and use *that hub's
      time-series* as the derived "SPD Hub Max [°C]".
    """
    spd_cols = [
        c for c in df.columns
        if re.match(r"^SPD Hub Temperature \[°C\](\s#\d+)?$", str(c))
    ]
    if not spd_cols:
        return

    spd_numeric = df[spd_cols].apply(pd.to_numeric, errors="coerce")
    means = spd_numeric.mean(axis=0, skipna=True)
    means = means.dropna()
    if means.empty:
        return

    best_col = str(means.idxmax())
    df["SPD Hub Max [°C]"] = spd_numeric[best_col]


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
    patterns = [norm_hwinfo_text(p) for p in (patterns or [])]

    for p in patterns:
        p = str(p)
        key = p.lower()

        # if it looks like an exact CSV column, require exact match
        looks_exact = ("[" in p) or (" #" in p)

        if key in exact_map:
            selected.append(exact_map[key])
            continue

        if looks_exact:
            # don't regex-match "almost the same" and pick the wrong duplicate
            continue

        # only for non-exact patterns allow regex fallback
        try:
            rx = re.compile(p, re.I)
        except re.error:
            continue

        for c in cols:
            if rx.search(str(c)):
                selected.append(c)

    # Allow requesting this virtual/derived series without it existing as a raw CSV header.
    virtual_exact = {"spd hub max [°c]"}

    missing_exact = [
        p for p in patterns
        if (("[" in str(p)) or (" #" in str(p)))
        and (str(p).lower() not in exact_map)
        and (str(p).lower() not in virtual_exact)
    ]
    if missing_exact:
        raise SystemExit("Exact columns not found in CSV:\n- " + "\n- ".join(missing_exact))


    # dedupe preserving order
    seen = set()
    selected = [c for c in selected if not (c in seen or seen.add(c))]

    # SPD logic
    spd_max_requested = any(str(p).lower() == "spd hub max [°c]" for p in patterns)
    spd_requested = spd_max_requested or any("spd hub" in str(p).lower() for p in patterns) or any(is_spd_individual(c) for c in selected)
    selected = [c for c in selected if not is_spd_individual(c)]

    if spd_requested and "SPD Hub Max [°C]" not in df.columns:
        print(
            "[WARN] Requested SPD Hub Max, but no 'SPD Hub Temperature [°C]' columns were found in the CSV to compute it. "
            "Skipping SPD Hub Max.",
            file=sys.stderr,
        )

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
        usecols=range(len(names)),  # Only read columns we have names for
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


def _load_ambient_log_df(ambient_csv: Path) -> pd.DataFrame:
    """Load ambient log CSV produced by ambient_logger.py.

    Expected columns:
      - timestamp: "YYYY-MM-DD HH:MM:SS.mmm"
      - ambient_c: float
    """
    df = pd.read_csv(ambient_csv, header=0)
    if df.shape[0] == 0:
        return pd.DataFrame(columns=["dt", "ambient_c"])

    # Normalize column names
    cols = {str(c).strip().lower(): str(c) for c in df.columns}
    ts_col = cols.get("timestamp") or cols.get("time") or cols.get("datetime")
    v_col = cols.get("ambient_c") or cols.get("ambient") or cols.get("value")
    if not ts_col or not v_col:
        return pd.DataFrame(columns=["dt", "ambient_c"])

    dt = pd.to_datetime(df[ts_col].astype(str), errors="coerce")
    amb = pd.to_numeric(df[v_col], errors="coerce")
    out = pd.DataFrame({"dt": dt, "ambient_c": amb})
    out = out.dropna(subset=["dt"]).sort_values("dt")
    return out


def _merge_ambient_into_hwinfo_df(
    *,
    df_hw: pd.DataFrame,
    ambient_df: pd.DataFrame,
    window_start: Optional[str],
    window_end: Optional[str],
    out_col_name: str,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Merge ambient readings into the HWiNFO window df by nearest timestamp.

    Returns (df_hw_with_ambient, ambient_window_df).
    """
    if df_hw is None or df_hw.empty:
        return df_hw, pd.DataFrame()
    if ambient_df is None or ambient_df.empty:
        return df_hw, pd.DataFrame()
    if "Date" not in df_hw.columns or "Time" not in df_hw.columns:
        return df_hw, pd.DataFrame()

    dt_hw = parse_dt_series(df_hw["Date"], df_hw["Time"])
    if not dt_hw.notna().any():
        return df_hw, pd.DataFrame()

    ws = pd.to_datetime(window_start, errors="coerce") if window_start else None
    we = pd.to_datetime(window_end, errors="coerce") if window_end else None

    tol = pd.Timedelta(seconds=2)

    amb = ambient_df.copy()
    if ws is not None and we is not None and (not pd.isna(ws)) and (not pd.isna(we)):
        # Include a small pad so boundary rows still merge (nearest within tolerance).
        amb = amb[(amb["dt"] >= (ws - tol)) & (amb["dt"] <= (we + tol))]
    if amb.empty:
        return df_hw, pd.DataFrame()

    # Align ambient to each hwinfo row by nearest timestamp.
    left = pd.DataFrame({"_row": df_hw.index.to_numpy(), "dt": dt_hw})
    left = left.dropna(subset=["dt"]).sort_values("dt")

    merged = pd.merge_asof(
        left,
        amb[["dt", "ambient_c"]].sort_values("dt"),
        on="dt",
        direction="nearest",
        tolerance=tol,
    )

    # Create full-length aligned series
    aligned = pd.Series(index=df_hw.index, dtype="float64")
    try:
        aligned.loc[merged["_row"].to_numpy()] = merged["ambient_c"].to_numpy(dtype=float)
    except Exception:
        # fallback: best-effort assignment
        for _, r in merged.iterrows():
            try:
                aligned.loc[int(r["_row"])] = float(r["ambient_c"]) if pd.notna(r["ambient_c"]) else float("nan")
            except Exception:
                continue

    df_hw = df_hw.copy()
    df_hw[out_col_name] = aligned

    amb_window = amb.copy()
    return df_hw, amb_window


# ----------------- main -----------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--patterns", nargs="*", default=[])

    ap.add_argument("--window-start", default=None)
    ap.add_argument("--window-end", default=None)
    ap.add_argument("--export-window-csv", action="store_true")

    ap.add_argument("--ambient-csv", default=None, help="Optional ambient log CSV (timestamp, ambient_c)")
    ap.add_argument("--ambient-col-name", default="Ambient [°C]", help="Column name to use in outputs")

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

    # Normalize loaded column names (helps with degree symbol / whitespace issues)
    df.columns = make_unique([norm_hwinfo_text(c) for c in df.columns])

    add_spd_hub_max(df)

    # Best-effort: merge ambient log as a virtual temperature series.
    ambient_window_df = pd.DataFrame()
    try:
        if args.ambient_csv:
            amb_path = Path(str(args.ambient_csv))
            if amb_path.exists() and amb_path.is_file():
                amb_df = _load_ambient_log_df(amb_path)
                df, ambient_window_df = _merge_ambient_into_hwinfo_df(
                    df_hw=df,
                    ambient_df=amb_df,
                    window_start=args.window_start,
                    window_end=args.window_end,
                    out_col_name=str(args.ambient_col_name),
                )
    except Exception:
        ambient_window_df = pd.DataFrame()

    selected = select_series(df, args.patterns)

    # Ensure ambient shows up in run_window.csv + plots + summary when available.
    try:
        amb_name = str(args.ambient_col_name)
        if amb_name in df.columns and amb_name not in selected:
            selected.append(amb_name)
    except Exception:
        pass
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

        # Also export the ambient window slice for auditing (if we have it).
        try:
            if ambient_window_df is not None and not ambient_window_df.empty:
                (outdir / "ambient_window.csv").write_text(
                    ambient_window_df.to_csv(index=False),
                    encoding="utf-8",
                )
        except Exception:
            pass

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

        fig = plt.figure(figsize=(8, 3.5))
        ax = fig.gca()
        ax.plot(x, y, color=LINE_COLOR, linewidth=2.2)
        ax.set_xlabel("Time (s)")
        ax.set_ylabel("Value")
        ax.set_title(str(c))
        ax.grid(True, linestyle=':', color="#2A2A2A", linewidth=0.6)
        # style spines subtle
        for sp in ax.spines.values():
            sp.set_color("#2A2A2A")
        fig.tight_layout()
        fig.savefig(outdir / f"{sanitize(str(c))}.png", dpi=160, facecolor=fig.get_facecolor())
        plt.close(fig)

        summary.append({"sensor": str(c), "min": float(y.min()), "max": float(y.max()), "avg": float(y.mean())})

    fig = plt.figure(figsize=(10, 4))
    ax = fig.gca()
    # color map for distinct series
    cmap = plt.get_cmap("tab20")
    color_count = cmap.N
    for i, c in enumerate(selected):
        y = pd.to_numeric(df[c], errors="coerce")
        if y.notna().sum() < 2:
            continue
        color = cmap(i % color_count)
        ax.plot(x, y, linewidth=2.4, color=color, label=str(c))

    ax.set_xlabel("Time (s)")
    ax.set_ylabel("Value")
    ax.set_title("Selected sensors")
    # subtle dotted grid lines horizontally
    ax.grid(True, which='both', axis='y', linestyle=':', color="#2A2A2A", linewidth=0.6)
    # style spines and ticks
    for sp in ax.spines.values():
        sp.set_color("#2A2A2A")
    ax.tick_params(axis='x', colors="#BDBDBD")
    ax.tick_params(axis='y', colors="#BDBDBD")

    # legend small, semi-transparent background matching theme
    leg = ax.legend(fontsize=8, frameon=True)
    if leg:
        try:
            leg.get_frame().set_facecolor('#171717')
            leg.get_frame().set_edgecolor('#2A2A2A')
            leg.get_frame().set_alpha(0.9)
        except Exception:
            pass

    fig.tight_layout()
    fig.savefig(outdir / "ALL_SELECTED.png", dpi=160, facecolor=fig.get_facecolor())
    plt.close(fig)

    pd.DataFrame(summary).to_csv(outdir / "summary.csv", index=False)

    # NOTE: We no longer create avg_temperature.json for new runs.
    # Ambient temperature for deltas is sourced from the ambient sensor series
    # (merged into run_window.csv) and legacy runs can still provide the JSON.


if __name__ == "__main__":
    main()
