import json
import os
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

_MONTHS_EN = (
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
)
from typing import Optional

from PySide6.QtCore import QAbstractItemModel, QModelIndex, Qt, QTimer

_RUN_FOLDER_RE = re.compile(
    r"^(?:"
    r"\d{8}_\d{6}"
    r"|(?:CPU|GPU|CPUGPU)_W\d+_L\d+_V\d+"
    r"|.+\s(?:CPU|GPU|CPUGPU)(?:\svs\s.+\s(?:CPU|GPU|CPUGPU))+(?:\s\+\d+)?"
    r")$",
    re.IGNORECASE,
)


@dataclass
class _TreeNode:
    kind: str  # root | month | case | run
    name: str
    path: str = ""
    parent: Optional["_TreeNode"] = None
    children: list["_TreeNode"] = field(default_factory=list)
    month_key: str = ""

    def row(self) -> int:
        if self.parent is None:
            return 0
        try:
            return self.parent.children.index(self)
        except ValueError:
            return 0


class MonthGroupedRunsModel(QAbstractItemModel):
    """
    Virtual tree model:

        <month>
            <case>
                <run>

    Disk structure remains:
        runs/<case>/<run>
    """

    def __init__(self, runs_root: Path, parent=None):
        super().__init__(parent)
        self._runs_root = Path(runs_root)
        self._root = _TreeNode(kind="root", name="root")
        self._path_to_node: dict[str, _TreeNode] = {}
        self._available_months: list[str] = []
        self._current_month: str = datetime.now().strftime("%Y-%m")
        self._folder_name_filter: str = ""
        QTimer.singleShot(0, self.refresh)

    # -------------------------
    # public API
    # -------------------------
    def refresh(self) -> None:
        self.beginResetModel()
        try:
            self._rebuild()
        except Exception:
            pass
        finally:
            self.endResetModel()

    def set_current_month(self, month_key: str) -> None:
        month_key = str(month_key or "").strip()
        if not month_key:
            return
        if month_key == self._current_month:
            return
        self._current_month = month_key
        self.refresh()

    def current_month(self) -> str:
        return str(self._current_month or "")

    def available_months(self) -> list[str]:
        return list(self._available_months)

    def set_folder_name_filter(self, text: str | None) -> None:
        new_value = str(text or "").strip().casefold()
        if new_value == self._folder_name_filter:
            return
        self._folder_name_filter = new_value
        self.refresh()

    def folder_name_filter(self) -> str:
        return str(self._folder_name_filter or "")

    # QFileSystemModel-like compatibility helpers
    def filePath(self, index: QModelIndex) -> str:
        node = self._node_from_index(index)
        return str(node.path or "")

    def fileName(self, index: QModelIndex) -> str:
        node = self._node_from_index(index)
        return str(node.name or "")

    def isDir(self, index: QModelIndex) -> bool:
        node = self._node_from_index(index)
        return node.kind in {"month", "case", "run"}

    def index_for_path(self, path: str) -> QModelIndex:
        norm = self._norm(path)
        node = self._path_to_node.get(norm)
        if node is None or node.parent is None:
            return QModelIndex()
        return self.createIndex(node.row(), 0, node)

    # -------------------------
    # Qt model
    # -------------------------
    def rowCount(self, parent=QModelIndex()) -> int:
        node = self._node_from_index(parent)
        return len(node.children)

    def columnCount(self, parent=QModelIndex()) -> int:
        return 1

    def index(self, row, column, parent=QModelIndex()) -> QModelIndex:
        if column != 0 or row < 0:
            return QModelIndex()

        parent_node = self._node_from_index(parent)
        if row >= len(parent_node.children):
            return QModelIndex()

        child = parent_node.children[row]
        return self.createIndex(row, column, child)

    def parent(self, index: QModelIndex) -> QModelIndex:
        if not index.isValid():
            return QModelIndex()

        node = self._node_from_index(index)
        parent_node = node.parent

        if parent_node is None or parent_node.kind == "root":
            return QModelIndex()

        return self.createIndex(parent_node.row(), 0, parent_node)

    def data(self, index: QModelIndex, role=Qt.DisplayRole):
        if not index.isValid():
            return None

        node = self._node_from_index(index)

        if role == Qt.DisplayRole:
            return node.name

        return None

    def flags(self, index: QModelIndex):
        if not index.isValid():
            return Qt.NoItemFlags
        return Qt.ItemIsEnabled | Qt.ItemIsSelectable

    def hasChildren(self, parent: QModelIndex) -> bool:
        node = self._node_from_index(parent)
        return bool(node.children)

    # -------------------------
    # internals
    # -------------------------
    def _node_from_index(self, index: QModelIndex) -> _TreeNode:
        if index.isValid():
            node = index.internalPointer()
            if isinstance(node, _TreeNode):
                return node
        return self._root

    def _rebuild(self) -> None:
        self._root.children.clear()
        self._path_to_node.clear()

        grouped: dict[str, dict[str, list[Path]]] = {}

        if self._runs_root.exists() and self._runs_root.is_dir():
            try:
                case_entries = list(os.scandir(str(self._runs_root)))
            except OSError:
                case_entries = []

            for case_ent in case_entries:
                if not case_ent.is_dir():
                    continue

                case_dir = Path(case_ent.path)
                case_name = case_dir.name

                try:
                    run_entries = list(os.scandir(case_ent.path))
                except OSError:
                    continue

                for run_ent in run_entries:
                    if not run_ent.is_dir():
                        continue

                    run_dir = Path(run_ent.path)
                    if not self._is_result_run_dir(run_dir):
                        continue

                    month_key = self._month_key_for_run(run_dir)
                    grouped.setdefault(month_key, {}).setdefault(case_name, []).append(run_dir)

        self._available_months = sorted(grouped.keys(), reverse=True)

        now_month = datetime.now().strftime("%Y-%m")
        if self._current_month not in grouped:
            if now_month in grouped:
                self._current_month = now_month
            elif self._available_months:
                self._current_month = self._available_months[0]
            else:
                self._current_month = now_month

        cases = grouped.get(self._current_month, {})
        for case_name in sorted(cases.keys(), key=str.casefold):
            run_dirs = sorted(cases[case_name], key=self._run_sort_key, reverse=True)

            filtered_runs = [
                rd for rd in run_dirs
                if self._matches_filter(case_name, rd.name)
            ]
            if not filtered_runs:
                continue

            case_path = self._runs_root / case_name
            case_node = _TreeNode(
                kind="case",
                name=case_name,
                path=str(case_path),
                month_key=self._current_month,
                parent=self._root,
            )
            self._root.children.append(case_node)
            self._path_to_node[self._norm(str(case_path))] = case_node

            for rd in filtered_runs:
                run_node = _TreeNode(
                    kind="run",
                    name=rd.name,
                    path=str(rd),
                    month_key=self._current_month,
                    parent=case_node,
                )
                case_node.children.append(run_node)
                self._path_to_node[self._norm(str(rd))] = run_node

    def _matches_filter(self, case_name: str, run_name: str) -> bool:
        filt = self._folder_name_filter
        if not filt:
            return True
        hay = f"{case_name} {run_name}".casefold()
        return filt in hay

    def _is_result_run_dir(self, run_dir: Path) -> bool:
        """True for normal result folders and compare result folders.

        Compare run folder names can receive extra collision suffixes, especially
        for 3+ source-run compares. The manifest is the authoritative marker.
        """
        try:
            if _RUN_FOLDER_RE.match(run_dir.name):
                return True
            mp = run_dir / "compare_manifest.json"
            if not mp.is_file():
                return False
            try:
                payload = json.loads(mp.read_text(encoding="utf-8"))
            except Exception:
                payload = {}
            return str((payload or {}).get("type") or "").strip().lower() == "compare"
        except Exception:
            return False

    def _month_key_for_run(self, run_dir: Path) -> str:
        return self._run_datetime(run_dir).strftime("%Y-%m")

    def _run_sort_key(self, run_dir: Path) -> datetime:
        return self._run_datetime(run_dir)

    def _run_datetime(self, run_dir: Path) -> datetime:
        settings_path = run_dir / "test_settings.json"
        if settings_path.is_file():
            try:
                payload = json.loads(settings_path.read_text(encoding="utf-8"))
                recorded_at = str(payload.get("recorded_at") or "").strip()
                if recorded_at:
                    return datetime.fromisoformat(recorded_at.replace("Z", "+00:00")).replace(tzinfo=None)
            except Exception:
                pass

        manifest_path = run_dir / "compare_manifest.json"
        if manifest_path.is_file():
            try:
                payload = json.loads(manifest_path.read_text(encoding="utf-8"))
                created_at = str(payload.get("created_at") or "").strip()
                if created_at:
                    return datetime.fromisoformat(created_at.replace("Z", "+00:00")).replace(tzinfo=None)
            except Exception:
                pass

        try:
            return datetime.strptime(run_dir.name, "%Y%m%d_%H%M%S")
        except Exception:
            pass

        try:
            return datetime.fromtimestamp(run_dir.stat().st_mtime)
        except Exception:
            return datetime.now()

    @staticmethod
    def _pretty_month(month_key: str) -> str:
        try:
            y, m = month_key.split("-", 1)
            return f"{_MONTHS_EN[int(m) - 1]} {y}"
        except Exception:
            return str(month_key)

    @staticmethod
    def _norm(path: str) -> str:
        try:
            return os.path.normcase(os.path.abspath(str(path)))
        except Exception:
            return str(path or "")
