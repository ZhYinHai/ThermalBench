
# main_window.py
import os
import re
import json
import time
from pathlib import Path
from datetime import datetime
from collections import defaultdict

from PySide6.QtCore import QProcess, Qt, QTimer
from PySide6.QtGui import QFontMetrics, QWheelEvent, QKeyEvent
from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTextEdit,
    QFileDialog,
    QMessageBox,
    QSpinBox,
    QComboBox,
    QSizePolicy,
    QDialog,
    QTreeWidget,
    QTreeWidgetItem,
    QDialogButtonBox,
)

from ui_rounding import apply_rounded_corners
from ui_titlebar import TitleBar
from hwinfo_csv import read_hwinfo_headers, sensor_leafs_from_header, make_unique
from hwinfo_metadata import build_precise_group_map, load_sensor_map, save_sensor_map
from ui_sensor_picker import SensorPickerDialog, SPD_MAX_TOKEN


RUNMAP_RE = re.compile(r"RUN MAP:\s*(.+)$")


def ps_quote(s: str) -> str:
    return "'" + s.replace("'", "''") + "'"


def _try_open_hwinfo_sm2() -> tuple[bool | None, str]:
    """
    Returns:
      (True,  msg)  -> definitely accessible (ON)
      (False, msg)  -> definitely not found (OFF)
      (None,  msg)  -> uncertain (e.g., access denied)
    """
    try:
        import mmap
    except Exception as e:
        return None, f"mmap import failed: {e}"

    names = [
        "Local\\HWiNFO_SENS_SM2",
        "Global\\HWiNFO_SENS_SM2",
        "HWiNFO_SENS_SM2",
    ]

    last_err = None
    for name in names:
        try:
            mm = mmap.mmap(-1, 1, tagname=name, access=mmap.ACCESS_READ)
            mm.close()
            return True, f"opened mapping: {name}"
        except PermissionError as e:
            return None, f"permission denied opening {name}: {e}"
        except OSError as e:
            last_err = f"{name}: {e}"
            continue
        except Exception as e:
            last_err = f"{name}: {e}"
            continue

    return False, f"not found (tried Global/Local): {last_err or 'no details'}"


class KeyboardOnlySpinBox(QSpinBox):
    """Numbers only. No arrows, no mouse wheel changes, no up/down stepping."""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setButtonSymbols(QSpinBox.NoButtons)

    def stepBy(self, steps: int) -> None:
        return

    def wheelEvent(self, event: QWheelEvent) -> None:
        event.ignore()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() in (Qt.Key_Up, Qt.Key_Down, Qt.Key_PageUp, Qt.Key_PageDown):
            event.ignore()
            return
        super().keyPressEvent(event)


class SelectedSensorsDialog(QDialog):
    """
    Read-only dialog that lists ONLY the currently selected sensors,
    grouped by their HWiNFO group name.
    """
    def __init__(
        self,
        parent: QWidget,
        *,
        selected_tokens: list[str],
        group_map: dict[str, str],
        has_spd: bool,
    ):
        super().__init__(parent)

        self.corner_radius = 12
        apply_rounded_corners(self, self.corner_radius)

        self.setModal(True)
        self.setWindowTitle("Selected sensors")
        self.setWindowFlag(Qt.FramelessWindowHint, True)
        self.setWindowFlag(Qt.Window, True)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        tb = TitleBar(self, "", show_title=False, show_buttons=False, draggable=True)
        tb.setFixedHeight(28)
        outer.addWidget(tb)

        root = QVBoxLayout()
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(10)
        outer.addLayout(root)

        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setUniformRowHeights(True)
        root.addWidget(self.tree, 1)

        grouped: dict[str, list[str]] = defaultdict(list)

        for tok in selected_tokens:
            if tok == SPD_MAX_TOKEN:
                if has_spd:
                    grouped["Memory / SPD"].append("SPD Hub (Max of DIMMs)")
                continue
            grp = group_map.get(tok, "Other")
            grouped[grp].append(tok)

        if not grouped:
            grouped["(none)"].append("No sensors selected.")

        for grp in sorted(grouped.keys(), key=lambda s: s.lower()):
            gi = QTreeWidgetItem([grp])
            gi.setFirstColumnSpanned(True)
            f = gi.font(0)
            f.setBold(True)
            gi.setFont(0, f)
            self.tree.addTopLevelItem(gi)

            for tok in grouped[grp]:
                disp = tok
                if tok != "SPD Hub (Max of DIMMs)":
                    disp = tok.replace(" #", "  (#") + (")" if " #" in tok else "")
                QTreeWidgetItem(gi, [disp])

        self.tree.expandAll()

        btns = QDialogButtonBox(QDialogButtonBox.Ok)
        btns.accepted.connect(self.accept)
        root.addWidget(btns)

        self.resize(440, 300)

    def showEvent(self, event):
        super().showEvent(event)
        p = self.parentWidget()
        if p:
            pg = p.geometry()
            sg = self.geometry()
            self.move(pg.center().x() - sg.width() // 2, pg.center().y() - sg.height() // 2)


class MainWindow(QWidget):
    def __init__(self):
        super().__init__()

        self.setWindowTitle("Temp Test Runner")
        self.setWindowFlag(Qt.FramelessWindowHint, True)
        self.setWindowFlag(Qt.Window, True)

        self.corner_radius = 12
        apply_rounded_corners(self, self.corner_radius)

        self.proc = QProcess(self)
        self.proc.readyReadStandardOutput.connect(self.on_stdout)
        self.proc.readyReadStandardError.connect(self.on_stderr)
        self.proc.finished.connect(self.on_finished)

        self.last_run_dir: str | None = None

        self._timer = QTimer(self)
        self._timer.setInterval(250)
        self._timer.timeout.connect(self._tick_timer)
        self._run_started_at: datetime | None = None
        self._warmup_total = 0
        self._log_total = 0

        self.settings_path = Path(__file__).with_name("settings.json")
        self.selected_tokens: list[str] = [SPD_MAX_TOKEN]

        # Stress defaults
        self.stress_cpu = True
        self.stress_gpu = True

        # Inputs
        self.case_edit = QLineEdit("TEST")

        self.warmup_min = self._make_time_spin(2, 24 * 60, 20)
        self.warmup_sec = self._make_time_spin(2, 59, 0)
        self.log_min = self._make_time_spin(2, 24 * 60, 15)
        self.log_sec = self._make_time_spin(2, 59, 0)

        self.hwinfo_edit = QLineEdit(r"C:\TempTesting\hwinfo.csv")

        # --- Status dots ---
        self.csv_dot = QLabel("●")
        self.csv_dot.setObjectName("StatusDot")
        self.csv_dot.setProperty("state", "bad")
        self.csv_dot.setToolTip("CSV: unknown")

        self.sm2_dot = QLabel("●")
        self.sm2_dot.setObjectName("StatusDot")
        self.sm2_dot.setProperty("state", "bad")
        self.sm2_dot.setToolTip("SM2: unknown")

        self._csv_last_mtime: float | None = None
        self._csv_last_size: int | None = None
        self._csv_last_change_ts: float | None = None

        # FurMark dropdowns
        self.fur_demo_combo = QComboBox()
        self.fur_demo_map = {
            "FurMark Knot (OpenGL)": "furmark-knot-gl",
            "FurMark (OpenGL)": "furmark-gl",
            "FurMark Knot (Vulkan)": "furmark-knot-vk",
            "FurMark (Vulkan)": "furmark-vk",
        }
        for k in self.fur_demo_map.keys():
            self.fur_demo_combo.addItem(k)
        self.fur_demo_combo.setCurrentText("FurMark Knot (OpenGL)")

        self.fur_res_combo = QComboBox()
        self.res_order = ["3840 x 2160", "3840 x 1600", "3440 x 1440", "2560 x 1440", "1920 x 1080"]
        self.res_map = {
            "3840 x 2160": (3840, 2160),
            "3840 x 1600": (3840, 1600),
            "3440 x 1440": (3440, 1440),
            "2560 x 1440": (2560, 1440),
            "1920 x 1080": (1920, 1080),
        }
        for k in self.res_order:
            self.fur_res_combo.addItem(k)
        self.fur_res_combo.setCurrentText("3840 x 1600")

        # Sensors summary (clickable)
        self.sensors_summary = QLineEdit()
        self.sensors_summary.setReadOnly(True)
        self.sensors_summary.setPlaceholderText("No sensors selected (will use defaults).")
        self.sensors_summary.setCursor(Qt.PointingHandCursor)
        self.sensors_summary.mousePressEvent = lambda e: self.open_selected_sensors_view()

        self.pick_sensors_btn = QPushButton("Select sensors…")
        self.pick_sensors_btn.clicked.connect(self.open_sensor_picker)

        # Stress toggle buttons
        self.cpu_btn = QPushButton("CPU")
        self.gpu_btn = QPushButton("GPU")
        for b in (self.cpu_btn, self.gpu_btn):
            b.setCheckable(True)
            # Make checked visible even if theme is subtle
            b.setStyleSheet("QPushButton:checked { border: 1px solid #4A90E2; }")

        # default ON (before loading settings)
        self.cpu_btn.setChecked(True)
        self.gpu_btn.setChecked(True)

        # use toggled
        self.cpu_btn.toggled.connect(self._on_cpu_toggled)
        self.gpu_btn.toggled.connect(self._on_gpu_toggled)

        # Buttons
        self.run_btn = QPushButton("Run")
        self.run_btn.clicked.connect(self.run)

        self.abort_btn = QPushButton("Abort")
        self.abort_btn.setEnabled(False)
        self.abort_btn.clicked.connect(self.abort)

        self.open_btn = QPushButton("Open Run Folder")
        self.open_btn.setEnabled(False)
        self.open_btn.clicked.connect(self.open_run_folder)

        self.pick_hwinfo_btn = QPushButton("Pick HWiNFO CSV…")
        self.pick_hwinfo_btn.clicked.connect(self.pick_hwinfo)

        # Log box
        self.log = QTextEdit()
        self.log.setReadOnly(True)
        self.log.setStyleSheet("font-family: Consolas, 'Courier New', monospace;")

        self.live_timer = QLabel("Idle")
        self.live_timer.setObjectName("LiveTimer")

        # Layout
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self.titlebar = TitleBar(self, "Temp Test Runner")
        outer.addWidget(self.titlebar)

        root = QVBoxLayout()
        root.setContentsMargins(16, 12, 16, 16)
        root.setSpacing(10)
        outer.addLayout(root)

        top_row = QHBoxLayout()
        top_row.addWidget(self._bold_label("Name"))
        top_row.addStretch(1)
        top_row.addWidget(self.live_timer)
        root.addLayout(top_row)
        root.addWidget(self.case_edit)

        time_row = QHBoxLayout()
        time_row.setSpacing(18)

        warm_col = QVBoxLayout()
        warm_col.setSpacing(4)
        warm_col.addWidget(self._bold_label("Warmup"))
        warm_row = QHBoxLayout()
        warm_row.setSpacing(1)
        warm_row.addWidget(self.warmup_min)
        warm_row.addWidget(self._unit_label("min"))
        warm_row.addWidget(self.warmup_sec)
        warm_row.addWidget(self._unit_label("sec"))
        warm_row.addStretch(1)
        warm_col.addLayout(warm_row)

        log_col = QVBoxLayout()
        log_col.setSpacing(4)
        log_col.addWidget(self._bold_label("Log"))
        log_row = QHBoxLayout()
        log_row.setSpacing(1)
        log_row.addWidget(self.log_min)
        log_row.addWidget(self._unit_label("min"))
        log_row.addWidget(self.log_sec)
        log_row.addWidget(self._unit_label("sec"))
        log_row.addStretch(1)
        log_col.addLayout(log_row)

        time_row.addLayout(warm_col)
        time_row.addLayout(log_col)
        root.addLayout(time_row)

        # CSV row + dots
        row = QHBoxLayout()
        row.addWidget(self._bold_label("HWiNFO CSV (continuous)"))
        row.addWidget(self.hwinfo_edit, 1)
        row.addWidget(self.pick_hwinfo_btn)
        row.addSpacing(8)
        row.addWidget(self.csv_dot)
        row.addSpacing(6)
        row.addWidget(self.sm2_dot)
        root.addLayout(row)

        # Stress selection row
        stress_row = QHBoxLayout()
        stress_row.setSpacing(10)
        stress_row.addWidget(self._bold_label("Stress test"))
        stress_row.addWidget(self.cpu_btn)
        stress_row.addWidget(self.gpu_btn)
        stress_row.addStretch(1)
        root.addLayout(stress_row)

        fur_row = QHBoxLayout()
        fur_row.setSpacing(18)

        demo_col = QVBoxLayout()
        demo_col.setSpacing(6)
        demo_col.addWidget(self._bold_label("FurMark Demo"))
        demo_col.addWidget(self.fur_demo_combo)

        res_col = QVBoxLayout()
        res_col.setSpacing(6)
        res_col.addWidget(self._bold_label("FurMark Resolution"))
        res_col.addWidget(self.fur_res_combo)

        fur_row.addLayout(demo_col)
        fur_row.addLayout(res_col)
        root.addLayout(fur_row)

        root.addWidget(self._bold_label("Sensors to monitor"))
        sensors_row = QHBoxLayout()
        sensors_row.setSpacing(10)
        sensors_row.addWidget(self.sensors_summary, 1)
        sensors_row.addWidget(self.pick_sensors_btn)
        root.addLayout(sensors_row)

        btns = QHBoxLayout()
        btns.addWidget(self.run_btn)
        btns.addWidget(self.abort_btn)
        btns.addWidget(self.open_btn)
        root.addLayout(btns)

        root.addWidget(self._bold_label("Output"))
        root.addWidget(self.log)

        self.resize(980, 800)

        self.load_settings()
        self._refresh_sensors_summary()

        # Status timer
        self._status_timer = QTimer(self)
        self._status_timer.setInterval(2000)
        self._status_timer.timeout.connect(self._refresh_hwinfo_status)
        self._status_timer.start()
        self._refresh_hwinfo_status()

        self.case_edit.textChanged.connect(self.save_settings)
        self.hwinfo_edit.textChanged.connect(self.save_settings)
        self.warmup_min.valueChanged.connect(lambda *_: self.save_settings())
        self.warmup_sec.valueChanged.connect(lambda *_: self.save_settings())
        self.log_min.valueChanged.connect(lambda *_: self.save_settings())
        self.log_sec.valueChanged.connect(lambda *_: self.save_settings())
        self.fur_demo_combo.currentIndexChanged.connect(lambda *_: self.save_settings())
        self.fur_res_combo.currentIndexChanged.connect(lambda *_: self.save_settings())

    # ---------- stress toggles ----------
    def _on_cpu_toggled(self, checked: bool) -> None:
        # can't turn off the last enabled option
        if (not checked) and (not self.gpu_btn.isChecked()):
            self.cpu_btn.blockSignals(True)
            self.cpu_btn.setChecked(True)
            self.cpu_btn.blockSignals(False)
            return

        self.stress_cpu = checked
        self.save_settings()

    def _on_gpu_toggled(self, checked: bool) -> None:
        # can't turn off the last enabled option
        if (not checked) and (not self.cpu_btn.isChecked()):
            self.gpu_btn.blockSignals(True)
            self.gpu_btn.setChecked(True)
            self.gpu_btn.blockSignals(False)
            return

        self.stress_gpu = checked
        self.save_settings()

    # ---------- settings ----------
    def load_settings(self):
        if not self.settings_path.exists():
            return
        try:
            data = json.loads(self.settings_path.read_text(encoding="utf-8"))
        except Exception:
            return

        self.case_edit.setText(str(data.get("case_name", self.case_edit.text())))
        self.hwinfo_edit.setText(str(data.get("hwinfo_csv", self.hwinfo_edit.text())))

        self.warmup_min.setValue(int(data.get("warmup_min", self.warmup_min.value())))
        self.warmup_sec.setValue(int(data.get("warmup_sec", self.warmup_sec.value())))
        self.log_min.setValue(int(data.get("log_min", self.log_min.value())))
        self.log_sec.setValue(int(data.get("log_sec", self.log_sec.value())))

        demo_display = data.get("fur_demo_display")
        if demo_display in self.fur_demo_map:
            self.fur_demo_combo.setCurrentText(demo_display)

        res_display = data.get("fur_res_display")
        if res_display in self.res_map:
            self.fur_res_combo.setCurrentText(res_display)

        tokens = data.get("selected_tokens")
        if isinstance(tokens, list) and tokens:
            self.selected_tokens = [str(t) for t in tokens]

        # stress toggles
        self.stress_cpu = bool(data.get("stress_cpu", True))
        self.stress_gpu = bool(data.get("stress_gpu", True))
        if (not self.stress_cpu) and (not self.stress_gpu):
            # enforce at least one
            self.stress_cpu = True
            self.stress_gpu = True

        self.cpu_btn.blockSignals(True)
        self.gpu_btn.blockSignals(True)
        self.cpu_btn.setChecked(self.stress_cpu)
        self.gpu_btn.setChecked(self.stress_gpu)
        self.cpu_btn.blockSignals(False)
        self.gpu_btn.blockSignals(False)

    def save_settings(self):
        payload = {
            "case_name": self.case_edit.text().strip(),
            "hwinfo_csv": self.hwinfo_edit.text().strip(),
            "warmup_min": int(self.warmup_min.value()),
            "warmup_sec": int(self.warmup_sec.value()),
            "log_min": int(self.log_min.value()),
            "log_sec": int(self.log_sec.value()),
            "fur_demo_display": self.fur_demo_combo.currentText(),
            "fur_res_display": self.fur_res_combo.currentText(),
            "selected_tokens": list(self.selected_tokens),
            "stress_cpu": bool(self.stress_cpu),
            "stress_gpu": bool(self.stress_gpu),
        }
        try:
            self.settings_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        except Exception:
            pass

    def closeEvent(self, event):
        self.save_settings()
        super().closeEvent(event)

    # ---------- helpers ----------
    def _bold_label(self, text: str) -> QLabel:
        lab = QLabel(text)
        f = lab.font()
        f.setBold(True)
        lab.setFont(f)
        return lab

    def _unit_label(self, text: str) -> QLabel:
        lab = QLabel(text)
        lab.setObjectName("UnitLabel")
        return lab

    def _make_time_spin(self, min_chars: int, max_value: int, initial: int) -> QSpinBox:
        sp = KeyboardOnlySpinBox()
        sp.setRange(0, max_value)
        sp.setValue(initial)
        sp.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        sp.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Fixed)

        def update_width():
            txt = str(sp.value())
            shown_len = max(min_chars, len(txt))
            fm = QFontMetrics(sp.font())
            w = fm.horizontalAdvance("0" * shown_len) + 30
            sp.setFixedWidth(w)

        sp.valueChanged.connect(lambda *_: update_width())
        update_width()
        return sp

    # ---------- dots ----------
    def _set_dot_state(self, dot: QLabel, ok: bool) -> None:
        dot.setProperty("state", "ok" if ok else "bad")
        dot.style().unpolish(dot)
        dot.style().polish(dot)
        dot.update()

    def _refresh_hwinfo_status(self) -> None:
        path = self.hwinfo_edit.text().strip()

        csv_exists = False
        csv_updating = False
        try:
            if path and os.path.exists(path):
                st = os.stat(path)
                mtime = st.st_mtime
                size = st.st_size
                now = time.time()

                if self._csv_last_mtime is None:
                    self._csv_last_mtime = mtime
                    self._csv_last_size = size
                    self._csv_last_change_ts = now
                else:
                    if (mtime != self._csv_last_mtime) or (size != self._csv_last_size):
                        self._csv_last_mtime = mtime
                        self._csv_last_size = size
                        self._csv_last_change_ts = now

                csv_exists = True
                csv_updating = bool(self._csv_last_change_ts and (now - self._csv_last_change_ts) <= 6.0)
        except Exception:
            csv_exists = False
            csv_updating = False

        self._set_dot_state(self.csv_dot, ok=(csv_exists and csv_updating))

        sm2_state, sm2_msg = _try_open_hwinfo_sm2()
        self._set_dot_state(self.sm2_dot, ok=(sm2_state is True))

    # ---------- sensors summary ----------
    def _refresh_sensors_summary(self):
        if not self.selected_tokens:
            self.sensors_summary.setText("")
            self.sensors_summary.setPlaceholderText("No sensors selected (will use defaults).")
            return
        display = [("SPD Hub (Max)" if t == SPD_MAX_TOKEN else t) for t in self.selected_tokens]
        self.sensors_summary.setText(
            "; ".join(display[:4]) + (f"; … (+{len(display)-4})" if len(display) > 4 else "")
        )

    # ---------- precise mapping cache ----------
    def _ensure_precise_map(self, csv_leafs: list[str], csv_unique_leafs: list[str]) -> dict[str, str]:
        cache_path = Path(__file__).with_name("sensor_map.json")
        payload = load_sensor_map(cache_path)
        if payload and payload.get("schema") == 1 and payload.get("header_unique") == csv_unique_leafs:
            return dict(payload.get("mapping", {}))

        mapping = build_precise_group_map(csv_leafs, csv_unique_leafs)
        save_sensor_map(cache_path, csv_unique_leafs, mapping)
        return mapping

    def open_selected_sensors_view(self):
        hwinfo_path = self.hwinfo_edit.text().strip()
        try:
            header = read_hwinfo_headers(hwinfo_path)
            csv_leafs, has_spd = sensor_leafs_from_header(header)
            csv_unique_leafs = make_unique(csv_leafs)
        except Exception as e:
            QMessageBox.critical(self, "Cannot read HWiNFO CSV", str(e))
            return

        try:
            group_map = self._ensure_precise_map(csv_leafs, csv_unique_leafs)
        except Exception:
            group_map = {}

        dlg = SelectedSensorsDialog(
            self,
            selected_tokens=list(self.selected_tokens),
            group_map=group_map,
            has_spd=has_spd,
        )
        dlg.exec()

    def open_sensor_picker(self):
        hwinfo_path = self.hwinfo_edit.text().strip()
        try:
            header = read_hwinfo_headers(hwinfo_path)
            csv_leafs, has_spd = sensor_leafs_from_header(header)
            csv_unique_leafs = make_unique(csv_leafs)
        except Exception as e:
            QMessageBox.critical(self, "Cannot read HWiNFO CSV", str(e))
            return

        try:
            group_map = self._ensure_precise_map(csv_leafs, csv_unique_leafs)
        except Exception:
            group_map = {}

        pre = set(self.selected_tokens)
        dlg = SensorPickerDialog(
            self,
            csv_unique_leafs=csv_unique_leafs,
            has_spd=has_spd,
            group_map=group_map,
            preselected=pre,
        )
        if dlg.exec() == QDialog.Accepted:
            self.selected_tokens = dlg.selected_tokens()
            self._refresh_sensors_summary()
            self.save_settings()

    def build_selected_columns(self) -> list[str]:
        if not self.selected_tokens:
            return ["CPU Package [°C]", "GPU Temperature [°C]", "GPU VRM Temperature [°C]", "SPD Hub Max [°C]"]

        cols: list[str] = []
        spd_selected = False

        for t in self.selected_tokens:
            if t == SPD_MAX_TOKEN:
                spd_selected = True
                continue
            if "SPD Hub Temperature" in t:
                spd_selected = True
            cols.append(t)

        if spd_selected:
            cols.append("SPD Hub Max [°C]")

        seen = set()
        out = []
        for c in cols:
            if c not in seen:
                seen.add(c)
                out.append(c)
        return out

    # ---------- live timer ----------
    @staticmethod
    def _fmt_mmss(total_seconds: int) -> str:
        total_seconds = max(0, total_seconds)
        return f"{total_seconds//60:02d}:{total_seconds%60:02d}"

    def _start_live_timer(self, warmup_sec: int, log_sec: int):
        self._warmup_total = warmup_sec
        self._log_total = log_sec
        self._run_started_at = datetime.now()
        self._tick_timer()
        self._timer.start()

    def _stop_live_timer(self, final_text: str = "Idle"):
        self._timer.stop()
        self._run_started_at = None
        self.live_timer.setText(final_text)

    def _tick_timer(self):
        if not self._run_started_at:
            return
        elapsed = int((datetime.now() - self._run_started_at).total_seconds())
        if elapsed < self._warmup_total:
            self.live_timer.setText(f"Warmup  {self._fmt_mmss(self._warmup_total - elapsed)}")
            return
        log_elapsed = elapsed - self._warmup_total
        if log_elapsed < self._log_total:
            self.live_timer.setText(f"Log  {self._fmt_mmss(self._log_total - log_elapsed)}")
            return
        self.live_timer.setText("Done  00:00")

    # ---------- misc ----------
    def append(self, text: str) -> None:
        self.log.append(text.rstrip())

    def pick_hwinfo(self):
        path, _ = QFileDialog.getOpenFileName(self, "Select hwinfo.csv", str(Path.cwd()), "CSV Files (*.csv)")
        if path:
            self.hwinfo_edit.setText(path)
            self.save_settings()
            self._refresh_hwinfo_status()

    # ---------- run / abort ----------
    def run(self):
        if self.proc.state() != QProcess.NotRunning:
            QMessageBox.warning(self, "Running", "A test is already running.")
            return

        script = Path(__file__).with_name("run_case.ps1")
        if not script.exists():
            QMessageBox.critical(self, "Missing", f"run_case.ps1 not found: {script}")
            return

        self.save_settings()

        self.last_run_dir = None
        self.open_btn.setEnabled(False)
        self.log.clear()

        case = self.case_edit.text().strip()
        warm = int(self.warmup_min.value()) * 60 + int(self.warmup_sec.value())
        logsec = int(self.log_min.value()) * 60 + int(self.log_sec.value())
        if warm <= 0 or logsec <= 0:
            QMessageBox.warning(self, "Invalid time", "Warmup and Log must be > 0 seconds.")
            return

        hwinfo = self.hwinfo_edit.text().strip()

        demo_display = self.fur_demo_combo.currentText()
        fur_demo = self.fur_demo_map[demo_display]
        res_display = self.fur_res_combo.currentText()
        fur_w, fur_h = self.res_map[res_display]

        columns = self.build_selected_columns()

        cmd_parts = [
            f"& {ps_quote(str(script))}",
            f"-CaseName {ps_quote(case)}",
            f"-WarmupSec {warm}",
            f"-LogSec {logsec}",
            f"-HwinfoCsv {ps_quote(hwinfo)}",
            f"-FurDemo {ps_quote(fur_demo)}",
            f"-FurWidth {fur_w}",
            f"-FurHeight {fur_h}",
        ]

        # IMPORTANT: pass as switches (no true/false strings)
        if self.stress_cpu:
            cmd_parts.append("-StressCPU")
        if self.stress_gpu:
            cmd_parts.append("-StressGPU")

        if columns:
            cmd_parts.append(f"-TempPatterns {','.join(ps_quote(c) for c in columns)}")

        cmd = " ".join(cmd_parts)

        self.append("Starting PowerShell:")
        self.append("powershell -NoProfile -ExecutionPolicy Bypass -Command " + cmd)
        self.append("")

        self.run_btn.setEnabled(False)
        self.abort_btn.setEnabled(True)

        self.proc.setProgram("powershell")
        self.proc.setArguments(["-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", cmd])

        self._pending_warm = warm
        self._pending_log = logsec
        self._timer_started = False

        self.proc.start()

    def abort(self):
        if self.proc.state() == QProcess.NotRunning:
            return
        self.append("ABORT requested: StopNow")
        self.abort_btn.setEnabled(False)

        script = Path(__file__).with_name("run_case.ps1")
        p = QProcess(self)
        p.start("powershell", ["-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script), "-StopNow"])

    def on_stdout(self):
        data = bytes(self.proc.readAllStandardOutput()).decode("utf-8", errors="replace")
        for line in data.splitlines():
            self.append(line)

            if (not getattr(self, "_timer_started", False)) and "GUI_TIMER:WARMUP_START" in line:
                self._timer_started = True
                self._start_live_timer(self._pending_warm, self._pending_log)

            m = RUNMAP_RE.search(line)
            if m:
                self.last_run_dir = m.group(1).strip()

    def on_stderr(self):
        data = bytes(self.proc.readAllStandardError()).decode("utf-8", errors="replace")
        for line in data.splitlines():
            self.append("[ERR] " + line)

    def on_finished(self, code, status):
        self.append(f"Finished (exit code {code})")
        self.run_btn.setEnabled(True)
        self.abort_btn.setEnabled(False)
        self._stop_live_timer("Idle" if code == 0 else "Stopped")
        self.open_btn.setEnabled(bool(self.last_run_dir and Path(self.last_run_dir).exists()))

    def open_run_folder(self):
        if self.last_run_dir and Path(self.last_run_dir).exists():
            os.startfile(self.last_run_dir)
