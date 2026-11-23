<#
  trader-agent 고객 PC 설치 키트
  - 관리자 권한을 요구하지 않는다(사용자 폴더 + 예약작업).
  - 네트워크 저장소(trader-feed)를 clone 하고, 이후 매일 08:45 sync 로 피드+코드를 자동 갱신한다.
  사용 예:
    powershell -ExecutionPolicy Bypass -File install.ps1 -CustomerId pilot1
    powershell -ExecutionPolicy Bypass -File install.ps1 -CustomerId pilot1 -DryRun
#>
[CmdletBinding()]
param(
    [string]$Root = "C:\TraderAgent",
    [Parameter(Mandatory = $true)][string]$CustomerId,
    [string]$RepoUrl = "git@github.com:sjh146/trader-feed.git",
    [string]$Branch = "main",
    [string]$PythonCore = "",
    [string]$PythonBridge = "",
    [string]$GitExe = "",
    [string]$Channel = "stable",
    [switch]$SkipGitBootstrap,
    [switch]$SkipTasks,
    [switch]$SkipRehearsal,
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"
$script:LogPath = Join-Path $Root "logs\install.log"

function Write-Step([string]$Message) {
    $line = "[{0}] {1}" -f (Get-Date -Format "HH:mm:ss"), $Message
    Write-Host $line
    try { Add-Content -Path $script:LogPath -Value $line -Encoding UTF8 } catch { }
}

function Fail([string]$Message, [int]$Code = 1) {
    $line = "[FAIL] " + $Message
    Write-Host $line -ForegroundColor Red
    try { Add-Content -Path $script:LogPath -Value $line -Encoding UTF8 } catch { }
    exit $Code
}

function Resolve-Git {
    if ($GitExe -and (Test-Path $GitExe)) { return $GitExe }
    $cmd = Get-Command git.exe -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    $portable = Join-Path $Root "tools\mingit\cmd\git.exe"
    if (Test-Path $portable) { return $portable }
    if ($SkipGitBootstrap) { Fail "git 을 찾지 못했습니다(-SkipGitBootstrap)." 3 }

    Write-Step "MinGit 다운로드(무설치 git)"
    $tools = Join-Path $Root "tools"
    New-Item -ItemType Directory -Force -Path $tools | Out-Null
    $release = Invoke-RestMethod -Uri "https://api.github.com/repos/git-for-windows/git/releases/latest" `
        -Headers @{ "User-Agent" = "trader-kit" }
    $asset = $release.assets | Where-Object { $_.name -like "MinGit-*-64-bit.zip" } | Select-Object -First 1
    if (-not $asset) { Fail "MinGit 배포 자산을 찾지 못했습니다." 4 }
    $zip = Join-Path $tools "mingit.zip"
    Invoke-WebRequest -Uri $asset.browser_download_url -OutFile $zip -UseBasicParsing
    Expand-Archive -Path $zip -DestinationPath (Join-Path $tools "mingit") -Force
    Remove-Item $zip -Force
    if (-not (Test-Path $portable)) { Fail "MinGit 압축 해제 실패" 5 }
    return $portable
}

function Resolve-Python([string]$Preferred, [string]$Label) {
    if ($Preferred -and (Test-Path $Preferred)) { return $Preferred }
    $candidates = @(
        "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe",
        "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe",
        "C:\Python312\python.exe",
        "C:\Python311\python.exe",
        "C:\Users\$env:USERNAME\Python312-64\python.exe",
        "C:\Users\$env:USERNAME\Python311-32\python.exe"
    )
    foreach ($path in $candidates) {
        if ($path -and (Test-Path $path)) { return $path }
    }
    $cmd = Get-Command python.exe -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    Fail "$Label 파이썬을 찾지 못했습니다. -PythonCore/-PythonBridge 로 경로를 지정하세요." 6
}

function Bitness([string]$PythonPath) {
    $out = & $PythonPath -c "import struct;print(struct.calcsize('P')*8)" 2>$null
    if ($LASTEXITCODE -ne 0) { return 0 }
    return [int]$out.Trim()
}

function Resolve-Ssh {
    $cmd = Get-Command ssh.exe -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    $candidate = "$env:WINDIR\System32\OpenSSH\ssh.exe"
    if (Test-Path $candidate) { return $candidate }
    return "ssh"
}

function Ensure-SshKey([string]$KeyPath, [string]$Comment) {
    if (Test-Path $KeyPath) { return }
    $sshKeygen = (Get-Command ssh-keygen.exe -ErrorAction SilentlyContinue).Source
    if (-not $sshKeygen) {
        $candidate = "$env:WINDIR\System32\OpenSSH\ssh-keygen.exe"
        if (Test-Path $candidate) { $sshKeygen = $candidate }
    }
    if (-not $sshKeygen) { Fail "ssh-keygen 을 찾지 못했습니다(Windows OpenSSH 기능 필요)." 7 }
    New-Item -ItemType Directory -Force -Path (Split-Path $KeyPath) | Out-Null
    $keyArgs = '-q -t ed25519 -N "" -C "' + $Comment + '" -f "' + $KeyPath + '"'
    Start-Process -FilePath $sshKeygen -ArgumentList $keyArgs -Wait -NoNewWindow
    if (-not (Test-Path $KeyPath)) { Fail "SSH 키 생성 실패: $KeyPath" 8 }
}

Write-Step "설치 시작 (CustomerId=$CustomerId, Root=$Root)"
if ($DryRun) { Write-Step "DRY RUN: 실제 변경은 하지 않습니다." }

$dirs = @{
    Root     = $Root
    App      = Join-Path $Root "app"
    FeedRepo = Join-Path $Root "feed-repo"
    Keys     = Join-Path $Root "keys"
    Logs     = Join-Path $Root "logs"
    DataFeed = Join-Path $Root "data\feed"
}
foreach ($dir in $dirs.Values) {
    if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
}

$drive = (Get-PSDrive C).Free / 1GB
Write-Step ("C: 여유 공간 {0:N1} GB" -f $drive)
if ($drive -lt 2) { Fail "디스크 여유 공간이 2GB 미만입니다." 9 }

$git = Resolve-Git
Write-Step "git: $git"

$pyCore = Resolve-Python $PythonCore "코어(64bit)"
$coreBits = Bitness $pyCore
Write-Step ("코어 파이썬: {0} ({1}bit)" -f $pyCore, $coreBits)
if ($coreBits -ne 64) { Fail "코어 파이썬은 64bit 여야 합니다(현재 ${coreBits}bit)." 10 }

if ($PythonBridge -and (Test-Path $PythonBridge)) {
    $bridgeBits = Bitness $PythonBridge
    Write-Step ("브리지 파이썬: {0} ({1}bit)" -f $PythonBridge, $bridgeBits)
    if ($bridgeBits -ne 32) { Write-Step "경고: 크레온 COM 은 32bit 파이썬이 필요합니다." }
}
else {
    Write-Step "경고: -PythonBridge 미지정. 크레온 브리지는 32bit 파이썬이 필요합니다."
}

$keyPath = Join-Path $dirs.Keys "feed_deploy"
Ensure-SshKey $keyPath ("trader-feed-" + $CustomerId)
$pubKey = (Get-Content ($keyPath + ".pub") -Raw).Trim()
Write-Step "deploy key 공개키:"
Write-Host ("    " + $pubKey)

if ($DryRun) {
    Write-Step "DRY RUN 종료: clone/예약작업/리허설은 실행하지 않았습니다."
    exit 0
}

$sshExe = (Resolve-Ssh) -replace '\\', '/'
$keyForGit = $keyPath -replace '\\', '/'
$sshCmd = '"' + $sshExe + '" -i "' + $keyForGit + '" -o StrictHostKeyChecking=accept-new -o IdentitiesOnly=yes'
Write-Step "ssh: $sshExe"
if (-not (Test-Path (Join-Path $dirs.FeedRepo ".git"))) {
    Write-Step "저장소 clone"
    & $git -c ("core.sshCommand=" + $sshCmd) clone --depth 1 --branch $Branch $RepoUrl $dirs.FeedRepo
    if ($LASTEXITCODE -ne 0) {
        Write-Step "clone 실패 — deploy key 등록이 필요합니다."
        Write-Host "  gh repo deploy-key add `"$keyPath.pub`" --repo sjh146/trader-feed --title $CustomerId"
        exit 11
    }
}
else {
    Write-Step "저장소 갱신"
    & $git -C $dirs.FeedRepo -c ("core.sshCommand=" + $sshCmd) fetch --depth 1 origin $Branch
    if ($LASTEXITCODE -ne 0) { Fail "fetch 실패 — deploy key 또는 네트워크 확인" 12 }
    & $git -C $dirs.FeedRepo reset --hard FETCH_HEAD | Out-Null
}

& $git -C $dirs.FeedRepo config core.autocrlf false
& $git -C $dirs.FeedRepo rm --cached -r -q .
& $git -C $dirs.FeedRepo reset --hard HEAD | Out-Null
$metaPath = Join-Path $dirs.FeedRepo "deploy\meta.json"
$versionPath = Join-Path $dirs.FeedRepo "app\VERSION"
if (-not (Test-Path $metaPath)) { Fail "저장소에 deploy/meta.json 이 없습니다(피드 미게시)." 13 }
if (-not (Test-Path $versionPath)) { Fail "저장소에 app/VERSION 이 없습니다(코드 미게시)." 14 }
$codeVersion = (Get-Content $versionPath -Raw -Encoding UTF8).Trim()
Write-Step "저장소 코드 버전: $codeVersion"

$clientSrc = Join-Path $dirs.FeedRepo "client"
$syncPy = Join-Path $Root "sync_feed.py"
if (Test-Path (Join-Path $clientSrc "sync_feed.py")) {
    Copy-Item (Join-Path $clientSrc "sync_feed.py") $syncPy -Force
    Write-Step "클라이언트 스크립트 복사(client/sync_feed.py)"
}
elseif (Test-Path (Join-Path $Root "sync_feed.py")) {
    Write-Step "기존 sync_feed.py 사용"
}
else {
    Fail "client/sync_feed.py 가 없습니다. 서버에서 scripts/publish_client_github.py 를 실행하세요." 15
}
if (Test-Path (Join-Path $clientSrc "preflight.ps1")) {
    Copy-Item (Join-Path $clientSrc "preflight.ps1") (Join-Path $Root "preflight.ps1") -Force
}

$config = [ordered]@{
    base_url            = ""
    customer_id         = $CustomerId
    channel             = $Channel
    repo_url            = $RepoUrl
    repo_dir            = $dirs.FeedRepo
    branch              = $Branch
    app_dir             = $dirs.App
    feed_out            = (Join-Path $dirs.DataFeed "screener_latest.json")
    log_dir             = $dirs.Logs
    feed_schema_version = 1
    max_age_hours       = 18
    install_code        = $true
    git_exe             = $git
    git_ssh_command     = $sshCmd
}
$configPath = Join-Path $Root "config.json"
$configJson = $config | ConvertTo-Json -Depth 5
[System.IO.File]::WriteAllText($configPath, $configJson, (New-Object System.Text.UTF8Encoding($false)))
Write-Step "config 작성: $configPath"

$syncBat = @"
@echo off
setlocal
cd /d "%~dp0"
set GIT_EXE=$git
set GIT_SSH_COMMAND=$sshCmd
if not exist logs mkdir logs
"$pyCore" sync_feed.py --config config.json >> logs\sync_feed.log 2>&1
exit /b %ERRORLEVEL%
"@
Set-Content -Path (Join-Path $Root "sync_feed.bat") -Value $syncBat -Encoding ASCII

$loopBat = @"
@echo off
setlocal
cd /d "%~dp0app"
if not exist config_live.json copy /y config.example.json config_live.json >nul
"$pyCore" run_market_loop.py run --loop --live --yes --feed "%~dp0data\feed\screener_latest.json" --config "config_live.json" >> "%~dp0logs\loop.log" 2>&1
"@
Set-Content -Path (Join-Path $Root "loop_start.bat") -Value $loopBat -Encoding ASCII

$postBat = @"
@echo off
setlocal
cd /d "%~dp0"
"$pyCore" sync_feed.py --config config.json >> logs\post_close.log 2>&1
"@
Set-Content -Path (Join-Path $Root "post_close.bat") -Value $postBat -Encoding ASCII
Write-Step "실행 배치 3종 생성(sync_feed/loop_start/post_close)"

if (-not $SkipRehearsal) {
    Write-Step "리허설: 피드+코드 1회 동기화"
    Push-Location $Root
    try {
        & $pyCore $syncPy --config $configPath
        $rc = $LASTEXITCODE
    }
    finally { Pop-Location }
    if ($rc -ne 0) { Fail "리허설 실패(exit $rc). logs\sync_feed.log 확인" 16 }
    $installedVersion = ""
    $vpath = Join-Path $dirs.App "version.json"
    if (Test-Path $vpath) { $installedVersion = (Get-Content $vpath -Raw -Encoding UTF8 | ConvertFrom-Json).version }
    Write-Step "리허설 성공: app 버전 $installedVersion"
}

if (-not $SkipTasks) {
    Write-Step "예약작업 등록"
    $tasks = @(
        @{ Name = "TraderAgent-SyncFeed"; Time = "08:45"; Cmd = ('cmd /c "' + (Join-Path $Root "sync_feed.bat") + '"') },
        @{ Name = "TraderAgent-LoopStart"; Time = "08:50"; Cmd = ('cmd /c "' + (Join-Path $Root "loop_start.bat") + '"') },
        @{ Name = "TraderAgent-PostClose"; Time = "15:40"; Cmd = ('cmd /c "' + (Join-Path $Root "post_close.bat") + '"') }
    )
    foreach ($task in $tasks) {
        & schtasks /Create /F /SC DAILY /ST $task.Time /TN $task.Name /TR $task.Cmd | Out-Null
        if ($LASTEXITCODE -ne 0) { Fail ("예약작업 생성 실패: " + $task.Name) 17 }
        Write-Step ("  등록: {0} @ {1}" -f $task.Name, $task.Time)
    }
}

$report = [ordered]@{
    customer_id   = $CustomerId
    root          = $Root
    git           = $git
    python_core   = $pyCore
    python_bridge = $PythonBridge
    code_version  = $codeVersion
    installed_at  = (Get-Date).ToString("s")
    tasks         = (-not $SkipTasks)
    rehearsal     = (-not $SkipRehearsal)
}
$reportPath = Join-Path $Root "install_report.json"
$reportJson = $report | ConvertTo-Json -Depth 5
[System.IO.File]::WriteAllText($reportPath, $reportJson, (New-Object System.Text.UTF8Encoding($false)))
Write-Step "설치 완료. 리포트: $reportPath"
Write-Host ""
Write-Host "다음 단계:" -ForegroundColor Cyan
Write-Host "  1) 크레온 PLUS 로그인(사람 1회)"
Write-Host "  2) 32bit 브리지 실행: python32 run_bridge.py  (또는 bridge_run.bat)"
Write-Host "  3) 파이프라인 확인: powershell -ExecutionPolicy Bypass -File `"$Root\preflight.ps1`" -Root `"$Root`""
exit 0
