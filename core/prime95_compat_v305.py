from __future__ import annotations

import json
from pathlib import Path
from typing import Any


_PRIME95_SETTINGS_KEYS = (
	"MinTortureFFT",
	"MaxTortureFFT",
	"TortureMem",
	"TortureTime",
	"TortureWeak",
	"TortureHyperthreading",
	"WorkerThreads",
	"CoresPerTest",
)


def _int_or_none(value: Any) -> int | None:
	try:
		if value is None:
			return None
		text = str(value).strip()
		if not text:
			return None
		return int(text)
	except Exception:
		return None


def _load_json(path: Path) -> dict[str, Any]:
	try:
		data = json.loads(path.read_text(encoding="utf-8"))
	except Exception:
		return {}
	return data if isinstance(data, dict) else {}


def _parse_prime_txt(path: Path) -> dict[str, str]:
	settings: dict[str, str] = {}
	try:
		for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
			if "=" not in line:
				continue
			left, right = line.split("=", 1)
			key = left.strip()
			value = right.strip()
			if key:
				settings[key] = value
	except Exception:
		return {}
	return settings


def is_run_ffts_in_place(settings: dict[str, Any] | None) -> bool:
	"""Return True when Prime95 is using the internal sentinel for in-place FFTs."""
	if not isinstance(settings, dict):
		return False
	for key in ("RunFFTsInPlace", "TortureMem"):
		value = settings.get(key)
		if value is None:
			continue
		try:
			parsed = int(str(value).strip())
		except Exception:
			continue
		if key == "RunFFTsInPlace":
			return parsed != 0
		if key == "TortureMem":
			return parsed == 8
	return False


def _format_settings_summary(settings: dict[str, Any]) -> str:
	parts: list[str] = []
	labels = {
		"MinTortureFFT": "Min FFT size (in K)",
		"MaxTortureFFT": "Max FFT size (in K)",
		"TortureMem": "Memory to use (in MB)",
		"TortureTime": "Time to run each FFT size (in minutes)",
	}
	for key in _PRIME95_SETTINGS_KEYS:
		label = labels.get(key)
		if label is None:
			continue
		value = settings.get(key)
		if value is None:
			continue
		text = str(value).strip()
		if not text:
			continue
		if key == "TortureMem":
			# Prime95 may persist 8 as an internal sentinel for GUI value 0.
			if text == "8":
				text = "0"
		parts.append(f"{label}: {text}")

	torture_weak = _int_or_none(settings.get("TortureWeak"))
	if torture_weak is not None:
		avx512_on = bool(torture_weak & 0x100000)
		avx2_on = bool(torture_weak & 0x8000)
		avx_on = bool(torture_weak & 0x4000)
		sse2_on = bool(torture_weak & 0x0200)
		parts.append(f"Disable AVX-512: {'true' if avx512_on else 'false'}")
		parts.append(f"Disable AVX2 (fused multiply-add): {'true' if avx2_on else 'false'}")
		parts.append(f"Disable AVX: {'true' if avx_on else 'false'}")
		parts.append(f"Disable SSE2: {'true' if sse2_on else 'false'}")

	if is_run_ffts_in_place(settings):
		parts.append("Run FFTs in-place: true")
	return " / ".join(parts) if parts else "No Prime95 torture settings found."


def _infer_preset_name(settings: dict[str, Any]) -> str:
	min_fft = _int_or_none(settings.get("MinTortureFFT"))
	max_fft = _int_or_none(settings.get("MaxTortureFFT"))
	torture_mem = _int_or_none(settings.get("TortureMem"))

	if min_fft is None or max_fft is None:
		return "unknown"

	# Best-effort preset inference from the saved FFT bounds.
	if min_fft <= 8 and max_fft >= 8192:
		return "Blend"
	if max_fft <= 512:
		return "Smallest FFTs" if min_fft <= 8 else "Small FFTs"
	if max_fft <= 2048:
		return "Small FFTs" if min_fft <= 32 else "Medium FFTs"
	if max_fft <= 4096:
		return "Medium FFTs"
	if max_fft >= 8192:
		if min_fft >= 64:
			return "Large FFTs"
		if torture_mem is not None and torture_mem > 0:
			return "Blend"
		return "Large FFTs"

	return "unknown"


def load_prime95_torture_snapshot(source: str | Path | None) -> dict[str, Any]:
	"""Return the Prime95 torture snapshot for a file, directory, or exe path."""

	text = str(source or "").strip()
	if not text:
		return {
			"prime_exe": "",
			"source_files": [],
			"settings": {},
			"settings_summary": "No Prime95 torture settings found.",
			"inferred_preset": {
				"preset_name": "unknown",
				"confidence": "low",
				"rationale": "No Prime95 settings were available.",
				"method": "best-effort-fft-bounds",
				"matched_candidates": [],
			},
		}

	path = Path(text).expanduser()

	if path.is_file() and path.suffix.lower() == ".json":
		data = _load_json(path)
		if data:
			settings = data.get("settings") if isinstance(data.get("settings"), dict) else {}
			inferred = data.get("inferred_preset") if isinstance(data.get("inferred_preset"), dict) else {}
			return {
				"prime_exe": str(data.get("prime_exe") or ""),
				"source_files": [str(s) for s in (data.get("source_files") or []) if str(s).strip()],
				"settings": settings,
				"settings_summary": _format_settings_summary(settings),
				"inferred_preset": {
					"preset_name": str(inferred.get("preset_name") or "unknown"),
					"confidence": str(inferred.get("confidence") or "low"),
					"rationale": str(inferred.get("rationale") or ""),
					"method": str(inferred.get("method") or "best-effort-fft-bounds"),
					"matched_candidates": list(inferred.get("matched_candidates") or []),
				},
			}

	if path.is_dir():
		prime_txt = path / "prime.txt"
		snapshot_json = path / "prime95_torture_settings.json"
		prime_exe = ""
	else:
		prime_txt = path.with_name("prime.txt") if path.suffix.lower() == ".exe" else path
		snapshot_json = path.with_name("prime95_torture_settings.json") if path.suffix.lower() == ".exe" else path.with_suffix(".json")
		prime_exe = str(path) if path.suffix.lower() == ".exe" else ""

	if snapshot_json.is_file():
		data = _load_json(snapshot_json)
		if data:
			settings = data.get("settings") if isinstance(data.get("settings"), dict) else {}
			inferred = data.get("inferred_preset") if isinstance(data.get("inferred_preset"), dict) else {}
			return {
				"prime_exe": str(data.get("prime_exe") or prime_exe),
				"source_files": [str(s) for s in (data.get("source_files") or []) if str(s).strip()],
				"settings": settings,
				"settings_summary": _format_settings_summary(settings),
				"inferred_preset": {
					"preset_name": str(inferred.get("preset_name") or "unknown"),
					"confidence": str(inferred.get("confidence") or "low"),
					"rationale": str(inferred.get("rationale") or ""),
					"method": str(inferred.get("method") or "best-effort-fft-bounds"),
					"matched_candidates": list(inferred.get("matched_candidates") or []),
				},
			}

	settings = _parse_prime_txt(prime_txt) if prime_txt.is_file() else {}
	preset_name = _infer_preset_name(settings)
	confidence = "medium" if preset_name not in {"unknown", "ambiguous"} else "low"
	return {
		"prime_exe": prime_exe,
		"source_files": [str(prime_txt)] if prime_txt.is_file() else [],
		"settings": settings,
		"settings_summary": _format_settings_summary(settings),
		"inferred_preset": {
			"preset_name": preset_name,
			"confidence": confidence,
			"rationale": "Best-effort inference from prime.txt FFT bounds.",
			"method": "best-effort-fft-bounds",
			"matched_candidates": [],
		},
	}
