import os
import sys
import tempfile
import unittest
from pathlib import Path

_repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from core.bundled_tools import afterburner_profile_details_from_dir, format_afterburner_profile_details


class AfterburnerProfileTests(unittest.TestCase):
    def test_current_gpu_profile_wins_over_stale_device_profile(self):
        with tempfile.TemporaryDirectory() as td:
            profiles = Path(td)
            (profiles / "MSIAfterburner.cfg").write_text("[Settings]\nCurrentGpu=0\n", encoding="utf-8")
            (profiles / "VEN_10DE&DEV_OLD.cfg").write_text(
                "[Profile1]\n"
                "PowerLimit=62\n"
                "ThermalLimit=70\n"
                "CoreClkBoost=-92000\n"
                "MemClkBoost=-352000\n"
                "FanMode=1\n"
                "FanSpeed=25\n",
                encoding="utf-8",
            )
            (profiles / "VEN_1002&DEV_CURRENT.cfg").write_text(
                "[Profile1]\n"
                "PowerLimit=0\n"
                "CoreClk=1310000\n"
                "MemClk=2028000\n"
                "FanMode=0\n"
                "FanSpeed=30\n",
                encoding="utf-8",
            )

            details = afterburner_profile_details_from_dir(profiles)

            self.assertEqual(details[1]["PowerLimit"], "0")
            self.assertEqual(details[1]["CoreClk"], "1310000")
            self.assertEqual(details[1]["MemClk"], "2028000")
            self.assertNotIn("CoreClkBoost", details[1])

            display = format_afterburner_profile_details(1, details[1])
            self.assertIn("Core clock: 1310 MHz", display)
            self.assertIn("Memory clock: 2028 MHz", display)
            self.assertIn("Fan speed: manual 30%", display)
            self.assertNotIn("-92 MHz", display)

    def test_single_device_profile_is_used_without_current_gpu_setting(self):
        with tempfile.TemporaryDirectory() as td:
            profiles = Path(td)
            (profiles / "VEN_10DE&DEV_ONLY.cfg").write_text(
                "[Profile2]\nCoreClkBoost=45000\nMemClkBoost=100000\n",
                encoding="utf-8",
            )

            details = afterburner_profile_details_from_dir(profiles)
            display = format_afterburner_profile_details(2, details[2])

            self.assertIn("Core: +45 MHz", display)
            self.assertIn("Memory: +100 MHz", display)


if __name__ == "__main__":
    unittest.main()
