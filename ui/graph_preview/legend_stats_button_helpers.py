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

        # Qt mouse coordinates are in device-independent pixels (DIP) on high-DPI
        # displays, while Matplotlib renderer bboxes are typically in *device*
        # pixels. Convert DIP -> device px using the canvas scale.
        try:
            w_log = float(canvas.width())
            h_log = float(canvas.height())
        except Exception:
            w_log = 0.0
            h_log = 0.0

        scale_x = 1.0
        scale_y = 1.0
        w_dev = None
        h_dev = None
        try:
            # Matplotlib canvas reports renderer size in device pixels.
            w_dev, h_dev = canvas.get_width_height()
            w_dev = float(w_dev)
            h_dev = float(h_dev)
            if w_log > 0.0 and h_log > 0.0:
                scale_x = w_dev / w_log
                scale_y = h_dev / h_log
        except Exception:
            try:
                dpr = float(canvas.devicePixelRatioF())
            except Exception:
                try:
                    dpr = float(canvas.devicePixelRatio())
                except Exception:
                    dpr = 1.0
            scale_x = 1.0 if not (dpr and dpr > 0.0) else dpr
            scale_y = scale_x
            try:
                h_dev = float(h_log) * float(scale_y)
            except Exception:
                h_dev = None

        # Matplotlib renderer coords: origin bottom-left.
        if h_dev is None:
            h_dev = float(h_log) * float(scale_y)

        dx = float(qt_x) * float(scale_x)
        dy = float(h_dev) - (float(qt_y) * float(scale_y))
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
