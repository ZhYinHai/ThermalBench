from __future__ import annotations

from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QEvent, QObject, QSize, Qt, QTimer
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QTabWidget,
    QToolButton,
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
            "These are the actual live widgets. The table on top and the graph on the bottom are driven through the same monitor to graph signal path as a real run."
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
            "ThermalBench automates a stress test run, records the selected HWiNFO sensors, "
            "and saves graphs and result folders for review."
        )
        intro.setWordWrap(True)
        intro.setStyleSheet(f"color:{self._theme['text']}; font-size:12px;")
        body.addWidget(intro)

        tabs = QTabWidget()
        tabs.setDocumentMode(True)
        tabs.setMovable(False)
        tabs.setUsesScrollButtons(True)
        self._tabs = tabs

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
        self._apply_tab_scroll_button_style()

    def _tinted_pixmap(self, svg_path: str, color: str, size: int) -> QPixmap:
        try:
            pix = QIcon(svg_path).pixmap(size, size)
            if pix.isNull():
                return pix
            tinted = QPixmap(pix.size())
            tinted.fill(Qt.transparent)
            painter = QPainter(tinted)
            try:
                painter.drawPixmap(0, 0, pix)
                painter.setCompositionMode(QPainter.CompositionMode_SourceIn)
                painter.fillRect(tinted.rect(), QColor(color))
            finally:
                painter.end()
            return tinted
        except Exception:
            return QPixmap()

    def _apply_tab_scroll_button_style(self) -> None:
        try:
            icon_dir = Path(__file__).resolve().parents[2] / "resources" / "icons"
            left_svg = str(icon_dir / "left_chevron.svg")
            right_svg = str(icon_dir / "right_chevron.svg")
            arrow_color = self._theme["tab_text"]
            icon_size = QSize(14, 14)
            btn_ss = (
                f"QToolButton {{ background: {self._theme['button_bg']}; "
                f"border: 1px solid {self._theme['button_border']}; "
                f"border-radius: 6px; padding: 2px; }}"
                f"QToolButton:hover {{ background: {self._theme['button_hover_bg']}; "
                f"border-color: {self._theme['button_hover_border']}; }}"
                f"QToolButton:pressed {{ background: {self._theme['button_pressed_bg']}; }}"
            )
            tab_bar = self._tabs.tabBar()
            for btn in tab_bar.findChildren(QToolButton):
                arrow = btn.arrowType()
                if arrow == Qt.LeftArrow:
                    pix = self._tinted_pixmap(left_svg, arrow_color, 16)
                elif arrow == Qt.RightArrow:
                    pix = self._tinted_pixmap(right_svg, arrow_color, 16)
                else:
                    continue
                if not pix.isNull():
                    btn.setIcon(QIcon(pix))
                    btn.setArrowType(Qt.NoArrow)
                    btn.setIconSize(icon_size)
                btn.setStyleSheet(btn_ss)
                btn.setCursor(Qt.PointingHandCursor)
        except Exception:
            pass

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

        self._build_steps_block(spec.get("steps") or [], root)

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

    def _build_steps_block(self, steps: list, root) -> None:
        """Render a steps list into root, supporting inline {"image": path} entries."""
        text_buf: list[str] = []

        def _flush() -> None:
            if not text_buf:
                return
            lbl = QLabel(self._list_to_html(text_buf))
            lbl.setTextFormat(Qt.RichText)
            lbl.setWordWrap(True)
            lbl.setStyleSheet(f"color:{self._theme['muted_text']}; font-size:12px;")
            root.addWidget(lbl)
            text_buf.clear()

        for item in steps:
            if isinstance(item, dict) and "image" in item:
                _flush()
                scale = float(item.get("scale", 0.75))
                widget = self._build_inline_image(str(item["image"]), scale=scale)
                if widget is not None:
                    root.addWidget(widget)
            elif isinstance(item, str):
                text_buf.append(item)

        _flush()

    def _build_inline_image(self, image_path: str, scale: float = 1.0) -> QWidget | None:
        """Render an image inline. scale=0.5 renders at 50% of natural size."""
        try:
            pix = QPixmap(image_path)
            if pix.isNull():
                return None
            if scale != 1.0:
                pix = pix.scaled(
                    int(pix.width() * scale),
                    int(pix.height() * scale),
                    Qt.KeepAspectRatio,
                    Qt.SmoothTransformation,
                )
            lbl = QLabel()
            lbl.setPixmap(pix)
            lbl.setScaledContents(False)
            lbl.setAlignment(Qt.AlignLeft | Qt.AlignTop)
            lbl.setStyleSheet(
                f"background:{self._theme['card_bg']}; border:1px solid {self._theme['card_border']};"
                " border-radius:8px; padding:8px;"
            )
            lbl.setMaximumWidth(pix.width() + 18)
            return lbl
        except Exception:
            return None

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
        # Single source of truth lives in _HelpContentMixin; delegate at runtime
        # (defined later in this module, but already present when this is called).
        return _HelpContentMixin._tab_specs()

# ---------------------------------------------------------------------------
# Shared content-building logic used by both HelpDialog and HelpPanel.
# _tab_specs() is the single source of truth here; HelpDialog delegates to it.
# ---------------------------------------------------------------------------
class _HelpContentMixin:
    """Methods shared between HelpDialog and HelpPanel."""

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

    def _tinted_pixmap(self, svg_path: str, color: str, size: int) -> QPixmap:
        try:
            pix = QIcon(svg_path).pixmap(size, size)
            if pix.isNull():
                return pix
            tinted = QPixmap(pix.size())
            tinted.fill(Qt.transparent)
            painter = QPainter(tinted)
            try:
                painter.drawPixmap(0, 0, pix)
                painter.setCompositionMode(QPainter.CompositionMode_SourceIn)
                painter.fillRect(tinted.rect(), QColor(color))
            finally:
                painter.end()
            return tinted
        except Exception:
            return QPixmap()

    def _apply_tab_scroll_button_style(self) -> None:
        try:
            icon_dir = Path(__file__).resolve().parents[2] / "resources" / "icons"
            left_svg = str(icon_dir / "left_chevron.svg")
            right_svg = str(icon_dir / "right_chevron.svg")
            arrow_color = self._theme["tab_text"]
            icon_size = QSize(14, 14)
            btn_ss = (
                f"QToolButton {{ background: {self._theme['button_bg']}; "
                f"border: 1px solid {self._theme['button_border']}; "
                f"border-radius: 6px; padding: 2px; }}"
                f"QToolButton:hover {{ background: {self._theme['button_hover_bg']}; "
                f"border-color: {self._theme['button_hover_border']}; }}"
                f"QToolButton:pressed {{ background: {self._theme['button_pressed_bg']}; }}"
            )
            tab_bar = self._tabs.tabBar()
            for btn in tab_bar.findChildren(QToolButton):
                arrow = btn.arrowType()
                if arrow == Qt.LeftArrow:
                    pix = self._tinted_pixmap(left_svg, arrow_color, 16)
                elif arrow == Qt.RightArrow:
                    pix = self._tinted_pixmap(right_svg, arrow_color, 16)
                else:
                    continue
                if not pix.isNull():
                    btn.setIcon(QIcon(pix))
                    btn.setArrowType(Qt.NoArrow)
                    btn.setIconSize(icon_size)
                btn.setStyleSheet(btn_ss)
                btn.setCursor(Qt.PointingHandCursor)
        except Exception:
            pass

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

        self._build_steps_block(spec.get("steps") or [], root)

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

    def _build_steps_block(self, steps: list, root) -> None:
        """Render a steps list into root, supporting inline {"image": path} entries."""
        text_buf: list[str] = []

        def _flush() -> None:
            if not text_buf:
                return
            lbl = QLabel(self._list_to_html(text_buf))
            lbl.setTextFormat(Qt.RichText)
            lbl.setWordWrap(True)
            lbl.setStyleSheet(f"color:{self._theme['muted_text']}; font-size:12px;")
            root.addWidget(lbl)
            text_buf.clear()

        for item in steps:
            if isinstance(item, dict) and "image" in item:
                _flush()
                scale = float(item.get("scale", 0.75))
                widget = self._build_inline_image(str(item["image"]), scale=scale)
                if widget is not None:
                    root.addWidget(widget)
            elif isinstance(item, str):
                text_buf.append(item)

        _flush()

    def _build_inline_image(self, image_path: str, scale: float = 1.0) -> QWidget | None:
        try:
            pix = QPixmap(image_path)
            if pix.isNull():
                return None
            if scale != 1.0:
                pix = pix.scaled(
                    int(pix.width() * scale),
                    int(pix.height() * scale),
                    Qt.KeepAspectRatio,
                    Qt.SmoothTransformation,
                )
            lbl = QLabel()
            lbl.setPixmap(pix)
            lbl.setScaledContents(False)
            lbl.setAlignment(Qt.AlignLeft | Qt.AlignTop)
            lbl.setStyleSheet(
                f"background:{self._theme['card_bg']}; border:1px solid {self._theme['card_border']};"
                " border-radius:8px; padding:8px;"
            )
            # Wrap in a scroll area so the image is always fully visible
            # vertically, but can never force a horizontal overflow in the panel.
            wrapper = QScrollArea()
            wrapper.setWidget(lbl)
            wrapper.setWidgetResizable(False)
            wrapper.setFrameShape(QScrollArea.NoFrame)
            wrapper.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            wrapper.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            wrapper.setFixedHeight(pix.height() + 20)
            wrapper.setStyleSheet("QScrollArea { background: transparent; border: none; }")
            return wrapper
        except Exception:
            return None

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
                "tab": "1. CSV",
                "title": "Configure HWiNFO Sensor Logging",
                "description": "ThermalBench reads sensor data from a fixed CSV path it controls. You cannot pick a different file. HWiNFO must be configured to log to that exact location.",
                "steps": [
                    "Click the <b>Open HWiNFO</b> button.",
                    "In HWiNFO, click on the <b>Logging Start</b> button, shown in the image.",
                    {"image": str(Path(__file__).resolve().parents[2] / "resources" / "helper_window_images" / "logging_start.png")},
                    "While choosing the path for HWiNFO to log to, make sure to use the path shown in ThermalBench. Copy the path directly from ThermalBench to avoid mistakes.",
                    {"image": str(Path(__file__).resolve().parents[2] / "resources" / "helper_window_images" / "csv_to_log_to.png")},
                    "Follow the steps in the image below. HWiNFO must then be actively writing to that path.",
                    {"image": str(Path(__file__).resolve().parents[2] / "resources" / "helper_window_images" / "instruc.png")},
                    "<b>Note:<br></b> <b>Step 1</b> is mandatory only at first installation. Once you have done it once, HWiNFO will remember the logging path and automatically log to it every time you start HWiNFO in the future. You only need to do Step 1 again if you change your HWiNFO logging path or install ThermalBench on a new system.<br><b>Step 2 and 3</b> are recurrent and must be done at the start of every boot/session to ensure HWiNFO is actively logging to the correct path.",
                    "Back in ThermalBench, confirm the <b>CSV status indicator</b> (next to the path field) shows a healthy/green state. If it shows an error, verify that HWiNFO is running, logging is active, and the output path matches exactly.",
                ],
                "note": "ThermalBench does not launch HWiNFO for you, therefore you must manually log hwinfo data to the specified CSV path every time at application launch. If HWiNFO logs to any other path, ThermalBench will not see the data and the CSV indicator will show an error.",
            },
            {
                "tab": "2. Sensors",
                "title": "Choose Which Sensors To Record",
                "description": "Only the sensors you select here are shown in the live view, stored in result folders, and included in graphs and statistics. Take a moment to pick exactly what you need.",
                "steps": [
                    "On the Run page, click the <b>Select sensors... button</b> to open the sensor selection dialog.",
                    "The picker shows sensors grouped by device (e.g. CPU, GPU, Storage). <b>Expand each device</b> to see its individual sensors.",
                    "Check the box next to each sensor you want to record.",
                    "<b>Important:</b> Check only sensors you need. Keep it at 2 to 10 sensors for optimal performance. <b>Fewer sensors means cleaner graphs and smaller result files.</b>",
                    "Click <b>Confirm</b> to apply the selection.",
                    "Back on the Run page, verify the <b>sensor summary</b> (left of the sensor select button) to review the sensors you just selected.",
                ],
                "edge_case": "If the sensor picker shows a flat, ungrouped list instead of sensors grouped under device headers, open HWiNFO → Settings → General → Main Settings, enable <b>Shared Memory Support</b>, restart HWiNFO logging, and then reopen the sensor picker in ThermalBench.",
            },
            {
                "tab": "3. Run Setup",
                "title": "Configure The Benchmark Run",
                "description": "Before clicking Run, fill in the case name and timing fields. These settings are saved automatically and remembered for your next session.",
                "steps": [
                    "In the <b>Name</b> field, type a descriptive name for this group of tests. All runs with the same case name are grouped together in the Results page.",
                    "Set the <b>Warmup</b> time (minutes + seconds). This is the period the stress tools run before sensor recording begins. Recommended minimum is at least 15 minutes for thermals to stabilise.",
                    "Set the <b>Log</b> time (minutes + seconds). This is the actual recording duration after warmup ends. Recommended minimum is 10 minutes for meaningful averages.",
                    "Select the <b>FurMark demo</b> from the dropdown (e.g. Furmark Knot OpenGL, Vulkan). The OpenGL demos show a moving 3D render; Vulkan demos show a black window — this is normal, the GPU is still being fully stressed.",
                    "Select the <b>FurMark resolution</b> from the second dropdown (e.g. 1920×1080, 3840×2160). Higher resolution = more GPU load.",
                    "Check or uncheck <b>Stress CPU</b> and <b>Stress GPU</b> to control which tools are launched. You can run CPU only, GPU only, or both simultaneously.",
                ],
                "note": "The case name is the only field that affects where result files are saved. Everything else only affects how the benchmark runs.",
            },
            {
                "tab": "4. Start & Monitor",
                "title": "Start And Monitor A Run",
                "description": "Launching a run starts the selected stress tools, feeds live sensor readings into the table and graph below, and saves a result folder when the run completes. The interactive demo at the bottom uses the real live widgets.",
                "steps": [
                    "Click the <b>Run</b> button. ThermalBench launches the configured stress tools (FurMark and/or Prime95) in the background.",
                    "The <b>live sensor table</b> will begin populating within a few seconds. Each row is one of your selected sensors with its current reading.",
                    "The <b>warmup phase</b> runs silently. Sensors are updating but data is not yet recorded. A phase marker line will appear in the graph when warmup ends and logging begins.",
                    "Once logging starts, watch the <b>live graph</b> for real time temperature and power trends. A rising slope that flattens indicates thermals are stabilising.",
                    "Use the <b>checkboxes in the live table</b> to show or hide individual sensor lines in the live graph at any time during the run.",
                    "When the configured log duration elapses, the run ends automatically. The stress tools are closed and a result folder is saved under <code>&lt;case_name&gt;/&lt;timestamp&gt;/</code>.",
                    "If you need to stop early, click <b>Abort</b>. Do not close Furmark or Prime95 windows manually, as that may cause issues. Always use the Abort button to end the run.",
                ],
                "embed_demo": "live_widgets",
                "note": "Do not close ThermalBench or put the system to sleep during a run. Doing so aborts the run and may produce an incomplete result file.",
            },
            {
                "tab": "5. Results",
                "title": "Review A Saved Run",
                "description": "The Results page lets you browse all saved run folders, preview their graphs, and inspect per-sensor statistics. Open it any time. You do not need to have just finished a run.",
                "steps": [
                    "Click the <b>node icon</b> in the left navigation rail to open the Results page.",
                    "In the <b>left panel</b>, expand a case folder to see all run folders under it. Click any run folder to load its graph preview on the right.",
                    "The <b>graph preview</b> appears on the right side.",
                    "Click the <b>Legend &amp; Stats</b> button (top-right corner of the graph area) to open the statistics popup. It shows Min, Max, and Average for every sensor across the full recording period.",
                    "In the Legend &amp; Stats popup, use the <b>sensor checkboxes</b> to show or hide individual sensor lines in the graph preview.",
                ],
                "note": "Graph previews are generated from the saved CSV inside each run folder. If the CSV is missing or corrupt, the preview will be empty. The original HWiNFO CSV is also saved inside the run folder as a backup.",
            },
            {
                "tab": "6. Compare",
                "title": "Compare Multiple Runs",
                "description": "The compare workflow overlays two or more runs in a single graph and shows a combined statistics table. The result is saved as a new folder so you can revisit it later.",
                "steps": [
                    "Go to the <b>Results page</b> using the left rail.",
                    "In the left panel, <b>double click</b> to select two or more run folders you want to compare. Selected folders are highlighted.",
                    "Click the <b>Compare</b> button that becomes clickable below the results tree when multiple runs are selected.",
                    "ThermalBench builds the compare result.",
                    "The <b>compare graph</b> opens on the right, with each run drawn as a separate line set. A combined statistics table shows Min/Max/Avg per run per sensor.",
                    "Use the <b>Legend &amp; Stats</b> popup (top-right of the graph) to toggle which sensors and which runs are visible in the compare graph.",
                    "The compare result is automatically saved as a new folder under <code>&lt;case_name&gt;/&lt;run_A&gt; vs &lt;run_B&gt;/</code> and will appear in the left panel for future reference.",
                ],
                "note": "Compare folders behave exactly like regular run folders. You can select and preview them again at any time from the Results page.",
            },
            {
                "tab": "7. Reports",
                "title": "Export Data For Reports",
                "description": "The Legend & Stats popup contains a one-click Copy Table feature that puts a formatted table on your clipboard, ready to paste directly into Microsoft Word, Google Docs, or Excel. You can also copy the graph as a PNG image.",
                "steps": [
                    "Go to the <b>Results page</b> and select the run (or compare result) you want to report on.",
                    "<b>To export the graph as an image:</b> click the <b>Copy Graph</b> button (top-right of the graph area). For compare results with multiple sensors, a small popup lets you pick which graphs to copy. The selected graph(s) are copied to your clipboard as a PNG. Paste into Word, PowerPoint, or any image editor with <b>Ctrl + V</b>.",
                    "<b>To export the stats table:</b> click <b>Legend &amp; Stats</b> (top-right of the graph area) to open the statistics popup.",
                    "Review the table. It shows <b>Min, Max, and Average</b> for every visible sensor over the full recording period.",
                    "If you want to include only specific sensors in the exported table, <b>uncheck the sensors</b> you do not need using the checkboxes on the left of each row.",
                    "Click <b>Copy Table</b> at the top of the popup. The table is copied to your clipboard in a tab separated format.",
                    "Switch to Microsoft Word, Google Docs, or Excel and press <b>Ctrl + V</b> to paste. Word will automatically format the data as a table.",
                ],
                "note": "The Copy Table button only copies currently visible (checked) sensors. Uncheck sensors in the popup before copying if you want a shorter table.",
            },
            {
                "tab": "8. Settings (Optional)",
                "title": "Configure The Application",
                "description": "Complete this once before your first run. Settings are saved automatically and persist across restarts.",
                "image_path": str(settings_icon),
                "image_label": "Click the gear icon at the bottom of the left navigation rail to open Settings.",
                "steps": [
                    "<b>Click the gear icon</b> at the bottom of the left navigation rail to open the Settings dialog.",
                    "(Optional) In the <b>Notifications</b> field, enter your ntfy topic name (e.g. <code>my-bench-alerts</code>) or a full ntfy URL (e.g. <code>https://ntfy.sh/my-bench-alerts</code>). ThermalBench will push a notification when each run finishes or if an error occurs.",
                    "(Optional) In the Mode drop down, select Device, Dark, or Light theme. Device theme matches your OS setting, while Dark and Light force that mode regardless of OS.",
                    "Click <b>Accept</b> to close the dialog and save all values.",
                    "Check the <b>Run</b> button on the Run page. It must be enabled (not greyed out) before you can start a benchmark. If it is still disabled, reopen Settings and verify both tool paths are set correctly.",
                ],
                "note": "The Run button is disabled until at least one stress tool path is configured.",
            },
        ]

    @staticmethod
    def _min_image_width() -> int:
        """Return the minimum panel width needed to fully display the widest inline image.

        Horizontal overhead breakdown:
          - body layout margins:         14 + 14 = 28 px
          - tab content root margins:    16 + 16 = 32 px
          - QTabWidget border/pane:             ~  8 px
          - vertical scrollbar:                ~ 17 px
          - small safety buffer:               ~ 11 px
          Total:                               ~ 96 px
        """
        max_w = 0
        for spec in _HelpContentMixin._tab_specs():
            for item in spec.get("steps") or []:
                if not isinstance(item, dict) or "image" not in item:
                    continue
                try:
                    pix = QPixmap(str(item["image"]))
                    if pix.isNull():
                        continue
                    scale = float(item.get("scale", 0.75))
                    w = int(pix.width() * scale)
                    if w > max_w:
                        max_w = w
                except Exception:
                    pass
        return max_w + 96 if max_w else 300


# ---------------------------------------------------------------------------
# Inline side-panel version — embedded in the MainWindow, visible to the right
# ---------------------------------------------------------------------------
class HelpPanel(QWidget, _HelpContentMixin):
    """Help content as a side panel embedded in the main window."""

    PANEL_WIDTH = 420

    def __init__(self, parent: QWidget | None = None, *, theme_mode: str = "device"):
        super().__init__(parent)

        self._theme_mode = resolve_effective_theme_mode(theme_mode, QApplication.instance())
        self._theme_is_dark = self._theme_mode == "dark"
        self._theme = self._build_theme_palette()

        self.setMinimumWidth(self._min_image_width())

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # --- Header row ---
        header = QWidget()
        header.setObjectName("HelpPanelHeader")
        header.setFixedHeight(42)
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(14, 0, 8, 0)
        header_layout.setSpacing(0)

        title_lbl = QLabel("How to use ThermalBench")
        title_lbl.setStyleSheet(
            f"color:{self._theme['heading']}; font-size:12px; font-weight:600; border:none;"
        )

        close_btn = QToolButton()
        close_btn.setText("✕")
        close_btn.setFixedSize(28, 28)
        close_btn.setCursor(Qt.PointingHandCursor)
        close_btn.setStyleSheet(
            f"QToolButton {{ background:transparent; border:none; border-radius:6px; "
            f"color:{self._theme['tab_text']}; font-size:14px; }}"
            f"QToolButton:hover {{ background:{self._theme['tab_hover_bg']}; }}"
        )
        close_btn.clicked.connect(self.hide)

        header_layout.addWidget(title_lbl, 1)
        header_layout.addWidget(close_btn)
        outer.addWidget(header)

        # --- Separator below header ---
        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setFixedHeight(1)
        sep.setStyleSheet(f"background:{self._theme['dialog_border']}; border:none;")
        outer.addWidget(sep)

        # --- Body ---
        body = QVBoxLayout()
        body.setContentsMargins(14, 10, 14, 14)
        body.setSpacing(10)
        outer.addLayout(body)

        intro = QLabel(
            "ThermalBench automates a stress test run, records the selected HWiNFO sensors, "
            "and saves graphs and result folders for review."
        )
        intro.setWordWrap(True)
        intro.setStyleSheet(f"color:{self._theme['text']}; font-size:12px; border:none;")
        body.addWidget(intro)

        tabs = QTabWidget()
        tabs.setDocumentMode(True)
        tabs.setMovable(False)
        tabs.setUsesScrollButtons(True)
        self._tabs = tabs

        for spec in self._tab_specs():
            tabs.addTab(self._build_tab(spec), str(spec["tab"]))

        body.addWidget(tabs, 1)

        # --- Sticky footer: prev / tab label / next ---
        footer_sep = QFrame()
        footer_sep.setFrameShape(QFrame.HLine)
        footer_sep.setFixedHeight(1)
        footer_sep.setStyleSheet(f"background:{self._theme['dialog_border']}; border:none;")
        outer.addWidget(footer_sep)

        footer = QWidget()
        footer.setFixedHeight(44)
        footer_layout = QHBoxLayout(footer)
        footer_layout.setContentsMargins(14, 0, 14, 0)
        footer_layout.setSpacing(8)

        self._nav_prev_btn = QPushButton("← Previous")
        self._nav_prev_btn.setCursor(Qt.PointingHandCursor)
        self._nav_prev_btn.setFixedHeight(30)
        self._nav_prev_btn.clicked.connect(self._go_prev_tab)

        self._nav_label = QLabel()
        self._nav_label.setAlignment(Qt.AlignCenter)
        self._nav_label.setStyleSheet(
            f"color:{self._theme['muted_text']}; font-size:11px; border:none;"
        )

        self._nav_next_btn = QPushButton("Next →")
        self._nav_next_btn.setCursor(Qt.PointingHandCursor)
        self._nav_next_btn.setFixedHeight(30)
        self._nav_next_btn.clicked.connect(self._go_next_tab)

        footer_layout.addWidget(self._nav_prev_btn)
        footer_layout.addWidget(self._nav_label, 1)
        footer_layout.addWidget(self._nav_next_btn)
        outer.addWidget(footer)

        self._tabs.currentChanged.connect(self._update_nav_footer)
        self._update_nav_footer(0)

        self.setStyleSheet(self._panel_stylesheet())

    def _go_prev_tab(self) -> None:
        i = self._tabs.currentIndex()
        if i > 0:
            self._tabs.setCurrentIndex(i - 1)

    def _go_next_tab(self) -> None:
        i = self._tabs.currentIndex()
        if i < self._tabs.count() - 1:
            self._tabs.setCurrentIndex(i + 1)

    def _update_nav_footer(self, index: int) -> None:
        count = self._tabs.count()
        self._nav_prev_btn.setEnabled(index > 0)
        self._nav_next_btn.setEnabled(index < count - 1)
        self._nav_label.setText(f"{index + 1} / {count}")

    def _panel_stylesheet(self) -> str:
        return f"""
            HelpPanel, HelpPanel > QWidget {{
                background: {self._theme['dialog_bg']};
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
            QPushButton {{
                background: {self._theme['button_bg']};
                color: {self._theme['text']};
                border: 1px solid {self._theme['button_border']};
                border-radius: 8px;
                padding: 4px 14px;
                font-size: 12px;
            }}
            QPushButton:hover {{
                background: {self._theme['button_hover_bg']};
                border-color: {self._theme['button_hover_border']};
            }}
            QPushButton:pressed {{
                background: {self._theme['button_pressed_bg']};
            }}
            QPushButton:disabled {{
                color: {self._theme['muted_text']};
                background: {self._theme['pane_bg']};
                border-color: {self._theme['dialog_border']};
            }}
        """

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._apply_tab_scroll_button_style()

    def set_theme_mode(self, mode: str) -> None:
        """Refresh palette and stylesheet when the app theme changes."""
        self._theme_mode = resolve_effective_theme_mode(mode, QApplication.instance())
        self._theme_is_dark = self._theme_mode == "dark"
        self._theme = self._build_theme_palette()
        self._nav_label.setStyleSheet(
            f"color:{self._theme['muted_text']}; font-size:11px; border:none;"
        )
        self.setStyleSheet(self._panel_stylesheet())