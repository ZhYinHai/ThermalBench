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
