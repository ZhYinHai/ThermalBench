# graph_plot_helpers.py
"""Helpers for CSV parsing and matplotlib plotting used by GraphPreview.

This module is intentionally thin and keeps behavior identical to the original
inline implementation in `ui/graph_preview.py`.
"""

from __future__ import annotations

import re
import numpy as np
import pandas as pd

import matplotlib.cm as cm
import matplotlib.dates as mdates
import matplotlib.patheffects as pe


import re
import numpy as np
import pandas as pd

import matplotlib.cm as cm
import matplotlib.dates as mdates
import matplotlib.patheffects as pe


def extract_unit_from_column(col_name: str) -> str:
    """Extract the unit from a column name (text inside brackets).
    
    Examples:
        'CPU (Tctl/Tdie) [°C]' -> '°C'
        'Memory Clock [MHz]' -> 'MHz'
        'Package C6 Residency [%]' -> '%'
        'Tcas [T]' -> 'T'
    """
    match = re.search(r'\[([^\]]+)\]', str(col_name))
    if match:
        return match.group(1)
    return "other"


def group_columns_by_unit(cols: list[str]) -> dict[str, list[str]]:
    """Group column names by their unit (text inside brackets).
    
    Returns a dictionary where keys are unit strings and values are lists of column names.
    Columns without units are grouped under 'other'.
    """
    groups: dict[str, list[str]] = {}
    for col in cols:
        unit = extract_unit_from_column(col)
        if unit not in groups:
            groups[unit] = []
        groups[unit].append(col)
    return groups


def get_measurement_type_label(unit: str) -> str:
    """Get a human-readable label for a measurement type based on unit.
    
    Maps common units to measurement categories.
    """
    unit_lower = str(unit).lower().strip()
    
    # Temperature
    if unit_lower in ('°c', 'c', '°f', 'f', 'k'):
        return "Temperature"
    
    # Power / Watt
    if unit_lower in ('w', 'watts', 'watt', 'mw', 'milliwatts'):
        return "Power (W)"
    
    # RPM / Speed
    if unit_lower in ('rpm', 'r/min', 'rev/min'):
        return "RPM"
    
    # Percentage
    if unit_lower in ('%', 'percent', 'percentage'):
        return "Percentage (%)"
    
    # Voltage
    if unit_lower in ('v', 'volt', 'volts', 'mv', 'millivolt'):
        return "Voltage (V)"
    
    # Frequency / Clock
    if unit_lower in ('mhz', 'ghz', 'khz', 'hz'):
        return "Clock (MHz)"
    
    # Timing
    if unit_lower in ('t', 'ns', 'nanosecond'):
        return "Timing (T)"
    
    # Default: use the unit itself
    return f"[{unit}]"


def load_run_csv_dataframe(fpath: str) -> tuple[pd.DataFrame, list[str]]:
    """Load the run CSV and return (df_data, cols) exactly like the original code."""
    df = pd.read_csv(fpath, header=0)
    if df.shape[0] == 0:
        raise RuntimeError("Empty CSV")

    if df.shape[1] >= 2:
        c0 = str(df.columns[0]).strip().lower()
        c1 = str(df.columns[1]).strip().lower()
    else:
        c0 = str(df.columns[0]).strip().lower()
        c1 = ""

    dt_index = None
    if c0 == "date" and c1 == "time":
        dt_index = pd.to_datetime(
            df.iloc[:, 0].astype(str) + " " + df.iloc[:, 1].astype(str),
            dayfirst=True,
            errors="coerce",
        )
        df_data = df.iloc[:, 2:].copy()
    else:
        dt_try = pd.to_datetime(df.iloc[:, 0].astype(str), dayfirst=True, errors="coerce")
        if dt_try.notna().any():
            dt_index = dt_try
            df_data = df.iloc[:, 1:].copy()
        else:
            df_data = df.select_dtypes(include=["number"]).copy()
            dt_index = None

    if dt_index is not None:
        df_data.index = dt_index
        df_data = df_data.loc[~df_data.index.isna()]
    else:
        df_data.index = pd.RangeIndex(start=0, stop=len(df_data))

    if df_data.empty:
        raise RuntimeError("No plottable columns found in CSV")

    cols: list[str] = []
    for c in list(df_data.columns):
        y = pd.to_numeric(df_data[c], errors="coerce")
        if y.notna().any():
            cols.append(str(c))
    if not cols:
        raise RuntimeError("No numeric series found in CSV")

    return df_data, cols


def compute_x_vals(df_data: pd.DataFrame) -> tuple[bool, np.ndarray]:
    is_dt = df_data.index.dtype.kind == "M"
    x_vals = mdates.date2num(df_data.index.to_pydatetime()) if is_dt else np.arange(len(df_data))
    return is_dt, x_vals


def apply_dark_axes_style(fig, ax, *, grid_color: str, dot_dashes) -> None:
    try:
        fig.set_facecolor("#121212")
    except Exception:
        pass

    try:
        ax.set_facecolor("#121212")
        for side in ("left", "right"):
            ax.spines[side].set_visible(False)
        for side in ("top", "bottom"):
            sp = ax.spines[side]
            sp.set_visible(True)
            sp.set_color(grid_color)
            sp.set_linewidth(0.9)
            sp.set_linestyle(dot_dashes)
            sp.set_alpha(0.9)

        ax.tick_params(axis="both", length=0)
        ax.tick_params(axis="x", colors="#BDBDBD")
        ax.tick_params(axis="y", colors="#BDBDBD")
        ax.xaxis.label.set_color("#EAEAEA")
        ax.yaxis.label.set_color("#EAEAEA")
    except Exception:
        pass

    try:
        ax.grid(True, which="major", axis="y", color=grid_color, linewidth=0.9)
        for gl in ax.get_ygridlines():
            gl.set_linestyle(dot_dashes)
            gl.set_alpha(0.9)
    except Exception:
        pass


def build_tab20_color_map(cols: list[str]) -> dict[str, str]:
    cmap = cm.get_cmap("tab20")
    color_map: dict[str, str] = {}
    for idx, name in enumerate(cols):
        colc = cmap(idx % 20)
        try:
            if isinstance(colc, tuple):
                import matplotlib.colors as mcolors

                colc = mcolors.to_hex(colc)
        except Exception:
            pass
        color_map[str(name)] = colc
    return color_map


def plot_lines_with_glow(
    ax,
    *,
    df_all: pd.DataFrame,
    cols: list[str],
    x_vals: np.ndarray,
    is_dt: bool,
    color_map: dict[str, str],
) -> tuple[dict[str, object], dict[str, np.ndarray], list[str]]:
    base_lw = 2.6
    glow_lw = base_lw + 2.0
    glow_alpha = 0.18

    line_kwargs = dict(
        linewidth=base_lw,
        alpha=0.98,
        solid_capstyle="round",
        solid_joinstyle="round",
        antialiased=True,
        zorder=10,
    )

    lines: dict[str, object] = {}
    series_data: dict[str, np.ndarray] = {}
    colors: list[str] = []

    for c in cols:
        y = pd.to_numeric(df_all[c], errors="coerce").to_numpy(dtype=float)
        colc = color_map.get(str(c), "#FFFFFF")
        colors.append(colc)

        if is_dt:
            ln = ax.plot_date(x_vals, y, "-", color=colc, **line_kwargs)[0]
        else:
            ln = ax.plot(x_vals, y, "-", color=colc, **line_kwargs)[0]

        try:
            ln.set_path_effects([
                pe.Stroke(linewidth=glow_lw, foreground=colc, alpha=glow_alpha),
                pe.Normal(),
            ])
        except Exception:
            pass

        lines[str(c)] = ln
        series_data[str(c)] = y

    return lines, series_data, colors


def apply_elapsed_time_formatter(ax, *, is_dt: bool, x_vals: np.ndarray) -> None:
    if not (is_dt and len(x_vals) > 0):
        return

    try:
        from matplotlib.ticker import FuncFormatter

        def elapsed_time_formatter(x, pos):
            try:
                dt_current = mdates.num2date(x)
                dt_start = mdates.num2date(x_vals[0])
                elapsed = dt_current - dt_start
                total_seconds = int(elapsed.total_seconds())
                hours = total_seconds // 3600
                minutes = (total_seconds % 3600) // 60
                seconds = total_seconds % 60
                if hours > 0:
                    return f"{hours}:{minutes:02d}:{seconds:02d}"
                return f"{minutes}:{seconds:02d}"
            except Exception:
                return ""

        ax.xaxis.set_major_formatter(FuncFormatter(elapsed_time_formatter))
        ax.set_xlabel("")
    except Exception:
        pass


def create_hover_vline(ax, *, x0: float, grid_color: str, dot_dashes):
    try:
        vline = ax.axvline(
            x0,
            color=grid_color,
            linewidth=0.9,
            alpha=0.9,
            zorder=900,
        )
        vline.set_linestyle(dot_dashes)
        vline.set_clip_on(True)
        vline.set_visible(False)
        vline.set_animated(True)
        return vline
    except Exception:
        return None


def trim_dataframes_to_shortest_duration(dfs: list[pd.DataFrame]) -> list[pd.DataFrame]:
    """Trim all dataframes to the shortest measured duration.

    - If all dfs use a datetime index, trims each df to:
        df.index <= df.index.min() + min_duration
      where min_duration is the smallest (max-min) duration across dfs.

    - Otherwise, trims by row count to the smallest length.

    This is used for compare-mode plotting where different runs may have
    different measured times/durations.
    """
    if not dfs:
        return []

    non_empty = [df for df in dfs if df is not None and not df.empty]
    if not non_empty:
        return [df for df in dfs]

    all_dt = all(getattr(df.index, "dtype", None) is not None and df.index.dtype.kind == "M" for df in non_empty)

    if all_dt:
        durations = []
        for df in non_empty:
            try:
                durations.append(df.index.max() - df.index.min())
            except Exception:
                pass

        if not durations:
            return [df for df in dfs]

        min_duration = min(durations)
        out: list[pd.DataFrame] = []
        for df in dfs:
            if df is None or df.empty:
                out.append(df)
                continue
            try:
                start = df.index.min()
                end = start + min_duration
                out.append(df.loc[df.index <= end])
            except Exception:
                out.append(df)
        return out

    # Fallback: trim by number of rows (works for RangeIndex and mixed indices)
    min_len = None
    for df in non_empty:
        try:
            ln = int(len(df))
        except Exception:
            continue
        if min_len is None or ln < min_len:
            min_len = ln

    if not min_len:
        return [df for df in dfs]

    out2: list[pd.DataFrame] = []
    for df in dfs:
        if df is None or df.empty:
            out2.append(df)
            continue
        try:
            out2.append(df.iloc[:min_len].copy())
        except Exception:
            out2.append(df)
    return out2
