import os
import re
import json
from pathlib import Path
from datetime import datetime

from PySide6.QtCore import QProcess, Qt, QTimer
from PySide6.QtGui import QFontMetrics
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
    QToolButton,
    QSizePolicy,
    QDialog,
)

from ui_titlebar import TitleBar
from hwinfo_csv import read_hwinfo_headers, sensor_leafs_from_header, make_unique
from hwinfo_metadata import build_precise_group_map, load_sensor_map, save_sensor_map
from ui_sensor_picker import SensorPickerDialog, SPD_MAX_TOKEN


RUNMAP_RE = re.compile(r"RUN MAP:\s*(.+)$")


def ps_quote(s: str) -> str:
    return "'" + s.replace("'", "''") + "'"


class MainWindow(QWidget):
    def __init__(self):
        super().__init__()

        self.setWindowTitle("Temp Test Runner")
        self.setWindowFlag(Qt.FramelessWindowHint, True)
        self.setWindowFlag(Qt.Window, True)

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
        self.selected_tokens: list[str] = [SPD_MAX_TOKEN]  # stores UNIQUE columns

        # Inputs
        self.case_edit = QLineEdit("TEST")

        self.warmup_min = self._make_time_spin(2, 24 * 60, 20)
        self.warmup_sec = self._make_time_spin(2, 59, 0)
        self.log_min = self._make_time_spin(2, 24 * 60, 15)
        self.log_sec = self._make_time_spin(2, 59, 0)

        self.hwinfo_edit = QLineEdit(r"C:\TempTesting\hwinfo.csv")

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

        # Sensors summary
        self.sensors_summary = QLineEdit()
        self.sensors_summary.setReadOnly(True)
        self.sensors_summary.setPlaceholderText("No sensors selected (will use defaults).")

        self.pick_sensors_btn = QPushButton("Select sensors…")
        self.pick_sensors_btn.clicked.connect(self.open_sensor_picker)

        # Buttons
        self.run_btn = QPushButton("Run")
        self.run_btn.clicked.connect(self.run)

        self.abort_btn = QPushButton("Abort (StopNow)")
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
        top_row.addWidget(QLabel("CaseName"))
        top_row.addStretch(1)
        top_row.addWidget(self.live_timer)
        root.addLayout(top_row)
        root.addWidget(self.case_edit)

        time_row = QHBoxLayout()
        time_row.setSpacing(18)

        warm_col = QVBoxLayout()
        warm_col.setSpacing(6)
        warm_col.addWidget(QLabel("Warmup"))
        warm_row = QHBoxLayout()
        warm_row.setSpacing(6)
        warm_row.addWidget(self.warmup_min)
        warm_row.addWidget(self._unit_label("min"))
        warm_row.addWidget(self.warmup_sec)
        warm_row.addWidget(self._unit_label("sec"))
        warm_row.addStretch(1)
        warm_col.addLayout(warm_row)

        log_col = QVBoxLayout()
        log_col.setSpacing(6)
        log_col.addWidget(QLabel("Log"))
        log_row = QHBoxLayout()
        log_row.setSpacing(6)
        log_row.addWidget(self.log_min)
        log_row.addWidget(self._unit_label("min"))
        log_row.addWidget(self.log_sec)
        log_row.addWidget(self._unit_label("sec"))
        log_row.addStretch(1)
        log_col.addLayout(log_row)

        time_row.addLayout(warm_col)
        time_row.addLayout(log_col)
        root.addLayout(time_row)

        row = QHBoxLayout()
        row.addWidget(QLabel("HWiNFO CSV (continuous)"))
        row.addWidget(self.hwinfo_edit)
        row.addWidget(self.pick_hwinfo_btn)
        root.addLayout(row)

        fur_row = QHBoxLayout()
        fur_row.setSpacing(18)
        demo_col = QVBoxLayout()
        demo_col.setSpacing(6)
        demo_col.addWidget(QLabel("FurMark Demo"))
        demo_col.addWidget(self.fur_demo_combo)
        res_col = QVBoxLayout()
        res_col.setSpacing(6)
        res_col.addWidget(QLabel("FurMark Resolution"))
        res_col.addWidget(self.fur_res_combo)
        fur_row.addLayout(demo_col)
        fur_row.addLayout(res_col)
        root.addLayout(fur_row)

        root.addWidget(QLabel("Sensors to monitor"))
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

        root.addWidget(QLabel("Output"))
        root.addWidget(self.log)

        self.resize(980, 800)

        self.load_settings()
        self._refresh_sensors_summary()

        self.case_edit.textChanged.connect(self.save_settings)
        self.hwinfo_edit.textChanged.connect(self.save_settings)
        self.warmup_min.valueChanged.connect(lambda *_: self.save_settings())
        self.warmup_sec.valueChanged.connect(lambda *_: self.save_settings())
        self.log_min.valueChanged.connect(lambda *_: self.save_settings())
        self.log_sec.valueChanged.connect(lambda *_: self.save_settings())
        self.fur_demo_combo.currentIndexChanged.connect(lambda *_: self.save_settings())
        self.fur_res_combo.currentIndexChanged.connect(lambda *_: self.save_settings())

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
        }
        try:
            self.settings_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        except Exception:
            pass

    def closeEvent(self, event):
        self.save_settings()
        super().closeEvent(event)

    # ---------- helpers ----------
    def _unit_label(self, text: str) -> QLabel:
        lab = QLabel(text)
        lab.setObjectName("UnitLabel")
        return lab

    def _make_time_spin(self, min_chars: int, max_value: int, initial: int) -> QSpinBox:
        sp = QSpinBox()
        sp.setRange(0, max_value)
        sp.setValue(initial)
        sp.setButtonSymbols(QSpinBox.NoButtons)
        sp.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        sp.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Fixed)

        def update_width():
            txt = str(sp.value())
            shown_len = max(min_chars, len(txt))
            fm = QFontMetrics(sp.font())
            w = fm.horizontalAdvance("0" * shown_len) + 26
            sp.setFixedWidth(w)

        sp.valueChanged.connect(lambda *_: update_width())
        update_width()
        return sp

    def _refresh_sensors_summary(self):
        if not self.selected_tokens:
            self.sensors_summary.setText("")
            self.sensors_summary.setPlaceholderText("No sensors selected (will use defaults).")
            return
        display = [("SPD Hub (Max)" if t == SPD_MAX_TOKEN else t) for t in self.selected_tokens]
        self.sensors_summary.setText("; ".join(display[:4]) + (f"; … (+{len(display)-4})" if len(display) > 4 else ""))

    # ---------- precise mapping cache ----------
    def _ensure_precise_map(self, csv_leafs: list[str], csv_unique_leafs: list[str]) -> dict[str, str]:
        cache_path = Path(__file__).with_name("sensor_map.json")
        payload = load_sensor_map(cache_path)
        if payload and payload.get("schema") == 1 and payload.get("header_unique") == csv_unique_leafs:
            return dict(payload.get("mapping", {}))

        mapping = build_precise_group_map(csv_leafs, csv_unique_leafs)
        save_sensor_map(cache_path, csv_unique_leafs, mapping)
        return mapping

    # ---------- sensor picker ----------
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
        except Exception as e:
            group_map = {}
            QMessageBox.warning(
                self,
                "Precise groups unavailable",
                "Couldn't read HWiNFO shared memory.\n\n"
                "Make sure HWiNFO is running and Shared Memory Support is enabled.\n\n"
                f"Details: {e}",
            )

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

    # ---------- build exact columns for plotter ----------
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

        # de-dup while preserving order
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
        if columns:
            cmd_parts.append(f"-TempPatterns {','.join(ps_quote(c) for c in columns)}")

        cmd = " ".join(cmd_parts)

        self.append("Starting PowerShell:")
        self.append("powershell -NoProfile -ExecutionPolicy Bypass -Command " + cmd)
        self.append("")

        self.run_btn.setEnabled(False)
        self.abort_btn.setEnabled(True)

        self._start_live_timer(warm, logsec)

        self.proc.setProgram("powershell")
        self.proc.setArguments(["-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", cmd])
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
