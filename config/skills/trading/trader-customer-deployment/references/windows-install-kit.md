# Windows 고객 PC 설치 키트 (PowerShell 5.1)

목표: 신규 PC 1대를 60분 안에 "피드+코드 자동 갱신 + 예약작업" 상태로 만든다. 스크립트는 install / preflight / uninstall 3종.

## 인코딩 (가장 먼저 막히는 곳)

- **스크립트는 UTF-8 BOM 으로 저장한다.** PS 5.1 은 BOM 없는 `.ps1` 을 ANSI(CP949)로 읽어 한글 문자열에서 따옴표가 깨지고
  `ParserError: UnrecognizedToken` 이 쏟아진다.
- **데이터 파일은 BOM 없이 쓴다**: `Set-Content -Encoding UTF8` 은 BOM 을 붙여 Python `json.load` 가 `Unexpected UTF-8 BOM` 으로 죽는다.
  `[IO.File]::WriteAllText($path, $json, (New-Object System.Text.UTF8Encoding($false)))` 로 쓰고, 읽는 쪽은 `utf-8-sig` 로 연다.
- **읽을 때도 인코딩을 명시한다**: `Get-Content -Raw -Encoding UTF8`. 기본값은 ANSI 라 한글 피드 JSON 이 깨져 `ConvertFrom-Json` 이 실패한다.

## git / ssh

- Git for Windows 가 없는 PC 가 흔하다. MinGit(zip)을 받아 `tools\mingit` 에 풀면 관리자 권한 없이 쓸 수 있다.
  이후 `git.exe` 경로를 config(`git_exe`)로 고정하고, 파이썬은 `GIT_EXE` env 로 그 경로를 쓰게 한다.
- **MinGit 의 git 은 자체 ssh shim 을 써서 `/usr/bin/ssh` 를 찾다 실패한다.** `core.sshCommand` 를 Windows OpenSSH 로 고정한다:
  `"C:/WINDOWS/System32/OpenSSH/ssh.exe" -i "C:/path/key" -o StrictHostKeyChecking=accept-new -o IdentitiesOnly=yes`
  (슬래시 방향 주의 — git 은 `C:/...` 형태를 안전하게 받는다).
- 키 생성: `ssh-keygen -N ""` 의 빈 인자는 PowerShell 이 **버린다**. `Start-Process -ArgumentList '<전체 인자 문자열>' -Wait -NoNewWindow`
  로 한 문자열로 넘겨야 빈 passphrase 가 전달된다.
- clone 실패(키 미등록)는 실패로 끝내지 말고 **등록 명령을 출력**하고 종료코드를 달리한다. 설치 스크립트는 멱등이어야 재실행으로 이어진다.

## 예약작업·정리

- `schtasks /Create /F /SC DAILY /ST 08:45 /TN TraderAgent-SyncFeed /TR "cmd /c <bat>"` — 등록 후 `/Query` 로 확인한다.
- uninstall 은 `schtasks /Delete /F` + `Get-CimInstance Win32_Process | Where CommandLine -like '*run_market_loop*'` 로 프로세스를 정리한다.
  **개발 PC 에서 리허설할 때 이 kill 이 운영 중인 브리지·루프를 죽인다** — 리허설은 전용 경로(예: `C:\Users\<u>\TraderAgentTest`)에서만 하고,
  끝나면 무엇을 죽였는지 보고한다.

## preflight 체크(장전 매일)

- 피드: `generated_at` 나이 ≤ 18h, 후보 합계 > 0
- 코드: 설치된 `version.json` == 저장소 `app/VERSION`
- 크레온 프로세스 실행 여부(사람 로그인 필요) · 브리지 `http://127.0.0.1:8100/health` 200 · C: 여유 ≥ 1GB
- 결과는 JSON 으로 남기고 종료코드로 차단(0=매매 가능, 1=차단).
