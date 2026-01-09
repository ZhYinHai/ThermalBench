# graph_stats_helpers.py
"""Helper functions for calculating statistics from sensor data."""

from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd


def stats_from_summary_csv(csv_path: str) -> dict[str, tuple[float, float, float]]:
    """
    Load statistics from a summary.csv file in the same directory as the data CSV.
    
    Args:
        csv_path: Path to the main CSV file (summary.csv should be in same dir)
    
    Returns:
        Dictionary mapping sensor names to (min, max, avg) tuples
    """
    try:
        if not csv_path:
            return {}
        p = Path(csv_path).parent / "summary.csv"
        if not p.exists():
            return {}

        df = pd.read_csv(p)
        if df.empty:
            return {}

        cols = {str(c).strip().lower(): c for c in df.columns}

        name_col = None
        for key in ("measurement", "sensor", "name", "metric"):
            if key in cols:
                name_col = cols[key]
                break
        if name_col is None:
            name_col = df.columns[0]

        def pick(*keys):
            for k in keys:
                for low, orig in cols.items():
                    if k in low:
                        return orig
            return None

        min_col = pick("min")
        max_col = pick("max")
        avg_col = pick("avg", "mean", "average")

        if not (min_col and max_col and avg_col):
            return {}

        out = {}
        for _, r in df.iterrows():
            name = str(r[name_col]).strip()
            if not name:
                continue
            out[name] = (
                float(pd.to_numeric(r[min_col], errors="coerce")),
                float(pd.to_numeric(r[max_col], errors="coerce")),
                float(pd.to_numeric(r[avg_col], errors="coerce")),
            )
        return out
    except Exception:
        return {}


def stats_from_dataframe(df: Optional[pd.DataFrame]) -> dict[str, tuple[float, float, float]]:
    """
    Calculate min/max/avg statistics for all columns in a dataframe.
    
    Args:
        df: DataFrame with numeric sensor data
    
    Returns:
        Dictionary mapping column names to (min, max, avg) tuples
    """
    out = {}
    try:
        if df is None:
            return out
        for c in df.columns:
            y = pd.to_numeric(df[c], errors="coerce").to_numpy(dtype=float)
            if y.size == 0:
                continue
            finite = np.isfinite(y)
            if not finite.any():
                out[str(c)] = (float("nan"), float("nan"), float("nan"))
                continue
            out[str(c)] = (
                float(np.nanmin(y)),
                float(np.nanmax(y)),
                float(np.nanmean(y)),
            )
    except Exception:
        pass
    return out


def infer_stats_title(available_columns: list[str]) -> str:
    """
    Infer an appropriate title for the stats popup based on column names.
    
    Args:
        available_columns: List of column/sensor names
    
    Returns:
        Appropriate title string
    """
    cols = [str(c).lower() for c in (available_columns or [])]
    if any("°c" in c or "[°c]" in c for c in cols):
        return "Legend and Stats for Temperature (°C)"
    if any("rpm" in c for c in cols):
        return "Legend and Stats for Fan Speed (RPM)"
    if any("[w]" in c or " watt" in c or " w" in c for c in cols):
        return "Legend and Stats for Power (W)"
    return "Legend and Stats"
