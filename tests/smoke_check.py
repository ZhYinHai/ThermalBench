import importlib
import os
import sys

_repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

modules = [
    'core.resources',
    'ui',
    'ui.ui_widgets',
    'ui.ui_theme',
    'app',
]

for m in modules:
    try:
        importlib.import_module(m)
        print(m, 'OK')
    except Exception as e:
        print(m, 'ERR:', type(e).__name__, e)
