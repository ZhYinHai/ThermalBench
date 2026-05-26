import os
import sys


def _enable_windows_dpi_awareness() -> None:
    """Enable the highest DPI-awareness level available on Windows."""
    if not sys.platform.startswith("win"):
        return

    try:
        import ctypes

        user32 = ctypes.windll.user32
        # Windows 10+ per-monitor v2 (best rendering and sizing on mixed-DPI setups).
        if hasattr(user32, "SetProcessDpiAwarenessContext"):
            DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = ctypes.c_void_p(-4)
            if user32.SetProcessDpiAwarenessContext(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2):
                return
    except Exception:
        pass

    try:
        import ctypes

        shcore = ctypes.windll.shcore
        PROCESS_PER_MONITOR_DPI_AWARE = 2
        shcore.SetProcessDpiAwareness(PROCESS_PER_MONITOR_DPI_AWARE)
        return
    except Exception:
        pass

    try:
        import ctypes

        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass


def _configure_qt_high_dpi() -> None:
    """Configure Qt high-DPI behavior before QApplication is created."""
    try:
        from PySide6.QtCore import Qt
        from PySide6.QtGui import QGuiApplication

        QGuiApplication.setHighDpiScaleFactorRoundingPolicy(
            Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
        )
    except Exception:
        pass


def _suppress_qt_info_logs() -> None:
    """Install a Qt message handler that only prints Critical and Fatal messages."""
    from PySide6.QtCore import QtMsgType, qInstallMessageHandler

    def _handler(msg_type: QtMsgType, _context, message: str) -> None:
        if msg_type in (QtMsgType.QtCriticalMsg, QtMsgType.QtFatalMsg):
            print(message, file=sys.stderr)

    qInstallMessageHandler(_handler)


def _suppress_ffmpeg_verbose() -> None:
    """Set FFmpeg's internal log level to ERROR to silence informational output."""
    import ctypes
    import ctypes.util

    AV_LOG_ERROR = 16

    lib_name = ctypes.util.find_library("avutil")
    if lib_name:
        try:
            avutil = ctypes.cdll.LoadLibrary(lib_name)
            avutil.av_log_set_level(AV_LOG_ERROR)
            return
        except Exception:
            pass

    # Fallback: search PySide6's own directory for the bundled avutil DLL.
    try:
        import importlib.util as ilu
        import pathlib

        spec = ilu.find_spec("PySide6")
        if spec and spec.submodule_search_locations:
            pyside_dir = pathlib.Path(list(spec.submodule_search_locations)[0])
            for pattern in ("avutil*.dll", "libavutil*.so*", "libavutil*.dylib"):
                for dll in pyside_dir.glob(pattern):
                    try:
                        avutil = ctypes.cdll.LoadLibrary(str(dll))
                        avutil.av_log_set_level(AV_LOG_ERROR)
                        return
                    except Exception:
                        continue
    except Exception:
        pass


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
    _enable_windows_dpi_awareness()
    _configure_qt_high_dpi()
    _configure_tls_ca_bundle()
    _suppress_ffmpeg_verbose()
    _suppress_qt_info_logs()

    from PySide6.QtWidgets import QApplication

    from core.settings_store import get_settings_path, load_json
    from ui.widgets.ui_theme import apply_theme
    from ui.main_window import MainWindow

    app = QApplication(sys.argv)

    # Apply saved theme early (no dark->light flash)
    settings = load_json(get_settings_path())
    apply_theme(app, settings.get("theme", "device"))

    w = MainWindow()
    w.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
