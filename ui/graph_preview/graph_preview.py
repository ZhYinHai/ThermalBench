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

        # --- Qt overlay tooltip (single mode)
        self._qt_tt: Optional[QLabel] = None
        self._qt_tt_mode = "UR"
        self._qt_tt_margin_px = 4
        self._qt_last_mouse_xy = None  # (qt_x, qt_y) used for smoother anchoring

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
                " background-color: rgba(0,0,0,71);"
                " border: 1px solid rgba(255,255,255,15);"
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
                # pre spacing
                pad_name = ne + (" " * max(0, name_w - len(n)))
                pad_val = (" " * max(0, val_w - len(v))) + ve
                # keep two columns; color both sides
                lines.append(
                    f"<span style='color:{col}'>{pad_name}</span>"
                    f"  "
                    f"<span style='color:{col}'>{pad_val}</span>"
                )

            body = "\n".join(lines)
            # white-space:pre keeps alignment
            return (
                "<div style=\"white-space:pre;\">"
                f"{body}"
                "</div>"
            )
        except Exception:
            # fallback minimal
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

    def _qt_place_tooltip_in_ax(self, tt: QLabel, ax, *, xdata: float, ydata: float, prefer_mode: str = "UR") -> None:
        """
        Place + clamp the Qt tooltip inside the axis bbox, mimicking the Matplotlib corner-flip behavior.
        """
        try:
            if self._preview_canvas is None or ax is None or tt is None:
                return

            # Need tooltip size
            tt.adjustSize()
            w = int(tt.width())
            h = int(tt.height())

            # Axis bbox in display coords (origin bottom-left), convert to Qt coords (origin top-left)
            bb = ax.bbox  # display pixels
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
                # fallback to mouse position if available
                if self._qt_last_mouse_xy is not None:
                    cx = float(self._qt_last_mouse_xy[0])
                    cy = float(canvas_h - self._qt_last_mouse_xy[1])
                else:
                    cx = 0.5 * (ax_left + ax_right)
                    cy = 0.5 * (canvas_h - (ax_top + ax_bottom))  # unused

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

            # Candidate modes (same as matplotlib logic)
            candidates = ["UR", "DR", "UL", "DL"]
            # start with prefer_mode
            if prefer_mode in candidates:
                candidates = [prefer_mode] + [m for m in candidates if m != prefer_mode]

            best = None
            best_score = None
            best_pos = None

            for m in candidates:
                ox, oy, align = mode_to_offsets(m)

                # map "box_alignment" into Qt top-left
                if align == (0, 0):          # lower-left anchored
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

                # overflow score vs axis bbox (with margin)
                left_over = max(0.0, (ax_left + margin) - x0)
                right_over = max(0.0, (x0 + w) - (ax_right - margin))
                top_over = max(0.0, (ax_top + margin) - y0)
                bot_over = max(0.0, (y0 + h) - (ax_bottom - margin))
                score = left_over + right_over + top_over + bot_over

                if best_score is None or score < best_score:
                    best_score = score
                    best = m
                    best_pos = (x0, y0)

            if best_pos is None:
                return

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

            tt.move(int(round(x0)), int(round(y0)))
            self._qt_tt_mode = str(best)
        except Exception:
            pass

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
                        w.hide()
                except Exception:
                    pass
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
                    # fast numeric matrix; NaN for missing
                    self._preview_df_np = self._preview_df.to_numpy(dtype=float, copy=False)
                except Exception:
                    self._preview_df_np = np.asarray(self._preview_df.to_numpy(), dtype=float)

                try:
                    self._preview_cols_cached = [str(c) for c in list(self._preview_df.columns)]
                except Exception:
                    self._preview_cols_cached = []
                try:
                    self._preview_colors_cached = [str(self._preview_color_map.get(str(c), "#FFFFFF")) for c in self._preview_cols_cached]
                except Exception:
                    self._preview_colors_cached = ["#FFFFFF"] * len(self._preview_cols_cached)

            # precompute elapsed time strings (single mode uses elapsed formatter)
            self._preview_time_strs = None
            try:
                if self._preview_x_np is not None and len(self._preview_x_np) > 0:
                    # matplotlib dates are days
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
        Same behavior, much higher responsiveness:
        - Matplotlib draws ONLY the vline (blitting)
        - Tooltip is a Qt overlay QLabel (no Matplotlib text rendering cost)
        - All per-sensor values are read from a cached numpy matrix
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
                # best-effort rebuild
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

            # Update vline every time (feels responsive)
            try:
                vl = getattr(self, "_preview_vline", None)
                if vl is not None:
                    vl.set_xdata([xdata, xdata])
                    vl.set_visible(True)
            except Exception:
                pass

            # Ensure bg exists (keep original)
            try:
                if getattr(self, "_preview_bg", None) is None and self._preview_canvas is not None:
                    self._preview_canvas.draw()
                    self._on_preview_draw()
            except Exception:
                pass

            # Tooltip overlay
            tt = self._ensure_qt_tooltip()
            if tt is None:
                # fallback: no tooltip overlay, still show vline
                self._preview_blit()
                return

            # Content updates only when idx changes (key for smoothness)
            if self._preview_last_tt_idx != idx:
                self._preview_last_tt_idx = idx

                # header time string (elapsed, like original single-mode tooltip)
                try:
                    if self._preview_time_strs is not None and 0 <= idx < len(self._preview_time_strs):
                        tstr = self._preview_time_strs[idx]
                    else:
                        # fallback (should be rare)
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

                # row values from numpy (fast)
                try:
                    vals = np.asarray(self._preview_df_np[idx, :], dtype=float)
                except Exception:
                    vals = None

                cols = list(self._preview_cols_cached or [])
                colors = list(self._preview_colors_cached or [])

                if vals is None or len(cols) != int(getattr(vals, "size", 0)):
                    # defensive fallback
                    try:
                        vals = np.asarray(self._preview_df.iloc[idx].to_numpy(dtype=float, na_value=np.nan), dtype=float)
                    except Exception:
                        vals = np.full((len(cols),), np.nan, dtype=float)

                ncols = int(len(cols))
                if ncols != int(vals.size):
                    # align defensively
                    try:
                        vals = np.resize(vals, ncols).astype(float, copy=False)
                    except Exception:
                        pass

                # sort descending, NaNs last (same behavior)
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

            # Show + position tooltip every time (cheap)
            try:
                tt.show()
            except Exception:
                pass

            # Anchor y follows cursor y (like original); if ydata missing, use mid
            try:
                if ydata is None:
                    y0, y1 = self._preview_ax.get_ylim()
                    ty = 0.5 * (float(y0) + float(y1))
                else:
                    ty = float(ydata)
            except Exception:
                ty = 0.0

            # Place tooltip within the axes bbox
            self._qt_place_tooltip_in_ax(tt, self._preview_ax, xdata=float(xdata), ydata=float(ty), prefer_mode=self._qt_tt_mode)

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
                    self._preview_set_active_cols(active)
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
                self._preview_set_active_cols(active)
            except Exception:
                pass

        top = self.parent.window() if hasattr(self.parent, "window") else self.parent

        stats_map = self._preview_get_stats_map()
        title = self._preview_infer_stats_title()

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
            if self._preview_ax is None or self._preview_canvas is None or self._preview_df_all is None:
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

            # rebuild numpy hover caches so responsiveness stays constant with many sensors
            self._rebuild_hover_cache()
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
        if self._preview_canvas is None or self._preview_fig is None:
            raise RuntimeError("Preview canvas unavailable")

        self._close_legend_popup()
        self._exit_compare_mode()
        self._hide_qt_tooltip()

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
        for rel in runs_rel:
            try:
                p = Path(*str(rel).replace("\\", "/").split("/"))
            except Exception:
                p = Path(str(rel))
            rd = (runs_root / p)
            run_dirs.append(rd)
            try:
                run_labels.append(rd.name)
            except Exception:
                run_labels.append(str(rel))

        # Load and keep only requested sensors
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
                # ensure all requested sensors exist (missing -> NaN)
                for s in sensors:
                    if s not in df_keep.columns:
                        df_keep[s] = np.nan
                df_keep = df_keep[sensors]
                run_dfs.append(df_keep)
            except Exception:
                run_dfs.append(pd.DataFrame())

        # Trim to shortest elapsed duration across runs
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

        common_elapsed = np.linspace(0.0, max(0.0, min_dur_sec), num=min_len)
        base = pd.Timestamp("2000-01-01")
        common_index = base + pd.to_timedelta(common_elapsed, unit="s")

        # Precompute each run's elapsed seconds axis
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

        # Build subplots: one per sensor
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

        # Global compare palette: ensure colors don't repeat per subplot.
        n_runs = len(run_labels)
        try:
            cmaps = [cm.get_cmap("tab20"), cm.get_cmap("tab20b"), cm.get_cmap("tab20c")]
            palette = []
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

        # Create per-axis Qt tooltip widgets (compare mode shows one tooltip per subplot)
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
                w.setStyleSheet(
                    "QLabel {"
                    " background-color: rgba(0,0,0,71);"
                    " border: 1px solid rgba(255,255,255,15);"
                    " border-radius: 8px;"
                    " padding: 8px 10px;"
                    " color: #FFFFFF;"
                    "}"
                )
                w.hide()
                return w
            except Exception:
                return None

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

            # build df for this sensor: columns are run labels
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

            # Unique colors across ALL subplots (sensor_index * n_runs + run_index)
            color_map: dict[str, str] = {}
            for j, lbl in enumerate(list(df_sensor.columns)):
                try:
                    color_map[str(lbl)] = palette[(i * max(1, n_runs) + j) % len(palette)]
                except Exception:
                    color_map[str(lbl)] = "#FFFFFF"

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

            # sensor label (small, top-left)
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

            # only bottom axis gets elapsed formatter
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
                "df": df_sensor,
                "df_np": df_np,
                "cols": cols,
                "colors": cols_colors,
                "vline": vline,
                "bg": None,
                "qt_tt": _make_compare_tt(),
            }

        try:
            self._preview_label.clear()
            self._preview_label.hide()
        except Exception:
            pass

        try:
            self._preview_canvas.show()
        except Exception:
            pass

        # Prepare backgrounds for blitting
        self._refresh_compare_backgrounds()

        try:
            self._preview_last_canvas_wh = (int(self._preview_canvas.width()), int(self._preview_canvas.height()))
        except Exception:
            pass

        def _compare_mouse_move(ev):
            try:
                if self._preview_canvas is None or not self._compare_mode:
                    return

                # refresh cached backgrounds on resize
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

                # find which axis is under cursor
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

                # cursor -> data coords on the hovered axis
                try:
                    data_xy = hit_ax.transData.inverted().transform((display_x, display_y))
                    xdata, ydata = float(data_xy[0]), float(data_xy[1])
                except Exception:
                    return

                # outside x-limits? hide everything
                try:
                    x0, x1 = hit_ax.get_xlim()
                    if xdata < min(x0, x1) or xdata > max(x0, x1):
                        self._hide_compare_hover_all()
                        return
                except Exception:
                    pass

                # sync vertical vline across ALL subplots at the same x
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

                # nearest index in x (sorted)
                try:
                    xa = st_hit.get("x")
                    if xa is None or len(xa) < 2:
                        return
                    idx = self._nearest_index_sorted(np.asarray(xa, dtype=float), float(xdata))
                except Exception:
                    return
                idx = int(max(0, min(int(idx), int(len(st_hit["x"]) - 1))))

                # Only rebuild tooltip text when idx changes (huge performance win)
                idx_changed = (self._compare_last_idx != idx)
                self._compare_last_idx = idx

                # timestamp header in compare mode is the full datetime index (like your screenshot)
                try:
                    df_idx = st_hit.get("df").index
                    tstr = str(df_idx[int(idx)])
                except Exception:
                    tstr = ""

                # Update + place tooltips for each axis (Qt overlay; cheap)
                for ax2 in self._compare_axes:
                    st2 = self._compare_axis_state.get(ax2)
                    if not st2:
                        continue
                    tt = st2.get("qt_tt")
                    if tt is None:
                        continue

                    # Compute yref per your original behavior
                    try:
                        y0, y1 = ax2.get_ylim()
                        lo, hi = (float(y0), float(y1)) if y0 <= y1 else (float(y1), float(y0))
                    except Exception:
                        lo, hi = (0.0, 1.0)

                    try:
                        if ax2 is hit_ax:
                            yref = float(ydata)
                        else:
                            yref = lo + 0.65 * (hi - lo)
                    except Exception:
                        yref = lo

                    # clamp inside axis
                    try:
                        if hi > lo:
                            pad = 0.03 * (hi - lo)
                            yref = max(lo + pad, min(hi - pad, yref))
                    except Exception:
                        pass

                    if idx_changed:
                        # build text for this axis at idx
                        cols = st2.get("cols") or []
                        colors = st2.get("colors") or []
                        try:
                            vals = np.asarray(st2.get("df_np")[idx, :], dtype=float)
                        except Exception:
                            vals = np.full((len(cols),), np.nan, dtype=float)

                        # sort desc; NaNs last
                        try:
                            work = np.where(np.isfinite(vals), vals, -1e30)
                            order = np.argsort(work)[::-1]
                        except Exception:
                            order = np.arange(len(cols), dtype=int)

                        names_sorted = []
                        values_sorted = []
                        colors_sorted = []
                        for i2 in order:
                            try:
                                name = cols[int(i2)]
                            except Exception:
                                name = ""
                            try:
                                v = float(vals[int(i2)])
                            except Exception:
                                v = float("nan")
                            try:
                                col = colors[int(i2)]
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

                    # place & clamp inside that axis
                    self._qt_place_tooltip_in_ax(tt, ax2, xdata=float(xdata), ydata=float(yref), prefer_mode=self._qt_tt_mode)

                # Blit vlines only (tooltips are Qt)
                self._compare_blit_vlines_only()

            except Exception:
                pass

        try:
            self._preview_canvas.mouseMoveEvent = _compare_mouse_move
        except Exception:
            pass

        # disable legend&stats button in compare mode
        self._ls_btn_text = None
        self._ls_btn_bbox = None

        try:
            self._preview_canvas.draw_idle()
        except Exception:
            pass

        # Defer a compare-aware relayout until the widget has its real size.
        try:
            QTimer.singleShot(0, self._compare_relayout_and_redraw)
        except Exception:
            pass

    def _plot_run_csv(self, fpath: str) -> None:
        if self._preview_canvas is None or self._preview_ax is None:
            raise RuntimeError("Preview canvas unavailable")

        self._exit_compare_mode()

        self._close_legend_popup()
        self._preview_csv_path = fpath

        df_data, cols = load_run_csv_dataframe(fpath)

        self._preview_df_all = df_data[cols]
        self._preview_colors = [
            self._preview_color_map.get(c, "#FFFFFF") for c in self._preview_active_cols
        ]
        self._preview_available_cols = list(cols)

        # apply last saved selection for THIS result (if any)
        self._apply_saved_or_default_selection()

        is_dt, x_vals = compute_x_vals(df_data)
        self._preview_is_dt = bool(is_dt)

        try:
            self._tt_anim_timer.stop()
        except Exception:
            pass
        self._tt_anim_start_xy = None
        self._tt_anim_target_xy = None

        self._preview_ax.clear()
        self._ls_btn_text = None
        self._ls_btn_bbox = None

        apply_dark_axes_style(
            self._preview_fig,
            self._preview_ax,
            grid_color=self._preview_grid_color,
            dot_dashes=self._preview_dot_dashes,
        )

        self._preview_color_map = build_tab20_color_map(list(cols))

        self._preview_lines, self._preview_series_data, self._preview_colors = plot_lines_with_glow(
            self._preview_ax,
            df_all=self._preview_df_all,
            cols=list(cols),
            x_vals=x_vals,
            is_dt=is_dt,
            color_map=self._preview_color_map,
        )

        # Hide lines that aren't active (based on saved selection)
        aset = set(self._preview_active_cols)
        for name, ln in list(self._preview_lines.items()):
            try:
                ln.set_visible(name in aset)
            except Exception:
                pass

        self._preview_x = x_vals

        # Step 1: make sure hover has a dataframe immediately (before any toggles)
        try:
            self._preview_df = self._preview_df_all[self._preview_active_cols]
        except Exception:
            self._preview_df = self._preview_df_all

        # Keep original tooltip builder (safe), but hover uses Qt overlay
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

            # force initial draw so blit bg exists
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
