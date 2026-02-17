# ui/main_window.py
import os
import sys
from pathlib import Path

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt, QSize, QTimer, QObject, QThread, Signal
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
    QAbstractItemView,
    QTreeView,
    QFileSystemModel,
    QSplitter,
    QToolButton,
    QStackedWidget,
    QFrame,
    QMessageBox,
    QProgressDialog,
)

from .widgets.ui_theme import apply_theme, style_combobox_popup
from .widgets.ui_widgets import CustomComboBox
from .dialogs import SettingsDialog
from .widgets.ui_titlebar import TitleBar
from .widgets.ui_time_spin import make_time_spin

from .graph_preview import GraphPreview
from .sensor_manager import SensorManager
from .benchmark_controller import BenchmarkController
from .live_monitor_widget import LiveMonitorWidget
from .live_graph_widget import LiveGraphWidget

# keep if you have it
from .runs_proxy_model import RunsProxyModel

from core.settings_store import get_settings_path, load_json, save_json

from ui.ntfy_notifier import NtfyNotifier

from core.version import __version__
from core.resources import app_root
from core.updater import (
    ReleaseInfo,
    UpdateError,
    download_release_asset,
    fetch_latest_release_info,
    is_newer_version,
    launch_installer,
    launch_installer_with_updater_ui,
)


# Manual update checker (Windows-only)
GITHUB_OWNER = "ZhYinHai"
GITHUB_REPO = "ThermalBench"
INSTALLER_PREFIX = "ThermalBench-Setup-v"


class _FetchLatestReleaseWorker(QObject):
    finished = Signal(object)  # ReleaseInfo
    failed = Signal(str)

    def __init__(self, owner: str, repo: str, installer_prefix: str):
        super().__init__()
        self._owner = owner
        self._repo = repo
        self._installer_prefix = installer_prefix

    def run(self) -> None:
        try:
            info = fetch_latest_release_info(
                self._owner, self._repo, installer_prefix=self._installer_prefix
            )
            self.finished.emit(info)
        except Exception as e:
            self.failed.emit(str(e))


class _DownloadInstallerWorker(QObject):
    progress = Signal(int, int)  # downloaded_bytes, total_bytes (-1 if unknown)
    finished = Signal(str)  # installer_path
    failed = Signal(str)

    def __init__(self, release: ReleaseInfo):
        super().__init__()
        self._release = release

    def run(self) -> None:
        try:
            def _on_progress(downloaded: int, total: int | None) -> None:
                self.progress.emit(int(downloaded), int(total) if total is not None else -1)

            installer_path = download_release_asset(self._release, progress_cb=_on_progress)
            self.finished.emit(str(installer_path))
        except Exception as e:
            self.failed.emit(str(e))


class MainWindow(QWidget):
    def __init__(self):
        super().__init__()

        # Default startup size
        DEFAULT_W, DEFAULT_H = 1366, 768
        self.resize(DEFAULT_W, DEFAULT_H)

        # Enable custom titlebar by making window frameless
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Window)

        # Keep the real window title in sync (taskbar/alt-tab) with the custom titlebar text
        self.setWindowTitle(f"ThermalBench v{__version__}")

        self.settings_path = get_settings_path("ThermalBench")
        self.furmark_exe = ""
        self.prime_exe = ""
        self.theme_mode = "dark"

        # Push notifications (ntfy)
        # Store either a full topic URL (https://ntfy.sh/<topic>) or just a topic name.
        self.ntfy_topic = ""

        self._ntfy_notifier = NtfyNotifier(self)
        try:
            self._ntfy_notifier.finished.connect(self._on_ntfy_notify_finished)
        except Exception:
            pass

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

        # Live monitor table (shown during runs)
        self._live_monitor = LiveMonitorWidget(self)
        self._live_graph = LiveGraphWidget(self)
        self._output_stack = None
        self._output_btn_live = None
        self._output_btn_console = None

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
        runs_root = app_root() / "runs"
        self._runs_model = QFileSystemModel()
        try:
            self._runs_root = runs_root
            self._runs_model.setRootPath(str(self._runs_root))
        except Exception:
            self._runs_model.setRootPath("")

        self._runs_tree = QTreeView()
        self._runs_tree.setHeaderHidden(True)
        try:
            self._runs_tree.setSelectionMode(QAbstractItemView.ExtendedSelection)
            self._runs_tree.setSelectionBehavior(QAbstractItemView.SelectRows)
            self._runs_tree.setExpandsOnDoubleClick(False)
        except Exception:
            pass
        self._runs_tree.setStyleSheet("""
            QTreeView {
                border: none;
                border-right: 1px solid rgba(128, 128, 128, 0.3);
                border-radius: 0px;
                color: #B0B0B0;
            }
        """)

        self.compare_btn = QPushButton("Compare")
        self.compare_btn.setEnabled(False)
        self.compare_btn.setCursor(Qt.PointingHandCursor)

        self.remove_result_btn = QPushButton("Remove Selected")
        self.remove_result_btn.setEnabled(False)
        self.remove_result_btn.setCursor(Qt.PointingHandCursor)

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
            remove_btn=self.remove_result_btn,
            compare_btn=self.compare_btn,
            runs_tree=self._runs_tree,
            runs_model=self._runs_tree.model(),        # proxy or source (whatever the tree uses)
            runs_source_model=self._runs_model,        # always the QFileSystemModel (source)
            runs_root=self._runs_root,
            graph_preview=self.graph,
            sensor_manager=self.sensors,
            save_settings_callback=self.save_settings,
            get_settings_callback=self._get_current_settings,
            append_log_callback=self.append,
            on_run_started=self._on_run_started,
            on_run_finished=self._on_run_finished,
            on_log_started=self._on_log_started,
            on_log_finished=self._on_log_finished,
            on_ambient_csv=self._on_ambient_csv,
        )

        # Connect component signals
        self.sensors_summary.mousePressEvent = lambda e: self.sensors.open_selected_sensors_view()
        self.pick_sensors_btn.clicked.connect(self.sensors.open_sensor_picker)
        self.run_btn.clicked.connect(self.benchmark.run)
        self.abort_btn.clicked.connect(self.benchmark.abort)
        self.open_btn.clicked.connect(self.benchmark.open_run_folder)
        self.compare_btn.clicked.connect(self.benchmark.compare_selected_results)
        self.remove_result_btn.clicked.connect(self.benchmark.remove_selected_result)

        try:
            self._runs_tree.doubleClicked.connect(self.benchmark.toggle_compare_selection_for_index)
        except Exception:
            pass

        # ======================================================================
        # BUILD UI LAYOUT
        # ======================================================================

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self.titlebar = TitleBar(self, f"ThermalBench v{__version__}")
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

        # Output area: Live monitor (during run) + Console (log)
        out_hdr = QHBoxLayout()
        out_hdr.setContentsMargins(0, 0, 0, 0)
        out_hdr.addWidget(self._bold_label("Output"))
        out_hdr.addStretch(1)

        def _mk_out_btn(text: str):
            b = QPushButton(text)
            b.setCursor(Qt.PointingHandCursor)
            b.setCheckable(True)
            b.setStyleSheet(
                """
                QPushButton {
                    background: #2A2A2A;
                    color: #EAEAEA;
                    border: 1px solid #3A3A3A;
                    border-radius: 6px;
                    padding: 6px 12px;
                    font-size: 12px;
                }
                QPushButton:hover { background: #333333; border-color: #4A4A4A; }
                QPushButton:pressed { background: #252525; }
                QPushButton:checked { background: #1F2B1F; border-color: #2E4A2E; }
                """
            )
            return b

        self._output_btn_live = _mk_out_btn("Live")
        self._output_btn_console = _mk_out_btn("Console")
        self._output_btn_live.setAutoExclusive(True)
        self._output_btn_console.setAutoExclusive(True)

        out_hdr.addWidget(self._output_btn_live)
        out_hdr.addWidget(self._output_btn_console)
        root.addLayout(out_hdr)

        # Live panel: left = table, right = live graph
        live_panel = QWidget()
        live_layout = QVBoxLayout(live_panel)
        live_layout.setContentsMargins(0, 0, 0, 0)
        live_layout.setSpacing(0)

        self._live_split = QSplitter(Qt.Horizontal)
        try:
            self._live_split.setCollapsible(0, False)
            self._live_split.setCollapsible(1, False)
        except Exception:
            pass

        self._live_split.addWidget(self._live_monitor)
        self._live_split.addWidget(self._live_graph)

        # Keep this run-layout ratio (table : graph)
        self._live_split_ratio = (0.34, 0.66)  # tweak to match your screenshot exactly

        live_layout.addWidget(self._live_split, 1)

        # Stream samples from the table parser to the live graph
        try:
            self._live_monitor.sample_updated.connect(self._live_graph.on_sample)
        except Exception:
            pass

        # Stream (de)selection from the live table to the live graph
        try:
            self._live_monitor.active_columns_changed.connect(self._live_graph.set_active_columns)
        except Exception:
            pass

        self._output_stack = QStackedWidget()
        self._output_stack.addWidget(live_panel)          # index 0
        self._output_stack.addWidget(self.log)            # index 1
        self._output_stack.setCurrentIndex(1)
        self._output_btn_console.setChecked(True)

        self._output_btn_live.clicked.connect(lambda *_: self._output_stack.setCurrentIndex(0))
        self._output_btn_console.clicked.connect(lambda *_: self._output_stack.setCurrentIndex(1))

        root.addWidget(self._output_stack, 1)

        # --------------------------
        # Results page
        # --------------------------
        results_container = QWidget()
        results_layout = QHBoxLayout(results_container)
        results_layout.setContentsMargins(0, 0, 8, 0)
        results_layout.setSpacing(8)

        splitter = QSplitter(Qt.Horizontal)
        # Results page splitter: keep the folder tree around ~20% of window width by default.
        # If the user drags the splitter, we remember their ratio and preserve it on resize.
        self._results_split = splitter
        self._results_split_ratio = (0.15, 0.85)
        self._results_split_user_set = False
        try:
            splitter.splitterMoved.connect(self._on_results_splitter_moved)
        except Exception:
            pass
        tree_panel = QWidget()
        tree_panel.setStyleSheet("""
            QWidget {
                border-right: 1px solid rgba(128, 128, 128, 0.3);
            }
        """)
        tree_panel_layout = QVBoxLayout(tree_panel)
        tree_panel_layout.setContentsMargins(0, 0, 0, 0)
        tree_panel_layout.setSpacing(0)
        tree_panel_layout.addWidget(self._runs_tree, 1)

        tree_footer = QVBoxLayout()
        tree_footer.setContentsMargins(10, 0, 10, 5)
        tree_footer.setSpacing(4)
        tree_footer.addWidget(self.compare_btn)
        tree_footer.addWidget(self.remove_result_btn)
        tree_panel_layout.addLayout(tree_footer)

        splitter.addWidget(tree_panel)

        preview_widget = QWidget()
        preview_layout = QVBoxLayout(preview_widget)
        preview_layout.setContentsMargins(0, 6, 0, 6)
        preview_layout.setSpacing(6)
        preview_layout.addWidget(self._preview_label)
        preview_layout.addWidget(self.graph.get_canvas())

        splitter.addWidget(preview_widget)

        try:
            splitter.setStretchFactor(0, 0)
            splitter.setStretchFactor(1, 1)
            splitter.setCollapsible(0, False)
            splitter.setCollapsible(1, False)
            total = self.width() or DEFAULT_W
            left = max(120, int(total * 0.20))
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

        self.resize(DEFAULT_W, DEFAULT_H)

        # Apply results splitter ratio once after initial layout.
        try:
            QTimer.singleShot(0, self._apply_results_split_ratio)
        except Exception:
            pass

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

        # Update threads (kept as attributes to avoid GC)
        self._update_fetch_thread = None
        self._update_fetch_worker = None
        self._update_download_thread = None
        self._update_download_worker = None
        self._update_in_progress = False
        self._update_progress_dialog: QProgressDialog | None = None

        self._update_last_release: ReleaseInfo | None = None
        self._update_downloaded_installer: Path | None = None
        self._update_ui_set_status = None
        self._update_ui_set_button_text = None
        self._update_ui_set_button_enabled = None

    def _set_update_busy(self, busy: bool) -> None:
        self._update_in_progress = bool(busy)

    def _bind_update_ui(
        self,
        *,
        set_status,
        set_button_text,
        set_button_enabled,
    ) -> None:
        self._update_ui_set_status = set_status
        self._update_ui_set_button_text = set_button_text
        self._update_ui_set_button_enabled = set_button_enabled

    def _update_ui(
        self,
        *,
        status_text: str | None = None,
        status_level: str = "info",
        button_text: str | None = None,
        button_enabled: bool | None = None,
    ) -> None:
        """Best-effort: update Settings dialog inline status/button without hard dependency."""
        try:
            if status_text is not None and self._update_ui_set_status is not None:
                try:
                    self._update_ui_set_status(status_text, status_level)
                except Exception:
                    # Dialog likely closed/destroyed
                    self._update_ui_set_status = None

            if button_text is not None and self._update_ui_set_button_text is not None:
                try:
                    self._update_ui_set_button_text(button_text)
                except Exception:
                    self._update_ui_set_button_text = None

            if button_enabled is not None and self._update_ui_set_button_enabled is not None:
                try:
                    self._update_ui_set_button_enabled(bool(button_enabled))
                except Exception:
                    self._update_ui_set_button_enabled = None
        except Exception:
            pass

    def _show_update_progress(self, text: str) -> None:
        """Show a simple progress UI while update work runs in background threads."""
        try:
            if self._update_progress_dialog is None:
                dlg = QProgressDialog(self)
                dlg.setWindowTitle("Update")
                dlg.setCancelButton(None)
                dlg.setRange(0, 0)  # indeterminate
                dlg.setMinimumDuration(0)
                dlg.setAutoClose(False)
                dlg.setAutoReset(False)
                dlg.setWindowModality(Qt.WindowModal)
                self._update_progress_dialog = dlg

            self._update_progress_dialog.setLabelText(text)
            self._update_progress_dialog.show()
            self._update_progress_dialog.raise_()
            self._update_progress_dialog.activateWindow()
        except Exception:
            pass

    def _hide_update_progress(self) -> None:
        try:
            if self._update_progress_dialog is not None:
                self._update_progress_dialog.hide()
        except Exception:
            pass

    # ---------- rail/page switching ----------
    def _on_page_changed(self, index: int) -> None:
        try:
            if index == getattr(self, "_page_results_index", -1):
                # Let the UI finish switching tabs first, then select/plot.
                QTimer.singleShot(0, self.benchmark.select_latest_result)
                QTimer.singleShot(0, self._apply_results_split_ratio)
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

    # ---------- manual update checker ----------
    def check_for_updates(self, *, set_status, set_button_text, set_button_enabled) -> None:
        """Manual updater entrypoint used by Settings dialog.

        This is a multi-step flow driven by the same button:
        - Check for updates
        - If update available: Download
        - If downloaded: Install
        All status is surfaced inline next to the button.
        """
        self._bind_update_ui(
            set_status=set_status,
            set_button_text=set_button_text,
            set_button_enabled=set_button_enabled,
        )

        if sys.platform != "win32":
            self._update_ui(status_text="Windows-only.", status_level="error")
            return

        # Dev/test hook: bypass GitHub and stage a local installer for the Install step.
        # Usage:
        #   set THERMALBENCH_UPDATER_TEST_INSTALLER=C:\path\to\ThermalBench-Setup-vX.Y.Z.exe
        test_installer = os.environ.get("THERMALBENCH_UPDATER_TEST_INSTALLER", "").strip()
        if test_installer:
            installer_path = Path(test_installer).expanduser()
            if not installer_path.exists():
                self._update_ui(
                    status_text=f"Test installer not found: {installer_path}",
                    status_level="error",
                )
                return

            self._update_last_release = None
            self._update_downloaded_installer = installer_path
            self._update_ui(
                status_text="Test mode: installer staged. Click again to install…",
                status_level="info",
                button_text="Install update…",
                button_enabled=True,
            )
            return

        if not GITHUB_OWNER or not GITHUB_REPO:
            self._update_ui(
                status_text="Updater not configured.",
                status_level="error",
            )
            return

        # If an installer is already downloaded, clicking becomes the install action.
        if self._update_downloaded_installer is not None:
            try:
                self._update_ui(
                    status_text="Installing update… (ThermalBench will close briefly)",
                    status_level="info",
                    button_enabled=False,
                )
                launch_installer_with_updater_ui(
                    self._update_downloaded_installer,
                    wait_for_pid=os.getpid(),
                    silent=True,
                )
            except Exception as e:
                self._update_ui(
                    status_text=f"Error: {e}",
                    status_level="error",
                    button_enabled=True,
                )
                return

            # The updater helper shows a progress window and will restart the app when done.
            # We still must exit for the installer to replace files.
            QTimer.singleShot(250, QApplication.quit)
            return

        # If we already know an update is available, clicking becomes the download action.
        if self._update_last_release is not None:
            try:
                if is_newer_version(__version__, self._update_last_release.version):
                    self._start_update_download(self._update_last_release)
                    return
            except Exception:
                # fall back to re-check
                self._update_last_release = None

        # Otherwise, click triggers a fresh check.
        self._start_update_check()

    def _start_update_check(self) -> None:
        if getattr(self, "_update_in_progress", False):
            self._update_ui(status_text="Busy…", status_level="info")
            return

        self._set_update_busy(True)
        self._update_ui(
            status_text="Checking…",
            status_level="info",
            button_text="Checking…",
            button_enabled=False,
        )

        thread = QThread(self)
        worker = _FetchLatestReleaseWorker(GITHUB_OWNER, GITHUB_REPO, INSTALLER_PREFIX)
        worker.moveToThread(thread)

        thread.started.connect(worker.run)
        worker.finished.connect(self._on_update_release_info)
        worker.failed.connect(self._on_update_failed)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        worker.failed.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)

        self._update_fetch_thread = thread
        self._update_fetch_worker = worker
        thread.start()

    def _start_update_download(self, release: ReleaseInfo) -> None:
        if getattr(self, "_update_in_progress", False):
            self._update_ui(status_text="Busy…", status_level="info")
            return

        self._set_update_busy(True)
        self._update_ui(
            status_text="Downloading…",
            status_level="info",
            button_text="Downloading…",
            button_enabled=False,
        )

        thread = QThread(self)
        worker = _DownloadInstallerWorker(release)
        worker.moveToThread(thread)

        thread.started.connect(worker.run)
        worker.progress.connect(self._on_update_download_progress)
        worker.finished.connect(self._on_update_downloaded)
        worker.failed.connect(self._on_update_download_failed)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        worker.failed.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)

        self._update_download_thread = thread
        self._update_download_worker = worker
        thread.start()

    def _on_update_failed(self, reason: str) -> None:
        self._set_update_busy(False)
        self._update_ui(
            status_text=f"Error: {reason}",
            status_level="error",
            button_text="Check for updates…",
            button_enabled=True,
        )

    def _on_update_release_info(self, release: ReleaseInfo) -> None:
        try:
            newer = is_newer_version(__version__, release.version)
        except Exception as e:
            self._set_update_busy(False)
            self._update_ui(
                status_text=f"Error: {e}",
                status_level="error",
                button_text="Check for updates…",
                button_enabled=True,
            )
            return

        if not newer:
            self._set_update_busy(False)
            self._update_last_release = None
            self._update_downloaded_installer = None
            self._update_ui(
                status_text="Up to date.",
                status_level="ok",
                button_text="Check for updates…",
                button_enabled=True,
            )
            return

        # Update available: surface inline and let the same button become Download.
        self._update_last_release = release
        self._set_update_busy(False)
        self._update_ui(
            status_text=f"Update available: {release.version}",
            status_level="warn",
            button_text=f"Download {release.version}",
            button_enabled=True,
        )

    def _on_update_download_progress(self, downloaded_bytes: int, total_bytes: int) -> None:
        try:
            if total_bytes and total_bytes > 0:
                pct = int(round((downloaded_bytes / total_bytes) * 100))
                self._update_ui(status_text=f"Downloading… {pct}%", status_level="info")
            else:
                self._update_ui(status_text="Downloading…", status_level="info")
        except Exception:
            pass

    def _on_update_download_failed(self, reason: str) -> None:
        self._set_update_busy(False)
        # Keep the last release so the user can click Download again.
        btn = "Download update"
        try:
            if self._update_last_release is not None and self._update_last_release.version:
                btn = f"Download {self._update_last_release.version}"
        except Exception:
            pass
        self._update_ui(
            status_text=f"Error: {reason}",
            status_level="error",
            button_text=btn,
            button_enabled=True,
        )

    def _on_update_downloaded(self, installer_path_str: str) -> None:
        installer_path = Path(installer_path_str)
        self._set_update_busy(False)
        self._update_downloaded_installer = installer_path
        self._update_ui(
            status_text="Downloaded. Ready to install.",
            status_level="warn",
            button_text="Install update",
            button_enabled=True,
        )

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
            "fur_demo_display": demo_display,
            "fur_width": fur_w,
            "fur_height": fur_h,
            "fur_res_display": res_display,
            "furmark_exe": self.furmark_exe,
            "prime_exe": self.prime_exe,
            "ntfy_topic": self.ntfy_topic,
            "stress_cpu": bool(getattr(self.sensors, "stress_cpu", True)),
            "stress_gpu": bool(getattr(self.sensors, "stress_gpu", True)),
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

        self.ntfy_topic = str(data.get("ntfy_topic", self.ntfy_topic or "")).strip()

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
            "ntfy_topic": self.ntfy_topic,
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
            ntfy_topic=self.ntfy_topic,
            theme=self.theme_mode,
            update_callback=self.check_for_updates,
        )
        if dlg.exec() != QDialog.Accepted:
            return

        self.furmark_exe = dlg.furmark_exe()
        self.prime_exe = dlg.prime_exe()
        try:
            self.ntfy_topic = dlg.ntfy_topic()
        except Exception:
            pass
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

    # ---------- live monitor hooks ----------
    def _on_run_started(self, settings: dict, columns: list[str]) -> None:
        try:
            csv_path = str((settings or {}).get("hwinfo_csv") or "").strip()
            cols = [str(c) for c in (columns or []) if str(c).strip()]

            # Always include ambient as a temperature series during runs.
            ambient_col = "Ambient [°C]"
            if ambient_col not in cols:
                cols.append(ambient_col)

            try:
                self._live_monitor.start(csv_path=csv_path, columns=cols)
                self._live_graph.start(columns=cols)
            except Exception:
                pass

            if self._output_stack is not None:
                self._output_stack.setCurrentIndex(0)
                QTimer.singleShot(0, self._apply_live_split_ratio)
            if self._output_btn_live is not None:
                self._output_btn_live.setChecked(True)
        except Exception:
            pass

        self._apply_live_split_ratio()

    def _on_ambient_csv(self, ambient_csv_path: str) -> None:
        try:
            self._live_monitor.set_ambient_csv(str(ambient_csv_path or "").strip())
        except Exception:
            pass

    def _on_run_finished(self, result: dict | None = None) -> None:
        try:
            try:
                self._live_monitor.stop()
                self._live_graph.stop()
            except Exception:
                pass

            # Best-effort: send a push notification if configured.
            try:
                self._notify_run_finished_ntfy(result)
            except Exception:
                pass

            if self._output_stack is not None:
                self._output_stack.setCurrentIndex(1)
            if self._output_btn_console is not None:
                self._output_btn_console.setChecked(True)
        except Exception:
            pass

    def _notify_run_finished_ntfy(self, result: dict | None = None) -> None:
        topic = str(getattr(self, "ntfy_topic", "") or "").strip()
        if not topic:
            return

        case_name = ""
        try:
            case_name = str(self.case_edit.text() or "").strip()
        except Exception:
            case_name = ""
        if isinstance(result, dict):
            case_name = str(result.get("case_name") or case_name or "").strip()

        ok = True
        elapsed_sec = None
        run_dir = ""
        if isinstance(result, dict):
            try:
                ok = int(result.get("exit_code", 0) or 0) == 0
            except Exception:
                ok = True
            try:
                elapsed_sec = int(result.get("elapsed_sec")) if result.get("elapsed_sec") is not None else None
            except Exception:
                elapsed_sec = None
            run_dir = str(result.get("run_dir") or "").strip()

        status = "SUCCESS" if ok else "FAILED"
        dur = ""
        if isinstance(elapsed_sec, int) and elapsed_sec >= 0:
            mm = elapsed_sec // 60
            ss = elapsed_sec % 60
            dur = f" ({mm:02d}:{ss:02d})"

        subject = f"ThermalBench: {case_name or 'Test'} finished - {status}"
        body = f"Case: {case_name or 'Test'}\nStatus: {status}{dur}\n"
        if run_dir:
            body += f"Run folder: {run_dir}\n"

        try:
            self._ntfy_notifier.send(topic=topic, title=subject, message=body)
        except Exception:
            pass

    def _on_ntfy_notify_finished(self, ok: bool, message: str) -> None:
        try:
            if not ok:
                self.append(str(message or "Push notification failed"))
        except Exception:
            pass

    def _on_log_started(self) -> None:
        """Called when warmup ends and the logging window begins."""
        try:
            self._live_monitor.reset_window_stats()
            self._live_graph.mark_phase_boundary()
        except Exception:
            pass

    def _on_log_finished(self) -> None:
        """Called when the logging window ends (freeze live stats)."""
        try:
            self._live_monitor.stop()
            self._live_graph.stop()
        except Exception:
            pass

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

    def _apply_live_split_ratio(self) -> None:
        try:
            sp = getattr(self, "_live_split", None)
            if sp is None:
                return

            w = max(1, sp.width())
            a, b = getattr(self, "_live_split_ratio", (0.34, 0.66))

            left = int(w * float(a))
            right = max(1, w - left)

            # Avoid splitter "drift"
            sp.blockSignals(True)
            try:
                sp.setSizes([left, right])
                try:
                    self._live_graph._relayout_visible_axes()
                    self._live_graph._update_phase_labels()
                    self._live_graph._canvas.draw_idle()
                except Exception:
                    pass
            finally:
                sp.blockSignals(False)
        except Exception:
            pass

    def _on_results_splitter_moved(self, *_args) -> None:
        try:
            sp = getattr(self, "_results_split", None)
            if sp is None:
                return
            sizes = list(sp.sizes() or [])
            if len(sizes) < 2:
                return
            total = float(max(1, int(sizes[0]) + int(sizes[1])))
            a = float(max(0.05, min(0.95, float(sizes[0]) / total)))
            self._results_split_ratio = (a, 1.0 - a)
            self._results_split_user_set = True
        except Exception:
            pass

    def _apply_results_split_ratio(self) -> None:
        try:
            sp = getattr(self, "_results_split", None)
            if sp is None:
                return

            # Only enforce while Results page is visible.
            try:
                if getattr(self, "_stack", None) is not None:
                    if self._stack.currentIndex() != getattr(self, "_page_results_index", -1):
                        return
            except Exception:
                pass

            w = max(1, sp.width())
            a, _b = getattr(self, "_results_split_ratio", (0.20, 0.80))
            left = max(120, int(w * float(a)))
            right = max(1, w - left)

            sp.blockSignals(True)
            try:
                sp.setSizes([left, right])
            finally:
                sp.blockSignals(False)
        except Exception:
            pass

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        try:
            # only enforce while Live output is shown
            if self._output_stack is not None and self._output_stack.currentIndex() == 0:
                QTimer.singleShot(0, self._apply_live_split_ratio)
        except Exception:
            pass

        try:
            QTimer.singleShot(0, self._apply_results_split_ratio)
        except Exception:
            pass
