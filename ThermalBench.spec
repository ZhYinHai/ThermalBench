# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import (
    collect_all,
    collect_data_files,
    collect_dynamic_libs,
    copy_metadata,
)

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

# PySide6 runtime modules used by the GUI.
# Do not collect all PySide6 submodules; that also pulls in PySide6 developer/deployment scripts.
hiddenimports += [
    'PySide6.QtCore',
    'PySide6.QtGui',
    'PySide6.QtWidgets',
]

try:
    binaries += collect_dynamic_libs('PySide6')
    datas += collect_data_files('PySide6')
except Exception as e:
    print(f'Warning: could not collect PySide6 data/libs: {e}')

# Runtime packages used by the GUI and graph preview.
# These must be included in the main ThermalBench.exe bundle, not only in the plotter EXE.
def _not_tests(name: str) -> bool:
    lowered = name.lower()
    return (
        ".tests" not in lowered
        and ".test" not in lowered
        and "pandas.core._numba" not in lowered
        and not lowered.endswith(".tests")
        and not lowered.endswith(".test")
    )

for pkg in [
    'pandas',
    'numpy',
    'matplotlib',
    'pytz',
    'dateutil',
    'tzdata',
    'psutil',
    'requests',
    'packaging',
    'temper_windows',
    'pywinusb',
]:
    try:
        pkg_datas, pkg_binaries, pkg_hiddenimports = collect_all(
            pkg,
            filter_submodules=_not_tests,
            on_error='warn once',
        )
        datas += pkg_datas
        binaries += pkg_binaries
        hiddenimports += pkg_hiddenimports
    except Exception as e:
        print(f'Warning: could not collect {pkg}: {e}')

# Package metadata needed by libraries that check dependency versions at runtime.
for dist in [
    'pandas',
    'numpy',
    'matplotlib',
    'pytz',
    'tzdata',
    'python-dateutil',
    'six',
    'packaging',
    'certifi',
]:
    try:
        datas += copy_metadata(dist)
    except Exception as e:
        print(f'Warning: could not copy metadata for {dist}: {e}')

a = Analysis(
    ['app.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'pytest',
        'pandas.tests',
        'numpy.tests',
        'matplotlib.tests',
        'numba',
        'pandas.core._numba',
    ],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

# Windows application manifest declaring per-monitor V2 DPI awareness.
# This ensures Windows does NOT bitmap-scale the window before any Python code runs,
# which would cause blurry rendering at 125 %, 150 %, etc.
_dpi_manifest = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<assembly xmlns="urn:schemas-microsoft-com:asm.v1" manifestVersion="1.0">
  <assemblyIdentity version="1.0.0.0" processorArchitecture="*"
                    name="ThermalBench" type="win32"/>
  <dependency>
    <dependentAssembly>
      <assemblyIdentity type="win32" name="Microsoft.Windows.Common-Controls"
                        version="6.0.0.0" processorArchitecture="*"
                        publicKeyToken="6595b64144ccf1df" language="*"/>
    </dependentAssembly>
  </dependency>
  <trustInfo xmlns="urn:schemas-microsoft-com:asm.v2">
    <security>
      <requestedPrivileges>
        <requestedExecutionLevel level="asInvoker" uiAccess="false"/>
      </requestedPrivileges>
    </security>
  </trustInfo>
  <application xmlns="urn:schemas-microsoft-com:asm.v3">
    <windowsSettings>
      <dpiAwareness xmlns="http://schemas.microsoft.com/SMI/2016/WindowsSettings">PerMonitorV2</dpiAwareness>
      <dpiAware xmlns="http://schemas.microsoft.com/SMI/2005/WindowsSettings">True/PM</dpiAware>
    </windowsSettings>
  </application>
</assembly>"""

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
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    manifest=_dpi_manifest,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='ThermalBench',
)
