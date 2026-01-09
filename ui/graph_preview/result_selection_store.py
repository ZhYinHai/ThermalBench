"""Per-result (folder) sensor selection persistence.

Kept intentionally small and behavior-compatible with the original helpers that
lived in `ui/graph_preview.py`.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional


def get_selection_json_path(preview_csv_path: Optional[str]) -> Optional[Path]:
    """Return the JSON path for the current result folder (where run_window.csv lives)."""
    try:
        if not preview_csv_path:
            return None
        csvp = Path(preview_csv_path)
        if not csvp.exists():
            return None
        return csvp.parent / "sensor_selection.json"
    except Exception:
        return None


def load_active_cols(preview_csv_path: Optional[str]) -> Optional[list[str]]:
    """Load saved list of active sensors for this result folder, or None."""
    try:
        jp = get_selection_json_path(preview_csv_path)
        if jp is None or not jp.exists():
            return None

        with jp.open("r", encoding="utf-8") as f:
            data = json.load(f)

        cols = data.get("active_cols", None)
        if not isinstance(cols, list):
            return None

        return [str(c) for c in cols]
    except Exception:
        return None


def save_active_cols(
    preview_csv_path: Optional[str],
    *,
    active_cols: Optional[list[str]],
    available_cols: Optional[list[str]],
) -> None:
    """Save current active sensors for this result folder."""
    try:
        jp = get_selection_json_path(preview_csv_path)
        if jp is None:
            return

        active = list(active_cols or [])
        available = list(available_cols or [])

        # must always store at least one
        if not active and available:
            active = [available[0]]

        payload = {"active_cols": active}

        tmp = jp.with_suffix(".json.tmp")
        with tmp.open("w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)

        tmp.replace(jp)
    except Exception:
        pass


def apply_saved_or_default_active_cols(
    *,
    available_cols: Optional[list[str]],
    saved_cols: Optional[list[str]],
) -> list[str]:
    """Compute active cols after a CSV is loaded.

    Mirrors original behavior:
    - If saved exists, keep only those still present
    - Else default to all available
    - Ensure at least one if available
    """
    available = list(available_cols or [])

    try:
        saved = list(saved_cols or [])
        if saved:
            saved = [c for c in saved if c in available]
        if not saved:
            saved = list(available)
        if not saved and available:
            saved = [available[0]]
        return list(saved)
    except Exception:
        return list(available)
