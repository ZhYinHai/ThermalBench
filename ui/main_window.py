# ui/main_window.py
from pathlib import Path

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt, QSize, QTimer
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTextEdit,
    QFileDialog,
    QDialog,
    QSizePolicy,
    QTreeView,
    QFileSystemModel,
    QSplitter,
    QToolButton,
    QStackedWidget,
    QFrame,
)

from .ui_theme import apply_theme, style_combobox_popup
from .ui_widgets import CustomComboBox
from .ui_settings_dialog import SettingsDialog
from .ui_titlebar import TitleBar
from .ui_time_spin import make_time_spin

from .graph_preview import GraphPreview
from .sensor_manager import SensorManager
from .benchmark_controller import BenchmarkController

# keep if you have it
from .runs_proxy_model import RunsProxyModel

from core.settings_store import get_settings_path, load_json, save_json


class MainWindow(QWidget):
    def __init__(self):
        super().__init__()

        # Enable custom titlebar by making window frameless
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Window)

        self.settings_path = get_settings_path("ThermalBench")
        self.furmark_exe = ""
        self.prime_exe = ""
        self.theme_mode = "dark"

        # Inputs
        self.case_edit = QLineEdit("TEST")

        self.warmup_min = make_time_spin(2, 24 * 60, 20)
        self.warmup_sec = make_time_spin(2, 59, 0)
        self.log_min = make_time_spin(2, 24 * 60, 15)
        self.log_sec = make_time_spin(2, 59, 0)

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

        # FurMark dropdowns
        self.fur_demo_combo = CustomComboBox(mode=self.theme_mode)
        self.fur_demo_map = {
            "FurMark Knot (OpenGL)": "furmark-knot-gl",
            "FurMark (OpenGL)": "furmark-gl",
            "FurMark Knot (Vulkan)": "furmark-knot-vk",
            "FurMark (Vulkan)": "furmark-vk",
        }
        for k in self.fur_demo_map.keys():
            self.fur_demo_combo.addItem(k)
        self.fur_demo_combo.setCurrentText("FurMark Knot (OpenGL)")

        self.fur_res_combo = CustomComboBox(mode=self.theme_mode)
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

        self.pick_sensors_btn = QPushButton("Select sensors…")

        # Stress toggle buttons
        self.cpu_btn = QPushButton("CPU")
        self.gpu_btn = QPushButton("GPU")
        for b in (self.cpu_btn, self.gpu_btn):
            b.setCheckable(True)
            b.setStyleSheet("QPushButton:checked { border: 1px solid #4A90E2; }")

        self.cpu_btn.setChecked(True)
        self.gpu_btn.setChecked(True)

        # Buttons
        self.run_btn = QPushButton("Run")
        self.abort_btn = QPushButton("Abort")
        self.abort_btn.setEnabled(False)
        self.open_btn = QPushButton("Open Run Folder")
        self.open_btn.setEnabled(False)
        self.pick_hwinfo_btn = QPushButton("Pick HWiNFO CSV…")
        self.pick_hwinfo_btn.clicked.connect(self.pick_hwinfo)

        # Log box
        self.log = QTextEdit()
        self.log.setReadOnly(True)
        self.log.setStyleSheet("font-family: Consolas, 'Courier New', monospace;")

        self.live_timer = QLabel("Idle")
        self.live_timer.setObjectName("LiveTimer")

        # ======================================================================
        # INITIALIZE COMPONENTS
        # ======================================================================

        # Preview label for images
        self._preview_label = QLabel()
        self._preview_label.setAlignment(Qt.AlignCenter)
        self._preview_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        # Sensor Manager
        self.sensors = SensorManager(
            parent=self,
            hwinfo_edit=self.hwinfo_edit,
            csv_dot=self.csv_dot,
            sm2_dot=self.sm2_dot,
            sensors_summary=self.sensors_summary,
            save_settings_callback=self.save_settings,
            update_run_button_callback=self._update_run_button_state,
            stress_cpu_btn=self.cpu_btn,
            stress_gpu_btn=self.gpu_btn,
        )

        # Graph Preview
        self.graph = GraphPreview(
            parent=self,
            preview_label=self._preview_label,
            build_selected_columns_callback=self.sensors.build_selected_columns,
        )

        # Results tree setup
        runs_root = Path(__file__).parent.parent / "runs"
        self._runs_model = QFileSystemModel()
        try:
            self._runs_root = runs_root
            self._runs_model.setRootPath(str(self._runs_root))
        except Exception:
            self._runs_model.setRootPath("")

        self._runs_tree = QTreeView()
        self._runs_tree.setHeaderHidden(True)
        self._runs_tree.setStyleSheet("""
            QTreeView {
                border: none;
                border-right: 1px solid rgba(128, 128, 128, 0.3);
                border-radius: 0px;
                color: #B0B0B0;
            }
        """)

        # Use proxy model if present
        self._runs_proxy = None
        try:
            self._runs_proxy = RunsProxyModel(self)
            self._runs_proxy.setSourceModel(self._runs_model)
            self._runs_tree.setModel(self._runs_proxy)
            try:
                self._runs_tree.setRootIndex(self._runs_proxy.mapFromSource(self._runs_model.index(str(self._runs_root))))
            except Exception:
                pass
        except Exception:
            self._runs_tree.setModel(self._runs_model)
            try:
                self._runs_tree.setRootIndex(self._runs_model.index(str(self._runs_root)))
            except Exception:
                pass

        for c in range(1, 4):
            try:
                self._runs_tree.hideColumn(c)
            except Exception:
                pass

        # Enable single-click to expand/collapse folders
        self._runs_tree.clicked.connect(self._toggle_tree_item)

        # Benchmark Controller
        # IMPORTANT FIX: pass both runs_model (tree model) and runs_source_model (QFileSystemModel)
        self.benchmark = BenchmarkController(
            parent=self,
            log_widget=self.log,
            run_btn=self.run_btn,
            abort_btn=self.abort_btn,
            open_btn=self.open_btn,
            live_timer=self.live_timer,
            runs_tree=self._runs_tree,
            runs_model=self._runs_tree.model(),        # proxy or source (whatever the tree uses)
            runs_source_model=self._runs_model,        # always the QFileSystemModel (source)
            runs_root=self._runs_root,
            graph_preview=self.graph,
            sensor_manager=self.sensors,
            save_settings_callback=self.save_settings,
            get_settings_callback=self._get_current_settings,
            append_log_callback=self.append,
        )

        # Connect component signals
        self.sensors_summary.mousePressEvent = lambda e: self.sensors.open_selected_sensors_view()
        self.pick_sensors_btn.clicked.connect(self.sensors.open_sensor_picker)
        self.run_btn.clicked.connect(self.benchmark.run)
        self.abort_btn.clicked.connect(self.benchmark.abort)
        self.open_btn.clicked.connect(self.benchmark.open_run_folder)

        # ======================================================================
        # BUILD UI LAYOUT
        # ======================================================================

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self.titlebar = TitleBar(self, "ThermalBench")
        outer.addWidget(self.titlebar)
        
        # Titlebar bottom border line
        titlebar_border = QFrame()
        titlebar_border.setFrameShape(QFrame.HLine)
        titlebar_border.setFrameShadow(QFrame.Plain)
        titlebar_border.setFixedHeight(1)
        titlebar_border.setStyleSheet("background-color: rgba(128, 128, 128, 0.3); border: none;")
        outer.addWidget(titlebar_border)

        # --------------------------
        # Left tab rail + page stack
        # --------------------------
        center = QWidget()
        center_layout = QHBoxLayout(center)
        center_layout.setContentsMargins(0, 0, 0, 0)
        center_layout.setSpacing(0)
        outer.addWidget(center, 1)

        # Left rail (VS Code style)
        self._nav = QWidget()
        self._nav.setFixedWidth(40)
        nav_layout = QVBoxLayout(self._nav)
        nav_layout.setContentsMargins(0, 0, 0, 8)
        nav_layout.setSpacing(0)

        # Icon-only buttons (ASCII placeholders)
        self._btn_run_page = QToolButton()
        self._btn_run_page.setText(">")
        self._btn_run_page.setToolTip("Run Benchmark")
        self._btn_run_page.setCheckable(True)
        self._btn_run_page.setAutoExclusive(True)
        self._btn_run_page.setCursor(Qt.PointingHandCursor)
        self._btn_run_page.setFixedSize(40, 40)

        self._btn_results_page = QToolButton()
        self._btn_results_page.setText("#")
        self._btn_results_page.setToolTip("Results")
        self._btn_results_page.setCheckable(True)
        self._btn_results_page.setAutoExclusive(True)
        self._btn_results_page.setCursor(Qt.PointingHandCursor)
        self._btn_results_page.setFixedSize(40, 40)

        self._btn_settings = QToolButton()
        settings_icon_path = Path(__file__).parent.parent / "resources" / "icons" / "settings.svg"
        self._btn_settings.setIcon(QIcon(str(settings_icon_path)))
        self._btn_settings.setIconSize(QSize(18, 18))
        self._btn_settings.setToolTip("Settings")
        self._btn_settings.setCursor(Qt.PointingHandCursor)
        self._btn_settings.setFixedSize(40, 40)
        self._btn_settings.clicked.connect(self.open_settings)

        rail_style = """
        QWidget {
            border-right: 1px solid rgba(128, 128, 128, 0.3);
        }
        QToolButton {
            border: none;
            border-radius: 0px;
            font-size: 18px;
            color: #D0D0D0;
            background: transparent;
        }
        QToolButton:hover {
            background: rgba(255,255,255,0.06);
        }
        QToolButton:checked {
            background: rgba(255,255,255,0.10);
        }
        """
        self._nav.setStyleSheet(rail_style)

        nav_layout.addWidget(self._btn_run_page)
        nav_layout.addWidget(self._btn_results_page)
        nav_layout.addStretch(1)
        nav_layout.addWidget(self._btn_settings)

        # Page stack (replaces QTabWidget)
        self._stack = QStackedWidget()

        center_layout.addWidget(self._nav)
        center_layout.addWidget(self._stack, 1)

        # --------------------------
        # Run page
        # --------------------------
        run_container = QWidget()
        root = QVBoxLayout(run_container)
        root.setContentsMargins(16, 12, 16, 16)
        root.setSpacing(10)

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

        row = QHBoxLayout()
        row.addWidget(self._bold_label("HWiNFO CSV (continuous)"))
        row.addWidget(self.hwinfo_edit, 1)
        row.addWidget(self.pick_hwinfo_btn)
        row.addSpacing(8)
        row.addWidget(self.csv_dot)
        row.addSpacing(6)
        row.addWidget(self.sm2_dot)
        root.addLayout(row)

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

        # --------------------------
        # Results page
        # --------------------------
        results_container = QWidget()
        results_layout = QHBoxLayout(results_container)
        results_layout.setContentsMargins(0, 0, 8, 8)
        results_layout.setSpacing(8)

        splitter = QSplitter(Qt.Horizontal)
        splitter.addWidget(self._runs_tree)

        preview_widget = QWidget()
        preview_layout = QVBoxLayout(preview_widget)
        preview_layout.setContentsMargins(0, 6, 0, 6)
        preview_layout.setSpacing(6)
        preview_layout.addWidget(self._preview_label)
        preview_layout.addWidget(self.graph.get_canvas())

        splitter.addWidget(preview_widget)

        try:
            splitter.setStretchFactor(0, 1)
            splitter.setStretchFactor(1, 5)
            splitter.setCollapsible(0, False)
            splitter.setCollapsible(1, False)
            total = self.width() or 1200
            left = max(80, int(total * 0.15))
            right = max(400, total - left)
            splitter.setSizes([left, right])
        except Exception:
            pass

        results_layout.addWidget(splitter)

        # Add pages to stack
        self._page_run_index = self._stack.addWidget(run_container)
        self._page_results_index = self._stack.addWidget(results_container)

        # Wire rail buttons -> stack
        self._btn_run_page.clicked.connect(lambda: self._stack.setCurrentIndex(self._page_run_index))
        self._btn_results_page.clicked.connect(lambda: self._stack.setCurrentIndex(self._page_results_index))
        self._stack.currentChanged.connect(self._on_page_changed)

        # Default page
        self._btn_run_page.setChecked(True)
        self._stack.setCurrentIndex(self._page_run_index)

        self.resize(1200, 800)

        # Load settings and initialize state
        self.load_settings()
        self.sensors.refresh_sensors_summary()
        self._update_run_button_state()

        # Connect settings change handlers
        self.case_edit.textChanged.connect(self.save_settings)
        self.hwinfo_edit.textChanged.connect(self.save_settings)
        self.warmup_min.valueChanged.connect(lambda *_: self.save_settings())
        self.warmup_sec.valueChanged.connect(lambda *_: self.save_settings())
        self.log_min.valueChanged.connect(lambda *_: self.save_settings())
        self.log_sec.valueChanged.connect(lambda *_: self.save_settings())
        self.fur_demo_combo.currentIndexChanged.connect(lambda *_: self.save_settings())
        self.fur_res_combo.currentIndexChanged.connect(lambda *_: self.save_settings())
        self.hwinfo_edit.textChanged.connect(lambda *_: self._update_run_button_state())

    # ---------- rail/page switching ----------
    def _on_page_changed(self, index: int) -> None:
        try:
            if index == getattr(self, "_page_results_index", -1):
                # Let the UI finish switching tabs first, then select/plot.
                QTimer.singleShot(0, self.benchmark.select_latest_result)
        except Exception:
            pass

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

    def _update_run_button_state(self) -> None:
        """Update run button enabled state and tooltip."""
        try:
            if self.benchmark.is_running():
                self.run_btn.setEnabled(False)
                return
        except Exception:
            pass

        try:
            ok = self.sensors.can_run(self.furmark_exe, self.prime_exe)
        except Exception:
            ok = False
        self.run_btn.setEnabled(ok)

        if not ok:
            try:
                if self.benchmark.is_running():
                    self.run_btn.setToolTip("Test running — abort to enable new runs.")
                else:
                    reasons = self.sensors.missing_reasons(self.furmark_exe, self.prime_exe)
                    self.run_btn.setToolTip("\n".join(reasons))
            except Exception:
                pass
        else:
            self.run_btn.setToolTip("Start the test")

    def _get_current_settings(self) -> dict:
        """Get current settings as a dictionary."""
        warm = int(self.warmup_min.value()) * 60 + int(self.warmup_sec.value())
        logsec = int(self.log_min.value()) * 60 + int(self.log_sec.value())

        demo_display = self.fur_demo_combo.currentText()
        fur_demo = self.fur_demo_map.get(demo_display, "furmark-knot-gl")

        res_display = self.fur_res_combo.currentText()
        fur_w, fur_h = self.res_map.get(res_display, (3840, 1600))

        return {
            "case_name": self.case_edit.text().strip(),
            "warmup_total_sec": warm,
            "log_total_sec": logsec,
            "hwinfo_csv": self.hwinfo_edit.text().strip(),
            "fur_demo": fur_demo,
            "fur_width": fur_w,
            "fur_height": fur_h,
            "furmark_exe": self.furmark_exe,
            "prime_exe": self.prime_exe,
        }

    # ---------- settings ----------
    def load_settings(self):
        """Load settings from JSON file."""
        data = load_json(self.settings_path)
        if not data:
            return

        self.case_edit.setText(str(data.get("case_name", self.case_edit.text())))
        self.hwinfo_edit.setText(str(data.get("hwinfo_csv", self.hwinfo_edit.text())))

        self.warmup_min.setValue(int(data.get("warmup_min", self.warmup_min.value())))
        self.warmup_sec.setValue(int(data.get("warmup_sec", self.warmup_sec.value())))
        self.log_min.setValue(int(data.get("log_min", self.log_min.value())))
        self.log_sec.setValue(int(data.get("log_sec", self.log_sec.value())))

        self.furmark_exe = str(data.get("furmark_exe", self.furmark_exe or "")).strip()
        self.prime_exe = str(data.get("prime_exe", self.prime_exe or "")).strip()
        self.theme_mode = str(data.get("theme", self.theme_mode or "dark")).strip().lower() or "dark"

        try:
            style_combobox_popup(self.fur_demo_combo, self.theme_mode)
            style_combobox_popup(self.fur_res_combo, self.theme_mode)
        except Exception:
            pass

        demo_display = data.get("fur_demo_display")
        if demo_display in self.fur_demo_map:
            self.fur_demo_combo.setCurrentText(demo_display)

        res_display = data.get("fur_res_display")
        if res_display in self.res_map:
            self.fur_res_combo.setCurrentText(res_display)

        tokens = data.get("selected_tokens")
        if isinstance(tokens, list) and tokens:
            self.sensors.selected_tokens = [str(t) for t in tokens]

        stress_cpu = bool(data.get("stress_cpu", True))
        stress_gpu = bool(data.get("stress_gpu", True))
        if (not stress_cpu) and (not stress_gpu):
            stress_cpu = True
            stress_gpu = True

        self.sensors.stress_cpu = stress_cpu
        self.sensors.stress_gpu = stress_gpu

        self.cpu_btn.blockSignals(True)
        self.gpu_btn.blockSignals(True)
        self.cpu_btn.setChecked(stress_cpu)
        self.gpu_btn.setChecked(stress_gpu)
        self.cpu_btn.blockSignals(False)
        self.gpu_btn.blockSignals(False)

    def save_settings(self):
        """Save settings to JSON file."""
        payload = {
            "case_name": self.case_edit.text().strip(),
            "hwinfo_csv": self.hwinfo_edit.text().strip(),
            "warmup_min": int(self.warmup_min.value()),
            "warmup_sec": int(self.warmup_sec.value()),
            "log_min": int(self.log_min.value()),
            "log_sec": int(self.log_sec.value()),
            "fur_demo_display": self.fur_demo_combo.currentText(),
            "fur_res_display": self.fur_res_combo.currentText(),
            "selected_tokens": list(self.sensors.selected_tokens),
            "stress_cpu": bool(self.sensors.stress_cpu),
            "stress_gpu": bool(self.sensors.stress_gpu),
            "furmark_exe": self.furmark_exe,
            "prime_exe": self.prime_exe,
            "theme": self.theme_mode,
        }
        save_json(self.settings_path, payload)
        self._update_run_button_state()

    def open_settings(self) -> None:
        """Open settings dialog."""
        dlg = SettingsDialog(
            self,
            furmark_exe=self.furmark_exe,
            prime_exe=self.prime_exe,
            theme=self.theme_mode,
        )
        if dlg.exec() != QDialog.Accepted:
            return

        self.furmark_exe = dlg.furmark_exe()
        self.prime_exe = dlg.prime_exe()
        self.theme_mode = dlg.theme()

        app = QApplication.instance()
        if app is not None:
            apply_theme(app, self.theme_mode)
            style_combobox_popup(self.fur_demo_combo, self.theme_mode)
            style_combobox_popup(self.fur_res_combo, self.theme_mode)

        self.save_settings()

    def closeEvent(self, event):
        """Handle window close event."""
        self.save_settings()
        super().closeEvent(event)

    # ---------- misc ----------
    def append(self, text: str) -> None:
        """Append text to log."""
        self.log.append(text.rstrip())

    def pick_hwinfo(self):
        """Open file dialog to select HWiNFO CSV."""
        path, _ = QFileDialog.getOpenFileName(self, "Select hwinfo.csv", str(Path.cwd()), "CSV Files (*.csv)")
        if path:
            self.hwinfo_edit.setText(path)
            self.save_settings()
            self.sensors.refresh_csv_status()

    def _toggle_tree_item(self, index):
        """Toggle expand/collapse state of tree item on single click."""
        if self._runs_tree.isExpanded(index):
            self._runs_tree.collapse(index)
        else:
            self._runs_tree.expand(index)
