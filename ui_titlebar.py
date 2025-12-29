from PySide6.QtCore import Qt
from PySide6.QtWidgets import QWidget, QHBoxLayout, QLabel, QToolButton, QSizePolicy


class TitleBar(QWidget):
    def __init__(self, parent: QWidget, title: str):
        super().__init__(parent)
        self.setObjectName("TitleBar")
        self.setFixedHeight(42)

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

        layout.addWidget(self.title_label)
        layout.addWidget(self.btn_min)
        layout.addWidget(self.btn_max)
        layout.addWidget(self.btn_close)

        self.btn_min.clicked.connect(self.window().showMinimized)
        self.btn_max.clicked.connect(self._toggle_max_restore)
        self.btn_close.clicked.connect(self.window().close)

    def _toggle_max_restore(self):
        w = self.window()
        if w.isMaximized():
            w.showNormal()
        else:
            w.showMaximized()

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._toggle_max_restore()
        super().mouseDoubleClickEvent(event)
