# ui_selected_sensors.py
from __future__ import annotations

from collections import defaultdict

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QWidget,
    QVBoxLayout,
    QTreeWidgetItem,
    QDialogButtonBox,
    QAbstractItemView,
)

from ..widgets.ui_titlebar import TitleBar
from ..widgets.ui_rounding import apply_rounded_corners
from ..widgets.ui_theme import resolve_effective_theme_mode
from ..graph_preview.ui_dim_overlay import DimOverlay
from ..widgets.ui_full_row_tree import FullRowHoverTree
from .ui_sensor_picker import SPD_MAX_TOKEN


class SelectedSensorsDialog(QDialog):
    def __init__(
        self,
        parent: QWidget,
        selected_tokens: list[str],
        group_map: dict[str, str],
        has_spd: bool,
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
        self.setWindowTitle("Selected sensors")
        self.setWindowFlag(Qt.FramelessWindowHint, True)
        self.setWindowFlag(Qt.Window, True)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # Drag-only title bar (no text/buttons), like your picker dialog
        tb = TitleBar(self, "", show_title=False, show_buttons=False, draggable=True)
        tb.setFixedHeight(28)
        outer.addWidget(tb)

        root = QVBoxLayout()
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(10)
        outer.addLayout(root)

        self.tree = FullRowHoverTree(
            hover_rgba=self._theme["tree_hover_rgba"],
            selected_rgba=self._theme["tree_selected_rgba"],
        )
        self.tree.setHeaderHidden(True)
        self.tree.setUniformRowHeights(True)
        self.tree.setSelectionMode(QAbstractItemView.NoSelection)
        self.tree.setFocusPolicy(Qt.NoFocus)
        self.tree.setEditTriggers(QAbstractItemView.NoEditTriggers)
        # Match Legend&Stats interaction: single click toggles expand/collapse.
        # (No checkboxes in this dialog.)
        self.tree.viewport().installEventFilter(self)
        self.tree.installEventFilter(self)
        root.addWidget(self.tree, 1)

        # Build grouped view
        grouped: dict[str, list[str]] = defaultdict(list)

        for tok in selected_tokens:
            if tok == SPD_MAX_TOKEN:
                grouped["Memory / SPD"].append("SPD Hub (Max of DIMMs)")
                continue

            grp = group_map.get(tok, "Other")
            grouped[grp].append(tok)

        # If SPD exists (even if not selected) you might want to show it only if selected.
        # Current behavior: show only selected entries.

        # Insert groups + items
        for grp in sorted(grouped.keys(), key=lambda s: s.lower()):
            gitem = QTreeWidgetItem([grp])
            gitem.setFirstColumnSpanned(True)
            f = gitem.font(0)
            f.setBold(True)
            gitem.setFont(0, f)

            self.tree.addTopLevelItem(gitem)

            for tok in grouped[grp]:
                # match your display format for duplicates: "X #1" -> "X  (#1)"
                disp = tok.replace(" #", "  (#") + (")" if " #" in tok else "")
                it = QTreeWidgetItem(gitem, [disp])
                # keep leaf normal font
                it.setFlags(it.flags() & ~Qt.ItemIsUserCheckable)

        self.tree.expandAll()

        btns = QDialogButtonBox(QDialogButtonBox.Ok)
        btns.accepted.connect(self.accept)
        root.addWidget(btns)

        # Match Legend & Stats popup look
        self.setObjectName("SelectedSensorsDialog")
        self.setStyleSheet(self._dialog_stylesheet())

        self.resize(900, 600)

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
            QDialog#SelectedSensorsDialog {{ background: {self._theme['dialog_bg']}; border: 1px solid {self._theme['dialog_border']}; border-radius: 10px; }}
            QWidget#TitleBar {{ background: {self._theme['titlebar_bg']}; }}

            QTreeWidget {{ background: transparent; border: none; color: {self._theme['text']}; outline: none; }}
            QTreeWidget::item {{ padding: 6px 6px; background: transparent; }}
            QTreeWidget::item:hover {{ background: transparent; }}
            QTreeWidget::item:selected, QTreeWidget::item:selected:hover {{ background: transparent; }}

            QTreeView::branch:selected {{ background: transparent; }}
            QTreeView::branch:hover {{ background: transparent; }}

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
                def __init__(self, dlg: "SelectedSensorsDialog"):
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

    def eventFilter(self, obj, event):
        if obj is self.tree.viewport() and event.type() == QEvent.MouseButtonPress and event.button() == Qt.LeftButton:
            try:
                try:
                    pos = event.position().toPoint()
                except Exception:
                    pos = event.pos()

                item = self.tree.itemAt(pos)
                if item is None:
                    return False

                if item.childCount() > 0:
                    item.setExpanded(not item.isExpanded())
                    return True
            except Exception:
                return False

        return super().eventFilter(obj, event)
