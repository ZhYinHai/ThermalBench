# ui/runs_proxy_model.py
import json
import os
import re
from pathlib import Path

from PySide6.QtCore import QSortFilterProxyModel, QModelIndex, Qt
from PySide6.QtGui import QColor, QBrush

_RUN_FOLDER_RE = re.compile(
    r"^(?:"
    r"\d{8}_\d{6}"
    r"|(?:CPU|GPU|CPUGPU)_W\d+_L\d+_V\d+"
    # Compare result run folders (created by GUI):
    # "<case> CPU vs <case> GPU" or "<case> CPU vs <case> GPU vs <case> CPUGPU"
    # with an optional collision suffix appended to the final full name.
    r"|.+\s(?:CPU|GPU|CPUGPU)(?:\svs\s.+\s(?:CPU|GPU|CPUGPU))+(?:\s\+\d+)?"
    r")$",
    re.IGNORECASE,
)


class RunsProxyModel(QSortFilterProxyModel):
    """
    Proxy model that:
    - Removes ALL icons (DecorationRole)
        - Makes run folders behave like leaf nodes:
      - Their children are filtered out
      - hasChildren/canFetchMore are forced False so no expand arrow appears
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._folder_name_filter = ""

        # Display prefix for compare result folders in the tree.
        # Subtle but clearly distinct from normal run folders.
        self._compare_prefix = "↔ "
        # Cache signature -> bool. Keys are prefixed with "run:" or "case:".
        self._compare_dir_cache: dict[str, tuple[float, bool]] = {}
        self._compare_display_name_cache: dict[str, tuple[float, str]] = {}

        # Compare-selection highlighting (enabled only while a compare result is selected)
        self._active_compare_dir_norm: str | None = None
        self._compare_highlight_run_dirs_norm: dict[str, QColor] = {}
        self._active_compare_segment_colors: list[QColor] = []
        self._preview_current_dir_norm: str | None = None
        self._compare_selected_run_dirs_norm: set[str] = set()

        try:
            self.setDynamicSortFilter(True)
        except Exception:
            pass

    def set_folder_name_filter(self, text: str | None) -> None:
        try:
            new_value = str(text or "").strip().casefold()
        except Exception:
            new_value = ""

        if new_value == self._folder_name_filter:
            return

        self._folder_name_filter = new_value
        try:
            self.invalidateFilter()
        except Exception:
            self.invalidate()

    def folder_name_filter(self) -> str:
        try:
            return str(self._folder_name_filter or "")
        except Exception:
            return ""

    def _source_name_matches_folder_filter(self, source_index: QModelIndex) -> bool:
        filter_text = self.folder_name_filter()
        if not filter_text:
            return True

        try:
            if not source_index.isValid():
                return False

            sm = self.sourceModel()
            if sm is None:
                return False

            name = str(sm.fileName(source_index) or "").casefold()
            return filter_text in name
        except Exception:
            return True

    def _has_matching_descendant(self, source_index: QModelIndex) -> bool:
        filter_text = self.folder_name_filter()
        if not filter_text:
            return True

        try:
            if not source_index.isValid():
                return False

            sm = self.sourceModel()
            if sm is None:
                return False

            try:
                is_dir = bool(sm.isDir(source_index))
            except Exception:
                is_dir = False
            if not is_dir:
                return False

            child_count = int(sm.rowCount(source_index) or 0)
            for child_row in range(child_count):
                child_index = sm.index(child_row, 0, source_index)
                if self._source_name_matches_folder_filter(child_index):
                    return True
                if self._has_matching_descendant(child_index):
                    return True
        except Exception:
            return True

        return False

    def _has_matching_ancestor(self, source_index: QModelIndex) -> bool:
        filter_text = self.folder_name_filter()
        if not filter_text:
            return True

        try:
            parent_index = source_index.parent()
            while parent_index.isValid():
                if self._source_name_matches_folder_filter(parent_index):
                    return True
                parent_index = parent_index.parent()
        except Exception:
            return True

        return False

    @staticmethod
    def _norm_path(p: str) -> str:
        try:
            return os.path.normcase(os.path.abspath(str(p)))
        except Exception:
            return str(p or "")

    def clear_compare_highlights(self) -> None:
        try:
            self._active_compare_dir_norm = None
            self._compare_highlight_run_dirs_norm = {}
            self._active_compare_segment_colors = []
        except Exception:
            pass

        try:
            # Force repaint
            self.layoutChanged.emit()
        except Exception:
            pass

    def set_compare_highlights(self, *, compare_dir: str, runs_root: str) -> None:
        """Enable compare highlights based on compare_manifest.json.

        - Uses the same stable palette logic as GraphPreview compare mode.
        - Highlights run folders referenced by the compare manifest.
        """
        try:
            compare_dir_p = Path(str(compare_dir))
            runs_root_p = Path(str(runs_root))

            mp = compare_dir_p / "compare_manifest.json"
            if not mp.is_file():
                self.clear_compare_highlights()
                return

            try:
                m = json.loads(mp.read_text(encoding="utf-8"))
            except Exception:
                m = {}

            runs_rel = [str(r) for r in (m.get("runs") or []) if str(r).strip()]
            if not runs_rel:
                self.clear_compare_highlights()
                return

            def _build_palette() -> list[str]:
                fallback = [
                    "#1f77b4",
                    "#ff7f0e",
                    "#2ca02c",
                    "#d62728",
                    "#9467bd",
                    "#8c564b",
                ]
                try:
                    from matplotlib import cm
                    from matplotlib import colors as mcolors

                    cmaps = [cm.get_cmap("tab20"), cm.get_cmap("tab20b"), cm.get_cmap("tab20c")]
                    palette: list[str] = []
                    for cmap in cmaps:
                        for k in range(int(getattr(cmap, "N", 20) or 20)):
                            try:
                                palette.append(mcolors.to_hex(cmap(k)))
                            except Exception:
                                pass
                    return palette or fallback
                except Exception:
                    return fallback

            palette_hex = _build_palette()

            run_color: dict[str, QColor] = {}
            seg_colors: list[QColor] = []

            # IMPORTANT: assign colors in the same order GraphPreview uses (manifest order).
            for j, rel in enumerate(runs_rel):
                rel_norm = str(rel).replace("\\", "/")
                parts = [p for p in rel_norm.split("/") if p]
                if len(parts) < 2:
                    continue

                c = QColor(palette_hex[j % len(palette_hex)])
                abs_run = runs_root_p.joinpath(*parts)
                run_color[self._norm_path(str(abs_run))] = c
                seg_colors.append(c)

            self._active_compare_dir_norm = self._norm_path(str(compare_dir_p))
            self._compare_highlight_run_dirs_norm = dict(run_color)
            self._active_compare_segment_colors = list(seg_colors)

            try:
                self.layoutChanged.emit()
            except Exception:
                pass
        except Exception:
            self.clear_compare_highlights()

    def get_active_compare_dir_norm(self) -> str | None:
        try:
            return self._active_compare_dir_norm
        except Exception:
            return None

    def get_active_compare_segment_colors(self) -> list[QColor]:
        try:
            return list(self._active_compare_segment_colors or [])
        except Exception:
            return []

    def get_compare_case_color_map(self) -> dict[str, QColor]:
        """Backward-compat shim for older delegate logic."""
        return {}

    def set_preview_current_dir(self, path: str | None) -> None:
        try:
            new_value = self._norm_path(path) if str(path or "").strip() else None
            if new_value == self._preview_current_dir_norm:
                return
            self._preview_current_dir_norm = new_value
        except Exception:
            self._preview_current_dir_norm = None

        try:
            self.layoutChanged.emit()
        except Exception:
            pass

    def is_preview_current_dir_path(self, path: str) -> bool:
        try:
            return bool(self._preview_current_dir_norm and self._norm_path(path) == self._preview_current_dir_norm)
        except Exception:
            return False

    def set_compare_selected_dirs(self, paths: list[str] | set[str] | tuple[str, ...] | None) -> None:
        try:
            new_set = {
                self._norm_path(p)
                for p in (paths or [])
                if str(p or "").strip()
            }
            if new_set == self._compare_selected_run_dirs_norm:
                return
            self._compare_selected_run_dirs_norm = set(new_set)
        except Exception:
            self._compare_selected_run_dirs_norm = set()

        try:
            self.layoutChanged.emit()
        except Exception:
            pass

    def is_compare_selected_dir_path(self, path: str) -> bool:
        try:
            return self._norm_path(path) in (self._compare_selected_run_dirs_norm or set())
        except Exception:
            return False

    def _is_run_folder_source_index(self, source_index: QModelIndex) -> bool:
        try:
            if not source_index.isValid():
                return False
            sm = self.sourceModel()
            if sm is None:
                return False
            name = str(sm.fileName(source_index) or "")
            path = str(sm.filePath(source_index) or "")
            return bool(path) and bool(_RUN_FOLDER_RE.match(name))
        except Exception:
            return False

    def _is_compare_result_dir_path(self, p: str) -> bool:
        """True if `p` is either a compare run folder OR a compare case folder."""
        try:
            return bool(self._is_compare_run_dir_path(p) or self._is_compare_case_dir_path(p))
        except Exception:
            return False

    def _manifest_is_compare(self, manifest_path: Path) -> bool:
        try:
            m = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception:
            m = {}
        return str((m.get("type") or "")).strip().lower() == "compare"

    def _is_compare_run_dir_path(self, p: str) -> bool:
        """True if directory contains compare_manifest.json with type==compare."""
        try:
            pn = self._norm_path(p)
            key = f"run:{pn}"

            cached = (self._compare_dir_cache or {}).get(key)
            if cached is not None:
                return bool(cached[1])

            dp = Path(p)
            if not dp.is_dir():
                self._compare_dir_cache[key] = (0.0, False)
                return False

            mp = dp / "compare_manifest.json"
            if not mp.is_file():
                self._compare_dir_cache[key] = (0.0, False)
                return False

            try:
                sig = float(mp.stat().st_mtime or 0.0)
            except Exception:
                sig = 0.0

            is_compare = bool(self._manifest_is_compare(mp))
            self._compare_dir_cache[key] = (sig, is_compare)
            return is_compare
        except Exception:
            return False

    def _is_compare_case_dir_path(self, p: str) -> bool:
        """True if directory has an immediate child directory that is a compare run dir."""
        try:
            pn = self._norm_path(p)
            key = f"case:{pn}"

            cached = (self._compare_dir_cache or {}).get(key)
            if cached is not None:
                return bool(cached[1])

            dp = Path(p)
            if not dp.is_dir():
                self._compare_dir_cache[key] = (0.0, False)
                return False

            # If this is already a compare run dir, it's not a case dir.
            if (dp / "compare_manifest.json").is_file():
                self._compare_dir_cache[key] = (0.0, False)
                return False

            child_sig = -1.0
            is_compare_case = False
            try:
                for ent in dp.iterdir():
                    try:
                        if not ent.is_dir():
                            continue
                        mp = ent / "compare_manifest.json"
                        if not mp.is_file():
                            continue
                        if self._manifest_is_compare(mp):
                            is_compare_case = True
                            try:
                                child_sig = max(child_sig, float(mp.stat().st_mtime or 0.0))
                            except Exception:
                                child_sig = max(child_sig, 0.0)
                    except Exception:
                        continue
            except Exception:
                pass

            sig = float(child_sig)
            self._compare_dir_cache[key] = (sig, bool(is_compare_case))
            return bool(is_compare_case)
        except Exception:
            return False

    def is_compare_result_dir_path(self, p: str) -> bool:
        """Public helper for views/delegates: True if `p` is a compare-result directory."""
        return bool(self._is_compare_result_dir_path(str(p or "")))

    def is_compare_case_dir_path(self, p: str) -> bool:
        """Public helper: True if `p` is a compare case folder (parent of compare run dirs)."""
        return bool(self._is_compare_case_dir_path(str(p or "")))

    def is_compare_run_dir_path(self, p: str) -> bool:
        """Public helper: True if `p` is a compare run folder (contains compare_manifest.json)."""
        return bool(self._is_compare_run_dir_path(str(p or "")))

    def get_compare_prefix(self) -> str:
        """Public helper for delegates that want to draw a compare marker."""
        return str(self._compare_prefix or "")

    @staticmethod
    def _stress_label_for_run_name(run_name: str) -> str:
        try:
            m = re.match(r"^(CPU|GPU|CPUGPU)_W\d+_L\d+_V\d+$", str(run_name or ""), flags=re.IGNORECASE)
            if m:
                return str(m.group(1)).upper()
        except Exception:
            pass
        return str(run_name or "").strip()

    def _build_compare_display_names(self, manifest: dict) -> tuple[str, str]:
        try:
            case_name = str(manifest.get("display_case_name") or "").strip()
            run_name = str(manifest.get("display_run_name") or "").strip()
            if case_name and run_name:
                return case_name, run_name

            runs_rel = [str(r) for r in (manifest.get("runs") or []) if str(r).strip()]
            case_parts: list[str] = []
            run_parts: list[str] = []
            for rel in runs_rel:
                parts = [p for p in str(rel).replace("\\", "/").split("/") if p]
                if len(parts) < 2:
                    continue
                case = str(parts[-2]).strip()
                run = str(parts[-1]).strip()
                if case:
                    case_parts.append(case)
                stress = self._stress_label_for_run_name(run)
                run_parts.append(f"{case} {stress}".strip() if case else stress)

            if not case_name:
                case_name = " vs ".join([p for p in case_parts if p])
            if not run_name:
                run_name = " vs ".join([p for p in run_parts if p])
            return case_name, run_name
        except Exception:
            return "", ""

    def _compare_run_display_name(self, p: str) -> str:
        try:
            dp = Path(str(p or ""))
            mp = dp / "compare_manifest.json"
            if not mp.is_file():
                return ""

            key = f"run-name:{self._norm_path(str(dp))}"
            try:
                sig = float(mp.stat().st_mtime or 0.0)
            except Exception:
                sig = 0.0

            cached = self._compare_display_name_cache.get(key)
            if cached is not None and cached[0] == sig:
                return str(cached[1] or "")

            try:
                manifest = json.loads(mp.read_text(encoding="utf-8"))
            except Exception:
                manifest = {}
            _, run_name = self._build_compare_display_names(manifest)
            self._compare_display_name_cache[key] = (sig, run_name)
            return str(run_name or "")
        except Exception:
            return ""

    def _compare_case_display_name(self, p: str) -> str:
        try:
            dp = Path(str(p or ""))
            if not dp.is_dir():
                return ""

            key = f"case-name:{self._norm_path(str(dp))}"
            best_sig = -1.0
            cached = self._compare_display_name_cache.get(key)

            manifests: list[Path] = []
            try:
                for ent in dp.iterdir():
                    try:
                        if ent.is_dir() and (ent / "compare_manifest.json").is_file():
                            manifests.append(ent / "compare_manifest.json")
                    except Exception:
                        continue
            except Exception:
                manifests = []

            for mp in manifests:
                try:
                    best_sig = max(best_sig, float(mp.stat().st_mtime or 0.0))
                except Exception:
                    best_sig = max(best_sig, 0.0)

            if cached is not None and cached[0] == float(best_sig):
                return str(cached[1] or "")

            for mp in manifests:
                try:
                    manifest = json.loads(mp.read_text(encoding="utf-8"))
                except Exception:
                    manifest = {}
                case_name, _ = self._build_compare_display_names(manifest)
                if case_name:
                    self._compare_display_name_cache[key] = (float(best_sig), case_name)
                    return case_name

            self._compare_display_name_cache[key] = (float(best_sig), "")
            return ""
        except Exception:
            return ""

    # ---- icons off ----
    def data(self, index, role=Qt.DisplayRole):
        if role == Qt.DecorationRole:
            return None

        try:
            if role == Qt.DisplayRole and index is not None and index.isValid():
                src = self.mapToSource(index)
                sm = self.sourceModel()
                if sm is not None and src.isValid() and hasattr(sm, "filePath") and hasattr(sm, "isDir"):
                    p = str(sm.filePath(src) or "")
                    if p and bool(sm.isDir(src)):
                        if self._is_compare_run_dir_path(p):
                            display = self._compare_run_display_name(p)
                            if display:
                                return display
                        if self._is_compare_case_dir_path(p):
                            display = self._compare_case_display_name(p)
                            if display:
                                return display
        except Exception:
            pass

        # Highlight run folders referenced by the active compare selection.
        try:
            if role in (Qt.ForegroundRole, Qt.BackgroundRole):
                if index is not None and index.isValid():
                    src = self.mapToSource(index)
                    sm = self.sourceModel()
                    if sm is not None and src.isValid() and hasattr(sm, "filePath"):
                        p = str(sm.filePath(src) or "")
                        pn = self._norm_path(p)
                        col = (self._compare_highlight_run_dirs_norm or {}).get(pn)
                        if col is not None:
                            if role == Qt.ForegroundRole:
                                return QBrush(col)
                            if role == Qt.BackgroundRole:
                                bg = QColor(col)
                                bg.setAlpha(28)
                                return QBrush(bg)
        except Exception:
            pass

        return super().data(index, role)

    # ---- make run folders not expandable ----
    def filterAcceptsRow(self, source_row: int, source_parent: QModelIndex) -> bool:
        try:
            sm = self.sourceModel()
            if sm is None:
                return True

            source_index = sm.index(source_row, 0, source_parent)
            if not source_index.isValid():
                return False

            # keep run nodes as leaf nodes
            if self._is_run_folder_source_index(source_parent):
                return False

            return True
        except Exception:
            return True

    def hasChildren(self, parent: QModelIndex) -> bool:
        try:
            src_parent = self.mapToSource(parent) if parent.isValid() else QModelIndex()
            if self._is_run_folder_source_index(src_parent):
                return False
        except Exception:
            pass
        return super().hasChildren(parent)

    def canFetchMore(self, parent: QModelIndex) -> bool:
        try:
            src_parent = self.mapToSource(parent) if parent.isValid() else QModelIndex()
            if self._is_run_folder_source_index(src_parent):
                return False
        except Exception:
            pass
        return super().canFetchMore(parent)

    def fetchMore(self, parent: QModelIndex) -> None:
        try:
            src_parent = self.mapToSource(parent) if parent.isValid() else QModelIndex()
            if self._is_run_folder_source_index(src_parent):
                return
        except Exception:
            pass
        super().fetchMore(parent)

    # ---- sorting: keep compare cases at bottom ----
    def lessThan(self, left: QModelIndex, right: QModelIndex) -> bool:
        """Sort normal case folders before compare case folders.

        Compare results are stored as runs/<compare case>/<compare run>/compare_manifest.json.
        The compare *case* folder should be grouped at the bottom of the tree.
        """
        try:
            sm = self.sourceModel()
            if sm is not None and hasattr(sm, "filePath") and hasattr(sm, "isDir"):
                lp = str(sm.filePath(left) or "")
                rp = str(sm.filePath(right) or "")
                if lp and rp:
                    try:
                        l_is_dir = bool(sm.isDir(left))
                        r_is_dir = bool(sm.isDir(right))
                    except Exception:
                        l_is_dir = False
                        r_is_dir = False

                    # Only apply grouping for directories; fall back for files.
                    if l_is_dir and r_is_dir:
                        l_is_compare_case = bool(self._is_compare_case_dir_path(lp))
                        r_is_compare_case = bool(self._is_compare_case_dir_path(rp))
                        if l_is_compare_case != r_is_compare_case:
                            # False (normal) < True (compare) => normal first
                            return (not l_is_compare_case) and r_is_compare_case
        except Exception:
            pass

        return super().lessThan(left, right)
