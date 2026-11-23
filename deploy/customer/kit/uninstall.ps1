<#
  설치 제거. 기본은 예약작업·실행 프로세스만 정리하고 데이터는 남긴다.
  -RemoveData 를 주면 C:\TraderAgent 전체와 deploy key 를 삭제한다.
#>
[CmdletBinding()]
param(
    [string]$Root = "C:\TraderAgent",
    [switch]$RemoveData,
    [switch]$Force
)

$ErrorActionPreference = "Continue"
$tasks = @("TraderAgent-SyncFeed", "TraderAgent-LoopStart", "TraderAgent-PostClose")

foreach ($task in $tasks) {
    & schtasks /Query /TN $task 2>$null | Out-Null
    if ($LASTEXITCODE -eq 0) {
        & schtasks /Delete /F /TN $task | Out-Null
        Write-Host ("예약작업 삭제: " + $task) -ForegroundColor Yellow
    }
}

$procs = Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
    Where-Object { $_.CommandLine -like "*run_market_loop*" -or $_.CommandLine -like "*run_bridge*" }
foreach ($p in $procs) {
    Write-Host ("프로세스 종료: pid {0}" -f $p.ProcessId) -ForegroundColor Yellow
    Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
}

if ($RemoveData) {
    if (-not $Force) {
        $answer = Read-Host "C:\TraderAgent 전체와 deploy key 를 삭제합니다. 진행하려면 DELETE 입력"
        if ($answer -ne "DELETE") {
            Write-Host "중단했습니다(예약작업만 정리됨)."
            exit 0
        }
    }
    if (Test-Path $Root) {
        Remove-Item -Recurse -Force $Root
        Write-Host ("삭제: " + $Root) -ForegroundColor Yellow
    }
    Write-Host "GitHub 에서 deploy key 도 제거하세요: gh repo deploy-key list --repo sjh146/trader-feed"
}

Write-Host "정리 완료."
exit 0
