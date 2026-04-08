from __future__ import annotations

from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QEvent, QObject, Qt, QTimer
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSplitter,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ..live_graph_widget import LiveGraphWidget
from ..live_monitor_widget import LiveMonitorWidget
from ..graph_preview.ui_dim_overlay import DimOverlay
from ..widgets.ui_rounding import apply_rounded_corners
from ..widgets.ui_theme import resolve_effective_theme_mode
from ..widgets.ui_titlebar import TitleBar


class _HelpInteractiveDemo(QWidget):
    def __init__(self, parent: QWidget | None = None, *, theme_mode: str = "device"):
        super().__init__(parent)

        self._sample_index = 0
        self._phase_marked = False
        self._columns = [
            "CPU Package [°C]",
            "GPU Hotspot [°C]",
            "Ambient [°C]",
            "CPU Package Power [W]",
        ]
        self._sample_script = [
            {"CPU Package [°C]": 38.2, "GPU Hotspot [°C]": 35.8, "Ambient [°C]": 23.6, "CPU Package Power [W]": 18.4},
            {"CPU Package [°C]": 44.7, "GPU Hotspot [°C]": 39.5, "Ambient [°C]": 23.6, "CPU Package Power [W]": 32.9},
            {"CPU Package [°C]": 53.1, "GPU Hotspot [°C]": 45.2, "Ambient [°C]": 23.7, "CPU Package Power [W]": 56.4},
            {"CPU Package [°C]": 61.8, "GPU Hotspot [°C]": 51.6, "Ambient [°C]": 23.8, "CPU Package Power [W]": 74.3},
            {"CPU Package [°C]": 69.4, "GPU Hotspot [°C]": 58.9, "Ambient [°C]": 23.9, "CPU Package Power [W]": 88.7},
            {"CPU Package [°C]": 74.2, "GPU Hotspot [°C]": 64.1, "Ambient [°C]": 24.0, "CPU Package Power [W]": 101.6},
            {"CPU Package [°C]": 77.5, "GPU Hotspot [°C]": 68.3, "Ambient [°C]": 24.1, "CPU Package Power [W]": 108.2},
            {"CPU Package [°C]": 79.1, "GPU Hotspot [°C]": 70.4, "Ambient [°C]": 24.1, "CPU Package Power [W]": 111.5},
        ]
        self._theme_mode = resolve_effective_theme_mode(theme_mode, QApplication.instance())
        self._theme_is_dark = self._theme_mode == "dark"
        self._theme = self._build_theme_palette()

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(12)

        banner = QLabel(
            "These are the actual live widgets. The table on top and the graph on the bottom are driven through the same monitor-to-graph signal path as a real run."
        )
        banner.setWordWrap(True)
        banner.setStyleSheet(self._card_stylesheet())
        root.addWidget(banner)

        self._live_monitor = LiveMonitorWidget(self)
        self._live_graph = LiveGraphWidget(self)
        try:
            self._live_monitor.set_theme_mode(self._theme_mode)
            self._live_graph.set_theme_mode(self._theme_mode)
        except Exception:
            pass

        splitter = QSplitter(Qt.Vertical)
        splitter.setChildrenCollapsible(False)
        splitter.addWidget(self._live_monitor)
        splitter.addWidget(self._live_graph)
        splitter.setStretchFactor(0, 48)
        splitter.setStretchFactor(1, 52)

        try:
            self._live_monitor.sample_updated.connect(self._live_graph.on_sample)
            self._live_monitor.active_columns_changed.connect(self._live_graph.set_active_columns)
        except Exception:
            pass

        self._live_monitor.start(csv_path="", columns=self._columns)
        self._live_graph.start(columns=self._columns)
        try:
            self._live_graph._canvas.installEventFilter(self)
        except Exception:
            pass
        splitter.setSizes([280, 300])
        root.addWidget(splitter, 1)

        footer = QLabel("Click the checkboxes in the real live table to hide or show series in the real live graph. Power starts hidden by default, which matches the current run behavior.")
        footer.setWordWrap(True)
        footer.setStyleSheet(f"color:{self._theme['muted_text']}; font-size:12px;")
        root.addWidget(footer)

        self._timer = QTimer(self)
        self._timer.setInterval(850)
        self._timer.timeout.connect(self._push_next_sample)
        self._timer.start()

        self._push_next_sample()

    def _build_theme_palette(self) -> dict[str, str]:
        if self._theme_is_dark:
            return {
                "card_bg": "#171717",
                "card_border": "#2A2A2A",
                "card_text": "#CFCFCF",
                "muted_text": "#BEBEBE",
            }
        return {
            "card_bg": "#FFFFFF",
            "card_border": "#D5D5D5",
            "card_text": "#1E1E1E",
            "muted_text": "#555555",
        }

    def _card_stylesheet(self) -> str:
        return (
            f"color:{self._theme['card_text']}; font-size:12px; "
            f"background:{self._theme['card_bg']}; border:1px solid {self._theme['card_border']}; "
            "border-radius:8px; padding:10px;"
        )

    def _push_next_sample(self) -> None:
        try:
            sample = self._sample_script[self._sample_index % len(self._sample_script)]
            self._sample_index += 1
            self._live_monitor.ingest_sample(sample, ts=datetime.now())
            if self._sample_index == 4 and not self._phase_marked:
                self._phase_marked = True
                self._live_graph.mark_phase_boundary()
        except Exception:
            pass

    def eventFilter(self, watched: object, event: object) -> bool:
        try:
            if watched is getattr(self._live_graph, "_canvas", None) and event is not None:
                if event.type() == QEvent.Wheel:
                    return self._forward_wheel_to_scroll_area(event)
        except Exception:
            pass
        return super().eventFilter(watched, event)

    def _forward_wheel_to_scroll_area(self, event) -> bool:
        try:
            scroll = self._find_parent_scroll_area()
            if scroll is None:
                return False

            bar = scroll.verticalScrollBar()
            if bar is None:
                return False

            delta = event.angleDelta().y()
            if delta == 0:
                return False

            single_step = max(20, bar.singleStep())
            steps = delta / 120.0
            bar.setValue(bar.value() - int(round(steps * single_step * 3)))
            event.accept()
            return True
        except Exception:
            return False

    def _find_parent_scroll_area(self) -> QScrollArea | None:
        parent = self.parentWidget()
        while parent is not None:
            if isinstance(parent, QScrollArea):
                return parent
            parent = parent.parentWidget()
        return None


class HelpDialog(QDialog):
    def __init__(self, parent: QWidget | None = None, *, theme_mode: str = "device"):
        super().__init__(parent)

        self._dim_overlay: DimOverlay | None = None
        self._overlay_filter: QObject | None = None
        self._theme_mode = resolve_effective_theme_mode(theme_mode, QApplication.instance())
        self._theme_is_dark = self._theme_mode == "dark"
        self._theme = self._build_theme_palette()

        self.corner_radius = 12
        apply_rounded_corners(self, self.corner_radius)

        self.setModal(True)
        self.setWindowTitle("How To Use ThermalBench")
        self.setWindowFlag(Qt.FramelessWindowHint, True)
        self.setWindowFlag(Qt.Window, True)
        self.resize(700, 620)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        tb = TitleBar(self, "How To Use ThermalBench", show_title=True, show_buttons=False, draggable=True)
        tb.setFixedHeight(42)
        outer.addWidget(tb)

        body = QVBoxLayout()
        body.setContentsMargins(14, 14, 14, 14)
        body.setSpacing(12)
        outer.addLayout(body)

        intro = QLabel(
            "ThermalBench automates a stress-test run, records the selected HWiNFO sensors, "
            "and saves graphs and result folders for review."
        )
        intro.setWordWrap(True)
        intro.setStyleSheet(f"color:{self._theme['text']}; font-size:12px;")
        body.addWidget(intro)

        tabs = QTabWidget()
        tabs.setDocumentMode(True)
        tabs.setMovable(False)
        tabs.setUsesScrollButtons(True)

        for spec in self._tab_specs():
            tabs.addTab(self._build_tab(spec), str(spec["tab"]))

        body.addWidget(tabs, 1)

        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        close_btn.setCursor(Qt.PointingHandCursor)
        close_btn.setStyleSheet(self._button_stylesheet())
        body.addWidget(close_btn, 0, Qt.AlignRight)

        self.setStyleSheet(self._dialog_stylesheet())

    def _build_theme_palette(self) -> dict[str, str]:
        if self._theme_is_dark:
            return {
                "text": "#EAEAEA",
                "heading": "#F0F0F0",
                "muted_text": "#D8D8D8",
                "card_text": "#CFCFCF",
                "dialog_bg": "#1A1A1A",
                "dialog_border": "#2A2A2A",
                "pane_bg": "#141414",
                "tab_bg": "#171717",
                "tab_text": "#BEBEBE",
                "tab_selected_bg": "#222222",
                "tab_selected_text": "#F0F0F0",
                "tab_hover_bg": "#1D1D1D",
                "tab_hover_text": "#D8D8D8",
                "card_bg": "#171717",
                "card_border": "#2A2A2A",
                "button_bg": "#2A2A2A",
                "button_border": "#3A3A3A",
                "button_hover_bg": "#333333",
                "button_hover_border": "#4A4A4A",
                "button_pressed_bg": "#252525",
            }
        return {
            "text": "#111111",
            "heading": "#111111",
            "muted_text": "#4D4D4D",
            "card_text": "#1D1D1D",
            "dialog_bg": "#F7F7F7",
            "dialog_border": "#D4D4D4",
            "pane_bg": "#FFFFFF",
            "tab_bg": "#EFEFEF",
            "tab_text": "#505050",
            "tab_selected_bg": "#FFFFFF",
            "tab_selected_text": "#111111",
            "tab_hover_bg": "#E6E6E6",
            "tab_hover_text": "#222222",
            "card_bg": "#FFFFFF",
            "card_border": "#D4D4D4",
            "button_bg": "#FFFFFF",
            "button_border": "#CFCFCF",
            "button_hover_bg": "#F0F0F0",
            "button_hover_border": "#BDBDBD",
            "button_pressed_bg": "#E8E8E8",
        }

    def _button_stylesheet(self) -> str:
        return (
            "QPushButton { "
            f"background: {self._theme['button_bg']}; color: {self._theme['text']}; border: 1px solid {self._theme['button_border']};"
            " border-radius: 6px; padding: 7px 18px; font-size: 12px;"
            " }"
            "QPushButton:hover { "
            f"background: {self._theme['button_hover_bg']}; border-color: {self._theme['button_hover_border']};"
            " }"
            "QPushButton:pressed { "
            f"background: {self._theme['button_pressed_bg']};"
            " }"
        )

    def _dialog_stylesheet(self) -> str:
        return f"""
            QDialog {{
                background: {self._theme['dialog_bg']};
                border: 1px solid {self._theme['dialog_border']};
                border-radius: 12px;
            }}
            QLabel {{
                background: transparent;
            }}
            QScrollArea {{
                border: none;
                background: transparent;
            }}
            QScrollArea > QWidget > QWidget {{
                background: transparent;
            }}
            QTabWidget::pane {{
                border: 1px solid {self._theme['dialog_border']};
                border-radius: 10px;
                top: -1px;
                background: {self._theme['pane_bg']};
            }}
            QTabBar::tab {{
                background: {self._theme['tab_bg']};
                color: {self._theme['tab_text']};
                border: 1px solid {self._theme['dialog_border']};
                padding: 8px 12px;
                min-width: 78px;
            }}
            QTabBar::tab:selected {{
                background: {self._theme['tab_selected_bg']};
                color: {self._theme['tab_selected_text']};
            }}
            QTabBar::tab:hover:!selected {{
                background: {self._theme['tab_hover_bg']};
                color: {self._theme['tab_hover_text']};
            }}
            QFrame#HelpRefCard {{
                background: {self._theme['card_bg']};
                border: 1px solid {self._theme['card_border']};
                border-radius: 10px;
            }}
        """

    def _top_window(self) -> QWidget | None:
        try:
            parent = self.parentWidget()
            if parent is None:
                return None
            return parent.window() if hasattr(parent, "window") else parent
        except Exception:
            return None

    def _ensure_dim_overlay(self) -> None:
        top = self._top_window()
        if top is None:
            return

        if self._dim_overlay is None or self._dim_overlay.parentWidget() is not top:
            try:
                if self._dim_overlay is not None:
                    self._dim_overlay.deleteLater()
            except Exception:
                pass
            self._dim_overlay = DimOverlay(top, on_click=self.reject)

        try:
            self._dim_overlay.setGeometry(top.rect())
        except Exception:
            pass

        if self._overlay_filter is None:
            class _Filter(QObject):
                def __init__(self, dlg: "HelpDialog"):
                    super().__init__(dlg)
                    self._dlg = dlg

                def eventFilter(self, obj, event):
                    try:
                        if event.type() in (QEvent.Resize, QEvent.Show):
                            self._dlg._ensure_dim_overlay()
                    except Exception:
                        pass
                    return False

            self._overlay_filter = _Filter(self)
            try:
                top.installEventFilter(self._overlay_filter)
            except Exception:
                pass

    def _set_dimmed(self, on: bool) -> None:
        try:
            if on:
                self._ensure_dim_overlay()
                if self._dim_overlay is not None:
                    self._dim_overlay.show()
                    self._dim_overlay.raise_()
            else:
                if self._dim_overlay is not None:
                    self._dim_overlay.hide()
        except Exception:
            pass

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._set_dimmed(True)

    def closeEvent(self, event) -> None:
        try:
            self._set_dimmed(False)
        except Exception:
            pass
        super().closeEvent(event)

    def accept(self) -> None:
        try:
            self._set_dimmed(False)
        except Exception:
            pass
        return super().accept()

    def reject(self) -> None:
        try:
            self._set_dimmed(False)
        except Exception:
            pass
        return super().reject()

    def _build_tab(self, spec: dict) -> QWidget:
        page = QWidget()
        page_layout = QVBoxLayout(page)
        page_layout.setContentsMargins(0, 0, 0, 0)
        page_layout.setSpacing(0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)

        content = QWidget()
        root = QVBoxLayout(content)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(14)

        title = QLabel(str(spec["title"]))
        title.setStyleSheet(f"color:{self._theme['heading']}; font-size:16px; font-weight:600;")
        root.addWidget(title)

        desc = QLabel(str(spec["description"]))
        desc.setWordWrap(True)
        desc.setStyleSheet(f"color:{self._theme['muted_text']}; font-size:12px;")
        root.addWidget(desc)

        image_row = self._build_image_row(spec)
        if image_row is not None:
            root.addWidget(image_row)

        steps_title = QLabel("What to do")
        steps_title.setStyleSheet(f"color:{self._theme['heading']}; font-size:13px; font-weight:600;")
        root.addWidget(steps_title)

        steps = QLabel(self._list_to_html(spec.get("steps") or []))
        steps.setTextFormat(Qt.RichText)
        steps.setWordWrap(True)
        steps.setStyleSheet(f"color:{self._theme['muted_text']}; font-size:12px;")
        root.addWidget(steps)

        if spec.get("embed_demo") == "live_widgets":
            demo_title = QLabel("Interactive Example")
            demo_title.setStyleSheet(f"color:{self._theme['heading']}; font-size:13px; font-weight:600;")
            root.addWidget(demo_title)
            root.addWidget(_HelpInteractiveDemo(theme_mode=self._theme_mode))

        edge_case = str(spec.get("edge_case") or "").strip()
        if edge_case:
            root.addWidget(self._build_info_panel("Edge Case", edge_case))

        note = str(spec.get("note") or "").strip()
        if note:
            root.addWidget(self._build_info_panel("Tip", note))

        root.addStretch(1)
        scroll.setWidget(content)
        page_layout.addWidget(scroll)
        return page

    def _build_image_row(self, spec: dict) -> QWidget | None:
        image_path = str(spec.get("image_path") or "").strip()
        image_label = str(spec.get("image_label") or "").strip()
        if not image_path:
            return None

        pix = self._themed_help_pixmap(image_path, 56)
        if pix.isNull():
            return None

        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        icon_lab = QLabel()
        icon_lab.setPixmap(pix)
        icon_lab.setFixedSize(64, 64)
        icon_lab.setAlignment(Qt.AlignCenter)
        icon_lab.setStyleSheet(
            f"background:{self._theme['card_bg']}; border:1px solid {self._theme['card_border']}; border-radius:10px;"
        )
        layout.addWidget(icon_lab, 0, Qt.AlignTop)

        if image_label:
            text_lab = QLabel(image_label)
            text_lab.setWordWrap(True)
            text_lab.setStyleSheet(f"color:{self._theme['card_text']}; font-size:12px;")
            layout.addWidget(text_lab, 1)
        else:
            layout.addStretch(1)

        return row

    def _themed_help_pixmap(self, image_path: str, size: int) -> QPixmap:
        try:
            icon = QIcon(image_path)
            pix = icon.pixmap(size, size)
            if pix.isNull():
                return pix

            if not str(image_path).lower().endswith(".svg"):
                return pix

            tinted = QPixmap(pix.size())
            tinted.fill(Qt.transparent)

            painter = QPainter(tinted)
            try:
                painter.drawPixmap(0, 0, pix)
                painter.setCompositionMode(QPainter.CompositionMode_SourceIn)
                painter.fillRect(tinted.rect(), QColor(self._theme["text"]))
            finally:
                painter.end()

            return tinted
        except Exception:
            return QIcon(image_path).pixmap(size, size)

    def _build_info_panel(self, title: str, text: str) -> QWidget:
        label = QLabel(f"<b>{title}:</b> {text}")
        label.setTextFormat(Qt.RichText)
        label.setWordWrap(True)
        label.setStyleSheet(
            f"color:{self._theme['card_text']}; font-size:12px; background:{self._theme['card_bg']}; border:1px solid {self._theme['card_border']}; border-radius:8px; padding:10px;"
        )
        return label

    @staticmethod
    def _list_to_html(items: list[str]) -> str:
        parts = []
        for item in items:
            parts.append(f"<div style='margin:0 0 6px 0;'>&bull; {item}</div>")
        return "".join(parts)

    @staticmethod
    def _tab_specs() -> list[dict]:
        settings_icon = Path(__file__).resolve().parents[2] / "resources" / "icons" / "settings.svg"
        app_icon = Path(__file__).resolve().parents[2] / "resources" / "thermal_bench.ico"
        return [
            {
                "tab": "1. Setup",
                "title": "Configure The Application",
                "description": "Set the external tool paths and notification settings before you run your first benchmark.",
                "image_path": str(settings_icon),
                "image_label": "Use the gear button in the left rail to open Settings and configure FurMark, Prime95, ntfy, and appearance.",
                "steps": [
                    "Open Settings from the left rail.",
                    "Set the FurMark executable path.",
                    "Set the Prime95 executable path.",
                    "Optionally configure your ntfy topic or full ntfy URL.",
                    "Close the dialog to save the updated values.",
                ],
                "note": "If the Run button remains disabled, verify that the required stress tool paths are filled in Settings.",
            },
            {
                "tab": "2. CSV",
                "title": "Select The Sensor Source",
                "description": "ThermalBench reads live sensor data from the HWiNFO CSV log that HWiNFO is already writing.",
                "steps": [
                    "Make sure HWiNFO logging is enabled and already running.",
                    "Pick the hwinfo.csv file in ThermalBench.",
                    "Confirm the CSV status indicator shows a healthy state.",
                ],
                "note": "ThermalBench does not start HWiNFO for you. HWiNFO logging must already be active.",
            },
            {
                "tab": "3. Sensors",
                "title": "Choose Sensors",
                "description": "Only the selected sensors are tracked in the live view, stored in the result window, and shown in graphs and statistics.",
                "steps": [
                    "Open the sensor picker.",
                    "Select the CPU, GPU, power, temperature, or other sensors you want to record.",
                    "Review the selected sensors summary before starting the run.",
                ],
                "edge_case": "If the sensor picker shows a flat list of sensors instead of devices with their sensors grouped underneath, open HWiNFO, go to Main Settings, enable Shared Memory support, and then reopen the sensor picker in ThermalBench.",
            },
            {
                "tab": "4. Run",
                "title": "Set Up The Benchmark",
                "description": "Define the case metadata and the stress-test timing before launching the run.",
                "image_path": str(app_icon),
                "image_label": "ThermalBench groups results by case name, and each finished run gets its own run folder under that case.",
                "steps": [
                    "Set the case name.",
                    "Choose warmup time and logging time.",
                    "Select the FurMark demo and resolution.",
                    "Choose whether to stress CPU, GPU, or both.",
                ],
            },
            {
                "tab": "5. Start",
                "title": "Start And Monitor A Run",
                "description": "Launching a run starts the stress tools, updates the live table, and continuously redraws the live graph. The embedded demo below uses the actual live widgets so you can see that interaction before starting a real run.",
                "steps": [
                    "Click Run.",
                    "Watch the live table for incoming sensor values.",
                    "Toggle sensors in the live table when you want to show or hide their lines in the live graph.",
                    "Watch the live graph for real-time trends.",
                    "Use Abort if you need to stop the run early.",
                ],
                "embed_demo": "live_widgets",
            },
            {
                "tab": "6. Results",
                "title": "Review Results",
                "description": "After a run finishes, the Results page lets you browse run folders, preview plots, and compare saved runs.",
                "steps": [
                    "Open the Results page from the left rail.",
                    "Select a run folder or previewable file.",
                    "Review graphs and saved outputs.",
                    "Open the Legend & Stats subwindow when you want to toggle sensors on or off in the graph preview.",
                ],
            },
            {
                "tab": "7. Compare",
                "title": "Create Compare Results",
                "description": "Compare results are a core workflow when you want to inspect multiple runs together, check behavior differences, and save a combined compare result.",
                "steps": [
                    "Open the Results page from the left rail.",
                    "Select the runs you want to compare in the results tree.",
                    "Click the Compare Button to open the compare result window.",
                    "Review the combined compare graphs and compare statistics once the compare result is built.",
                    "Use the saved compare result later from the Results page like any other result folder.",
                ],
                "note": "If compare is a major part of your workflow, think of it as creating a new saved result from multiple existing runs rather than just opening a temporary view.",
            },
            {
                "tab": "8. Reports",
                "title": "Copy Data For Reports",
                "description": "Use the legend/statistics popup when you need a Word-compatible table for reports or documentation.",
                "steps": [
                    "Open a run graph in the Results page.",
                    "Open the legend and statistics popup.",
                    "Click Copy Table.",
                    "Paste directly into Word.",
                ],
                "note": "Copying from ntfy notifications is not the same as using Copy Table inside the app. Use the in-app Copy Table button for the exact Word result.",
            },
        ]