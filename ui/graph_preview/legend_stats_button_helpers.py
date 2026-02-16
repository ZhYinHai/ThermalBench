"""Helpers for the matplotlib-in-Qt "Legend & stats" button hit testing."""

from __future__ import annotations

from typing import Any, Optional


def is_over_button_bbox(*, canvas: Any, btn_bbox: Optional[Any], qt_x: int, qt_y: int) -> bool:
    """Return True if the Qt pixel position is over a matplotlib bbox.

    - qt_x/qt_y are Qt widget coordinates (origin top-left)
    - Matplotlib renderer bbox uses origin bottom-left
    """
    try:
        if canvas is None:
            return False
        if btn_bbox is None:
            return False

        h = int(canvas.height())
        dx = float(qt_x)
        dy = float(h - qt_y)
        return bool(btn_bbox.contains(dx, dy))
    except Exception:
        return False


def is_over_ls_button(*, canvas: Any, ls_btn_bbox: Optional[Any], qt_x: int, qt_y: int) -> bool:
    """Return True if the Qt pixel position is over the legend/stats button bbox.

    Preserves the original coordinate conversion used in GraphPreview:
    - qt_x/qt_y are Qt widget coordinates (origin top-left)
    - Matplotlib renderer bbox uses origin bottom-left
    """
    return is_over_button_bbox(canvas=canvas, btn_bbox=ls_btn_bbox, qt_x=qt_x, qt_y=qt_y)
