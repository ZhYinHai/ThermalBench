import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

_repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from core.prime95_compat_v305 import load_prime95_torture_snapshot


class Prime95SnapshotHelperTests(unittest.TestCase):
    def test_parses_prime_txt_and_infers_preset(self):
        with tempfile.TemporaryDirectory() as td:
            prime_dir = Path(td)
            (prime_dir / "prime.txt").write_text(
                """
MinTortureFFT = 329
MaxTortureFFT = 8192
TortureMem = 29691
TortureTime = 6
TortureWeak = 1048576
""".strip(),
                encoding="utf-8",
            )

            snapshot = load_prime95_torture_snapshot(prime_dir)

            self.assertEqual(snapshot["settings"]["MinTortureFFT"], "329")
            self.assertEqual(snapshot["settings"]["MaxTortureFFT"], "8192")
            self.assertEqual(
                snapshot["settings_summary"],
                "Min FFT size (in K): 329 / Max FFT size (in K): 8192 / Memory to use (in MB): 29691 / Time to run each FFT size (in minutes): 6 / Disable AVX-512: true / Disable AVX2 (fused multiply-add): false / Disable AVX: false / Disable SSE2: false",
            )
            self.assertEqual(snapshot["inferred_preset"]["preset_name"], "Large FFTs")

    def test_preserves_existing_snapshot_json(self):
        with tempfile.TemporaryDirectory() as td:
            snap = {
                "prime_exe": r"C:\Prime95\prime95.exe",
                "source_files": [r"C:\Prime95\prime.txt"],
                "settings": {
                    "MinTortureFFT": "4",
                    "MaxTortureFFT": "8192",
                },
                "inferred_preset": {
                    "preset_name": "Blend",
                    "confidence": "high",
                    "rationale": "example",
                    "method": "deterministic-topology-match",
                    "matched_candidates": ["Blend@workers=16"],
                },
            }
            json_path = Path(td) / "prime95_torture_settings.json"
            json_path.write_text(json.dumps(snap), encoding="utf-8")

            snapshot = load_prime95_torture_snapshot(json_path)

            self.assertEqual(snapshot["prime_exe"], r"C:\Prime95\prime95.exe")
            self.assertEqual(snapshot["settings_summary"], "Min FFT size (in K): 4 / Max FFT size (in K): 8192")
            self.assertEqual(snapshot["inferred_preset"]["preset_name"], "Blend")
            self.assertEqual(snapshot["inferred_preset"]["confidence"], "high")


if __name__ == "__main__":
    unittest.main()
