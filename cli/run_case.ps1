# run_case.ps1
param(
  [string]$CaseName  = "caseA",
  [int]$WarmupSec    = 1200,
  [int]$LogSec       = 900,

  # IMPORTANT: make these switches (present = ON, absent = OFF)
  [switch]$StressCPU,
  [switch]$StressGPU,

  # HWiNFO continuous log (must already be running)
  [string]$HwinfoCsv = "",

  # Results root
  [string]$RunsRoot = "",
  # Ambient sensor logging (TEMPer USB dongle)
  [switch]$EnableAmbient = $true,
  [int]$AmbientIntervalMs = 1000,

  # tools
  [string]$FurMarkExe = "",
  [string]$PrimeExe   = "",


  # FurMark settings
  [string]$FurDemo = "furmark-knot-gl",
  [int]$FurWidth  = 3840,
  [int]$FurHeight = 1600,

  # python plotting
  [string]$PythonExe   = (Join-Path $PSScriptRoot ".venv\Scripts\python.exe"),
  [string]$PlotScript  = (Join-Path $PSScriptRoot "plot_hwinfo.py"),
  [string[]]$TempPatterns = @("CPU Package", "GPU Temperature", "GPU VRM", "SPD Hub"),

  # Abort flag file (works for GUI/no-console runs)
  [string]$AbortFlag = (Join-Path $env:TEMP "temptesting_abort.flag"),

  # after run: try to clear master log (may fail if file locked - ok)
  [switch]$ClearHwinfoAfter = $false,

  # after run: try to clear ambient log (temp file) (best-effort)
  [switch]$ClearAmbientAfter = $true,

  # STOP command
  [switch]$StopNow
)

function Assert-File($p, $label) {
  if (-not (Test-Path $p)) { throw "$label does not exist: $p" }
}

function Resolve-BundledToolPath {
  param(
    [string]$Current,
    [string]$AppRoot,
    [string[]]$RelativeCandidates,
    [string]$Label
  )

  if ($Current -and (Test-Path -LiteralPath $Current)) {
    try { return (Resolve-Path -LiteralPath $Current).Path } catch { return $Current }
  }

  foreach ($rel in $RelativeCandidates) {
    try {
      $candidate = Join-Path $AppRoot $rel
      if (Test-Path -LiteralPath $candidate) {
        return (Resolve-Path -LiteralPath $candidate).Path
      }
    } catch {}
  }

  return $Current
}

function Stop-StressToolsByName {
  Stop-Process -Name "furmark","prime95" -Force -ErrorAction SilentlyContinue
}

function Write-FurMarkLogTail {
  param([string]$FurDir = "")

  try {
    if ([string]::IsNullOrWhiteSpace($FurDir)) { return }
    $furLog = Join-Path $FurDir "_furmark_log.txt"
    if (-not (Test-Path -LiteralPath $furLog)) { return }
    $logLines = Get-Content -LiteralPath $furLog -Tail 30 -ErrorAction SilentlyContinue
    if (-not $logLines) { return }
    Write-Host "--- FurMark log (last 30 lines) ---"
    $logLines | ForEach-Object { Write-Host $_ }
    Write-Host "---"
  } catch {}
}

function Resolve-DefaultRunsRoot {
  $docs = [Environment]::GetFolderPath([Environment+SpecialFolder]::MyDocuments)

  if ([string]::IsNullOrWhiteSpace($docs)) {
    $base = Join-Path $env:LOCALAPPDATA "ThermalBench"
  } else {
    $base = Join-Path $docs "ThermalBench"
  }

  return (Join-Path $base "runs")
}

function Resolve-DefaultHwinfoCsv {
  $docs = [Environment]::GetFolderPath([Environment+SpecialFolder]::MyDocuments)

  if ([string]::IsNullOrWhiteSpace($docs)) {
    $base = Join-Path $env:LOCALAPPDATA "ThermalBench"
  } else {
    $base = Join-Path $docs "ThermalBench"
  }

  return (Join-Path $base "HWiNFO\hwinfo.csv")
}

function Set-AbortFlag {
  try { Set-Content -Path $AbortFlag -Value "ABORT" -Force } catch {}
}

function Clear-AbortFlag {
  try { Remove-Item -Force $AbortFlag -ErrorAction SilentlyContinue } catch {}
}

function Is-AbortFlagSet {
  return (Test-Path $AbortFlag)
}

function Has-InteractiveConsole {
  try {
    $null = [Console]::KeyAvailable
    return $true
  } catch {
    return $false
  }
}

function Resolve-PythonRuntime {
  # Returns: @{ Exe='python'|'py'|'...path...'; UsePyLauncher=$true/$false }
  $usePy = $false
  $exe = $PythonExe

  if (-not (Test-Path $exe)) {
    # Prefer repo-root venv first (common layout): <repo>\.venv\Scripts\python.exe
    $repoRootGuess = Split-Path -Parent $PSScriptRoot
    $venvRepo = Join-Path $repoRootGuess ".venv\Scripts\python.exe"
    $venvCli  = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"

    if (Test-Path $venvRepo) {
      $exe = $venvRepo
    } elseif (Test-Path $venvCli) {
      $exe = $venvCli
    } elseif (Get-Command python -ErrorAction SilentlyContinue) {
      $exe = 'python'
    } elseif (Get-Command py -ErrorAction SilentlyContinue) {
      $exe = 'py'
      $usePy = $true
    } else {
      return @{ Exe=$null; UsePyLauncher=$false }
    }
  }

  return @{ Exe=$exe; UsePyLauncher=$usePy }
}

function Countdown-OrAbort($seconds, $label, [string[]]$MonitorNames = @()) {
  $interactive = Has-InteractiveConsole

  if ($interactive) {
    Write-Host ("{0}: {1} sec... (press 'Q' to stop)" -f $label, $seconds)
  } else {
    Write-Host ("{0}: {1} sec..." -f $label, $seconds)
  }

  for ($i = $seconds; $i -gt 0; $i--) {

    if (Is-AbortFlagSet) { throw "ABORT" }

    # Verify that every monitored stress tool is still running (by name).
    # FurMark 2 is a GeeXLab launcher that exits after spawning the real
    # render child process, so we must check by name rather than the original
    # PID (which belongs to the short-lived launcher, not the render worker).
    foreach ($name in $MonitorNames) {
      if (-not (Get-Process -Name $name -ErrorAction SilentlyContinue)) {
        if ($name -eq "furmark") { Write-FurMarkLogTail $script:FurMarkDirForLogs }
        throw "Stress tool '$name' stopped unexpectedly during the test."
      }
    }

    if ($interactive) {
      if ([Console]::KeyAvailable) {
        $k = [Console]::ReadKey($true)
        if ($k.Key -eq [ConsoleKey]::Q) { throw "ABORT" }
      }
    }

    if ($i % 60 -eq 0 -and $i -ne $seconds) {
      Write-Host ("  {0} min remaining..." -f [int]($i/60))
    }
    Start-Sleep -Seconds 1
  }
}

function Get-RunName([string]$CaseName, [int]$WarmupSec, [int]$LogSec, [switch]$StressCPU, [switch]$StressGPU, [string]$RunsRoot) {
  # Stress prefix
  $stressName = ""
  if ($StressCPU.IsPresent -and $StressGPU.IsPresent) { $stressName = "CPUGPU" }
  elseif ($StressCPU.IsPresent) { $stressName = "CPU" }
  elseif ($StressGPU.IsPresent) { $stressName = "GPU" }
  else { $stressName = "CPU" }

  # Convert seconds -> minutes for naming (UI uses minute-based inputs)
  $wMin = [int][math]::Round(($WarmupSec / 60.0), 0)
  $lMin = [int][math]::Round(($LogSec / 60.0), 0)
  if ($wMin -lt 0) { $wMin = 0 }
  if ($lMin -lt 0) { $lMin = 0 }

  $base = ("{0}_W{1}_L{2}" -f $stressName, $wMin, $lMin)

  # Auto-increment version if same base already exists for this case.
  if ([string]::IsNullOrWhiteSpace($RunsRoot)) {
    $RunsRoot = Resolve-DefaultRunsRoot
  }

  $caseDir = Join-Path $RunsRoot $CaseName
  New-Item -ItemType Directory -Force $caseDir | Out-Null

  $re = ("^{0}_V(\\d+)$" -f [regex]::Escape($base))
  $maxV = 0
  try {
    Get-ChildItem -LiteralPath $caseDir -Directory -ErrorAction SilentlyContinue | ForEach-Object {
      $n = $_.Name
      if ($n -match $re) {
        $v = 0
        try { $v = [int]$matches[1] } catch { $v = 0 }
        if ($v -gt $maxV) { $maxV = $v }
      }
    }
  } catch {}

  $nextV = $maxV + 1
  return ("{0}_V{1}" -f $base, $nextV)
}

# Load Win32 ShowWindow so stress tools can be restored to normal size
# WITHOUT stealing focus from ThermalBench.
Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;
public class TBWinHelper {
    [DllImport("user32.dll")]
    public static extern bool ShowWindow(IntPtr hWnd, int nCmdShow);
}
"@ -ErrorAction SilentlyContinue

function Show-ProcessNoActivate {
  param([string]$Name = "", [int]$Id = 0)
  # SW_SHOWNOACTIVATE (4): restore to normal size/position without stealing focus.
  $SW_SHOWNOACTIVATE = 4
  try {
    $p = $null
    if ($Id -gt 0) {
      $p = Get-Process -Id $Id -ErrorAction SilentlyContinue
    }
    if (-not $p -and $Name) {
      $p = Get-Process -Name $Name -ErrorAction SilentlyContinue | Select-Object -First 1
    }
    if ($p -and $p.MainWindowHandle -ne [IntPtr]::Zero) {
      [TBWinHelper]::ShowWindow($p.MainWindowHandle, $SW_SHOWNOACTIVATE) | Out-Null
    }
  } catch {}
}

function Start-StressTools {
  # return pids even if one tool is not started
  $furPid = 0
  $prPid  = 0

  if ($StressGPU.IsPresent) {
    Assert-File $FurMarkExe "FurMarkExe"
    $furDir = Split-Path -Parent $FurMarkExe
    $script:FurMarkDirForLogs = $furDir
    $furArgs = @("--demo",$FurDemo,"--width",$FurWidth,"--height",$FurHeight,"--vsync","0","--hpgfx","1")
    if ($FurDemo -match '-vk$') {
      # Vulkan swapchain creation can fail when Windows window decorations make
      # the actual client/window size larger than the requested resolution.
      # Borderless Vulkan demos avoid that failure while keeping OpenGL unchanged.
      $furArgs += @("--title-bar","0")
    }
    Write-Host "Start FurMark2: $FurMarkExe $($furArgs -join ' ')"
    # Start minimised so FurMark does not steal focus from ThermalBench;
    # Show-ProcessNoActivate restores it to normal size without activating it.
    $fur = Start-Process -FilePath $FurMarkExe -ArgumentList $furArgs -WorkingDirectory $furDir -PassThru -WindowStyle Minimized

    # FurMark 2 (GeeXLab) exits the launcher process after spawning the real
    # render child, so we cannot rely on $fur.Id surviving.  Wait up to 5 s for
    # any "furmark" process (launcher or child) to appear/remain running.
    $furName = [System.IO.Path]::GetFileNameWithoutExtension($FurMarkExe)
    $furRunning = $false
    for ($i = 0; $i -lt 10; $i++) {
      Start-Sleep -Milliseconds 500
      if (Get-Process -Name $furName -ErrorAction SilentlyContinue) {
        $furRunning = $true
        break
      }
    }
    if (-not $furRunning) {
      # Surface the GeeXLab / FurMark log to help diagnose the crash.
      Write-FurMarkLogTail $furDir
      throw "FurMark2 exited immediately."
    }
    $furPid = [int]$fur.Id
    # Restore FurMark to normal size without activating (no focus steal).
    Start-Sleep -Milliseconds 500
    Show-ProcessNoActivate -Name $furName
  } else {
    Write-Host "GPU stress disabled."
  }

  if ($StressCPU.IsPresent) {
    Assert-File $PrimeExe "PrimeExe"
    $primeDir = Split-Path -Parent $PrimeExe
    Write-Host "Start Prime95: $PrimeExe -t"
    # Start minimised so Prime95 does not steal focus from ThermalBench;
    # Show-ProcessNoActivate restores it to normal size without activating it.
    $pr = Start-Process -FilePath $PrimeExe -ArgumentList "-t" -WorkingDirectory $primeDir -PassThru -WindowStyle Minimized

    Start-Sleep -Seconds 2
    if (-not (Get-Process -Id $pr.Id -ErrorAction SilentlyContinue)) {
      throw "Prime95 exited immediately (possible first-run prompt)."
    }
    $prPid = [int]$pr.Id
    # Restore Prime95 to normal size without activating (no focus steal).
    Show-ProcessNoActivate -Id $prPid
  } else {
    Write-Host "CPU stress disabled."
  }

  if (-not $StressCPU.IsPresent -and -not $StressGPU.IsPresent) {
    throw "Both CPU and GPU stress were disabled (should never happen from GUI)."
  }

  return @{ FurPid=$furPid; PrimePid=$prPid }
}

function Stop-StressTools([int]$FurPid, [int]$PrimePid) {
  Write-Host ""
  Write-Host "Stop stress tools..."

  foreach ($procId in @($FurPid, $PrimePid)) {
    if ($procId -and (Get-Process -Id $procId -ErrorAction SilentlyContinue)) {
      try { Stop-Process -Id $procId -ErrorAction SilentlyContinue } catch {}
    }
  }

  Start-Sleep -Seconds 2

  foreach ($procId in @($FurPid, $PrimePid)) {
    if ($procId -and (Get-Process -Id $procId -ErrorAction SilentlyContinue)) {
      try { Stop-Process -Id $procId -Force -ErrorAction SilentlyContinue } catch {}
    }
  }

  Stop-StressToolsByName
}

# ---- STOPNOW ----
if ($StopNow) {
  Write-Host "StopNow: killing FurMark + Prime95 and signaling abort..."
  Set-AbortFlag
  Stop-StressToolsByName
  exit 0
}

Write-Host ""
Clear-AbortFlag

$scriptDir = $PSScriptRoot

# In a frozen app, this script typically lives at <AppRoot>\_internal\cli\run_case.ps1.
# In dev, it lives at <RepoRoot>\cli\run_case.ps1.
# Resolve a stable "root" that points at the app root (frozen) or repo root (dev).
$repoRoot = Split-Path -Parent $scriptDir
try {
  $parentLeaf = Split-Path -Leaf $repoRoot
  if ($parentLeaf -eq '_internal') {
    $repoRoot = Split-Path -Parent $repoRoot
  }
} catch {}

if ([string]::IsNullOrWhiteSpace($RunsRoot)) {
  $RunsRoot = Resolve-DefaultRunsRoot
}

try {
  New-Item -ItemType Directory -Force $RunsRoot | Out-Null
  $RunsRoot = (Resolve-Path -LiteralPath $RunsRoot).Path
} catch {}

Write-Host "Runs root: $RunsRoot"

# Results should live in Documents\ThermalBench\runs, not inside Program Files.
if ([string]::IsNullOrWhiteSpace($RunsRoot)) {
  $RunsRoot = Resolve-DefaultRunsRoot
}

try {
  New-Item -ItemType Directory -Force $RunsRoot | Out-Null
  $RunsRoot = (Resolve-Path -LiteralPath $RunsRoot).Path
} catch {}

# Fixed bundled HWiNFO CSV.
# ThermalBench always expects HWiNFO to log here:
#   <AppRoot>\tools\HWiNFO\hwinfo.csv
if ([string]::IsNullOrWhiteSpace($HwinfoCsv)) {
  $HwinfoCsv = Resolve-DefaultHwinfoCsv
}

try {
  if (Test-Path -LiteralPath $HwinfoCsv) {
    $HwinfoCsv = (Resolve-Path -LiteralPath $HwinfoCsv).Path
  }
} catch {}

Write-Host ""
Write-Host "HWiNFO continuous CSV: $HwinfoCsv"
if (-not (Test-Path -LiteralPath $HwinfoCsv)) {
  Write-Host "WARNING: $HwinfoCsv does not exist yet." -ForegroundColor Yellow
  Write-Host "Open HWiNFO and enable sensor logging to this exact path:" -ForegroundColor Yellow
  Write-Host "  $HwinfoCsv" -ForegroundColor Yellow
  exit 1
}

# Prefer installed/bundled tool paths when explicit paths are missing or invalid.
$FurMarkExe = Resolve-BundledToolPath `
  -Current $FurMarkExe `
  -AppRoot $repoRoot `
  -RelativeCandidates @(
    "tools\FurMark\furmark.exe",
    "tools\FurMark\FurMark_win64\furmark.exe"
  ) `
  -Label "FurMark"

$PrimeExe = Resolve-BundledToolPath `
  -Current $PrimeExe `
  -AppRoot $repoRoot `
  -RelativeCandidates @(
    "tools\Prime95\prime95.exe",
    "tools\Prime95\prime95\prime95.exe"
  ) `
  -Label "Prime95"

# Resolve Python runtime early (needed for ambient logger as well as plotting).
$py = Resolve-PythonRuntime
$PythonExe = $py.Exe
$UsePyLauncher = [bool]$py.UsePyLauncher

$ambientPid = 0
$ambientCsv = $null

$furPid = 0
$prPid  = 0
$windowStart = $null
$windowEnd   = $null
$aborted = $false
$outDir = $null

try {
  # Start ambient logging (best-effort). We always slice/merge by windowStart/windowEnd later.
  # NOTE: The GUI does not pass -EnableAmbient explicitly. Using `.IsPresent` here
  # makes ambient logging silently OFF even though the parameter default is `$true`.
  # Treat this as a boolean flag instead.
  if ($EnableAmbient) {
    try {
      $rand = Get-Random
      $ambientCsv = Join-Path $env:TEMP ("ThermalBench_ambient_{0}_{1}.csv" -f $PID, $rand)
      $intervalSec = [math]::Max(0.1, ([double]$AmbientIntervalMs / 1000.0))
      $intervalSecStr = $intervalSec.ToString([System.Globalization.CultureInfo]::InvariantCulture)

      # Let the GUI know where to read ambient data for live stats/plotting.
      try { Write-Host ("GUI_AMBIENT_CSV:{0}" -f $ambientCsv) } catch {}

      # Prefer a bundled ambient logger EXE when present (release builds).
      $ambientExe = Join-Path $repoRoot "ThermalBench-AmbientLogger.exe"
      if (Test-Path $ambientExe) {
        $args = @('--out', $ambientCsv, '--interval', $intervalSecStr)
        Write-Host "Ambient logger (bundled): $ambientExe $($args -join ' ')"
        $p = Start-Process -FilePath $ambientExe -ArgumentList $args -PassThru -WindowStyle Hidden
        if ($p -and $p.Id) { $ambientPid = [int]$p.Id }
      } else {
        # Fallback: use Python + ambient_logger.py (dev/workspace runs)
        if (-not $PythonExe) {
          Write-Host "Ambient logger skipped (Python not found and bundled ambient logger missing)." -ForegroundColor Yellow
        } else {
          $ambientScript = Join-Path $repoRoot "ambient_logger.py"
          if (Test-Path $ambientScript) {
            if ($UsePyLauncher) {
              $args = @('-3', $ambientScript, '--out', $ambientCsv, '--interval', $intervalSecStr)
            } else {
              $args = @($ambientScript, '--out', $ambientCsv, '--interval', $intervalSecStr)
            }
            Write-Host "Ambient logger (python): $PythonExe $($args -join ' ')"
            $p = Start-Process -FilePath $PythonExe -ArgumentList $args -PassThru -WindowStyle Hidden
            if ($p -and $p.Id) { $ambientPid = [int]$p.Id }
          } else {
            Write-Host "Ambient logger script not found: $ambientScript" -ForegroundColor Yellow
          }
        }
      }
    } catch {
      Write-Host "Ambient logger could not be started (continuing)." -ForegroundColor Yellow
      $ambientPid = 0
      $ambientCsv = $null
    }
  } else {
    Write-Host "Ambient logging disabled." 
  }

  $stress = Start-StressTools
  $furPid = [int]$stress.FurPid
  $prPid  = [int]$stress.PrimePid

  Write-Host ""
  Write-Host "RUNNING:"
  if ($furPid -ne 0) { Write-Host "  FurMark PID: $furPid" }
  if ($prPid  -ne 0) { Write-Host "  Prime95  PID: $prPid" }
  Write-Host ""

  Write-Host "GUI_TIMER:WARMUP_START"
  $monNames = @()
  if ($furPid -ne 0) { $monNames += "furmark" }
  if ($prPid  -ne 0) { $monNames += "prime95" }
  Countdown-OrAbort -seconds $WarmupSec -label "Warm-up (stress ON, logging IGNORE)" -MonitorNames $monNames

  $runId  = Get-RunName -CaseName $CaseName -WarmupSec $WarmupSec -LogSec $LogSec -StressCPU:$StressCPU -StressGPU:$StressGPU -RunsRoot $RunsRoot
  # Place run outputs in the user-writable ThermalBench runs folder.

  # Safety net: never reuse an existing output directory (prevents overwriting prior runs)
  $m = [regex]::Match($runId, '^(.*)_V(\d+)$')
  $base = $runId
  $v = 1
  if ($m.Success) {
    $base = $m.Groups[1].Value
    try { $v = [int]$m.Groups[2].Value } catch { $v = 1 }
  }

  $outDir = Join-Path $RunsRoot ("{0}\{1}" -f $CaseName, $runId)
  while (Test-Path -LiteralPath $outDir) {
    $v = $v + 1
    $runId = ("{0}_V{1}" -f $base, $v)
    $outDir = Join-Path $RunsRoot ("{0}\{1}" -f $CaseName, $runId)
  }

  New-Item -ItemType Directory -Force $outDir | Out-Null
  Write-Host ""
  Write-Host "RUN MAP: $outDir"

  $windowStart = Get-Date
  Write-Host ("WindowStart: {0}" -f $windowStart.ToString("yyyy-MM-dd HH:mm:ss.fff"))

  Write-Host "GUI_TIMER:LOG_START"
  Countdown-OrAbort -seconds $LogSec -label "Logging window (stress ON, data USED)" -MonitorNames $monNames

  $windowEnd = Get-Date
  Write-Host ("WindowEnd:   {0}" -f $windowEnd.ToString("yyyy-MM-dd HH:mm:ss.fff"))

  Write-Host "GUI_TIMER:LOG_END"

} catch {
  if ($_.Exception.Message -eq "ABORT") {
    $aborted = $true
    Write-Host ""
    Write-Host "ABORT requested." -ForegroundColor Yellow
  } else {
    Write-Host ""
    Write-Host ("ERROR: {0}" -f $_.Exception.Message) -ForegroundColor Red
    throw
  }
} finally {
  if ($furPid -ne 0 -or $prPid -ne 0) {
    Stop-StressTools -FurPid $furPid -PrimePid $prPid
  }

  # Stop ambient logger
  if ($ambientPid -and (Get-Process -Id $ambientPid -ErrorAction SilentlyContinue)) {
    try { Stop-Process -Id $ambientPid -ErrorAction SilentlyContinue } catch {}
    Start-Sleep -Milliseconds 400
    if (Get-Process -Id $ambientPid -ErrorAction SilentlyContinue) {
      try { Stop-Process -Id $ambientPid -Force -ErrorAction SilentlyContinue } catch {}
    }
  }

  Clear-AbortFlag

  if ($aborted -and $outDir -and (Test-Path $outDir)) {
    try {
      Remove-Item -Recurse -Force $outDir
      Write-Host "Run folder removed due to abort: $outDir"
    } catch {
      Write-Host "Could not remove run folder (files may be open): $outDir" -ForegroundColor Yellow
    }
  }
}

if ($aborted -or -not $windowStart -or -not $windowEnd -or -not $outDir) {
  Write-Host "No plotting executed." -ForegroundColor Yellow
  exit 0
}

Start-Sleep -Seconds 6

$ws = $windowStart.ToString("yyyy-MM-dd HH:mm:ss.fff")
$we = $windowEnd.ToString("yyyy-MM-dd HH:mm:ss.fff")

# Prefer bundled plotter EXE in installed/release builds.
$plotExe = Join-Path $repoRoot "ThermalBench-PlotHwinfo.exe"

$plotArgs = @(
  '--csv', $HwinfoCsv,
  '--out', $outDir,
  '--patterns'
)

if ($TempPatterns) {
  $plotArgs += $TempPatterns
}

$plotArgs += @(
  '--window-start', $ws,
  '--window-end', $we,
  '--export-window-csv'
)

if ($ambientCsv -and (Test-Path $ambientCsv)) {
  $plotArgs += @('--ambient-csv', $ambientCsv)
}

if (Test-Path $plotExe) {
  Write-Host "Plotter (bundled): $plotExe"
  & $plotExe @plotArgs
} else {
  # Dev fallback: use Python + plot_hwinfo.py
  if (-not $PythonExe) {
    Write-Host "Python executable not found. Create a virtualenv or ensure 'python' or 'py' is on PATH."
    exit 1
  }

  Write-Host "Plotter (python): $PythonExe $PlotScript"

  $pythonPlotArgs = @($PlotScript) + $plotArgs

  if ($UsePyLauncher) {
    & $PythonExe -3 @pythonPlotArgs
  } else {
    & $PythonExe @pythonPlotArgs
  }
}

$pyExit = $LASTEXITCODE

Write-Host ""
if ($pyExit -ne 0) {
  Write-Host "Plotting FAILED (exit code $pyExit). See window_check.txt for details." -ForegroundColor Red
} else {
  Write-Host "DONE. In $outDir you should now have outputs."
}

if ($ClearHwinfoAfter) {
  try {
    Clear-Content -Path $HwinfoCsv -ErrorAction Stop
    Write-Host "HWiNFO master log cleared: $HwinfoCsv"
  } catch {
    Write-Host "Could not clear HWiNFO master log (likely locked). That's fine; run_window.csv is saved." -ForegroundColor Yellow
  }
}

if ($ClearAmbientAfter -and $ambientCsv -and (Test-Path $ambientCsv)) {
  $removed = $false
  for ($i = 0; $i -lt 5 -and -not $removed; $i++) {
    try {
      Remove-Item -Force -ErrorAction Stop $ambientCsv
      Write-Host "Ambient temp log removed: $ambientCsv"
      $removed = $true
    } catch {
      Start-Sleep -Milliseconds 300
    }
  }
  if (-not $removed) {
    Write-Host "Could not remove ambient temp log (likely open): $ambientCsv" -ForegroundColor Yellow
  }
}

exit $pyExit
