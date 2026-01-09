"""Helpers for positioning/focusing the Legend & stats popup.

These functions are extracted from GraphPreview to keep behavior identical.
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtWidgets import QDialog, QWidget


def center_popup_on_app(parent: QWidget, dlg: QDialog) -> None:
    """Center popup in the application window (not the monitor)."""
    try:
        w = parent.window() if hasattr(parent, "window") else parent
        if w is None:
            return

        dlg.adjustSize()
        dw, dh = dlg.width(), dlg.height()

        geo = w.frameGeometry()
        x = geo.x() + (geo.width() - dw) // 2
        y = geo.y() + (geo.height() - dh) // 2
        dlg.move(int(x), int(y))
    except Exception:
        pass


def raise_center_and_focus(
    *,
    parent: QWidget,
    dlg: QDialog,
    dim_overlay: Optional[QWidget],
) -> None:
    """Mimic GraphPreview's bring-to-front sequence."""
    try:
        if dim_overlay is not None:
            dim_overlay.raise_()
    except Exception:
        pass

    try:
        dlg.raise_()
    except Exception:
        pass

    try:
        center_popup_on_app(parent, dlg)
    except Exception:
        pass

    try:
        dlg.activateWindow()
        dlg.setFocus()
    except Exception:
        pass
