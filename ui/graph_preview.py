# graph_preview.py
"""Graph preview component for displaying CSV sensor data with interactive tooltips + legend-button selector."""

import time
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

from PySide6.QtCore import QTimer, Qt, QEvent, QObject, QPoint
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QLabel,
    QSizePolicy,
    QApplication,
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QListWidget,
    QListWidgetItem,
    QPushButton,
)

from matplotlib.figure import Figure
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
import matplotlib.cm as cm
import matplotlib.dates as mdates
from matplotlib.backend_bases import MouseEvent as MPLMouseEvent
from matplotlib.offsetbox import AnnotationBbox, TextArea, VPacker, HPacker
import matplotlib.patheffects as pe


class GraphPreview(QObject):
    """Handles matplotlib graph rendering, legend-button sensor selection, and interactive tooltip system."""

    def __init__(self, parent, preview_label: QLabel, build_selected_columns_callback):
        super().__init__(parent)

        self.parent = parent
        self._preview_label = preview_label
        self._build_selected_columns = build_selected_columns_callback

        # app focus binding guard
        self._app_focus_bound = False
        self._app_is_active = True  # assume active at start

        # Sensor selector popup
        self._sensor_popup: Optional[QDialog] = None

        # Series state
        self._preview_all_cols: list[str] = []       # all numeric columns in this run_window.csv
        self._preview_lines = {}                     # name -> Line2D
        self._preview_series_data = {}               # name -> np.ndarray of y
        self._preview_series_visible = {}            # name -> bool

        # Simple cache to avoid expensive redraw when switching tabs repeatedly
        self._last_csv_path: Optional[str] = None
        self._last_csv_mtime: Optional[float] = None

        # Initialize matplotlib canvas
        try:
            self._preview_fig = Figure(figsize=(5, 3))

            # -------------------- layout constants --------------------
            self._preview_left_margin_px_base = 60  # base, auto-grown by tick label widths
            self._preview_top_frac = 0.98
            self._preview_bottom_frac = 0.50

            # Legend hugging right border + dynamic axes resize
            self._preview_legend_edge_pad_px = 6   # padding from right border
            self._preview_legend_gap_px = 10       # gap between axes and legend
            self._preview_leg = None               # legend handle
            self._preview_last_right_frac = 0.98   # cached right edge of axes (fraction)

            self._preview_canvas = FigureCanvas(self._preview_fig)
            self._preview_canvas.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
            self._preview_canvas.setMouseTracking(True)
            self._preview_canvas.setFocusPolicy(Qt.StrongFocus)

            self._preview_ax = self._preview_fig.add_subplot(111)

            # Initial axes rect (full-ish width until legend measured)
            self._preview_apply_axes_rect(right_frac=0.98, left_margin_px=self._preview_left_margin_px_base)

            # track canvas size to detect minimize/restore geometry changes
            self._preview_last_canvas_wh = None

            # watch canvas resize/show/hide/click to invalidate caches + legend clicks
            try:
                self._preview_canvas.installEventFilter(self)
            except Exception:
                pass

            # Connect draw event for background caching
            self._preview_canvas.mpl_connect("draw_event", self._on_preview_draw)

            # Qt mouse move -> data coords
            def _qc(ev):
                try:
                    if not getattr(self, "_app_is_active", True):
                        return

                    x = ev.pos().x()
                    y = ev.pos().y()
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
            self._preview_canvas.hide()

            # Legacy annotation (kept for safety)
            self._preview_annot = self._preview_ax.annotate(
                "", xy=(0, 0), xytext=(10, 10), textcoords="offset points",
                bbox=dict(boxstyle="round", fc="white", ec="black", alpha=0.9),
                color="black", fontsize=8, visible=False
            )

            self._preview_mpl_cid = None
            self._preview_click_cid = None  # (we will NOT use mpl click for legend)
            self._preview_x = None
            self._preview_df = None

            # Tooltip state
            self._preview_collective_box = None
            self._preview_collective_time = None
            self._preview_name_areas = None
            self._preview_value_areas = None
            self._preview_colors = []
            self._preview_last_idx = None

            # Tooltip placement config (points)
            self._preview_tt_default_xybox = (10, 10)       # above-right
            self._preview_tt_flipped_xybox = (10, -10)      # below-right
            self._preview_tt_left_xybox = (-10, 10)         # above-left
            self._preview_tt_left_down_xybox = (-10, -10)   # below-left
            self._preview_tt_margin_px = 4                  # margin from axes in pixels

            # Hover performance
            self._hover_last_ts = 0.0
            self._hover_min_interval = 1.0 / 120.0  # up to ~120fps

            # Tooltip movement animation (smooth glide)
            self._tt_anim_timer = QTimer(self.parent)
            self._tt_anim_timer.setInterval(16)  # ~60fps
            self._tt_anim_timer.timeout.connect(self._tt_anim_tick)

            self._tt_anim_duration = 0.10  # seconds
            self._tt_anim_t0 = 0.0
            self._tt_anim_start_xy = None
            self._tt_anim_target_xy = None

            # Blit cache
            self._preview_bg = None
            self._preview_ax_bbox = None

            # Cached tooltip metrics
            self._preview_tt_w_px = None
            self._preview_tt_h_px = None
            self._preview_tt_mode = "UR"  # UR, DR, UL, DL

            # Hover vline
            self._preview_vline = None

            # Grid style shared
            self._preview_grid_color = "#3A3A3A"
            self._preview_dot_dashes = (0, (1.2, 3.2))

        except Exception:
            self._preview_fig = None
            self._preview_canvas = None
            self._preview_ax = None
            self._preview_annot = None
            self._preview_mpl_cid = None
            self._preview_click_cid = None
            self._preview_x = None
            self._preview_df = None
            self._preview_collective_box = None
            self._preview_collective_time = None
            self._preview_name_areas = None
            self._preview_value_areas = None
            self._preview_colors = []
            self._preview_last_idx = None
            self._preview_tt_default_xybox = (10, 10)
            self._preview_tt_flipped_xybox = (10, -10)
            self._preview_tt_left_xybox = (-10, 10)
            self._preview_tt_left_down_xybox = (-10, -10)
            self._preview_tt_margin_px = 4
            self._hover_last_ts = 0.0
            self._hover_min_interval = 1.0 / 60.0
            self._preview_bg = None
            self._preview_ax_bbox = None
            self._preview_tt_w_px = None
            self._preview_tt_h_px = None
            self._preview_tt_mode = "UR"
            self._preview_vline = None
            self._preview_leg = None
            self._preview_last_right_frac = 0.98
            self._preview_left_margin_px_base = 15
            self._preview_legend_edge_pad_px = 6
            self._preview_legend_gap_px = 10
            self._preview_top_frac = 0.98
            self._preview_bottom_frac = 0.50

    # ---------------------------------------------------------------------
    # HiDPI helpers + legend hit test (THIS IS THE FIX)
    # ---------------------------------------------------------------------
    def _dpr(self) -> float:
        """Device pixel ratio (Qt logical px -> Matplotlib renderer px)."""
        try:
            if self._preview_canvas is None:
                return 1.0
            # PySide6 returns float
            return float(self._preview_canvas.devicePixelRatioF())
        except Exception:
            return 1.0

    def _legend_hit_test_qtpos(self, qt_pos: QPoint) -> bool:
        """Hit test if a Qt mouse position is inside the legend bbox (HiDPI-safe)."""
        try:
            if not getattr(self, "_app_is_active", True):
                return False
            if self._preview_canvas is None or self._preview_leg is None:
                return False

            renderer = self._preview_canvas.get_renderer()
            if renderer is None:
                self._preview_canvas.draw()
                renderer = self._preview_canvas.get_renderer()
                if renderer is None:
                    return False

            bbox = self._preview_leg.get_window_extent(renderer)  # renderer/display px (physical)

            dpr = self._dpr()

            # Qt pos: logical px, origin top-left
            x_l = float(qt_pos.x())
            y_l = float(qt_pos.y())
            h_l = float(self._preview_canvas.height())

            # Convert to Matplotlib display coords: physical px, origin bottom-left
            x = x_l * dpr
            y = (h_l - y_l) * dpr

            return (bbox.x0 <= x <= bbox.x1) and (bbox.y0 <= y <= bbox.y1)
        except Exception:
            return False

    # ---------------------------------------------------------------------
    # Layout helpers (legend hugs right border + axes resizes + y-label fit)
    # ---------------------------------------------------------------------
    def _preview_apply_axes_rect(self, right_frac: float, left_margin_px: float) -> None:
        try:
            fig = self._preview_fig
            ax = self._preview_ax
            if fig is None or ax is None:
                return

            fig_w_px = float(fig.get_figwidth() * fig.dpi)
            if fig_w_px <= 1:
                return

            left = float(left_margin_px) / fig_w_px
            top = float(self._preview_top_frac)
            bottom = float(self._preview_bottom_frac)

            left = max(0.0, min(left, 0.95))
            right = max(left + 0.05, min(float(right_frac), 0.995))

            ax.set_position([left, bottom, right - left, top - bottom])
        except Exception:
            pass

    def _preview_relayout_for_legend(self) -> None:
        try:
            fig = self._preview_fig
            canvas = self._preview_canvas
            leg = getattr(self, "_preview_leg", None)

            if fig is None or canvas is None:
                return

            if leg is None:
                self._preview_last_right_frac = 0.98
                return

            renderer = canvas.get_renderer()
            if renderer is None:
                canvas.draw()
                renderer = canvas.get_renderer()
                if renderer is None:
                    return

            bbox = leg.get_window_extent(renderer)  # physical px

            fig_w_px = float(fig.get_figwidth() * fig.dpi)
            if fig_w_px <= 1:
                return

            leg_w_frac = float(bbox.width) / fig_w_px
            edge_pad_frac = float(self._preview_legend_edge_pad_px) / fig_w_px
            gap_frac = float(self._preview_legend_gap_px) / fig_w_px

            right_frac = 1.0 - leg_w_frac - edge_pad_frac - gap_frac
            right_frac = max(0.20, min(right_frac, 0.98))
            self._preview_last_right_frac = right_frac

            leg.set_bbox_to_anchor(
                (1.0 - edge_pad_frac, float(self._preview_top_frac)),
                transform=fig.transFigure
            )
            leg.set_zorder(2000)
        except Exception:
            pass

    def _preview_required_left_margin_px(self, renderer, pad_px: int = 8) -> float:
        try:
            ax = self._preview_ax
            if ax is None or renderer is None:
                return float(self._preview_left_margin_px_base)

            bboxes = []

            for t in ax.get_yticklabels():
                if t.get_visible() and t.get_text():
                    try:
                        bboxes.append(t.get_window_extent(renderer))
                    except Exception:
                        pass

            try:
                ot = ax.yaxis.get_offset_text()
                if ot is not None and ot.get_visible() and ot.get_text():
                    bboxes.append(ot.get_window_extent(renderer))
            except Exception:
                pass

            if not bboxes:
                return float(self._preview_left_margin_px_base)

            min_x0 = min(bb.x0 for bb in bboxes)
            max_width = max(bb.width for bb in bboxes)

            if min_x0 < pad_px:
                extra = float(pad_px) - float(min_x0)
                return float(self._preview_left_margin_px_base) + extra
            else:
                required = max_width + float(pad_px)
                return max(float(self._preview_left_margin_px_base), required)
        except Exception:
            return float(getattr(self, "_preview_left_margin_px_base", 60))

    def _preview_relayout_and_redraw(self) -> None:
        try:
            if self._preview_canvas is None or self._preview_ax is None:
                return
            if not self._preview_canvas.isVisible():
                return

            self._preview_canvas.draw()
            renderer = self._preview_canvas.get_renderer()
            if renderer is None:
                return

            self._preview_relayout_for_legend()
            right_frac = float(getattr(self, "_preview_last_right_frac", 0.98) or 0.98)

            self._preview_canvas.draw()
            renderer = self._preview_canvas.get_renderer()
            if renderer is None:
                return

            left_px = self._preview_required_left_margin_px(renderer, pad_px=8)
            self._preview_apply_axes_rect(right_frac=right_frac, left_margin_px=left_px)

            self._preview_relayout_for_legend()

            self._preview_invalidate_interaction_cache()
            self._preview_canvas.draw()
            self._on_preview_draw()

        except Exception:
            try:
                if self._preview_canvas is not None:
                    self._preview_canvas.draw_idle()
            except Exception:
                pass

    # ---------------------------------------------------------------------
    # App focus binding
    # ---------------------------------------------------------------------
    def bind_app_focus(self) -> None:
        if self._app_focus_bound:
            return
        self._app_focus_bound = True

        app = QApplication.instance()
        if app is None:
            return

        try:
            app.applicationStateChanged.connect(self._on_app_state_changed)
        except Exception:
            pass

    def _on_app_state_changed(self, state):
        try:
            if state == Qt.ApplicationActive:
                self._app_is_active = True
                self._preview_invalidate_interaction_cache()
                QTimer.singleShot(0, self._preview_relayout_and_redraw)
            else:
                self._app_is_active = False
                self._hide_preview_hover(hard=True)
                self._preview_invalidate_interaction_cache()
                self._close_sensor_popup()
        except Exception:
            pass

    # ---------------------------------------------------------------------
    # Public API
    # ---------------------------------------------------------------------
    def get_canvas(self):
        return self._preview_canvas

    def preview_path(self, fpath: str) -> None:
        """Preview a file (CSV or image)."""
        try:
            p = Path(fpath)

            if p.is_file() and p.suffix.lower() == ".csv" and self._preview_canvas is not None:
                # cache to reduce tab-switch delay
                try:
                    mtime = p.stat().st_mtime
                except Exception:
                    mtime = None

                if (
                    self._last_csv_path == str(p)
                    and mtime is not None
                    and self._last_csv_mtime == float(mtime)
                ):
                    # Same file unchanged -> just ensure visible & relayout once
                    try:
                        self._preview_label.clear()
                        self._preview_label.hide()
                    except Exception:
                        pass
                    try:
                        self._preview_canvas.show()
                    except Exception:
                        pass
                    QTimer.singleShot(0, self._preview_relayout_and_redraw)
                    return

                try:
                    self._plot_run_csv(str(p))
                    self._last_csv_path = str(p)
                    self._last_csv_mtime = float(mtime) if mtime is not None else None
                    return
                except Exception:
                    pass

            if p.is_file() and p.suffix.lower() in (".png", ".jpg", ".jpeg", ".bmp"):
                if self._preview_canvas is not None:
                    try:
                        self._preview_canvas.hide()
                    except Exception:
                        pass
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
        self._preview_label.clear()
        self._preview_label.show()

    def preview_folder(self, folder: str) -> None:
        """Preview a folder by showing its run_window.csv or ALL_SELECTED.png if available."""
        try:
            p = Path(folder)
            if not p.exists() or not p.is_dir():
                try:
                    if self._preview_canvas is not None:
                        self._preview_canvas.hide()
                except Exception:
                    pass
                self._preview_label.clear()
                return

            csv_path = p / "run_window.csv"
            if csv_path.exists() and csv_path.is_file():
                self.preview_path(str(csv_path))
                return

            png_path = p / "ALL_SELECTED.png"
            if png_path.exists() and png_path.is_file():
                self.preview_path(str(png_path))
                return

            try:
                if self._preview_canvas is not None:
                    self._preview_canvas.hide()
            except Exception:
                pass
            self._preview_label.clear()
        except Exception:
            self._preview_label.clear()

    # -------------------------------------------------------------------------
    # Blit / draw helpers
    # -------------------------------------------------------------------------
    def _on_preview_draw(self, event=None) -> None:
        try:
            if self._preview_canvas is None or self._preview_ax is None:
                return
            self._preview_bg = self._preview_canvas.copy_from_bbox(self._preview_ax.bbox)
            renderer = self._preview_canvas.get_renderer()
            self._preview_ax_bbox = self._preview_ax.get_window_extent(renderer)
        except Exception:
            pass

    def _preview_blit(self) -> None:
        try:
            if self._preview_canvas is None or self._preview_ax is None:
                return
            if self._preview_bg is None:
                self._preview_canvas.draw_idle()
                return

            c = self._preview_canvas
            ax = self._preview_ax
            c.restore_region(self._preview_bg)

            if getattr(self, "_preview_vline", None) is not None and self._preview_vline.get_visible():
                ax.draw_artist(self._preview_vline)

            ab = getattr(self, "_preview_collective_box", None)
            if ab is not None and ab.get_visible():
                ax.draw_artist(ab)

            c.blit(ax.bbox)
        except Exception:
            try:
                if self._preview_canvas is not None:
                    self._preview_canvas.draw_idle()
            except Exception:
                pass

    def _preview_update_tooltip_metrics(self) -> None:
        try:
            ab = getattr(self, "_preview_collective_box", None)
            if ab is None or self._preview_canvas is None or self._preview_ax is None:
                return
            renderer = self._preview_canvas.get_renderer()
            bbox = ab.get_window_extent(renderer)
            self._preview_tt_w_px = float(bbox.width)
            self._preview_tt_h_px = float(bbox.height)
            self._preview_ax_bbox = self._preview_ax.get_window_extent(renderer)
        except Exception:
            pass

    def _preview_update_tooltip_mode_for(self, xdata: float, ydata: float) -> None:
        ab = getattr(self, "_preview_collective_box", None)
        ax = getattr(self, "_preview_ax", None)
        canvas = getattr(self, "_preview_canvas", None)
        fig = getattr(self, "_preview_fig", None)

        if ab is None or ax is None or canvas is None or fig is None:
            return
        if xdata is None or ydata is None:
            return

        if self._preview_tt_w_px is None or self._preview_tt_h_px is None or self._preview_ax_bbox is None:
            self._preview_update_tooltip_metrics()
            if self._preview_tt_w_px is None or self._preview_tt_h_px is None or self._preview_ax_bbox is None:
                return

        try:
            dpi = float(getattr(fig, "dpi", 100) or 100)
            margin = float(getattr(self, "_preview_tt_margin_px", 4))
            cx, cy = ax.transData.transform((xdata, ydata))

            def pts_to_px(v):
                return float(v) * dpi / 72.0

            ur = getattr(self, "_preview_tt_default_xybox", (10, 10))
            dr = getattr(self, "_preview_tt_flipped_xybox", (10, -10))
            ul = getattr(self, "_preview_tt_left_xybox", (-10, 10))
            dl = getattr(self, "_preview_tt_left_down_xybox", (-10, -10))

            urx, ury = pts_to_px(ur[0]), pts_to_px(ur[1])

            w = float(self._preview_tt_w_px)
            h = float(self._preview_tt_h_px)

            ax_right = float(self._preview_ax_bbox.x1) - margin
            ax_top = float(self._preview_ax_bbox.y1) - margin

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

            if mode != getattr(self, "_preview_tt_mode", "UR"):
                self._preview_tt_mode = mode
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

    def _tt_anim_tick(self) -> None:
        ab = getattr(self, "_preview_collective_box", None)
        if ab is None or not ab.get_visible():
            try:
                self._tt_anim_timer.stop()
            except Exception:
                pass
            return

        try:
            if self._tt_anim_start_xy is None or self._tt_anim_target_xy is None:
                self._tt_anim_timer.stop()
                return

            now = time.time()
            dur = float(getattr(self, "_tt_anim_duration", 0.10) or 0.10)
            t = (now - float(getattr(self, "_tt_anim_t0", 0.0))) / dur if dur > 0 else 1.0

            if t >= 1.0:
                ab.xy = self._tt_anim_target_xy
                self._tt_anim_timer.stop()
                self._preview_blit()
                return

            ease = 1.0 - (1.0 - t) ** 3

            sx, sy = self._tt_anim_start_xy
            tx, ty = self._tt_anim_target_xy

            cx = sx + (tx - sx) * ease
            cy = sy + (ty - sy) * ease

            ab.xy = (cx, cy)
            self._preview_blit()
        except Exception:
            try:
                self._tt_anim_timer.stop()
            except Exception:
                pass

    def _preview_invalidate_interaction_cache(self) -> None:
        try:
            self._preview_bg = None
            self._preview_ax_bbox = None
            self._preview_tt_w_px = None
            self._preview_tt_h_px = None
            self._preview_tt_mode = "UR"
        except Exception:
            pass

        try:
            if getattr(self, "_tt_anim_timer", None) is not None:
                self._tt_anim_timer.stop()
        except Exception:
            pass

        try:
            ab = getattr(self, "_preview_collective_box", None)
            if ab is not None:
                ab.set_visible(False)
        except Exception:
            pass

        try:
            vl = getattr(self, "_preview_vline", None)
            if vl is not None:
                vl.set_visible(False)
        except Exception:
            pass

    def _safe_preview_redraw(self) -> None:
        try:
            if self._preview_canvas is None:
                return
            if not self._preview_canvas.isVisible():
                return
            self._preview_canvas.draw()
            try:
                self._preview_canvas.update()
            except Exception:
                pass
            self._on_preview_draw()
        except Exception:
            try:
                self._preview_canvas.draw_idle()
            except Exception:
                pass

    # -------------------------------------------------------------------------
    # IMPORTANT: Qt eventFilter handles legend click reliably (HiDPI-safe)
    # -------------------------------------------------------------------------
    def eventFilter(self, obj, event):
        try:
            if obj is getattr(self, "_preview_canvas", None):
                et = event.type()

                if et in (QEvent.Resize, QEvent.Show):
                    self._preview_invalidate_interaction_cache()
                    QTimer.singleShot(0, self._preview_relayout_and_redraw)

                elif et == QEvent.Hide:
                    self._hide_preview_hover(hard=True)
                    self._preview_invalidate_interaction_cache()
                    self._close_sensor_popup()

                elif et in (QEvent.WindowDeactivate, QEvent.FocusOut):
                    self._hide_preview_hover(hard=True)
                    self._close_sensor_popup()

                elif et == QEvent.Leave:
                    self._hide_preview_hover(hard=True)

                # ---- Legend "button" click (FIX) ----
                elif et == QEvent.MouseButtonPress:
                    try:
                        if not getattr(self, "_app_is_active", True):
                            return False

                        # close popup if click elsewhere on canvas
                        if self._sensor_popup is not None and self._sensor_popup.isVisible():
                            gp = self._preview_canvas.mapToGlobal(event.pos())
                            if not self._sensor_popup.geometry().contains(gp):
                                # clicking legend should toggle it back open again, so only close if not legend
                                if not self._legend_hit_test_qtpos(event.pos()):
                                    self._close_sensor_popup()

                        if event.button() == Qt.LeftButton and self._legend_hit_test_qtpos(event.pos()):
                            self._open_sensor_popup()
                            return True  # consume event
                    except Exception:
                        pass

        except Exception:
            pass

        return super().eventFilter(obj, event)

    # -------------------------------------------------------------------------
    # Legend-button sensor popup
    # -------------------------------------------------------------------------
    def _close_sensor_popup(self) -> None:
        try:
            if self._sensor_popup is not None:
                self._sensor_popup.close()
        except Exception:
            pass
        self._sensor_popup = None

    def _open_sensor_popup(self) -> None:
        """Open a closable popup near the legend that toggles visible series."""
        try:
            if self._preview_canvas is None or self._preview_ax is None or self._preview_fig is None:
                return
            if self._preview_leg is None:
                return

            # Toggle behavior
            if self._sensor_popup is not None and self._sensor_popup.isVisible():
                self._close_sensor_popup()
                return

            # Ensure renderer is ready
            try:
                self._preview_canvas.draw()
            except Exception:
                pass

            renderer = self._preview_canvas.get_renderer()
            if renderer is None:
                return

            bbox = self._preview_leg.get_window_extent(renderer)  # physical px (bottom-left origin)

            dpr = self._dpr()
            canvas_h_l = int(self._preview_canvas.height())

            # Convert to Qt logical coords (origin top-left)
            qt_x = int(bbox.x0 / dpr)
            qt_y = int(canvas_h_l - (bbox.y1 / dpr))

            global_pos = self._preview_canvas.mapToGlobal(QPoint(qt_x, qt_y))

            dlg = QDialog(self.parent)
            dlg.setAttribute(Qt.WA_DeleteOnClose, True)
            dlg.setWindowFlag(Qt.Popup, True)                 # click outside closes (when possible)
            dlg.setWindowFlag(Qt.FramelessWindowHint, True)   # clean popup
            dlg.setModal(False)

            root = QVBoxLayout(dlg)
            root.setContentsMargins(10, 10, 10, 10)
            root.setSpacing(8)

            lst = QListWidget()
            lst.setUniformItemSizes(True)

            # Build list from all columns in this run (chosen at start)
            names = list(self._preview_all_cols or [])
            if not names:
                names = list(self._preview_lines.keys())

            # Buttons
            btn_row = QHBoxLayout()
            btn_all = QPushButton("All")
            btn_none = QPushButton("None")
            btn_close = QPushButton("Close")
            btn_row.addWidget(btn_all)
            btn_row.addWidget(btn_none)
            btn_row.addStretch(1)
            btn_row.addWidget(btn_close)

            # Populate
            for name in names:
                it = QListWidgetItem(str(name))
                it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
                vis = bool(self._preview_series_visible.get(name, True))
                it.setCheckState(Qt.Checked if vis else Qt.Unchecked)
                lst.addItem(it)

            def _apply_from_list():
                try:
                    any_changed = False
                    for i in range(lst.count()):
                        it = lst.item(i)
                        name = it.text()
                        want_vis = (it.checkState() == Qt.Checked)
                        cur_vis = bool(self._preview_series_visible.get(name, True))
                        if want_vis != cur_vis:
                            self._preview_series_visible[name] = want_vis
                            ln = self._preview_lines.get(name)
                            if ln is not None:
                                ln.set_visible(want_vis)
                            any_changed = True

                    if any_changed:
                        self._preview_apply_visibility_styling()
                        self._preview_autoscale_y_to_visible()
                        self._preview_invalidate_interaction_cache()
                        self._safe_preview_redraw()
                except Exception:
                    pass

            def _set_all(state: bool):
                try:
                    for i in range(lst.count()):
                        it = lst.item(i)
                        it.setCheckState(Qt.Checked if state else Qt.Unchecked)
                except Exception:
                    pass
                _apply_from_list()

            lst.itemChanged.connect(lambda *_: _apply_from_list())
            btn_all.clicked.connect(lambda: _set_all(True))
            btn_none.clicked.connect(lambda: _set_all(False))
            btn_close.clicked.connect(lambda: dlg.close())

            root.addWidget(lst, 1)
            root.addLayout(btn_row)

            dlg.setStyleSheet("""
                QDialog { background: #171717; border: 1px solid #2A2A2A; border-radius: 10px; }
                QListWidget { background: #121212; border: 1px solid #2A2A2A; border-radius: 8px; color: #EAEAEA; }
                QListWidget::item { padding: 6px; }
                QPushButton { background: #222; border: 1px solid #333; padding: 6px 10px; border-radius: 8px; color: #EAEAEA; }
                QPushButton:hover { background: #2A2A2A; }
            """)

            dlg.resize(340, min(520, 80 + 26 * max(6, min(18, lst.count()))))

            self._sensor_popup = dlg

            dlg.move(global_pos)
            dlg.show()
            dlg.raise_()
            dlg.activateWindow()

            dlg.finished.connect(lambda *_: self._close_sensor_popup())

        except Exception:
            self._close_sensor_popup()

    def _preview_apply_visibility_styling(self) -> None:
        try:
            leg = self._preview_leg
            if leg is None:
                return
            texts = leg.get_texts()
            for t in texts:
                name = t.get_text()
                vis = bool(self._preview_series_visible.get(name, True))
                t.set_alpha(1.0 if vis else 0.35)
        except Exception:
            pass

    def _preview_autoscale_y_to_visible(self) -> None:
        try:
            ax = self._preview_ax
            if ax is None:
                return

            ys = []
            for name, ln in self._preview_lines.items():
                if ln is None:
                    continue
                if not bool(self._preview_series_visible.get(name, True)):
                    continue
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

            if ymin == ymax:
                pad = 1.0
            else:
                pad = 0.06 * (ymax - ymin)

            ax.set_ylim(ymin - pad, ymax + pad)
        except Exception:
            pass

    # -------------------------------------------------------------------------
    # Hover handlers
    # -------------------------------------------------------------------------
    def _on_preview_hover(self, event) -> None:
        try:
            if not getattr(self, "_app_is_active", True):
                return
            xdata = getattr(event, "xdata", None)
            ydata = getattr(event, "ydata", None)
            if xdata is None and ydata is None:
                return
            self._on_preview_hover_xy(xdata, ydata)
        except Exception:
            pass

    def _hide_preview_hover(self, hard: bool = False) -> None:
        try:
            if getattr(self, "_preview_vline", None) is not None:
                self._preview_vline.set_visible(False)
        except Exception:
            pass
        try:
            if getattr(self, "_preview_collective_box", None) is not None:
                self._preview_collective_box.set_visible(False)
        except Exception:
            pass
        try:
            self._tt_anim_timer.stop()
        except Exception:
            pass

        try:
            if hard or getattr(self, "_preview_bg", None) is None:
                self._safe_preview_redraw()
            else:
                self._preview_blit()
        except Exception:
            pass

    def _format_value(self, col_name: str, val: float) -> str:
        try:
            if val != val:
                return "-"
            s = str(col_name)
            if "[°C]" in s or "°C" in s:
                return f"{val:.2f} °C" if abs(val) < 100 else f"{val:.1f} °C"
            if "[W]" in s or " W" in s:
                return f"{val:.1f} W"
            if "[%]" in s or "%" in s:
                return f"{val:.1f} %"
            return f"{val:.3g}"
        except Exception:
            return "-"

    def _on_preview_hover_xy(self, xdata: float, ydata: float) -> None:
        try:
            if not getattr(self, "_app_is_active", True):
                return
            if self._preview_df is None or self._preview_x is None or self._preview_ax is None:
                return
            if xdata is None:
                return

            # If canvas geometry changed (minimize/restore/resize), invalidate caches and redraw once.
            try:
                if self._preview_canvas is not None:
                    wh = (int(self._preview_canvas.width()), int(self._preview_canvas.height()))
                    if getattr(self, "_preview_last_canvas_wh", None) != wh:
                        self._preview_last_canvas_wh = wh
                        self._preview_invalidate_interaction_cache()
                        self._preview_relayout_and_redraw()
            except Exception:
                pass

            # throttle
            try:
                now = time.time()
                if (now - getattr(self, "_hover_last_ts", 0.0)) < getattr(self, "_hover_min_interval", 0.0):
                    return
                self._hover_last_ts = now
            except Exception:
                pass

            # if outside x-range -> hide
            try:
                x0, x1 = self._preview_ax.get_xlim()
                if xdata < min(x0, x1) or xdata > max(x0, x1):
                    self._hide_preview_hover(hard=True)
                    return
            except Exception:
                pass

            try:
                idx = int(np.argmin(np.abs(self._preview_x - xdata)))
            except Exception:
                return
            idx = max(0, min(idx, len(self._preview_x) - 1))
            row = self._preview_df.iloc[idx]

            # vertical dotted cursor line
            try:
                vl = getattr(self, "_preview_vline", None)
                if vl is not None:
                    vl.set_xdata([xdata, xdata])
                    vl.set_visible(True)
            except Exception:
                pass

            # time string
            try:
                dt_current = mdates.num2date(self._preview_x[idx])
                dt_start = mdates.num2date(self._preview_x[0])
                elapsed = dt_current - dt_start
                total_seconds = int(elapsed.total_seconds())
                hours = total_seconds // 3600
                minutes = (total_seconds % 3600) // 60
                seconds = total_seconds % 60
                tstr = f"{hours}:{minutes:02d}:{seconds:02d}" if hours > 0 else f"{minutes}:{seconds:02d}"
            except Exception:
                tstr = f"{idx}"

            # build visible sensor list
            sensor_data = []
            cols = list(self._preview_df.columns)
            for i, col in enumerate(cols):
                if not bool(self._preview_series_visible.get(str(col), True)):
                    continue

                try:
                    val = float(row.iloc[i])
                except Exception:
                    val = float("nan")
                try:
                    color = self._preview_colors[i] if i < len(self._preview_colors) else "#FFFFFF"
                except Exception:
                    color = "#FFFFFF"
                sensor_data.append((str(col), val, color))

            def _sort_key(t):
                v = t[1]
                return (v if v == v else -1e30)

            sensor_data.sort(key=_sort_key, reverse=True)

            # update tooltip
            ab = getattr(self, "_preview_collective_box", None)
            if ab is not None:
                try:
                    if getattr(self, "_preview_collective_time", None) is not None:
                        self._preview_collective_time.set_text(tstr)
                except Exception:
                    pass

                name_areas = getattr(self, "_preview_name_areas", None) or []
                value_areas = getattr(self, "_preview_value_areas", None) or []

                MAX_NAME_CHARS = 70

                def _shorten(s: str, cap: int) -> str:
                    s = str(s)
                    return s if len(s) <= cap else (s[: cap - 1] + "…")

                n = min(len(sensor_data), len(name_areas), len(value_areas))
                for j in range(n):
                    name, val, color = sensor_data[j]
                    name = _shorten(name, MAX_NAME_CHARS)
                    vtxt = self._format_value(name, val)

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

                for j in range(n, len(name_areas)):
                    try:
                        name_areas[j].set_text("")
                    except Exception:
                        pass
                for j in range(n, len(value_areas)):
                    try:
                        value_areas[j].set_text("")
                    except Exception:
                        pass

                ab.set_visible(True)

            # ensure background cache exists
            try:
                if getattr(self, "_preview_bg", None) is None and self._preview_canvas is not None:
                    self._preview_canvas.draw()
                    self._on_preview_draw()
            except Exception:
                pass

            # edge flip mode
            try:
                ty = ydata if ydata is not None else 0.0
                self._preview_update_tooltip_mode_for(xdata, ty)
            except Exception:
                ty = ydata if ydata is not None else 0.0

            # smooth tooltip movement
            try:
                ab = getattr(self, "_preview_collective_box", None)
                if ab is not None:
                    if ydata is None:
                        try:
                            y0, y1 = self._preview_ax.get_ylim()
                            ty = 0.5 * (y0 + y1)
                        except Exception:
                            ty = 0.0

                    cur = getattr(ab, "xy", None)
                    if not cur or len(cur) != 2:
                        cur = (xdata, ty)

                    self._tt_anim_start_xy = (float(cur[0]), float(cur[1]))
                    self._tt_anim_target_xy = (float(xdata), float(ty))
                    self._tt_anim_t0 = time.time()

                    if not self._tt_anim_timer.isActive():
                        self._tt_anim_timer.start()
            except Exception:
                try:
                    if ab is not None:
                        ab.xy = (xdata, ty)
                        ab.set_visible(True)
                except Exception:
                    pass

            # redraw fast
            try:
                self._preview_blit()
            except Exception:
                try:
                    if self._preview_canvas is not None:
                        self._preview_canvas.draw_idle()
                except Exception:
                    pass

        except Exception:
            pass

    # -------------------------------------------------------------------------
    # Tooltip rebuild
    # -------------------------------------------------------------------------
    def _rebuild_tooltip(self, cols: list[str]) -> None:
        try:
            ax = self._preview_ax
            fig = self._preview_fig
            if ax is None or fig is None:
                return

            try:
                old = getattr(self, "_preview_collective_box", None)
                if old is not None:
                    old.set_visible(False)
                    try:
                        old.remove()
                    except Exception:
                        pass
            except Exception:
                pass

            dpi = float(getattr(fig, "dpi", 100) or 100)
            sep_pts = 5.0 * 72.0 / dpi

            self._preview_collective_time = TextArea(
                "",
                textprops=dict(color="#FFFFFF", family="DejaVu Sans Mono", fontsize=10, weight="bold"),
            )

            self._preview_name_areas = [
                TextArea("", textprops=dict(color="#FFFFFF", family="DejaVu Sans Mono", fontsize=10))
                for _ in cols
            ]
            self._preview_value_areas = [
                TextArea("", textprops=dict(color="#FFFFFF", family="DejaVu Sans Mono", fontsize=10))
                for _ in cols
            ]

            left_col = VPacker(children=self._preview_name_areas, align="left", pad=0, sep=3)
            right_col = VPacker(children=self._preview_value_areas, align="right", pad=0, sep=3)
            two_col = HPacker(children=[left_col, right_col], align="top", pad=0, sep=sep_pts)

            vbox = VPacker(children=[self._preview_collective_time, two_col], align="left", pad=0, sep=4)

            self._preview_collective_box = AnnotationBbox(
                vbox,
                (self._preview_x[0], 0),
                xybox=self._preview_tt_default_xybox,
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
                self._preview_collective_box.patch.set_path_effects([
                    pe.withSimplePatchShadow(offset=(1, -1), shadow_rgbFace=(0, 0, 0), alpha=0.35),
                    pe.Normal(),
                ])
            except Exception:
                pass

            self._preview_collective_box._box_alignment = (0, 0)
            self._preview_collective_box.set_visible(False)
            self._preview_collective_box.set_clip_on(False)
            self._preview_collective_box.set_animated(True)
            ax.add_artist(self._preview_collective_box)
        except Exception:
            self._preview_collective_box = None
            self._preview_collective_time = None
            self._preview_name_areas = None
            self._preview_value_areas = None

    # -------------------------------------------------------------------------
    # Plotting
    # -------------------------------------------------------------------------
    def _plot_run_csv(self, fpath: str) -> None:
        """Plot sensor data from a CSV file (uses columns in the CSV = sensors chosen for that run)."""
        if self._preview_canvas is None or self._preview_ax is None:
            raise RuntimeError("Preview canvas unavailable")

        self._close_sensor_popup()

        df = pd.read_csv(fpath, header=0)
        if df.shape[0] == 0:
            raise RuntimeError("Empty CSV")

        # datetime handling
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

        cols = []
        for c in list(df_data.columns):
            y = pd.to_numeric(df_data[c], errors="coerce")
            if y.notna().any():
                cols.append(str(c))

        if not cols:
            raise RuntimeError("No numeric series found in CSV")

        self._preview_all_cols = list(cols)

        is_dt = df_data.index.dtype.kind == "M"
        if is_dt:
            x_vals = mdates.date2num(df_data.index.to_pydatetime())
        else:
            x_vals = np.arange(len(df_data))

        # reset any running tooltip animation
        try:
            self._tt_anim_timer.stop()
        except Exception:
            pass
        self._tt_anim_start_xy = None
        self._tt_anim_target_xy = None

        self._preview_ax.clear()

        # reset legend + base axes rect
        self._preview_leg = None
        self._preview_last_right_frac = 0.98
        self._preview_apply_axes_rect(right_frac=0.98, left_margin_px=self._preview_left_margin_px_base)

        # reset series state
        self._preview_lines = {}
        self._preview_series_data = {}
        self._preview_series_visible = {name: True for name in cols}

        # theme
        try:
            self._preview_fig.set_facecolor("#121212")
        except Exception:
            pass
        try:
            self._preview_ax.set_facecolor("#121212")

            for side in ("left", "right"):
                self._preview_ax.spines[side].set_visible(False)

            for side in ("top", "bottom"):
                sp = self._preview_ax.spines[side]
                sp.set_visible(True)
                sp.set_color(self._preview_grid_color)
                sp.set_linewidth(0.9)
                sp.set_linestyle(self._preview_dot_dashes)
                sp.set_alpha(0.9)

            self._preview_ax.tick_params(axis="both", length=0)
            self._preview_ax.tick_params(axis="x", colors="#BDBDBD")
            self._preview_ax.tick_params(axis="y", colors="#BDBDBD")
            self._preview_ax.xaxis.label.set_color("#EAEAEA")
            self._preview_ax.yaxis.label.set_color("#EAEAEA")
        except Exception:
            pass

        # grid
        try:
            self._preview_ax.grid(
                True,
                which="major",
                axis="y",
                color=self._preview_grid_color,
                linewidth=0.9,
            )
            for gl in self._preview_ax.get_ygridlines():
                gl.set_linestyle(self._preview_dot_dashes)
                gl.set_alpha(0.9)
        except Exception:
            pass

        # plot series
        cmap = cm.get_cmap("tab20")

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

        self._preview_colors = []
        used_cols_for_df = []
        for i, c in enumerate(cols):
            y = pd.to_numeric(df_data[c], errors="coerce").to_numpy(dtype=float)
            used_cols_for_df.append(c)

            colc = cmap(i % 20)
            try:
                if isinstance(colc, tuple):
                    import matplotlib.colors as mcolors
                    colc = mcolors.to_hex(colc)
            except Exception:
                pass
            self._preview_colors.append(colc)

            if is_dt:
                ln = self._preview_ax.plot_date(x_vals, y, "-", color=colc, **line_kwargs)[0]
            else:
                ln = self._preview_ax.plot(x_vals, y, "-", color=colc, **line_kwargs)[0]

            try:
                ln.set_path_effects([
                    pe.Stroke(linewidth=glow_lw, foreground=colc, alpha=glow_alpha),
                    pe.Normal()
                ])
            except Exception:
                pass

            self._preview_lines[str(c)] = ln
            self._preview_series_data[str(c)] = y

        # legend
        try:
            self._preview_leg = self._preview_ax.legend(
                used_cols_for_df,
                loc="upper right",
                fontsize=8,
                frameon=True,
                bbox_to_anchor=(1.0, float(self._preview_top_frac)),
                bbox_transform=self._preview_fig.transFigure,
                borderaxespad=0.0,
            )
            leg = self._preview_leg
            if leg:
                leg.get_frame().set_facecolor("#171717")
                leg.get_frame().set_edgecolor("#2A2A2A")
                leg.get_frame().set_alpha(0.9)
                for text in leg.get_texts():
                    text.set_color("#FFFFFF")
        except Exception:
            self._preview_leg = None

        # store for hover
        self._preview_x = x_vals
        self._preview_df = df_data[used_cols_for_df]

        # xlim no padding
        try:
            if len(x_vals) > 0:
                self._preview_ax.set_xlim(left=x_vals[0], right=x_vals[-1])
        except Exception:
            pass

        # elapsed time x-axis labels
        if is_dt and len(x_vals) > 0:
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

                self._preview_ax.xaxis.set_major_formatter(FuncFormatter(elapsed_time_formatter))
                self._preview_ax.set_xlabel("")
            except Exception:
                pass

        # vertical hover line
        try:
            self._preview_vline = self._preview_ax.axvline(
                self._preview_x[0],
                color=self._preview_grid_color,
                linewidth=0.9,
                alpha=0.9,
                zorder=900,
            )
            self._preview_vline.set_linestyle(self._preview_dot_dashes)
            self._preview_vline.set_clip_on(True)
            self._preview_vline.set_visible(False)
            self._preview_vline.set_animated(True)
        except Exception:
            self._preview_vline = None

        # tooltip rebuild
        self._rebuild_tooltip(used_cols_for_df)

        # default autoscale to all visible
        self._preview_autoscale_y_to_visible()

        # show canvas
        try:
            self._preview_label.clear()
            self._preview_label.hide()
        except Exception:
            pass
        try:
            self._preview_canvas.show()
        except Exception:
            pass

        # connect mpl hover handler
        try:
            if self._preview_mpl_cid is not None:
                try:
                    self._preview_canvas.mpl_disconnect(self._preview_mpl_cid)
                except Exception:
                    pass
            self._preview_mpl_cid = self._preview_canvas.mpl_connect("motion_notify_event", self._on_preview_hover)
        except Exception:
            self._preview_mpl_cid = None

        # IMPORTANT: do NOT connect mpl button_press_event for legend anymore (Qt eventFilter handles it)
        try:
            if self._preview_click_cid is not None:
                try:
                    self._preview_canvas.mpl_disconnect(self._preview_click_cid)
                except Exception:
                    pass
            self._preview_click_cid = None
        except Exception:
            self._preview_click_cid = None

        # relayout for legend AND y-label fit, then cache background
        self._preview_relayout_and_redraw()

        # dim legend if any invisible (none initially)
        self._preview_apply_visibility_styling()

    # -------------------------------------------------------------------------
    # END
    # -------------------------------------------------------------------------
