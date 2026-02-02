"""Backward-compatible import shim.

Older code/tests import `ui.ui_theme`; the implementation lives in `ui.widgets.ui_theme`.
"""

from ui.widgets.ui_theme import *  # noqa: F401,F403
