from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Callable

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QTreeWidgetItem,
    QDialogButtonBox,
    QAbstractItemView,
    QPushButton,
)

from ..widgets.ui_titlebar import TitleBar
from ..widgets.ui_rounding import apply_rounded_corners
from ..widgets.ui_theme import resolve_effective_theme_mode
from ..graph_preview.ui_dim_overlay import DimOverlay
from ..widgets.ui_full_row_tree import FullRowHoverTree


class Prime95SettingsDialog(QDialog):
    def __init__(
        self,
        parent: QWidget,
        *,
        settings_lines: list[str],
        weak_lines: list[str],
        preset_name: str,
        confidence: str,
        rationale: str,
        prime_exe: str = "",
        refresh_payload: Callable[[], dict[str, Any]] | None = None,
        theme_mode: str = "device",
    ):
        super().__init__(parent)

        self._dim_overlay: DimOverlay | None = None
        self._overlay_filter: QObject | None = None
        self._theme_mode = resolve_effective_theme_mode(theme_mode, QApplication.instance())
        self._theme_is_dark = self._theme_mode == "dark"
        self._theme = self._build_theme_palette()

        self.corner_radius = 12
        apply_rounded_corners(self, self.corner_radius)

        self.setModal(True)
        self.setWindowTitle("Prime95 torture settings")
        self.setWindowFlag(Qt.FramelessWindowHint, True)
        self.setWindowFlag(Qt.Window, True)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        tb = TitleBar(self, "", show_title=False, show_buttons=False, draggable=True)
        tb.setFixedHeight(28)
        outer.addWidget(tb)

        self._root = QVBoxLayout()
        self._root.setContentsMargins(14, 14, 14, 14)
        self._root.setSpacing(8)
        outer.addLayout(self._root)

        self._preset = str(preset_name or "unknown").strip() or "unknown"
        self._conf = str(confidence or "low").strip() or "low"
        self._prime_exe = str(prime_exe or "").strip()
        self._refresh_payload = refresh_payload

        self._main_widget = QWidget()
        self._main_layout = QVBoxLayout(self._main_widget)
        self._main_layout.setContentsMargins(0, 0, 0, 0)
        self._main_layout.setSpacing(8)
        self._root.addWidget(self._main_widget)

        self._help_widget = QWidget()
        self._help_layout = QVBoxLayout(self._help_widget)
        self._help_layout.setContentsMargins(0, 0, 0, 0)
        self._help_layout.setSpacing(10)
        self._help_widget.hide()
        self._root.addWidget(self._help_widget)

        self._build_main_view(settings_lines, weak_lines, rationale)
        self._build_help_view()

        self.setObjectName("Prime95SettingsDialog")
        self.setStyleSheet(self._dialog_stylesheet())
        self.resize(900, max(1, self.sizeHint().height()))

    def _build_main_view(self, settings_lines: list[str], weak_lines: list[str], rationale: str) -> None:
        self.left_tree = self._create_lines_tree(settings_lines)
        self.right_tree = self._create_lines_tree(weak_lines)

        rat = str(rationale or "").strip()
        if rat:
            self.right_tree.addTopLevelItem(QTreeWidgetItem([f"Rationale: {rat}"]))

        self._sync_tree_heights()

        columns = QHBoxLayout()
        columns.setSpacing(12)

        left_col_widget = QWidget()
        left_col_layout = QVBoxLayout(left_col_widget)
        left_col_layout.setContentsMargins(0, 0, 0, 0)
        left_col_layout.setSpacing(8)

        left_hdr = QLabel("Prime95 settings")
        lf = left_hdr.font()
        lf.setBold(True)
        left_hdr.setFont(lf)

        left_col_layout.addWidget(left_hdr)
        left_col_layout.addWidget(self.left_tree, 1)

        right_col_widget = QWidget()
        right_col_layout = QVBoxLayout(right_col_widget)
        right_col_layout.setContentsMargins(0, 0, 0, 0)
        right_col_layout.setSpacing(8)

        right_col_layout.addWidget(self.right_tree, 1)

        columns.addWidget(left_col_widget, 1)
        columns.addWidget(right_col_widget, 1)
        self._main_layout.addLayout(columns, 1)

        preset_line = f"Inferred preset name: {self._preset}"
        preset_row = QHBoxLayout()
        preset_row.setContentsMargins(0, 0, 0, 0)
        preset_row.setSpacing(8)

        self._preset_label = QLabel(preset_line)
        self._preset_label.setObjectName("InferredPresetLabel")
        pf = self._preset_label.font()
        pf.setBold(True)
        self._preset_label.setFont(pf)
        self._preset_label.setWordWrap(False)
        self._preset_label.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Preferred)
        self._preset_label.setContentsMargins(0, 2, 0, 0)
        self._preset_label.setMargin(3)

        self.help_btn = QPushButton("Help")
        self.help_btn.setFixedWidth(72)
        self.help_btn.setToolTip("How preset inference works")
        self.help_btn.clicked.connect(self._show_preset_help)

        preset_row.addWidget(self._preset_label, 0, Qt.AlignLeft | Qt.AlignBottom)
        preset_row.addWidget(self.help_btn)
        preset_row.addStretch(1)

        self._ok_btn_main = QPushButton("OK")
        self._ok_btn_main.clicked.connect(self.accept)
        preset_row.addWidget(self._ok_btn_main)
        self._main_layout.addLayout(preset_row)

    def _build_help_view(self) -> None:
        self._help_title = QLabel("About inferred Prime95 presets")
        tf = self._help_title.font()
        tf.setBold(True)
        self._help_title.setFont(tf)
        self._help_layout.addWidget(self._help_title)

        self._help_intro = QLabel(
            "ThermalBench reads the saved Prime95 values such as Min FFT, Max FFT, and TortureMem to produce a best-effort label such as 'Blend', 'Large FFTs', or 'Small FFTs'.\n\n"
            "This inferred label is only a display aid. It does not change the actual Prime95 settings. The real behavior still comes from the Prime95 values themselves."
        )
        self._help_intro.setWordWrap(True)
        self._help_intro.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        self._help_layout.addWidget(self._help_intro)

        self._help_change_header = QLabel("How to change the torture test")
        cf = self._help_change_header.font()
        cf.setBold(True)
        self._help_change_header.setFont(cf)
        self._help_layout.addWidget(self._help_change_header)

        self._help_change_body = QLabel(
            '1. Navigate to the <a href="open_prime95_dir"><span style="font-weight:600; text-decoration: underline;">Prime95 directory used by ThermalBench</span></a>.<br>'
            "2. Open prime95.exe and configure the desired torture test settings.<br>"
            "3. Start the torture test to apply and save the new configuration.<br>"
            "4. After the test begins, stop it and close Prime95. Prime95 will save the updated settings to 'prime.txt'.<br>"
            "5. ThermalBench will automatically use these settings the next time it runs."
        )
        self._help_change_body.setWordWrap(True)
        self._help_change_body.setTextFormat(Qt.RichText)
        self._help_change_body.setTextInteractionFlags(Qt.TextBrowserInteraction)
        self._help_change_body.setOpenExternalLinks(False)
        self._help_change_body.linkActivated.connect(self._open_prime95_directory)
        self._help_change_body.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        self._help_layout.addWidget(self._help_change_body, 1)

        help_btns = QHBoxLayout()
        help_btns.setContentsMargins(0, 0, 0, 0)
        help_btns.addStretch(1)
        self._back_btn = QPushButton("Back")
        self._back_btn.clicked.connect(self._show_main_view)
        help_btns.addWidget(self._back_btn)
        self._ok_btn_help = QPushButton("OK")
        self._ok_btn_help.clicked.connect(self.accept)
        help_btns.addWidget(self._ok_btn_help)
        self._help_layout.addLayout(help_btns)

    def _show_preset_help(self) -> None:
        self._main_widget.hide()
        self._help_widget.show()
        self.resize(900, max(1, self.sizeHint().height()))

    def _show_main_view(self) -> None:
        self._refresh_main_view_from_latest_snapshot()
        self._help_widget.hide()
        self._main_widget.show()
        self.resize(900, max(1, self.sizeHint().height()))

    def _refresh_main_view_from_latest_snapshot(self) -> None:
        if self._refresh_payload is None:
            return
        try:
            payload = self._refresh_payload() or {}
            self._apply_payload(payload)
        except Exception:
            pass

    def _apply_payload(self, payload: dict[str, Any]) -> None:
        settings_lines = payload.get("settings_lines")
        weak_lines = payload.get("weak_lines")
        rationale = payload.get("rationale")
        preset_name = payload.get("preset_name")
        confidence = payload.get("confidence")

        self._preset = str(preset_name or "unknown").strip() or "unknown"
        self._conf = str(confidence or "low").strip() or "low"

        self.left_tree.clear()
        for line in settings_lines if isinstance(settings_lines, list) else []:
            text = str(line or "").strip()
            if text:
                self.left_tree.addTopLevelItem(QTreeWidgetItem([text]))

        self.right_tree.clear()
        for line in weak_lines if isinstance(weak_lines, list) else []:
            text = str(line or "").strip()
            if text:
                self.right_tree.addTopLevelItem(QTreeWidgetItem([text]))

        rat = str(rationale or "").strip()
        if rat:
            self.right_tree.addTopLevelItem(QTreeWidgetItem([f"Rationale: {rat}"]))

        preset_line = f"Inferred preset name: {self._preset}"
        self._preset_label.setText(preset_line)

        self._sync_tree_heights()

    def _open_prime95_directory(self, _: str = "") -> None:
        try:
            path_text = str(self._prime_exe or "").strip()
            if not path_text:
                return
            p = Path(path_text).expanduser()
            target = p.parent if p.suffix.lower() == ".exe" else p
            if not target.exists():
                return
            os.startfile(str(target))
        except Exception:
            pass

    def _create_lines_tree(self, lines: list[str]) -> FullRowHoverTree:
        tree = FullRowHoverTree(
            hover_rgba=self._theme["tree_hover_rgba"],
            selected_rgba=self._theme["tree_selected_rgba"],
        )
        tree.setHeaderHidden(True)
        tree.setUniformRowHeights(False)
        tree.setSelectionMode(QAbstractItemView.NoSelection)
        tree.setFocusPolicy(Qt.NoFocus)
        tree.setEditTriggers(QAbstractItemView.NoEditTriggers)
        tree.setItemsExpandable(False)
        tree.setRootIsDecorated(False)
        tree.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        tree.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        for line in lines:
            text = str(line or "").strip()
            if not text:
                continue
            tree.addTopLevelItem(QTreeWidgetItem([text]))
        return tree

    def _tree_content_height(self, tree: FullRowHoverTree) -> int:
        count = int(tree.topLevelItemCount())
        if count <= 0:
            return 28

        row_h = 0
        for i in range(count):
            try:
                rh = int(tree.sizeHintForRow(i))
            except Exception:
                rh = 20
            row_h += max(18, rh)

        try:
            frame = int(tree.frameWidth()) * 2
        except Exception:
            frame = 2

        return row_h + frame + 6

    def _sync_tree_heights(self) -> None:
        try:
            left_h = self._tree_content_height(self.left_tree)
            right_h = self._tree_content_height(self.right_tree)
            target = max(left_h, right_h)
            self.left_tree.setFixedHeight(target)
            self.right_tree.setFixedHeight(target)
        except Exception:
            pass

    def _build_theme_palette(self) -> dict[str, object]:
        if self._theme_is_dark:
            return {
                "dialog_bg": "#1A1A1A",
                "dialog_border": "#2A2A2A",
                "titlebar_bg": "#151515",
                "text": "#EAEAEA",
                "button_bg": "#2A2A2A",
                "button_border": "#3A3A3A",
                "button_hover_bg": "#333333",
                "button_hover_border": "#4A4A4A",
                "button_pressed_bg": "#252525",
                "tree_hover_rgba": (255, 255, 255, 15),
                "tree_selected_rgba": (255, 255, 255, 18),
            }
        return {
            "dialog_bg": "#F7F7F7",
            "dialog_border": "#D4D4D4",
            "titlebar_bg": "#ECECEC",
            "text": "#111111",
            "button_bg": "#FFFFFF",
            "button_border": "#CFCFCF",
            "button_hover_bg": "#F0F0F0",
            "button_hover_border": "#BDBDBD",
            "button_pressed_bg": "#E8E8E8",
            "tree_hover_rgba": (0, 0, 0, 12),
            "tree_selected_rgba": (0, 0, 0, 16),
        }

    def _dialog_stylesheet(self) -> str:
        return f"""
            QDialog#Prime95SettingsDialog {{ background: {self._theme['dialog_bg']}; border: 1px solid {self._theme['dialog_border']}; border-radius: 10px; }}
            QWidget#TitleBar {{ background: {self._theme['titlebar_bg']}; }}
            QLabel {{ color: {self._theme['text']}; }}
            QLabel#InferredPresetLabel {{
                border: 1px solid {self._theme['button_border']};
                border-radius: 8px;
                background: {self._theme['button_bg']};
                padding: 2px 6px;
            }}

            QTreeWidget {{ background: transparent; border: none; color: {self._theme['text']}; outline: none; }}
            QTreeWidget::item {{ padding: 6px 6px; background: transparent; }}
            QTreeWidget::item:hover {{ background: transparent; }}
            QTreeWidget::item:selected, QTreeWidget::item:selected:hover {{ background: transparent; }}

            QTreeView::branch:selected {{ background: transparent; }}
            QTreeView::branch:hover {{ background: transparent; }}

            QPushButton {{
                background: {self._theme['button_bg']};
                color: {self._theme['text']};
                border: 1px solid {self._theme['button_border']};
                border-radius: 8px;
                padding: 6px 12px;
                min-width: 88px;
            }}
            QPushButton:hover {{ background: {self._theme['button_hover_bg']}; border-color: {self._theme['button_hover_border']}; }}
            QPushButton:pressed {{ background: {self._theme['button_pressed_bg']}; }}
            QDialogButtonBox QPushButton {{
                background: {self._theme['button_bg']};
                color: {self._theme['text']};
                border: 1px solid {self._theme['button_border']};
                border-radius: 8px;
                padding: 6px 12px;
                min-width: 88px;
            }}
            QDialogButtonBox QPushButton:hover {{ background: {self._theme['button_hover_bg']}; border-color: {self._theme['button_hover_border']}; }}
            QDialogButtonBox QPushButton:pressed {{ background: {self._theme['button_pressed_bg']}; }}
        """

    def _top_window(self) -> QWidget | None:
        try:
            p = self.parentWidget()
            if p is None:
                return None
            return p.window() if hasattr(p, "window") else p
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
            self._dim_overlay = DimOverlay(top, on_click=self.close)

        try:
            self._dim_overlay.setGeometry(top.rect())
        except Exception:
            pass

        if self._overlay_filter is None:
            class _Filter(QObject):
                def __init__(self, dlg: "Prime95SettingsDialog"):
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

    def showEvent(self, event):
        super().showEvent(event)
        self._set_dimmed(True)
        p = self.parentWidget()
        if p:
            pg = p.geometry()
            sg = self.geometry()
            self.move(pg.center().x() - sg.width() // 2, pg.center().y() - sg.height() // 2)

    def closeEvent(self, event):
        try:
            self._set_dimmed(False)
        except Exception:
            pass
        super().closeEvent(event)

    def accept(self):
        try:
            self._set_dimmed(False)
        except Exception:
            pass
        return super().accept()

    def reject(self):
        try:
            self._set_dimmed(False)
        except Exception:
            pass
        return super().reject()
