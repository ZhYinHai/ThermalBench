# ui_titlebar.py
from PySide6.QtCore import Qt, QPoint
from PySide6.QtWidgets import QWidget, QHBoxLayout, QLabel, QToolButton, QSizePolicy


class TitleBar(QWidget):
    def __init__(
        self,
        parent: QWidget,
        title: str,
        *,
        show_title: bool = True,
        show_buttons: bool = True,
        draggable: bool = True,
    ):
        super().__init__(parent)
        self.setObjectName("TitleBar")
        self.setFixedHeight(42)

        self._draggable = draggable
        self._drag_active = False
        self._drag_offset = QPoint()

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 6, 8, 6)
        layout.setSpacing(6)

        self.title_label = QLabel(title)
        self.title_label.setObjectName("TitleText")
        self.title_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)

        self.btn_min = QToolButton()
        self.btn_min.setObjectName("WinBtn")
        self.btn_min.setText("—")

        self.btn_max = QToolButton()
        self.btn_max.setObjectName("WinBtn")
        self.btn_max.setText("□")

        self.btn_close = QToolButton()
        self.btn_close.setObjectName("WinClose")
        self.btn_close.setText("✕")

        # Layout: title fills, buttons at right
        layout.addWidget(self.title_label, 1)
        layout.addWidget(self.btn_min)
        layout.addWidget(self.btn_max)
        layout.addWidget(self.btn_close)

        # Default connections (main window)
        self.btn_min.clicked.connect(self.window().showMinimized)
        self.btn_max.clicked.connect(self._toggle_max_restore)
        self.btn_close.clicked.connect(self.window().close)

        # Configure visibility for “drag-only” dialogs
        self.title_label.setVisible(show_title)

        self.btn_min.setVisible(show_buttons)
        self.btn_max.setVisible(show_buttons)
        self.btn_close.setVisible(show_buttons)

        # If buttons hidden, don't let them take space
        if not show_buttons:
            self.btn_min.setEnabled(False)
            self.btn_max.setEnabled(False)
            self.btn_close.setEnabled(False)

    def _toggle_max_restore(self):
        w = self.window()
        if w.isMaximized():
            w.showNormal()
        else:
            w.showMaximized()

    # --- Dragging support (works for frameless windows) ---
    def mousePressEvent(self, event):
        if self._draggable and event.button() == Qt.LeftButton:
            # Start drag (only when clicking empty titlebar, not a child widget)
            if self.childAt(event.pos()) in (self.btn_min, self.btn_max, self.btn_close):
                super().mousePressEvent(event)
                return
            self._drag_active = True
            self._drag_offset = event.globalPosition().toPoint() - self.window().frameGeometry().topLeft()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._draggable and self._drag_active and (event.buttons() & Qt.LeftButton):
            w = self.window()
            if not w.isMaximized():
                w.move(event.globalPosition().toPoint() - self._drag_offset)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._drag_active = False
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event):
        # Keep existing behavior for main window; in drag-only dialogs this is harmless
        if event.button() == Qt.LeftButton:
            # only toggle if buttons are visible (i.e., likely main window)
            if self.btn_max.isVisible():
                self._toggle_max_restore()
        super().mouseDoubleClickEvent(event)
