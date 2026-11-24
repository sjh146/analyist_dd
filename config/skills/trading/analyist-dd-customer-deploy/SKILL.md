---
name: analyist-dd-customer-deploy
description: Use when shipping feed or code to customer PCs.
---

# 고객 배포 체인 (서버 → GitHub → 고객 PC)

## 구조

    서버(analyist_dd) 08:20~08:40 추론·게시
      → 비공개 GitHub sjh146/trader-feed
           deploy/latest.json + meta.json (피드, sha256/content_sha256)
           app/VERSION + trader-agent 코드
           client/ (sync_feed.py, install.ps1, preflight.ps1, uninstall.ps1)
      → 고객 Windows PC 08:45 sync_feed.bat (읽기 전용 deploy key)
    Jenkins(deploy/jenkins, 127.0.0.1:8081) 08:40 feed-publish: 피드 게시(+VERSION 지정 시 코드)
    호스트 크론(feed_publish.sh, 08:40/20:30) 피드 발행 직후 publish_feed_github.py 를 **같은 스크립트에서** 호출

## 불변 규칙

- **발행(피드 파일 생성)과 게시(GitHub push)는 다른 단계다.** 로컬 `data/feed/screener_latest.json` 이
  새로 씌워졌다고 고객이 받는 것이 아니다. 게시 호출이 발행 잡에 배선돼 있지 않으면 피드는 서버에서만
  갱신되고 원격은 며칠씩 정지한다.
- **게시 성공 판정은 원격 head 로만 한다**: `git -C <deploy repo> fetch -q && git log origin/main -1 --format='%h %ad %s'`
  의 커밋 시각이 피드 `generated_at` 과 같은 영업일인지 확인. 로컬 `git log` 를 보면 커밋 전 상태를
  성공으로 오독한다.
- 발행 스크립트를 `exec <python>` 으로 끝내면 뒤 단계를 붙일 수 없다 → `exec` 를 풀고
  `RC=$?` 보존 후 게시를 붙이고 `exit $RC` 로 끝낸다(발행 성공·게시 실패를 구분해 로그에 남긴다).
- 고객 수신 검증 절차: 고객 사본에서 `git fetch --depth 1 origin main && git reset --hard FETCH_HEAD` →
  `git log -1 --format='%h %s'` · `sha256sum deploy/latest.json` · 후보 수를 서버 값과 대조한다.
  해시가 다르면 오래된 클론·다른 브랜치(master vs main)·CRLF 체크아웃을 순서대로 의심한다.
- 상세: `references/deploy-key-and-ack.md` — 읽기 전용 deploy key 발급·검증·회수(`gh api`, 고객 GitHub 계정 불필요),
  ack 체인(`POST /v1/ack` → `feed_ack_check` rc 0/1/2, 08:58 크론), 고정 공개 주소(Tailscale Funnel)와
  relay/Funnel 감시 방법.
- **고객에게 "매일 갱신된다"고 말하기 전에 서버 쪽 3단을 확인한다**: ① 게시 크론이 오늘 커밋을 만들었는지(원격 head),
  ② relay(8095)가 응답하는지, ③ 고객이 쓰는 ack/피드 주소가 살아있는지. 하나라도 죽으면 고객은 에러 없이
  **옛 피드로 거래**한다 — 로컬 피드 파일이 새것인지만 보면 이 상태를 놓친다.

- 고객 PC는 저장소에 커밋하지 않는다 → `pull` 대신 `fetch + reset --hard`.
- 피드 검증 실패 시 **거래하지 않는다**(fail-closed).
- 코드 교체는 스테이징→검증→백업→스왑, 실패 시 롤백. `config_live.json`·`logs/`·`journal/`·`kill_switch.txt` 보존.
- 서명은 고객별 파생 키 `HMAC(master, "release:<id>")`. 공유 키 금지. `/v1/manifest` 는 토큰 필수.

## 개장 게이트

`DAYTRADING_OPEN_GATE_PCT=0.5` → 09:10 파이프라인이 KIS 코스닥 전일대비율 조회,
미달·판정불가면 `--regime-block on` 으로 차단하고 `feed_clear_daytrading.py` 로 피드 단타 목록을 비운다.
결과는 `data/state/kosdaq_open_gate.json`.

## 함정

- **피드 데이터는 analyist_dd 저장소에 없다**(git 비추적). 고객이 pull 하는 저장소는 `trader-feed` 다 —
  "analyist_dd 클론해뒀다"는 말을 확인 없이 수신 검증으로 받아들이면 엉뚱한 저장소를 뒤진다.
- **고객 PC에는 `trader-feed` 하나만 둔다.** 그 안에 데이터(`deploy/`)와 실행 코드(`app/`)가 같이 있으므로
  `trader-agent`(개발 리포)를 함께 clone 하면 코드가 두 벌 생기고 어느 쪽이 도는지 불분명해진다. 고객에게는
  "clone 할 것 1개 + deploy key 1개 + 설치 스크립트 + 크레온 로그인" 네 가지로만 안내한다.
- **저장소 공개 여부는 알파 노출로 판단한다**(기본 = 비공개). `deploy/latest.json` 은 매일 상위 후보·점수·근거를
  담고 `deploy/history/*` 가 누적되므로, 공개하면 후보를 따라 사는 쪽이 체결가를 나쁘게 만든다. 공개가 정말
  필요하면 **`client/`(설치 키트)만 공개**하고 피드는 비공개 경로에 둔다. 공개 전환 검토 시 먼저 이 둘을 본다:
  ① 하드코딩된 키·계좌번호가 있는가(`grep -rlE 'ghp_|sk-|AKIA|[0-9]{8}-01'`) ② 히스토리 파일이 전략 파라미터를
  역산할 만큼 쌓였는가. 비공개면 같은 실수가 급성 사고가 되지 않는다는 점도 유지 이유다.
- Jenkins·크레온처럼 로그인·세션이 필요한 컴포넌트를 게시의 유일 경로로 두지 않는다. 호스트 크론에서
  직접 게시하는 백업 경로를 항상 함께 돌리고, 두 경로가 같은 커밋을 만들지(중복 unchanged) 확인한다.
- `.ps1` 은 UTF-8 BOM 으로 저장(PS 5.1 한글 오독 방지).
- Windows CRLF 체크아웃은 피드 sha256 을 깬다 → `.gitattributes(* -text)` + `core.autocrlf=false`
  + 필요 시 `git rm --cached -r . && git reset --hard`.
- MinGit 은 `core.sshCommand` 에 `C:/WINDOWS/System32/OpenSSH/ssh.exe` 절대경로를 줘야 한다.
- PS `Set-Content -Encoding UTF8` 은 BOM 을 붙인다 → `File.WriteAllText`+`UTF8Encoding($false)`, 로더 `utf-8-sig`.
- `/etc/cron.d` 는 sudo 없이 `docker run --rm -v /etc/cron.d:/c alpine sed -i`(백업 후).
- 트레이더 repo pre-commit lint 는 `docs/lint_baseline.json` 키를 접미사로 매칭해야 막히지 않는다.

## 데이터 판단

- 미국장 등락률은 반드시 마감(일봉) 두 종가로 계산한다 — 장중 스냅샷을 마감으로 오독한 사고 있음.
- KIS: `EGW00103`=AppKey 무효, `EGW00105`=AppKey 유효·AppSecret 불일치. 시크릿은 k/K 같은
  대소문자까지 전수 검사(실측 131+146회).
- KRX 지수·파생 원천은 D-2 반영 — 당일 0행이 정상.
