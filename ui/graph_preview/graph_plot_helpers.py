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


def get_mpl_cmap(name: str):
    """Return a Matplotlib colormap across old and new Matplotlib APIs."""
    get_cmap = getattr(cm, "get_cmap", None)
    if callable(get_cmap):
        return get_cmap(name)

    from matplotlib import colormaps

    return colormaps[name]


def _parse_dt_flexible(s: pd.Series) -> pd.Series:
    """Parse a datetime Series, supporting both EU (dd.mm.yyyy) and Asia (yyyy/mm/dd) formats."""
    for fmt in (
        "%d.%m.%Y %H:%M:%S.%f",   # NL/EU: 06.05.2026 13:23:01.975
        "%d.%m.%Y %H:%M:%S",       # NL/EU without ms
        "%Y/%m/%d %H:%M:%S.%f",   # TW/Asia: 2026/05/06 13:23:01.975
        "%Y/%m/%d %H:%M:%S",       # TW/Asia without ms
        "%Y-%m-%d %H:%M:%S.%f",   # ISO fallback
        "%Y-%m-%d %H:%M:%S",       # ISO fallback without ms
    ):
        dt = pd.to_datetime(s, format=fmt, errors="coerce")
        if dt.notna().any():
            return dt
    return pd.to_datetime(s, dayfirst=True, errors="coerce")


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
        unit = str(match.group(1))
        # Normalize common CSV encoding artifacts (e.g. 'Â°C', 'Â%').
        try:
            unit = unit.replace("\ufeff", "")
            unit = unit.replace("\u00a0", " ")
            unit = unit.replace("Â", "")
            unit = unit.strip()
        except Exception:
            pass
        return unit
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
    df = pd.read_csv(fpath, header=0, engine="c", low_memory=False)
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
        dt_index = _parse_dt_flexible(
            df.iloc[:, 0].astype(str) + " " + df.iloc[:, 1].astype(str)
        )
        df_data = df.iloc[:, 2:].copy()
    else:
        dt_try = _parse_dt_flexible(df.iloc[:, 0].astype(str))
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


def apply_light_axes_style(fig, ax, *, grid_color: str, dot_dashes) -> None:
    try:
        fig.set_facecolor("#FFFFFF")
    except Exception:
        pass

    try:
        ax.set_facecolor("#FFFFFF")
        for side in ("left", "right"):
            ax.spines[side].set_visible(False)
        for side in ("top", "bottom"):
            sp = ax.spines[side]
            sp.set_visible(True)
            sp.set_color(grid_color)
            sp.set_linewidth(0.9)
            sp.set_linestyle(dot_dashes)
            sp.set_alpha(0.95)

        ax.tick_params(axis="both", length=0)
        ax.tick_params(axis="x", colors="#666666")
        ax.tick_params(axis="y", colors="#666666")
        ax.xaxis.label.set_color("#1A1A1A")
        ax.yaxis.label.set_color("#1A1A1A")
    except Exception:
        pass

    try:
        ax.grid(True, which="major", axis="y", color=grid_color, linewidth=0.9)
        for gl in ax.get_ygridlines():
            gl.set_linestyle(dot_dashes)
            gl.set_alpha(0.95)
    except Exception:
        pass


def build_tab20_color_map(cols: list[str]) -> dict[str, str]:
    cmap = get_mpl_cmap("tab20")
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
    # Thinner series lines for readability with many sensors/runs.
    base_lw = 1.6
    glow_lw = base_lw + 1.2
    glow_alpha = 0.16

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

        # Matplotlib 3.11 removed Axes.plot_date; date values are already
        # converted to Matplotlib floats by compute_x_vals(), so plot() works
        # for both datetime and numeric x data.
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

    def _trim_by_row_count() -> list[pd.DataFrame]:
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

        out_rows: list[pd.DataFrame] = []
        for df in dfs:
            if df is None or df.empty:
                out_rows.append(df)
                continue
            try:
                out_rows.append(df.iloc[:min_len].copy())
            except Exception:
                out_rows.append(df)
        return out_rows

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
        try:
            if getattr(min_duration, "total_seconds", lambda: 0.0)() <= 0.0:
                return _trim_by_row_count()
        except Exception:
            pass

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

        try:
            trimmed_non_empty = [df for df in out if df is not None and not df.empty]
            if trimmed_non_empty:
                trimmed_min_len = min(int(len(df)) for df in trimmed_non_empty)
                original_min_len = min(int(len(df)) for df in non_empty)
                if trimmed_min_len < 2 <= original_min_len:
                    return _trim_by_row_count()
        except Exception:
            pass
        return out

    # Fallback: trim by number of rows (works for RangeIndex and mixed indices)
    return _trim_by_row_count()
