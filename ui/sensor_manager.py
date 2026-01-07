# sensor_manager.py
"""Sensor configuration and validation component."""

import os
import time
from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QLabel, QMessageBox, QDialog

from core.hwinfo_csv import read_hwinfo_headers, sensor_leafs_from_header, make_unique
from core.hwinfo_metadata import build_precise_group_map, load_sensor_map, save_sensor_map
from core.hwinfo_status import try_open_hwinfo_sm2
from core.resources import resource_path

from .ui_sensor_picker import SensorPickerDialog, SPD_MAX_TOKEN
from .ui_selected_sensors import SelectedSensorsDialog


class SensorManager:
    """Manages sensor selection, validation, and status monitoring."""

    def __init__(self, parent, hwinfo_edit, csv_dot, sm2_dot, sensors_summary, save_settings_callback,
                 update_run_button_callback, stress_cpu_btn=None, stress_gpu_btn=None):
        """
        Initialize sensor manager.

        Args:
            parent: Parent widget (MainWindow)
            hwinfo_edit: QLineEdit for HWiNFO CSV path
            csv_dot: QLabel status indicator for CSV file
            sm2_dot: QLabel status indicator for shared memory
            sensors_summary: QLineEdit showing selected sensors summary
            save_settings_callback: Callable to save settings
            update_run_button_callback: Callable to update run button state
            stress_cpu_btn: Optional QPushButton for CPU stress toggle
            stress_gpu_btn: Optional QPushButton for GPU stress toggle
        """
        self.parent = parent
        self._hwinfo_edit = hwinfo_edit
        self._csv_dot = csv_dot
        self._sm2_dot = sm2_dot
        self._sensors_summary = sensors_summary
        self._save_settings = save_settings_callback
        self._update_run_button_state = update_run_button_callback
        self._cpu_btn = stress_cpu_btn
        self._gpu_btn = stress_gpu_btn

        # Selected sensor tokens
        self.selected_tokens = [SPD_MAX_TOKEN]

        # Stress test toggles
        self.stress_cpu = True
        self.stress_gpu = True

        # CSV status tracking
        self._csv_last_mtime = None
        self._csv_last_size = None
        self._csv_last_change_ts = None
        self._csv_exists = False
        self._csv_updating = False
        self._csv_update_window = 2.0

        # Start monitoring timers
        self._csv_timer = QTimer(self.parent)
        self._csv_timer.setInterval(700)
        self._csv_timer.timeout.connect(self.refresh_csv_status)
        self._csv_timer.start()

        self._sm2_timer = QTimer(self.parent)
        self._sm2_timer.setInterval(2000)
        self._sm2_timer.timeout.connect(self.refresh_sm2_status)
        self._sm2_timer.start()

        # Initial status check
        self.refresh_csv_status()
        self.refresh_sm2_status()

        # Connect stress toggles if provided
        if self._cpu_btn is not None:
            self._cpu_btn.toggled.connect(self._on_cpu_toggled)
        if self._gpu_btn is not None:
            self._gpu_btn.toggled.connect(self._on_gpu_toggled)

    def _set_dot_state(self, dot: QLabel, ok: bool) -> None:
        """Update status dot appearance."""
        dot.setProperty("state", "ok" if ok else "bad")
        dot.style().unpolish(dot)
        dot.style().polish(dot)
        dot.update()

    def refresh_csv_status(self) -> None:
        """Check if HWiNFO CSV file exists and is being updated."""
        path = self._hwinfo_edit.text().strip()

        csv_exists = False
        csv_updating = False
        try:
            if path and os.path.exists(path):
                st = os.stat(path)
                mtime = st.st_mtime
                size = st.st_size
                now = time.time()

                if self._csv_last_mtime is None:
                    self._csv_last_mtime = mtime
                    self._csv_last_size = size
                else:
                    if (mtime != self._csv_last_mtime) or (size != self._csv_last_size):
                        self._csv_last_mtime = mtime
                        self._csv_last_size = size
                        self._csv_last_change_ts = now

                csv_exists = True
                csv_updating = bool(
                    self._csv_last_change_ts and (now - self._csv_last_change_ts) <= self._csv_update_window
                )
        except Exception:
            csv_exists = False
            csv_updating = False

        self._set_dot_state(self._csv_dot, ok=(csv_exists and csv_updating))

        self._csv_exists = csv_exists
        self._csv_updating = csv_updating
        self._update_run_button_state()

    def refresh_sm2_status(self) -> None:
        """Check if HWiNFO shared memory is accessible."""
        sm2_state, _sm2_msg = try_open_hwinfo_sm2()
        self._set_dot_state(self._sm2_dot, ok=(sm2_state is True))

    def refresh_sensors_summary(self) -> None:
        """Update the sensors summary display."""
        if not self.selected_tokens:
            self._sensors_summary.setText("")
            self._sensors_summary.setPlaceholderText("No sensors selected (will use defaults).")
            return

        display = [("SPD Hub (Max)" if t == SPD_MAX_TOKEN else t) for t in self.selected_tokens]
        self._sensors_summary.setText(
            "; ".join(display[:4]) + (f"; … (+{len(display)-4})" if len(display) > 4 else "")
        )

    def _ensure_precise_map(self, csv_leafs: list[str], csv_unique_leafs: list[str]) -> dict[str, str]:
        """Ensure precise group mapping exists, creating it if needed."""
        cache_path = resource_path("resources", "sensor_map.json")
        payload = load_sensor_map(cache_path)
        if payload and payload.get("schema") == 1 and payload.get("header_unique") == csv_unique_leafs:
            return dict(payload.get("mapping", {}))

        mapping = build_precise_group_map(csv_leafs, csv_unique_leafs)
        save_sensor_map(cache_path, csv_unique_leafs, mapping)
        return mapping

    def open_selected_sensors_view(self) -> None:
        """Open dialog showing currently selected sensors."""
        hwinfo_path = self._hwinfo_edit.text().strip()
        try:
            header = read_hwinfo_headers(hwinfo_path)
            csv_leafs, has_spd = sensor_leafs_from_header(header)
            csv_unique_leafs = make_unique(csv_leafs)
        except Exception as e:
            QMessageBox.critical(self.parent, "Cannot read HWiNFO CSV", str(e))
            return

        try:
            group_map = self._ensure_precise_map(csv_leafs, csv_unique_leafs)
        except Exception:
            group_map = {}

        dlg = SelectedSensorsDialog(
            self.parent,
            selected_tokens=list(self.selected_tokens),
            group_map=group_map,
            has_spd=has_spd,
        )
        dlg.exec()

    def open_sensor_picker(self) -> None:
        """Open sensor picker dialog for user to select sensors."""
        hwinfo_path = self._hwinfo_edit.text().strip()
        try:
            header = read_hwinfo_headers(hwinfo_path)
            csv_leafs, has_spd = sensor_leafs_from_header(header)
            csv_unique_leafs = make_unique(csv_leafs)
        except Exception as e:
            QMessageBox.critical(self.parent, "Cannot read HWiNFO CSV", str(e))
            return

        try:
            group_map = self._ensure_precise_map(csv_leafs, csv_unique_leafs)
        except Exception:
            group_map = {}

        pre = set(self.selected_tokens)
        dlg = SensorPickerDialog(
            self.parent,
            csv_unique_leafs=csv_unique_leafs,
            has_spd=has_spd,
            group_map=group_map,
            preselected=pre,
        )
        if dlg.exec() == QDialog.Accepted:
            self.selected_tokens = dlg.selected_tokens()
            self.refresh_sensors_summary()
            self._save_settings()

    def build_selected_columns(self) -> list[str]:
        """
        Build list of column names to plot based on selected tokens.

        Returns:
            List of unique column names in order
        """
        if not self.selected_tokens:
            return ["CPU Package [°C]", "GPU Temperature [°C]", "GPU VRM Temperature [°C]", "SPD Hub Max [°C]"]

        cols = []
        spd_selected = False

        for t in self.selected_tokens:
            if t == SPD_MAX_TOKEN:
                spd_selected = True
                continue
            if "SPD Hub Temperature" in t:
                spd_selected = True
            cols.append(t)

        if spd_selected:
            cols.append("SPD Hub Max [°C]")

        seen = set()
        out = []
        for c in cols:
            if c not in seen:
                seen.add(c)
                out.append(c)
        return out

    def can_run(self, furmark_exe: str, prime_exe: str) -> bool:
        """
        Check if prerequisites are met to run benchmark.

        Args:
            furmark_exe: Path to FurMark executable
            prime_exe: Path to Prime95 executable

        Returns:
            True if all requirements satisfied
        """
        if not (self._csv_exists and self._csv_updating):
            return False
        if self.stress_cpu:
            if not prime_exe or not os.path.exists(prime_exe):
                return False
        if self.stress_gpu:
            if not furmark_exe or not os.path.exists(furmark_exe):
                return False
        return True

    def missing_reasons(self, furmark_exe: str, prime_exe: str) -> list[str]:
        """
        Get list of reasons why benchmark cannot run.

        Args:
            furmark_exe: Path to FurMark executable
            prime_exe: Path to Prime95 executable

        Returns:
            List of reason strings
        """
        reasons = []
        if not self._csv_exists:
            reasons.append("HWiNFO CSV file not found (check path).")
        elif not self._csv_updating:
            reasons.append("HWiNFO CSV not being updated (HWiNFO not logging).")

        if self.stress_cpu:
            if not prime_exe:
                reasons.append("Prime95 path not set in Settings.")
            elif not os.path.exists(prime_exe):
                reasons.append(f"Prime95 not found at: {prime_exe}")

        if self.stress_gpu:
            if not furmark_exe:
                reasons.append("FurMark path not set in Settings.")
            elif not os.path.exists(furmark_exe):
                reasons.append(f"FurMark not found at: {furmark_exe}")

        if not reasons:
            reasons.append("Unknown: prerequisites not met.")
        return reasons

    def _on_cpu_toggled(self, checked: bool) -> None:
        """Handle CPU stress toggle."""
        if (not checked) and (not self._gpu_btn.isChecked()):
            self._cpu_btn.blockSignals(True)
            self._cpu_btn.setChecked(True)
            self._cpu_btn.blockSignals(False)
            return
        self.stress_cpu = checked
        self._save_settings()
        self._update_run_button_state()

    def _on_gpu_toggled(self, checked: bool) -> None:
        """Handle GPU stress toggle."""
        if (not checked) and (not self._cpu_btn.isChecked()):
            self._gpu_btn.blockSignals(True)
            self._gpu_btn.setChecked(True)
            self._gpu_btn.blockSignals(False)
            return
        self.stress_gpu = checked
        self._save_settings()

    def get_csv_exists(self) -> bool:
        """Check if CSV file exists."""
        return self._csv_exists

    def get_csv_updating(self) -> bool:
        """Check if CSV file is being updated."""
        return self._csv_updating
