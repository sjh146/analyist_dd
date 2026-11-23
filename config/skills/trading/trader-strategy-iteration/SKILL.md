---
name: trader-strategy-iteration
description: Use when tuning a trading strategy from live data.
---

# trader-strategy-iteration (데이터로 전략을 진단하고 고치는 절차)

Journal + broker evidence → one knob at a time → tests → deploy → document.

Scope: the `trader-agent` workspace (`C:\Users\jhshi\trader-agent`) — reviewing whether the
strategy/algorithms are working, and changing them safely.  Runbook/COM quirks live in
trading/creon-cybosplus-trader-agent; this skill is the *iteration methodology*.

Core rule: **change nothing without a measured reason, and never conclude from one day.**
Every change is either (a) a *mechanical* fact provable from data (score saturation, buying the
day's high, stop inside the day range) or (b) a *statistical* claim that needs 20+ closed trades
before you believe it.

## 1. Pull the evidence (never reason from memory)

Journal: `journal/trade_journal.sqlite3` (`decisions`, `trades` tables).  Broker: live endpoints.

The Windows console is **cp949**, so Korean from the broker/journal prints as mojibake.  Write the
analysis to a UTF-8 file and read it back instead of printing:

```python
import io
out = io.open("tmp_analysis.txt", "w", encoding="utf-8")   # then read_file() it
```

```python
# a) 결정 퍼널 — 게이트 dict 키는 'ok' 이다 ('passed' 아님)
rows = j._conn.execute(
    "SELECT ts, code, score, gates_json, plan_json FROM decisions WHERE ts >= ?", (day,))
for r in rows:
    gates = json.loads(r["gates_json"] or "[]")
    failed = [g["gate"] for g in gates if not g.get("ok")]

# b) 점수 포화 검사 — 같은 사이클(ts) 안에서 점수 폭이 작으면 선별력이 없다
#    실측 2026-09-10: 후보 88.2~98.7, 사이클 내 폭 평균 3.9점 → 문턱·상위N 선별이 무의미

# c) 진입 특성 vs 결과 — entry_reason 에 근거가 남아 있다
#    예: "10위 +18.28% 거래량 5,551,806"
m = re.search(r"(\d+)위 \+\s*([0-9.]+)% 거래량 ([\d,]+)", reason)

# d) 브로커 마크 = 포지션 행의 eval_amount / qty  (시세와 교차검증 대상)
mark = position["eval_amount"] / position["qty"]
```

What to look at, in order of signal:

| 진단 | 보는 값 | 위험 신호(실측) |
|---|---|---|
| 선별력 | 사이클 내 점수 폭, 점수 min/max | 폭 < 10점, 전부 88~99 → 문턱이 죽어 있다 |
| 추격 정도 | (일중고 − 진입가)/일중고 | 평균 −4.6% = 고점 매수 |
| 손절 적정성 | 7% ÷ 일중 변동폭 | 22~36% = 노이즈 안쪽 |
| 과열 | 후보 평균 점수·평균 등락률 | 93.7점 / +19.7% → 그날 진입이 -31,455원 |
| 실증 | 청산 건수, 승률, 손익비 b | 5건 미만이면 `tools/review.py` 가 파라미터를 안 쓴다 |

## 2. Choose the change (한 번에 하나)

- Prefer a **설정 기본값 + 주석에 측정 근거**.  Every knob needs: a default in code, a comment with
  the measured number that justifies it, and a way to disable it (`0` / `""` = off).
- Filters must **fail open** when data is missing (no day high → don't silently drop the candidate).
- 돌리는 값은 `strategy_params.json`(랭크 필터·priors), `runner/config.py`·`trader_core/config.py`
  기본값, 또는 `TRADER_CORE_*` 환경변수.
- Sizing/exit changes belong in `trader_core/risk.py` + `engine.py` (plan-time) or `monitor.py`
  (runtime).  청산 판단은 **broker mark 와 교차검증**한 가격으로만 한다.

## 3. Lock it with tests

**Record what ran.**  Every fill carries `trades.algo_json` (schema v4, see `trader_core/algo.py`):
version, screener, scoring mode, the filter values actually applied, the candidate's own metrics,
the sizing path (kelly vs equal-risk qty) and the stop model; the close merges the exit rule and
the price source into the same record, and the loop logs one `ALGO buy CODE qty :: ...` line per
order.  Keep it pure in-memory (dict work at plan time) -- attribution must never add a broker
request or a sleep, because latency is part of the strategy.  When you change the decision logic,
the record changes with it, so "which algorithm produced this trade?" is answerable from data.

Suites run on the deployment runtimes, 32/64 split:

```bat
C:\Users\jhshi\Python311-32\python.exe -m pytest bridge/tests -q
C:\Users\jhshi\Python312-64\python.exe -m pytest trader_core/tests runner/tests -q
```

Regression fixtures in `trader_core/tests/test_rankfeed.py` / `test_risk_reinforcements.py` are the
right home: one test per knob (enabled / disabled / missing-data), plus an end-to-end engine test
that asserts the **number** (qty, stop_pct, score spread) — not just "it ran".

When a fixture breaks because the change is *intended*, do not soften the test: re-anchor it around
the new intent (e.g. isolate Kelly from the new risk cap with `risk_per_trade_pct=0.0`) and keep the
old expectation in a dedicated test for the new behaviour.

## 4. Deploy + verify

1. dry-run first: `run_market_loop.py run --once --dry-run --feed rank` (no orders).
2. Filter/score changes take effect on the next cycle (the params file is re-read every fetch);
   config-default changes need a **loop restart** (`loop_stop.bat` → `loop_start_bg.bat`).
3. Confirm on the live object, not on the test: read `runner.log` for the new line, check the state
   file, and re-run a read-only probe (monitor heartbeat / `/positions`) to see the new decision.
4. Update `OPERATIONS.md` (§7.3.1 가드 표) and the related skills in the same session.

## 5. Pitfalls that cost real money

- **랭크 창(`rank_pct_to`)과 추격 상한(`max_pct`)은 한 쌍이다.** 랭킹 페이지는 상승률 내림차순이라
  창이 상한보다 넓으면 표본 상위가 전부 상한 위쪽에 앉아 상한이 모든 후보를 버린다 — 2026-09-11
  실측: 창 30% + 상한 12% = 오전 내내 후보 0건, 창 15% + 상한 15% = 같은 날 5건 진입. 상한만
  올리면 "고점 추격"을 다시 허용하고, 창만 좁히면 상한이 의미를 잃는다. 둘을 같이 움직인다.
- **백분위 점수에서 R1 평균 문턱은 이진값이다.** 점수는 후보 집합 *내부* 백분위라 상위N 평균이
  구조적으로 ~50이다(2026-09-11 실측 84/58/50/44/40/24 → 50.0). 50 이상이면 매 사이클 차단,
  미만이면 절대 안 걸린다. 자기 생성 랭크 피드의 과열 판단은 pct 기반 가드
  (`r1_max_avg_pct`·`max_pct`·`min_pullback_pct`)가 맡아야 하고, `tools/review.py` 자동 조정도
  문턱을 평균 위로 올리지 못하게 막아야 한다(안 막으면 리뷰 한두 번에 피드가 다시 죽는다).
- **켈리 prior 를 "안전하게" 낮추면 매매가 멈춘다.** `f* = p − (1−p)/b`; b=1 일 때 p ≤ 0.50 이면
  f* ≤ 0 → 신규 진입 0건.  0.45 대신 0.53(얇은 우위) + 크기 축소로 간다.
- **추격 스톱은 `arm ≥ trail`을 지켜야 한다.** trail이 arm보다 넓으면 armed 포지션의 청산 트리거가
  `peak×(1−trail)` = 진입가 *아래*에 생겨, "이익 보호" 규칙이 손실 청산으로 뒤집힌다 — 2026-09-15 실측
  3%/5%: 추격스톱 청산 5건이 -3.0/-1.6/-2.1/-2.0/+0.3%(평균 -1.7%), 고점 8.6%까지 갔던 종목도 +2.4%에서
  나갔다. 3%/3%의 최악 armed 청산은 `1.03×0.97−1 = -0.09%`(사실상 본전).
- **익절 배수 변경 전에 hwm 분포부터 본다.** 진입 후 최고가(hwm)가 손절폭×2에 도달한 비율이 낮으면
  익절을 늘려도 아무도 못 닿고 승률만 떨어진다 — 2026-09-15 청산 20건 중 1건(중앙값 hwm +3.3%),
  즉 그날 문제는 청산 파라미터가 아니라 진입 선별/타이밍이었다. 청산 손잡이를 돌리기 전에
  `hwm_price` 분포를 먼저 측정한다.
- **보유 기간을 줄이는 손잡이는 청산선(손절/익절 %)이 아니다 (2026-09-22 실측).** 회전율 문제를
  "손절·익절 라인"으로 풀려 하면 거의 모든 조합이 손해다: 같은날/익일 청산(폐쇄 +2,975/+11,855 vs
  현재 +17,890), 3일·4일 하드 상한(+7,790/+11,310), 손절 상한 6%(미결 -35,520), 트레일 좁히기
  (arm2/trail2: 폐쇄 -290). 큰 수익은 2~5세션에 걸쳐 나왔다(468530은 1일차 종가 -3.4% → 5일차 +33%,
  036540 -0.2% → +22%). 실제로 돈을 안 내고 보유를 줄인 규칙은 두 개뿐: **보합 청산**(2세션째
  종가창에서 |손익| ≤ ±2% → 청산; 폐쇄 +17,890 그대로, 미결 -24,250 → -15,090)과 **보유 상한**
  (5세션, 17건 기준 약 -2.1천원). "성과 없는 종목 조기 청산"(time_stop: 2세션 +1% 미달)은 비용이
  더 크다(미결 -25,910) — 못 오른 종목이 나중에 크게 오는 경우가 그만큼 있다.
- **진짜 병목은 슬롯이다.** 저널 `decisions.gates_json` 의 `balance_position_amount` 게이트
  detail("already holding 10 codes ... max_open_positions 10")을 세면 "보유 슬롯 때문에 못 산
  후보"가 정확히 나온다 — 2026-09-11~22 실측 103 code-day(하루 10~38개). 청산 규칙을 만지기 전에
  여기부터 측정한다. 게이트는 dict 키 `detail` 이다(`reason` 아님).
- **네이버 1분봉은 `count` 를 올려도 ~2,205봉(약 6세션)에서 잘린다.** 리플레이 표본은 날이 갈수록
  줄어든다(2026-09-21 21건 → 09-22 7건). 표본을 늘리려면 **미결 포지션도 같은 도구로 평가**한다
  (진입→현재가 평가, n=10 추가). 미결/폐쇄를 합친 총합으로 규칙을 비교하되 절대값은 믿지 않는다.
- **리플레이의 시간 규칙은 N번째 세션이 데이터에 실제로 있을 때만 발동시킨다.** 세션 수가 부족할 때
  마지막 봉으로 대체하면 5세션 상한이 이틀 된 포지션을 오늘 청산한 것으로 계산돼 규칙 비교가 망가진다.
- **`tools/status_snapshot.py` 의 트레일 상수는 Config 에서 읽는다.** 3%/3% 하드코딩이 남아
  5.5%/5% 배포 후에도 armed 포지션의 청산선을 26,142(실제 25,602)로 잘못 보여줬다. Config 값이
  바뀌면 이 도구도 같이 움직여야 한다(지금은 `Config()` 에서 읽음 — tools/ 는 스크립트 디렉터리가
  sys.path[0] 이므로 저장소 루트를 직접 넣어야 import 된다).
- **분 단위 보유는 아직 근거가 없다 (2026-09-22 실측, n=17).** `tools/replay_exits.py` 에
  `hold_minutes`/`time_stop_minutes` 로 분 단위 규칙을 넣어 돌려보면, *미결(평가)* 그룹에서는
  5·15분 보유가 크게 좋아 보이지만(-26,245 → -2,015/-2,805) *폐쇄 거래* 그룹에서는 같은 규칙이
  큰 수익을 잘라버린다(+16,480 → -795/-170). 비단조성(30분 -17.7k, 60분 -23.7k 가 5분·R0 보다
  나쁨)이 핵심 신호다 — 그 정도 표본에서 나오는 "개선"은 몇 건의 청산 시점 차이일 뿐이다.
  **청산을 빨리 하는 것과 청산선을 좁히는 것은 다른 문제다**: 좁히면 손해, 빨리 반응하는 것은
  슬리피지/오버슈트를 줄이는 개선이다. 사이클 사이 실시간 캐시 감시로 반응 시간을 30~300초에서
  약 1초로 줄이는 것이 그 답(`runner/loop.py::_tick_watch_pass`).
- **실시간 캐시는 Daishin 요청 없이 읽힌다**: 브리지는 `GET /quotes` 를 **codes 없이** 받으면
  `RealtimeHub.latest_quotes()`(StockCur 틱 스냅샷)를 그대로 돌려준다. 초당 예산이 병목이라
  사이클을 빨리 돌릴 수 없을 때의 유일한 저비용 채널이다. 단 청산 판정은 캐시 단독 금지 —
  이탈 후보가 뜨면 `/positions` 1회로 브로커 마크(eval_amount/qty)로 재확인하고 둘 중 더 나쁜
  가격을 쓴다(2026-09-10: 캐시 6,050 vs 브로커 5,040 → -17.8% 방치).
- **트레일 폭은 hwm 분포와 함께 본다 (2026-09-21 실측).** 3%/3% 트레일은 번 종목을 본전에서
  잘라냈다: 청산 30건 중 트레일 청산 10건 평균 **-0.23%**, 고정 손절 10건 -10.45%, 익절 9건 +13.0%,
  그리고 진입 후 고점(hwm) 중앙값 +4.17%·손절폐 1.5배 도달 10%이라 "+4% 고점 → 3% 반납 = 본전"이었다.
  `tools/replay_exits.py`(1분봉, 진입/보유기간 고정, 브리지 요청 없음)로 재보면 넓히는 방향이 단조 개선:
  arm3/trail3 +2,883 → arm4/trail4 +16,680 → **arm5.5/trail5 +25,535**(트레일 청산 13→6, 익절 2→7),
  좁히거나 손절을 조이면 악화(trail2 -37 / vol*0.35 -8,272 / 고정 7%/7% -9,510). 불변식
  `arm ≥ trail/(1-trail)`을 지키면 armed 청산이 이익(+0.22%). 대가: +5.5% 미만 고점 종목은 armed 되지 않아
  고정 손절까지 보유한다. **리플레이의 절대값은 믿지 말 것** — R0 재현이 실제 체결과 다르다
  (1분 종가가 라이브 트레일 타이밍을 놓친다). **규칙 간 델타만** 읽는다.
- **고정 % 손절은 변동성 종목에서 무의미**하다.  일중 변동폭 19~32% 종목의 7% 손절은 노이즈
  안쪽.  변동폭 기반 손절 + 동일 리스크 사이징(수량 = 리스크예산 ÷ (가격×손절폭))으로 바꾼다.
- **점수 포화**: 참조값이 낮으면 모든 항이 1.0 에 붙어 점수가 88~99 로 압축된다.  백분위 점수로
  바꾸면 같은 스냅샷 안에서 0~100 으로 퍼지고, 그때부터 문턱이 의미를 갖는다.
- **큰 일일 상한 + 짧은 주기 = 같은 종목 반복매수.** 종목당 하루 1회 가드 없이는 사이클마다
  같은 리스트를 다시 산다.
- **체결 직후 60초 내 reconcile 금지** — 브로커 포지션은 초~1분에 걸쳐 정산돼 그 창에서는 flat
  으로 보인다(방금 산 포지션을 청산 처리하게 된다).
- **시세와 브로커 마크가 다르면 손절이 안 걸린다.** 청산 판단은 `eval_amount/qty` 와 비교해
  더 나쁜(낮은) 가격을 쓴다.  이걸 빼먹어 -17.8% 포지션이 -1% 로 보였다.
- **시계가 틀어지면 세션 모델 전체가 틀어진다** (`trader_core/clockcheck.py` 가 신규 진입 차단).
- **WSL interop 이 죽으면 Windows exe 실행이 전부 실패**한다 (`UtilAcceptVsock ... accept4 failed`).
  `export WSL_INTEROP=/run/WSL/2_interop` 로 복구한다.

## 6. Report honestly

State n and what it does/does not prove: "구조적 사실(점수 포화)" vs "하루 표본의 방향성".
If a change guards against a known failure, say which failure and what it cost; if a strategy has no
measured edge yet, say that too instead of implying the numbers support it.
