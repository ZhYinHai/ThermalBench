# ui/live_monitor_widget.py

from __future__ import annotations

import csv
import os
import time
from datetime import datetime
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QTimer, Qt, Signal
from PySide6.QtGui import QColor, QBrush, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QTreeWidget,
    QTreeWidgetItem,
    QHeaderView,
    QVBoxLayout,
    QSizePolicy,
)

from core.hwinfo_csv import read_hwinfo_headers, sensor_leafs_from_header, make_unique
from ui.graph_preview.graph_plot_helpers import group_columns_by_unit, get_measurement_type_label
from ui.graph_preview.graph_plot_helpers import build_tab20_color_map


@dataclass
class _OnlineStats:
    n: int = 0
    mean: float = 0.0
    min_v: float = float("inf")
    max_v: float = float("-inf")
    cur: float = float("nan")

    def push(self, x: float) -> None:
        self.cur = x
        if x < self.min_v:
            self.min_v = x
        if x > self.max_v:
            self.max_v = x
        self.n += 1
        # Welford mean update
        self.mean += (x - self.mean) / float(self.n)


class LiveMonitorWidget(QFrame):
    """Live stats table for the continuous HWiNFO CSV (min/max/avg/current)."""

    sample_updated = Signal(object)
    active_columns_changed = Signal(list)

    def __init__(self, parent=None):
        super().__init__(parent)

        self._csv_path: str = ""
        self._columns: list[str] = []
        self._color_map: dict[str, str] = {}

        self._header_index: dict[str, int] = {}
        self._date_idx: Optional[int] = None
        self._time_idx: Optional[int] = None
        self._items: dict[str, QTreeWidgetItem] = {}
        self._stats: dict[str, _OnlineStats] = {}
        self._enabled: set[str] = set()
        self._is_rebuilding = False

        self._last_size: Optional[int] = None
        self._last_mtime: Optional[float] = None
        self._last_emit_ts: float = 0.0

        # Optional ambient sidecar CSV written by ambient_logger.py
        self._ambient_csv_path: str = ""
        self._ambient_col_name: str = "Ambient [°C]"

        self._timer = QTimer(self)
        self._timer.setInterval(200)
        self._timer.timeout.connect(self._tick)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.tree = QTreeWidget()
        self.tree.setColumnCount(5)
        self.tree.setHeaderLabels(["Measurement", "Current", "Min", "Max", "Avg"])
        self.tree.setRootIsDecorated(False)
        self.tree.setUniformRowHeights(True)
        self.tree.setSortingEnabled(False)
        self.tree.setSelectionMode(QAbstractItemView.NoSelection)
        self.tree.setFocusPolicy(Qt.NoFocus)
        self.tree.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.tree.setFrameShape(QFrame.NoFrame)
        self.tree.setLineWidth(0)

        hdr = self.tree.header()
        hdr.setSectionsClickable(False)
        hdr.setSectionsMovable(False)
        hdr.setStretchLastSection(False)
        hdr.setSectionResizeMode(0, QHeaderView.Stretch)
        for c in range(1, 5):
            hdr.setSectionResizeMode(c, QHeaderView.ResizeToContents)

        self.tree.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.tree.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        try:
            self.tree.itemClicked.connect(self._on_item_clicked)
        except Exception:
            pass

        root.addWidget(self.tree, 1)

        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        # Match Legend & Stats popup look
        self.setObjectName("LiveMonitor")
        self.setStyleSheet(
            """
            QFrame#LiveMonitor { background: #1A1A1A; border: 1px solid #2A2A2A; border-radius: 10px; }
            QTreeWidget { background: transparent; border: none; color: #EAEAEA; outline: none; }
            QTreeWidget::item { padding: 6px 6px; background: transparent; }
            QTreeWidget::item:hover { background: rgba(255,255,255,0.06); }
            QTreeWidget::item:selected, QTreeWidget::item:selected:hover { background: transparent; }

            QHeaderView { background: #151515; }
            QHeaderView::section {
                background: transparent;
                color: #9A9A9A;
                font-weight: 600;
                padding: 6px 10px;
                border: none;
            }
            QHeaderView::viewport { background: #151515; margin: 0px; padding: 0px; border: none; }
            """
        )

    def start(self, *, csv_path: str, columns: list[str]) -> None:
        self._csv_path = str(csv_path or "").strip()
        self._columns = [str(c) for c in (columns or []) if str(c).strip()]
        self._color_map = build_tab20_color_map(self._columns) if self._columns else {}

        self._header_index = {}
        self._date_idx = None
        self._time_idx = None
        self._stats = {c: _OnlineStats() for c in self._columns}
        # Default to temperature sensors only during runs; user can toggle others on.
        self._enabled = set(self._default_enabled_columns(self._columns))
        self._items = {}
        self._last_size = None
        self._last_mtime = None
        self._last_emit_ts = 0.0

        # Keep any previously provided ambient CSV path (it may arrive slightly
        # after start via the runner stdout hook).
        self._ambient_csv_path = str(getattr(self, "_ambient_csv_path", "") or "").strip()

        self._rebuild_rows()

        try:
            enabled_sorted = [c for c in self._columns if c in self._enabled]
            self.active_columns_changed.emit(enabled_sorted)
        except Exception:
            pass

        try:
            if self._columns:
                self.tree.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
            else:
                self.tree.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        except Exception:
            pass

        self._timer.start()

    def _default_enabled_columns(self, columns: list[str]) -> list[str]:
        cols = [str(c) for c in (columns or []) if str(c).strip()]
        if not cols:
            return []

        # Use the same unit parsing helpers as the graph/legend.
        temp_cols: list[str] = []
        try:
            groups = group_columns_by_unit(cols)
            for unit, unit_cols in (groups or {}).items():
                if get_measurement_type_label(unit) == "Temperature":
                    for c in unit_cols or []:
                        if c not in temp_cols:
                            temp_cols.append(str(c))
        except Exception:
            temp_cols = []

        # If we found temperature sensors, default to those; otherwise keep all.
        return temp_cols if temp_cols else cols

    def set_ambient_csv(self, path: str) -> None:
        """Provide ambient logger CSV path for live ambient stats/plotting."""
        try:
            self._ambient_csv_path = str(path or "").strip()
        except Exception:
            self._ambient_csv_path = ""

    def stop(self) -> None:
        try:
            self._timer.stop()
        except Exception:
            pass

    def reset_window_stats(self) -> None:
        """Reset min/max/avg counters (used when warmup ends and log window begins)."""
        try:
            self._stats = {c: _OnlineStats() for c in self._columns}
            # Clear displayed aggregates; current will repopulate on next tick.
            for col in self._columns:
                it = self._items.get(col)
                if it is None:
                    continue
                it.setText(2, "-")
                it.setText(3, "-")
                it.setText(4, "-")
        except Exception:
            pass

    def _rebuild_rows(self) -> None:
        self._is_rebuilding = True
        try:
            self.tree.clear()
            self._items.clear()

            groups = group_columns_by_unit(self._columns) if self._columns else {}

            # Stable ordering with Temperature always on top
            _prio = {
                "Temperature": 0,
                "Power (W)": 1,
                "RPM": 2,
                "Voltage (V)": 3,
                "Percentage (%)": 4,
                "Clock (MHz)": 5,
                "Timing (T)": 6,
            }

            ordered_units = list(groups.keys())
            ordered_units.sort(key=lambda u: (_prio.get(get_measurement_type_label(u), 99), get_measurement_type_label(u)))

            header_color = "#9A9A9A"
            for unit in ordered_units:
                cols = list(groups.get(unit, []) or [])
                if not cols:
                    continue

                hdr = QTreeWidgetItem(self.tree)
                hdr.setFlags(Qt.ItemIsEnabled)
                hdr.setText(0, get_measurement_type_label(unit))
                for j in range(1, 5):
                    hdr.setText(j, "")
                try:
                    f = hdr.font(0)
                    f.setBold(True)
                    hdr.setFont(0, f)
                    hdr.setForeground(0, header_color)
                except Exception:
                    pass

                for c in cols:
                    it = QTreeWidgetItem(self.tree)
                    it.setText(0, str(c))
                    for j in range(1, 5):
                        it.setText(j, "-")
                    try:
                        it.setFlags(it.flags() | Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
                        it.setCheckState(0, Qt.Checked if c in self._enabled else Qt.Unchecked)
                    except Exception:
                        pass

                    # Match line color from live graph / Legend & Stats
                    try:
                        self._apply_item_color(it, str(c))
                    except Exception:
                        pass

                    self._items[str(c)] = it
        finally:
            self._is_rebuilding = False

    def _apply_item_color(self, item: QTreeWidgetItem, name: str) -> None:
        try:
            col_hex = str(self._color_map.get(str(name), "") or "").strip()
            if not col_hex:
                return

            qcol = QColor(col_hex)
            # Dim when unchecked
            try:
                if item.checkState(0) != Qt.Checked:
                    qcol.setAlpha(120)
            except Exception:
                pass

            # Keep label text white; show color via a swatch icon.
            try:
                item.setForeground(0, QBrush(QColor("#EAEAEA")))
            except Exception:
                pass

            # Rounded square swatch (Legend & Stats style)
            try:
                size = 12
                pm = QPixmap(size, size)
                pm.fill(Qt.transparent)
                p = QPainter(pm)
                try:
                    p.setRenderHint(QPainter.Antialiasing, True)
                    p.setPen(Qt.NoPen)
                    p.setBrush(qcol)
                    p.drawRoundedRect(0, 0, size - 1, size - 1, 3, 3)
                finally:
                    p.end()
                item.setIcon(0, QIcon(pm))
            except Exception:
                pass
        except Exception:
            pass

        try:
            self.tree.resizeColumnToContents(1)
            self.tree.resizeColumnToContents(2)
            self.tree.resizeColumnToContents(3)
            self.tree.resizeColumnToContents(4)
        except Exception:
            pass

    def _ensure_header_index(self) -> None:
        if self._header_index:
            return

        p = Path(self._csv_path)
        if not self._csv_path or not p.exists() or not p.is_file():
            return

        # Read full header row (normalized) and build a unique leaf mapping
        header = read_hwinfo_headers(str(p))

        # Cache date/time indices for live graph x-axis if present
        self._date_idx = None
        self._time_idx = None
        try:
            for idx, raw in enumerate(header):
                lo = str(raw or "").strip().lower()
                if lo == "date" and self._date_idx is None:
                    self._date_idx = idx
                elif lo == "time" and self._time_idx is None:
                    self._time_idx = idx
        except Exception:
            self._date_idx = None
            self._time_idx = None

        # Build index map: unique leaf name -> csv column index
        seen: dict[str, int] = {}
        out: dict[str, int] = {}
        for idx, raw in enumerate(header):
            lo = str(raw or "").strip().lower()
            if lo in ("date", "time"):
                continue
            if lo.startswith("unnamed"):
                continue

            base = str(raw).strip()
            if base not in seen:
                seen[base] = 0
                uniq = base
            else:
                seen[base] += 1
                uniq = f"{base} #{seen[base]}"
            out[uniq] = idx

        self._header_index = out

        # If selected tokens come from leaf list, make sure our unique mapping matches that scheme
        try:
            _leafs, _has_spd = sensor_leafs_from_header(header)
            _ = make_unique(_leafs)
        except Exception:
            pass

    def _read_last_data_line(self) -> Optional[list[str]]:
        """Tail-read the last CSV data line and parse it into fields."""
        p = Path(self._csv_path)
        if not self._csv_path or not p.exists() or not p.is_file():
            return None

        try:
            with open(p, "rb") as f:
                f.seek(0, os.SEEK_END)
                size = f.tell()
                if size <= 0:
                    return None

                # Read backwards until we find a non-empty line
                chunk = bytearray()
                pos = size
                newlines = 0
                while pos > 0 and newlines < 3 and len(chunk) < 65536:
                    step = 4096 if pos >= 4096 else pos
                    pos -= step
                    f.seek(pos)
                    buf = f.read(step)
                    chunk[:0] = buf
                    newlines = chunk.count(b"\n")

                text = None
                for enc in ("utf-8-sig", "utf-8", "cp1252"):
                    try:
                        text = bytes(chunk).decode(enc, errors="replace")
                        break
                    except Exception:
                        continue
                if not text:
                    return None

                lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
                if len(lines) < 2:
                    return None

                last = lines[-1]
                # If the last line is header-like, step back one.
                if last.lower().startswith("date,") or last.lower().startswith("time,"):
                    if len(lines) >= 2:
                        last = lines[-2]

                row = next(csv.reader([last]))
                return [str(x).strip().strip('"') for x in row]
        except Exception:
            return None

    def _read_last_ambient_value(self) -> Optional[float]:
        p = Path(self._ambient_csv_path)
        if not self._ambient_csv_path or not p.exists() or not p.is_file():
            return None

        try:
            with open(p, "rb") as f:
                f.seek(0, os.SEEK_END)
                size = f.tell()
                if size <= 0:
                    return None

                chunk = bytearray()
                pos = size
                newlines = 0
                while pos > 0 and newlines < 3 and len(chunk) < 65536:
                    step = 4096 if pos >= 4096 else pos
                    pos -= step
                    f.seek(pos)
                    buf = f.read(step)
                    chunk[:0] = buf
                    newlines = chunk.count(b"\n")

                text = None
                for enc in ("utf-8-sig", "utf-8", "cp1252"):
                    try:
                        text = bytes(chunk).decode(enc, errors="replace")
                        break
                    except Exception:
                        continue
                if not text:
                    return None

                lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
                if len(lines) < 2:
                    return None

                last = lines[-1]
                if last.lower().startswith("timestamp,"):
                    if len(lines) >= 2:
                        last = lines[-2]

                row = next(csv.reader([last]))
                row = [str(x).strip().strip('"') for x in row]
                if len(row) < 2:
                    return None

                v = self._parse_float(row[1])
                if v is None:
                    return None
                return float(v)
        except Exception:
            return None

    def _parse_float(self, s: str) -> Optional[float]:
        try:
            t = str(s).strip()
            if not t:
                return None
            # Remove thousands separators
            if "," in t and "." in t:
                t = t.replace(",", "")
            t = t.replace(" ", "")
            return float(t)
        except Exception:
            return None

    def _fmt_value(self, name: str, v: float) -> str:
        try:
            s = str(name).lower()
            if "[rpm]" in s or " rpm" in s or s.endswith("rpm"):
                return f"{int(round(float(v))):,}"
            if "°c" in s or "[°c]" in s:
                return f"{v:.2f}" if abs(v) < 100 else f"{v:.1f}"
            if "[w]" in s or " w" in s:
                return f"{v:.1f}"
            if "[%]" in s or "%" in s:
                return f"{v:.1f}"
            return f"{v:.3g}"
        except Exception:
            return "-"

    def _tick(self) -> None:
        try:
            p = Path(self._csv_path)
            if not self._csv_path or not p.exists() or not p.is_file():
                return

            try:
                st = p.stat()
                mtime = float(st.st_mtime)
                size = int(st.st_size)
            except Exception:
                return

            # Detect file changes
            if self._last_size is None or self._last_mtime is None:
                self._last_size = size
                self._last_mtime = mtime

            changed = (size != self._last_size) or (mtime != self._last_mtime)
            if not changed:
                return

            # If file shrank (rotated/cleared), reset stats.
            if self._last_size is not None and size < self._last_size:
                self._stats = {c: _OnlineStats() for c in self._columns}

            self._last_size = size
            self._last_mtime = mtime

            self._ensure_header_index()
            if not self._header_index:
                return

            row = self._read_last_data_line()
            if not row:
                return

            # Best-effort timestamp extraction for live graph
            ts_obj = None
            try:
                if self._date_idx is not None and self._time_idx is not None:
                    if self._date_idx < len(row) and self._time_idx < len(row):
                        ds = str(row[self._date_idx]).strip()
                        ts = str(row[self._time_idx]).strip()
                        dt_s = f"{ds} {ts}".strip()
                        for fmt in (
                            "%Y-%m-%d %H:%M:%S.%f",
                            "%Y-%m-%d %H:%M:%S",
                            "%m/%d/%Y %H:%M:%S.%f",
                            "%m/%d/%Y %H:%M:%S",
                            "%d/%m/%Y %H:%M:%S.%f",
                            "%d/%m/%Y %H:%M:%S",
                        ):
                            try:
                                ts_obj = datetime.strptime(dt_s, fmt)
                                break
                            except Exception:
                                continue
            except Exception:
                ts_obj = None

            # Update stats from this single row
            updated_any = False
            sample_vals: dict[str, float] = {}
            for col in self._columns:
                if col == self._ambient_col_name:
                    continue
                idx = self._header_index.get(col)
                if idx is None or idx >= len(row):
                    continue
                x = self._parse_float(row[idx])
                if x is None:
                    continue
                stt = self._stats.get(col)
                if stt is None:
                    stt = _OnlineStats()
                    self._stats[col] = stt
                stt.push(float(x))
                sample_vals[col] = float(x)
                updated_any = True

            # Ambient (sidecar) update
            if self._ambient_col_name in self._columns and self._ambient_csv_path:
                amb = self._read_last_ambient_value()
                if amb is not None:
                    stt = self._stats.get(self._ambient_col_name)
                    if stt is None:
                        stt = _OnlineStats()
                        self._stats[self._ambient_col_name] = stt
                    stt.push(float(amb))
                    sample_vals[self._ambient_col_name] = float(amb)
                    updated_any = True

            if not updated_any:
                return

            # Update UI (throttle slightly to avoid overpainting)
            now = time.time()
            if (now - self._last_emit_ts) < 0.08:
                return
            self._last_emit_ts = now

            # Emit sample for live graph (timestamp may be None)
            try:
                self.sample_updated.emit({"ts": ts_obj, "values": sample_vals})
            except Exception:
                pass

            for col, stt in self._stats.items():
                it = self._items.get(col)
                if it is None:
                    continue

                if stt.n <= 0:
                    it.setText(1, "-")
                    it.setText(2, "-")
                    it.setText(3, "-")
                    it.setText(4, "-")
                    continue

                it.setText(1, self._fmt_value(col, stt.cur))
                it.setText(2, self._fmt_value(col, stt.min_v))
                it.setText(3, self._fmt_value(col, stt.max_v))
                it.setText(4, self._fmt_value(col, stt.mean))
        except Exception:
            pass

    def _on_item_changed(self, item: QTreeWidgetItem, column: int) -> None:
        try:
            if self._is_rebuilding:
                return
            if item is None:
                return
            if column != 0:
                return
            # Only handle checkable sensor rows (not group headers)
            if not (item.flags() & Qt.ItemIsUserCheckable):
                return

            name = str(item.text(0) or "").strip()
            if not name:
                return

            if item.checkState(0) == Qt.Checked:
                self._enabled.add(name)
            else:
                if name in self._enabled:
                    self._enabled.remove(name)

            try:
                self._apply_item_color(item, name)
            except Exception:
                pass

            try:
                self.active_columns_changed.emit(list(self._enabled))
            except Exception:
                pass
        except Exception:
            pass

    def _on_item_clicked(self, item: QTreeWidgetItem, column: int) -> None:
        try:
            if self._is_rebuilding:
                return
            if item is None:
                return
            if column != 0:
                return
            if not (item.flags() & Qt.ItemIsUserCheckable):
                return

            name = str(item.text(0) or "").strip()
            if not name:
                return

            if item.checkState(0) == Qt.Checked:
                self._enabled.add(name)
            else:
                self._enabled.discard(name)

            try:
                self._apply_item_color(item, name)
            except Exception:
                pass

            # emit a stable ordering (optional but nice)
            enabled_sorted = [c for c in self._columns if c in self._enabled]
            self.active_columns_changed.emit(enabled_sorted)
        except Exception:
            pass
