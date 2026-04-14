from __future__ import annotations

from typing import Dict

from PySide6.QtCore import QEvent, QObject, QPropertyAnimation, QTimer, Qt, QEasingCurve
from PySide6.QtWidgets import QApplication, QAbstractScrollArea, QGraphicsOpacityEffect, QScrollBar


def _scrollbar_stylesheet(mode: str, orientation: Qt.Orientation) -> str:
    is_light = str(mode or "dark").strip().lower() == "light"
    if orientation == Qt.Vertical:
        size_rule = "width: 12px;"
        margin_rule = "margin: 4px 2px 4px 2px;"
        handle_rule = "min-height: 36px;"
        subcontrol = "vertical"
    else:
        size_rule = "height: 12px;"
        margin_rule = "margin: 2px 4px 2px 4px;"
        handle_rule = "min-width: 36px;"
        subcontrol = "horizontal"

    groove = "rgba(0, 0, 0, 0.06)" if is_light else "rgba(255, 255, 255, 0.05)"
    handle = "rgba(0, 0, 0, 0.24)" if is_light else "rgba(255, 255, 255, 0.22)"
    hover = "rgba(0, 0, 0, 0.34)" if is_light else "rgba(255, 255, 255, 0.34)"
    pressed = "rgba(0, 0, 0, 0.44)" if is_light else "rgba(255, 255, 255, 0.44)"

    return f"""
        QScrollBar {{
            background: transparent;
            border: none;
            {size_rule}
            {margin_rule}
        }}
        QScrollBar::groove:{subcontrol} {{
            background: {groove};
            border-radius: 6px;
        }}
        QScrollBar::handle:{subcontrol} {{
            background: {handle};
            border-radius: 6px;
            {handle_rule}
        }}
        QScrollBar::handle:{subcontrol}:hover {{
            background: {hover};
        }}
        QScrollBar::handle:{subcontrol}:pressed {{
            background: {pressed};
        }}
        QScrollBar::add-line:{subcontrol},
        QScrollBar::sub-line:{subcontrol},
        QScrollBar::add-page:{subcontrol},
        QScrollBar::sub-page:{subcontrol} {{
            background: transparent;
            border: none;
            width: 0px;
            height: 0px;
        }}
    """


class _OverlayScrollAreaController(QObject):
    def __init__(self, area: QAbstractScrollArea, mode: str):
        super().__init__(area)
        self._area = area
        self._viewport = area.viewport()
        self._mode = "light" if mode == "light" else "dark"

        self._vbar = QScrollBar(Qt.Vertical, self._viewport)
        self._hbar = QScrollBar(Qt.Horizontal, self._viewport)

        self._vbar.setObjectName("TbOverlayVScrollBar")
        self._hbar.setObjectName("TbOverlayHScrollBar")
        self._vbar.hide()
        self._hbar.hide()
        self._vbar.raise_()
        self._hbar.raise_()

        self._bar_effects = {
            self._vbar: QGraphicsOpacityEffect(self._vbar),
            self._hbar: QGraphicsOpacityEffect(self._hbar),
        }
        self._fade_animations: Dict[QScrollBar, QPropertyAnimation] = {}
        self._fade_hide_pending = {self._vbar: False, self._hbar: False}
        for bar, effect in self._bar_effects.items():
            effect.setOpacity(0.0)
            bar.setGraphicsEffect(effect)
            anim = QPropertyAnimation(effect, b"opacity", self)
            anim.setDuration(2000)
            anim.setEasingCurve(QEasingCurve.OutCubic)
            anim.finished.connect(lambda b=bar: self._finish_fade(b))
            self._fade_animations[bar] = anim

        self._area.installEventFilter(self)
        self._viewport.installEventFilter(self)
        self._vbar.installEventFilter(self)
        self._hbar.installEventFilter(self)

        self._viewport.setMouseTracking(True)

        internal_vbar = self._area.verticalScrollBar()
        internal_hbar = self._area.horizontalScrollBar()
        internal_vbar.rangeChanged.connect(self._sync_all)
        internal_vbar.valueChanged.connect(self._sync_vertical)
        internal_hbar.rangeChanged.connect(self._sync_all)
        internal_hbar.valueChanged.connect(self._sync_horizontal)
        self._vbar.valueChanged.connect(self._on_overlay_vertical_changed)
        self._hbar.valueChanged.connect(self._on_overlay_horizontal_changed)

        self.set_theme_mode(self._mode)
        self._sync_all()

    def set_theme_mode(self, mode: str) -> None:
        self._mode = "light" if str(mode or "dark").strip().lower() == "light" else "dark"
        self._vbar.setStyleSheet(_scrollbar_stylesheet(self._mode, Qt.Vertical))
        self._hbar.setStyleSheet(_scrollbar_stylesheet(self._mode, Qt.Horizontal))
        self._sync_all()

    def eventFilter(self, watched, event):
        try:
            et = event.type()
            if watched in (self._area, self._viewport, self._vbar, self._hbar):
                if et in (QEvent.Resize, QEvent.Show, QEvent.LayoutRequest, QEvent.PolishRequest):
                    self._sync_all()
                elif et in (QEvent.Enter, QEvent.HoverEnter, QEvent.MouseMove, QEvent.Wheel):
                    self._refresh_visibility(force_visible=True)
                elif et in (QEvent.Leave, QEvent.HoverLeave, QEvent.Hide):
                    QTimer.singleShot(0, self._refresh_visibility)
        except Exception:
            pass
        return super().eventFilter(watched, event)

    def _on_overlay_vertical_changed(self, value: int) -> None:
        try:
            internal = self._area.verticalScrollBar()
            if internal.value() != value:
                internal.setValue(value)
        except Exception:
            pass

    def _on_overlay_horizontal_changed(self, value: int) -> None:
        try:
            internal = self._area.horizontalScrollBar()
            if internal.value() != value:
                internal.setValue(value)
        except Exception:
            pass

    def _sync_vertical(self, *_args) -> None:
        self._sync_bar(self._area.verticalScrollBar(), self._vbar)
        self._position_bars()
        self._refresh_visibility()

    def _sync_horizontal(self, *_args) -> None:
        self._sync_bar(self._area.horizontalScrollBar(), self._hbar)
        self._position_bars()
        self._refresh_visibility()

    def _sync_all(self, *_args) -> None:
        try:
            self._area.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            self._area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        except Exception:
            pass

        self._sync_bar(self._area.verticalScrollBar(), self._vbar)
        self._sync_bar(self._area.horizontalScrollBar(), self._hbar)
        self._position_bars()
        self._refresh_visibility()

    def _sync_bar(self, internal: QScrollBar, overlay: QScrollBar) -> None:
        try:
            overlay.blockSignals(True)
            overlay.setRange(internal.minimum(), internal.maximum())
            overlay.setPageStep(internal.pageStep())
            overlay.setSingleStep(internal.singleStep())
            overlay.setValue(internal.value())
            overlay.blockSignals(False)
        except Exception:
            pass

    def _refresh_visibility(self, force_visible: bool = False) -> None:
        try:
            hovered = force_visible or self._area.underMouse() or self._viewport.underMouse() or self._vbar.underMouse() or self._hbar.underMouse()
            show_vertical = bool(hovered and self._vbar.maximum() > self._vbar.minimum())
            show_horizontal = bool(hovered and self._hbar.maximum() > self._hbar.minimum())
            self._set_bar_visible(self._vbar, show_vertical)
            self._set_bar_visible(self._hbar, show_horizontal)
        except Exception:
            pass

    def _set_bar_visible(self, bar: QScrollBar, visible: bool) -> None:
        try:
            effect = self._bar_effects[bar]
            anim = self._fade_animations[bar]

            if visible:
                self._fade_hide_pending[bar] = False
                anim.stop()
                effect.setOpacity(1.0)
                if not bar.isVisible():
                    bar.show()
                bar.raise_()
                return

            if not bar.isVisible():
                effect.setOpacity(0.0)
                return

            if self._fade_hide_pending[bar]:
                return

            self._fade_hide_pending[bar] = True
            anim.stop()
            anim.setStartValue(effect.opacity())
            anim.setEndValue(0.0)
            anim.start()
        except Exception:
            pass

    def _finish_fade(self, bar: QScrollBar) -> None:
        try:
            if self._fade_hide_pending.get(bar):
                self._fade_hide_pending[bar] = False
                effect = self._bar_effects[bar]
                effect.setOpacity(0.0)
                bar.hide()
        except Exception:
            pass

    def _position_bars(self) -> None:
        try:
            viewport = self._viewport
            width = max(0, viewport.width())
            height = max(0, viewport.height())
            v_extent = 12 if self._vbar.maximum() > self._vbar.minimum() else 0
            h_extent = 12 if self._hbar.maximum() > self._hbar.minimum() else 0

            v_x = max(0, width - 12 - 2)
            v_height = max(0, height - 8 - h_extent)
            self._vbar.setGeometry(v_x, 4, 12, v_height)

            h_y = max(0, height - 12 - 2)
            h_width = max(0, width - 8 - v_extent)
            self._hbar.setGeometry(4, h_y, h_width, 12)
        except Exception:
            pass


class OverlayScrollbarManager(QObject):
    def __init__(self, app: QApplication, mode: str):
        super().__init__(app)
        self._app = app
        self._mode = "light" if mode == "light" else "dark"
        self._controllers: Dict[int, _OverlayScrollAreaController] = {}
        self._app.installEventFilter(self)
        self._scan_existing_widgets()

    def set_theme_mode(self, mode: str) -> None:
        self._mode = "light" if str(mode or "dark").strip().lower() == "light" else "dark"
        self._scan_existing_widgets()
        for controller in list(self._controllers.values()):
            try:
                controller.set_theme_mode(self._mode)
            except Exception:
                pass

    def eventFilter(self, watched, event):
        try:
            if isinstance(watched, QAbstractScrollArea) and event.type() in (QEvent.Show, QEvent.Polish, QEvent.PolishRequest):
                self._ensure_controller(watched)
        except Exception:
            pass
        return super().eventFilter(watched, event)

    def _scan_existing_widgets(self) -> None:
        try:
            for widget in self._app.allWidgets():
                if isinstance(widget, QAbstractScrollArea):
                    self._ensure_controller(widget)
        except Exception:
            pass

    def _ensure_controller(self, area: QAbstractScrollArea) -> None:
        try:
            if area is None:
                return
            if bool(area.property("_tb_overlay_scrollbars_disabled")):
                return

            key = id(area)
            controller = self._controllers.get(key)
            if controller is None:
                controller = _OverlayScrollAreaController(area, self._mode)
                self._controllers[key] = controller
                try:
                    area.destroyed.connect(lambda *_args, k=key: self._controllers.pop(k, None))
                except Exception:
                    pass
            else:
                controller.set_theme_mode(self._mode)
        except Exception:
            pass


_OVERLAY_MANAGERS: Dict[int, OverlayScrollbarManager] = {}


def install_overlay_scrollbars(app: QApplication, mode: str) -> OverlayScrollbarManager:
    key = id(app)
    manager = _OVERLAY_MANAGERS.get(key)
    if manager is None:
        manager = OverlayScrollbarManager(app, mode)
        _OVERLAY_MANAGERS[key] = manager
    else:
        manager.set_theme_mode(mode)
    return manager