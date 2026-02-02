"""Backward-compatible import shim.

Older code/tests import `ui.ui_settings_dialog`; the implementation lives in `ui.dialogs.ui_settings_dialog`.
"""

from ui.dialogs.ui_settings_dialog import *  # noqa: F401,F403
