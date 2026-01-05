# settings_store.py
import json
import os
from pathlib import Path
from typing import Any


def get_settings_path(app_name: str = "ThermalBench") -> Path:
    """
    Store settings somewhere writable for BOTH:
      - normal workspace runs
      - PyInstaller dist runs (where _internal may not be writable)
    """
    base = os.getenv("LOCALAPPDATA") or os.getenv("APPDATA")
    if base:
        p = Path(base) / app_name
    else:
        p = Path.home() / f".{app_name.lower()}"
    p.mkdir(parents=True, exist_ok=True)
    return p / "settings.json"


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_json(path: Path, data: dict[str, Any]) -> None:
    try:
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except Exception:
        # don't crash the app if disk is locked / read-only
        pass
