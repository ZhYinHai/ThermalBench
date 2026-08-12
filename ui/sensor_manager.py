# sensor_manager.py
"""Sensor configuration and validation component."""

import os
import time
from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QLabel, QMessageBox, QDialog, QPushButton

from core.hwinfo_csv import read_hwinfo_headers, sensor_leafs_from_header, make_unique
from core.hwinfo_metadata import build_precise_group_map, load_sensor_map, save_sensor_map
from core.resources import resource_path
from core.bundled_tools import resolve_hwinfo_csv
from core.user_paths import sensor_map_cache_path

from .dialogs import SensorPickerDialog, SPD_MAX_TOKEN, SelectedSensorsDialog


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
        self._csv_header_ready = False
        self._csv_update_window = 2.0
        self._csv_last_path = ""
        self._csv_unique_leafs: list[str] | None = None  # current CSV columns, used for cache validation
        self._csv_leafs: list[str] | None = None          # base labels (before dedup), used for SM2 matching
        self._csv_has_spd = False

        try:
            fixed_csv = resolve_hwinfo_csv()
            self._hwinfo_edit.setText(fixed_csv)
            self._hwinfo_edit.setReadOnly(True)
            self._hwinfo_edit.setToolTip(
                "Fixed HWiNFO log file.\n"
                "Configure HWiNFO sensor logging to write here:\n"
                f"{fixed_csv}"
            )
        except Exception:
            pass

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

    def _fixed_hwinfo_csv_path(self) -> str:
        path = resolve_hwinfo_csv()

        try:
            if self._hwinfo_edit.text().strip() != path:
                self._hwinfo_edit.setText(path)
        except Exception:
            pass

        return path

    def _hwinfo_csv_not_ready_message(self, path: str) -> str:
        return (
            "The HWiNFO CSV exists, but it does not contain a valid sensor header yet.\n\n"
            "This usually means HWiNFO is open, but sensor logging has not actually started.\n\n"
            "Do this in HWiNFO:\n"
            "1. Open the Sensors window.\n"
            "2. Click the logging/start-log button.\n"
            "3. Choose this exact CSV path:\n\n"
            f"{path}\n\n"
            "Keep HWiNFO running while using ThermalBench."
        )

    def _read_hwinfo_header_or_warn(self) -> list[str] | None:
        path = self._fixed_hwinfo_csv_path()

        if not path:
            QMessageBox.warning(
                self.parent,
                "HWiNFO CSV not configured",
                "ThermalBench could not resolve the fixed HWiNFO CSV path.",
            )
            return None

        p = Path(path)

        if not p.exists():
            QMessageBox.warning(
                self.parent,
                "HWiNFO CSV not found",
                "The HWiNFO CSV file does not exist yet.\n\n"
                "Open HWiNFO and start sensor logging to this exact path:\n\n"
                f"{path}",
            )
            return None

        try:
            if p.stat().st_size <= 0:
                QMessageBox.warning(
                    self.parent,
                    "HWiNFO CSV is empty",
                    self._hwinfo_csv_not_ready_message(path),
                )
                return None
        except Exception:
            pass

        try:
            header = read_hwinfo_headers(path)
        except Exception as e:
            QMessageBox.warning(
                self.parent,
                "HWiNFO CSV not ready",
                self._hwinfo_csv_not_ready_message(path) + f"\n\nDetails:\n{e}",
            )
            return None

        if not header:
            QMessageBox.warning(
                self.parent,
                "HWiNFO CSV header is empty",
                self._hwinfo_csv_not_ready_message(path),
            )
            return None

        return header

    def refresh_csv_status(self) -> None:
        """Check if the fixed HWiNFO CSV exists, has a header, and is being updated."""
        path = self._fixed_hwinfo_csv_path()

        if path != self._csv_last_path:
            self._csv_last_path = path
            self._csv_last_mtime = None
            self._csv_last_size = None
            self._csv_last_change_ts = None

        csv_exists = False
        csv_updating = False
        csv_header_ready = False

        try:
            if path and os.path.exists(path):
                st = os.stat(path)
                mtime = st.st_mtime
                size = st.st_size
                now = time.time()

                if self._csv_last_mtime is None:
                    self._csv_last_mtime = mtime
                    self._csv_last_size = size
                    # Do not set _csv_last_change_ts here — first poll only establishes
                    # the baseline. A change (file growth) must be detected on a subsequent
                    # poll before the CSV is considered "actively logging".
                else:
                    if (mtime != self._csv_last_mtime) or (size != self._csv_last_size):
                        # Only count as "updating" when the file has GROWN.
                        # HWiNFO always appends rows, so growth is a reliable signal of
                        # active logging. Ignoring mtime-only or size-decrease changes
                        # avoids false positives from OneDrive/cloud-sync touching the
                        # file, or from HWiNFO starting a new session (which truncates
                        # then re-writes, showing a temporary size decrease).
                        if size > (self._csv_last_size or 0):
                            self._csv_last_change_ts = now
                        self._csv_last_mtime = mtime
                        self._csv_last_size = size

                csv_exists = True

                if size > 0:
                    try:
                        header = read_hwinfo_headers(path)
                        csv_header_ready = bool(header)
                        if csv_header_ready and header:
                            try:
                                leafs, has_spd = sensor_leafs_from_header(header)
                                self._csv_leafs = leafs
                                self._csv_unique_leafs = make_unique(leafs)
                                self._csv_has_spd = bool(has_spd)
                            except Exception:
                                self._csv_has_spd = False
                    except Exception:
                        csv_header_ready = False

                csv_updating = bool(
                    self._csv_last_change_ts
                    and (now - self._csv_last_change_ts) <= self._csv_update_window
                )
        except Exception:
            csv_exists = False
            csv_updating = False
            csv_header_ready = False

        self._set_dot_state(self._csv_dot, ok=(csv_exists and csv_header_ready and csv_updating))

        if csv_exists and csv_header_ready and csv_updating:
            self._csv_dot.setToolTip(f"CSV active:\n{path}")
        elif csv_exists and not csv_header_ready:
            self._csv_dot.setToolTip(
                "CSV found, but it does not contain a valid HWiNFO header yet.\n"
                "Start HWiNFO sensor logging to:\n"
                f"{path}"
            )
        elif csv_exists:
            self._csv_dot.setToolTip(
                "CSV found, but it is not being updated.\n"
                "Start HWiNFO sensor logging to:\n"
                f"{path}"
            )
        else:
            self._csv_dot.setToolTip(
                "CSV not found.\n"
                "Open HWiNFO and configure sensor logging to:\n"
                f"{path}"
            )

        self._csv_exists = csv_exists
        self._csv_updating = csv_updating
        self._csv_header_ready = csv_header_ready
        if not csv_header_ready:
            self._csv_has_spd = False
        self._update_run_button_state()

        if csv_exists and csv_header_ready and csv_updating:
            self._prune_missing_selected_tokens(show_warning=True)

    def refresh_sm2_status(self) -> None:
        """Check whether the sensor picker will show a grouped or flat list."""
        grouped, source = self._resolve_grouping_source()
        self._set_dot_state(self._sm2_dot, ok=grouped)
        if grouped and source == "sm2":
            self._sm2_dot.setToolTip(
                "Sensors are grouped by device in the sensor picker.\n"
                "HWiNFO Shared Memory is active and reporting multiple device groups."
            )
        elif grouped and source == "cache":
            self._sm2_dot.setToolTip(
                "Sensors are grouped by device in the sensor picker.\n"
                "(Group data is from a cached mapping — Shared Memory may not be active.)"
            )
        else:
            self._sm2_dot.setToolTip(
                "Sensors appear as a flat ungrouped list in the sensor picker.\n"
                "To fix: open HWiNFO → open the Sensors window → Settings → General → "
                "enable Shared Memory Support, then restart HWiNFO logging."
            )

    def _resolve_grouping_source(self) -> tuple[bool, str]:
        """Return (is_grouped, source) reflecting what the sensor picker will actually show.

        Mirrors the picker's own lookup order: cache first, then live SM2.
        Returns source as 'sm2', 'cache', or 'none'.
        """
        # Check live SM2 first (fastest path when HWiNFO is running)
        if self._sm2_has_real_groups():
            return True, "sm2"
        # Fall back to the same cache the picker uses — but only when the cached
        # header_unique still matches the current CSV columns.  If it doesn't
        # match (different machine, reinstall, or CSV not yet read) the picker
        # would also miss the cache and show a flat list, so the dot must agree.
        try:
            current_unique = self._csv_unique_leafs
            payload = self._load_cached_sensor_map(current_unique)
            if payload and payload.get("schema") == 1:
                if current_unique and payload.get("header_unique") == current_unique:
                    groups = {g for g in payload.get("mapping", {}).values() if g and g != "Other"}
                    if len(groups) >= 2:
                        return True, "cache"
        except Exception:
            pass
        return False, "none"

    def _load_cached_sensor_map(self, expected_header_unique: list[str] | None = None) -> dict | None:
        """Load writable cache first, then bundled read-only fallback."""
        for path in (sensor_map_cache_path(), resource_path("resources", "sensor_map.json")):
            payload = load_sensor_map(path)
            if isinstance(payload, dict) and payload.get("schema") == 1:
                if expected_header_unique is not None and payload.get("header_unique") != expected_header_unique:
                    continue
                return payload
        return None

    def _sm2_has_real_groups(self) -> bool:
        """Return True only when live SM2 produces ≥2 distinct non-Other groups
        for the sensors that are actually present in the current CSV.

        Checking raw SM2 group names is not enough: HWiNFO creates the SM2
        segment whenever the Sensors window is open (regardless of the
        'Shared Memory Support' setting on some versions), so the segment can
        be accessible with real device names while the label-to-CSV matching
        inside build_precise_group_map still fails and the picker shows a flat
        list.  We replicate the same matching step here so the dot and the
        picker always agree.
        """
        csv_leafs = self._csv_leafs
        if not csv_leafs:
            return False
        try:
            from core.hwinfo_metadata import _read_sm2_entries  # noqa: PLC0415
            entries = _read_sm2_entries()
            # Build label -> [group, ...] exactly as build_precise_group_map does.
            label_to_groups: dict[str, list[str]] = {}
            for lbl, grp in entries:
                label_to_groups.setdefault(lbl, []).append(grp)
            # Walk the base CSV labels and collect which non-Other groups they map to.
            occ: dict[str, int] = {}
            matched_groups: set[str] = set()
            for base in csv_leafs:
                k = occ.get(base, 0)
                occ[base] = k + 1
                grp_list = label_to_groups.get(base, [])
                if k < len(grp_list):
                    g = grp_list[k]
                    if g and g != "Other":
                        matched_groups.add(g)
            return len(matched_groups) >= 2
        except Exception:
            return False

    def _has_live_sm2_entries(self) -> bool:
        """Return True when live HWiNFO shared-memory entries are readable."""
        try:
            from core.hwinfo_metadata import _read_sm2_entries  # noqa: PLC0415
            entries = _read_sm2_entries()
            return bool(entries)
        except Exception:
            return False

    def refresh_sensors_summary(self) -> None:
        """Update the sensors summary display."""
        if getattr(self, "_sensors_summary", None) is None:
            return

        if not self.selected_tokens:
            self._sensors_summary.setText("")
            self._sensors_summary.setPlaceholderText("No sensors selected (will use defaults).")
            return

        display = [("SPD Hub (Max)" if t == SPD_MAX_TOKEN else t) for t in self.selected_tokens]
        self._sensors_summary.setText(
            "; ".join(display[:4]) + (f"; … (+{len(display)-4})" if len(display) > 4 else "")
        )

    def _available_sensor_tokens(self) -> set[str]:
        """Return the sensor tokens currently present in the live HWiNFO CSV."""
        available = set(self._csv_unique_leafs or [])
        if self._csv_has_spd:
            available.add(SPD_MAX_TOKEN)
        return available

    def _prune_missing_selected_tokens(self, show_warning: bool = True) -> list[str]:
        """Remove selected sensors that no longer exist in the live CSV and optionally warn the user."""
        missing = self._missing_selected_tokens()
        if not missing:
            return list(self.selected_tokens)

        kept = [tok for tok in self.selected_tokens if tok not in {t for t in self.selected_tokens if t == SPD_MAX_TOKEN or t in (self._csv_unique_leafs or [])}]
        # Rebuild from the current selection while removing any token absent from the live CSV.
        available = self._available_sensor_tokens()
        kept = []
        for tok in self.selected_tokens:
            if tok == SPD_MAX_TOKEN:
                if self._csv_has_spd:
                    kept.append(tok)
                continue
            if tok in available:
                kept.append(tok)

        if kept != self.selected_tokens:
            self.selected_tokens = kept
            self.refresh_sensors_summary()
            save_settings = getattr(self, "_save_settings", None)
            if callable(save_settings):
                save_settings()
            if show_warning:
                self._show_stale_sensor_warning(missing)
        return list(self.selected_tokens)

    def _show_stale_sensor_warning(self, missing: list[str]) -> None:
        """Show a modal warning dialog with only an OK button when stale sensors are pruned."""
        try:
            msg = (
                "Some previously selected sensors are no longer present in the current HWiNFO log and were removed from the selection."
                f"\n\nRemoved sensors:\n- {'\n- '.join(missing)}"
            )
            dlg = QMessageBox(self.parent)
            dlg.setWindowTitle("Sensor selection updated")
            dlg.setText(msg)
            dlg.setIcon(QMessageBox.Warning)
            dlg.setStandardButtons(QMessageBox.Ok)
            dlg.setDefaultButton(QMessageBox.Ok)
            dlg.button(QMessageBox.Ok).setText("Ok")
            dlg.exec()
        except Exception:
            pass

    def _missing_selected_tokens(self) -> list[str]:
        """Return selected tokens that are no longer available in the current CSV."""
        if not self.selected_tokens:
            return []

        available = self._available_sensor_tokens()
        missing: list[str] = []
        for tok in self.selected_tokens:
            if tok == SPD_MAX_TOKEN:
                if not self._csv_has_spd:
                    missing.append("SPD Hub (Max of DIMMs)")
                continue
            if tok not in available:
                missing.append(tok)
        return missing

    def _ensure_precise_map(self, csv_leafs: list[str], csv_unique_leafs: list[str]) -> dict[str, str]:
        """Ensure precise group mapping exists, creating it if needed."""
        # Prefer live SM2 mapping when available so stale cached maps don't keep
        # temperature sensors in "Other" after label-matching improvements.
        if self._has_live_sm2_entries():
            mapping = build_precise_group_map(csv_leafs, csv_unique_leafs)
            try:
                save_sensor_map(sensor_map_cache_path(), csv_unique_leafs, mapping)
            except Exception:
                pass
            return mapping

        payload = self._load_cached_sensor_map(csv_unique_leafs)
        if payload and payload.get("schema") == 1 and payload.get("header_unique") == csv_unique_leafs:
            return dict(payload.get("mapping", {}))

        mapping = build_precise_group_map(csv_leafs, csv_unique_leafs)
        try:
            save_sensor_map(sensor_map_cache_path(), csv_unique_leafs, mapping)
        except Exception:
            pass
        return mapping

    def open_selected_sensors_view(self) -> None:
        """Open dialog showing currently selected sensors."""
        header = self._read_hwinfo_header_or_warn()
        if header is None:
            return

        try:
            csv_leafs, has_spd = sensor_leafs_from_header(header)
            csv_unique_leafs = make_unique(csv_leafs)
        except Exception as e:
            QMessageBox.critical(self.parent, "Cannot read HWiNFO sensors", str(e))
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
            theme_mode=str(getattr(self.parent, "theme_mode", "device")),
        )
        dlg.exec()

    def open_sensor_picker(self) -> None:
        """Open sensor picker dialog for user to select sensors."""
        header = self._read_hwinfo_header_or_warn()
        if header is None:
            return

        try:
            csv_leafs, has_spd = sensor_leafs_from_header(header)
            csv_unique_leafs = make_unique(csv_leafs)
        except Exception as e:
            QMessageBox.critical(self.parent, "Cannot read HWiNFO sensors", str(e))
            return

        try:
            group_map = self._ensure_precise_map(csv_leafs, csv_unique_leafs)
        except Exception:
            group_map = {}

        available_tokens = set(csv_unique_leafs)
        if has_spd:
            available_tokens.add(SPD_MAX_TOKEN)
        pre = {tok for tok in self.selected_tokens if tok in available_tokens}
        dlg = SensorPickerDialog(
            self.parent,
            csv_unique_leafs=csv_unique_leafs,
            has_spd=has_spd,
            group_map=group_map,
            preselected=pre,
            theme_mode=str(getattr(self.parent, "theme_mode", "device")),
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

    def selected_sensor_device_names(self) -> list[str]:
        """Return unique non-Other device-group names for currently selected sensors."""
        if not self.selected_tokens:
            return []

        csv_leafs = list(self._csv_leafs or [])
        csv_unique_leafs = list(self._csv_unique_leafs or [])
        has_spd = bool(self._csv_has_spd)

        if not csv_leafs or not csv_unique_leafs:
            try:
                header = read_hwinfo_headers(self._fixed_hwinfo_csv_path())
                csv_leafs, has_spd = sensor_leafs_from_header(header)
                csv_unique_leafs = make_unique(csv_leafs)
            except Exception:
                return []

        try:
            group_map = self._ensure_precise_map(csv_leafs, csv_unique_leafs)
        except Exception:
            group_map = {}

        out: list[str] = []
        seen: set[str] = set()
        for tok in self.selected_tokens:
            if tok == SPD_MAX_TOKEN:
                dev = "Memory / SPD" if has_spd else ""
            else:
                dev = str(group_map.get(tok, "") or "").strip()

            if not dev or dev == "Other" or dev in seen:
                continue
            seen.add(dev)
            out.append(dev)

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
        if not (self._csv_exists and self._csv_header_ready and self._csv_updating):
            return False
        missing_tokens = self._missing_selected_tokens()
        if missing_tokens:
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
        fixed_csv = self._fixed_hwinfo_csv_path()

        if not self._csv_exists:
            reasons.append(
                "HWiNFO CSV file not found. Click 'Open HWiNFO' and log sensors to:\n"
                f"{fixed_csv}"
            )
        elif not self._csv_header_ready:
            reasons.append(
                "HWiNFO CSV exists but has no valid sensor header yet. Start HWiNFO sensor logging to:\n"
                f"{fixed_csv}"
            )
        elif not self._csv_updating:
            reasons.append(
                "HWiNFO CSV exists but is not being updated. Start HWiNFO sensor logging to:\n"
                f"{fixed_csv}"
            )

        missing_tokens = self._missing_selected_tokens()
        if missing_tokens:
            reasons.append(
                "The selected sensor(s) are no longer present in the current HWiNFO CSV:\n- "
                + "\n- ".join(missing_tokens)
                + "\n\nOpen the sensor picker and choose a currently available sensor."
            )

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
