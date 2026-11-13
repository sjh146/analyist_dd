# trader-agent 운영 런북 (OPERATIONS)

이 문서는 **실제로 켜고, 확인하고, 고치는 절차**만 담는다. 설계 배경/COM 규약은
`bridge/api_notes.md`, 크레온 샘플 레시피는 `CREON_ALGORISM_CATALOG.md` /
`bridge/SIGNALS_NOTES.md`, 코어 구조는 `INTEGRATION_MAP.md` 참고.

---

## 0. 절대 규칙 (Python 비트 분리)

| 프로세스 | Python | 이유 |
|---|---|---|
| `run_bridge.py` (CybosPlus COM) | **32bit** `C:\Users\jhshi\Python311-32\python.exe` | 크레온 COM은 32bit 전용. `pywin32` 설치돼 있음 |
| `run_trader_core.py` / `run_market_loop.py` | **64bit** `C:\Users\jhshi\Python312-64\python.exe` | 전략/리스크 코어. `pywin32` 없음 = 의도적 (64bit에서 COM 시도 금지) |

- 32bit 쪽에 ML/수치 라이브러리를 넣지 말 것, 64bit 쪽에서 `win32com`을 import 하지 말 것.
- 브리지 없이 64bit 코어는 아무것도 못 한다. 항상 **브리지 먼저** 띄운다.

## 1. 실행 전제

1. Creon PLUS (`C:\CREON\STARTER\coStarter.exe`) 실행 + 로그인
   - 관리자 권한 필요: `powershell Start-Process 'C:\CREON\STARTER\coStarter.exe' -Verb RunAs`
   - 로그인 창에서 계좌 선택 (모의투자 / 실계좌). 현재 검증된 계좌: `7770******` (모의투자, 예수금 5,000,000)
2. 로그인 후 연결 확인:
   ```
   C:\Users\jhshi\Python311-32\python.exe run_bridge.py --no-server
   # CONNECTED: account=7770****** goods=10  <- 이 줄이 나오면 정상
   ```
   `NOT CONNECTED` 면 Creon PLUS가 안 떠 있거나 로그인 전이다.

## 2. 브리지 켜기 / 끄기 / 확인

> **Creon 재로그인 후에는 브리지를 반드시 재시작할 것.** 브리지 프로세스는
> 재로그인 전에 떠 있으면 COM 연결이 stale 상태로 남아 `/health` 가 계속
> `connected:false` 를 반환한다. 새 프로세스는 `CONNECTED` 로 나오므로, 먼저
> `run_bridge.py --no-server` 로 계좌 연결을 확인하고 → 그게 되는데 `/health` 가
> false면 브리지만 껐다 켜면 된다 (2026-09-10 실제 발생).

```bat
:: 켜기 (백그라운드, 로그 append)
C:\Users\jhshi\analyist_dd\trader-agent\bridge_start_bg.bat            :: 기본 8100
C:\Users\jhshi\analyist_dd\trader-agent\bridge_start_bg.bat 8200       :: 포트 지정

:: 끄기 (해당 포트 LISTENING 프로세스 종료)
C:\Users\jhshi\analyist_dd\trader-agent\bridge_stop.bat
C:\Users\jhshi\analyist_dd\trader-agent\bridge_stop.bat 8200
```

```bat
:: 상태 확인 (Windows 쪽에서)
curl http://127.0.0.1:8100/health
curl http://127.0.0.1:8100/balance
curl http://127.0.0.1:8100/positions
curl http://127.0.0.1:8100/orders
curl "http://127.0.0.1:8100/quotes?codes=005930,000660"
```

- WSL(에이전트 셸)에서는 `127.0.0.1:8100` 접근 불가 — 브리지는 Windows 루프백에만 바인딩된다.
  WSL에서 호출해야 하면 Windows 셸(`cmd.exe /c curl ...`)을 거칠 것.
- 로그: `bridge.out.log` (기동/표준출력), `bridge.err.log` (예외). 로그가 비어 있어도
  `/health` 와 `netstat`이 진실이다.

## 3. 자체 점검 (브로커 없이)

```bat
:: 순수 파이썬 스모크 테스트 (fake COM, Creon 불필요)
C:\Users\jhshi\Python311-32\python.exe run_bridge.py --selftest

:: 실시간 수신 단독 프로브 (Creon 로그인 필요, 틱 20초 카운트)
C:\Users\jhshi\Python311-32\python.exe tools\rt_probe.py

:: 테스트 스위트
C:\Users\jhshi\Python311-32\python.exe -m pytest bridge/tests -q
C:\Users\jhshi\Python312-64\python.exe -m pytest trader_core/tests runner/tests -q
```
2026-09-10 기준: bridge 44 / trader_core 83 / runner 63 전부 통과.

## 4. 실시간(StockCur) 사용 규칙 — 반드시 지킬 것

**`bridge/plus.py` 의 idle pump 를 제거하지 말 것.** CybosPlus 이벤트는 "작업을 수행한
아파트"의 메시지 창으로 배달된다. BlockRequest 워커가 먼저 Cybos를 호출해 놓고 그 워커가
메시지를 펌프하지 않으면, 이후 구독은 `SUBSCRIBE OK` 를 반환하고도 **틱을 0건** 받는다.
(2026-09-10 라이브로 3가지 조합 재현 → 워커 유휴 펌프로 해결. 회귀 테스트
`bridge/tests/test_plus.py::IdlePumpTest`.)

소비자가 지킬 것:
- 실시간 시세는 `/subscribe` 후 `GET /quotes`(codes 없이)로 캐시를 읽는다 — 폴링 대신 푸시.
- `ex_flag` 는 문자 `'2'`가 아니라 **ASCII 코드 50** 으로 온다 (`49`='1' 동시호가, `50`='2' 장중).
- 구독 해지는 `/unsubscribe`(캐시는 남는다). 프로세스를 죽이면 구독은 자동 정리된다.

## 5. 요청 예산 (차단 방지)

Daishin은 초당 요청 수를 제한하고 초과하면 클라이언트를 차단한다. 그래서:

| 경로 | 게이트 | 구현 |
|---|---|---|
| 주문/취소 | `GetLimitRemainCount(0)` | `orders._trade_job` → `wait_for_request_slot(REQ_TYPE_ORDER)` |
| 현재가 조회(`/quotes`) | `GetLimitRemainCount(1)` | `orders.fetch_quotes` (중복 코드는 1회로 합침) |
| 랭킹/조건검색 | `GetLimitRemainCount(1)` | `bridge/SIGNALS_NOTES.md` 레시피로 추가 예정 |

- `BlockRequest` 가 `4`(연속조회 제한)를 반환하면 `LimitRequestRemainTime` 만큼 쉬고 재시도 (`block_until_ok`).
- 30초 안에 슬롯이 안 나오면 `rate_limited` 로 실패한다 — 재시도 폭주 금지.
- 같은 종목을 반복 조회하지 말 것. 실시간 구독이 가능하면 구독으로 대체.

## 6. 자주 나는 문제

| 증상 | 원인 / 조치 |
|---|---|
| `not_connected` | Creon PLUS 미실행/미로그인 → 실행 후 재로그인 |
| `trade_init_failed` | 계좌/상품 초기화 실패 → 로그인 계좌 확인, 장 종료 후 재시도 |
| `com_unavailable` | 32bit가 아닌 Python으로 실행했거나 pywin32 없음 → `Python311-32` 확인 |
| `/subscribe` 는 ok 인데 틱 0건 | idle pump 제거 여부 확인 (§4) |
| `rate_limited` | 요청 폭주 → 배치 축소·실시간 구독 전환 |
| `/balance` 에 `buyable_error 94007` | 모의투자 서버는 매수가능금액 조회 미지원 → `cash` 는 `d2_deposit` 폴백값 |
| 포트 8100 사용중 | `bridge_stop.bat` 후 재기동 |
| bridge.log 에 `ConnectionAbortedError [WinError 10053]` traceback | 클라이언트(루프)가 응답을 받기 전에 끊은 것 — 브리지 결함 아님. 브리지 재기동 직후 첫 `/health` 가 5초 클라이언트 타임아웃에 걸릴 때 발생한다(2026-09-21). `_send` 가 OSError 를 무시하므로 이제 traceback 이 남지 않는다. 그래도 뜨면 구버전 브리지가 실행 중이라는 뜻 |
| 루프 기동 직후 `CHECK failure 1: ... timed out` | 갓 뜬 브리지의 첫 요청은 느릴 수 있다 → 다음 사이클(5분)에 자동 회복. 브리지 재기동 후 `/health` 를 한 번 호출해 워밍업한 뒤 루프를 켜면 피할 수 있다 |

---

## 7. 자체 종목선정 + 전략 (외부 피드 없이)

BC-250 피드가 없는 동안 후보는 **브로커 랭킹에서 직접** 만든다.

```
GET /rank (CpSysDib.CpSvrNew7043, 당일 상승률 상위)
      ↓ 필터: 상한가 제외 / 거래량 하한 / 등락률 하한
      ↓ 상위 N종목만 GET /quotes (요청예산: 종목당 시세 1회)
      ↓ 점수화 (0~100)  →  FeedSnapshot
      ↓ 엔진 6단 게이트 → 루프 R1~R5 → 주문
```

실행: `--feed rank` 를 붙이면 외부 URL 대신 이 경로를 쓴다.

```bat
C:\Users\jhshi\Python312-64\python.exe run_trader_core.py scan --feed rank --dry-run
C:\Users\jhshi\Python312-64\python.exe run_market_loop.py run --once --feed rank --dry-run
C:\Users\jhshi\Python312-64\python.exe run_market_loop.py run --once --live --yes --feed rank
```

### 7.1 점수 공식 (trader_core/rankfeed.py)

```
score = 100 * (0.40*등락률 + 0.30*거래량 + 0.20*랭킹순위 + 0.10*거래대금)
등락률 = min(1, pct / pct_ref)          pct_ref   = 10.0 %
거래량 = min(1, log1p(vol)/log1p(ref))  volume_ref= 20,000,000
순위   = (전체 - 순위 + 1) / 전체
상한가 종목은 score *= 0.6 (추격 회피)
```
- `pct` = 대비 ÷ (현재가 − 대비) — `close_price`는 `/quotes`의 현재가를 쓴다.
- 점수는 **설명 가능하고 결정적**이다. 임계값만 조정하면 된다.

### 7.2 필터 · 파라미터 (strategy_params.json)

`tools/review.py` 가 만들고 **매 사이클 읽는다** (재시작 불필요).

| 키 | 기본 | 의미 |
|---|---|---|
| `min_pct` | 2.0 | 최소 당일 상승률 % |
| `max_pct` | 15.0 | 추격 상한 % — `rank_pct_to`와 짝으로 움직인다 (아래 참고) |
| `min_pullback_pct` | 3.0 | 일중 고점 대비 최소 되돌림 % (고점 매수 방지) |
| `min_volume` | 100000 | 최소 당일 거래량 |
| `skip_limit_up` | true | 상한가 종목 제외 |
| `top_n` | 8 | 사이클당 후보 수 (= 시세 요청 수) |
| `pct_ref` / `volume_ref` / `turnover_ref` | 10 / 2천만 / 20만 | 점수 기준점 |
| `prior_win_rate` / `prior_avg_win_pct` / `prior_avg_loss_pct` | 자동 | Kelly 사이징 사전확률 |
| `r1_min_avg_score` | `{rank_momentum: 45}` | 루프 R1 진입 문턱. **랭크 피드에서는 사실상 무력하다**: 백분위 점수는 후보 집합 내부 기준이라 후보 전체의 상위N 평균이 구조적으로 50 근처다(2026-09-11 실측 84/58/50/44/40/24 → 50.0). 50 이상이면 항상 차단, 50 미만이면 절대 안 걸린다 — "과열" 판단은 `r1_max_avg_pct`(과열 게이지)·`max_pct`·`min_pullback_pct`가 담당한다. |
| `screeners` | `[rank_momentum]` | 거래할 스크리너 |

### 7.3 리스크 (변경 전 반드시 확인)

엔진 게이트 6단: feed 신선도 → kill switch → 장중 → 일일 체결수 → 잔고/현금/포지션 한도 → 신호 정상성.
루프 규칙: R1 진입문턱 · **R2 하루 최대 100건**(사이클당 남은 한도까지만 집행, **종목당 하루 1회** — 같은 종목 반복매수 방지) · R3 계획가 대비 ±3% 이탈 시 스킵 ·
R4 자정 대비 −3% 시 전량 청산+정지 · R5 뉴스 게이트(랭크 모드에서는 꺼짐).

```json
// 자주 만지는 값 (runner 설정 / TRADER_CORE_* 환경변수로도 가능)
"r2_max_entries_per_day": 100,
"r2_one_entry_per_code_per_day": true,
"r4_loss_limit_pct": 0.03,
"max_position_pct": 0.20,        // 종목당 자산 비중
"max_open_positions": 5,         // 동시 보유 종목 수 (0=무제한). 2026-09-25 10→5 축소(사용자 승인)
"max_invested_pct": 0.40,        // 총 투입원가 / 자산 (켈리는 베팅 1건만 본다)
"max_order_amount": 10000000,    // 1주문 최대 금액
"kelly_fraction": 0.25,          // 켈리 축소 계수
"default_win_rate": 0.53,        // 콜드스타트 승률 가정 (0.50 이하면 켈리=0 → 매매 정지)
"stop_loss_pct": 0.07, "take_profit_pct": 0.07,   // 익절 7% (2026-09-11 변경, 이전 15%)
"trailing_stop_pct": 0.05,       // +trailing_arm_pct 도달 후 고점 대비 -5%면 청산 (2026-09-21 3%→5%)
"trailing_arm_pct": 0.055,       // arm ≥ trail/(1-trail) 유지: 5%면 최악 armed 청산 = 1.055x0.95-1 = +0.22%
"max_hold_sessions": 5,          // rank_momentum: 5번째 세션 종가창(15:10-15:25)에 무조건 청산 (0=해제)
"flat_stop_sessions": 2,         // ... 2번째 세션에 보합(±2%)이면 청산 = 죽은 슬롯 회수
"flat_stop_band": 0.02,
"time_stop_sessions": 0,         // 성과 없는 종목 조기 청산 (실측상 비용 > 이득 → 기본 꺼짐)
"tick_watch_seconds": 1.0,      // 사이클 사이 실시간 캐시 감시 간격 (0=해제, 다이신 요청 0건)
"tick_watch_max_age_seconds": 5.0,  // 이보다 오래된 틱은 무시 (정지된 실시간 피드 방어)
"quote_divergence_pct": 0.01,    // 시세 vs 브로커 평가가 1% 이상 어긋나면 보수적 가격 사용
"clock_max_skew_seconds": 300,   // 시계가 5분 이상 어긋나면 신규 매수 중단

// 전략 알고리즘 (strategy_params.json 의 rankfeed params + 엔진 기본값)
"min_pct": 2.0,                  // 당일 등락률 하한
"max_pct": 15.0,                 // 추격 상한 (2026-09-11 12→15) — rank_pct_to와 짝
"top_n": 8,                      // 랭킹 상위 N개만 시세 조회 (요청 예산)
"min_pullback_pct": 3.0,         // 일중 고점 대비 되돌림 요구 (고점 매수 방지)
"scoring": "percentile",         // 후보 집합 내 백분위 점수 (absolute=구 방식)
"stop_range_factor": 0.5,        // 손절폭 = max(7%, 0.5 x 일중폭)
"max_stop_pct": 0.20,            // 손절폭 상한
"risk_per_trade_pct": 0.005,     // 종목당 리스크 예산 (자산의 0.5%)
"r1_max_avg_score": 95.0,        // 과열 상한: 후보 평균 점수 (0=해제)
"r1_max_avg_pct": 15.0           // 과열 상한: 후보 평균 등락률 (0=해제)
```

### 7.3.1 안전 가드 (2026-09-10 사고 후 추가)

| 가드 | 동작 | 끄는 법 |
|---|---|---|
| **시세 교차검증** | `/positions` 의 `eval_amount ÷ qty`(브로커 마크)와 시세가 1% 이상 다르면 **더 나쁜 가격**으로 손절/익절 판단. 2026-09-10: 시세 6,050 vs 실제 5,040 이라 -17.8% 포지션이 손절 안 됐다. | `quote_divergence_pct: 0` |
| **시계 대조** | 사이클마다 외부 HTTP Date(기본 google.com)와 대조. 5분 초과 어긋나면 신규 매수 차단(청산은 계속). 캐시 10분. | `clock_check_url: ""` |
| **트레일링 스탑** | +5.5% 도달 후 고점 대비 -5%면 시장가 청산. 고점(HWM)은 저널 `hwm_price`에 저장되어 재시작해도 유지. **arm ≥ trail 을 지킨다**: 2026-09-15 이전 3%/5% 조합은 armed 포지션의 청산 트리거가 `peak×0.95` 로 진입가 *아래*에 생겨, 그날 추격스톱 청산 5건이 -3.0/-1.6/-2.1/-2.0/+0.3%(평균 -1.7%)로 전부 손실·본전이었다. 3%/3%는 최악 armed 청산 -0.09%(사실상 본전), 5.5%/5%는 +0.22%로 armed 청산이 구조적으로 이익이다. **2026-09-21 실측으로 3%/3% → 5.5%/5% 로 확대**: 청산 30건 중 트레일 청산 10건이 평균 -0.23%(번 것을 그대로 반납), 고정 손절 10건은 -10.45%였고, 진입 후 고점(hwm) 중앙값이 +4.17%·1.5배 도달이 10%뿐이라 3% 반납은 곧 본전이었다. 1분봉 리플레이(21건, 보유기간 고정)에서도 넓히는 방향으로 단조 개선: arm3/trail3 +2,883 → arm5.5/trail5 +25,535 (트레일 청산 13→6건, 익절 2→7건). 되돌림(트레일 2%: -37)·손절 축소(변동폭x0.35: -8,272, 고정 7%: -9,510)는 모두 악화. 대가: +5.5% 미만에서 고점을 찍은 종목은 armed 되지 않아 고정 손절까지 보유한다. | `trailing_stop_pct: 0` |
| **시간 청산 래더 (모멘텀 슬롯 회수)** | rank_momentum 프로필에 추가된 시간 기반 청산. `flat_stop_sessions`(2)·`flat_stop_band`(±2%) = 2번째 세션 종가창에서 보합(±2% 이내)이면 청산, `max_hold_sessions`(5) = 5번째 세션 종가창에 무조건 청산, `time_stop_sessions`(0=꺼짐) = 성과 없는 종목 조기 청산. 모든 규칙은 `close_exit_window`(15:10-15:25) 안에서만 발동하고, 시세·진입가가 없으면 가격이 필요한 규칙(보합/시간손절)은 발동하지 않는다(보유 유지 = fail-open). 2026-09-22 1분봉 리플레이(폐쇄 7 + 미결 10, 진입·수량 고정, 절대값이 아니라 델타만 신뢰): 보합청산 2세션은 미결 평가 -24,250 → -15,090 으로 개선되고 폐쇄 거래는 +17,890 그대로(비용 0), max_hold 5세션은 폐쇄 +18,870 / 미결 -27,340 으로 17건 기준 약 -2.1천원을 내고 보유 상한을 보장한다. time_stop 2세션 +1% 는 폐쇄 +17,695 / 미결 -25,910 로 비용이 더 커 기본 꺼짐 (468530은 1일차 -3.4%였다가 5일차 +33%). 손절 조이기(6% 상한: 미결 -35,520)와 트레일 좁히기(arm2/trail2: 폐쇄 -290)는 모두 악화되어 그대로 둔다. 왜 회전율이 문제였나: 2026-09-11~22 저널 게이트 기록에서 후보 103 code-day 가 오직 `max_open_positions`(10) 때문에 거부됐다. | `flat_stop_sessions: 0`, `max_hold_sessions: 0` |
| **틱 기반 청산 감시 (사이클 사이)** | 루프가 사이클 사이(`tick_watch_seconds`, 기본 1초)에 브리지의 실시간 캐시(`GET /quotes` 무코드 = StockCur 스냅샷, **다이신 요청 0건**)를 읽어 손절/익절/트레일 이탈을 감지한다. 캐시 틱은 *후보*일 뿐이고, 발동하면 `/positions`를 1회 호출해 브로커 마크로 같은 조건을 재확인한 뒤(둘 중 **더 나쁜 가격**을 기록) 브로커의 매도가능수량으로 시장가 청산한다. 5초보다 오래된 틱은 무시하고, 시세·진입가·저널행이 없으면 보유 유지(fail-open). 2026-09-10 사고(캐시 6,050 vs 브로커 5,040 → -17.8% 포지션 방치) 때문에 캐시 단독 판단은 금지. 반응 시간이 사이클(집중창 30~60초, 그 외 300초)에서 약 1초로 줄지만, 주문 자체는 시장가 체결 지연(수백 ms~수 초)을 그대로 갖는다. | `tick_watch_seconds: 0` |
| **추격 상한** | 랭크 후보가 당일 +15% 초과면 제외 (`rankfeed` params `max_pct`). **랭킹 창(`Config.rank_pct_to`, 기본 15%)과 반드시 짝으로 본다**: 랭킹 페이지는 상승률 내림차순이라 창이 30%면 표본 상위가 전부 +18~30%가 되어 상한이 모든 후보를 버린다(2026-09-11 오전 실측: 후보 0건). 창 ≤ 상한이면 표본이 실제 매수 구간 안에 들어온다. | `max_pct: 0` (창은 `rank_pct_to`) |
| **고점 되돌림** | 일중 고점 대비 3% 이상 눌린 종목만 진입 (`min_pullback_pct`). 2026-09-10 실측: 진입가가 평균 고점 −4.6%, 즉 고점 매수였다. | `min_pullback_pct: 0` |
| **과열 게이지** | 후보 평균 점수 > 95 또는 평균 등락률 > 15% 면 그 스크리너 진입 차단 (`r1_max_avg_score`/`r1_max_avg_pct`, R1 과 동일 경로). | 각 값 `0` |
| **변동폭 손절/사이징** | 손절폭 = max(7%, 0.5×일중폭), 수량 = 리스크예산(자산 0.5%) ÷ (가격×손절폭) → 종목당 손실 상한 균일. | `risk_per_trade_pct: 0` |
| **알고리즘 귀속 기록** | 모든 체결에 `trades.algo_json` (스키마 v4): 스크리너·점수 방식·필터·후보 지표·사이징 경로·손절 모델. 로그에도 `ALGO buy CODE qty :: ...` 한 줄. | (항상 켜짐) |
| **브리지 DibRq 재시도** | `BlockRequest()` 가 `(-2147352567) DibRq 통신요청 실패... 다시 요청하십시오` 를 던지면 3회 재시도(0.3→1.5s). 이게 없으면 계좌 조회가 통째로 실패한다. | (항상 켜짐) |

**시계가 어긋난 상태로 매매하면** 세션 모델(장중·집중창·일자 롤오버)이 전부 틀어진다. 2026-09-10 에는 시계가 약 -4시간 틀어진 채 매매했고, 동기화되자 루프가 마감으로 오판해 하루를 종료했다. 시계가 의심되면:
```bat
wsl.exe -u root -- timedatectl          :: NTP 동기화 상태
curl -sI https://www.google.com | findstr /i "^date:"
```


### 7.4 자기수정 루프 (저널 → 파라미터)

```bat
C:\Users\jhshi\Python312-64\python.exe tools\review.py --dry-run   :: 결정만 확인
C:\Users\jhshi\Python312-64\python.exe tools\review.py             :: 파라미터 반영
```

1. 청산 완료된 거래의 **실현 성과**를 읽는다 (승률, 평균 수익/손실 %, 손익비).
2. 관측값을 보수적 사전확률로 **축소(shrinkage, 의사표본 10개)** 해서 3~5건의 우연한 결과가 사이징을 흔들지 않게 한다.
3. 진입 문턱을 **±5.0 / −2.5 범위 내**에서만 조정한다 (하한 40, 상한 85).
4. 청산 거래가 `--min-samples`(기본 5) 미만이면 **아무것도 쓰지 않는다** (기본값 유지).

반영 경로: 엔진은 `fetch()` 마다(즉시), 루프는 기동 시(다음 실행) 새 파라미터를 읽는다.

## 8. 자율 루프 가동 / 중지

```bat
:: 상시 루프 (장 시작 5분 전 ~ 마감 10분 후까지, 300초 주기)
C:\Users\jhshi\analyist_dd\trader-agent\loop_start_bg.bat
C:\Users\jhshi\analyist_dd\trader-agent\loop_stop.bat

:: 상태 확인
C:\Users\jhshi\Python312-64\python.exe run_market_loop.py status
```

### 8.1 평일 자동 시작 (작업 스케줄러)

`register_tasks.bat` 로 등록되어 있다 (다시 등록해도 안전, `/F` 덮어쓰기):

| 작업 | 시각 | 동작 |
|---|---|---|
| `trader-bridge` | 평일 08:45 | 32비트 COM 브리지 기동 |
| `trader-loop` | 평일 08:50 | 자율 루프 기동 (08:55 시작 대기) |
| `trader-bridge-sup` | 로그온 +30초, 30분 워치독 | 브리지 감독·자동 재기동 (Highest) |
| `trader-loop-sup` | 로그온 +30초, 30분 워치독 | 루프 감독·자동 재기동 (Highest) |
| `trader-tunnel` | 로그온 +20초 | 프록시 터널(mihomo) 감독 (이 회선 전용, Highest) |

`register_tasks.bat` 을 **관리자 콘솔**에서 실행하면 위 5개가 한 번에 (재)등록된다.
감독 작업들은 XML(`tools/tasks/*.xml`)로 등록하며 로그온 트리거 + 30분 반복 트리거를
가진다 (인스턴스가 죽어 있으면 30분 안에 되살아난다, `MultipleInstancesPolicy=IgnoreNew`
라 중복 실행은 없다).

```bat
schtasks /Query /TN "trader-loop"          :: 다음 실행 시각 확인
schtasks /Run   /TN "trader-loop"          :: 즉시 시작
schtasks /Delete /TN "trader-loop" /F      :: 자동 시작 해제
```

**주의 3가지**
1. **Creon PLUS 로그인은 사람이 해야 한다.** 로그인이 없으면 브리지가 `connected:false` 로 뜨고
   그날은 매매하지 않는다 (루프는 check 실패 예산 3회를 소모하고 정지).
2. **루프는 하루가 닫히면 스스로 종료**한다 (`loop_state.json` 의 `daily.closed`). 날짜가 바뀌어야
   일자 카운터가 롤오버되므로 **저녁에 실행해도 그날은 아무것도 하지 않는다** — 아침에 시작해야 한다.
3. 스케줄러는 공휴일을 모른다. 휴장일에는 시세가 없어 R3(계획가 대비 이탈)에서 대부분 걸러지지만,
   휴장일에는 `loop_stop.bat` 으로 꺼두는 편이 안전하다.
4. **수동 기동과 예약 작업이 겹쳐도 두 번째 인스턴스는 뜨지 않는다** (2026-09-28 추가, 실측 검증).
   `loop_start_bg.bat` 은 `tools/loop_pids.ps1` 로 이미 도는 루프를 찾고, 있으면
   `loop.out.log` 에 `market loop already running (pid N) - skip start` 를 남기고 즉시 끝난다.
   `bridge_start_bg.bat` 은 브리지 포트가 이미 LISTEN 이면 시작하지 않는다. `loop_stop.bat` 의
   fallback 도 같은 helper 를 쓴다 — batch 안에서 `powershell -Command "... | ..."` 형태의 파이프는
   cmd 이스케이프 단계에서 `^|` 로 새어나가 조용히 실패하므로(2026-09-28 측정) 파이프라인은
   반드시 `.ps1` 파일에 둔다.
5. **감독 작업이 브리지·루프의 생존을 책임진다** (2026-09-28 추가 — 이날 08:50경 브리지가
   로그 없이 사라져 루프가 09:0x에 HALT, 보유 10건이 장중 내내 무관리였다).
   - `tools/supervise_bridge.ps1` (`bridge_sup.log`): HTS 프로세스가 없으면 브리지를 **띄우지
     않는다**(로그인 전에 뜬 브리지는 낡은 CybosPlus 링크를 물고 `connected:false` 로 남는다).
     HTS가 있고 포트가 비면 기동, `/health connected:false` 가 3회 연속이면 재기동 → 로그인/재로그인
     후 브리지 재기동 규칙이 자동화된다.
   - `tools/supervise_loop.ps1` (`loop_sup.log`): 평일 08:30-15:35 에 한해, 브리지가
     `connected:true` 일 때만 루프를 띄운다. 루프가 이미 돌면 건드리지 않고, 장 마감으로 루프가
     스스로 끝나면 창 밖이라 재기동하지 않는다. halt 플래그는 그대로 남으므로 자동복구 판정
     (`RESUME (auto)`)은 루프가 스스로 한다.
   - 즉 **크레온 로그인만 하면 브리지→루프가 30~60초 안에 자동으로 따라 올라온다.**


즉시 멈추는 3가지 방법 (빠른 순):
1. `loop_stop.bat` — 루프 프로세스 종료 (포지션은 유지됨)
2. `kill_switch.txt` 파일 생성 — **다음 사이클에 전량 청산 + 정지** (엔진 게이트 2)
3. `run_market_loop.py halt --reason "..."` — 신규 진입 정지 (청산은 안 함)

### 8.2 마감 후 자동 대조 (journal ↔ broker)

장이 닫히면 마감 사이클이 **미체결 주문을 취소한 뒤 저널과 브로커를 자동 대조**한다
(`RunnerConfig.close_reconcile`, 기본 ON; 끄려면 `close_reconcile=False` 로 실행).

자동으로 고치는 것 (브로커가 진실인 경우만, `close_reconcile_apply=True`):

| 상황 | 처리 |
|---|---|
| 주문을 체결로 기록했지만 브로커는 미체결 (2026-09-17 288180 사례) | 원가 청산 = 손익 0 |
| 부분체결 (저널 수량 ≠ 브로커 수량) | 브로커의 체결 수량·단가로 정정 |
| 브로커만 보유 (저널 누락) | 브로커 평균단가로 편입 |
| 저널만 청산 (유령 청산) | 재개(reopen) |

**자동으로 고치지 않는 것**: "저널은 보유, 브로커는 없음, 미체결 주문도 없음" 건은 **보고만** 한다.
진짜 청산가는 브로커 체결단가이고 현재가(호가)는 체결가가 아니기 때문이다 (추측 금지 원칙).
로그에 `CLOSE flat but journal open: ... --fix-exit CODE=PRICE` 로 남으므로 사람이 정정한다:

```bat
C:\Users\jhshi\Python311-32\python.exe tools\daily_pnl.py        :: 브로커 평균매도단가 확인
C:\Users\jhshi\Python312-64\python.exe tools\reconcile_journal.py --apply --fix-exit 010640=2240
```

**장중에 생긴 유령 청산은 마감까지 기다리지 않고 되돌릴 수 있다** (2026-09-28 067630 사례):
TP 도달로 시장가 매도가 나갔는데 모의투자 체결엔진이 그 주문을 채우지 않았고
(`/orders filled_qty=0`, `sellable_qty=0`, CpTd6032 당일 실현손익 0건), 그런데 저널은
현재가 기준으로 청산을 기록해 **저널 9건 vs 브로커 10건**으로 갈라졌다. 이 상태로 두면
동시보유 게이트가 실제보다 1건 적게 세고, 당일 `closed_pnl` 이 +18,090 원 과대계상된다.

```bat
:: 되돌리기 (백업 자동 생성, 주문은 건드리지 않는다)
C:\Users\jhshi\Python312-64\python.exe tools\reopen_phantom_exit.py --code 067630 --dry-run
C:\Users\jhshi\Python312-64\python.exe tools\reopen_phantom_exit.py --code 067630 --yes
```

되돌린 뒤에도 **살아 있는 미체결 매도 주문은 그대로 둔다** — 상한가처럼 체결 틱이 멈춘
구간에서는 시장가 매도가 즉시 체결되지 않으므로, 주문이 실제로 체결되면 그때 브로커 체결가로
정리한다(위 `--fix-exit` 경로). 청산 체결 확인이 진입(`_confirm_entry`)처럼 자동화되기 전까지는
이 수동 경로가 유일한 정정 수단이다.

결과는 `loop_state.json`(`daily.reconcile`)에 코드 목록으로 남는다:
`unfilled` / `qty_fixed` / `adopted` / `reopened` / `corrected` 는 조치 완료, `report_only`(= `closed` + `needs_price` + `bogus_exits`)는 사람 확인 필요.
대조 자체가 실패해도(브리지 끊김 등) 마감은 정상 진행되고 로그에 사유만 남는다.

### 8.3 진입 체결 확인 (주문 응답은 체결이 아니다)

`CpTd0311` 주문 응답의 `order_no=0` + `94059/94060 ... 이미 처리되었습니다` 는 **성공과 미체결을 구분하지 않는다**.
2026-09-21 317830: 응답은 "정상처리"였고 루프는 `filled buy` 로 기록·통보했지만 브로커에는 미체결 주문(2548, filled_qty 0)만 있고 보유는 없었다
(실제 체결은 약 5분 뒤). 같은 클래스가 2026-09-17 288180, 2026-09-10 424980.

이제 매수 주문 직후 `TraderEngine._confirm_entry()` 가 브로커의 미체결 주문 목록과 보유 목록을 읽어 판정한다
(`Config.confirm_entry_fills`, 기본 ON — 주문 1건당 요청 2건이 추가로 나간다):

| 판정 | 근거 | 루프 동작 |
|---|---|---|
| `filled` | 브로커가 보유, 또는 미체결 주문의 filled_qty ≥ 요청수량 | `filled` 로 보고 + 진입 카운터 증가 |
| `partial` | 미체결 주문에 0 < filled_qty < 요청수량 | **브로커 체결수량**으로 저널 기록 |
| `working` | 주문이 살아 있고 체결 0 | `FILL NOT CONFIRMED` 로 보고, 행은 유지(슬롯 예약), `pending_entries` 에 등록 |
| `unconfirmed` | 보유도 주문도 조회되지 않음 (또는 조회 실패) | 위와 동일 |

`pending_entries` 는 **매 사이클 재검증**한다 (`MarketLoop._verify_pending_entries`):
브로커가 보유하면 확정(`fill confirmed` 로그), 미체결 주문이 남아 있으면 유지, 둘 다 없으면 그 자리에서
**원가 청산(손익 0)으로 슬롯을 회수**한다 — 마감 대조(8.2)까지 슬롯이 묶여 신규 진입이 막히는 일이 없다.
날짜가 넘어가면 `pending_entries` 는 비운다. `loop_state.json` 의 `pending_entries` 가 비어 있지 않으면
"아직 브로커가 체결을 확인해 주지 않은 진입"이 있다는 뜻이다.

주의: 미체결로 끝난 진입도 그날의 진입 카운터와 `r2_one_entry_per_code_per_day`(종목당 1회)는 소모한다.
주문을 실제로 냈기 때문이며, 슬롯만 회수된다.

### 8.4 연결 끊김 = 일시정지 + 자동 복구

체크 실패가 `max_consecutive_check_failures`(기본 3)에 도달하면 루프는 HALT 플래그를 세우되 **프로세스는 계속 살아서 사이클을 돈다**
(`auto_resume_check_halt`, 기본 ON). 매 사이클 `/health` + `/balance` + 피드 신선도를 다시 확인(`_probe_ok`)하고,
세 가지가 모두 정상이 되면 스스로 HALT 를 해제해 정상 매매로 복귀한다 (로그/알림 `RESUME (auto)`).

- 2026-09-21: CybosPlus 링크가 17분 끊겼을 때는 halt 와 동시에 프로세스가 종료되어 사람이 `resume` 을 입력할 때까지 멈춰 있었다.
  지금은 HALT 동안 그 사이클만 건너뛰고, 링크가 돌아오면 자동으로 재개한다 (사람 개입 불필요).
- R4 일일손실 HALT 와 `kill_switch.txt` 는 **자동 해제되지 않는다** (사람 개입 필요, 의도된 동작).
- 정지 중에도 1사이클당 요청은 health/balance/feed 3건뿐이라 요청 예산에 부담이 없다.
- 구 동작(실패 시 즉시 종료)이 필요하면 `RunnerConfig.auto_resume_check_halt=False`.

## 9. 2026-09-10 실제 집행 기록 (모의투자)

- 사이클 1 (13:49): 6건 판단 → 6건 계획 → dry-run 체결 (R2 미적용 버그 발견)
- 사이클 2 (13:52, live): 6건 주문 → **5건 체결 + 1건 미체결**
  - 024060 흥구석유 8주 @14,452 · 026910 광진실업 30주 @4,045 · 214680 디알텍 20주 @6,116
  - 432720 퀄리타스반도체 11주 @10,671 · 452450 피아이이 24주 @5,086
  - 424980 마이크로투나노 12주 @9,700 **미체결** (주문 3729) — 체결 아님
- 14:04 026910 손절 (-7% 조건 충족, 3,755 < 손절선 3,762) → 시장가 매도 **체결됨**
  (`CpTd6032` 확인: 매도 30/30주, 평균매도단가 **3,747**, 실현손익 **-8,954원**)
- 14:04~14:15 Creon 세션 끊김 → 루프 정지(오기록 방지) → 재로그인 → 브리지 재시작
- 14:31 저널 정정: 026910 청산가 0 → 3,747 (-121,500 → **-9,090**), 424980 원가 청산(0)
- 이 과정에서 잡은 버그 4건: **R2가 사이클 내 배치에 적용 안 됨**, **equity가 주식평가만 반영(≈-88% 오판 → R4 전량청산 위험)**, **시장가 청산이 0원으로 기록**, **수용된 미체결 주문이 포지션으로 기록**

## 10. 도구 (tools/)

| 도구 | 실행 인터프리터 | 용도 |
|---|---|---|
| `tools\portfolio_check.py` | 64bit | 현재 수익률/포지션/미체결 + **저널-브로커 불일치 표시** |
| `tools\status_snapshot.py` | 64bit | 현재 상황 요약: equity / 당일 실현손익 / 보유+청산선 거리(가까운 순) / 저널-브로커 불일치 / 미체결 (읽기 전용) |
| `tools\reconcile_journal.py` | 64bit | 불일치 정정 (기본 리포트, `--apply` 로 반영, `--fix-exit CODE=PRICE`) |
| `tools\daily_pnl.py` | **32bit** | 브로커 기준 당일 실현손익 (CpTd6032) — 청산단가의 진실 |
| `tools\review.py` | 64bit | 저널 → 파라미터 자기조정 (Kelly 사전확률, 진입문턱) |
| `tools\gap_audit.py` | 64bit | 특정일 1분봉으로 청산선 이탈(놓친 청산) 사후 점검 - 브리지/브로커 요청 불필요 |
| `tools\replay_exits.py` | 64bit | 1분봉으로 **청산 규칙 리플레이**(진입 고정, 손절/익절/트레일/보유기간 비교) - 튜닝 전 근거 만들기. 규칙 키: `hold_minutes`(N분 보유), `time_stop_minutes`({minutes,gain}), `hold_sessions`(N번째 세션 종가 청산), `time_stop`({sessions,gain}), `flat_stop`({sessions,band}), `stop_cap`. `avg_hold` = 평균 보유(세션 분), `--detail` = 종목별 세션 종가 경로 |
| `tools\rt_probe.py` | **32bit** | 실시간 틱 수신 단독 진단 |

```bat
:: 표준 점검 순서
C:\Users\jhshi\Python312-64\python.exe tools\portfolio_check.py
C:\Users\jhshi\Python312-64\python.exe tools\reconcile_journal.py            :: 리포트만
C:\Users\jhshi\Python311-32\python.exe tools\daily_pnl.py                    :: 진짜 체결단가
C:\Users\jhshi\Python312-64\python.exe tools\reconcile_journal.py --apply --fix-exit 026910=3747
```

정정 원칙: **추측 금지.** 가격은 브로커(체결단가/시세) 또는 명시적으로 넘긴 값만 쓴다.
0원 청산·미체결 주문처럼 저널이 틀린 상태를 발견하면 그대로 보고하고, 근거가 있을 때만 고친다.

## 11. 자기수정 루프 운영 (저널 → 파라미터)

```bat
C:\Users\jhshi\Python312-64\python.exe tools\review.py --dry-run   :: 결정만 확인
C:\Users\jhshi\Python312-64\python.exe tools\review.py             :: strategy_params.json 반영
```

- 청산 완료 거래가 `--min-samples`(기본 5) 미만이면 **아무것도 쓰지 않는다** (기본값 유지).
- 반영 즉시: 엔진은 다음 `fetch()` 부터 필터·Kelly 사전확률을 새 값으로 사용.
- 루프쪽(`screeners`, `r1_min_avg_score`)은 **루프 재시작 시** 반영.

## 12. 실계좌 운영 (2026-09-28 전환)

모의투자(계좌 777020450)에서 실계좌로 넘어가며 바꾼 것과, 실계좌에서 지킬 규칙.

### 12.1 전환 시 한 일

| 항목 | 내용 |
|---|---|
| 모의 저널 은퇴 | `journal/paper_20260928.sqlite3` (청산 33건 · 미청산 8건 · 실현 +3,662원) + 성적표 `journal/PAPER_SESSION_20260928.md` |
| 새 저널 | `journal/trade_journal.sqlite3` — 스키마 v4 빈 상태. 실계좌 실적만 쌓인다 |
| 도구 | `tools/retire_paper_journal.py` (예행 → `--apply`) |

왜 아카이브했나: 모의 미청산 8건은 실계좌에 없다. 그대로 두면 동시보유 게이트가 8건을 세어
신규 진입을 막고, 마감 대조가 '저널 보유/브로커 없음'을 반복하며, 없는 종목을 팔려는 시도가 생긴다.

### 12.2 실계좌 한도 (모의 대비 하향, 2026-09-28 사용자 승인)

| 설정 | 위치 | 모의 | **실계좌** |
|---|---|---|---|
| 일일 진입 상한 | `runner/config.py: r2_max_entries_per_day` | 100 | **3** |
| 일일 집행 상한 | `trader_core/config.py: daily_max_trades` | 100 | **3** |
| 종목당 비중 | `max_position_pct` | 0.20 | **0.10** |
| 동시 보유 | `max_open_positions` | 5 | **3** |
| 총 투자 비중 | `max_invested_pct` | 0.40 | **0.30** |
| Kelly 배포계수 | `trader_core/config.py: kelly_fraction` | 0.25 | **1.0** |
| 일일 손실 자동청산 | `runner/config.py: r4_loss_limit_pct` | 0.03 | 0.03 (그대로) |

`kelly_fraction` 을 올린 이유(2026-09-28 실측): f* = 0.06(콜드스타트 승률 0.53) × 0.25 = 종목당
1.5% = **8,001원**(53만원 계좌) → `sized_qty` 로 23,103원 0주 · 15,400원 0주 · 10,000원 0주.
1.0 으로 올려 종목당 6% = 32,000원 → 23,103원 1주 · 10,000원 3주. f* 자체는 그대로이고
`kelly_cap` 0.25·등가리스크 캡(0.5% = 2,667원)이 계속 걸린다. **계좌가 커지면 0.25 로 되돌릴 것.**

테스트가 값을 고정한다: `trader_core/tests/test_risk_reinforcements.py::ConfigDefaultsTest`.
값을 바꾸면 테스트도 함께 고치고 `python -m pytest trader_core/tests -q`(284 통과 기준) 를 돌린다.

### 12.3 비상 정지 / 해제

```bat
emergency_stop.bat     :: kill_switch.txt 생성 + 루프 종료 (포지션은 건드리지 않는다)
emergency_resume.bat   :: kill_switch.txt 삭제 + 루프 재기동
```

- `kill_switch.txt` 는 **매매 게이트**다. 생성되면 신규 진입·매매가 멈추고 자동으로 지워지지 않는다.
- 전량 청산은 별개다: 일일 손실 한도(R4, -3%)를 넘으면 루프가 **스스로 전량 시장가 청산 + 정지**한다.
  즉시 청산이 필요하면 HTS 에서 직접 매도한다(브리지 경유 청산은 루프를 켜야 한다).

### 12.4 실계좌 첫날 체크리스트

1. 크레온 로그인(사람 단계) → 브리지 `curl --noproxy "*" http://127.0.0.1:8100/health` 가 `connected:true`.
   로그인 직후 낡은 세션은 `trader-bridge-sup` 이 자동 재기동한다(`bridge_run_admin.bat` 수동 가능).
2. `curl --noproxy "*" http://127.0.0.1:8100/balance` — **계좌번호가 실계좌인지**(모의 777020450 이 아니어야 함)와 시작자본 확인.
3. 저널이 비어 있는지: `tools/reconcile_journal.py` → '불일치 없음'(보유 0).
4. 루프 기동은 `trader-loop-sup` 작업을 다시 켜면 된다(다음 평일 08:30 자동 시작):
   `schtasks /Change /TN trader-loop-sup /ENABLE`
5. 첫 거래일은 진입 3건·노출 30% 상한이 실제로 걸리는지 `loop.out.log` 에서 확인.

되돌리기(모의 복귀): 위 4개 한도를 100/100/0.20/5/0.40 으로 원복하고, `trade_journal.sqlite3` 를 지운 뒤
`paper_20260928.sqlite3` 를 그 이름으로 되돌린다(**루프 정지 상태에서만**).

### 12.5 청산 후 수수료·세금 반영 (백로그 T1)

저널의 청산 손익은 `(exit - entry) x qty` **gross** 이고 `fees` 는 0 이다. 모의에서는 비용이 안 나가서
문제가 없었지만 실계좌에서는 그 비용이 현금으로 나간다 — net 을 모르면 전략 판정이 불가능하다.

```bat
C:\Users\jhshi\Python311-32\python.exe tools\backfill_fees.py                  :: 예행
C:\Users\jhshi\Python311-32\python.exe tools\backfill_fees.py --apply          :: 저널 반영
C:\Users\jhshi\Python311-32\python.exe tools\backfill_fees.py --date 2026-09-29
```

- 방식: 종목별 `저널 gross - 브로커 매도실현손익(CpTd6032) = 비용`. **요율을 가정하지 않는다** —
  브로커 값이 기준이고, 하루에 같은 종목을 여러 번 청산했으면 금액 비중으로 배분한다.
- 근거(2026-09-28 삼현 실측): 저널 -6,400 vs 브로커 -6,516 → 116원 = 회전 6.9만원의 0.17%(수수료+거래세).
- 반영은 `Journal.set_fees()` 가 **가격에서 pnl 을 다시 계산**하므로 두 번 돌려도 값이 어긋나지 않는다.
- ⚠ 가정: 브로커 매도실현손익이 net 이라는 전제다. 첫 실계좌 청산에서 차이가 0 에 가깝게 나오면
  (브로커도 gross 라면) 이 방식은 무효 — 그때는 요율 기반으로 바꾼다. `check` 플래그(음수 비용·회전 1% 초과)는
  사람이 확인한다.
- 마감 대조(§8.2) 뒤에 돌리는 것이 순서다: 대조가 청산가를 바로잡은 다음 비용을 얹는다.


