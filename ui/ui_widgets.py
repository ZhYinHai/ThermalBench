from __future__ import annotations

from PySide6.QtWidgets import QComboBox
from typing import Optional

from .ui_theme import style_combobox_popup
from core.resources import resource_path


class CustomComboBox(QComboBox):
    """QComboBox subclass that centralizes the arrow and dropdown popup styling.

    Use `update_style(mode)` to (re)apply the popup styling for "light", "dark", or "device".
    """

    def __init__(self, parent=None, *, mode: Optional[str] = None, arrow_path: str | None = None):
        super().__init__(parent)
        if arrow_path is None:
            arrow_path = resource_path("resources", "icons", "down_triangle.svg")
        self._arrow_path = arrow_path
        # Leave actual styling to the caller; allow optional immediate application
        if mode is not None:
            self.update_style(mode)

    def update_style(self, mode: str) -> None:
        """Apply styling for the given theme mode."""
        style_combobox_popup(self, mode, arrow_path=self._arrow_path)
