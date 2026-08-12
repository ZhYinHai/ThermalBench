import os
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from core.hwinfo_metadata import build_precise_group_map
from ui.sensor_manager import SensorManager


class SensorManagerValidationTests(unittest.TestCase):
    def _make_manager(self, *, selected_tokens, available_tokens, has_spd=False):
        mgr = SensorManager.__new__(SensorManager)
        mgr.selected_tokens = list(selected_tokens)
        mgr._csv_exists = True
        mgr._csv_header_ready = True
        mgr._csv_updating = True
        mgr._csv_unique_leafs = list(available_tokens)
        mgr._csv_leafs = list(available_tokens)
        mgr._csv_has_spd = bool(has_spd)
        mgr.stress_cpu = True
        mgr.stress_gpu = True
        mgr._fixed_hwinfo_csv_path = lambda: "C:/dummy/hwinfo.csv"
        return mgr

    def test_run_is_blocked_when_selected_sensor_is_missing(self):
        with tempfile.NamedTemporaryFile(suffix=".exe", delete=False) as tmp:
            furmark_path = tmp.name
        with tempfile.NamedTemporaryFile(suffix=".exe", delete=False) as tmp:
            prime_path = tmp.name

        try:
            mgr = self._make_manager(
                selected_tokens=["CPU Fan [RPM]"],
                available_tokens=["GPU Temperature [°C]", "CPU Package [°C]"],
            )

            self.assertFalse(mgr.can_run(furmark_path, prime_path))
            reasons = mgr.missing_reasons(furmark_path, prime_path)
            self.assertTrue(any("no longer present" in r for r in reasons))
        finally:
            for path in (furmark_path, prime_path):
                try:
                    os.remove(path)
                except FileNotFoundError:
                    pass

    def test_run_is_allowed_when_selected_sensor_still_exists(self):
        with tempfile.NamedTemporaryFile(suffix=".exe", delete=False) as tmp:
            furmark_path = tmp.name
        with tempfile.NamedTemporaryFile(suffix=".exe", delete=False) as tmp:
            prime_path = tmp.name

        try:
            mgr = self._make_manager(
                selected_tokens=["CPU Package [°C]"],
                available_tokens=["GPU Temperature [°C]", "CPU Package [°C]"],
            )

            self.assertTrue(mgr.can_run(furmark_path, prime_path))
            self.assertEqual(mgr.missing_reasons(furmark_path, prime_path), [])
        finally:
            for path in (furmark_path, prime_path):
                try:
                    os.remove(path)
                except FileNotFoundError:
                    pass

    def test_prunes_missing_selected_sensors_when_active_logging(self):
        mgr = self._make_manager(
            selected_tokens=["CPU Fan [RPM]", "CPU Package [°C]"],
            available_tokens=["GPU Temperature [°C]", "CPU Package [°C]"],
        )
        mgr._csv_exists = True
        mgr._csv_header_ready = True
        mgr._csv_updating = True

        pruned = mgr._prune_missing_selected_tokens(show_warning=False)

        self.assertEqual(pruned, ["CPU Package [°C]"])
        self.assertEqual(mgr.selected_tokens, ["CPU Package [°C]"])

    @patch("core.hwinfo_metadata._read_sm2_entries")
    def test_group_map_matches_celsius_suffix(self, mock_read_entries):
        mock_read_entries.return_value = [
            ("CPU (Tctl/Tdie)", "CPU [#0]: AMD Ryzen 7 5800X: Enhanced"),
            ("CPU Die (Average)", "CPU [#0]: AMD Ryzen 7 5800X: Enhanced"),
        ]

        mapping = build_precise_group_map(
            ["CPU (Tctl/Tdie) [°C]", "CPU Die (average) [°C]"],
            ["CPU (Tctl/Tdie) [°C]", "CPU Die (average) [°C]"],
        )

        self.assertEqual(mapping["CPU (Tctl/Tdie) [°C]"], "CPU [#0]: AMD Ryzen 7 5800X: Enhanced")
        self.assertEqual(mapping["CPU Die (average) [°C]"], "CPU [#0]: AMD Ryzen 7 5800X: Enhanced")

    @patch("ui.sensor_manager.sensor_map_cache_path", return_value=Path("C:/tmp/sensor_map.json"))
    @patch("ui.sensor_manager.save_sensor_map")
    @patch("ui.sensor_manager.build_precise_group_map")
    def test_ensure_precise_map_prefers_live_sm2_over_stale_cache(self, mock_build_map, mock_save_map, _mock_cache_path):
        mgr = SensorManager.__new__(SensorManager)
        mgr._has_live_sm2_entries = lambda: True
        mgr._load_cached_sensor_map = lambda _hdr: {
            "schema": 1,
            "header_unique": ["CPU (Tctl/Tdie) [°C]"],
            "mapping": {"CPU (Tctl/Tdie) [°C]": "Other"},
        }

        mock_build_map.return_value = {
            "CPU (Tctl/Tdie) [°C]": "CPU [#0]: AMD Ryzen 7 5800X: Enhanced"
        }

        result = mgr._ensure_precise_map(
            ["CPU (Tctl/Tdie) [°C]"],
            ["CPU (Tctl/Tdie) [°C]"],
        )

        self.assertEqual(
            result["CPU (Tctl/Tdie) [°C]"],
            "CPU [#0]: AMD Ryzen 7 5800X: Enhanced",
        )
        mock_build_map.assert_called_once()
        mock_save_map.assert_called_once()


if __name__ == "__main__":
    unittest.main()
