; ThermalBench Inno Setup script
; Build with:
;   ISCC.exe installer\ThermalBench.iss /DMyAppVersion=1.2.3
;
; You can override these with ISCC /D... defines.

#ifndef MyAppName
  #define MyAppName "ThermalBench"
#endif

#ifndef MyAppVersion
  #define MyAppVersion "0.0.0"
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
DefaultDirName={localappdata}\\Programs\\{#MyAppName}
DisableDirPage=no
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
OutputDir={#OutputDir}
OutputBaseFilename={#InstallerPrefix}{#MyAppVersion}
Compression=lzma2
SolidCompression=yes
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=lowest
SetupIconFile=..\\resources\\thermal_bench.ico
WizardStyle=modern

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop icon"; GroupDescription: "Additional icons:"; Flags: unchecked

[Files]
; Install the full PyInstaller onedir bundle
Source: "{#SourceDir}\\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{autoprograms}\\{#MyAppName}"; Filename: "{app}\\{#AppExeName}"
Name: "{autodesktop}\\{#MyAppName}"; Filename: "{app}\\{#AppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\\{#AppExeName}"; Description: "Launch {#MyAppName}"; Flags: nowait postinstall skipifsilent
