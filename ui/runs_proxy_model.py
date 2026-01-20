# ui/runs_proxy_model.py
import re

from PySide6.QtCore import QSortFilterProxyModel, QModelIndex, Qt

_RUN_FOLDER_RE = re.compile(
    r"^(?:"
    r"\d{8}_\d{6}"
    r"|(?:CPU|GPU|CPUGPU)_W\d+_L\d+_V\d+"
    # Compare result folders (created by GUI): "<case> CPU vs <case> CPUGPU" (+ optional suffix)
    r"|.+\s(?:CPU|GPU|CPUGPU)\svs\s.+\s(?:CPU|GPU|CPUGPU)(?:\s\+\d+)?"
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

    # ---- icons off ----
    def data(self, index, role=Qt.DisplayRole):
        if role == Qt.DecorationRole:
            return None
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
