# graph_preview.py
"""Graph preview component for displaying CSV sensor data with interactive tooltips + Legend&Stats popup button."""

from pathlib import Path
import json
import time
from typing import Optional

import numpy as np
import pandas as pd

from PySide6.QtCore import QTimer, Qt, QEvent, QObject
from PySide6.QtGui import QPixmap, QFont
from PySide6.QtWidgets import (
    QLabel,
    QSizePolicy,
    QDialog,
    QWidget,
)

from matplotlib.figure import Figure
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.backend_bases import MouseEvent as MPLMouseEvent
import matplotlib.cm as cm
import matplotlib.colors as mcolors
import matplotlib.dates as mdates

from .graph_plot_helpers import (
    apply_dark_axes_style,
    apply_elapsed_time_formatter,
    build_tab20_color_map,
    compute_x_vals,
    create_hover_vline,
    load_run_csv_dataframe,
    plot_lines_with_glow,
    trim_dataframes_to_shortest_duration,
    extract_unit_from_column,
    group_columns_by_unit,
    get_measurement_type_label,
)

from .ui_dim_overlay import DimOverlay
from .ui_legend_stats_popup import LegendStatsPopup
from .graph_stats_helpers import stats_from_summary_csv, stats_from_dataframe, infer_stats_title
from .result_selection_store import (
    apply_saved_or_default_active_cols,
    get_selection_json_path,
    load_active_cols,
    save_active_cols,
)
from .preview_path_helpers import choose_preview_file_for_folder, is_csv_file, is_image_file
from .legend_stats_button_helpers import is_over_ls_button
from .legend_popup_helpers import center_popup_on_app, raise_center_and_focus
from .graph_preview_qt_helpers import (
    bind_app_focus as _gp_bind_app_focus,
    ensure_dim_overlay as _gp_ensure_dim_overlay,
    handle_preview_canvas_event_filter as _gp_handle_preview_canvas_event_filter,
    install_outside_click_closer as _gp_install_outside_click_closer,
    on_app_state_changed as _gp_on_app_state_changed,
    on_legend_popup_closed as _gp_on_legend_popup_closed,
    set_dimmed as _gp_set_dimmed,
)
from .graph_preview_layout_helpers import (
    preview_apply_axes_rect as _gp_preview_apply_axes_rect,
    preview_relayout_and_redraw as _gp_preview_relayout_and_redraw,
    preview_required_left_margin_px as _gp_preview_required_left_margin_px,
)
from .graph_preview_tooltip_helpers import (
    format_value as _gp_format_value,
    hide_preview_hover as _gp_hide_preview_hover,
    on_preview_draw as _gp_on_preview_draw,
    on_preview_hover as _gp_on_preview_hover,
    preview_invalidate_interaction_cache as _gp_preview_invalidate_interaction_cache,
    preview_update_tooltip_metrics as _gp_preview_update_tooltip_metrics,
    preview_update_tooltip_mode_for as _gp_preview_update_tooltip_mode_for,
    safe_preview_redraw as _gp_safe_preview_redraw,
    tt_anim_tick as _gp_tt_anim_tick,
    preview_build_tooltip_for_cols as _gp_preview_build_tooltip_for_cols,
)

# ---------------------------------------------------------------------
# Graph Preview
# ---------------------------------------------------------------------
class GraphPreview(QObject):
    """Handles matplotlib graph rendering, interactive tooltip system, and a clickable 'Legend & stats' popup button."""

    def __init__(self, parent, preview_label: QLabel, build_selected_columns_callback):
        super().__init__(parent)

        self.parent = parent
        self._preview_label = preview_label
        self._build_selected_columns = build_selected_columns_callback

        # focus
        self._app_focus_bound = False
        self._app_is_active = True
        self._global_click_filter = None

        # popup + dim overlay
        self._legend_popup: Optional[LegendStatsPopup] = None
        self._dim_overlay: Optional[DimOverlay] = None

        # data/series state
        self._preview_df_all = None
        self._preview_available_cols: list[str] = []
        self._preview_active_cols: list[str] = []
        self._preview_lines = {}       # col -> Line2D
        self._preview_series_data = {} # col -> np.ndarray
        self._preview_color_map = {}   # col -> color hex
        self._preview_csv_path: str | None = None

        # Legend&Stats button drawn inside the axes
        self._ls_btn_text = None
        self._ls_btn_bbox = None
        self._hovering_ls_btn = False

        # --- High-perf hover caches (single mode)
        self._preview_is_dt = True
        self._preview_x_np: Optional[np.ndarray] = None
        self._preview_df_np: Optional[np.ndarray] = None
        self._preview_cols_cached: list[str] = []
        self._preview_colors_cached: list[str] = []
        self._preview_time_strs: Optional[list[str]] = None
        self._preview_last_tt_idx = None

        # Debounce timers for smoother legend toggling
        self._preview_apply_active_timer: Optional[QTimer] = None
        self._preview_pending_active_cols: Optional[list[str]] = None
        self._hover_cache_timer: Optional[QTimer] = None
        self._single_bg_refresh_timer: Optional[QTimer] = None

        # --- Qt overlay tooltip (single mode)
        self._qt_tt: Optional[QLabel] = None
        self._qt_tt_mode = "UR"
        self._qt_tt_margin_px = 4
        self._qt_last_mouse_xy = None  # (qt_x, qt_y) used for smoother anchoring

        # --- Qt tooltip movement animation (single + compare)
        # IMPORTANT: compare mode has MULTIPLE tooltips, so animation must be per-widget.
        self._qt_move_timer = QTimer(self.parent)
        try:
            self._qt_move_timer.setTimerType(Qt.PreciseTimer)
        except Exception:
            pass
        self._qt_move_timer.setInterval(8)  # ~125 fps cap (cheap)
        self._qt_move_timer.timeout.connect(self._qt_move_tick)

        self._qt_move_duration = 0.09  # seconds; tune 0.07..0.12
        # dict: QLabel -> {t0: float, sx: float, sy: float, tx: float, ty: float}
        self._qt_move_map: dict[QLabel, dict] = {}

        # matplotlib
        try:
            self._preview_fig = Figure(figsize=(5, 3))
            self._preview_left_margin_px_base = 60
            self._preview_top_frac = 0.98
            self._preview_bottom_frac = 0.08

            self._preview_canvas = FigureCanvas(self._preview_fig)
            self._preview_canvas.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
            self._preview_canvas.setMouseTracking(True)
            self._preview_ax = self._preview_fig.add_subplot(111)

            self._preview_apply_axes_rect(right_frac=0.985, left_margin_px=self._preview_left_margin_px_base)

            self._preview_last_canvas_wh = None
            try:
                self._preview_canvas.installEventFilter(self)
            except Exception:
                pass

            self._preview_canvas.mpl_connect("draw_event", self._on_preview_draw)

            # tooltip + hover state (matplotlib tooltip objects still exist, but we render tooltip via Qt overlay)
            self._preview_mpl_cid = None
            self._preview_x = None
            self._preview_df = None

            self._preview_collective_box = None
            self._preview_collective_time = None
            self._preview_name_areas = None
            self._preview_value_areas = None
            self._preview_colors = []

            self._preview_tt_default_xybox = (10, 10)       # UR
            self._preview_tt_flipped_xybox = (10, -10)      # DR
            self._preview_tt_left_xybox = (-10, 10)         # UL
            self._preview_tt_left_down_xybox = (-10, -10)   # DL
            self._preview_tt_margin_px = 4

            self._preview_tt_w_px = None
            self._preview_tt_h_px = None
            self._preview_tt_mode = "UR"
            self._preview_ax_bbox = None

            # We keep throttling for correctness, but hover now stays smooth because redraw work is constant.
            self._hover_last_ts = 0.0
            self._hover_min_interval = 1.0 / 240.0

            self._tt_anim_timer = QTimer(self.parent)
            try:
                self._tt_anim_timer.setTimerType(Qt.PreciseTimer)
            except Exception:
                pass
            self._tt_anim_timer.setInterval(4)
            self._tt_anim_timer.timeout.connect(self._tt_anim_tick)

            self._tt_instant_follow = True
            self._tt_anim_duration = 0.10
            self._tt_anim_t0 = 0.0
            self._tt_anim_start_xy = None
            self._tt_anim_target_xy = None

            self._preview_bg = None
            self._preview_vline = None

            self._preview_grid_color = "#3A3A3A"
            self._preview_dot_dashes = (0, (1.2, 3.2))

            def _qc(ev):
                try:
                    if not getattr(self, "_app_is_active", True):
                        return

                    try:
                        if self._is_over_ls_button(ev.pos().x(), ev.pos().y()):
                            if not self._hovering_ls_btn:
                                self._hovering_ls_btn = True
                                self._preview_canvas.setCursor(Qt.PointingHandCursor)
                            self._hide_preview_hover(hard=False)
                            return
                        else:
                            if self._hovering_ls_btn:
                                self._hovering_ls_btn = False
                                self._preview_canvas.setCursor(Qt.ArrowCursor)
                    except Exception:
                        pass

                    x = ev.pos().x()
                    y = ev.pos().y()
                    self._qt_last_mouse_xy = (int(x), int(y))

                    h = self._preview_canvas.height()
                    display_x = x
                    display_y = h - y

                    try:
                        data_xy = self._preview_ax.transData.inverted().transform((display_x, display_y))
                        xdata, ydata = data_xy[0], data_xy[1]
                        self._on_preview_hover_xy(xdata, ydata)
                    except Exception:
                        try:
                            me = MPLMouseEvent("motion_notify_event", self._preview_canvas, x, display_y)
                            self._on_preview_hover(me)
                        except Exception:
                            pass
                except Exception:
                    pass

            self._preview_canvas.mouseMoveEvent = _qc

            # keep a handle so we can swap in compare-mode handlers
            self._default_mouse_move_event = self._preview_canvas.mouseMoveEvent

            _orig_press = self._preview_canvas.mousePressEvent

            def _press(ev):
                try:
                    if ev.button() == Qt.LeftButton:
                        if self._handle_ls_click(ev.pos().x(), ev.pos().y()):
                            return
                except Exception:
                    pass
                return _orig_press(ev)

            self._preview_canvas.mousePressEvent = _press

            self._default_mouse_press_event = self._preview_canvas.mousePressEvent

            self._preview_canvas.hide()

        except Exception:
            self._preview_fig = None
            self._preview_canvas = None
            self._preview_ax = None

        # compare-mode state
        self._compare_mode = False
        self._compare_axes = []
        self._compare_axis_state = {}
        self._compare_last_canvas_wh = None
        self._compare_last_idx = None

        # single-mode multi-axis state (for splitting by measurement type)
        self._single_mode_multi_axis = False
        self._single_axes = []
        self._single_axis_state = {}
        self._single_axis_vlines = {}
        self._single_last_canvas_wh = None
        self._single_last_idx = None

    # ---------------------------------------------------------------------
    # Qt tooltip animation helpers (per-widget)
    # ---------------------------------------------------------------------
    @staticmethod
    def _ease_out_cubic(t: float) -> float:
        t = 0.0 if t < 0.0 else (1.0 if t > 1.0 else t)
        return 1.0 - (1.0 - t) ** 3

    def _qt_cancel_move(self, w: Optional[QLabel] = None) -> None:
        try:
            if w is None:
                self._qt_move_map.clear()
            else:
                self._qt_move_map.pop(w, None)
        except Exception:
            pass
        try:
            if not self._qt_move_map and self._qt_move_timer.isActive():
                self._qt_move_timer.stop()
        except Exception:
            pass

    def _qt_move_to(self, w: QLabel, target_x: int, target_y: int) -> None:
        """
        Smoothly move QLabel `w` to (target_x, target_y).
        If `w` is hidden (first show), snap to target to avoid flying in from (0,0).
        """
        try:
            if w is None:
                return

            # If not visible yet, snap immediately (prevents top-left glitch)
            if not w.isVisible():
                try:
                    w.move(int(target_x), int(target_y))
                except Exception:
                    pass
                return

            # Suppress tiny changes to avoid jitter
            try:
                cur = w.pos()
                if abs(int(target_x) - int(cur.x())) <= 1 and abs(int(target_y) - int(cur.y())) <= 1:
                    return
            except Exception:
                pass

            cur = w.pos()
            now = time.time()
            self._qt_move_map[w] = {
                "t0": float(now),
                "sx": float(cur.x()),
                "sy": float(cur.y()),
                "tx": float(target_x),
                "ty": float(target_y),
            }
            if not self._qt_move_timer.isActive():
                self._qt_move_timer.start()
        except Exception:
            pass

    def _qt_move_tick(self) -> None:
        try:
            if not self._qt_move_map:
                if self._qt_move_timer.isActive():
                    self._qt_move_timer.stop()
                return

            now = time.time()
            dur = float(getattr(self, "_qt_move_duration", 0.09) or 0.09)
            if dur <= 0:
                dur = 0.001

            done = []
            for w, st in list(self._qt_move_map.items()):
                try:
                    if w is None or (hasattr(w, "isVisible") and not w.isVisible()):
                        done.append(w)
                        continue

                    t0 = float(st.get("t0", now))
                    t = (now - t0) / dur
                    if t >= 1.0:
                        w.move(int(round(st["tx"])), int(round(st["ty"])))
                        done.append(w)
                        continue

                    e = self._ease_out_cubic(float(t))
                    sx = float(st["sx"])
                    sy = float(st["sy"])
                    tx = float(st["tx"])
                    ty = float(st["ty"])
                    cx = sx + (tx - sx) * e
                    cy = sy + (ty - sy) * e
                    w.move(int(round(cx)), int(round(cy)))
                except Exception:
                    done.append(w)

            for w in done:
                try:
                    self._qt_move_map.pop(w, None)
                except Exception:
                    pass

            if not self._qt_move_map and self._qt_move_timer.isActive():
                self._qt_move_timer.stop()
        except Exception:
            try:
                self._qt_move_map.clear()
            except Exception:
                pass
            try:
                if self._qt_move_timer.isActive():
                    self._qt_move_timer.stop()
            except Exception:
                pass

    # ---------------------------------------------------------------------
    # Qt overlay tooltip helpers (single + compare)
    # ---------------------------------------------------------------------
    def _ensure_qt_tooltip(self) -> Optional[QLabel]:
        try:
            if self._preview_canvas is None:
                return None
            if self._qt_tt is not None:
                return self._qt_tt

            tt = QLabel(self._preview_canvas)
            tt.setObjectName("PreviewTooltipOverlay")
            tt.setAttribute(Qt.WA_TransparentForMouseEvents, True)
            tt.setTextFormat(Qt.RichText)
            tt.setWordWrap(False)

            f = QFont("DejaVu Sans Mono")
            try:
                f.setStyleHint(QFont.Monospace)
            except Exception:
                pass
            f.setPointSize(10)
            tt.setFont(f)

            # Match the existing tooltip style closely (semi-transparent dark background + subtle border)
            tt.setStyleSheet(
                "QLabel#PreviewTooltipOverlay {"
                " background-color: rgba(24,24,24,160);"
                " border: 1px solid rgba(255,255,255,18);"
                " border-radius: 8px;"
                " padding: 8px 10px;"
                " color: #FFFFFF;"
                "}"
            )
            tt.hide()
            self._qt_tt = tt
            return tt
        except Exception:
            return None

    def _hide_qt_tooltip(self) -> None:
        try:
            if self._qt_tt is not None:
                self._qt_cancel_move(self._qt_tt)
                self._qt_tt.hide()
        except Exception:
            pass

    @staticmethod
    def _html_escape(s: str) -> str:
        s = str(s)
        return (
            s.replace("&", "&amp;")
             .replace("<", "&lt;")
             .replace(">", "&gt;")
             .replace('"', "&quot;")
        )

    def _qt_build_tooltip_html(self, header: str, names: list[str], values: list[str], colors: list[str]) -> str:
        """
        Build a monospace, aligned, colored tooltip body with white-space preserved.
        """
        try:
            # shorten
            MAX_NAME_CHARS = 70
            n2, v2, c2 = [], [], []
            for n, v, c in zip(names, values, colors):
                n = str(n)
                if len(n) > MAX_NAME_CHARS:
                    n = n[: MAX_NAME_CHARS - 1] + "…"
                n2.append(n)
                v2.append(str(v))
                c2.append(str(c))

            name_w = 0
            val_w = 0
            for n in n2:
                name_w = max(name_w, len(n))
            for v in v2:
                val_w = max(val_w, len(v))

            header_e = self._html_escape(header)

            lines = []
            # header
            lines.append(f"<span style='font-weight:700;color:#FFFFFF'>{header_e}</span>")

            # content lines aligned using pre-like whitespace
            for n, v, col in zip(n2, v2, c2):
                ne = self._html_escape(n)
                ve = self._html_escape(v)
                pad_name = ne + (" " * max(0, name_w - len(n)))
                pad_val = (" " * max(0, val_w - len(v))) + ve
                row_style = "font-weight:600;"
                row_style += "text-shadow: 0 0 0.5px rgba(0,0,0,0.35);"

                lines.append(
                    f"<span style='color:{col};{row_style}'>{pad_name}</span>"
                    f"  "
                    f"<span style='color:{col};{row_style}'>{pad_val}</span>"
                )

            body = "\n".join(lines)
            return "<div style=\"white-space:pre;\">" + body + "</div>"
        except Exception:
            return f"<div style='white-space:pre;'><b>{self._html_escape(header)}</b></div>"

    @staticmethod
    def _nearest_index_sorted(x_sorted: np.ndarray, x: float) -> int:
        """
        Fast nearest index for sorted 1D array.
        """
        i = int(np.searchsorted(x_sorted, x))
        if i <= 0:
            return 0
        if i >= len(x_sorted):
            return len(x_sorted) - 1
        left = float(x_sorted[i - 1])
        right = float(x_sorted[i])
        return (i - 1) if abs(x - left) <= abs(x - right) else i

    def _qt_compute_tooltip_pos_in_ax(self, tt: QLabel, ax, *, xdata: float, ydata: float, prefer_mode: str = "UR"):
        """
        Compute (x0, y0, mode) for the tooltip top-left in Qt coords (origin top-left),
        clamped to the axis bbox. DOES NOT move the widget.
        """
        try:
            if self._preview_canvas is None or ax is None or tt is None:
                return None

            # Ensure we have correct widget size
            try:
                tt.adjustSize()
            except Exception:
                pass
            w = int(tt.width())
            h = int(tt.height())

            # Axis bbox in display coords (origin bottom-left). Convert to Qt coords.
            bb = ax.bbox
            canvas_h = int(self._preview_canvas.height())
            ax_left = float(bb.x0)
            ax_right = float(bb.x1)
            ax_top = float(canvas_h - bb.y1)
            ax_bottom = float(canvas_h - bb.y0)

            margin = float(getattr(self, "_preview_tt_margin_px", 4) or 4)

            # Anchor in display coords -> Qt coords
            try:
                cx, cy = ax.transData.transform((float(xdata), float(ydata)))
            except Exception:
                # fallback to last mouse position if available
                if self._qt_last_mouse_xy is not None:
                    cx = float(self._qt_last_mouse_xy[0])
                    cy = float(canvas_h - self._qt_last_mouse_xy[1])
                else:
                    cx = 0.5 * (ax_left + ax_right)
                    cy = 0.5 * (bb.y0 + bb.y1)  # display
            qt_anchor_x = float(cx)
            qt_anchor_y = float(canvas_h - cy)

            # Convert offset points -> px
            fig = getattr(self, "_preview_fig", None)
            dpi = float(getattr(fig, "dpi", 100) or 100) if fig is not None else 100.0

            def pt_to_px(v):
                return float(v) * dpi / 72.0

            ur = getattr(self, "_preview_tt_default_xybox", (10, 10))
            dr = getattr(self, "_preview_tt_flipped_xybox", (10, -10))
            ul = getattr(self, "_preview_tt_left_xybox", (-10, 10))
            dl = getattr(self, "_preview_tt_left_down_xybox", (-10, -10))

            def mode_to_offsets(mode: str):
                if mode == "UR":
                    xy = ur
                    align = (0, 0)
                elif mode == "DR":
                    xy = dr
                    align = (0, 1)
                elif mode == "UL":
                    xy = ul
                    align = (1, 0)
                else:
                    xy = dl
                    align = (1, 1)
                ox = pt_to_px(xy[0])
                oy = -pt_to_px(xy[1])  # display up -> Qt negative
                return float(ox), float(oy), align

            # Candidate modes (try prefer_mode first)
            candidates = ["UR", "DR", "UL", "DL"]
            if prefer_mode in candidates:
                candidates = [prefer_mode] + [m for m in candidates if m != prefer_mode]

            best_mode = None
            best_pos = None
            best_score = None

            for m in candidates:
                ox, oy, align = mode_to_offsets(m)

                # map "box_alignment" into Qt top-left
                if align == (0, 0):          # lower-left anchored (matplotlib)
                    x0 = qt_anchor_x + ox
                    y0 = qt_anchor_y + oy - h
                elif align == (0, 1):        # upper-left anchored
                    x0 = qt_anchor_x + ox
                    y0 = qt_anchor_y + oy
                elif align == (1, 0):        # lower-right anchored
                    x0 = qt_anchor_x + ox - w
                    y0 = qt_anchor_y + oy - h
                else:                         # upper-right anchored
                    x0 = qt_anchor_x + ox - w
                    y0 = qt_anchor_y + oy

                left_over = max(0.0, (ax_left + margin) - x0)
                right_over = max(0.0, (x0 + w) - (ax_right - margin))
                top_over = max(0.0, (ax_top + margin) - y0)
                bot_over = max(0.0, (y0 + h) - (ax_bottom - margin))
                score = left_over + right_over + top_over + bot_over

                if best_score is None or score < best_score:
                    best_score = score
                    best_mode = m
                    best_pos = (x0, y0)

            if best_pos is None or best_mode is None:
                return None

            x0, y0 = best_pos

            # HARD clamp inside axis bbox
            min_x = ax_left + margin
            max_x = (ax_right - margin) - w
            min_y = ax_top + margin
            max_y = (ax_bottom - margin) - h

            if max_x < min_x:
                x0 = min_x
            else:
                x0 = max(min_x, min(max_x, x0))

            if max_y < min_y:
                y0 = min_y
            else:
                y0 = max(min_y, min(max_y, y0))

            ix = int(round(x0))
            iy = int(round(y0))
            return (ix, iy, str(best_mode))
        except Exception:
            return None

    # ---------------------------------------------------------------------
    # Compare-mode helpers
    # ---------------------------------------------------------------------
    def _exit_compare_mode(self) -> None:
        # ensure compare overlay tooltips are destroyed/hidden
        try:
            for st in list((self._compare_axis_state or {}).values()):
                try:
                    w = st.get("qt_tt")
                    if w is not None:
                        self._qt_cancel_move(w)
                        w.hide()
                        w.setParent(None)
                except Exception:
                    pass
        except Exception:
            pass

        try:
            self._compare_mode = False
            self._compare_axes = []
            self._compare_axis_state = {}
            self._compare_last_canvas_wh = None
            self._compare_last_idx = None
        except Exception:
            pass

        # Compare-mode uses multiple subplots; reset the figure back to a single axis
        # so old subplots can't linger when switching back to normal preview.
        try:
            if self._preview_fig is not None and self._preview_canvas is not None:
                self._preview_fig.clear()
                self._preview_ax = self._preview_fig.add_subplot(111)
                try:
                    self._preview_apply_axes_rect(right_frac=0.985, left_margin_px=self._preview_left_margin_px_base)
                except Exception:
                    pass
        except Exception:
            pass

        try:
            if self._preview_canvas is not None:
                if hasattr(self, "_default_mouse_move_event"):
                    self._preview_canvas.mouseMoveEvent = self._default_mouse_move_event
                if hasattr(self, "_default_mouse_press_event"):
                    self._preview_canvas.mousePressEvent = self._default_mouse_press_event
        except Exception:
            pass

    def _exit_single_mode_multi_axis(self) -> None:
        """Exit single-mode multi-axis view and reset to default single axis."""
        # ensure single-mode overlay tooltips are destroyed/hidden
        try:
            for st in list((self._single_axis_state or {}).values()):
                try:
                    w = st.get("qt_tt")
                    if w is not None:
                        self._qt_cancel_move(w)
                        w.hide()
                        w.setParent(None)
                except Exception:
                    pass
        except Exception:
            pass

        try:
            self._single_mode_multi_axis = False
            self._single_axes = []
            self._single_axis_state = {}
            self._single_axis_vlines = {}
            self._single_last_canvas_wh = None
            self._single_last_idx = None
        except Exception:
            pass

        # Reset figure back to a single axis
        try:
            if self._preview_fig is not None and self._preview_canvas is not None:
                self._preview_fig.clear()
                self._preview_ax = self._preview_fig.add_subplot(111)
                try:
                    self._preview_apply_axes_rect(right_frac=0.985, left_margin_px=self._preview_left_margin_px_base)
                except Exception:
                    pass
        except Exception:
            pass

        try:
            if self._preview_canvas is not None:
                if hasattr(self, "_default_mouse_move_event"):
                    self._preview_canvas.mouseMoveEvent = self._default_mouse_move_event
                if hasattr(self, "_default_mouse_press_event"):
                    self._preview_canvas.mousePressEvent = self._default_mouse_press_event
        except Exception:
            pass

    def _hide_compare_hover_all(self) -> None:
        # hide vlines + compare overlay tooltips
        try:
            for st in (self._compare_axis_state or {}).values():
                try:
                    if st.get("vline") is not None:
                        st["vline"].set_visible(False)
                except Exception:
                    pass
                try:
                    w = st.get("qt_tt")
                    if w is not None:
                        self._qt_cancel_move(w)
                        w.hide()
                except Exception:
                    pass
        except Exception:
            pass

    def _hide_single_hover_all(self) -> None:
        """Hide vlines + single-mode overlay tooltips (multi-axis)."""
        try:
            for st in (self._single_axis_state or {}).values():
                try:
                    if st.get("vline") is not None:
                        st["vline"].set_visible(False)
                except Exception:
                    pass
                try:
                    w = st.get("qt_tt")
                    if w is not None:
                        self._qt_cancel_move(w)
                        w.hide()
                except Exception:
                    pass
        except Exception:
            pass
        try:
            if self._preview_canvas is not None:
                self._single_blit_vlines_only()
        except Exception:
            pass

    def _refresh_single_backgrounds(self) -> None:
        try:
            if self._preview_canvas is None or not self._single_axes:
                return
            self._preview_canvas.draw()
            for ax in self._single_axes:
                try:
                    bg = self._preview_canvas.copy_from_bbox(ax.bbox)
                    st = self._single_axis_state.get(ax)
                    if st is not None:
                        st["bg"] = bg
                except Exception:
                    pass
        except Exception:
            pass

    def _single_blit_vlines_only(self) -> None:
        """Fast single multi-axis blit: vlines only (tooltips are Qt overlays)."""
        try:
            if self._preview_canvas is None or not self._single_axes:
                return
            c = self._preview_canvas

            # Ensure backgrounds exist
            need_bg = False
            for ax in self._single_axes:
                st = self._single_axis_state.get(ax)
                if st is None or st.get("bg") is None:
                    need_bg = True
                    break
            if need_bg:
                self._refresh_single_backgrounds()

            for ax in self._single_axes:
                st = self._single_axis_state.get(ax)
                if not st:
                    continue
                bg = st.get("bg")
                if bg is None:
                    continue
                try:
                    c.restore_region(bg)
                except Exception:
                    continue

                try:
                    vl = st.get("vline")
                    if vl is not None and vl.get_visible():
                        ax.draw_artist(vl)
                except Exception:
                    pass
                try:
                    c.blit(ax.bbox)
                except Exception:
                    pass
        except Exception:
            try:
                if self._preview_canvas is not None:
                    self._preview_canvas.draw_idle()
            except Exception:
                pass
        try:
            if self._preview_canvas is not None:
                # restore background + hide vlines quickly
                self._compare_blit_vlines_only()
        except Exception:
            pass

    def _refresh_compare_backgrounds(self) -> None:
        try:
            if self._preview_canvas is None or not self._compare_axes:
                return
            self._preview_canvas.draw()
            for ax in self._compare_axes:
                try:
                    bg = self._preview_canvas.copy_from_bbox(ax.bbox)
                    st = self._compare_axis_state.get(ax)
                    if st is not None:
                        st["bg"] = bg
                except Exception:
                    pass
        except Exception:
            pass

    def _compare_blit_vlines_only(self) -> None:
        """Fast compare blit: vlines only (tooltips rendered via Qt overlay)."""
        try:
            if self._preview_canvas is None or not self._compare_axes:
                return
            c = self._preview_canvas

            # Ensure backgrounds exist
            need_bg = False
            for ax in self._compare_axes:
                st = self._compare_axis_state.get(ax)
                if st is None or st.get("bg") is None:
                    need_bg = True
                    break
            if need_bg:
                self._refresh_compare_backgrounds()

            for ax in self._compare_axes:
                st = self._compare_axis_state.get(ax)
                if not st:
                    continue
                bg = st.get("bg")
                if bg is None:
                    continue
                try:
                    c.restore_region(bg)
                except Exception:
                    continue
                try:
                    vl = st.get("vline")
                    if vl is not None and vl.get_visible():
                        ax.draw_artist(vl)
                except Exception:
                    pass
                try:
                    c.blit(ax.bbox)
                except Exception:
                    pass
        except Exception:
            try:
                if self._preview_canvas is not None:
                    self._preview_canvas.draw_idle()
            except Exception:
                pass

    # ---------------------------------------------------------------------
    # Public API
    # ---------------------------------------------------------------------
    def get_canvas(self):
        return self._preview_canvas

    def preview_path(self, fpath: str) -> None:
        try:
            self._exit_compare_mode()
            p = Path(fpath)
            if is_csv_file(p) and self._preview_canvas is not None:
                self._plot_run_csv(str(p))
                return

            if is_image_file(p):
                if self._preview_canvas is not None:
                    try:
                        self._preview_canvas.hide()
                    except Exception:
                        pass
                self._hide_qt_tooltip()
                pix = QPixmap(str(p))
                if not pix.isNull():
                    w = max(100, self._preview_label.width())
                    h = max(100, self._preview_label.height())
                    self._preview_label.setPixmap(pix.scaled(w, h, Qt.KeepAspectRatio, Qt.SmoothTransformation))
                    self._preview_label.show()
                    return
        except Exception:
            pass

        try:
            if self._preview_canvas is not None:
                self._preview_canvas.hide()
        except Exception:
            pass
        self._hide_qt_tooltip()
        self._preview_label.clear()
        self._preview_label.show()

    def preview_folder(self, folder: str) -> None:
        try:
            # Compare results: render multi-sensor compare view
            try:
                mp = Path(folder) / "compare_manifest.json"
                if mp.exists() and mp.is_file():
                    self._plot_compare_manifest(mp)
                    return
            except Exception:
                pass

            pick = choose_preview_file_for_folder(folder)
            if pick is None:
                try:
                    if self._preview_canvas is not None:
                        self._preview_canvas.hide()
                except Exception:
                    pass
                self._hide_qt_tooltip()
                self._preview_label.clear()
                return

            self.preview_path(str(pick))
            return
        except Exception:
            self._hide_qt_tooltip()
            self._preview_label.clear()

    # ---------------------------------------------------------------------
    # Event filter
    # ---------------------------------------------------------------------
    def eventFilter(self, obj, event):
        _gp_handle_preview_canvas_event_filter(self, obj, event)
        return super().eventFilter(obj, event)

    # ---------------------------------------------------------------------
    # Draw / blit cache
    # ---------------------------------------------------------------------
    def _on_preview_draw(self, event=None) -> None:
        # Single-mode multi-axis does not have a stable `_preview_ax` (the figure is cleared
        # and subplots are created). We still need the draw hook to:
        #  - update Legend&stats button bbox for hover/click hit-testing
        #  - cache per-axis backgrounds for fast vline-only blitting
        if getattr(self, "_single_mode_multi_axis", False):
            try:
                if self._preview_canvas is None:
                    return
                renderer = self._preview_canvas.get_renderer()
                if renderer is not None:
                    try:
                        if self._ls_btn_text is not None:
                            self._ls_btn_bbox = self._ls_btn_text.get_window_extent(renderer)
                        else:
                            self._ls_btn_bbox = None
                    except Exception:
                        self._ls_btn_bbox = None

                try:
                    # cache backgrounds per axis
                    for ax in (self._single_axes or []):
                        try:
                            bg = self._preview_canvas.copy_from_bbox(ax.bbox)
                            st = (self._single_axis_state or {}).get(ax)
                            if st is not None:
                                st["bg"] = bg
                        except Exception:
                            pass
                except Exception:
                    pass
            except Exception:
                pass
            return

        _gp_on_preview_draw(self, event)

    def _preview_blit(self) -> None:
        """
        High-perf blit path: vline only. Tooltip is rendered via Qt overlay (so cost doesn't scale with sensor count).
        """
        try:
            if self._preview_canvas is None or self._preview_ax is None:
                return
            if self._preview_bg is None:
                self._preview_canvas.draw_idle()
                return

            c = self._preview_canvas
            ax = self._preview_ax
            c.restore_region(self._preview_bg)

            try:
                if getattr(self, "_preview_vline", None) is not None and self._preview_vline.get_visible():
                    ax.draw_artist(self._preview_vline)
            except Exception:
                pass

            c.blit(ax.bbox)
        except Exception:
            try:
                if self._preview_canvas is not None:
                    self._preview_canvas.draw_idle()
            except Exception:
                pass

    def _preview_invalidate_interaction_cache(self) -> None:
        _gp_preview_invalidate_interaction_cache(self)
        # also reset overlay positioning state
        try:
            self._preview_last_tt_idx = None
            self._qt_tt_mode = "UR"
        except Exception:
            pass

    def _safe_preview_redraw(self) -> None:
        _gp_safe_preview_redraw(self)

    # ---------------------------------------------------------------------
    # Tooltip boundary logic (matplotlib path still exists; we keep it for compatibility)
    # ---------------------------------------------------------------------
    def _preview_update_tooltip_metrics(self) -> None:
        _gp_preview_update_tooltip_metrics(self)

    def _preview_update_tooltip_mode_for(self, xdata: float, ydata: float) -> None:
        _gp_preview_update_tooltip_mode_for(self, xdata, ydata)

    # ---------------------------------------------------------------------
    # Legend&Stats button click logic
    # ---------------------------------------------------------------------
    def _is_over_ls_button(self, qt_x: int, qt_y: int) -> bool:
        return is_over_ls_button(
            canvas=self._preview_canvas,
            ls_btn_bbox=self._ls_btn_bbox,
            qt_x=qt_x,
            qt_y=qt_y,
        )

    def _handle_ls_click(self, qt_x: int, qt_y: int) -> bool:
        if not self._is_over_ls_button(qt_x, qt_y):
            return False

        if not self._preview_available_cols:
            return True

        try:
            self._open_legend_popup()
        except Exception:
            pass

        return True

    def _close_legend_popup(self) -> None:
        try:
            if self._legend_popup is not None and self._legend_popup.isVisible():
                self._legend_popup.close()
        except Exception:
            pass
        self._set_dimmed(False)
        self._legend_popup = None

    # ---------------------------------------------------------------------
    # Hover / tooltip (single mode)
    # ---------------------------------------------------------------------
    def _on_preview_hover(self, event) -> None:
        _gp_on_preview_hover(self, event)

    def _hide_preview_hover(self, hard: bool = False) -> None:
        # keep original vline hide behavior but also hide Qt tooltip overlay
        try:
            self._hide_qt_tooltip()
        except Exception:
            pass
        _gp_hide_preview_hover(self, hard=hard)

    def _format_value(self, col_name: str, val: float) -> str:
        return _gp_format_value(self, col_name, val)

    def _rebuild_hover_cache(self) -> None:
        """
        Build numpy caches so hover cost is essentially constant per frame.
        """
        try:
            # x cache
            x = self._preview_x
            if x is None:
                self._preview_x_np = None
            else:
                self._preview_x_np = np.asarray(x, dtype=float)

            # df cache (active columns)
            if self._preview_df is None:
                self._preview_df_np = None
                self._preview_cols_cached = []
                self._preview_colors_cached = []
            else:
                try:
                    self._preview_df_np = self._preview_df.to_numpy(dtype=float, copy=False)
                except Exception:
                    self._preview_df_np = np.asarray(self._preview_df.to_numpy(), dtype=float)

                try:
                    self._preview_cols_cached = [str(c) for c in list(self._preview_df.columns)]
                except Exception:
                    self._preview_cols_cached = []
                try:
                    self._preview_colors_cached = [
                        str(self._preview_color_map.get(str(c), "#FFFFFF")) for c in self._preview_cols_cached
                    ]
                except Exception:
                    self._preview_colors_cached = ["#FFFFFF"] * len(self._preview_cols_cached)

            # precompute elapsed time strings
            self._preview_time_strs = None
            try:
                if self._preview_x_np is not None and len(self._preview_x_np) > 0:
                    dsec = (self._preview_x_np - float(self._preview_x_np[0])) * 86400.0
                    dsec = np.maximum(dsec, 0.0)
                    out = []
                    for s in dsec.astype(np.int64, copy=False):
                        s = int(s)
                        h = s // 3600
                        m = (s % 3600) // 60
                        sec = s % 60
                        if h > 0:
                            out.append(f"{h}:{m:02d}:{sec:02d}")
                        else:
                            out.append(f"{m}:{sec:02d}")
                    self._preview_time_strs = out
            except Exception:
                self._preview_time_strs = None

            self._preview_last_tt_idx = None
        except Exception:
            pass

    def _on_preview_hover_xy(self, xdata: float, ydata: float) -> None:
        """
        Same behavior, high responsiveness:
        - Matplotlib draws ONLY the vline (blitting)
        - Tooltip is a Qt overlay QLabel
        - Content updates only when idx changes
        - NEW: tooltip position animates smoothly between targets
        """
        try:
            if not getattr(self, "_app_is_active", True):
                return
            if self._preview_ax is None:
                return
            if xdata is None:
                return

            # Resize invalidation (keep original behavior)
            try:
                if self._preview_canvas is not None:
                    wh = (int(self._preview_canvas.width()), int(self._preview_canvas.height()))
                    if getattr(self, "_preview_last_canvas_wh", None) != wh:
                        self._preview_last_canvas_wh = wh
                        self._preview_invalidate_interaction_cache()
                        self._preview_relayout_and_redraw()
            except Exception:
                pass

            # Throttle (keep original behavior)
            try:
                now = time.time()
                if (now - getattr(self, "_hover_last_ts", 0.0)) < getattr(self, "_hover_min_interval", 0.0):
                    return
                self._hover_last_ts = now
            except Exception:
                pass

            # Outside x-lims => hide (keep original behavior)
            try:
                x0, x1 = self._preview_ax.get_xlim()
                if xdata < min(x0, x1) or xdata > max(x0, x1):
                    self._hide_preview_hover(hard=True)
                    return
            except Exception:
                pass

            # Need caches
            if self._preview_x_np is None or self._preview_df_np is None:
                try:
                    self._rebuild_hover_cache()
                except Exception:
                    pass
            if self._preview_x_np is None or self._preview_df_np is None:
                return
            if len(self._preview_x_np) < 2:
                return

            # Fast nearest index
            try:
                idx = self._nearest_index_sorted(self._preview_x_np, float(xdata))
            except Exception:
                return
            idx = max(0, min(int(idx), int(len(self._preview_x_np) - 1)))

            # Update vline every time
            try:
                vl = getattr(self, "_preview_vline", None)
                if vl is not None:
                    vl.set_xdata([xdata, xdata])
                    vl.set_visible(True)
            except Exception:
                pass

            # Ensure bg exists
            try:
                if getattr(self, "_preview_bg", None) is None and self._preview_canvas is not None:
                    self._preview_canvas.draw()
                    self._on_preview_draw()
            except Exception:
                pass

            # Tooltip overlay
            tt = self._ensure_qt_tooltip()
            if tt is None:
                self._preview_blit()
                return

            # Content updates only when idx changes
            if self._preview_last_tt_idx != idx:
                self._preview_last_tt_idx = idx

                # header time string
                try:
                    if self._preview_time_strs is not None and 0 <= idx < len(self._preview_time_strs):
                        tstr = self._preview_time_strs[idx]
                    else:
                        dt_current = mdates.num2date(self._preview_x_np[idx])
                        dt_start = mdates.num2date(self._preview_x_np[0])
                        elapsed = dt_current - dt_start
                        total_seconds = int(elapsed.total_seconds())
                        hours = total_seconds // 3600
                        minutes = (total_seconds % 3600) // 60
                        seconds = total_seconds % 60
                        tstr = f"{hours}:{minutes:02d}:{seconds:02d}" if hours > 0 else f"{minutes}:{seconds:02d}"
                except Exception:
                    tstr = f"{idx}"

                # row values from numpy
                try:
                    vals = np.asarray(self._preview_df_np[idx, :], dtype=float)
                except Exception:
                    vals = None

                cols = list(self._preview_cols_cached or [])
                colors = list(self._preview_colors_cached or [])

                if vals is None or len(cols) != int(getattr(vals, "size", 0)):
                    try:
                        vals = np.asarray(self._preview_df.iloc[idx].to_numpy(dtype=float, na_value=np.nan), dtype=float)
                    except Exception:
                        vals = np.full((len(cols),), np.nan, dtype=float)

                ncols = int(len(cols))
                if ncols != int(vals.size):
                    try:
                        vals = np.resize(vals, ncols).astype(float, copy=False)
                    except Exception:
                        pass

                # sort descending, NaNs last
                try:
                    work = np.where(np.isfinite(vals), vals, -1e30)
                    order = np.argsort(work)[::-1]
                except Exception:
                    order = np.arange(ncols, dtype=int)

                names_sorted = []
                values_sorted = []
                colors_sorted = []
                for i in order:
                    try:
                        name = cols[int(i)]
                    except Exception:
                        name = ""
                    try:
                        v = float(vals[int(i)])
                    except Exception:
                        v = float("nan")
                    try:
                        col = colors[int(i)]
                    except Exception:
                        col = "#FFFFFF"

                    names_sorted.append(name)
                    values_sorted.append(self._format_value(name, v))
                    colors_sorted.append(col)

                html = self._qt_build_tooltip_html(tstr, names_sorted, values_sorted, colors_sorted)
                try:
                    tt.setText(html)
                except Exception:
                    pass

            # Show (important: show BEFORE animating; first-show snaps in _qt_move_to)
            try:
                tt.show()
            except Exception:
                pass

            # Anchor y follows cursor y; if ydata missing, use mid
            try:
                if ydata is None:
                    y0, y1 = self._preview_ax.get_ylim()
                    yref = 0.5 * (float(y0) + float(y1))
                else:
                    yref = float(ydata)
            except Exception:
                yref = 0.0

            # Compute target position (clamped) + animate to it
            pos = self._qt_compute_tooltip_pos_in_ax(
                tt, self._preview_ax, xdata=float(xdata), ydata=float(yref), prefer_mode=self._qt_tt_mode
            )
            if pos is not None:
                tx, ty, mode = pos
                self._qt_tt_mode = str(mode)
                self._qt_move_to(tt, int(tx), int(ty))

            # Blit vline (fast)
            self._preview_blit()
        except Exception:
            pass

    def _tt_anim_tick(self) -> None:
        _gp_tt_anim_tick(self)

    # ---------------------------------------------------------------------
    # Tooltip builder (still used to keep behavior identical elsewhere / future-proof)
    # ---------------------------------------------------------------------
    def _preview_build_tooltip_for_cols(self, cols: list[str]) -> None:
        _gp_preview_build_tooltip_for_cols(self, cols)

    # ---------------------------------------------------------------------
    # Dim overlay helpers
    # ---------------------------------------------------------------------
    def _ensure_dim_overlay(self):
        _gp_ensure_dim_overlay(self)

    def _set_dimmed(self, on: bool):
        _gp_set_dimmed(self, on)

    def _on_legend_popup_closed(self):
        _gp_on_legend_popup_closed(self)

    # ---------------------------------------------------------------------
    # Outside click closer (global)
    # ---------------------------------------------------------------------
    def _install_outside_click_closer(self):
        _gp_install_outside_click_closer(self)

    # ---------------------------------------------------------------------
    # Layout helpers
    # ---------------------------------------------------------------------
    def _preview_apply_axes_rect(self, right_frac: float, left_margin_px: float) -> None:
        _gp_preview_apply_axes_rect(self, right_frac=right_frac, left_margin_px=left_margin_px)

    def _preview_required_left_margin_px(self, renderer, pad_px: int = 8) -> float:
        return _gp_preview_required_left_margin_px(self, renderer, pad_px=pad_px)

    def _preview_relayout_and_redraw(self) -> None:
        try:
            if getattr(self, "_compare_mode", False):
                self._compare_relayout_and_redraw()
                return
            if getattr(self, "_single_mode_multi_axis", False):
                self._single_mode_relayout_and_redraw()
                return
        except Exception:
            pass
        _gp_preview_relayout_and_redraw(self)

    def _compare_relayout_and_redraw(self) -> None:
        """Relayout all compare subplots on resize/show."""
        try:
            if self._preview_canvas is None or self._preview_fig is None:
                return
            if not self._preview_canvas.isVisible():
                return
            if not getattr(self, "_compare_mode", False) or not getattr(self, "_compare_axes", None):
                return

            self._preview_canvas.draw()
            renderer = self._preview_canvas.get_renderer()
            if renderer is None:
                return

            # Compute a left margin that satisfies all subplots' tick labels.
            left_px = float(getattr(self, "_preview_left_margin_px_base", 60) or 60)
            for ax in list(self._compare_axes):
                try:
                    self._preview_ax = ax
                    left_px = max(left_px, float(self._preview_required_left_margin_px(renderer, pad_px=8)))
                except Exception:
                    continue

            try:
                fig_w_px = float(self._preview_fig.get_figwidth() * self._preview_fig.dpi)
                left = (left_px / fig_w_px) if fig_w_px > 1 else 0.08
                left = max(0.02, min(left, 0.35))

                self._preview_fig.subplots_adjust(
                    left=left,
                    right=0.985,
                    top=float(getattr(self, "_preview_top_frac", 0.98)),
                    bottom=float(getattr(self, "_preview_bottom_frac", 0.08)),
                    hspace=0.18,
                )
            except Exception:
                pass

            try:
                self._preview_invalidate_interaction_cache()
            except Exception:
                pass

            self._preview_canvas.draw()
            self._refresh_compare_backgrounds()
        except Exception:
            try:
                if self._preview_canvas is not None:
                    self._preview_canvas.draw_idle()
            except Exception:
                pass

    # ---------------------------------------------------------------------
    # Focus handling
    # ---------------------------------------------------------------------
    def bind_app_focus(self) -> None:
        _gp_bind_app_focus(self)

    def _on_app_state_changed(self, state):
        _gp_on_app_state_changed(self, state)

    # ---------------------------------------------------------------------
    # Per-result persistence (checked sensors -> JSON)
    # ---------------------------------------------------------------------
    def _selection_json_path(self) -> Optional[Path]:
        """
        Returns the JSON path for the current result folder (where run_window.csv lives).
        """
        return get_selection_json_path(self._preview_csv_path)

    def _load_saved_selection_for_current_result(self) -> Optional[list[str]]:
        """
        Loads the saved list of active sensors for this result folder (if present).
        Returns a list of column names, or None if no saved selection.
        """
        return load_active_cols(self._preview_csv_path)

    def _save_selection_for_current_result(self) -> None:
        """
        Saves current active sensors for this result folder.
        """
        save_active_cols(
            self._preview_csv_path,
            active_cols=list(self._preview_active_cols or []),
            available_cols=list(self._preview_available_cols or []),
        )

    def _apply_saved_or_default_selection(self) -> None:
        """
        After loading CSV and setting _preview_available_cols, apply saved selection if it exists.
        """
        saved = self._load_saved_selection_for_current_result()
        self._preview_active_cols = apply_saved_or_default_active_cols(
            available_cols=list(self._preview_available_cols or []),
            saved_cols=saved,
        )

    # ---------------------------------------------------------------------
    # Legend popup
    # ---------------------------------------------------------------------
    def _preview_stats_from_summary_csv(self) -> dict[str, tuple[float, float, float]]:
        return stats_from_summary_csv(self._preview_csv_path)

    def _preview_stats_from_df(self) -> dict[str, tuple[float, float, float]]:
        return stats_from_dataframe(self._preview_df_all)

    def _preview_get_stats_map(self) -> dict[str, tuple[float, float, float]]:
        s = self._preview_stats_from_summary_csv()
        if s:
            return s
        return self._preview_stats_from_df()

    def _preview_get_room_temperature(self) -> Optional[float]:
        """Load room temperature from avg_temperature.json if available."""
        try:
            if not self._preview_csv_path:
                return None
            avg_temp_path = Path(self._preview_csv_path).parent / "avg_temperature.json"
            if not avg_temp_path.exists():
                return None
            
            data = json.loads(avg_temp_path.read_text(encoding="utf-8"))
            room_temp = data.get("manual_average_temperature")
            if room_temp is not None:
                return float(room_temp)
        except Exception:
            pass
        return None

    def _preview_get_test_settings(self) -> Optional[dict]:
        """Load test settings from test_settings.json in the run folder, if present."""
        try:
            if not self._preview_csv_path:
                return None
            p = Path(self._preview_csv_path).parent / "test_settings.json"
            if not p.exists():
                return None
            data = json.loads(p.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else None
        except Exception:
            return None

    def _preview_infer_stats_title(self) -> str:
        return infer_stats_title(self._preview_available_cols)

    def _center_popup_on_app(self, dlg: QDialog) -> None:
        """Center popup in the application window (not the monitor)."""
        try:
            center_popup_on_app(self.parent, dlg)
        except Exception:
            pass

    def _open_legend_popup(self):
        try:
            if self._legend_popup is not None and self._legend_popup.isVisible():
                self._legend_popup.close()
                self._legend_popup = None
                self._set_dimmed(False)
                return
        except Exception:
            pass

        def _color_for(col: str) -> str:
            return str(self._preview_color_map.get(str(col), "#FFFFFF"))

        def _on_toggle(col: str, checked: bool, bulk_active_list: Optional[list[str]]):
            try:
                if bulk_active_list is not None:
                    active = [c for c in bulk_active_list if c in self._preview_available_cols]
                    if not active and self._preview_available_cols:
                        active = [self._preview_available_cols[0]]
                    self._preview_schedule_set_active_cols(active)
                    return

                active = list(self._preview_active_cols)
                if checked:
                    if col not in active and col in self._preview_available_cols:
                        active.append(col)
                else:
                    if col in active:
                        active.remove(col)
                    if not active and self._preview_available_cols:
                        active = [self._preview_available_cols[0]]
                self._preview_schedule_set_active_cols(active)
            except Exception:
                pass

        top = self.parent.window() if hasattr(self.parent, "window") else self.parent

        stats_map = self._preview_get_stats_map()
        title = self._preview_infer_stats_title()
        room_temp = self._preview_get_room_temperature()
        test_settings = self._preview_get_test_settings()

        # dim app behind popup
        self._set_dimmed(True)

        self._legend_popup = LegendStatsPopup(
            top,
            title=title,
            columns=self._preview_available_cols,
            active_set=set(self._preview_active_cols),
            color_for=_color_for,
            on_toggle=_on_toggle,
            stats_map=stats_map,
            room_temperature=room_temp,
            test_settings=test_settings,
            on_close=self._on_legend_popup_closed,
        )

        self._install_outside_click_closer()

        self._legend_popup.show()

        def _after_show_1():
            if self._legend_popup is None:
                return
            self._legend_popup._autosize_to_content()

            def _after_show_2():
                if self._legend_popup is None:
                    return
                self._ensure_dim_overlay()

                raise_center_and_focus(
                    parent=top,
                    dlg=self._legend_popup,
                    dim_overlay=self._dim_overlay,
                )

            QTimer.singleShot(0, _after_show_2)

        QTimer.singleShot(0, _after_show_1)

    def _preview_schedule_set_active_cols(self, cols: list[str]) -> None:
        """Debounce legend toggles so multiple clicks batch into one redraw."""
        try:
            cols = [c for c in (cols or []) if c in (self._preview_available_cols or [])]
            if not cols and self._preview_available_cols:
                cols = [self._preview_available_cols[0]]
            self._preview_pending_active_cols = list(cols)

            if self._preview_apply_active_timer is None:
                t = QTimer(self.parent)
                t.setSingleShot(True)
                try:
                    t.setTimerType(Qt.PreciseTimer)
                except Exception:
                    pass
                t.timeout.connect(self._preview_flush_pending_active_cols)
                self._preview_apply_active_timer = t

            # Small delay to coalesce rapid toggles; keeps UI feeling snappy.
            self._preview_apply_active_timer.start(35)
        except Exception:
            try:
                self._preview_set_active_cols(cols)
            except Exception:
                pass

    def _preview_flush_pending_active_cols(self) -> None:
        try:
            cols = list(self._preview_pending_active_cols or [])
            self._preview_pending_active_cols = None
            self._preview_set_active_cols(cols)
        except Exception:
            pass

    def _preview_set_active_cols(self, cols: list[str]) -> None:
        try:
            cols = [c for c in cols if c in self._preview_available_cols]
            if not cols and self._preview_available_cols:
                cols = [self._preview_available_cols[0]]

            self._preview_active_cols = list(cols)
            self._preview_apply_active_series()

            # persist per-result selection
            self._save_selection_for_current_result()
        except Exception:
            pass

    def _preview_apply_active_series(self) -> None:
        try:
            if getattr(self, "_compare_mode", False):
                return

            if self._preview_canvas is None or self._preview_df_all is None:
                return

            # If we are currently in single-axis mode but the active selection spans
            # multiple measurement types, switch to multi-axis mode so each unit gets
            # its own subplot.
            try:
                if not getattr(self, "_single_mode_multi_axis", False):
                    active_set = set(self._preview_active_cols or [])
                    all_cols = list(self._preview_available_cols or [])
                    groups = group_columns_by_unit(all_cols)

                    active_units: list[str] = []
                    for unit, cols in (groups or {}).items():
                        try:
                            if any(c in active_set for c in (cols or [])):
                                active_units.append(unit)
                        except Exception:
                            continue

                    if len(active_units) > 1 and self._preview_fig is not None and self._preview_x is not None:
                        def sort_key(item):
                            unit = item[0]
                            label = get_measurement_type_label(unit)
                            if "Temperature" in label:
                                return (0, label)
                            elif "Power" in label or "Watt" in label:
                                return (1, label)
                            elif "RPM" in label:
                                return (2, label)
                            else:
                                return (3, label)

                        # Plot ALL columns for each active unit; visibility is handled by active_set.
                        sorted_groups = sorted(
                            [(u, list(groups.get(u, []) or [])) for u in active_units],
                            key=sort_key,
                        )

                        self._plot_run_csv_multi_axis(
                            self._preview_df_all,
                            sorted_groups,
                            np.asarray(self._preview_x, dtype=float),
                            bool(getattr(self, "_preview_is_dt", False)),
                            dict(getattr(self, "_preview_color_map", {}) or {}),
                        )
                        return
            except Exception:
                pass

            # Single-mode multi-axis: update visibility per subplot, and rebuild layout
            # if measurement groups become empty/non-empty.
            if getattr(self, "_single_mode_multi_axis", False):
                self._single_apply_active_series()
                return

            if self._preview_ax is None:
                return

            aset = set(self._preview_active_cols)
            for c, ln in list(self._preview_lines.items()):
                try:
                    ln.set_visible(c in aset)
                except Exception:
                    pass

            try:
                self._preview_df = self._preview_df_all[self._preview_active_cols]
            except Exception:
                self._preview_df = self._preview_df_all

            self._preview_colors = [self._preview_color_map.get(c, "#FFFFFF") for c in self._preview_active_cols]

            # keep existing tooltip builder calls (safe), but hover uses Qt overlay
            self._preview_build_tooltip_for_cols(self._preview_active_cols)

            self._preview_autoscale_y_to_active()
            self._preview_relayout_and_redraw()

            # Rebuild hover caches after a short idle (expensive)
            self._schedule_hover_cache_rebuild()
        except Exception:
            try:
                if self._preview_canvas is not None:
                    self._preview_canvas.draw_idle()
            except Exception:
                pass

    def _schedule_hover_cache_rebuild(self) -> None:
        try:
            if self._hover_cache_timer is None:
                t = QTimer(self.parent)
                t.setSingleShot(True)
                try:
                    t.setTimerType(Qt.PreciseTimer)
                except Exception:
                    pass
                t.timeout.connect(self._rebuild_hover_cache)
                self._hover_cache_timer = t
            self._hover_cache_timer.start(60)
        except Exception:
            try:
                self._rebuild_hover_cache()
            except Exception:
                pass

    def _single_apply_active_series(self) -> None:
        """Apply active sensor selection to single-mode multi-axis plots.

        - Shows/hides lines immediately.
        - If an entire measurement unit becomes empty (or becomes active again),
          rebuilds the subplot stack so the remaining plots expand to fill the canvas.
        """
        try:
            if self._preview_canvas is None or self._preview_fig is None or self._preview_df_all is None:
                return
            if not getattr(self, "_single_mode_multi_axis", False) or not getattr(self, "_single_axes", None):
                return

            active_set = set(self._preview_active_cols or [])
            all_cols = list(self._preview_available_cols or [])
            all_groups = group_columns_by_unit(all_cols)

            def sort_key(item):
                unit = item[0]
                label = get_measurement_type_label(unit)
                if "Temperature" in label:
                    return (0, label)
                elif "Power" in label or "Watt" in label:
                    return (1, label)
                elif "RPM" in label:
                    return (2, label)
                else:
                    return (3, label)

            # Units that currently have at least one active sensor.
            required_units: list[str] = []
            for unit, group_cols in (all_groups or {}).items():
                try:
                    if any(c in active_set for c in (group_cols or [])):
                        required_units.append(unit)
                except Exception:
                    continue

            required_units = [u for u in required_units if u in (all_groups or {})]
            required_units_sorted = [u for (u, _cols) in sorted(((u, all_groups.get(u, [])) for u in required_units), key=sort_key)]
            required_unit_set = set(required_units_sorted)

            current_units: list[str] = []
            for ax in list(self._single_axes or []):
                st = (self._single_axis_state or {}).get(ax)
                if st and st.get("unit"):
                    current_units.append(str(st.get("unit")))
            current_unit_set = set(current_units)

            # Rebuild if the subplot stack needs to change (unit became empty / reappeared),
            # or if any required column isn't currently plotted.
            need_replot = (current_unit_set != required_unit_set)
            if not need_replot:
                for ax in list(self._single_axes or []):
                    st = (self._single_axis_state or {}).get(ax)
                    if not st:
                        continue
                    unit = st.get("unit")
                    if unit not in required_unit_set:
                        need_replot = True
                        break
                    want_cols = list((all_groups or {}).get(unit, []) or [])
                    have_lines = st.get("lines") or {}
                    for c in want_cols:
                        if c not in have_lines:
                            need_replot = True
                            break
                    if need_replot:
                        break

            # If we only need a single unit, fall back to single-axis mode.
            if need_replot:
                try:
                    x_vals = self._preview_x
                    if x_vals is None:
                        return
                except Exception:
                    return

                try:
                    self._exit_single_mode_multi_axis()
                except Exception:
                    pass

                if len(required_units_sorted) > 1:
                    sorted_groups = [(u, list((all_groups or {}).get(u, []) or [])) for u in required_units_sorted if (all_groups or {}).get(u)]
                    self._plot_run_csv_multi_axis(
                        self._preview_df_all,
                        sorted_groups,
                        np.asarray(x_vals, dtype=float),
                        bool(getattr(self, "_preview_is_dt", False)),
                        dict(getattr(self, "_preview_color_map", {}) or {}),
                    )
                else:
                    # Single-axis: keep behavior consistent with initial plot.
                    self._plot_run_csv_single_axis(
                        self._preview_df_all,
                        list(all_cols),
                        np.asarray(x_vals, dtype=float),
                        bool(getattr(self, "_preview_is_dt", False)),
                        dict(getattr(self, "_preview_color_map", {}) or {}),
                    )
                return

            # Update visibility + per-axis tooltip caches for active series.
            for ax in list(self._single_axes or []):
                st = (self._single_axis_state or {}).get(ax)
                if not st:
                    continue

                unit = st.get("unit")
                group_cols = list((all_groups or {}).get(unit, []) or [])

                lines = st.get("lines") or {}
                for name, ln in list(lines.items()):
                    try:
                        ln.set_visible(name in active_set)
                    except Exception:
                        pass

                active_cols = [c for c in group_cols if c in active_set]
                st["cols"] = list(active_cols)
                st["colors"] = [str(self._preview_color_map.get(c, "#FFFFFF")) for c in active_cols]

                # Rebuild numpy hover caches to reflect active cols only.
                try:
                    if active_cols:
                        df_np = self._preview_df_all[active_cols].to_numpy(dtype=float, copy=False)
                        st["df"] = self._preview_df_all[active_cols].copy()
                    else:
                        df_np = np.zeros((int(len(self._preview_x or [])), 0), dtype=float)
                        st["df"] = self._preview_df_all.iloc[:, 0:0].copy()
                    st["df_np"] = df_np
                except Exception:
                    pass

            # Autoscale each subplot to its active lines.
            for ax in list(self._single_axes or []):
                st = (self._single_axis_state or {}).get(ax)
                if not st:
                    continue
                active_cols = list(st.get("cols") or [])
                if not active_cols:
                    continue
                ys = []
                series_data = st.get("series_data") or {}
                for name in active_cols:
                    y = series_data.get(name)
                    if y is None:
                        continue
                    try:
                        y = np.asarray(y, dtype=float)
                        y = y[np.isfinite(y)]
                        if y.size:
                            ys.append(y)
                    except Exception:
                        pass
                if not ys:
                    continue
                try:
                    y_all = np.concatenate(ys)
                    ymin = float(np.nanmin(y_all))
                    ymax = float(np.nanmax(y_all))
                    if np.isfinite(ymin) and np.isfinite(ymax):
                        pad = 1.0 if ymin == ymax else 0.06 * (ymax - ymin)
                        ax.set_ylim(ymin - pad, ymax + pad)
                except Exception:
                    pass

            try:
                self._single_last_idx = None
            except Exception:
                pass

            try:
                self._single_mode_relayout_and_redraw()
            except Exception:
                pass

            # Background refresh is relatively heavy; debounce it.
            try:
                if self._single_bg_refresh_timer is None:
                    t = QTimer(self.parent)
                    t.setSingleShot(True)
                    try:
                        t.setTimerType(Qt.PreciseTimer)
                    except Exception:
                        pass
                    t.timeout.connect(self._refresh_single_backgrounds)
                    self._single_bg_refresh_timer = t
                self._single_bg_refresh_timer.start(80)
            except Exception:
                pass

            try:
                self._preview_canvas.draw_idle()
            except Exception:
                pass
        except Exception:
            try:
                if self._preview_canvas is not None:
                    self._preview_canvas.draw_idle()
            except Exception:
                pass

    def _preview_autoscale_y_to_active(self) -> None:
        try:
            ax = self._preview_ax
            if ax is None or not self._preview_active_cols:
                return

            ys = []
            for name in self._preview_active_cols:
                y = self._preview_series_data.get(name)
                if y is None:
                    continue
                y = np.asarray(y, dtype=float)
                y = y[np.isfinite(y)]
                if y.size:
                    ys.append(y)
            if not ys:
                return

            y_all = np.concatenate(ys)
            ymin = float(np.nanmin(y_all))
            ymax = float(np.nanmax(y_all))
            if not np.isfinite(ymin) or not np.isfinite(ymax):
                return

            pad = 1.0 if ymin == ymax else 0.06 * (ymax - ymin)
            ax.set_ylim(ymin - pad, ymax + pad)
        except Exception:
            pass

    # ---------------------------------------------------------------------
    # Plotting
    # ---------------------------------------------------------------------
    def _plot_compare_manifest(self, manifest_path: Path) -> None:
        """
        Compare mode: one subplot per sensor; each subplot overlays the same set of runs.
        - Stable per-run colors across ALL subplots (run -> color)
        - Qt overlay tooltip per subplot (animated)
        - Hovered subplot follows cursor y; other subplots follow highest line at idx
        - Tooltip header shows elapsed time like single mode (m:ss or h:mm:ss)
        """
        if self._preview_canvas is None or self._preview_fig is None:
            raise RuntimeError("Preview canvas unavailable")

        self._close_legend_popup()
        self._exit_compare_mode()
        self._hide_qt_tooltip()

        # -----------------------------
        # Load manifest
        # -----------------------------
        try:
            m = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception:
            m = {}

        sensors = [str(s) for s in (m.get("sensors") or []) if str(s).strip()]
        runs_rel = [str(r) for r in (m.get("runs") or []) if str(r).strip()]
        if not sensors or len(runs_rel) < 2:
            self._preview_label.clear()
            return

        # manifest lives at: runs/<compare_case>/<compare_run>/compare_manifest.json
        # so runs root is 2 parents up.
        try:
            runs_root = manifest_path.parents[2]
        except Exception:
            runs_root = manifest_path.parent

        run_dirs: list[Path] = []
        run_labels: list[str] = []

        def _stress_label_for_run_dir(rd: Path) -> str:
            """Best-effort CPU/GPU/CPUGPU label for a run folder."""
            try:
                m2 = re.match(r"^(CPU|GPU|CPUGPU)_W\d+_L\d+_V\d+$", str(rd.name), flags=re.IGNORECASE)
                if m2:
                    return str(m2.group(1)).upper()
            except Exception:
                pass

            # Fallback: infer from recorded settings (if present)
            try:
                p = rd / "test_settings.json"
                if p.exists():
                    s = json.loads(p.read_text(encoding="utf-8"))
                    sm = str((s or {}).get("stress_mode") or "").upper()
                    if "CPU" in sm and "GPU" in sm:
                        return "CPUGPU"
                    if "GPU" in sm:
                        return "GPU"
                    if "CPU" in sm:
                        return "CPU"
            except Exception:
                pass

            return "CPU"

        def _compare_display_label(rd: Path) -> str:
            """Use '<case> <stress>' for compare tooltips/legend."""
            try:
                case = (rd.parent.name if rd.parent is not None else "").strip()
            except Exception:
                case = ""
            stress = _stress_label_for_run_dir(rd)
            return (f"{case} {stress}".strip() if case else stress)

        used_labels: dict[str, int] = {}
        for rel in runs_rel:
            try:
                p = Path(*str(rel).replace("\\", "/").split("/"))
            except Exception:
                p = Path(str(rel))
            rd = (runs_root / p)
            run_dirs.append(rd)
            try:
                base = _compare_display_label(rd)
                n = used_labels.get(base, 0) + 1
                used_labels[base] = n
                run_labels.append(base if n == 1 else f"{base} #{n}")
            except Exception:
                run_labels.append(str(rel))

        # -----------------------------
        # Load run CSVs (keep only requested sensors)
        # -----------------------------
        run_dfs: list[pd.DataFrame] = []
        for rd in run_dirs:
            csvp = rd / "run_window.csv"
            if not csvp.exists():
                run_dfs.append(pd.DataFrame())
                continue
            try:
                df_all, cols = load_run_csv_dataframe(str(csvp))
                available = set(cols or [])
                keep = [s for s in sensors if s in available]
                df_keep = df_all[keep].copy() if keep else pd.DataFrame(index=df_all.index)

                # Ensure all sensors exist as columns (fill missing with NaN)
                for s in sensors:
                    if s not in df_keep.columns:
                        df_keep[s] = np.nan
                df_keep = df_keep[sensors]
                run_dfs.append(df_keep)
            except Exception:
                run_dfs.append(pd.DataFrame())

        run_dfs = trim_dataframes_to_shortest_duration(run_dfs)
        non_empty = [df for df in run_dfs if df is not None and not df.empty]
        if not non_empty:
            self._preview_label.clear()
            return

        try:
            min_len = min(int(len(df)) for df in non_empty)
        except Exception:
            min_len = 0
        if min_len < 2:
            self._preview_label.clear()
            return

        try:
            min_dur = min((df.index.max() - df.index.min()) for df in non_empty)
            min_dur_sec = float(getattr(min_dur, "total_seconds", lambda: 0.0)())
        except Exception:
            min_dur_sec = float(min_len - 1)

        # Common time base for interpolation (elapsed seconds -> Timestamp index)
        common_elapsed = np.linspace(0.0, max(0.0, min_dur_sec), num=min_len)
        base_ts = pd.Timestamp("2000-01-01")
        common_index = base_ts + pd.to_timedelta(common_elapsed, unit="s")

        # Per-run elapsed axes (seconds from each run's own start)
        run_elapsed_axes: list[np.ndarray] = []
        for df in run_dfs:
            if df is None or df.empty:
                run_elapsed_axes.append(np.array([], dtype=float))
                continue
            try:
                td = (df.index - df.index.min())
                run_elapsed_axes.append(td.total_seconds().to_numpy(dtype=float))
            except Exception:
                run_elapsed_axes.append(np.arange(len(df), dtype=float))

        # -----------------------------
        # Build subplots: one per sensor
        # -----------------------------
        self._preview_fig.clear()
        n = len(sensors)
        axes = self._preview_fig.subplots(nrows=n, ncols=1, sharex=True)
        if not isinstance(axes, (list, tuple, np.ndarray)):
            axes = [axes]
        else:
            axes = list(np.ravel(axes))

        self._compare_mode = True
        self._compare_axes = axes
        self._compare_axis_state = {}
        self._compare_last_idx = None

        # -----------------------------
        # Stable per-run palette (run -> color)
        # -----------------------------
        try:
            cmaps = [cm.get_cmap("tab20"), cm.get_cmap("tab20b"), cm.get_cmap("tab20c")]
            palette: list[str] = []
            for cmap in cmaps:
                for k in range(int(getattr(cmap, "N", 20) or 20)):
                    try:
                        palette.append(mcolors.to_hex(cmap(k)))
                    except Exception:
                        pass
            if not palette:
                palette = ["#1f77b4", "#ff7f0e", "#2ca02c", "#d62728", "#9467bd", "#8c564b"]
        except Exception:
            palette = ["#1f77b4", "#ff7f0e", "#2ca02c", "#d62728", "#9467bd", "#8c564b"]

        run_color_map: dict[str, str] = {}
        for j, lbl in enumerate(run_labels):
            run_color_map[str(lbl)] = palette[j % len(palette)]

        # -----------------------------
        # Per-axis Qt tooltip widgets (compare mode shows one per subplot)
        # -----------------------------
        def _make_compare_tt() -> Optional[QLabel]:
            try:
                if self._preview_canvas is None:
                    return None
                w = QLabel(self._preview_canvas)
                w.setAttribute(Qt.WA_TransparentForMouseEvents, True)
                w.setTextFormat(Qt.RichText)
                w.setWordWrap(False)

                f = QFont("DejaVu Sans Mono")
                try:
                    f.setStyleHint(QFont.Monospace)
                except Exception:
                    pass
                f.setPointSize(10)
                w.setFont(f)

                # Match single-mode style
                w.setStyleSheet(
                    "QLabel {"
                    " background-color: rgba(24,24,24,160);"
                    " border: 1px solid rgba(255,255,255,18);"
                    " border-radius: 8px;"
                    " padding: 8px 10px;"
                    " color: #FFFFFF;"
                    "}"
                )
                w.hide()
                return w
            except Exception:
                return None

        # -----------------------------
        # Build each sensor subplot
        # -----------------------------
        for i, sensor in enumerate(sensors):
            ax = axes[i]

            apply_dark_axes_style(
                self._preview_fig,
                ax,
                grid_color=self._preview_grid_color,
                dot_dashes=self._preview_dot_dashes,
            )

            try:
                ax.spines["top"].set_visible(False)
                ax.spines["bottom"].set_visible(False)
            except Exception:
                pass

            # Build per-sensor dataframe: columns are runs (labels), index is common_index
            df_sensor = pd.DataFrame(index=common_index)
            for lbl, df_run, x_run in zip(run_labels, run_dfs, run_elapsed_axes):
                if df_run is None or df_run.empty or sensor not in df_run.columns or x_run.size < 2:
                    df_sensor[str(lbl)] = np.full(shape=(min_len,), fill_value=np.nan, dtype=float)
                    continue

                y = pd.to_numeric(df_run[sensor], errors="coerce").to_numpy(dtype=float)
                mask = np.isfinite(y) & np.isfinite(x_run)
                if int(mask.sum()) < 2:
                    df_sensor[str(lbl)] = np.full(shape=(min_len,), fill_value=np.nan, dtype=float)
                    continue
                try:
                    y_i = np.interp(common_elapsed, x_run[mask], y[mask], left=np.nan, right=np.nan)
                except Exception:
                    y_i = np.full(shape=(min_len,), fill_value=np.nan, dtype=float)

                df_sensor[str(lbl)] = y_i

            is_dt, x_vals = compute_x_vals(df_sensor)

            # Per-subplot color map: SAME for each sensor (run -> color)
            color_map: dict[str, str] = {}
            for lbl in list(df_sensor.columns):
                color_map[str(lbl)] = run_color_map.get(str(lbl), "#FFFFFF")

            lines, series_data, _colors = plot_lines_with_glow(
                ax,
                df_all=df_sensor,
                cols=list(df_sensor.columns),
                x_vals=x_vals,
                is_dt=is_dt,
                color_map=color_map,
            )

            # y autoscale
            try:
                ys = []
                for yarr in series_data.values():
                    yarr = np.asarray(yarr, dtype=float)
                    yarr = yarr[np.isfinite(yarr)]
                    if yarr.size:
                        ys.append(yarr)
                if ys:
                    y_all = np.concatenate(ys)
                    ymin = float(np.nanmin(y_all))
                    ymax = float(np.nanmax(y_all))
                    pad = 1.0 if ymin == ymax else 0.06 * (ymax - ymin)
                    ax.set_ylim(ymin - pad, ymax + pad)
            except Exception:
                pass

            try:
                if len(x_vals) > 0:
                    ax.set_xlim(left=x_vals[0], right=x_vals[-1])
            except Exception:
                pass

            # sensor label
            try:
                ax.text(
                    0.01,
                    1.0,
                    str(sensor),
                    transform=ax.transAxes,
                    ha="left",
                    va="bottom",
                    fontsize=9,
                    color="#EAEAEA",
                    zorder=2500,
                    clip_on=False,
                )
            except Exception:
                pass

            if i == (n - 1):
                apply_elapsed_time_formatter(ax, is_dt=is_dt, x_vals=x_vals)

            vline = create_hover_vline(
                ax,
                x0=x_vals[0] if len(x_vals) else 0.0,
                grid_color=self._preview_grid_color,
                dot_dashes=self._preview_dot_dashes,
            )

            # Cache compare data as numpy for fast hover; tooltip rendered via Qt overlay
            try:
                df_np = df_sensor.to_numpy(dtype=float, copy=False)
            except Exception:
                df_np = np.asarray(df_sensor.to_numpy(), dtype=float)

            cols = [str(c) for c in list(df_sensor.columns)]
            cols_colors = [str(color_map.get(str(c), "#FFFFFF")) for c in cols]

            self._compare_axis_state[ax] = {
                "x": np.asarray(x_vals, dtype=float),
                "is_dt": bool(is_dt),
                "df": df_sensor,
                "df_np": df_np,
                "cols": cols,
                "colors": cols_colors,
                "vline": vline,
                "bg": None,
                "qt_tt": _make_compare_tt(),
            }

        # -----------------------------
        # Show canvas
        # -----------------------------
        try:
            self._preview_label.clear()
            self._preview_label.hide()
        except Exception:
            pass

        try:
            self._preview_canvas.show()
        except Exception:
            pass

        self._refresh_compare_backgrounds()

        try:
            self._preview_last_canvas_wh = (int(self._preview_canvas.width()), int(self._preview_canvas.height()))
        except Exception:
            pass

        # -----------------------------
        # Hover handler (compare mode)
        # -----------------------------
        def _compare_mouse_move(ev):
            try:
                if self._preview_canvas is None or not self._compare_mode:
                    return

                wh = (int(self._preview_canvas.width()), int(self._preview_canvas.height()))
                try:
                    self._preview_last_canvas_wh = wh
                except Exception:
                    pass
                if self._compare_last_canvas_wh != wh:
                    self._compare_last_canvas_wh = wh
                    self._refresh_compare_backgrounds()

                x = ev.pos().x()
                y = ev.pos().y()
                self._qt_last_mouse_xy = (int(x), int(y))

                h = self._preview_canvas.height()
                display_x = x
                display_y = h - y

                # Find which axis is under cursor
                hit_ax = None
                for ax in self._compare_axes:
                    try:
                        if ax.bbox.contains(display_x, display_y):
                            hit_ax = ax
                            break
                    except Exception:
                        continue

                if hit_ax is None:
                    self._hide_compare_hover_all()
                    return

                st_hit = self._compare_axis_state.get(hit_ax)
                if not st_hit:
                    return

                # Cursor -> data coords on hovered axis
                try:
                    data_xy = hit_ax.transData.inverted().transform((display_x, display_y))
                    xdata = float(data_xy[0])
                    ydata2 = float(data_xy[1])
                except Exception:
                    return

                # Outside x-limits? hide
                try:
                    x0, x1 = hit_ax.get_xlim()
                    if xdata < min(x0, x1) or xdata > max(x0, x1):
                        self._hide_compare_hover_all()
                        return
                except Exception:
                    pass

                # Sync vlines across ALL subplots
                try:
                    for ax2 in self._compare_axes:
                        st2 = self._compare_axis_state.get(ax2)
                        if not st2:
                            continue
                        vl2 = st2.get("vline")
                        if vl2 is not None:
                            try:
                                vl2.set_xdata([xdata, xdata])
                                vl2.set_visible(True)
                            except Exception:
                                pass
                except Exception:
                    pass

                # Nearest index in x (sorted)
                try:
                    xa = st_hit.get("x")
                    if xa is None or len(xa) < 2:
                        return
                    idx = self._nearest_index_sorted(np.asarray(xa, dtype=float), float(xdata))
                except Exception:
                    return
                idx = int(max(0, min(int(idx), int(len(st_hit["x"]) - 1))))

                idx_changed = (self._compare_last_idx != idx)
                self._compare_last_idx = idx

                # Elapsed header like single mode (m:ss or h:mm:ss)
                try:
                    xa_ref = np.asarray(st_hit.get("x"), dtype=float)
                    if xa_ref.size >= 1:
                        is_dt_ref = bool(st_hit.get("is_dt", True))
                        base_v = float(xa_ref[0])
                        cur_v = float(xa_ref[int(idx)])
                        d = (cur_v - base_v) * 86400.0 if is_dt_ref else (cur_v - base_v)
                        if not np.isfinite(d):
                            d = 0.0
                        d = max(0.0, float(d))
                        total_seconds = int(d)
                        hours = total_seconds // 3600
                        minutes = (total_seconds % 3600) // 60
                        seconds = total_seconds % 60
                        tstr = f"{hours}:{minutes:02d}:{seconds:02d}" if hours > 0 else f"{minutes}:{seconds:02d}"
                    else:
                        tstr = ""
                except Exception:
                    tstr = ""

                # Update + animate tooltips for each axis
                for ax2 in self._compare_axes:
                    st2 = self._compare_axis_state.get(ax2)
                    if not st2:
                        continue
                    tt = st2.get("qt_tt")
                    if tt is None:
                        continue

                    # yref behavior:
                    # - hovered axis follows cursor y
                    # - other axes follow the highest line at idx (nanmax across runs)
                    try:
                        y0, y1 = ax2.get_ylim()
                        lo, hi = (float(y0), float(y1)) if y0 <= y1 else (float(y1), float(y0))
                    except Exception:
                        lo, hi = (0.0, 1.0)

                    if ax2 is hit_ax:
                        try:
                            yref = float(ydata2)
                        except Exception:
                            yref = lo
                    else:
                        try:
                            df_np2 = st2.get("df_np", None)
                            if df_np2 is not None:
                                row_vals = np.asarray(df_np2[int(idx), :], dtype=float)
                                ymax = float(np.nanmax(row_vals))
                            else:
                                ymax = float("nan")
                        except Exception:
                            ymax = float("nan")

                        if ymax == ymax:  # not NaN
                            yref = ymax
                        else:
                            yref = lo + 0.65 * (hi - lo)

                    # Clamp inside axis a bit (avoid touching borders)
                    try:
                        if hi > lo:
                            pad = 0.03 * (hi - lo)
                            yref = max(lo + pad, min(hi - pad, yref))
                    except Exception:
                        pass

                    if idx_changed:
                        cols2 = st2.get("cols") or []
                        colors2 = st2.get("colors") or []
                        try:
                            vals2 = np.asarray(st2.get("df_np")[idx, :], dtype=float)
                        except Exception:
                            vals2 = np.full((len(cols2),), np.nan, dtype=float)

                        try:
                            work2 = np.where(np.isfinite(vals2), vals2, -1e30)
                            order2 = np.argsort(work2)[::-1]
                        except Exception:
                            order2 = np.arange(len(cols2), dtype=int)

                        names_sorted = []
                        values_sorted = []
                        colors_sorted = []
                        for i2 in order2:
                            try:
                                name = cols2[int(i2)]
                            except Exception:
                                name = ""
                            try:
                                v = float(vals2[int(i2)])
                            except Exception:
                                v = float("nan")
                            try:
                                col = colors2[int(i2)]
                            except Exception:
                                col = "#FFFFFF"
                            names_sorted.append(name)
                            values_sorted.append(self._format_value(name, v))
                            colors_sorted.append(col)

                        html = self._qt_build_tooltip_html(tstr, names_sorted, values_sorted, colors_sorted)
                        try:
                            tt.setText(html)
                        except Exception:
                            pass

                    try:
                        tt.show()
                    except Exception:
                        pass

                    pos2 = self._qt_compute_tooltip_pos_in_ax(
                        tt, ax2, xdata=float(xdata), ydata=float(yref), prefer_mode=self._qt_tt_mode
                    )
                    if pos2 is not None:
                        tx2, ty2, mode2 = pos2
                        self._qt_tt_mode = str(mode2)
                        self._qt_move_to(tt, int(tx2), int(ty2))

                # Blit vlines only (tooltips are Qt overlays)
                self._compare_blit_vlines_only()

            except Exception:
                pass

        try:
            self._preview_canvas.mouseMoveEvent = _compare_mouse_move
        except Exception:
            pass

        # Disable legend&stats button in compare mode
        self._ls_btn_text = None
        self._ls_btn_bbox = None

        try:
            self._preview_canvas.draw_idle()
        except Exception:
            pass

        try:
            QTimer.singleShot(0, self._compare_relayout_and_redraw)
        except Exception:
            pass


    def _plot_run_csv(self, fpath: str) -> None:
        if self._preview_canvas is None or self._preview_ax is None:
            raise RuntimeError("Preview canvas unavailable")

        self._exit_compare_mode()
        self._exit_single_mode_multi_axis()

        self._close_legend_popup()
        self._preview_csv_path = fpath

        df_data, cols = load_run_csv_dataframe(fpath)

        self._preview_df_all = df_data[cols]
        self._preview_available_cols = list(cols)

        # apply last saved selection for THIS result (if any)
        self._apply_saved_or_default_selection()

        is_dt, x_vals = compute_x_vals(df_data)
        self._preview_is_dt = bool(is_dt)
        self._preview_x = x_vals

        try:
            self._tt_anim_timer.stop()
        except Exception:
            pass
        self._tt_anim_start_xy = None
        self._tt_anim_target_xy = None

        # Group columns by measurement type (unit)
        all_groups = group_columns_by_unit(list(cols))
        
        # Filter groups to only include units that have at least one active column.
        # Keep ALL columns within that unit so selecting additional sensors later is instant.
        active_set = set(self._preview_active_cols)
        filtered_groups: dict[str, list[str]] = {}
        for unit, group_cols in all_groups.items():
            active_in_group = [c for c in group_cols if c in active_set]
            if active_in_group:
                filtered_groups[unit] = list(group_cols)
        
        # Sort groups by a consistent order (temperature first, then others)
        def sort_key(item):
            unit = item[0]
            label = get_measurement_type_label(unit)
            if "Temperature" in label:
                return (0, label)
            elif "Power" in label or "Watt" in label:
                return (1, label)
            elif "RPM" in label:
                return (2, label)
            else:
                return (3, label)
        
        sorted_groups = sorted(filtered_groups.items(), key=sort_key)
        
        # Decide: multi-axis if we have multiple measurement types, otherwise single
        num_groups = len(sorted_groups)
        use_multi_axis = num_groups > 1
        
        # Build color map for all columns
        self._preview_color_map = build_tab20_color_map(list(cols))
        
        # =========================================
        # Multi-axis mode (split by measurement type)
        # =========================================
        if use_multi_axis:
            self._plot_run_csv_multi_axis(
                df_data, sorted_groups, x_vals, is_dt, self._preview_color_map
            )
        # =========================================
        # Single-axis mode (all on one graph)
        # =========================================
        else:
            self._plot_run_csv_single_axis(
                df_data, list(cols), x_vals, is_dt, self._preview_color_map
            )

    def _plot_run_csv_single_axis(
        self,
        df_data: pd.DataFrame,
        cols: list[str],
        x_vals: np.ndarray,
        is_dt: bool,
        color_map: dict[str, str],
    ) -> None:
        """Plot all active columns on a single axis."""
        if self._preview_canvas is None or self._preview_ax is None:
            return

        self._preview_ax.clear()
        self._ls_btn_text = None
        self._ls_btn_bbox = None

        apply_dark_axes_style(
            self._preview_fig,
            self._preview_ax,
            grid_color=self._preview_grid_color,
            dot_dashes=self._preview_dot_dashes,
        )

        self._preview_lines, self._preview_series_data, self._preview_colors = plot_lines_with_glow(
            self._preview_ax,
            df_all=df_data,
            cols=list(cols),
            x_vals=x_vals,
            is_dt=is_dt,
            color_map=color_map,
        )

        # Hide lines that aren't active
        aset = set(self._preview_active_cols)
        for name, ln in list(self._preview_lines.items()):
            try:
                ln.set_visible(name in aset)
            except Exception:
                pass

        self._preview_x = x_vals

        try:
            self._preview_df = df_data[self._preview_active_cols]
        except Exception:
            self._preview_df = df_data

        self._preview_build_tooltip_for_cols(self._preview_active_cols)
        self._preview_autoscale_y_to_active()

        try:
            if len(x_vals) > 0:
                self._preview_ax.set_xlim(left=x_vals[0], right=x_vals[-1])
        except Exception:
            pass

        apply_elapsed_time_formatter(self._preview_ax, is_dt=is_dt, x_vals=x_vals)

        try:
            self._preview_vline = create_hover_vline(
                self._preview_ax,
                x0=self._preview_x[0],
                grid_color=self._preview_grid_color,
                dot_dashes=self._preview_dot_dashes,
            )
        except Exception:
            self._preview_vline = None

        self._preview_build_tooltip_for_cols(self._preview_active_cols)
        self._preview_autoscale_y_to_active()

        try:
            self._ls_btn_text = self._preview_ax.text(
                0.995, 0.995, "≡ Legend & stats",
                transform=self._preview_ax.transAxes,
                ha="right", va="top",
                fontsize=9,
                color="#BDBDBD",
                zorder=3000,
                bbox=dict(boxstyle="round,pad=0.35", fc=(0, 0, 0, 0.0), ec=(0, 0, 0, 0.0)),
            )
        except Exception:
            self._ls_btn_text = None
            self._ls_btn_bbox = None

        try:
            self._preview_label.clear()
            self._preview_label.hide()
        except Exception:
            pass

        try:
            self._preview_canvas.show()
            try:
                self._preview_canvas.draw()
                self._on_preview_draw()
            except Exception:
                pass
        except Exception:
            pass

        # Build hover caches AFTER first draw & df selection
        try:
            self._rebuild_hover_cache()
        except Exception:
            pass

        try:
            if self._preview_mpl_cid is not None:
                try:
                    self._preview_canvas.mpl_disconnect(self._preview_mpl_cid)
                except Exception:
                    pass

            self._preview_mpl_cid = self._preview_canvas.mpl_connect(
                "motion_notify_event", self._on_preview_hover
            )
        except Exception:
            self._preview_mpl_cid = None

        QTimer.singleShot(0, self._preview_relayout_and_redraw)

    def _plot_run_csv_multi_axis(
        self,
        df_data: pd.DataFrame,
        sorted_groups: list[tuple[str, list[str]]],
        x_vals: np.ndarray,
        is_dt: bool,
        color_map: dict[str, str],
    ) -> None:
        """Plot active columns split across multiple axes by measurement type."""
        if self._preview_canvas is None or self._preview_fig is None:
            return

        self._single_mode_multi_axis = True
        self._ls_btn_text = None
        self._ls_btn_bbox = None

        # Clear figure and create subplots
        self._preview_fig.clear()
        n = len(sorted_groups)
        axes = self._preview_fig.subplots(nrows=n, ncols=1, sharex=True)
        if not isinstance(axes, (list, tuple, np.ndarray)):
            axes = [axes]
        else:
            axes = list(np.ravel(axes))

        self._single_axes = axes
        self._single_axis_state = {}
        self._single_axis_vlines = {}

        # Per-axis Qt tooltip widgets (single-mode multi-axis shows one per subplot)
        def _make_single_tt() -> Optional[QLabel]:
            try:
                if self._preview_canvas is None:
                    return None
                w = QLabel(self._preview_canvas)
                w.setAttribute(Qt.WA_TransparentForMouseEvents, True)
                w.setTextFormat(Qt.RichText)
                w.setWordWrap(False)

                f = QFont("DejaVu Sans Mono")
                try:
                    f.setStyleHint(QFont.Monospace)
                except Exception:
                    pass
                f.setPointSize(10)
                w.setFont(f)

                w.setStyleSheet(
                    "QLabel {"
                    " background-color: rgba(24,24,24,160);"
                    " border: 1px solid rgba(255,255,255,18);"
                    " border-radius: 8px;"
                    " padding: 8px 10px;"
                    " color: #FFFFFF;"
                    "}"
                )
                w.hide()
                return w
            except Exception:
                return None

        # Plot each measurement type on its own axis
        for idx, (unit, group_cols) in enumerate(sorted_groups):
            ax = axes[idx]
            measurement_label = get_measurement_type_label(unit)

            apply_dark_axes_style(
                self._preview_fig,
                ax,
                grid_color=self._preview_grid_color,
                dot_dashes=self._preview_dot_dashes,
            )

            try:
                ax.spines["top"].set_visible(False)
                ax.spines["bottom"].set_visible(False)
            except Exception:
                pass

            # Plot lines for this measurement group
            lines, series_data, colors = plot_lines_with_glow(
                ax,
                df_all=df_data,
                cols=group_cols,
                x_vals=x_vals,
                is_dt=is_dt,
                color_map=color_map,
            )

            # Hide lines not in active set
            active_set = set(self._preview_active_cols)
            for col_name, ln in list(lines.items()):
                try:
                    ln.set_visible(col_name in active_set)
                except Exception:
                    pass

            # Set x limits
            try:
                if len(x_vals) > 0:
                    ax.set_xlim(left=x_vals[0], right=x_vals[-1])
            except Exception:
                pass

            # Create vline for this axis
            try:
                vline = create_hover_vline(
                    ax,
                    x0=x_vals[0],
                    grid_color=self._preview_grid_color,
                    dot_dashes=self._preview_dot_dashes,
                )
            except Exception:
                vline = None

            # Add measurement label above the axes (left), so it doesn't collide with plot content.
            try:
                ax.text(
                    0.0,
                    1.02,
                    str(measurement_label),
                    transform=ax.transAxes,
                    ha="left",
                    va="bottom",
                    fontsize=11,
                    color="#EAEAEA",
                    zorder=2600,
                    clip_on=False,
                )
            except Exception:
                pass

            # Add Legend & stats button only on the TOP-most graph, aligned to the same row (right).
            if idx == 0:
                try:
                    self._ls_btn_text = ax.text(
                        1.0,
                        1.02,
                        "≡ Legend & stats",
                        transform=ax.transAxes,
                        ha="right",
                        va="bottom",
                        fontsize=9,
                        color="#BDBDBD",
                        zorder=3000,
                        clip_on=False,
                        bbox=dict(boxstyle="round,pad=0.35", fc=(0, 0, 0, 0.0), ec=(0, 0, 0, 0.0)),
                    )
                    self._ls_btn_bbox = None
                except Exception:
                    self._ls_btn_text = None
                    self._ls_btn_bbox = None

            # Store axis state
            try:
                df_np = df_data[group_cols].to_numpy(dtype=float, copy=False)
            except Exception:
                try:
                    df_np = np.asarray(df_data[group_cols].to_numpy(), dtype=float)
                except Exception:
                    df_np = None

            cols2 = [str(c) for c in list(group_cols)]
            colors2 = [str(color_map.get(str(c), "#FFFFFF")) for c in cols2]

            self._single_axis_state[ax] = {
                "unit": unit,
                "cols": cols2,
                "lines": lines,
                "series_data": series_data,
                "colors": colors2,
                "x": np.asarray(x_vals, dtype=float),
                "is_dt": bool(is_dt),
                "df": df_data[group_cols].copy(),
                "df_np": df_np,
                "vline": vline,
                "bg": None,
                "qt_tt": _make_single_tt(),
            }
            self._single_axis_vlines[ax] = vline

            # Apply time formatter only to the last (bottom) axis
            if idx == len(sorted_groups) - 1:
                apply_elapsed_time_formatter(ax, is_dt=is_dt, x_vals=x_vals)
            else:
                # Remove x-axis labels for non-bottom axes
                try:
                    ax.set_xticklabels([])
                except Exception:
                    pass

        # Adjust layout (will be refined in relayout)
        try:
            self._preview_fig.subplots_adjust(
                left=0.08,
                right=0.985,
                top=0.93,
                bottom=0.05,
                hspace=0.35,
            )
        except Exception:
            pass

        try:
            self._preview_label.clear()
            self._preview_label.hide()
        except Exception:
            pass

        try:
            self._preview_canvas.show()
            self._preview_canvas.draw()
        except Exception:
            pass

        # Cache backgrounds for fast vline blit
        try:
            self._single_last_canvas_wh = (int(self._preview_canvas.width()), int(self._preview_canvas.height()))
        except Exception:
            pass
        try:
            self._refresh_single_backgrounds()
        except Exception:
            pass

        # Set up multi-axis hover handler
        try:
            if self._preview_mpl_cid is not None:
                try:
                    self._preview_canvas.mpl_disconnect(self._preview_mpl_cid)
                except Exception:
                    pass
        except Exception:
            pass

        # Install custom mouse move handler for multi-axis mode
        def _single_multi_mouse_move(ev):
            try:
                if self._preview_canvas is None or not self._single_mode_multi_axis:
                    return

                wh = (int(self._preview_canvas.width()), int(self._preview_canvas.height()))
                try:
                    if self._single_last_canvas_wh != wh:
                        self._single_last_canvas_wh = wh
                        self._refresh_single_backgrounds()
                except Exception:
                    pass

                # Check legend&stats button
                try:
                    if hasattr(ev, 'pos') and ev.pos():
                        x, y = ev.pos().x(), ev.pos().y()
                        if self._is_over_ls_button(int(x), int(y)):
                            if not self._hovering_ls_btn:
                                self._hovering_ls_btn = True
                                self._preview_canvas.setCursor(Qt.PointingHandCursor)
                            return
                        else:
                            if self._hovering_ls_btn:
                                self._hovering_ls_btn = False
                                self._preview_canvas.setCursor(Qt.ArrowCursor)
                except Exception:
                    pass

                # Get mouse position
                try:
                    x = ev.pos().x()
                    y = ev.pos().y()
                    self._qt_last_mouse_xy = (int(x), int(y))
                except Exception:
                    return

                h = self._preview_canvas.height()
                display_x = x
                display_y = h - y

                # Find which axis is under the cursor
                hit_ax = None
                for ax in self._single_axes:
                    try:
                        if ax.bbox.contains(display_x, display_y):
                            hit_ax = ax
                            break
                    except Exception:
                        pass

                if hit_ax is None:
                    self._hide_single_hover_all()
                    return

                # Get x data from cursor position
                try:
                    data_xy = hit_ax.transData.inverted().transform((display_x, display_y))
                    xdata = float(data_xy[0])
                    ydata2 = float(data_xy[1])
                except Exception:
                    return

                # Outside x-limits? hide
                try:
                    x0, x1 = hit_ax.get_xlim()
                    if xdata < min(x0, x1) or xdata > max(x0, x1):
                        self._hide_single_hover_all()
                        return
                except Exception:
                    pass

                # Update all vlines
                try:
                    xa = np.asarray(self._single_axis_state[hit_ax].get("x"), dtype=float)
                    if xa is None or len(xa) < 2:
                        return

                    idx = self._nearest_index_sorted(xa, float(xdata))
                    idx_changed = (self._single_last_idx != idx)
                    self._single_last_idx = idx

                    idx = int(max(0, min(int(idx), int(len(xa) - 1))))

                    # Elapsed header (m:ss or h:mm:ss)
                    try:
                        base_v = float(xa[0])
                        cur_v = float(xa[int(idx)])
                        d = (cur_v - base_v) * 86400.0 if bool(self._single_axis_state[hit_ax].get("is_dt", True)) else (cur_v - base_v)
                        if not np.isfinite(d):
                            d = 0.0
                        d = max(0.0, float(d))
                        total_seconds = int(d)
                        hours = total_seconds // 3600
                        minutes = (total_seconds % 3600) // 60
                        seconds = total_seconds % 60
                        tstr = f"{hours}:{minutes:02d}:{seconds:02d}" if hours > 0 else f"{minutes}:{seconds:02d}"
                    except Exception:
                        tstr = ""

                    # Update vlines on all axes to the same x position
                    for ax in self._single_axes:
                        vline = self._single_axis_vlines.get(ax)
                        if vline is not None:
                            try:
                                vline.set_xdata([xa[int(idx)], xa[int(idx)]])
                                vline.set_visible(True)
                            except Exception:
                                pass

                    # Update + animate tooltips for each axis
                    for ax2 in self._single_axes:
                        st2 = self._single_axis_state.get(ax2)
                        if not st2:
                            continue
                        tt = st2.get("qt_tt")
                        if tt is None:
                            continue

                        # yref behavior:
                        # - hovered axis follows cursor y
                        # - other axes follow the highest line at idx (nanmax across series)
                        try:
                            y0, y1 = ax2.get_ylim()
                            lo, hi = (float(y0), float(y1)) if y0 <= y1 else (float(y1), float(y0))
                        except Exception:
                            lo, hi = (0.0, 1.0)

                        if ax2 is hit_ax:
                            try:
                                yref = float(ydata2)
                            except Exception:
                                yref = lo
                        else:
                            try:
                                df_np2 = st2.get("df_np", None)
                                if df_np2 is not None:
                                    row_vals = np.asarray(df_np2[int(idx), :], dtype=float)
                                    ymax = float(np.nanmax(row_vals))
                                else:
                                    ymax = float("nan")
                            except Exception:
                                ymax = float("nan")

                            if ymax == ymax:
                                yref = ymax
                            else:
                                yref = lo + 0.65 * (hi - lo)

                        try:
                            if hi > lo:
                                pad = 0.03 * (hi - lo)
                                yref = max(lo + pad, min(hi - pad, yref))
                        except Exception:
                            pass

                        if idx_changed:
                            cols3 = st2.get("cols") or []
                            colors3 = st2.get("colors") or []
                            try:
                                df_np3 = st2.get("df_np")
                                vals3 = np.asarray(df_np3[int(idx), :], dtype=float) if df_np3 is not None else np.full((len(cols3),), np.nan, dtype=float)
                            except Exception:
                                vals3 = np.full((len(cols3),), np.nan, dtype=float)

                            try:
                                work3 = np.where(np.isfinite(vals3), vals3, -1e30)
                                order3 = np.argsort(work3)[::-1]
                            except Exception:
                                order3 = np.arange(len(cols3), dtype=int)

                            names_sorted = []
                            values_sorted = []
                            colors_sorted = []
                            for i3 in order3:
                                try:
                                    name = cols3[int(i3)]
                                except Exception:
                                    name = ""
                                try:
                                    v = float(vals3[int(i3)])
                                except Exception:
                                    v = float("nan")
                                try:
                                    col = colors3[int(i3)]
                                except Exception:
                                    col = "#FFFFFF"
                                names_sorted.append(name)
                                values_sorted.append(self._format_value(name, v))
                                colors_sorted.append(col)

                            html = self._qt_build_tooltip_html(tstr, names_sorted, values_sorted, colors_sorted)
                            try:
                                tt.setText(html)
                            except Exception:
                                pass

                        try:
                            tt.show()
                        except Exception:
                            pass

                        pos2 = self._qt_compute_tooltip_pos_in_ax(
                            tt, ax2, xdata=float(xdata), ydata=float(yref), prefer_mode=self._qt_tt_mode
                        )
                        if pos2 is not None:
                            tx2, ty2, mode2 = pos2
                            self._qt_tt_mode = str(mode2)
                            self._qt_move_to(tt, int(tx2), int(ty2))

                    # Blit vlines only (tooltips are Qt overlays)
                    try:
                        self._single_blit_vlines_only()
                    except Exception:
                        try:
                            self._preview_canvas.draw_idle()
                        except Exception:
                            pass

                except Exception:
                    pass

            except Exception:
                pass

        try:
            self._preview_canvas.mouseMoveEvent = _single_multi_mouse_move
        except Exception:
            pass

        # Draw
        try:
            self._preview_canvas.draw_idle()
        except Exception:
            pass

        try:
            QTimer.singleShot(0, self._single_mode_relayout_and_redraw)
        except Exception:
            pass

    def _single_mode_relayout_and_redraw(self) -> None:
        """Relayout multi-axis subplots on resize/show."""
        try:
            if self._preview_canvas is None or self._preview_fig is None:
                return
            if not self._preview_canvas.isVisible():
                return
            if not getattr(self, "_single_mode_multi_axis", False) or not getattr(self, "_single_axes", None):
                return

            self._preview_canvas.draw()
            renderer = self._preview_canvas.get_renderer()
            if renderer is None:
                return

            # Compute left margin
            left_px = float(getattr(self, "_preview_left_margin_px_base", 60) or 60)
            for ax in list(self._single_axes):
                try:
                    self._preview_ax = ax
                    left_px = max(left_px, float(self._preview_required_left_margin_px(renderer, pad_px=8)))
                except Exception:
                    continue

            try:
                fig_w_px = float(self._preview_fig.get_figwidth() * self._preview_fig.dpi)
                left = (left_px / fig_w_px) if fig_w_px > 1 else 0.08
                left = max(0.02, min(left, 0.35))

                self._preview_fig.subplots_adjust(
                    left=left,
                    right=0.985,
                    top=0.93,
                    bottom=0.05,
                    hspace=0.35,
                )
            except Exception:
                pass

            try:
                self._preview_invalidate_interaction_cache()
            except Exception:
                pass

            self._preview_canvas.draw()
            try:
                self._refresh_single_backgrounds()
            except Exception:
                pass
        except Exception:
            try:
                if self._preview_canvas is not None:
                    self._preview_canvas.draw_idle()
            except Exception:
                pass

