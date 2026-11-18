# 스펙 — 실매매 → analyist_dd 피드백 루프 (자동 측정·제안·백로그 등록)

작성 2026-11-18. **사용자 지시**: "계속 analyist_dd를 피드백해서 개선시킬 수 있도록 할 수 있게 해줘".
목표: 실계좌 체결 결과가 **매일 자동으로** 피드(스크리너·엣지 스코어·ML) 개선 입력으로 돌아가게 한다.
**자동 주문 파라미터 변경은 하지 않는다** — 측정·제안·백로그 등록까지만(돈이 걸린 값은 사람 승인).

## 실측 배경 (오늘 관측)

- 단타 2건(30분 스캘프): 021320 +29원(30.2분) · 143240 −41원(34.6분) — 변동이 ±0.1% 미만인데
  왕복비용은 약 0.21%. 즉 **비용 대비 변동이 작다**는 것을 실측으로 확인(그래서 보유 90분으로 확대).
- 엣지 게이트 차단 분포(오늘): 2점 185건 · 3점 95건 · 1점 31건, 통과 26건.
- 이 값들을 **매일 자동 집계**해 어떤 점수·요인이 실제 돈과 연결되는지 피드백한다.

## 1. 트레이더측 1줄 변경 (선행)

`trader-agent/trader_core/engine.py` — 진입 `algo_json.candidate` 에 **`edge_factors`** 를 추가한다
(현재 `edge_score`·`atr_pct` 는 기록됨). 값은 피드의 `edge_factors` dict 그대로(없으면 `None`).
다른 동작 변경 금지, 기존 테스트 그린 유지.

## 2. 신규 `scripts/feed_feedback.py` (analyist_dd)

```
python3 scripts/feed_feedback.py [--days 14] [--min-n 10] [--json-out ...] [--md-out ...] [--register] [--quiet]
```
- 입력(전부 읽기 전용):
  - 저널 `trader-agent/journal/trade_journal.sqlite3` — **WSL 에서 직접 열면 disk I/O error** 이므로
    `/tmp` 복사 후 `mode=ro` (기존 `scripts/reentry_after_exit.py` 관용구 재사용).
    `trades(id, ts, code, qty, price, screener, exit_ts, exit_price, pnl, fees, algo_json)`.
  - `minute_close_bars`(있으면 보유 중 최대 변동), `market_data`(진입일 OHLC).
- 산출 차원(각 차원별 `n, win_pct, expectancy_krw, expectancy_pct, avg_win, avg_loss, fee_share_pct,
  avg_hold_min`):
  1. `screener` 2. `edge_score`(0-2 / 3 / 4 / 5 / 6-7 / None) 3. 진입 시각대(09-10,10-11,11-12,12-13,13-14,14-15)
  4. 보유 분(≤30 / 31-60 / 61-90 / >90) 5. **`edge_factors` 개별 요인**(on/off 별 기대값 차이, 있으면)
  6. `stop.model`(fixed/atr/vol-range)
- 출력 `data/reports/feed_feedback.json` / `.md`(실행 시각 미기록 — 멱등):
  ```json
  {"days":14, "trades":N, "overall":{...}, "by_screener":[...], "by_edge_score":[...],
   "by_hour":[...], "by_hold":[...], "by_factor":[{"factor":"sr_breakout","on":{...},"off":{...},"delta_krw":...}],
   "cost": {"fee_share_pct": ..., "avg_trade_krw": ...},
   "suggestions": [{"param":"min_edge_score","current":4,"suggested":5,"why":"...","n":...}],
   "queued": [{"title":"...","priority":N}]}
  ```
- **제안 규칙(자동, 결정적)**: `edge_score` 버킷별 기대값이 n≥`--min-n` 로 유의하게 갈리면
  기대값 최대 버킷의 최소 점수를 `min_edge_score` 제안값으로 낸다(동률이면 현행 유지).
  단, **제안만** — `data/state/edge_params_suggested.json` 에 쓰고 트레이더가 읽는
  `edge_params.json` 은 건드리지 않는다(그 파일은 사람 승인 절차).
- `--register` 가 있으면 부정 버킷(n≥`--min-n`, 기대값 < 0, 비용 포함)을
  `python3 scripts/improve_queue.py add --title ... --why ... --acceptance ... --priority <int>` 로 등록한다.
  (`--priority` 는 **정수**; 이미 같은 제목이 큐에 있으면 건너뛴다 — 멱등.) 기본은 `--register` 없이 dry.
- 종료 코드: 산출 성공 0, 저널 없음 1.

## 3. 크론 (평일 16:45 — 16:40 체결 적재 뒤)

```
45 16 * * 1-5 cd /home/jhshi/analyist_dd && set -a; . ./.env; set +a; /usr/bin/python3 scripts/feed_feedback.py --register >> reports/cron_feed_feedback.log 2>&1
```
(등록은 오케스트레이터가 한다 — 스크립트만 만들 것.)

## 4. 테스트 — `tests/test_feed_feedback.py` (신규 7개)

① 기대값·승률·비용 비중 계산(합성 5건 손계산) ② edge_score 버킷 경계(0-2/3/4/5/6-7/None)
③ 보유분 버킷(30/60/90 경계) ④ `edge_factors` on/off 델타 ⑤ 제안 규칙(최적 버킷 → min_edge_score)
⑥ `--register` 멱등(같은 부정 버킷 2회 실행 시 1건만) ⑦ 멱등(같은 입력 → 같은 바이트)

## 5. 검증 (실행 출력 원문 첨부)

```
cd /home/jhshi/analyist_dd
/usr/bin/python3 -m pytest tests/test_feed_feedback.py -q
/usr/bin/python3 scripts/feed_feedback.py --days 14 --quiet && echo OK
/usr/bin/python3 -c "import json;d=json.load(open('data/reports/feed_feedback.json'));print(json.dumps({k:d[k] for k in ('trades','overall','cost','suggestions')},ensure_ascii=False)[:800])"
```
- 저널이 실제로 4청산건(오늘) 포함됨을 `trades` 값으로 확인(현재 기준 4).
- `data/state/edge_params_suggested.json` 생성 확인(제안이 없으면 `suggested: null` + 사유).

## 6. 금지

- `git` 금지(커밋은 오케스트레이터). `trades`·`minute_close_bars`·`market_data` **쓰기 금지**.
- `data/state/edge_params.json`(트레이더 입력) 수정 금지 — 제안 파일만.
- 실주문·브리지·루프 프로세스 접근 금지. 자동 주문 파라미터 변경 금지.
- 기존 스크립트 수정 금지(import·관용구 재사용만). 기존 테스트 기대값 수정 금지.
- 결측을 0으로 메우지 말 것(측정 불가는 null + 사유).
