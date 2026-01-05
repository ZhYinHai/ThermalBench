# ui_theme.py
from PySide6.QtWidgets import QApplication, QComboBox
from PySide6.QtGui import QPalette, QColor
from pathlib import Path as _Path
from core.resources import resource_path

def apply_theme(app: QApplication, mode: str) -> None:
    mode = (mode or "dark").strip().lower()
    if mode not in ("dark", "light", "device"):
        mode = "dark"

    app.setStyle("Fusion")

    if mode == "device":
        # Clear any custom stylesheet so palette reflects OS defaults
        app.setStyleSheet("")
        pal = app.palette()
        bg = pal.color(QPalette.Window)
        # lightness(): 0 (black) -> 255 (white)
        mode = "dark" if bg.lightness() < 128 else "light"

    if mode == "dark":
        app.setStyleSheet(
            """
            QWidget { background-color: #121212; color: #EAEAEA; font-size: 12px; }

            QLineEdit, QTextEdit, QPlainTextEdit, QSpinBox, QComboBox {
                background-color: #1E1E1E;
                border: 1px solid #2A2A2A;
                border-radius: 8px;
                padding: 6px 8px;
                selection-background-color: #3A3A3A;
            }
            QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus, QSpinBox:focus, QComboBox:focus {
                border: 1px solid #4A90E2;
            }

            QPushButton {
                background-color: #242424;
                border: 1px solid #2F2F2F;
                border-radius: 10px;
                padding: 8px 12px;
            }
            QPushButton:hover { background-color: #2E2E2E; }
            QPushButton:pressed { background-color: #1C1C1C; }
            QPushButton:disabled {
                color: #777;
                background-color: #1A1A1A;
                border: 1px solid #222;
            }

            QWidget#TitleBar {
                background-color: #171717;
                border-bottom: 1px solid #232323;
            }
            QLabel#TitleText { color: #EAEAEA; font-size: 12px; }
            QToolButton#WinBtn {
                background-color: transparent;
                border: none;
                border-radius: 8px;
                padding: 6px 10px;
            }
            QToolButton#WinBtn:hover { background-color: #2A2A2A; }
            QToolButton#WinClose:hover { background-color: #C42B1C; }

            QLabel#UnitLabel { color: #BDBDBD; padding-left: 1px; padding-right: 8px; }

            QLabel#LiveTimer {
                font-size: 14px;
                font-weight: 600;
                color: #EAEAEA;
                padding: 6px 10px;
                border: 1px solid #2A2A2A;
                border-radius: 10px;
                background-color: #171717;
            }

            QTreeWidget {
                background-color: #1A1A1A;
                border: 1px solid #2A2A2A;
                border-radius: 10px;
            }

            QLabel#StatusDot { font-size: 14px; }
            QLabel#StatusDot[state="ok"] { color: #2ECC71; }
            QLabel#StatusDot[state="bad"] { color: #E74C3C; }

            /* Tooltips */
            QToolTip {
                background-color: #222222;
                color: #EAEAEA;
                border: 1px solid #2A2A2A;
                padding: 3px;
                border-radius: 6px;
            }

            /* ---------- ComboBox popup (dropdown list) ---------- */
            QComboBox QAbstractItemView {
                border: 0px;
                outline: 0px;
                background-color: #1A1A1A;
                color: #EAEAEA;
                selection-background-color: #2A2A2A;
            }

            QComboBox QAbstractItemView::item {
                padding: 6px 10px;
            }

            /* Kill the popup frame (top/bottom lines) */
            QComboBoxPrivateContainer {
                border: 0px;
                background: transparent;
            }

            QComboBoxPrivateContainer QFrame {
                border: 0px;
                background: transparent;
                padding: 0px;
                margin: 0px;
            }

            """
        )
        # Re-apply custom combobox styling after the app stylesheet to cover CustomComboBox instances
        try:
            update_all_custom_comboboxes("dark")
        except Exception:
            pass
    else:
        app.setStyleSheet(
            """
            QWidget { background-color: #F6F6F6; color: #1A1A1A; font-size: 12px; }

            QLineEdit, QTextEdit, QPlainTextEdit, QSpinBox, QComboBox {
                background-color: #FFFFFF;
                border: 1px solid #D0D0D0;
                border-radius: 8px;
                padding: 6px 8px;
                selection-background-color: #CFE4FF;
            }
            QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus, QSpinBox:focus, QComboBox:focus {
                border: 1px solid #2B7DE9;
            }

            QPushButton {
                background-color: #FFFFFF;
                border: 1px solid #D0D0D0;
                border-radius: 10px;
                padding: 8px 12px;
            }
            QPushButton:hover { background-color: #F0F0F0; }
            QPushButton:pressed { background-color: #E6E6E6; }
            QPushButton:disabled {
                color: #888;
                background-color: #F3F3F3;
                border: 1px solid #E0E0E0;
            }

            QWidget#TitleBar {
                background-color: #FFFFFF;
                border-bottom: 1px solid #E0E0E0;
            }
            QLabel#TitleText { color: #1A1A1A; font-size: 12px; }
            QToolButton#WinBtn {
                background-color: transparent;
                border: none;
                border-radius: 8px;
                padding: 6px 10px;
            }
            QToolButton#WinBtn:hover { background-color: #EFEFEF; }
            QToolButton#WinClose:hover { background-color: #FFDFDF; }

            QLabel#UnitLabel { color: #555; padding-left: 1px; padding-right: 8px; }

            QLabel#LiveTimer {
                font-size: 14px;
                font-weight: 600;
                color: #1A1A1A;
                padding: 6px 10px;
                border: 1px solid #D0D0D0;
                border-radius: 10px;
                background-color: #FFFFFF;
            }

            QTreeWidget {
                background-color: #FFFFFF;
                border: 1px solid #D0D0D0;
                border-radius: 10px;
            }

            QLabel#StatusDot { font-size: 14px; }
            QLabel#StatusDot[state="ok"] { color: #178A43; }
            QLabel#StatusDot[state="bad"] { color: #C7342A; }

            /* Tooltips */
            QToolTip {
                background-color: #FFFFFF;
                color: #1A1A1A;
                border: 1px solid #D0D0D0;
                padding: 6px;
                border-radius: 6px;
            }

            /* ---------- ComboBox popup (dropdown list) ---------- */
            QComboBox QAbstractItemView {
                border: 0px;
                outline: 0px;
                background-color: #FFFFFF;
                color: #1A1A1A;
                selection-background-color: #CFE4FF;
                selection-color: #1A1A1A;
            }

            QComboBox QAbstractItemView::item {
                padding: 6px 10px;
            }

            /* Kill the popup frame (top/bottom lines) */
            QComboBoxPrivateContainer {
                border: 0px;
                background: transparent;
            }

            QComboBoxPrivateContainer QFrame {
                border: 0px;
                background: transparent;
                padding: 0px;
                margin: 0px;
            }

            """
        )
        # Re-apply custom combobox styling after the app stylesheet to cover CustomComboBox instances
        try:
            update_all_custom_comboboxes("light")
        except Exception:
            pass


def style_combobox_popup(combo: QComboBox, mode: str, arrow_path: str | None = None) -> None:
    """Apply arrow icon and popup colors to a QComboBox so its dropdown matches the requested theme.
    `mode` may be "light", "dark", or "device" (device resolves from app palette).
    """
    mode = (mode or "dark").strip().lower()
    if mode == "device":
        try:
            from PySide6.QtWidgets import QApplication
            pal = QApplication.instance().palette()
            mode = "dark" if pal.color(QPalette.Window).lightness() < 128 else "light"
        except Exception:
            mode = "dark"

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

    # Resolve arrow asset path if not provided
    arrow = arrow_path if arrow_path is not None else resource_path("resources", "icons", "down_triangle.svg")

    # Normalize to an absolute forward-slash path suitable for QSS (avoid file: scheme on Windows)
    try:
        p = _Path(arrow)
        if p.exists():
            arrow = str(p.resolve()).replace('\\', '/')
        else:
            arrow = str(arrow).replace('\\', '/')
    except Exception:
        arrow = str(arrow).replace('\\', '/')
    # Apply primary stylesheet (arrow + popup view styling)
    combo.setStyleSheet(f"""
    QComboBox {{ padding-right: 22px; }}
    QComboBox::drop-down {{ border: none; background: transparent; width: 22px; }}
    QComboBox::down-arrow {{ image: url("{arrow}"); width: 10px; height: 6px; }}

    QComboBox QAbstractItemView {{
        border: 0px;
        outline: 0px;
        background-color: {popup_bg};
        color: {popup_fg};
        selection-background-color: {popup_sel_bg};
        selection-color: {popup_sel_fg};
    }}

    QComboBox QAbstractItemView::item {{ padding: 6px 10px; }}

    QComboBoxPrivateContainer {{ border: 0px; background: transparent; }}
    QComboBoxPrivateContainer QFrame {{ border: 0px; background: transparent; padding: 0px; margin: 0px; }}
    """)

    # Also set styling and palette on the view itself (some platforms prefer the view's palette)
    try:
        view = combo.view()
        view.setStyleSheet(f"background-color: {popup_bg}; color: {popup_fg};")

        pal = view.palette()
        pal.setColor(QPalette.Base, QColor(popup_bg))
        pal.setColor(QPalette.Text, QColor(popup_fg))
        pal.setColor(QPalette.Highlight, QColor(popup_sel_bg))
        pal.setColor(QPalette.HighlightedText, QColor(popup_sel_fg))
        view.setPalette(pal)

        try:
            popup_win = view.window()
            popup_win.setStyleSheet(f"background-color: {popup_bg}; color: {popup_fg};")
        except Exception:
            pass
    except Exception:
        pass


def update_all_custom_comboboxes(mode: str) -> None:
    """Find all CustomComboBox instances in top-level windows and reapply their styles.
    This imports CustomComboBox lazily to avoid circular imports at module import time.
    """
    try:
        from .ui_widgets import CustomComboBox
        app = QApplication.instance()
        if app is None:
            return
        for top in app.topLevelWidgets():
            try:
                for cb in top.findChildren(CustomComboBox):
                    try:
                        cb.update_style(mode)
                    except Exception:
                        pass
            except Exception:
                pass
    except Exception:
        pass


def apply_dark_theme(app: QApplication) -> None:
    apply_theme(app, "dark")
