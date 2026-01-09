"""Helpers for deciding what GraphPreview should display for a given path/folder.

This keeps the decision logic (CSV vs image) out of GraphPreview while preserving
existing behavior.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional


_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".bmp"}


def is_csv_file(path: Path) -> bool:
    try:
        return path.is_file() and path.suffix.lower() == ".csv"
    except Exception:
        return False


def is_image_file(path: Path) -> bool:
    try:
        return path.is_file() and path.suffix.lower() in _IMAGE_SUFFIXES
    except Exception:
        return False


def choose_preview_file_for_folder(folder: str) -> Optional[Path]:
    """Return the preferred preview file for a run folder.

    Mirrors GraphPreview.preview_folder behavior:
    - Prefer run_window.csv
    - Else prefer ALL_SELECTED.png
    - Else None
    """
    try:
        p = Path(folder)
        if not p.exists() or not p.is_dir():
            return None

        csv_path = p / "run_window.csv"
        if csv_path.exists() and csv_path.is_file():
            return csv_path

        png_path = p / "ALL_SELECTED.png"
        if png_path.exists() and png_path.is_file():
            return png_path

        return None
    except Exception:
        return None
