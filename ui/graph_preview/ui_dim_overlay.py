# ui_dim_overlay.py
"""Semi-transparent overlay widget for dimming the application window."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QWidget


class DimOverlay(QWidget):
    """Semi-transparent overlay to dim the application while a popup is open."""

    def __init__(self, parent: QWidget, on_click):
        super().__init__(parent)
        self._on_click = on_click
        self.setObjectName("LegendStatsDimOverlay")

        # Make stylesheet background paint reliably
        self.setAttribute(Qt.WA_StyledBackground, True)

        # Slightly darker shade, matching your reference screenshots.
        # Adjust alpha (last number) if you want more/less dim:
        # 0..255 (115 ~= 45%).
        self.setStyleSheet(
            "#LegendStatsDimOverlay { background-color: rgba(0, 0, 0, 115); }"
        )
        self.hide()

    def mousePressEvent(self, ev):
        if ev.button() == Qt.LeftButton:
            try:
                self._on_click()
            except Exception:
                pass
        ev.accept()
