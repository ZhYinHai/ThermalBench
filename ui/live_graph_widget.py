from __future__ import annotations

import math
import time
from collections import deque
from datetime import datetime
from typing import Optional

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QFrame, QVBoxLayout, QSizePolicy

from matplotlib.figure import Figure
import matplotlib.patheffects as pe

from ui.interactive_canvas import InteractiveCanvas
from ui.graph_preview.graph_plot_helpers import (
    apply_dark_axes_style,
    build_tab20_color_map,
    group_columns_by_unit,
    get_measurement_type_label,
)


class LiveGraphWidget(QFrame):
    """Live graph that updates from the continuous HWiNFO CSV stream.

    Optimized + responsive:
    - Build all axes + all line artists once (for all columns)
    - (De)selection toggles line visibility instead of rebuilding the figure
    - Hide empty axes (units) and re-layout only when axis visibility changes
    - Coalesce rapid selection changes with a 0ms single-shot timer
    - Phase boundary marker (warmup/log)
    - Manual layout that respects widget edges (no "bleed" past rounded frame)
    """

    def __init__(self, parent=None):
        super().__init__(parent)

        self._columns: list[str] = []
        self._active: set[str] = set()

        self._t0: Optional[float] = None
        self._max_points = 20000

        self._buffers: dict[str, deque[tuple[float, float]]] = {}
        self._color_map: dict[str, str] = {}

        # Track runtime for x-axis (Matplotlib autoscale gets disabled by set_xlim)
        self._x_max: float = 0.0

        self._fig = Figure(figsize=(5, 3))
        self._canvas = InteractiveCanvas(self._fig)

        # (Qt) ensure canvas doesn't "paint outside" rounded frame visually
        try:
            self._canvas.setAttribute(Qt.WA_TranslucentBackground, False)
            self._canvas.setStyleSheet("background: transparent;")
        except Exception:
            pass

        self._axes: list[object] = []
        self._lines: dict[str, object] = {}
        self._line_to_ax: dict[str, object] = {}
        self._unit_axes: dict[str, object] = {}
        self._unit_cols: dict[str, list[str]] = {}
        self._unit_order: list[str] = []

        # warmup/log marker
        self._phase_x: Optional[float] = None
        self._phase_lines: list[object] = []
        self._phase_texts: list[tuple[object, object]] = []  # per axis: (warmup_text, log_text)

        self._last_draw_ts = 0.0

        # Coalesce rapid checkbox changes (same Qt event-loop tick)
        self._pending_active: Optional[set[str]] = None
        self._apply_active_timer = QTimer(self)
        self._apply_active_timer.setSingleShot(True)
        self._apply_active_timer.setInterval(0)
        self._apply_active_timer.timeout.connect(self._apply_pending_active)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._canvas, 1)

        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        self.setObjectName("LiveGraph")
        self.setStyleSheet(
            """
            QFrame#LiveGraph { background: #1A1A1A; border: 1px solid #2A2A2A; border-radius: 10px; }
            """
        )

        try:
            self._fig.set_facecolor("#121212")
        except Exception:
            pass

    def start(self, *, columns: list[str]) -> None:
        self._columns = [str(c) for c in (columns or []) if str(c).strip()]
        self._active = set(self._columns)

        self._color_map = build_tab20_color_map(self._columns) if self._columns else {}
        self.setVisible(bool(self._columns))

        self.reset_window()

    def stop(self) -> None:
        pass

    def reset_window(self) -> None:
        # reset boundary
        self._phase_x = None
        self._phase_lines.clear()
        self._phase_texts.clear()

        self._t0 = None
        self._x_max = 0.0
        self._buffers = {c: deque(maxlen=self._max_points) for c in self._columns}
        self._build_plot_once()

    # ----------------------------
    # Fast (de)selection handling
    # ----------------------------
    def set_active_columns(self, columns: list[str]) -> None:
        new_active = {str(c) for c in (columns or []) if str(c).strip()}
        if new_active == self._active:
            return
        self._pending_active = new_active
        self._apply_active_timer.start()

    def _apply_pending_active(self) -> None:
        if self._pending_active is None:
            return

        self._active = set(self._pending_active)
        self._pending_active = None

        if not self._active:
            self.setVisible(False)
            self.updateGeometry()
            return

        if not self.isVisible():
            self.setVisible(True)
            self.updateGeometry()

        self._apply_active_visibility(layout=True)
        self._refresh_from_buffers(full=False)

        try:
            self._canvas.draw_idle()
        except Exception:
            pass

    # ----------------------------
    # One-time plot build
    # ----------------------------
    def _build_plot_once(self) -> None:
        try:
            self._fig.clear()
        except Exception:
            return

        self._axes = []
        self._lines = {}
        self._line_to_ax = {}
        self._unit_axes = {}
        self._unit_cols = {}
        self._unit_order = []

        if not self._columns:
            try:
                self._canvas.draw_idle()
            except Exception:
                pass
            return

        groups_all = group_columns_by_unit(self._columns) if self._columns else {}
        units = [u for u, cols in groups_all.items() if cols]
        if not units:
            try:
                self._canvas.draw_idle()
            except Exception:
                pass
            return

        # Match LiveMonitorWidget ordering: keep Temperature on top.
        _prio = {
            "Temperature": 0,
            "Voltage (V)": 1,
            "Clock (MHz)": 2,
            "Timing (T)": 3,
            "Power (W)": 4,
            "RPM": 5,
            "Percentage (%)": 6,
        }
        self._unit_order = list(units)
        self._unit_order.sort(
            key=lambda u: (
                _prio.get(get_measurement_type_label(u), 99),
                get_measurement_type_label(u),
            )
        )
        self._unit_cols = {u: list(groups_all.get(u, []) or []) for u in units}

        n = len(self._unit_order)
        for i, unit in enumerate(self._unit_order):
            ax = self._fig.add_subplot(n, 1, i + 1)
            self._axes.append(ax)
            self._unit_axes[unit] = ax

            apply_dark_axes_style(self._fig, ax, grid_color="#2A2A2A", dot_dashes=(1, 2))

            try:
                ax.margins(x=0.0)
                ax.set_xmargin(0.0)
            except Exception:
                pass

            try:
                ax.tick_params(axis="x", colors="#BDBDBD")
                ax.tick_params(axis="y", colors="#BDBDBD")
            except Exception:
                pass

            if i != (n - 1):
                try:
                    ax.set_xticklabels([])
                except Exception:
                    pass
            else:
                try:
                    ax.set_xlabel("Time (s)")
                except Exception:
                    pass

            try:
                if unit and unit != "other":
                    ax.set_ylabel(f"[{unit}]", fontsize=9)
            except Exception:
                pass

            for col in self._unit_cols.get(unit, []):
                try:
                    colc = self._color_map.get(col, "#FFFFFF")
                    (ln,) = ax.plot(
                        [],
                        [],
                        linewidth=2.6,
                        alpha=0.98,
                        color=colc,
                        solid_capstyle="round",
                        solid_joinstyle="round",
                        antialiased=True,
                        zorder=10,
                    )
                    try:
                        ln.set_path_effects(
                            [
                                pe.Stroke(linewidth=4.6, foreground=colc, alpha=0.18),
                                pe.Normal(),
                            ]
                        )
                    except Exception:
                        pass
                    self._lines[col] = ln
                    self._line_to_ax[col] = ax
                except Exception:
                    continue

        self._apply_active_visibility(layout=True)
        self._refresh_from_buffers(full=True)

        try:
            self._canvas.draw_idle()
        except Exception:
            pass

    def _apply_active_visibility(self, *, layout: bool) -> None:
        for col, ln in self._lines.items():
            try:
                ln.set_visible(col in self._active)
            except Exception:
                pass

        axes_visibility_changed = False
        for unit in self._unit_order:
            ax = self._unit_axes.get(unit)
            if ax is None:
                continue
            cols = self._unit_cols.get(unit, [])
            has_any = any(c in self._active for c in cols)

            try:
                prev = bool(ax.get_visible())
            except Exception:
                prev = True
            try:
                ax.set_visible(bool(has_any))
            except Exception:
                pass
            try:
                now = bool(ax.get_visible())
            except Exception:
                now = prev

            if prev != now:
                axes_visibility_changed = True

        if layout and axes_visibility_changed:
            self._relayout_visible_axes()

    # ----------------------------
    # Sample ingestion + drawing
    # ----------------------------
    def on_sample(self, sample: object) -> None:
        try:
            if not isinstance(sample, dict):
                return
            values = sample.get("values")
            if not isinstance(values, dict) or not values:
                return

            ts = sample.get("ts")
            t = None
            if isinstance(ts, (int, float)) and math.isfinite(float(ts)):
                t = float(ts)
            elif isinstance(ts, datetime):
                t = ts.timestamp()
            if t is None:
                t = time.time()

            if self._t0 is None:
                self._t0 = t
            x = float(t - self._t0)

            # Track runtime so x-axis always grows
            if x > self._x_max:
                self._x_max = x

            updated_active = False
            for col, v in values.items():
                if col not in self._buffers:
                    continue
                try:
                    y = float(v)
                except Exception:
                    continue
                self._buffers[col].append((x, y))
                if col in self._active:
                    updated_active = True

            if not updated_active:
                return

            now = time.time()
            if (now - self._last_draw_ts) < 0.10:
                return
            self._last_draw_ts = now

            for col in self._active:
                ln = self._lines.get(col)
                if ln is None:
                    continue
                buf = self._buffers.get(col)
                if not buf:
                    continue
                xs = [p[0] for p in buf]
                ys = [p[1] for p in buf]
                try:
                    ln.set_data(xs, ys)
                except Exception:
                    pass

            for ax in self._axes:
                try:
                    if not ax.get_visible():
                        continue
                except Exception:
                    pass
                self._autoscale_visible_x(ax)
                self._autoscale_visible_y(ax)

            self._update_phase_labels()

            try:
                self._canvas.draw_idle()
            except Exception:
                pass
        except Exception:
            pass

    def _refresh_from_buffers(self, *, full: bool) -> None:
        try:
            cols_iter = list(self._lines.keys()) if full else list(self._active)

            for col in cols_iter:
                ln = self._lines.get(col)
                if ln is None:
                    continue
                buf = self._buffers.get(col)
                if not buf:
                    continue
                xs = [p[0] for p in buf]
                ys = [p[1] for p in buf]
                try:
                    ln.set_data(xs, ys)
                except Exception:
                    pass

            for ax in self._axes:
                try:
                    if not ax.get_visible():
                        continue
                except Exception:
                    pass
                self._autoscale_visible_x(ax)
                self._autoscale_visible_y(ax)

            self._update_phase_labels()
        except Exception:
            pass

    # ----------------------------
    # Layout helpers
    # ----------------------------
    def _relayout_visible_axes(self) -> None:
        """Reposition only the visible axes so they fill the whole widget (no bleed past rounded frame)."""
        try:
            vis_axes = []
            for unit in self._unit_order:
                ax = self._unit_axes.get(unit)
                if ax is None:
                    continue
                try:
                    if ax.get_visible():
                        vis_axes.append(ax)
                except Exception:
                    vis_axes.append(ax)

            n = len(vis_axes)
            if n <= 0:
                return

            left = 0.065
            right = 0.985
            bottom = 0.12
            top = 0.965

            if right <= left:
                right = min(0.99, left + 0.2)

            total_h = max(0.0, top - bottom)
            gap = 0.06 if n > 1 else 0.0
            h = (total_h - gap * (n - 1)) / float(n) if n > 0 else total_h
            if h <= 0:
                h = total_h / float(n)

            for i, ax in enumerate(vis_axes):
                y0 = top - (i + 1) * h - i * gap
                w = max(0.01, right - left)

                if left < 0.0:
                    left = 0.0
                if (left + w) > 1.0:
                    w = 1.0 - left
                if y0 < 0.0:
                    y0 = 0.0
                if (y0 + h) > 1.0:
                    h = 1.0 - y0
                try:
                    ax.set_position([left, y0, w, h])
                except Exception:
                    pass

            for ax in vis_axes[:-1]:
                try:
                    ax.set_xticklabels([])
                except Exception:
                    pass
                try:
                    ax.set_xlabel("")
                except Exception:
                    pass
            try:
                vis_axes[-1].set_xlabel("Time (s)")
            except Exception:
                pass
        except Exception:
            pass

    def _autoscale_visible_y(self, ax: object) -> None:
        try:
            y_min = None
            y_max = None

            for col, ln in self._lines.items():
                if self._line_to_ax.get(col) is not ax:
                    continue
                try:
                    if not ln.get_visible():
                        continue
                except Exception:
                    pass

                try:
                    ys = ln.get_ydata()
                except Exception:
                    continue
                if ys is None or len(ys) == 0:
                    continue

                try:
                    local_min = float(min(ys))
                    local_max = float(max(ys))
                except Exception:
                    continue

                if y_min is None or local_min < y_min:
                    y_min = local_min
                if y_max is None or local_max > y_max:
                    y_max = local_max

            if y_min is None or y_max is None:
                return

            span = float(y_max - y_min)
            if span <= 1e-12:
                pad = max(0.5, abs(y_min) * 0.01)
            else:
                pad = span * 0.08

            ax.set_ylim(y_min - pad, y_max + pad)
        except Exception:
            pass

    def _autoscale_visible_x(self, ax: object) -> None:
        """Keep x anchored at 0 and extend to current runtime (fixes 'stuck at first 5s')."""
        try:
            # Ensure boundary is always visible too
            xmax = float(self._x_max)
            if self._phase_x is not None and float(self._phase_x) > xmax:
                xmax = float(self._phase_x)

            # Avoid a too-tight right edge; small pad scales with runtime
            pad = max(0.5, xmax * 0.02)
            right = max(1.0, xmax + pad)

            ax.set_xlim(0.0, right)
        except Exception:
            pass

    # ----------------------------
    # Phase boundary (warmup/log)
    # ----------------------------
    def mark_phase_boundary(self) -> None:
        try:
            if self._phase_x is not None:
                return
            if self._t0 is None:
                return

            x = float(time.time() - self._t0)
            self._phase_x = x

            for ln in list(self._phase_lines):
                try:
                    ln.remove()
                except Exception:
                    pass
            self._phase_lines.clear()

            for wt, lt in list(self._phase_texts):
                try:
                    wt.remove()
                except Exception:
                    pass
                try:
                    lt.remove()
                except Exception:
                    pass
            self._phase_texts.clear()

            for ax in self._axes:
                try:
                    vln = ax.axvline(
                        x,
                        linewidth=1.6,
                        linestyle=(0, (3, 3)),
                        color="#BDBDBD",
                        alpha=0.55,
                        zorder=50,
                    )
                    self._phase_lines.append(vln)

                    warm = ax.text(
                        0.15, 0.92, "Warmup",
                        transform=ax.transAxes,
                        ha="center", va="top",
                        fontsize=9,
                        color="#BDBDBD",
                        alpha=0.85,
                        zorder=60,
                    )
                    log = ax.text(
                        0.85, 0.92, "Log",
                        transform=ax.transAxes,
                        ha="center", va="top",
                        fontsize=9,
                        color="#BDBDBD",
                        alpha=0.85,
                        zorder=60,
                    )
                    self._phase_texts.append((warm, log))
                except Exception:
                    continue

            self._update_phase_labels()

            try:
                self._canvas.draw_idle()
            except Exception:
                pass
        except Exception:
            pass

    def _update_phase_labels(self) -> None:
        try:
            if self._phase_x is None:
                return

            for i, ax in enumerate(self._axes):
                if i >= len(self._phase_texts):
                    break

                try:
                    if not ax.get_visible():
                        continue
                except Exception:
                    pass

                try:
                    x0, x1 = ax.get_xlim()
                except Exception:
                    continue

                denom = float(x1 - x0)
                if denom <= 1e-12:
                    continue

                frac = (float(self._phase_x) - float(x0)) / denom
                if frac < 0.0:
                    frac = 0.0
                if frac > 1.0:
                    frac = 1.0

                warm_txt, log_txt = self._phase_texts[i]
                warm_x = frac * 0.5
                log_x = frac + (1.0 - frac) * 0.5

                try:
                    warm_txt.set_x(warm_x)
                    log_txt.set_x(log_x)
                except Exception:
                    pass
        except Exception:
            pass

    def resizeEvent(self, event) -> None:
        try:
            super().resizeEvent(event)
        except Exception:
            pass

        try:
            self._relayout_visible_axes()
            self._update_phase_labels()
            self._canvas.draw_idle()
        except Exception:
            pass
