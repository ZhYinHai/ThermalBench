# app.py
import sys
from PySide6.QtWidgets import QApplication

from core.settings_store import get_settings_path, load_json
from ui.ui_theme import apply_theme
from ui.main_window import MainWindow


def main() -> int:
    app = QApplication(sys.argv)

    # Apply saved theme early (no dark->light flash)
    settings = load_json(get_settings_path())
    apply_theme(app, settings.get("theme", "dark"))

    w = MainWindow()
    w.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
