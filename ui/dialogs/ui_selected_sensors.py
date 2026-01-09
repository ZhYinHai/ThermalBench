# ui_selected_sensors.py
from __future__ import annotations

from collections import defaultdict

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QWidget,
    QVBoxLayout,
    QTreeWidget,
    QTreeWidgetItem,
    QDialogButtonBox,
)

from ..widgets.ui_titlebar import TitleBar
from .ui_sensor_picker import SPD_MAX_TOKEN


class SelectedSensorsDialog(QDialog):
    def __init__(
        self,
        parent: QWidget,
        selected_tokens: list[str],
        group_map: dict[str, str],
        has_spd: bool,
    ):
        super().__init__(parent)

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

        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setUniformRowHeights(True)
        self.tree.itemClicked.connect(self._toggle_tree_item)
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

        self.resize(900, 600)

    def showEvent(self, event):
        super().showEvent(event)
        p = self.parentWidget()
        if p:
            pg = p.geometry()
            sg = self.geometry()
            self.move(pg.center().x() - sg.width() // 2, pg.center().y() - sg.height() // 2)

    def _toggle_tree_item(self, item: QTreeWidgetItem, column: int):
        """Toggle expand/collapse state of tree item on single click."""
        if item.childCount() > 0:
            if item.isExpanded():
                self.tree.collapseItem(item)
            else:
                self.tree.expandItem(item)
