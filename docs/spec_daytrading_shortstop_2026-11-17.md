# 작업 지시서 — 진입 필터 + 초단기(1~2%) 단타 라인

작성 2026-11-17 / 대상 저장소 `trader-agent`(실행) + `analyist_dd`(피드) / 요청자: 사용자(jhshi)
이 문서는 **Claude Code 에 그대로 넘기는 위임 지시서**다. 아래 "실측 근거"는 이미 측정된 값이므로
다시 측정하지 말고 그대로 인용할 것.

---

## 0.0 배포 방식 (사용자 확정 2026-11-17)
- **모의계좌 트랙 없음. 실계좌로 계속 테스트한다**("모의계좌 말고 실계좌로 계속 테스트해줘").
- 순서: ① 진입 필터 배포 → ② 단타 프로파일(1.5%/2.5%) 실계좌 적용. 둘 다 이 지시서 범위.
- 실계좌 제약: 계좌 783247576, 자본 약 29만원, 일 3건·종목 1/3·총 100% 한도 **불변**.

## 0. 배경 (실측된 사실)

1. 현 전략은 스윙(손절 −7% / 익절 +7%, 최대 5세션)이고, 실계좌 8건 중 3승.
2. 사용자 방향 전환: **손절·익절 1~2%의 초단기 단타 위주**(스윙 아님).
3. 왕복 비용은 **0.21%**(수수료+거래세, 실제 체결로 확인). 따라서 ±1% 라인의 손익분기 승률은 **60.5%**,
   ±2% 는 **55.2%** — 좁은 라인은 수수료가 지배한다.
4. 일봉 OHLC 그리드 실측(신호 11,300건, 2026-04-01~11-14, scripts/_stoploss_grid.py):
   | 손절/익절 | worst평균 | best평균 | 중립 | 손절터치 | 익절터치 | 손절후종가↑ |
   |---|---|---|---|---|---|---|
   | 1%/1% | −0.992% | +0.255% | −0.369% | 88.9% | 73.1% | 26.8% |
   | 2%/2% | −1.370% | +0.228% | −0.571% | 77.6% | 59.6% | 18.4% |
   | 1%/2% | −0.892% | +0.590% | **−0.151%** | 88.9% | 59.6% | 26.8% |
   | 7%/7% | −1.152% | −0.492% | −0.822% | 25.8% | 24.5% | 2.0% |
   → **결론: 손절 폭을 좁히는 것만으로는 이익이 생기지 않는다.** 같은 신호의 무손절 기준선이
   −1.328%(승률 36.0%)이므로 병목은 청산이 아니라 **진입**이다. 좁은 라인의 유일한 효과는 손실 축소.
5. 진입 필터 실측(scripts/_entry_filter_test.py, 동일 신호 11,300건):
   | 필터 | 차단 | 차단분 평균 | 통과분 평균 | 개선 |
   |---|---|---|---|---|
   | ROE<0 | 2,153건(19%) | −1.428% | −1.291% | **+0.137%p** |
   | 경고성 공시 30일 | 209건 | −1.391% | −1.327% | +0.064%p |
   | 희석성 공시 14일 | 733건 | −1.191% | −1.338% | −0.147%p(역효과) |
   | 조합(ROE<0 + 경고공시) | ~24% | −1.505% | −1.273% | +0.056%p |
   → **ROE<0 만이 통계적으로 유효**하고, 희석성 공시 단독 필터는 근거가 없다. 희석성 공시는
   **청산 트리거**(보유 중 발생 시 즉시 청산)로 쓰는 것이 맞다(푸드나무: 진입 11-11, 유상증자 공시는 11-17 = 사후).

---

## 1. WP1 — 진입 필터 (재무·공시)

### 1.1 analyist_dd (피드 생성)
- 파일: `scripts/swing_screener.py`(또는 피드 발행 경로) + 신규 `scripts/entry_filters.py`
- 함수 `apply_entry_filters(rows, asof) -> (kept, blocked)`:
  - `financial_statements` 최신 `report_date <= asof` 기준 **roe < 0 → 차단**(사유 `roe_negative`)
  - `disclosures` 최근 30일 `report_nm ~ '관리종목|투자주의|불성실공시|상장폐지|거래정지'` → 차단(`market_warning`)
  - 재무 데이터가 없는 종목은 **통과**(fail-open) + `blocked_reason="no_financials"` 로그만
- 피드 JSON 에 `blocked` 배열 추가: `[{code, name, reason}]` — 왜 뺐는지 사용자가 볼 수 있어야 한다.
- 회귀: 차단된 종목 수·사유 분포를 `data/reports/entry_filter_YYYYMMDD.json` 로 저장.

### 1.2 trader-agent (2차 방어)
- 파일: `trader_core/engine.py` 진입 게이트, `trader_core/config.py`
- `entry_block_roe_negative: bool = True`, `entry_block_market_warning: bool = True` 설정 추가.
- 신호 수신 시 DB 조회로 재확인(피드를 신뢰하지 않는다). 실패 시 **fail-open**(거래 계속) — 단 로그 필수.
- 게이트 사유 문자열은 `gates` 딕셔너리에 `entry_filter_roe`, `entry_filter_warning` 키로 남긴다(현재 `feed_fresh` 등과 동일 형식).

### 1.3 테스트
- `trader-agent/tests/test_entry_filter.py`: ROE<0 차단 / 재무 없음 통과 / 경고공시 차단 / 조회 실패 fail-open 4케이스.
- `analyist_dd/scripts/_entry_filter_test.py` 를 정식화해 `--since` 로 재측정 가능하게 유지.

---

## 2. WP2 — 초단기(1~2%) 단타 프로파일

### 2.1 프로파일 추가 (`trader_core/profiles.py`)
```python
"daytrading": {
    "entry_window": "09:05-14:30",     # 시가 추격 금지: 09:00 정확 진입 대신 09:05 이후
    "exit_mode": EXIT_SAME_DAY_CLOSE,
    "close_exit_window": "15:00-15:20",
    "hold_overnight": False,
    "kelly_fraction": 0.10,            # 좁은 라인 = 잦은 손절 → 사이즈 축소
    "max_entries_per_day": 3,
    "stop_mode": "fixed",
    "stop_pct": 0.015,                 # 손절 1.5%
    "tp_pct": 0.025,                   # 익절 2.5% (R:R 1.67)
    "max_stop_pct": 0.02,              # 변동성 확대 시에도 2% 초과 금지
},
"swing": { ... "stop_pct": 0.07, "tp_pct": 0.07 ... }   # 기존 값 명시 고정
```
- `engine.py` 의 `volatility_stop_pct()` 경로에서 `stop_mode == "fixed"` 이면 **고정값 사용**,
  `max_stop_pct` 로 상한, `tp_pct` 는 고정 익절로 사용. 변동성 스톱은 `stop_mode="vol"` 로 남겨 롤백 가능하게.
- 손익분기 승률 표를 코드 주석에 남긴다(±1.5% → 57.0%).

### 2.2 청산 트리거 추가(요청 근거)
- 보유 중 **희석성 공시**(유상증자·CB·신주인수권) 발생 시 당일 청산: `reason="dilution_disclosure"`.
  푸드나무 사례(진입 후 유상증자 공시 → −7% 손절)를 규칙으로 막는다.
- 보유 중 **5거래일 상대약세 ≤ −3%p** → 다음 틱에 청산(`reason="relative_weakness"`). 스윙 전용.

### 2.2b 단타 피드 배선 (필수 — 지금 daytrading 후보 0건)
- 현재 피드: `close 0 · swing 20 · daytrading 0`.
- `scripts/daytrading_screener.py --top-n 20 --json-out reports/daytrading_latest.json` 를
  평일 **08:35** 크론에 추가하고 `scripts/feed_export.py --daytrading reports/daytrading_latest.json` 로 발행.
- `score_kind="composite"` 유지(calibrated_prob 변환 금지). 발행 후 `candidates.daytrading` ≥ 1건 확인.

### 2.3 테스트
- `tests/test_profiles_fixed_stop.py`: 고정 손절이 변동성 스톱을 덮는지, `max_stop_pct` 상한, TP 도달 시 익절 가격.
- `tests/test_same_day_close.py`: 15:20 이후 미청산 포지션이 0건인지(당일 청산 보장).
- 회귀: 기존 `close`/`swing`/`rank_momentum` 프로파일의 손절·익절 값이 **바뀌지 않았는지** 스냅샷 테스트.

---

## 3. 절대 하지 말 것
- `max_invested_pct`, `daily_max_trades`(일 3건), `max_open_positions`(3종목) 변경 금지 — 자금 한도는 사용자 승인 항목.
- 실계좌 경로의 주문 함수 시그니처 변경 금지. 청산 규칙 추가는 `engine` 내부 분기로만.
- 기존 저널 스키마 변경 금지(컬럼 추가는 허용, 삭제·타입 변경 금지).

## 4. 완료 조건 (검증 가능)
1. `pytest trader-agent/tests -q` 전체 통과(신규 8케이스 포함).
2. `python analyist_dd/scripts/contract_check.py` → ALL PASS 유지.
3. 실계좌 1일 관찰(다음 거래일): 09:05~14:30 진입 → **15:20 이전 전량 청산**, 미청산 0건,
   손절 체결가 슬리피지 트리거 대비 ±0.3% 이내.
4. 종료 시 보고: 변경 파일(경로), 테스트 결과, 실측 손익, 남은 리스크.
5. 롤백: `stop_mode="vol"` 로 되돌리면 기존(변동성 스톱·7% 익절) 동작.
