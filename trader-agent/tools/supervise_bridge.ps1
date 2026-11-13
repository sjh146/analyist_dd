# Supervises the 32-bit Creon bridge (run_bridge.py) so a crash never leaves
# the trading stack blind.  Registered as the scheduled task `trader-bridge-sup`
# (logon trigger, RunLevel Highest - the bridge needs admin to attach to the
# elevated Creon PLUS session, so it must not be started medium-integrity).
#
# Truth rules (learned the hard way, 2026-09-28/29)
#  - `CreonHostUp` = any of coMain / CpStart / DibServer / coStarter is running.
#    coMain (the HTS window) is the *window*, but a healthy CybosPlus session can
#    outlive it in this install, so its absence is logged as a warning.
#  - `/health connected:true` alone is NOT proof of a live session: corroborate with a
#    real broker query (`/balance` ok:true).
#  - A half-dead session cannot be revived by restarting the bridge: restart once, then
#    log `RE-LOGIN REQUIRED` and back off to 5-minute polls.
#  - **A disconnected (not just half-dead) session churned the bridge.** Measured 2026-09-30:
#    the stale-restart path had no bound and reset its own counter on every restart, so while
#    Creon's leftover components were up but no session existed it restarted the bridge every
#    ~97 s -- 37 restarts/hour, 322 processes in a day (trader backlog T6). Same rule as the
#    half-dead path now: at most 2 restarts, then `RE-LOGIN REQUIRED` once and 5-minute polls.
#    The budget resets only when the bridge answers a broker query OR when Creon itself is
#    restarted (a re-login changes the host process set), never on a restart we just performed.
#  - **A live bridge BLOCKS the Creon updater.** Measured 2026-09-29: the bridge (32-bit
#    python attached to CybosPlus) mapped C:\daishin\ncfsys\data\cct*.dat, so the starter's
#    NCFSYS patch could not replace them -> `다운로드 오류 (NCFSYS, exitcode=-1)` -> the
#    login never completed. Restart Manager named pid <bridge> as the sole holder.
#    Therefore: while Creon is down, or while the updater (spdn/coStarter/MainUpdate) runs,
#    the bridge must be STOPPED -- it is restarted automatically once the HTS is up and the
#    updater has exited.
#
# Log: bridge_sup.log (one line per state change).
#
# Rehearse the state machine without touching the live bridge:
#   $env:SUPERVISE_BRIDGE_DRYRUN='1' $env:SUPERVISE_BRIDGE_FORCE_UNHEALTHY='1'
#   $env:SUPERVISE_BRIDGE_POLL_S='2'   -> logs to bridge_sup_dryrun.log, never stops/starts a process.
param([switch]$DryRun)

$repo = if ($env:TRADER_REPO) { $env:TRADER_REPO } else { Split-Path -Parent $PSScriptRoot }
$py = if ($env:TRADER_PY32) { $env:TRADER_PY32 } else { Join-Path $env:USERPROFILE 'Python311-32\python.exe' }
$port = 8100
$dry = $DryRun -or ($env:SUPERVISE_BRIDGE_DRYRUN -eq '1')
$forceUnhealthy = ($env:SUPERVISE_BRIDGE_FORCE_UNHEALTHY -eq '1')
$pollS = 30
if ($env:SUPERVISE_BRIDGE_POLL_S) { $pollS = [int]$env:SUPERVISE_BRIDGE_POLL_S }
$staleMax = 2          # restarts allowed before giving up on a disconnected bridge
# Rehearsal keeps the same state machine but compresses the real back-offs.
$attachWaitS = 60
$giveupWaitS = 240
if ($dry) { $attachWaitS = $pollS; $giveupWaitS = 2 * $pollS }
$log = Join-Path $repo ($(if ($dry) { 'bridge_sup_dryrun.log' } else { 'bridge_sup.log' }))

function Log([string]$m) {
  Add-Content -Path $log -Value ("[{0}] {1}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $m)
}
function CreonHostUp {
  return (@(Get-Process -Name coMain, CpStart, DibServer, coStarter -ErrorAction SilentlyContinue).Count -gt 0)
}
function UpdaterRunning {
  # spdn = file updater spawned by the Creon starter; MainUpdate/coStarter = the starter itself.
  return (@(Get-Process -Name spdn, coStarter, MainUpdate, ncStarter -ErrorAction SilentlyContinue).Count -gt 0)
}
function HtsWindowUp {
  return (@(Get-Process -Name coMain -ErrorAction SilentlyContinue).Count -gt 0)
}
function BridgeRunning {
  return (@(Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'python.exe' -and $_.CommandLine -match 'run_bridge' }).Count -gt 0)
}
function BridgeListening {
  return (@(Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue).Count -gt 0)
}
function BridgeConnected {
  try {
    $j = (Invoke-WebRequest -UseBasicParsing -TimeoutSec 10 -Uri ("http://127.0.0.1:{0}/health" -f $port)).Content | ConvertFrom-Json
    return [bool]$j.connected
  } catch { return $false }
}
function BridgeResponsive {
  try {
    $j = (Invoke-WebRequest -UseBasicParsing -TimeoutSec 15 -Uri ("http://127.0.0.1:{0}/balance" -f $port)).Content | ConvertFrom-Json
    return [bool]$j.ok
  } catch { return $false }
}
function CreonGeneration {
  # Identity of the *running* Creon host processes. A human re-login restarts them,
  # so a changed value means "a new session may exist, let the supervisor try again";
  # an unchanged value means we are still staring at the same dead session.
  # (dry-run only: read it from a file so the reset rule itself can be rehearsed)
  if ($dry -and $env:SUPERVISE_BRIDGE_FAKE_GEN_FILE -and (Test-Path $env:SUPERVISE_BRIDGE_FAKE_GEN_FILE)) {
    return (Get-Content -Raw $env:SUPERVISE_BRIDGE_FAKE_GEN_FILE).Trim()
  }
  $ps = @(Get-Process -Name coMain, CpStart, DibServer, coStarter -ErrorAction SilentlyContinue)
  if ($ps.Count -eq 0) { return '' }
  $parts = foreach ($p in ($ps | Sort-Object Id)) {
    $started = ''
    try { $started = $p.StartTime.ToString('yyyyMMddHHmm') } catch { $started = '?' }
    '{0}@{1}' -f $p.Id, $started
  }
  return ($parts -join '|')
}
function StopBridge {
  if ($dry) {
    Log '[dry-run] would stop the bridge (no process touched)'
    return
  }
  foreach ($p in (Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'python.exe' -and $_.CommandLine -match 'run_bridge' })) {
    Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
    Log ("stopped bridge pid {0}" -f $p.ProcessId)
  }
}
function StartBridge {
  if ($dry) {
    Log '[dry-run] would start the bridge (no process started)'
    return
  }
  $p = Start-Process -FilePath $py -ArgumentList (Join-Path $repo 'run_bridge.py'), '--port', $port -WorkingDirectory $repo -WindowStyle Hidden -PassThru
  Log ("started bridge pid {0}" -f $p.Id)
}

Log ('bridge supervisor start (v2 bounded stale restarts: {0} max, poll {1}s{2})' -f $staleMax, $pollS, $(if ($dry) { ', DRY-RUN' } else { '' }))
$unhealthy = 0
$halfdead = 0
$staleRestarts = 0
$generation = ''
$windowWarned = $false   # the missing-HTS-window note must print once, not every poll
$state = ''
while ($true) {
  $host_up = CreonHostUp
  $updating = UpdaterRunning
  $window = HtsWindowUp
  $listening = BridgeListening

  if ($dry -and $forceUnhealthy) {
    # Rehearsal: pretend Creon is up, the port is listening and the session is dead.
    $host_up = $true
    $updating = $false
    $window = $true
    $listening = $true
  }

  # A re-login restarts Creon's host processes: that is the signal to let the
  # supervisor restart the bridge again (a fresh session may need one restart).
  $generation_now = CreonGeneration
  if ($generation_now -ne $generation) {
    if ($generation -ne '' -and $staleRestarts -ge $staleMax) {
      Log ('Creon host processes changed ({0} -> {1}) - restart budget reset' -f $generation, $generation_now)
    }
    $generation = $generation_now
    $staleRestarts = 0
    if ($state -eq 'stale-giveup' -or $state -eq 'halfdead-giveup') { $state = '' }
  }
  if (-not $host_up) {
    # Creon is down: a running bridge would hold the NCFSYS data files and block the next login's update.
    if (BridgeRunning) {
      Log 'Creon not running - stopping the bridge so the Creon updater can replace NCFSYS data files'
      StopBridge
      $state = 'creon-down-bridge-stopped'
    } elseif ($state -ne 'hts-down') {
      Log 'Creon not running - waiting for the Creon login'
      $state = 'hts-down'
    }
    $unhealthy = 0
    $halfdead = 0
    $staleRestarts = 0
  } elseif ($updating) {
    # The starter/updater is patching files right now: keep the bridge down.
    if (BridgeRunning) {
      Log 'Creon updater running (spdn/coStarter) - stopping the bridge to free its file locks'
      StopBridge
    } elseif ($state -ne 'updating') {
      Log 'Creon updater running (spdn/coStarter) - bridge stays down until it finishes'
      $state = 'updating'
    }
    $unhealthy = 0
    $halfdead = 0
    $staleRestarts = 0
  } else {
    if (-not $window) {
      if (-not $windowWarned) {
        Log 'note: HTS main window (coMain) is not running - only CybosPlus components. If queries fail, the session is half-dead (re-login).'
        $windowWarned = $true
      }
    } else {
      $windowWarned = $false
    }
    if (-not $listening) {
      Log 'Creon up and not updating - starting bridge'
      StartBridge
      $state = 'starting'
      $unhealthy = 0
      $halfdead = 0
    } else {
      $connected = BridgeConnected
      $responsive = $false
      if ($connected) { $responsive = BridgeResponsive }
      if ($dry -and $forceUnhealthy) { $connected = $false; $responsive = $false }

      if ($connected -and $responsive) {
        if ($state -ne 'ok') { Log 'bridge connected and answering broker queries'; $state = 'ok' }
        $unhealthy = 0
        $halfdead = 0
        $staleRestarts = 0
      } elseif ($connected -and -not $responsive) {
        $halfdead++
        Log ("bridge HALF-DEAD: /health connected but /balance failed (attempt {0}/2)" -f $halfdead)
        if ($halfdead -le 2) {
          Log 'restarting half-dead bridge once'
          StopBridge
          Start-Sleep -Seconds 5
          StartBridge
          $state = 'halfdead-restarted'
          Start-Sleep -Seconds 60
        } else {
          if ($state -ne 'halfdead-giveup') {
            Log 'bridge cannot be revived by restart - RE-LOGIN REQUIRED (human step). Polling every 5 minutes.'
            $state = 'halfdead-giveup'
          }
          Start-Sleep -Seconds 240
        }
      } else {
        $unhealthy++
        Log ("bridge not connected (poll {0}/3, restarts {1}/{2})" -f $unhealthy, $staleRestarts, $staleMax)
        if ($unhealthy -ge 3) {
          if ($staleRestarts -lt $staleMax) {
            Log ("restarting stale bridge (Creon login / re-login) - attempt {0}/{1}" -f ($staleRestarts + 1), $staleMax)
            StopBridge
            Start-Sleep -Seconds 5
            StartBridge
            $staleRestarts++
            $state = 'restarted'
            $unhealthy = 0
            Start-Sleep -Seconds $attachWaitS   # let the new instance attach before judging it again
          } else {
            # Bounded: a session nobody logged into cannot be revived by restarting
            # (measured 2026-09-30: 37 restarts/hour, 322 bridge processes in a day).
            if ($state -ne 'stale-giveup') {
              Log ('bridge stays disconnected after {0} restarts - RE-LOGIN REQUIRED (human step). Polling every 5 minutes until Creon is restarted.' -f $staleMax)
              $state = 'stale-giveup'
            }
            $unhealthy = 3        # stay here; only a live session or a new Creon generation clears it
            Start-Sleep -Seconds $giveupWaitS
          }
        }
      }
    }
  }
  Start-Sleep -Seconds $pollS
}
