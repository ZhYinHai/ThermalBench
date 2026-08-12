import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

_repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from core import bundled_tools


class Prime95RuntimeResolutionTests(unittest.TestCase):
    def _norm(self, p: str | Path) -> str:
        return os.path.normcase(os.path.normpath(str(Path(p).resolve())))

    def test_resolve_returns_runtime_when_already_present(self):
        with tempfile.TemporaryDirectory() as td:
            runtime = Path(td) / "runtime"
            runtime.mkdir(parents=True, exist_ok=True)
            runtime_exe = runtime / "prime95.exe"
            runtime_exe.write_text("exe", encoding="utf-8")

            configured = Path(td) / "configured" / "prime95.exe"
            configured.parent.mkdir(parents=True, exist_ok=True)
            configured.write_text("other", encoding="utf-8")

            with patch.object(bundled_tools, "ensure_prime95_runtime_dir", return_value=runtime):
                with patch.object(bundled_tools, "_bundled_prime95_dir", return_value=Path(td) / "missing"):
                    with patch.object(bundled_tools, "_read_hkcu_string", return_value=""):
                        resolved = bundled_tools.resolve_prime95_exe(str(configured))

            self.assertEqual(self._norm(resolved), self._norm(runtime_exe))

    def test_resolve_seeds_runtime_from_configured_source(self):
        with tempfile.TemporaryDirectory() as td:
            runtime = Path(td) / "runtime"
            runtime.mkdir(parents=True, exist_ok=True)

            configured_dir = Path(td) / "configured"
            configured_dir.mkdir(parents=True, exist_ok=True)
            configured_exe = configured_dir / "prime95.exe"
            configured_exe.write_text("exe", encoding="utf-8")
            (configured_dir / "readme.txt").write_text("payload", encoding="utf-8")

            with patch.object(bundled_tools, "ensure_prime95_runtime_dir", return_value=runtime):
                with patch.object(bundled_tools, "_bundled_prime95_dir", return_value=Path(td) / "missing"):
                    with patch.object(bundled_tools, "_read_hkcu_string", return_value=""):
                        resolved = bundled_tools.resolve_prime95_exe(str(configured_exe))

            self.assertEqual(self._norm(resolved), self._norm(runtime / "prime95.exe"))
            self.assertTrue((runtime / "prime95.exe").exists())
            self.assertTrue((runtime / "readme.txt").exists())


if __name__ == "__main__":
    unittest.main()
