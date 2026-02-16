# ui_settings_dialog.py
from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QFileDialog,
    QDialogButtonBox,
    QCheckBox,
)
from PySide6.QtWidgets import QComboBox
from PySide6.QtGui import QPalette, QColor
from ..widgets.ui_widgets import CustomComboBox
from core.resources import resource_path


from ..widgets.ui_titlebar import TitleBar
from ..widgets.ui_rounding import apply_rounded_corners


class SettingsDialog(QDialog):
    def __init__(
        self,
        parent: QWidget,
        *,
        furmark_exe: str,
        prime_exe: str,
        theme: str,
        update_callback=None,
    ):
        super().__init__(parent)

        self._update_callback = update_callback

        self.corner_radius = 12
        apply_rounded_corners(self, self.corner_radius)

        self.setModal(True)
        self.setWindowTitle("Settings")
        self.setWindowFlag(Qt.FramelessWindowHint, True)
        self.setWindowFlag(Qt.Window, True)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        tb = TitleBar(self, "Settings", show_title=True, show_buttons=False, draggable=True)
        tb.setFixedHeight(42)
        outer.addWidget(tb)

        root = QVBoxLayout()
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(12)
        outer.addLayout(root)

        # --- FurMark path ---
        root.addWidget(QLabel("FurMark executable (.exe)"))
        fur_row = QHBoxLayout()
        self.fur_edit = QLineEdit(furmark_exe or "")
        self.fur_edit.setPlaceholderText(r"C:\Path\To\furmark.exe")
        btn_fur = QPushButton("Browse…")
        btn_fur.clicked.connect(lambda: self._pick_exe(self.fur_edit))
        fur_row.addWidget(self.fur_edit, 1)
        fur_row.addWidget(btn_fur)
        root.addLayout(fur_row)

        # --- Prime95 path ---
        root.addWidget(QLabel("Prime95 executable (.exe)"))
        pr_row = QHBoxLayout()
        self.prime_edit = QLineEdit(prime_exe or "")
        self.prime_edit.setPlaceholderText(r"C:\Path\To\prime95.exe")
        btn_pr = QPushButton("Browse…")
        btn_pr.clicked.connect(lambda: self._pick_exe(self.prime_edit))
        pr_row.addWidget(self.prime_edit, 1)
        pr_row.addWidget(btn_pr)
        root.addLayout(pr_row)

        # --- Appearance (Mode dropdown) ---
        row = QHBoxLayout()
        lab = QLabel("Mode")

        self.mode_combo = CustomComboBox(mode=theme)
        self.mode_combo.addItems(["Light", "Dark", "Device"])

        arrow_path = resource_path("resources", "icons", "down_triangle.svg")
        self._mode_combo_arrow = arrow_path

        # Apply initial popup style based on input theme and update it when selection changes
        self._apply_mode_combo_theme(theme)
        self.mode_combo.currentTextChanged.connect(self._apply_mode_combo_theme)

        cur = (theme or "dark").strip().lower()
        if cur == "light":
            self.mode_combo.setCurrentText("Light")
        elif cur == "device":
            self.mode_combo.setCurrentText("Device")
        else:
            self.mode_combo.setCurrentText("Dark")

        row.addWidget(lab)
        row.addWidget(self.mode_combo)
        row.addStretch(1)
        row.setSpacing(10)
        root.addLayout(row)


        # --- Updates ---
        if self._update_callback is not None:
            upd_row = QHBoxLayout()
            self.check_updates_btn = QPushButton("Check for updates…")
            self.check_updates_btn.clicked.connect(self._on_check_updates)
            upd_row.addWidget(self.check_updates_btn)

            self.update_status_label = QLabel("")
            self.update_status_label.setWordWrap(True)
            self.update_status_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
            self.update_status_label.setStyleSheet("color: #8A8A8A;")
            upd_row.addWidget(self.update_status_label, 1)
            upd_row.setSpacing(10)
            root.addLayout(upd_row)


        # --- Buttons ---
        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        root.addWidget(btns)

        self.resize(640, 260)

    def _on_check_updates(self) -> None:
        try:
            cb = getattr(self, "_update_callback", None)
            if cb is None:
                return
            cb(
                set_status=self._set_update_status,
                set_button_text=self.check_updates_btn.setText,
                set_button_enabled=self.check_updates_btn.setEnabled,
            )
        except Exception as e:
            self._set_update_status(f"Error: {e}", "error")

    def _set_update_status(self, text: str, level: str = "info") -> None:
        """Set the inline update status message next to the update button."""
        try:
            lab = getattr(self, "update_status_label", None)
            if lab is None:
                return

            lab.setText(text or "")

            lvl = (level or "info").strip().lower()
            if lvl in {"ok", "success"}:
                lab.setStyleSheet("color: #2E7D32;")
            elif lvl in {"warn", "warning"}:
                lab.setStyleSheet("color: #9A6A00;")
            elif lvl in {"error", "bad"}:
                lab.setStyleSheet("color: #B00020;")
            else:
                lab.setStyleSheet("color: #8A8A8A;")
        except Exception:
            pass

    def _resolve_theme(self, t: str) -> str:
        """Resolve a theme value to 'light' or 'dark'.
        If 'device' is provided, inspect the application palette to pick light/dark.
        """
        t = (t or "dark").strip().lower()
        if t == "device":
            try:
                from PySide6.QtWidgets import QApplication
                pal = QApplication.instance().palette()
                bg = pal.color(QPalette.Window)
                return "dark" if bg.lightness() < 128 else "light"
            except Exception:
                return "dark"
        return "light" if t == "light" else "dark"

    def _apply_mode_combo_theme(self, t: str | None) -> None:
        """Apply the mode combo's popup stylesheet for the resolved theme.
        This is called on init and whenever the combo selection changes.
        """
        mode = self._resolve_theme(t if isinstance(t, str) else (t or "dark"))
        if mode == "light":
            popup_bg = "#FFFFFF"
            popup_fg = "#1A1A1A"
            popup_sel_bg = "#CFE4FF"
            popup_sel_fg = "#1A1A1A"
        else:
            popup_bg = "#1A1A1A"
            popup_fg = "#EAEAEA"
            popup_sel_bg = "#2A2A2A"
            popup_sel_fg = "#EAEAEA"

        arrow = getattr(self, "_mode_combo_arrow", None) or resource_path("resources", "icons", "down_triangle.svg")
        try:
            p = _Path(arrow)
            if p.exists():
                arrow = str(p.resolve()).replace('\\', '/')
            else:
                arrow = str(arrow).replace('\\', '/')
        except Exception:
            arrow = str(arrow).replace('\\', '/')

        self.mode_combo.setStyleSheet(f"""
        QComboBox {{
            padding-right: 22px;      /* room for arrow */
        }}

        QComboBox::drop-down {{
            border: none;             /* removes the box */
            background: transparent;
            width: 22px;              /* clickable area */
        }}

        QComboBox::down-arrow {{
            image: url("{arrow}");
            width: 10px;
            height: 6px;
        }}

        QComboBox QAbstractItemView {{
            border: 0px;
            outline: 0px;
            background-color: {popup_bg};
            color: {popup_fg};
            selection-background-color: {popup_sel_bg};
            selection-color: {popup_sel_fg};
        }}

        QComboBox QAbstractItemView::item {{
            padding: 6px 10px;
        }}

        QComboBoxPrivateContainer {{
            border: 0px;
            background: transparent;
        }}
        QComboBoxPrivateContainer QFrame {{
            border: 0px;
            background: transparent;
            padding: 0px;
            margin: 0px;
        }}
        """)

        # Also apply styling directly to the underlying view and its palette. Some platforms
        # show the popup as a separate window and prefer the view's palette over stylesheets.
        try:
            view = self.mode_combo.view()
            # Ensure visual colors via stylesheet
            view.setStyleSheet(f"background-color: {popup_bg}; color: {popup_fg};")

            pal = view.palette()
            pal.setColor(QPalette.Base, QColor(popup_bg))
            pal.setColor(QPalette.Text, QColor(popup_fg))
            pal.setColor(QPalette.Highlight, QColor(popup_sel_bg))
            pal.setColor(QPalette.HighlightedText, QColor(popup_sel_fg))
            view.setPalette(pal)

            # Also try to style the popup window itself if available
            try:
                popup_win = view.window()
                popup_win.setStyleSheet(f"background-color: {popup_bg}; color: {popup_fg};")
            except Exception:
                pass
        except Exception:
            pass

    def _pick_exe(self, target: QLineEdit) -> None:
        start_dir = ""
        cur = target.text().strip()
        if cur and os.path.exists(cur):
            start_dir = str(Path(cur).parent)
        else:
            start_dir = str(Path.cwd())

        path, _ = QFileDialog.getOpenFileName(
            self,
            "Select executable",
            start_dir,
            "Executable (*.exe);;All Files (*.*)",
        )
        if path:
            target.setText(path)

    def furmark_exe(self) -> str:
        return self.fur_edit.text().strip()

    def prime_exe(self) -> str:
        return self.prime_edit.text().strip()

    def theme(self) -> str:
        t = self.mode_combo.currentText().strip().lower()
        return t if t in ("light", "dark", "device") else "dark"


    def showEvent(self, event):
        super().showEvent(event)
        p = self.parentWidget()
        if p:
            pg = p.geometry()
            sg = self.geometry()
            self.move(pg.center().x() - sg.width() // 2, pg.center().y() - sg.height() // 2)
