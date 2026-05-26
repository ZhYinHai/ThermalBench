# ui_compare_popup.py
"""Compare popup dialog for showing common sensors across selected results.

Uses the same visual style as the Legend & Stats popup.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Iterable, Optional, Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication,
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

from ui.widgets.ui_theme import resolve_effective_theme_mode


class ComparePopup(QDialog):
    def __init__(
        self,
        parent,
        *,
        title: str,
        sensors: Iterable[str],
        group_map: Optional[dict[str, str]] = None,
        run_labels: Optional[list[str]] = None,
        run_dates: Optional[list[str]] = None,
        on_close: Optional[Callable[[], None]] = None,
        on_compare: Optional[Callable[[list[str]], None]] = None,
        theme_mode: str = "device",
    ):
        super().__init__(parent)

        self._theme_mode = resolve_effective_theme_mode(theme_mode, QApplication.instance())
        self._theme_is_dark = self._theme_mode == "dark"
        self._theme = self._build_theme_palette()

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
        title_area.setStyleSheet(
            f"color:{self._theme['text']}; font-weight:600; font-size:13px;"
        )
        title_area.setMinimumWidth(0)
        title_area.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        close_btn = QToolButton()
        close_btn.setText("✕")
        close_btn.setCursor(Qt.PointingHandCursor)
        close_btn.clicked.connect(self.close)
        close_btn.setStyleSheet(
            f"""
            QToolButton {{ color:{self._theme['close_fg']}; background: transparent; border: none; padding: 4px 6px; }}
            QToolButton:hover {{ color:{self._theme['close_hover_fg']}; background: {self._theme['close_hover_bg']}; border-radius: 6px; }}
            """
        )

        title_row.addWidget(title_area)
        title_row.addStretch(1)
        title_row.addWidget(close_btn)
        root.addLayout(title_row)

        if run_labels:
            sep = QFrame()
            sep.setFrameShape(QFrame.HLine)
            sep.setFrameShadow(QFrame.Plain)
            sep.setFixedHeight(1)
            sep.setStyleSheet(
                f"background: {self._theme['dialog_border']}; border: none;"
            )
            root.addWidget(sep)

            runs_section = QVBoxLayout()
            runs_section.setContentsMargins(0, 2, 0, 2)
            runs_section.setSpacing(2)

            runs_header = QLabel("Comparing:")
            runs_header.setStyleSheet(
                f"color: {self._theme['close_fg']}; font-size: 11px; font-weight: 600;"
            )
            runs_section.addWidget(runs_header)

            dates = list(run_dates or [])
            for i, lbl in enumerate(run_labels):
                date_str = dates[i] if i < len(dates) else ""
                row = QHBoxLayout()
                row.setContentsMargins(0, 0, 0, 0)
                row.setSpacing(6)

                name_lbl = QLabel(f"\u2022  {lbl}")
                name_lbl.setStyleSheet(
                    f"color: {self._theme['text']}; font-size: 11px;"
                )
                row.addWidget(name_lbl, 1)

                if date_str:
                    date_lbl = QLabel(date_str)
                    date_lbl.setStyleSheet(
                        f"color: {self._theme['close_fg']}; font-size: 10px;"
                    )
                    date_lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
                    row.addWidget(date_lbl)

                runs_section.addLayout(row)
            root.addLayout(runs_section)

            sep2 = QFrame()
            sep2.setFrameShape(QFrame.HLine)
            sep2.setFrameShadow(QFrame.Plain)
            sep2.setFixedHeight(1)
            sep2.setStyleSheet(
                f"background: {self._theme['dialog_border']}; border: none;"
            )
            root.addWidget(sep2)

        self.tree = QTreeWidget()
        self.tree.setColumnCount(1)
        self.tree.setHeaderHidden(True)
        # Grouped view (device -> sensors) needs expand/collapse affordance.
        self.tree.setRootIsDecorated(True)
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

        def _display_name(tok: str) -> str:
            # Match other sensor dialogs: "X #1" -> "X  (#1)"
            try:
                t = str(tok)
                if " #" in t:
                    return t.replace(" #", "  (#") + ")"
                return t
            except Exception:
                return str(tok)

        gm = dict(group_map or {})

        grouped: dict[str, list[str]] = defaultdict(list)
        for s in sorted({str(x) for x in (sensors or []) if str(x).strip()}):
            grp = str(gm.get(s) or "").strip() or "Other"
            grouped[grp].append(s)

        # Insert groups + items
        for grp in sorted(grouped.keys(), key=lambda x: str(x).lower()):
            gitem = QTreeWidgetItem(self.tree, [str(grp)])
            gitem.setFirstColumnSpanned(True)
            # Group headers should not be selectable.
            gitem.setFlags(Qt.ItemIsEnabled)
            try:
                f = gitem.font(0)
                f.setBold(True)
                gitem.setFont(0, f)
            except Exception:
                pass

            for s in sorted(grouped.get(grp, []) or [], key=lambda x: str(x).lower()):
                it = QTreeWidgetItem(gitem, [_display_name(s)])
                it.setData(0, Qt.UserRole, str(s))
                it.setFlags(it.flags() | Qt.ItemIsSelectable | Qt.ItemIsEnabled)

        try:
            self.tree.expandAll()
        except Exception:
            pass

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

        self.select_all_btn = QPushButton("Select all")
        self.select_all_btn.setCursor(Qt.PointingHandCursor)
        try:
            self.select_all_btn.clicked.connect(self._select_all)
        except Exception:
            pass
        footer.addWidget(self.select_all_btn)

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
            f"""
            QDialog {{ background: {self._theme['dialog_bg']}; border: 1px solid {self._theme['dialog_border']}; border-radius: 10px; }}
            QLabel {{ background: transparent; color: {self._theme['text']}; }}

            QTreeWidget {{ background: transparent; border: none; color: {self._theme['text']}; outline: none; }}

            QTreeWidget::item {{
                padding: 8px 14px;
                border-radius: 0px;
                background: transparent;
            }}

            QTreeWidget::item:hover {{
                background: {self._theme['tree_hover_bg']};
            }}

            QTreeWidget::item:selected,
            QTreeWidget::item:selected:hover {{
                background-color: {self._theme['tree_selected_bg']};
                color: {self._theme['text']};
                outline: none;
                border: none;
            }}

            QPushButton {{
                background: {self._theme['button_bg']};
                color: {self._theme['text']};
                border: 1px solid {self._theme['button_border']};
                border-radius: 8px;
                padding: 6px 12px;
            }}
            QPushButton:hover {{ background: {self._theme['button_hover_bg']}; border-color: {self._theme['button_hover_border']}; }}
            QPushButton:pressed {{ background: {self._theme['button_pressed_bg']}; }}
            QPushButton:disabled {{ color: {self._theme['button_disabled_fg']}; border-color: {self._theme['button_disabled_border']}; background: {self._theme['button_disabled_bg']}; }}
            """
        )

        self.setSizeGripEnabled(False)
        self.setMinimumSize(0, 0)
        self.setMaximumSize(16777215, 16777215)
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)

        # Make the popup taller/wider by default so more sensors are visible.
        # Keep it resizable and allow content to drive larger sizes when needed.
        try:
            self.setMinimumSize(640, 480)
            self.resize(920, 680)
        except Exception:
            pass

        self._update_compare_btn_state()

    def _build_theme_palette(self) -> dict[str, str]:
        if self._theme_is_dark:
            return {
                "dialog_bg": "#1A1A1A",
                "dialog_border": "#2A2A2A",
                "text": "#EAEAEA",
                "close_fg": "#9A9A9A",
                "close_hover_fg": "#EAEAEA",
                "close_hover_bg": "rgba(255,255,255,0.06)",
                "tree_hover_bg": "rgba(255,255,255,0.06)",
                "tree_selected_bg": "#2A2A2A",
                "button_bg": "#2A2A2A",
                "button_border": "#3A3A3A",
                "button_hover_bg": "#333333",
                "button_hover_border": "#4A4A4A",
                "button_pressed_bg": "#252525",
                "button_disabled_fg": "#7A7A7A",
                "button_disabled_bg": "#202020",
                "button_disabled_border": "#2D2D2D",
            }
        return {
            "dialog_bg": "#F7F7F7",
            "dialog_border": "#D4D4D4",
            "text": "#111111",
            "close_fg": "#666666",
            "close_hover_fg": "#111111",
            "close_hover_bg": "rgba(0,0,0,0.06)",
            "tree_hover_bg": "rgba(0,0,0,0.05)",
            "tree_selected_bg": "#E6E6E6",
            "button_bg": "#FFFFFF",
            "button_border": "#CFCFCF",
            "button_hover_bg": "#F0F0F0",
            "button_hover_border": "#BDBDBD",
            "button_pressed_bg": "#E8E8E8",
            "button_disabled_fg": "#8E8E8E",
            "button_disabled_bg": "#F1F1F1",
            "button_disabled_border": "#D8D8D8",
        }

    def _select_all(self) -> None:
        try:
            self.tree.blockSignals(True)

            def _select_leaf_items(parent: QTreeWidgetItem) -> None:
                try:
                    for i in range(parent.childCount()):
                        ch = parent.child(i)
                        if ch is None:
                            continue
                        if ch.childCount() > 0:
                            _select_leaf_items(ch)
                            continue
                        tok = ch.data(0, Qt.UserRole)
                        if tok is None:
                            continue
                        ch.setSelected(True)
                except Exception:
                    pass

            for i in range(self.tree.topLevelItemCount()):
                top = self.tree.topLevelItem(i)
                if top is None:
                    continue
                _select_leaf_items(top)
        except Exception:
            pass
        finally:
            try:
                self.tree.blockSignals(False)
            except Exception:
                pass
            self._update_compare_btn_state()

    def selected_sensors(self) -> list[str]:
        try:
            out: list[str] = []
            for it in (self.tree.selectedItems() or []):
                # Only leaf sensor rows have a UserRole token.
                tok = it.data(0, Qt.UserRole)
                if tok is None:
                    continue
                s = str(tok).strip()
                if s:
                    out.append(s)
            return out
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
            self.compare_btn.setEnabled(bool(self.selected_sensors()))
        except Exception:
            pass

    def closeEvent(self, event):
        try:
            if callable(getattr(self, "_on_close", None)):
                self._on_close()
        except Exception:
            pass
        super().closeEvent(event)
