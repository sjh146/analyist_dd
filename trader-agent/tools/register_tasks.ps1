# Registers the four trading tasks for THIS machine.
#
# Everything that used to be hardcoded (repo path, Windows user, python paths) is
# resolved at run time, so a fresh clone registers without editing any file:
#   powershell -NoProfile -ExecutionPolicy Bypass -File tools\register_tasks.ps1 -DryRun
#   powershell -NoProfile -ExecutionPolicy Bypass -File tools\register_tasks.ps1
#
# Tasks
#   trader-bridge       weekdays 08:45  bridge_start_bg.bat  (32-bit bridge, detached)
#   trader-loop         weekdays 08:50  loop_start_bg.bat    (64-bit loop, detached)
#   trader-bridge-sup   logon + every 30 min, RunLevel Highest, no time limit
#   trader-loop-sup     logon + every 30 min, RunLevel Highest, no time limit
#
# Requires an ADMIN console: the supervisors must run at Highest to attach to the
# elevated Creon PLUS session. Override interpreters with -Py32 / -Py64 or the
# TRADER_PY32 / TRADER_PY64 environment variables.
param(
  [switch]$DryRun,
  [string]$Py32 = "",
  [string]$Py64 = ""
)

$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
$user = "$env:USERDOMAIN\$env:USERNAME"
if (-not $Py32) { $Py32 = if ($env:TRADER_PY32) { $env:TRADER_PY32 } else { Join-Path $env:USERPROFILE 'Python311-32\python.exe' } }
if (-not $Py64) { $Py64 = if ($env:TRADER_PY64) { $env:TRADER_PY64 } else { Join-Path $env:USERPROFILE 'Python312-64\python.exe' } }

Write-Host "repo  : $repo"
Write-Host "user  : $user"
Write-Host "py32  : $Py32 ($(if (Test-Path $Py32) { 'found' } else { 'MISSING' }))"
Write-Host "py64  : $Py64 ($(if (Test-Path $Py64) { 'found' } else { 'MISSING' }))"

$xmlTemplate = @'
<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Author>__USER__</Author>
    <Description>__DESC__</Description>
  </RegistrationInfo>
  <Triggers>
    <LogonTrigger>
      <Enabled>true</Enabled>
      <UserId>__USER__</UserId>
      <Delay>PT20S</Delay>
    </LogonTrigger>
    <TimeTrigger>
      <StartBoundary>2026-09-28T00:05:00</StartBoundary>
      <Enabled>true</Enabled>
      <Repetition>
        <Interval>PT30M</Interval>
        <StopAtDurationEnd>false</StopAtDurationEnd>
      </Repetition>
    </TimeTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      <UserId>__USER__</UserId>
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>HighestAvailable</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <AllowHardTerminate>true</AllowHardTerminate>
    <StartWhenAvailable>true</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>
    <AllowStartOnDemand>true</AllowStartOnDemand>
    <Enabled>true</Enabled>
    <Hidden>false</Hidden>
    <RestartOnFailure>
      <Interval>PT1M</Interval>
      <Count>3</Count>
    </RestartOnFailure>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
    <Priority>7</Priority>
    <IdleSettings>
      <StopOnIdleEnd>false</StopOnIdleEnd>
      <RestartOnIdle>false</RestartOnIdle>
    </IdleSettings>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>powershell.exe</Command>
      <Arguments>-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "__SCRIPT__"</Arguments>
      <WorkingDirectory>__REPO__</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
'@

function New-SupTask {
  param([string]$TaskName, [string]$ScriptName, [string]$Description)
  $scriptPath = Join-Path $repo ("tools\" + $ScriptName)
  if (-not (Test-Path $scriptPath)) { throw "supervisor not found: $scriptPath" }
  $xml = $xmlTemplate.Replace('__USER__', $user).Replace('__REPO__', $repo).
          Replace('__SCRIPT__', $scriptPath).Replace('__DESC__', $Description)
  $dir = Join-Path $repo 'tools\tasks'
  New-Item -ItemType Directory -Force -Path $dir | Out-Null
  $file = Join-Path $dir ($TaskName + '.xml')
  [IO.File]::WriteAllText($file, $xml, [Text.Encoding]::Unicode)
  if ($DryRun) {
    Write-Host "[dry-run] would register $TaskName from $file"
    return
  }
  & schtasks.exe /Create /XML $file /TN $TaskName /F | Out-Null
  if ($LASTEXITCODE -ne 0) { throw "schtasks failed for $TaskName (rc=$LASTEXITCODE) — admin console required" }
  Write-Host "registered $TaskName"
}

function New-WeeklyTask {
  param([string]$TaskName, [string]$BatName, [string]$StartTime)
  $bat = Join-Path $repo $BatName
  if (-not (Test-Path $bat)) { throw "launcher not found: $bat" }
  if ($DryRun) {
    Write-Host "[dry-run] schtasks /Create /TN $TaskName /TR `"$bat`" /SC WEEKLY /D MON,TUE,WED,THU,FRI /ST $StartTime"
    return
  }
  & schtasks.exe /Create /TN $TaskName /TR $bat /SC WEEKLY /D MON,TUE,WED,THU,FRI /ST $StartTime /F | Out-Null
  if ($LASTEXITCODE -ne 0) { throw "schtasks failed for $TaskName (rc=$LASTEXITCODE)" }
  Write-Host "registered $TaskName ($StartTime)"
}

New-WeeklyTask -TaskName 'trader-bridge' -BatName 'bridge_start_bg.bat' -StartTime '08:45'
New-WeeklyTask -TaskName 'trader-loop'   -BatName 'loop_start_bg.bat'   -StartTime '08:50'
New-SupTask -TaskName 'trader-bridge-sup' -ScriptName 'supervise_bridge.ps1' `
  -Description 'Bridge supervisor: keeps the 32-bit Creon bridge alive, starts it when the HTS is up, restarts it after a Creon login. Needs Highest (elevated Creon COM).'
New-SupTask -TaskName 'trader-loop-sup' -ScriptName 'supervise_loop.ps1' `
  -Description 'Loop supervisor: starts the 64-bit market loop on weekdays 08:30-15:35 when the bridge is connected; never two instances.'

Write-Host ''
Write-Host 'done. verify with: schtasks /Query /TN trader-bridge-sup /V /FO LIST'
