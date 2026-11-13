# Supervises the 64-bit market loop so a crash cannot leave open positions
# unmanaged (2026-09-28: the loop process vanished ~13:29 while 10 positions
# were open).  Registered as the scheduled task `trader-loop-sup`
# (logon trigger, RunLevel Highest).
#
# Starts the loop only on weekdays 08:30-15:35 AND only when the bridge reports
# connected:true; the loop itself owns the finer focus windows and the day
# close, so it exits by itself after the close and is not restarted outside the
# window.  Never starts a second instance (tools/loop_pids.ps1 logic inline).
#
# Log: loop_sup.log (state changes only).

$repo = if ($env:TRADER_REPO) { $env:TRADER_REPO } else { Split-Path -Parent $PSScriptRoot }
$py = if ($env:TRADER_PY64) { $env:TRADER_PY64 } else { Join-Path $env:USERPROFILE 'Python312-64\python.exe' }
$feed = if ($env:TRADER_FEED) { $env:TRADER_FEED } else { 'http://127.0.0.1:8090/screener_latest.json' }
$log = Join-Path $repo 'loop_sup.log'
$windowStart = [TimeSpan]'08:30:00'
$windowEnd = [TimeSpan]'15:35:00'

function Log([string]$m) {
  Add-Content -Path $log -Value ("[{0}] {1}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $m)
}
function LoopPids {
  return @(Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'python.exe' -and $_.CommandLine -match 'run_market_loop' } | Select-Object -ExpandProperty ProcessId)
}
function BridgeConnected {
  try {
    $j = (Invoke-WebRequest -UseBasicParsing -TimeoutSec 10 -Uri 'http://127.0.0.1:8100/health').Content | ConvertFrom-Json
    return [bool]$j.connected
  } catch { return $false }
}

Log 'loop supervisor start'
$state = ''
while ($true) {
  $now = Get-Date
  $weekday = ($now.DayOfWeek -ne [System.DayOfWeek]::Saturday) -and ($now.DayOfWeek -ne [System.DayOfWeek]::Sunday)
  $inWindow = $weekday -and ($now.TimeOfDay -ge $windowStart) -and ($now.TimeOfDay -le $windowEnd)
  $pids = LoopPids

  if ($pids.Count -gt 0) {
    if ($state -ne 'running') { Log ("loop running (pid {0})" -f ($pids -join ',')); $state = 'running' }
    Start-Sleep -Seconds 30
    continue
  }
  if (-not $inWindow) {
    if ($state -ne 'outside') { Log 'outside 08:30-15:35 on a weekday - loop not started'; $state = 'outside' }
    Start-Sleep -Seconds 60
    continue
  }
  if (-not (BridgeConnected)) {
    if ($state -ne 'no-bridge') { Log 'bridge not connected - loop not started (waiting for HTS login + bridge)'; $state = 'no-bridge' }
    Start-Sleep -Seconds 60
    continue
  }
  $p = Start-Process -FilePath $py -ArgumentList '-u', 'run_market_loop.py', 'run', '--loop', '--live', '--yes', '--feed', $feed -WorkingDirectory $repo -WindowStyle Hidden -PassThru
  Log ("started loop pid {0}" -f $p.Id)
  $state = 'started'
  Start-Sleep -Seconds 60
}
