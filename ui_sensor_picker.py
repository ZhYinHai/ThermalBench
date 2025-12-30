# ui_sensor_picker.py
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLineEdit,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QDialogButtonBox,
)

from ui_titlebar import TitleBar
from ui_rounding import apply_rounded_corners

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
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setUniformRowHeights(True)
        self.tree.itemDoubleClicked.connect(self._on_double_click)
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

        self.resize(900, 600)

    def showEvent(self, event):
        super().showEvent(event)
        p = self.parentWidget()
        if p:
            pg = p.geometry()
            sg = self.geometry()
            self.move(pg.center().x() - sg.width() // 2, pg.center().y() - sg.height() // 2)

    def _set_all_checked(self, checked: bool):
        state = Qt.Checked if checked else Qt.Unchecked
        for it in self.leaf_items:
            if not it.isHidden():
                it.setCheckState(0, state)

    def _on_double_click(self, item: QTreeWidgetItem, column: int):
        if item.childCount() > 0:
            item.setExpanded(not item.isExpanded())
            return
        cs = item.checkState(0)
        item.setCheckState(0, Qt.Unchecked if cs == Qt.Checked else Qt.Checked)

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
