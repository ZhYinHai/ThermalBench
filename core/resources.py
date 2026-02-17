from pathlib import Path
import sys


def app_root() -> Path:
  """Return the application root folder.

  - Dev: repository root
  - Frozen (PyInstaller): folder containing the executable
  """
  if getattr(sys, "frozen", False):
    try:
      return Path(sys.executable).resolve().parent
    except Exception:
      return Path.cwd()
  return Path(__file__).resolve().parent.parent


def resource_path(*parts: str) -> Path:
    """Return a Path to a resource file.

    Behavior:
    - When running normally in development, returns a path under the repository root
      (parent of the package directories).
    - When bundled with PyInstaller (onefile/onedir), detect `sys._MEIPASS` and
      return a path relative to that extraction folder so bundled resources work.

    Usage:
        resource_path('resources', 'icons', 'down_triangle.svg')
    """
    # If running from a PyInstaller bundle, resources are extracted to sys._MEIPASS
    if getattr(sys, "_MEIPASS", None):
        base = Path(sys._MEIPASS)
    else:
        base = Path(__file__).resolve().parent.parent
    return base.joinpath(*parts)
