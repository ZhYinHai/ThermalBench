"""Backward-compatible import shim.

Older code/tests import `ui.ui_widgets`; the implementation lives in `ui.widgets.ui_widgets`.
"""

from ui.widgets.ui_widgets import *  # noqa: F401,F403
