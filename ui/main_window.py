import json
import ctypes
import re
import html
from datetime import datetime
from ctypes import wintypes

_MONTHS_EN = (
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
)


def _month_key_to_label(month_key: str, include_year: bool = True) -> str:
    """Return an English month name from a 'YYYY-MM' key, locale-independently."""
    try:
        y, m = month_key.split("-", 1)
        name = _MONTHS_EN[int(m) - 1]
        return f"{name} {y}" if include_year else name
    except Exception:
        return str(month_key)

# ui/main_window.py
import os
import sys
from pathlib import Path

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt, QSize, QTimer, QObject, QThread, Signal, QEvent, QItemSelectionModel, QRectF
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtGui import QIcon, QPainter, QColor, QPalette, QPixmap
from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTextEdit,
    QFileDialog,
    QDialog,
    QSizePolicy,
    QAbstractItemView,
    QAbstractScrollArea,
    QTreeView,
    QSplitter,
    QScrollArea,
    QToolButton,
    QStackedWidget,
    QFrame,
    QMessageBox,
    QProgressDialog,
    QMenu,
    QListWidget,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QStyle,
    QGraphicsOpacityEffect,
)

from .widgets.ui_theme import apply_theme, resolve_effective_theme_mode, style_combobox_popup
from .widgets.ui_widgets import CustomComboBox
from .dialogs import HelpDialog, HelpPanel, SettingsDialog, UpdateAvailableDialog, Prime95SettingsDialog
from .widgets.ui_titlebar import TitleBar
from .widgets.ui_time_spin import make_time_spin
from .graph_preview import GraphPreview
from .sensor_manager import SensorManager
from .benchmark_controller import BenchmarkController
from .live_monitor_widget import LiveMonitorWidget
from .live_graph_widget import LiveGraphWidget

# keep if you have it
from .runs_proxy_model import RunsProxyModel

from ui.ntfy_notifier import NtfyNotifier

from ui.graph_preview.graph_plot_helpers import load_run_csv_dataframe
from ui.graph_preview.graph_stats_helpers import infer_stats_title, stats_from_dataframe, stats_from_summary_csv
from ui.graph_preview.ui_legend_stats_popup import LegendStatsPopup

from ui.graph_preview.ui_dim_overlay import DimOverlay
from ui.graph_preview.legend_popup_helpers import raise_center_and_focus

from .month_grouped_runs_model import MonthGroupedRunsModel

from core.app_paths import resolve_runs_root, migrate_legacy_runs_to_data_root
from core.settings_store import get_settings_path, load_json, save_json
from core.version import __version__
from core.resources import app_root
from core.updater import (
    ReleaseInfo,
    UpdateError,
    download_release_asset,
    fetch_latest_release_info,
    is_newer_version,
    launch_installer,
    launch_installer_with_updater_ui,
)
from core.bundled_tools import (
    resolve_furmark_exe,
    resolve_prime95_exe,
    resolve_hwinfo_exe,
    resolve_hwinfo_csv,
    launch_hwinfo,
    hwinfo_dir,
)
from core.prime95_compat_v305 import load_prime95_torture_snapshot
from core.user_paths import thermalbench_runs_root

from PySide6.QtWidgets import QGridLayout

# Manual update checker (Windows-only)
GITHUB_OWNER = "ZhYinHai"
GITHUB_REPO = "ThermalBench"
INSTALLER_PREFIX = "ThermalBench-Setup-v"

_RESULT_RUN_FOLDER_RE = re.compile(
    r"^(?:\d{8}_\d{6}|(?:CPU|GPU|CPUGPU)_W\d+_L\d+_V\d+)$",
    re.IGNORECASE,
)


class _WinMsg(ctypes.Structure):
    _fields_ = [
        ("hwnd", wintypes.HWND),
        ("message", wintypes.UINT),
        ("wParam", wintypes.WPARAM),
        ("lParam", wintypes.LPARAM),
        ("time", wintypes.DWORD),
        ("pt", wintypes.POINT),
        ("lPrivate", wintypes.DWORD),
    ]


_DWMWA_WINDOW_CORNER_PREFERENCE = 33
_DWMWCP_DEFAULT = 0
_DWMWCP_DONOTROUND = 1
_DWMWCP_ROUND = 2


class _FetchLatestReleaseWorker(QObject):
    finished = Signal(object)  # ReleaseInfo
    failed = Signal(str)

    def __init__(self, owner: str, repo: str, installer_prefix: str):
        super().__init__()
        self._owner = owner
        self._repo = repo
        self._installer_prefix = installer_prefix

    def run(self) -> None:
        try:
            info = fetch_latest_release_info(
                self._owner, self._repo, installer_prefix=self._installer_prefix
            )
            self.finished.emit(info)
        except Exception as e:
            self.failed.emit(str(e))


class _DownloadInstallerWorker(QObject):
    progress = Signal(int, int)  # downloaded_bytes, total_bytes (-1 if unknown)
    finished = Signal(str)  # installer_path
    failed = Signal(str)

    def __init__(self, release: ReleaseInfo):
        super().__init__()
        self._release = release

    def run(self) -> None:
        try:
            def _on_progress(downloaded: int, total: int | None) -> None:
                self.progress.emit(int(downloaded), int(total) if total is not None else -1)

            installer_path = download_release_asset(self._release, progress_cb=_on_progress)
            self.finished.emit(str(installer_path))
        except Exception as e:
            self.failed.emit(str(e))


class _CompareNameDelegate(QStyledItemDelegate):
    """Paint the selected compare folder name with per-run colors.

    Activates only while RunsProxyModel reports an active compare directory.
    """

    def __init__(self, parent=None, theme_mode: str = "dark"):
        super().__init__(parent)
        self._run_meta_cache: dict[str, tuple[float, str]] = {}
        self._theme_mode = str(theme_mode or "dark").strip().lower() or "dark"
        self._compare_prefix_svg = Path(__file__).parent.parent / "resources" / "icons" / "compare.svg"
        self._compare_prefix_pixmap = QPixmap()
        self._compare_prefix_icon_px = 14
        self._rebuild_compare_prefix_icon()

    def _compare_prefix_icon_color(self) -> QColor:
        try:
            effective_mode = resolve_effective_theme_mode(self._theme_mode, QApplication.instance())
        except Exception:
            effective_mode = "dark"
        return QColor("#000000") if effective_mode == "light" else QColor("#FFFFFF")

    def _rebuild_compare_prefix_icon(self) -> None:
        self._compare_prefix_pixmap = QPixmap()
        try:
            if not self._compare_prefix_svg.is_file():
                return

            renderer = QSvgRenderer(str(self._compare_prefix_svg))
            if not renderer.isValid():
                return

            # Scale the pixmap to physical pixels so the icon is crisp at any DPI.
            try:
                app = QApplication.instance()
                dpr = float(app.devicePixelRatio()) if app is not None else 1.0
            except Exception:
                dpr = 1.0
            if dpr <= 0:
                dpr = 1.0

            base_size = max(32, int(self._compare_prefix_icon_px) * 2)
            phys_size = max(1, round(base_size * dpr))

            src = QPixmap(phys_size, phys_size)
            src.fill(Qt.transparent)

            p = QPainter(src)
            try:
                renderer.render(p, QRectF(0.0, 0.0, float(phys_size), float(phys_size)))
            finally:
                p.end()

            tinted = QPixmap(phys_size, phys_size)
            tinted.fill(Qt.transparent)

            p = QPainter(tinted)
            try:
                p.drawPixmap(0, 0, src)
                p.setCompositionMode(QPainter.CompositionMode_SourceIn)
                p.fillRect(tinted.rect(), self._compare_prefix_icon_color())
            finally:
                p.end()

            tinted.setDevicePixelRatio(dpr)
            self._compare_prefix_pixmap = tinted
        except Exception:
            self._compare_prefix_pixmap = QPixmap()

    def set_theme_mode(self, theme_mode: str) -> None:
        try:
            self._theme_mode = str(theme_mode or "dark").strip().lower() or "dark"
            self._rebuild_compare_prefix_icon()

            parent = self.parent()
            if parent is not None and hasattr(parent, "viewport"):
                parent.viewport().update()
            elif parent is not None:
                parent.update()
        except Exception:
            pass

    @staticmethod
    def _norm_path(p: str) -> str:
        try:
            return os.path.normcase(os.path.abspath(str(p)))
        except Exception:
            return str(p or "")

    def _compare_separator_text(self) -> str:
        return " ↔ "

    def _compare_prefix_draw_width(self, fm, text_rect, pref: str) -> int:
        try:
            if not self._compare_prefix_pixmap.isNull():
                icon_size = min(self._compare_prefix_icon_px, max(10, int(text_rect.height()) - 4))
                return icon_size + fm.horizontalAdvance(" ")
        except Exception:
            pass
        return fm.horizontalAdvance(pref) if pref else 0

    def _draw_compare_prefix_icon(self, painter: QPainter, text_rect, x: int, fm, pref: str, opt) -> int:
        try:
            if not self._compare_prefix_pixmap.isNull():
                icon_size = min(self._compare_prefix_icon_px, max(10, int(text_rect.height()) - 4))
                pm = self._compare_prefix_pixmap.scaled(
                    icon_size,
                    icon_size,
                    Qt.KeepAspectRatio,
                    Qt.SmoothTransformation,
                )
                y = int(text_rect.y() + (text_rect.height() - pm.height()) // 2)
                painter.drawPixmap(x, y, pm)
                return x + pm.width() + fm.horizontalAdvance(" ")
        except Exception:
            pass

        if pref:
            painter.setPen(QColor(opt.palette.text().color()))
            painter.drawText(
                x,
                text_rect.y() + (text_rect.height() + fm.ascent() - fm.descent()) // 2,
                pref,
            )
            return x + fm.horizontalAdvance(pref)

        return x

    def _run_meta_text(self, path: str) -> str:
        try:
            run_dir = Path(str(path or ""))
            if not run_dir.is_dir():
                return ""

            settings_path = run_dir / "test_settings.json"

            sig = 0.0
            try:
                sig = float(run_dir.stat().st_mtime or 0.0)
            except Exception:
                sig = 0.0
            try:
                if settings_path.is_file():
                    sig = max(sig, float(settings_path.stat().st_mtime or 0.0))
            except Exception:
                pass

            cache_key = self._norm_path(str(run_dir))
            cached = self._run_meta_cache.get(cache_key)
            if cached is not None and cached[0] == sig:
                return str(cached[1] or "")

            dt = None
            if settings_path.is_file():
                try:
                    payload = json.loads(settings_path.read_text(encoding="utf-8"))
                except Exception:
                    payload = {}
                recorded_at = str(payload.get("recorded_at") or "").strip()
                if recorded_at:
                    try:
                        dt = datetime.fromisoformat(recorded_at.replace("Z", "+00:00"))
                    except Exception:
                        dt = None

            if dt is None:
                manifest_path = run_dir / "compare_manifest.json"
                if manifest_path.is_file():
                    try:
                        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
                    except Exception:
                        payload = {}
                    created_at = str(payload.get("created_at") or "").strip()
                    if created_at:
                        try:
                            dt = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
                        except Exception:
                            dt = None

            if dt is None:
                try:
                    dt = datetime.strptime(run_dir.name, "%Y%m%d_%H%M%S")
                except Exception:
                    dt = None

            if dt is None:
                try:
                    dt = datetime.fromtimestamp(float(run_dir.stat().st_mtime or 0.0))
                except Exception:
                    dt = None

            text = dt.strftime("%Y-%m-%d %H:%M") if dt is not None else ""
            self._run_meta_cache[cache_key] = (sig, text)
            return text
        except Exception:
            return ""

    def paint(self, painter: QPainter, option, index):
        try:
            model = index.model()
            get_active = getattr(model, "get_active_compare_dir_norm", None)
            get_seg_colors = getattr(model, "get_active_compare_segment_colors", None)
            if not (callable(get_active) and callable(get_seg_colors)):
                return super().paint(painter, option, index)

            path = None
            is_dir = False
            try:
                if hasattr(model, "mapToSource") and hasattr(model, "sourceModel"):
                    sm = model.sourceModel()
                    src = model.mapToSource(index)
                    if sm is not None and hasattr(sm, "filePath") and src.isValid():
                        path = sm.filePath(src)
                        if hasattr(sm, "isDir"):
                            is_dir = bool(sm.isDir(src))
                elif hasattr(model, "filePath"):
                    path = model.filePath(index)
                    if hasattr(model, "isDir"):
                        is_dir = bool(model.isDir(index))
            except Exception:
                path = None
                is_dir = False

            if not path:
                return super().paint(painter, option, index)

            is_compare_result_dir = False
            try:
                is_compare_fn = getattr(model, "is_compare_result_dir_path", None)
                if is_dir and callable(is_compare_fn):
                    is_compare_result_dir = bool(is_compare_fn(path))
            except Exception:
                is_compare_result_dir = False

            is_regular_run_dir = bool(
                is_dir
                and (not is_compare_result_dir)
                and _RESULT_RUN_FOLDER_RE.match(Path(path).name or "")
            )

            is_compare_case_dir = False
            try:
                is_compare_case_fn = getattr(model, "is_compare_case_dir_path", None)
                if is_dir and callable(is_compare_case_fn):
                    is_compare_case_dir = bool(is_compare_case_fn(path))
            except Exception:
                is_compare_case_dir = False

            is_compare_selected = False
            try:
                is_compare_sel_fn = getattr(model, "is_compare_selected_dir_path", None)
                if callable(is_compare_sel_fn):
                    is_compare_selected = bool(is_compare_sel_fn(path))
            except Exception:
                is_compare_selected = False

            compare_prefix = "↔ "
            try:
                get_pref = getattr(model, "get_compare_prefix", None)
                if callable(get_pref):
                    compare_prefix = str(get_pref() or compare_prefix)
            except Exception:
                pass

            def _selected_row_text_color(opt: QStyleOptionViewItem, *, secondary: bool = False) -> QColor:
                try:
                    if not (opt.state & QStyle.State_Selected):
                        base = QColor(opt.palette.color(QPalette.Text))
                        if secondary:
                            base.setAlpha(150)
                        return base

                    base_text = QColor(opt.palette.color(QPalette.Text))
                    if base_text.isValid() and base_text.lightness() < 80:
                        return QColor("#000000")

                    c = QColor(opt.palette.color(QPalette.HighlightedText))
                    if secondary:
                        c.setAlpha(235)
                    return c
                except Exception:
                    c = QColor(option.palette.text().color())
                    if secondary:
                        c.setAlpha(150)
                    return c

            def _symbol_pen_color(opt: QStyleOptionViewItem) -> QColor:
                try:
                    if not (opt.state & QStyle.State_Selected):
                        try:
                            link = opt.palette.color(QPalette.Link)
                            if isinstance(link, QColor) and link.isValid():
                                c = QColor(link)
                                c.setAlpha(min(int(c.alpha()), 220))
                                return c
                        except Exception:
                            pass
                        try:
                            ph = opt.palette.color(QPalette.PlaceholderText)
                            if isinstance(ph, QColor) and ph.isValid():
                                return QColor(ph)
                        except Exception:
                            pass

                    base = _selected_row_text_color(opt, secondary=True)
                    c = QColor(base)
                    if c.lightness() < 128:
                        c = c.lighter(165)
                    else:
                        c = c.darker(165)
                    return QColor(c)
                except Exception:
                    return QColor(opt.palette.text().color())

            def _meta_pen_color(opt: QStyleOptionViewItem) -> QColor:
                try:
                    if opt.state & QStyle.State_Selected:
                        return _selected_row_text_color(opt, secondary=True)
                    try:
                        c = QColor(opt.palette.color(QPalette.PlaceholderText))
                        if c.isValid():
                            return c
                    except Exception:
                        pass
                    c = QColor(opt.palette.color(QPalette.Text))
                    c.setAlpha(150)
                    return c
                except Exception:
                    c = QColor(option.palette.text().color())
                    c.setAlpha(150)
                    return c

            def _paint_row_state_overlay(opt: QStyleOptionViewItem) -> None:
                try:
                    if not is_compare_selected:
                        return

                    base_text = QColor(opt.palette.color(QPalette.Text))
                    light_mode = bool(base_text.isValid() and base_text.lightness() < 80)

                    fill = QColor("#CDEECC") if light_mode else QColor("#1F4D2E")

                    fill_rect = option.rect.adjusted(0, 0, 0, -1)
                    try:
                        if opt.widget is not None and hasattr(opt.widget, "viewport"):
                            vp = opt.widget.viewport()
                            if vp is not None:
                                fill_rect = option.rect.adjusted(
                                    -option.rect.x(),
                                    0,
                                    int(vp.width() - option.rect.right() - 1),
                                    -1,
                                )
                    except Exception:
                        fill_rect = option.rect.adjusted(0, 0, 0, -1)

                    painter.save()
                    painter.setClipRect(fill_rect)
                    painter.fillRect(fill_rect, fill)
                    painter.restore()
                except Exception:
                    pass

            def _paint_text_with_meta(txt: str, meta: str) -> None:
                if not txt:
                    super().paint(painter, option, index)
                    return

                opt = QStyleOptionViewItem(option)
                self.initStyleOption(opt, index)
                opt.text = ""
                if is_compare_selected:
                    opt.state &= ~QStyle.State_Selected
                style = opt.widget.style() if opt.widget is not None else QApplication.style()
                style.drawControl(QStyle.CE_ItemViewItem, opt, painter, opt.widget)
                _paint_row_state_overlay(opt)

                text_rect = style.subElementRect(QStyle.SE_ItemViewItemText, opt, opt.widget)
                fm = opt.fontMetrics
                baseline = text_rect.y() + (text_rect.height() + fm.ascent() - fm.descent()) // 2

                painter.save()
                painter.setClipRect(option.rect)

                gap = fm.horizontalAdvance("   ") if meta else 0
                meta_w = fm.horizontalAdvance(meta) if meta else 0
                avail = max(0, int(text_rect.width() - meta_w - gap))
                txt_elided = fm.elidedText(txt, Qt.ElideRight, avail)

                x = text_rect.x()
                painter.setPen(QColor(_selected_row_text_color(opt)))
                painter.drawText(x, baseline, txt_elided)
                x += fm.horizontalAdvance(txt_elided)

                if meta:
                    x += gap
                    painter.setPen(_meta_pen_color(opt))
                    painter.drawText(x, baseline, meta)

                painter.restore()

            def _paint_compare_prefix_and_text(txt: str) -> None:
                if not txt:
                    super().paint(painter, option, index)
                    return

                opt = QStyleOptionViewItem(option)
                self.initStyleOption(opt, index)
                opt.text = ""
                if is_compare_selected:
                    opt.state &= ~QStyle.State_Selected
                style = opt.widget.style() if opt.widget is not None else QApplication.style()
                style.drawControl(QStyle.CE_ItemViewItem, opt, painter, opt.widget)
                _paint_row_state_overlay(opt)

                text_rect = style.subElementRect(QStyle.SE_ItemViewItemText, opt, opt.widget)
                fm = opt.fontMetrics
                baseline = text_rect.y() + (text_rect.height() + fm.ascent() - fm.descent()) // 2

                painter.save()
                painter.setClipRect(option.rect)

                x = text_rect.x()

                pref = str(compare_prefix or "")
                shown_txt = txt[len(pref):] if pref and txt.startswith(pref) else txt
                shown_txt = shown_txt.replace(" vs ", self._compare_separator_text())

                pref_w = self._compare_prefix_draw_width(fm, text_rect, pref)
                avail = max(0, int(text_rect.width() - pref_w))
                txt_elided = fm.elidedText(shown_txt, Qt.ElideRight, avail)

                x = self._draw_compare_prefix_icon(painter, text_rect, x, fm, pref, opt)

                painter.setPen(QColor(_selected_row_text_color(opt)))
                painter.drawText(x, baseline, txt_elided)

                painter.restore()

            active_norm = get_active()

            if is_regular_run_dir:
                txt = str(index.data(Qt.DisplayRole) or "")
                meta = self._run_meta_text(path)
                if meta:
                    _paint_text_with_meta(txt, meta)
                    return

            if is_compare_case_dir and (not active_norm or self._norm_path(path) != str(active_norm)):
                txt = str(index.data(Qt.DisplayRole) or "")
                _paint_compare_prefix_and_text(txt)
                return

            if not active_norm or self._norm_path(path) != str(active_norm):
                return super().paint(painter, option, index)

            txt = str(index.data(Qt.DisplayRole) or "")
            seg_colors = list(get_seg_colors() or [])
            if (not txt) or (" vs " not in txt) or (not seg_colors):
                if is_compare_case_dir:
                    _paint_compare_prefix_and_text(txt)
                    return
                return super().paint(painter, option, index)

            has_prefix = bool(compare_prefix and txt.startswith(compare_prefix))
            txt_for_split = txt[len(compare_prefix):] if has_prefix else txt

            opt = QStyleOptionViewItem(option)
            self.initStyleOption(opt, index)
            opt.text = ""
            if is_compare_selected:
                opt.state &= ~QStyle.State_Selected
            style = opt.widget.style() if opt.widget is not None else QApplication.style()
            style.drawControl(QStyle.CE_ItemViewItem, opt, painter, opt.widget)
            _paint_row_state_overlay(opt)

            text_rect = style.subElementRect(QStyle.SE_ItemViewItemText, opt, opt.widget)
            fm = opt.fontMetrics
            baseline = text_rect.y() + (text_rect.height() + fm.ascent() - fm.descent()) // 2

            painter.save()
            painter.setClipRect(option.rect)

            x = text_rect.x()

            pref = str(compare_prefix or "")
            if is_compare_case_dir and pref:
                x = self._draw_compare_prefix_icon(painter, text_rect, x, fm, pref, opt)

            parts = txt_for_split.split(" vs ")
            for i, part in enumerate(parts):
                color = seg_colors[i] if i < len(seg_colors) else opt.palette.text().color()

                painter.setPen(QColor(color))
                painter.drawText(x, baseline, part)
                x += fm.horizontalAdvance(part)

                if i != (len(parts) - 1):
                    sep = self._compare_separator_text()
                    painter.setPen(_symbol_pen_color(opt))
                    painter.drawText(x, baseline, sep)
                    x += fm.horizontalAdvance(sep)

            painter.restore()
            return
        except Exception:
            return super().paint(painter, option, index)


class MainWindow(QWidget):
    _WM_NCHITTEST = 0x0084
    _HTCLIENT = 1
    _HTLEFT = 10
    _HTRIGHT = 11
    _HTTOP = 12
    _HTTOPLEFT = 13
    _HTTOPRIGHT = 14
    _HTBOTTOM = 15
    _HTBOTTOMLEFT = 16
    _HTBOTTOMRIGHT = 17

    def _update_compare_manifests_for_case_rename(self, *, old_case: str, new_case: str) -> None:
        """Rewrite compare manifests so compare results survive case-folder renames.

        Compare manifests store run paths relative to the runs root like:
          "runs": ["<case>/<run>", ...]

        If the case folder is renamed, those references break. This method scans all
        compare result folders and rewrites any run path whose first segment equals
        old_case to use new_case.
        """
        try:
            old_case = str(old_case or "").strip()
            new_case = str(new_case or "").strip()
            if not old_case or not new_case or old_case == new_case:
                return

            runs_root = Path(getattr(self, "_runs_root", "") or "")
            if not runs_root.exists() or not runs_root.is_dir():
                return

            try:
                runs_root_r = runs_root.resolve()
            except Exception:
                runs_root_r = runs_root

            def _rewrite_run_ref(ref: str) -> str:
                s = str(ref or "").strip()
                if not s:
                    return s

                # Try to interpret as absolute path first.
                try:
                    p = Path(s)
                    if p.is_absolute():
                        try:
                            rel = p.resolve().relative_to(runs_root_r)
                            parts = list(rel.parts)
                            if parts and parts[0] == old_case:
                                parts[0] = new_case
                                return "/".join(parts)
                            return "/".join(parts)
                        except Exception:
                            pass
                except Exception:
                    pass

                # Relative path: normalize separators.
                parts = [p for p in s.replace("\\", "/").split("/") if p]
                if parts and parts[0] == old_case:
                    parts[0] = new_case
                    return "/".join(parts)
                return "/".join(parts)

            # Scan: runs/<case>/<run>/compare_manifest.json
            for case_ent in os.scandir(str(runs_root)):
                if not case_ent.is_dir():
                    continue
                try:
                    for run_ent in os.scandir(case_ent.path):
                        if not run_ent.is_dir():
                            continue
                        mp = Path(run_ent.path) / "compare_manifest.json"
                        if not mp.is_file():
                            continue
                        try:
                            m = json.loads(mp.read_text(encoding="utf-8"))
                        except Exception:
                            m = {}

                        runs_list = m.get("runs")
                        if not isinstance(runs_list, list) or not runs_list:
                            continue

                        new_runs = []
                        changed = False
                        for r in runs_list:
                            nr = _rewrite_run_ref(str(r))
                            if nr != str(r):
                                changed = True
                            new_runs.append(nr)

                        if not changed:
                            continue

                        m["runs"] = new_runs
                        try:
                            display_case_name = str(m.get("display_case_name") or "")
                            if display_case_name:
                                m["display_case_name"] = display_case_name.replace(old_case, new_case)
                        except Exception:
                            pass
                        try:
                            display_run_name = str(m.get("display_run_name") or "")
                            if display_run_name:
                                m["display_run_name"] = display_run_name.replace(old_case, new_case)
                        except Exception:
                            pass
                        try:
                            mp.write_text(json.dumps(m, indent=2), encoding="utf-8")
                        except Exception:
                            # best-effort; ignore individual failures
                            pass
                except Exception:
                    continue
        except Exception:
            pass

    def _on_runs_tree_clicked(self, index) -> None:
        try:
            btn = self._runs_tree.property("_tb_last_button")
            if btn is not None and int(btn) == int(Qt.RightButton):
                return
        except Exception:
            pass

        try:
            handled = False
            if hasattr(self, "benchmark") and hasattr(self.benchmark, "activate_results_index"):
                handled = bool(self.benchmark.activate_results_index(index))
            if handled:
                return
        except Exception:
            pass

        self._toggle_tree_item(index)

    def _rename_compare_folders_for_case_rename(self, *, old_case: str, new_case: str) -> None:
        """Rename compare-result directories whose names include a renamed case.

        This keeps the *folder names* in the Results tree consistent after a case rename.

        We only operate on folders that are confirmed compare results (contain a
        compare_manifest.json with type == "compare").
        """
        try:
            old_case = str(old_case or "").strip()
            new_case = str(new_case or "").strip()
            if not old_case or not new_case or old_case == new_case:
                return

            runs_root = Path(getattr(self, "_runs_root", "") or "")
            if not runs_root.exists() or not runs_root.is_dir():
                return

            # Collect compare run dirs first (don't rename while scanning).
            compare_run_dirs: list[Path] = []
            try:
                for case_ent in os.scandir(str(runs_root)):
                    if not case_ent.is_dir():
                        continue
                    try:
                        for run_ent in os.scandir(case_ent.path):
                            if not run_ent.is_dir():
                                continue
                            rd = Path(run_ent.path)
                            mp = rd / "compare_manifest.json"
                            if not mp.is_file():
                                continue
                            try:
                                m = json.loads(mp.read_text(encoding="utf-8"))
                            except Exception:
                                m = {}
                            if str(m.get("type") or "").strip().lower() != "compare":
                                continue
                            compare_run_dirs.append(rd)
                    except Exception:
                        continue
            except Exception:
                compare_run_dirs = []

            # First rename parent compare case dirs (so children move once).
            # Map: old_case_dir -> desired_case_dir
            case_dir_moves: dict[Path, Path] = {}
            for rd in compare_run_dirs:
                try:
                    cd = rd.parent
                    if cd is None:
                        continue
                    desired_name = str(cd.name).replace(old_case, new_case)
                    if desired_name and desired_name != cd.name:
                        dest = cd.parent / desired_name
                        case_dir_moves[cd] = dest
                except Exception:
                    continue

            for src, dest in list(case_dir_moves.items()):
                try:
                    if not src.exists() or not src.is_dir():
                        continue
                    if dest.exists():
                        # Avoid collisions; keep existing name rather than inventing one.
                        continue
                    src.rename(dest)
                except Exception:
                    continue

            # Then rename compare run dirs (some may have moved with their parent).
            for rd in compare_run_dirs:
                try:
                    # Re-resolve current location if parent moved.
                    mp = rd / "compare_manifest.json"
                    if not mp.is_file():
                        # Try to find moved folder by walking parent moves.
                        try:
                            cd = rd.parent
                            if cd in case_dir_moves:
                                rd2 = case_dir_moves[cd] / rd.name
                                rd = rd2
                                mp = rd / "compare_manifest.json"
                        except Exception:
                            pass

                    if not rd.exists() or not rd.is_dir():
                        continue
                    if not mp.is_file():
                        continue

                    desired_run_name = str(rd.name).replace(old_case, new_case)
                    if not desired_run_name or desired_run_name == rd.name:
                        continue
                    dest = rd.parent / desired_run_name
                    if dest.exists():
                        continue
                    rd.rename(dest)
                except Exception:
                    continue

        except Exception:
            pass

    def __init__(self):
        super().__init__()

        # Default startup size
        DEFAULT_W, DEFAULT_H = 1366, 768
        self.resize(DEFAULT_W, DEFAULT_H)
        self.setMinimumHeight(DEFAULT_H)

        self._pending_latest_result_path = ""
        self._pending_latest_result_month = ""

        # Enable custom titlebar by making window frameless
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Window)
        self._resize_border_px = 8
        self._rounded_corners_applied = False
        self._restoring_window_state = False
        self._window_settings_timer = QTimer(self)
        self._window_settings_timer.setSingleShot(True)
        self._window_settings_timer.timeout.connect(self.save_settings)
        QTimer.singleShot(0, self._apply_window_corner_preference)

        # Keep the real window title in sync (taskbar/alt-tab) with the custom titlebar text
        self.setWindowTitle(f"ThermalBench v{__version__}")

        self.settings_path = get_settings_path("ThermalBench")
        self.furmark_exe = resolve_furmark_exe("")
        self.prime_exe = resolve_prime95_exe("")
        self.hwinfo_exe = resolve_hwinfo_exe("")
        self.hwinfo_csv = resolve_hwinfo_csv()
        self.theme_mode = "device"

        # Push notifications (ntfy)
        # Store either a full topic URL (https://ntfy.sh/<topic>) or just a topic name.
        self.ntfy_topic = ""

        self._ntfy_notifier = NtfyNotifier(self)
        try:
            self._ntfy_notifier.finished.connect(self._on_ntfy_notify_finished)
        except Exception:
            pass

        # Inputs
        self.case_edit = QLineEdit("TEST")
        self._case_name_popup = QListWidget(self)
        self._case_name_popup.hide()
        self._case_name_popup.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._case_name_popup.setSelectionMode(QAbstractItemView.SingleSelection)
        self._case_name_popup.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._case_name_popup.setFocusPolicy(Qt.NoFocus)
        self._case_name_popup.itemClicked.connect(self._on_case_name_popup_item_clicked)
        self.case_edit.installEventFilter(self)
        self._case_name_popup.installEventFilter(self)
        app = QApplication.instance()
        if app is not None:
            app.installEventFilter(self)
            try:
                app.focusChanged.connect(self._on_app_focus_changed)
            except Exception:
                pass
        self._apply_case_name_completer_theme()
        self._refresh_case_name_suggestions()

        self.warmup_min = make_time_spin(2, 24 * 60, 20)
        self.warmup_sec = make_time_spin(2, 59, 0)
        self.log_min = make_time_spin(2, 24 * 60, 15)
        self.log_sec = make_time_spin(2, 59, 0)

        self.hwinfo_edit = QLineEdit(self.hwinfo_csv)
        self.hwinfo_edit.setReadOnly(True)
        self.hwinfo_edit.setToolTip(
            "Fixed HWiNFO CSV path.\n"
            "Configure HWiNFO sensor logging to write here:\n"
            f"{self.hwinfo_csv}"
        )

        self.copy_hwinfo_path_btn = QToolButton(self.hwinfo_edit)
        self.copy_hwinfo_path_btn.setText("Copy Path")
        self.copy_hwinfo_path_btn.setToolTip(
            "Copy the folder path to the clipboard.\n"
            "Paste it into HWiNFO's sensor logging dialog so HWiNFO\n"
            "can write hwinfo.csv to the correct folder."
        )
        self.copy_hwinfo_path_btn.setStyleSheet(
            """
            QToolButton {
                background-color: rgba(128, 128, 128, 0.13);
                border: 1px solid rgba(128, 128, 128, 0.22);
                border-radius: 6px;
                padding: 2px 8px;
                font-size: 11px;
            }
            QToolButton:hover {
                background-color: rgba(128, 128, 128, 0.22);
                border: 1px solid rgba(128, 128, 128, 0.35);
            }
            QToolButton:pressed {
                background-color: rgba(128, 128, 128, 0.35);
            }
            """
        )
        self.copy_hwinfo_path_btn.setCursor(Qt.PointingHandCursor)
        self.copy_hwinfo_path_btn.clicked.connect(self._copy_hwinfo_folder_path)
        self.copy_hwinfo_path_btn.adjustSize()
        self.hwinfo_edit.installEventFilter(self)
        # Reserve space on the right so text doesn't slide under the button
        _btn_w = self.copy_hwinfo_path_btn.sizeHint().width() + 5
        self.hwinfo_edit.setTextMargins(0, 0, _btn_w, 0)

        # --- Status dots ---
        self.csv_dot = QLabel("●")
        self.csv_dot.setObjectName("StatusDot")
        self.csv_dot.setProperty("state", "bad")
        self.csv_dot.setToolTip("CSV: unknown")

        self.sm2_dot = QLabel("●")
        self.sm2_dot.setObjectName("StatusDot")
        self.sm2_dot.setProperty("state", "bad")
        self.sm2_dot.setToolTip("Sensor grouping: unknown")

        # FurMark dropdowns
        self.fur_demo_combo = CustomComboBox(mode=self.theme_mode)
        self.fur_demo_map = {
            "FurMark Knot (OpenGL)": "furmark-knot-gl",
            "FurMark (OpenGL)": "furmark-gl",
            "FurMark Knot (Vulkan)": "furmark-knot-vk",
            "FurMark (Vulkan)": "furmark-vk",
        }
        for k in self.fur_demo_map.keys():
            self.fur_demo_combo.addItem(k)
        self.fur_demo_combo.setCurrentText("FurMark Knot (OpenGL)")

        self.fur_res_combo = CustomComboBox(mode=self.theme_mode)
        self.res_order = ["3840 x 2160", "3840 x 1600", "3440 x 1440", "2560 x 1440", "1920 x 1080"]
        self.res_map = {
            "3840 x 2160": (3840, 2160),
            "3840 x 1600": (3840, 1600),
            "3440 x 1440": (3440, 1440),
            "2560 x 1440": (2560, 1440),
            "1920 x 1080": (1920, 1080),
        }
        for k in self.res_order:
            self.fur_res_combo.addItem(k)
        self.fur_res_combo.setCurrentText("3840 x 1600")

        self._prime95_torture_snapshot = {}
        self.prime95_settings_btn = QPushButton("Prime95 torture settings")
        self.prime95_settings_btn.setCursor(Qt.PointingHandCursor)
        self.prime95_settings_btn.clicked.connect(self._show_prime95_torture_settings_popup)

        # Sensors summary (clickable)
        self.sensors_summary = QLineEdit()
        self.sensors_summary.setReadOnly(True)
        self.sensors_summary.setPlaceholderText("No sensors selected (will use defaults).")
        self.sensors_summary.setCursor(Qt.PointingHandCursor)

        self.pick_sensors_btn = QPushButton("Select sensors…")

        # Stress toggle buttons
        self.cpu_btn = QPushButton("CPU")
        self.gpu_btn = QPushButton("GPU")
        for b in (self.cpu_btn, self.gpu_btn):
            b.setCheckable(True)
            b.setStyleSheet("QPushButton:checked { border: 1px solid #4A90E2; }")

        self.cpu_btn.setChecked(True)
        self.gpu_btn.setChecked(True)

        # Buttons
        self.run_btn = QPushButton("Run")
        self.abort_btn = QPushButton("Abort")
        self.abort_btn.setEnabled(False)
        self.pick_hwinfo_btn = QPushButton("Open HWiNFO")
        self.pick_hwinfo_btn.setToolTip(
            "Open bundled HWiNFO.\n"
            "In HWiNFO, configure sensor logging to:\n"
            f"{self.hwinfo_csv}"
        )
        self.pick_hwinfo_btn.clicked.connect(self.open_hwinfo)

        # Log box
        self.log = QTextEdit()
        self.log.setReadOnly(True)
        self.log.setStyleSheet("font-family: Consolas, 'Courier New', monospace;")

        # Live monitor table (shown during runs)
        self._live_monitor = LiveMonitorWidget(self)
        self._live_graph = LiveGraphWidget(self)
        self._live_monitor.set_theme_mode(self.theme_mode)
        self._live_graph.set_theme_mode(self.theme_mode)
        self._output_stack = None
        self._output_btn_live = None
        self._output_btn_console = None

        self.live_timer = QLabel("Idle")
        self.live_timer.setObjectName("LiveTimer")

        # ======================================================================
        # INITIALIZE COMPONENTS
        # ======================================================================

        # Preview label for images
        self._preview_label = QLabel()
        self._preview_label.setAlignment(Qt.AlignCenter)
        self._preview_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        # Sensor Manager
        self.sensors = SensorManager(
            parent=self,
            hwinfo_edit=self.hwinfo_edit,
            csv_dot=self.csv_dot,
            sm2_dot=self.sm2_dot,
            sensors_summary=self.sensors_summary,
            save_settings_callback=self.save_settings,
            update_run_button_callback=self._update_run_button_state,
            stress_cpu_btn=self.cpu_btn,
            stress_gpu_btn=self.gpu_btn,
        )

        # Graph Preview
        self.graph = GraphPreview(
            parent=self,
            preview_label=self._preview_label,
            build_selected_columns_callback=self.sensors.build_selected_columns,
        )
        self.graph.set_theme_mode(self.theme_mode)

        try:
            self._runs_migration_result = migrate_legacy_runs_to_data_root()
        except Exception:
            self._runs_migration_result = None

        runs_root = resolve_runs_root()
        self._runs_root = runs_root
        self._runs_source_model = MonthGroupedRunsModel(self._runs_root, self)

        self._runs_tree = QTreeView()
        self._runs_tree.setHeaderHidden(True)
        try:
            self._runs_tree.setSelectionMode(QAbstractItemView.ExtendedSelection)
            self._runs_tree.setSelectionBehavior(QAbstractItemView.SelectRows)
            self._runs_tree.setExpandsOnDoubleClick(False)
        except Exception:
            pass

        # Right-click context menu for case folders (runs/<case>)
        try:
            self._runs_tree.setContextMenuPolicy(Qt.CustomContextMenu)
            self._runs_tree.customContextMenuRequested.connect(self._on_runs_tree_context_menu)
        except Exception:
            pass

        # Track last mouse button so right-click does not trigger left-click behavior.
        try:
            self._runs_tree.setProperty("_tb_last_button", int(Qt.LeftButton))
            self._runs_tree.viewport().installEventFilter(self)
        except Exception:
            pass

        self.compare_btn = QPushButton("Compare")
        self.compare_btn.setEnabled(False)
        self.compare_btn.setCursor(Qt.PointingHandCursor)

        # Removed: bottom "Remove Selected" button (deletion is available via right-click menu).
        self.remove_result_btn = None

        # Use proxy model if present
        self._runs_proxy = RunsProxyModel(self)
        self._runs_proxy.setSourceModel(self._runs_source_model)
        self._runs_tree.setModel(self._runs_proxy)

        # Multi-colored compare folder name when a compare result is selected.
        try:
            self._runs_tree_compare_delegate = _CompareNameDelegate(self._runs_tree, theme_mode=self.theme_mode)
            self._runs_tree.setItemDelegate(self._runs_tree_compare_delegate)
        except Exception:
            self._runs_tree_compare_delegate = None

        self._runs_tree_search = QLineEdit()
        self._runs_tree_search.setPlaceholderText("Search folders")
        self._runs_tree_search.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self._runs_tree_search_expanded_paths: set[str] | None = None
        try:
            self._runs_tree_search.setClearButtonEnabled(True)
        except Exception:
            pass
        self._runs_tree_search.textChanged.connect(self._on_runs_tree_search_changed)

        for c in range(1, 4):
            try:
                self._runs_tree.hideColumn(c)
            except Exception:
                pass

        # Enable sorting so compare cases can be grouped at the bottom.
        try:
            self._runs_tree.setSortingEnabled(True)
            self._runs_tree.sortByColumn(0, Qt.AscendingOrder)
        except Exception:
            pass

        # Enable single-click to expand/collapse folders
        self._runs_tree.clicked.connect(self._on_runs_tree_clicked)

        # Benchmark Controller
        # IMPORTANT FIX: pass both runs_model (tree model) and runs_source_model (QFileSystemModel)
        self.benchmark = BenchmarkController(
            parent=self,
            log_widget=self.log,
            run_btn=self.run_btn,
            abort_btn=self.abort_btn,
            open_btn=None,
            live_timer=self.live_timer,
            remove_btn=None,
            compare_btn=self.compare_btn,
            runs_tree=self._runs_tree,
            runs_model=self._runs_tree.model(),          # proxy
            runs_source_model=self._runs_source_model,   # virtual source model
            runs_root=self._runs_root,
            graph_preview=self.graph,
            sensor_manager=self.sensors,
            save_settings_callback=self.save_settings,
            get_settings_callback=self._get_current_settings,
            append_log_callback=self.append,
            on_run_started=self._on_run_started,
            on_run_finished=self._on_run_finished,
            on_log_started=self._on_log_started,
            on_log_finished=self._on_log_finished,
            on_ambient_csv=self._on_ambient_csv,
        )

        # Connect component signals
        self.sensors_summary.mousePressEvent = lambda e: self.sensors.open_selected_sensors_view()
        self.pick_sensors_btn.clicked.connect(self.sensors.open_sensor_picker)
        self.run_btn.clicked.connect(self.benchmark.run)
        self.abort_btn.clicked.connect(self.benchmark.abort)
        self.compare_btn.clicked.connect(self.benchmark.compare_selected_results)
        self.cpu_btn.toggled.connect(self._sync_furmark_gpu_controls)
        self.gpu_btn.toggled.connect(self._sync_furmark_gpu_controls)
        # Removed: bottom delete button

        try:
            self._runs_tree.doubleClicked.connect(self.benchmark.toggle_compare_selection_for_index)
        except Exception:
            pass

        # ======================================================================
        # BUILD UI LAYOUT
        # ======================================================================

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self.titlebar = TitleBar(self, f"ThermalBench v{__version__}")
        outer.addWidget(self.titlebar)
        
        # Titlebar bottom border line
        titlebar_border = QFrame()
        titlebar_border.setFrameShape(QFrame.HLine)
        titlebar_border.setFrameShadow(QFrame.Plain)
        titlebar_border.setFixedHeight(1)
        titlebar_border.setStyleSheet("background-color: rgba(128, 128, 128, 0.3); border: none;")
        outer.addWidget(titlebar_border)

        # --------------------------
        # Left tab rail + page stack
        # --------------------------
        center = QWidget()
        center_layout = QHBoxLayout(center)
        center_layout.setContentsMargins(0, 0, 0, 0)
        center_layout.setSpacing(0)
        outer.addWidget(center, 1)

        # Left rail (VS Code style)
        self._nav = QWidget()
        self._nav.setFixedWidth(40)
        nav_layout = QVBoxLayout(self._nav)
        nav_layout.setContentsMargins(0, 0, 0, 8)
        nav_layout.setSpacing(0)

        # Icon-only buttons
        self._btn_run_page = QToolButton()
        self._rail_icon_size = QSize(18, 18)
        self._run_icon_path = Path(__file__).parent.parent / "resources" / "icons" / "play_arrow_24dp_FFFFFF_FILL0_wght400_GRAD0_opsz24.svg"
        self._btn_run_page.setIconSize(self._rail_icon_size)
        self._btn_run_page.setToolTip("Run Benchmark")
        self._btn_run_page.setCheckable(True)
        self._btn_run_page.setAutoExclusive(True)
        self._btn_run_page.setCursor(Qt.PointingHandCursor)
        self._btn_run_page.setFixedSize(40, 40)

        self._btn_results_page = QToolButton()
        self._results_icon_path = Path(__file__).parent.parent / "resources" / "icons" / "graph_1_24dp_FFFFFF_FILL0_wght400_GRAD0_opsz24.svg"
        self._btn_results_page.setIconSize(self._rail_icon_size)
        self._btn_results_page.setToolTip("Results")
        self._btn_results_page.setCheckable(True)
        self._btn_results_page.setAutoExclusive(True)
        self._btn_results_page.setCursor(Qt.PointingHandCursor)
        self._btn_results_page.setFixedSize(40, 40)

        self._btn_help = QToolButton()
        self._help_icon_path = Path(__file__).parent.parent / "resources" / "icons" / "help_24dp_FFFFFF_FILL0_wght400_GRAD0_opsz24.svg"
        self._btn_help.setIconSize(self._rail_icon_size)
        self._btn_help.setToolTip("How to use ThermalBench")
        self._btn_help.setCursor(Qt.PointingHandCursor)
        self._btn_help.setFixedSize(40, 40)
        self._btn_help.clicked.connect(self.open_help)

        self._btn_settings = QToolButton()
        self._settings_icon_path = Path(__file__).parent.parent / "resources" / "icons" / "settings_24dp_FFFFFF_FILL0_wght400_GRAD0_opsz24.svg"
        self._btn_settings.setIconSize(self._rail_icon_size)
        self._btn_settings.setToolTip("Settings")
        self._btn_settings.setCursor(Qt.PointingHandCursor)
        self._btn_settings.setFixedSize(40, 40)
        self._btn_settings.clicked.connect(self.open_settings)

        self._apply_left_rail_theme()

        nav_layout.addWidget(self._btn_run_page)
        nav_layout.addWidget(self._btn_results_page)
        nav_layout.addStretch(1)
        nav_layout.addWidget(self._btn_help)
        nav_layout.addWidget(self._btn_settings)

        # Page stack (replaces QTabWidget)
        self._stack = QStackedWidget()

        center_layout.addWidget(self._nav)

        # Content splitter: main stack on left, help panel on right
        self._content_splitter = QSplitter(Qt.Horizontal)
        self._content_splitter.setChildrenCollapsible(True)
        self._content_splitter.setHandleWidth(5)
        self._content_splitter.setStyleSheet(
            "QSplitter::handle:horizontal {"
            "  background: rgba(128, 128, 128, 0.25);"
            "  border-left: 1px solid rgba(128, 128, 128, 0.15);"
            "  border-right: 1px solid rgba(128, 128, 128, 0.15);"
            "}"
            "QSplitter::handle:horizontal:hover {"
            "  background: rgba(128, 128, 128, 0.45);"
            "}"
        )
        self._content_splitter.addWidget(self._stack)

        # Help side-panel (hidden by default; shown in place of a floating dialog)
        self._help_panel = HelpPanel(self, theme_mode=self.theme_mode)
        self._help_panel.setVisible(False)
        self._content_splitter.addWidget(self._help_panel)

        center_layout.addWidget(self._content_splitter, 1)

        # --------------------------
        # Run page
        # --------------------------
        run_container = QWidget()
        root = QVBoxLayout(run_container)
        root.setContentsMargins(16, 12, 16, 16)
        root.setSpacing(10)

        top_row = QHBoxLayout()
        top_row.addWidget(self._bold_label("Name"))
        top_row.addStretch(1)
        root.addLayout(top_row)
        root.addWidget(self.case_edit)

        row = QHBoxLayout()
        row.addWidget(self._bold_label("Log HWiNFO CSV to"))
        row.addWidget(self.hwinfo_edit, 1)
        row.addWidget(self.pick_hwinfo_btn)
        row.addSpacing(8)
        row.addWidget(self.csv_dot)
        row.addSpacing(6)
        row.addWidget(self.sm2_dot)
        root.addLayout(row)

        time_row = QHBoxLayout()
        time_row.setSpacing(18)

        stress_col = QVBoxLayout()
        stress_col.setSpacing(4)
        stress_col.addWidget(self._bold_label("Stress test"))
        stress_toggle_row = QHBoxLayout()
        stress_toggle_row.setSpacing(10)
        stress_toggle_row.addWidget(self.cpu_btn)
        stress_toggle_row.addWidget(self.gpu_btn)
        stress_toggle_row.addWidget(self.prime95_settings_btn)
        stress_toggle_row.addStretch(1)
        stress_col.addLayout(stress_toggle_row)
        stress_col.addStretch(1)

        warm_col = QVBoxLayout()
        warm_col.setSpacing(4)
        warm_col.addWidget(self._bold_label("Warmup"))
        warm_row = QHBoxLayout()
        warm_row.setSpacing(1)
        warm_row.addWidget(self.warmup_min)
        warm_row.addWidget(self._unit_label("min"))
        warm_row.addWidget(self.warmup_sec)
        warm_row.addWidget(self._unit_label("sec"))
        warm_row.addStretch(1)
        warm_col.addLayout(warm_row)
        warm_col.addStretch(1)

        log_col = QVBoxLayout()
        log_col.setSpacing(4)
        log_col.addWidget(self._bold_label("Log"))
        log_row = QHBoxLayout()
        log_row.setSpacing(1)
        log_row.addWidget(self.log_min)
        log_row.addWidget(self._unit_label("min"))
        log_row.addWidget(self.log_sec)
        log_row.addWidget(self._unit_label("sec"))
        log_row.addStretch(1)
        log_col.addLayout(log_row)
        log_col.addStretch(1)

        time_row.addLayout(stress_col, 1)
        time_row.addLayout(warm_col, 1)
        time_row.addLayout(log_col, 1)
        root.addLayout(time_row)

        fur_row = QHBoxLayout()
        fur_row.setSpacing(18)

        demo_col = QVBoxLayout()
        demo_col.setSpacing(6)
        self._fur_demo_label = self._bold_label("FurMark Demo")
        demo_col.addWidget(self._fur_demo_label)
        demo_col.addWidget(self.fur_demo_combo)

        res_col = QVBoxLayout()
        res_col.setSpacing(6)
        self._fur_res_label = self._bold_label("FurMark Resolution")
        res_col.addWidget(self._fur_res_label)
        res_col.addWidget(self.fur_res_combo)

        fur_row.addLayout(demo_col)
        fur_row.addLayout(res_col)
        root.addLayout(fur_row)

        root.addWidget(self._bold_label("Sensors to monitor"))
        sensors_row = QHBoxLayout()
        sensors_row.setSpacing(10)
        sensors_row.addWidget(self.sensors_summary, 1)
        sensors_row.addWidget(self.pick_sensors_btn)
        root.addLayout(sensors_row)

        btns = QHBoxLayout()
        btns.addWidget(self.run_btn)
        btns.addWidget(self.abort_btn)
        root.addLayout(btns)

        # Output area: Live monitor (during run) + Console (log)
        out_hdr = QHBoxLayout()
        out_hdr.setContentsMargins(0, 0, 0, 0)
        out_hdr.addWidget(self._bold_label("Output"))
        out_hdr.addStretch(1)

        def _mk_out_btn(text: str):
            b = QPushButton(text)
            b.setCursor(Qt.PointingHandCursor)
            b.setCheckable(True)
            return b

        self._output_btn_live = _mk_out_btn("Live")
        self._output_btn_console = _mk_out_btn("Console")
        self._output_btn_live.setAutoExclusive(True)
        self._output_btn_console.setAutoExclusive(True)
        self._apply_output_toggle_theme()

        out_hdr.addWidget(self._output_btn_live)
        out_hdr.addWidget(self._output_btn_console)
        out_hdr.addWidget(self.live_timer)
        root.addLayout(out_hdr)

        # Live panel: left = table, right = live graph
        live_panel = QWidget()
        live_layout = QVBoxLayout(live_panel)
        live_layout.setContentsMargins(0, 0, 0, 0)
        live_layout.setSpacing(0)

        self._live_split = QSplitter(Qt.Horizontal)
        self._live_split.addWidget(self._live_monitor)
        self._live_split.addWidget(self._live_graph)

        try:
            self._live_split.setCollapsible(0, False)
            self._live_split.setCollapsible(1, False)
        except Exception:
            pass

        # Keep this run-layout ratio (table : graph)
        self._live_split_ratio = (0.34, 0.66)  # tweak to match your screenshot exactly

        live_layout.addWidget(self._live_split, 1)

        # Stream samples from the table parser to the live graph
        try:
            self._live_monitor.sample_updated.connect(self._live_graph.on_sample)
        except Exception:
            pass

        # Stream (de)selection from the live table to the live graph
        try:
            self._live_monitor.active_columns_changed.connect(self._live_graph.set_active_columns)
        except Exception:
            pass

        self._output_stack = QStackedWidget()
        self._output_stack.addWidget(live_panel)          # index 0
        self._output_stack.addWidget(self.log)            # index 1
        self._output_stack.setCurrentIndex(1)
        self._output_btn_console.setChecked(True)

        self._output_btn_live.clicked.connect(self._show_live_output)
        self._output_btn_console.clicked.connect(self._show_console_output)

        root.addWidget(self._output_stack, 1)

        # --------------------------
        # Results page
        # --------------------------
        results_container = QWidget()
        results_layout = QHBoxLayout(results_container)
        results_layout.setContentsMargins(0, 0, 8, 0)
        results_layout.setSpacing(8)

        splitter = QSplitter(Qt.Horizontal)
        # Results page splitter: keep the folder tree around ~45% of window width by default.
        # If the user drags the splitter, we remember their ratio and preserve it on resize.
        self._results_split = splitter
        self._results_split_ratio = (0.45, 0.55)
        self._results_split_user_set = False
        # Splitter drag smoothness: avoid heavy redraws (matplotlib) on every pixel.
        try:
            splitter.setOpaqueResize(False)
        except Exception:
            pass

        self._results_split_dragging = False
        self._results_split_drag_timer = QTimer(self)
        try:
            self._results_split_drag_timer.setSingleShot(True)
            self._results_split_drag_timer.timeout.connect(self._on_results_split_drag_finished)
        except Exception:
            pass
        try:
            splitter.splitterMoved.connect(self._on_results_splitter_moved)
        except Exception:
            pass
        tree_panel = QWidget()
        tree_panel.setObjectName("ResultsTreePanel")
        tree_panel.setStyleSheet("""
            QWidget#ResultsTreePanel {
                border-right: 1px solid rgba(128, 128, 128, 0.3);
            }
        """)
        tree_panel_layout = QVBoxLayout(tree_panel)
        tree_panel_layout.setContentsMargins(0, 0, 0, 0)
        tree_panel_layout.setSpacing(0)

        tree_month_row = QHBoxLayout()
        tree_month_row.setContentsMargins(5, 6, 5, 2)
        tree_month_row.setSpacing(6)

        self._current_real_month = datetime.now().strftime("%Y-%m")
        self._month_picker_overlay_visible = False
        self._month_picker_display_year = int(datetime.now().year)
        self._month_picker_buttons: list[QPushButton] = []
        self._month_picker_stats_cache: dict[int, dict[str, dict[str, int]]] = {}

        self._runs_current_month_btn = QPushButton("Current Month")
        self._runs_current_month_btn.setCursor(Qt.PointingHandCursor)

        self._runs_month_prev_btn = QPushButton()
        self._runs_month_next_btn = QPushButton()
        self._runs_month_label = QPushButton("")
        self._runs_month_label.setCursor(Qt.PointingHandCursor)
        self._runs_month_label.setFlat(True)
        self._runs_month_label.setCheckable(False)

        self._month_prev_icon_path = Path(__file__).parent.parent / "resources" / "icons" / "left_chevron.svg"
        self._month_next_icon_path = Path(__file__).parent.parent / "resources" / "icons" / "right_chevron.svg"

        self._runs_month_prev_btn.setCursor(Qt.PointingHandCursor)
        self._runs_month_next_btn.setCursor(Qt.PointingHandCursor)

        self._refresh_month_nav_icons()

        try:
            self._runs_month_prev_btn.setIconSize(QSize(16, 16))
            self._runs_month_next_btn.setIconSize(QSize(16, 16))
            self._runs_month_prev_btn.setFixedSize(28, 28)
            self._runs_month_next_btn.setFixedSize(28, 28)
        except Exception:
            pass

        left_month_nav = QWidget()
        left_month_nav_layout = QHBoxLayout(left_month_nav)
        left_month_nav_layout.setContentsMargins(0, 0, 0, 0)
        left_month_nav_layout.setSpacing(4)
        left_month_nav_layout.addWidget(self._runs_month_prev_btn)
        left_month_nav_layout.addWidget(self._runs_month_next_btn)

        tree_month_row.addWidget(left_month_nav, 0, Qt.AlignLeft)
        tree_month_row.addStretch(1)
        tree_month_row.addWidget(self._runs_month_label, 0, Qt.AlignCenter)
        tree_month_row.addStretch(1)
        tree_month_row.addWidget(self._runs_current_month_btn, 0, Qt.AlignRight)

        tree_panel_layout.addLayout(tree_month_row)

        tree_search_row = QHBoxLayout()
        tree_search_row.setContentsMargins(5, 4, 5, 4)
        tree_search_row.setSpacing(0)
        tree_search_row.addWidget(self._runs_tree_search)
        tree_panel_layout.addLayout(tree_search_row)

        self._results_tree_stack = QStackedWidget()

        # Normal mode page: the regular current-month tree
        self._results_tree_stack.addWidget(self._runs_tree)

        # Search mode page: sectioned month results with visual separators
        self._runs_search_scroll = QScrollArea()
        self._runs_search_scroll.setWidgetResizable(True)
        self._runs_search_scroll.setFrameShape(QFrame.NoFrame)
        self._runs_search_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._runs_search_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)

        self._runs_search_content = QWidget()
        self._runs_search_layout = QVBoxLayout(self._runs_search_content)
        self._runs_search_layout.setContentsMargins(0, 0, 0, 0)
        self._runs_search_layout.setSpacing(8)
        self._runs_search_scroll.setWidget(self._runs_search_content)

        self._results_tree_stack.addWidget(self._runs_search_scroll)
        self._runs_month_label.clicked.connect(self._toggle_month_picker_overlay)

        tree_panel_layout.addWidget(self._results_tree_stack, 1)
        self._month_picker_overlay = QWidget(tree_panel)
        self._month_picker_overlay.hide()
        self._month_picker_overlay.raise_()
        self._month_picker_overlay.setObjectName("RunsMonthPickerOverlay")

        self._month_picker_overlay_layout = QVBoxLayout(self._month_picker_overlay)
        self._month_picker_overlay_layout.setContentsMargins(12, 12, 12, 12)
        self._month_picker_overlay_layout.setSpacing(10)

        self._month_picker_header = QWidget()
        self._month_picker_header_layout = QGridLayout(self._month_picker_header)
        self._month_picker_header_layout.setContentsMargins(5, 0, 5, 0)
        self._month_picker_header_layout.setHorizontalSpacing(0)
        self._month_picker_header_layout.setVerticalSpacing(0)

        self._month_picker_prev_btn = QPushButton()
        self._month_picker_next_btn = QPushButton()
        self._month_picker_current_year_btn = QPushButton("Current Year")
        self._month_picker_current_year_btn.setCursor(Qt.PointingHandCursor)
        self._month_picker_current_year_btn.clicked.connect(self._go_to_current_overlay_year)
        self._month_picker_prev_btn.setCursor(Qt.PointingHandCursor)
        self._month_picker_next_btn.setCursor(Qt.PointingHandCursor)

        try:
            self._month_picker_prev_btn.setIconSize(QSize(16, 16))
            self._month_picker_next_btn.setIconSize(QSize(16, 16))
            self._month_picker_prev_btn.setFixedSize(28, 28)
            self._month_picker_next_btn.setFixedSize(28, 28)
        except Exception:
            pass

        self._month_picker_prev_btn.setIcon(
            self._tinted_rail_icon(
                self._month_prev_icon_path,
                "#000000" if resolve_effective_theme_mode(self.theme_mode, QApplication.instance()) == "light" else "#FFFFFF"
            )
        )
        self._month_picker_next_btn.setIcon(
            self._tinted_rail_icon(
                self._month_next_icon_path,
                "#000000" if resolve_effective_theme_mode(self.theme_mode, QApplication.instance()) == "light" else "#FFFFFF"
            )
        )

        self._month_picker_prev_btn.clicked.connect(self._show_previous_results_month)
        self._month_picker_next_btn.clicked.connect(self._show_next_results_month)

        self._month_picker_nav_left = QWidget()
        self._month_picker_nav_left_layout = QHBoxLayout(self._month_picker_nav_left)
        self._month_picker_nav_left_layout.setContentsMargins(0, 0, 0, 0)
        self._month_picker_nav_left_layout.setSpacing(4)
        self._month_picker_nav_left_layout.addWidget(self._month_picker_prev_btn)
        self._month_picker_nav_left_layout.addWidget(self._month_picker_next_btn)

        # right spacer with same width as left nav, so the year stays truly centered
        self._month_picker_year_label = QLabel("")
        self._month_picker_year_label.setAlignment(Qt.AlignCenter)
        self._month_picker_year_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)

        self._month_picker_header_layout.addWidget(self._month_picker_nav_left, 0, 0, Qt.AlignLeft)
        self._month_picker_header_layout.addWidget(self._month_picker_year_label, 0, 1, Qt.AlignCenter)
        self._month_picker_header_layout.addWidget(self._month_picker_current_year_btn, 0, 2, Qt.AlignRight)

        self._month_picker_header_layout.setColumnStretch(0, 0)
        self._month_picker_header_layout.setColumnStretch(1, 1)
        self._month_picker_header_layout.setColumnStretch(2, 0)

        self._month_picker_overlay_layout.addWidget(self._month_picker_header)

        self._month_picker_scroll = QScrollArea()
        self._month_picker_scroll.setWidgetResizable(True)
        self._month_picker_scroll.setFrameShape(QFrame.NoFrame)
        self._month_picker_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._month_picker_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self._hide_month_picker_horizontal_scrollbar()

        self._month_picker_grid_host = QWidget()
        self._month_picker_grid = QGridLayout(self._month_picker_grid_host)
        self._month_picker_grid.setContentsMargins(0, 0, 0, 0)
        self._month_picker_grid.setHorizontalSpacing(10)
        self._month_picker_grid.setVerticalSpacing(10)

        self._month_picker_scroll.setWidget(self._month_picker_grid_host)
        self._hide_month_picker_horizontal_scrollbar()
        self._month_picker_overlay_layout.addWidget(self._month_picker_scroll, 1)

        tree_footer = QVBoxLayout()
        tree_footer.setContentsMargins(10, 0, 10, 5)
        tree_footer.setSpacing(4)

        # Compare-queue panel: shows which runs are currently selected for comparison.
        # Visible only when at least one run has been double-clicked; persists across
        # month navigation.
        self._compare_queue_frame = QFrame()
        self._compare_queue_frame.setObjectName("CompareQueueFrame")
        self._compare_queue_frame.setFrameShape(QFrame.NoFrame)
        self._compare_queue_frame.hide()
        _cq_layout = QVBoxLayout(self._compare_queue_frame)
        _cq_layout.setContentsMargins(8, 6, 8, 6)
        _cq_layout.setSpacing(3)

        # Header row: "Selected for comparison:" label + "Deselect all" button
        _cq_header_row = QHBoxLayout()
        _cq_header_row.setContentsMargins(0, 0, 0, 0)
        _cq_header_row.setSpacing(6)

        self._compare_queue_header_label = QLabel("Selected for comparison:")
        self._compare_queue_header_label.setObjectName("CompareQueueHeader")
        _cq_header_row.addWidget(self._compare_queue_header_label, 1)

        self._compare_queue_deselect_btn = QPushButton("Deselect all")
        self._compare_queue_deselect_btn.setObjectName("CompareQueueDeselectBtn")
        self._compare_queue_deselect_btn.setCursor(Qt.PointingHandCursor)
        self._compare_queue_deselect_btn.setFocusPolicy(Qt.NoFocus)
        _cq_header_row.addWidget(self._compare_queue_deselect_btn)
        _cq_layout.addLayout(_cq_header_row)

        # List container: rows rebuilt dynamically per selected run
        self._compare_queue_list_widget = QWidget()
        self._compare_queue_list_widget.setObjectName("CompareQueueListWidget")
        _cq_list_layout = QVBoxLayout(self._compare_queue_list_widget)
        _cq_list_layout.setContentsMargins(0, 0, 0, 0)
        _cq_list_layout.setSpacing(1)
        _cq_layout.addWidget(self._compare_queue_list_widget)

        tree_footer.addWidget(self._compare_queue_frame)
        tree_footer.addWidget(self.compare_btn)
        # Removed: bottom delete button
        tree_panel_layout.addLayout(tree_footer)

        # Wire the compare-queue display panel into the benchmark controller now
        # that the widgets exist.
        try:
            self.benchmark.set_compare_selection_panel(
                self._compare_queue_frame,
                self._compare_queue_list_widget,
                self._compare_queue_deselect_btn,
            )
        except Exception:
            pass

        splitter.addWidget(tree_panel)

        preview_widget = QScrollArea()
        preview_widget.setWidgetResizable(True)
        preview_widget.setFrameShape(QFrame.NoFrame)
        preview_widget.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        preview_widget.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self._preview_widget = preview_widget

        preview_content = QWidget()
        self._preview_content_widget = preview_content
        preview_layout = QVBoxLayout(preview_content)
        preview_layout.setContentsMargins(0, 0, 0, 0)
        preview_layout.setSpacing(0)
        preview_layout.addWidget(self._preview_label)
        preview_layout.addWidget(self.graph.get_canvas())
        preview_widget.setWidget(preview_content)
        try:
            self.graph.set_preview_scroll_area(preview_widget)
        except Exception:
            pass

        preview_footer_canvas = self.graph.get_timeline_canvas()
        self._preview_timeline_canvas = preview_footer_canvas
        preview_footer_canvas.hide()

        preview_header = QWidget()
        self._preview_header = preview_header
        preview_header.hide()
        preview_header_layout = QHBoxLayout(preview_header)
        preview_header_layout.setContentsMargins(10, 8, 10, 8)
        preview_header_layout.setSpacing(8)

        preview_header_meta = QWidget()
        self._preview_header_meta = preview_header_meta
        preview_header_meta.setMinimumWidth(0)
        preview_header_meta.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        preview_header_meta_layout = QVBoxLayout(preview_header_meta)
        preview_header_meta_layout.setContentsMargins(0, 0, 0, 0)
        preview_header_meta_layout.setSpacing(1)
        self._preview_header_title = QLabel("")
        self._preview_header_subtitle = QLabel("")
        self._preview_header_title.setMinimumWidth(0)
        self._preview_header_subtitle.setMinimumWidth(0)
        self._preview_header_title.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self._preview_header_subtitle.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self._preview_header_title.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self._preview_header_subtitle.setTextInteractionFlags(Qt.TextSelectableByMouse)
        preview_header_meta_layout.addWidget(self._preview_header_title)
        preview_header_meta_layout.addWidget(self._preview_header_subtitle)
        preview_header_layout.addWidget(preview_header_meta, 1)

        self._preview_copy_btn = QPushButton("Copy Graph")
        self._preview_zero_btn = QPushButton("AutoY")
        self._preview_delta_btn = QPushButton("T")
        self._preview_legend_btn = QPushButton("≡ Legend & stats")
        for btn in (self._preview_copy_btn, self._preview_zero_btn, self._preview_delta_btn, self._preview_legend_btn):
            btn.setCursor(Qt.PointingHandCursor)
            btn.setFocusPolicy(Qt.NoFocus)
        preview_header_layout.addWidget(self._preview_copy_btn)
        preview_header_layout.addWidget(self._preview_zero_btn)
        preview_header_layout.addWidget(self._preview_delta_btn)
        preview_header_layout.addWidget(self._preview_legend_btn)

        preview_header_separator = QFrame()
        self._preview_header_separator = preview_header_separator
        preview_header_separator.setFrameShape(QFrame.HLine)
        preview_header_separator.setFrameShadow(QFrame.Plain)
        preview_header_separator.setFixedHeight(1)
        preview_header_separator.hide()

        try:
            self.graph.set_preview_header_controls(
                header_widget=preview_header,
                separator=preview_header_separator,
                title_label=self._preview_header_title,
                subtitle_label=self._preview_header_subtitle,
                zero_btn=self._preview_zero_btn,
                delta_btn=self._preview_delta_btn,
                legend_btn=self._preview_legend_btn,
                copy_btn=self._preview_copy_btn,
            )
        except Exception:
            pass

        preview_panel = QWidget()
        self._preview_panel = preview_panel
        preview_panel_layout = QVBoxLayout(preview_panel)
        preview_panel_layout.setContentsMargins(0, 0, 0, 0)
        preview_panel_layout.setSpacing(0)
        preview_panel_layout.addWidget(preview_header, 0)
        preview_panel_layout.addWidget(preview_header_separator, 0)
        preview_panel_layout.addWidget(preview_widget, 1)
        preview_panel_layout.addWidget(preview_footer_canvas, 0)

        splitter.addWidget(preview_panel)
        self._apply_results_preview_theme()
        self._apply_results_tree_theme()

        try:
            splitter.setStretchFactor(0, 0)
            splitter.setStretchFactor(1, 1)
            splitter.setCollapsible(0, False)
            splitter.setCollapsible(1, False)
            total = self.width() or DEFAULT_W
            left = max(220, int(total * 0.45))
            right = max(400, total - left)
            splitter.setSizes([left, right])
        except Exception:
            pass

        results_layout.addWidget(splitter)

        # Add pages to stack
        self._page_run_index = self._stack.addWidget(run_container)
        self._page_results_index = self._stack.addWidget(results_container)

        # Wire rail buttons -> stack
        self._btn_run_page.clicked.connect(lambda: self._stack.setCurrentIndex(self._page_run_index))
        self._btn_results_page.clicked.connect(lambda: self._stack.setCurrentIndex(self._page_results_index))
        self._stack.currentChanged.connect(self._on_page_changed)

        # Default page
        self._btn_run_page.setChecked(True)
        self._stack.setCurrentIndex(self._page_run_index)
        self._refresh_left_rail_icons()

        self.resize(DEFAULT_W, DEFAULT_H)

        # Apply results splitter ratio once after initial layout.
        try:
            QTimer.singleShot(0, self._apply_results_split_ratio)
        except Exception:
            pass

        # Load settings and initialize state
        self.load_settings()
        self._sync_furmark_gpu_controls()
        self._refresh_prime95_torture_display()
        self.sensors.refresh_sensors_summary()
        self._update_run_button_state()

        # Connect settings change handlers
        self.case_edit.textChanged.connect(self.save_settings)
        self.case_edit.textEdited.connect(self._on_case_name_text_edited)
        self.hwinfo_edit.textChanged.connect(self.save_settings)
        self.warmup_min.valueChanged.connect(lambda *_: self.save_settings())
        self.warmup_sec.valueChanged.connect(lambda *_: self.save_settings())
        self.log_min.valueChanged.connect(lambda *_: self.save_settings())
        self.log_sec.valueChanged.connect(lambda *_: self.save_settings())
        self.fur_demo_combo.currentIndexChanged.connect(lambda *_: self.save_settings())
        self.fur_res_combo.currentIndexChanged.connect(lambda *_: self.save_settings())
        self.hwinfo_edit.textChanged.connect(lambda *_: self._update_run_button_state())

        # Update threads (kept as attributes to avoid GC)
        self._update_fetch_thread = None
        self._update_fetch_worker = None
        self._update_download_thread = None
        self._update_download_worker = None
        self._update_in_progress = False
        self._update_progress_dialog: QProgressDialog | None = None

        self._update_last_release: ReleaseInfo | None = None
        self._update_downloaded_installer: Path | None = None
        self._update_ui_set_status = None
        self._update_ui_set_button_text = None
        self._update_ui_set_button_enabled = None

        # Silent background pre-download (started as soon as an update is detected)
        self._silent_dl_thread = None
        self._silent_dl_worker = None
        self._silent_dl_bytes: int = 0
        self._silent_dl_total: int = -1
        self._silent_dl_done: bool = False

        self._search_section_trees: list[QTreeView] = []
        self._search_section_models: list[tuple[MonthGroupedRunsModel, RunsProxyModel]] = []

        self._runs_current_month_btn.clicked.connect(self._go_to_current_results_month)
        self._runs_month_prev_btn.clicked.connect(self._show_previous_results_month)
        self._runs_month_next_btn.clicked.connect(self._show_next_results_month)
        self._refresh_results_month_nav()

    def _set_update_busy(self, busy: bool) -> None:
        self._update_in_progress = bool(busy)

    def _refresh_results_month_nav(self) -> None:
        try:
            source = getattr(self, "_runs_source_model", None)
            if source is None:
                return

            months = source.available_months()
            current = source.current_month()

            overlay_mode = bool(getattr(self, "_month_picker_overlay_visible", False))

            if overlay_mode:
                year = int(getattr(self, "_month_picker_display_year", datetime.now().year))

                # Keep the clickable month-year header text unchanged while overlay is open.
                try:
                    self._runs_month_label.setText(_month_key_to_label(current))
                except Exception:
                    self._runs_month_label.setText(str(current or ""))

                available_years = self._month_picker_overlay_years()

                # Main header chevrons stay disabled while overlay is active.
                self._runs_month_prev_btn.setEnabled(False)
                self._runs_month_next_btn.setEnabled(False)

                try:
                    self._runs_current_month_btn.setEnabled(True)
                except Exception:
                    pass

                # Update overlay-local chevrons instead.
                try:
                    if year in available_years:
                        idx = available_years.index(year)
                        self._month_picker_prev_btn.setEnabled(idx < len(available_years) - 1)
                        self._month_picker_next_btn.setEnabled(idx > 0)
                    else:
                        self._month_picker_prev_btn.setEnabled(bool(available_years and year < max(available_years)))
                        self._month_picker_next_btn.setEnabled(bool(available_years and year > min(available_years)))
                except Exception:
                    pass

                try:
                    current_overlay_year = self._overlay_current_year()
                    self._month_picker_current_year_btn.setEnabled(year != current_overlay_year)
                except Exception:
                    pass

                return

            try:
                self._runs_month_label.setText(_month_key_to_label(current))
            except Exception:
                self._runs_month_label.setText(str(current or ""))

            if current in months:
                idx = months.index(current)
                self._runs_month_prev_btn.setEnabled(idx < len(months) - 1)
                self._runs_month_next_btn.setEnabled(idx > 0)
            else:
                self._runs_month_prev_btn.setEnabled(False)
                self._runs_month_next_btn.setEnabled(False)

            try:
                current_real = str(getattr(self, "_current_real_month", "") or "").strip()
                self._runs_current_month_btn.setEnabled(bool(current_real and current != current_real))
            except Exception:
                pass
        except Exception:
            pass

    def _month_picker_month_name(self, month: int) -> str:
        try:
            return _MONTHS_EN[int(month) - 1]
        except Exception:
            return str(month)

    def _month_picker_min_bubble_width(self) -> int:
        return 136

    def _month_picker_bubble_height(self, cols: int) -> int:
        try:
            if cols == 2:
                return 72

            overlay = getattr(self, "_month_picker_overlay", None)
            if overlay is None:
                return 92

            avail_h = int(overlay.height() or 0)
            if avail_h <= 0:
                return 92

            top_bottom_margins = 24
            header_h = 28
            layout_spacing = 10
            grid_v_spacing = int(self._month_picker_grid.verticalSpacing() or 10)

            usable_h = max(
                120,
                avail_h - top_bottom_margins - header_h - layout_spacing - 4
            )

            rows = (12 + max(1, cols) - 1) // max(1, cols)
            total_spacing = grid_v_spacing * max(0, rows - 1)
            cell_h = max(72, (usable_h - total_spacing) // max(1, rows))
            return max(72, min(104, cell_h))
        except Exception:
            return 72 if cols == 2 else 92

    def _month_picker_grid_content_height(self, cols: int, bubble_h: int) -> int:
        try:
            rows = (12 + max(1, cols) - 1) // max(1, cols)
            v_spacing = int(self._month_picker_grid.verticalSpacing() or 10)
            margins = self._month_picker_grid.contentsMargins()
            return (
                int(margins.top())
                + int(margins.bottom())
                + (rows * int(bubble_h))
                + (max(0, rows - 1) * v_spacing)
            )
        except Exception:
            rows = (12 + max(1, cols) - 1) // max(1, cols)
            return rows * int(bubble_h) + max(0, rows - 1) * 10

    def _month_picker_collect_year_stats(self, year: int) -> dict[str, dict[str, int]]:
        try:
            year = int(year)
            cached = self._month_picker_stats_cache.get(year)
            if cached is not None:
                return cached

            out: dict[str, dict[str, int]] = {}
            for m in range(1, 13):
                key = f"{year:04d}-{m:02d}"
                out[key] = {
                    "total": 0,
                    "single": 0,
                    "compare": 0,
                }

            runs_root = Path(getattr(self, "_runs_root", "") or "")
            if not runs_root.exists() or not runs_root.is_dir():
                self._month_picker_stats_cache[year] = out
                return out

            compare_name_re = re.compile(
                r"^.+\s(?:CPU|GPU|CPUGPU)(?:\svs\s.+\s(?:CPU|GPU|CPUGPU))+(?:\s\+\d+)?$",
                flags=re.IGNORECASE,
            )

            for case_ent in os.scandir(str(runs_root)):
                if not case_ent.is_dir():
                    continue

                for run_ent in os.scandir(case_ent.path):
                    if not run_ent.is_dir():
                        continue

                    run_dir = Path(run_ent.path)
                    run_name = str(run_dir.name or "")

                    is_single = bool(_RESULT_RUN_FOLDER_RE.match(run_name))
                    is_compare = bool(compare_name_re.match(run_name))
                    if not is_single and not is_compare:
                        continue

                    month_key = ""
                    try:
                        if hasattr(self.benchmark, "_month_key_for_run_dir"):
                            month_key = str(self.benchmark._month_key_for_run_dir(run_dir) or "").strip()
                    except Exception:
                        month_key = ""

                    if not month_key.startswith(f"{year:04d}-"):
                        continue
                    if month_key not in out:
                        continue

                    out[month_key]["total"] += 1
                    if is_compare:
                        out[month_key]["compare"] += 1
                    else:
                        out[month_key]["single"] += 1

            self._month_picker_stats_cache[year] = out
            return out
        except Exception:
            return {}

    def _invalidate_month_picker_stats_cache(self) -> None:
        try:
            self._month_picker_stats_cache.clear()
        except Exception:
            pass

    def _month_picker_cell_width(self, cols: int) -> int:
        try:
            scroll = getattr(self, "_month_picker_scroll", None)
            if scroll is not None and scroll.isVisible():
                vp = scroll.viewport()
                if vp is not None:
                    usable_w = int(vp.width() or 0)
                else:
                    usable_w = 0
            else:
                usable_w = 0

            if usable_w <= 0:
                overlay = getattr(self, "_month_picker_overlay", None)
                if overlay is None:
                    return self._month_picker_min_bubble_width()

                layout = getattr(self, "_month_picker_overlay_layout", None)
                margins = layout.contentsMargins() if layout is not None else None
                left = int(margins.left()) if margins is not None else 12
                right = int(margins.right()) if margins is not None else 12
                usable_w = max(0, int(overlay.width() or 0) - left - right)

            spacing = int(self._month_picker_grid.horizontalSpacing() or 10)

            if cols <= 0:
                cols = 1

            return max(
                self._month_picker_min_bubble_width(),
                (usable_w - max(0, cols - 1) * spacing) // cols
            )
        except Exception:
            return self._month_picker_min_bubble_width()

    def _show_previous_results_month(self) -> None:
        try:
            if bool(getattr(self, "_month_picker_overlay_visible", False)):
                self._step_month_picker_overlay_year(+1)
                return

            source = getattr(self, "_runs_source_model", None)
            if source is None:
                return

            months = source.available_months()
            current = source.current_month()
            if current not in months:
                return

            idx = months.index(current)
            if idx < len(months) - 1:
                source.set_current_month(months[idx + 1])
                try:
                    sm = self._runs_tree.selectionModel()
                    if sm is not None:
                        sm.clearSelection()
                        sm.clearCurrentIndex()
                except Exception:
                    pass
                self._refresh_results_month_nav()
        except Exception:
            pass

    def _overlay_current_year(self) -> int:
        try:
            current_real = str(getattr(self, "_current_real_month", "") or "").strip()
            if re.match(r"^\d{4}-\d{2}$", current_real):
                return int(current_real[:4])
        except Exception:
            pass
        return int(datetime.now().year)


    def _go_to_current_overlay_year(self) -> None:
        try:
            self._month_picker_display_year = self._overlay_current_year()
            self._rebuild_month_picker_overlay()
            self._refresh_results_month_nav()
        except Exception:
            pass

    def _show_next_results_month(self) -> None:
        try:
            if bool(getattr(self, "_month_picker_overlay_visible", False)):
                self._step_month_picker_overlay_year(-1)
                return

            source = getattr(self, "_runs_source_model", None)
            if source is None:
                return

            months = source.available_months()
            current = source.current_month()
            if current not in months:
                return

            idx = months.index(current)
            if idx > 0:
                source.set_current_month(months[idx - 1])
                self._refresh_results_month_nav()
        except Exception:
            pass

    def _remember_finished_result(self, result: dict | None) -> None:
        try:
            run_dir = str((result or {}).get("run_dir") or "").strip()
            if not run_dir:
                return

            self._pending_latest_result_path = run_dir

            month_key = ""
            try:
                if hasattr(self.benchmark, "_month_key_for_run_dir"):
                    month_key = str(self.benchmark._month_key_for_run_dir(Path(run_dir)) or "").strip()
            except Exception:
                month_key = ""

            self._pending_latest_result_month = month_key
        except Exception:
            pass


    def _focus_latest_finished_result(self, retries: int = 12) -> None:
        try:
            try:
                if self._runs_source_model is not None and hasattr(self._runs_source_model, "refresh"):
                    self._runs_source_model.refresh()
            except Exception:
                pass

            try:
                if self._runs_proxy is not None and hasattr(self._runs_proxy, "invalidate"):
                    self._runs_proxy.invalidate()
            except Exception:
                pass
            run_dir = str(getattr(self, "_pending_latest_result_path", "") or "").strip()
            if not run_dir:
                self.benchmark.select_latest_result()
                return

            run_path = Path(run_dir)
            if not run_path.exists() or not run_path.is_dir():
                if retries > 0:
                    QTimer.singleShot(150, lambda: self._focus_latest_finished_result(retries - 1))
                else:
                    self.benchmark.select_latest_result()
                return

            month_key = str(getattr(self, "_pending_latest_result_month", "") or "").strip()
            if month_key:
                try:
                    self._runs_source_model.set_current_month(month_key)
                except Exception:
                    pass

            try:
                self._runs_proxy.invalidate()
            except Exception:
                pass

            src_idx = None
            try:
                if hasattr(self._runs_source_model, "index_for_path"):
                    src_idx = self._runs_source_model.index_for_path(str(run_path))
            except Exception:
                src_idx = None

            if src_idx is None or not src_idx.isValid():
                if retries > 0:
                    QTimer.singleShot(150, lambda: self._focus_latest_finished_result(retries - 1))
                else:
                    self.benchmark.select_latest_result()
                return

            try:
                view_idx = self._runs_proxy.mapFromSource(src_idx) if self._runs_proxy is not None else src_idx
            except Exception:
                view_idx = src_idx

            if view_idx is None or not view_idx.isValid():
                if retries > 0:
                    QTimer.singleShot(150, lambda: self._focus_latest_finished_result(retries - 1))
                else:
                    self.benchmark.select_latest_result()
                return

            try:
                parent = view_idx.parent()
                while parent.isValid():
                    self._runs_tree.expand(parent)
                    parent = parent.parent()
            except Exception:
                pass

            try:
                sm = self._runs_tree.selectionModel()
                if sm is not None:
                    sm.setCurrentIndex(
                        view_idx,
                        QItemSelectionModel.ClearAndSelect | QItemSelectionModel.Rows,
                    )
                else:
                    self._runs_tree.setCurrentIndex(view_idx)
            except Exception:
                try:
                    self._runs_tree.setCurrentIndex(view_idx)
                except Exception:
                    pass

            try:
                self._runs_tree.scrollTo(view_idx)
            except Exception:
                pass

            try:
                handled = False
                if hasattr(self.benchmark, "activate_results_index"):
                    handled = bool(self.benchmark.activate_results_index(view_idx))
                if not handled and hasattr(self.benchmark, "_on_runs_current_changed"):
                    self.benchmark._on_runs_current_changed(view_idx, view_idx)
            except Exception:
                pass

        except Exception:
            try:
                self.benchmark.select_latest_result()
            except Exception:
                pass

    def _go_to_current_results_month(self) -> None:
        try:
            self._hide_month_picker_overlay()
        except Exception:
            pass
        try:
            source = getattr(self, "_runs_source_model", None)
            if source is None:
                return

            target_month = str(getattr(self, "_current_real_month", "") or "").strip()
            if not target_month:
                target_month = datetime.now().strftime("%Y-%m")

            source.set_current_month(target_month)

            # If search mode is active, leave search mode and return to the normal tree.
            try:
                search_text = str(self._runs_tree_search.text() or "").strip()
            except Exception:
                search_text = ""

            if search_text:
                try:
                    self._runs_tree_search.blockSignals(True)
                    self._runs_tree_search.clear()
                finally:
                    try:
                        self._runs_tree_search.blockSignals(False)
                    except Exception:
                        pass

                try:
                    self._runs_source_model.set_folder_name_filter("")
                except Exception:
                    pass

                try:
                    if getattr(self, "_runs_proxy", None) is not None:
                        self._runs_proxy.invalidate()
                except Exception:
                    pass

                try:
                    if getattr(self, "_results_tree_stack", None) is not None:
                        self._results_tree_stack.setCurrentWidget(self._runs_tree)
                except Exception:
                    pass

                try:
                    self._clear_search_results_sections()
                except Exception:
                    pass

            try:
                sm = self._runs_tree.selectionModel()
                if sm is not None:
                    sm.clearSelection()
                    sm.clearCurrentIndex()
            except Exception:
                pass

            self._refresh_results_month_nav()
        except Exception:
            pass

    def _refresh_compare_delegate_theme(self) -> None:
        try:
            if getattr(self, "_runs_tree_compare_delegate", None) is not None:
                self._runs_tree_compare_delegate.set_theme_mode(self.theme_mode)
        except Exception:
            pass

        try:
            for tree in getattr(self, "_search_section_trees", []) or []:
                delegate = tree.itemDelegate()
                if isinstance(delegate, _CompareNameDelegate):
                    delegate.set_theme_mode(self.theme_mode)
        except Exception:
            pass
    
    def _month_picker_column_count(self) -> int:
        try:
            usable_w = 0

            # Prefer the real scroll viewport width when available.
            scroll = getattr(self, "_month_picker_scroll", None)
            if scroll is not None:
                vp = scroll.viewport()
                if vp is not None:
                    usable_w = int(vp.width() or 0)

            # Fallback before first show/layout.
            if usable_w <= 0:
                overlay = getattr(self, "_month_picker_overlay", None)
                if overlay is None:
                    return 3

                layout = getattr(self, "_month_picker_overlay_layout", None)
                margins = layout.contentsMargins() if layout is not None else None
                left = int(margins.left()) if margins is not None else 12
                right = int(margins.right()) if margins is not None else 12

                usable_w = int(overlay.width() or 0) - left - right

            if usable_w <= 0:
                return 3

            spacing = int(self._month_picker_grid.horizontalSpacing() or 10)
            min_bubble_w = int(self._month_picker_min_bubble_width())

            best_cols = 1
            for cols in range(4, 0, -1):
                needed = cols * min_bubble_w + max(0, cols - 1) * spacing
                if usable_w >= needed:
                    best_cols = cols
                    break

            return max(1, min(4, best_cols))
        except Exception:
            return 3


    def _clear_month_picker_grid(self) -> None:
        try:
            while self._month_picker_grid.count():
                item = self._month_picker_grid.takeAt(0)
                w = item.widget()
                if w is not None:
                    w.deleteLater()

            # Important: clear stale column/row sizing from previous layouts
            # (e.g. when going from 4 cols -> 2 cols -> 1 col).
            for i in range(12):
                try:
                    self._month_picker_grid.setColumnMinimumWidth(i, 0)
                    self._month_picker_grid.setColumnStretch(i, 0)
                    self._month_picker_grid.setRowMinimumHeight(i, 0)
                    self._month_picker_grid.setRowStretch(i, 0)
                except Exception:
                    pass

            self._month_picker_buttons = []
        except Exception:
            pass


    def _fit_month_picker_grid_host_to_viewport(self) -> None:
        try:
            scroll = getattr(self, "_month_picker_scroll", None)
            host = getattr(self, "_month_picker_grid_host", None)
            if scroll is None or host is None:
                return

            vp = scroll.viewport()
            if vp is None:
                return

            viewport_w = max(0, int(vp.width() or 0))
            if viewport_w > 0:
                host.setFixedWidth(viewport_w)

            self._hide_month_picker_horizontal_scrollbar()
        except Exception:
            pass

    def _month_picker_is_future_month(self, month_key: str) -> bool:
        try:
            mk = str(month_key or "").strip()
            if not re.match(r"^\d{4}-\d{2}$", mk):
                return False

            current_real = str(getattr(self, "_current_real_month", "") or "").strip()
            if not re.match(r"^\d{4}-\d{2}$", current_real):
                current_real = datetime.now().strftime("%Y-%m")

            # Safe because format is YYYY-MM
            return mk > current_real
        except Exception:
            return False


    def _month_picker_is_selectable(self, month_key: str, stats: dict[str, int] | None) -> bool:
        try:
            if self._month_picker_is_future_month(month_key):
                return False
            return int((stats or {}).get("total", 0)) > 0
        except Exception:
            return False

    def _rebuild_month_picker_overlay(self) -> None:
        try:
            year = int(getattr(self, "_month_picker_display_year", datetime.now().year))
            stats_by_month = self._month_picker_collect_year_stats(year)

            self._clear_month_picker_grid()
            self._month_picker_year_label.setText(str(year))

            cols = max(1, self._month_picker_column_count())
            bubble_h = self._month_picker_bubble_height(cols)
            cell_w = self._month_picker_cell_width(cols)

            month_keys = [f"{year:04d}-{m:02d}" for m in range(1, 13)]
            for i, month_key in enumerate(month_keys):
                stats = stats_by_month.get(month_key, {"total": 0, "single": 0, "compare": 0})
                bubble = self._make_month_picker_bubble(month_key, stats, bubble_h)
                bubble.setMinimumWidth(0)
                bubble.setMaximumWidth(16777215)
                row = i // cols
                col = i % cols
                self._month_picker_grid.addWidget(bubble, row, col)
                self._month_picker_buttons.append(bubble)

            rows = (len(month_keys) + cols - 1) // cols

            for col in range(cols):
                self._month_picker_grid.setColumnStretch(col, 1)
                self._month_picker_grid.setColumnMinimumWidth(col, cell_w)

            for row in range(rows):
                self._month_picker_grid.setRowStretch(row, 0)

            content_h = self._month_picker_grid_content_height(cols, bubble_h)

            self._month_picker_scroll.setWidgetResizable(True)
            self._month_picker_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

            if cols == 2:
                self._month_picker_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
                self._month_picker_scroll.setFixedHeight(content_h)
                self._month_picker_grid_host.setFixedHeight(content_h)
            else:
                self._month_picker_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
                self._month_picker_scroll.setMinimumHeight(0)
                self._month_picker_scroll.setMaximumHeight(16777215)
                self._month_picker_grid_host.setMinimumHeight(0)
                self._month_picker_grid_host.setMaximumHeight(16777215)

            self._month_picker_grid.invalidate()
            self._month_picker_grid_host.adjustSize()
            self._month_picker_grid_host.updateGeometry()
            self._month_picker_scroll.widget().updateGeometry()
            self._month_picker_scroll.viewport().update()

            # Clamp host width to the real viewport width so it never overflows horizontally.
            self._fit_month_picker_grid_host_to_viewport()
            QTimer.singleShot(0, self._fit_month_picker_grid_host_to_viewport)
        except Exception:
            pass

    def _step_month_picker_overlay_year(self, step: int) -> None:
        try:
            years = self._month_picker_overlay_years()
            if not years:
                return

            current_year = int(getattr(self, "_month_picker_display_year", datetime.now().year))

            if current_year not in years:
                # snap to the nearest valid starting point
                self._month_picker_display_year = int(years[0])
            else:
                idx = years.index(current_year)
                new_idx = max(0, min(len(years) - 1, idx + int(step)))
                if new_idx == idx:
                    return
                self._month_picker_display_year = int(years[new_idx])

            self._rebuild_month_picker_overlay()
            self._refresh_results_month_nav()
        except Exception:
            pass

    def _month_picker_overlay_years(self) -> list[int]:
        """Years that can be browsed inside the month-picker overlay.

        Test behavior:
        - include all years that actually have results
        - also include exactly one extra year before the earliest result year
        """
        try:
            source = getattr(self, "_runs_source_model", None)
            if source is None:
                return []

            months = list(source.available_months() or [])
            years = sorted(
                {
                    int(str(m).split("-")[0])
                    for m in months
                    if re.match(r"^\d{4}-\d{2}$", str(m or ""))
                },
                reverse=True,
            )

            if years:
                extra_year = min(years) - 1
                if extra_year not in years:
                    years.append(extra_year)
                    years = sorted(years, reverse=True)

            return years
        except Exception:
            return []

    def _hide_month_picker_horizontal_scrollbar(self) -> None:
        try:
            scroll = getattr(self, "_month_picker_scroll", None)
            if scroll is None:
                return

            scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

            try:
                scroll.setStyleSheet(
                    """
                    QScrollArea {
                        border: none;
                    }
                    QScrollBar:horizontal {
                        height: 0px;
                        max-height: 0px;
                        min-height: 0px;
                        margin: 0px;
                        padding: 0px;
                        border: none;
                        background: transparent;
                    }
                    """
                )
            except Exception:
                pass

            bar = scroll.horizontalScrollBar()
            if bar is not None:
                bar.hide()
                bar.setEnabled(False)
                bar.setFixedHeight(0)
                bar.setMaximumHeight(0)
                bar.setMinimumHeight(0)
                bar.setValue(0)

            try:
                scroll.setViewportMargins(0, 0, 0, 0)
            except Exception:
                pass

            scroll.updateGeometry()
            scroll.viewport().update()
            scroll.update()
        except Exception:
            pass

    def _update_month_picker_overlay_geometry(self) -> None:
        try:
            overlay = getattr(self, "_month_picker_overlay", None)
            stack = getattr(self, "_results_tree_stack", None)
            if overlay is None or stack is None:
                return

            host_w = int(stack.width() or 0)
            host_h = int(stack.height() or 0)
            if host_w <= 0 or host_h <= 0:
                return

            overlay_w = max(240, int(host_w * 0.80))
            cols = self._month_picker_column_count()

            if cols == 2:
                bubble_h = 72
                grid_h = self._month_picker_grid_content_height(cols, bubble_h)

                overlay_layout_margins = self._month_picker_overlay_layout.contentsMargins()
                overlay_spacing = int(self._month_picker_overlay_layout.spacing() or 0)

                header_h = max(
                    28,
                    int(self._month_picker_header.sizeHint().height() or 28)
                )

                required_h = (
                    int(overlay_layout_margins.top())
                    + int(overlay_layout_margins.bottom())
                    + header_h
                    + overlay_spacing
                    + grid_h
                )

                overlay_h = min(required_h, max(220, host_h - 20))
            else:
                overlay_h = min(max(220, int(host_h * 0.82)), max(220, host_h - 20))

            x = max(0, (host_w - overlay_w) // 2)
            y = max(10, (host_h - overlay_h) // 2)

            overlay.setGeometry(x, y, overlay_w, overlay_h)
        except Exception:
            pass

    def _show_month_picker_overlay(self) -> None:
        try:
            source = getattr(self, "_runs_source_model", None)
            if source is not None and hasattr(source, "current_month"):
                current = str(source.current_month() or "").strip()
                if re.match(r"^\d{4}-\d{2}$", current):
                    self._month_picker_display_year = int(current[:4])

            self._month_picker_overlay_visible = True

            self._update_month_picker_overlay_geometry()
            self._month_picker_overlay.show()
            self._month_picker_overlay.raise_()

            def _finalize():
                try:
                    self._update_month_picker_overlay_geometry()
                    self._rebuild_month_picker_overlay()
                    self._update_month_picker_overlay_geometry()
                    self._hide_month_picker_horizontal_scrollbar()
                    self._month_picker_overlay.raise_()
                except Exception:
                    pass

            QTimer.singleShot(0, _finalize)
            self._refresh_results_month_nav()
        except Exception:
            pass

    def _hide_month_picker_overlay(self) -> None:
        try:
            self._month_picker_overlay_visible = False
            if getattr(self, "_month_picker_overlay", None) is not None:
                self._month_picker_overlay.hide()
            self._refresh_results_month_nav()
        except Exception:
            pass

    def _toggle_month_picker_overlay(self) -> None:
        try:
            if bool(getattr(self, "_month_picker_overlay_visible", False)):
                self._hide_month_picker_overlay()
            else:
                self._show_month_picker_overlay()
        except Exception:
            pass

    def _is_month_picker_overlay_target(self, obj) -> bool:
        try:
            overlay = getattr(self, "_month_picker_overlay", None)
            if overlay is None:
                return False

            if obj is overlay:
                return True

            if isinstance(obj, QWidget) and overlay.isAncestorOf(obj):
                return True
        except Exception:
            pass
        return False

    def _select_month_from_picker(self, month_key: str) -> None:
        try:
            source = getattr(self, "_runs_source_model", None)
            if source is None:
                return

            month_key = str(month_key or "").strip()
            if not re.match(r"^\d{4}-\d{2}$", month_key):
                return

            stats_by_month = self._month_picker_collect_year_stats(int(month_key[:4]))
            stats = stats_by_month.get(month_key, {"total": 0, "single": 0, "compare": 0})

            if not self._month_picker_is_selectable(month_key, stats):
                return

            source.set_current_month(month_key)
            self._hide_month_picker_overlay()
            self._refresh_results_month_nav()
        except Exception:
            pass

    def _make_month_picker_bubble(self, month_key: str, stats: dict[str, int], bubble_h: int = 92) -> QPushButton:
        btn = QPushButton()
        btn.setCheckable(False)
        btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        btn.setFixedHeight(int(bubble_h))

        try:
            title = _month_key_to_label(month_key, include_year=False)
        except Exception:
            title = str(month_key)

        total = int((stats or {}).get("total", 0))
        single = int((stats or {}).get("single", 0))
        compare = int((stats or {}).get("compare", 0))

        is_future = self._month_picker_is_future_month(month_key)
        is_selectable = self._month_picker_is_selectable(month_key, stats)

        # Future months: month name only.
        # Empty past/current months: keep stats text, but greyed out + disabled.
        if is_future:
            btn.setText(title)
        else:
            btn.setText(
                f"{title}\n"
                f"{total} tests\n"
                f"{single} single • {compare} compare"
            )

        btn.setProperty("_month_key", month_key)
        btn.clicked.connect(lambda checked=False, mk=month_key: self._select_month_from_picker(mk))

        btn.setEnabled(is_selectable)
        btn.setCursor(Qt.PointingHandCursor if is_selectable else Qt.ArrowCursor)

        try:
            current = ""
            if getattr(self, "_runs_source_model", None) is not None and hasattr(self._runs_source_model, "current_month"):
                current = str(self._runs_source_model.current_month() or "")
        except Exception:
            current = ""

        try:
            effective_mode = resolve_effective_theme_mode(self.theme_mode, QApplication.instance())
            is_current = bool(is_selectable and month_key == current)

            if effective_mode == "light":
                if is_selectable:
                    bg = "#F7F7F7" if not is_current else "#DCEBFF"
                    border = "#D8D8D8" if not is_current else "#7BA7F7"
                    hover = "#ECECEC" if not is_current else "#D2E5FF"
                    text = "#1A1A1A"
                else:
                    bg = "#F1F1F1"
                    border = "#E0E0E0"
                    hover = bg
                    text = "#A0A0A0"
            else:
                if is_selectable:
                    bg = "#1B1B1B" if not is_current else "#1F2E44"
                    border = "#343434" if not is_current else "#4D7FCC"
                    hover = "#252525" if not is_current else "#263954"
                    text = "#EAEAEA"
                else:
                    bg = "#171717"
                    border = "#2A2A2A"
                    hover = bg
                    text = "#6F6F6F"

            radius = max(18, min(28, bubble_h // 3))

            btn.setStyleSheet(
                f"""
                QPushButton {{
                    text-align: center;
                    padding: 8px 10px 12px 10px;
                    border-radius: {radius}px;
                    border: 1px solid {border};
                    background-color: {bg};
                    color: {text};
                    font-weight: 600;
                    line-height: 1.2em;
                }}
                QPushButton:hover:!disabled {{
                    background-color: {hover};
                }}
                QPushButton:disabled {{
                    border: 1px solid {border};
                    background-color: {bg};
                    color: {text};
                }}
                """
            )
        except Exception:
            pass

        return btn

    def _bind_update_ui(
        self,
        *,
        set_status,
        set_button_text,
        set_button_enabled,
    ) -> None:
        self._update_ui_set_status = set_status
        self._update_ui_set_button_text = set_button_text
        self._update_ui_set_button_enabled = set_button_enabled

    def _update_ui(
        self,
        *,
        status_text: str | None = None,
        status_level: str = "info",
        button_text: str | None = None,
        button_enabled: bool | None = None,
    ) -> None:
        """Best-effort: update Settings dialog inline status/button without hard dependency."""
        try:
            if status_text is not None and self._update_ui_set_status is not None:
                try:
                    self._update_ui_set_status(status_text, status_level)
                except Exception:
                    # Dialog likely closed/destroyed
                    self._update_ui_set_status = None

            if button_text is not None and self._update_ui_set_button_text is not None:
                try:
                    self._update_ui_set_button_text(button_text)
                except Exception:
                    self._update_ui_set_button_text = None

            if button_enabled is not None and self._update_ui_set_button_enabled is not None:
                try:
                    self._update_ui_set_button_enabled(bool(button_enabled))
                except Exception:
                    self._update_ui_set_button_enabled = None
        except Exception:
            pass

    def _show_update_progress(self, text: str) -> None:
        """Show a simple progress UI while update work runs in background threads."""
        try:
            if self._update_progress_dialog is None:
                dlg = QProgressDialog(self)
                dlg.setWindowTitle("Update")
                dlg.setCancelButton(None)
                dlg.setRange(0, 0)  # indeterminate
                dlg.setMinimumDuration(0)
                dlg.setAutoClose(False)
                dlg.setAutoReset(False)
                dlg.setWindowModality(Qt.WindowModal)
                self._update_progress_dialog = dlg

            self._update_progress_dialog.setLabelText(text)
            self._update_progress_dialog.show()
            self._update_progress_dialog.raise_()
            self._update_progress_dialog.activateWindow()
        except Exception:
            pass

    def _toggle_search_tree_item(self, index) -> None:
        try:
            sender_tree = self.sender()
            if sender_tree is None:
                return

            if sender_tree.isExpanded(index):
                sender_tree.collapse(index)
            else:
                sender_tree.expand(index)
        except Exception:
            pass


    def _on_search_tree_double_clicked(self, tree, proxy, source, idx) -> None:
        try:
            old_tree = self.benchmark._runs_tree
            old_model = self.benchmark._runs_model
            old_source = self.benchmark._runs_source_model

            self.benchmark._runs_tree = tree
            self.benchmark._runs_model = proxy
            self.benchmark._runs_source_model = source
            try:
                self.benchmark.toggle_compare_selection_for_index(idx)
            finally:
                self.benchmark._runs_tree = old_tree
                self.benchmark._runs_model = old_model
                self.benchmark._runs_source_model = old_source
        except Exception:
            pass


    def _on_search_tree_current_changed(self, tree, proxy, source, current, previous) -> None:
        try:
            old_tree = self.benchmark._runs_tree
            old_model = self.benchmark._runs_model
            old_source = self.benchmark._runs_source_model

            self.benchmark._runs_tree = tree
            self.benchmark._runs_model = proxy
            self.benchmark._runs_source_model = source
            try:
                self.benchmark._on_runs_current_changed(current, previous)
            finally:
                self.benchmark._runs_tree = old_tree
                self.benchmark._runs_model = old_model
                self.benchmark._runs_source_model = old_source
        except Exception:
            pass


    def _on_search_tree_context_menu(self, tree, proxy, source, pos) -> None:
        try:
            old_tree = self._runs_tree
            old_proxy = self._runs_proxy
            old_source = self._runs_source_model

            self._runs_tree = tree
            self._runs_proxy = proxy
            self._runs_source_model = source
            try:
                self._on_runs_tree_context_menu(pos)
            finally:
                self._runs_tree = old_tree
                self._runs_proxy = old_proxy
                self._runs_source_model = old_source
        except Exception:
            pass

    def _hide_update_progress(self) -> None:
        try:
            if self._update_progress_dialog is not None:
                self._update_progress_dialog.hide()
        except Exception:
            pass

    # ---------- rail/page switching ----------
    def _on_page_changed(self, index: int) -> None:
        try:
            self._refresh_left_rail_icons()
            if index == getattr(self, "_page_results_index", -1):
                self._apply_results_split_ratio()
                # Show header text instantly so it appears in the first
                # painted frame.  Avoid touching the canvas — that would
                # trigger a heavyweight matplotlib repaint.
                try:
                    gp = self.graph
                    has_content = bool(
                        gp._preview_csv_path
                        or getattr(gp, "_compare_mode", False)
                    )
                    # First launch: nothing plotted yet, but the prescan
                    # already cached the latest folder — derive header text
                    # from it so we can show it immediately.
                    if not has_content:
                        folder = getattr(self, "_pending_latest_result_path", None) or getattr(self.benchmark, "_latest_cached_folder", None)
                        if folder is not None:
                            from ui.graph_preview.preview_path_helpers import choose_preview_file_for_folder
                            pick = choose_preview_file_for_folder(str(folder))
                            if pick is not None:
                                from pathlib import Path as _P
                                gp._set_preview_header_path(_P(pick))
                                has_content = True
                    if has_content:
                        hw = getattr(gp, "_preview_header_widget", None)
                        if hw is not None:
                            hw.setVisible(True)
                        tl = getattr(gp, "_preview_header_title_label", None)
                        if tl is not None:
                            tl.setVisible(bool(getattr(gp, "_preview_header_title_text", "")))
                        sl = getattr(gp, "_preview_header_subtitle_label", None)
                        if sl is not None:
                            sl.setVisible(bool(getattr(gp, "_preview_header_subtitle_text", "")))
                        sep = getattr(gp, "_preview_header_separator", None)
                        if sep is not None:
                            sep.setVisible(True)
                        # Tell the sync function to keep the header
                        # visible until the canvas catches up.
                        gp._preview_header_preshow = True
                        # Show header buttons immediately so they don't
                        # lag behind the title/subtitle on first launch.
                        for _attr in (
                            "_preview_header_copy_btn",
                            "_preview_header_zero_btn",
                            "_preview_header_delta_btn",
                            "_preview_header_legend_btn",
                        ):
                            _btn = getattr(gp, _attr, None)
                            if _btn is not None:
                                _btn.setVisible(True)
                except Exception:
                    pass
                # Defer the heavier canvas relayout to the next tick.
                QTimer.singleShot(0, self._focus_latest_finished_result)
        except Exception:
            pass

    def _set_widget_dimmed(self, widget: QWidget, dimmed: bool) -> None:
        try:
            effect = widget.graphicsEffect()
            if not isinstance(effect, QGraphicsOpacityEffect):
                effect = QGraphicsOpacityEffect(widget)
                widget.setGraphicsEffect(effect)
            effect.setOpacity(0.45 if dimmed else 1.0)
        except Exception:
            pass

    def _sync_furmark_gpu_controls(self) -> None:
        try:
            cpu_enabled = bool(self.cpu_btn.isChecked())
            gpu_enabled = bool(self.gpu_btn.isChecked())

            self.prime95_settings_btn.setEnabled(cpu_enabled)
            self._set_widget_dimmed(self.prime95_settings_btn, not cpu_enabled)

            self.fur_demo_combo.setEnabled(gpu_enabled)
            self.fur_res_combo.setEnabled(gpu_enabled)

            self._set_widget_dimmed(self._fur_demo_label, not gpu_enabled)
            self._set_widget_dimmed(self.fur_demo_combo, not gpu_enabled)
            self._set_widget_dimmed(self._fur_res_label, not gpu_enabled)
            self._set_widget_dimmed(self.fur_res_combo, not gpu_enabled)
        except Exception:
            pass

    def _refresh_prime95_torture_display(self) -> None:
        try:
            snapshot = load_prime95_torture_snapshot(self.prime_exe)
            self._prime95_torture_snapshot = dict(snapshot or {})
            settings_summary = str(snapshot.get("settings_summary") or "No Prime95 torture settings found.")
            has_settings = bool((snapshot.get("settings") or {}))
            inferred = snapshot.get("inferred_preset") if isinstance(snapshot.get("inferred_preset"), dict) else {}
            preset_name = str((inferred or {}).get("preset_name") or "unknown")
            confidence = str((inferred or {}).get("confidence") or "low")
            rationale = str((inferred or {}).get("rationale") or "")

            if has_settings:
                self.prime95_settings_btn.setText("Prime95 torture settings")
                self.prime95_settings_btn.setToolTip("Click to view the captured Prime95 torture settings.")
            else:
                self.prime95_settings_btn.setText("Prime95 torture settings (no data)")
                self.prime95_settings_btn.setToolTip(settings_summary)

            preset_display = preset_name if preset_name else "unknown"
            if confidence and confidence not in {"", "low"}:
                preset_display = f"{preset_display} ({confidence})"
            self.prime95_preset_edit.setText(preset_display)

            tooltip = preset_display
            if rationale:
                tooltip = f"{preset_display}\n{rationale}"
            self.prime95_preset_edit.setToolTip(tooltip)
        except Exception:
            pass

    def _latest_prime95_txt_path(self) -> Path | None:
        roots: list[Path] = []

        try:
            exe_path = Path(str(self.prime_exe or "").strip())
            if exe_path:
                roots.append(exe_path.parent if exe_path.suffix.lower() == ".exe" else exe_path)
        except Exception:
            pass

        try:
            snap = dict(getattr(self, "_prime95_torture_snapshot", {}) or {})
            for src in (snap.get("source_files") or []):
                s = str(src or "").strip()
                if not s:
                    continue
                p = Path(s)
                roots.append(p.parent if p.suffix else p)
        except Exception:
            pass

        dedup: dict[str, Path] = {}
        for r in roots:
            try:
                dedup[str(r.resolve())] = r
            except Exception:
                dedup[str(r)] = r

        latest_path: Path | None = None
        latest_mtime = -1.0
        for root in dedup.values():
            try:
                if not root.exists():
                    continue
                for p in root.rglob("prime.txt"):
                    try:
                        mt = float(p.stat().st_mtime)
                    except Exception:
                        continue
                    if mt > latest_mtime:
                        latest_mtime = mt
                        latest_path = p
            except Exception:
                continue

        return latest_path

    def _show_prime95_torture_settings_popup(self) -> None:
        try:
            def _dialog_payload_from_snapshot(snapshot_obj: dict) -> dict[str, object]:
                settings_obj = snapshot_obj.get("settings") if isinstance(snapshot_obj.get("settings"), dict) else {}
                inferred_obj = snapshot_obj.get("inferred_preset") if isinstance(snapshot_obj.get("inferred_preset"), dict) else {}

                settings_lines_obj: list[str] = []
                weak_lines_obj: list[str] = []
                label_map_obj = {
                    "MinTortureFFT": "Min FFT size (in K)",
                    "MaxTortureFFT": "Max FFT size (in K)",
                    "TortureMem": "Memory to use (in MB)",
                    "TortureTime": "Time to run each FFT size (in minutes)",
                }
                for key in ("MinTortureFFT", "MaxTortureFFT", "TortureMem", "TortureTime"):
                    value = settings_obj.get(key)
                    text = str(value).strip() if value is not None else ""
                    if key == "TortureMem" and text == "8":
                        # Prime95 may persist 8 as an internal sentinel for GUI value 0.
                        text = "0"
                    if text:
                        settings_lines_obj.append(f"{label_map_obj[key]}: {text}")

                run_in_place = str(settings_obj.get("RunFFTsInPlace") or "").strip()
                torture_mem_raw = str(settings_obj.get("TortureMem") or "").strip()
                in_place_sentinel = False
                try:
                    in_place_sentinel = int(torture_mem_raw) == 8
                except Exception:
                    in_place_sentinel = False
                if run_in_place or in_place_sentinel:
                    settings_lines_obj.append(
                        f"Run FFTs in-place: {'true' if run_in_place not in {'', '0', 'False', 'false'} or in_place_sentinel else run_in_place}"
                    )

                weak_raw = settings_obj.get("TortureWeak")
                weak_value = None
                try:
                    weak_value = int(str(weak_raw).strip())
                except Exception:
                    weak_value = None

                if weak_value is not None:
                    # Prime95 stores weak-mode checkboxes in a bitmask.
                    avx512_on = bool(weak_value & 0x100000)
                    avx2_on = bool(weak_value & 0x8000)
                    avx_on = bool(weak_value & 0x4000)
                    sse2_on = bool(weak_value & 0x0200)
                    weaker_on = bool(weak_value != 0)

                    def _bool_text(v: bool) -> str:
                        return "true" if v else "false"

                    weak_lines_obj.append(f"Run a Weaker Torture Test (not recommended): {_bool_text(weaker_on)}")
                    weak_lines_obj.append(f"Disable AVX-512: {_bool_text(avx512_on)}")
                    weak_lines_obj.append(f"Disable AVX2 (fused multiply-add): {_bool_text(avx2_on)}")
                    weak_lines_obj.append(f"Disable AVX: {_bool_text(avx_on)}")
                    weak_lines_obj.append(f"Disable SSE2: {_bool_text(sse2_on)}")
                    weak_lines_obj.append(f"TortureWeak raw value: {weak_value}")
                elif weak_raw is not None:
                    weak_lines_obj.append(f"TortureWeak: {weak_raw}")

                return {
                    "settings_lines": settings_lines_obj,
                    "weak_lines": weak_lines_obj,
                    "preset_name": str((inferred_obj or {}).get("preset_name") or "unknown"),
                    "confidence": str((inferred_obj or {}).get("confidence") or "low"),
                    "rationale": str((inferred_obj or {}).get("rationale") or "").strip(),
                }

            latest_prime_txt = self._latest_prime95_txt_path()
            if latest_prime_txt is not None:
                snapshot = load_prime95_torture_snapshot(latest_prime_txt)
            else:
                snapshot = dict(getattr(self, "_prime95_torture_snapshot", {}) or {})
                if not snapshot:
                    snapshot = load_prime95_torture_snapshot(self.prime_exe)

            self._prime95_torture_snapshot = dict(snapshot or {})

            settings = snapshot.get("settings") if isinstance(snapshot.get("settings"), dict) else {}
            inferred = snapshot.get("inferred_preset") if isinstance(snapshot.get("inferred_preset"), dict) else {}

            if not settings:
                QMessageBox.information(self, "Prime95 torture settings", "No Prime95 torture settings found.")
                return

            payload = _dialog_payload_from_snapshot(snapshot)

            def _refresh_payload() -> dict[str, object]:
                fresh_txt = self._latest_prime95_txt_path()
                fresh_snapshot = load_prime95_torture_snapshot(fresh_txt if fresh_txt is not None else self.prime_exe)
                self._prime95_torture_snapshot = dict(fresh_snapshot or {})
                return _dialog_payload_from_snapshot(fresh_snapshot)
            dlg = Prime95SettingsDialog(
                self,
                settings_lines=list(payload.get("settings_lines") or []),
                weak_lines=list(payload.get("weak_lines") or []),
                preset_name=str(payload.get("preset_name") or "unknown"),
                confidence=str(payload.get("confidence") or "low"),
                rationale=str(payload.get("rationale") or ""),
                prime_exe=self.prime_exe,
                refresh_payload=_refresh_payload,
                theme_mode=self.theme_mode,
            )
            dlg.exec()
        except Exception:
            pass

    def _apply_left_rail_theme(self) -> None:
        try:
            effective_mode = resolve_effective_theme_mode(self.theme_mode, QApplication.instance())
            if effective_mode == "light":
                hover_bg = "rgba(0,0,0,0.06)"
                checked_bg = "rgba(0,0,0,0.12)"
            else:
                hover_bg = "rgba(255,255,255,0.06)"
                checked_bg = "rgba(255,255,255,0.10)"

            rail_style = f"""
            QWidget {{
                border-right: 1px solid rgba(128, 128, 128, 0.3);
            }}
            QToolButton {{
                border: none;
                border-radius: 0px;
                font-size: 18px;
                color: #D0D0D0;
                background: transparent;
            }}
            QToolButton:hover {{
                background: {hover_bg};
            }}
            QToolButton:checked {{
                background: {checked_bg};
            }}
            """
            self._nav.setStyleSheet(rail_style)
        except Exception:
            pass

    def _apply_results_tree_theme(self) -> None:
        try:
            effective_mode = resolve_effective_theme_mode(self.theme_mode, QApplication.instance())
            if effective_mode == "light":
                tree_text = "#000000"
                selection_text = "#0F172A"
                selection_bg = "#BFD8FF"
                hover_bg = "#ECECEC"
                placeholder_text = "#5B6472"
                search_bg = "#FFFFFF"
                search_border = "#B8C0CC"
                search_focus = "#2F6FEB"
            else:
                tree_text = "#B0B0B0"
                selection_text = tree_text
                selection_bg = "#2A2A2A"
                hover_bg = "#242424"
                placeholder_text = "#7F7F7F"
                search_bg = "#171717"
                search_border = "rgba(128, 128, 128, 0.45)"
                search_focus = "#5B9BFF"

            try:
                pal = self._runs_tree.palette()
                text_color = QColor(tree_text)
                selected_text_color = QColor(selection_text)
                selected_bg_color = QColor(selection_bg)
                placeholder_color = QColor(placeholder_text)

                for group in (QPalette.Active, QPalette.Inactive):
                    pal.setColor(group, QPalette.Text, text_color)
                    pal.setColor(group, QPalette.WindowText, text_color)
                    pal.setColor(group, QPalette.Highlight, selected_bg_color)
                    pal.setColor(group, QPalette.HighlightedText, selected_text_color)
                    pal.setColor(group, QPalette.PlaceholderText, placeholder_color)

                self._runs_tree.setPalette(pal)
                self._runs_tree.viewport().setPalette(pal)
            except Exception:
                pass

            self._runs_tree.setStyleSheet(
                f"""
                QTreeView {{
                    border: none;
                    border-right: 1px solid rgba(128, 128, 128, 0.3);
                    border-radius: 0px;
                    color: {tree_text};
                }}
                QTreeView::item:selected {{
                    background-color: {selection_bg};
                    color: {selection_text};
                    outline: none;
                    border: none;
                }}
                QTreeView::item:selected:active {{
                    background-color: {selection_bg};
                    color: {selection_text};
                }}
                QTreeView::item:selected:!active {{
                    background-color: {selection_bg};
                    color: {selection_text};
                }}
                QTreeView::item:hover {{
                    background-color: {hover_bg};
                }}
                QTreeView::item:focus {{
                    outline: none;
                    border: none;
                }}
                """
            )

            try:
                self._runs_tree_search.setStyleSheet(
                    f"""
                    QLineEdit {{
                        background-color: {search_bg};
                        color: {tree_text};
                        border: 1px solid {search_border};
                        border-radius: 8px;
                        padding: 2px 8px;
                    }}
                    QLineEdit:focus {{
                        border: 1px solid {search_focus};
                    }}
                    """
                )
            except Exception:
                pass

            try:
                nav_btn_css = f"""
                QPushButton {{
                    background-color: {search_bg};
                    color: {tree_text};
                    border: 1px solid {search_border};
                    border-radius: 8px;
                    padding: 4px 10px;
                }}
                QPushButton:hover {{
                    background-color: {hover_bg};
                }}
                QPushButton:disabled {{
                    color: {placeholder_text};
                }}
                """

                chevron_btn_css = f"""
                QPushButton {{
                    background: transparent;
                    border: none;
                    border-radius: 10px;
                    padding: 0px;
                }}
                QPushButton:hover {{
                    background-color: {hover_bg};
                }}
                QPushButton:pressed {{
                    background-color: {hover_bg};
                }}
                QPushButton:disabled {{
                    background: transparent;
                    border: none;
                }}
                """

                self._runs_current_month_btn.setStyleSheet(nav_btn_css)
                self._month_picker_current_year_btn.setStyleSheet(nav_btn_css)
                self._runs_month_prev_btn.setStyleSheet(chevron_btn_css)
                self._runs_month_next_btn.setStyleSheet(chevron_btn_css)
            except Exception:
                pass

            try:
                self._month_picker_prev_btn.setStyleSheet(chevron_btn_css)
                self._month_picker_next_btn.setStyleSheet(chevron_btn_css)
            except Exception:
                pass

            try:
                overlay_bg = "rgba(255,255,255,0.96)" if effective_mode == "light" else "rgba(20,20,20,0.96)"
                overlay_border = "rgba(0,0,0,0.10)" if effective_mode == "light" else "rgba(255,255,255,0.10)"
                inner_bg = "#FFFFFF" if effective_mode == "light" else "#141414"

                overlay_css = f"""
                QWidget#RunsMonthPickerOverlay {{
                    background-color: {overlay_bg};
                    border: 1px solid {overlay_border};
                    border-radius: 14px;
                }}
                """
                self._month_picker_overlay.setStyleSheet(overlay_css)

                self._month_picker_grid_host.setStyleSheet(
                    f"background-color: {inner_bg}; border: none;"
                )

                try:
                    self._month_picker_scroll.setStyleSheet(
                        f"""
                        QScrollArea {{
                            background-color: {inner_bg};
                            border: none;
                        }}
                        QScrollArea > QWidget > QWidget {{
                            background-color: {inner_bg};
                            border: none;
                        }}
                        QScrollBar:horizontal {{
                            height: 0px;
                            max-height: 0px;
                            min-height: 0px;
                            margin: 0px;
                            padding: 0px;
                            border: none;
                            background: transparent;
                        }}
                        """
                    )
                except Exception:
                    pass

                self._month_picker_header.setStyleSheet("background: transparent; border: none;")
                self._month_picker_nav_left.setStyleSheet("background: transparent; border: none;")
                # REMOVE this line unless you actually create self._month_picker_nav_right
                # self._month_picker_nav_right.setStyleSheet("background: transparent; border: none;")

                self._month_picker_year_label.setStyleSheet(
                    f"""
                    QLabel {{
                        color: {tree_text};
                        font-weight: 700;
                        font-size: 15px;
                        background: transparent;
                        border: none;
                        margin: 0px;
                        padding: 0px;
                    }}
                    """
                )

                self._runs_month_label.setObjectName("RunsMonthLabel")
                self._runs_month_label.setFlat(True)
                self._runs_month_label.setAutoDefault(False)
                self._runs_month_label.setDefault(False)
                self._runs_month_label.setStyleSheet(
                    f"""
                    QPushButton#RunsMonthLabel {{
                        background: transparent;
                        border: none;
                        padding: 2px 8px;
                        margin: 0px;
                        color: {tree_text};
                        font-weight: 700;
                    }}
                    QPushButton#RunsMonthLabel:hover {{
                        background-color: {hover_bg};
                        border: none;
                        border-radius: 8px;
                    }}
                    QPushButton#RunsMonthLabel:pressed {{
                        background-color: {hover_bg};
                        border: none;
                    }}
                    """
                )
            except Exception as e:
                print("month nav styling failed:", e)
            except Exception:
                pass

            try:
                self._runs_tree.viewport().update()
                self._runs_tree.update()
            except Exception:
                pass

            try:
                if effective_mode == "light":
                    queue_bg = "rgba(0, 0, 0, 0.05)"
                    queue_border = "rgba(0, 0, 0, 0.12)"
                    queue_header_color = "#5B6472"
                    queue_text_color = "#1A1A1A"
                else:
                    queue_bg = "rgba(255, 255, 255, 0.05)"
                    queue_border = "rgba(255, 255, 255, 0.12)"
                    queue_header_color = "#7F7F7F"
                    queue_text_color = "#C8C8C8"

                self._compare_queue_frame.setStyleSheet(
                    f"""
                    QFrame#CompareQueueFrame {{
                        background-color: {queue_bg};
                        border: 1px solid {queue_border};
                        border-radius: 6px;
                    }}
                    QWidget#CompareQueueListWidget, QWidget#CompareQueueRow {{
                        background: transparent;
                        border: none;
                    }}
                    QLabel#CompareQueueHeader {{
                        color: {queue_header_color};
                        font-size: 11px;
                        font-weight: 600;
                        background: transparent;
                        border: none;
                    }}
                    QLabel#CompareQueueItemLabel {{
                        color: {queue_text_color};
                        font-size: 11px;
                        background: transparent;
                        border: none;
                    }}
                    QLabel#CompareQueueItemDate {{
                        color: {queue_header_color};
                        font-size: 10px;
                        background: transparent;
                        border: none;
                    }}
                    QPushButton#CompareQueueDeselectBtn {{
                        color: {queue_header_color};
                        font-size: 10px;
                        font-weight: 500;
                        background: transparent;
                        border: none;
                        padding: 0px 2px;
                    }}
                    QPushButton#CompareQueueDeselectBtn:hover {{
                        color: {queue_text_color};
                    }}
                    """
                )
            except Exception:
                pass

            try:
                self._apply_results_preview_theme()
            except Exception:
                pass

            try:
                tt = getattr(self, "_runs_tree_tooltip", None)
                if tt is not None:
                    tt.setStyleSheet(self._runs_tree_tooltip_stylesheet())
                    tt.setFont(self._runs_tree_tooltip_font())
            except Exception:
                pass
        except Exception:
            pass
    
    def _on_search_tree_clicked(self, tree, proxy, source, index) -> None:
        try:
            btn = tree.property("_tb_last_button")
            if btn is not None and int(btn) == int(Qt.RightButton):
                return
        except Exception:
            pass

        try:
            old_tree = self.benchmark._runs_tree
            old_model = self.benchmark._runs_model
            old_source = self.benchmark._runs_source_model

            self.benchmark._runs_tree = tree
            self.benchmark._runs_model = proxy
            self.benchmark._runs_source_model = source
            try:
                handled = False
                if hasattr(self.benchmark, "activate_results_index"):
                    handled = bool(self.benchmark.activate_results_index(index))
                if not handled:
                    if tree.isExpanded(index):
                        tree.collapse(index)
                    else:
                        tree.expand(index)
            finally:
                self.benchmark._runs_tree = old_tree
                self.benchmark._runs_model = old_model
                self.benchmark._runs_source_model = old_source
        except Exception:
            pass

    def _runs_tree_tooltip_stylesheet(self) -> str:
        try:
            effective_mode = resolve_effective_theme_mode(self.theme_mode, QApplication.instance())
        except Exception:
            effective_mode = "dark"

        if effective_mode == "light":
            bg = "rgba(255,255,255,235)"
            border = "rgba(0,0,0,35)"
            text = "#1A1A1A"
        else:
            bg = "rgba(24,24,24,160)"
            border = "rgba(255,255,255,18)"
            text = "#FFFFFF"

        return (
            "QLabel {"
            f" background-color: {bg};"
            f" border: 1px solid {border};"
            " border-radius: 6px;"
            " padding: 3px 10px;"
            f" color: {text};"
            "}"
        )

    def _runs_tree_tooltip_font(self):
        try:
            src = getattr(self, "_preview_header_title", None)
            if src is not None:
                return src.font()
        except Exception:
            pass
        try:
            return self._runs_tree.font()
        except Exception:
            return self.font()

    @staticmethod
    def _html_escape(text: str) -> str:
        try:
            return html.escape(str(text or ""), quote=True)
        except Exception:
            return str(text or "")

    def _runs_tree_tooltip_text_color(self) -> str:
        try:
            effective_mode = resolve_effective_theme_mode(self.theme_mode, QApplication.instance())
            return "#1A1A1A" if effective_mode == "light" else "#FFFFFF"
        except Exception:
            return "#FFFFFF"

    def _runs_tree_compare_tooltip_html(self, text: str, compare_prefix: str, seg_colors: list) -> str:
        try:
            full_text = str(text or "")
            prefix = str(compare_prefix or "")
            body_text = full_text[len(prefix):] if prefix and full_text.startswith(prefix) else full_text

            parts: list[str] = []
            for p in body_text.split(" vs "):
                cleaned = re.sub(r"\s+", " ", str(p or "")).strip()
                if cleaned:
                    parts.append(cleaned)

            if not parts:
                return ""

            base = self._runs_tree_tooltip_text_color()

            rows: list[str] = []
            for idx, part in enumerate(parts):
                color = base
                try:
                    current = seg_colors[idx] if idx < len(seg_colors) else None
                    if hasattr(current, "name"):
                        color = str(current.name() or base)
                except Exception:
                    pass

                safe_part = self._html_escape(part).replace(" ", "&nbsp;")

                suffix = ""
                if idx != len(parts) - 1:
                    suffix = f"&nbsp;<span style='color:{base};'>↔</span>"

                rows.append(
                    f"<span style='color:{color};'>{safe_part}</span>{suffix}"
                )

            return (
                "<span style='white-space:nowrap; margin:0; padding:0;'>"
                + "<br/>".join(rows)
                + "</span>"
            )
        except Exception:
            return ""

    def _ensure_runs_tree_tooltip(self) -> QLabel | None:
        try:
            tt = getattr(self, "_runs_tree_tooltip", None)
            if tt is not None:
                return tt

            tt = QLabel(self)
            tt.setObjectName("RunsTreeTooltip")
            tt.setAttribute(Qt.WA_TransparentForMouseEvents, True)
            tt.setTextFormat(Qt.RichText)
            tt.setWordWrap(True)
            tt.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
            tt.setStyleSheet(self._runs_tree_tooltip_stylesheet())
            tt.setFont(self._runs_tree_tooltip_font())
            tt.hide()
            self._runs_tree_tooltip = tt
            return tt
        except Exception:
            return None

    def _hide_runs_tree_tooltip(self) -> None:
        try:
            tt = getattr(self, "_runs_tree_tooltip", None)
            if tt is not None:
                tt.hide()
        except Exception:
            pass

    def _show_runs_tree_tooltip(self, tree: QTreeView, text: str, *, viewport_pos, item_rect, rich_html: str = "") -> None:
        try:
            if tree is None:
                return

            tt = self._ensure_runs_tree_tooltip()
            if tt is None:
                return

            tt.setStyleSheet(self._runs_tree_tooltip_stylesheet())
            tt.setFont(self._runs_tree_tooltip_font())

            markup = str(rich_html or "").strip()
            is_rich_compare = bool(markup)

            if not markup:
                markup = f"<div style='white-space:pre-wrap;'>{self._html_escape(text)}</div>"

            tt.setText(markup)

            host_w = max(0, int(self.width() or 0))
            host_h = max(0, int(self.height() or 0))
            margin = 8
            max_w = max(160, host_w - (margin * 2))

            if is_rich_compare:
                tt.setMaximumWidth(16777215)
                tt.setWordWrap(False)
                tt.adjustSize()
            else:
                tt.setMaximumWidth(max_w)
                tt.setWordWrap(False)
                tt.adjustSize()
                natural_w = int(tt.sizeHint().width() or tt.width() or 0)
                if natural_w > max_w:
                    tt.setWordWrap(True)
                    tt.resize(max_w, 1)
                    tt.adjustSize()
                else:
                    tt.setWordWrap(False)
                    tt.adjustSize()

            try:
                if item_rect is not None and item_rect.isValid():
                    anchor_top_left = tree.viewport().mapTo(self, item_rect.topLeft())
                    anchor_bottom_left = tree.viewport().mapTo(self, item_rect.bottomLeft())
                else:
                    anchor_top_left = tree.viewport().mapTo(self, viewport_pos)
                    anchor_bottom_left = anchor_top_left
            except Exception:
                anchor_top_left = tree.viewport().mapTo(self, viewport_pos)
                anchor_bottom_left = anchor_top_left

            x = int(anchor_top_left.x())
            y = int(anchor_bottom_left.y()) + 6

            try:
                hover_anchor = tree.viewport().mapTo(self, viewport_pos)
                x = int(hover_anchor.x()) - 18
            except Exception:
                pass

            max_x = max(margin, host_w - margin - tt.width())
            x = max(margin, min(x, max_x))

            max_y = max(margin, host_h - margin - tt.height())
            if y > max_y:
                above_y = int(anchor_top_left.y()) - tt.height() - 6
                if above_y >= margin:
                    y = above_y
                else:
                    y = max_y
            y = max(margin, min(y, max_y))

            tt.move(x, y)
            tt.raise_()
            tt.show()
        except Exception:
            self._hide_runs_tree_tooltip()

    def _apply_results_preview_theme(self) -> None:
        try:
            preview_widget = getattr(self, "_preview_widget", None)
            preview_content = getattr(self, "_preview_content_widget", None)
            preview_panel = getattr(self, "_preview_panel", None)
            if preview_widget is None:
                return

            effective_mode = resolve_effective_theme_mode(self.theme_mode, QApplication.instance())
            if effective_mode == "light":
                bg = "#FFFFFF"
            else:
                bg = "#121212"

            preview_widget.setStyleSheet(
                f"QScrollArea {{ background: {bg}; border: none; margin: 0px; padding: 0px; }}"
            )
            if preview_content is not None:
                preview_content.setStyleSheet(
                    f"QWidget {{ background: {bg}; border: none; margin: 0px; padding: 0px; }}"
                )
            if preview_panel is not None:
                preview_panel.setStyleSheet(
                    f"QWidget {{ background: {bg}; border: none; margin: 0px; padding: 0px; }}"
                )
        except Exception:
            pass

    def _on_runs_tree_search_changed(self, text: str) -> None:
        try:
            search_text = str(text or "").strip()
            if search_text and bool(getattr(self, "_month_picker_overlay_visible", False)):
                self._hide_month_picker_overlay()

            if search_text:
                self._rebuild_search_results_sections(search_text)
                if getattr(self, "_results_tree_stack", None) is not None:
                    self._results_tree_stack.setCurrentWidget(self._runs_search_scroll)
            else:
                try:
                    if getattr(self, "_runs_source_model", None) is not None:
                        self._runs_source_model.set_folder_name_filter("")
                except Exception:
                    pass

                try:
                    if getattr(self, "_runs_proxy", None) is not None:
                        self._runs_proxy.invalidate()
                except Exception:
                    pass

                if getattr(self, "_results_tree_stack", None) is not None:
                    self._results_tree_stack.setCurrentWidget(self._runs_tree)

                try:
                    self._clear_search_results_sections()
                except Exception:
                    pass
        except Exception:
            pass

    def _clear_search_results_sections(self) -> None:
        try:
            layout = getattr(self, "_runs_search_layout", None)
            if layout is None:
                return

            while layout.count():
                item = layout.takeAt(0)
                widget = item.widget()
                child_layout = item.layout()
                if widget is not None:
                    try:
                        widget.deleteLater()
                    except Exception:
                        pass
                elif child_layout is not None:
                    try:
                        while child_layout.count():
                            sub = child_layout.takeAt(0)
                            w = sub.widget()
                            if w is not None:
                                w.deleteLater()
                    except Exception:
                        pass

            self._search_section_trees = []
            self._search_section_models = []
        except Exception:
            pass


    def _search_month_keys_with_matches(self, search_text: str) -> list[str]:
        try:
            runs_root = Path(getattr(self, "_runs_root", "") or "")
            if not runs_root.exists() or not runs_root.is_dir():
                return []

            grouped: dict[str, bool] = {}

            for case_ent in os.scandir(str(runs_root)):
                if not case_ent.is_dir():
                    continue

                case_name = str(case_ent.name or "")
                for run_ent in os.scandir(case_ent.path):
                    if not run_ent.is_dir():
                        continue

                    run_dir = Path(run_ent.path)
                    run_name = run_dir.name
                    if not _RESULT_RUN_FOLDER_RE.match(run_name) and not re.match(
                        r"^.+\s(?:CPU|GPU|CPUGPU)(?:\svs\s.+\s(?:CPU|GPU|CPUGPU))+(?:\s\+\d+)?$",
                        run_name,
                        flags=re.IGNORECASE,
                    ):
                        continue

                    hay = f"{case_name} {run_name}".casefold()
                    if str(search_text or "").strip().casefold() not in hay:
                        continue

                    month_key = ""
                    try:
                        if hasattr(self.benchmark, "_month_key_for_run_dir"):
                            month_key = str(self.benchmark._month_key_for_run_dir(run_dir) or "").strip()
                    except Exception:
                        month_key = ""

                    if month_key:
                        grouped[month_key] = True

            months = sorted(grouped.keys(), reverse=True)

            current = ""
            try:
                if getattr(self, "_runs_source_model", None) is not None and hasattr(self._runs_source_model, "current_month"):
                    current = str(self._runs_source_model.current_month() or "").strip()
            except Exception:
                current = ""

            if current and current in months:
                months.remove(current)
                months.insert(0, current)

            return months
        except Exception:
            return []


    def _make_search_month_header(self, month_key: str) -> QWidget:
        header = QFrame()
        header.setObjectName("RunsSearchMonthHeader")

        layout = QHBoxLayout(header)
        layout.setContentsMargins(10, 3, 10, 3)
        layout.setSpacing(0)

        label = QLabel("")
        label.setObjectName("RunsSearchMonthHeaderLabel")
        try:
            label.setText(_month_key_to_label(month_key))
        except Exception:
            label.setText(str(month_key or ""))

        try:
            f = label.font()
            f.setBold(True)
            label.setFont(f)
        except Exception:
            pass

        layout.addWidget(label)

        try:
            effective_mode = resolve_effective_theme_mode(self.theme_mode, QApplication.instance())
            if effective_mode == "light":
                header.setStyleSheet("""
                    QFrame#RunsSearchMonthHeader {
                        border: none;
                        border-top: 1px solid rgba(0,0,0,0.16);
                        border-bottom: 1px solid rgba(0,0,0,0.10);
                        background-color: rgba(0,0,0,0.03);
                    }
                    QLabel#RunsSearchMonthHeaderLabel {
                        border: none;
                        background-color: transparent;
                        color: #1A1A1A;
                    }
                """)
            else:
                header.setStyleSheet("""
                    QFrame#RunsSearchMonthHeader {
                        border: none;
                        border-top: 1px solid rgba(255,255,255,0.14);
                        border-bottom: 1px solid rgba(255,255,255,0.08);
                        background-color: rgba(255,255,255,0.03);
                    }
                    QLabel#RunsSearchMonthHeaderLabel {
                        border: none;
                        background-color: transparent;
                        color: #EAEAEA;
                    }
                """)
        except Exception:
            pass

        return header


    def _make_search_month_tree(self, month_key: str, search_text: str) -> QTreeView | None:
        try:
            source = MonthGroupedRunsModel(self._runs_root, self)
            source.set_current_month(month_key)
            source.set_folder_name_filter(search_text)

            proxy = RunsProxyModel(self)
            proxy.setSourceModel(source)

            tree = QTreeView()
            tree.setHeaderHidden(True)
            tree.setModel(proxy)

            try:
                tree.setSelectionMode(QAbstractItemView.ExtendedSelection)
                tree.setSelectionBehavior(QAbstractItemView.SelectRows)
                tree.setExpandsOnDoubleClick(False)
            except Exception:
                pass

            try:
                for c in range(1, 4):
                    tree.hideColumn(c)
            except Exception:
                pass

            try:
                tree.setSortingEnabled(True)
                tree.sortByColumn(0, Qt.AscendingOrder)
            except Exception:
                pass

            try:
                tree.clicked.connect(lambda idx, t=tree, p=proxy, s=source: self._on_search_tree_clicked(t, p, s, idx))
            except Exception:
                pass

            try:
                tree.doubleClicked.connect(lambda idx, t=tree, p=proxy, s=source: self._on_search_tree_double_clicked(t, p, s, idx))
            except Exception:
                pass

            try:
                tree.selectionModel().currentChanged.connect(
                    lambda current, previous, t=tree, p=proxy, s=source: self._on_search_tree_current_changed(t, p, s, current, previous)
                )
            except Exception:
                pass

            try:
                tree.selectionModel().selectionChanged.connect(
                    lambda selected, deselected: self.benchmark._update_compare_btn_state()
                )
            except Exception:
                pass

            try:
                tree.setContextMenuPolicy(Qt.CustomContextMenu)
                tree.customContextMenuRequested.connect(lambda pos, t=tree, p=proxy, s=source: self._on_search_tree_context_menu(t, p, s, pos))
            except Exception:
                pass

            try:
                tree.viewport().installEventFilter(self)
            except Exception:
                pass

            try:
                tree.setProperty("_tb_last_button", int(Qt.LeftButton))
            except Exception:
                pass

            try:
                tree.setItemDelegate(_CompareNameDelegate(tree, theme_mode=self.theme_mode))
            except Exception:
                pass

            try:
                effective_mode = resolve_effective_theme_mode(self.theme_mode, QApplication.instance())
                if effective_mode == "light":
                    tree_text = "#000000"
                    selection_text = "#0F172A"
                    selection_bg = "#BFD8FF"
                    hover_bg = "#ECECEC"
                else:
                    tree_text = "#B0B0B0"
                    selection_text = tree_text
                    selection_bg = "#2A2A2A"
                    hover_bg = "#242424"

                tree.setStyleSheet(
                    f"""
                    QTreeView {{
                        border: none;
                        color: {tree_text};
                        background: transparent;
                    }}
                    QTreeView::item:selected {{
                        background-color: {selection_bg};
                        color: {selection_text};
                        outline: none;
                        border: none;
                    }}
                    QTreeView::item:hover {{
                        background-color: {hover_bg};
                    }}
                    QTreeView::item:focus {{
                        outline: none;
                        border: none;
                    }}
                    """
                )
            except Exception:
                pass

            self._search_section_trees.append(tree)
            self._search_section_models.append((source, proxy))
            return tree
        except Exception:
            return None


    def _rebuild_search_results_sections(self, search_text: str) -> None:
        try:
            self._clear_search_results_sections()

            layout = getattr(self, "_runs_search_layout", None)
            if layout is None:
                return

            months = self._search_month_keys_with_matches(search_text)
            if not months:
                empty = QLabel("No matching folders")
                try:
                    empty.setAlignment(Qt.AlignCenter)
                except Exception:
                    pass
                layout.addWidget(empty)
                layout.addStretch(1)
                return

            for month_key in months:
                header = self._make_search_month_header(month_key)
                layout.addWidget(header)

                tree = self._make_search_month_tree(month_key, search_text)
                if tree is None:
                    continue

                try:
                    rows = 0
                    try:
                        model = tree.model()
                        if model is not None:
                            rows = model.rowCount()
                    except Exception:
                        rows = 6

                    row_h = tree.sizeHintForRow(0)
                    if row_h <= 0:
                        row_h = 22

                    tree.setMinimumHeight(max(80, min(420, row_h * max(3, rows) + 8)))
                except Exception:
                    tree.setMinimumHeight(120)

                layout.addWidget(tree)

            layout.addStretch(1)
        except Exception:
            pass

    def _normalize_runs_tree_path(self, path: str) -> str:
        try:
            return os.path.normcase(os.path.abspath(str(path or "")))
        except Exception:
            return str(path or "")

    def _capture_runs_tree_expanded_paths(self) -> set[str]:
        expanded_paths: set[str] = set()
        try:
            model = self._runs_tree.model()
            if model is None:
                return expanded_paths

            root_index = self._runs_tree.rootIndex()

            def _walk(parent_index) -> None:
                try:
                    row_count = int(model.rowCount(parent_index) or 0)
                except Exception:
                    row_count = 0

                for row in range(row_count):
                    try:
                        child_index = model.index(row, 0, parent_index)
                    except Exception:
                        continue
                    if child_index is None or not child_index.isValid():
                        continue

                    try:
                        is_expanded = bool(self._runs_tree.isExpanded(child_index))
                    except Exception:
                        is_expanded = False

                    if not is_expanded:
                        continue

                    path = self._runs_tree_index_to_path(child_index)
                    if path:
                        expanded_paths.add(self._normalize_runs_tree_path(path))
                    _walk(child_index)

            _walk(root_index)
        except Exception:
            return expanded_paths
        return expanded_paths

    def _apply_runs_tree_expanded_paths(self, paths: set[str] | None) -> None:
        try:
            model = self._runs_tree.model()
            if model is None:
                return

            normalized_paths = {
                self._normalize_runs_tree_path(path)
                for path in (paths or set())
                if str(path or "").strip()
            }
            root_index = self._runs_tree.rootIndex()

            def _walk(parent_index) -> None:
                try:
                    row_count = int(model.rowCount(parent_index) or 0)
                except Exception:
                    row_count = 0

                for row in range(row_count):
                    try:
                        child_index = model.index(row, 0, parent_index)
                    except Exception:
                        continue
                    if child_index is None or not child_index.isValid():
                        continue

                    path = self._normalize_runs_tree_path(self._runs_tree_index_to_path(child_index))
                    if path in normalized_paths:
                        try:
                            self._runs_tree.expand(child_index)
                        except Exception:
                            pass
                    else:
                        try:
                            self._runs_tree.collapse(child_index)
                        except Exception:
                            pass
                    _walk(child_index)

            _walk(root_index)
        except Exception:
            pass

    def _horizontal_scrollbar_for_object(self, obj):
        try:
            cur = obj
            visited = 0
            while cur is not None and visited < 12:
                visited += 1
                try:
                    if isinstance(cur, QAbstractScrollArea):
                        bar = cur.horizontalScrollBar()
                        if bar is not None:
                            return bar
                except Exception:
                    pass

                try:
                    if hasattr(cur, "parentWidget"):
                        cur = cur.parentWidget()
                    else:
                        cur = None
                except Exception:
                    cur = None
        except Exception:
            pass
        return None

    def _handle_shift_wheel_horizontal_scroll(self, obj, event) -> bool:
        try:
            if event is None or event.type() != QEvent.Wheel:
                return False

            try:
                if not bool(event.modifiers() & Qt.ShiftModifier):
                    return False
            except Exception:
                return False

            bar = self._horizontal_scrollbar_for_object(obj)
            if bar is None:
                return False

            try:
                if int(bar.maximum()) <= int(bar.minimum()):
                    return False
            except Exception:
                return False

            angle_delta = 0
            try:
                ad = event.angleDelta()
                if ad is not None:
                    angle_delta = int(ad.y()) if int(ad.y()) != 0 else int(ad.x())
            except Exception:
                angle_delta = 0

            pixel_delta = 0
            try:
                pd = event.pixelDelta()
                if pd is not None:
                    pixel_delta = int(pd.y()) if int(pd.y()) != 0 else int(pd.x())
            except Exception:
                pixel_delta = 0

            if angle_delta == 0 and pixel_delta == 0:
                return False

            cur_val = int(bar.value())
            if pixel_delta != 0:
                new_val = int(cur_val - pixel_delta)
            else:
                steps = float(angle_delta) / 120.0
                single_step = 20
                try:
                    single_step = max(1, int(bar.singleStep()))
                except Exception:
                    pass
                new_val = int(round(cur_val - (steps * single_step)))

            try:
                bar.setValue(max(int(bar.minimum()), min(int(bar.maximum()), int(new_val))))
            except Exception:
                return False

            try:
                event.accept()
            except Exception:
                pass
            return True
        except Exception:
            return False

    def _existing_case_folder_names(self) -> list[str]:
        try:
            runs_root = Path(getattr(self, "_runs_root", "") or "")
            if not runs_root.exists() or not runs_root.is_dir():
                return []

            names: list[str] = []
            for entry in os.scandir(str(runs_root)):
                if not entry.is_dir():
                    continue

                is_compare_case = False
                try:
                    for child in os.scandir(entry.path):
                        if not child.is_dir():
                            continue
                        if (Path(child.path) / "compare_manifest.json").is_file():
                            is_compare_case = True
                            break
                except Exception:
                    is_compare_case = False

                if is_compare_case:
                    continue

                name = str(entry.name or "").strip()
                if name:
                    names.append(name)
            return sorted(set(names), key=str.casefold)
        except Exception:
            return []

    def _runs_tree_index_is_dir(self, idx, tree: QTreeView | None = None) -> bool:
        try:
            if idx is None or (hasattr(idx, "isValid") and not idx.isValid()):
                return False

            use_tree = tree
            if use_tree is None:
                use_tree = getattr(self, "_runs_tree", None)
            if use_tree is None:
                return False

            model = use_tree.model()
            if model is None:
                return False

            if hasattr(model, "mapToSource") and hasattr(model, "sourceModel"):
                src = model.mapToSource(idx)
                sm = model.sourceModel()
                if sm is not None and hasattr(sm, "isDir"):
                    return bool(sm.isDir(src))

            if hasattr(model, "isDir"):
                return bool(model.isDir(idx))
        except Exception:
            return False

        return False

    def _runs_tree_compare_prefix(self, tree: QTreeView | None = None) -> str:
        try:
            use_tree = tree
            if use_tree is None:
                use_tree = getattr(self, "_runs_tree", None)
            if use_tree is None:
                return "↔ "

            model = use_tree.model()
            get_pref = getattr(model, "get_compare_prefix", None)
            if callable(get_pref):
                pref = str(get_pref() or "").strip()
                if pref:
                    return pref + (" " if not pref.endswith(" ") else "")
        except Exception:
            pass
        return "↔ "

    @staticmethod
    def _norm_fs_path(path: str) -> str:
        try:
            return os.path.normcase(os.path.abspath(str(path or "")))
        except Exception:
            return str(path or "")

    def _runs_tree_tooltip_payload_at(self, tree: QTreeView, pos) -> dict[str, str]:
        try:
            if tree is None:
                return {"text": "", "html": ""}

            index = tree.indexAt(pos)
            if index is None or not index.isValid():
                return {"text": "", "html": ""}

            text = str(index.data(Qt.DisplayRole) or "")
            if not text:
                return {"text": "", "html": ""}

            delegate = tree.itemDelegateForIndex(index) or tree.itemDelegate()
            option = QStyleOptionViewItem()
            try:
                option = tree.viewOptions()
            except Exception:
                pass

            option.rect = tree.visualRect(index)
            option.widget = tree
            try:
                if delegate is not None:
                    delegate.initStyleOption(option, index)
            except Exception:
                pass

            style = option.widget.style() if option.widget is not None else tree.style()
            text_rect = style.subElementRect(QStyle.SE_ItemViewItemText, option, option.widget)
            avail = int(text_rect.width())
            if avail <= 0:
                return {"text": "", "html": ""}

            fm = option.fontMetrics
            path = self._runs_tree_index_to_path(index, tree)
            is_dir = self._runs_tree_index_is_dir(index, tree)
            model = tree.model()

            is_compare_result_dir = False
            try:
                is_compare_fn = getattr(model, "is_compare_result_dir_path", None)
                if is_dir and callable(is_compare_fn):
                    is_compare_result_dir = bool(is_compare_fn(path))
            except Exception:
                pass

            is_regular_run_dir = bool(
                is_dir
                and (not is_compare_result_dir)
                and _RESULT_RUN_FOLDER_RE.match(Path(path).name or "")
            )

            is_compare_case_dir = False
            try:
                is_compare_case_fn = getattr(model, "is_compare_case_dir_path", None)
                if is_dir and callable(is_compare_case_fn):
                    is_compare_case_dir = bool(is_compare_case_fn(path))
            except Exception:
                pass

            is_compare_run_dir = False
            try:
                is_compare_run_fn = getattr(model, "is_compare_run_dir_path", None)
                if is_dir and callable(is_compare_run_fn):
                    is_compare_run_dir = bool(is_compare_run_fn(path))
            except Exception:
                pass

            active_norm = ""
            seg_colors = []
            try:
                get_active = getattr(model, "get_active_compare_dir_norm", None)
                if callable(get_active):
                    active_norm = str(get_active() or "")
            except Exception:
                pass
            try:
                get_seg_colors = getattr(model, "get_active_compare_segment_colors", None)
                if callable(get_seg_colors):
                    seg_colors = list(get_seg_colors() or [])
            except Exception:
                pass

            if is_regular_run_dir and isinstance(delegate, _CompareNameDelegate):
                meta = str(delegate._run_meta_text(path) or "")
                if meta:
                    gap = int(fm.horizontalAdvance("   "))
                    meta_w = int(fm.horizontalAdvance(meta))
                    name_avail = max(0, avail - meta_w - gap)
                    if fm.elidedText(text, Qt.ElideRight, name_avail) != text:
                        return {"text": text, "html": ""}
                    return {"text": "", "html": ""}

            compare_prefix = self._runs_tree_compare_prefix(tree)

            is_active_compare_path = bool(active_norm and self._norm_fs_path(path) == str(active_norm))
            is_compare_name = bool((is_compare_case_dir or is_compare_run_dir) and (" vs " in text))

            if is_compare_name:
                shown_text = text[len(compare_prefix):] if compare_prefix and text.startswith(compare_prefix) else text
                shown_text_for_measure = shown_text.replace(" vs ", " ↔ ")

                pref_w = 0
                if is_compare_case_dir and compare_prefix:
                    try:
                        if isinstance(delegate, _CompareNameDelegate):
                            pref_w = int(delegate._compare_prefix_draw_width(fm, text_rect, compare_prefix))
                        else:
                            pref_w = int(fm.horizontalAdvance(compare_prefix))
                    except Exception:
                        pref_w = int(fm.horizontalAdvance(compare_prefix))

                name_avail = max(0, avail - pref_w)

                if fm.elidedText(shown_text_for_measure, Qt.ElideRight, name_avail) != shown_text_for_measure:
                    rich_html = self._runs_tree_compare_tooltip_html(
                        text,
                        compare_prefix,
                        seg_colors if is_active_compare_path else [],
                    )
                    return {"text": shown_text_for_measure, "html": rich_html}

                return {"text": "", "html": ""}
        except Exception:
            return {"text": "", "html": ""}

    def _refresh_case_name_suggestions(self) -> None:
        try:
            names = self._existing_case_folder_names()
            text = str(self.case_edit.text() or "").strip().casefold()
            if text:
                names = [name for name in names if text in name.casefold()]

            self._case_name_popup.blockSignals(True)
            self._case_name_popup.clear()
            self._case_name_popup.addItems(names)
            self._case_name_popup.clearSelection()
            self._case_name_popup.setCurrentRow(-1)
            self._case_name_popup.blockSignals(False)
        except Exception:
            pass

    def _apply_case_name_completer_theme(self) -> None:
        try:
            popup = self._case_name_popup
            effective_mode = resolve_effective_theme_mode(self.theme_mode, QApplication.instance())
            if effective_mode == "light":
                bg = "#FFFFFF"
                fg = "#000000"
                border = "rgba(0, 0, 0, 0.18)"
                hover = "#ECECEC"
                selected = "#D9E9FF"
            else:
                bg = "#1E1E1E"
                fg = "#E6E6E6"
                border = "rgba(255, 255, 255, 0.16)"
                hover = "#242424"
                selected = "#2A2A2A"

            popup.setStyleSheet(
                f"""
                QListWidget {{
                    background: {bg};
                    color: {fg};
                    border: 1px solid {border};
                    outline: none;
                }}
                QListWidget::item {{
                    padding: 3px 10px;
                }}
                QListWidget::item:hover {{
                    background: {hover};
                }}
                QListWidget::item:selected {{
                    background: {selected};
                    color: {fg};
                }}
                """
            )
        except Exception:
            pass

    def _on_case_name_text_edited(self, _text: str) -> None:
        try:
            self._show_case_name_suggestions()
        except Exception:
            pass

    def _show_case_name_suggestions(self, *, force: bool = False) -> None:
        try:
            self._refresh_case_name_suggestions()
            names = self._case_name_popup.count()
            if not names:
                self._case_name_popup.hide()
                return

            anchor = self.mapFromGlobal(self.case_edit.mapToGlobal(self.case_edit.rect().bottomLeft()))
            row_height = self._case_name_popup.sizeHintForRow(0)
            if row_height <= 0:
                row_height = max(20, self.case_edit.height() - 4)
            visible_rows = min(10, self._case_name_popup.count())
            frame = self._case_name_popup.frameWidth() * 2
            popup_height = (row_height * visible_rows) + frame + 2
            self._case_name_popup.setGeometry(anchor.x(), anchor.y() + 1, self.case_edit.width(), popup_height)
            self._case_name_popup.raise_()
            self._case_name_popup.show()
        except Exception:
            pass

    def _dismiss_case_name_suggestions(self, *, clear_focus: bool = True, require_edit_to_reopen: bool = False) -> None:
        try:
            self._case_name_popup.hide()
            if clear_focus and self.case_edit.hasFocus():
                self.case_edit.clearFocus()
        except Exception:
            pass

    def _on_case_name_popup_item_clicked(self, item) -> None:
        try:
            if item is None:
                return
            text = str(item.text() or "")
            self.case_edit.setText(text)
            self.case_edit.setCursorPosition(len(text))
            self.case_edit.setFocus()
            self._dismiss_case_name_suggestions(clear_focus=False, require_edit_to_reopen=False)
        except Exception:
            pass

    def _is_case_name_popup_target(self, obj) -> bool:
        try:
            popup = self._case_name_popup
            if obj in (self.case_edit, popup, popup.viewport()):
                return True

            if isinstance(obj, QWidget):
                if self.case_edit.isAncestorOf(obj) or popup.isAncestorOf(obj):
                    return True
        except Exception:
            pass
        return False

    def _case_name_click_target(self, obj, event):
        try:
            if event is not None:
                gp = None
                if hasattr(event, "globalPosition"):
                    try:
                        gp = event.globalPosition().toPoint()
                    except Exception:
                        gp = None
                if gp is None and hasattr(event, "globalPos"):
                    try:
                        gp = event.globalPos()
                    except Exception:
                        gp = None
                if gp is not None:
                    hit = QApplication.widgetAt(gp)
                    if hit is not None:
                        return hit
        except Exception:
            pass
        return obj

    def _on_app_focus_changed(self, _old, now) -> None:
        try:
            if self._case_name_popup.isVisible() and not self._is_case_name_popup_target(now):
                QTimer.singleShot(0, self._dismiss_case_name_suggestions)
        except Exception:
            pass

    def _apply_output_toggle_theme(self) -> None:
        try:
            mode = resolve_effective_theme_mode(self.theme_mode, QApplication.instance())
            if mode == "light":
                button_css = """
                QPushButton {
                    background: #FFFFFF;
                    color: #1A1A1A;
                    border: 1px solid #D0D0D0;
                    border-radius: 6px;
                    padding: 6px 12px;
                    font-size: 12px;
                }
                QPushButton:hover { background: #F0F0F0; border-color: #C2C2C2; }
                QPushButton:pressed { background: #E6E6E6; }
                QPushButton:checked {
                    background: #E3F1E5;
                    color: #16341A;
                    border-color: #8CB994;
                }
                """
            else:
                button_css = """
                QPushButton {
                    background: #2A2A2A;
                    color: #EAEAEA;
                    border: 1px solid #3A3A3A;
                    border-radius: 6px;
                    padding: 6px 12px;
                    font-size: 12px;
                }
                QPushButton:hover { background: #333333; border-color: #4A4A4A; }
                QPushButton:pressed { background: #252525; }
                QPushButton:checked {
                    background: #1F2B1F;
                    color: #EAEAEA;
                    border-color: #2E4A2E;
                }
                """

            for btn in (self._output_btn_live, self._output_btn_console):
                if btn is not None:
                    btn.setStyleSheet(button_css)
        except Exception:
            pass

    def _tinted_rail_icon(self, icon_path: Path, color: str) -> QIcon:
        try:
            # Render SVG at physical resolution so it stays sharp at any DPI scale.
            try:
                dpr = float(self.devicePixelRatioF())
            except Exception:
                dpr = 1.0
            if dpr <= 0:
                dpr = 1.0

            log_w = self._rail_icon_size.width()
            log_h = self._rail_icon_size.height()
            phys_w = max(1, round(log_w * dpr))
            phys_h = max(1, round(log_h * dpr))

            renderer = QSvgRenderer(str(icon_path))
            if not renderer.isValid():
                return QIcon(str(icon_path))

            src = QPixmap(phys_w, phys_h)
            src.fill(Qt.transparent)
            p = QPainter(src)
            try:
                renderer.render(p, QRectF(0.0, 0.0, float(phys_w), float(phys_h)))
            finally:
                p.end()

            tinted = QPixmap(phys_w, phys_h)
            tinted.fill(Qt.transparent)
            p = QPainter(tinted)
            try:
                p.drawPixmap(0, 0, src)
                p.setCompositionMode(QPainter.CompositionMode_SourceIn)
                p.fillRect(tinted.rect(), QColor(color))
            finally:
                p.end()

            tinted.setDevicePixelRatio(dpr)
            return QIcon(tinted)
        except Exception:
            return QIcon(str(icon_path))

    def _refresh_month_nav_icons(self) -> None:
        try:
            effective_mode = resolve_effective_theme_mode(self.theme_mode, QApplication.instance())
            icon_color = "#000000" if effective_mode == "light" else "#FFFFFF"

            self._runs_month_prev_btn.setIcon(self._tinted_rail_icon(self._month_prev_icon_path, icon_color))
            self._runs_month_next_btn.setIcon(self._tinted_rail_icon(self._month_next_icon_path, icon_color))
        except Exception:
            pass

        try:
            self._month_picker_prev_btn.setIcon(self._tinted_rail_icon(self._month_prev_icon_path, icon_color))
            self._month_picker_next_btn.setIcon(self._tinted_rail_icon(self._month_next_icon_path, icon_color))
        except Exception:
            pass

    def _refresh_left_rail_icons(self) -> None:
        try:
            effective_mode = resolve_effective_theme_mode(self.theme_mode, QApplication.instance())
            if effective_mode == "light":
                active = "#000000"
                inactive = "#7A7A7A"
            else:
                active = "#FFFFFF"
                inactive = "#A7A7A7"
            current_index = self._stack.currentIndex() if getattr(self, "_stack", None) is not None else -1

            run_color = active if current_index == getattr(self, "_page_run_index", -1) else inactive
            results_color = active if current_index == getattr(self, "_page_results_index", -1) else inactive

            self._btn_run_page.setIcon(self._tinted_rail_icon(self._run_icon_path, run_color))
            self._btn_results_page.setIcon(self._tinted_rail_icon(self._results_icon_path, results_color))
            self._btn_help.setIcon(self._tinted_rail_icon(self._help_icon_path, inactive))
            self._btn_settings.setIcon(self._tinted_rail_icon(self._settings_icon_path, inactive))
        except Exception:
            pass

    # ---------- helpers ----------
    def _bold_label(self, text: str) -> QLabel:
        lab = QLabel(text)
        f = lab.font()
        f.setBold(True)
        lab.setFont(f)
        return lab

    def _unit_label(self, text: str) -> QLabel:
        lab = QLabel(text)
        lab.setObjectName("UnitLabel")
        return lab

    def _update_run_button_state(self) -> None:
        """Update run button enabled state and tooltip."""
        try:
            if self.benchmark.is_running():
                self.run_btn.setEnabled(False)
                return
        except Exception:
            pass

        try:
            ok = self.sensors.can_run(self.furmark_exe, self.prime_exe)
        except Exception:
            ok = False
        self.run_btn.setEnabled(ok)

        if not ok:
            try:
                if self.benchmark.is_running():
                    self.run_btn.setToolTip("Test running — abort to enable new runs.")
                else:
                    reasons = self.sensors.missing_reasons(self.furmark_exe, self.prime_exe)
                    self.run_btn.setToolTip("\n".join(reasons))
            except Exception:
                pass
        else:
            self.run_btn.setToolTip("Start the test")

    # ---------- manual update checker ----------
    def check_for_updates(self, *, set_status, set_button_text, set_button_enabled) -> None:
        """Manual updater entrypoint used by Settings dialog.

        This is a multi-step flow driven by the same button:
        - Check for updates
        - If update available: Download
        - If downloaded: Install
        All status is surfaced inline next to the button.
        """
        self._bind_update_ui(
            set_status=set_status,
            set_button_text=set_button_text,
            set_button_enabled=set_button_enabled,
        )

        if sys.platform != "win32":
            self._update_ui(status_text="Windows-only.", status_level="error")
            return

        # Dev/test hook: bypass GitHub and stage a local installer for the Install step.
        # Usage:
        #   set THERMALBENCH_UPDATER_TEST_INSTALLER=C:\path\to\ThermalBench-Setup-vX.Y.Z.exe
        test_installer = os.environ.get("THERMALBENCH_UPDATER_TEST_INSTALLER", "").strip()
        if test_installer:
            installer_path = Path(test_installer).expanduser()
            if not installer_path.exists():
                self._update_ui(
                    status_text=f"Test installer not found: {installer_path}",
                    status_level="error",
                )
                return

            self._update_last_release = None
            self._update_downloaded_installer = installer_path
            self._update_ui(
                status_text="Test mode: installer staged. Click again to install…",
                status_level="info",
                button_text="Install update…",
                button_enabled=True,
            )
            return

        if not GITHUB_OWNER or not GITHUB_REPO:
            self._update_ui(
                status_text="Updater not configured.",
                status_level="error",
            )
            return

        # If an installer is already downloaded, clicking becomes the install action.
        if self._update_downloaded_installer is not None:
            try:
                self._update_ui(
                    status_text="Installing update… (ThermalBench will close briefly)",
                    status_level="info",
                    button_enabled=False,
                )
                launch_installer_with_updater_ui(
                    self._update_downloaded_installer,
                    wait_for_pid=os.getpid(),
                    silent=True,
                )
            except Exception as e:
                self._update_ui(
                    status_text=f"Error: {e}",
                    status_level="error",
                    button_enabled=True,
                )
                return

            # The updater helper shows a progress window and will restart the app when done.
            # We still must exit for the installer to replace files.
            QTimer.singleShot(250, QApplication.quit)
            return

        # If we already know an update is available, clicking becomes the download action.
        if self._update_last_release is not None:
            try:
                if is_newer_version(__version__, self._update_last_release.version):
                    self._start_update_download(self._update_last_release)
                    return
            except Exception:
                # fall back to re-check
                self._update_last_release = None

        # Otherwise, click triggers a fresh check.
        self._start_update_check()

    def _start_update_check(self) -> None:
        if getattr(self, "_update_in_progress", False):
            self._update_ui(status_text="Busy…", status_level="info")
            return

        self._set_update_busy(True)
        self._update_ui(
            status_text="Checking…",
            status_level="info",
            button_text="Checking…",
            button_enabled=False,
        )

        thread = QThread(self)
        worker = _FetchLatestReleaseWorker(GITHUB_OWNER, GITHUB_REPO, INSTALLER_PREFIX)
        worker.moveToThread(thread)

        thread.started.connect(worker.run)
        worker.finished.connect(self._on_update_release_info)
        worker.failed.connect(self._on_update_failed)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        worker.failed.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)

        self._update_fetch_thread = thread
        self._update_fetch_worker = worker
        thread.start()

    def _start_update_download(self, release: ReleaseInfo) -> None:
        # Pre-download already finished — skip straight to the install step.
        if self._update_downloaded_installer is not None:
            self._on_update_downloaded(str(self._update_downloaded_installer))
            return

        # Silent download is still in progress — adopt it; show current progress
        # and let the silent slots forward subsequent updates via _update_ui.
        if self._silent_dl_thread is not None:
            self._update_ui(
                status_text=(
                    f"Downloading… {int(round(self._silent_dl_bytes / self._silent_dl_total * 100))}%"
                    if self._silent_dl_total > 0
                    else "Downloading…"
                ),
                status_level="info",
                button_text="Downloading…",
                button_enabled=False,
            )
            return

        if getattr(self, "_update_in_progress", False):
            self._update_ui(status_text="Busy…", status_level="info")
            return

        self._set_update_busy(True)
        self._update_ui(
            status_text="Downloading…",
            status_level="info",
            button_text="Downloading…",
            button_enabled=False,
        )

        thread = QThread(self)
        worker = _DownloadInstallerWorker(release)
        worker.moveToThread(thread)

        thread.started.connect(worker.run)
        worker.progress.connect(self._on_update_download_progress)
        worker.finished.connect(self._on_update_downloaded)
        worker.failed.connect(self._on_update_download_failed)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        worker.failed.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)

        self._update_download_thread = thread
        self._update_download_worker = worker
        thread.start()

    # ---------- startup (silent) update check ----------
    def _startup_update_check(self) -> None:
        """Run silently at startup; only prompt the user if a newer version exists."""
        if sys.platform != "win32":
            return
        if not GITHUB_OWNER or not GITHUB_REPO:
            return
        # Don't interfere with a manual check already in progress.
        if getattr(self, "_update_in_progress", False):
            return

        thread = QThread(self)
        worker = _FetchLatestReleaseWorker(GITHUB_OWNER, GITHUB_REPO, INSTALLER_PREFIX)
        worker.moveToThread(thread)

        thread.started.connect(worker.run)
        worker.finished.connect(self._on_startup_update_found)
        # Silently swallow failures — no UI noise on startup.
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        worker.failed.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)

        # Keep references so they aren't garbage-collected.
        self._startup_fetch_thread = thread
        self._startup_fetch_worker = worker
        thread.start()

    def _on_startup_update_found(self, release: ReleaseInfo) -> None:
        """Called (on the main thread) when the startup check receives release info."""
        try:
            newer = is_newer_version(__version__, release.version)
        except Exception:
            return

        if not newer:
            return

        # Store immediately so Settings can use it regardless of what the user does.
        self._update_last_release = release

        # Begin silently pre-downloading in the background straight away.
        self._start_silent_download(release)

        dlg = UpdateAvailableDialog(
            self,
            current_version=__version__,
            new_version=release.version,
            release_notes=release.notes,
            theme_mode=getattr(self, "theme_mode", "device"),
        )
        if dlg.exec() == UpdateAvailableDialog.Accepted:
            try:
                self._open_settings_to_updates()
            except Exception:
                pass

    # ---------- silent background pre-download ----------
    def _start_silent_download(self, release: ReleaseInfo) -> None:
        """Download the installer silently; no UI feedback until Settings is opened."""
        if sys.platform != "win32":
            return
        # Already running or already finished.
        if self._silent_dl_thread is not None or self._silent_dl_done:
            return
        if self._update_downloaded_installer is not None:
            return

        self._silent_dl_bytes = 0
        self._silent_dl_total = -1
        self._silent_dl_done = False

        thread = QThread(self)
        worker = _DownloadInstallerWorker(release)
        worker.moveToThread(thread)

        thread.started.connect(worker.run)
        worker.progress.connect(self._on_silent_dl_progress)
        worker.finished.connect(self._on_silent_dl_done)
        worker.failed.connect(self._on_silent_dl_failed)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        worker.failed.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self._on_silent_dl_thread_finished)

        self._silent_dl_thread = thread
        self._silent_dl_worker = worker
        thread.start()

    def _on_silent_dl_progress(self, downloaded: int, total: int) -> None:
        self._silent_dl_bytes = downloaded
        self._silent_dl_total = total
        # Forward to Settings dialog if it is currently open.
        try:
            if total > 0:
                pct = int(round(downloaded / total * 100))
                self._update_ui(status_text=f"Downloading… {pct}%", status_level="info")
            else:
                self._update_ui(status_text="Downloading…", status_level="info")
        except Exception:
            pass

    def _on_silent_dl_done(self, installer_path_str: str) -> None:
        self._silent_dl_done = True
        self._update_downloaded_installer = Path(installer_path_str)
        # If Settings is open, flip it straight to the install state.
        self._set_update_busy(False)
        self._update_ui(
            status_text="Downloaded. Ready to install.",
            status_level="warn",
            button_text="Install update",
            button_enabled=True,
        )

    def _on_silent_dl_failed(self, reason: str) -> None:
        # Silently discard; the manual flow will offer a retry if Settings is opened.
        self._silent_dl_done = False
        self._silent_dl_bytes = 0
        self._silent_dl_total = -1

    def _on_silent_dl_thread_finished(self) -> None:
        self._silent_dl_thread = None
        self._silent_dl_worker = None

    def _open_settings_to_updates(self) -> None:
        """Open the Settings dialog and navigate to the Updates section."""
        try:
            # Re-use the existing mechanism that opens the settings dialog.
            self.open_settings()
        except Exception:
            pass

    def _on_update_failed(self, reason: str) -> None:
        self._set_update_busy(False)
        self._update_ui(
            status_text=f"Error: {reason}",
            status_level="error",
            button_text="Check for updates…",
            button_enabled=True,
        )

    def _on_update_release_info(self, release: ReleaseInfo) -> None:
        try:
            newer = is_newer_version(__version__, release.version)
        except Exception as e:
            self._set_update_busy(False)
            self._update_ui(
                status_text=f"Error: {e}",
                status_level="error",
                button_text="Check for updates…",
                button_enabled=True,
            )
            return

        if not newer:
            self._set_update_busy(False)
            self._update_last_release = None
            self._update_downloaded_installer = None
            self._update_ui(
                status_text="Up to date.",
                status_level="ok",
                button_text="Check for updates…",
                button_enabled=True,
            )
            return

        # Update available: surface inline and let the same button become Download.
        self._update_last_release = release
        self._set_update_busy(False)
        self._update_ui(
            status_text=f"Update available: {release.version}",
            status_level="warn",
            button_text=f"Download {release.version}",
            button_enabled=True,
        )

    def _on_update_download_progress(self, downloaded_bytes: int, total_bytes: int) -> None:
        try:
            if total_bytes and total_bytes > 0:
                pct = int(round((downloaded_bytes / total_bytes) * 100))
                self._update_ui(status_text=f"Downloading… {pct}%", status_level="info")
            else:
                self._update_ui(status_text="Downloading…", status_level="info")
        except Exception:
            pass

    def _on_update_download_failed(self, reason: str) -> None:
        self._set_update_busy(False)
        # Keep the last release so the user can click Download again.
        btn = "Download update"
        try:
            if self._update_last_release is not None and self._update_last_release.version:
                btn = f"Download {self._update_last_release.version}"
        except Exception:
            pass
        self._update_ui(
            status_text=f"Error: {reason}",
            status_level="error",
            button_text=btn,
            button_enabled=True,
        )

    def _on_update_downloaded(self, installer_path_str: str) -> None:
        installer_path = Path(installer_path_str)
        self._set_update_busy(False)
        self._update_downloaded_installer = installer_path
        self._update_ui(
            status_text="Downloaded. Ready to install.",
            status_level="warn",
            button_text="Install update",
            button_enabled=True,
        )

    def _get_current_settings(self) -> dict:
        """Get current settings as a dictionary."""
        warm = int(self.warmup_min.value()) * 60 + int(self.warmup_sec.value())
        logsec = int(self.log_min.value()) * 60 + int(self.log_sec.value())

        demo_display = self.fur_demo_combo.currentText()
        fur_demo = self.fur_demo_map.get(demo_display, "furmark-knot-gl")

        hwinfo_csv = resolve_hwinfo_csv()
        hwinfo_exe = resolve_hwinfo_exe(self.hwinfo_exe)

        self.hwinfo_csv = hwinfo_csv
        self.hwinfo_exe = hwinfo_exe

        try:
            if self.hwinfo_edit.text().strip() != hwinfo_csv:
                self.hwinfo_edit.setText(hwinfo_csv)
        except Exception:
            pass

        res_display = self.fur_res_combo.currentText()
        fur_w, fur_h = self.res_map.get(res_display, (3840, 1600))
        furmark_exe = resolve_furmark_exe(self.furmark_exe)
        prime_exe = resolve_prime95_exe(self.prime_exe)

        if furmark_exe:
            self.furmark_exe = furmark_exe

        if prime_exe:
            self.prime_exe = prime_exe

        return {
            "case_name": self.case_edit.text().strip(),
            "warmup_total_sec": warm,
            "log_total_sec": logsec,
            "hwinfo_csv": hwinfo_csv,
            "hwinfo_exe": hwinfo_exe,
            "fur_demo": fur_demo,
            "fur_demo_display": demo_display,
            "fur_width": fur_w,
            "fur_height": fur_h,
            "fur_res_display": res_display,
            "furmark_exe": furmark_exe,
            "prime_exe": prime_exe,
            "ntfy_topic": self.ntfy_topic,
            "stress_cpu": bool(getattr(self.sensors, "stress_cpu", True)),
            "stress_gpu": bool(getattr(self.sensors, "stress_gpu", True)),
        }

    # ---------- settings ----------
    def load_settings(self):
        """Load settings from JSON file."""
        data = load_json(self.settings_path)
        if not data:
            # Fresh install — show help automatically once the window is visible.
            QTimer.singleShot(200, self.open_help)
            return

        # Show help whenever the installed version is newer than what the
        # settings were last saved with.  This catches the case where old
        # settings survive an uninstall/reinstall (settings.json lives in
        # %LOCALAPPDATA% and is intentionally not removed by the uninstaller).
        last_seen = str(data.get("last_seen_version") or "").strip()
        if last_seen != __version__:
            QTimer.singleShot(200, self.open_help)

        self._apply_saved_window_state(data)

        self.case_edit.setText(str(data.get("case_name", self.case_edit.text())))
        self.hwinfo_csv = resolve_hwinfo_csv()
        self.hwinfo_edit.setText(self.hwinfo_csv)

        self.warmup_min.setValue(int(data.get("warmup_min", self.warmup_min.value())))
        self.warmup_sec.setValue(int(data.get("warmup_sec", self.warmup_sec.value())))
        self.log_min.setValue(int(data.get("log_min", self.log_min.value())))
        self.log_sec.setValue(int(data.get("log_sec", self.log_sec.value())))

        saved_furmark = str(data.get("furmark_exe", "")).strip()
        saved_prime = str(data.get("prime_exe", "")).strip()
        saved_hwinfo = ""

        self.furmark_exe = resolve_furmark_exe(saved_furmark)
        self.prime_exe = resolve_prime95_exe(saved_prime)
        self.hwinfo_exe = resolve_hwinfo_exe("")

        self.ntfy_topic = str(data.get("ntfy_topic", self.ntfy_topic or "")).strip()

        self.theme_mode = str(data.get("theme", self.theme_mode or "device")).strip().lower() or "device"

        try:
            style_combobox_popup(self.fur_demo_combo, self.theme_mode)
            style_combobox_popup(self.fur_res_combo, self.theme_mode)
            self._apply_case_name_completer_theme()
            self._refresh_case_name_suggestions()
            self._live_monitor.set_theme_mode(self.theme_mode)
            self._live_graph.set_theme_mode(self.theme_mode)
            self.graph.set_theme_mode(self.theme_mode)
            self._apply_left_rail_theme()
            self._apply_results_tree_theme()
            self._apply_output_toggle_theme()
            self._refresh_left_rail_icons()
            self._refresh_month_nav_icons()
            self._refresh_compare_delegate_theme()
        except Exception:
            pass

        demo_display = data.get("fur_demo_display")
        if demo_display in self.fur_demo_map:
            self.fur_demo_combo.setCurrentText(demo_display)

        res_display = data.get("fur_res_display")
        if res_display in self.res_map:
            self.fur_res_combo.setCurrentText(res_display)

        tokens = data.get("selected_tokens")
        if isinstance(tokens, list) and tokens:
            self.sensors.selected_tokens = [str(t) for t in tokens]

        stress_cpu = bool(data.get("stress_cpu", True))
        stress_gpu = bool(data.get("stress_gpu", True))
        if (not stress_cpu) and (not stress_gpu):
            stress_cpu = True
            stress_gpu = True

        self.sensors.stress_cpu = stress_cpu
        self.sensors.stress_gpu = stress_gpu

        self.cpu_btn.blockSignals(True)
        self.gpu_btn.blockSignals(True)
        self.cpu_btn.setChecked(stress_cpu)
        self.gpu_btn.setChecked(stress_gpu)
        self.cpu_btn.blockSignals(False)
        self.gpu_btn.blockSignals(False)
        self._sync_furmark_gpu_controls()
        self._refresh_prime95_torture_display()

    def _apply_saved_window_state(self, data: dict) -> None:
        try:
            width = int(data.get("window_width", 0) or 0)
            height = int(data.get("window_height", 0) or 0)
            was_maximized = bool(data.get("window_maximized", False))
        except Exception:
            return

        if width <= 0 or height <= 0:
            if was_maximized:
                try:
                    QTimer.singleShot(0, self.showMaximized)
                except Exception:
                    pass
            return

        try:
            self._restoring_window_state = True
            self.resize(max(960, width), max(768, height))
        except Exception:
            pass
        finally:
            self._restoring_window_state = False

        if was_maximized:
            try:
                QTimer.singleShot(0, self.showMaximized)
            except Exception:
                pass

    def _current_window_settings(self) -> dict:
        try:
            geom = self.normalGeometry() if self.isMaximized() else self.geometry()
            width = int(geom.width())
            height = int(geom.height())
        except Exception:
            width = int(self.width())
            height = int(self.height())

        return {
            "window_width": max(1, width),
            "window_height": max(1, height),
            "window_maximized": bool(self.isMaximized()),
        }

    def save_settings(self):
        """Save settings to JSON file."""
        payload = {
            "case_name": self.case_edit.text().strip(),
            "hwinfo_csv": resolve_hwinfo_csv(),
            "warmup_min": int(self.warmup_min.value()),
            "warmup_sec": int(self.warmup_sec.value()),
            "log_min": int(self.log_min.value()),
            "log_sec": int(self.log_sec.value()),
            "fur_demo_display": self.fur_demo_combo.currentText(),
            "fur_res_display": self.fur_res_combo.currentText(),
            "selected_tokens": list(self.sensors.selected_tokens),
            "stress_cpu": bool(self.sensors.stress_cpu),
            "stress_gpu": bool(self.sensors.stress_gpu),
            "furmark_exe": self.furmark_exe,
            "prime_exe": self.prime_exe,
            "hwinfo_exe": resolve_hwinfo_exe(""),
            "ntfy_topic": self.ntfy_topic,
            "theme": self.theme_mode,
            "last_seen_version": __version__,
        }
        payload.update(self._current_window_settings())
        save_json(self.settings_path, payload)
        self._update_run_button_state()

    def open_settings(self) -> None:
        """Open settings dialog."""
        dlg = SettingsDialog(
            self,
            furmark_exe=self.furmark_exe,
            prime_exe=self.prime_exe,
            ntfy_topic=self.ntfy_topic,
            theme=self.theme_mode,
            update_callback=self.check_for_updates,
        )
        if dlg.exec() != QDialog.Accepted:
            return

        self.furmark_exe = resolve_furmark_exe(dlg.furmark_exe())
        self.prime_exe = resolve_prime95_exe(dlg.prime_exe())
        try:
            self.ntfy_topic = dlg.ntfy_topic()
        except Exception:
            pass
        self.theme_mode = dlg.theme()

        app = QApplication.instance()
        if app is not None:
            apply_theme(app, self.theme_mode)
            style_combobox_popup(self.fur_demo_combo, self.theme_mode)
            style_combobox_popup(self.fur_res_combo, self.theme_mode)
            self._apply_case_name_completer_theme()
            self._refresh_case_name_suggestions()
            self._live_monitor.set_theme_mode(self.theme_mode)
            self._live_graph.set_theme_mode(self.theme_mode)
            self.graph.set_theme_mode(self.theme_mode)
            self._apply_left_rail_theme()
            self._apply_results_tree_theme()
            self._apply_output_toggle_theme()
            self._refresh_left_rail_icons()
            self._refresh_month_nav_icons()
            self._sync_furmark_gpu_controls()
            self._refresh_prime95_torture_display()
            self._refresh_compare_delegate_theme()

        self.hwinfo_exe = resolve_hwinfo_exe("")
        self.hwinfo_csv = resolve_hwinfo_csv()
        try:
            self.hwinfo_edit.setText(self.hwinfo_csv)
        except Exception:
            pass

        self.save_settings()

    def open_help(self) -> None:
        """Toggle the help side panel."""
        if self._help_panel.isVisible():
            self._help_panel.hide()
            return
        self._help_panel.set_theme_mode(self.theme_mode)
        self._help_panel.setVisible(True)
        # Allocate the panel its default width; stack gets the rest
        total = self._content_splitter.width()
        panel_w = HelpPanel.PANEL_WIDTH
        self._content_splitter.setSizes([max(0, total - panel_w), panel_w])

    def closeEvent(self, event):
        """Handle window close event."""
        self.save_settings()
        super().closeEvent(event)

    # ---------- misc ----------
    def append(self, text: str) -> None:
        """Append text to log."""
        self.log.append(text.rstrip())

    def _refresh_live_widget_theme(self) -> None:
        try:
            self._live_monitor.set_theme_mode(self.theme_mode)
            self._live_graph.set_theme_mode(self.theme_mode)
            self._live_monitor.repaint()
            self._live_graph.repaint()
        except Exception:
            pass

    def _show_live_output(self, *_args) -> None:
        try:
            self._refresh_live_widget_theme()
            if self._output_stack is not None:
                self._output_stack.setCurrentIndex(0)
            QTimer.singleShot(0, self._refresh_live_widget_theme)
            QTimer.singleShot(0, self._apply_live_split_ratio)
        except Exception:
            pass

    def _show_console_output(self, *_args) -> None:
        try:
            if self._output_stack is not None:
                self._output_stack.setCurrentIndex(1)
        except Exception:
            pass

    # ---------- live monitor hooks ----------
    def _on_run_started(self, settings: dict, columns: list[str]) -> None:
        try:
            csv_path = str((settings or {}).get("hwinfo_csv") or "").strip()
            cols = [str(c) for c in (columns or []) if str(c).strip()]

            # Always include ambient as a temperature series during runs.
            ambient_col = "Ambient [°C]"
            if ambient_col not in cols:
                cols.append(ambient_col)

            try:
                self._live_monitor.start(csv_path=csv_path, columns=cols)
                self._live_graph.start(columns=cols)
            except Exception:
                pass

            if self._output_stack is not None:
                self._show_live_output()
            if self._output_btn_live is not None:
                self._output_btn_live.setChecked(True)
        except Exception:
            pass

        self._apply_live_split_ratio()

    def _on_ambient_csv(self, ambient_csv_path: str) -> None:
        try:
            self._live_monitor.set_ambient_csv(str(ambient_csv_path or "").strip())
        except Exception:
            pass

    def _on_run_finished(self, result: dict | None = None) -> None:
        try:
            self._invalidate_month_picker_stats_cache()
            self._refresh_results_month_nav()
        except Exception:
            pass
        try:
            self._remember_finished_result(result)
        except Exception:
            pass
        try:
            try:
                self._live_monitor.stop()
                self._live_graph.stop()
            except Exception:
                pass

            # Best-effort: send a push notification if configured.
            try:
                self._notify_run_finished_ntfy(result)
            except Exception:
                pass

            if self._output_stack is not None:
                self._show_console_output()
            if self._output_btn_console is not None:
                self._output_btn_console.setChecked(True)

            try:
                if self._stack is not None and self._stack.currentIndex() == getattr(self, "_page_results_index", -1):
                    QTimer.singleShot(150, self._focus_latest_finished_result)
            except Exception:
                pass
        except Exception:
            pass

    def _notify_run_finished_ntfy(self, result: dict | None = None) -> None:
        topic = str(getattr(self, "ntfy_topic", "") or "").strip()
        if not topic:
            return

        case_name = ""
        try:
            case_name = str(self.case_edit.text() or "").strip()
        except Exception:
            case_name = ""
        if isinstance(result, dict):
            case_name = str(result.get("case_name") or case_name or "").strip()

        ok = True
        elapsed_sec = None
        run_dir = ""
        if isinstance(result, dict):
            try:
                ok = int(result.get("exit_code", 0) or 0) == 0
            except Exception:
                ok = True
            try:
                elapsed_sec = int(result.get("elapsed_sec")) if result.get("elapsed_sec") is not None else None
            except Exception:
                elapsed_sec = None
            run_dir = str(result.get("run_dir") or "").strip()

        status = "SUCCESS" if ok else "FAILED"
        dur = ""
        if isinstance(elapsed_sec, int) and elapsed_sec >= 0:
            mm = elapsed_sec // 60
            ss = elapsed_sec % 60
            dur = f" ({mm:02d}:{ss:02d})"

        subject = f"ThermalBench: {case_name or 'Test'} finished - {status}"
        body = f"Case: {case_name or 'Test'}\nStatus: {status}{dur}\n"
        if run_dir:
            body += f"Run folder: {run_dir}\n"

        table_attachment = self._export_ntfy_word_table_html(run_dir)
        if table_attachment is not None:
            body += f"Word table file saved: {table_attachment.name}\n"

        try:
            self._ntfy_notifier.send(topic=topic, title=subject, message=body)
        except Exception:
            pass

        if table_attachment is not None:
            try:
                self._ntfy_notifier.send_file(
                    topic=topic,
                    title=f"ThermalBench: {case_name or 'Test'} Word table",
                    file_path=str(table_attachment),
                    filename=str(table_attachment.name),
                )
            except Exception:
                pass

    def _export_ntfy_word_table_html(self, run_dir: str) -> Path | None:
        try:
            folder = Path(str(run_dir or "").strip())
            if not folder.exists() or not folder.is_dir():
                return None

            payload = self._build_word_table_payload(run_dir)
            if not payload:
                return None

            out_path = folder / "word_table.html"
            out_path.write_text(str(payload[1] or ""), encoding="utf-8")
            return out_path
        except Exception:
            return None

    def _build_word_table_payload(self, run_dir: str) -> tuple[str, str] | None:
        try:
            folder = Path(str(run_dir or "").strip())
            if not folder.exists() or not folder.is_dir():
                return None

            csv_path = folder / "run_window.csv"
            if not csv_path.exists() or not csv_path.is_file():
                return None

            df_data, cols = load_run_csv_dataframe(str(csv_path))
            stats_map = stats_from_summary_csv(str(csv_path))
            if not stats_map:
                stats_map = stats_from_dataframe(df_data[cols])
            if not stats_map:
                return None

            room_temperature = None
            try:
                ambient_col = None
                if "Ambient [°C]" in df_data.columns:
                    ambient_col = "Ambient [°C]"
                else:
                    for col in df_data.columns:
                        if "ambient" in str(col).lower():
                            ambient_col = str(col)
                            break
                if ambient_col and ambient_col in df_data.columns:
                    ser = df_data[ambient_col]
                    room_temperature = float(ser.mean(skipna=True))
            except Exception:
                room_temperature = None

            if room_temperature is None:
                try:
                    avg_temp_path = folder / "avg_temperature.json"
                    if avg_temp_path.exists() and avg_temp_path.is_file():
                        payload = json.loads(avg_temp_path.read_text(encoding="utf-8"))
                        if isinstance(payload, dict) and payload.get("manual_average_temperature") is not None:
                            room_temperature = float(payload.get("manual_average_temperature"))
                except Exception:
                    room_temperature = None

            test_settings = None
            try:
                settings_path = folder / "test_settings.json"
                if settings_path.exists() and settings_path.is_file():
                    payload = json.loads(settings_path.read_text(encoding="utf-8"))
                    if isinstance(payload, dict):
                        test_settings = payload
            except Exception:
                test_settings = None

            popup = LegendStatsPopup(
                self,
                title=infer_stats_title(list(cols or [])),
                columns=list(cols or []),
                active_set=set(cols or []),
                color_for=lambda _name: "#EAEAEA",
                on_toggle=lambda _name, _checked, _active_cols=None: None,
                stats_map=stats_map,
                room_temperature=room_temperature,
                test_settings=test_settings,
                on_close=None,
            )
            try:
                return popup.build_table_clipboard_payload()
            finally:
                try:
                    popup.deleteLater()
                except Exception:
                    pass
        except Exception:
            return None

    def _on_ntfy_notify_finished(self, ok: bool, message: str) -> None:
        try:
            if not ok:
                self.append(str(message or "Push notification failed"))
        except Exception:
            pass

    def _on_log_started(self) -> None:
        """Called when warmup ends and the logging window begins."""
        try:
            self._live_monitor.reset_window_stats()
            self._live_graph.mark_phase_boundary()
        except Exception:
            pass

    def _on_log_finished(self) -> None:
        """Called when the logging window ends (freeze live stats)."""
        try:
            self._live_monitor.stop()
            self._live_graph.stop()
        except Exception:
            pass

    def open_hwinfo(self):
        """Open bundled HWiNFO and keep ThermalBench locked to tools/HWiNFO/hwinfo.csv."""
        try:
            self.hwinfo_csv = resolve_hwinfo_csv()
            self.hwinfo_exe = resolve_hwinfo_exe("")

            try:
                self.hwinfo_edit.setText(self.hwinfo_csv)
            except Exception:
                pass

            if not self.hwinfo_exe:
                QMessageBox.critical(
                    self,
                    "HWiNFO not found",
                    "Bundled HWiNFO was not found.\n\nExpected one of:\n"
                    f"{hwinfo_dir() / 'HWiNFO64.exe'}\n"
                    f"{hwinfo_dir() / 'HWiNFO.exe'}",
                )
                return

            launched = launch_hwinfo(self.hwinfo_exe)
            if not launched:
                QMessageBox.critical(
                    self,
                    "HWiNFO not found",
                    "Could not launch bundled HWiNFO.",
                )
                return

            self.append("Opened HWiNFO.")
            self.append(f"Set HWiNFO sensor logging CSV to: {self.hwinfo_csv}")

            try:
                self.sensors.refresh_csv_status()
            except Exception:
                pass

            self._update_run_button_state()

        except Exception as e:
            QMessageBox.critical(self, "Open HWiNFO failed", str(e))

    def _copy_hwinfo_folder_path(self):
        try:
            folder = str(Path(self.hwinfo_csv).parent)
            QApplication.clipboard().setText(folder)
            self.copy_hwinfo_path_btn.setText("Copied!")
            QTimer.singleShot(2000, lambda: self.copy_hwinfo_path_btn.setText("Copy Path"))
        except Exception as e:
            QMessageBox.critical(self, "Copy failed", str(e))

    def _toggle_tree_item(self, index):
        try:
            btn = self._runs_tree.property("_tb_last_button")
            if btn is not None and int(btn) == int(Qt.RightButton):
                return
        except Exception:
            pass

        try:
            if index is None or (hasattr(index, "isValid") and not index.isValid()):
                return

            # Make full-row click also update current index immediately.
            try:
                self._runs_tree.setCurrentIndex(index)
            except Exception:
                pass

            if self._runs_tree.isExpanded(index):
                self._runs_tree.collapse(index)
            else:
                self._runs_tree.expand(index)
        except Exception:
            pass

    def eventFilter(self, obj, event):
        """Track mouse button used in the results tree.

        We use this to ensure right-click does not trigger the same behavior as left-click
        (auto-expand/select/preview).
        """
        try:
            if obj is getattr(self, "hwinfo_edit", None) and event.type() == QEvent.Resize:
                btn = getattr(self, "copy_hwinfo_path_btn", None)
                if btn is not None:
                    bh = obj.height() - 10
                    bw = btn.sizeHint().width()
                    btn.setGeometry(obj.width() - bw - 5, 5, bw, bh)
        except Exception:
            pass
        try:
            if self._handle_shift_wheel_horizontal_scroll(obj, event):
                return True

            if event is not None and event.type() == QEvent.MouseButtonPress:
                click_target = self._case_name_click_target(obj, event)

                if self._case_name_popup.isVisible() and not self._is_case_name_popup_target(click_target):
                    self._dismiss_case_name_suggestions()

                try:
                    if bool(getattr(self, "_month_picker_overlay_visible", False)):
                        month_label_btn = getattr(self, "_runs_month_label", None)
                        if click_target is not month_label_btn and not self._is_month_picker_overlay_target(click_target):
                            self._hide_month_picker_overlay()
                except Exception:
                    pass

            if obj is getattr(self, "case_edit", None):
                if event is not None and event.type() in (QEvent.FocusIn, QEvent.MouseButtonPress):
                    QTimer.singleShot(0, lambda: self._show_case_name_suggestions(force=True))

            tree_for_tooltip = None
            try:
                all_trees = []
                if getattr(self, "_runs_tree", None) is not None:
                    all_trees.append(self._runs_tree)
                all_trees.extend(list(getattr(self, "_search_section_trees", []) or []))

                for candidate in all_trees:
                    try:
                        if candidate is not None and obj is candidate.viewport():
                            tree_for_tooltip = candidate
                            break
                    except Exception:
                        continue
            except Exception:
                tree_for_tooltip = None

            if tree_for_tooltip is not None:
                if event is not None and event.type() == QEvent.ToolTip:
                    try:
                        pos = event.position().toPoint()
                    except Exception:
                        pos = event.pos()

                    payload = self._runs_tree_tooltip_payload_at(tree_for_tooltip, pos)
                    text = str((payload or {}).get("text") or "")
                    if text:
                        idx = tree_for_tooltip.indexAt(pos)
                        rect = tree_for_tooltip.visualRect(idx)
                        self._show_runs_tree_tooltip(
                            tree_for_tooltip,
                            text,
                            viewport_pos=pos,
                            item_rect=rect,
                            rich_html=str((payload or {}).get("html") or ""),
                        )
                    else:
                        self._hide_runs_tree_tooltip()

                    try:
                        event.accept()
                    except Exception:
                        pass
                    return True

                if event is not None and event.type() in (QEvent.Leave, QEvent.Hide, QEvent.MouseButtonPress):
                    self._hide_runs_tree_tooltip()

                if event is not None and event.type() == QEvent.MouseButtonPress:
                    try:
                        tree_for_tooltip.setProperty("_tb_last_button", int(event.button()))
                    except Exception:
                        pass
        except Exception:
            pass

        return super().eventFilter(obj, event)

    def _runs_tree_index_to_path(self, idx, tree: QTreeView | None = None) -> str:
        try:
            if idx is None or (hasattr(idx, "isValid") and not idx.isValid()):
                return ""

            use_tree = tree
            if use_tree is None:
                use_tree = getattr(self, "_runs_tree", None)
            if use_tree is None:
                return ""

            model = use_tree.model()
            if model is None:
                return ""

            if hasattr(model, "mapToSource"):
                src = model.mapToSource(idx)
                source_model = model.sourceModel() if hasattr(model, "sourceModel") else None
                if source_model is not None and hasattr(source_model, "filePath"):
                    return str(source_model.filePath(src) or "")

            if hasattr(model, "filePath"):
                return str(model.filePath(idx) or "")
        except Exception:
            return ""

        return ""

    def _is_case_folder(self, p: Path) -> bool:
        try:
            if p is None:
                return False
            p = Path(p)
            if not p.exists() or not p.is_dir():
                return False
            rr = getattr(self, "_runs_root", None)
            if rr is None:
                return False
            try:
                return p.resolve().parent == Path(rr).resolve()
            except Exception:
                return p.parent == rr
        except Exception:
            return False

    def _on_runs_tree_context_menu(self, pos) -> None:
        """Right-click context menu for files/folders in the Results tree."""
        try:
            if getattr(self, "_runs_tree", None) is None:
                return

            idx = self._runs_tree.indexAt(pos)
            if idx is None or (hasattr(idx, "isValid") and not idx.isValid()):
                return

            # If the right-clicked row isn't selected, select it so actions apply to it.
            try:
                sm = self._runs_tree.selectionModel()
                if sm is not None and (not sm.isSelected(idx)):
                    sm.setCurrentIndex(
                        idx,
                        QItemSelectionModel.ClearAndSelect | QItemSelectionModel.Rows,
                    )
            except Exception:
                pass

            fpath = self._runs_tree_index_to_path(idx)
            if not fpath:
                return

            target = Path(fpath)
            is_case = self._is_case_folder(target)

            menu = QMenu(self)
            try:
                effective_mode = resolve_effective_theme_mode(self.theme_mode, QApplication.instance())
            except Exception:
                effective_mode = "dark"

            # Match the context menu to the active app theme so remove actions do not
            # fall back to a dark popup while the rest of the UI is light.
            try:
                menu.setStyleSheet(
                    (
                        """
                        QMenu {
                            background-color: #FFFFFF;
                            border: 1px solid #D0D0D0;
                            padding: 4px;
                        }
                        QMenu::item {
                            padding: 6px 22px;
                            color: #1A1A1A;
                        }
                        QMenu::item:selected {
                            background-color: #CFE4FF;
                        }
                        """
                        if effective_mode == "light"
                        else
                        """
                        QMenu {
                            background-color: #1E1E1E;
                            border: 1px solid rgba(128, 128, 128, 0.3);
                            padding: 4px;
                        }
                        QMenu::item {
                            padding: 6px 22px;
                            color: #EAEAEA;
                        }
                        QMenu::item:selected {
                            background-color: rgba(255,255,255,0.06);
                        }
                        """
                    )
                )
            except Exception:
                pass

            act_open_explorer = menu.addAction("Open in File Explorer")
            menu.addSeparator()
            act_rename = None
            if is_case:
                _proxy = getattr(self, "_runs_proxy", None)
                _is_compare_case = (
                    bool(_proxy.is_compare_case_dir_path(str(target)))
                    if _proxy is not None and hasattr(_proxy, "is_compare_case_dir_path")
                    else False
                )
                if not _is_compare_case:
                    act_rename = menu.addAction("Rename…")
            act_remove = menu.addAction("Remove")

            chosen = menu.exec(self._runs_tree.viewport().mapToGlobal(pos))
            if chosen is None:
                return

            if chosen == act_open_explorer:
                try:
                    import subprocess
                    # If target is a file, select it in Explorer; if a folder, open it.
                    explore_path = target if target.is_dir() else target.parent
                    if target.is_file():
                        subprocess.Popen(["explorer", "/select,", str(target)])
                    else:
                        subprocess.Popen(["explorer", str(explore_path)])
                except Exception:
                    pass
                return

            if chosen == act_remove:
                try:
                    if hasattr(self, "benchmark") and hasattr(self.benchmark, "remove_selected_tree_items"):
                        self.benchmark.remove_selected_tree_items()
                    elif hasattr(self, "benchmark") and hasattr(self.benchmark, "remove_selected_results"):
                        self.benchmark.remove_selected_results()
                except Exception:
                    pass
                return

            if act_rename is not None and chosen == act_rename:
                case_dir = target
                cur_name = case_dir.name
                new_name = ""
                ok = False
                try:
                    # Custom frameless rename dialog (no native white header/title bar).
                    dlg = QDialog(self)
                    try:
                        dlg.setWindowFlags(Qt.Dialog | Qt.FramelessWindowHint)
                    except Exception:
                        pass
                    dlg.setModal(True)

                    # Dim the main window while the dialog is open.
                    top = self.window() if hasattr(self, "window") else self
                    dim = None
                    try:
                        dim = DimOverlay(top, on_click=lambda: dlg.reject())
                        try:
                            dim.setGeometry(top.rect())
                        except Exception:
                            pass
                        dim.show()
                    except Exception:
                        dim = None

                    root = QVBoxLayout(dlg)
                    root.setContentsMargins(16, 14, 16, 14)
                    root.setSpacing(10)

                    lab = QLabel("New name:")
                    edit = QLineEdit()
                    edit.setText(cur_name)
                    try:
                        edit.selectAll()
                    except Exception:
                        pass

                    btn_row = QHBoxLayout()
                    btn_row.setContentsMargins(0, 4, 0, 0)
                    btn_row.setSpacing(8)
                    btn_row.addStretch(1)

                    ok_btn = QPushButton("OK")
                    cancel_btn = QPushButton("Cancel")
                    ok_btn.setDefault(True)
                    try:
                        ok_btn.clicked.connect(dlg.accept)
                        cancel_btn.clicked.connect(dlg.reject)
                    except Exception:
                        pass
                    btn_row.addWidget(ok_btn)
                    btn_row.addWidget(cancel_btn)

                    root.addWidget(lab)
                    root.addWidget(edit)
                    root.addLayout(btn_row)

                    # Style the dialog to match the current app theme.
                    try:
                        _eff = resolve_effective_theme_mode(self.theme_mode, QApplication.instance())
                        if _eff == "light":
                            _dlg_bg       = "#F7F7F7"
                            _dlg_border   = "rgba(0,0,0,0.15)"
                            _text         = "#111111"
                            _input_bg     = "#FFFFFF"
                            _input_border = "rgba(0,0,0,0.20)"
                            _input_focus  = "#2F6FEB"
                            _btn_bg       = "#FFFFFF"
                            _btn_border   = "rgba(0,0,0,0.18)"
                            _btn_hover    = "#EFEFEF"
                            _btn_pressed  = "#E4E4E4"
                        else:
                            _dlg_bg       = "#151515"
                            _dlg_border   = "rgba(128,128,128,0.35)"
                            _text         = "#EAEAEA"
                            _input_bg     = "#0F0F0F"
                            _input_border = "rgba(128,128,128,0.35)"
                            _input_focus  = "#5B9BFF"
                            _btn_bg       = "#252525"
                            _btn_border   = "rgba(128,128,128,0.35)"
                            _btn_hover    = "#2E2E2E"
                            _btn_pressed  = "#1F1F1F"

                        dlg.setStyleSheet(
                            f"""
                            QDialog {{
                                background-color: {_dlg_bg};
                                border: 1px solid {_dlg_border};
                                border-radius: 10px;
                            }}
                            QLabel {{ color: {_text}; background: transparent; }}
                            QLineEdit {{
                                background-color: {_input_bg};
                                color: {_text};
                                border: 1px solid {_input_border};
                                border-radius: 8px;
                                padding: 8px 10px;
                            }}
                            QLineEdit:focus {{
                                border: 1px solid {_input_focus};
                            }}
                            QPushButton {{
                                background: {_btn_bg};
                                border: 1px solid {_btn_border};
                                color: {_text};
                                padding: 6px 16px;
                                border-radius: 8px;
                            }}
                            QPushButton:hover   {{ background: {_btn_hover};   }}
                            QPushButton:pressed {{ background: {_btn_pressed}; }}
                            """
                        )
                    except Exception:
                        pass

                    # Make it wide enough for long names.
                    try:
                        dlg.setMinimumWidth(760)
                    except Exception:
                        pass

                    # Focus the textbox.
                    try:
                        edit.setFocus()
                    except Exception:
                        pass

                    try:
                        QTimer.singleShot(0, lambda: raise_center_and_focus(parent=top, dlg=dlg, dim_overlay=dim))
                    except Exception:
                        pass

                    ok = dlg.exec() == QDialog.Accepted
                    if ok:
                        new_name = str(edit.text() or "")

                    try:
                        if dim is not None:
                            dim.hide()
                            dim.deleteLater()
                    except Exception:
                        pass
                except Exception:
                    ok = False

                if not ok:
                    return

                new_name = str(new_name or "").strip()
                if not new_name or new_name == cur_name:
                    return
                if any(sep in new_name for sep in ("/", "\\")):
                    QMessageBox.warning(self, "Rename Folder", "Folder name cannot contain path separators.")
                    return

                dest = case_dir.parent / new_name
                try:
                    if dest.exists():
                        QMessageBox.warning(self, "Rename Folder", f"A folder named '{new_name}' already exists.")
                        return
                except Exception:
                    pass

                # Warn if this case folder's runs span more than one month.
                # Because there is only one physical folder on disk, renaming it
                # updates every month that shows it — make sure the user knows that.
                try:
                    source_model = getattr(self, "_runs_source_model", None)
                    months_spanned: set[str] = set()
                    for _run_ent in os.scandir(str(case_dir)):
                        if _run_ent.is_dir() and _RESULT_RUN_FOLDER_RE.match(_run_ent.name):
                            try:
                                _mkey = source_model._month_key_for_run(Path(_run_ent.path)) if source_model else None
                                if _mkey:
                                    months_spanned.add(_mkey)
                            except Exception:
                                pass
                    if len(months_spanned) > 1:
                        _month_labels = [_month_key_to_label(m) for m in sorted(months_spanned)]
                        _msg = (
                            f"<b>{html.escape(cur_name)}</b> contains runs from "
                            f"<b>{len(months_spanned)} different months</b>:<br><br>"
                            + "<br>".join(f"&nbsp;&nbsp;• {html.escape(lbl)}" for lbl in _month_labels)
                            + "<br><br>Because all months share the same folder on disk, "
                            "renaming it will update <b>every</b> month listed above.<br><br>"
                            "Do you want to continue?"
                        )
                        _warn_box = QMessageBox(self)
                        _warn_box.setWindowTitle("Rename Affects Multiple Months")
                        _warn_box.setTextFormat(Qt.RichText)
                        _warn_box.setText(_msg)
                        _warn_box.setStandardButtons(QMessageBox.Yes | QMessageBox.Cancel)
                        _warn_box.setDefaultButton(QMessageBox.Cancel)
                        if _warn_box.exec() != QMessageBox.Yes:
                            return
                except Exception:
                    pass  # if the check fails, proceed with the rename anyway

                try:
                    case_dir.rename(dest)
                except Exception as e:
                    QMessageBox.warning(self, "Rename Folder", f"Rename failed: {e}")
                    return

                # Keep compare results working by rewriting stored run references.
                try:
                    self._update_compare_manifests_for_case_rename(old_case=cur_name, new_case=new_name)
                except Exception:
                    pass

                # Keep compare folder names consistent (tree labels).
                try:
                    self._rename_compare_folders_for_case_rename(old_case=cur_name, new_case=new_name)
                except Exception:
                    pass

                # Rebuild the tree model from disk so the new name is visible
                # immediately without needing to navigate away and back.
                try:
                    self.benchmark._refresh_results_models()
                    self._refresh_results_month_nav()
                except Exception:
                    pass

                # Re-select renamed folder after the model has been refreshed.
                def _reselect():
                    try:
                        src_idx = self._runs_source_model.index_for_path(str(dest))
                        if src_idx is None or not src_idx.isValid():
                            return
                        view_idx = self._runs_proxy.mapFromSource(src_idx) if self._runs_proxy is not None else src_idx
                        if view_idx is None or (hasattr(view_idx, "isValid") and not view_idx.isValid()):
                            return
                        self._runs_tree.setCurrentIndex(view_idx)
                        try:
                            self._runs_tree.scrollTo(view_idx)
                        except Exception:
                            pass
                    except Exception:
                        pass

                QTimer.singleShot(0, _reselect)
                return

        except Exception:
            pass

    def _apply_live_split_ratio(self) -> None:
        try:
            sp = getattr(self, "_live_split", None)
            if sp is None:
                return

            w = max(1, sp.width())
            a, b = getattr(self, "_live_split_ratio", (0.34, 0.66))

            left = int(w * float(a))
            right = max(1, w - left)

            # Avoid splitter "drift"
            sp.blockSignals(True)
            try:
                sp.setSizes([left, right])
                try:
                    self._live_graph._relayout_visible_axes()
                    self._live_graph._update_phase_labels()
                    self._live_graph._canvas.draw_idle()
                except Exception:
                    pass
            finally:
                sp.blockSignals(False)
        except Exception:
            pass

    def _on_results_splitter_moved(self, *_args) -> None:
        try:
            if bool(getattr(self, "_month_picker_overlay_visible", False)):
                QTimer.singleShot(0, lambda: (
                    self._update_month_picker_overlay_geometry(),
                    self._rebuild_month_picker_overlay(),
                    self._update_month_picker_overlay_geometry(),
                    self._hide_month_picker_horizontal_scrollbar()
                ))
        except Exception:
            pass
        try:
            sp = getattr(self, "_results_split", None)
            if sp is None:
                return
            sizes = list(sp.sizes() or [])
            if len(sizes) < 2:
                return
            total = float(max(1, int(sizes[0]) + int(sizes[1])))
            a = float(max(0.05, min(0.95, float(sizes[0]) / total)))
            self._results_split_ratio = (a, 1.0 - a)
            self._results_split_user_set = True

            # Debounce graph canvas redraw until dragging stops.
            self._on_results_split_drag_moved()
        except Exception:
            pass

    def _set_results_preview_updates_enabled(self, enabled: bool) -> None:
        try:
            gp = getattr(self, "graph", None)
            if gp is None or not hasattr(gp, "get_canvas"):
                return
            canvas = gp.get_canvas()
            if canvas is None:
                return
            try:
                canvas.setUpdatesEnabled(bool(enabled))
            except Exception:
                pass

            if enabled:
                try:
                    canvas.update()
                except Exception:
                    pass
        except Exception:
            pass

    def _on_results_split_drag_moved(self) -> None:
        try:
            if not bool(getattr(self, "_results_split_dragging", False)):
                self._results_split_dragging = True
                self._set_results_preview_updates_enabled(False)
            t = getattr(self, "_results_split_drag_timer", None)
            if t is not None:
                # Consider drag "finished" after a short pause.
                t.start(120)
        except Exception:
            pass

    def _on_results_split_drag_finished(self) -> None:
        try:
            if not bool(getattr(self, "_results_split_dragging", False)):
                return
            self._results_split_dragging = False
            self._set_results_preview_updates_enabled(True)

            # Trigger a single redraw after the final size settles.
            try:
                gp = getattr(self, "graph", None)
                if gp is not None and hasattr(gp, "get_canvas"):
                    canvas = gp.get_canvas()
                    if canvas is not None and hasattr(canvas, "draw_idle"):
                        canvas.draw_idle()
            except Exception:
                pass
        except Exception:
            pass

    def _apply_results_split_ratio(self) -> None:
        try:
            sp = getattr(self, "_results_split", None)
            if sp is None:
                return

            # Only enforce while Results page is visible.
            try:
                if getattr(self, "_stack", None) is not None:
                    if self._stack.currentIndex() != getattr(self, "_page_results_index", -1):
                        return
            except Exception:
                pass

            w = max(1, sp.width())
            a, _b = getattr(self, "_results_split_ratio", (0.45, 0.55))
            left = max(220, int(w * float(a)))
            right = max(1, w - left)

            sp.blockSignals(True)
            try:
                sp.setSizes([left, right])
            finally:
                sp.blockSignals(False)
        except Exception:
            pass

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        try:
            if bool(getattr(self, "_month_picker_overlay_visible", False)):
                QTimer.singleShot(0, lambda: (
                    self._update_month_picker_overlay_geometry(),
                    self._rebuild_month_picker_overlay(),
                    self._update_month_picker_overlay_geometry(),
                    self._hide_month_picker_horizontal_scrollbar()
                ))
        except Exception:
            pass
        try:
            # only enforce while Live output is shown
            if self._output_stack is not None and self._output_stack.currentIndex() == 0:
                QTimer.singleShot(0, self._apply_live_split_ratio)
        except Exception:
            pass

        try:
            QTimer.singleShot(0, self._apply_results_split_ratio)
        except Exception:
            pass

        try:
            if not bool(getattr(self, "_restoring_window_state", False)) and not self.isMinimized():
                self._window_settings_timer.start(250)
        except Exception:
            pass

    def showEvent(self, event) -> None:
        super().showEvent(event)
        try:
            QTimer.singleShot(0, self._apply_window_corner_preference)
        except Exception:
            pass
        # Run once: check for updates silently 3 seconds after the window appears.
        if not getattr(self, "_startup_update_check_scheduled", False):
            self._startup_update_check_scheduled = True
            try:
                QTimer.singleShot(3000, self._startup_update_check)
            except Exception:
                pass

    def _apply_window_corner_preference(self) -> None:
        try:
            if not sys.platform.startswith("win"):
                return

            hwnd = int(self.winId())
            if not hwnd:
                return

            dwmapi = ctypes.windll.dwmapi
            preference = ctypes.c_int(_DWMWCP_DONOTROUND if self.isMaximized() or self.isFullScreen() else _DWMWCP_ROUND)
            result = dwmapi.DwmSetWindowAttribute(
                wintypes.HWND(hwnd),
                ctypes.c_uint(_DWMWA_WINDOW_CORNER_PREFERENCE),
                ctypes.byref(preference),
                ctypes.sizeof(preference),
            )
            self._rounded_corners_applied = (int(result) == 0)
        except Exception:
            self._rounded_corners_applied = False

    def nativeEvent(self, eventType, message):
        """Allow edge and corner resizing for the frameless main window on Windows."""
        try:
            if not sys.platform.startswith("win"):
                return super().nativeEvent(eventType, message)

            msg = _WinMsg.from_address(int(message))
            if int(msg.message) != self._WM_NCHITTEST:
                return super().nativeEvent(eventType, message)

            if self.isMaximized() or self.isFullScreen():
                return False, self._HTCLIENT

            border = int(getattr(self, "_resize_border_px", 8) or 8)
            if border <= 0:
                return super().nativeEvent(eventType, message)

            x = ctypes.c_short(int(msg.lParam) & 0xFFFF).value
            y = ctypes.c_short((int(msg.lParam) >> 16) & 0xFFFF).value

            # WM_NCHITTEST lParam is in physical screen pixels.
            # QWidget.frameGeometry() returns logical (device-independent) pixels.
            # Divide by DPR to bring physical coords into logical space.
            try:
                dpr = float(self.devicePixelRatio()) or 1.0
            except Exception:
                dpr = 1.0
            x_log = x / dpr
            y_log = y / dpr

            frame = self.frameGeometry()
            local_x = x_log - frame.left()
            local_y = y_log - frame.top()
            width = int(frame.width())
            height = int(frame.height())

            on_left = local_x < border
            on_right = local_x >= max(border, width - border)
            on_top = local_y < border
            on_bottom = local_y >= max(border, height - border)

            if on_top and on_left:
                return True, self._HTTOPLEFT
            if on_top and on_right:
                return True, self._HTTOPRIGHT
            if on_bottom and on_left:
                return True, self._HTBOTTOMLEFT
            if on_bottom and on_right:
                return True, self._HTBOTTOMRIGHT
            if on_left:
                return True, self._HTLEFT
            if on_right:
                return True, self._HTRIGHT
            if on_top:
                return True, self._HTTOP
            if on_bottom:
                return True, self._HTBOTTOM

            return False, self._HTCLIENT
        except Exception:
            return super().nativeEvent(eventType, message)
