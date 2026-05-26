"""Update-available dialog styled to match the ThermalBench theme."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ..widgets.ui_rounding import apply_rounded_corners
from ..widgets.ui_theme import resolve_effective_theme_mode
from ..widgets.ui_titlebar import TitleBar


class UpdateAvailableDialog(QDialog):
    """Frameless, theme-aware dialog shown when a newer GitHub release is detected."""

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        current_version: str,
        new_version: str,
        release_notes: str = "",
        theme_mode: str = "device",
    ) -> None:
        super().__init__(parent)

        effective = resolve_effective_theme_mode(theme_mode, QApplication.instance())
        self._is_dark = effective == "dark"

        self.setModal(True)
        self.setWindowTitle("Update Available")
        self.setWindowFlag(Qt.FramelessWindowHint, True)
        self.setWindowFlag(Qt.Window, True)

        self.corner_radius = 12
        apply_rounded_corners(self, self.corner_radius)

        # ── Outer border shell ────────────────────────────────────────────────
        # A 1 px border is painted via a wrapper widget so it is visible over
        # whatever content is behind the frameless window.
        outer_shell = QVBoxLayout(self)
        outer_shell.setContentsMargins(1, 1, 1, 1)  # border thickness
        outer_shell.setSpacing(0)

        inner_widget = QWidget()
        inner_widget.setObjectName("UpdateDialogInner")
        inner_widget.setStyleSheet(
            f"QWidget#UpdateDialogInner {{"
            f" background-color: {self._bg_color()};"
            f" border-radius: {self.corner_radius - 1}px;"
            f"}}"
        )
        outer_shell.addWidget(inner_widget)

        # Paint the outer border colour on self directly
        self.setStyleSheet(
            f"UpdateAvailableDialog {{"
            f" background-color: {self._dialog_border_color()};"
            f" border-radius: {self.corner_radius}px;"
            f"}}"
        )

        # ── Layout ────────────────────────────────────────────────────────────
        outer = QVBoxLayout(inner_widget)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        tb = TitleBar(self, "Update Available", show_title=True, show_buttons=False, draggable=True)
        tb.setFixedHeight(42)
        outer.addWidget(tb)

        body = QVBoxLayout()
        body.setContentsMargins(20, 16, 20, 20)
        body.setSpacing(14)
        outer.addLayout(body)

        # ── Version info ──────────────────────────────────────────────────────
        title_lbl = QLabel(f"ThermalBench <b>{new_version}</b> is available")
        title_lbl.setStyleSheet(f"font-size: 13px; color: {self._text_primary()};")

        sub_lbl = QLabel(f"You are on version <b>{current_version}</b>")
        sub_lbl.setStyleSheet(f"font-size: 11px; color: {self._text_secondary()};")

        body.addWidget(title_lbl)
        body.addWidget(sub_lbl)

        # ── Divider ───────────────────────────────────────────────────────────
        divider = QWidget()
        divider.setFixedHeight(1)
        divider.setStyleSheet(f"background-color: {self._border_color()};")
        body.addWidget(divider)

        # ── Release notes (truncated) ─────────────────────────────────────────
        if release_notes and release_notes.strip():
            notes_text = self._truncate_notes(release_notes, max_chars=240)
            notes_lbl = QLabel(notes_text)
            notes_lbl.setWordWrap(True)
            notes_lbl.setTextInteractionFlags(Qt.TextSelectableByMouse)
            notes_lbl.setStyleSheet(
                f"font-size: 11px; color: {self._text_secondary()};"
                f" background-color: {self._surface_color()};"
                f" border: 1px solid {self._border_color()};"
                f" border-radius: 8px; padding: 8px 10px;"
            )
            notes_lbl.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
            body.addWidget(notes_lbl)

        # ── Buttons ───────────────────────────────────────────────────────────
        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)
        btn_row.addStretch(1)

        self._dismiss_btn = QPushButton("Not now")
        self._dismiss_btn.setFixedHeight(34)
        self._dismiss_btn.setMinimumWidth(90)
        self._dismiss_btn.setStyleSheet(self._secondary_btn_style())
        self._dismiss_btn.clicked.connect(self.reject)

        self._open_btn = QPushButton("Open Settings")
        self._open_btn.setFixedHeight(34)
        self._open_btn.setMinimumWidth(120)
        self._open_btn.setStyleSheet(self._primary_btn_style())
        self._open_btn.setDefault(True)
        self._open_btn.clicked.connect(self.accept)

        btn_row.addWidget(self._dismiss_btn)
        btn_row.addWidget(self._open_btn)
        body.addLayout(btn_row)

        self.setFixedWidth(420)
        self.adjustSize()

    # ── Theme helpers ─────────────────────────────────────────────────────────

    def _bg_color(self) -> str:
        return "#121212" if self._is_dark else "#F6F6F6"

    def _surface_color(self) -> str:
        return "#1E1E1E" if self._is_dark else "#FFFFFF"

    def _border_color(self) -> str:
        return "#2A2A2A" if self._is_dark else "#D8D8D8"

    def _dialog_border_color(self) -> str:
        """Accent-tinted border that makes the dialog pop against the main window."""
        return "#3A5A8A" if self._is_dark else "#9BBDE0"

    def _text_primary(self) -> str:
        return "#EAEAEA" if self._is_dark else "#1A1A1A"

    def _text_secondary(self) -> str:
        return "#9A9A9A" if self._is_dark else "#555555"

    def _accent_color(self) -> str:
        return "#4A90E2" if self._is_dark else "#2B7DE9"

    def _accent_hover(self) -> str:
        return "#5A9EEA" if self._is_dark else "#1A6ED4"

    def _accent_pressed(self) -> str:
        return "#3A7BC8" if self._is_dark else "#1060BE"

    def _primary_btn_style(self) -> str:
        accent = self._accent_color()
        hover = self._accent_hover()
        pressed = self._accent_pressed()
        return (
            f"QPushButton {{"
            f" background-color: {accent};"
            f" color: #FFFFFF;"
            f" border: none;"
            f" border-radius: 8px;"
            f" padding: 6px 14px;"
            f" font-size: 12px;"
            f"}}"
            f"QPushButton:hover {{ background-color: {hover}; }}"
            f"QPushButton:pressed {{ background-color: {pressed}; }}"
        )

    def _secondary_btn_style(self) -> str:
        bg = self._surface_color()
        border = self._border_color()
        text = self._text_primary()
        hover_bg = "#2E2E2E" if self._is_dark else "#EBEBEB"
        pressed_bg = "#1C1C1C" if self._is_dark else "#DEDEDE"
        return (
            f"QPushButton {{"
            f" background-color: {bg};"
            f" color: {text};"
            f" border: 1px solid {border};"
            f" border-radius: 8px;"
            f" padding: 6px 14px;"
            f" font-size: 12px;"
            f"}}"
            f"QPushButton:hover {{ background-color: {hover_bg}; }}"
            f"QPushButton:pressed {{ background-color: {pressed_bg}; }}"
        )

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _truncate_notes(notes: str, max_chars: int) -> str:
        """Return at most *max_chars* characters of the release notes."""
        notes = notes.strip()
        if len(notes) <= max_chars:
            return notes
        return notes[:max_chars].rstrip() + "…"
