# benchmark_controller.py
"""Benchmark execution and results browsing component."""

import os
import re
import shutil
import time
import json
from pathlib import Path
from datetime import datetime
from typing import Optional

from PySide6.QtCore import QProcess, QTimer, QItemSelectionModel, Qt
from PySide6.QtWidgets import QTreeView, QFileSystemModel, QMessageBox

from core.ps_helpers import RUNMAP_RE, ps_quote, build_ps_array_literal

from core.resources import resource_path
from core.hwinfo_metadata import load_sensor_map
from ui.graph_preview.graph_plot_helpers import load_run_csv_dataframe
from ui.graph_preview.graph_plot_helpers import extract_unit_from_column, get_measurement_type_label
from ui.graph_preview.legend_popup_helpers import raise_center_and_focus
from ui.graph_preview.ui_compare_popup import ComparePopup
from ui.graph_preview.ui_dim_overlay import DimOverlay

_RUN_FOLDER_RE = re.compile(
    r"^(?:"
    r"\d{8}_\d{6}"
    r"|(?:CPU|GPU|CPUGPU)_W\d+_L\d+_V\d+"
    # Compare result folders (created by GUI): "<case> CPU vs <case> CPUGPU" (+ optional suffix)
    r"|.+\s(?:CPU|GPU|CPUGPU)\svs\s.+\s(?:CPU|GPU|CPUGPU)(?:\s\+\d+)?"
    r")$",
    re.IGNORECASE,
)


class BenchmarkController:
    """Manages benchmark execution, process control, and results browsing."""

    def __init__(
        self,
        parent,
        log_widget,
        run_btn,
        abort_btn,
        open_btn,
        live_timer,
        remove_btn,
        compare_btn,
        runs_tree: QTreeView,
        runs_model,
        runs_source_model: QFileSystemModel,
        runs_root: Path,
        graph_preview,
        sensor_manager,
        save_settings_callback,
        get_settings_callback,
        append_log_callback,
        on_run_started=None,
        on_run_finished=None,
        on_log_started=None,
        on_log_finished=None,
        on_ambient_csv=None,
    ):
        """
        runs_model: proxy model (or QFileSystemModel)
        runs_source_model: QFileSystemModel (source)
        """
        self.parent = parent
        self._log = log_widget
        self._run_btn = run_btn
        self._abort_btn = abort_btn
        self._open_btn = open_btn
        self._live_timer = live_timer
        self._remove_btn = remove_btn
        self._compare_btn = compare_btn
        self._runs_tree = runs_tree
        self._runs_model = runs_model
        self._runs_source_model = runs_source_model
        self._runs_root = runs_root
        self._graph_preview = graph_preview
        self._sensor_manager = sensor_manager

        # Debounced preview scheduling: selecting a result can trigger heavy CSV parsing
        # + matplotlib replot. Scheduling it to the next tick gives immediate UI feedback
        # (selection highlight) and collapses rapid selection changes into one preview.
        self._pending_preview_target: Optional[str] = None
        self._pending_preview_is_dir: bool = False
        self._preview_debounce_timer: Optional[QTimer] = None
        self._save_settings = save_settings_callback
        self._get_settings = get_settings_callback
        self._append_log = append_log_callback
        self._on_run_started = on_run_started
        self._on_run_finished = on_run_finished
        self._on_log_started = on_log_started
        self._on_log_finished = on_log_finished
        self._on_ambient_csv = on_ambient_csv

        # Prevent double plotting when we programmatically set selection
        self._suppress_selection_preview = False

        # Cache: latest result folder (avoid expensive scans on every tab switch)
        self._latest_cached_folder: Optional[Path] = None
        self._latest_cached_mtime: float = 0.0
        self._latest_cached_at_ts: float = 0.0
        self._latest_scan_cooldown_sec: float = 5.0

        # Remember last user-selected path (if user clicked something in the tree)
        self._last_selected_path: Optional[Path] = None

        # Compare selection + popup
        self._compare_selected_dirs: set[Path] = set()
        self._compare_restoring = False
        self._compare_popup: Optional[ComparePopup] = None
        self._compare_dim_overlay: Optional[DimOverlay] = None

        # Process for running benchmarks
        try:
            self.proc = QProcess(self.parent)
            self.proc.readyReadStandardOutput.connect(self.on_stdout)
            self.proc.readyReadStandardError.connect(self.on_stderr)
            self.proc.finished.connect(self.on_finished)
        except Exception:
            self.proc = None

        # Last run directory
        self.last_run_dir = None

        # Live timer
        self._timer = QTimer(self.parent)
        self._timer.setInterval(250)
        self._timer.timeout.connect(self._tick_timer)
        self._run_started_at = None
        self._warmup_total = 0
        self._log_total = 0

        # Pending timer values (set when process starts)
        self._pending_warm = 0
        self._pending_log = 0
        self._timer_started = False

        # Pending per-run settings (persisted into the run folder once known)
        self._pending_run_settings: dict = {}

        # Connect results tree selection
        try:
            self._runs_tree.selectionModel().selectionChanged.connect(self._on_runs_selection_changed)
        except Exception:
            pass

        try:
            self._runs_tree.selectionModel().currentChanged.connect(self._on_runs_current_changed)
        except Exception:
            pass

        self._update_remove_btn_state()
        self._update_compare_btn_state()

    def _schedule_preview_target(self, *, fpath: str, is_dir: bool) -> None:
        try:
            self._pending_preview_target = str(fpath)
            self._pending_preview_is_dir = bool(is_dir)

            if self._preview_debounce_timer is None:
                t = QTimer(self.parent)
                t.setSingleShot(True)
                try:
                    t.setTimerType(Qt.PreciseTimer)
                except Exception:
                    pass
                t.timeout.connect(self._apply_pending_preview_target)
                self._preview_debounce_timer = t

            # 0ms => next event-loop turn (lets selection paint first)
            self._preview_debounce_timer.start(0)
        except Exception:
            # Fallback: run immediately
            try:
                if is_dir:
                    self._graph_preview.preview_folder(str(fpath))
                else:
                    self._graph_preview.preview_path(str(fpath))
            except Exception:
                pass

    def _apply_pending_preview_target(self) -> None:
        tgt = None
        is_dir = False
        try:
            tgt = self._pending_preview_target
            is_dir = bool(self._pending_preview_is_dir)
        except Exception:
            tgt = None

        if not tgt:
            return

        # If we're suppressing previews (during programmatic selection changes), bail.
        if getattr(self, "_suppress_selection_preview", False):
            return

        try:
            if is_dir:
                self._graph_preview.preview_folder(str(tgt))
            else:
                self._graph_preview.preview_path(str(tgt))
        except Exception:
            pass

    # -------------------------------------------------------------------------
    # compare + selection helpers
    # -------------------------------------------------------------------------
    def _run_folder_from_index(self, idx) -> Optional[Path]:
        try:
            if idx is None or (hasattr(idx, "isValid") and not idx.isValid()):
                return None

            fpath = self._idx_to_path(idx)
            if not fpath:
                return None

            p = Path(fpath)
            run_dir = p if p.is_dir() else p.parent
            if not run_dir.exists() or not run_dir.is_dir():
                return None

            try:
                root = self._runs_root.resolve()
                run_dir.resolve().relative_to(root)
            except Exception:
                return None

            if not _RUN_FOLDER_RE.match(run_dir.name):
                return None

            return run_dir
        except Exception:
            return None

    def _selected_run_folders(self) -> set[Path]:
        try:
            sm = self._runs_tree.selectionModel()
            if sm is None:
                return set()

            rows = []
            try:
                rows = sm.selectedRows(0)
            except Exception:
                rows = [i for i in sm.selectedIndexes() if getattr(i, "column", lambda: 0)() == 0]

            out: set[Path] = set()
            for idx in rows:
                rd = self._run_folder_from_index(idx)
                if rd is not None:
                    out.add(rd)
            return out
        except Exception:
            return set()

    def _apply_compare_selection_to_view(self) -> None:
        if self._compare_restoring:
            return

        sm = None
        try:
            sm = self._runs_tree.selectionModel()
        except Exception:
            sm = None
        if sm is None:
            return

        self._compare_restoring = True
        try:
            try:
                sm.clearSelection()
            except Exception:
                try:
                    sm.clear()
                except Exception:
                    pass

            for run_dir in sorted(self._compare_selected_dirs, key=lambda p: p.name):
                idx = self._path_to_proxy_index(str(run_dir))
                if idx is None or (hasattr(idx, "isValid") and not idx.isValid()):
                    continue
                try:
                    sm.select(idx, QItemSelectionModel.Select | QItemSelectionModel.Rows)
                except Exception:
                    pass
        finally:
            self._compare_restoring = False

    def _update_compare_btn_state(self) -> None:
        try:
            if self._compare_btn is None:
                return

            # Guard: don't allow comparing already-compared results.
            # Compare-result folders are created by this app and contain compare_manifest.json.
            valid = [p for p in (self._compare_selected_dirs or set()) if not self._is_compare_result_dir(p)]
            self._compare_btn.setEnabled(len(valid) >= 2)
        except Exception:
            pass

    @staticmethod
    def _is_compare_result_dir(run_dir: Path) -> bool:
        try:
            if run_dir is None:
                return False
            p = Path(run_dir)
            if not p.exists() or not p.is_dir():
                return False
            mp = p / "compare_manifest.json"
            return mp.is_file()
        except Exception:
            return False

    @staticmethod
    def _sort_sensors_for_compare(sensors: list[str], group_map: dict[str, str] | None = None) -> list[str]:
        """Sort sensor column names for compare plotting.

        Ordering:
        1) Measurement type (Temperature first, then Power/RPM/Voltage/Percentage/Clock/Timing/other)
        2) Within a type, prioritize CPU -> GPU -> Ambient -> other (using name and/or group_map)
        3) Finally, stable alphabetical by sensor name
        """

        gm = dict(group_map or {})

        type_prio = {
            "Temperature": 0,
            "Power (W)": 1,
            "RPM": 2,
            "Voltage (V)": 3,
            "Percentage (%)": 4,
            "Clock (MHz)": 5,
            "Timing (T)": 6,
        }

        def _bucket_text(name: str) -> str:
            # Use both the column name and its HWiNFO group title for better classification.
            try:
                grp = str(gm.get(name) or "")
            except Exception:
                grp = ""
            return f"{name} {grp}".lower()

        def _device_subprio(name: str) -> int:
            t = _bucket_text(name)
            # CPU-ish
            if "cpu" in t or "package" in t or "ccd" in t or "tctl" in t:
                return 0
            # GPU-ish
            if "gpu" in t:
                return 1
            # Ambient
            if "ambient" in t or "room" in t:
                return 2
            return 3

        def _type_label(name: str) -> str:
            try:
                unit = extract_unit_from_column(name)
                return str(get_measurement_type_label(unit))
            except Exception:
                return "[other]"

        def _sort_key(name: str):
            tl = _type_label(name)
            return (
                int(type_prio.get(tl, 99)),
                int(_device_subprio(name)),
                str(name).lower(),
            )

        try:
            return sorted([str(s) for s in (sensors or []) if str(s).strip()], key=_sort_key)
        except Exception:
            return [str(s) for s in (sensors or []) if str(s).strip()]

    def toggle_compare_selection_for_index(self, idx) -> None:
        """Double-click handler: toggles a run folder in the compare selection set."""
        run_dir = self._run_folder_from_index(idx)
        if run_dir is None:
            return

        # Don't allow selecting existing compare-results as inputs.
        # If one is somehow present from an older version, prune it.
        if self._is_compare_result_dir(run_dir):
            try:
                self._compare_selected_dirs.discard(run_dir)
            except Exception:
                pass
            self._apply_compare_selection_to_view()
            self._update_compare_btn_state()
            return

        try:
            if run_dir in self._compare_selected_dirs:
                self._compare_selected_dirs.remove(run_dir)
            else:
                self._compare_selected_dirs.add(run_dir)
        except Exception:
            pass

        self._apply_compare_selection_to_view()
        self._update_compare_btn_state()

    def _ensure_compare_overlay(self, top) -> None:
        try:
            if top is None:
                return
            if self._compare_dim_overlay is None or self._compare_dim_overlay.parentWidget() is not top:
                try:
                    if self._compare_dim_overlay is not None:
                        self._compare_dim_overlay.deleteLater()
                except Exception:
                    pass
                self._compare_dim_overlay = DimOverlay(top, on_click=self._close_compare_popup)

            try:
                self._compare_dim_overlay.setGeometry(top.rect())
            except Exception:
                pass
        except Exception:
            pass

    def _set_compare_dimmed(self, on: bool) -> None:
        try:
            if on:
                top = self.parent.window() if hasattr(self.parent, "window") else self.parent
                self._ensure_compare_overlay(top)
                if self._compare_dim_overlay is not None:
                    self._compare_dim_overlay.show()
                    self._compare_dim_overlay.raise_()
            else:
                if self._compare_dim_overlay is not None:
                    self._compare_dim_overlay.hide()
        except Exception:
            pass

    def _close_compare_popup(self) -> None:
        try:
            if self._compare_popup is not None and self._compare_popup.isVisible():
                self._compare_popup.close()
        except Exception:
            pass
        self._set_compare_dimmed(False)
        self._compare_popup = None

    def compare_selected_results(self) -> None:
        """Show sensors that exist in ALL compare-selected results."""
        try:
            if len(self._compare_selected_dirs) < 2:
                return

            run_dirs = list(sorted(self._compare_selected_dirs, key=lambda p: p.name))

            sensor_sets: list[set[str]] = []
            for rd in run_dirs:
                csvp = rd / "run_window.csv"
                if not csvp.exists():
                    sensor_sets.append(set())
                    continue
                try:
                    _df, cols = load_run_csv_dataframe(str(csvp))
                    sensor_sets.append(set(cols or []))
                except Exception:
                    sensor_sets.append(set())

            common: set[str] = set()
            if sensor_sets:
                common = set.intersection(*sensor_sets) if sensor_sets else set()

            title = f"Compare ({len(run_dirs)} results)"

            # Best-effort: group sensors by their HWiNFO device group (cached from SM2).
            # This is the same device grouping used by the sensor picker dialogs.
            group_map: dict[str, str] = {}
            try:
                payload = load_sensor_map(resource_path("resources", "sensor_map.json"))
                if isinstance(payload, dict) and payload.get("schema") == 1:
                    group_map = dict(payload.get("mapping") or {})
            except Exception:
                group_map = {}

            top = self.parent.window() if hasattr(self.parent, "window") else self.parent
            self._set_compare_dimmed(True)

            self._compare_popup = ComparePopup(
                top,
                title=title,
                sensors=sorted(common),
                group_map=group_map,
                on_close=self._close_compare_popup,
                on_compare=self._create_compare_result_from_popup,
            )

            self._compare_popup.show()

            def _after_show():
                if self._compare_popup is None:
                    return
                try:
                    self._compare_popup.adjustSize()
                except Exception:
                    pass
                self._ensure_compare_overlay(top)
                raise_center_and_focus(parent=top, dlg=self._compare_popup, dim_overlay=self._compare_dim_overlay)

            QTimer.singleShot(0, _after_show)

        except Exception:
            self._close_compare_popup()

    def _create_compare_result_from_popup(self, sensors: list[str]) -> None:
        """Create a compare result folder and select it in the tree."""
        try:
            sensors = [str(s) for s in (sensors or []) if str(s).strip()]
            if not sensors:
                return

            # Sort sensors so compare plots are consistently ordered.
            try:
                group_map: dict[str, str] = {}
                payload = load_sensor_map(resource_path("resources", "sensor_map.json"))
                if isinstance(payload, dict) and payload.get("schema") == 1:
                    group_map = dict(payload.get("mapping") or {})
                sensors = self._sort_sensors_for_compare(sensors, group_map=group_map)
            except Exception:
                sensors = self._sort_sensors_for_compare(sensors)

            run_dirs = list(sorted(self._compare_selected_dirs, key=lambda p: p.name))
            if len(run_dirs) < 2:
                return

            def _case_label(rd: Path) -> str:
                try:
                    return (rd.parent.name if rd.parent is not None else "") or ""
                except Exception:
                    return ""

            def _stress_label(rd: Path) -> str:
                """Return CPU/GPU/CPUGPU, best-effort."""
                try:
                    m = re.match(r"^(CPU|GPU|CPUGPU)_W\d+_L\d+_V\d+$", str(rd.name), flags=re.IGNORECASE)
                    if m:
                        return str(m.group(1)).upper()
                except Exception:
                    pass

                # Fallback: try test_settings.json (older/different naming)
                try:
                    p = rd / "test_settings.json"
                    if p.exists():
                        s = json.loads(p.read_text(encoding="utf-8"))
                        sm = str((s or {}).get("stress_mode") or "").upper()
                        if "CPU" in sm and "GPU" in sm:
                            return "CPUGPU"
                        if "GPU" in sm:
                            return "GPU"
                        if "CPU" in sm:
                            return "CPU"
                except Exception:
                    pass

                return "CPU"

            def _compare_run_dir_name(rd_a: Path, rd_b: Path) -> str:
                a_case = _case_label(rd_a)
                b_case = _case_label(rd_b)
                a_stress = _stress_label(rd_a)
                b_stress = _stress_label(rd_b)
                left = f"{a_case} {a_stress}".strip() if a_case else a_stress
                right = f"{b_case} {b_stress}".strip() if b_case else b_stress
                return f"{left} vs {right}"

            case_a = _case_label(run_dirs[0]) or run_dirs[0].name
            case_b = _case_label(run_dirs[1]) or run_dirs[1].name
            if len(run_dirs) == 2:
                case_name = f"{case_a} vs {case_b}"
            else:
                case_name = f"{case_a} vs {case_b} +{len(run_dirs) - 2}"

            out_case_dir = (self._runs_root / case_name)
            out_case_dir.mkdir(parents=True, exist_ok=True)

            base_run_name = _compare_run_dir_name(run_dirs[0], run_dirs[1])
            if len(run_dirs) != 2:
                base_run_name = f"{base_run_name} +{len(run_dirs) - 2}"

            out_run_dir = out_case_dir / base_run_name
            if out_run_dir.exists():
                # Avoid collisions while keeping names readable.
                i = 1
                while True:
                    cand = out_case_dir / f"{base_run_name} +{i}"
                    if not cand.exists():
                        out_run_dir = cand
                        break
                    i += 1

            out_run_dir.mkdir(parents=True, exist_ok=True)

            # Store run paths relative to runs root so previews are portable.
            runs_rel: list[str] = []
            for rd in run_dirs:
                try:
                    runs_rel.append(str(rd.resolve().relative_to(self._runs_root.resolve())).replace("\\", "/"))
                except Exception:
                    # fallback: best-effort, still normalized
                    runs_rel.append(str(rd).replace("\\", "/"))

            manifest = {
                "type": "compare",
                "created_at": datetime.now().isoformat(timespec="seconds"),
                "trim": "elapsed_duration_shortest",
                "runs": runs_rel,
                "sensors": sensors,
            }

            mpath = out_run_dir / "compare_manifest.json"
            try:
                mpath.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
            except Exception:
                # keep going; preview will fail gracefully
                pass

            # Close popup + undim
            try:
                if self._compare_popup is not None:
                    self._compare_popup.close()
            except Exception:
                pass
            self._close_compare_popup()

            # Compare is complete; clear compare-input selection so the new compare result
            # can remain selected/previewable in the results tree.
            try:
                self._compare_selected_dirs.clear()
            except Exception:
                self._compare_selected_dirs = set()
            self._update_compare_btn_state()

            # Select and preview the new compare run
            try:
                idx = self._path_to_proxy_index(str(out_run_dir))
                if idx is not None and (not hasattr(idx, "isValid") or idx.isValid()):
                    sm = self._runs_tree.selectionModel()
                    if sm is not None:
                        self._suppress_selection_preview = True
                        try:
                            sm.setCurrentIndex(
                                idx,
                                QItemSelectionModel.ClearAndSelect | QItemSelectionModel.Rows,
                            )
                            try:
                                self._runs_tree.scrollTo(idx)
                            except Exception:
                                pass
                        finally:
                            self._suppress_selection_preview = False
            except Exception:
                pass

            try:
                self._graph_preview.preview_folder(str(out_run_dir))
            except Exception:
                pass

        except Exception:
            self._close_compare_popup()

    # -------------------------------------------------------------------------
    # index -> filesystem path helpers (proxy-safe)
    # -------------------------------------------------------------------------
    def _idx_to_path(self, idx) -> str:
        try:
            if idx is None or (hasattr(idx, "isValid") and not idx.isValid()):
                return ""
            # if proxy, map to source
            if hasattr(self._runs_model, "mapToSource") and self._runs_source_model is not None:
                src_idx = self._runs_model.mapToSource(idx)
                return self._runs_source_model.filePath(src_idx)
            # if direct fs model
            if hasattr(self._runs_model, "filePath"):
                return self._runs_model.filePath(idx)
        except Exception:
            return ""
        return ""

    def _path_to_proxy_index(self, path: str):
        try:
            if self._runs_source_model is None:
                return None
            src_idx = self._runs_source_model.index(str(path))
            if not src_idx.isValid():
                return None
            if hasattr(self._runs_model, "mapFromSource"):
                return self._runs_model.mapFromSource(src_idx)
            return src_idx
        except Exception:
            return None

    def _current_run_folder(self) -> Optional[Path]:
        """Return the selected run folder if valid, else None."""
        try:
            idx = self._runs_tree.currentIndex()
            fpath = self._idx_to_path(idx)
            if not fpath:
                return None

            p = Path(fpath)
            run_dir = p if p.is_dir() else p.parent
            if not run_dir.exists() or not run_dir.is_dir():
                return None

            # Ensure inside runs root and shaped like a run folder
            try:
                root = self._runs_root.resolve()
                run_dir.resolve().relative_to(root)
            except Exception:
                return None

            if not _RUN_FOLDER_RE.match(run_dir.name):
                return None

            return run_dir
        except Exception:
            return None

    def _update_remove_btn_state(self) -> None:
        try:
            if self._remove_btn is None:
                return
            has_any = bool(self._selected_run_folders()) or (self._current_run_folder() is not None)
            self._remove_btn.setEnabled(has_any)
        except Exception:
            pass

    # -------------------------------------------------------------------------
    # Latest-result discovery (FAST, cached)
    # -------------------------------------------------------------------------
    def _fast_find_latest_result_folder(self) -> Optional[Path]:
        """
        Fast scan for latest run folder:
          runs/<case>/<runId>/(run_window.csv OR ALL_SELECTED.png)

        Uses scandir (2-level) instead of rglob (recursive).
        """
        root = self._runs_root
        if not root or not root.exists():
            return None

        best_folder = None
        best_mtime = -1.0

        try:
            for case_ent in os.scandir(str(root)):
                if not case_ent.is_dir():
                    continue

                try:
                    for run_ent in os.scandir(case_ent.path):
                        if not run_ent.is_dir():
                            continue

                        run_dir = Path(run_ent.path)

                        csvp = run_dir / "run_window.csv"
                        if csvp.is_file():
                            try:
                                mt = csvp.stat().st_mtime
                                if mt > best_mtime:
                                    best_mtime = mt
                                    best_folder = run_dir
                                continue
                            except Exception:
                                pass

                        pngp = run_dir / "ALL_SELECTED.png"
                        if pngp.is_file():
                            try:
                                mt = pngp.stat().st_mtime
                                if mt > best_mtime:
                                    best_mtime = mt
                                    best_folder = run_dir
                            except Exception:
                                pass

                except Exception:
                    continue

        except Exception:
            return None

        if best_folder is not None and best_mtime >= 0:
            self._latest_cached_folder = best_folder
            self._latest_cached_mtime = float(best_mtime)
            self._latest_cached_at_ts = time.time()

        return best_folder

    def _get_cached_latest_folder(self) -> Optional[Path]:
        """
        Return cached latest folder if available and still exists.
        Re-scan only if cache is empty/stale and cooldown passed.
        """
        try:
            if self._latest_cached_folder is not None and self._latest_cached_folder.exists():
                return self._latest_cached_folder
        except Exception:
            pass

        now = time.time()
        if (now - float(self._latest_cached_at_ts or 0.0)) < float(self._latest_scan_cooldown_sec or 5.0):
            return None

        return self._fast_find_latest_result_folder()

    # -------------------------------------------------------------------------
    # Live Timer
    # -------------------------------------------------------------------------
    @staticmethod
    def _fmt_mmss(total_seconds: int) -> str:
        total_seconds = max(0, total_seconds)
        return f"{total_seconds//60:02d}:{total_seconds%60:02d}"

    def start_live_timer(self, warmup_sec: int, log_sec: int):
        self._warmup_total = warmup_sec
        self._log_total = log_sec
        self._run_started_at = datetime.now()
        self._tick_timer()
        self._timer.start()

    def stop_live_timer(self, final_text: str = "Idle"):
        self._timer.stop()
        self._run_started_at = None
        self._live_timer.setText(final_text)

    def _tick_timer(self):
        if not self._run_started_at:
            return
        elapsed = int((datetime.now() - self._run_started_at).total_seconds())
        if elapsed < self._warmup_total:
            self._live_timer.setText(f"Warmup  {self._fmt_mmss(self._warmup_total - elapsed)}")
            return
        log_elapsed = elapsed - self._warmup_total
        if log_elapsed < self._log_total:
            self._live_timer.setText(f"Log  {self._fmt_mmss(self._log_total - log_elapsed)}")
            return
        self._live_timer.setText("Done  00:00")

    # -------------------------------------------------------------------------
    # Results Browser
    # -------------------------------------------------------------------------
    def _on_runs_current_changed(self, current, previous) -> None:
        try:
            if current is None or (hasattr(current, "isValid") and not current.isValid()):
                return

            fpath = self._idx_to_path(current)
            if not fpath:
                return

            p = Path(fpath)
            self._last_selected_path = p

            # If the user clicks a compare-result folder, exit compare-selection mode.
            # Otherwise selection-change logic may restore the previous compare-input
            # selection and make compare results appear to "not show" when clicked.
            try:
                if p.is_dir() and self._is_compare_result_dir(p):
                    try:
                        self._compare_selected_dirs.clear()
                    except Exception:
                        self._compare_selected_dirs = set()
                    self._update_compare_btn_state()
            except Exception:
                pass

            if self._suppress_selection_preview:
                return

            if p.is_dir():
                self._schedule_preview_target(fpath=str(p), is_dir=True)
                return

            self._schedule_preview_target(fpath=fpath, is_dir=False)
        except Exception:
            pass

    def _on_runs_selection_changed(self, selected, deselected):
        try:
            self._update_remove_btn_state()

            # Keep compare-selection highlights stable even if a normal click clears selection.
            if self._compare_selected_dirs and (not self._compare_restoring):
                sel_dirs = self._selected_run_folders()
                if sel_dirs != self._compare_selected_dirs:
                    self._apply_compare_selection_to_view()

            self._update_compare_btn_state()
        except Exception:
            pass

    def remove_selected_result(self) -> None:
        """Backward-compatible entrypoint: removes all selected results."""
        self.remove_selected_results()

    def remove_selected_results(self) -> None:
        """Delete all selected result folders from disk (bulk)."""
        # Prefer what the user actually selected in the tree.
        run_dirs = list(sorted(self._selected_run_folders(), key=lambda p: p.name))

        # If compare-selection is active (it owns selection), fall back to it.
        if not run_dirs and self._compare_selected_dirs:
            run_dirs = list(sorted(self._compare_selected_dirs, key=lambda p: p.name))

        # Final fallback: current run folder.
        if not run_dirs:
            cur = self._current_run_folder()
            if cur is not None:
                run_dirs = [cur]

        self._update_remove_btn_state()
        if not run_dirs:
            return

        n = len(run_dirs)
        preview_names = [p.name for p in run_dirs[:5]]
        more = "" if n <= 5 else f"\n(+{n - 5} more)"
        name_block = "\n".join(preview_names) + more

        confirm = QMessageBox.question(
            self.parent,
            "Remove Results",
            f"Delete {n} result folder(s)?\nThis action cannot be undone.\n\n{name_block}",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if confirm != QMessageBox.Yes:
            return

        def _is_empty_dir(p: Path) -> bool:
            try:
                if not p.exists() or not p.is_dir():
                    return False
                with os.scandir(str(p)) as it:
                    for _ in it:
                        return False
                return True
            except Exception:
                return False

        def _cleanup_empty_parents(p: Path) -> None:
            """Remove empty parent folders up to runs_root (exclusive)."""
            try:
                root = self._runs_root.resolve()
            except Exception:
                root = self._runs_root

            cur = None
            try:
                cur = p.resolve()
            except Exception:
                cur = p

            # Only operate inside runs_root.
            try:
                cur.relative_to(root)
            except Exception:
                return

            while True:
                try:
                    if cur is None or cur == root:
                        return
                    if not _is_empty_dir(cur):
                        return
                    try:
                        cur.rmdir()
                    except Exception:
                        return
                    cur = cur.parent
                except Exception:
                    return

        failed: list[tuple[Path, str]] = []
        for rd in run_dirs:
            try:
                parent = None
                try:
                    parent = rd.parent
                except Exception:
                    parent = None
                shutil.rmtree(rd)

                # If the case folder becomes empty, remove it too.
                if parent is not None:
                    _cleanup_empty_parents(parent)
            except Exception as exc:
                failed.append((rd, str(exc)))

        # Prune compare selection and clear selection UI
        try:
            for rd in run_dirs:
                self._compare_selected_dirs.discard(rd)
        except Exception:
            pass

        self._last_selected_path = None

        try:
            sm = self._runs_tree.selectionModel()
            if sm is not None:
                sm.clearSelection()
        except Exception:
            pass

        self._update_remove_btn_state()
        self._update_compare_btn_state()

        if failed:
            msg_lines = [f"{p.name}: {err}" for p, err in failed[:5]]
            more_err = "" if len(failed) <= 5 else f"\n(+{len(failed) - 5} more)"
            QMessageBox.warning(
                self.parent,
                "Remove Partially Failed",
                "Some folders could not be removed:\n\n" + "\n".join(msg_lines) + more_err,
            )

        QTimer.singleShot(150, self.select_latest_result)

    def select_latest_result(self) -> None:
        """
        Always select + preview the latest run when Results tab opens,
        but avoid doing extra work if we're already on that folder.
        """
        try:
            root = self._runs_root
            if not root or not root.exists():
                return

            # 1) Determine latest folder (prefer last_run_dir, else cache, else fast scan)
            target_folder = None

            try:
                if self.last_run_dir:
                    cand = Path(self.last_run_dir)
                    if cand.exists() and cand.is_dir():
                        target_folder = cand
                        self._latest_cached_folder = cand
                        self._latest_cached_at_ts = time.time()
            except Exception:
                target_folder = None

            if target_folder is None:
                target_folder = self._get_cached_latest_folder()

            if target_folder is None:
                target_folder = self._fast_find_latest_result_folder()

            if target_folder is None:
                return

            # 2) If current selection already points at that folder (or inside it), do nothing
            try:
                cur_idx = self._runs_tree.currentIndex()
                cur_path_str = self._idx_to_path(cur_idx) if cur_idx is not None else ""
                cur_path = Path(cur_path_str) if cur_path_str else None

                if cur_path and cur_path.exists():
                    cur_dir = cur_path if cur_path.is_dir() else cur_path.parent
                    if cur_dir.resolve() == target_folder.resolve():
                        return
            except Exception:
                pass

            # 3) Select it in the tree (cheap), then preview on next tick (smooth UI)
            idx = self._path_to_proxy_index(str(target_folder))
            if idx is None or (hasattr(idx, "isValid") and not idx.isValid()):
                return

            parent = idx.parent()
            while parent.isValid():
                try:
                    self._runs_tree.expand(parent)
                except Exception:
                    pass
                parent = parent.parent()

            self._suppress_selection_preview = True
            try:
                self._runs_tree.setCurrentIndex(idx)
                self._runs_tree.scrollTo(idx)
                sm = self._runs_tree.selectionModel()
                if sm is not None:
                    sm.select(idx, QItemSelectionModel.ClearAndSelect | QItemSelectionModel.Select)
            finally:
                self._suppress_selection_preview = False

            QTimer.singleShot(0, lambda: self._graph_preview.preview_folder(str(target_folder)))

        except Exception:
            pass


    # -------------------------------------------------------------------------
    # Benchmark Execution
    # -------------------------------------------------------------------------
    @staticmethod
    def _fmt_hhmmss(total_seconds: int) -> str:
        try:
            total_seconds = int(total_seconds)
        except Exception:
            total_seconds = 0
        total_seconds = max(0, total_seconds)
        h = total_seconds // 3600
        m = (total_seconds % 3600) // 60
        s = total_seconds % 60
        return f"{h}:{m:02d}:{s:02d}" if h > 0 else f"{m:02d}:{s:02d}"

    def _write_test_settings_for_run_dir(self, run_dir: Path) -> None:
        try:
            if run_dir is None or not run_dir.exists() or not run_dir.is_dir():
                return

            payload = dict(self._pending_run_settings or {})
            if not payload:
                return

            outp = run_dir / "test_settings.json"
            try:
                outp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            except Exception:
                # best-effort
                pass
        except Exception:
            pass

    def run(self):
        try:
            if self.proc is not None and self.proc.state() != QProcess.NotRunning:
                QMessageBox.warning(self.parent, "Running", "A test is already running.")
                return
        except Exception:
            pass

        script = resource_path("cli", "run_case.ps1")
        if not script.exists():
            QMessageBox.critical(self.parent, "Missing", f"run_case.ps1 not found: {script}")
            return

        self._save_settings()

        self.last_run_dir = None
        self._open_btn.setEnabled(False)
        self._log.clear()

        settings = self._get_settings()
        case = settings.get("case_name", "TEST").strip()
        warm = settings.get("warmup_total_sec", 0)
        logsec = settings.get("log_total_sec", 0)
        hwinfo = settings.get("hwinfo_csv", "").strip()
        fur_demo = settings.get("fur_demo", "furmark-knot-gl")
        fur_demo_display = settings.get("fur_demo_display", "").strip()
        fur_w = settings.get("fur_width", 3840)
        fur_h = settings.get("fur_height", 1600)
        fur_res_display = settings.get("fur_res_display", "").strip()
        furmark_exe = settings.get("furmark_exe", "")
        prime_exe = settings.get("prime_exe", "")

        if warm <= 0 or logsec <= 0:
            QMessageBox.warning(self.parent, "Invalid time", "Warmup and Log must be > 0 seconds.")
            return

        columns = self._sensor_manager.build_selected_columns()

        cmd_parts = [
            f"& {ps_quote(str(script))}",
            f"-CaseName {ps_quote(case)}",
            f"-WarmupSec {warm}",
            f"-LogSec {logsec}",
            f"-HwinfoCsv {ps_quote(hwinfo)}",
            f"-FurDemo {ps_quote(fur_demo)}",
            f"-FurWidth {fur_w}",
            f"-FurHeight {fur_h}",
        ]

        if self._sensor_manager.stress_cpu:
            cmd_parts.append("-StressCPU")
        if self._sensor_manager.stress_gpu:
            cmd_parts.append("-StressGPU")
        if furmark_exe:
            cmd_parts.append(f"-FurMarkExe {ps_quote(furmark_exe)}")
        if prime_exe:
            cmd_parts.append(f"-PrimeExe {ps_quote(prime_exe)}")

        if columns:
            cmd_parts.append(f"-TempPatterns {build_ps_array_literal(columns)}")

        cmd = " ".join(cmd_parts)

        self._append_log("Starting PowerShell:")
        self._append_log("powershell -NoProfile -ExecutionPolicy Bypass -Command " + cmd)
        self._append_log("")

        self._run_btn.setEnabled(False)
        self._abort_btn.setEnabled(True)

        if self.proc is None:
            QMessageBox.critical(self.parent, "Error", "Cannot start process (QProcess unavailable).")
            return

        self.proc.setProgram("powershell")
        self.proc.setArguments(["-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", cmd])

        self._pending_warm = warm
        self._pending_log = logsec
        self._timer_started = False

        # Notify UI that a run is starting (for live monitor table).
        try:
            if callable(getattr(self, "_on_run_started", None)):
                self._on_run_started(dict(settings or {}), list(columns or []))
        except Exception:
            pass

        # Snapshot settings for persistence into the run folder once it's created.
        try:
            stress_cpu = bool(getattr(self._sensor_manager, "stress_cpu", True))
            stress_gpu = bool(getattr(self._sensor_manager, "stress_gpu", True))
            if stress_cpu and stress_gpu:
                stress_mode = "CPU + GPU"
            elif stress_cpu:
                stress_mode = "CPU only"
            elif stress_gpu:
                stress_mode = "GPU only"
            else:
                stress_mode = "None"

            demo_disp = fur_demo_display or str(fur_demo)
            res_disp = fur_res_display or f"{fur_w}x{fur_h}"

            self._pending_run_settings = {
                "case_name": str(case),
                "warmup_total_sec": int(warm),
                "warmup_display": self._fmt_hhmmss(int(warm)),
                "log_total_sec": int(logsec),
                "log_display": self._fmt_hhmmss(int(logsec)),
                "stress_mode": str(stress_mode),
                "furmark_demo": str(demo_disp),
                "furmark_resolution": f"{int(fur_w)}x{int(fur_h)}",
                "furmark_resolution_display": str(res_disp),
                "recorded_at": datetime.now().isoformat(timespec="seconds"),
            }
        except Exception:
            self._pending_run_settings = {}

        self.proc.start()

    def abort(self):
        if self.proc is None:
            return
        if self.proc.state() == QProcess.NotRunning:
            return
        self._append_log("ABORT requested: StopNow")
        self._abort_btn.setEnabled(False)

        script = resource_path("cli", "run_case.ps1")
        p = QProcess(self.parent)
        p.start("powershell", ["-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script), "-StopNow"])

    def on_stdout(self):
        if self.proc is None:
            return
        data = bytes(self.proc.readAllStandardOutput()).decode("utf-8", errors="replace")
        for line in data.splitlines():
            self._append_log(line)

            # Ambient CSV path (emitted by run_case.ps1) so the GUI can include
            # ambient in live plot + min/max/avg table.
            if line.startswith("GUI_AMBIENT_CSV:"):
                try:
                    ambient_csv = line.split(":", 1)[1].strip()
                except Exception:
                    ambient_csv = ""
                if ambient_csv:
                    try:
                        if callable(getattr(self, "_on_ambient_csv", None)):
                            self._on_ambient_csv(str(ambient_csv))
                    except Exception:
                        pass

            if (not self._timer_started) and "GUI_TIMER:WARMUP_START" in line:
                self._timer_started = True
                self.start_live_timer(self._pending_warm, self._pending_log)

            # Warmup -> Log window transition: reset live monitor stats so
            # min/max/avg reflect only the logging window.
            if "GUI_TIMER:LOG_START" in line:
                try:
                    if callable(getattr(self, "_on_log_started", None)):
                        self._on_log_started()
                except Exception:
                    pass

            # Logging window finished: freeze live monitor stats so they match
            # the Legend & Stats popup (which uses the log window only).
            if "GUI_TIMER:LOG_END" in line:
                try:
                    if callable(getattr(self, "_on_log_finished", None)):
                        self._on_log_finished()
                except Exception:
                    pass

            m = RUNMAP_RE.search(line)
            if m:
                self.last_run_dir = m.group(1).strip()
                # update cache immediately (so Results tab is instant)
                try:
                    cand = Path(self.last_run_dir)
                    if cand.exists() and cand.is_dir():
                        self._write_test_settings_for_run_dir(cand)
                        self._latest_cached_folder = cand
                        self._latest_cached_at_ts = time.time()
                        # best-effort: prefer mtime of run_window.csv if present
                        try:
                            csvp = cand / "run_window.csv"
                            if csvp.is_file():
                                self._latest_cached_mtime = float(csvp.stat().st_mtime)
                        except Exception:
                            pass
                except Exception:
                    pass

    def on_stderr(self):
        if self.proc is None:
            return
        data = bytes(self.proc.readAllStandardError()).decode("utf-8", errors="replace")
        for line in data.splitlines():
            self._append_log("[ERR] " + line)

    def on_finished(self, code, status):
        started_at = None
        try:
            started_at = self._run_started_at
        except Exception:
            started_at = None

        finished_at = None
        try:
            finished_at = datetime.now()
        except Exception:
            finished_at = None

        self._append_log(f"Finished (exit code {code})")
        self._run_btn.setEnabled(True)
        self._abort_btn.setEnabled(False)
        self.stop_live_timer("Idle" if code == 0 else "Stopped")
        self._open_btn.setEnabled(bool(self.last_run_dir and Path(self.last_run_dir).exists()))

        try:
            if callable(getattr(self, "_on_run_finished", None)):
                elapsed_sec = None
                try:
                    if started_at is not None and finished_at is not None:
                        elapsed_sec = int((finished_at - started_at).total_seconds())
                except Exception:
                    elapsed_sec = None

                case_name = ""
                try:
                    case_name = str((self._pending_run_settings or {}).get("case_name") or "").strip()
                except Exception:
                    case_name = ""

                result = {
                    "exit_code": int(code) if code is not None else None,
                    "run_dir": str(self.last_run_dir or "").strip(),
                    "case_name": case_name,
                    "started_at": started_at.isoformat(timespec="seconds") if started_at else None,
                    "finished_at": finished_at.isoformat(timespec="seconds") if finished_at else None,
                    "elapsed_sec": elapsed_sec,
                }

                self._on_run_finished(result)
        except Exception:
            pass

        # Refresh cache one more time (in case RUN MAP didn't arrive for some reason)
        try:
            if self.last_run_dir:
                cand = Path(self.last_run_dir)
                if cand.exists() and cand.is_dir():
                    self._write_test_settings_for_run_dir(cand)
                    self._latest_cached_folder = cand
                    self._latest_cached_at_ts = time.time()
        except Exception:
            pass

    def open_run_folder(self):
        if self.last_run_dir and Path(self.last_run_dir).exists():
            os.startfile(self.last_run_dir)

    def is_running(self) -> bool:
        try:
            if self.proc is not None and self.proc.state() != QProcess.NotRunning:
                return True
        except Exception:
            pass
        return False
