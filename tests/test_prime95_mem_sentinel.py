import os
import sys
import json
import tempfile
import unittest
from pathlib import Path

_repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from core.prime95_compat_v305 import is_run_ffts_in_place, load_prime95_torture_snapshot


class Prime95MemSentinelTests(unittest.TestCase):
    def test_torture_mem_8_indicates_run_ffts_in_place(self):
        self.assertTrue(is_run_ffts_in_place({"TortureMem": "8"}))
        self.assertTrue(is_run_ffts_in_place({"RunFFTsInPlace": "1"}))
        self.assertFalse(is_run_ffts_in_place({"TortureMem": "0"}))

    def test_torture_mem_8_displays_as_zero(self):
        with tempfile.TemporaryDirectory() as td:
            payload = {
                "prime_exe": r"C:\Prime95\prime95.exe",
                "source_files": [r"C:\Prime95\prime.txt"],
                "settings": {
                    "MinTortureFFT": "4",
                    "MaxTortureFFT": "21",
                    "TortureMem": "8",
                    "TortureTime": "6",
                    "TortureWeak": "1081344",
                },
                "inferred_preset": {
                    "preset_name": "Smallest FFTs",
                    "confidence": "medium",
                    "rationale": "test",
                    "method": "deterministic-topology-match",
                    "matched_candidates": [],
                },
            }
            p = Path(td) / "prime95_torture_settings.json"
            p.write_text(json.dumps(payload), encoding="utf-8")

            snapshot = load_prime95_torture_snapshot(p)
            self.assertIn("Memory to use (in MB): 0", snapshot.get("settings_summary", ""))


if __name__ == "__main__":
    unittest.main()
