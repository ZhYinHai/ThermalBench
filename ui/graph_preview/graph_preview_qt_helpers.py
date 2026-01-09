"""Qt-related helpers extracted from `ui/graph_preview.py`.

These functions are written to preserve behavior exactly.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QEvent, QObject, Qt, QTimer
from PySide6.QtWidgets import QApplication

from .ui_dim_overlay import DimOverlay


def ensure_dim_overlay(gp: Any) -> None:
    top = gp.parent.window() if hasattr(gp.parent, "window") else gp.parent
    if top is None:
        return

    if gp._dim_overlay is None or gp._dim_overlay.parentWidget() is not top:
        try:
            if gp._dim_overlay is not None:
                gp._dim_overlay.deleteLater()
        except Exception:
            pass
        gp._dim_overlay = DimOverlay(top, on_click=gp._close_legend_popup)

    try:
        gp._dim_overlay.setGeometry(top.rect())
    except Exception:
        pass


def set_dimmed(gp: Any, on: bool) -> None:
    try:
        if on:
            ensure_dim_overlay(gp)
            if gp._dim_overlay is not None:
                gp._dim_overlay.show()
                gp._dim_overlay.raise_()
        else:
            if gp._dim_overlay is not None:
                gp._dim_overlay.hide()
    except Exception:
        pass


def on_legend_popup_closed(gp: Any) -> None:
    set_dimmed(gp, False)
    gp._legend_popup = None


def install_outside_click_closer(gp: Any) -> None:
    if gp._global_click_filter is not None:
        return

    class _Filter(QObject):
        def __init__(self, owner):
            super().__init__()
            self.gp = owner

        def eventFilter(self, obj, event):
            try:
                if event.type() in (QEvent.MouseButtonPress, QEvent.MouseButtonDblClick):
                    dlg = self.gp._legend_popup
                    if dlg is None or not dlg.isVisible():
                        return False

                    try:
                        gpnt = event.globalPosition().toPoint()
                    except Exception:
                        gpnt = event.globalPos()

                    if not dlg.frameGeometry().contains(gpnt):
                        self.gp._close_legend_popup()
            except Exception:
                pass
            return False

    gp._global_click_filter = _Filter(gp)
    app = QApplication.instance()
    if app is not None:
        app.installEventFilter(gp._global_click_filter)


def bind_app_focus(gp: Any) -> None:
    if gp._app_focus_bound:
        return
    gp._app_focus_bound = True
    app = QApplication.instance()
    if app is None:
        return
    try:
        app.applicationStateChanged.connect(gp._on_app_state_changed)
    except Exception:
        pass


def on_app_state_changed(gp: Any, state) -> None:
    try:
        if state == Qt.ApplicationActive:
            gp._app_is_active = True
            gp._preview_invalidate_interaction_cache()
            QTimer.singleShot(0, gp._preview_relayout_and_redraw)

            if gp._legend_popup is not None and gp._legend_popup.isVisible():
                gp._legend_popup.raise_()
                gp._legend_popup.activateWindow()
        else:
            gp._app_is_active = False
            gp._hide_preview_hover(hard=True)
            gp._preview_invalidate_interaction_cache()
            # IMPORTANT: do NOT close the legend popup on focus loss
    except Exception:
        pass


def handle_preview_canvas_event_filter(gp: Any, obj, event) -> None:
    try:
        if obj is getattr(gp, "_preview_canvas", None):
            et = event.type()

            if et in (QEvent.Resize, QEvent.Show):
                gp._preview_invalidate_interaction_cache()
                QTimer.singleShot(0, gp._preview_relayout_and_redraw)

                # keep overlay synced while popup is open
                if gp._legend_popup is not None and gp._legend_popup.isVisible():
                    gp._ensure_dim_overlay()

            elif et == QEvent.Hide:
                gp._hide_preview_hover(hard=True)
                gp._preview_invalidate_interaction_cache()
                gp._close_legend_popup()

            elif et == QEvent.Leave:
                gp._hide_preview_hover(hard=True)
    except Exception:
        pass
