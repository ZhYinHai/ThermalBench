param(
    [string]$Configuration = "Release",
    [string]$InnoIsccPath = ""
)

$ErrorActionPreference = 'Stop'

function Get-AppVersion {
    $versionFile = Join-Path $PSScriptRoot 'core\version.py'
    if (-not (Test-Path $versionFile)) {
        throw "Missing core/version.py at $versionFile"
    }

    $content = Get-Content -LiteralPath $versionFile -Raw
    $m = [regex]::Match($content, '__version__\s*=\s*"(?<v>[^"]+)"')
    if (-not $m.Success) {
        throw "Could not parse __version__ from core/version.py"
    }

    return $m.Groups['v'].Value
}

function Find-ISCC {
    param([string]$Explicit)

    if ($Explicit -and (Test-Path $Explicit)) {
        return (Resolve-Path $Explicit).Path
    }

    if ($env:INNO_SETUP_ISCC -and (Test-Path $env:INNO_SETUP_ISCC)) {
        return (Resolve-Path $env:INNO_SETUP_ISCC).Path
    }

    $cmd = Get-Command -Name 'ISCC.exe' -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }

    $candidates = @(
        "$env:ProgramFiles(x86)\Inno Setup 6\ISCC.exe",
        "$env:ProgramFiles\Inno Setup 6\ISCC.exe",
        "$env:ProgramFiles(x86)\Inno Setup 5\ISCC.exe",
        "$env:ProgramFiles\Inno Setup 5\ISCC.exe",
        "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe",
        "$env:LOCALAPPDATA\Programs\Inno Setup 5\ISCC.exe"
    )

    foreach ($c in $candidates) {
        if ($c -and (Test-Path $c)) {
            return (Resolve-Path $c).Path
        }
    }

    # Registry-based discovery (helps when installed in non-default location)
    $uninstallRoots = @(
        'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall',
        'HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall',
        'HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall'
    )

    foreach ($root in $uninstallRoots) {
        try {
            foreach ($k in (Get-ChildItem -LiteralPath $root -ErrorAction SilentlyContinue)) {
                $p = Get-ItemProperty -LiteralPath $k.PSPath -ErrorAction SilentlyContinue
                if (-not $p) { continue }

                $name = [string]$p.DisplayName
                if ($name -notlike 'Inno Setup*') { continue }

                $installLocation = [string]$p.InstallLocation
                if ($installLocation) {
                    $guess = Join-Path $installLocation 'ISCC.exe'
                    if (Test-Path $guess) { return (Resolve-Path $guess).Path }
                }

                $displayIcon = [string]$p.DisplayIcon
                if ($displayIcon) {
                    $iconPath = $displayIcon.Split(',')[0].Trim('"')
                    if ($iconPath -and (Test-Path $iconPath)) {
                        $base = Split-Path -Parent $iconPath
                        $guess2 = Join-Path $base 'ISCC.exe'
                        if (Test-Path $guess2) { return (Resolve-Path $guess2).Path }
                    }
                }
            }
        } catch {
            # ignore
        }
    }

    return $null
}

$version = Get-AppVersion
Write-Host "Building ThermalBench installer for version $version" -ForegroundColor Cyan

# 1) Build PyInstaller onedir
$python = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path $python)) {
    throw "Python venv not found at $python. Activate/create venv first."
}

$spec = Join-Path $PSScriptRoot 'ThermalBench.spec'
if (-not (Test-Path $spec)) {
    throw "Missing $spec"
}

Write-Host "Running PyInstaller..." -ForegroundColor Cyan
& $python -m PyInstaller --noconfirm --clean $spec
if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller failed for ThermalBench.spec (exit code $LASTEXITCODE)"
}

$distExe = Join-Path $PSScriptRoot 'dist\ThermalBench\ThermalBench.exe'
if (-not (Test-Path $distExe)) {
    throw "Expected PyInstaller output not found: $distExe"
}

$distInternal = Join-Path $PSScriptRoot 'dist\ThermalBench\_internal'
if (-not (Test-Path $distInternal)) {
    throw "PyInstaller output looks incomplete (missing _internal): $distInternal"
}

# 1b) Build standalone ambient logger (so releases don't depend on a system Python)
$ambientScript = Join-Path $PSScriptRoot 'ambient_logger.py'
if (Test-Path $ambientScript) {
    $ambientName = 'ThermalBench-AmbientLogger'
    $ambientDistDirTmp = Join-Path $PSScriptRoot 'dist\\_ambient_logger'
    $ambientDistFinal = Join-Path $PSScriptRoot 'dist\\ThermalBench'
    $ambientWorkDir = Join-Path $PSScriptRoot 'build\ambient_logger'
    New-Item -ItemType Directory -Force -Path $ambientWorkDir | Out-Null
    New-Item -ItemType Directory -Force -Path $ambientDistDirTmp | Out-Null

    Write-Host "Building ambient logger executable..." -ForegroundColor Cyan
    & $python -m PyInstaller --noconfirm --clean --onefile --console `
        --name $ambientName `
        --distpath $ambientDistDirTmp `
        --workpath $ambientWorkDir `
        --specpath $ambientWorkDir `
        $ambientScript

    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller failed for ambient_logger.py (exit code $LASTEXITCODE)"
    }

    $ambientExeTmp = Join-Path $ambientDistDirTmp ("{0}.exe" -f $ambientName)
    $ambientExeFinal = Join-Path $ambientDistFinal ("{0}.exe" -f $ambientName)
    if (-not (Test-Path $ambientExeTmp)) {
        throw "Ambient logger build succeeded but expected output not found: $ambientExeTmp"
    }

    # Copy into the onedir app folder (avoid building directly into dist\ThermalBench,
    # which can break PyInstaller's own cleanup on subsequent builds).
    try {
        if (Test-Path $ambientExeFinal) {
            Remove-Item -Force $ambientExeFinal -ErrorAction SilentlyContinue
        }
    } catch {}
    Copy-Item -Force -LiteralPath $ambientExeTmp -Destination $ambientExeFinal
    if (-not (Test-Path $ambientExeFinal)) {
        throw "Failed to copy ambient logger into app bundle: $ambientExeFinal"
    }
} else {
    Write-Host "Ambient logger script not found; skipping ambient logger EXE build: $ambientScript" -ForegroundColor Yellow
}

# 2) Compile Inno Setup installer
$iscc = Find-ISCC -Explicit $InnoIsccPath
if (-not $iscc) {
    throw "ISCC.exe not found. Install Inno Setup, or pass -InnoIsccPath 'C:\\Program Files (x86)\\Inno Setup 6\\ISCC.exe', or set env INNO_SETUP_ISCC." 
}

$iss = Join-Path $PSScriptRoot 'installer\ThermalBench.iss'
if (-not (Test-Path $iss)) {
    throw "Missing $iss"
}

$outDir = Join-Path $PSScriptRoot 'dist_installer'
New-Item -ItemType Directory -Force -Path $outDir | Out-Null

Write-Host "Running Inno Setup compiler..." -ForegroundColor Cyan

$isccArgs = @(
    $iss,
    "/DMyAppVersion=$version",
    "/DOutputDir=$outDir",
    ("/DSourceDir={0}" -f (Join-Path $PSScriptRoot 'dist\ThermalBench')),
    "/DInstallerPrefix=ThermalBench-Setup-v",
    "/DMyAppName=ThermalBench",
    "/DAppExeName=ThermalBench.exe"
)

& $iscc @isccArgs

$expectedInstaller = Join-Path $outDir ("ThermalBench-Setup-v{0}.exe" -f $version)
if (-not (Test-Path $expectedInstaller)) {
    throw "Installer build succeeded but expected output not found: $expectedInstaller"
}

Write-Host "Installer created: $expectedInstaller" -ForegroundColor Green
Write-Host "Upload that file to GitHub Release tag v$version" -ForegroundColor Green
