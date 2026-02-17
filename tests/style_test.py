import os
import sys

_repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _repo_root not in sys.path:
	sys.path.insert(0, _repo_root)

from PySide6.QtWidgets import QApplication

from ui.widgets.ui_widgets import CustomComboBox
from ui.ui_settings_dialog import SettingsDialog

app = QApplication([])
# Instantiate some widgets that apply stylesheets
cb = CustomComboBox(mode='dark')
dlg = SettingsDialog(None, furmark_exe='', prime_exe='', theme='dark')
print('widgets created')
# Clean up
app.quit()
