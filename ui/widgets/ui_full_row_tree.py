from __future__ import annotations

from PySide6.QtCore import QModelIndex, Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QStyle, QTreeWidget


class FullRowHoverTree(QTreeWidget):
    """QTreeWidget that paints hover/selection backgrounds across the full row.

    Qt's default hover/selection background often doesn't cover the branch/indent
    area, which makes the left gutter look like a different color.
    """

    def __init__(
        self,
        *args,
        hover_rgba: tuple[int, int, int, int] = (255, 255, 255, 15),
        selected_rgba: tuple[int, int, int, int] = (255, 255, 255, 18),
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        self._hover_color = QColor(*hover_rgba)
        self._selected_color = QColor(*selected_rgba)

        self._hover_index = QModelIndex()

        # Needed so Qt marks rows with State_MouseOver.
        self.setMouseTracking(True)
        try:
            self.viewport().setMouseTracking(True)
        except Exception:
            pass

        # Some styles/stylesheet combos don't reliably propagate State_MouseOver.
        # Keep hover stable by also tracking the index under the cursor.
        try:
            self.viewport().setAttribute(Qt.WA_Hover, True)
        except Exception:
            pass

    def set_full_row_colors(
        self,
        *,
        hover_rgba: tuple[int, int, int, int] | None = None,
        selected_rgba: tuple[int, int, int, int] | None = None,
    ) -> None:
        if hover_rgba is not None:
            self._hover_color = QColor(*hover_rgba)
        if selected_rgba is not None:
            self._selected_color = QColor(*selected_rgba)
        try:
            self.viewport().update()
        except Exception:
            pass

    def drawRow(self, painter, option, index) -> None:  # type: ignore[override]
        try:
            r = option.rect
            r.setX(0)
            r.setWidth(self.viewport().width())

            is_hover = bool(option.state & QStyle.State_MouseOver) or (self._hover_index.isValid() and index == self._hover_index)
            is_sel = bool(option.state & QStyle.State_Selected)

            if is_hover:
                painter.fillRect(r, self._hover_color)
            elif is_sel:
                painter.fillRect(r, self._selected_color)
        except Exception:
            pass

        return super().drawRow(painter, option, index)

    def mouseMoveEvent(self, event) -> None:  # type: ignore[override]
        try:
            try:
                pos = event.position().toPoint()
            except Exception:
                pos = event.pos()
            idx = self.indexAt(pos)
            if idx != self._hover_index:
                self._hover_index = idx
                try:
                    self.viewport().update()
                except Exception:
                    pass
        except Exception:
            pass
        return super().mouseMoveEvent(event)

    def leaveEvent(self, event) -> None:  # type: ignore[override]
        try:
            if self._hover_index.isValid():
                self._hover_index = QModelIndex()
                try:
                    self.viewport().update()
                except Exception:
                    pass
        except Exception:
            pass
        return super().leaveEvent(event)
