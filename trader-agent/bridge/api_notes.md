# creon-bridge v1 — api_notes.md

Reference notes for `bridge/`, the headless localhost JSON HTTP service that
wraps Daishin CybosPlus (Creon). Every COM class listed below was derived
from the official samples in `creon_extracted_v1/` (source filename given per
table). Index tables are **verified against the samples**, not guessed; where
the samples are ambiguous or silent the assumption is flagged.

---

## 1. Architecture & threading model

```
64-bit world (strategy/risk/agent)             32-bit Python 3.11 (this package)
┌───────────────────────────────┐              ┌──────────────────────────────────────┐
│ HTTP client (127.0.0.1:8100)  │ ── JSON ──▶  │ bridge/server.py ThreadingHTTPServer   │
└───────────────────────────────┘              │   │ each request runs on its own thread │
                                               │   ▼                                     │
                                               │ orders.py ──▶ plus.py CybosPlus         │
                                               │   │  │                                  │
                                               │   │  └─ invoke() ──▶ single STA worker  │
                                               │   │      thread (CoInitialize once;     │
                                               │   │      ALL BlockRequest serialised)   │
                                               │   ▼                                     │
                                               │ realtime.py RealtimeHub ──▶ dedicated   │
                                               │      pump thread (own CoInitialize +    │
                                               │      PumpWaitingMessages loop)          │
                                               └──────────────────────────────────────┘
```

CybosPlus COM objects are apartment-threaded (STA). To satisfy constraint 4
of PHASE2_BRIEF.md:

1. **One serialised STA worker thread owns every blocking `BlockRequest`.**
   `CybosPlus.invoke(func)` enqueues `func` on that thread and blocks the
   caller until it finishes. This guarantees:
   - all `win32com` objects are created and used from a single apartment;
   - blocking calls are serialised under an implicit lock;
   - blocking calls can **never** starve or block the realtime message pump.
2. **Realtime events run on their own pump thread.** Daishin samples relied
   on the Qt event loop (`app.exec_()`). The samples in
   `creon_algorism__03__...BlockRequest_와_Request...` demonstrate the
   headless alternative: a `MsgWaitForMultipleObjects` + 
   `pythoncom.PumpWaitingMessages()` loop. `bridge/realtime.py` uses the
   simpler equivalent: a loop that drains its command queue and calls
   `pythoncom.PumpWaitingMessages()` every ~20 ms. Realtime COM objects are
   created and `Subscribe()`d only from that pump thread.
3. pywin32 (`win32com`, `pythoncom`) is imported **lazily inside
   functions**, so the package imports and unit-tests on Linux with no
   pywin32. Tests inject a fake `win32com` package via `sys.modules`.

**Verified live 2026-09-10 — the worker MUST pump while idle.** The two
apartments are not independent in practice: CybosPlus delivers realtime events
through a COM message window owned by the apartment that performs the Cybos
work. If a blocking request runs *before* a realtime subscription exists, that
window belongs to the blocking worker's apartment, and a subscription created
afterwards from the pump thread is then registered successfully
(`Subscribe()` returns, the code reports `SUBSCRIBE OK`) but **never receives a
single event**. Reproduced three ways on Creon PLUS 모의투자:

| first Cybos action in the process | then subscribe | events |
|---|---|---|
| `Subscribe()` (nothing else) | — | ~40 ticks / 20 s on 005930 |
| `/health` → `/quotes` (blocking worker) → subscribe | ok | **0** |
| pump-thread warm-up (`create("cybos")` at boot) → `/health` → `/quotes` → subscribe | ok | **0** |

The fix is in `bridge/plus.py`: the serialised worker polls its job queue with
a 20 ms timeout and calls `pythoncom.PumpWaitingMessages()` while idle
(`pump_waiting_messages()`, bounded by `_MAX_IDLE_PUMP` so queued jobs are
never starved). With that pump in place the failing sequence above delivers
ticks immediately, and a subscription that is already flowing survives any
number of later blocking requests. Regression tests:
`bridge/tests/test_plus.py::IdlePumpTest`.

Consequence for consumers: because realtime ticks are dispatched on the
worker thread while it is idle, `DsCbo1.*` `OnReceived` handlers run **on
either thread** (pump thread or blocking worker). `RealtimeHub._store_quote` /
`_store_execution` already guard with a lock, so hub state stays consistent.


---

## 2. Stock code convention

| name | example | used for |
|---|---|---|
| public API code | `005930` | HTTP request/response fields |
| trade code | `A005930` | `CpTrade.*` order input values |

`bridge/codes.py`:
- `to_trade_code("005930") == "A005930"` (also idempotent on `A005930`).
- `to_plain_code("A005930") == "005930"` (also idempotent on `005930`).
- Foreign Cybos prefixes (indices/futures such as `Q530031`, seen in the
  multi-code sample) are passed through **unchanged** — only the stock `A`
  prefix is managed.

Source samples: `creon_algorism__01__주식_매수...` sets `input(3)="A005930"`,
`creon_algorism3__01__...IOC_FOK...` normalises UI input to `A`+6
(`if not code[0]=='A': code='A'+code`), and balance/quote responses carry the
prefix which the bridge strips.

---

## 3. Communication model and error handling

`BlockRequest()` returns an int. Mapping observed in the samples:

| return | meaning (samples) |
|---|---|
| `0` | normal success |
| `1` | 통신요청 실패 (CpTd0311 sample treats any non-0 as error) |
| `2`, `3` | 통신 오류 (CpTd5339 sample `creon_algorism3__08__...` fails on 2/3) |
| `4` | 연속 통신/주문요청 제한 개수 초과 — wait `CpCybos.LimitRequestRemainTime` ms then retry |

`GetDibStatus() != 0` is a remote/business error; `GetDibMsg1()` holds the
Korean message. The samples loop on `ret == 4` sleeping
`LimitRequestRemainTime / 1000` seconds before retrying.

**Proactive request-budget gate (added Phase 2c, 2026-09-04):** Daishin
enforces per-second request budgets per type. `CpCybos.GetLimitRemainCount(t)`
returns the remaining requests (t: `0` = order, `1` = quote, `2` = realtime —
per the official limit-check sample `creon_algorism3__12__시세_연속_조회_제한_...`)
and `CpCybos.LimitRequestRemainTime` the ms until the budget resets.
`place_order`/`cancel_order` call `CybosPlus.wait_for_request_slot(0)` before
every `BlockRequest`, sleeping while the order budget is exhausted and
raising `rate_limited` after 30 s. This paces bursts (multi-buy, close-all
liquidation) inside the broker budget so the client is never blocked; the
`ret == 4` loop remains the backstop for races. Clients without the counter
surface are passed through untouched.

`bridge/plus.py` maps this to `BridgeError(code, message, action)`:

| error `code` | raised when | `action` hint |
|---|---|---|
| `com_unavailable` | pywin32 missing on this Python | install pywin32 on 32-bit Python |
| `not_connected` | `CpCybos.IsConnect == 0` | start Creon PLUS, re-login |
| `trade_init_failed` | `CpTdUtil.TradeInit(0) != 0` | check credentials / market hours |
| `request_failed` | `BlockRequest()` returned a non-OK code | wait for request slot and retry |
| `request_limit` | retried `4` `max_retries` times | retry later |
| `rate_limited` | no order request slot freed within timeout | wait and retry later |
| `remote_error` | `GetDibStatus() != 0` | check order/account state at broker |
| `invalid_input` | request validation failed | fix the request |
| `unknown_dispatch` | unknown symbolic COM name | developer error |

HTTP envelope is always `{ok: true, ...}` or
`{ok: false, error: {code, message, action?}}`. Malformed JSON / wrong
container types / unknown route → HTTP 400/404 with `ok: false`; business
errors → HTTP 200 with `ok: false` (see §9).

---

## 4. COM class reference — connection & account

### 4.1 `CpUtil.CpCybos` (connection status) — used in every sample
Source: all samples (e.g. `creon_algorism3__19__주식_현금_매도주문.py`).

| member | notes |
|---|---|
| `IsConnect` | int property, `1` connected / `0` not connected |
| `LimitRequestRemainTime` | int ms to wait after a `ret==4` (연속통신 제한) |

### 4.2 `CpUtil.CpCodeMgr` (code → name) — samples `__03`, `__08`, `__10`
Source: `creon_algorism3__08__...` etc.

| member | notes |
|---|---|
| `CodeToName(code)` | returns 종목명; accepts `A`-prefixed codes |

### 4.3 `CpTrade.CpTdUtil` (trade initialisation) — every order sample
Source: `creon_algorism3__19__주식_현금_매도주문.py`,
`creon_algorism__03__...BlockRequest_와_Request...` `InitPlusCheck()`.

| member | notes |
|---|---|
| `TradeInit(0)` | int return; `0` success. Called before any CpTrade use |
| `AccountNumber[0]` | first (default) 계좌번호 |
| `GoodsList(acc, 1)[0]` | first 주식상품 구분 code (`"01"`) |

`bridge/plus.py` re-runs `TradeInit(0)` at the start of each serialised job
and reads `AccountNumber[0]` / `GoodsList(acc,1)[0]`. **Assumption:** samples
call `TradeInit(0)` once at startup; calling it per request is treated as
idempotent — flagged because Daishin docs describe it as a process-level
init.

---

## 5. COM class reference — new orders & cancel

### 5.1 `CpTrade.CpTd0311` — 신규 주식 매수/매도 주문
Sources: `creon_algorism3__19__주식_현금_매도주문.py`,
`creon_algorism3__20__주식_현금_매수주문.py`,
`creon_algorism__01__주식_매수_주문_요청_및_에러_처리_방법.py`,
`creon_algorism3__01__주식_IOC_FOK_주문_테스트_예제.py`.

Inputs (SetInputValue index → meaning → value the bridge sends):

| index | meaning | value |
|---|---|---|
| 0 | 매수/매도 구분 | `"2"` 매수, `"1"` 매도 |
| 1 | 계좌번호 | from `CpTdUtil.AccountNumber[0]` |
| 2 | 상품구분 | from `CpTdUtil.GoodsList(acc,1)[0]` |
| 3 | 종목코드 | `"A"`+6 digits (trade form) |
| 4 | 주문수량 | int |
| 5 | 주문단가 | int; `0` for market orders (assumption, see §10) |
| 6 | (unused for new orders) | — |
| 7 | 주문조건구분코드 | `"0"` 기본 / `"1"` IOC / `"2"` FOK; bridge always sends `"0"` |
| 8 | 주문호가구분코드 | `"01"` 보통/지정가, `"03"` 시장가 (per IOC/FOK sample comment) |

`BlockRequest()` then `GetDibStatus()/GetDibMsg1()`. The sample comment in
`creon_algorism3__01__...` warns: *"매수 주문에 대한 구체적인 처리는
cpconclusion 으로 파악해야 한다"* — the definitive 주문번호/체결 arrives via
the realtime `DsCbo1.CpConclusion` object, not the blocking reply.

Outputs read by the bridge: `GetHeaderValue(7)` is treated as the new
주문번호 — the samples read header 7 as the new order number on the sibling
`CpTrade.CpTd0313` (정정) object in
`creon_algorism3__14__주식_주문_체결_실시간_처리_예제.py`. **Assumption:**
CpTd0311 exposes the same header layout as CpTd0313; the bridge therefore
returns header 7 but does **not** rely on it (real state comes from the
realtime conclusion callback).

### 5.2 `CpTrade.CpTd0314` — 주식 취소 주문
Sources: `creon_algorism3__08__...취소주문_일괄_취소...`,
`creon_algorism3__14__...`.

Inputs:

| index | meaning | value |
|---|---|---|
| 1 | 원주문번호 (order to cancel) | string |
| 2 | 계좌번호 | from `CpTdUtil` |
| 3 | 상품구분 | from `CpTdUtil` |
| 4 | 종목코드 | `"A"`+6 digits |
| 5 | 정정/취소 수량 | `0` ⇒ 잔량 취소 (cancel full remaining); else qty |

Both samples follow this identical input layout (the `__08` inline comments
mislabel index 2/3, the actual calls match `__14`). `BlockRequest()` +
`GetDibStatus()`.

### 5.3 `CpTrade.CpTd0313` — 주식 정정 주문 (reference, not exposed)
Source: `creon_algorism3__14__...`. Inputs 1 원주문번호, 2 계좌, 3 상품구분,
4 종목코드, 5 정정수량(0=잔량정정), 6 정정주문단가. Not part of the v1 API.

---

## 6. COM class reference — trade queries

### 6.1 `CpTrade.CpTd5339` — 미체결 주문 조회 (GET /orders)
Source: `creon_algorism3__08__주식_미체결_조회_및_실시간_미체결_업데이트_취소주문_일괄_취소_예제.py`.

Inputs:

| index | meaning | bridge value |
|---|---|---|
| 0 | 계좌번호 | from CpTdUtil |
| 1 | 상품구분 | from CpTdUtil |
| 4 | 전체/종목 구분 | `"0"` 전체 |
| 5 | 정렬 기준 | `"1"` 역순 |
| 6 | 전체 구분 | `"0"` |
| 7 | 요청 개수 | `20` (maximum per page) |

Header: `GetHeaderValue(5)` = 수신 개수 per page. Paging: repeat
`BlockRequest()` on the same object while `obj.Continue == True`.

Row mapping (`GetDataValue(col, i)`), from `__08` `orderData` fields:

| col | field | returned key |
|---|---|---|
| 1 | 주문번호 | `order_no` |
| 2 | 원주문번호 | `original_order_no` |
| 3 | 종목코드 (A-form) | `code` → stripped to 6 digits |
| 4 | 종목명 | `name` |
| 5 | 주문구분내용 | `description` |
| 6 | 주문수량 | `qty` |
| 7 | 주문단가 | `price` |
| 8 | 체결수량 | `filled_qty` |
| 9 | 신용구분 | `credit` |
| 11 | 정정/취소 가능수량 | `cancelable_qty` |
| 13 | 매매구분코드 (1/2) | `bs_code` |
| 17 | 대출일 | `loan_date` |
| 19 | 주문호가구분 내용 | `order_hoga_desc` |
| 21 | 주문호가구분코드 | `order_hoga_code` |

### 6.2 `CpTrade.CpTd6033` — 주식 잔고 (GET /positions, GET /balance)
Sources: `creon_algorism2__04__주식_잔고_실시간_조회_현재가_및_주문_체결_실시간_반영.py`,
`creon_algorism3__15__주식_잔고_종목_조회_및_실시간_현재가_업데이트.py`.

Inputs:

| index | meaning | bridge value |
|---|---|---|
| 0 | 계좌번호 | from CpTdUtil |
| 1 | 상품구분 | from CpTdUtil |
| 2 | 요청 건수 | `50` (maximum) |

Header: `GetHeaderValue(7)` = 수신 개수 per page (note: header 7 counts
rows while data col 7 holds 체결잔고수량 — the samples use both). Paging via
`obj.Continue`.

Row mapping (`GetDataValue(col, i)`), cross-referencing `2__04` and `__15`:

| col | field | returned key |
|---|---|---|
| 12 | 종목코드 (A-form) | `code` → stripped |
| 0 | 종목명 | `name` |
| 1 | 신용구분 (single char: space/Y/D/B/M/P/I) | `credit_type` |
| 2 | 대출일 | `loan_date` |
| 7 | 체결잔고수량 | `qty` |
| 9 | 평가금액 (천원미만 절사) | `eval_amount` |
| 11 | 평가손익 (천원미만 절사) | `eval_pnl` |
| 15 | 매도가능수량 | `sellable_qty` |
| 17 | 체결장부단가 | `avg_price` |

`GET /balance` runs the same CpTd6033 request and aggregates, using the
samples' own "매입금액 = 장부가 × 잔고수량" computation
(`creon_algorism2__04__...`):
`total_buy_amount` = Σ(체결장부단가 × 체결잔고수량),
`total_eval_amount` = Σ(평가금액), `total_eval_pnl` = Σ(평가손익).

Official CpTd6033 header fields (verified 2026-09-04 against the Daishin
helper doc "[계좌별 잔고 평가현황] CpTrade.CpTd6033", money2.creontrade.com
HTS_Plus_Helper seq=176 — NOT the legacy `trade-executor` indices, which
disagree with the official table and are unused here):

| header | field | bridge key |
|---|---|---|
| 0 | 계좌명 (string) | — |
| 1 | 결제잔고수량 | — |
| 2 | 체결잔고수량 | — |
| 3 | 총평가금액 (예수금·대주평가·잔고평가 감안) | `equity` (fallback: cash + total_eval) |
| 4 | 평가손익 | — |
| 6 | 대출금액 | `loan_amount` |
| 7 | 수신개수 | (paging) |
| 9 | D+2 예상예수금 | `d2_deposit` |
| 11 | 총평가 내 잔고평가금액 | — |

### 6.2a `CpTrade.CpTdNew5331A` — 계좌별 매수 가능금액/수량 (GET /balance)

Source: Daishin helper doc "[계좌별 매수 가능금액/수량] CpTrade.CpTdNew5331A"
(money2.creontrade.com HTS_Plus_Helper seq=171). The bridge calls it with an
**amount-only** query to learn how much cash is actually orderable for buys —
the missing "cash" figure the CpTd6033 rows cannot provide. User Q&A
(money2.daishin.com boardseq=60 seq=26823) confirms header 10 =
현금주문가능금액 is the number to check before a cash buy order.

Inputs:

| index | meaning | bridge value |
|---|---|---|
| 0 | 계좌번호 | from CpTdUtil |
| 1 | 상품구분 | from CpTdUtil |
| 2 | 종목코드 (수량조회 시) | `""` (amount query) |
| 3 | 주문호가구분 | `"01"` 보통 |
| 4 | 주문단가 | — |
| 5 | 미수발생 증거금 100% 징수 여부 | default |
| 6 | 조회구분 | `"1"` 금액조회 |

Header (금액조회):

| header | field | bridge key |
|---|---|---|
| 10 | 현금주문가능금액 | `cash` (primary) |
| 45 | 예수금 | `deposit` |
| 46 | 대용금 | — |
| 47 | 가능예수금 | `usable_deposit` / `withdrawable` |
| 5 | 증거금40% 주문가능금액 | — (cash-only trading) |

Degradation: when the buyable query errors (e.g. simulation server
unsupported) `cash` falls back to `d2_deposit` (CpTd6033 header 9) and the
response carries `buyable_error`; with no source at all `cash` is omitted and
the 64-bit core fails closed (no cash ⇒ no buy orders).

### 6.3 `CpTrade.CpTd6032` — 체결기준 주식 당일 매매 손익 (daily P&L)
Source: `creon_algorism2__02__체결기준_주식_당일_매매_손익_예제.py`.

Inputs: 0 계좌번호, 1 상품구분 (only).

Header: `GetHeaderValue(0)` = 수신 개수; summary on the first page:

| header | field | returned key |
|---|---|---|
| 1 | 잔고평가손익 합계 | `summary.eval_pnl` |
| 2 | 매도실현손익 합계 | `summary.realized_pnl` |
| 3 | 수익률 | `summary.return_rate` |

Row mapping (`GetDataValue(col, i)`):

| col | field | returned key |
|---|---|---|
| 12 | 종목코드 | `code` → stripped |
| 0 | 종목명 | `name` |
| 1 | 신용일자 | `loan_date` |
| 2 | 전일잔고 | `prev_balance_qty` |
| 3 | 금일매수수량 | `buy_qty` |
| 4 | 금일매도수량 | `sell_qty` |
| 5 | 금일잔고 | `today_balance_qty` |
| 6 | 평균매입단가 | `avg_buy_price` |
| 7 | 평균매도단가 | `avg_sell_price` |
| 8 | 현재가 | `current_price` |
| 9 | 잔량평가손익 | `eval_pnl` |
| 10 | 매도실현손익 | `realized_pnl` |
| 11 | 수익률 | `return_rate` |

---

## 7. COM class reference — quotes & realtime

### 7.1 `DsCbo1.StockMst` — 현재가 조회 (GET /quotes?codes=…)
Sources: `creon_algorism3__23__주식_현재가_조회_실시간.py`,
`creon_algorism3__01__...IOC_FOK...` (`CpRPCurrentPrice.Request`),
`creon_algorism__03__...`.

Input: `SetInputValue(0, code)` (A-form). `BlockRequest()` + status check.

Header mapping used:

| header | field | returned key |
|---|---|---|
| 0 | 종목코드 | `code` → stripped |
| 1 | 종목명 | `name` |
| 4 | 시간 | `time` |
| 11 | 종가/현재가 | `current` |
| 12 | 전일대비 | `diff` |
| 13 | 시가 | `open` |
| 14 | 고가 | `high` |
| 15 | 저가 | `low` |
| 16 | 매도호가(1차) | `offer1` |
| 17 | 매수호가(1차) | `bid1` |
| 18 | 거래량 | `volume` |
| 19 | 거래대금 | `amount` |
| 27 | 기준가 | `base` |

Multi-code batches in the samples (`creon_algorism3__21__...`,
`creon_algorism3__15__...`) use `CpSysDib.MarketEye`. The MarketEye field
ordering comments are **self-contradictory** across samples (requested field
list `[0,1,2,3,4,10,17]` is read back as if fields were requested in a
different order), so the bridge does **not** use MarketEye and instead issues
one `StockMst` BlockRequest per code. Flagged as a deliberate choice.

### 7.2 `DsCbo1.StockCur` — 실시간 현재가 (realtime per-stock)
Sources: `creon_algorism3__22__주식_복수종목_실시간_등록_해지.py`,
`creon_algorism3__23__주식_현재가_조회_실시간.py`, `2__04`.

Flow (samples): `Dispatch("DsCbo1.StockCur")` →
`win32com.client.WithEvents(obj, Handler)` → `obj.SetInputValue(0, "A" + code)`
→ `obj.Subscribe()`. `Handler.OnReceived` fires on the pump thread.

Header mapping (per stock-cur samples):

| header | meaning | snapshot key |
|---|---|---|
| 0 | 종목코드 | `code` (matches subscription) |
| 1 | 종목명 | `name` |
| 2 | 대비 | `diff` |
| 9 | 거래량 | `volume` |
| 13 | 현재가 | `current` |
| 17 | 순간체결수량 | `cont_volume` |
| 18 | 시간(초) | `time` |
| 19 | 예상체결 플래그 (ord '1'/'2') | `ex_flag` |

`RealtimeHub.latest_quotes()` returns the cache keyed by plain 6-digit code.

### 7.2b 주문 응답의 의미 — `order_no == 0` 은 "실패"가 아니다 (2026-09-10 실측)

`CpTd0311`(주문) 응답에서 header(7) 주문번호가 `0` 이고 DIB 메시지가
`94060 이미 처리되었습니다 (ordss.cststkord.ats)` 인 경우가 있다. 실측 결과:

| 종목 | 요청 수량 | 응답 | 실제 결과 |
|---|---|---|---|
| 073010 | 37주 @3,300 | order_no=0, 94060 | **37주 전량 체결** |
| 950250 | 34주 @3,605 | order_no=0, 94060 | **34주 전량 체결** |
| 424980 | 12주 @9,700 | order_no=0, 94060 | 미체결 → 수동 취소 |

즉 이 메시지는 "**이미 처리된(중복) 요청**"이라는 뜻이며, 주문의 성공/실패
판정에 쓸 수 없다. 체결 여부는 (1) `CpConclusion` 실시간(`flag=1 체결`) 또는
(2) 체결 후 `/positions` 로만 확정한다. 현재 브리지는 어느 쪽도 하지 않고
주문 응답을 그대로 돌려주므로, **주문 응답은 "체결 확인서"가 아니다**.

매도 쪽도 같다. 2026-09-21 실측: 매도 응답이 `order_no=0` +
`94059모의투자 정상처리 되었습니다 (ordss.cststkord.ats)` 였고 두 건(010640
33주, 036540 8주)은 전량 체결됐다. `94059`(정상처리)와 `94060`(이미 처리)은
문구만 다를 뿐 **둘 다 체결/미체결을 구분하지 않는다**.

2026-09-21 317830(매수 15주 @4,880)도 응답은 `94060` 이었지만 브로커에는
미체결 주문(주문번호 2548, filled_qty 0)만 있었고 보유는 없었다. 약 5분 뒤
실제로 체결됐다. 2026-09-17 288180, 2026-09-10 424980 과 같은 클래스.

**해결**: 브리지가 아니라 매매 코어가 확인한다.
`TraderEngine._confirm_entry()` 가 주문 직후 `/orders`(미체결 목록)와
`/positions` 를 읽어 `filled` / `partial` / `working` / `unconfirmed` 를
판정하고(`Config.confirm_entry_fills`, 기본 ON), `working`/`unconfirmed` 건은
`loop_state.json` 의 `pending_entries` 로 등록해 매 사이클 재검증한다
(OPERATIONS.md 8.3). 브리지는 지금처럼 주문 응답을 그대로 돌려주면 된다.

**체결 정산 지연 (중요)**: 주문 직후 `/positions` 는 수 초~1분 동안 일부
수량만 보여준다. 실측: 073010 은 14:55:51 에 3주 → 14:57:22 에 37주로 바뀌었다.
따라서
- 저널 정정(`tools/reconcile_journal.py`)은 **매수 후 최소 60초 이후**에 돌린다.
  그 전에 돌리면 브로커가 flat 으로 보여 방금 산 포지션을 청산 처리(룰 1)한다.
- 루프가 매 사이클 자동으로 reconcile 을 돌리지 않는 이유가 이것이다(수동 도구 유지).
- 부분체결이 실제로 발생하면 저널 수량이 브로커와 어긋난다 → 룰 6
  (`qty_fixed`, `reconcile.py`)이 브로커 체결 기준으로 수량/단가를 정정한다.

### 7.3 `DsCbo1.CpConclusion` — 실시간 주문/체결 (optional callback)
Sources: `creon_algorism3__01__...IOC_FOK...`, `creon_algorism3__08__...`,
`creon_algorism3__14__주식_주문_체결_실시간_처리_예제.py`.

`Dispatch("DsCbo1.CpConclusion")`, `WithEvents`, `Subscribe()` (no input
code — process-wide). Header mapping used by the bridge execution callback:

| header | meaning (sample dic/comment) | key |
|---|---|---|
| 14 | 체결 플래그 1체결 2확인 3거부 4접수 | `flag` |
| 3 | 주문/체결 수량 | `qty` |
| 4 | 주문/체결 가격 | `price` |
| 5 | 주문번호 | `order_no` |
| 6 | 원주문번호 | `original_order_no` |
| 9 | 종목코드 | `code` → stripped |
| 12 | 매수/매도 구분 1매도 2매수 | `bs_code` |
| 16 | 정정/취소 구분 1정상 2정정 3취소 | `amend_flag` |
| 18 | 주문호가구분코드 | `hoga_code` |
| 19 | 주문조건구분코드 | `condition_code` |
| 21 | 장부가 | `book_price` |
| 22 | 매도가능수량 | `sellable_qty` |
| 23 | 체결기준잔고수량 | `balance_qty` |

Only flag values are numeric in the samples (often compared as strings, e.g.
`i14 == "1"`); the bridge keeps them as strings.

---

## 8. RealtimeHub API summary

| method | behaviour |
|---|---|
| `subscribe(codes)` | registers plain codes, starts the pump thread if needed, enqueues a subscribe command |
| `unsubscribe(codes)` | removes codes and enqueues an unsubscribe command (cached quote stays) |
| `latest_quotes()` | thread-safe snapshot `{plain_code: quote}` |
| `executions()` | recorded conclusion events (only when `track_executions=True`) |
| `stop(timeout)` | clean shutdown: stops loop, `Unsubscribe()`s every active object, `CoUninitialize()` |

All realtime COM objects are created inside the pump thread, preserving STA
affinity. The pump loop is a headless equivalent of the samples' Qt loop /
`win32event.MsgWaitForMultipleObjects` example: `PumpWaitingMessages()` +
`time.sleep(0.02)`.

---

## 9. HTTP API

Base `http://127.0.0.1:8100` (configurable via env `CREON_BRIDGE_HOST`,
`CREON_BRIDGE_PORT` or CLI `--host/--port`). All responses JSON UTF-8.

| endpoint | method | body / query | ok payload |
|---|---|---|---|
| `/health` | GET | — | `{connected, ts}` |
| `/balance` | GET | — | `{balance: {...}, ts}` |
| `/positions` | GET | — | `{positions: [...]}` |
| `/orders` | GET | — | `{orders: [...]}` (미체결) |
| `/quotes` | GET | `?codes=005930,000660` optional | `{quotes: {...}}` or realtime cache |
| `/order` | POST | `{side, code, qty, price?, order_type?}` | `{order: {...}}` |
| `/cancel` | POST | `{order_no, code, qty?}` | `{cancel: {...}}` |
| `/subscribe` | POST | `{codes: [...]}` | `{subscribed}` |
| `/unsubscribe` | POST | `{codes: [...]}` | `{unsubscribed}` |
| `/shutdown` | POST | — | `{shutdown: true}` |

Validation: missing required fields / bad values → `ok:false,
code=invalid_input` (HTTP 200). Malformed JSON or a non-object body → HTTP
400 `code=bad_request`. Unknown route → HTTP 404. Handlers never raise past
`BaseHTTPRequestHandler`.

---

## 10. Assumptions and sample contradictions

1. **Market order price = 0.** No sample actually *executes* a `03` market
   order — `03` 시장가 appears only in the IOC/FOK sample comment
   (`"01: 보통 03 시장가 05 조건부지정가"`) and the flag dictionary
   `dicflag18`. The bridge sends 호가구분 `"03"` with 주문단가 `0`. Flagged.
2. **`CpTd0311` output header 7 = 주문번호.** Only verified on the 정정
   object `CpTd0313` (`GetHeaderValue(7)` in `creon_algorism3__14__...`);
   assumed identical for `CpTd0311`. The bridge returns it but does not rely
   on it — real fills arrive via `CpConclusion`.
3. **Repeated `TradeInit(0)`.** Samples init once; the bridge re-inits on
   every serialised job. Assumed idempotent.
4. **No 예수금/cash request in the samples.** `/balance` = CpTd6033 stock
   aggregate + CpTd6032 daily P&L. A dedicated deposit query would need a
   COM input table not present in the provided samples.
5. **`CpTd5339` may legally return `1`.** `creon_algorism3__08__...` only
   fails on `ret` 2/3 (and retries 4), tolerating `0` and `1`. The paged
   queries (`5339`, `6033`, `6032`) therefore accept `{0, 1}` as OK, while
   order/cancel objects (`0311`, `0314`) accept only `0` (per
   `creon_algorism__01__...` which treats any non-zero as error). This is
   the reason the bridge has two `ok_returns` sets.
6. **Balance code prefixes.** All `CpTrade.*` responses embed codes in the
   `A`+6 form (samples pass them straight back into order/cancel inputs that
   require the `A` form). The bridge strips to 6 digits on output.
7. **Realtime pump granularity.** `PumpWaitingMessages` polling (≈20 ms)
   replaces the samples' blocking `PumpMessages` / `MsgWaitForMultipleObjects`
   so shutdown is clean and no GUI loop exists. `win32event` is not required.
8. **`CpSysDib.MarketEye` avoided** because field-ordering comments differ
   across `__15`/`__21` (see §7.1); one `StockMst` per code is unambiguous.
9. **Real-time quote `ex_flag`.** Samples only print on `'1'` (동시호가) and
   `'2'` (장중). The bridge caches every tick and records `ex_flag` as an
   int so the consumer can filter; it does not drop out-of-hours ticks.

---

## 11. Live verification log — 2026-09-10, Creon PLUS 모의투자

Runtime: `C:\Users\jhshi\Python311-32\python.exe` (3.11.9, 32-bit),
`pywin32` present. Account `7770******` (모의투자), `equity = 5,000,000`,
0 positions, 0 open orders. Server start to first order: **all read paths
verified against live market data** (KRX session, 13:0x–13:3x KST).

### 11.1 Confirmed working against the live broker

| endpoint | observed |
|---|---|
| `GET /health` | `{ok:true, connected:true}` |
| `GET /balance` | `equity=5000000 d2_deposit=5000000 cash=5000000 loan_amount=0 positions_count=0` |
| `GET /positions` | `[]` |
| `GET /orders` | `[]` |
| `GET /quotes?codes=005930,000660` | real values (e.g. 005930 현재가 267,000 / 시 269,000 / 고 270,500 / 저 263,500 / 거래량 8,515,415) |
| `POST /subscribe` + `GET /quotes` | realtime ticks flowing (005930, ~40 ticks / 20 s, 거래량 monotonically increasing) |
| `run_trader_core.py status` (64-bit) | `bridge: connected=True`, equity/cash/positions read through the HTTP layer |

### 11.2 Findings that differ from the sample-derived assumptions

| # | assumption in this file | observed live | consequence |
|---|---|---|---|
| 1 | §7.2 `ex_flag` header 19 is the character `'1'`/`'2'` | it is the **ASCII code** — `50` = `ord('2')` (장중 체결) every tick | consumers must compare against `50`/`49` (or `chr()` it), not `'2'` |
| 2 | §6.2a `CpTdNew5331A` supplies `cash` | on 모의투자 it fails: `remote error -1: 94007 모의투자 매매불가 종목입니다 (ord.stk.dpmyinqr.selpsblqtyamt)` | `cash` silently degrades to the `d2_deposit` fallback and the response carries `buyable_error` — check for it before treating `cash` as a true buyable amount |
| 3 | §4.3 `GoodsList(acc, 1)[0]` example is `"01"` | returned **`"10"`** on this account | never hardcode the goods flag; read it from `CpTdUtil` (the bridge already does) |
| 4 | §1 "blocking calls can never starve the realtime pump" | true, but the reverse also matters: with no idle pump the blocking worker *silently swallows every realtime event* (see §1, verified 3 ways) | fixed in `plus.py`; do not remove the idle pump |
| 5 | §6.2 `equity` = CpTd6033 header(3) 총평가금액 (예수금·대주·잔고 감안) | with 5 positions filled, header(3) returned **594,590 = the stock evaluation alone**, while orderable cash was 4,401,227 | `equity` is now `cash + total_eval_amount` (4,995,817 in that sample) and the raw header is kept as `broker_total_eval`. Using the header as equity made the 64-bit core read an ~88 % daily loss, which would have tripped R4 (liquidate-all + halt) |
| 6 | `cash == 0` from a *successful* buyable query means an empty account | on 모의투자 the query fails but the code path can also yield `0` | `cash` now falls back to `d2_deposit` when the buyable figure is missing **or zero**, so a zero never silently becomes "no buying power" |

### 11.3 모의투자 (paper account) constraints to design around

* Buyable-cash query unsupported (finding 2) — size orders from
  `d2_deposit`/`cash` and expect the fallback.
* Some instruments are rejected outright as `모의투자 매매불가 종목`; the
  order path must surface the broker message rather than retrying blindly.
* Realtime quotes and order/execution events are available and identical in
  shape to the real server, so strategy logic can be developed here.

