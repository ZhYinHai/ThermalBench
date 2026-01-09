from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QFontMetrics, QWheelEvent, QKeyEvent
from PySide6.QtWidgets import QSpinBox, QSizePolicy


class KeyboardOnlySpinBox(QSpinBox):
    """Numbers only. No arrows, no mouse wheel changes, no up/down stepping."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setButtonSymbols(QSpinBox.NoButtons)

    def stepBy(self, steps: int) -> None:
        return

    def wheelEvent(self, event: QWheelEvent) -> None:
        event.ignore()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() in (Qt.Key_Up, Qt.Key_Down, Qt.Key_PageUp, Qt.Key_PageDown):
            event.ignore()
            return
        super().keyPressEvent(event)


def make_time_spin(min_chars: int, max_value: int, initial: int) -> QSpinBox:
    sp = KeyboardOnlySpinBox()
    sp.setRange(0, max_value)
    sp.setValue(initial)
    sp.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
    sp.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Fixed)

    def update_width():
        txt = str(sp.value())
        shown_len = max(min_chars, len(txt))
        fm = QFontMetrics(sp.font())
        w = fm.horizontalAdvance("0" * shown_len) + 30
        sp.setFixedWidth(w)

    sp.valueChanged.connect(lambda *_: update_width())
    update_width()
    return sp
