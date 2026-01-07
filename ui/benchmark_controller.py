# benchmark_controller.py
"""Benchmark execution and results browsing component."""

import os
import time
from pathlib import Path
from datetime import datetime
from typing import Optional

from PySide6.QtCore import QProcess, QTimer, QItemSelectionModel, Qt
from PySide6.QtWidgets import QTreeView, QFileSystemModel, QMessageBox

from core.ps_helpers import RUNMAP_RE, ps_quote, build_ps_array_literal
from core.resources import resource_path


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
        runs_tree: QTreeView,
        runs_model,
        runs_source_model: QFileSystemModel,
        runs_root: Path,
        graph_preview,
        sensor_manager,
        save_settings_callback,
        get_settings_callback,
        append_log_callback,
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
        self._runs_tree = runs_tree
        self._runs_model = runs_model
        self._runs_source_model = runs_source_model
        self._runs_root = runs_root
        self._graph_preview = graph_preview
        self._sensor_manager = sensor_manager
        self._save_settings = save_settings_callback
        self._get_settings = get_settings_callback
        self._append_log = append_log_callback

        # Prevent double plotting when we programmatically set selection
        self._suppress_selection_preview = False

        # Cache: latest result folder (avoid expensive scans on every tab switch)
        self._latest_cached_folder: Optional[Path] = None
        self._latest_cached_mtime: float = 0.0
        self._latest_cached_at_ts: float = 0.0
        self._latest_scan_cooldown_sec: float = 5.0

        # Remember last user-selected path (if user clicked something in the tree)
        self._last_selected_path: Optional[Path] = None

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

        # Connect results tree selection
        try:
            self._runs_tree.selectionModel().selectionChanged.connect(self._on_runs_selection_changed)
        except Exception:
            pass

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
    def _on_runs_selection_changed(self, selected, deselected):
        try:
            indexes = selected.indexes()
            if not indexes:
                return
            idx = indexes[0]
            fpath = self._idx_to_path(idx)
            if not fpath:
                return

            p = Path(fpath)
            self._last_selected_path = p

            # If we are programmatically selecting, don't immediately preview here.
            if self._suppress_selection_preview:
                return

            if p.is_dir():
                try:
                    self._graph_preview.preview_folder(str(p))
                except Exception:
                    pass
                return

            self._graph_preview.preview_path(fpath)
        except Exception:
            pass

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
        fur_w = settings.get("fur_width", 3840)
        fur_h = settings.get("fur_height", 1600)
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

            if (not self._timer_started) and "GUI_TIMER:WARMUP_START" in line:
                self._timer_started = True
                self.start_live_timer(self._pending_warm, self._pending_log)

            m = RUNMAP_RE.search(line)
            if m:
                self.last_run_dir = m.group(1).strip()
                # update cache immediately (so Results tab is instant)
                try:
                    cand = Path(self.last_run_dir)
                    if cand.exists() and cand.is_dir():
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
        self._append_log(f"Finished (exit code {code})")
        self._run_btn.setEnabled(True)
        self._abort_btn.setEnabled(False)
        self.stop_live_timer("Idle" if code == 0 else "Stopped")
        self._open_btn.setEnabled(bool(self.last_run_dir and Path(self.last_run_dir).exists()))

        # Refresh cache one more time (in case RUN MAP didn't arrive for some reason)
        try:
            if self.last_run_dir:
                cand = Path(self.last_run_dir)
                if cand.exists() and cand.is_dir():
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
