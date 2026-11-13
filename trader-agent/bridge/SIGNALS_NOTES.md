# creon-bridge — SIGNALS_NOTES.md

COM recipe spec for the **signal-source** classes: the ones that let the
trading system generate its own stock candidates (ranking, 특징주, 실시간
뉴스/공시, HTS 종목검색). Companion to `bridge/api_notes.md` (which covers the
already-shipped order/quote/balance classes). Nothing here is implemented in
`bridge/plus.py` `DISPATCH_NAMES` yet.

**Evidence rule.** Every index/value claim below is checked against the *raw
Daishin sample text*, not against `CREON_ALGORISM_CATALOG.md`. Locators are
`<file> L<line>`; the section markers (`#16`, `#05`, `#04`, `#02`) are the
catalog's feature numbers, given for cross-reference only.

**Confidence tags.**
- **VERIFIED** — the exact call is read directly in the sample text.
- **ASSUMED** — inferred from a sibling class / comment; needs the noted test.
- **CONTRADICTION** — samples (or sample vs catalog) disagree; both readings shown.
- **NEEDS-LIVE-TEST** — must be exercised once against a live CBP session
  before hardcoding.

**The `ord()` question (applies to every table below).** Daishin samples write
`SetInputValue(0, ord('0'))` for *char-typed* inputs and plain ints
(`SetInputValue(3, 21)`, `SetInputValue(7, 0)`) for *numeric* inputs **on the
same object** — see `creon_algorism3.txt L5125–L5133` (CpSvrNew7043 mixes
`ord('0')`, `ord('2')`, plain `21`, plain `0`, plain `30` in one call site).
That co-existence is the strongest available evidence that the COM layer
accepts both forms and that `ord('x')` is the sample author's idiom for "this
parameter is a char". **Recommended default: reproduce the sample byte-for-byte
(so `ord('0')` → 48) and add one live test per class that re-sends the same
query with the bare int (0) and compares `GetDibStatus()`.** Where the two
disagree, keep the sample form; where both succeed, send the bare int for
clarity. Each affected table carries a `ord/char` note.

**Global request budget.** Every class below is a *quote* (`시세`) request, i.e.
it consumes the **type-1** budget: `CpUtil.CpCybos.GetLimitRemainCount(1)`.
`GetLimitRemainCount(t)` type map is read from
`creon_algorism3.txt L3881` (`0: 주문 관련 1: 시세 요청 관련 2: 실시간 요청
관련`) and L1297 (`GetLimitRemainCount(1)  # 1 시세 제한`) — VERIFIED. The
blocking gate pattern is `creon_algorism3.txt L1296–L1300`:

```python
while True:
    if g_objCpStatus.GetLimitRemainCount(1) <= 0:
        time.sleep(g_objCpStatus.LimitRequestRemainTime / 1000)
    objRq.BlockRequest()
```

`CpSvr7254` is the only signal class that actually applies this gate in the
samples (L1297); `CpSvrNew7043` and the Css* classes do **not** — but they issue
N `BlockRequest`s in a paging loop and therefore need the same gate added.

**Common threading rule (all classes).** CybosPlus is STA/apartment-threaded.
Dispatch, `SetInputValue`, `BlockRequest` and `WithEvents`/`Subscribe` must all
happen on **one** COM-initialised thread. For the bridge: all blocking recipes
(`7043`, `CpMarketWatch` one-shot, `CssStgList`, `CssStgFind`,
`CssWatchStgSubscribe`, `CssWatchStgControl`, `CpSvr7210d`, `7221`, `7254`) go
through the existing serialised STA worker `CybosPlus.invoke(func)`. All
realtime recipes (`CpMarketWatchS`, `CpSvr8092S`, `CssAlert`) are created,
`SetInputValue`d, `WithEvents`ed and `Subscribe`d **inside the realtime pump
thread** (the `bridge/realtime.py` analogue of the samples' `app.exec_()` /
`MsgWaitForMultipleObjects` loop).

---

## 1. `CpSysDib.CpSvrNew7043` — 당일 상승률/하락률 상위 랭킹

- Sample: `creon_algorism3.txt` §"[파이썬] 당일 상승률 상위 200 종목 실시간
  통신 예제" (section marker at **L5075**); class `Cp7043` at **L5121–L5174**.
- Catalog ref: ⭐ #16.

### 1.1 Dispatch / threading

| item | value |
|---|---|
| progid | `"CpSysDib.CpSvrNew7043"` — **VERIFIED**, `L5124` |
| shape | blocking request object (needs `BlockRequest`) |
| thread | serialised STA worker (`CybosPlus.invoke`) |
| limit type | 1 (시세) |
| paging | `obj.Continue` boolean property — **VERIFIED**, `L5164` |

The class is constructed **once** in `__init__` and all nine `SetInputValue`s are
sent once at construction (`L5122–L5133`); the paging loop reuses the same
object, calling only `BlockRequest()`. So paging does **not** re-send inputs.

### 1.2 SetInputValue table

| idx | sample call (`L5125–L5133`) | meaning (sample comment) | legal values | confidence |
|---|---|---|---|---|
| 0 | `ord('0')` | 시장: 거래소 + 코스닥 | `'0'` only value in sample; comment says 거래소+코스닥 | VERIFIED ('0' = 거래소+코스닥); NEEDS-LIVE-TEST for 코스닥-only / 거래소-only codes |
| 1 | `ord('2')` | 상승 (등락 방향) | `2` = 상승 in sample | VERIFIED (`2`=상승); other codes (e.g. `1`=하락) **ASSUMED**, not in sample |
| 2 | `ord('1')` | 당일 (기간) | `1` = 당일 | VERIFIED; other periods **ASSUMED** |
| 3 | `21` **plain int** | 전일 대비 상위 순 (정렬키) | `21` | VERIFIED (`21`), other sort keys **ASSUMED** |
| 4 | `ord('1')` | 관리 종목 제외 | `1` = 제외 | VERIFIED (`1`) |
| 5 | `ord('0')` | 거래량 필터 — 전체 | `0` = 전체 | VERIFIED (`0`); non-zero thresholds **ASSUMED** |
| 6 | `ord('0')` | 표시 항목 선택 — `'0'` = 시가대비 | `0` | VERIFIED (`0`), meaning only from comment |
| 7 | `0` **plain int** | 등락율 시작 (%) | `0` | VERIFIED |
| 8 | `30` **plain int** | 등락율 끝 (%) | `30` | VERIFIED |

Notes that matter for a tradeable filter:
- indices **7/8 are a percentage *window***: the sample asks for `0..30 %`, so
  **상한가(+30 %) stocks are excluded by their own filter**. For a 급등주 scan
  set idx 8 to `100` (e.g. `0..100`) — but the sample never does, so
  `100` is **ASSUMED / NEEDS-LIVE-TEST**.
- idx 4 = `1` (관리종목 제외) is a free risk filter — keep it on.
- The catalog warns the ord() args "may actually mean the integer 0". The
  sample itself is the counter-evidence (it mixes `ord('0')` and plain `21`),
  so **send exactly what the sample sends**. Track as `ord/char` per §0.

### 1.3 BlockRequest / status handling

**VERIFIED**, `L5136–L5147`:

```python
self.objRq.BlockRequest()
rqStatus = self.objRq.GetDibStatus()
rqRet    = self.objRq.GetDibMsg1()
if rqStatus != 0:
    return False          # <-- the ONLY failure test in the sample
```

- The sample **ignores the `BlockRequest()` return value entirely** (contrast
  the order samples, `api_notes.md` §3). **OK = `GetDibStatus() == 0`**, for any
  `BlockRequest` return. Bridge policy: treat `ret == 4` (연속통신 제한) as
  retry-after-`LimitRequestRemainTime` like the order path, and `{0,1}` as OK;
  any other non-zero → `request_failed`.
- `GetDibMsg1()` is a Korean diagnostic string (logged only).

### 1.4 GetHeaderValue table — **VERIFIED**, `L5145–L5146`

| header | meaning | bridge key |
|---|---|---|
| 0 | 이번 수신 종목 수 (`cnt`) | `page_count` |
| 1 | 전체 종목 수 (`cntTotal`) | `total_count` |

### 1.5 GetDataValue column table — **VERIFIED**, `L5149–L5158`

| col | field (Korean) | bridge key |
|---|---|---|
| 0 | 코드 | `code` (strip `A` per `bridge/codes.py`) |
| 1 | 종목명 | `name` |
| 3 | 대비부호 | `diff_sign` |
| 4 | 대비 | `diff` |
| 6 | 거래량 | `volume` |

Cols 2, 5, 7+ are **not read** in the sample → unknown; do not key off them.
Note the column order is *not* dense (0,1,3,4,6) — mirror it exactly.

### 1.6 Paging loop — **VERIFIED**, `L5160–L5174`

```python
def Request(self, retCode):
    self.rq7043(retCode)                     # first page
    while self.objRq.Continue:               # Continue is a bool property
        self.rq7043(retCode)
        if len(retCode) >= 200:              # hard cap
            break
    return True
# rq7043() itself also breaks the *row* loop at len(retcode) >= 200 (L5152-5153)
```

Bridge port: accumulate into a list, stop at the caller's `limit` (default
200) **or** when `Continue` goes False — whichever first. `Continue` is read as
a Python bool.

Rate-limit interaction: one `BlockRequest` per page (≈ 15–20 rows/page), so a
200-code collection is ~10–14 quote requests. Must call
`GetLimitRemainCount(1)` between pages (§0).

---

## 2. `CpSysDib.CpMarketWatch` (one-shot) + `CpSysDib.CpMarketWatchS` (realtime) — 특징주 포착

- Sample: `creon_algorism2.txt` §"특징주 포착" (marker **L1487**,
  window title `#8092 Market-Watch` at **L1542**). Catalog ref: ⭐ #05, 298L.

### 2.1 `CpSysDib.CpMarketWatch` — one-shot history

| item | value | locator |
|---|---|---|
| progid | `'CpSysDib.CpMarketWatch'` | `L1490` VERIFIED |
| shape | blocking (`BlockRequest`) | `L1505` VERIFIED |
| thread | serialised STA worker | — |
| limit type | 1 (시세) | ASSUMED (no limit gate in sample) |

**SetInputValue** — `L1499–L1503`:

| idx | sample value | meaning (comment `L1500`) | confidence |
|---|---|---|---|
| 0 | `code` (string; `'*'` = 전종목) | 종목코드 or `'*'` | VERIFIED (`L1524`+`L1563` `Request('*', …)`) |
| 1 | `'1,2,10,11,12,13'` | `1` 종목뉴스, `2` 공시정보, `10` 외국계 창구 첫매수, `11` 첫매도, `12` 외국인 순매수, `13` 순매도 | VERIFIED (comment `L1500`); this is a **comma-joined string**, not a list |
| 2 | `0` | 시작 시간 — `0` = 처음부터 | VERIFIED |

Idx 1 accepts the **same numeric category codes as the realtime `diccode`
dictionary** (§2.3), so the one-shot filter and the realtime filter share one
dictionary.

**Status:** `BlockRequest()`; `GetDibStatus() != 0` → fail (`L1505–L1508`,
**VERIFIED**). `BlockRequest` return value ignored.

**Header:** `GetHeaderValue(2)` = 수신 개수 (row count) — **VERIFIED**,
`L1510`. (Headers 0/1 not read.)

**Row columns** — `L1514–L1520`, **VERIFIED**:

| col | field | bridge key |
|---|---|---|
| 0 | 시간 (HHMM int; `divmod(t,100)`) | `time` (`"HH:MM"`) |
| 1 | 코드 | `code` |
| 2 | *(not read)* | — |
| 3 | 카테고리 (code) | `category` |
| 4 | 특이사항 (text) | `detail` |

⚠️ **The one-shot object reads category at col 3; the realtime object reads it
at col 2** (§2.3). Do not share the row parser.

### 2.2 `CpSysDib.CpMarketWatchS` — realtime 특징주

| item | value | locator |
|---|---|---|
| progid | `'CpSysDib.CpMarketWatchS'` | `L1480` VERIFIED |
| shape | realtime: `Dispatch` + `WithEvents` + `Subscribe()` | `L1467–L1469` VERIFIED |
| subscribe input | `SetInputValue(0, var)` **only if `len(var) > 0`** | `L1464–L1465` VERIFIED |
| sample subscribe value | `'*'` (from `Request('*', self)`) | `L1563`→`L1524` VERIFIED |
| thread | realtime pump thread | — |

Same `CpPublish` base as `CpSvr8092S` (§3): `Subscribe(var, caller)` unsubscribes
first (`L1461–L1462`), sets input 0 if non-empty, then
`WithEvents(self.obj, CpEvent)` → `handler.set_params(obj, name, caller)` → `Subscribe()`.

**OnReceived (name == `'marketwatch'`)** — `L1412–L1435`, **VERIFIED**:

| access | meaning |
|---|---|
| `GetHeaderValue(0)` | 코드 (`code`) |
| `GetHeaderValue(2)` | 건수 (`cnt` — iterate rows) |
| `GetDataValue(0, i)` | 시간 (HHMM int) |
| `GetDataValue(1, i)` | 갱신/취소 flag — `ord('c')` ⇒ `'[취소]'` prefix (`L1427–L1428`) |
| `GetDataValue(2, i)` | 카테고리 code → `diccode[cate]` |

Headers 1, 3+ not read. Note `GetHeaderValue(2)` = row count here, but in the
*one-shot* object header 2 is also the row count yet the category moved columns
— again, separate parsers.

### 2.3 The `diccode` category dictionary — **VERIFIED verbatim**, `L1359–L1407`

This is the **core asset** of the class (a tradeable event taxonomy). Copy it
whole; do not trim.

| code | meaning |
|---|---|
| 10 | 외국계증권사창구첫매수 |
| 11 | 외국계증권사창구첫매도 |
| 12 | 외국인순매수 |
| 13 | 외국인순매도 |
| 21 | 전일거래량갱신 |
| 22 | 최근5일거래량최고갱신 |
| 23 | 최근5일매물대돌파 |
| 24 | 최근60일매물대돌파 |
| 28 | 최근5일첫상한가 |
| 29 | 최근5일신고가갱신 |
| 30 | 최근5일신저가갱신 |
| 31 | 상한가직전 |
| 32 | 하한가직전 |
| 41 | 주가 5MA 상향돌파 |
| 42 | 주가 5MA 하향돌파 |
| 43 | 거래량 5MA 상향돌파 |
| 44 | 주가데드크로스(5MA < 20MA) |
| 45 | 주가골든크로스(5MA > 20MA) |
| 46 | MACD 매수-Signal(9) 상향돌파 |
| 47 | MACD 매도-Signal(9) 하향돌파 |
| 48 | CCI 매수-기준선(-100) 상향돌파 |
| 49 | CCI 매도-기준선(100) 하향돌파 |
| 50 | Stochastic(10,5,5) 매수-기준선 상향돌파 |
| 51 | Stochastic(10,5,5) 매도-기준선 하향돌파 |
| 52 | Stochastic(10,5,5) 매수- %K%D 교차 |
| 53 | Stochastic(10,5,5) 매도- %K%D 교차 |
| 54 | Sonar 매수-Signal(9) 상향돌파 |
| 55 | Sonar 매도-Signal(9) 하향돌파 |
| 56 | Momentum 매수-기준선(100) 상향돌파 |
| 57 | Momentum 매도-기준선(100) 하향돌파 |
| 58 | RSI(14) 매수-Signal(9) 상향돌파 |
| 59 | RSI(14) 매도-Signal(9) 하향돌파 |
| 60 | Volume Oscillator 매수-Signal(9) 상향돌파 |
| 61 | Volume Oscillator 매도-Signal(9) 하향돌파 |
| 62 | Price roc 매수-Signal(9) 상향돌파 |
| 63 | Price roc 매도-Signal(9) 하향돌파 |
| 64 | 일목균형표 매수-전환선 > 기준선 상향교차 |
| 65 | 일목균형표 매도-전환선 < 기준선 하향교차 |
| 66 | 일목균형표 매수-주가가 선행스팬 상향돌파 |
| 67 | 일목균형표 매도-주가가 선행스팬 하향돌파 |
| 68 | 삼선전환도-양전환 |
| 69 | 삼선전환도-음전환 |
| 70 | 캔들패턴-상승반전형 |
| 71 | 캔들패턴-하락반전형 |
| 81 | 단기급락후 5MA 상향돌파 |
| 82 | 주가이동평균밀집-5%이내 |
| 83 | 눌림목재상승-20MA 지지 |

Grouping for signal routing: **10–13** 외국계/외국인 수급, **21–24** 거래량·매물대,
**28–32** 상한가/신고가 이벤트, **41–71** 기술적 크로스, **81–83** 눌림목/이동평균.
The dictionary is confirmed by the catalog's grouping and by the codes present
in the sample — **VERIFIED**, no gaps in the sample's own list.

### 2.4 Suggested bridge surface

`GET /signals/features?code=*&categories=10,31,46` →
`{events: [{time, code, name, category, category_text, detail, canceled}]}`.

---

## 3. `Dscbo1.CpSvr8092S` — 실시간 뉴스/공시 feed

- Sample: `creon_algorism2.txt` **L1483–L1485** (`CpPB8092news`), handler
  `OnReceived` branch `'marketnews'` **L1438–L1452**. Catalog ref: ⭐ #05.

⚠️ **PROGID is `Dscbo1.…` in the sample but the catalog writes `Dscbo1`/`DsCbo1`
inconsistently.** The sample text at `L1485` is
`super().__init__('marketnews', 'Dscbo1.CpSvr8092S')` — copy the case
**exactly as the sample** and, if Dispatch fails, try the `DsCbo1` capital-B
variant. PrologID case-sensitivity is **NEEDS-LIVE-TEST** (win32com Dispatch is
usually case-insensitive via the registry, but this one is flagged).

| item | value | locator |
|---|---|---|
| progid | `'Dscbo1.CpSvr8092S'` | `L1485` VERIFIED (case as written) |
| shape | realtime `Dispatch` + `WithEvents` + `Subscribe()` | `L1467–L1469` VERIFIED |
| subscribe input | `SetInputValue(0, var)` if `len(var) > 0` | `L1464–L1465` VERIFIED |
| sample value | `'*'` (전종목) | `L1525` VERIFIED |
| thread | realtime pump thread | — |

**OnReceived (name == `'marketnews'`)** — `L1438–L1452`, **VERIFIED**:

| access | meaning | notes |
|---|---|---|
| `GetHeaderValue(0)` | 갱신 flag — `ord('D')` ⇒ `'[삭제]'` prefix (`L1442–L1443`) | delete/갱신 flag requested in the task |
| `GetHeaderValue(1)` | 코드 | → `CpCodeMgr.CodeToName` |
| `GetHeaderValue(2)` | 시간 (HHMM int) | `divmod(t,100)` |
| `GetHeaderValue(3)` | *(not read)* | — |
| `GetHeaderValue(4)` | 카테고리 code | read into `cate` but **never used** (`L1449`) → meaning ASSUMED |
| `GetHeaderValue(5)` | 특이사항 / 뉴스·공시 본문 | the payload |

All values come from `GetHeaderValue` — this class uses **no** `GetDataValue`.
Header 0 is the 갱신/삭제 flag; only the value `ord('D')` is exercised in the
sample (ASSUMED: any other value = add/update). `GetHeaderValue(4)`'s exact
codes are **ASSUMED** (unused in sample) — treat as opaque until a live test
dumps it.

Suggested bridge surface: `GET /signals/news` (ring buffer) — each event
`{flag, code, name, time, category, text, deleted}`.

---

## 4. HTS 종목검색 — `CssStgList` / `CssStgFind` / `CssWatchStgSubscribe` / `CssWatchStgControl` / `CssAlert`

Two samples implement the identical flow:
- `creon_algorism3.txt` §"종목검색 서비스를 이용하여 예제 전략 조건 조회하기"
  (**L329**), class `Cp8537` **L343–L407** (list + find only, no monitoring).
- `creon_algorism.txt` §"종목검색 조회 및 실시간 감시" (**L1268**), class
  `Cp8537` **L1340–L1510** (full flow **incl.** monitoring + realtime).

The five-step flow (comment block at `creon_algorism.txt L1561–L1591`, VERIFIED):

```
1  CssStgList             → 전략 목록 (name/ID/…)
2  CssStgFind(id)         → 그 전략의 합격 종목코드 목록   (findByStgID)
3  CssWatchStgSubscribe(id) → 감시일련번호 monID
4  CssWatchStgControl(id, monID, '1'=start|'3'=cancel) → 감시 상태
   CssAlert (WithEvents)  → 실시간 합격/퇴출 이벤트
```

**Hard constraint (VERIFIED, `creon_algorism.txt L1578–L1580`):** if the search
returns **≥ 200** codes the sample refuses to start realtime monitoring
(`'검색종목이 200 을 초과할 경우 실시간 감시 불가'`). Bridge must enforce the
same 200 cap before subscribing. The comment at `L1562` also warns that the
number of simultaneous realtime 감시 is limited, so the flow calls `Clear()`
(which `stopAllStgControl()`s everything) before switching strategy
(`L1561–L1563`) — the bridge must track and cancel its own monitors the same way.

### 4.1 `CpSysDib.CssStgList`

| item | value | locator |
|---|---|---|
| progid | `"CpSysDib.CssStgList"` | a1 `L1357`, a3 `L351` VERIFIED |
| shape | blocking | — |
| thread | serialised STA worker | — |
| limit type | 1 (시세) | ASSUMED |

**SetInputValue** — a1 `L1360–L1363`, a3 `L354`:

| idx | value | meaning | confidence |
|---|---|---|---|
| 0 | `ord('0')` = 예제전략 · `ord('1')` = 나의전략 | 요청구분 | VERIFIED (both values exercised: a1 `L1361` vs `L1363`) |

**Status:** `BlockRequest()`; `GetDibStatus() != 0` → fail (a1 `L1367–L1371`).
`BlockRequest` return ignored.

**Header** — VERIFIED a1 `L1373–L1374`, a3 `L364–L365`:

| header | meaning | bridge key |
|---|---|---|
| 0 | 전략 목록 수 (long) | `count` |
| 1 | 요청구분 (char) | `req_kind` |

**Data columns** — VERIFIED a1 `L1380–L1386`, a3 `L371–L377`:

| col | field (Korean) | bridge key |
|---|---|---|
| 0 | 전략명 | `name` |
| 1 | ID | `strategy_id` |
| 2 | 전략등록일시 | `registered_at` |
| 3 | 작성자필명 | `author` |
| 4 | 평균종목수 | `avg_count` |
| 5 | 평균승률 | `avg_win_rate` |
| 6 | 평균수익 | `avg_profit` |

No paging — single `BlockRequest` returns the whole list.

### 4.2 `CpSysDib.CssStgFind`

| item | value | locator |
|---|---|---|
| progid | `"CpSysDib.CssStgFind"` | a1 `L1395`, a3 `L386` VERIFIED |
| SetInputValue(0, …) | `id` = the `ID` from §4.1 (string) | a1 `L1396`, a3 `L387` VERIFIED |
| thread | serialised STA worker | — |
| limit type | 1 (시세) | ASSUMED |

**Status:** `BlockRequest()`; `GetDibStatus() != 0` → fail (a1 `L1399–L1403`).

**Header** — VERIFIED a1 `L1405–L1407`, a3 `L396–L398`:

| header | meaning |
|---|---|
| 0 | 검색된 결과 종목 수 (long) — this page |
| 1 | 총 검색 종목 수 (long) |
| 2 | 검색시간 (string) |

**Data:** col 0 = `code` (VERIFIED a1 `L1412`, a3 `L403`). The sample passes it
straight into `CpUtil.CpStockCode.CodeToName(code)` / `CpCodeMgr.CodeToName`
(`a1 L1413`, `a3 L404`) → the code is in `A`-prefixed form.
**ASSUMED** (needs one live test): the `A` prefix; use
`bridge.codes.to_trade_code` / `to_plain_code` to normalise both ways.
No `Continue` paging is used by the sample — `cnt` rows of one page only;
whether the object pages at all is **NEEDS-LIVE-TEST** (test a broad strategy
whose `총 검색 종목 수` > `검색된 결과 종목 수`).

### 4.3 `CpSysDib.CssWatchStgSubscribe` — get 감시일련번호

- class `Cp8537.requestMonitorID`, `creon_algorism.txt L1418–L1439`.
- `SetInputValue(0, id)` (`L1420`), `BlockRequest()` (`L1421`).
- `GetDibStatus() != 0` → `(False, 0)` (`L1425–L1429`).
- **`GetHeaderValue(0)` = `monID` (감시일련번호)** — VERIFIED `L1431`.
  `monID == 0` ⇒ 실패 (`L1432–L1434`).
- Comment `L1436–L1438` (VERIFIED): if no strategy is being monitored yet the
  serial returns **1**; if others are already being monitored, a new serial
  unique to that 통신 ID is returned. **Bridge must key monitors by this monID,
  not by strategy ID.**

### 4.4 `CpSysDib.CssWatchStgControl` — start/stop monitoring

- class `Cp8537.requestStgControl`, `creon_algorism.txt L1441–L1488`.
- `SetInputValue(0, id)`, `SetInputValue(1, monID)` (`L1443–L1444`).
- **`SetInputValue(2, ord('1'))` = 감시시작** / **`SetInputValue(2, ord('3'))`
  = 감시취소** — VERIFIED `L1448` / `L1451` (task's '1'/'3' confirmed).
- `BlockRequest()` (`L1453`); `GetDibStatus() != 0` → `(False, '')`
  (`L1457–L1461`).
- **`GetHeaderValue(0)` = 상태** — VERIFIED `L1463–L1472`:
  `0` 초기상태 · `1` 감시중 · `2` 감시중단 · `3` 등록취소.
- Side effect (VERIFIED `L1476–L1478`): after every control call, if not
  already subscribed it does `self.objpb.Subscribe('', self)` — i.e. the
  `CssAlert` realtime object is subscribed **once, with an empty input**
  (`len('') == 0` ⇒ `SetInputValue` is skipped). It also maintains
  `self.monList[id] = monID` and deletes on cancel (`L1481–L1485`).

### 4.5 `CpSysDib.CssAlert` — realtime 합격종목 event

- `class CpPBCssAlert(CpPublish)`, progid `'CpSysDib.CssAlert'`, name
  `'cssalert'` — VERIFIED `creon_algorism.txt L1334–L1336`.
- Subscribed with an **empty** input (`Subscribe('')`); verify
  `SetInputValue` is never called for it.
- **OnReceived (name == `'cssalert'`)** — VERIFIED `L1288–L1304`, all via
  `GetHeaderValue`:

| header | meaning | bridge key |
|---|---|---|
| 0 | 전략ID | `strategy_id` |
| 1 | 감시일련번호 | `monitor_id` |
| 2 | 코드 | `code` |
| 3 | IN/OUT flag — `ord('1')` = 진입, `ord('2')` = 퇴출 | `inout` |
| 4 | 시각 | `time` |
| 5 | 현재가 | `price` |

- The caller filters events (`checkRealtimeStg`, `L1500–L1510`): **only forward
  the event if `strategy_id` is in its own monitor map AND
  `monitor_id == monList[strategy_id]`** — otherwise ignore. The bridge must
  keep the same (strategy_id, monitor_id) pair check to avoid shipping stale
  events from a cancelled monitor.

Suggested bridge surface: `POST /signals/search/run {strategy_id}` → codes;
`POST /signals/search/watch {strategy_id}` → `{monitor_id}`; `DELETE …/watch`
→ cancel; plus a realtime event stream `/signals/conditional`.

---

## 5. Per-stock / per-investor 매매동향 (lower priority)

### 5.1 `CpSysDib.CpSvr7210d` — 종목별 투자자 매매동향(잠정)

- Sample: `creon_algorism3.txt` §"종목별 투자자 매매동향 (잠정)데이터"
  (**L482**), class `Cp7210` **L496–L553**. Catalog ref: #03.

| item | value | locator |
|---|---|---|
| progid | `"CpSysDib.CpSvr7210d"` | `L506` VERIFIED |
| thread | serialised STA worker | — |
| limit type | 1 (시세) | ASSUMED |

**SetInputValue** — `L509–L512`, all VALUES VERIFIED; extra legal values are
from the sample's own comments:

| idx | sample value | meaning (comment) | confidence |
|---|---|---|---|
| 0 | `'0'` (**string**, not `ord`) | 시장: `0` 전체 / `1` 거래소 / `2` 코스닥 / `3` 업종 / `4` 관심종목 | VERIFIED sample = `'0'` 전체; other codes from comment (ASSUMED) |
| 1 | `ord('0')` | `0` 수량 / `1` 금액 | VERIFIED |
| 2 | `investFlag` (int `1` or `2` in the two UI buttons) | `0` 종목 / `1` 외국인 / `2` 기관계 / `3` 보험기타 / `4` 투신 … | VERIFIED sample calls `request(2,…)` 기관계 `L601`, `request(1,…)` 외국인 `L606` |
| 3 | `ord('0')` | `0` 상위순 / `1` 하위순 | VERIFIED |

**Note the mix again:** idx 0 is a plain **string** `'0'`, idx 1/3 are
`ord('0')`, idx 2 a bare int. Another data point for §0.

**Header** — VERIFIED `L524–L526`: `0` = 수신 수, `1` = 집계날짜, `2` = 집계시간.

**Data columns** — VERIFIED `L531–L544`:

| col | field | col | field |
|---|---|---|---|
| 0 | code | 7 | 기관계 |
| 1 | 종목명 | 8 | 보험기타금융 |
| 2 | 현재가 | 9 | 투신 |
| 3 | 대비 | 10 | 은행 |
| 4 | 대비율 | 11 | 연기금 |
| 5 | 거래량 | 12 | 국가지자체 |
| 6 | 외국인 | 13 | 기타법인 |

**Paging:** `maxRqCont = 1` so the sample breaks after a single page
(`L502`, `L548–L549`); the `while` + `Continue` structure (`L508`, `L551–L552`)
is present but only exercised once.

### 5.2 `CpSysDib.CpSvrNew7221` — 투자자별 매매 종합

- Sample: `creon_algorism.txt` §"투자자별 매매 종합" (**L1614**), class
  `Cp7221` **L1647–L1700**. Catalog ref: #05 (a1).

| item | value | locator |
|---|---|---|
| progid | `"CpSysDib.CpSvrNew7221"` | `L1649` VERIFIED |
| SetInputValue(0, …) | `ord('1')` — comment `옵션금액 선물계약` | `L1677` VERIFIED (meaning only from comment) |
| header | `GetHeaderValue(1)` = 수신 수 | `L1686` VERIFIED |
| thread | serialised STA worker | — |

**Row addressing is unusual — VERIFIED `L1687–L1697`:** the sample iterates the
`InvestIndex` dict (keys `0`…`22`, instrument/market types — full table
`L1650–L1674`) and reads **`GetDataValue(col, key)`** where `key` is the
instrument index, i.e. **rows are keyed by instrument type**, not by a returned
count. Columns:

| col | field | col | field |
|---|---|---|---|
| 0 | 개인매도 | 5 | 외국인순매수 |
| 1 | 개인매수 | 6 | 기관매도 |
| 2 | 개인순매수 | 7 | 기관매수 |
| 3 | 외국인매도 | 8 | 기관순매수 |
| 4 | 외국인매수 | | |

Because the loop reads `range(len(InvestIndex))` implicitly, `cnt`
(`GetHeaderValue(1)`) is read but unused — **row count vs instrument-index
range must be confirmed live** (NEEDS-LIVE-TEST: whether `GetDataValue(col, r)`
r = 0..cnt-1 in the same instrument order).

### 5.3 `CpSysDib.CpSvr7254` — 매매입체분석(투자주체별 일자별)

- Sample: `creon_algorism3.txt` §"매매입체분석(투자주체별현황)" (**L1253**),
  class `CpRp7354` **L1273–L1343**. Catalog ref: #06.

| item | value | locator |
|---|---|---|
| progid | `'CpSysDib.CpSvr7254'` | `L1283` VERIFIED |
| thread | serialised STA worker | — |
| limit type | 1 (시세) — **the only signal class that gates on it** | `L1297` VERIFIED |

**SetInputValue** — `L1284–L1288`:

| idx | sample value | meaning (comment) | confidence |
|---|---|---|---|
| 0 | `code` — `'A000660'` | 종목코드 (`A`-form) | VERIFIED `L1284`, sample call `L1377` |
| 1 | `6` | 일자별 | VERIFIED (comment `# 일자별`); other granularities ASSUMED |
| 2 | *(not set)* | — | — |
| 3 | *(not set)* | — | — |
| 4 | `ord('0')` | `'0'` 순매수 / `'1'` 매매비중 | VERIFIED |
| 5 | `0` **plain int** | `'전체` (전체 = 0) | VERIFIED (comment truncated in source) |
| 6 | `ord('1')` | `'1'` 순매수량 / `'2'` 추정금액(백만) | VERIFIED |

Indices 2 and 3 are **never set** by the sample — leave them unset rather than
guessing zeros.

**Status/limit loop** — VERIFIED `L1296–L1308`: gate
`GetLimitRemainCount(1) <= 0` → sleep `LimitRequestRemainTime/1000`, **then**
`BlockRequest()`, `GetDibStatus() != 0` → fail.

**Header:** `GetHeaderValue(1)` = 수신 수 (`cnt`) — VERIFIED `L1310`.

**Data columns** — VERIFIED `L1315–L1328`:

| col | field | col | field |
|---|---|---|---|
| 0 | date | 8 | 기타금융 |
| 1 | 개인 | 9 | 연기금 |
| 2 | 외국인 | 10 | 기타법인 |
| 3 | 기관계 | 11 | 기타외인 |
| 4 | 금융투자 | 13 | 국가,지자체 |
| 5 | 보험 | 14 | close |
| 6 | 투신 | | |
| 7 | 은행 | | |

(col 12 not read.) **Paging** — VERIFIED `L1296–L1337`: `while True` +
`objRq.Continue != True` break, plus a hard `sumcnt > 1000` cap.

---

## 6. Request-rate-limit interaction (all signal classes)

| class | limit type | gate in sample? | bridge action |
|---|---|---|---|
| `CpSvrNew7043` | 1 (시세) | no | add `GetLimitRemainCount(1)` gate between pages |
| `CpMarketWatch` (one-shot) | 1 | no | one request; gate not critical |
| `CpMarketWatchS` / `CpSvr8092S` / `CssAlert` | 2 (실시간) | n/a | realtime budget; subscribe once, do not re-subscribe in a loop |
| `CssStgList` / `CssStgFind` | 1 | no | gate before `CssStgFind` (called per strategy switch) |
| `CssWatchStgSubscribe/Control` | 1 | no | gate; keep monitor count small (sample warns) |
| `CpSvr7210d` | 1 | no | gate between pages |
| `CpSvrNew7221` | 1 | no | one request |
| `CpSvr7254` | 1 | **yes** (`L1297`) | replicate exactly |

Type map is VERIFIED (`creon_algorism3.txt L3881`, `L1297`). Bridge already
implements `CybosPlus.wait_for_request_slot(t)` for the order budget
(`api_notes.md` §3) — extend it to type 1 for these recipes and pass type 2 for
the realtime subscribes.

---

## 7. Headless port checklist

For each class: what the headless STA worker / pump thread **keeps**, and what
Qt/UI code must be **dropped** when porting.

### `CpSvrNew7043`
- **Keep:** `Dispatch("CpSysDib.CpSvrNew7043")`; all 9 `SetInputValue`s verbatim
  (`ord('0')`, `ord('2')`, `ord('1')`, plain `21`, `ord('1')`, `ord('0')`,
  `ord('0')`, plain `0`, plain `30`); `BlockRequest()` +
  `GetDibStatus()`; header 0/1; data cols 0,1,3,4,6; the
  `while obj.Continue` paging loop with a `limit` cap (default 200).
- **Drop:** `CpEvent`/`CpStockCur`/`CpMarketEye` (the sample's follow-on
  pipeline — the bridge already covers realtime via `DsCbo1.StockCur` and
  quotes via `StockMst`); `MyWindow(QMainWindow)`; `QPushButton`; `app.exec_()`;
  all `print()`.
- **Add:** `GetLimitRemainCount(1)` gate between pages; `ret == 4` retry
  backstop via `LimitRequestRemainTime`.

### `CpMarketWatch` (one-shot)
- **Keep:** progid; `SetInputValue(0, code|'*')`, `(1, '1,2,10,11,12,13')`,
  `(2, 0)`; `BlockRequest()` + `GetDibStatus()`; `GetHeaderValue(2)`; row cols
  **0,1,3,4** (time, code, category, detail) — note category is col **3** here.
- **Drop:** the Excel/pandas export (`btnExcel_clicked`, `pd.ExcelWriter`,
  `os.startfile`), `listWatchData`, `MyWindow`, buttons, `app.exec_()`.
- **Add:** re-issue with the caller's category filter string; map col 3 through
  the §2.3 dictionary.

### `CpMarketWatchS` (realtime)
- **Keep:** `Dispatch("CpSysDib.CpMarketWatchS")`; `WithEvents`; conditional
  `SetInputValue(0, var)` when `len(var) > 0`; `Subscribe()`; `Unsubscribe()` on
  shutdown; `OnReceived` reading `GetHeaderValue(0)`=code, `(2)`=count and rows
  `GetDataValue(0,i)`=time, `(1,i)`=update (`ord('c')`=cancel), `(2,i)`=category
  → `diccode`.
- **Drop:** the shared `CpEvent` class' `marketnews` branch (split it out —
  §3 has a **different** header layout); `self.caller.listWatchData.insert(0,…)`
  UI list; `MyWindow`/buttons/Excel.
- **Add:** the full `diccode` dict from §2.3 as a module constant; push events
  onto the bridge's realtime queue instead of a Qt list.

### `CpSvr8092S` (realtime)
- **Keep:** `Dispatch("Dscbo1.CpSvr8092S")` (case as written; fall back to
  `DsCbo1.…` on Dispatch failure); `WithEvents`; conditional
  `SetInputValue(0, var)`; `Subscribe()`; `OnReceived` reading
  `GetHeaderValue(0)`=flag (`ord('D')`=삭제), `(1)`=code, `(2)`=time,
  `(4)`=category (dump to learn codes), `(5)`=text.
- **Drop:** the `marketwatch` branch of the shared `CpEvent` (separate class);
  `listWatchData` UI list; Excel export; `MyWindow`; `app.exec_()`.
- **Add:** the delete-flag handling (`'[삭제]'`) as an event field; a bounded
  ring buffer (this feed is chatty).

### `CssStgList` / `CssStgFind`
- **Keep:** both progids; `CssStgList` input 0 (`ord('0')`/`ord('1')`); headers
  0/1; cols 0–6; `CssStgFind` input 0 = ID; headers 0/1/2; col 0 = code.
- **Drop:** `QComboBox`, `comboChanged`, `btnOpt1_clicked`, `MyWindow`,
  `app.exec_()`, `pd.DataFrame`/`os`.
- **Add:** `A`-prefix normalisation via `bridge.codes`; a `Continue` probe for
  `CssStgFind` when `총 검색 종목 수` > `검색된 결과 종목 수` (NEEDS-LIVE-TEST);
  `GetLimitRemainCount(1)` gate.

### `CssWatchStgSubscribe` / `CssWatchStgControl` / `CssAlert`
- **Keep:** all three progids; `Subscribe.Stg` `SetInputValue(0,id)` +
  `GetHeaderValue(0)`=monID (0 ⇒ fail); `Control.SetInputValue(0,id)(1,monID)
  (2, ord('1')|ord('3'))` + `GetHeaderValue(0)` status 0/1/2/3; `CssAlert`
  `WithEvents` + **empty** `Subscribe('')`; `OnReceived` headers
  0=전략ID,1=monID,2=code,3=in/out,4=시각,5=현재가; the
  `(strategy_id, monitor_id)` pair check before forwarding; `stopAllStgControl()`
  on strategy switch and on shutdown.
- **Drop:** `QComboBox`/`MyWindow`/buttons; the `Clear()`/`__del__` UI lifecycle
  hooks (replace with the bridge's `RealtimeHub.stop()`-style teardown).
- **Add:** the ≥200-results ⇒ **refuse to monitor** rule
  (`creon_algorism.txt L1578–L1580`); a per-strategy monitor map keyed by
  monID; pass realtime budget type 2 for the `CssAlert` subscribe.

### `CpSvr7210d` / `CpSvrNew7221` / `CpSvr7254`
- **Keep:** progids; every `SetInputValue` exactly as written (note the
  string/`ord`/int mix per §5); status checks; header tables; data-column tables;
  the `Continue` paging loops; **the `GetLimitRemainCount(1)` gate from
  `CpSvr7254 L1297` applied to all three**.
- **Drop:** `pandas`/`ExcelWriter`/`os.startfile` exports; `MyWindow`,
  `QPushButton`, `app.exec_()`; the `locale.setlocale` call.
- **Add:** for `CpSvrNew7221`, resolve the row-keying question (instrument-index
  vs returned count) in one live test before exposing it.
