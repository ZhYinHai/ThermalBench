import os
import sys


def _configure_tls_ca_bundle() -> None:
    """Ensure Requests can find a CA bundle in frozen builds.

    In some PyInstaller layouts, certifi's bundle path discovery can break unless the
    CA bundle is explicitly bundled and/or pointed to via env vars.
    """

    # Only set if the user hasn't overridden it already.
    if os.environ.get("REQUESTS_CA_BUNDLE") or os.environ.get("SSL_CERT_FILE"):
        return

    try:
        import certifi

        ca_path = certifi.where()
        if ca_path and os.path.exists(ca_path):
            os.environ.setdefault("SSL_CERT_FILE", ca_path)
            os.environ.setdefault("REQUESTS_CA_BUNDLE", ca_path)
    except Exception:
        # Best-effort; if this fails, Requests may still work via OS defaults.
        pass


def main() -> int:
    _configure_tls_ca_bundle()

    from PySide6.QtWidgets import QApplication

    from core.settings_store import get_settings_path, load_json
    from ui.widgets.ui_theme import apply_theme
    from ui.main_window import MainWindow

    app = QApplication(sys.argv)

    # Apply saved theme early (no dark->light flash)
    settings = load_json(get_settings_path())
    apply_theme(app, settings.get("theme", "dark"))

    w = MainWindow()
    w.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
