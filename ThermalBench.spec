# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_all, collect_submodules, collect_data_files, collect_dynamic_libs

# Icon path (relative to spec file location)
icon_path = 'resources/thermal_bench.ico'

datas = [
    ('resources', 'resources'),
    ('cli', 'cli'),
    ('core', 'core'),
    ('ui', 'ui'),
    ('ambient_logger.py', '.'),
]
binaries = []
hiddenimports = []

# Requests relies on certifi's CA bundle. In frozen apps the data file can be missed unless
# we explicitly bundle it.
try:
    datas += collect_data_files('certifi')
    # Be extra explicit: some PyInstaller layouts can still omit cacert.pem.
    try:
        import certifi

        _ca_path = certifi.where()
        if _ca_path:
            datas += [(_ca_path, 'certifi')]
    except Exception:
        pass
except Exception:
    pass

# Collect PySide6 explicitly (collect_all failed, so use individual helpers)
try:
    hiddenimports += collect_submodules('PySide6')
    datas += collect_data_files('PySide6', include_py_files=True)
    binaries += collect_dynamic_libs('PySide6')
except Exception:
    # Fallback: add known critical imports
    hiddenimports += [
        'PySide6.QtCore',
        'PySide6.QtGui',
        'PySide6.QtWidgets',
    ]


a = Analysis(
    ['app.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='ThermalBench',
    debug=False,
    icon=icon_path,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='ThermalBench',
)
