---
name: customer-pc-agent-deployment
description: Use when shipping feed and code to customer PCs.
---

# 고객 PC 에이전트 배포 (피드 + 코드 자동 갱신)

내가 통제하지 않는 고객 컴퓨터에서 돌릴 실행기를 만들고, **매일 피드와 코드를 자동 갱신**하는 클래스의 작업.
기준 구조: 데이터·모델·추론은 중앙, 고객 PC 는 얇은 실행기(주문·리스크·로그).

## 1. 배포 채널 선택 (순서대로)

1. **비공개 git 저장소 pull** — 피드(`deploy/`)와 코드(`app/`)를 한 저장소에 두고 고객 PC 가 08:45 에 당긴다.
   이력·롤백·감사가 공짜이고, 고객이 방화벽을 건드릴 필요가 없다(아웃바운드 443/22).
2. 오브젝트 스토리지/소형 VPS pull — 내 서버 가동과 고객 수신을 분리한다(홈 서버 OOM·재부팅 대비).
3. 터널(Tailscale Funnel / Cloudflare quick tunnel) — **파일럿 전용**. 빠른 터널 URL 은 재시작마다 바뀌어 고객 설정이 깨진다.
4. push 는 기본 선택지가 아니다: 고객 PC 는 NAT 뒤라서 서버가 밀어넣을 수 없다. "긴급 정지"가 필요하면 푸시가 아니라
   고객 PC 가 주기적으로 확인하는 `halt` 플래그 파일로 처리한다.

CI 선택: **GitHub Actions 는 쓸 수 없다** — 피드 원본이 저장소 밖(내 호스트)에 있다. 호스트 디렉터리를 마운트하는
Jenkins 나 호스트 크론이 게시자다. Jenkins 를 쓸 때는 파이프라인이 `python3` 를 호출하므로 이미지에 python3 를 넣고,
`/work` 소유권을 jenkins 로 둔다(`@tmp` 생성 실패로 빌드가 죽는다). 컨테이너 구축·플러그인 설치·잡 생성 함정:
`references/jenkins-host-mount.md`

## 2. 저장소 계약 (고정 레이아웃)

    deploy/latest.json            오늘 피드 (원자적 교체)
    deploy/meta.json              published_at, generated_at, sha256, content_sha256, candidates
    deploy/history/YYYY-MM-DD.json
    app/                          트레이더 코드 + VERSION(X.Y.Z) + BUILD_INFO.json
    client/                       클라이언트 스크립트(sync/preflight/install) — 자기 배포

- `.gitattributes` 에 `* -text` 를 둔다. Windows 체크아웃이 CRLF 로 변환하면 `meta.sha256`(파일 바이트 해시)이 맞지 않아
  고객 sync 가 "무결성 실패"로 멈춘다. 고객 스크립트도 `core.autocrlf false` 를 설정하고, 이미 CRLF 로 받아둔 사본은
  `git rm --cached -r . && git reset --hard` 로 재작성한다.
- `content_sha256`(published_at 제외 해시)을 따로 둔다. 같은 피드를 다시 게시할 때 `published_at` 만 바뀌어
  매번 커밋되는 것을 막는다.
- 게시 전 가드: `generated_at` 18시간 초과 거부, 후보 0건 거부, 필수 코드 파일 존재 확인, `VERSION` 은 `^\d+\.\d+\.\d+$`.
- 첫 push 는 `git ls-remote --heads origin <branch>` 로 원격 브랜치 존재를 확인하고 없으면 `push -u` 로 보낸다.
  없는 ref 로 `pull` 을 먼저 시도하면 재시도 루프만 돌고 push 가 영원히 실행되지 않는다.

## 3. 고객 PC 동기화 규칙 (fail-closed)

- 인증은 **저장소별 read-only deploy key**. 고객 PC 는 절대 커밋하지 않는다 → `pull` 충돌이 원천적으로 없다.
  그래서 `pull` 대신 `fetch --depth 1` + `reset --hard FETCH_HEAD` 를 쓴다.
- 피드 검증 순서: `meta.sha256` → 오늘 날짜(18h 이내) → `feed_schema_version` 일치. 하나라도 실패하면
  **기존 피드를 건드리지 않고 종료코드 1**. 수신 실패는 거래 중단이지 직전 피드로 계속 도는 것이 아니다.
- 코드 교체: 스테이징 복사 → 필수 파일(`trader_core/engine.py` 등) 검증 → 현재 앱을 백업으로 rename → 스왑 →
  실패 시 롤백. `config_live.json`·`logs/`·`journal/`·`kill_switch.txt` 는 보존 목록으로 덮어쓰지 않는다.
- **백업 디렉터리를 앱 디렉터리 밖에 만든다.** 안에 만들면 백업 정리 단계의 실패가 고객 데이터 삭제로 이어지고,
  정리 예외가 피드 sync 전체를 중단시킨다.
- 실행 배치는 환경을 박아서 만든다(`GIT_EXE`, `GIT_SSH_COMMAND`). 크론/작업스케줄러는 PATH 를 물려주지 않는다.

## 4. 서명 — 전 고객 공유 키를 쓰지 마라

- 고객 config 에 **공유 릴리스 키**를 넣으면 고객 1명이 전 고객용 악성 업데이트를 서명할 수 있다(= CRITICAL).
  파일럿이라도 이 구조로 만들지 않는다.
- 최소 수정: 고객별 파생 키 `HMAC-SHA256(master, "release:<customer_id>")`. 마스터는 서버에만 두고,
  `/v1/manifest` 는 Bearer 인증을 요구하며 **요청한 고객의 파생 키로 서명**해 내려준다. 고객 config 에는 그 고객 키만 넣는다.
- 상용 승격: Ed25519 로 바꾸고 공개키를 클라이언트에 고정(개인키는 서버). 고객 PC 에 의존성 추가가 부담이면
  파생 HMAC 유지도 가능하지만, 키 회전 절차를 문서화한다.
- 회귀 테스트는 "다른 고객 키로 검증하면 실패"까지 포함한다(같은 키로 통과하면 그건 테스트가 아니다).

## 5. Windows 설치 키트 필수 함정

- `.ps1` 은 **UTF-8 BOM** 으로 저장한다. PowerShell 5.1 은 BOM 없는 UTF-8 을 ANSI(CP949)로 읽어 한글 문자열에서
  따옴표가 깨지고 파싱 에러가 난다.
- PowerShell `Set-Content -Encoding UTF8` 은 **BOM 을 붙인다**. JSON 설정은 `[System.IO.File]::WriteAllText(...)` +
  `UTF8Encoding($false)` 로 쓰고, Python 쪽 로더는 `encoding="utf-8-sig"` 로 읽는다(둘 다 해야 안전).
- `Get-Content -Raw` 기본 인코딩은 ANSI → JSON/한글 파일은 `-Encoding UTF8` 을 명시한다.
- git 이 없으면 MinGit(무설치)을 내려받아 쓴다. MinGit 의 ssh 래퍼는 `/usr/bin/ssh` 를 찾다 실패하므로
  `core.sshCommand` 를 Windows OpenSSH 전체 경로로 고정하고 **슬래시 경로**로 넘긴다(`C:/WINDOWS/System32/OpenSSH/ssh.exe`).
- 설치 스크립트는 **멱등**이어야 한다: clone 실패 시 deploy key 등록 명령을 출력하고 종료코드로 구분, 재실행하면 이어서 진행.
- 리허설은 전용 `-Root`(예: `C:\Users\<user>\TraderAgentTest`)로 한다. 제거 스크립트는 브리지·루프 프로세스를 죽이므로,
  라이브 스택이 있는 PC 에서 그대로 돌리면 장중 매매가 내려간다.
- 상세 스켈레톤·체크리스트: `references/windows-kit.md`

## 6. 고객 커뮤니케이션 (매일 알림)

- 장전 알림은 3단계 행동으로 쓴다: ① PC 전원 ② 브로커 로그인(사람만 가능) ③ 동기화 확인. 미완료면 "오늘 매매 시작 안 됨"을 명시.
- 피드가 오늘 것이 아니면 알림을 **"PC 켜지 마세요" 경고로 바꾼다** — 상태를 낙관적으로 알리지 않는다.
- 후보 종목명·손익은 공개 채널에 쓰지 않는다(건수·상태만). 계좌번호·비밀번호는 어떤 채널에도 남기지 않는다.
- 수신 확인(ack) 경로가 없으면 "누가 못 받았는지"를 알 수 없다. 봇 반응 집계 또는 ack 엔드포인트를 먼저 정한다.
- 알림 문구·채널 구성 절차: `references/customer-comms.md`

## 7. 배포 파이프라인 시크릿 위생

배포 토큰·서명키·DB 비밀번호가 한 번 저장소에 들어가면 공개 여부와 상관없이 이력·포크·CI 로그에 남는다.

- 커밋 전 회귀를 둔다(`tests/test_no_secrets.py` 유형): ① `.env` 실제 값이 추적 파일에 있는지 ② `*.oc`/`*.stderr*`
  같은 에이전트 출력 아티팩트가 추적되는지 ③ `os.environ.get("...PASSWORD...", "리터럴")` 기본값이 있는지 ④ 개인키 머리말.
  기본값 검사는 `PATH|HOST|PORT|USER|DB|URL|DIR` 키를 제외하고 플레이스홀더(`stock_password` 등)를 허용해야 오탐이 없다.
  개인키 머리말 검사는 테스트 자기 자신을 `:(exclude)` 로 빼지 않으면 자기 문자열을 잡아 항상 실패한다.
- **에이전트 CLI 출력 파일이 가장 흔한 유출 경로다.** `*.md.stderr.oc` 같은 덤프에 `ls -la`·`export POSTGRES_PASSWORD=...`
  가 그대로 들어간다. 종료코드·파일 목록만 보고 넘기지 말고 확장자 단위로 `.gitignore` 에 막고, 발견되면
  `git rm --cached` + **비밀번호 즉시 회전** + 포트 노출 축소를 같이 한다(이력은 자동으로 안 지워진다).
- 회전 순서: DB(`ALTER USER`) → `.env` → `docker compose up -d` 재생성. `pg_hba.conf` 에 `127.0.0.1/32 trust` 가 있으면
  localhost 검증은 비밀번호 없이도 통과해 회전 성공처럼 보인다 → 컨테이너 네트워크(`postgres:5432`) 경로로 확인한다.
- 컴포우즈 포트는 `127.0.0.1:` 를 명시한다. `"8080:8080"` 은 모든 인터페이스에 열리고 redis·neo4j·prometheus·exporter 는
  인증이 없어 LAN/테일넷에서 바로 접근된다. 바인딩 후 `ss -ltn | grep 0.0.0.0` 으로 재확인하고, 남는 것은 의도한 것만 둔다.
- `docker.sock` 마운트는 호스트 root 등가 권한이다 — CI 컨테이너에 주지 않는다. 레거시 컴포우즈에 남아 있으면
  서비스 불록을 제거하고 컨테이너도 `docker rm -f` 로 지운다(파일만 지우면 돌아가는 컨테이너는 그대로).
- 외부 보안 스캐너 결과는 **주장을 저장소에서 재확인한 뒤에만 채택**한다: `references/security-audit.md`

## 8. 완료 판정

- 실제 Windows PC(또는 동급 VM)에서 설치 1회를 끝까지 돌렸다: clone → 리허설 sync(피드 건수·코드 버전) → 예약작업 등록.
- 코드 `VERSION` 을 올려 게시 → 고객 sync → `app/version.json` 이 새 버전으로 바뀌는 것을 실측했다.
- `preflight` 가 실제 결측(브로커 미로그인 등)을 FAIL 로 잡고, 정상 항목은 OK 로 통과한다.
- `uninstall` 로 작업·프로세스·(옵션)데이터가 정리된다.
- 회귀: 교차 고객 서명 위조 차단, CRLF sha 불일치 감지, 오래된 피드 거부, 코드 스왑 실패 시 롤백.
- 보고는 "무엇이 자동이고 무엇이 사람 단계인지"를 분리해 쓴다(브로커 로그인은 항상 사람).
