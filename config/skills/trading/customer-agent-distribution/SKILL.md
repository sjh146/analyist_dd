---
name: customer-agent-distribution
description: "Use when shipping data/code to customer PCs via git."
---

# 고객 PC 배포(피드 + 코드) — git pull 경로

중앙 서버가 데이터·모델·추론을 담당하고, 고객 PC에는 실행기(trader-agent + 브로커 브리지)만 두는 구조의 배포 규칙.
전송로는 **비공개 git 저장소 하나**(피드 + 코드 + 클라이언트 스크립트)로 통일한다 — 터널·오브젝트스토리지보다
가용성·버전관리·감사가 쉽고, 고객 PC는 아웃바운드 HTTPS/SSH만 쓰면 된다.

## 저장소 레이아웃 (한 저장소에 세 층)

    deploy/latest.json        오늘의 피드(후보·국면·지수)   deploy/meta.json  sha256·content_sha256·게시시각
    deploy/history/YYYY-MM-DD.json
    app/                      실행기 코드 번들 + app/VERSION(설치 판정 기준)
    client/                   sync_feed.py·preflight.ps1·install.ps1 (클라이언트도 git으로 갱신)
    .gitattributes            `* -text`  ← 체크아웃 줄바꿈 변환 금지

## 게시(서버) 불변식

1. **게시 전 검증**: `generated_at` 신선도(기본 18h), 후보 합계 1건 이상. 실패하면 커밋하지 않고 종료코드 1 —
   고객은 fail-closed 이므로 "게시 안 됨"이 곧 "오늘 거래 없음"이다.
2. **멱등**: 내용 해시(`content_sha256`, `published_at` 제외)를 meta에 남기고 같으면 커밋을 건너뛴다.
   게시 시각을 매번 갱신하면 하루에도 수십 커밋이 쌓인다.
3. `latest.json`은 tmp 파일 + `os.replace`로 원자 교체(읽는 쪽이 반쪽 JSON을 보지 않게).
4. 서버가 게시에 실패해도 호스트 크론 백업 경로를 하나 남긴다(멱등이므로 중복 실행이 안전).

## 고객측 동기화 불변식

1. **`git pull` 대신 `fetch --depth 1` + `reset --hard FETCH_HEAD`**. 고객 PC는 읽기 전용 deploy key만 갖고
   커밋하지 않으므로 병합·충돌 자체를 만들지 않는다.
2. **줄바꿈**: Windows 체크아웃이 CRLF로 바꾸면 `meta.sha256`(바이트 기준)와 불일치한다.
   `core.autocrlf false` + `* -text`를 먼저 적용하고, 이미 CRLF로 받아둔 트리는 `git rm --cached -r . && git reset --hard`
   로 재체크아웃한다. `deploy/meta.json`에 `\r`이 있으면 자동 복구하도록 클라이언트에 넣는다.
3. 피드 검증: sha256 → `feed_schema_version` → 신선도. 하나라도 실패하면 **피드 파일을 건드리지 않고** 종료코드 1.
4. 코드 교체: `app/VERSION` 비교 → 스테이징 복사 → 필수 파일 확인 → 백업 → 스왑 → `version.json` 기록.
   실패 시 자동 롤백. **보존 목록**(`config_live.json`, `logs/`, `journal/`, `data/`, `kill_switch.txt`)은 덮어쓰지 않는다.
5. 백업 디렉터리는 설치 디렉터리 **밖**에 만든다(안에 만들면 다음 교체에서 고객 데이터가 같이 지워진다).
6. 업데이트는 장외 시각(장전 또는 마감 후)에만. 장중 자동 업데이트는 만들지 않는다.

## 서명 — 고객별 파생 키 (절대 공유 키를 고객에게 주지 말 것)

공유 HMAC 서명키를 고객 config에 넣으면 **고객 1명이 전 고객용 악성 업데이트를 서명**할 수 있다(설계 결함).

- 마스터 시크릿은 서버에만(`data/feed_relay/release_key.txt`, gitignore, 0600).
- 고객 키 = `HMAC-SHA256(master, "release:<customer_id>")`. 고객 config에는 이 파생 키만 넣는다.
- manifest는 **고객별로 서버가 서명**해 내려준다: `/v1/manifest?channel=&customer_id=` 는 Bearer 토큰 필수.
- 검증 테스트를 둘 것: 고객 A의 키로 서명된 manifest를 고객 B 키로 검증하면 **False**, 토큰 없이 요청하면 **401**.
- git 경로는 저장소 읽기 권한이 곧 신뢰다(파일별 sha는 meta로 확인). 릴레이 경로와 git 경로의 신뢰 모델을 문서에 구분해 적는다.

## Windows 설치 키트 — PowerShell 5.1 함정

- **`.ps1`은 UTF-8 BOM으로 저장**한다. BOM이 없으면 5.1이 CP949로 읽어 한국어 문자열에서 파서 오류가 터진다.
- `Get-Content -Raw`는 UTF-8을 ANSI로 읽는다 → 항상 `-Encoding UTF8`. JSON 파싱 실패의 절반이 이것이다.
- `Set-Content -Encoding UTF8`은 **BOM을 붙여** config.json을 망친다 → `[System.IO.File]::WriteAllText($path,$json,(New-Object System.Text.UTF8Encoding($false)))`.
  파이썬 로더는 `encoding="utf-8-sig"`로 읽게 한다(둘 다 방어).
- PS 5.1은 네이티브 명령에 빈 문자열 인자를 넘기지 못한다(`ssh-keygen -N ""`이 사라져 실패) →
  `Start-Process -ArgumentList '-q -t ed25519 -N "" -C "..." -f "..."' -Wait -NoNewWindow` 로 넘긴다.
- **MinGit에는 OpenSSH가 없다**(`/usr/bin/ssh: Permission denied`) → `core.sshCommand`에
  `C:/WINDOWS/System32/OpenSSH/ssh.exe` 같은 **절대경로+정방향 슬래시**로 지정한다. 역슬래시는 셸을 거치며 깨진다.
- 설치 스크립트는 멱등해야 한다(deploy key 미등록으로 clone 실패 → 등록 후 재실행하면 이어서 진행).
  리허설 1회(sync)를 설치 단계에 넣어 실패 지점을 설치 시점에 노출한다.
- 제거 스크립트의 프로세스 종료는 **설치 경로로 스코프**한다(`CommandLine -like "*$Root*"`).
  `*run_bridge*` 같은 이름 매칭은 개발 PC에서 실제 운영 브리지까지 죽인다.

## Jenkins를 게시기로 쓸 때

- 잡 정의에 JCasC `jobs:`를 쓰려면 job-dsl 플러그인이 필요하다 — 없으면 `No configurator found for root element: jobs`로 부팅 실패.
  대신 `init.groovy.d/create_jobs.groovy`로 `WorkflowJob`을 만든다(파이프라인 스크립트는 `readFileFromWorkspace`로 읽어 재기동 시 갱신).
- `options { timestamps() }`는 timestamper 플러그인 필요 → 없으면 파이프라인 컴파일 실패.
- `sh` 스텝은 작업 디렉터리의 **형제** `<dir>@tmp`를 만든다 → 마운트한 경로의 **부모**가 jenkins uid로 쓰기 가능해야 한다
  (Dockerfile에서 `mkdir -p /work && chown jenkins /work`).
- 파이프라인은 컨테이너 안에서 돌므로 **python3를 이미지에 넣는다**(`FROM jenkins/jenkins:lts-jdk17` + `apt-get install python3`).
- 파라미터 잡은 `/build`가 아니라 **`/buildWithParameters`** + crumb + 쿠키로 트리거한다(400/403의 원인).
- `set -u` + 빈 환경변수 조합은 실패한다 → 분기 값은 `withEnv`로 넘기거나 Groovy 문자열에서 조건부로 조립한다.
- 플러그인 다운로드가 수십 KB/s로 느릴 수 있다(수십 분) — 부트스트랩 스크립트로 분리하고 진행을 로그에 남긴다.

## 설치 후 검증(고객 PC)

1. `preflight`: config·git·repo HEAD·피드 신선도·sync_state·코드 버전(설치본=저장소)·브리지 헬스·디스크, 실패 항목만 차단 사유로 출력.
2. 서버에서 `VERSION`을 올려 게시 → 고객 PC에서 sync 1회 실행 → `app/version.json`이 새 버전인지 확인(로컬 config 보존도 함께 확인).
3. 예약작업(장전 sync/개장 루프/마감 후)이 의도한 시각에 등록됐는지 `schtasks /Query`로 확인.
