# PHASE 2 BRIEF — creon-bridge v1 (Windows 32-bit Python, CybosPlus/Creon)

## Mission
Implement `bridge/` inside this repo: a **headless localhost JSON HTTP service** that wraps the
Daishin CybosPlus (Creon) COM API for a Windows **32-bit Python 3.11** runtime
(`C:\Users\jhshi\Python311-32\python.exe` on the laptop). It is the "creon-bridge" of a larger
agent-trading system: 32-bit side owns ALL Creon COM; everything else (strategy/risk/agent) is
64-bit and talks to this bridge only over HTTP on 127.0.0.1.

## Reference material (READ ONLY — never modify)
- `creon_extracted_v1/*.py` — official Daishin sample code, one module per feature
  (PyQt5 UI already stripped by an extractor; some modules still carry `## REVIEW` headers and
  `removed/` fragments hold the stripped UI — read originals if a module looks incomplete).
  Also `creon_src/*.txt` holds the raw text if you need the full original context.
Key sample modules to study:
  - Connect/init: `creon_algorism__03__...BlockRequest_와_Request_방식...` (InitPlusCheck pattern),
    `creon_algorism__01__주식_매수_주문...`
  - Orders: `creon_algorism3__19__주식_현금_매도주문.py`, `creon_algorism3__20__주식_현금_매수주문.py`
    (clean minimal), `creon_algorism3__01__주식_IOC_FOK...`, `creon_algorism3__08__주식_미체결_조회...취소주문_일괄_취소...`,
    `creon_algorism3__10__주식_예약_매수_매도_취소_조회...`
  - Balance/positions: `creon_algorism3__15__주식_잔고_종목_조회_및_실시간_현재가_업데이트.py`,
    `creon_algorism2__02__체결기준_주식_당일_매매_손익...`, `creon_algorism2__04__주식_잔고_실시간_조회...`
  - Realtime quotes: `creon_algorism3__23__주식_현재가_조회_실시간.py`,
    `creon_algorism3__21__주식_복수종목_조회_실시간.py`, `creon_algorism3__22__주식_복수종목_실시간_등록_해지.py`,
    `creon_algorism3__14__주식_주문_체결_실시간_처리...`, `creon_algorism2__03__실시간_분차트...`,
    `creon_algorism3__16__당일_상승률_상위_200...`, `creon_algorism3__13__VI_발동...`

## Non-negotiable constraints
1. Python 3.8+ compatible, **stdlib only** except `pywin32` (`win32com.client`, `pythoncom`,
   `ctypes` ok). Import win32com/pythoncom **lazily, inside functions** (never at module top level)
   so the whole package imports and unit-tests on Linux/CI without pywin32.
2. **NO PyQt5 / Qt / UI code anywhere.** No strategy, no risk engine, no order-caching decisions —
   this is pure execution plumbing. No persistence beyond the log file.
3. All COM object creation goes through one factory (`plus.py`) so tests can inject fakes.
4. **STA threading**: CybosPlus COM is apartment-threaded. Realtime event handling runs in its own
   background thread with its own `CoInitialize` + message pump (`pythoncom.PumpMessages()` or an
   equivalent headless loop — the samples relied on Qt's loop; you must design the headless
   equivalent). Blocking `BlockRequest` calls are serialized under a lock and must not block the
   realtime pump. Document this in `api_notes.md`.
5. Stock code convention: public API accepts 6 digits ("005930"). Internally normalize to the
   CybosPlus trade form **"A" + code** ("A005930") for CpTrade order inputs exactly as the samples
   do; balance/quote responses strip the prefix back to 6 digits. Normalization helpers must be
   unit-tested.
6. Order input tables MUST be derived from the samples, not guessed. Notably confirm for
   CpTrade.CpTd0311: input(0)=매수2/매도1, (3)=code, (4)=qty, (5)=price, (7)=주문조건
   0:기본 1:IOC 2:FOK, (8)=호가구분코드 ("01" 보통/지정가, market-order code as used by samples).
   Also derive cancel (미체결 취소) and 조회 object names/indexes from `__08`/`__10`.
   Deliverable `bridge/api_notes.md` documents every COM class used with its verified input/output
   index table + source sample filename. Flag any ambiguity you resolved with an assumption.

## Deliverables (create under `bridge/`)
1. `bridge/config.py` — port(8100), host(127.0.0.1), timeouts; overridable via env/CLI.
2. `bridge/plus.py` — CybosPlus session: `is_connected()`, `trade_init()`, factory methods;
   dispatch names centralised here.
3. `bridge/codes.py` — normalization helpers (`to_trade_code`, `to_plain_code`).
4. `bridge/orders.py` — buy/sell (limit & market), cancel, 미체결 list, balance/positions/daily PnL
   queries (whichever the samples support), each returning plain dicts.
5. `bridge/realtime.py` — RealtimeHub: `subscribe(codes)`, `unsubscribe(codes)`,
   `latest_quotes()` (thread-safe snapshot dict keyed by plain code), background pump thread,
   clean shutdown. Order-execution event callback hookup optional but preferred if samples show it
   simply (주문체결 실시간).
6. `bridge/server.py` — `http.server.ThreadingHTTPServer` on 127.0.0.1:8100, JSON UTF-8.
   Endpoints:
   - `GET /health` → {ok, connected, ts}
   - `GET /balance` · `GET /positions` · `GET /orders`(미체결) · `GET /quotes`
   - `POST /order`  body {side: "buy"|"sell", code, qty, price?, order_type: "limit"|"market"}
   - `POST /cancel` body {order_no, code, qty?}
   - `POST /subscribe` {codes:[...]} · `POST /unsubscribe` {codes:[...]}
   - `POST /shutdown`
   Responses always `{ok: true, ...}` or `{ok: false, error: {code, message, action?}}`.
   Validate bodies; 400 on malformed; never raise past the handler.
7. `bridge/api_notes.md` — COM reference tables (see constraint 6).
8. `run_bridge.py` (repo root) — CLI entry: `--port`, `--selftest` (runs stub smoke, exits),
   `--no-server` (connect checks only). Prints local endpoint URL on start.
9. `bridge/tests/` — `unittest` (no pytest dependency): a `stubs/win32com` fake package injected
   via `sys.modules` capturing SetInputValue/BlockRequest; tests for code normalization, order
   request construction (buy/sell/limit/market incl. A-prefix + 호가구분 values), error mapping
   (BlockRequest !=0 / GetDibStatus !=0), and an end-to-end HTTP test (server + fake plus:
   /health, /order happy & error path). **All tests must pass on Linux `python3` with no pywin32.**

## Verification (run these in the repo; iterate until clean)
```
python3 -m compileall -q bridge run_bridge.py
python3 -m unittest discover -s bridge/tests -v
```
Report: files created, test counts (assertions/tests run), key tables from api_notes.md,
assumptions you made, and anything in the samples that contradicts this brief.

## House rules
- Only create/edit files under `bridge/`, `run_bridge.py`, `bridge/api_notes.md`, `PHASE2_BRIEF.md`
  is reference (you may not need to touch it). Do NOT touch `creon_src/`, `creon_extracted_v1/`,
  `README.md`, or git history.
- Python code: PEP8, type hints on public functions, docstrings in English, ASCII-safe source
  (UTF-8 with no non-ASCII identifiers). Keep modules small and testable.
