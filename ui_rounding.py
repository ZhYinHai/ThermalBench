# ui_rounding.py
from __future__ import annotations

from PySide6.QtCore import QObject, QEvent
from PySide6.QtGui import QPainterPath, QRegion
from PySide6.QtWidgets import QWidget


class _RoundingFilter(QObject):
    """
    Fail-safe rounding filter:
    - Never assumes attributes exist.
    - Uses parent() as the widget source.
    - Never raises inside eventFilter (prevents crashing the app).
    """

    def __init__(self, w: QWidget, radius: int):
        super().__init__(w)  # parented to the widget
        self._radius = int(radius)

    def set_radius(self, radius: int) -> None:
        self._radius = int(radius)
        self._apply()

    def _widget(self) -> QWidget | None:
        p = self.parent()
        return p if isinstance(p, QWidget) else None

    def _apply(self) -> None:
        w = self._widget()
        if w is None:
            return

        r = max(0, int(getattr(self, "_radius", 0)))
        if r <= 0:
            w.clearMask()
            return

        rect = w.rect()
        if rect.isNull():
            return

        path = QPainterPath()
        path.addRoundedRect(rect, r, r)
        w.setMask(QRegion(path.toFillPolygon().toPolygon()))

    def eventFilter(self, obj, event):
        try:
            w = self._widget()
            if w is not None and obj is w and event.type() in (QEvent.Resize, QEvent.Show):
                self._apply()
        except Exception:
            # Never crash the app because of rounding
            return False
        return super().eventFilter(obj, event)


def apply_rounded_corners(w: QWidget, radius: int) -> None:
    """
    Mask-based rounded corners (no translucent background).
    Safe to call repeatedly; will update radius.
    """
    if not isinstance(w, QWidget):
        return

    filt = getattr(w, "_rounding_filter", None)
    if isinstance(filt, _RoundingFilter):
        filt.set_radius(radius)
        return

    filt = _RoundingFilter(w, radius)
    w._rounding_filter = filt  # type: ignore[attr-defined]
    w.installEventFilter(filt)
