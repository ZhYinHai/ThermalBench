import importlib

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
