# ui_sensor_picker.py
from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtWidgets import (
    QDialog,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLineEdit,
    QPushButton,
    QTreeWidgetItem,
    QDialogButtonBox,
    QAbstractItemView,
)

from ..widgets.ui_titlebar import TitleBar
from ..widgets.ui_rounding import apply_rounded_corners
from ..graph_preview.ui_dim_overlay import DimOverlay
from ..widgets.ui_full_row_tree import FullRowHoverTree

SPD_MAX_TOKEN = "__SPD_MAX__"


class SensorPickerDialog(QDialog):
    def __init__(
        self,
        parent: QWidget,
        csv_unique_leafs: list[str],
        has_spd: bool,
        group_map: dict[str, str],
        preselected: set[str],
    ):
        super().__init__(parent)

        self._dim_overlay: DimOverlay | None = None
        self._overlay_filter: QObject | None = None

        self.corner_radius = 12  # <-- adjust for this window
        apply_rounded_corners(self, self.corner_radius)

        # Frameless window (we draw our own title bar area)
        self.setWindowTitle("Select sensors to monitor")
        self.setModal(True)
        self.setWindowFlag(Qt.FramelessWindowHint, True)
        self.setWindowFlag(Qt.Window, True)

        # ---------- Layout: outer (titlebar) + inner (content) ----------
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # Drag-only title bar: no text, no buttons
        self.titlebar = TitleBar(self, "", show_title=False, show_buttons=False, draggable=True)
        self.titlebar.setFixedHeight(28)  # optional: slimmer bar for this dialog
        outer.addWidget(self.titlebar)

        root = QVBoxLayout()
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(10)
        outer.addLayout(root)

        # ---------- Top controls ----------
        top = QHBoxLayout()
        top.setSpacing(10)

        self.search = QLineEdit()
        self.search.setPlaceholderText("Search sensors… (e.g. CPU, GPU, VRM, Temp)")
        self.search.textChanged.connect(self._apply_filter)

        self.btn_all = QPushButton("Select all")
        self.btn_none = QPushButton("Deselect all")
        self.btn_all.clicked.connect(lambda: self._set_all_checked(True))
        self.btn_none.clicked.connect(lambda: self._set_all_checked(False))

        top.addWidget(self.search, 1)
        top.addWidget(self.btn_all)
        top.addWidget(self.btn_none)
        root.addLayout(top)

        # ---------- Tree ----------
        self.tree = FullRowHoverTree(hover_rgba=(255, 255, 255, 15), selected_rgba=(255, 255, 255, 18))
        self.tree.setHeaderHidden(True)
        self.tree.setUniformRowHeights(True)
        self.tree.setSelectionMode(QAbstractItemView.NoSelection)
        self.tree.setFocusPolicy(Qt.NoFocus)
        self.tree.setEditTriggers(QAbstractItemView.NoEditTriggers)
        # Legend&Stats style: one-press toggles checkbox anywhere on the row.
        # We handle clicks via an eventFilter so the user doesn't need to click the checkbox itself.
        self.tree.viewport().installEventFilter(self)
        self.tree.installEventFilter(self)
        root.addWidget(self.tree, 1)

        self.group_items: dict[str, QTreeWidgetItem] = {}
        self.leaf_items: list[QTreeWidgetItem] = []

        def ensure_group(gname: str) -> QTreeWidgetItem:
            if gname in self.group_items:
                return self.group_items[gname]

            gi = QTreeWidgetItem([gname])
            gi.setFirstColumnSpanned(True)
            gi.setFlags(
                gi.flags()
                | Qt.ItemFlag.ItemIsAutoTristate
                | Qt.ItemFlag.ItemIsUserCheckable
            )
            gi.setCheckState(0, Qt.Unchecked)

            # Bold group names (leaves remain normal)
            f = gi.font(0)
            f.setBold(True)
            gi.setFont(0, f)

            self.tree.addTopLevelItem(gi)
            self.group_items[gname] = gi
            return gi

        # ---------- Optional SPD Max helper ----------
        if has_spd:
            g = ensure_group("Memory / SPD")
            checked = (SPD_MAX_TOKEN in preselected)
            it = QTreeWidgetItem(g, ["SPD Hub (Max of DIMMs)"])
            it.setFlags(it.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            it.setCheckState(0, Qt.Checked if checked else Qt.Unchecked)
            it.setData(0, Qt.UserRole, SPD_MAX_TOKEN)
            self.leaf_items.append(it)

        # ---------- Insert sensors ----------
        for uniq_leaf in csv_unique_leafs:
            grp = group_map.get(uniq_leaf, "Other")
            g = ensure_group(grp)

            # Display formatting: "X #1" -> "X  (#1)"
            display = uniq_leaf.replace(" #", "  (#") + (")" if " #" in uniq_leaf else "")
            it = QTreeWidgetItem(g, [display])
            it.setFlags(it.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            it.setCheckState(0, Qt.Checked if (uniq_leaf in preselected) else Qt.Unchecked)
            it.setData(0, Qt.UserRole, uniq_leaf)  # store exact unique CSV token
            self.leaf_items.append(it)

        # Start collapsed for readability
        self.tree.collapseAll()

        # ---------- OK / Cancel ----------
        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        root.addWidget(btns)

        # Match Legend & Stats popup look
        self.setObjectName("SensorPickerDialog")
        self.setStyleSheet(
            """
            QDialog#SensorPickerDialog { background: #1A1A1A; border: 1px solid #2A2A2A; border-radius: 10px; }
            QWidget#TitleBar { background: #151515; }

            QLineEdit {
                background: #121212;
                color: #EAEAEA;
                border: 1px solid #2A2A2A;
                border-radius: 8px;
                padding: 6px 10px;
            }

            QPushButton {
                background: #2A2A2A;
                color: #EAEAEA;
                border: 1px solid #3A3A3A;
                border-radius: 8px;
                padding: 6px 12px;
            }
            QPushButton:hover { background: #333333; border-color: #4A4A4A; }
            QPushButton:pressed { background: #252525; }

            QTreeWidget { background: transparent; border: none; color: #EAEAEA; outline: none; }
            QTreeWidget::item { padding: 6px 6px; background: transparent; }
            /* Full row hover is painted by FullRowHoverTree (covers left gutter too) */
            QTreeWidget::item:hover { background: transparent; }
            QTreeWidget::item:selected, QTreeWidget::item:selected:hover { background: transparent; }

            /* Prevent branch-area selection tint ("blue bar") */
            QTreeView::branch:selected { background: transparent; }
            QTreeView::branch:hover { background: transparent; }

            QDialogButtonBox QPushButton { min-width: 88px; }
            """
        )

        self.resize(900, 600)

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
                def __init__(self, dlg: "SensorPickerDialog"):
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

    def _set_all_checked(self, checked: bool):
        state = Qt.Checked if checked else Qt.Unchecked
        for it in self.leaf_items:
            if not it.isHidden():
                it.setCheckState(0, state)

    def eventFilter(self, obj, event):
        # One-press behavior matching Legend & Stats:
        # - leaf rows: click anywhere (except checkbox gutter) toggles check
        # - group rows: click anywhere (except checkbox gutter) expands/collapses
        if obj is self.tree.viewport() and event.type() == QEvent.MouseButtonPress and event.button() == Qt.LeftButton:
            try:
                try:
                    pos = event.position().toPoint()
                except Exception:
                    pos = event.pos()

                item = self.tree.itemAt(pos)
                if item is None:
                    return False

                rect = self.tree.visualItemRect(item)
                in_checkbox_gutter = (pos.x() - rect.x()) < 24

                # Let Qt handle checkbox clicks (group tristate + leaf checkbox)
                if in_checkbox_gutter:
                    return False

                if item.childCount() > 0:
                    item.setExpanded(not item.isExpanded())
                    return True

                # Leaf: toggle check
                cs = item.checkState(0)
                item.setCheckState(0, Qt.Unchecked if cs == Qt.Checked else Qt.Checked)
                return True
            except Exception:
                return False

        return super().eventFilter(obj, event)

    def _apply_filter(self):
        q = self.search.text().strip().lower()

        for g in self.group_items.values():
            any_visible = False

            for i in range(g.childCount()):
                c = g.child(i)
                txt = c.text(0).lower()
                match = (not q) or (q in txt)
                c.setHidden(not match)
                if match:
                    any_visible = True

            g.setHidden(not any_visible)

            # Auto-expand visible groups when searching; collapse otherwise
            if q and any_visible:
                self.tree.expandItem(g)
            else:
                self.tree.collapseItem(g)

    def selected_tokens(self) -> list[str]:
        out: list[str] = []
        for it in self.leaf_items:
            if it.checkState(0) == Qt.Checked:
                tok = it.data(0, Qt.UserRole)
                if tok:
                    out.append(str(tok))
        return out
