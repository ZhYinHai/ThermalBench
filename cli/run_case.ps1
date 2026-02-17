# run_case.ps1
param(
  [string]$CaseName  = "caseA",
  [int]$WarmupSec    = 1200,
  [int]$LogSec       = 900,

  # IMPORTANT: make these switches (present = ON, absent = OFF)
  [switch]$StressCPU,
  [switch]$StressGPU,

  # HWiNFO continuous log (must already be running)
  [string]$HwinfoCsv = "C:\TempTesting\hwinfo.csv",

  # Ambient sensor logging (TEMPer USB dongle)
  [switch]$EnableAmbient = $true,
  [int]$AmbientIntervalMs = 1000,

  # tools
  # [string]$FurMarkExe = "C:\Program Files\Geeks3D\FurMark2_x64\furmark.exe",
  # [string]$PrimeExe   = "C:\Users\Intel Testbench\Downloads\Prime_95_v30.3build6\prime95.exe",
  [string]$FurMarkExe = "C:\Users\Dennis\Downloads\FurMark_2.10.2_win64\FurMark_win64\furmark.exe",
  [string]$PrimeExe   = "C:\Users\Dennis\Downloads\p95v3019b20.win64\prime95.exe",


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
  [switch]$ClearHwinfoAfter = $true,

  # after run: try to clear ambient log (temp file) (best-effort)
  [switch]$ClearAmbientAfter = $true,

  # STOP command
  [switch]$StopNow
)

function Assert-File($p, $label) {
  if (-not (Test-Path $p)) { throw "$label does not exist: $p" }
}

function Stop-StressToolsByName {
  Stop-Process -Name "furmark","prime95" -Force -ErrorAction SilentlyContinue
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

function Countdown-OrAbort($seconds, $label) {
  $interactive = Has-InteractiveConsole

  if ($interactive) {
    Write-Host ("{0}: {1} sec... (press 'Q' to stop)" -f $label, $seconds)
  } else {
    Write-Host ("{0}: {1} sec..." -f $label, $seconds)
  }

  for ($i = $seconds; $i -gt 0; $i--) {

    if (Is-AbortFlagSet) { throw "ABORT" }

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

function Get-RunName([string]$CaseName, [int]$WarmupSec, [int]$LogSec, [switch]$StressCPU, [switch]$StressGPU) {
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
  $repoRoot = Split-Path -Parent $PSScriptRoot
  $caseDir = Join-Path $repoRoot ("runs\{0}" -f $CaseName)
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

function Start-StressTools {
  # return pids even if one tool is not started
  $furPid = 0
  $prPid  = 0

  if ($StressGPU.IsPresent) {
    Assert-File $FurMarkExe "FurMarkExe"
    $furDir = Split-Path -Parent $FurMarkExe
    $furArgs = @("--demo",$FurDemo,"--width",$FurWidth,"--height",$FurHeight,"--vsync","0")
    Write-Host "Start FurMark2: $FurMarkExe $($furArgs -join ' ')"
    $fur = Start-Process -FilePath $FurMarkExe -ArgumentList $furArgs -WorkingDirectory $furDir -PassThru -WindowStyle Normal

    Start-Sleep -Seconds 2
    if (-not (Get-Process -Id $fur.Id -ErrorAction SilentlyContinue)) {
      throw "FurMark2 exited immediately."
    }
    $furPid = [int]$fur.Id
  } else {
    Write-Host "GPU stress disabled."
  }

  if ($StressCPU.IsPresent) {
    Assert-File $PrimeExe "PrimeExe"
    $primeDir = Split-Path -Parent $PrimeExe
    Write-Host "Start Prime95: $PrimeExe -t"
    $pr = Start-Process -FilePath $PrimeExe -ArgumentList "-t" -WorkingDirectory $primeDir -PassThru -WindowStyle Normal

    Start-Sleep -Seconds 2
    if (-not (Get-Process -Id $pr.Id -ErrorAction SilentlyContinue)) {
      throw "Prime95 exited immediately (possible first-run prompt)."
    }
    $prPid = [int]$pr.Id
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
Write-Host "HWiNFO continuous CSV: $HwinfoCsv"
if (-not (Test-Path $HwinfoCsv)) {
  Write-Host "WARNING: $HwinfoCsv does not exist yet." -ForegroundColor Yellow
  Write-Host "Enable HWiNFO logging to this path and run again." -ForegroundColor Yellow
  exit 1
}

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

      # Let the GUI know where to read ambient data for live stats/plotting.
      try { Write-Host ("GUI_AMBIENT_CSV:{0}" -f $ambientCsv) } catch {}

      # Prefer a bundled ambient logger EXE when present (release builds).
      $ambientExe = Join-Path $repoRoot "ThermalBench-AmbientLogger.exe"
      if (Test-Path $ambientExe) {
        $args = @('--out', $ambientCsv, '--interval', ("{0}" -f $intervalSec))
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
              $args = @('-3', $ambientScript, '--out', $ambientCsv, '--interval', ("{0}" -f $intervalSec))
            } else {
              $args = @($ambientScript, '--out', $ambientCsv, '--interval', ("{0}" -f $intervalSec))
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
  Countdown-OrAbort -seconds $WarmupSec -label "Warm-up (stress ON, logging IGNORE)"

  $runId  = Get-RunName -CaseName $CaseName -WarmupSec $WarmupSec -LogSec $LogSec -StressCPU:$StressCPU -StressGPU:$StressGPU
  # Place run outputs at repository-level `runs/` (one level above this script's folder)

  # Safety net: never reuse an existing output directory (prevents overwriting prior runs)
  $m = [regex]::Match($runId, '^(.*)_V(\d+)$')
  $base = $runId
  $v = 1
  if ($m.Success) {
    $base = $m.Groups[1].Value
    try { $v = [int]$m.Groups[2].Value } catch { $v = 1 }
  }

  $outDir = Join-Path $repoRoot ("runs\{0}\{1}" -f $CaseName, $runId)
  while (Test-Path -LiteralPath $outDir) {
    $v = $v + 1
    $runId = ("{0}_V{1}" -f $base, $v)
    $outDir = Join-Path $repoRoot ("runs\{0}\{1}" -f $CaseName, $runId)
  }

  New-Item -ItemType Directory -Force $outDir | Out-Null
  Write-Host ""
  Write-Host "RUN MAP: $outDir"

  $windowStart = Get-Date
  Write-Host ("WindowStart: {0}" -f $windowStart.ToString("yyyy-MM-dd HH:mm:ss.fff"))

  Write-Host "GUI_TIMER:LOG_START"
  Countdown-OrAbort -seconds $LogSec -label "Logging window (stress ON, data USED)"

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

if (-not $PythonExe) {
  Write-Host "Python executable not found. Create a virtualenv in $PSScriptRoot (python -m venv .venv) or ensure 'python' or 'py' is on PATH."
  exit 1
}

# Invoke the plotter
$plotArgs = @(
  $PlotScript,
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

if ($UsePyLauncher) {
  & $PythonExe -3 @plotArgs
} else {
  & $PythonExe @plotArgs
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
  try {
    Remove-Item -Force -ErrorAction Stop $ambientCsv
    Write-Host "Ambient temp log removed: $ambientCsv"
  } catch {
    Write-Host "Could not remove ambient temp log (likely open): $ambientCsv" -ForegroundColor Yellow
  }
}

exit $pyExit
