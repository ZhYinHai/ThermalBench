# ui_compare_popup.py
"""Compare popup dialog for showing common sensors across selected results.

Uses the same visual style as the Legend & Stats popup.
"""

from __future__ import annotations

from typing import Iterable, Optional, Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QTreeWidget,
    QTreeWidgetItem,
    QAbstractItemView,
    QPushButton,
    QToolButton,
    QLayout,
    QFrame,
    QSizePolicy,
)


class ComparePopup(QDialog):
    def __init__(
        self,
        parent,
        *,
        title: str,
        sensors: Iterable[str],
        on_close: Optional[Callable[[], None]] = None,
        on_compare: Optional[Callable[[list[str]], None]] = None,
    ):
        super().__init__(parent)

        self.setWindowFlag(Qt.Tool, True)
        self.setWindowFlag(Qt.FramelessWindowHint, True)
        self.setModal(False)

        self._on_close = on_close
        self._on_compare = on_compare

        root = QVBoxLayout(self)
        root.setSizeConstraint(QLayout.SetDefaultConstraint)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(10)

        title_row = QHBoxLayout()
        title_row.setContentsMargins(0, 0, 0, 0)

        title_area = QLabel(title)
        title_area.setStyleSheet("color:#EAEAEA; font-weight:600; font-size:13px;")
        title_area.setMinimumWidth(0)
        title_area.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        close_btn = QToolButton()
        close_btn.setText("✕")
        close_btn.setCursor(Qt.PointingHandCursor)
        close_btn.clicked.connect(self.close)
        close_btn.setStyleSheet(
            """
            QToolButton { color:#9A9A9A; background: transparent; border: none; padding: 4px 6px; }
            QToolButton:hover { color:#EAEAEA; background: rgba(255,255,255,0.06); border-radius: 6px; }
            """
        )

        title_row.addWidget(title_area)
        title_row.addStretch(1)
        title_row.addWidget(close_btn)
        root.addLayout(title_row)

        self.tree = QTreeWidget()
        self.tree.setColumnCount(1)
        self.tree.setHeaderHidden(True)
        self.tree.setRootIsDecorated(False)
        self.tree.setUniformRowHeights(True)
        self.tree.setSortingEnabled(False)
        self.tree.setSelectionMode(QAbstractItemView.MultiSelection)
        self.tree.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.tree.setFocusPolicy(Qt.StrongFocus)
        self.tree.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.tree.setFrameShape(QFrame.NoFrame)
        self.tree.setLineWidth(0)

        try:
            self.tree.itemSelectionChanged.connect(self._update_compare_btn_state)
        except Exception:
            pass

        for s in sorted({str(x) for x in (sensors or [])}):
            QTreeWidgetItem(self.tree, [s])

        PAD = 14
        tree_wrap = QVBoxLayout()
        tree_wrap.setContentsMargins(-PAD, 0, -PAD, 0)
        tree_wrap.setSpacing(0)
        tree_wrap.addWidget(self.tree, 1)
        root.addLayout(tree_wrap, 1)

        footer = QHBoxLayout()
        footer.setContentsMargins(0, 0, 0, 0)
        footer.setSpacing(8)
        footer.addStretch(1)

        self.compare_btn = QPushButton("Compare")
        self.compare_btn.setCursor(Qt.PointingHandCursor)
        self.compare_btn.setEnabled(False)
        try:
            self.compare_btn.clicked.connect(self._emit_compare)
        except Exception:
            pass
        footer.addWidget(self.compare_btn)

        root.addLayout(footer)

        self.setStyleSheet(
            """
            QDialog { background: #1A1A1A; border: 1px solid #2A2A2A; border-radius: 10px; }
            QLabel { background: transparent; }

            QTreeWidget { background: transparent; border: none; color: #EAEAEA; outline: none; }

            QTreeWidget::item {
                padding: 8px 14px;
                border-radius: 0px;
                background: transparent;
            }

            QTreeWidget::item:hover {
                background: rgba(255,255,255,0.06);
            }

            QTreeWidget::item:selected,
            QTreeWidget::item:selected:hover {
                background-color: #2A2A2A;
                color: #EAEAEA;
                outline: none;
                border: none;
            }
            """
        )

        self.setSizeGripEnabled(False)
        self.setMinimumSize(0, 0)
        self.setMaximumSize(16777215, 16777215)
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)

        self._update_compare_btn_state()

    def selected_sensors(self) -> list[str]:
        try:
            return [str(it.text(0)) for it in (self.tree.selectedItems() or [])]
        except Exception:
            return []

    def _emit_compare(self) -> None:
        try:
            cb = getattr(self, "_on_compare", None)
            if callable(cb):
                cb(self.selected_sensors())
        except Exception:
            pass

    def _update_compare_btn_state(self) -> None:
        try:
            any_selected = bool(self.tree.selectedItems())
            self.compare_btn.setEnabled(any_selected)
        except Exception:
            pass

    def closeEvent(self, event):
        try:
            if callable(getattr(self, "_on_close", None)):
                self._on_close()
        except Exception:
            pass
        super().closeEvent(event)
