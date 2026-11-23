<#
  장전 점검(preflight). 매일 08:10 과 장애 대응 시 실행한다.
  종료코드 0 = 매매 가능, 1 = 차단(원인은 report 의 failures).
#>
[CmdletBinding()]
param(
    [string]$Root = "C:\TraderAgent",
    [int]$MaxFeedAgeHours = 18,
    [switch]$SkipBridge
)

$ErrorActionPreference = "Continue"
$checks = New-Object System.Collections.ArrayList

function Add-Check([string]$Name, [bool]$Ok, [string]$Detail, [bool]$Critical) {
    [void]$checks.Add([ordered]@{ name = $Name; ok = $Ok; critical = $Critical; detail = $Detail })
    $color = "Green"
    if (-not $Ok) { if ($Critical) { $color = "Red" } else { $color = "Yellow" } }
    Write-Host ("[{0}] {1} — {2}" -f $(if ($Ok) { "OK" } else { "FAIL" }), $Name, $Detail) -ForegroundColor $color
}

$configPath = Join-Path $Root "config.json"
if (-not (Test-Path $configPath)) {
    Add-Check "config" $false "config.json 없음: $configPath" $true
    Write-Host "설치가 필요합니다: install.ps1"
    exit 1
}
$cfg = Get-Content $configPath -Raw -Encoding UTF8 | ConvertFrom-Json
Add-Check "config" $true $configPath $true

$git = $cfg.git_exe
if (-not $git -or -not (Test-Path $git)) { $git = (Get-Command git.exe -ErrorAction SilentlyContinue).Source }
Add-Check "git" ($git -and (Test-Path $git)) ([string]$git) $true

$repoHead = ""
$repoDir = $cfg.repo_dir
if (Test-Path (Join-Path $repoDir ".git")) {
    $repoHead = (& $git -C $repoDir rev-parse --short HEAD 2>$null | Out-String).Trim()
}
Add-Check "repo" ([bool]$repoHead) ("HEAD " + $repoHead) $true

$feedPath = $cfg.feed_out
$feedOk = $false
$feedDetail = "피드 없음: $feedPath"
if (Test-Path $feedPath) {
    $feed = Get-Content $feedPath -Raw -Encoding UTF8 | ConvertFrom-Json
    $gen = [datetime]::Parse($feed.generated_at)
    $ageHours = ((Get-Date) - $gen).TotalHours
    $feedOk = ($ageHours -le $MaxFeedAgeHours)
    $counts = @()
    foreach ($prop in $feed.candidates.PSObject.Properties) {
        $counts += ("{0}={1}" -f $prop.Name, @($prop.Value.items).Count)
    }
    $feedDetail = ("generated {0} ({1:N1}h 전) · {2}" -f $gen.ToString("MM-dd HH:mm"), $ageHours, ($counts -join " "))
}
Add-Check "feed" $feedOk $feedDetail $true

$statePath = Join-Path (Split-Path $feedPath) "sync_state.json"
if (Test-Path $statePath) {
    $state = Get-Content $statePath -Raw -Encoding UTF8 | ConvertFrom-Json
    Add-Check "sync_state" $true ("synced " + $state.synced_at) $false
}
else {
    Add-Check "sync_state" $false "sync_state.json 없음(한 번도 sync 안 됨)" $false
}

$verPath = Join-Path $cfg.app_dir "version.json"
$codeOk = Test-Path $verPath
$codeDetail = "version.json 없음"
if ($codeOk) {
    $v = (Get-Content $verPath -Raw -Encoding UTF8 | ConvertFrom-Json).version
    $repoVerPath = Join-Path $repoDir "app\VERSION"
    $repoVer = if (Test-Path $repoVerPath) { (Get-Content $repoVerPath -Raw -Encoding UTF8).Trim() } else { "" }
    $codeDetail = ("installed {0} / repo {1}" -f $v, $repoVer)
    $codeOk = ($v -eq $repoVer)
}
Add-Check "code_version" $codeOk $codeDetail $false

$creon = Get-Process -ErrorAction SilentlyContinue | Where-Object { $_.ProcessName -match "creon" } | Select-Object -First 1
Add-Check "creon_process" ([bool]$creon) $(if ($creon) { "pid " + $creon.Id } else { "크레온 미실행 — 로그인 필요" }) $true

if (-not $SkipBridge) {
    $bridgeOk = $false
    $bridgeDetail = "브리지 응답 없음"
    try {
        $resp = Invoke-WebRequest -Uri "http://127.0.0.1:8100/health" -TimeoutSec 5 -UseBasicParsing
        $bridgeOk = ($resp.StatusCode -eq 200)
        $bridgeDetail = "HTTP " + $resp.StatusCode
    }
    catch { $bridgeDetail = "연결 실패: " + $_.Exception.Message }
    Add-Check "bridge" $bridgeOk $bridgeDetail $true
}

$freeGb = [math]::Round((Get-PSDrive C).Free / 1GB, 1)
Add-Check "disk" ($freeGb -ge 1) ("C: 여유 {0} GB" -f $freeGb) $false

$failures = @($checks | Where-Object { (-not $_.ok) -and $_.critical })
$report = [ordered]@{
    checked_at = (Get-Date).ToString("s")
    root       = $Root
    ok         = ($failures.Count -eq 0)
    failures   = ($failures | ForEach-Object { $_.name })
    checks     = $checks
}
$reportPath = Join-Path $Root "logs\preflight_report.json"
New-Item -ItemType Directory -Force -Path (Split-Path $reportPath) | Out-Null
$report | ConvertTo-Json -Depth 6 | Set-Content -Path $reportPath -Encoding UTF8
Write-Host ("리포트: {0}" -f $reportPath)

if ($failures.Count -gt 0) {
    Write-Host ("차단 사유: " + (($failures | ForEach-Object { $_.name }) -join ", ")) -ForegroundColor Red
    exit 1
}
Write-Host "매매 가능 상태입니다." -ForegroundColor Green
exit 0
