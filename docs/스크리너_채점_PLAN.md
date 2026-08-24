# 스크리너 승률 채점 시스템 계획서

> 작성일: 2026-08-24
> 목적: 종가/스윙/단타 3개 스크리너의 발굴 후보를 각자의 시간창에서 **승률**로 채점하는
> 자동화 파이프라인을 만든다. 기존 성능 스크립트(`close_screener_performance.py`,
> `swing_performance.py`)의 검증 로직을 통합·재정의하고, 중복 채점을 레지스트리로 방지한다.

---

## 1. 3개 스크리너 시간창 정의 (매수→매도)

| 스크리너 | 매수 시점 | 매도 시점 | 승(win) 정의 | 채점 데이터 |
|---|---|---|---|---|
| **종가(close)** | 신호일 **D 종가** | **D+1 시가** | `D+1 열 > D 종가` (갭 업) | `market_data` 일봉 |
| **스윙(swing)** | **D+1 종가** | **D+N 종가** (N=7) | `D+N 종가 > D+1 종가` | `market_data` 일봉 |
| **단타(daytrading)** | **D+1 시가** | **D+1 개장 30분 후** | 30분 후 가격 > D+1 시가 | 분봉(아직 없음) |

### 1.1 단타 30분 창 분봉 데이터 부재 (KIS 발급 전)

- KIS 분봉 미발급이므로 **지금은 프록시**로 채점한다: `D 종가 → D+1 시가 갭`.
  - 매수 = D 종가(프록시), 매도 = D+1 시가(프록시). 승 = 갭 업.
- `MinutePriceProvider` 인터페이스로 30분 창을 **설계만** 해두고, 현행 구현체
  `DailyGapProvider`가 분봉 대신 일봉 갭으로 채점한다.
- 분봉(KIS)이 도착하면 `KisMinuteProvider`(MinutePriceProvider 구현)를 추가하기만 하면
  자동으로 30분 채점으로 전환된다. 단타 채점 본체는 인터페이스만 의존한다.

---

## 2. 승률·수익률 산식

단일 종목의 수익률: `(매도가 − 매수가) / 매수가 × 100` (%).

- **종가**: `(D+1 시가 / D 종가 − 1) × 100`. 승 = `> 0`.
- **스윙**: `fetch_forward_returns` 패턴 재사용 — `D+1 종가`(=rows[0]) 매수,
  `D+7 종가`(=rows[N-1], N=7) 매도 → `rows[N-1]` 오프바이원 주의. 승 = `> 0`.
- **단타(프록시)**: `(D+1 시가 / D 종가 − 1) × 100` (종가와 동일 산식이지만
  "갭 채점"이라는 별도 창 의미로 취급).

통계(스크리너별):
- `win_rate` = `승수 / 채점수 × 100` (%)
- `avg_return_pct` = 평균 수익률
- `median_return_pct`, `max_/min_return_pct`
- `sample_count` = 채점된 종목 수

---

## 3. 중복 채점 방지 — `scored.jsonl` 레지스트리

- 파일: `data/scoring/scored.jsonl` (append-only, 한 줄 = JSON).
- 키 식별자: `screener|signal_date|stock_code` (예: `close|2026-08-19|000001`).
- 동작:
  1. 스캔 대상 후보(`*_candidates_*.csv`)를 각각 `(screener, signal_date, stock_code)`로 분해.
  2. `scored.jsonl`에 이미 존재하는 키 → **스킵** (채점하지 않음).
  3. 창이 경과했고(해당 가격 존재) + 레지스트리에 없을 때만 채점.
  4. 채점한 건을 레지스트리에 append → 다음 실행에서 중복 방지.
- **idempotent**: 같은 후보를 몇 번 실행하든 채점은 1회만 수행. 크론(Hermes 담당)으로
  재실행해도 안전.

### 레지스트리 행 스키마
```json
{"key": "close|2026-08-19|000001",
 "screener": "close", "signal_date": "2026-08-19", "stock_code": "000001",
 "scored_at": "2026-08-24T14:00:00", "win": true, "return_pct": 2.5}
```

---

## 4. 통합 러너 `scripts/screener_score.py`

### 4.1 CLI
```
python3 scripts/screener_score.py \
    --screeners close,swing,daytrading \   # 생략 시 전부
    --score \                               # (기본 켜짐) 후보 채점
    --scoring-dir data/scoring \            # scored.jsonl / results
    --report-dir data/reports               # 후보 CSV + summary
```

- `--screeners` 생략 → 3개 스크리너 전부 대상.
- 각 스크리너 실행 시 기본 인자로 작동:
  - 종가: `close_screener.py --top-n 20` (기본)
  - 스윙: `swing_screener.py` (신호 기준 기본)
  - 단타: `daytrading_screener.py --top-n 20` (기본)
- 후보 CSV는 `data/reports/*_candidates_*.csv` 규약을 따른다.

### 4.2 채점 흐름
1. `--score`(기본) 모드 → `data/reports/*_candidates_*.csv` 스캔.
2. 각 후보를 `(screener, signal_date, stock_code)`로 정규화.
3. 스킵 조건:
   - 레지스트리에 이미 존재
   - 창 미경과 (필요 가격이 DB에 아직 없음)
4. `market_data`에서 가격 조회 → 창별 채점.
5. 채점 결과 append-only 레지스트리 + 상세 CSV + 요약 JSON.

### 4.3 산출물
| 파일 | 내용 |
|---|---|
| `data/scoring/scored.jsonl` | 중복 방지 레지스트리 (append-only) |
| `data/scoring/results_<date>.csv` | 채점 상세 (창·가격·수익률·승패·만료일) |
| `data/reports/scoring_summary.json` | 스크리너별 승률/평균수익률/샘플수 |

`scoring_summary.json` 스키마:
```json
{"generated_at": "...", "total_scored": 0, "skipped_existing": 0,
 "screener_stats": {
   "close":  {"win_rate": 60.0, "avg_return_pct": 1.2, "median_return_pct": 0.8,
              "max_return_pct": 8.0, "min_return_pct": -3.0, "sample_count": 5}
 }}
```

---

## 5. 단타 30분 지연 설계 (`scripts/day_trading_engine/`)

신규 모듈 (기존 파일 수정 금지):

```python
# minute_provider.py
class MinutePriceProvider(abc.ABC):
    """분봉 가격 제공 인터페이스 — 30분 창 채점에만 사용 (스크리너 본체와 분리)."""
    @abc.abstractmethod
    def get_minute_price(self, stock_code, d_plus_1_date, minute_offset=30) -> float | None:
        """D+1일 개장 후 minute_offset분 시점의 가격을 반환. 없으면 None."""

class DailyGapProvider(MinutePriceProvider):
    """현행 프록시: 분봉이 아직 없어 일봉 D→D+1 시가 갭으로 채점."""
    def __init__(self, pg_conn): ...
    def get_minute_price(self, stock_code, d_plus_1_date, minute_offset=30):
        # 분봉 부재 → D+1 시가를 "30분 시점가" 대용으로 반환
```

- 분봉 도착 시 `KisMinuteProvider(MinutePriceProvider)`만 신규 추가 → 매수(분봉 시가)/매도(30분가)로 동작.
- 채점 본체(`daytrading_performance.py`)는 `MinutePriceProvider`만 의존.

---

## 6. `scripts/daytrading_performance.py`

`close_screener_performance.py`의 T+1 채점 패턴을 복제해 단타용으로 생성:

```
python3 scripts/daytrading_performance.py --report-dir data/reports
python3 scripts/daytrading_performance.py --input data/reports/daytrading_candidates_x.csv
```

- 입력: `daytrading_candidates_*.csv` (칼럼: `stock_code, signal_date, close_price, ...`).
- 채점:
  1. `MinutePriceProvider` 인스턴스 획득 (현행 `DailyGapProvider`).
  2. D 종가(CSV or DB) → D+1 시가 갭 = 프록시 수익률.
  3. `minute_offset=30` 인자 제공 — 분봉 Provider로 전환 시 그대로 30분 시점 채점.
- 산출: `data/reports/daytrading_performance_<ts>.json/.csv` (선택 `--output`).

---

## 7. 크론 통합 지점

- **임의 예약 없음**: 본 작업에서 크론 등 자동 스케줄은 만들지 않는다 (Hermes 담당).
- **idempotency**: `scored.jsonl` 레지스트리로 재실행 안전 — Hermes가 크론에 등록해도
  중복 채점이 발생하지 않는 것이 설계 전제.
- 통합 예상 흐름(운영): 스크리너 실행 → 후보 CSV → `screener_score.py --score` 채점 →
  창 경과 후 채점 → `scoring_summary.json` 갱신.
- 단타 30분: KIS 분봉 도입 전까지 갭 프록시; 도입 후 `KisMinuteProvider` 교체 + 스케줄
  실행 시점을 개장 30분 이후로 이동.

---

## 8. 테스트 전략 (DB 없이 픽스처)

`tests/test_screener_score*.py`:
- 픽스처 CSV를 `tmp_path`에 생성 → 러너/채점 함수에 로드.
- `market_data`는 **전량 모킹**(`mock.MagicMock` cursor) 또는 함수 인자 주입으로 DB 배제.
- 항목:
  1. 창 계산 (종가 D→D+1 / 스윙 D+1→D+7 / 단타 갭)
  2. 갭 승패 판정
  3. **중복 채점 방지** — 같은 후보 2회 실행 시 채점 1회만 (레지스트리 카운트 확인)
  4. 요약 집계 (win_rate/avg/sample_count)
  5. 빈 입력 안전 (후보 없음/레지스트리 없음 → 크래시 없음)

실행: `cd /home/dduckbeagy/analyist_dd && python3 -m pytest tests/test_screener_score*.py -q`

---

## 9. 산출물 (신규 파일 목록)

| 경로 | 역할 |
|---|---|
| `docs/스크리너_채점_PLAN.md` | 본 계획 |
| `scripts/day_trading_engine/minute_provider.py` | `MinutePriceProvider` + `DailyGapProvider` |
| `scripts/day_trading_engine/__init__.py` | `MinutePriceProvider`/`DailyGapProvider` export (기존 파일은 **추가만**, 변경 금지) |
| `scripts/daytrading_performance.py` | 단타 성과 채점 (갭 프록시 + 30분 창 대비) |
| `scripts/screener_score.py` | 통합 러너 (스크리너 실행 + 채점 + 레지스트리 + 요약) |
| `tests/test_screener_score.py` | 채점 로직 단위 테스트 |

---

## 10. 제약 (준수)

1. **기존 파일 수정 금지** — 신규 파일만. (단, `day_trading_engine/__init__.py`는
   신규 export를 붙이는 **추가**를 의미하며 기존 export는 유지.)
2. `services/xgboost-ml/app/models/` 하위 쓰기 금지.
3. pytest는 호스트에서만.
4. `.env` 읽기 금지.
5. Git 커밋/푸시 금지.
6. 산출물은 `data/scoring/`, `data/reports/` 아래만.
7. 크론 등 자동 스케줄은 Hermes 담당 (스크립트는 idempotent).

---

## 11. 완료 기준

1. `docs/스크리너_채점_PLAN.md` 존재 (본 문서).
2. `python3 scripts/screener_score.py --help` + `python3 scripts/daytrading_performance.py --help` 정상.
3. `tests/test_screener_score*.py` 전부 green.
4. 마지막 메시지에 파일 경로 목록 + 테스트 결과 + 예시 명령.
