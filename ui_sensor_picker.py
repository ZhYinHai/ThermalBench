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
        self.setWindowTitle("Select sensors to monitor")
        self.setModal(True)

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(10)

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
            self.tree.addTopLevelItem(gi)
            self.group_items[gname] = gi
            return gi

        # Optional SPD Max helper
        if has_spd:
            g = ensure_group("Memory / SPD")
            checked = (SPD_MAX_TOKEN in preselected)
            it = QTreeWidgetItem(g, ["SPD Hub (Max of DIMMs)"])
            it.setFlags(it.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            it.setCheckState(0, Qt.Checked if checked else Qt.Unchecked)
            it.setData(0, Qt.UserRole, SPD_MAX_TOKEN)
            self.leaf_items.append(it)

        # Insert sensors
        for uniq_leaf in csv_unique_leafs:
            grp = group_map.get(uniq_leaf, "Other")
            g = ensure_group(grp)

            it = QTreeWidgetItem(g, [uniq_leaf.replace(" #", "  (#") + (")" if " #" in uniq_leaf else "")])
            it.setFlags(it.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            it.setCheckState(0, Qt.Checked if (uniq_leaf in preselected) else Qt.Unchecked)
            it.setData(0, Qt.UserRole, uniq_leaf)  # store the REAL token (unique column)
            self.leaf_items.append(it)

        self.tree.expandAll()

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

    def selected_tokens(self) -> list[str]:
        out: list[str] = []
        for it in self.leaf_items:
            if it.checkState(0) == Qt.Checked:
                tok = it.data(0, Qt.UserRole)
                if tok:
                    out.append(str(tok))
        return out
