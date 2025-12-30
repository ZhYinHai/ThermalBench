# ui_theme.py
from PySide6.QtWidgets import QApplication


def apply_dark_theme(app: QApplication) -> None:
    app.setStyle("Fusion")
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

        /* Status indicator dots */
        QLabel#StatusDot {
            font-size: 14px;
            min-width: 14px;
            max-width: 14px;
            padding: 0px;
        }
        QLabel#StatusDot[state="ok"] { color: #2ECC71; }
        QLabel#StatusDot[state="bad"] { color: #E74C3C; }
        """
    )
