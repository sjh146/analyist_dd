# analyist_dd 시스템 지도 (실측 2026-10-04)

"시스템을 이해하고 최적화한다"의 결과물. 모든 수치는 실측이고, 조치한 항목은 표시했다.

## 1. 목표와 통제 구조 (돈 기준)

- 최상위 목표: **돈을 버는 것**. 기계가 읽는 정의는 `config/objective.json`
  (지표 = 체결 가능 OOS 순기대 %p/세션 · 프로토콜 · 수용 기준 · 봉투 · 게이트).
- 현재 목표값(실측): **챔피언 순기대 −0.182%p/세션**(87세션·1,340표본, 앞/뒤 절반 모두 음수) ·
  다중폴드 OOS AUC 0.501 · 라이브 신호 12건 → **지금 사는 대상에는 기대값이 없다**.
- 자율 루프: 23:30 목표·돈 상태 → 20:00 파이프라인(돈 게이트로 승격) → 22:30 되돌림 감시 →
  03:30 개선 오케스트레이터 → 03:45 T1 탐색. 크론 ID는 `docs/AUTONOMY.md`.

## 2. 데이터 흐름 (수집 → 피처 → 모델 → 스크리너 → 트레이더)

| 단계 | 위치 | 상태(실측) |
|---|---|---|
| 일봉 수집 | `services/kis-collector`(KIS) + `scripts/krx_daily.py` + yfinance | `market_data` 3,112,266행 · 2023-01-02~2026-10-02 |
| 분봉 | `services/kis-collector`(minute) | 300종목 × 30행/일 (23:00 크론) |
| 공시 | `scripts/dart_disclosure_backfill.py` | `disclosures` 214,982행(10-02까지) + 이벤트 피처 빌더 19:10 |
| 매크로 | `scripts/macro_backfill.py`(FRED/ECOS) | 키별 가드 적용 |
| 피처 | `services/xgboost-ml/app/feature_engine/*` + `scripts/build_*` | `financial_ratio_features` 78만행, `supply_market_features`, `event_features` 21.7만행 |
| 학습·승격 | `services/xgboost-ml/app/training/*` (파이프라인 20:00) | 돈 게이트(`--require-expectancy`) + 라이브 스코어 토큰 |
| 스크리너·피드 | `scripts/swing_screener.py` · `feed_server.py`(8090) · `data/feed/screener_latest.json` | 08:30/14:40/20:30 발행 |
| 주문 | `trader-agent/`(WSL 밖, Windows) | 실계좌 783247576 · 봉투: 일 3건·종목 10%·총 30%·일손실 3% 자동중단 |

## 3. 자원 실측

- WSL 디스크 43G/1007G(5%) · 리포 541MB(services 349M · .git 95M · data 77M).
- 컨테이너 17개, 재시작 이상 없음. 메모리 상위: `stock_neo4j` 822MB · `stock_news_analyzer` 719MB ·
  `stock_xgboost_ml` 613MB (총 7.7GB 중 여유).
- DB 1,920MB. 상위 테이블: `market_data` 626MB · `financial_ratio_features` 337MB ·
  `supply_market_features` 324MB · `sns_posts` 302MB · `stock_vectors` 108MB · `event_features` 71MB.

## 4. 이번에 조치한 최적화

1. **중복 인덱스 제거**: `market_data` 에 `(stock_code, trade_date)` 유니크 인덱스가 **둘**(각 131MB)
   있었다 → 하나를 DROP(**−131MB**, 쓰기 비용도 감소).
2. **날짜 범위 인덱스 추가**: 야간 잡들이 `trade_date > X` 형태를 자주 쓰는데 전용 인덱스가 없어
   626MB 테이블을 순차 스캔했다 → `idx_market_data_trade_date`(21MB) 추가.
   적용 후 플랜: `Index Only Scan using idx_market_data_trade_date` ✓ (순차 스캔 → 인덱스 온리).
   순효과: **−110MB + 날짜 범위 질의 인덱스 전환**. 스키마 원본(`init-scripts/postgres/01_schema.sql`)도
   같은 내용으로 갱신(재초기화 시 되돌아가지 않게).
3. **`ANALYZE market_data`** 로 플래너 통계 갱신.

## 5. 알려진 함정 (실측으로 확인한 것)

- `services/<name>_`(언더스코어)는 **심볼릭 링크**(호환용) — 실제 디렉터리가 아니다.
- 컨테이너는 `data/reports` 를 마운트하지 않는다 → 밖 파일을 읽는 측정은 `docker cp` 로 스테이징해야 한다.
- `services/` 12곳이 각각 `app/` 패키지를 가진다 → 한 pytest 세션에서 두 서비스를 import 하면
  `app` 선점 충돌(`tests/_app_path.force_app()` 로 해결).
- 시세 적재 위생: 부분봉(장중)·거래정지 봉 제외, 보조 수집기 덮어쓰기 금지(기존 규칙 유지).
- 수집 호출 정책은 프로세스 밖에서 `scripts/net_guard.py` 가 강제(같은 uid 안에서만 — 다른 uid 는
  uid별 파일로 폴백하며 그 사실을 이벤트로 남긴다).

## 6. 남은 최적화 후보 (측정 필요)

1. **야간 증거 측정 재사용**: 파이프라인이 학습용으로 만든 피처 패널을 OOS 측정이 재사용하면
   매일 15~20분을 아낀다(현재는 따로 빌드). ML 내부 리팩터라 위험도 있음 → 별도 승인 필요.
2. **`sns_posts` 302MB**: 사용 경로(피처 반영 여부) 확인 후 보존 기간 정책.
3. **주말 피드 노후화**(42시간): 금요일 산출물을 월요일에 쓰는 구조 — 문구상 정상이나,
   월요일 08:30 파이프라인이 실패하면 화요일 매매가 낡은 후보로 돈다 → 감시 항목 추가 여지.
4. **`.git` 95MB**: 대형 산출물 리포트의 이력 — 필요 시 `git gc`/필터.
