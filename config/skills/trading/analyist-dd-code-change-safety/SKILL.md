---
name: analyist-dd-code-change-safety
description: "Use when editing code in the analyist_dd stack."
---

# analyist_dd / trader-agent 코드 변경 안전 절차

대상: 이 두 리포에서 **코드를 고치거나 고치게 하는** 일 전부 — 분석·측정 스크립트, 피드 계약, 트레이더 실주문 경로.
리포트 작성은 `trading-stack-status-report`, 스택 구축·수집 함정은 `analyist-dd-stack-ops`(사용자 소유) 를 본다.
좁은 손절/익절 라인의 판정·분봉 재생·자본 제약 시뮬은 `references/intraday-measurement.md`.
스크리너 파이프라인 지연 최적화(종목별 top-N 조회·사전 필터·배치 예측)는 `references/pipeline-latency.md`.
죽은 피처 진단·부활(패널 재빌드 → 종목 연결/커버리지 → IC 성능 게이트)은 `references/feature-revival.md`.

## Rule 0 — 고치기 전에 어느 리포·어느 경로인지 실측한다

- **리포 지배 구조를 기억으로 단정하지 마라 — 사본마다 실측한다.** 실측 한 줄:
  `cd <사본>/trader-agent && git rev-parse --show-toplevel && (ls -d .git >/dev/null 2>&1 || echo no-nested-git) && git remote -v && git branch --show-current`.
  실측 예: Windows 사본(`C:/Users/<user>/analyist_dd/trader-agent`)은 **중첩 리포(`trader-agent.git`, `main`)** 이고
  라이브 루프가 실행하는 코드이며, WSL 사본의 `trader-agent/` 는 **`.git` 없는 스냅샷**(상위 analyist_dd 에 포함)이라
  그쪽에서 import·pytest 하면 옛 코드가 잡힌다(신규 심볼이 `ImportError` 나면 그 사본이 낡은 것이다).
  트레이더 동작을 바꾸는 커밋은 **라이브가 실행하는 사본**에서 하고, 아니면 `scripts/sync_trader_agent.py` 로 동기화한다
  (→ `references/portability-and-sync.md`). 경로는 `pwd`·`ls <그 리포 고유 파일>` 로 확인한다.
- `scripts/ask_claude.sh`·`scripts/protected_paths.py` 는 **WSL 작업본에만** 있고 trader-agent 에는 `docs/` 조차 없다 → 위임 스펙 파일은
  그 리포 안에 만들어야 하고 필요하면 `mkdir -p docs` 를 먼저 한다.
- **`cd` 실패는 조용히 다른 리포에서 실행된다**: `cd <A> ; cmd1 ; cmd2` 처럼 `&&` 없이 나열하면 `cd` 가 실패해도 계속 돌아
  **엉뚱한 리포에 파일·디렉터리를 만든다**. 위임·편집은 `cd <절대경로> && <명령>` 으로 묶고 첫 명령을 `pwd`/`ls` 로 둔다.
- **`.env` 의 DB 값은 컨테이너 내부 기준이다** (`POSTGRES_HOST=postgres`, `POSTGRES_PORT=5432`).
  WSL 호스트에서 스크립트를 돌릴 때는 `POSTGRES_HOST=127.0.0.1` `POSTGRES_PORT=5434`(docker 노출 포트)로
  **덮어써야** 한다. 그대로 쓰면 `could not translate host name "postgres"` 또는 `connection ... 5432 failed`
  로 **후보/행 0건**이 되고, 증상은 스크립트 결함처럼 보인다(실측 2026-11-17). 호출 전 `docker port stock_postgres`
  로 노출 포트를 확인하고, 이 값을 크론 래퍼 안에서 export 한다.

## 피드 계약 변경 (그 피드는 실주문 경로가 소비한다)

1. 백업 → 재발행 → **키별 JSON 동일성 비교**로 기존 키 불변을 증명한 뒤 배포한다:
   `cp data/feed/screener_latest.json /tmp/feed_before.json` … `a['candidates'][k] == b['candidates'][k]`.
2. 새 전략 키의 점수 스케일이 다르면 `score_kind` 를 달리 기록하고 기존 키의 확률 변환 규약을 재사용하지 않는다(모델확률은 별도 필드로 보존).
3. 발행기는 `reports/*_latest.json` 을 읽어 합치므로 **고쳐도 다음 발행 시각**에야 반영된다.
4. 어느 쪽 문제인지 분리한다: **발행 측 건수**(키별 items)와 **소비 측 기록**(트레이더 저널 `decisions` 의 screener별 행수)을 함께 인용한다.
   소비가 0행이면 스키마 수용 여부를 의심하되 **거래일 여부를 먼저 확정**한다(휴장이면 정상).
5. **후보 항목에 새 필드를 더할 때는 발행기의 extras 화이트리스트를 같이 늘려라.** `feed_export` 는 item 을
   명시된 키만 복사해 만들기 때문에(그 뒤 `for extra in (...)` 목록), 소스 JSON 에 값을 넣어도 목록에 없으면
   **조용히 사라진다**. 반대 방향의 함정도 있다: 보강 쿼리가 실패해도 발행은 fail-open 으로 계속되므로
   (`edge_score: null, edge_reason: "db_error"`) 로그만 보면 성공처럼 보인다 → 발행 후 **`edge_scored=N/M` 같은
   커버리지 카운터를 후보 수와 비교**해야 한다(실측 0/60 → 원인은 `ORDER BY` 가 SELECT 목록에 없는 컬럼을 참조해
   매번 SQL 오류).

- 상세: `references/live-order-path.md` — 실주문 경로 변경 (`trader_core/engine.py`·`config.py`·한도)

## 실행 중인 잡을 멈춰야 할 때 (업데이트·회선 경합·오배포)

- 크론이 띄운 파이프라인은 **root 소유**라 일반 사용자 `kill` 이 `Operation not permitted` 로 실패한다 →
  `/mnt/c/Windows/System32/wsl.exe -u root -- kill -TERM <pid> <child>` 로 보낸다(인터롭이 살아 있을 때).
- 컨테이너 안에서 도는 자식(`docker exec ... python3`)은 부모만 죽여도 계속 돈다. 그 이미지에는 `ps`·`pkill`·`pgrep` 이
  **없는 경우가 흔하다**(`exec: "pkill": executable file not found`) → `/proc` 을 직접 열거한다:
  `docker exec <c> sh -c 'grep -la <스크립트명> /proc/[0-9]*/cmdline'` 로 pid 를 얻고 그 pid 로 kill 한다
  (자기 자신의 grep 도 목록에 잡히므로 결과 1건만 나오면 미종료로 오독하지 않는다).
- 정지 뒤에는 ① 부모·자식·컨테이너 프로세스 셋 다 사라졌는지, ② 컨테이너 자체는 Up 인지(서비스는 살려 둔다),
  ③ 진행 파일(부분 다운로드·백필 진행 마커)을 **건드리지 않았는지**를 실측해 보고에 붙인다.
- 멈춘 잡은 **반드시 다시 돌린다**: 재실행 명령과 산출 로그 경로(`reports/full_pipeline_dd_<ts>.log`)를 보고에 적고,
  재실행이 Phase 1 부터 다시 시작해도 되는 잡인지 확인한다(진행 마커가 있으면 이어받는다).

## 운영 데이터 삭제·되돌리기 (미추적이어도 실데이터다)

- **`rm -rf <디렉터리>` 전에 그 안을 열거하고 생성 주체를 찾는다**: `find <dir> -maxdepth 2 | head`, `grep -rn "<파일명 패턴>" --include=*.py`.
  `trader-agent/data/fills/fills_YYYY-MM-DD.json` 처럼 `git status` 에 `??` 로 보이는 파일도 **저널에서 파생된 운영 산출물**이다.
- 지웠다면 파생이므로 **생성기로 재생성해 내용을 검증**한다(예: `tools/export_fills.py --date <일>` → `청산 N건 · 실현 …`). 파생 원본이 당일만 제공되는 데이터면 복구 불가다.
- **`git checkout -- .` 를 쓰지 마라** — 파이프라인 상태 churn 파일 수십 개를 한꺼번에 되돌린다. 되돌릴 파일을 열거해 **개별 경로**로 지정하고,
  되돌리기 전에 `git status --porcelain` 으로 미추적 산출물이 사라지지 않는지 확인한다.

## 개선 큐·보호경로 (자동화에 넘길 때)

- **승인받은 백로그 항목을 실행하기 전에 그 항목의 `check` 를 다시 돌려라 — 등록 시점의 진단이 이미 낡았을 수 있다.**
  승인은 "이 처방을 해도 된다"는 뜻이고 "이 결손이 아직 있다"는 뜻이 아니다. 실측: 미해결 3원천을 한 번에 풀려던
  항목이 실제 재측정에서 3건 중 2건이 이미 해소돼 있었고(다른 경로가 먼저 고침), 남은 1건도 원래 처방(KIS 종목당 1콜
  ≈2.7시간)보다 싼 경로(키 불필요 API 103콜)가 있어 **쿼터 결정 자체가 불필요**해졌다. 순서: ① 항목의 `check`/`check_target`
  명령을 그대로 실행 ② 미달이 아니면 근거 수치와 함께 `done` 으로 닫는다 ③ 미달이면 처방이 다른 항목과 충돌하는지
  확인(실측: "빌더 로드창을 120거래일로 고정" 처방이 12개월 모멘텀을 살리려고 로드창을 분리해 둔 다른 항목과 정면 충돌)
  ④ 충돌하면 코드 주석에 적힌 **왜 그렇게 되어 있는지**를 근거로 처방을 바꿔 제안하고 결정 문서를 남긴다.
  승인 배치를 받았다고 처방을 그대로 기계적으로 실행하면 불필요한 호출·위험한 변경을 만든다.

- 등록: `python3 scripts/improve_queue.py add --title <…> --why <…> --acceptance <…> --priority <정수> [--protected]`.
  `--priority` 는 **정수**다(`high`/`medium` 문자열은 argparse 오류로 전량 미등록). 큐 파일 구조는 `{"updated","tasks"}` 이므로 `items` 키로 읽으면 **0건으로 오독**한다 → 확인은 `git show --stat HEAD` 의 증가 줄수로 한다.
- `trader-agent/**`·`config/objective.json`·`.env`·`kill_switch.txt` 등 **보호경로를 건드는 과제는 `--protected`** 로 넣는다(자동 병합 대신 승인패킷/needs_human).
  보호경로 단일 진실원은 `scripts/protected_paths.py`(`violation()`/`is_protected()`)이고 저작·위임 경로는 **fail-closed** 다.
- `--why` 에는 실측 근거(행수·시각·로그 문구), `--acceptance` 에는 검증 명령과 기대 변화(실패→통과)를 쓴다. 같은 손실원의 중복은 1건으로 합친다.

## 저널·산출물을 읽는 계측 스크립트 (읽기 전용)

- **실주문 저널을 `mode=ro` 로 직접 열지 마라 — drvfs+WAL 이라 `disk I/O error`** (2026-11-17 실측:
  `/mnt/c/.../trader-agent/journal/trade_journal.sqlite3` → `mode=ro` 실패, `mode=ro&nolock=1` 실패,
  **`immutable=1` 만 성공**). 리포 관용구(`trader_cycle._journal_copy`)처럼 `.sqlite3`/`-wal`/`-shm` 을 임시
  디렉터리로 복사한 뒤 복사본을 `mode=ro` 로 열고 끝나면 임시 디렉터리를 지운다(원본 쓰기 0).
- 저널 `trades.pnl` 은 **이미 비용 차감 후 순손익**이다(실측 id=11: (34050−36642.5)×2−146 = −5331 = pnl; 청산 11건 전부 pnl = gross − fees).
  `fees` 를 또 빼면 이중 차감 — `money_scoreboard` 가 `net_pnl = pnl − fees` 로 손실을 146원 과대계상하고 있었다(2026-11-17 정정).
  진입금액은 `order_resp_json` 주문가가 아니라 **`price`(체결 VWAP)×`qty`** 다(3×24200=72,600 ≠ 실측 3×24103.3333=72,310).
- **한도 숫자를 손으로 쓰지 마라.** 정본은 `trader-agent/trader_core/config.py` Config 이고,
  `scripts/risk_limits.py`(`LIMITS`, `LIMITS_META['source'|'loaded']`)가 그것을 읽는 단일 입구다
  (기준: 일 3건·종목 1/3·동시 3·총 100%). `loaded=False` 면 폴백(10%/30%)이므로 그 보고는 '측정 불가'로 쓴다.
  문서·스킬에 낡은 10%/30% 가 다시 들어가면 계기판·감사와 또 갈라진다.
- **일 손실한도는 당일 실현(`realized.today.pnl`)으로만 판정한다.** 누적 실현을 일 한도와 비교하는 것은
  범주 오류다(2026-11-16 오보). 계기판이 `today` 를 주지 않으면 '판정 불가'로 적고 0·누적으로 메우지 않는다.
- **브로커 대조(reconcile)가 체결가를 정정하면 파생 지표를 전부 다시 돌린다.** 저널의 `pnl` 이 바뀌면
  계기판·재투입 원장·`objective_state` 가 그 순간부터 낡은 값이 된다 — 재계산 후 보고에는 **정정 전/후 수치를 함께**
  쓰고(예: 당일 −6,831 → −7,128), 계약 문서(스펙·md)의 수치도 같이 고쳐 둔다. 정정 사실을 모른 채 다음 보고에
  옛 숫자를 넣으면 그게 바로 사용자가 잡아내는 오보가 된다.
- 포지션은 `side='buy'` 행 하나이고 청산 시 같은 행의 `exit_ts/exit_price/exit_reason/pnl` 이 채워진다.
  `exit_reason` 이 `unfilled:`·`reconcile: broker flat` 로 시작하는 행은 포지션이 아니므로 계측에서 제외하고 건수를 남긴다.
- 피드 날짜는 저널에 **기록되지 않는다**(algo_json 의 `version` 은 config 버전). 과거 진입의 피드 asof 는 복구 불가이므로
  `null`+사유로 남기고, 지금부터 `generated_at` 관측을 누적(`data/reports/feed_asof_history.jsonl`, 같은 값 중복 금지)해
  이후 실행에서 "진입 ts 이전 최근 관측"으로 해소한다 — 현재값을 과거에 소급 대입하지 않는다.

- **피처 단위 집계 전에 컬럼 이름 중복을 먼저 센다.** 학습 패널(`services/xgboost-ml/app/models/wf/panel_*.npz`)은
  `X`/`feature_names` 를 따로 갖는데, 213컬럼 중 고유 이름이 199개인 사례가 있다 — 이름이 같은 14쌍이 **값까지 동일**해
  피처 중요도가 쪼개지고 피처 수가 부풀려진다. 이름을 dict 키로 쓰는 집계는 마지막 컬럼이 앞 컬럼을 덮어써
  조용히 틀린다 → `len(names) vs len(set(names))` 를 먼저 찍고, 중복이 있으면 인덱스 기준으로 다룬다.
- **피처 생존은 한 축으로 판정하지 마라 — 분산·커버리지·누수 3축.** `nonzero_ratio` 하나로는
  "값이 있지만 전부 0"(죽음)과 "값이 드물게 있음"(희소)과 "결측을 0으로 세는 착시"가 구분되지 않는다.
  비영률은 **분모를 밝혀** 쓴다(전체 행 기준 = 패널 커버리지, 유효값 기준 = 조건부 밀도; 둘 다 기록).
  누수는 ① 미래 h일 라벨과의 상관(진짜 신호) ② 당일 수익률과의 동시상관(가격 파생 피처는 **정의상 1에 가깝다** —
  룩어헤드로 오독 금지) 로 나누고, 판정 보류·표본부족을 명시한다. 판정기 예: `scripts/feature_survival_report.py`.

## 크론·배치 배선 검증 ("등록했다" != "돈다")

- crontab 에 줄을 넣었을 뿐이면 검증이 아니다. **임시 라인으로 실제 발화를 증명한다**:
  `( crontab -l; printf '%s %s * * * <명령> >> /tmp/cron_probe.log 2>&1\n' <M> <H> ) | crontab -`
  (2분 뒤 시각) → 대기 → `ls -la /tmp/cron_probe.log` 와 **산출물 mtime 변화**를 함께 확인 →
  `crontab -l | grep -v <임시 라인>` 으로 **반드시 제거**하고 잔존 0건을 확인한다.
  `--quiet` 옵션이 있으면 로그는 0바이트가 정상이므로 **파일 생성·산출물 mtime** 으로 판정한다.
- 배치 래퍼는 환경을 스스로 만든다: `set -a; . ./.env; set +a` 후 호스트에서 필요한 값
  (`POSTGRES_HOST=127.0.0.1`, `POSTGRES_PORT` = `docker port` 노출값)을 **덮어쓴다**. 크론 로그 첫 줄에
  실행 여부·건수를 남겨 나중에 로그만 보고 판정할 수 있게 한다.
- **인자 요구·날짜 규칙을 지켜라 — 어기면 배치가 조용히 0건으로 '성공'한다.** 분봉 백필은 `--from/--to` 가
  **필수**인데 크론 줄에 빠져 매일 argparse 오류로 죽었고, `--from == --to` 도 거부한다(엄격한 `<`). crontab 에는
  `$(date +\%F)`·`$(date -d '-7 days' +\%F)` 처럼 **`%` 를 이스케이프**하고, 중첩 따옴표(`date -d \"7 days ago\"`)는
  sh 에서 리터럴 따옴표로 들어가 실패하므로 **작은따옴표**를 쓴다. 등록 후 그 줄을 `sh -c` 로 그대로 실행해
  날짜 확장 결과(`--from 2026-11-11 --to 2026-11-18`)까지 확인한다.
- **백필 래퍼의 프로젝트 경로를 고정하지 마라 — 홈이 둘인 머신에서는 진행파일 쓰기 실패로 절반이 죽는다.**
  스크립트가 `PROJ = os.environ.get("PROJ_DIR", "/home/<옛 사용자>/analyist_dd")` 처럼 **다른 홈을 기본값**으로
  들고 있으면, 진행 마커 파일 소유권이 어깻날 때 `PermissionError` 로 **호출당 한 건씩** 실패하고도 요약은
  정상처럼 찍힌다(실측: 호출 500건 중 250건 실패 · 로그에 같은 파일명만 250줄 · 적재 요약은 정상 출력).
  크론 래퍼에 `PROJ_DIR=<현재 리포 절대경로>` 를 export 하고, 실행 전에 `ls -ld <진행파일 디렉터리>` 로
  쓰기 가능 여부를 확인한다. 판정은 ``ok=N fail=M`` 의 M>0 여부이며, M>0 이면 성공 요약을 믿지 않는다.
- **수집 크론 시각이 벤더 게시 전이면 그 러너는 영원히 T-1 을 받는다.** 스크립트를 고쳐도 안 되고 **시각을 옮기는 것**이
  무비용 레버다(실측: 16:20 수급 크론이 항상 전일 → 커버리지 0.09%). 앞 단계 산출물에 의존하면 그 뒤로 옮긴다
  (예: 일봉 적재 18:55 → 19:10). `/etc/cron.d/*` 는 root 소유라 `wsl.exe -u root -- cp <새파일> <대상>` 로 바꾸고
  `ls -l` 로 `root:root 644` 를 확인한 뒤, **다음 실행 시점의 as-of 가 당일인지**를 검증 계획으로 남긴다.
- **컨테이너 phase 는 리포의 자기신고 헬퍼를 못 쓴다 — 호스트에서 행 델타를 재서 대신 남겨라.**
  `docker exec` 로 도는 수집 단계는 `dq_claim` 배선이 없어 실패해도 `dq_runner_claim` 에 흔적이 없다.
  처방: phase 전/후 `docker exec stock_postgres psql -tAc "select count(*) from <table>"` 델타를 계산해
  `record_claim(..., claimed_rows=델타, persisted_rows=델타, source_rows=None, note="rc=.. count a->b")` 로 기록한다.
  **`source_rows` 는 NULL 로 둬라** — 소스 행수를 모르는 상태에서 0 을 넣으면 `parse_failure`(source>0 & claimed==0) 오탐이 울린다.
  자기신고 헬퍼는 무슨 일이 있어도 `exit 0`(수집을 깨면 안 된다)이고, 실DB에 테스트 runner 로 1행 왕복 후 삭제해 검증한다.
- **"신호 기반 기본 대상" 배치는 신호 없는 날 0건으로 통과한다.** 백필 스크립트의 기본 대상이 capital_sim
  신호 종목이면 신호가 없는 날 `codes=0, rows_written=0` + rc=0 이라 로그만 보면 성공이다 → 당일 처리 대상
  (피드 후보 + 저널 체결)을 파일로 만들어 `--codes-file` 로 넘기고, 적재 후
  `select count(*), count(distinct stock_code) from <table> where trade_date=<날짜>` 로 **행 수를 확인**한다.

## 외부 신호(랭킹·괴리)를 후보 선별에 끌어들일 때

- **화이트리스트(교집합)가 "압축"인지 먼저 재라.** 외부 랭킹 ∩ 자체 스코어는 기준이 직교하면
  후보를 **대체**한다 — 실측: 상승률 상위 60코드 ∩ 칼만/모델 상위 20건 = **교집합 0건**(점수 55.1 vs 65.5).
  배포 전에 `교집합 수 · 점수 분포 · 후보 0건 여부`를 리포트로 남기고, 실사용은 ⓐ 랭킹 상단은
  **배제 필터**(후보 제거) ⓑ 대상 대역은 **정렬 전용 가점**(공개 `score` 불변 — 소비자 문턱 의미 보존)으로
  설계한다. 압축을 그대로 켜면 그날 후보가 0건이 될 수 있다.
- **외부 API 파라미터 타입·인코딩을 1회 실호출로 확인하라.** 같은 계열도 타입을 엄격히 본다
  (실측: `pct_from=2.0` → `invalid_input: pct_from must be an integer`; 네이버 ETF 목록은 **EUC-KR**).
  어댑터에서 정수화·디코딩을 흡수하고, 실패는 **fail-open + 사유 기록**으로 두어 크론 체인이 끊기지 않게 한다.
- **장중 계측을 장외 스냅샷으로 검증하지 마라.** 마감 후 NAV·괴리는 시차·결제 때문에 실제와 동떨어진다
  (실측: 합성 해외 ETF +3.76%/−2.18% — 장중 값이 아니다). 수집기는 `--window` 로 장중만 돌리고,
  분석은 국내형과 해외·합성형을 분리 집계한다.
- **시간 한도는 루프 주기만큼 늦게 집행된다(실측 30분 규칙 → 5분 주기에서 34.6분, 1분 주기 30.2분).**
  보유 한도를 바꿀 때는 주기 구간별 실제 집행 지연을 같이 적고, "그 시간 안에 변동이 왕복비용(약 0.21%)을
  넘는가"를 먼저 잰다 — 실측 단타 2건은 +0.04%/−0.07%로 비용 미달(수수료 비중 5.4%)이었다.

- 상세: `references/regime-gate.md` — 국면(regime) 연동 게이트를 넣을 때

## 청산 규칙 변경 (트레일링·시한부)

- **청산 판정은 두 경로에 있다 — 둘 다 고친다.** 사이클 판정 `trader_core/monitor.py::_trailing_verdict` 와
  초 단위 틱 감시 `trader_core/tickwatch.py::tick_exit_candidates` 가 각자 arm/trail 을 계산한다. 한쪽만 바꾸면
  **먼저 발동하는 쪽(틱)** 이 옛 규칙으로 청산한다. 회귀 테스트도 둘(`test_monitor.py`, `test_tickwatch.py`)에 각각 둔다.
- **전역 트레일링이 사실상 죽어 있는지 먼저 본다.** 고정 익절(+3%)이 전역 arm(5.5%)보다 먼저 닿으면 트레일링은 발동 기회가
  없다(설정은 있으나 무효). 프로파일에 `trailing_arm_pct`/`trailing_stop_pct`(0 = 전역 사용)를 두고, 두 경로 모두
  저널 행의 `screener` 로 프로파일을 해석해 덮어쓴다. **고정 익절선은 지우지 말고 상한으로 남긴다** — 지우면 급등 종목이
  무한 보유로 간다. 새 값은 `arm ≥ trail/(1−trail)` 을 만족해야 armed 청산이 진입가 아래로 안 내려간다(1%/0.8% → 0.806% ≤ 1%).
- **사후 반사실은 고점 가정임을 밝힌다.** "그날 고가까지 갔다면 X에서 청산"은 실제 체결가가 아니다 — 방향 근거로만 쓰고
  기대값 개선의 증거로 쓰지 않는다. 표본은 며칠 실현손익으로 다시 본다.
- **이 사용자는 본전~소폭 이익에서 시간 규칙으로 끊는 것을 '조기 청산'으로 본다.** 시한부 연장은 네 조건을 모두 요구한다:
  ① 미실현 이익 > 0 ② 진입 시점 지수 스냅샷 < 현재 지수(`algo_json.market_index` 에 기록) ③ 하드 상한(분)
  ④ 손익 0 이하 회귀 시 즉시 청산. '오늘 장이 올랐다'는 조건은 틀렸다(그날 지수가 올라도 우리가 산 뒤 하락했으면 근거 없음).

## 테스트·린트 게이트 (커밋 전 두 관문)

1. **스위트 러너 한 번으로 검증한다**: `python3 scripts/run_test_suites.py` (5스위트, 약 160초, `data/reports/test_runs/latest.json`).
   러너가 `.env` 를 스스로 읽고 호스트 매핑(`POSTGRES_HOST=127.0.0.1`, `POSTGRES_PORT=5434`)을 강제하며, 실패한 스위트만
   기본 2회 재시도해 **플래키**를 구분한다. 루트만 볼 때는 `set -a; . ./.env; set +a;` 후
   `POSTGRES_HOST=127.0.0.1 POSTGRES_PORT=5434 python3 -m pytest -q tests`.
2. **`.env` 없이 돌린 수치를 회귀로 오독하지 마라.** DB 비밀번호가 없으면 DB 의존 테스트가 1~2건 더 실패해 같은 코드가
   다른 건수로 보인다(수동 5건 vs .env 로딩 4건 실측). 수치가 실행 방식에 따라 달라지면 먼저 환경 로딩을 의심하고,
   판정 로직(env 로드·포트 강제)을 **실행기 안**에 넣어 두 경로가 같은 값을 내게 만든다.
3. **커밋 훅 린트가 커밋 자체를 막는다 — 새 코드에 주석을 달지 마라.** `scripts/lint_agent_rules.py` 가
   `docs/lint_baseline.json` 래칫으로 **C001(주석·인라인 주석 금지, 기계 지시자 예외)** 과 **C003(`except: pass`)** 를
   파일별 허용량으로 검사한다. 허용량 0인 신규 파일은 주석 1줄로 커밋이 실패하고, 실패하면 스테이징만 된 채 끝나
   "테스트는 통과했는데 커밋이 없다" 상태가 된다. 근거는 주석 대신 **문자열**(assert 메시지·로그·`note`)에 쓰고,
   예외는 `except OSError: fh = None` 처럼 **흐름으로** 처리한다. 커밋 전 `python3 scripts/lint_agent_rules.py` 로 먼저 확인한다.
4. **실패한 테스트를 고치기 전에 어느 쪽이 틀렸는지 판정한다** — ① 제품 결함 ② 낡은 스펙(테스트가 옛 규약을 고정)
   ③ 변경된 기본값(한도·주기) ④ 날짜 의존. ④는 두 얼굴이다: 픽스처 날짜 하드코딩(‘오늘 청산’ 카운트가 그날만 통과)과
   `today−N` 이 **주말·휴장일**이라 거래일 목록에 없는 경우. ②~④는 테스트를 새 규약으로 **강하게** 다시 쓴다
   (예: "창이 마지막 적재일까지 닿는가"를 창의 가장 오래된 거래일과 비교, 픽스처는 `date.today()` 기준 생성) —
   비교를 약화시키거나 기대값을 지우는 방식은 금지.
5. **정책을 뒤집으면 고정 테스트도 같은 커밋에서 다시 쓴다** — 문턱·호출형태를 못 박은 테스트가 남아 있으면
   다음 실행에서 옛 정책이 되살아난 것처럼 보인다.

## 마무리 규약

- **`git add` 는 경로 하나만 없어도 전체를 중단한다.** 계산해서 만든 파일 목록을 `git add A B C` 로 한 번에 넘기고 stderr 를 `2>/dev/null` 로 가리면,
  `pathspec 'X' did not match` 로 **아무것도 스테이징되지 않은 채** 종료코드 1 로 끝나고 이어지는 `git commit` 이 `no changes added to commit` 으로 실패한다 —
  로그를 가렸으므로 "수정이 없다"로 오독하기 쉽다. add 전에 `[ -e "$f" ]` 로 존재를 확인하거나 파일별로 add 하고,
  커밋 전에 `git diff --cached --stat` 으로 **스테이징된 파일 수**를 확인한다. 산출물 경로는 위임 결과물마다 달라지므로(존재 확인 없이 목록을 쓰는 것이 원인) 이 확인이 기본이다.
- **루트로 러너를 시험 실행하면 그 러너가 쓴 상태파일이 root 소유가 되어 이후 `git add .` 이 통째로 막힌다.**
  증상은 `error: open("data/state/<x>.json"): Permission denied` + `fatal: updating files failed` 이고,
  같은 명령을 다시 돌려도 계속 실패한다(파일이 목록에 남아 있으므로). 처방: `ls -la <그 파일>` 로 소유자를 확인해
  `wsl.exe -u root -- chown <user>:<user> <파일>` 로 되돌린 뒤 다시 add 한다. 크론 래퍼를 root 로 dry-run 했거나
  `wsl -u root --` 로 스크립트를 돌렸다면 **그 실행이 건드린 `data/state/*`·`reports/*` 목록을 먼저 chown** 해 두면
  커밋 단계에서 멈추지 않는다.
- **두 사본을 동기화할 때 방향을 먼저 확인하라 — 라이브에만 있는 수정이 있다.** `python3 scripts/sync_trader_agent.py`
  (미리보기)로 `내용 다름` 목록을 먼저 보고, 그 파일들이 **라이브에서 고친 것**이면 `--pull --apply` 로 저장소에
  먼저 가져와 커밋한 뒤 `--push` 한다. 미리보기 없이 push 하면 라이브의 핫픽스를 저장소의 옛 버전으로 덮어써
  운영 방어가 소리없이 되돌아간다(실측: 수수료 음수 거부 수정이 라이브에만 있었다).
  같은 이유로 **git 이 추적하지 않는 새 파일은 sync 가 복사하지 않는다** — 추적 목록(`git ls-files`) 기준이므로
  새 테스트·스크립트 파일은 직접 `cp` 해야 한다.
- 완료된 작업은 **커밋·푸시**까지가 한 세트다: 트레이더 코드가 analyist_dd 안에 있어도 `trader-agent/` 가
  **자체 `.git` 을 가진 중첩 리포**인 경우가 있다 — 그 안에서 커밋하면 부모 리포가 그 이력을 받지 못한다.
  커밋 전에 `cd trader-agent && git rev-parse --show-toplevel && git remote -v && git branch --show-current` 를 찍어라.
  실측: 중첩 리포의 `origin` 이 **부모 리포 URL**(`…/analyist_dd.git`)을 가리키고 브랜치도 `main` 이 아니라 `master`
  여서 `git push origin main` 이 `src refspec main does not match any` 로 실패했다. 그 상태에서 억지로 push 하면
  트레이더 이력이 부모 리포로 섮인다 → **로컬 커밋만 남기고 "커밋됨·푸시 안 됨(origin 오지정·브랜치 불일치)"으로
  **원격·브랜치는 단정하지 말고 실측값을 따른다** — 같은 중첩 리포라도 어떤 시점엔 `trader-agent.git`/`main` 으로
  정상 푸시되고, 어떤 시점엔 부모 URL·`master` 로 어긋난다. 푸시 후 `git ls-remote origin refs/heads/<branch>` 로
  원격 SHA 와 로컬 HEAD 를 대조한다. 사본 동기화는 커밋이 아니라 `scripts/sync_trader_agent.py` 의 영역이다.)
- 설정을 바꿨는데 반영이 안 되면 먼저 `Config.as_dict()` 화이트리스트와 프리셋/사본 계층을 본다(`references/portability-and-sync.md`).
- 발견한 결함은 그 자리에서 고치거나(비보호 경로) `--protected` 과제로 등록하고, 보고에는 **고친 것 / 등록한 것 / 사람 단계**를 나눠 쓴다.
- 보고는 한국어 평문 번호 목록(헤더·표 없음): 무엇을 바꿨는지 · 어떤 명령으로 검증했는지(수치) · 남은 것.
