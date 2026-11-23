# Windows 설치 키트 — 구조와 체크리스트

## 폴더 구조 (고객 PC)

    <Root>\app\            코드 번들 (VERSION, version.json 포함)
    <Root>\feed-repo\      배포 저장소 clone (읽기 전용 운용)
    <Root>\keys\           deploy key (feed_deploy, feed_deploy.pub)
    <Root>\data\feed\      sync 결과 피드 + sync_state.json
    <Root>\logs\           install.log, sync_feed.log, preflight_report.json
    <Root>\tools\mingit\   git 미설치 시 자동 받은 MinGit
    <Root>\config.json     customer_id, repo_url, repo_dir, app_dir, feed_out, git_exe, git_ssh_command
    <Root>\sync_feed.bat / loop_start.bat / post_close.bat

## install.ps1 체크리스트 (순서 고정)

1. 여유 공간 ≥ 2GB, Windows 11/10, 관리자 불필요(사용자 폴더 + 작업 스케줄러)
2. git 해석: 인자 → PATH → `<Root>\tools\mingit\cmd\git.exe` → MinGit 다운로드(GitHub releases API 로 `MinGit-*-64-bit.zip`)
3. python 해석: 인자 → 후보 경로 → PATH. 코어는 64bit 확인(`struct.calcsize('P')*8`), 브리지는 32bit 권장
4. deploy key 생성(`ssh-keygen -q -t ed25519 -N "" ...`) + 공개키 출력 + 등록 명령 안내
   - PowerShell 5.1 은 네이티브 명령에 빈 인자를 넘기지 못한다 → `Start-Process -ArgumentList '<한 줄 문자열>'` 로 호출
5. clone 또는 fetch + `core.autocrlf false` + `git rm --cached -r . && git reset --hard`
6. 저장소 검증: `deploy/meta.json`·`app/VERSION` 존재
7. `client/` 에서 sync/preflight 복사, `config.json` 생성(BOM 없이), 실행 배치 3종 생성(환경 박아넣기)
8. 리허설 sync 1회 → exit 0 이면 설치 성공(`install_report.json`)
9. 예약작업: 08:45 sync / 08:50 loop / 15:40 post_close

## preflight.ps1 체크 항목

config 존재 · git 실행 가능 · repo HEAD · 피드(`generated_at` 나이·전략별 건수) · sync_state ·
코드 버전(설치 vs repo) · 브로커 프로세스 · 브로커 브리지 `/health` · 디스크.
critical 실패가 하나라도 있으면 exit 1 + `logs/preflight_report.json`, 출력에 "차단 사유"를 나열한다.

## 삭제·재설치

- `uninstall.ps1`: 작업 3개 삭제 → `run_market_loop`/`run_bridge` 프로세스 종료 → (`-RemoveData`) 폴더 삭제
- deploy key 는 GitHub 쪽에서 별도 삭제(`gh repo deploy-key list/delete`)

## 실패 신호 → 원인

| 증상 | 원인 |
|---|---|
| `-File` 매개변수 오류 / 파싱 에러에 한글 깨짐 | .ps1 이 BOM 없는 UTF-8 |
| `Unexpected UTF-8 BOM (decode using utf-8-sig)` | PS 가 쓴 config.json 을 Python 이 utf-8 로 읽음 |
| `ConvertFrom-Json` 이 JSON 오류 | `Get-Content` 가 ANSI 로 읽어 한글 문자열 깨짐 → `-Encoding UTF8` |
| `latest.json sha256 불일치` | CRLF 체크아웃 |
| clone 시 `/usr/bin/ssh: Permission denied` | MinGit ssh 래퍼 → OpenSSH 전체 경로 |
| `AccessDeniedException ...@tmp` (Jenkins) | 마운트 디렉터리 소유자가 CI 사용자와 다름 |
