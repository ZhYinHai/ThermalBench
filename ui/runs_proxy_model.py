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
    # Compare result folders (created by GUI): "<case> CPU vs <case> CPUGPU" (+ optional suffix)
    r"|.+\s(?:CPU|GPU|CPUGPU)\svs\s.+\s(?:CPU|GPU|CPUGPU)(?:\s*\+\d+)*"
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

        # Display prefix for compare result folders in the tree.
        # Subtle but clearly distinct from normal run folders.
        self._compare_prefix = "↔ "
        # Cache signature -> bool. Keys are prefixed with "run:" or "case:".
        self._compare_dir_cache: dict[str, tuple[float, bool]] = {}

        # Compare-selection highlighting (enabled only while a compare result is selected)
        self._active_compare_dir_norm: str | None = None
        self._compare_highlight_run_dirs_norm: dict[str, QColor] = {}
        self._active_compare_segment_colors: list[QColor] = []

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

    def _is_run_folder_source_index(self, source_index: QModelIndex) -> bool:
        try:
            if not source_index.isValid():
                return False
            sm = self.sourceModel()
            if sm is None:
                return False
            name = str(sm.fileName(source_index) or "")
            return bool(_RUN_FOLDER_RE.match(name))
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
            dp = Path(p)
            if not dp.is_dir():
                return False

            mp = dp / "compare_manifest.json"
            if not mp.is_file():
                return False

            pn = self._norm_path(p)
            key = f"run:{pn}"

            try:
                sig = float(mp.stat().st_mtime or 0.0)
            except Exception:
                sig = 0.0

            cached = (self._compare_dir_cache or {}).get(key)
            if cached is not None and cached[0] == sig:
                return bool(cached[1])

            is_compare = bool(self._manifest_is_compare(mp))
            self._compare_dir_cache[key] = (sig, is_compare)
            return is_compare
        except Exception:
            return False

    def _is_compare_case_dir_path(self, p: str) -> bool:
        """True if directory has an immediate child directory that is a compare run dir."""
        try:
            dp = Path(p)
            if not dp.is_dir():
                return False

            # If this is already a compare run dir, it's not a case dir.
            if (dp / "compare_manifest.json").is_file():
                return False

            pn = self._norm_path(p)
            key = f"case:{pn}"

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
            cached = (self._compare_dir_cache or {}).get(key)
            if cached is not None and cached[0] == sig:
                return bool(cached[1])

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

    # ---- icons off ----
    def data(self, index, role=Qt.DisplayRole):
        if role == Qt.DecorationRole:
            return None

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
        # If the parent is a run folder, hide ALL its children
        try:
            if self._is_run_folder_source_index(source_parent):
                return False
        except Exception:
            pass
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
