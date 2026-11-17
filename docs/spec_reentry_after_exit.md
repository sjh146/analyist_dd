# 스펙 — 청산 후 재투입 계측 (reentry_after_exit)

작성 2026-11-17. 근거: `data/state/ceo_priority.json`(day=2026-11-16) 의 1순위 레버
"청산이 노출을 줄이지 않는다"(승인 불필요한 **계측** 부분만 구현) + expectation
"11-18 까지 '청산 후 재투입' 판정이 원장 한 줄로 자동 출력된다".

## 목표

청산 1건과 그 **직후 창(기본 10분) 안의 신규 진입**을 한 거래 단위로 묶어, 원장(journal)만 읽어
다음을 자동 산출하는 **읽기 전용** 계측 스크립트를 새로 만든다.

- (a) 청산 후 창 내 신규 진입 **건수·금액(수량×체결가)**
- (b) 그 진입이 쓴 **피드 날짜**(없으면 `null` + 사유. 추정값을 만들지 않는다)
- (c) 두 건 **합산 순손익**(비용 포함)
- 세션(일자) 누적 재투입 순손익이 음(-)이면 "재투입 킬 후보"로 한 줄 출력, 표본 3건 미만이면 "표본 부족"

## 비목표 (명시)

- 주문 경로·한도·게이트·전략 파라미터를 **바꾸지 않는다**. `trader-agent/**` 파일을 수정하지 않는다.
- 재투입을 **차단하지 않는다**(차단은 사람 승인 대상).
- 저널에 **쓰지 않는다**(`mode=ro`). 스키마 변경·마이그레이션 금지.
- 통계적 유의성 주장 금지 — 표본 3건 미만이면 판정 문구를 "표본 부족"으로 고정.

## 입력 (실측 확정값 — 재조사 금지)

저널(정본, 읽기 전용): `/mnt/c/Users/jhshi/analyist_dd/trader-agent/journal/trade_journal.sqlite3`

`trades` 테이블(실측 컬럼):
`id, ts, side, code, name, qty, price, order_type, order_resp_json, entry_reason, screener,
hwm_price, algo_json, stop_pct, tp_pct, kelly_f, exit_ts, exit_price, exit_reason, pnl, fees`

실측 사실(이 수치로 검증한다):
- 포지션은 `side='buy'` 행 하나로 표현되고, 청산되면 같은 행의 `exit_ts/exit_price/exit_reason/pnl` 이 채워진다.
- **`pnl` 은 이미 비용 차감 후 순손익이다.** id=11: (34050−36642.5)×2 = −5185, −5185−146(fees) = **−5331 = pnl**. 따라서 순손익 = `pnl` (fees 를 또 빼지 말 것).
- 기준선 2쌍(2026-11-17 시점 실측):
  - id=11 440110 청산 `2026-11-16T10:05:27.094281+09:00` (pnl −5331) → id=12 033100 진입 `2026-11-16T10:05:30.947190+09:00` 2주×37200 = **74,400원**, 간격 3.9초
  - id=10 290720 청산 `2026-11-17T09:04:31.661733+09:00` (pnl −6831) → id=13 096530 진입 `2026-11-17T09:05:17.389720+09:00` 3주×24103.3333(**체결 VWAP**) = **72,310원**, 간격 45.7초
    (초안은 주문가 24200 기준 72,600원으로 적었으나 계약은 "수량×체결가"이므로 72,310 이 정본 — 2026-11-17 실측 정정)

피드 날짜(`feed_generated_at`): 저널에 **기록되지 않는다**(decisions/algo_json 어디에도 없음 — 2026-11-17 실측).
따라서 다음 규칙으로만 채운다.
1. `data/feed/history/screener_latest_*.json` 처럼 과거 스냅샷 파일이 있으면 진입 ts 직전 스냅샷의 `generated_at` 을 쓴다.
2. 없으면 `null` + `feed_date_source: "unrecorded"`.
3. **매 실행 시** 현재 `data/feed/screener_latest.json` 의 `generated_at` 을
   `data/reports/feed_asof_history.jsonl` 에 append(같은 `generated_at` 중복 append 금지 = 멱등) 해서
   이후 실행은 1번 규칙으로 해소할 수 있게 한다. 스냅샷이 아닌 현재값을 과거 진입에 **소급 대입하지 않는다**.

## 산출물 (계약)

1. `data/reports/reentry_after_exit.jsonl` — 청산 1건당 1줄(최신 실행 시 전량 재작성, append 아님).
   필수 키: `exit_trade_id, exit_ts, code, name, screener, exit_reason, pnl_exit, fees_exit,
   window_min, n_reentries, reentry_notional_krw, reentries[{trade_id, ts, code, qty, price, notional,
   screener, gap_sec, pnl_if_closed}], pair_net_krw, pair_status("closed"|"open"), feed_generated_at,
   feed_date_source`
2. `data/reports/reentry_after_exit.json` — 요약: `ts, journal, window_min, sessions[{day, n_pairs,
   reentry_notional_krw, pair_net_krw}], judge{verdict, n_pairs, basis}`.
   `judge.verdict` 는 다음 3값만: `"재투입 킬 후보"`, `"재투입 유지"`, `"표본 부족"`.
3. **원장 한 줄**(위 expectation): 실행 시 stdout 마지막 줄에 아래 형식 1줄을 출력한다.
   `reentry_after_exit day=2026-11-16 pairs=1 reentry_krw=74400 pair_net_krw=-5331 verdict=표본 부족`
   (`--quiet` 없이 실행했을 때. 요약 json 에도 같은 문자열을 `judge.line` 으로 넣는다.)

## CLI

```
python3 scripts/reentry_after_exit.py [--journal PATH] [--window-min 10] [--days N] [--json-out PATH] [--jsonl-out PATH] [--quiet]
```
- 기본 `--journal` 은 환경변수 `TRADER_JOURNAL` → 없으면 위 정본 경로. 저널이 없으면 **rc=1 + 명확한 메시지**(빈 산출물을 성공으로 만들지 말 것).
- `--days`(기본 60) 만큼의 청산만 대상으로 한다.
- 타임스탬프는 ISO8601 오프셋 포함 KST 문자열로 비교한다(문자열 비교로 시간순이 성립하는 형식만 쓰되, 가능하면 `datetime.fromisoformat` 로 파싱해 비교).

## 판정 규칙(고정)

- 대상 청산 = `exit_ts IS NOT NULL` 인 buy 행. `exit_reason` 이 `unfilled:`/`reconcile: broker flat` 로 시작하는 행은 **포지션이 아니었으므로 제외**하고 제외 건수를 `judge.basis` 에 적는다.
- 재투입 = 같은 저널의 buy 행 중 `ts > exit_ts` 이고 `ts <= exit_ts + window_min` 인 것(청산 종목 재진입도 포함 — 종목 무관하게 창 내 신규 노출 전부).
- `pair_net_krw` = `pnl_exit + sum(pnl of reentries where pnl is not null)`. 재투입 중 미청산이 있으면 `pair_status="open"` 이고 `pair_net_krw` 는 **청산된 것만** 합산하되 `open_notional_krw`(미청산 진입 금액)를 함께 남긴다.
- 세션 누적: `day = exit_ts[:10]`. 세션 `pair_net_krw` 가 음수인 세션이 1개 이상이고 전체 pair 수 ≥ 3 이면 `"재투입 킬 후보"`, 전체 pair 수 < 3 이면 `"표본 부족"`, 그 외 `"재투입 유지"`.

## 검증 (위임 에이전트가 실제로 실행하고 출력을 보고에 붙인다)

1. `python3 -m py_compile scripts/reentry_after_exit.py`
2. `python3 scripts/reentry_after_exit.py` → stdout 마지막 줄이 `day=2026-11-16` / `day=2026-11-17` 두 세션을 포함하고, `data/reports/reentry_after_exit.json` 의 세션 배열에서
   `2026-11-16` 세션의 `reentry_notional_krw == 74400` 그리고 그 쌍의 `pnl_exit == -5331` 이 나온다.
3. 위 두 수치(id=11→12, id=10→13)를 스크립트 출력에서 **grep 으로 직접 확인**해 보고에 원문을 붙인다.
4. 재실행 멱등성: 같은 명령을 두 번 돌려 `data/reports/feed_asof_history.jsonl` 의 줄 수가 늘지 않는 것을 확인한다.
5. `git status --porcelain` 로 `scripts/reentry_after_exit.py` 외에 **수정된 파일이 없는지** 확인(런타임 산출물 `data/reports/reentry_after_exit.*`·`feed_asof_history.jsonl` 은 제외).

## 금지

- `git` 명령 실행 금지(커밋은 오케스트레이터가 한다).
- `.env`·`trader-agent/**`·`config/**`·`kill_switch.txt` 수정 금지, 리포 밖 파일 생성 금지.
- 저널 쓰기 금지(`file:...?mode=ro` 로만 연다).
- 네트워크 호출 금지(피드는 로컬 파일만 읽는다).
- 기존 파일 수정은 `.gitignore` 1~2줄 추가까지만 허용(런타임 산출물 무시), 그 외 기존 파일 수정 금지.
