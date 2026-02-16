"""Tooltip/hover + blitting helpers extracted from `ui/graph_preview.py`.

These functions intentionally keep the original logic and ordering,
but include performance optimizations for large numbers of selected sensors.
"""

from __future__ import annotations

import time
from typing import Any

import numpy as np

import matplotlib.dates as mdates
from matplotlib.offsetbox import AnnotationBbox, TextArea, VPacker, HPacker
import matplotlib.patheffects as pe


def on_preview_draw(gp: Any, event=None) -> None:
    try:
        if gp._preview_canvas is None or gp._preview_ax is None:
            return
        gp._preview_bg = gp._preview_canvas.copy_from_bbox(gp._preview_ax.bbox)
        renderer = gp._preview_canvas.get_renderer()
        if renderer is not None:
            gp._preview_ax_bbox = gp._preview_ax.get_window_extent(renderer)
            if gp._ls_btn_text is not None:
                gp._ls_btn_bbox = gp._ls_btn_text.get_window_extent(renderer)
            else:
                gp._ls_btn_bbox = None

            # Temperature delta toggle button: place it just to the left of the
            # Legend & stats button and cache its bbox for hit testing.
            try:
                if getattr(gp, "_delta_btn_text", None) is not None:
                    # If we have the LS bbox, position the delta button by pixel offset.
                    if gp._ls_btn_bbox is not None:
                        gap_px = 14.0
                        # Find the desired RIGHT edge in display coords.
                        desired_right_x = float(gp._ls_btn_bbox.x0) - gap_px

                        # Use the delta button's own axes for transforms (works in multi-axis too).
                        ax_btn = getattr(gp._delta_btn_text, "axes", None) or gp._preview_ax
                        y_axes = 0.995
                        try:
                            # For multi-axis mode, delta/ls are drawn at y=1.02; keep the same anchor.
                            _px, _py = gp._delta_btn_text.get_position()
                            y_axes = float(_py)
                        except Exception:
                            y_axes = 0.995

                        _x0_disp, y0_disp = ax_btn.transAxes.transform((0.995, y_axes))
                        new_axes_x, _ = ax_btn.transAxes.inverted().transform((desired_right_x, y0_disp))
                        try:
                            gp._delta_btn_text.set_position((float(new_axes_x), float(y_axes)))
                        except Exception:
                            pass

                    gp._delta_btn_bbox = gp._delta_btn_text.get_window_extent(renderer)
                else:
                    gp._delta_btn_bbox = None
            except Exception:
                gp._delta_btn_bbox = None

            # Zero-Y toggle button: place it just to the left of the ΔT button
            # and cache its bbox for hit testing.
            try:
                if getattr(gp, "_zero_btn_text", None) is not None:
                    if getattr(gp, "_delta_btn_bbox", None) is not None:
                        gap_px = 18.0
                        desired_right_x = float(gp._delta_btn_bbox.x0) - gap_px

                        ax_btn = getattr(gp._zero_btn_text, "axes", None) or gp._preview_ax
                        y_axes = 0.995
                        try:
                            _px, _py = gp._zero_btn_text.get_position()
                            y_axes = float(_py)
                        except Exception:
                            y_axes = 0.995

                        _x0_disp, y0_disp = ax_btn.transAxes.transform((0.995, y_axes))
                        new_axes_x, _ = ax_btn.transAxes.inverted().transform((desired_right_x, y0_disp))
                        try:
                            gp._zero_btn_text.set_position((float(new_axes_x), float(y_axes)))
                        except Exception:
                            pass

                    gp._zero_btn_bbox = gp._zero_btn_text.get_window_extent(renderer)

                    # If styling changes (active state) make the bbox wider, ensure we still have
                    # a clean gap by nudging left once if needed.
                    try:
                        if gp._delta_btn_bbox is not None and gp._zero_btn_bbox is not None:
                            min_gap = 14.0
                            max_right = float(gp._delta_btn_bbox.x0) - float(min_gap)
                            if float(gp._zero_btn_bbox.x1) > max_right:
                                shift = float(gp._zero_btn_bbox.x1) - max_right
                                ax_btn = getattr(gp._zero_btn_text, "axes", None) or gp._preview_ax
                                _px, _py = gp._zero_btn_text.get_position()
                                x_disp, y_disp = ax_btn.transAxes.transform((float(_px), float(_py)))
                                new_axes_x2, _ = ax_btn.transAxes.inverted().transform((float(x_disp) - shift, float(y_disp)))
                                gp._zero_btn_text.set_position((float(new_axes_x2), float(_py)))
                                gp._zero_btn_bbox = gp._zero_btn_text.get_window_extent(renderer)
                    except Exception:
                        pass
                else:
                    gp._zero_btn_bbox = None
            except Exception:
                gp._zero_btn_bbox = None
    except Exception:
        pass


def preview_blit(gp: Any) -> None:
    try:
        if gp._preview_canvas is None or gp._preview_ax is None:
            return
        if gp._preview_bg is None:
            gp._preview_canvas.draw_idle()
            return

        c = gp._preview_canvas
        ax = gp._preview_ax
        c.restore_region(gp._preview_bg)

        if getattr(gp, "_preview_vline", None) is not None and gp._preview_vline.get_visible():
            ax.draw_artist(gp._preview_vline)

        ab = getattr(gp, "_preview_collective_box", None)
        if ab is not None and ab.get_visible():
            ax.draw_artist(ab)

        c.blit(ax.bbox)
    except Exception:
        try:
            if gp._preview_canvas is not None:
                gp._preview_canvas.draw_idle()
        except Exception:
            pass


def preview_invalidate_interaction_cache(gp: Any) -> None:
    try:
        gp._preview_bg = None
        gp._preview_ax_bbox = None
        gp._preview_tt_w_px = None
        gp._preview_tt_h_px = None
        gp._preview_tt_mode = "UR"
        gp._ls_btn_bbox = None
        gp._delta_btn_bbox = None
        gp._zero_btn_bbox = None
    except Exception:
        pass
    try:
        gp._tt_anim_timer.stop()
    except Exception:
        pass
    try:
        ab = getattr(gp, "_preview_collective_box", None)
        if ab is not None:
            ab.set_visible(False)
    except Exception:
        pass
    try:
        vl = getattr(gp, "_preview_vline", None)
        if vl is not None:
            vl.set_visible(False)
    except Exception:
        pass


def safe_preview_redraw(gp: Any) -> None:
    try:
        if gp._preview_canvas is None:
            return
        if not gp._preview_canvas.isVisible():
            return
        gp._preview_canvas.draw()
        try:
            gp._preview_canvas.update()
        except Exception:
            pass
        gp._on_preview_draw()
    except Exception:
        try:
            gp._preview_canvas.draw_idle()
        except Exception:
            pass


def preview_update_tooltip_metrics(gp: Any) -> None:
    try:
        ab = getattr(gp, "_preview_collective_box", None)
        if ab is None or gp._preview_canvas is None or gp._preview_ax is None:
            return
        renderer = gp._preview_canvas.get_renderer()
        if renderer is None:
            return
        bbox = ab.get_window_extent(renderer)
        gp._preview_tt_w_px = float(bbox.width)
        gp._preview_tt_h_px = float(bbox.height)
        gp._preview_ax_bbox = gp._preview_ax.get_window_extent(renderer)
    except Exception:
        pass


def preview_update_tooltip_mode_for(gp: Any, xdata: float, ydata: float) -> None:
    ab = getattr(gp, "_preview_collective_box", None)
    ax = getattr(gp, "_preview_ax", None)
    canvas = getattr(gp, "_preview_canvas", None)
    fig = getattr(gp, "_preview_fig", None)

    if ab is None or ax is None or canvas is None or fig is None:
        return
    if xdata is None or ydata is None:
        return

    if gp._preview_tt_w_px is None or gp._preview_tt_h_px is None or gp._preview_ax_bbox is None:
        gp._preview_update_tooltip_metrics()
        if gp._preview_tt_w_px is None or gp._preview_tt_h_px is None or gp._preview_ax_bbox is None:
            return

    try:
        dpi = float(getattr(fig, "dpi", 100) or 100)
        margin = float(getattr(gp, "_preview_tt_margin_px", 4))
        cx, cy = ax.transData.transform((xdata, ydata))

        def pts_to_px(v):
            return float(v) * dpi / 72.0

        ur = getattr(gp, "_preview_tt_default_xybox", (10, 10))
        dr = getattr(gp, "_preview_tt_flipped_xybox", (10, -10))
        ul = getattr(gp, "_preview_tt_left_xybox", (-10, 10))
        dl = getattr(gp, "_preview_tt_left_down_xybox", (-10, -10))

        urx, ury = pts_to_px(ur[0]), pts_to_px(ur[1])

        w = float(gp._preview_tt_w_px)
        h = float(gp._preview_tt_h_px)

        ax_right = float(gp._preview_ax_bbox.x1) - margin
        ax_top = float(gp._preview_ax_bbox.y1) - margin

        ur_right = (cx + urx) + w
        ur_top = (cy + ury) + h

        overflow_top = ur_top > ax_top
        overflow_right = ur_right > ax_right

        if overflow_top and overflow_right:
            mode = "DL"
        elif overflow_top:
            mode = "DR"
        elif overflow_right:
            mode = "UL"
        else:
            mode = "UR"

        if mode != getattr(gp, "_preview_tt_mode", "UR"):
            gp._preview_tt_mode = mode
            if mode == "UR":
                ab._box_alignment = (0, 0)
                ab.xybox = ur
            elif mode == "DR":
                ab._box_alignment = (0, 1)
                ab.xybox = dr
            elif mode == "UL":
                ab._box_alignment = (1, 0)
                ab.xybox = ul
            else:
                ab._box_alignment = (1, 1)
                ab.xybox = dl
    except Exception:
        pass


def on_preview_hover(gp: Any, event) -> None:
    try:
        if not getattr(gp, "_app_is_active", True):
            return
        xdata = getattr(event, "xdata", None)
        ydata = getattr(event, "ydata", None)
        if xdata is None and ydata is None:
            return
        gp._on_preview_hover_xy(xdata, ydata)
    except Exception:
        pass


def hide_preview_hover(gp: Any, hard: bool = False) -> None:
    try:
        if getattr(gp, "_preview_vline", None) is not None:
            gp._preview_vline.set_visible(False)
    except Exception:
        pass
    try:
        if getattr(gp, "_preview_collective_box", None) is not None:
            gp._preview_collective_box.set_visible(False)
    except Exception:
        pass
    try:
        gp._tt_anim_timer.stop()
    except Exception:
        pass

    try:
        if hard or getattr(gp, "_preview_bg", None) is None:
            gp._safe_preview_redraw()
        else:
            gp._preview_blit()
    except Exception:
        pass


def format_value(gp: Any, col_name: str, val: float) -> str:
    try:
        if val != val:
            return "-"
        s = str(col_name).lower()
        if "[rpm]" in s or " rpm" in s or s.endswith("rpm"):
            try:
                return f"{int(round(float(val))):,} RPM"
            except Exception:
                return f"{val:.0f} RPM"
        if "°c" in s or "[°c]" in s:
            return f"{val:.2f} °C" if abs(val) < 100 else f"{val:.1f} °C"
        if "[w]" in s or " w" in s:
            return f"{val:.1f} W"
        if "[%]" in s or "%" in s:
            return f"{val:.1f} %"
        return f"{val:.3g}"
    except Exception:
        return "-"


def _nearest_index_sorted(x: np.ndarray, xdata: float) -> int:
    """Fast nearest index assuming x is sorted ascending (matplotlib date numbers are)."""
    try:
        n = int(x.size)
        if n <= 1:
            return 0
        i = int(np.searchsorted(x, xdata))
        if i <= 0:
            return 0
        if i >= n:
            return n - 1
        # choose closer neighbor
        left = float(x[i - 1])
        right = float(x[i])
        return i - 1 if abs(xdata - left) <= abs(xdata - right) else i
    except Exception:
        # safe fallback
        try:
            return int(np.argmin(np.abs(x - xdata)))
        except Exception:
            return 0


def on_preview_hover_xy(gp: Any, xdata: float, ydata: float) -> None:
    try:
        if not getattr(gp, "_app_is_active", True):
            return
        if gp._preview_df is None or gp._preview_x is None or gp._preview_ax is None:
            return
        if xdata is None:
            return

        # Resize invalidation (keep original behavior)
        try:
            if gp._preview_canvas is not None:
                wh = (int(gp._preview_canvas.width()), int(gp._preview_canvas.height()))
                if getattr(gp, "_preview_last_canvas_wh", None) != wh:
                    gp._preview_last_canvas_wh = wh
                    gp._preview_invalidate_interaction_cache()
                    gp._preview_relayout_and_redraw()
        except Exception:
            pass

        # Throttle (keep original behavior)
        try:
            now = time.time()
            if (now - getattr(gp, "_hover_last_ts", 0.0)) < getattr(gp, "_hover_min_interval", 0.0):
                return
            gp._hover_last_ts = now
        except Exception:
            pass

        # Outside x-lims => hide (keep original behavior)
        try:
            x0, x1 = gp._preview_ax.get_xlim()
            if xdata < min(x0, x1) or xdata > max(x0, x1):
                gp._hide_preview_hover(hard=True)
                return
        except Exception:
            pass

        # ---- FAST nearest index (O(log N) instead of O(N))
        try:
            x_arr = gp._preview_x
            if not isinstance(x_arr, np.ndarray):
                x_arr = np.asarray(x_arr, dtype=float)
            idx = _nearest_index_sorted(x_arr, float(xdata))
        except Exception:
            return

        idx = max(0, min(int(idx), int(len(gp._preview_x) - 1)))

        # Grab row once
        try:
            row = gp._preview_df.iloc[idx]
        except Exception:
            return

        # Update vline every time (feels responsive)
        try:
            vl = getattr(gp, "_preview_vline", None)
            if vl is not None:
                vl.set_xdata([xdata, xdata])
                vl.set_visible(True)
        except Exception:
            pass

        # Build time string (keep original)
        try:
            dt_current = mdates.num2date(gp._preview_x[idx])
            dt_start = mdates.num2date(gp._preview_x[0])
            elapsed = dt_current - dt_start
            total_seconds = int(elapsed.total_seconds())
            hours = total_seconds // 3600
            minutes = (total_seconds % 3600) // 60
            seconds = total_seconds % 60
            tstr = f"{hours}:{minutes:02d}:{seconds:02d}" if hours > 0 else f"{minutes}:{seconds:02d}"
        except Exception:
            tstr = f"{idx}"

        ab = getattr(gp, "_preview_collective_box", None)

        # ---- PERFORMANCE: only rebuild tooltip *content* when idx changes
        prev_idx = getattr(gp, "_preview_last_tt_idx", None)
        if prev_idx != idx:
            gp._preview_last_tt_idx = idx

            if ab is not None:
                # update time header
                try:
                    if getattr(gp, "_preview_collective_time", None) is not None:
                        gp._preview_collective_time.set_text(tstr)
                except Exception:
                    pass

                name_areas = getattr(gp, "_preview_name_areas", None) or []
                value_areas = getattr(gp, "_preview_value_areas", None) or []

                # Tooltip can only *display* this many rows; don't sort everything.
                K = min(len(name_areas), len(value_areas))
                if K <= 0:
                    try:
                        ab.set_visible(True)
                    except Exception:
                        pass
                else:
                    # Cached columns + colors (built in preview_build_tooltip_for_cols)
                    cols = getattr(gp, "_preview_tt_cols", None)
                    colors = getattr(gp, "_preview_tt_colors", None)

                    if cols is None:
                        try:
                            cols = list(gp._preview_df.columns)
                        except Exception:
                            cols = []
                    if colors is None:
                        try:
                            colors = [gp._preview_color_map.get(str(c), "#FFFFFF") for c in cols]
                        except Exception:
                            colors = ["#FFFFFF"] * len(cols)

                    # Values for this row as numpy
                    try:
                        # row may be Series; convert with to_numpy for speed
                        vals = np.asarray(row.to_numpy(dtype=float, na_value=np.nan), dtype=float)
                    except Exception:
                        # fallback slower
                        vals = np.array([float(row.iloc[i]) if row.iloc[i] == row.iloc[i] else np.nan for i in range(len(cols))], dtype=float)

                    ncols = int(len(cols))
                    if vals.size != ncols:
                        # align defensively
                        try:
                            vals = np.resize(vals, ncols).astype(float, copy=False)
                        except Exception:
                            pass

                    # Treat NaN as very low for ordering (same behavior as your sort_key)
                    # We must display in descending order of value (NaNs last).
                    try:
                        finite = np.isfinite(vals)
                        work = np.where(finite, vals, -1e30)

                        if ncols <= K:
                            top_idx = np.arange(ncols, dtype=int)
                        else:
                            # argpartition for top-K (unordered)
                            top_idx = np.argpartition(work, -K)[-K:]
                        # Now sort the top-K properly descending
                        top_idx = top_idx[np.argsort(work[top_idx])[::-1]]
                    except Exception:
                        top_idx = np.arange(min(ncols, K), dtype=int)

                    MAX_NAME_CHARS = 70

                    def _shorten(s: str, cap: int) -> str:
                        s = str(s)
                        return s if len(s) <= cap else (s[: cap - 1] + "…")

                    # Fill visible rows (top K)
                    used = 0
                    for j, ci in enumerate(top_idx[:K]):
                        try:
                            name = _shorten(cols[int(ci)], MAX_NAME_CHARS)
                        except Exception:
                            name = ""
                        try:
                            val = float(vals[int(ci)])
                        except Exception:
                            val = float("nan")
                        try:
                            color = colors[int(ci)]
                        except Exception:
                            color = "#FFFFFF"

                        vtxt = gp._format_value(name, val)

                        try:
                            name_areas[j].set_text(name)
                            name_areas[j]._text.set_color(color)
                        except Exception:
                            pass
                        try:
                            value_areas[j].set_text(vtxt)
                            value_areas[j]._text.set_color(color)
                        except Exception:
                            pass
                        used += 1

                    # Clear remaining rows
                    for j in range(used, len(name_areas)):
                        try:
                            name_areas[j].set_text("")
                        except Exception:
                            pass
                    for j in range(used, len(value_areas)):
                        try:
                            value_areas[j].set_text("")
                        except Exception:
                            pass

                    try:
                        ab.set_visible(True)
                    except Exception:
                        pass
        else:
            # idx unchanged: still update header time string if it changed (rare),
            # but avoid touching lots of text objects.
            if ab is not None:
                try:
                    if getattr(gp, "_preview_collective_time", None) is not None:
                        gp._preview_collective_time.set_text(tstr)
                except Exception:
                    pass
                try:
                    ab.set_visible(True)
                except Exception:
                    pass

        # Ensure bg exists (keep original)
        try:
            if getattr(gp, "_preview_bg", None) is None and gp._preview_canvas is not None:
                gp._preview_canvas.draw()
                gp._on_preview_draw()
        except Exception:
            pass

        # Choose tooltip corner mode (keep original)
        try:
            ty = float(ydata) if ydata is not None else 0.0
            gp._preview_update_tooltip_mode_for(xdata, ty)
        except Exception:
            pass

        # Anchor tooltip at (xdata, ydata) (keep original)
        try:
            ab = getattr(gp, "_preview_collective_box", None)
            if ab is not None:
                if ydata is None:
                    try:
                        y0, y1 = gp._preview_ax.get_ylim()
                        ty = 0.5 * (y0 + y1)
                    except Exception:
                        ty = 0.0
                else:
                    ty = float(ydata)

                try:
                    gp._tt_anim_timer.stop()
                except Exception:
                    pass

                ab.xy = (float(xdata), float(ty))
                ab.set_visible(True)
        except Exception:
            pass

        gp._preview_blit()
    except Exception:
        pass


def tt_anim_tick(gp: Any) -> None:
    ab = getattr(gp, "_preview_collective_box", None)
    if ab is None or not ab.get_visible():
        try:
            gp._tt_anim_timer.stop()
        except Exception:
            pass
        return

    try:
        if gp._tt_anim_start_xy is None or gp._tt_anim_target_xy is None:
            gp._tt_anim_timer.stop()
            return

        now = time.time()
        dur = float(getattr(gp, "_tt_anim_duration", 0.10) or 0.10)
        t = (now - float(getattr(gp, "_tt_anim_t0", 0.0))) / dur if dur > 0 else 1.0

        if t >= 1.0:
            ab.xy = gp._tt_anim_target_xy
            gp._tt_anim_timer.stop()
            gp._preview_blit()
            return

        ease = 1.0 - (1.0 - t) ** 3
        sx, sy = gp._tt_anim_start_xy
        tx, ty = gp._tt_anim_target_xy
        cx = sx + (tx - sx) * ease
        cy = sy + (ty - sy) * ease
        ab.xy = (cx, cy)
        gp._preview_blit()
    except Exception:
        try:
            gp._tt_anim_timer.stop()
        except Exception:
            pass


def preview_build_tooltip_for_cols(gp: Any, cols: list[str]) -> None:
    try:
        try:
            if gp._preview_collective_box is not None:
                gp._preview_collective_box.remove()
        except Exception:
            pass

        gp._preview_collective_box = None
        gp._preview_collective_time = None
        gp._preview_name_areas = None
        gp._preview_value_areas = None

        # clear perf caches (important when selection changes)
        try:
            gp._preview_last_tt_idx = None
        except Exception:
            pass
        try:
            gp._preview_tt_cols = None
            gp._preview_tt_colors = None
        except Exception:
            pass

        if gp._preview_ax is None or not cols or gp._preview_x is None or len(gp._preview_x) == 0:
            return

        dpi = float(getattr(gp._preview_fig, "dpi", 100) or 100)
        sep_pts = 5.0 * 72.0 / dpi

        gp._preview_collective_time = TextArea(
            "",
            textprops=dict(color="#FFFFFF", family="DejaVu Sans Mono", fontsize=10, weight="bold"),
        )

        gp._preview_name_areas = [
            TextArea("", textprops=dict(color="#FFFFFF", family="DejaVu Sans Mono", fontsize=10))
            for _ in cols
        ]
        gp._preview_value_areas = [
            TextArea("", textprops=dict(color="#FFFFFF", family="DejaVu Sans Mono", fontsize=10))
            for _ in cols
        ]

        left_col = VPacker(children=gp._preview_name_areas, align="left", pad=0, sep=3)
        right_col = VPacker(children=gp._preview_value_areas, align="right", pad=0, sep=sep_pts)
        two_col = HPacker(children=[left_col, right_col], align="top", pad=0, sep=sep_pts)

        vbox = VPacker(children=[gp._preview_collective_time, two_col], align="left", pad=0, sep=4)

        gp._preview_collective_box = AnnotationBbox(
            vbox,
            (gp._preview_x[0], 0),
            xybox=gp._preview_tt_default_xybox,
            xycoords="data",
            boxcoords="offset points",
            frameon=True,
            bboxprops=dict(
                boxstyle="round,pad=0.55",
                fc=(0, 0, 0, 0.28),
                ec=(1, 1, 1, 0.06),
                linewidth=1.0,
            ),
            zorder=1003,
        )

        try:
            gp._preview_collective_box.patch.set_path_effects([
                pe.withSimplePatchShadow(offset=(1, -1), shadow_rgbFace=(0, 0, 0), alpha=0.35),
                pe.Normal(),
            ])
        except Exception:
            pass

        gp._preview_collective_box._box_alignment = (0, 0)
        gp._preview_collective_box.set_visible(False)
        gp._preview_collective_box.set_clip_on(False)
        gp._preview_collective_box.set_animated(True)
        gp._preview_ax.add_artist(gp._preview_collective_box)

        gp._preview_tt_w_px = None
        gp._preview_tt_h_px = None
        gp._preview_tt_mode = "UR"
        gp._preview_ax_bbox = None

        # ---- PERF CACHES (used by on_preview_hover_xy)
        try:
            gp._preview_tt_cols = [str(c) for c in cols]
        except Exception:
            gp._preview_tt_cols = None
        try:
            # color lookup once
            gp._preview_tt_colors = [gp._preview_color_map.get(str(c), "#FFFFFF") for c in cols]
        except Exception:
            gp._preview_tt_colors = None

    except Exception:
        gp._preview_collective_box = None
        gp._preview_collective_time = None
        gp._preview_name_areas = None
        gp._preview_value_areas = None
        try:
            gp._preview_tt_cols = None
            gp._preview_tt_colors = None
        except Exception:
            pass
