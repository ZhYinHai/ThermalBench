; ThermalBench Inno Setup script
; Build with:
;   ISCC.exe installer\ThermalBench.iss /DMyAppVersion=1.2.3
;
; You can override these with ISCC /D... defines.

#ifndef MyAppName
  #define MyAppName "ThermalBench"
#endif

#ifndef MyAppVersion
  #define MyAppVersion "0.0.40"
#endif

#ifndef InstallerPrefix
  #define InstallerPrefix "ThermalBench-Setup-v"
#endif

#ifndef AppExeName
  #define AppExeName "ThermalBench.exe"
#endif

#ifndef SourceDir
  ; Default to PyInstaller onedir output
  #define SourceDir "..\\dist\\ThermalBench"
#endif

#ifndef OutputDir
  #define OutputDir "..\\dist_installer"
#endif

; A stable AppId ensures upgrades/uninstall work across versions.
#ifndef AppId
  #define AppId "{{B56E3B51-2A9A-4B2B-8B66-64B5A11E2C0D}"
#endif

[Setup]
AppId={#AppId}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppName}
DefaultDirName={autopf}\{#MyAppName}
DisableDirPage=no
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
OutputDir={#OutputDir}
OutputBaseFilename={#InstallerPrefix}{#MyAppVersion}
Compression=lzma2
SolidCompression=yes
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=admin
SetupIconFile=..\\resources\\thermal_bench.ico
WizardStyle=modern

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop icon"; GroupDescription: "Additional icons:"; Flags: unchecked

[Files]
; Install the full PyInstaller onedir bundle.
; Runtime data such as hwinfo.csv and runs/ belongs in Documents\ThermalBench,
; not inside Program Files.
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion; Excludes: "tools\HWiNFO\hwinfo.csv,tools\Prime95\*,tools\MSI Afterburner\*,runs\*"
; Prime95 must be writable (local.txt/results.txt), so install bundle files
; directly under LOCALAPPDATA instead of Program Files.
Source: "{#SourceDir}\tools\Prime95\*"; DestDir: "{localappdata}\ThermalBench\tools\Prime95"; Flags: recursesubdirs createallsubdirs ignoreversion
; MSI Afterburner writes profile/config/runtime files next to its executable,
; so keep the bundled copy under writable LOCALAPPDATA as well. Do not overwrite
; local profiles/settings during updates; those are machine/GPU-specific.
Source: "{#SourceDir}\tools\MSI Afterburner\*"; DestDir: "{localappdata}\ThermalBench\tools\MSI Afterburner"; Flags: recursesubdirs createallsubdirs ignoreversion; Excludes: "Profiles\*,MSIAfterburner.cfg"

[InstallDelete]
; Upgrade cleanup: remove legacy Prime95 copies from Program Files so runtime
; always relies on the writable AppData location.
Type: filesandordirs; Name: "{app}\tools\Prime95"
Type: filesandordirs; Name: "{app}\tools\prime95"
Type: files; Name: "{app}\tools\prime95.exe"
Type: filesandordirs; Name: "{app}\tools\MSI Afterburner"

[Registry]
Root: HKCU; Subkey: "Software\ThermalBench"; ValueType: string; ValueName: "InstallDir"; ValueData: "{app}"; Flags: uninsdeletekeyifempty
Root: HKCU; Subkey: "Software\ThermalBench"; ValueType: string; ValueName: "HwinfoExe"; ValueData: "{app}\tools\HWiNFO\HWiNFO64.exe"; Flags: uninsdeletekeyifempty
; FurMarkExe and PrimeExe are intentionally not written here.
; These tools are copied to %LOCALAPPDATA%\ThermalBench\tools\ at runtime so
; GeeXLab / Prime95 / MSI Afterburner can write their log files without UAC issues.
; The Python resolver (core/bundled_tools.py) handles path resolution.

[Icons]
Name: "{autoprograms}\\{#MyAppName}"; Filename: "{app}\\{#AppExeName}"
Name: "{autodesktop}\\{#MyAppName}"; Filename: "{app}\\{#AppExeName}"; Tasks: desktopicon

[Run]
; Normal UI install: show the optional "Launch" checkbox on the final page.
Filename: "{app}\\{#AppExeName}"; Description: "Launch {#MyAppName}"; Flags: nowait postinstall; Check: not WizardSilent

; Silent install (used by in-app updater): auto-launch after installation completes.
Filename: "{app}\\{#AppExeName}"; Flags: nowait runhidden; Check: WizardSilent
