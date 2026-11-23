param(
    [string]$Root = "C:\TraderAgent",
    [string]$Python = "C:\TraderAgent\python\python.exe",
    [string]$Git = "C:\Program Files\Git\cmd\git.exe",
    [string]$SyncAt = "08:45",
    [string]$LoopAt = "08:50",
    [string]$AfterClose = "15:40"
)

$ErrorActionPreference = "Stop"
$taskUser = "$env:USERDOMAIN\$env:USERNAME"

function Register-Task($name, $time, $command) {
    schtasks /Create /F /SC DAILY /ST $time /TN $name /TR $command /RU $taskUser 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "예약작업 생성 실패: $name" }
    Write-Host "등록: $name @ $time"
}

Register-Task "TraderAgent-SyncFeed" $SyncAt "cmd /c `"$Root\sync_feed.bat`""
Register-Task "TraderAgent-LoopStart" $LoopAt "cmd /c `"$Root\loop_start_bg.bat`""
Register-Task "TraderAgent-PostClose" $AfterClose "cmd /c `"$Root\post_close.bat`""

Write-Host "완료. 작업 스케줄러에서 TraderAgent-* 3개를 확인한다."
Write-Host "Python: $Python / Git: $Git"
