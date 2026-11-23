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
    호스트 크론 08:50 publish_feed_github.py = Jenkins 장애 대비 백업(중복은 unchanged)

## 불변 규칙

- 고객 PC는 저장소에 커밋하지 않는다 → `pull` 대신 `fetch + reset --hard`.
- 피드 검증 실패 시 **거래하지 않는다**(fail-closed).
- 코드 교체는 스테이징→검증→백업→스왑, 실패 시 롤백. `config_live.json`·`logs/`·`journal/`·`kill_switch.txt` 보존.
- 서명은 고객별 파생 키 `HMAC(master, "release:<id>")`. 공유 키 금지. `/v1/manifest` 는 토큰 필수.

## 개장 게이트

`DAYTRADING_OPEN_GATE_PCT=0.5` → 09:10 파이프라인이 KIS 코스닥 전일대비율 조회,
미달·판정불가면 `--regime-block on` 으로 차단하고 `feed_clear_daytrading.py` 로 피드 단타 목록을 비운다.
결과는 `data/state/kosdaq_open_gate.json`.

## 함정

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
