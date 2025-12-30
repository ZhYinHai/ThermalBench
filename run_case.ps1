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

  # tools
  [string]$FurMarkExe = "C:\Program Files\Geeks3D\FurMark2_x64\furmark.exe",
  [string]$PrimeExe   = "C:\Users\Intel Testbench\Downloads\Prime_95_v30.3build6\prime95.exe",

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

$furPid = 0
$prPid  = 0
$windowStart = $null
$windowEnd   = $null
$aborted = $false
$outDir = $null

try {
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

  $runId  = Get-Date -Format "yyyyMMdd_HHmmss"
  $outDir = Join-Path $PSScriptRoot ("runs\{0}\{1}" -f $CaseName, $runId)
  New-Item -ItemType Directory -Force $outDir | Out-Null
  Write-Host ""
  Write-Host "RUN MAP: $outDir"

  $windowStart = Get-Date
  Write-Host ("WindowStart: {0}" -f $windowStart.ToString("yyyy-MM-dd HH:mm:ss.fff"))

  Write-Host "GUI_TIMER:LOG_START"
  Countdown-OrAbort -seconds $LogSec -label "Logging window (stress ON, data USED)"

  $windowEnd = Get-Date
  Write-Host ("WindowEnd:   {0}" -f $windowEnd.ToString("yyyy-MM-dd HH:mm:ss.fff"))

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

& $PythonExe $PlotScript --csv "$HwinfoCsv" --out "$outDir" --patterns $TempPatterns `
  --window-start "$ws" --window-end "$we" --export-window-csv

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

exit $pyExit
