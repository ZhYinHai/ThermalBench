# graph_preview.py
"""Graph preview component for displaying CSV sensor data with interactive tooltips + Legend&Stats popup button."""

from pathlib import Path
import io
import json
import html
import re
import time
from typing import Optional

import numpy as np
import pandas as pd

from PySide6.QtCore import QTimer, Qt, QEvent, QObject, QEasingCurve, QPropertyAnimation
from PySide6.QtGui import QImage, QPixmap, QFont
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QMenu,
    QSizePolicy,
    QDialog,
    QPushButton,
    QToolTip,
    QVBoxLayout,
    QWidget,
    QGraphicsOpacityEffect,
)

from matplotlib.figure import Figure
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.backend_bases import MouseEvent as MPLMouseEvent
from matplotlib.lines import Line2D
import matplotlib.cm as cm
import matplotlib.colors as mcolors
import matplotlib.dates as mdates

from .graph_plot_helpers import (
    apply_dark_axes_style,
    apply_light_axes_style,
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
from ..widgets.ui_theme import resolve_effective_theme_mode

from .ui_dim_overlay import DimOverlay
from .ui_legend_stats_popup import LegendStatsPopup
from .ui_compare_legend_stats_popup import CompareLegendStatsPopup
from .graph_stats_helpers import stats_from_summary_csv, stats_from_dataframe, infer_stats_title
from .result_selection_store import (
    apply_saved_or_default_active_cols,
    get_selection_json_path,
    load_active_cols,
    save_active_cols,
)
from .preview_path_helpers import choose_preview_file_for_folder, is_csv_file, is_image_file
from .legend_stats_button_helpers import is_over_ls_button, is_over_button_bbox
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
        self._compare_legend_popup: Optional[CompareLegendStatsPopup] = None
        self._dim_overlay: Optional[DimOverlay] = None
        self._preview_scroll_area = None
        self._preview_scroll_viewport = None
        self._preview_visible_axis_slots = 2
        self._preview_min_axis_height = 220
        self._preview_header_widget = None
        self._preview_header_separator = None
        self._preview_header_title_label = None
        self._preview_header_subtitle_label = None
        self._preview_header_zero_btn = None
        self._preview_header_delta_btn = None
        self._preview_header_legend_btn = None
        self._preview_header_copy_btn = None
        self._preview_header_preshow = False
        self._preview_header_title_text = ""
        self._preview_header_subtitle_text = ""
        self._preview_header_title_elided = False
        self._preview_header_subtitle_elided = False
        self._preview_header_tt: Optional[QLabel] = None
        self._preview_header_tt_source: Optional[QLabel] = None

        # data/series state
        self._preview_df_all = None
        self._preview_available_cols: list[str] = []
        self._preview_active_cols: list[str] = []
        self._preview_lines = {}       # col -> Line2D
        self._preview_series_data = {} # col -> np.ndarray
        self._preview_color_map = {}   # col -> color hex
        self._preview_csv_path: str | None = None
        self._theme_mode: str = "dark"
        self._theme_is_dark: bool = True
        self._preview_theme: dict[str, object] = {}

        # Legend&Stats button drawn inside the axes
        self._ls_btn_text = None
        self._ls_btn_bbox = None
        self._hovering_ls_btn = False

        # Temperature delta toggle button (left of Legend&Stats)
        self._delta_btn_text = None
        self._delta_btn_bbox = None
        self._temp_delta_mode = False
        self._delta_toggle_enabled = False

        # Zero-based Y toggle button (left of ΔT)
        self._zero_btn_text = None
        self._zero_btn_bbox = None
        self._zero_y_mode = False

        # Debounced replot scheduling (keeps UI responsive on toggles)
        self._replot_timer: Optional[QTimer] = None
        self._replot_in_progress: bool = False

        # Keep a raw copy so we can toggle delta/absolute without losing baseline.
        self._preview_df_all_raw: Optional[pd.DataFrame] = None
        self._preview_df_all_delta: Optional[pd.DataFrame] = None

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

        # --- Graph transition animation (used by ΔT and 0Y/AutoY)
        self._graph_anim_overlay: Optional[QLabel] = None
        self._graph_anim_effect: Optional[QGraphicsOpacityEffect] = None
        self._graph_anim_fade: Optional[QPropertyAnimation] = None
        self._graph_anim_duration_ms = 350
        self._pending_replot_transition_pixmap: Optional[QPixmap] = None

        # --- Plot morph animation (line/y-range tween for toggle changes)
        self._plot_morph_timer = QTimer(self.parent)
        try:
            self._plot_morph_timer.setTimerType(Qt.PreciseTimer)
        except Exception:
            pass
        self._plot_morph_timer.setInterval(16)  # ~60 fps
        self._plot_morph_timer.timeout.connect(self._plot_morph_tick)

        self._plot_morph_duration_s = 0.10
        self._plot_morph_t0 = 0.0
        self._plot_morph_axes: list[dict] = []
        self._plot_morph_finish = None

        # Morph performance budget
        self._plot_morph_mode = "blit"   # "blit" or "redraw"
        self._plot_morph_max_points_per_line = 320
        self._plot_morph_max_total_points = 2400
        self._plot_morph_disable_glow = True
        self._plot_morph_reduce_antialias = True
        self._plot_morph_lock_interaction = False

        # Blit backgrounds for morph animation
        self._plot_morph_bgs: dict[object, object] = {}

        # Lower default timer pressure
        self._plot_morph_timer.setInterval(33)  # ~30 fps, steadier under heavy load

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
            self._preview_left_margin_px_base = 56
            self._preview_left_tick_pad_px = 3
            self._preview_right_frac = 0.995
            self._preview_right_pad_px = 18
            self._preview_top_frac = 0.93
            self._preview_bottom_frac = 0.05
            self._preview_top_pad_px = 30
            self._preview_bottom_pad_px = 16
            self._preview_stack_hspace = 0.20
            self._preview_title_axes_y = 1.02
            self._preview_title_font_size = 11

            self._preview_canvas = FigureCanvas(self._preview_fig)
            self._preview_canvas.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
            self._preview_canvas.setMouseTracking(True)
            self._preview_ax = self._preview_fig.add_subplot(111)

            self._preview_apply_axes_rect(
                right_frac=float(self._preview_effective_right_frac()),
                left_margin_px=self._preview_left_margin_px_base,
            )

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
            self._update_preview_theme_palette()

            def _qc(ev):
                try:
                    if not getattr(self, "_app_is_active", True):
                        return

                    try:
                        if (
                            self._is_over_zero_y_button(ev.pos().x(), ev.pos().y())
                            or (self._delta_toggle_is_enabled() and self._is_over_delta_button(ev.pos().x(), ev.pos().y()))
                            or self._is_over_ls_button(ev.pos().x(), ev.pos().y())
                        ):
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
                        if self._handle_zero_y_click(ev.pos().x(), ev.pos().y()):
                            return
                        if self._handle_delta_click(ev.pos().x(), ev.pos().y()):
                            return
                        if self._handle_ls_click(ev.pos().x(), ev.pos().y()):
                            return
                except Exception:
                    pass
                return _orig_press(ev)

            self._preview_canvas.mousePressEvent = _press

            self._default_mouse_press_event = self._preview_canvas.mousePressEvent

            self._preview_canvas.hide()

            self._preview_timeline_fig = Figure(figsize=(5, 0.45))
            self._preview_timeline_canvas = FigureCanvas(self._preview_timeline_fig)
            self._preview_timeline_canvas.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            self._preview_timeline_canvas.setFixedHeight(64)
            self._preview_timeline_ax = self._preview_timeline_fig.add_subplot(111)
            self._preview_timeline_canvas.hide()

        except Exception:
            self._preview_fig = None
            self._preview_canvas = None
            self._preview_ax = None
            self._preview_timeline_fig = None
            self._preview_timeline_canvas = None
            self._preview_timeline_ax = None

        # compare-mode state
        self._compare_mode = False
        self._compare_axes = []
        self._compare_axis_state = {}
        self._compare_last_canvas_wh = None
        self._compare_last_idx = None
        self._compare_run_dirs: list[Path] = []
        self._compare_run_labels: list[str] = []
        self._compare_run_color_map: dict[str, str] = {}
        self._compare_manifest_sensors: list[str] = []
        self._compare_manifest_path: Optional[Path] = None

        # single-mode multi-axis state (for splitting by measurement type)
        self._single_mode_multi_axis = False
        self._single_axes = []
        self._single_axis_state = {}
        self._single_axis_vlines = {}
        self._single_last_canvas_wh = None
        self._single_last_idx = None
        self._single_header_text = None

    def set_theme_mode(self, mode: str) -> None:
        try:
            self._theme_mode = str(mode or "dark").strip().lower() or "dark"
            self._update_preview_theme_palette()
            self._refresh_current_preview_theme()
        except Exception:
            pass

    def _update_preview_theme_palette(self) -> None:
        try:
            effective_mode = resolve_effective_theme_mode(getattr(self, "_theme_mode", "dark"))
            self._theme_is_dark = (effective_mode != "light")

            if self._theme_is_dark:
                self._preview_theme = {
                    "figure_bg": "#121212",
                    "grid": "#3A3A3A",
                    "label": "#EAEAEA",
                    "secondary_text": "#BDBDBD",
                    "tooltip_bg": "rgba(24,24,24,160)",
                    "tooltip_border": "rgba(255,255,255,18)",
                    "tooltip_text": "#FFFFFF",
                    "button_active_fill": (0.25, 0.25, 0.25, 0.35),
                    "button_active_border": (0.55, 0.55, 0.55, 0.85),
                    "button_active_text": (1.0, 1.0, 1.0, 0.98),
                }
            else:
                self._preview_theme = {
                    "figure_bg": "#FFFFFF",
                    "grid": "#DADADA",
                    "label": "#1A1A1A",
                    "secondary_text": "#5A5A5A",
                    "tooltip_bg": "rgba(255,255,255,235)",
                    "tooltip_border": "rgba(0,0,0,35)",
                    "tooltip_text": "#1A1A1A",
                    "button_active_fill": (0.83, 0.83, 0.83, 0.75),
                    "button_active_border": (0.58, 0.58, 0.58, 0.95),
                    "button_active_text": (0.05, 0.05, 0.05, 0.98),
                }

            self._preview_grid_color = str(self._preview_theme.get("grid", "#3A3A3A"))
            self._preview_dot_dashes = (0, (1.2, 3.2))

            try:
                if getattr(self, "_preview_fig", None) is not None:
                    self._preview_fig.set_facecolor(str(self._preview_theme.get("figure_bg", "#121212")))
            except Exception:
                pass

            try:
                self._apply_preview_surface_theme()
            except Exception:
                pass
        except Exception:
            pass

    def _preview_tooltip_stylesheet(self) -> str:
        theme = dict(getattr(self, "_preview_theme", {}) or {})
        bg = str(theme.get("tooltip_bg", "rgba(24,24,24,160)"))
        border = str(theme.get("tooltip_border", "rgba(255,255,255,18)"))
        text = str(theme.get("tooltip_text", "#FFFFFF"))
        return (
            "QLabel {"
            f" background-color: {bg};"
            f" border: 1px solid {border};"
            " border-radius: 8px;"
            " padding: 8px 10px;"
            f" color: {text};"
            "}"
        )

    def _preview_header_tooltip_stylesheet(self) -> str:
        theme = dict(getattr(self, "_preview_theme", {}) or {})
        bg = str(theme.get("tooltip_bg", "rgba(24,24,24,160)"))
        border = str(theme.get("tooltip_border", "rgba(255,255,255,18)"))
        text = str(theme.get("tooltip_text", "#FFFFFF"))
        return (
            "QLabel {"
            f" background-color: {bg};"
            f" border: 1px solid {border};"
            " border-radius: 6px;"
            " padding: 3px 10px;"
            f" color: {text};"
            "}"
        )

    @staticmethod
    def _html_escape(text: str) -> str:
        try:
            return html.escape(str(text or ""), quote=True)
        except Exception:
            return str(text or "")

    def _preview_header_compare_colors(self) -> list[str]:
        try:
            cmap = dict(getattr(self, "_compare_run_color_map", None) or {})
            return [str(v) for v in cmap.values() if str(v or "").strip()]
        except Exception:
            return []

    def _preview_header_compare_subtitle_html(self, text: str, *, avail: int | None = None) -> tuple[str, bool]:
        try:
            raw = str(text or "")
            parts = [str(p) for p in raw.split(" vs ")]
            colors = self._preview_header_compare_colors()
            base = self._preview_secondary_text_color()
            if len(parts) < 2 or len(colors) < 2:
                return (f"<div style='white-space:pre-wrap;color:{base};'>{self._html_escape(raw)}</div>", False)

            fm = None
            try:
                lab = getattr(self, "_preview_header_subtitle_label", None)
                if lab is not None:
                    fm = lab.fontMetrics()
            except Exception:
                fm = None

            chunks: list[str] = []
            overflow = False
            remaining = int(avail) if avail is not None else -1
            sep = " vs "
            sep_w = int(fm.horizontalAdvance(sep)) if (fm is not None and remaining >= 0) else 0

            for idx, part in enumerate(parts):
                if idx > 0:
                    if remaining >= 0:
                        if remaining <= 0:
                            overflow = True
                            break
                        if sep_w > remaining:
                            overflow = True
                            break
                        remaining -= sep_w
                    chunks.append(f"<span style='color:{base};'>{self._html_escape(sep)}</span>")

                color = colors[idx] if idx < len(colors) else base
                part_out = part
                if remaining >= 0 and fm is not None:
                    part_w = int(fm.horizontalAdvance(part))
                    if part_w > remaining:
                        overflow = True
                        part_out = str(fm.elidedText(part, Qt.ElideRight, max(0, remaining)))
                        remaining = 0
                        if part_out:
                            chunks.append(f"<span style='color:{color};'>{self._html_escape(part_out)}</span>")
                        break
                    remaining -= part_w

                chunks.append(f"<span style='color:{color};'>{self._html_escape(part_out)}</span>")

            if not chunks:
                return (f"<div style='white-space:pre-wrap;color:{base};'>{self._html_escape(raw)}</div>", False)
            return ("<div style='white-space:pre-wrap;'>" + "".join(chunks) + "</div>", overflow)
        except Exception:
            base = self._preview_secondary_text_color()
            return (f"<div style='white-space:pre-wrap;color:{base};'>{self._html_escape(text)}</div>", False)

    def _preview_header_tooltip_payload(self, label: Optional[QLabel]) -> tuple[str, str]:
        try:
            if label is None or not label.isVisible():
                return ("", "")

            if label is getattr(self, "_preview_header_title_label", None):
                if not bool(getattr(self, "_preview_header_title_elided", False)):
                    return ("", "")
                raw = str(getattr(self, "_preview_header_title_text", "") or "")
                text_color = self._preview_label_color()
                return (raw, f"<div style='white-space:pre-wrap;color:{text_color};'>{self._html_escape(raw)}</div>")

            if label is getattr(self, "_preview_header_subtitle_label", None):
                if not bool(getattr(self, "_preview_header_subtitle_elided", False)):
                    return ("", "")
                raw = str(getattr(self, "_preview_header_subtitle_text", "") or "")
                html_text, _ = self._preview_header_compare_subtitle_html(raw, avail=None)
                return (raw, html_text)
        except Exception:
            pass
        return ("", "")

    def _preview_header_tooltip_text(self, label: Optional[QLabel]) -> str:
        try:
            return self._preview_header_tooltip_payload(label)[0]
        except Exception:
            return ""

    def _ensure_preview_header_tooltip(self) -> Optional[QLabel]:
        try:
            anchor = (
                getattr(self, "_preview_header_widget", None)
                or getattr(self, "_preview_header_title_label", None)
                or getattr(self, "_preview_header_subtitle_label", None)
            )
            if anchor is None:
                return None

            host = anchor.window()
            if host is None:
                return None

            tt = getattr(self, "_preview_header_tt", None)
            if tt is not None:
                try:
                    if tt.parentWidget() is host:
                        return tt
                except Exception:
                    pass
                try:
                    tt.hide()
                    tt.deleteLater()
                except Exception:
                    pass
                self._preview_header_tt = None

            tt = QLabel(host)
            tt.setObjectName("PreviewHeaderTooltip")
            tt.setAttribute(Qt.WA_TransparentForMouseEvents, True)
            tt.setTextFormat(Qt.RichText)
            tt.setWordWrap(True)
            tt.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
            tt.setStyleSheet(self._preview_header_tooltip_stylesheet())
            tt.hide()
            self._preview_header_tt = tt
            return tt
        except Exception:
            return None

    def _hide_preview_header_tooltip(self) -> None:
        try:
            if self._preview_header_tt is not None:
                self._preview_header_tt.hide()
        except Exception:
            pass
        self._preview_header_tt_source = None

    def _show_preview_header_tooltip(
        self,
        label: Optional[QLabel],
        *,
        global_pos=None,
    ) -> None:
        try:
            text, rich_html = self._preview_header_tooltip_payload(label)
            if not text:
                self._hide_preview_header_tooltip()
                return

            tt = self._ensure_preview_header_tooltip()
            if tt is None or label is None:
                return

            host = tt.parentWidget()
            if host is None:
                return

            tt.setStyleSheet(self._preview_header_tooltip_stylesheet())
            try:
                tt.setFont(label.font())
            except Exception:
                pass
            markup = str(rich_html or "").strip()
            if not markup:
                markup = f"<div style='white-space:pre-wrap;'>{self._html_escape(text)}</div>"
            tt.setText(markup)

            host_w = max(0, int(host.width() or 0))
            host_h = max(0, int(host.height() or 0))
            margin = 8
            max_w = max(160, host_w - (margin * 2))

            tt.setMaximumWidth(max_w)
            tt.setWordWrap(False)
            tt.adjustSize()
            natural_w = int(tt.sizeHint().width() or tt.width() or 0)

            if natural_w > max_w:
                tt.setWordWrap(True)
                tt.resize(max_w, 1)
                tt.adjustSize()
            else:
                tt.setWordWrap(False)
                tt.adjustSize()

            label_top_left = label.mapTo(host, label.rect().topLeft())
            label_bottom_left = label.mapTo(host, label.rect().bottomLeft())
            x = int(label_top_left.x())
            y = int(label_bottom_left.y()) + 6

            if global_pos is not None:
                try:
                    anchor = host.mapFromGlobal(global_pos)
                    x = int(anchor.x()) - 18
                except Exception:
                    pass

            max_x = max(margin, host_w - margin - tt.width())
            x = max(margin, min(x, max_x))

            max_y = max(margin, host_h - margin - tt.height())
            if y > max_y:
                above_y = int(label_top_left.y()) - tt.height() - 6
                if above_y >= margin:
                    y = above_y
                else:
                    y = max_y
            y = max(margin, min(y, max_y))

            tt.move(x, y)
            tt.raise_()
            tt.show()
            self._preview_header_tt_source = label
        except Exception:
            self._hide_preview_header_tooltip()

    def _refresh_preview_header_tooltip(self) -> None:
        label = getattr(self, "_preview_header_tt_source", None)
        if label is None:
            return
        self._show_preview_header_tooltip(label)

    def _preview_label_color(self) -> str:
        try:
            return str(self._preview_theme.get("label", "#EAEAEA"))
        except Exception:
            return "#EAEAEA"

    def _preview_secondary_text_color(self) -> str:
        try:
            return str(self._preview_theme.get("secondary_text", "#BDBDBD"))
        except Exception:
            return "#BDBDBD"

    def _preview_css_rgba(self, value, fallback: str) -> str:
        try:
            if isinstance(value, str):
                return value
            if isinstance(value, tuple) and len(value) in (3, 4):
                parts = [float(v) for v in value]
                if len(parts) == 3:
                    parts.append(1.0)
                r = max(0, min(255, int(round(parts[0] * 255))))
                g = max(0, min(255, int(round(parts[1] * 255))))
                b = max(0, min(255, int(round(parts[2] * 255))))
                a = max(0.0, min(1.0, float(parts[3])))
                return f"rgba({r}, {g}, {b}, {a:.3f})"
        except Exception:
            pass
        return fallback

    def _preview_header_button_stylesheet(self, *, active: bool, enabled: bool = True) -> str:
        if not enabled:
            dark = bool(getattr(self, "_theme_is_dark", True))
            bg_color = "rgba(56, 56, 56, 0.16)" if dark else "rgba(235, 235, 235, 0.70)"
            border_color = "rgba(150, 150, 150, 0.28)" if dark else "rgba(170, 170, 170, 0.60)"
            text_color = "rgba(230, 230, 230, 0.42)" if dark else "rgba(90, 90, 90, 0.55)"
            return (
                "QPushButton {"
                f" background: {bg_color};"
                f" color: {text_color};"
                f" border: 1px solid {border_color};"
                " border-radius: 10px;"
                " padding: 4px 10px;"
                " font-weight: 500;"
                " }"
                "QPushButton:hover {"
                f" border-color: {border_color};"
                " }"
                "QPushButton:pressed {"
                f" background: {bg_color};"
                " }"
            )

        text_color = self._preview_css_rgba(self._preview_theme.get("secondary_text", "#BDBDBD"), "#BDBDBD")
        border_color = self._preview_css_rgba(self._preview_theme.get("grid", "#3A3A3A"), "#3A3A3A")
        bg_color = "transparent"
        font_weight = "500"
        if active:
            bg_color = self._preview_css_rgba(
                self._preview_theme.get("button_active_fill", (0.25, 0.25, 0.25, 0.35)),
                "rgba(64, 64, 64, 0.35)"
            )
            border_color = self._preview_css_rgba(
                self._preview_theme.get("button_active_border", (0.55, 0.55, 0.55, 0.85)),
                border_color
            )
            text_color = self._preview_css_rgba(
                self._preview_theme.get("button_active_text", (1, 1, 1, 0.98)),
                text_color
            )
            font_weight = "700"

        return (
            "QPushButton {"
            f" background: {bg_color};"
            f" color: {text_color};"
            f" border: 1px solid {border_color};"
            " border-radius: 10px;"
            " padding: 4px 10px;"
            f" font-weight: {font_weight};"
            " }"
            "QPushButton:hover {"
            f" border-color: {text_color};"
            " }"
            "QPushButton:pressed {"
            f" background: {bg_color};"
            " }"
        )

    def _sync_preview_header_controls(self) -> None:
        try:
            header = getattr(self, "_preview_header_widget", None)
            if header is None:
                return

            try:
                canvas_visible = bool(self._preview_canvas is not None and self._preview_canvas.isVisible())
            except Exception:
                canvas_visible = False
            if canvas_visible:
                self._preview_header_preshow = False
            show_header = canvas_visible or self._preview_header_preshow

            if not show_header:
                self._hide_preview_header_tooltip()

            try:
                header.setVisible(show_header)
            except Exception:
                pass

            bg = str(self._preview_theme.get("figure_bg", "#121212"))
            line = self._preview_css_rgba(self._preview_theme.get("grid", "#3A3A3A"), "#3A3A3A")

            try:
                header.setStyleSheet(f"background: {bg}; border: none;")
            except Exception:
                pass

            title_label = getattr(self, "_preview_header_title_label", None)
            if title_label is not None:
                try:
                    raw_title = str(getattr(self, "_preview_header_title_text", "") or "")
                    try:
                        avail = max(0, int(title_label.contentsRect().width() or title_label.width() or 0) - 2)
                    except Exception:
                        avail = max(0, int(title_label.width() or 0) - 2)
                    text_title = raw_title
                    if avail > 0 and raw_title:
                        try:
                            text_title = title_label.fontMetrics().elidedText(raw_title, Qt.ElideRight, avail)
                        except Exception:
                            text_title = raw_title
                    self._preview_header_title_elided = bool(raw_title and text_title != raw_title)
                    title_label.setTextFormat(Qt.PlainText)
                    title_label.setText(text_title)
                    title_label.setToolTip(raw_title if text_title != raw_title else "")
                    title_label.setVisible(bool(show_header and raw_title.strip()))
                    title_label.setStyleSheet(
                        f"background: transparent; color: {self._preview_label_color()}; font-size: 12px; font-weight: 600;"
                    )
                except Exception:
                    pass

            subtitle_label = getattr(self, "_preview_header_subtitle_label", None)
            if subtitle_label is not None:
                try:
                    raw_subtitle = str(getattr(self, "_preview_header_subtitle_text", "") or "")
                    try:
                        avail = max(0, int(subtitle_label.contentsRect().width() or subtitle_label.width() or 0) - 2)
                    except Exception:
                        avail = max(0, int(subtitle_label.width() or 0) - 2)
                    text_subtitle = raw_subtitle
                    subtitle_html = ""
                    subtitle_elided = False
                    if avail > 0 and raw_subtitle:
                        try:
                            subtitle_html, subtitle_elided = self._preview_header_compare_subtitle_html(raw_subtitle, avail=avail)
                            text_subtitle = subtitle_label.fontMetrics().elidedText(raw_subtitle, Qt.ElideRight, avail)
                        except Exception:
                            text_subtitle = raw_subtitle
                            subtitle_html = ""
                            subtitle_elided = False
                    self._preview_header_subtitle_elided = bool(raw_subtitle and subtitle_elided)
                    if subtitle_html and (" vs " in raw_subtitle):
                        subtitle_label.setTextFormat(Qt.RichText)
                        subtitle_label.setText(subtitle_html)
                    else:
                        subtitle_label.setTextFormat(Qt.PlainText)
                        subtitle_label.setText(text_subtitle)
                    subtitle_label.setToolTip(raw_subtitle if self._preview_header_subtitle_elided else "")
                    subtitle_label.setVisible(bool(show_header and raw_subtitle.strip()))
                    subtitle_label.setStyleSheet(
                        f"background: transparent; color: {self._preview_secondary_text_color()}; font-size: 10px; font-weight: 400;"
                    )
                except Exception:
                    pass

            try:
                sep = getattr(self, "_preview_header_separator", None)
                if sep is not None:
                    sep.setVisible(show_header)
                    sep.setStyleSheet(f"background: {line}; border: none;")
            except Exception:
                pass

            legend_btn = getattr(self, "_preview_header_legend_btn", None)
            if legend_btn is not None:
                try:
                    legend_btn.setText("≡ Legend & stats")
                    legend_btn.setStyleSheet(self._preview_header_button_stylesheet(active=False))
                    legend_btn.setVisible(show_header)
                except Exception:
                    pass

            delta_btn = getattr(self, "_preview_header_delta_btn", None)
            if delta_btn is not None:
                try:
                    delta_enabled = bool(self._delta_toggle_is_enabled())
                    delta_active = bool(getattr(self, "_temp_delta_mode", False))
                    delta_btn.setText("ΔT" if (delta_active or not delta_enabled) else "T")
                    delta_btn.setEnabled(delta_enabled)
                    delta_btn.setToolTip("" if delta_enabled else "Delta T unavailable: no logged ambient temperature for this result.")
                    delta_btn.setStyleSheet(
                        self._preview_header_button_stylesheet(
                            active=delta_active,
                            enabled=delta_enabled,
                        )
                    )
                    delta_btn.setVisible(show_header)
                except Exception:
                    pass

            zero_btn = getattr(self, "_preview_header_zero_btn", None)
            if zero_btn is not None:
                try:
                    zero_btn.setText("0Y" if bool(getattr(self, "_zero_y_mode", False)) else "AutoY")
                    zero_btn.setStyleSheet(self._preview_header_button_stylesheet(active=bool(getattr(self, "_zero_y_mode", False))))
                    zero_btn.setVisible(show_header)
                except Exception:
                    pass

            copy_btn = getattr(self, "_preview_header_copy_btn", None)
            if copy_btn is not None:
                try:
                    copy_btn.setStyleSheet(self._preview_header_button_stylesheet(active=False))
                    copy_btn.setVisible(show_header)
                except Exception:
                    pass

            self._refresh_preview_header_tooltip()
        except Exception:
            pass

    def set_preview_header_controls(
        self,
        *,
        header_widget: QWidget | None = None,
        separator: QWidget | None = None,
        title_label: QLabel | None = None,
        subtitle_label: QLabel | None = None,
        zero_btn: QPushButton | None = None,
        delta_btn: QPushButton | None = None,
        legend_btn: QPushButton | None = None,
        copy_btn: QPushButton | None = None,
    ) -> None:
        try:
            self._preview_header_widget = header_widget
            self._preview_header_separator = separator
            self._preview_header_title_label = title_label
            self._preview_header_subtitle_label = subtitle_label
            self._preview_header_zero_btn = zero_btn
            self._preview_header_delta_btn = delta_btn
            self._preview_header_legend_btn = legend_btn
            self._preview_header_copy_btn = copy_btn

            for widget in (header_widget, title_label, subtitle_label):
                if widget is not None:
                    try:
                        widget.installEventFilter(self)
                    except Exception:
                        pass
                    try:
                        widget.setMouseTracking(True)
                    except Exception:
                        pass

            if zero_btn is not None:
                try:
                    zero_btn.clicked.connect(self.toggle_zero_y_mode)
                except Exception:
                    pass
            if delta_btn is not None:
                try:
                    delta_btn.clicked.connect(self.toggle_delta_mode)
                except Exception:
                    pass
            if legend_btn is not None:
                try:
                    legend_btn.clicked.connect(self.toggle_legend_popup)
                except Exception:
                    pass
            if copy_btn is not None:
                try:
                    copy_btn.clicked.connect(self._copy_graph_to_clipboard)
                except Exception:
                    pass
        except Exception:
            pass
        self._sync_preview_header_controls()

    def _set_preview_header_path(self, path: Path | None) -> None:
        try:
            if path is None:
                self._preview_header_title_text = ""
                self._preview_header_subtitle_text = ""
            elif str(path.name).strip().lower() == "compare_manifest.json":
                try:
                    manifest = json.loads(path.read_text(encoding="utf-8"))
                except Exception:
                    manifest = {}

                case_name = str(manifest.get("display_case_name") or "").strip()
                run_name = str(manifest.get("display_run_name") or "").strip()

                if not case_name or not run_name:
                    runs_rel = [str(r) for r in (manifest.get("runs") or []) if str(r).strip()]

                    def _stress_label_from_run_name(run_name_value: str) -> str:
                        try:
                            m = re.match(r"^(CPU|GPU|CPUGPU)_W\d+_L\d+_V\d+$", str(run_name_value or ""), flags=re.IGNORECASE)
                            if m:
                                return str(m.group(1)).upper()
                        except Exception:
                            pass
                        return str(run_name_value or "").strip()

                    case_parts: list[str] = []
                    run_parts: list[str] = []
                    for rel in runs_rel:
                        parts = [p for p in str(rel).replace("\\", "/").split("/") if p]
                        if len(parts) < 2:
                            continue
                        case = str(parts[-2]).strip()
                        run = str(parts[-1]).strip()
                        if case:
                            case_parts.append(case)
                        stress = _stress_label_from_run_name(run)
                        run_parts.append(f"{case} {stress}".strip() if case else stress)

                    if not case_name:
                        case_name = " vs ".join([p for p in case_parts if p])
                    if not run_name:
                        run_name = " vs ".join([p for p in run_parts if p])

                self._preview_header_title_text = case_name or str(path.parent.parent.name or "").strip()
                self._preview_header_subtitle_text = run_name or str(path.parent.name or "").strip()
            else:
                run_folder = str(path.parent.name or "").strip()
                case_folder = str(path.parent.parent.name or "").strip()

                if not case_folder:
                    case_folder = run_folder
                if not run_folder:
                    run_folder = str(path.name or "").strip()

                self._preview_header_title_text = case_folder
                self._preview_header_subtitle_text = run_folder
        except Exception:
            self._preview_header_title_text = ""
            self._preview_header_subtitle_text = ""
        self._sync_preview_header_controls()


    def _copy_graph_to_clipboard(self) -> None:
        """Show a popup with checkboxes to select which graphs to copy to clipboard."""
        try:
            copy_btn = getattr(self, "_preview_header_copy_btn", None)
            if copy_btn is None:
                return

            # Determine available graphs
            is_compare = bool(getattr(self, "_compare_mode", False))
            is_multi = bool(getattr(self, "_single_mode_multi_axis", False)) and not is_compare

            graph_labels: list[str] = []
            if is_compare:
                graph_labels = [str(s) for s in (getattr(self, "_compare_manifest_sensors", []) or [])]
                # Fallback: try to reload compare manifest if sensors missing
                if not graph_labels:
                    mp = getattr(self, "_compare_manifest_path", None)
                    if mp is not None:
                        try:
                            self._plot_compare_manifest(mp)
                            graph_labels = [str(s) for s in (getattr(self, "_compare_manifest_sensors", []) or [])]
                        except Exception:
                            pass
                if not graph_labels:
                    from PySide6.QtWidgets import QMessageBox
                    QMessageBox.warning(copy_btn, "Copy Graph", "No compare sensors found. Compare manifest may be missing or invalid.")
                    return
            elif is_multi:
                for src_ax in (getattr(self, "_single_axes", []) or []):
                    st = (getattr(self, "_single_axis_state", {}) or {}).get(src_ax)
                    if st:
                        unit = str(st.get("unit", ""))
                        graph_labels.append(self._measurement_title_for_unit(unit, fallback="Graph"))
                    else:
                        graph_labels.append("Graph")

            # Single-axis mode: only one graph, copy directly
            if len(graph_labels) <= 1:
                self._render_graph_to_clipboard(axis_indices=None)
                return

            # --- Build popup ---
            dark = bool(getattr(self, "_theme_is_dark", True))
            if dark:
                bg, text, secondary = "#242424", "#EAEAEA", "#9A9A9A"
                border, sep_color = "#3A3A3A", "rgba(255,255,255,0.10)"
                hover_bg = "rgba(255,255,255,0.06)"
                btn_bg, btn_border = "#2A2A2A", "#3A3A3A"
                btn_hover_bg, btn_hover_border = "#333333", "#4A4A4A"
                indicator_off, indicator_on = "#555555", "#9A9A9A"
            else:
                bg, text, secondary = "#FFFFFF", "#1A1A1A", "#5E5E5E"
                border, sep_color = "#D0D0D0", "rgba(0,0,0,0.10)"
                hover_bg = "rgba(0,0,0,0.06)"
                btn_bg, btn_border = "#F5F5F5", "#D0D0D0"
                btn_hover_bg, btn_hover_border = "#E8E8E8", "#B0B0B0"
                indicator_off, indicator_on = "#C0C0C0", "#888888"

            popup = QDialog(copy_btn, Qt.Popup | Qt.FramelessWindowHint)
            popup.setAttribute(Qt.WA_DeleteOnClose)
            popup.setStyleSheet(
                f"QDialog {{ background: {bg}; border: 1px solid {border}; padding: 0; }}"
            )

            layout = QVBoxLayout(popup)
            layout.setContentsMargins(8, 8, 8, 8)
            layout.setSpacing(2)

            # Header label
            header = QLabel("Select graphs to copy")
            header.setStyleSheet(
                f"color: {secondary}; font-size: 10px; font-weight: 600;"
                f" padding: 2px 4px 6px 4px; background: transparent; border: none;"
            )
            layout.addWidget(header)

            cb_style = (
                f"QCheckBox {{ color: {text}; font-size: 12px; padding: 6px 6px;"
                f" background: transparent; border: none; border-radius: 0px; spacing: 8px; }}"
                f"QCheckBox:hover {{ background: {hover_bg}; }}"
                f"QCheckBox::indicator {{ width: 8px; height: 8px;"
                f" border: 2px solid {indicator_off}; border-radius: 6px; background: transparent; }}"
                f"QCheckBox::indicator:checked {{ background: {indicator_on};"
                f" border-color: {indicator_on}; }}"
            )

            # "Select all" checkbox
            select_all_cb = QCheckBox("Select all")
            select_all_cb.setChecked(False)
            select_all_cb.setStyleSheet(
                cb_style.replace("font-size: 12px;", "font-size: 12px; font-weight: 600;")
            )
            layout.addWidget(select_all_cb)

            # Separator
            sep = QWidget()
            sep.setFixedHeight(1)
            sep.setStyleSheet(f"background: {sep_color}; border: none;")
            layout.addWidget(sep)

            # Individual graph checkboxes
            checkboxes: list[QCheckBox] = []
            for lbl in graph_labels:
                cb = QCheckBox(lbl)
                cb.setChecked(False)
                cb.setStyleSheet(cb_style)
                layout.addWidget(cb)
                checkboxes.append(cb)

            # Wire Select All <-> individual checkboxes
            _updating_select_all = [False]

            def _on_select_all_toggled(state):
                _updating_select_all[0] = True
                checked = select_all_cb.isChecked()
                for cb in checkboxes:
                    cb.setChecked(checked)
                _updating_select_all[0] = False
                copy_action_btn.setEnabled(checked)

            def _on_individual_toggled(state=None):
                if _updating_select_all[0]:
                    return
                all_checked = all(cb.isChecked() for cb in checkboxes)
                any_checked = any(cb.isChecked() for cb in checkboxes)
                _updating_select_all[0] = True
                select_all_cb.setChecked(all_checked)
                _updating_select_all[0] = False
                copy_action_btn.setEnabled(any_checked)

            select_all_cb.toggled.connect(_on_select_all_toggled)
            for cb in checkboxes:
                cb.toggled.connect(_on_individual_toggled)

            # Bottom button row
            btn_row = QHBoxLayout()
            btn_row.setContentsMargins(0, 6, 0, 0)
            btn_row.setSpacing(8)
            btn_row.addStretch()

            copy_action_btn = QPushButton("Copy")
            copy_action_btn.setCursor(Qt.PointingHandCursor)
            copy_action_btn.setFocusPolicy(Qt.NoFocus)
            copy_action_btn.setEnabled(False)
            copy_action_btn.setStyleSheet(
                f"QPushButton {{ background: {btn_bg}; color: {text};"
                f" border: 1px solid {btn_border}; border-radius: 6px;"
                f" padding: 6px 20px; font-size: 12px; font-weight: 500; }}"
                f"QPushButton:hover {{ background: {btn_hover_bg}; border-color: {btn_hover_border}; }}"
                f"QPushButton:disabled {{ background: {sep_color}; color: {secondary}; border-color: {sep_color}; }}"
            )

            def _do_copy():
                selected = [i for i, cb in enumerate(checkboxes) if cb.isChecked()]
                popup.close()
                if not selected:
                    return
                if len(selected) == len(graph_labels):
                    self._render_graph_to_clipboard(axis_indices=None)
                else:
                    self._render_graph_to_clipboard(axis_indices=selected)

            copy_action_btn.clicked.connect(_do_copy)
            btn_row.addWidget(copy_action_btn)
            layout.addLayout(btn_row)

            # Position below the button
            pos = copy_btn.mapToGlobal(copy_btn.rect().bottomLeft())
            popup.adjustSize()
            popup.move(pos)
            popup.show()
        except Exception:
            pass

    def _render_graph_to_clipboard(self, *, axis_indices: list[int] | None = None) -> None:
        """Render graph(s) as a static PNG with axis labels + legend, then copy to clipboard.

        axis_indices=None       -> all graphs in one image.
        axis_indices=[0]        -> only the subplot at index 0.
        axis_indices=[0, 2]     -> selected subplots combined into one image.
        """
        import matplotlib.patheffects as pe
        import textwrap
        from matplotlib.figure import Figure as _Fig
        from matplotlib.backends.backend_agg import FigureCanvasAgg

        try:
            def _wrap_text(value: str, width: int) -> str:
                s = str(value or "").strip()
                if not s:
                    return ""
                try:
                    return textwrap.fill(
                        s,
                        width=max(8, int(width)),
                        break_long_words=False,
                        break_on_hyphens=False,
                    )
                except Exception:
                    return s

            def _format_elapsed_label(total_seconds: float) -> str:
                try:
                    s = max(0, int(round(float(total_seconds))))
                except Exception:
                    s = 0
                h = s // 3600
                m = (s % 3600) // 60
                sec = s % 60
                return f"{h}:{m:02d}:{sec:02d}" if h > 0 else f"{m}:{sec:02d}"

            def _even_tick_seconds(total_seconds: float, target_labels: int = 5) -> list[float]:
                """
                Always includes 0 and total_seconds.
                Interior ticks are spread evenly.
                Reduces label count automatically if rounding would create duplicate labels.
                """
                try:
                    total_seconds = max(0.0, float(total_seconds))
                except Exception:
                    total_seconds = 0.0

                if total_seconds <= 0.0:
                    return [0.0]

                # Start with a reasonable number of labels.
                # For very short ranges, do not request more whole-second labels than possible.
                max_unique_second_labels = max(2, int(np.floor(total_seconds)) + 1)
                n = max(2, min(int(target_labels), max_unique_second_labels))

                while n >= 2:
                    tick_secs = np.linspace(0.0, total_seconds, num=n)
                    labels = [_format_elapsed_label(s) for s in tick_secs]

                    # Keep shrinking until rounded labels are unique.
                    if len(labels) == len(set(labels)):
                        return [float(s) for s in tick_secs]

                    n -= 1

                return [0.0, float(total_seconds)]

            def _format_elapsed_label(total_seconds: float) -> str:
                try:
                    s = max(0, int(round(float(total_seconds))))
                except Exception:
                    s = 0
                h = s // 3600
                m = (s % 3600) // 60
                sec = s % 60
                return f"{h}:{m:02d}:{sec:02d}" if h > 0 else f"{m}:{sec:02d}"


            def _nice_tick_step_seconds(total_seconds: float, target_ticks: int = 5) -> int:
                steps = [
                    1, 2, 5, 10, 15, 30,
                    60, 120, 300, 600, 900, 1800,
                    3600, 7200, 10800, 21600,
                ]
                try:
                    total_seconds = max(1.0, float(total_seconds))
                    raw = total_seconds / max(2, int(target_ticks))
                    for step in steps:
                        if step >= raw:
                            return int(step)
                    return int(steps[-1])
                except Exception:
                    return 60

            def _apply_export_elapsed_axis(ax, x_vals, is_dt: bool) -> None:
                try:
                    x_arr = np.asarray(x_vals, dtype=float)
                    if x_arr.size == 0:
                        return

                    x0 = float(x_arr[0])
                    x1 = float(x_arr[-1])

                    # Exact graph bounds: start at origin, end at final sample.
                    ax.set_xlim(x0, x1)
                    try:
                        ax.margins(x=0)
                    except Exception:
                        pass

                    total_seconds = (x1 - x0) * 86400.0 if is_dt else (x1 - x0)
                    total_seconds = max(0.0, float(total_seconds))

                    tick_secs = _even_tick_seconds(total_seconds, target_labels=5)

                    if is_dt:
                        ticks = [x0 + (s / 86400.0) for s in tick_secs]
                    else:
                        ticks = [x0 + s for s in tick_secs]

                    labels = [_format_elapsed_label(s) for s in tick_secs]

                    ax.set_xticks(ticks)
                    ax.set_xticklabels(labels)
                    ax.tick_params(axis="x", pad=6)

                    # Keep edge labels visible inside the export.
                    xtl = ax.get_xticklabels()
                    for i, lbl in enumerate(xtl):
                        try:
                            lbl.set_clip_on(False)
                            if i == 0:
                                lbl.set_horizontalalignment("left")
                            elif i == len(xtl) - 1:
                                lbl.set_horizontalalignment("right")
                            else:
                                lbl.set_horizontalalignment("center")
                        except Exception:
                            pass

                except Exception:
                    try:
                        if len(x_vals) > 0:
                            ax.set_xlim(float(x_vals[0]), float(x_vals[-1]))
                            ax.margins(x=0)
                    except Exception:
                        pass

            def _wrap_legend_label(value: str) -> str:
                return _wrap_text(value, 24)

            def _wrap_header_text(value: str) -> str:
                return _wrap_text(value, 80)

            def _wrap_axis_title(value: str) -> str:
                return _wrap_text(value, 42)

            is_compare = bool(getattr(self, "_compare_mode", False))
            is_multi = bool(getattr(self, "_single_mode_multi_axis", False)) and not is_compare

            dark = bool(getattr(self, "_theme_is_dark", True))
            fig_bg = str(self._preview_theme.get("figure_bg", "#121212"))
            label_color = self._preview_label_color()
            grid_color = str(getattr(self, "_preview_grid_color", "#3A3A3A"))
            dot_dashes = getattr(self, "_preview_dot_dashes", (0, (2, 4)))
            tick_color = "#BDBDBD" if dark else "#666666"

            title_text = str(getattr(self, "_preview_header_title_text", "") or "")
            subtitle_text = str(getattr(self, "_preview_header_subtitle_text", "") or "")
            suptitle = title_text
            if subtitle_text:
                suptitle = f"{title_text}  —  {subtitle_text}" if title_text else subtitle_text
            suptitle = _wrap_header_text(suptitle)

            # ----- helper: apply axes style on the export figure -----
            def _style_ax(fig, ax):
                ax.set_facecolor(fig_bg)
                for side in ("left", "right"):
                    ax.spines[side].set_visible(False)
                for side in ("top", "bottom"):
                    sp = ax.spines[side]
                    sp.set_visible(True)
                    sp.set_color(grid_color)
                    sp.set_linewidth(0.9)
                    sp.set_linestyle(dot_dashes)
                    sp.set_alpha(0.9 if dark else 0.95)
                ax.tick_params(axis="both", length=0)
                ax.tick_params(axis="x", colors=tick_color)
                ax.tick_params(axis="y", colors=tick_color)
                ax.xaxis.label.set_color(label_color)
                ax.yaxis.label.set_color(label_color)
                ax.grid(True, which="major", axis="y", color=grid_color, linewidth=0.9)
                for gl in ax.get_ygridlines():
                    gl.set_linestyle(dot_dashes)
                    gl.set_alpha(0.9 if dark else 0.95)

            def _style_legend_ax(ax):
                ax.set_facecolor(fig_bg)
                ax.set_xticks([])
                ax.set_yticks([])
                for side in ("left", "right", "top", "bottom"):
                    ax.spines[side].set_visible(False)

            def _draw_legend(ax, handles):
                if not handles:
                    return

                wrapped_handles = []
                for h in handles:
                    try:
                        wrapped_handles.append(
                            Line2D(
                                [0], [0],
                                color=h.get_color(),
                                lw=h.get_linewidth(),
                                label=_wrap_legend_label(h.get_label()),
                            )
                        )
                    except Exception:
                        wrapped_handles.append(h)

                leg = ax.legend(
                    handles=wrapped_handles,
                    loc="upper left",
                    bbox_to_anchor=(0.02, 0.98),
                    borderaxespad=0.0,
                    fontsize=8,
                    framealpha=0.7,
                    facecolor=fig_bg,
                    edgecolor=grid_color,
                    labelcolor=label_color,
                    handlelength=2.2,
                    handletextpad=0.8,
                    borderpad=0.9,
                    labelspacing=0.65,
                )
                try:
                    leg.get_frame().set_linewidth(0.8)
                except Exception:
                    pass

            def _make_export_figure(plot_rows: int, *, legend_rows: int):
                plot_rows = max(1, int(plot_rows))
                legend_rows = max(1, int(legend_rows))

                suptitle_lines = max(1, len(str(suptitle).splitlines())) if suptitle else 0

                # Dedicated header band so long titles never bleed into the plot area.
                header_h_in = 0.0
                if suptitle_lines > 0:
                    header_h_in = 0.45 + max(0, suptitle_lines - 1) * 0.24

                plot_h_in = max(3.2 * plot_rows, 4.8) if plot_rows > 1 else 5.2
                fig_h_in = plot_h_in + header_h_in

                fig = _Fig(
                    figsize=(12.8, fig_h_in),
                    dpi=300,
                    facecolor=fig_bg,
                )
                FigureCanvasAgg(fig)

                if suptitle_lines > 0:
                    outer = fig.add_gridspec(
                        2,
                        2,
                        height_ratios=[header_h_in, plot_h_in],
                        width_ratios=[82, 18],   # wider legend column
                        hspace=0.03,
                        wspace=0.04,
                        left=0.055,
                        right=0.965,             # extra right padding so legend never clips
                        top=0.975,
                        bottom=0.075,
                    )

                    header_ax = fig.add_subplot(outer[0, :])
                    header_ax.set_facecolor(fig_bg)
                    header_ax.set_xticks([])
                    header_ax.set_yticks([])
                    for side in ("left", "right", "top", "bottom"):
                        header_ax.spines[side].set_visible(False)

                    header_ax.text(
                        0.0,
                        1.0,
                        suptitle,
                        transform=header_ax.transAxes,
                        ha="left",
                        va="top",
                        fontsize=12,
                        color=label_color,
                        fontweight=600,
                        linespacing=1.15,
                        clip_on=False,
                    )

                    # subtle divider so header and graph are clearly distinct
                    try:
                        header_ax.axhline(0.02, color=grid_color, linewidth=0.8, alpha=0.8)
                    except Exception:
                        pass

                    plot_cell = outer[1, 0]
                    legend_cell = outer[1, 1]
                else:
                    outer = fig.add_gridspec(
                        1,
                        2,
                        width_ratios=[82, 18],
                        wspace=0.04,
                        left=0.055,
                        right=0.965,
                        top=0.965,
                        bottom=0.075,
                    )
                    plot_cell = outer[0, 0]
                    legend_cell = outer[0, 1]

                plot_gs = plot_cell.subgridspec(plot_rows, 1, hspace=0.48)
                plot_axes = []
                for i in range(plot_rows):
                    ax = fig.add_subplot(plot_gs[i, 0])
                    plot_axes.append(ax)

                legend_gs = legend_cell.subgridspec(legend_rows, 1, hspace=0.34)
                legend_axes = []
                for i in range(legend_rows):
                    lax = fig.add_subplot(legend_gs[i, 0])
                    _style_legend_ax(lax)
                    legend_axes.append(lax)

                return fig, plot_axes, legend_axes

            # ----- helper: plot lines and return legend handles -----
            base_lw = 1.6
            glow_lw = base_lw + 1.2
            glow_alpha = 0.16

            def _plot_cols(ax, x_vals, df, cols, color_map, is_dt):
                handles = []
                for c in cols:
                    y = pd.to_numeric(df[c], errors="coerce").to_numpy(dtype=float)
                    colc = str(color_map.get(str(c), "#FFFFFF"))
                    kw = dict(
                        linewidth=base_lw,
                        alpha=0.98,
                        solid_capstyle="round",
                        solid_joinstyle="round",
                        antialiased=True,
                        zorder=10,
                    )
                    if is_dt:
                        ln = ax.plot_date(x_vals, y, "-", color=colc, **kw)[0]
                    else:
                        ln = ax.plot(x_vals, y, "-", color=colc, **kw)[0]
                    try:
                        ln.set_path_effects([
                            pe.Stroke(linewidth=glow_lw, foreground=colc, alpha=glow_alpha),
                            pe.Normal(),
                        ])
                    except Exception:
                        pass
                    handles.append(Line2D([0], [0], color=colc, lw=base_lw, label=str(c)))
                return handles

            # ================= COMPARE MODE =================
            if is_compare:
                axes_state = getattr(self, "_compare_axis_state", {}) or {}
                sensors = list(getattr(self, "_compare_manifest_sensors", []) or [])
                compare_axes = list(getattr(self, "_compare_axes", []) or [])
                run_labels = list(getattr(self, "_compare_run_labels", []) or [])
                run_color_map = dict(getattr(self, "_compare_run_color_map", {}) or {})

                if axis_indices is not None:
                    sel = [i for i in axis_indices if i < len(compare_axes)]
                    if not sel:
                        return
                else:
                    sel = list(range(len(compare_axes)))

                n = len(sel)
                fig, fig_axes, legend_axes = _make_export_figure(n, legend_rows=1)

                for plot_i, src_i in enumerate(sel):
                    src_ax = compare_axes[src_i]
                    st = axes_state.get(src_ax)
                    if not st:
                        continue

                    dst_ax = fig_axes[plot_i]
                    _style_ax(fig, dst_ax)

                    x_vals = np.asarray(st["x"], dtype=float)
                    df = st["df"]
                    cols = st["cols"]
                    colors = st["colors"]
                    is_dt = bool(st.get("is_dt", True))
                    cmap_local = {str(c): str(clr) for c, clr in zip(cols, colors)}

                    _plot_cols(dst_ax, x_vals, df, cols, cmap_local, is_dt)

                    sensor_name = sensors[src_i] if src_i < len(sensors) else ""

                    dst_ax.set_ylabel(
                        str(sensor_name or "Graph"),
                        color=label_color,
                        fontsize=10,
                        rotation=90,
                        labelpad=18,
                    )
                    try:
                        dst_ax.yaxis.set_label_position("left")
                    except Exception:
                        pass

                    _apply_export_elapsed_axis(dst_ax, x_vals, is_dt)
                    dst_ax.set_xlabel("Elapsed time", color=label_color, fontsize=10)
                    dst_ax.tick_params(axis="x", labelbottom=True, bottom=True)

                all_handles = []
                for label in run_labels:
                    clr = run_color_map.get(str(label), "#FFFFFF")
                    all_handles.append(Line2D([0], [0], color=clr, lw=base_lw, label=str(label)))

                _draw_legend(legend_axes[0], all_handles)

            # ================= MULTI-AXIS (single run) =================
            elif is_multi:
                axis_state = getattr(self, "_single_axis_state", {}) or {}
                single_axes = list(getattr(self, "_single_axes", []) or [])

                if axis_indices is not None:
                    sel = [i for i in axis_indices if i < len(single_axes)]
                    if not sel:
                        return
                else:
                    sel = list(range(len(single_axes)))

                n = len(sel)
                fig, fig_axes, legend_axes = _make_export_figure(n, legend_rows=n)

                for plot_i, src_i in enumerate(sel):
                    src_ax = single_axes[src_i]
                    st = axis_state.get(src_ax)
                    if not st:
                        continue

                    dst_ax = fig_axes[plot_i]
                    legend_ax = legend_axes[plot_i]

                    _style_ax(fig, dst_ax)

                    x_vals = np.asarray(st["x"], dtype=float)
                    df = st["df"]
                    cols = st["cols"]
                    colors = st["colors"]
                    is_dt = bool(st.get("is_dt", True))
                    unit = str(st.get("unit", ""))
                    cmap_local = {str(c): str(clr) for c, clr in zip(cols, colors)}

                    handles = _plot_cols(dst_ax, x_vals, df, cols, cmap_local, is_dt)
                    measurement_label = self._measurement_title_for_unit(unit, fallback="Graph")

                    dst_ax.set_ylabel(
                        measurement_label,
                        color=label_color,
                        fontsize=10,
                        rotation=90,
                        labelpad=18,
                    )
                    try:
                        dst_ax.yaxis.set_label_position("left")
                    except Exception:
                        pass

                    _draw_legend(legend_ax, handles)

                    _apply_export_elapsed_axis(dst_ax, x_vals, is_dt)
                    dst_ax.set_xlabel("Elapsed time", color=label_color, fontsize=10)
                    dst_ax.tick_params(axis="x", labelbottom=True, bottom=True)

            # ================= SINGLE AXIS =================
            else:
                df_all = getattr(self, "_preview_df_all", None)
                x_vals = getattr(self, "_preview_x", None)
                is_dt = bool(getattr(self, "_preview_is_dt", True))
                color_map = dict(getattr(self, "_preview_color_map", {}) or {})
                active_cols = list(self._effective_active_cols())

                if df_all is None or x_vals is None or not active_cols:
                    return

                x_vals = np.asarray(x_vals, dtype=float)

                fig, fig_axes, legend_axes = _make_export_figure(1, legend_rows=1)
                ax = fig_axes[0]
                legend_ax = legend_axes[0]

                _style_ax(fig, ax)

                handles = _plot_cols(ax, x_vals, df_all, active_cols, color_map, is_dt)

                measurement_label = str(self._single_axis_measurement_label() or "Graph")
                ax.set_ylabel(
                    measurement_label,
                    color=label_color,
                    fontsize=10,
                    rotation=90,
                    labelpad=18,
                )
                try:
                    ax.yaxis.set_label_position("left")
                except Exception:
                    pass

                _apply_export_elapsed_axis(ax, x_vals, is_dt)
                ax.set_xlabel("Elapsed time", color=label_color, fontsize=10)
                ax.tick_params(axis="x", labelbottom=True, bottom=True)

                _draw_legend(legend_ax, handles)

            try:
                fig.align_ylabels(fig_axes)
            except Exception:
                pass

            buf = io.BytesIO()
            fig.savefig(
                buf,
                format="png",
                facecolor=fig.get_facecolor(),
                edgecolor="none",
                bbox_inches="tight",
                pad_inches=0.08,
            )
            buf.seek(0)

            qimg = QImage()
            qimg.loadFromData(buf.read())
            clipboard = QApplication.clipboard()
            if clipboard is not None:
                clipboard.setImage(qimg)

            buf.close()
            try:
                fig.clear()
            except Exception:
                pass

            copy_btn = getattr(self, "_preview_header_copy_btn", None)
            if copy_btn is not None:
                try:
                    prev_text = copy_btn.text()
                    copy_btn.setText("Copied!")
                    QTimer.singleShot(1500, lambda: copy_btn.setText(prev_text))
                except Exception:
                    pass

        except Exception as e:
            import traceback
            traceback.print_exc()

    def _apply_preview_surface_theme(self) -> None:
        try:
            bg = str(self._preview_theme.get("figure_bg", "#121212"))
        except Exception:
            bg = "#121212"

        try:
            if getattr(self, "_preview_canvas", None) is not None:
                self._preview_canvas.setAttribute(Qt.WA_StyledBackground, True)
                self._preview_canvas.setContentsMargins(0, 0, 0, 0)
                self._preview_canvas.setStyleSheet(
                    f"background-color: {bg}; border: none; margin: 0px; padding: 0px;"
                )
        except Exception:
            pass

        try:
            if getattr(self, "_preview_label", None) is not None:
                self._preview_label.setStyleSheet(
                    f"background-color: {bg}; border: none; margin: 0px; padding: 0px;"
                )
        except Exception:
            pass

        try:
            if getattr(self, "_preview_timeline_fig", None) is not None:
                self._preview_timeline_fig.set_facecolor(bg)
        except Exception:
            pass

        try:
            if getattr(self, "_preview_timeline_canvas", None) is not None:
                self._preview_timeline_canvas.setAttribute(Qt.WA_StyledBackground, True)
                self._preview_timeline_canvas.setStyleSheet(
                    f"background-color: {bg}; border: none; margin: 0px; padding: 0px;"
                )
        except Exception:
            pass

        try:
            self._sync_preview_header_controls()
        except Exception:
            pass

    def _apply_preview_axes_style(self, ax) -> None:
        try:
            if bool(getattr(self, "_theme_is_dark", True)):
                apply_dark_axes_style(
                    self._preview_fig,
                    ax,
                    grid_color=self._preview_grid_color,
                    dot_dashes=self._preview_dot_dashes,
                )
            else:
                apply_light_axes_style(
                    self._preview_fig,
                    ax,
                    grid_color=self._preview_grid_color,
                    dot_dashes=self._preview_dot_dashes,
                )
        except Exception:
            pass

    def _refresh_current_preview_theme(self) -> None:
        try:
            try:
                if self._qt_tt is not None:
                    self._qt_tt.setStyleSheet(self._preview_tooltip_stylesheet())
            except Exception:
                pass

            try:
                for st in list((self._compare_axis_state or {}).values()):
                    tt = (st or {}).get("qt_tt") if isinstance(st, dict) else None
                    if tt is not None:
                        tt.setStyleSheet(self._preview_tooltip_stylesheet())
            except Exception:
                pass

            try:
                for st in list((self._single_axis_state or {}).values()):
                    tt = (st or {}).get("qt_tt") if isinstance(st, dict) else None
                    if tt is not None:
                        tt.setStyleSheet(self._preview_tooltip_stylesheet())
            except Exception:
                pass

            try:
                if self._preview_header_tt is not None:
                    self._preview_header_tt.setStyleSheet(self._preview_header_tooltip_stylesheet())
            except Exception:
                pass

            mp = getattr(self, "_compare_manifest_path", None)
            if mp is not None:
                try:
                    mp2 = Path(str(mp))
                    if mp2.exists() and mp2.is_file():
                        self._plot_compare_manifest(mp2)
                        return
                except Exception:
                    pass

            csvp = getattr(self, "_preview_csv_path", None)
            if csvp:
                try:
                    cp = Path(str(csvp))
                    if cp.exists() and cp.is_file():
                        self._plot_run_csv(str(cp))
                        return
                except Exception:
                    pass

            try:
                if getattr(self, "_preview_canvas", None) is not None:
                    self._preview_canvas.draw_idle()
            except Exception:
                pass
        except Exception:
            pass

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

    def _ensure_graph_transition_overlay(self) -> Optional[QLabel]:
        try:
            if self._preview_canvas is None:
                return None

            ov = getattr(self, "_graph_anim_overlay", None)
            if ov is not None:
                try:
                    if ov.parentWidget() is self._preview_canvas:
                        return ov
                except Exception:
                    pass
                try:
                    ov.hide()
                    ov.deleteLater()
                except Exception:
                    pass

            ov = QLabel(self._preview_canvas)
            ov.setObjectName("PreviewGraphTransitionOverlay")
            ov.setAttribute(Qt.WA_TransparentForMouseEvents, True)
            ov.setScaledContents(True)
            ov.hide()

            self._graph_anim_overlay = ov
            return ov
        except Exception:
            return None

    def _cancel_graph_transition(self) -> None:
        try:
            anim = getattr(self, "_graph_anim_fade", None)
            if anim is not None:
                anim.stop()
        except Exception:
            pass

        try:
            ov = getattr(self, "_graph_anim_overlay", None)
            if ov is not None:
                ov.hide()
                ov.clear()
                ov.setGraphicsEffect(None)
        except Exception:
            pass

        self._graph_anim_fade = None
        self._graph_anim_effect = None

    def _capture_graph_transition_before(self) -> Optional[QPixmap]:
        try:
            self._cancel_graph_transition()
        except Exception:
            pass

        try:
            if self._preview_canvas is None or not self._preview_canvas.isVisible():
                return None
            pm = self._preview_canvas.grab()
            if pm is None or pm.isNull():
                return None
            return pm
        except Exception:
            return None

    def _start_graph_transition(self, before: Optional[QPixmap], *, duration_ms: Optional[int] = None) -> None:
        try:
            if before is None or before.isNull():
                return
            if self._preview_canvas is None or not self._preview_canvas.isVisible():
                return

            ov = self._ensure_graph_transition_overlay()
            if ov is None:
                return

            ov.setGeometry(self._preview_canvas.rect())

            try:
                target_size = ov.size()
                pm = before if before.size() == target_size else before.scaled(
                    target_size,
                    Qt.IgnoreAspectRatio,
                    Qt.SmoothTransformation,
                )
            except Exception:
                pm = before

            ov.setPixmap(pm)

            eff = QGraphicsOpacityEffect(ov)
            eff.setOpacity(1.0)
            ov.setGraphicsEffect(eff)

            ov.show()
            ov.raise_()

            anim = QPropertyAnimation(eff, b"opacity", ov)
            anim.setDuration(max(1, int(duration_ms or getattr(self, "_graph_anim_duration_ms", 180) or 180)))
            anim.setStartValue(1.0)
            anim.setEndValue(0.0)
            anim.setEasingCurve(QEasingCurve.OutCubic)

            def _done():
                try:
                    ov.hide()
                    ov.clear()
                    ov.setGraphicsEffect(None)
                except Exception:
                    pass
                self._graph_anim_fade = None
                self._graph_anim_effect = None

            anim.finished.connect(_done)

            self._graph_anim_effect = eff
            self._graph_anim_fade = anim
            anim.start()
        except Exception:
            pass

    def _commit_graph_transition(self, before: Optional[QPixmap], *, duration_ms: Optional[int] = None) -> None:
        try:
            if self._preview_canvas is not None and self._preview_canvas.isVisible():
                self._preview_canvas.draw()
                try:
                    self._preview_canvas.update()
                except Exception:
                    pass
        except Exception:
            try:
                if self._preview_canvas is not None:
                    self._preview_canvas.draw_idle()
            except Exception:
                pass

        try:
            self._start_graph_transition(before, duration_ms=duration_ms)
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

    @staticmethod
    def _ease_in_out_cubic(t: float) -> float:
        t = 0.0 if t < 0.0 else (1.0 if t > 1.0 else t)
        if t < 0.5:
            return 4.0 * t * t * t
        return 1.0 - ((-2.0 * t + 2.0) ** 3) / 2.0

    @staticmethod
    def _is_ambient_sensor_name(name: str) -> bool:
        try:
            s = str(name).strip().lower()
        except Exception:
            return False
        if not s:
            return False
        return (s == "ambient [°c]") or ("ambient" in s)

    def _stop_plot_morph(self) -> None:
        try:
            if self._plot_morph_timer.isActive():
                self._plot_morph_timer.stop()
        except Exception:
            pass
        self._plot_morph_axes = []
        self._plot_morph_finish = None

    def _start_plot_morph(
        self,
        axis_payloads: list[dict],
        *,
        finish_callback=None,
        duration_s: Optional[float] = None,
    ) -> bool:
        try:
            if not axis_payloads:
                return False

            # Finish any running morph first so artists are restored cleanly.
            try:
                if self._plot_morph_timer.isActive():
                    self._finish_plot_morph()
            except Exception:
                pass

            # Hide all interaction overlays before starting the tween.
            try:
                self._plot_morph_lock_interaction = True
            except Exception:
                pass
            try:
                self._hide_preview_hover(hard=True)
            except Exception:
                pass
            try:
                self._hide_compare_hover_all()
            except Exception:
                pass
            try:
                self._hide_single_hover_all()
            except Exception:
                pass

            line_count = 0
            has_ylim_anim = False
            for ap in axis_payloads:
                has_ylim_anim = has_ylim_anim or bool(ap.get("animate_ylim", False))
                line_count += int(len(ap.get("lines", []) or []))

            # Adaptive animation LOD:
            # shrink each animated line further based on total line count.
            if line_count > 0:
                try:
                    per_line_budget = max(
                        48,
                        min(
                            int(getattr(self, "_plot_morph_max_points_per_line", 320) or 320),
                            int(getattr(self, "_plot_morph_max_total_points", 2400) or 2400) // max(1, line_count),
                        ),
                    )
                except Exception:
                    per_line_budget = 96

                for ap in axis_payloads:
                    new_lines = []
                    for lp in list(ap.get("lines", []) or []):
                        try:
                            y0 = np.asarray(lp.get("y0", []), dtype=float)
                            x_anim = np.asarray(lp.get("x_anim", []), dtype=float)
                            dy = np.asarray(lp.get("dy", []), dtype=float)
                            y1_anim = np.asarray(lp.get("y1_anim", []), dtype=float)

                            n = int(len(y0))
                            if n > per_line_budget:
                                idx = self._plot_morph_sample_indices(n, per_line_budget)
                                lp = dict(lp)
                                lp["x_anim"] = x_anim[idx]
                                lp["y0"] = y0[idx]
                                lp["dy"] = dy[idx]
                                lp["y1_anim"] = y1_anim[idx]

                                try:
                                    ln = lp["line"]
                                    ln.set_xdata(lp["x_anim"])
                                    ln.set_ydata(lp["y0"])
                                except Exception:
                                    pass
                        except Exception:
                            pass

                        new_lines.append(lp)

                    ap["lines"] = new_lines

            total_points = 0
            for ap in axis_payloads:
                for lp in list(ap.get("lines", []) or []):
                    try:
                        total_points += int(len(lp.get("y0", [])))
                    except Exception:
                        pass

            # Hard budget: skip morph if still too heavy.
            if total_points > int(getattr(self, "_plot_morph_max_total_points", 2400) or 2400):
                try:
                    self._plot_morph_lock_interaction = False
                except Exception:
                    pass
                return False

            self._plot_morph_axes = list(axis_payloads)
            self._plot_morph_finish = finish_callback
            self._plot_morph_t0 = float(time.time())
            self._plot_morph_duration_s = float(duration_s or 0.10)
            self._plot_morph_bgs = {}

            # y-limit animation needs redraws; line-only morph can use blit.
            self._plot_morph_mode = "redraw" if has_ylim_anim else "blit"

            try:
                if self._plot_morph_mode == "blit":
                    self._plot_morph_timer.setInterval(33)  # ~30 fps

                    if self._preview_canvas is not None:
                        animated_lines = []

                        # Mark animated lines so they are excluded from the cached background.
                        for ap in self._plot_morph_axes:
                            for lp in list(ap.get("lines", []) or []):
                                try:
                                    ln = lp["line"]
                                    ln.set_animated(True)
                                    animated_lines.append(ln)
                                except Exception:
                                    pass

                        # Draw clean background without animated artists.
                        self._preview_canvas.draw()

                        for ap in self._plot_morph_axes:
                            ax = ap.get("ax")
                            if ax is None:
                                continue
                            try:
                                self._plot_morph_bgs[ax] = self._preview_canvas.copy_from_bbox(ax.bbox)
                            except Exception:
                                pass

                        # Restore normal state; manual draw_artist will handle them during tween.
                        for ln in animated_lines:
                            try:
                                ln.set_animated(False)
                            except Exception:
                                pass
                else:
                    self._plot_morph_timer.setInterval(40)  # slower but steadier for redraw mode
            except Exception:
                pass

            self._plot_morph_tick()
            if self._plot_morph_axes:
                self._plot_morph_timer.start()
            return True
        except Exception:
            try:
                self._plot_morph_lock_interaction = False
            except Exception:
                pass
            return False

    def _finish_plot_morph(self) -> None:
        axes_payload = list(getattr(self, "_plot_morph_axes", []) or [])
        finish_cb = getattr(self, "_plot_morph_finish", None)

        try:
            for ap in axes_payload:
                ax = ap.get("ax")
                ylim1 = ap.get("ylim1")
                if ax is not None and ylim1 is not None:
                    try:
                        ax.set_ylim(float(ylim1[0]), float(ylim1[1]))
                    except Exception:
                        pass

                for lp in list(ap.get("lines", []) or []):
                    try:
                        ln = lp["line"]
                        ln.set_xdata(lp["x_full"])
                        ln.set_ydata(lp["y1_full"])
                        ln.set_alpha(float(lp["a1"]))
                        ln.set_visible(bool(lp["vis1"]))

                        try:
                            ln.set_path_effects(lp.get("restore_pe") or [])
                        except Exception:
                            pass
                        try:
                            ln.set_antialiased(bool(lp.get("restore_aa", True)))
                        except Exception:
                            pass
                        try:
                            ln.set_linewidth(float(lp.get("restore_lw", 1.6)))
                        except Exception:
                            pass
                    except Exception:
                        pass
        except Exception:
            pass

        self._stop_plot_morph()
        self._plot_morph_bgs = {}

        try:
            self._plot_morph_lock_interaction = False
        except Exception:
            pass

        # FINAL DRAW
        try:
            if self._preview_canvas is not None:
                self._preview_canvas.draw()
        except Exception:
            try:
                if self._preview_canvas is not None:
                    self._preview_canvas.draw_idle()
            except Exception:
                pass

        # IMPORTANT: refresh blit backgrounds so hover doesn't restore stale pre-toggle axes
        try:
            if getattr(self, "_compare_mode", False):
                self._compare_last_idx = None
                self._refresh_compare_backgrounds()
            elif getattr(self, "_single_mode_multi_axis", False):
                self._single_last_idx = None
                self._refresh_single_backgrounds()
            else:
                self._preview_last_tt_idx = None
                try:
                    self._on_preview_draw()
                except Exception:
                    pass
        except Exception:
            pass

        try:
            if callable(finish_cb):
                finish_cb()
        except Exception:
            pass

    def _plot_morph_tick(self) -> None:
        try:
            payloads = list(getattr(self, "_plot_morph_axes", []) or [])
            if not payloads:
                self._stop_plot_morph()
                return

            dur = float(getattr(self, "_plot_morph_duration_s", 0.20) or 0.20)
            if dur <= 0.0:
                self._finish_plot_morph()
                return

            t = (time.time() - float(getattr(self, "_plot_morph_t0", 0.0))) / dur
            if t >= 1.0:
                self._finish_plot_morph()
                return

            e = self._ease_in_out_cubic(float(t))

            if str(getattr(self, "_plot_morph_mode", "blit")) == "blit":
                c = self._preview_canvas
                if c is None:
                    self._finish_plot_morph()
                    return

                for ap in payloads:
                    ax = ap.get("ax")
                    if ax is None:
                        continue

                    bg = self._plot_morph_bgs.get(ax)
                    if bg is None:
                        continue

                    try:
                        c.restore_region(bg)
                    except Exception:
                        continue

                    for lp in list(ap.get("lines", []) or []):
                        try:
                            ln = lp["line"]
                            y = lp["y0"] + (lp["dy"] * e)
                            a = float(lp["a0"]) + (float(lp["a1"]) - float(lp["a0"])) * e
                            ln.set_ydata(y)
                            ln.set_alpha(a)
                            ln.set_visible(bool(a > 0.001 or lp["vis1"]))
                            ax.draw_artist(ln)
                        except Exception:
                            pass

                    try:
                        c.blit(ax.bbox)
                    except Exception:
                        pass
                return

            # redraw mode (used for quick ylim tween)
            for ap in payloads:
                ax = ap.get("ax")
                if ax is None:
                    continue

                if bool(ap.get("animate_ylim", False)):
                    try:
                        y0a, y0b = float(ap["ylim0"][0]), float(ap["ylim0"][1])
                        y1a, y1b = float(ap["ylim1"][0]), float(ap["ylim1"][1])
                        ax.set_ylim(
                            y0a + (y1a - y0a) * e,
                            y0b + (y1b - y0b) * e,
                        )
                    except Exception:
                        pass

                for lp in list(ap.get("lines", []) or []):
                    try:
                        ln = lp["line"]
                        y = lp["y0"] + (lp["dy"] * e)
                        a = float(lp["a0"]) + (float(lp["a1"]) - float(lp["a0"])) * e
                        ln.set_ydata(y)
                        ln.set_alpha(a)
                        ln.set_visible(bool(a > 0.001 or lp["vis1"]))
                    except Exception:
                        pass

            try:
                if self._preview_canvas is not None:
                    self._preview_canvas.draw_idle()
            except Exception:
                pass
        except Exception:
            self._stop_plot_morph()

    def _compare_target_sensor_list_for_current_mode(self) -> list[str]:
        try:
            mp = getattr(self, "_compare_manifest_path", None)
            if mp is None:
                return list(getattr(self, "_compare_manifest_sensors", []) or [])

            manifest = json.loads(Path(str(mp)).read_text(encoding="utf-8"))
            sensors = [str(s) for s in (manifest.get("sensors") or []) if str(s).strip()]

            if self._delta_toggle_is_enabled() and bool(getattr(self, "_temp_delta_mode", False)):
                sensors = [s for s in sensors if not self._is_ambient_sensor_name(s)]

            return sensors
        except Exception:
            return list(getattr(self, "_compare_manifest_sensors", []) or [])

    @staticmethod
    def _plot_morph_sample_indices(n: int, max_points: int) -> np.ndarray:
        try:
            n = int(n)
            max_points = max(8, int(max_points))
            if n <= max_points:
                return np.arange(n, dtype=int)
            step = int(np.ceil((n - 1) / max(1, max_points - 1)))
            idx = np.arange(0, n, step, dtype=int)
            if idx.size == 0 or int(idx[-1]) != (n - 1):
                idx = np.append(idx, n - 1)
            return idx
        except Exception:
            return np.arange(max(0, int(n)), dtype=int)

    def _collect_axis_target_ylim_from_arrays(
        self,
        arrays: list[np.ndarray],
        *,
        zero_mode: bool,
    ) -> Optional[tuple[float, float]]:
        try:
            ys = []
            for arr in arrays:
                if arr is None:
                    continue
                a = np.asarray(arr, dtype=float)
                a = a[np.isfinite(a)]
                if a.size:
                    ys.append(a)
            if not ys:
                return None

            y_all = np.concatenate(ys)
            ymin = float(np.nanmin(y_all))
            ymax = float(np.nanmax(y_all))
            if not (np.isfinite(ymin) and np.isfinite(ymax)):
                return None

            if zero_mode:
                ymin0 = float(min(ymin, 0.0))
                ymax0 = float(max(ymax, 0.0))
                span0 = float(ymax0 - ymin0)
                pad0 = 1.0 if span0 == 0.0 else 0.06 * span0
                low = 0.0 if ymin >= 0.0 else (ymin0 - pad0)
                high = 0.0 if ymax <= 0.0 else (ymax0 + pad0)
                return (float(low), float(high))

            pad = 1.0 if ymin == ymax else 0.06 * (ymax - ymin)
            return (float(ymin - pad), float(ymax + pad))
        except Exception:
            return None

    def _prepare_line_for_morph(
        self,
        line,
        target_y: np.ndarray,
        *,
        target_visible: bool,
    ) -> Optional[dict]:
        try:
            try:
                x_full = np.asarray(line.get_xdata(orig=False), dtype=float)
            except Exception:
                x_full = np.asarray(line.get_xdata(), dtype=float)

            try:
                y0_full = np.asarray(line.get_ydata(orig=False), dtype=float)
            except Exception:
                y0_full = np.asarray(line.get_ydata(), dtype=float)

            y1_full = np.asarray(target_y, dtype=float)

            if x_full.shape != y0_full.shape or y0_full.shape != y1_full.shape:
                return None

            idx = self._plot_morph_sample_indices(
                len(y1_full),
                int(getattr(self, "_plot_morph_max_points_per_line", 320) or 320),
            )

            x_anim = x_full[idx]
            y0_anim = y0_full[idx]
            y1_anim = y1_full[idx]

            try:
                base_alpha = float(line.get_alpha())
                if not np.isfinite(base_alpha):
                    base_alpha = 0.98
            except Exception:
                base_alpha = 0.98

            try:
                pe0 = line.get_path_effects()
            except Exception:
                pe0 = None
            try:
                aa0 = bool(line.get_antialiased())
            except Exception:
                try:
                    aa0 = bool(line.get_aa())
                except Exception:
                    aa0 = True
            try:
                lw0 = float(line.get_linewidth())
            except Exception:
                lw0 = 1.6

            # IMPORTANT: capture visibility BEFORE forcing visible for animation
            try:
                start_visible = bool(line.get_visible())
            except Exception:
                start_visible = True

            a0 = float(base_alpha if start_visible else 0.0)
            a1 = float(base_alpha if target_visible else 0.0)

            # Cheapen the artist during the tween
            try:
                if bool(getattr(self, "_plot_morph_disable_glow", True)):
                    line.set_path_effects([])
            except Exception:
                pass
            try:
                if bool(getattr(self, "_plot_morph_reduce_antialias", True)):
                    line.set_antialiased(False)
            except Exception:
                pass
            try:
                line.set_linewidth(min(lw0, 1.3))
            except Exception:
                pass

            # Use reduced vertex count during animation
            try:
                line.set_xdata(x_anim)
                line.set_ydata(y0_anim)
                line.set_visible(True)
            except Exception:
                pass

            return {
                "line": line,
                "x_full": x_full,
                "y1_full": y1_full,
                "x_anim": x_anim,
                "y0": y0_anim,
                "dy": (y1_anim - y0_anim),
                "y1_anim": y1_anim,
                "a0": a0,
                "a1": a1,
                "vis1": bool(target_visible),
                "restore_pe": pe0,
                "restore_aa": aa0,
                "restore_lw": lw0,
            }
        except Exception:
            return None

    def _animate_current_ylims_only(self) -> bool:
        try:
            zero_mode = bool(getattr(self, "_zero_y_mode", False))
            axis_payloads = []

            if getattr(self, "_compare_mode", False):
                for ax in list(getattr(self, "_compare_axes", []) or []):
                    st = (self._compare_axis_state or {}).get(ax)
                    if not st:
                        continue
                    arrays = []
                    for arr in list((st.get("series_data") or {}).values()):
                        arrays.append(arr)
                    ylim1 = self._collect_axis_target_ylim_from_arrays(arrays, zero_mode=zero_mode)
                    if ylim1 is None:
                        continue
                    axis_payloads.append({
                        "ax": ax,
                        "lines": [],
                        "ylim0": tuple(ax.get_ylim()),
                        "ylim1": tuple(ylim1),
                        "animate_ylim": True,
                    })

            elif getattr(self, "_single_mode_multi_axis", False):
                active_set = set(self._effective_active_cols())
                for ax in list(getattr(self, "_single_axes", []) or []):
                    st = (self._single_axis_state or {}).get(ax)
                    if not st:
                        continue
                    arrays = []
                    for name, arr in list((st.get("series_data") or {}).items()):
                        if name in active_set:
                            arrays.append(arr)
                    ylim1 = self._collect_axis_target_ylim_from_arrays(arrays, zero_mode=zero_mode)
                    if ylim1 is None:
                        continue
                    axis_payloads.append({
                        "ax": ax,
                        "lines": [],
                        "ylim0": tuple(ax.get_ylim()),
                        "ylim1": tuple(ylim1),
                        "animate_ylim": True,
                    })

            else:
                if self._preview_ax is None:
                    return False
                arrays = []
                for name in list(self._effective_active_cols()):
                    arrays.append((self._preview_series_data or {}).get(name))
                ylim1 = self._collect_axis_target_ylim_from_arrays(arrays, zero_mode=zero_mode)
                if ylim1 is None:
                    return False
                axis_payloads.append({
                    "ax": self._preview_ax,
                    "lines": [],
                    "ylim0": tuple(self._preview_ax.get_ylim()),
                    "ylim1": tuple(ylim1),
                    "animate_ylim": True,
                })

            if not axis_payloads:
                return False

            return self._start_plot_morph(
                axis_payloads,
                duration_s=0.10,
            )
        except Exception:
            return False

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
            tt.setStyleSheet(self._preview_tooltip_stylesheet())
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
            header_color = str((getattr(self, "_preview_theme", {}) or {}).get("tooltip_text", "#FFFFFF"))

            lines = []
            # header
            lines.append(f"<span style='font-weight:700;color:{header_color}'>{header_e}</span>")

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
            header_color = str((getattr(self, "_preview_theme", {}) or {}).get("tooltip_text", "#FFFFFF"))
            return f"<div style='white-space:pre;color:{header_color};'><b>{self._html_escape(header)}</b></div>"

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
            self._compare_manifest_path = None
        except Exception:
            pass

        # Clear button handles so hit-testing doesn't use stale artists.
        try:
            self._ls_btn_text = None
            self._ls_btn_bbox = None
            self._delta_btn_text = None
            self._delta_btn_bbox = None
            self._zero_btn_text = None
            self._zero_btn_bbox = None
        except Exception:
            pass

        # Compare-mode uses multiple subplots; reset the figure back to a single axis
        # so old subplots can't linger when switching back to normal preview.
        try:
            if self._preview_fig is not None and self._preview_canvas is not None:
                self._preview_fig.clear()
                self._preview_ax = self._preview_fig.add_subplot(111)
                try:
                    self._preview_apply_axes_rect(
                        right_frac=float(self._preview_effective_right_frac()),
                        left_margin_px=self._preview_left_margin_px_base,
                    )
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

        self._restore_preview_canvas_size_policy()
        self._hide_sticky_timeline()

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
                    self._preview_apply_axes_rect(
                        right_frac=float(self._preview_effective_right_frac()),
                        left_margin_px=self._preview_left_margin_px_base,
                    )
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

        self._restore_preview_canvas_size_policy()
        self._hide_sticky_timeline()

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

    def get_timeline_canvas(self):
        return self._preview_timeline_canvas

    def set_preview_scroll_area(self, scroll_area) -> None:
        try:
            old_viewport = getattr(self, "_preview_scroll_viewport", None)
            if old_viewport is not None:
                try:
                    old_viewport.removeEventFilter(self)
                except Exception:
                    pass

            self._preview_scroll_area = scroll_area
            self._preview_scroll_viewport = None

            if scroll_area is not None and hasattr(scroll_area, "viewport"):
                viewport = scroll_area.viewport()
                self._preview_scroll_viewport = viewport
                if viewport is not None:
                    try:
                        viewport.installEventFilter(self)
                    except Exception:
                        pass
        except Exception:
            pass

        try:
            self._sync_preview_canvas_scroll_height()
        except Exception:
            pass

    def _restore_preview_canvas_size_policy(self) -> None:
        try:
            if self._preview_canvas is None:
                return
            self._preview_canvas.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
            self._preview_canvas.setMinimumHeight(0)
            self._preview_canvas.setMaximumHeight(16777215)
        except Exception:
            pass

    def _preview_effective_right_frac(self) -> float:
        try:
            base = float(getattr(self, "_preview_right_frac", 0.995) or 0.995)
        except Exception:
            base = 0.995

        try:
            fig = getattr(self, "_preview_fig", None)
            if fig is None:
                return base
            fig_w_px = float(fig.get_figwidth() * fig.dpi)
            if fig_w_px <= 1:
                return base
            right_pad_px = float(getattr(self, "_preview_right_pad_px", 18) or 18)
            pad_frac = max(0.0, min(right_pad_px / fig_w_px, 0.2))
            return max(0.75, min(base - pad_frac, base))
        except Exception:
            return base

    def _preview_axis_slot_height_px(self) -> int:
        try:
            viewport_h = 0
            viewport = getattr(self, "_preview_scroll_viewport", None)
            if viewport is not None:
                viewport_h = int(viewport.height() or 0)
            if viewport_h <= 0:
                try:
                    viewport_h = int(self._preview_canvas.parentWidget().height() or 0)
                except Exception:
                    viewport_h = 0
            viewport_h = max(320, viewport_h)
            visible_slots = max(1, int(getattr(self, "_preview_visible_axis_slots", 2) or 2))
            return max(
                int(getattr(self, "_preview_min_axis_height", 220) or 220),
                int(viewport_h / max(1, visible_slots)),
            )
        except Exception:
            return int(getattr(self, "_preview_min_axis_height", 220) or 220)

    def _preview_effective_vertical_fracs(self) -> tuple[float, float]:
        try:
            fig = getattr(self, "_preview_fig", None)
            if fig is None:
                return (
                    float(getattr(self, "_preview_top_frac", 0.93) or 0.93),
                    float(getattr(self, "_preview_bottom_frac", 0.05) or 0.05),
                )
            fig_h_px = float(fig.get_figheight() * fig.dpi)
            if fig_h_px <= 1:
                return (
                    float(getattr(self, "_preview_top_frac", 0.93) or 0.93),
                    float(getattr(self, "_preview_bottom_frac", 0.05) or 0.05),
                )

            top_pad_px = float(getattr(self, "_preview_top_pad_px", 22) or 22)
            bottom_pad_px = float(getattr(self, "_preview_bottom_pad_px", 16) or 16)
            top = 1.0 - max(0.0, min(top_pad_px / fig_h_px, 0.25))
            bottom = max(0.0, min(bottom_pad_px / fig_h_px, 0.2))
            top = max(bottom + 0.1, min(top, 0.995))
            bottom = max(0.0, min(bottom, top - 0.1))
            return top, bottom
        except Exception:
            return (
                float(getattr(self, "_preview_top_frac", 0.93) or 0.93),
                float(getattr(self, "_preview_bottom_frac", 0.05) or 0.05),
            )

    def _apply_single_slot_axis_layout(self, ax) -> None:
        try:
            if ax is None or self._preview_fig is None:
                return
            pos = ax.get_position()
            fig_h_px = float(self._preview_fig.get_figheight() * self._preview_fig.dpi)
            axis_h_px = float(self._preview_axis_slot_height_px())
            top, min_bottom = self._preview_effective_vertical_fracs()
            bottom = max(0.0, min(top - 0.05, top - (axis_h_px / fig_h_px))) if fig_h_px > 1 else min_bottom
            bottom = max(float(min_bottom), bottom)
            ax.set_position([float(pos.x0), bottom, float(pos.width), max(0.05, top - bottom)])
        except Exception:
            pass

    def _hide_sticky_timeline(self) -> None:
        try:
            if getattr(self, "_preview_timeline_canvas", None) is not None:
                self._preview_timeline_canvas.hide()
        except Exception:
            pass

    def _update_sticky_timeline(self, source_ax=None) -> None:
        try:
            canvas = getattr(self, "_preview_timeline_canvas", None)
            fig = getattr(self, "_preview_timeline_fig", None)
            ax = getattr(self, "_preview_timeline_ax", None)
            if canvas is None or fig is None or ax is None or source_ax is None:
                self._hide_sticky_timeline()
                return

            ax.clear()
            pos = source_ax.get_position()
            ax.set_position([float(pos.x0), 0.52, float(pos.width), 0.20])
            x0, x1 = source_ax.get_xlim()
            ax.set_xlim((x0, x1))

            data_start = None
            data_end = None
            formatter = None
            try:
                formatter = source_ax.xaxis.get_major_formatter()
            except Exception:
                formatter = None

            try:
                for line in list(source_ax.get_lines() or []):
                    try:
                        xdata = np.asarray(line.get_xdata(orig=False), dtype=float)
                    except Exception:
                        continue
                    finite = xdata[np.isfinite(xdata)]
                    if finite.size == 0:
                        continue
                    cur_start = float(finite.min())
                    cur_end = float(finite.max())
                    data_start = cur_start if data_start is None else min(data_start, cur_start)
                    data_end = cur_end if data_end is None else max(data_end, cur_end)
            except Exception:
                pass

            range_start = float(data_start) if data_start is not None else float(x0)
            range_end = float(data_end) if data_end is not None else float(x1)
            if range_end < range_start:
                range_start, range_end = range_end, range_start

            try:
                width_px = float(getattr(source_ax, "bbox", None).width)
            except Exception:
                width_px = 0.0
            label_count = max(2, min(6, int(max(2.0, round(width_px / 180.0))) + 1))

            if abs(range_end - range_start) <= 1e-12:
                ticks = [float(range_start)]
            else:
                ticks = [float(t) for t in np.linspace(range_start, range_end, num=label_count)]

            labels = []
            try:
                labels = [str(formatter(t, i) or "") for i, t in enumerate(ticks)] if formatter is not None else [""] * len(ticks)
            except Exception:
                labels = [""] * len(ticks)

            try:
                dedup_ticks: list[float] = []
                dedup_labels: list[str] = []
                last_label = None
                for tick, label in zip(ticks, labels):
                    text = str(label or "")
                    if last_label is not None and text == last_label and len(ticks) > 1:
                        continue
                    dedup_ticks.append(float(tick))
                    dedup_labels.append(text)
                    last_label = text
                ticks = dedup_ticks
                labels = dedup_labels
            except Exception:
                pass

            minor_ticks = []
            try:
                if len(ticks) >= 2:
                    for left_tick, right_tick in zip(ticks[:-1], ticks[1:]):
                        step = (float(right_tick) - float(left_tick)) / 4.0
                        if step <= 0:
                            continue
                        minor_ticks.extend(
                            float(left_tick) + step * part
                            for part in (1.0, 2.0, 3.0)
                        )
            except Exception:
                minor_ticks = []

            try:
                bg = str(self._preview_theme.get("figure_bg", "#121212"))
            except Exception:
                bg = "#121212"
            try:
                fg = str(self._preview_theme.get("secondary_text", "#BDBDBD"))
            except Exception:
                fg = "#BDBDBD"
            try:
                grid = str(self._preview_theme.get("grid", "#3A3A3A"))
            except Exception:
                grid = "#3A3A3A"

            fig.set_facecolor(bg)
            try:
                fig.lines.clear()
                fig.lines.append(
                    Line2D([0.0, 1.0], [0.72, 0.72], transform=fig.transFigure, color=grid, linewidth=0.8)
                )
            except Exception:
                pass
            ax.set_facecolor(bg)
            ax.set_xticks(ticks)
            ax.set_xticklabels([])
            ax.set_xticks(minor_ticks, minor=True)
            ax.set_yticks([])
            ax.tick_params(
                axis="x",
                which="major",
                colors=fg,
                labelsize=10,
                top=True,
                bottom=False,
                labeltop=False,
                labelbottom=False,
                direction="in",
                length=6,
                width=0.8,
                pad=0,
            )
            ax.tick_params(
                axis="x",
                which="minor",
                colors=grid,
                top=True,
                bottom=False,
                direction="in",
                length=3,
                width=0.6,
            )
            ax.tick_params(axis="y", left=False, labelleft=False)
            try:
                ax.xaxis.set_label_position("bottom")
            except Exception:
                pass
            for spine_name, spine in ax.spines.items():
                try:
                    spine.set_visible(spine_name == "top")
                    if spine_name == "top":
                        spine.set_color(grid)
                        spine.set_linewidth(0.8)
                        spine.set_linestyle("-")
                except Exception:
                    pass

            try:
                xtrans = ax.get_xaxis_transform()
                for tick, label in zip(ticks, labels):
                    text = str(label or "").strip()
                    if not text:
                        continue
                    ax.text(
                        float(tick),
                        -0.95,
                        text,
                        transform=xtrans,
                        ha="center",
                        va="top",
                        color=fg,
                        fontsize=10,
                        clip_on=False,
                    )
            except Exception:
                pass

            canvas.show()
            canvas.draw_idle()
        except Exception:
            self._hide_sticky_timeline()

    def _sync_preview_canvas_scroll_height(self, axis_count: int | None = None) -> None:
        try:
            if self._preview_canvas is None:
                return

            if axis_count is None:
                if getattr(self, "_compare_mode", False):
                    axis_count = int(len(getattr(self, "_compare_axes", []) or []))
                elif getattr(self, "_single_mode_multi_axis", False):
                    axis_count = int(len(getattr(self, "_single_axes", []) or []))
                else:
                    axis_count = 1

            axis_count = max(1, int(axis_count or 1))
            if axis_count <= 0:
                self._restore_preview_canvas_size_policy()
                self._hide_sticky_timeline()
                return

            viewport_h = 0
            try:
                viewport = getattr(self, "_preview_scroll_viewport", None)
                if viewport is not None:
                    viewport_h = int(viewport.height() or 0)
            except Exception:
                viewport_h = 0

            if viewport_h <= 0:
                try:
                    viewport_h = int(self._preview_canvas.parentWidget().height() or 0)
                except Exception:
                    viewport_h = 0

            viewport_h = max(320, viewport_h)
            axis_height = self._preview_axis_slot_height_px()
            if axis_count == 1:
                target_h = max(viewport_h, axis_height)
            else:
                target_h = max(viewport_h, axis_height * axis_count)

            self._preview_canvas.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            self._preview_canvas.setMinimumHeight(target_h)
            self._preview_canvas.setMaximumHeight(target_h)
        except Exception:
            pass

    def _forward_preview_wheel_to_scroll_area(self, event) -> bool:
        try:
            scroll_area = getattr(self, "_preview_scroll_area", None)
            if scroll_area is None:
                return False

            bar = scroll_area.verticalScrollBar()
            if bar is None or int(bar.maximum() or 0) <= int(bar.minimum() or 0):
                return False

            delta = 0
            try:
                delta = int(event.angleDelta().y())
            except Exception:
                delta = 0
            if delta == 0:
                try:
                    delta = int(event.pixelDelta().y())
                except Exception:
                    delta = 0
            if delta == 0:
                return False

            single_step = max(20, int(bar.singleStep() or 20))
            steps = float(delta) / 120.0 if abs(delta) >= 120 else (1.0 if delta > 0 else -1.0)
            bar.setValue(int(bar.value() - round(steps * single_step * 3)))
            try:
                event.accept()
            except Exception:
                pass
            return True
        except Exception:
            return False

    def preview_path(self, fpath: str) -> None:
        try:
            p = Path(fpath)
            self._set_preview_header_path(p)
            if is_csv_file(p) and self._preview_canvas is not None:
                # Fast re-show: if the same CSV is already plotted, just
                # make the canvas visible and refresh the header/layout.
                if self._preview_csv_path == str(p) and (
                    self._preview_df is not None
                    or getattr(self, "_single_mode_multi_axis", False)
                ):
                    try:
                        self._preview_label.clear()
                        self._preview_label.hide()
                    except Exception:
                        pass
                    try:
                        self._preview_canvas.show()
                    except Exception:
                        pass
                    self._sync_preview_header_controls()
                    self._preview_relayout_and_redraw()
                    return
                self._exit_compare_mode()
                self._plot_run_csv(str(p))
                return

            self._exit_compare_mode()

            if is_image_file(p):
                if self._preview_canvas is not None:
                    try:
                        self._preview_canvas.hide()
                    except Exception:
                        pass
                self._hide_sticky_timeline()
                self._hide_qt_tooltip()
                self._sync_preview_header_controls()
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
        self._restore_preview_canvas_size_policy()
        self._hide_sticky_timeline()
        self._hide_qt_tooltip()
        self._set_preview_header_path(None)
        self._sync_preview_header_controls()
        self._preview_label.clear()
        self._preview_label.show()

    def preview_folder(self, folder: str) -> None:
        try:
            # Compare results: render multi-sensor compare view
            try:
                mp = Path(folder) / "compare_manifest.json"
                if mp.exists() and mp.is_file():
                    # Fast re-show: if this compare manifest is already plotted,
                    # just make the canvas visible and refresh the header/layout.
                    if (
                        getattr(self, "_compare_mode", False)
                        and getattr(self, "_compare_manifest_path", None) is not None
                        and Path(self._compare_manifest_path).resolve() == mp.resolve()
                        and getattr(self, "_compare_axes", None)
                    ):
                        self._set_preview_header_path(mp)
                        try:
                            self._preview_label.clear()
                            self._preview_label.hide()
                        except Exception:
                            pass
                        try:
                            self._preview_canvas.show()
                        except Exception:
                            pass
                        self._sync_preview_header_controls()
                        self._preview_relayout_and_redraw()
                        return
                    self._set_preview_header_path(mp)
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
                self._set_preview_header_path(None)
                self._sync_preview_header_controls()
                self._preview_label.clear()
                return

            self.preview_path(str(pick))
            return
        except Exception:
            self._hide_qt_tooltip()
            self._set_preview_header_path(None)
            self._preview_label.clear()

    # ---------------------------------------------------------------------
    # Event filter
    # ---------------------------------------------------------------------
    def eventFilter(self, obj, event):
        try:
            if obj is getattr(self, "_preview_canvas", None) and event is not None and event.type() == QEvent.Wheel:
                if self._forward_preview_wheel_to_scroll_area(event):
                    return True
        except Exception:
            pass

        try:
            title_label = getattr(self, "_preview_header_title_label", None)
            subtitle_label = getattr(self, "_preview_header_subtitle_label", None)
            header_widgets = {
                getattr(self, "_preview_header_widget", None),
                title_label,
                subtitle_label,
            }

            if obj in {title_label, subtitle_label} and event is not None:
                if event.type() == QEvent.ToolTip:
                    try:
                        QToolTip.hideText()
                    except Exception:
                        pass
                    global_pos = None
                    try:
                        global_pos = event.globalPos()
                    except Exception:
                        pass
                    self._show_preview_header_tooltip(obj, global_pos=global_pos)
                    try:
                        event.accept()
                    except Exception:
                        pass
                    return True

                if event.type() in (QEvent.Leave, QEvent.Hide, QEvent.MouseButtonPress):
                    if obj is getattr(self, "_preview_header_tt_source", None):
                        self._hide_preview_header_tooltip()

            if obj in header_widgets and event is not None and event.type() in (QEvent.Resize, QEvent.Show):
                self._sync_preview_header_controls()

            if obj is getattr(self, "_preview_header_widget", None) and event is not None and event.type() in (QEvent.Hide, QEvent.Leave):
                self._hide_preview_header_tooltip()
        except Exception:
            pass

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

                    # Delta toggle bbox + positioning (left of Legend & stats)
                    try:
                        if self._delta_btn_text is not None:
                            # If we have the LS bbox, position the delta button by pixel offset.
                            if self._ls_btn_bbox is not None:
                                gap_px = 14.0
                                desired_right_x = float(self._ls_btn_bbox.x0) - float(gap_px)

                                ax_btn = getattr(self._delta_btn_text, "axes", None)
                                if ax_btn is None:
                                    ax_btn = (self._single_axes or [None])[0] or self._preview_ax

                                try:
                                    _px, _py = self._delta_btn_text.get_position()
                                    y_axes = float(_py)
                                except Exception:
                                    y_axes = 1.02

                                try:
                                    _x0_disp, y0_disp = ax_btn.transAxes.transform((0.995, y_axes))
                                    new_axes_x, _ = ax_btn.transAxes.inverted().transform((desired_right_x, y0_disp))
                                    new_axes_x = float(max(0.02, min(float(new_axes_x), 0.98)))
                                    self._delta_btn_text.set_position((new_axes_x, y_axes))
                                except Exception:
                                    pass

                            # Cache bbox; if still overlapping, nudge left once.
                            self._delta_btn_bbox = self._delta_btn_text.get_window_extent(renderer)
                            try:
                                if self._ls_btn_bbox is not None and self._delta_btn_bbox is not None:
                                    max_right = float(self._ls_btn_bbox.x0) - 10.0
                                    if float(self._delta_btn_bbox.x1) > max_right:
                                        # shift left by overlap in display coords
                                        shift = float(self._delta_btn_bbox.x1) - max_right
                                        ax_btn = getattr(self._delta_btn_text, "axes", None) or (self._single_axes or [None])[0] or self._preview_ax
                                        _px, _py = self._delta_btn_text.get_position()
                                        x_disp, y_disp = ax_btn.transAxes.transform((float(_px), float(_py)))
                                        new_axes_x2, _ = ax_btn.transAxes.inverted().transform((float(x_disp) - shift, float(y_disp)))
                                        new_axes_x2 = float(max(0.02, min(float(new_axes_x2), 0.98)))
                                        self._delta_btn_text.set_position((new_axes_x2, float(_py)))
                                        self._delta_btn_bbox = self._delta_btn_text.get_window_extent(renderer)
                            except Exception:
                                pass
                        else:
                            self._delta_btn_bbox = None
                    except Exception:
                        self._delta_btn_bbox = None

                    # Zero-Y toggle bbox + positioning (left of ΔT)
                    try:
                        if self._zero_btn_text is not None:
                            if self._delta_btn_bbox is not None:
                                gap_px = 18.0
                                desired_right_x = float(self._delta_btn_bbox.x0) - float(gap_px)

                                ax_btn = getattr(self._zero_btn_text, "axes", None)
                                if ax_btn is None:
                                    ax_btn = (self._single_axes or [None])[0] or self._preview_ax

                                try:
                                    _px, _py = self._zero_btn_text.get_position()
                                    y_axes = float(_py)
                                except Exception:
                                    y_axes = 1.02

                                try:
                                    _x0_disp, y0_disp = ax_btn.transAxes.transform((0.995, y_axes))
                                    new_axes_x, _ = ax_btn.transAxes.inverted().transform((desired_right_x, y0_disp))
                                    new_axes_x = float(max(0.02, min(float(new_axes_x), 0.98)))
                                    self._zero_btn_text.set_position((new_axes_x, y_axes))
                                except Exception:
                                    pass

                            self._zero_btn_bbox = self._zero_btn_text.get_window_extent(renderer)
                            try:
                                if self._delta_btn_bbox is not None and self._zero_btn_bbox is not None:
                                    max_right = float(self._delta_btn_bbox.x0) - 14.0
                                    if float(self._zero_btn_bbox.x1) > max_right:
                                        shift = float(self._zero_btn_bbox.x1) - max_right
                                        ax_btn = getattr(self._zero_btn_text, "axes", None) or (self._single_axes or [None])[0] or self._preview_ax
                                        _px, _py = self._zero_btn_text.get_position()
                                        x_disp, y_disp = ax_btn.transAxes.transform((float(_px), float(_py)))
                                        new_axes_x2, _ = ax_btn.transAxes.inverted().transform((float(x_disp) - shift, float(y_disp)))
                                        new_axes_x2 = float(max(0.02, min(float(new_axes_x2), 0.98)))
                                        self._zero_btn_text.set_position((new_axes_x2, float(_py)))
                                        self._zero_btn_bbox = self._zero_btn_text.get_window_extent(renderer)
                            except Exception:
                                pass
                        else:
                            self._zero_btn_bbox = None
                    except Exception:
                        self._zero_btn_bbox = None

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

    def _is_over_delta_button(self, qt_x: int, qt_y: int) -> bool:
        return is_over_button_bbox(
            canvas=self._preview_canvas,
            btn_bbox=self._delta_btn_bbox,
            qt_x=qt_x,
            qt_y=qt_y,
        )

    def _is_over_zero_y_button(self, qt_x: int, qt_y: int) -> bool:
        return is_over_button_bbox(
            canvas=self._preview_canvas,
            btn_bbox=self._zero_btn_bbox,
            qt_x=qt_x,
            qt_y=qt_y,
        )

    def _delta_toggle_is_enabled(self) -> bool:
        try:
            return bool(getattr(self, "_delta_toggle_enabled", False))
        except Exception:
            return False


    def _set_delta_toggle_enabled(self, enabled: bool) -> None:
        try:
            self._delta_toggle_enabled = bool(enabled)
            if not self._delta_toggle_enabled:
                self._temp_delta_mode = False
        except Exception:
            pass

        try:
            self._update_delta_button_visual()
        except Exception:
            pass
        try:
            self._sync_preview_header_controls()
        except Exception:
            pass


    def _has_non_ambient_temperature_series(self, df: Optional[pd.DataFrame]) -> bool:
        try:
            if not isinstance(df, pd.DataFrame) or df.empty:
                return False

            temp_idxs = [int(i) for i in (self._temperature_column_indices(df) or [])]
            if not temp_idxs:
                return False

            amb_col = self._find_ambient_col(df)
            amb_idx_set = set()
            if amb_col:
                amb_idx_set = {
                    int(i) for i, c in enumerate(list(df.columns))
                    if str(c) == str(amb_col)
                }

            return any(i not in amb_idx_set for i in temp_idxs)
        except Exception:
            return False


    def _has_logged_ambient_for_df(
        self,
        df_raw: Optional[pd.DataFrame],
        *,
        run_dir: Optional[Path] = None,
    ) -> bool:
        """
        Logged ambient only.
        Do not count avg_temperature.json as logged ambient.
        """
        try:
            if isinstance(df_raw, pd.DataFrame) and not df_raw.empty:
                amb_col = self._find_ambient_col(df_raw)
                if amb_col and amb_col in df_raw.columns:
                    try:
                        ser = pd.to_numeric(df_raw[amb_col], errors="coerce")
                        if bool(ser.notna().any()):
                            return True
                    except Exception:
                        pass

            if run_dir is None:
                try:
                    p = getattr(self, "_preview_csv_path", None)
                    if p:
                        run_dir = Path(str(p)).parent
                except Exception:
                    run_dir = None

            if run_dir is None:
                return False

            aw = Path(run_dir) / "ambient_window.csv"
            if not aw.exists() or not aw.is_file():
                return False

            try:
                amb_df = pd.read_csv(str(aw), header=0)
            except Exception:
                return False

            if not isinstance(amb_df, pd.DataFrame) or amb_df.empty:
                return False

            cols = {str(c).strip().lower(): str(c) for c in list(amb_df.columns)}
            v_col = cols.get("ambient_c") or cols.get("ambient") or cols.get("value")
            if not v_col:
                return False

            try:
                ser = pd.to_numeric(amb_df[v_col], errors="coerce")
                return bool(ser.notna().any())
            except Exception:
                return False
        except Exception:
            return False


    def _delta_toggle_available_for_df(
        self,
        df_raw: Optional[pd.DataFrame],
        *,
        run_dir: Optional[Path] = None,
    ) -> bool:
        try:
            return bool(
                self._has_non_ambient_temperature_series(df_raw)
                and self._has_logged_ambient_for_df(df_raw, run_dir=run_dir)
            )
        except Exception:
            return False


    def _compare_manifest_delta_available(
        self,
        manifest_path: Path,
        manifest: Optional[dict] = None,
    ) -> bool:
        """
        Delta is only available in compare mode if every compared run has logged
        ambient data and at least one non-ambient temperature series.
        """
        try:
            m = manifest if isinstance(manifest, dict) else json.loads(
                manifest_path.read_text(encoding="utf-8")
            )
        except Exception:
            return False

        runs_rel = [str(r) for r in (m.get("runs") or []) if str(r).strip()]
        if not runs_rel:
            return False

        try:
            runs_root = manifest_path.parents[2]
        except Exception:
            runs_root = manifest_path.parent

        any_ok = False
        for rel in runs_rel:
            try:
                p = Path(*str(rel).replace("\\", "/").split("/"))
            except Exception:
                p = Path(str(rel))

            rd = runs_root / p
            csvp = rd / "run_window.csv"
            if not csvp.exists() or not csvp.is_file():
                return False

            try:
                df_all, _ = load_run_csv_dataframe(str(csvp))
            except Exception:
                return False

            if not self._delta_toggle_available_for_df(df_all, run_dir=rd):
                return False

            any_ok = True

        return any_ok

    def _ambient_col_for_current_result(self) -> Optional[str]:
        """Ambient column name for this result (from raw df when available)."""
        try:
            df = self._preview_df_all_raw if isinstance(getattr(self, "_preview_df_all_raw", None), pd.DataFrame) else self._preview_df_all
            return self._find_ambient_col(df) if isinstance(df, pd.DataFrame) else None
        except Exception:
            return None

    def _effective_available_cols(self) -> list[str]:
        """Available columns for UI/selection in the current display mode."""
        cols = [str(c) for c in (self._preview_available_cols or [])]
        if not getattr(self, "_temp_delta_mode", False):
            return cols
        amb = self._ambient_col_for_current_result()
        if amb:
            return [c for c in cols if c != amb]
        return cols

    def _effective_active_cols(self) -> list[str]:
        """Active columns for plotting/hover in the current display mode."""
        cols = [str(c) for c in (self._preview_active_cols or [])]
        if not getattr(self, "_temp_delta_mode", False):
            return cols
        amb = self._ambient_col_for_current_result()
        if amb:
            return [c for c in cols if c != amb]
        return cols

    def _normalized_display_unit(self, unit: str) -> str:
        try:
            u = str(unit or "").strip()
            if not u:
                return ""

            key = u.lower()
            aliases = {
                "°c": "°C",
                "degc": "°C",
                "c": "°C",
                "w": "W",
                "%": "%",
                "rpm": "RPM",
                "mb/s": "MB/s",
                "mb": "MB",
                "gb": "GB",
                "mhz": "MHz",
                "ghz": "GHz",
                "v": "V",
                "a": "A",
            }
            return aliases.get(key, u)
        except Exception:
            return str(unit or "").strip()


    def _measurement_title_for_unit(self, unit: str, fallback: str = "") -> str:
        try:
            u = self._normalized_display_unit(unit)
            key = u.lower()

            label_map = {
                "°c": "Temperature",
                "w": "Power",
                "%": "Utilization",
                "rpm": "Fan Speed",
                "mb/s": "Transfer Rate",
                "mb": "Memory",
                "gb": "Memory",
                "mhz": "Clock",
                "ghz": "Clock",
                "v": "Voltage",
                "a": "Current",
            }

            label = str(label_map.get(key) or get_measurement_type_label(unit) or fallback or "Measurement").strip()
            if not u:
                return label

            if re.search(rf"\(\s*{re.escape(u)}\s*\)$", label, flags=re.IGNORECASE):
                return label

            return f"{label} ({u})"
        except Exception:
            base = str(fallback or get_measurement_type_label(unit) or "Measurement").strip()
            u = str(unit or "").strip()
            return f"{base} ({u})" if u else base

    def _legend_stats_popup_title(self) -> str:
        try:
            cols = [str(c) for c in (self._effective_available_cols() or []) if str(c)]
            if not cols:
                cols = [str(c) for c in (self._preview_available_cols or []) if str(c)]
            if not cols:
                return self._preview_infer_stats_title()

            groups = group_columns_by_unit(cols)
            if len(groups) == 1:
                unit = next(iter(groups.keys()))
                return self._measurement_title_for_unit(unit)

            return self._preview_infer_stats_title()
        except Exception:
            return self._preview_infer_stats_title()

    def _single_axis_measurement_label(self, cols: list[str] | None = None) -> str:
        try:
            probe_cols = [str(c) for c in (cols or self._effective_active_cols() or []) if str(c)]
            if not probe_cols:
                probe_cols = [str(c) for c in (self._preview_available_cols or []) if str(c)]
            if not probe_cols:
                return ""

            groups = group_columns_by_unit(probe_cols)
            if len(groups) == 1:
                unit = next(iter(groups.keys()))
                return self._measurement_title_for_unit(unit)

            first_col = str(probe_cols[0]).strip()
            if not first_col:
                return ""
            fallback_groups = group_columns_by_unit([first_col])
            if len(fallback_groups) == 1:
                unit = next(iter(fallback_groups.keys()))
                return self._measurement_title_for_unit(unit)
        except Exception:
            pass
        return ""

    def _update_single_axis_header(self) -> None:
        try:
            if self._preview_ax is None:
                return
            try:
                if getattr(self, "_single_header_text", None) is not None:
                    self._single_header_text.remove()
            except Exception:
                pass
            self._single_header_text = None

            label = str(self._single_axis_measurement_label() or "").strip()
            if not label:
                return

            self._single_header_text = self._preview_ax.text(
                0.0,
                float(getattr(self, "_preview_title_axes_y", 1.02)),
                label,
                transform=self._preview_ax.transAxes,
                ha="left",
                va="bottom",
                fontsize=int(getattr(self, "_preview_title_font_size", 11) or 11),
                color=self._preview_label_color(),
                zorder=3000,
                clip_on=False,
            )
        except Exception:
            pass

    def _hide_single_axis_top_gridline(self) -> None:
        try:
            if self._preview_ax is None:
                return
            gridlines = [gl for gl in list(self._preview_ax.get_ygridlines() or []) if gl is not None]
            if not gridlines:
                return

            top_line = None
            top_y = None
            for gl in gridlines:
                try:
                    ydata = gl.get_ydata(orig=False)
                    if ydata is None or len(ydata) == 0:
                        continue
                    yval = float(ydata[0])
                except Exception:
                    continue
                if top_y is None or yval > top_y:
                    top_y = yval
                    top_line = gl

            for gl in gridlines:
                try:
                    gl.set_visible(gl is not top_line)
                except Exception:
                    pass
        except Exception:
            pass

    def _apply_single_axis_header_chrome(self) -> None:
        try:
            if self._preview_ax is None:
                return
            try:
                self._preview_ax.spines["top"].set_visible(False)
            except Exception:
                pass
            self._hide_single_axis_top_gridline()
        except Exception:
            pass

    def _find_ambient_col(self, df: Optional[pd.DataFrame]) -> Optional[str]:
        try:
            if not isinstance(df, pd.DataFrame) or df.empty:
                return None
            if "Ambient [°C]" in df.columns:
                return "Ambient [°C]"
            candidates = [c for c in df.columns if "ambient" in str(c).lower()]
            if not candidates:
                return None

            def _score(col: str) -> tuple[int, int]:
                s = str(col).lower()
                score = 0
                if "ambient" in s:
                    score += 10
                if "°c" in s or "[°c]" in s or "(°c)" in s or "degc" in s:
                    score += 5
                if "temp" in s or "temperature" in s:
                    score += 2
                return (-score, len(s))

            candidates.sort(key=_score)
            return str(candidates[0])
        except Exception:
            return None

    def _temperature_column_indices(self, df: Optional[pd.DataFrame]) -> list[int]:
        """Return indices of columns that are temperature series.

        Uses per-column unit extraction so it stays correct even if the CSV has
        duplicate column names (pandas will treat duplicates specially).
        """
        try:
            if not isinstance(df, pd.DataFrame) or df.empty:
                return []

            # Micro-optimization: unit parsing can be relatively expensive and
            # this function is called frequently during navigation/replots.
            # Cache the most recent result keyed by the full column label list.
            try:
                cols_key = tuple(str(c) for c in list(df.columns))
                if getattr(self, "_temp_idx_cache_key", None) == cols_key:
                    cached = getattr(self, "_temp_idx_cache_val", None)
                    if isinstance(cached, list):
                        return list(cached)
            except Exception:
                cols_key = None

            out: list[int] = []
            for i, col in enumerate(list(df.columns)):
                try:
                    unit = extract_unit_from_column(str(col))
                    if str(get_measurement_type_label(unit)).strip().lower() == "temperature":
                        out.append(int(i))
                except Exception:
                    continue

            try:
                if cols_key is not None:
                    self._temp_idx_cache_key = cols_key
                    self._temp_idx_cache_val = list(out)
            except Exception:
                pass
            return out
        except Exception:
            return []

    def _build_display_df(self) -> Optional[pd.DataFrame]:
        """Return the dataframe used for plotting/hover in the current mode."""
        df_raw = self._preview_df_all_raw
        if not isinstance(df_raw, pd.DataFrame):
            return self._preview_df_all

        if not getattr(self, "_temp_delta_mode", False):
            return df_raw

        # Ensure we have an ambient series available for delta mode.
        # Many run_window.csv files may not include ambient unless it was merged
        # during capture; try to inject it from sidecar files when possible.
        try:
            amb_col = self._find_ambient_col(df_raw)
            if not amb_col:
                df_with_amb = self._ensure_ambient_series_for_delta(df_raw)
                if isinstance(df_with_amb, pd.DataFrame):
                    self._preview_df_all_raw = df_with_amb
                    df_raw = df_with_amb
                    amb_col = self._find_ambient_col(df_raw)
        except Exception:
            amb_col = self._find_ambient_col(df_raw)
        if not amb_col:
            return df_raw

        # Resolve the ambient column index robustly (handles duplicate names).
        try:
            amb_idxs = [
                i for i, c in enumerate(list(df_raw.columns)) if str(c) == str(amb_col)
            ]
        except Exception:
            amb_idxs = []
        if not amb_idxs:
            return df_raw

        try:
            if len(amb_idxs) == 1:
                amb_idx = int(amb_idxs[0])
            else:
                # Pick the column with the most numeric data.
                best_i = None
                best_n = -1
                for i in amb_idxs:
                    try:
                        s = pd.to_numeric(df_raw.iloc[:, int(i)], errors="coerce")
                        n = int(s.notna().sum())
                    except Exception:
                        n = -1
                    if n > best_n:
                        best_n = n
                        best_i = int(i)
                amb_idx = int(best_i) if best_i is not None else int(amb_idxs[0])
        except Exception:
            amb_idx = int(amb_idxs[0])

        try:
            amb = pd.to_numeric(df_raw.iloc[:, amb_idx], errors="coerce")
        except Exception:
            return df_raw

        # Find temperature series columns (by unit) and apply pointwise delta:
        #   y_disp(t) = y_raw(t) - ambient(t)
        # This is done by positional index so it remains correct even with
        # duplicate column names.
        temp_idxs = self._temperature_column_indices(df_raw)
        if not temp_idxs:
            return df_raw

        try:
            # IMPORTANT: df_disp must not share data with df_raw.
            # Delta mode must preserve the raw baseline so toggling ΔT off returns
            # absolute temperatures rather than leaving the plot unchanged.
            df_disp = df_raw.copy(deep=True)

            try:
                amb_arr = pd.to_numeric(df_raw.iloc[:, amb_idx], errors="coerce").to_numpy(dtype=float, copy=False)
            except Exception:
                amb_arr = np.asarray(pd.to_numeric(df_raw.iloc[:, amb_idx], errors="coerce").to_numpy(), dtype=float)

            temp_idxs2 = [int(i) for i in list(temp_idxs) if int(i) != int(amb_idx)]
            if not temp_idxs2:
                return df_disp

            # Vectorized delta for all temperature columns at once.
            try:
                y_mat = df_raw.iloc[:, temp_idxs2].to_numpy(dtype=float, copy=False)
            except Exception:
                # Fallback: per-column numeric coercion, then numpy.
                cols_temp = [df_raw.columns[int(i)] for i in temp_idxs2]
                tmp = df_raw.loc[:, cols_temp].apply(lambda s: pd.to_numeric(s, errors="coerce"))
                y_mat = np.asarray(tmp.to_numpy(), dtype=float)

            try:
                delta_mat = y_mat - amb_arr.reshape((-1, 1))
            except Exception:
                delta_mat = y_mat - amb_arr[:, None]

            # Avoid pandas dtype-incompatible in-place assignment warnings when
            # temperature columns are int and delta values are float.
            # Preserve duplicate column names by assigning by positional index.
            try:
                delta_mat = np.asarray(delta_mat, dtype=float)

                cols_orig = df_disp.columns
                df_disp.columns = range(int(df_disp.shape[1]))
                df_disp.loc[:, temp_idxs2] = delta_mat
                df_disp.columns = cols_orig
            except Exception:
                # Last-resort: assign columns one by one.
                try:
                    cols_orig = df_disp.columns
                    df_disp.columns = range(int(df_disp.shape[1]))
                    for j, col_i in enumerate(temp_idxs2):
                        try:
                            df_disp.loc[:, int(col_i)] = np.asarray(delta_mat[:, int(j)], dtype=float)
                        except Exception:
                            continue
                finally:
                    try:
                        df_disp.columns = cols_orig
                    except Exception:
                        pass

            return df_disp
        except Exception:
            return df_raw

    def _ensure_ambient_series_for_delta(self, df_raw: pd.DataFrame) -> Optional[pd.DataFrame]:
        """Best-effort: add an ambient temperature series to df_raw.

        Sources (in priority order):
        - ambient_window.csv (timeline, produced by cli/plot_hwinfo.py when ambient logging is enabled)
        - avg_temperature.json (constant baseline, manual average room temperature)
        """
        try:
            if not isinstance(df_raw, pd.DataFrame) or df_raw.empty:
                return None

            # If ambient is already present, nothing to do.
            try:
                if self._find_ambient_col(df_raw):
                    return df_raw
            except Exception:
                pass

            # Need a datetime index to merge timeline ambient.
            try:
                is_dt = (df_raw.index.dtype.kind == "M")
            except Exception:
                is_dt = False

            run_dir = None
            try:
                p = getattr(self, "_preview_csv_path", None)
                if p:
                    run_dir = Path(str(p)).parent
            except Exception:
                run_dir = None

            ambient_col_name = "Ambient [°C]"

            # ---- 1) Merge ambient_window.csv timeline ----
            try:
                if run_dir is not None:
                    aw = run_dir / "ambient_window.csv"
                    if aw.exists() and aw.is_file() and is_dt:
                        try:
                            amb_df = pd.read_csv(str(aw), header=0)
                        except Exception:
                            amb_df = None

                        if isinstance(amb_df, pd.DataFrame) and (not amb_df.empty):
                            cols = {str(c).strip().lower(): str(c) for c in list(amb_df.columns)}
                            dt_col = cols.get("dt") or cols.get("timestamp") or cols.get("time") or cols.get("datetime")
                            v_col = cols.get("ambient_c") or cols.get("ambient") or cols.get("value")
                            if dt_col and v_col:
                                amb_dt = pd.to_datetime(amb_df[dt_col].astype(str), errors="coerce")
                                amb_val = pd.to_numeric(amb_df[v_col], errors="coerce")
                                amb2 = pd.DataFrame({"dt": amb_dt, "ambient_c": amb_val}).dropna(subset=["dt"])
                                amb2 = amb2.sort_values("dt")
                                if not amb2.empty:
                                    left = pd.DataFrame({"_row": np.arange(len(df_raw), dtype=int), "dt": pd.to_datetime(df_raw.index)})
                                    left = left.dropna(subset=["dt"]).sort_values("dt")
                                    tol = pd.Timedelta(seconds=2)
                                    merged = pd.merge_asof(
                                        left,
                                        amb2[["dt", "ambient_c"]],
                                        on="dt",
                                        direction="nearest",
                                        tolerance=tol,
                                    )

                                    aligned = pd.Series(index=np.arange(len(df_raw), dtype=int), dtype="float64")
                                    try:
                                        aligned.loc[merged["_row"].to_numpy(dtype=int)] = merged["ambient_c"].to_numpy(dtype=float)
                                    except Exception:
                                        for _, r in merged.iterrows():
                                            try:
                                                aligned.loc[int(r["_row"])] = float(r["ambient_c"]) if pd.notna(r["ambient_c"]) else float("nan")
                                            except Exception:
                                                continue

                                    out = df_raw.copy(deep=False)
                                    out[ambient_col_name] = aligned.to_numpy(dtype=float, copy=False)
                                    return out
            except Exception:
                pass

            # ---- 2) Fallback: avg_temperature.json constant ----
            try:
                if run_dir is not None:
                    aj = run_dir / "avg_temperature.json"
                    if aj.exists() and aj.is_file():
                        try:
                            payload = json.loads(aj.read_text(encoding="utf-8", errors="ignore") or "{}")
                        except Exception:
                            payload = {}
                        try:
                            t0 = float(payload.get("manual_average_temperature"))
                        except Exception:
                            t0 = None

                        if t0 is not None and np.isfinite(t0):
                            out = df_raw.copy(deep=False)
                            out[ambient_col_name] = float(t0)
                            return out
            except Exception:
                pass

            return None
        except Exception:
            return None

    def _delta_display_is_applied(self) -> bool:
        """Best-effort sanity check that delta mode is actually applied to the display DF.

        This guards against navigation/refresh paths where the toggle state is preserved
        but a freshly loaded plot ends up using absolute values.
        """
        try:
            if not bool(getattr(self, "_temp_delta_mode", False)):
                return True

            df_raw = getattr(self, "_preview_df_all_raw", None)
            df_disp = getattr(self, "_preview_df_all", None)
            if not isinstance(df_raw, pd.DataFrame) or not isinstance(df_disp, pd.DataFrame):
                return True
            if df_raw.empty or df_disp.empty:
                return True

            amb_col = self._find_ambient_col(df_raw)
            if not amb_col:
                return True

            # Resolve ambient index.
            amb_idxs = [i for i, c in enumerate(list(df_raw.columns)) if str(c) == str(amb_col)]
            if not amb_idxs:
                return True
            amb_idx = int(amb_idxs[0])

            # Pick a non-ambient temperature column index.
            temp_idxs = [i for i in self._temperature_column_indices(df_raw) if int(i) != int(amb_idx)]
            if not temp_idxs:
                return True
            col_idx = int(temp_idxs[0])

            amb = pd.to_numeric(df_raw.iloc[:, amb_idx], errors="coerce")
            y_raw = pd.to_numeric(df_raw.iloc[:, col_idx], errors="coerce")

            # Find a row where both are finite.
            mask = np.isfinite(amb.to_numpy(dtype=float, copy=False)) & np.isfinite(y_raw.to_numpy(dtype=float, copy=False))
            if not mask.any():
                return True
            j = int(np.flatnonzero(mask)[0])

            expected = float(y_raw.iloc[j] - amb.iloc[j])
            try:
                actual = float(pd.to_numeric(df_disp.iloc[j, col_idx], errors="coerce"))
            except Exception:
                # If display DF doesn't have the same positional column, skip check.
                return True

            if not (np.isfinite(expected) and np.isfinite(actual)):
                return True
            return abs(expected - actual) <= 1e-6
        except Exception:
            return True

    def _update_delta_button_visual(self) -> None:
        try:
            if self._delta_btn_text is None:
                self._sync_preview_header_controls()
                return

            is_on = bool(getattr(self, "_temp_delta_mode", False))
            is_enabled = bool(self._delta_toggle_is_enabled())

            try:
                if not hasattr(self, "_delta_btn_default_color"):
                    self._delta_btn_default_color = self._delta_btn_text.get_color()
                if not hasattr(self, "_delta_btn_default_fontweight"):
                    self._delta_btn_default_fontweight = self._delta_btn_text.get_fontweight()
            except Exception:
                pass

            label = "ΔT" if (is_on or not is_enabled) else "T"
            try:
                self._delta_btn_text.set_text(label)
            except Exception:
                pass

            try:
                if not is_enabled:
                    if bool(getattr(self, "_theme_is_dark", True)):
                        fc = (0.22, 0.22, 0.22, 0.16)
                        ec = (0.55, 0.55, 0.55, 0.26)
                        tc = (1.0, 1.0, 1.0, 0.38)
                    else:
                        fc = (0.92, 0.92, 0.92, 0.70)
                        ec = (0.62, 0.62, 0.62, 0.55)
                        tc = (0.10, 0.10, 0.10, 0.45)

                    self._delta_btn_text.set_bbox(
                        dict(boxstyle="round,pad=0.35", fc=fc, ec=ec, lw=1.0)
                    )
                    try:
                        self._delta_btn_text.set_color(tc)
                        self._delta_btn_text.set_fontweight("normal")
                    except Exception:
                        pass

                elif is_on:
                    fc = self._preview_theme.get("button_active_fill", (0.25, 0.25, 0.25, 0.35))
                    ec = self._preview_theme.get("button_active_border", (0.55, 0.55, 0.55, 0.85))
                    self._delta_btn_text.set_bbox(
                        dict(boxstyle="round,pad=0.45", fc=fc, ec=ec, lw=1.6)
                    )
                    try:
                        self._delta_btn_text.set_color(
                            self._preview_theme.get("button_active_text", (1, 1, 1, 0.98))
                        )
                        self._delta_btn_text.set_fontweight("bold")
                    except Exception:
                        pass
                else:
                    self._delta_btn_text.set_bbox(
                        dict(boxstyle="round,pad=0.35", fc=(0, 0, 0, 0.0), ec=(0, 0, 0, 0.0))
                    )
                    try:
                        if hasattr(self, "_delta_btn_default_color"):
                            self._delta_btn_text.set_color(self._delta_btn_default_color)
                        if hasattr(self, "_delta_btn_default_fontweight"):
                            self._delta_btn_text.set_fontweight(self._delta_btn_default_fontweight)
                    except Exception:
                        pass
            except Exception:
                pass
        except Exception:
            pass

        try:
            self._sync_preview_header_controls()
        except Exception:
            pass

    def _update_zero_y_button_visual(self) -> None:
        try:
            if self._zero_btn_text is None:
                self._sync_preview_header_controls()
                return

            is_on = bool(getattr(self, "_zero_y_mode", False))

            # Cache defaults so we can restore them when toggling off.
            try:
                if not hasattr(self, "_zero_btn_default_color"):
                    self._zero_btn_default_color = self._zero_btn_text.get_color()
                if not hasattr(self, "_zero_btn_default_fontweight"):
                    self._zero_btn_default_fontweight = self._zero_btn_text.get_fontweight()
            except Exception:
                pass

            label = "0Y" if is_on else "AutoY"
            try:
                self._zero_btn_text.set_text(label)
            except Exception:
                pass

            try:
                if is_on:
                    fc = self._preview_theme.get("button_active_fill", (0.25, 0.25, 0.25, 0.35))
                    ec = self._preview_theme.get("button_active_border", (0.55, 0.55, 0.55, 0.85))
                    self._zero_btn_text.set_bbox(dict(boxstyle="round,pad=0.45", fc=fc, ec=ec, lw=1.6))
                    try:
                        self._zero_btn_text.set_color(self._preview_theme.get("button_active_text", (1, 1, 1, 0.98)))
                        self._zero_btn_text.set_fontweight("bold")
                    except Exception:
                        pass
                else:
                    self._zero_btn_text.set_bbox(
                        dict(boxstyle="round,pad=0.35", fc=(0, 0, 0, 0.0), ec=(0, 0, 0, 0.0))
                    )
                    try:
                        if hasattr(self, "_zero_btn_default_color"):
                            self._zero_btn_text.set_color(self._zero_btn_default_color)
                        if hasattr(self, "_zero_btn_default_fontweight"):
                            self._zero_btn_text.set_fontweight(self._zero_btn_default_fontweight)
                    except Exception:
                        pass
            except Exception:
                pass
        except Exception:
            pass
        try:
            self._sync_preview_header_controls()
        except Exception:
            pass

    def _replot_current_result_for_display_mode(self) -> None:
        """Rebuild the current plot (single-axis or multi-axis) for the current display mode."""
        try:
            if self._preview_canvas is None or self._preview_fig is None:
                return
            if self._preview_x is None:
                return
            if not isinstance(self._preview_df_all_raw, pd.DataFrame) or self._preview_df_all_raw.empty:
                return

            df_disp = self._build_display_df()
            if not isinstance(df_disp, pd.DataFrame):
                return

            self._preview_df_all = df_disp

            all_cols = list(self._effective_available_cols() if getattr(self, "_temp_delta_mode", False) else (self._preview_available_cols or []))
            active_set = set(self._effective_active_cols() if getattr(self, "_temp_delta_mode", False) else (self._preview_active_cols or []))
            all_groups = group_columns_by_unit(all_cols)

            # Filter groups to those with at least one active column.
            filtered_groups: dict[str, list[str]] = {}
            for unit, group_cols in (all_groups or {}).items():
                try:
                    active_in_group = [c for c in (group_cols or []) if c in active_set]
                    if active_in_group:
                        filtered_groups[unit] = list(group_cols)
                except Exception:
                    continue

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
            use_multi_axis = len(sorted_groups) > 1

            # Tear down existing multi-axis stack if needed.
            try:
                if getattr(self, "_single_mode_multi_axis", False):
                    self._exit_single_mode_multi_axis()
            except Exception:
                pass

            x_vals = np.asarray(self._preview_x, dtype=float)
            is_dt = bool(getattr(self, "_preview_is_dt", False))
            color_map = dict(getattr(self, "_preview_color_map", {}) or {})

            if use_multi_axis:
                self._plot_run_csv_multi_axis(df_disp, sorted_groups, x_vals, is_dt, color_map)
            else:
                self._plot_run_csv_single_axis(df_disp, list(all_cols), x_vals, is_dt, color_map)
        except Exception:
            pass

    def _handle_delta_click(self, qt_x: int, qt_y: int) -> bool:
        try:
            if not self._delta_toggle_is_enabled():
                return False

            try:
                if self._delta_btn_bbox is None and self._delta_btn_text is not None and self._preview_canvas is not None:
                    try:
                        self._preview_canvas.draw()
                    except Exception:
                        pass
                    try:
                        self._on_preview_draw()
                    except Exception:
                        pass
            except Exception:
                pass

            if not self._is_over_delta_button(qt_x, qt_y):
                return False

            return self.toggle_delta_mode()

        except Exception:
            return False

    def toggle_delta_mode(self) -> bool:
        if not self._delta_toggle_is_enabled():
            return False
        try:
            self._temp_delta_mode = not bool(getattr(self, "_temp_delta_mode", False))

            try:
                self._update_delta_button_visual()
            except Exception:
                pass

            try:
                if getattr(self, "_temp_delta_mode", False):
                    eff = self._effective_active_cols()
                    if not eff:
                        avail = self._effective_available_cols()
                        if avail:
                            self._preview_active_cols = [avail[0]]
            except Exception:
                pass

            try:
                self._close_legend_popup()
            except Exception:
                pass
            try:
                self._hide_preview_hover(hard=True)
            except Exception:
                pass
            try:
                if getattr(self, "_compare_mode", False):
                    self._hide_compare_hover_all()
            except Exception:
                pass
            try:
                if getattr(self, "_single_mode_multi_axis", False):
                    self._hide_single_hover_all()
            except Exception:
                pass

            # Preferred path: morph existing plotted artists.
            try:
                if self._apply_temp_delta_mode_to_current_plot():
                    return True
            except Exception:
                pass

            # Fallback: compare mode rebuild if subplot topology changed.
            try:
                if getattr(self, "_compare_mode", False):
                    mp = getattr(self, "_compare_manifest_path", None)
                    if mp is not None:
                        mp2 = Path(str(mp))
                        if mp2.exists() and mp2.is_file():
                            self._plot_compare_manifest(mp2)
                            return True
            except Exception:
                pass

            self._schedule_replot_current_result_for_display_mode(delay_ms=0)
            return True
        except Exception:
            return False

    def _preview_get_display_df_for_current_mode(self) -> Optional[pd.DataFrame]:
        """Return the current display dataframe (raw or cached delta) quickly."""
        df_raw = getattr(self, "_preview_df_all_raw", None)
        if not isinstance(df_raw, pd.DataFrame):
            return self._preview_df_all if isinstance(self._preview_df_all, pd.DataFrame) else None

        if not bool(getattr(self, "_temp_delta_mode", False)):
            return df_raw

        df_delta = getattr(self, "_preview_df_all_delta", None)
        if isinstance(df_delta, pd.DataFrame) and (not df_delta.empty):
            try:
                if list(df_delta.columns) == list(df_raw.columns) and len(df_delta) == len(df_raw):
                    return df_delta
            except Exception:
                pass

        # Build once and cache.
        try:
            df_delta2 = self._build_display_df()
            if isinstance(df_delta2, pd.DataFrame):
                self._preview_df_all_delta = df_delta2
                return df_delta2
        except Exception:
            pass

        return df_raw

    def _fast_refresh_hover_cache_values_only(self, df_active: pd.DataFrame) -> None:
        """Update hover caches without recomputing elapsed-time strings."""
        try:
            if df_active is None or not isinstance(df_active, pd.DataFrame):
                return

            try:
                self._preview_df_np = df_active.to_numpy(dtype=float, copy=False)
            except Exception:
                self._preview_df_np = np.asarray(df_active.to_numpy(), dtype=float)

            try:
                self._preview_cols_cached = [str(c) for c in list(df_active.columns)]
            except Exception:
                self._preview_cols_cached = []
            try:
                self._preview_colors_cached = [
                    str(self._preview_color_map.get(str(c), "#FFFFFF")) for c in self._preview_cols_cached
                ]
            except Exception:
                self._preview_colors_cached = ["#FFFFFF"] * len(self._preview_cols_cached)

            self._preview_last_tt_idx = None
        except Exception:
            pass

    def _apply_temp_delta_mode_to_current_plot(self) -> bool:
        """Apply current ΔT/T mode to the existing plot with a fast line morph."""
        try:
            if self._preview_canvas is None or self._preview_fig is None:
                return False
            if not isinstance(getattr(self, "_preview_df_all_raw", None), pd.DataFrame):
                return False

            # Compare mode: only morph if subplot topology stays identical.
            if getattr(self, "_compare_mode", False):
                try:
                    target_sensors = list(self._compare_target_sensor_list_for_current_mode())
                    current_sensors = list(getattr(self, "_compare_manifest_sensors", []) or [])
                    if target_sensors != current_sensors:
                        return False
                except Exception:
                    return False

                axis_payloads = []
                zero_mode = bool(getattr(self, "_zero_y_mode", False))

                for ax in list(getattr(self, "_compare_axes", []) or []):
                    st = (self._compare_axis_state or {}).get(ax)
                    if not st:
                        continue

                    target_df = st.get("df_delta") if bool(getattr(self, "_temp_delta_mode", False)) else st.get("df_raw")
                    if not isinstance(target_df, pd.DataFrame):
                        return False

                    line_payloads = []
                    ylim_arrays = []

                    for name, ln in list((st.get("lines") or {}).items()):
                        if name not in target_df.columns:
                            continue
                        try:
                            y1 = pd.to_numeric(target_df[name], errors="coerce").to_numpy(dtype=float)
                        except Exception:
                            continue

                        lp = self._prepare_line_for_morph(ln, y1, target_visible=True)
                        if lp is not None:
                            line_payloads.append(lp)
                        ylim_arrays.append(y1)

                        try:
                            (st.get("series_data") or {})[name] = y1
                        except Exception:
                            pass

                    ylim1 = self._collect_axis_target_ylim_from_arrays(ylim_arrays, zero_mode=zero_mode)
                    if ylim1 is None:
                        try:
                            ylim1 = tuple(ax.get_ylim())
                        except Exception:
                            continue

                    axis_payloads.append({
                        "ax": ax,
                        "lines": line_payloads,
                        "ylim0": tuple(ax.get_ylim()),
                        "ylim1": tuple(ylim1),  # snapped at finish
                        "animate_ylim": False,
                        "_target_df": target_df,
                    })

                if not axis_payloads:
                    return False

                def _finish():
                    try:
                        for ap in axis_payloads:
                            ax = ap.get("ax")
                            st = (self._compare_axis_state or {}).get(ax)
                            target_df = ap.get("_target_df")
                            if st is None or not isinstance(target_df, pd.DataFrame):
                                continue
                            st["df"] = target_df
                            try:
                                st["df_np"] = target_df.to_numpy(dtype=float, copy=False)
                            except Exception:
                                st["df_np"] = np.asarray(target_df.to_numpy(), dtype=float)
                    except Exception:
                        pass
                    try:
                        self._compare_last_idx = None
                        self._refresh_compare_backgrounds()
                    except Exception:
                        pass

                return self._start_plot_morph(axis_payloads, finish_callback=_finish, duration_s=0.10)

            df_disp = self._preview_get_display_df_for_current_mode()
            if not isinstance(df_disp, pd.DataFrame) or df_disp.empty:
                return False

            self._preview_df_all = df_disp

            if getattr(self, "_single_mode_multi_axis", False):
                active_set = set(self._effective_active_cols())
                zero_mode = bool(getattr(self, "_zero_y_mode", False))
                axis_payloads = []

                for ax, st in list((self._single_axis_state or {}).items()):
                    lines = (st or {}).get("lines") or {}
                    series_data = (st or {}).get("series_data") or {}

                    line_payloads = []
                    ylim_arrays = []

                    for name, ln in list(lines.items()):
                        if name not in df_disp.columns:
                            continue
                        try:
                            y1 = pd.to_numeric(df_disp[name], errors="coerce").to_numpy(dtype=float)
                        except Exception:
                            continue

                        vis1 = bool(name in active_set)
                        lp = self._prepare_line_for_morph(ln, y1, target_visible=vis1)
                        if lp is not None:
                            line_payloads.append(lp)

                        series_data[name] = y1
                        if vis1:
                            ylim_arrays.append(y1)

                    ylim1 = self._collect_axis_target_ylim_from_arrays(ylim_arrays, zero_mode=zero_mode)
                    if ylim1 is None:
                        try:
                            ylim1 = tuple(ax.get_ylim())
                        except Exception:
                            continue

                    axis_payloads.append({
                        "ax": ax,
                        "lines": line_payloads,
                        "ylim0": tuple(ax.get_ylim()),
                        "ylim1": tuple(ylim1),
                        "animate_ylim": False,
                    })

                if not axis_payloads:
                    return False

                def _finish():
                    try:
                        self._preview_df_all = df_disp
                        for ax, st in list((self._single_axis_state or {}).items()):
                            lines = (st or {}).get("lines") or {}
                            cols_all = [str(n) for n in list(lines.keys()) if str(n) in df_disp.columns]
                            active_cols = [c for c in cols_all if c in set(self._effective_active_cols())]

                            st["cols"] = list(active_cols)
                            st["colors"] = [str(self._preview_color_map.get(c, "#FFFFFF")) for c in active_cols]

                            if active_cols:
                                st["df"] = df_disp[active_cols].copy()
                                try:
                                    st["df_np"] = st["df"].to_numpy(dtype=float, copy=False)
                                except Exception:
                                    st["df_np"] = np.asarray(st["df"].to_numpy(), dtype=float)
                            else:
                                st["df"] = df_disp.iloc[:, 0:0].copy()
                                st["df_np"] = np.zeros((int(len(self._preview_x or [])), 0), dtype=float)
                    except Exception:
                        pass
                    try:
                        self._single_last_idx = None
                        self._refresh_single_backgrounds()
                    except Exception:
                        pass

                return self._start_plot_morph(axis_payloads, finish_callback=_finish, duration_s=0.10)

            # Single axis
            if not getattr(self, "_preview_lines", None):
                return False

            amb = self._ambient_col_for_current_result()
            active_set = set(self._effective_active_cols())
            zero_mode = bool(getattr(self, "_zero_y_mode", False))

            line_payloads = []
            ylim_arrays = []

            for name, ln in list(self._preview_lines.items()):
                if name not in df_disp.columns:
                    continue

                try:
                    y1 = pd.to_numeric(df_disp[name], errors="coerce").to_numpy(dtype=float)
                except Exception:
                    continue

                vis1 = bool(name in active_set)
                if amb and name == amb and bool(getattr(self, "_temp_delta_mode", False)):
                    vis1 = False

                lp = self._prepare_line_for_morph(ln, y1, target_visible=vis1)
                if lp is not None:
                    line_payloads.append(lp)

                try:
                    self._preview_series_data[name] = y1
                except Exception:
                    pass

                if vis1:
                    ylim_arrays.append(y1)

            ylim1 = self._collect_axis_target_ylim_from_arrays(ylim_arrays, zero_mode=zero_mode)
            if ylim1 is None:
                try:
                    ylim1 = tuple(self._preview_ax.get_ylim())
                except Exception:
                    return False

            try:
                df_hover = df_disp[list(self._effective_active_cols())]
            except Exception:
                df_hover = df_disp

            def _finish():
                try:
                    self._preview_df_all = df_disp
                    self._preview_df = df_hover
                    self._preview_colors = [
                        self._preview_color_map.get(c, "#FFFFFF")
                        for c in list(self._effective_active_cols())
                    ]
                    self._fast_refresh_hover_cache_values_only(self._preview_df)
                    self._preview_last_tt_idx = None
                    self._preview_invalidate_interaction_cache()
                    try:
                        self._on_preview_draw()
                    except Exception:
                        pass
                except Exception:
                    pass

            return self._start_plot_morph(
                [{
                    "ax": self._preview_ax,
                    "lines": line_payloads,
                    "ylim0": tuple(self._preview_ax.get_ylim()),
                    "ylim1": tuple(ylim1),
                    "animate_ylim": False,
                }],
                finish_callback=_finish,
                duration_s=0.10,
            )
        except Exception:
            return False

    def _handle_zero_y_click(self, qt_x: int, qt_y: int) -> bool:
        try:
            # After certain navigation/replot paths, the cached bbox may not be ready yet.
            # Refresh it once on click so the button remains usable.
            try:
                if self._zero_btn_bbox is None and self._zero_btn_text is not None and self._preview_canvas is not None:
                    try:
                        self._preview_canvas.draw()
                    except Exception:
                        pass
                    try:
                        self._on_preview_draw()
                    except Exception:
                        pass
            except Exception:
                pass

            if not self._is_over_zero_y_button(qt_x, qt_y):
                return False

            return self.toggle_zero_y_mode()

        except Exception:
            return False

    def toggle_zero_y_mode(self) -> bool:
        try:
            self._zero_y_mode = not bool(getattr(self, "_zero_y_mode", False))

            try:
                self._update_zero_y_button_visual()
            except Exception:
                pass

            try:
                self._close_legend_popup()
            except Exception:
                pass
            try:
                self._hide_preview_hover(hard=True)
            except Exception:
                pass
            try:
                if getattr(self, "_compare_mode", False):
                    self._hide_compare_hover_all()
            except Exception:
                pass
            try:
                if getattr(self, "_single_mode_multi_axis", False):
                    self._hide_single_hover_all()
            except Exception:
                pass

            self._apply_zero_y_mode_to_current_plot()
            return True
        except Exception:
            return False

    def _schedule_replot_current_result_for_display_mode(
        self,
        *,
        delay_ms: int = 0,
        transition_before: Optional[QPixmap] = None,
    ) -> None:
        """Debounce heavy replots so repeated toggles collapse and UI can repaint first."""
        try:
            if transition_before is not None:
                self._pending_replot_transition_pixmap = transition_before

            if self._replot_timer is None:
                t = QTimer(self.parent)
                t.setSingleShot(True)
                try:
                    t.setTimerType(Qt.PreciseTimer)
                except Exception:
                    pass
                t.timeout.connect(self._replot_current_result_for_display_mode_debounced)
                self._replot_timer = t
            self._replot_timer.start(max(0, int(delay_ms)))
        except Exception:
            try:
                self._replot_current_result_for_display_mode()
                self._commit_graph_transition(transition_before)
            except Exception:
                pass

    def _replot_current_result_for_display_mode_debounced(self) -> None:
        before = getattr(self, "_pending_replot_transition_pixmap", None)
        self._pending_replot_transition_pixmap = None

        try:
            if bool(getattr(self, "_replot_in_progress", False)):
                return
            self._replot_in_progress = True
            try:
                self._replot_current_result_for_display_mode()
                self._commit_graph_transition(before)
            finally:
                self._replot_in_progress = False
        except Exception:
            try:
                self._replot_in_progress = False
            except Exception:
                pass

    def _apply_zero_y_mode_to_current_plot(self) -> None:
        """Apply current zero-y mode with a lightweight ylim-only tween."""
        try:
            if self._preview_canvas is None:
                return

            try:
                if self._animate_current_ylims_only():
                    return
            except Exception:
                pass

            # Fallback
            if getattr(self, "_single_mode_multi_axis", False):
                self._single_apply_active_series()
                return

            if getattr(self, "_compare_mode", False):
                mp = getattr(self, "_compare_manifest_path", None)
                if mp is not None:
                    mp2 = Path(str(mp))
                    if mp2.exists() and mp2.is_file():
                        self._plot_compare_manifest(mp2)
                        return

            self._preview_autoscale_y_to_active()
            self._preview_relayout_and_redraw()
            self._preview_canvas.draw_idle()
        except Exception:
            try:
                if self._preview_canvas is not None:
                    self._preview_canvas.draw_idle()
            except Exception:
                pass

    def _handle_ls_click(self, qt_x: int, qt_y: int) -> bool:
        if not self._is_over_ls_button(qt_x, qt_y):
            return False

        return self.toggle_legend_popup()

    def toggle_legend_popup(self) -> bool:
        try:

            # Compare mode: show per-run stats tables side-by-side.
            if getattr(self, "_compare_mode", False):
                if not (getattr(self, "_compare_manifest_sensors", None) or []):
                    return True
                try:
                    self._open_compare_legend_popup()
                except Exception:
                    pass
                return True

            if not self._preview_available_cols:
                return True

            try:
                self._open_legend_popup()
            except Exception:
                pass

            return True
        except Exception:
            return False

    def _close_legend_popup(self) -> None:
        try:
            if self._legend_popup is not None and self._legend_popup.isVisible():
                self._legend_popup.close()
        except Exception:
            pass
        try:
            if self._compare_legend_popup is not None and self._compare_legend_popup.isVisible():
                self._compare_legend_popup.close()
        except Exception:
            pass
        self._set_dimmed(False)
        self._legend_popup = None
        self._compare_legend_popup = None

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
            try:
                if bool(getattr(self, "_plot_morph_lock_interaction", False)) or self._plot_morph_timer.isActive():
                    return
            except Exception:
                pass

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
        try:
            if self._preview_canvas is None or self._preview_ax is None:
                return
            if not self._preview_canvas.isVisible():
                return

            # Single draw to obtain a renderer for measurement.
            self._preview_canvas.draw()
            renderer = self._preview_canvas.get_renderer()
            if renderer is None:
                return

            # Measure tick-label width and adjust left margin.
            left_pad_px = int(getattr(self, "_preview_left_tick_pad_px", 3) or 3)
            try:
                right_frac = float(self._preview_effective_right_frac())
            except Exception:
                right_frac = float(getattr(self, "_preview_right_frac", 0.995) or 0.995)
            left_px = self._preview_required_left_margin_px(renderer, pad_px=left_pad_px)
            self._preview_apply_axes_rect(right_frac=right_frac, left_margin_px=left_px)
            self._preview_invalidate_interaction_cache()

            # Additional single-slot layout adjustment.
            self._sync_preview_canvas_scroll_height(1)
            self._apply_single_slot_axis_layout(self._preview_ax)

            # One final draw to render the corrected layout.
            self._preview_canvas.draw()
            try:
                self._on_preview_draw()
            except Exception:
                pass
            self._update_sticky_timeline(self._preview_ax)
        except Exception:
            self._hide_sticky_timeline()

    def _compare_relayout_and_redraw(self) -> None:
        """Relayout all compare subplots on resize/show."""
        try:
            if self._preview_canvas is None or self._preview_fig is None:
                return
            if not self._preview_canvas.isVisible():
                return
            if not getattr(self, "_compare_mode", False) or not getattr(self, "_compare_axes", None):
                return

            self._sync_preview_canvas_scroll_height(len(getattr(self, "_compare_axes", []) or []))

            self._preview_canvas.draw()
            renderer = self._preview_canvas.get_renderer()
            if renderer is None:
                return

            # Compute a left margin that satisfies all subplots' tick labels.
            left_px = float(getattr(self, "_preview_left_margin_px_base", 60) or 60)
            for ax in list(self._compare_axes):
                try:
                    self._preview_ax = ax
                    left_px = max(
                        left_px,
                        float(
                            self._preview_required_left_margin_px(
                                renderer,
                                pad_px=int(getattr(self, "_preview_left_tick_pad_px", 3) or 3),
                            )
                        ),
                    )
                except Exception:
                    continue

            try:
                fig_w_px = float(self._preview_fig.get_figwidth() * self._preview_fig.dpi)
                left = (left_px / fig_w_px) if fig_w_px > 1 else 0.08
                left = max(0.02, min(left, 0.35))
                top, bottom = self._preview_effective_vertical_fracs()

                self._preview_fig.subplots_adjust(
                    left=left,
                    right=float(self._preview_effective_right_frac()),
                    top=float(top),
                    bottom=float(bottom),
                    hspace=float(getattr(self, "_preview_stack_hspace", 0.35)),
                )
            except Exception:
                pass

            try:
                self._preview_invalidate_interaction_cache()
            except Exception:
                pass

            if len(getattr(self, "_compare_axes", []) or []) == 1:
                try:
                    ax0 = list(getattr(self, "_compare_axes", []) or [None])[0]
                    self._apply_single_slot_axis_layout(ax0)
                    self._preview_ax = ax0
                    self._hide_single_axis_top_gridline()
                except Exception:
                    pass

            self._preview_canvas.draw()
            self._refresh_compare_backgrounds()
            try:
                axes = list(getattr(self, "_compare_axes", []) or [])
                self._update_sticky_timeline(axes[-1] if axes else None)
            except Exception:
                self._hide_sticky_timeline()
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

    def _preview_stats_from_raw_df(self) -> dict[str, tuple[float, float, float]]:
        df = self._preview_df_all_raw if isinstance(getattr(self, "_preview_df_all_raw", None), pd.DataFrame) else self._preview_df_all
        return stats_from_dataframe(df)

    def _preview_get_stats_map(self) -> dict[str, tuple[float, float, float]]:
        # Legend & stats should always show absolute values (not display-mode).
        # Prefer summary.csv when present, otherwise compute from the raw dataframe.
        s = self._preview_stats_from_summary_csv()
        if s:
            return s
        return self._preview_stats_from_raw_df()

    def _preview_get_room_temperature(self) -> Optional[float]:
        """Return the ambient/room temperature baseline used for delta calculations.

        Preference order:
        1) Ambient sensor series averaged over the run window (e.g. column "Ambient [°C]")
        2) Legacy manual entry from avg_temperature.json (older runs)
        """

        # 1) Prefer ambient sensor data (merged into run_window.csv by cli/plot_hwinfo.py)
        try:
            # Always compute baseline from RAW ambient series (not display-mode).
            df = self._preview_df_all_raw if isinstance(self._preview_df_all_raw, pd.DataFrame) else self._preview_df_all
            if isinstance(df, pd.DataFrame) and not df.empty:
                ambient_col = self._find_ambient_col(df)
                if ambient_col and ambient_col in df.columns:
                    ser = pd.to_numeric(df[ambient_col], errors="coerce")
                    if ser.notna().any():
                        v = float(ser.mean(skipna=True))
                        if np.isfinite(v):
                            return v
        except Exception:
            pass

        # 2) Legacy manual avg_temperature.json (kept for backwards compatibility)
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
        title = self._legend_stats_popup_title()
        room_temp = self._preview_get_room_temperature()
        test_settings = self._preview_get_test_settings()

        # dim app behind popup
        self._set_dimmed(True)

        self._legend_popup = LegendStatsPopup(
            top,
            title=title,
            columns=self._effective_available_cols(),
            active_set=set(self._effective_active_cols()),
            color_for=_color_for,
            on_toggle=_on_toggle,
            stats_map=stats_map,
            room_temperature=room_temp,
            test_settings=test_settings,
            theme_mode=getattr(self, "_theme_mode", "dark"),
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

    def _open_compare_legend_popup(self) -> None:
        try:
            if self._compare_legend_popup is not None and self._compare_legend_popup.isVisible():
                self._compare_legend_popup.close()
                self._compare_legend_popup = None
                self._set_dimmed(False)
                return
        except Exception:
            pass

        sensors = list(getattr(self, "_compare_manifest_sensors", None) or [])
        run_dirs = list(getattr(self, "_compare_run_dirs", None) or [])
        run_labels = list(getattr(self, "_compare_run_labels", None) or [])
        run_color_map = dict(getattr(self, "_compare_run_color_map", None) or {})

        if not sensors or len(run_dirs) < 2:
            return

        def _stats_for_run_dir(rd: Path) -> tuple[dict[str, tuple[float, float, float]], Optional[float]]:
            try:
                csvp = rd / "run_window.csv"
                if not csvp.exists():
                    return {}, None

                # Absolute stats
                abs_stats: dict[str, tuple[float, float, float]] = {}
                try:
                    abs_stats = stats_from_summary_csv(str(csvp)) or {}
                except Exception:
                    abs_stats = {}

                # Load raw dataframe (needed for accurate delta-T when ambient exists).
                df_all, cols = load_run_csv_dataframe(str(csvp))
                keep = [c for c in sensors if c in (cols or [])]
                if not abs_stats:
                    if keep:
                        abs_stats = stats_from_dataframe(df_all[keep])
                    else:
                        abs_stats = stats_from_dataframe(df_all)

                # Ambient average baseline for ΔT = Avg(temp) - Avg(ambient)
                ambient_avg: Optional[float] = None
                try:
                    ambient_col = self._find_ambient_col(df_all) if isinstance(df_all, pd.DataFrame) else None
                    if ambient_col and ambient_col in df_all.columns:
                        amb = pd.to_numeric(df_all[ambient_col], errors="coerce")
                        if amb.notna().any():
                            v = float(amb.mean(skipna=True))
                            if np.isfinite(v):
                                ambient_avg = v
                except Exception:
                    ambient_avg = None

                return abs_stats, ambient_avg
            except Exception:
                return {}, None

        run_tables: list[dict] = []
        for rd, lbl in zip(run_dirs, run_labels):
            abs_stats, ambient_avg = _stats_for_run_dir(rd)

            test_settings = None
            try:
                sp = rd / "test_settings.json"
                if sp.exists() and sp.is_file():
                    test_settings = json.loads(sp.read_text(encoding="utf-8"))
            except Exception:
                test_settings = None

            run_tables.append(
                {
                    "label": str(lbl),
                    "color": str(run_color_map.get(str(lbl), "#BDBDBD")),
                    "stats_map": abs_stats,
                    "ambient_avg": ambient_avg,
                    "test_settings": test_settings,
                }
            )

        top = self.parent.window() if hasattr(self.parent, "window") else self.parent

        self._set_dimmed(True)

        self._compare_legend_popup = CompareLegendStatsPopup(
            top,
            title=f"Legend and Stats ({len(run_tables)} results)",
            sensors=sensors,
            run_tables=run_tables,
            theme_mode=getattr(self, "_theme_mode", "dark"),
            on_close=self._on_legend_popup_closed,
            measurement_title_for_unit=self._measurement_title_for_unit,
        )

        self._install_outside_click_closer()
        self._compare_legend_popup.show()

        def _after_show():
            if self._compare_legend_popup is None:
                return
            try:
                self._ensure_dim_overlay()
            except Exception:
                pass
            raise_center_and_focus(parent=top, dlg=self._compare_legend_popup, dim_overlay=self._dim_overlay)

        QTimer.singleShot(0, _after_show)

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

            eff_active = list(self._effective_active_cols())
            aset = set(eff_active)
            for c, ln in list(self._preview_lines.items()):
                try:
                    ln.set_visible(c in aset)
                except Exception:
                    pass

            try:
                self._preview_df = self._preview_df_all[eff_active]
            except Exception:
                self._preview_df = self._preview_df_all

            self._preview_colors = [self._preview_color_map.get(c, "#FFFFFF") for c in eff_active]

            # keep existing tooltip builder calls (safe), but hover uses Qt overlay
            self._preview_build_tooltip_for_cols(eff_active)

            self._preview_autoscale_y_to_active()
            self._update_single_axis_header()
            self._apply_single_axis_header_chrome()
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

            active_set = set(self._effective_active_cols() if getattr(self, "_temp_delta_mode", False) else (self._preview_active_cols or []))
            all_cols = list(self._effective_available_cols() if getattr(self, "_temp_delta_mode", False) else (self._preview_available_cols or []))
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
                        try:
                            zero_mode = bool(getattr(self, "_zero_y_mode", False))
                        except Exception:
                            zero_mode = False

                        if zero_mode:
                            ymin0 = float(min(ymin, 0.0))
                            ymax0 = float(max(ymax, 0.0))
                            span = float(ymax0 - ymin0)
                            pad = 1.0 if span == 0.0 else 0.06 * span

                            low = 0.0 if ymin >= 0.0 else (ymin0 - pad)
                            high = 0.0 if ymax <= 0.0 else (ymax0 + pad)
                            ax.set_ylim(low, high)
                        else:
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

            try:
                zero_mode = bool(getattr(self, "_zero_y_mode", False))
            except Exception:
                zero_mode = False

            if zero_mode:
                ymin0 = float(min(ymin, 0.0))
                ymax0 = float(max(ymax, 0.0))
                span = float(ymax0 - ymin0)
                pad = 1.0 if span == 0.0 else 0.06 * span

                low = 0.0 if ymin >= 0.0 else (ymin0 - pad)
                high = 0.0 if ymax <= 0.0 else (ymax0 + pad)
                ax.set_ylim(low, high)
            else:
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

        try:
            self._compare_manifest_path = Path(manifest_path)
        except Exception:
            self._compare_manifest_path = None

        # -----------------------------
        # Load manifest
        # -----------------------------
        try:
            m = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception:
            m = {}

        try:
            self._set_delta_toggle_enabled(
                self._compare_manifest_delta_available(manifest_path, manifest=m)
            )
        except Exception:
            self._set_delta_toggle_enabled(False)

        sensors = [str(s) for s in (m.get("sensors") or []) if str(s).strip()]
        runs_rel = [str(r) for r in (m.get("runs") or []) if str(r).strip()]

        def _is_ambient_sensor_name(name: str) -> bool:
            try:
                s = str(name).strip().lower()
            except Exception:
                return False
            if not s:
                return False
            # Common formats: "Ambient [°C]", "Ambient Temp [°C]", etc.
            return (s == "ambient [°c]") or ("ambient" in s)

        # ΔT mode: ambient(t) - ambient(t) == 0, so hide the ambient subplot entirely.
        try:
            if self._delta_toggle_is_enabled() and bool(getattr(self, "_temp_delta_mode", False)) and sensors:
                sensors = [s for s in sensors if not _is_ambient_sensor_name(s)]
        except Exception:
            pass

        if not sensors or len(runs_rel) < 2:
            self._preview_label.clear()
            return

        # Keep manifest sensors for compare Legend & stats popup (match what we display).
        self._compare_manifest_sensors = list(sensors)

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

        # Keep compare run metadata for compare Legend & stats popup.
        self._compare_run_dirs = list(run_dirs)
        self._compare_run_labels = list(run_labels)

        # -----------------------------
        # Load run CSVs (keep only requested sensors)
        # -----------------------------
        run_dfs: list[pd.DataFrame] = []
        run_amb_dfs: list[pd.DataFrame] = []
        for rd in run_dirs:
            csvp = rd / "run_window.csv"
            if not csvp.exists():
                run_dfs.append(pd.DataFrame())
                run_amb_dfs.append(pd.DataFrame())
                continue
            try:
                df_all, cols = load_run_csv_dataframe(str(csvp))
                available = set(cols or [])
                keep = [s for s in sensors if s in available]
                df_keep = df_all[keep].copy() if keep else pd.DataFrame(index=df_all.index)

                # Keep ambient available for ΔT mode (even if it's not in the compare sensor list).
                try:
                    amb_col = self._find_ambient_col(df_all)
                except Exception:
                    amb_col = None
                if amb_col and amb_col in df_all.columns:
                    try:
                        run_amb_dfs.append(df_all[[amb_col]].copy())
                    except Exception:
                        run_amb_dfs.append(pd.DataFrame(index=df_all.index))
                else:
                    run_amb_dfs.append(pd.DataFrame(index=df_all.index))

                # Ensure all sensors exist as columns (fill missing with NaN)
                for s in sensors:
                    if s not in df_keep.columns:
                        df_keep[s] = np.nan
                df_keep = df_keep[sensors]
                run_dfs.append(df_keep)
            except Exception:
                run_dfs.append(pd.DataFrame())
                run_amb_dfs.append(pd.DataFrame())

        run_dfs = trim_dataframes_to_shortest_duration(run_dfs)
        run_amb_dfs = trim_dataframes_to_shortest_duration(run_amb_dfs)
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

        # If ΔT mode is enabled, precompute ambient interpolation per run (to common_elapsed).
        amb_interp_by_run: list[np.ndarray] = [np.full(shape=(min_len,), fill_value=np.nan, dtype=float) for _ in run_dfs]
        try:
            if bool(getattr(self, "_temp_delta_mode", False)):
                for j, (df_amb, x_run) in enumerate(zip(run_amb_dfs, run_elapsed_axes)):
                    try:
                        if df_amb is None or df_amb.empty or x_run.size < 2:
                            continue
                        amb_col = str(df_amb.columns[0]) if len(df_amb.columns) else ""
                        if not amb_col:
                            continue
                        y_amb = pd.to_numeric(df_amb[amb_col], errors="coerce").to_numpy(dtype=float)
                        mask = np.isfinite(y_amb) & np.isfinite(x_run)
                        if int(mask.sum()) < 2:
                            continue
                        amb_interp_by_run[j] = np.interp(common_elapsed, x_run[mask], y_amb[mask], left=np.nan, right=np.nan)
                    except Exception:
                        continue
        except Exception:
            pass

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
        self._sync_preview_canvas_scroll_height(len(axes))
        # Use the top axis for shared draw helpers (button bbox caching, etc.).
        try:
            self._preview_ax = axes[0] if axes else self._preview_ax
        except Exception:
            pass
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
        self._compare_run_color_map = dict(run_color_map)

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
                w.setStyleSheet(self._preview_tooltip_stylesheet())
                w.hide()
                return w
            except Exception:
                return None

        # -----------------------------
        # Build each sensor subplot
        # -----------------------------
        for i, sensor in enumerate(sensors):
            ax = axes[i]

            # Temperature sensor? (used for ΔT mode)
            try:
                unit = extract_unit_from_column(str(sensor))
                is_temp_sensor = (get_measurement_type_label(unit) == "Temperature")
            except Exception:
                is_temp_sensor = False
            try:
                is_ambient_sensor = ("ambient" in str(sensor).lower())
            except Exception:
                is_ambient_sensor = False

            self._apply_preview_axes_style(ax)

            try:
                ax.spines["top"].set_visible(False)
                ax.spines["bottom"].set_visible(False)
            except Exception:
                pass

            # Build per-sensor dataframe: columns are runs (labels), index is common_index
            df_sensor_raw = pd.DataFrame(index=common_index)
            df_sensor_delta = pd.DataFrame(index=common_index)

            for j, (lbl, df_run, x_run) in enumerate(zip(run_labels, run_dfs, run_elapsed_axes)):
                if df_run is None or df_run.empty or sensor not in df_run.columns or x_run.size < 2:
                    nan_arr = np.full(shape=(min_len,), fill_value=np.nan, dtype=float)
                    df_sensor_raw[str(lbl)] = nan_arr
                    df_sensor_delta[str(lbl)] = nan_arr
                    continue

                y = pd.to_numeric(df_run[sensor], errors="coerce").to_numpy(dtype=float)
                mask = np.isfinite(y) & np.isfinite(x_run)
                if int(mask.sum()) < 2:
                    nan_arr = np.full(shape=(min_len,), fill_value=np.nan, dtype=float)
                    df_sensor_raw[str(lbl)] = nan_arr
                    df_sensor_delta[str(lbl)] = nan_arr
                    continue

                try:
                    y_raw_i = np.interp(common_elapsed, x_run[mask], y[mask], left=np.nan, right=np.nan)
                except Exception:
                    y_raw_i = np.full(shape=(min_len,), fill_value=np.nan, dtype=float)

                y_delta_i = np.asarray(y_raw_i, dtype=float)
                try:
                    if bool(is_temp_sensor) and (not bool(is_ambient_sensor)):
                        amb_i = amb_interp_by_run[j] if j < len(amb_interp_by_run) else None
                        if amb_i is not None:
                            y_delta_i = np.asarray(y_raw_i, dtype=float) - np.asarray(amb_i, dtype=float)
                except Exception:
                    pass

                df_sensor_raw[str(lbl)] = y_raw_i
                df_sensor_delta[str(lbl)] = y_delta_i

            df_sensor = df_sensor_delta.copy() if bool(getattr(self, "_temp_delta_mode", False)) else df_sensor_raw.copy()
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
                    try:
                        zero_mode = bool(getattr(self, "_zero_y_mode", False))
                    except Exception:
                        zero_mode = False

                    if np.isfinite(ymin) and np.isfinite(ymax):
                        if zero_mode:
                            ymin0 = float(min(ymin, 0.0))
                            ymax0 = float(max(ymax, 0.0))
                            span0 = float(ymax0 - ymin0)
                            pad0 = 1.0 if span0 == 0.0 else 0.06 * span0
                            low = 0.0 if ymin >= 0.0 else (ymin0 - pad0)
                            high = 0.0 if ymax <= 0.0 else (ymax0 + pad0)
                            ax.set_ylim(low, high)
                        else:
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
                    0.0,
                    float(getattr(self, "_preview_title_axes_y", 1.02)),
                    str(sensor),
                    transform=ax.transAxes,
                    ha="left",
                    va="bottom",
                    fontsize=int(getattr(self, "_preview_title_font_size", 11) or 11),
                    color=self._preview_label_color(),
                    zorder=2500,
                    clip_on=False,
                )
            except Exception:
                pass

            # Sticky header owns preview controls for compare mode.

            if i == (n - 1):
                apply_elapsed_time_formatter(ax, is_dt=is_dt, x_vals=x_vals)
                try:
                    ax.tick_params(axis="x", which="both", labelbottom=False, bottom=False)
                except Exception:
                    pass

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
                "sensor": str(sensor),
                "x": np.asarray(x_vals, dtype=float),
                "is_dt": bool(is_dt),
                "df": df_sensor,
                "df_np": df_np,
                "df_raw": df_sensor_raw.copy(),
                "df_delta": df_sensor_delta.copy(),
                "cols": cols,
                "colors": cols_colors,
                "lines": lines,
                "series_data": dict(series_data),
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
        try:
            self._sync_preview_header_controls()
        except Exception:
            pass

        # Avoid a visible intermediate layout by applying relayout immediately.
        try:
            c = self._preview_canvas
            was_updates = True
            try:
                was_updates = bool(c.updatesEnabled())
            except Exception:
                was_updates = True
            try:
                c.setUpdatesEnabled(False)
            except Exception:
                pass

            try:
                self._compare_relayout_and_redraw()
            except Exception:
                # Fallback: still try to build hover backgrounds
                try:
                    self._refresh_compare_backgrounds()
                except Exception:
                    pass
        finally:
            try:
                if self._preview_canvas is not None:
                    try:
                        self._preview_canvas.setUpdatesEnabled(True)
                    except Exception:
                        pass
                    try:
                        self._preview_canvas.update()
                    except Exception:
                        pass
            except Exception:
                pass

        try:
            self._preview_last_canvas_wh = (int(self._preview_canvas.width()), int(self._preview_canvas.height()))
        except Exception:
            pass

        # -----------------------------
        # Hover handler (compare mode)
        # -----------------------------
        def _compare_mouse_move(ev):
            try:
                try:
                    if bool(getattr(self, "_plot_morph_lock_interaction", False)) or self._plot_morph_timer.isActive():
                        return
                except Exception:
                    pass
    
                if self._preview_canvas is None or not self._compare_mode:
                    return

                # Button hover: match single-mode cursor behavior and avoid showing tooltips.
                try:
                    if (
                        self._is_over_zero_y_button(ev.pos().x(), ev.pos().y())
                        or (self._delta_toggle_is_enabled() and self._is_over_delta_button(ev.pos().x(), ev.pos().y()))
                        or self._is_over_ls_button(ev.pos().x(), ev.pos().y())
                    ):
                        if not self._hovering_ls_btn:
                            self._hovering_ls_btn = True
                            self._preview_canvas.setCursor(Qt.PointingHandCursor)
                        self._hide_compare_hover_all()
                        return
                    else:
                        if self._hovering_ls_btn:
                            self._hovering_ls_btn = False
                            self._preview_canvas.setCursor(Qt.ArrowCursor)
                except Exception:
                    pass

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

        # (Relayout already applied above; no delayed resize pass needed.)


    def _plot_run_csv(self, fpath: str) -> None:
        if self._preview_canvas is None or self._preview_ax is None:
            raise RuntimeError("Preview canvas unavailable")

        self._exit_compare_mode()
        self._exit_single_mode_multi_axis()

        self._close_legend_popup()
        self._preview_csv_path = fpath

        df_data, cols = load_run_csv_dataframe(fpath)

        # Keep raw for baseline computations; build a display df for plotting.
        self._preview_df_all_raw = df_data[cols]
        try:
            self._set_delta_toggle_enabled(
                self._delta_toggle_available_for_df(
                    self._preview_df_all_raw,
                    run_dir=Path(fpath).parent,
                )
            )
        except Exception:
            self._set_delta_toggle_enabled(False)
        self._preview_df_all_delta = None
        try:
            df_disp = self._build_display_df() or self._preview_df_all_raw
            self._preview_df_all = df_disp
            if bool(getattr(self, "_temp_delta_mode", False)) and isinstance(df_disp, pd.DataFrame):
                self._preview_df_all_delta = df_disp
        except Exception:
            self._preview_df_all = self._preview_df_all_raw
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
                self._preview_df_all, sorted_groups, x_vals, is_dt, self._preview_color_map
            )
        # =========================================
        # Single-axis mode (all on one graph)
        # =========================================
        else:
            self._plot_run_csv_single_axis(
                self._preview_df_all, list(cols), x_vals, is_dt, self._preview_color_map
            )

        # Navigation robustness: if delta mode is ON but the freshly loaded plot ended up
        # in absolute units, force a replot in the correct display mode.
        try:
            if bool(getattr(self, "_temp_delta_mode", False)) and (not self._delta_display_is_applied()):
                if not self._apply_temp_delta_mode_to_current_plot():
                    self._schedule_replot_current_result_for_display_mode(delay_ms=0)
        except Exception:
            pass

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
        self._single_header_text = None
        self._ls_btn_text = None
        self._ls_btn_bbox = None
        self._delta_btn_text = None
        self._delta_btn_bbox = None
        self._zero_btn_text = None
        self._zero_btn_bbox = None

        # The dataframe passed into this function is the DISPLAY dataframe.
        self._preview_df_all = df_data

        self._apply_preview_axes_style(self._preview_ax)

        cols_plot = [str(c) for c in (cols or [])]

        self._preview_lines, self._preview_series_data, self._preview_colors = plot_lines_with_glow(
            self._preview_ax,
            df_all=df_data,
            cols=list(cols_plot),
            x_vals=x_vals,
            is_dt=is_dt,
            color_map=color_map,
        )

        # Hide lines that aren't active (and always hide ambient in ΔT mode).
        amb = None
        try:
            amb = self._ambient_col_for_current_result()
        except Exception:
            amb = None
        aset = set(self._effective_active_cols())
        for name, ln in list(self._preview_lines.items()):
            try:
                vis = (name in aset)
                if amb and name == amb and bool(getattr(self, "_temp_delta_mode", False)):
                    vis = False
                ln.set_visible(bool(vis))
            except Exception:
                pass

        self._preview_x = x_vals

        active_cols_eff = list(self._effective_active_cols())
        try:
            self._preview_df = df_data[active_cols_eff]
        except Exception:
            self._preview_df = df_data

        self._preview_build_tooltip_for_cols(active_cols_eff)
        self._preview_autoscale_y_to_active()

        try:
            if len(x_vals) > 0:
                self._preview_ax.set_xlim(left=x_vals[0], right=x_vals[-1])
        except Exception:
            pass

        apply_elapsed_time_formatter(self._preview_ax, is_dt=is_dt, x_vals=x_vals)
        try:
            self._preview_ax.tick_params(axis="x", which="both", labelbottom=False, bottom=False)
        except Exception:
            pass

        try:
            self._preview_vline = create_hover_vline(
                self._preview_ax,
                x0=self._preview_x[0],
                grid_color=self._preview_grid_color,
                dot_dashes=self._preview_dot_dashes,
            )
        except Exception:
            self._preview_vline = None

        self._preview_build_tooltip_for_cols(self._effective_active_cols())
        self._preview_autoscale_y_to_active()

        self._update_single_axis_header()
        self._apply_single_axis_header_chrome()

        # Sticky header owns preview controls for single-axis mode.

        try:
            self._preview_label.clear()
            self._preview_label.hide()
        except Exception:
            pass

        try:
            self._sync_preview_canvas_scroll_height(1)
        except Exception:
            pass

        try:
            self._preview_canvas.show()
            self._sync_preview_header_controls()
            # Batch draw + relayout so we don't paint an intermediate (cropped) layout.
            c = self._preview_canvas
            was_updates = True
            try:
                was_updates = bool(c.updatesEnabled())
            except Exception:
                was_updates = True
            try:
                c.setUpdatesEnabled(False)
            except Exception:
                pass

            try:
                self._preview_relayout_and_redraw()
            finally:
                try:
                    c.setUpdatesEnabled(bool(was_updates))
                except Exception:
                    try:
                        c.setUpdatesEnabled(True)
                    except Exception:
                        pass
                try:
                    c.update()
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

        # (Relayout already applied above; keep the canvas size stable.)

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
        self._delta_btn_text = None
        self._delta_btn_bbox = None
        self._zero_btn_text = None
        self._zero_btn_bbox = None

        # The dataframe passed into this function is the DISPLAY dataframe.
        self._preview_df_all = df_data

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
        self._sync_preview_canvas_scroll_height(len(axes))

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

                w.setStyleSheet(self._preview_tooltip_stylesheet())
                w.hide()
                return w
            except Exception:
                return None

        amb_col = None
        try:
            amb_col = self._ambient_col_for_current_result()
        except Exception:
            amb_col = None

        active_set = set(self._effective_active_cols())

        # Plot each measurement type on its own axis
        for idx, (unit, group_cols) in enumerate(sorted_groups):
            ax = axes[idx]
            measurement_label = self._measurement_title_for_unit(unit)

            self._apply_preview_axes_style(ax)

            try:
                ax.spines["top"].set_visible(False)
                ax.spines["bottom"].set_visible(False)
            except Exception:
                pass

            # Plot lines for this measurement group (ambient line stays plotted, but hidden in ΔT mode)
            group_cols2 = [str(c) for c in (group_cols or [])]

            lines, series_data, colors = plot_lines_with_glow(
                ax,
                df_all=df_data,
                cols=group_cols2,
                x_vals=x_vals,
                is_dt=is_dt,
                color_map=color_map,
            )

            # Hide lines not in active set (and always hide ambient in ΔT mode)
            for col_name, ln in list(lines.items()):
                try:
                    vis = (col_name in active_set)
                    if amb_col and col_name == amb_col and bool(getattr(self, "_temp_delta_mode", False)):
                        vis = False
                    ln.set_visible(bool(vis))
                except Exception:
                    pass

            # y autoscale for this subplot (respect zero-Y mode)
            try:
                ys = []
                for name, yarr in (series_data or {}).items():
                    if name not in active_set:
                        continue
                    try:
                        yarr = np.asarray(yarr, dtype=float)
                        yarr = yarr[np.isfinite(yarr)]
                        if yarr.size:
                            ys.append(yarr)
                    except Exception:
                        pass
                if ys:
                    y_all = np.concatenate(ys)
                    ymin = float(np.nanmin(y_all))
                    ymax = float(np.nanmax(y_all))
                    if np.isfinite(ymin) and np.isfinite(ymax):
                        try:
                            zero_mode = bool(getattr(self, "_zero_y_mode", False))
                        except Exception:
                            zero_mode = False

                        if zero_mode:
                            ymin0 = float(min(ymin, 0.0))
                            ymax0 = float(max(ymax, 0.0))
                            span = float(ymax0 - ymin0)
                            pad = 1.0 if span == 0.0 else 0.06 * span
                            low = 0.0 if ymin >= 0.0 else (ymin0 - pad)
                            high = 0.0 if ymax <= 0.0 else (ymax0 + pad)
                            ax.set_ylim(low, high)
                        else:
                            pad = 1.0 if ymin == ymax else 0.06 * (ymax - ymin)
                            ax.set_ylim(ymin - pad, ymax + pad)
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
                    float(getattr(self, "_preview_title_axes_y", 1.02)),
                    str(measurement_label),
                    transform=ax.transAxes,
                    ha="left",
                    va="bottom",
                    fontsize=int(getattr(self, "_preview_title_font_size", 11) or 11),
                    color=self._preview_label_color(),
                    zorder=2600,
                    clip_on=False,
                )
            except Exception:
                pass

            # Sticky header owns preview controls for multi-axis mode.

            # Store axis state
            try:
                if group_cols2:
                    df_np = df_data[group_cols2].to_numpy(dtype=float, copy=False)
                else:
                    df_np = np.zeros((int(len(x_vals)), 0), dtype=float)
            except Exception:
                try:
                    df_np = np.asarray(df_data[group_cols2].to_numpy(), dtype=float) if group_cols2 else np.zeros((int(len(x_vals)), 0), dtype=float)
                except Exception:
                    df_np = None

            cols2 = [str(c) for c in list(group_cols2)]
            colors2 = [str(color_map.get(str(c), "#FFFFFF")) for c in cols2]

            self._single_axis_state[ax] = {
                "unit": unit,
                "cols": cols2,
                "lines": lines,
                "series_data": series_data,
                "colors": colors2,
                "x": np.asarray(x_vals, dtype=float),
                "is_dt": bool(is_dt),
                "df": df_data[group_cols2].copy(),
                "df_np": df_np,
                "vline": vline,
                "bg": None,
                "qt_tt": _make_single_tt(),
            }
            self._single_axis_vlines[ax] = vline

            # Apply time formatter only to the last (bottom) axis
            if idx == len(sorted_groups) - 1:
                apply_elapsed_time_formatter(ax, is_dt=is_dt, x_vals=x_vals)
                try:
                    ax.tick_params(axis="x", which="both", labelbottom=False, bottom=False)
                except Exception:
                    pass
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
                right=float(self._preview_effective_right_frac()),
                top=float(getattr(self, "_preview_top_frac", 0.93)),
                bottom=float(getattr(self, "_preview_bottom_frac", 0.05)),
                hspace=float(getattr(self, "_preview_stack_hspace", 0.35)),
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
            self._sync_preview_header_controls()
            # Batch draw + relayout so we don't paint an intermediate (cropped) layout.
            c = self._preview_canvas
            was_updates = True
            try:
                was_updates = bool(c.updatesEnabled())
            except Exception:
                was_updates = True
            try:
                c.setUpdatesEnabled(False)
            except Exception:
                pass

            try:
                self._single_mode_relayout_and_redraw()
            finally:
                try:
                    c.setUpdatesEnabled(bool(was_updates))
                except Exception:
                    try:
                        c.setUpdatesEnabled(True)
                    except Exception:
                        pass
                try:
                    c.update()
                except Exception:
                    pass
        except Exception:
            pass

        # Cache backgrounds for fast vline blit (relayout already refreshed bgs).
        try:
            self._single_last_canvas_wh = (int(self._preview_canvas.width()), int(self._preview_canvas.height()))
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
                try:
                    if bool(getattr(self, "_plot_morph_lock_interaction", False)) or self._plot_morph_timer.isActive():
                        return
                except Exception:
                    pass
    
                if self._preview_canvas is None or not self._single_mode_multi_axis:
                    return

                wh = (int(self._preview_canvas.width()), int(self._preview_canvas.height()))
                try:
                    if self._single_last_canvas_wh != wh:
                        self._single_last_canvas_wh = wh
                        self._refresh_single_backgrounds()
                except Exception:
                    pass

                # Check delta + legend&stats buttons
                try:
                    if hasattr(ev, 'pos') and ev.pos():
                        x, y = ev.pos().x(), ev.pos().y()
                        if (
                            self._is_over_zero_y_button(int(x), int(y))
                            or (self._delta_toggle_is_enabled() and self._is_over_delta_button(int(x), int(y)))
                            or self._is_over_ls_button(int(x), int(y))
                        ):
                            if not self._hovering_ls_btn:
                                self._hovering_ls_btn = True
                                self._preview_canvas.setCursor(Qt.PointingHandCursor)
                            self._hide_single_hover_all()
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

        # (Relayout already applied above; no delayed resize pass needed.)

    def _single_mode_relayout_and_redraw(self) -> None:
        """Relayout multi-axis subplots on resize/show."""
        try:
            if self._preview_canvas is None or self._preview_fig is None:
                return
            if not self._preview_canvas.isVisible():
                return
            if not getattr(self, "_single_mode_multi_axis", False) or not getattr(self, "_single_axes", None):
                return

            self._sync_preview_canvas_scroll_height(len(getattr(self, "_single_axes", []) or []))

            self._preview_canvas.draw()
            renderer = self._preview_canvas.get_renderer()
            if renderer is None:
                return

            # Compute left margin
            left_px = float(getattr(self, "_preview_left_margin_px_base", 60) or 60)
            for ax in list(self._single_axes):
                try:
                    self._preview_ax = ax
                    left_px = max(
                        left_px,
                        float(
                            self._preview_required_left_margin_px(
                                renderer,
                                pad_px=int(getattr(self, "_preview_left_tick_pad_px", 3) or 3),
                            )
                        ),
                    )
                except Exception:
                    continue

            try:
                fig_w_px = float(self._preview_fig.get_figwidth() * self._preview_fig.dpi)
                left = (left_px / fig_w_px) if fig_w_px > 1 else 0.08
                left = max(0.02, min(left, 0.35))
                top, bottom = self._preview_effective_vertical_fracs()

                self._preview_fig.subplots_adjust(
                    left=left,
                    right=float(self._preview_effective_right_frac()),
                    top=float(top),
                    bottom=float(bottom),
                    hspace=float(getattr(self, "_preview_stack_hspace", 0.35)),
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
            try:
                axes = list(getattr(self, "_single_axes", []) or [])
                self._update_sticky_timeline(axes[-1] if len(axes) > 1 else None)
            except Exception:
                self._hide_sticky_timeline()
        except Exception:
            try:
                if self._preview_canvas is not None:
                    self._preview_canvas.draw_idle()
            except Exception:
                pass

