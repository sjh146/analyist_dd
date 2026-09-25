
## [리서처 R2] institution_ownership_pct 소스 발굴 (현재 100% 결측)  (2026-09-25 14:04)
- 결과: [조사]R2: 0 >= 10 → 미달
- 판정: 충족
- 근거: dbg: dq_col_quality_null_ratio{column=institution_ownership_pct} 가 사실상 1.0 (전량 NULL).
- 엔지니어 백로그: `XR2` (command·대조군 기입 필요)

## [리서처 R1] DART 공시 인덱스 확보 (2026-09-25 14:15)
- 결과: `disclosures` 테이블 신설(승인됨) + DART 정기공시 백필. rcept_dt·rcept_no·report_nm·corp_code 적재, 자기신고 원장 기록.
- 의미: 재무 피처의 공시 지연을 **가정(90/45일) → 실제 접수일**로 바꿀 수 있게 됐다.
- 엔지니어 백로그: `XR1` (command·대조군 기입 필요)

## [정정] 공시 지연 실측 — 소표본 오류 바로잡음 (2026-09-25 14:44)
- 앞선 보고: 반기보고서 45일 가정은 as-of 누수 위험(p50 63일)
- **정정**: 그 측정의 표본이 n=24(늦게 제출된 편향 부분집합)였다. DART 백필을 완주해
  n=2,708 로 재측정하니 **중간값 45일** — 가정이 정확했다. 누수 없음.
- 남는 것: 사업보고서 90일 가정은 과도하게 보수적(실제 중간값 78일, n=6,559).
- 교훈: **표본이 작으면 결론을 내지 말고 커버리지를 먼저 채워라.** DQ 원칙(미커버를
  0 으로 세지 않는다)이 통계 결론에도 그대로 적용된다.

## [리서처 R5] 뉴스 저장 전면 실패 발견·수정 (2026-09-25 15:21)
- 증상: news_analysis 저장 실패 24h 3,657건 / 마지막 저장 성공 2026-09-23 16:08
- 원인: 테이블에 url 유니크 제약 없음 + 앱의 ON CONFLICT (url) → PostgreSQL 거부
- 수정: uq_news_analysis_url 유니크 인덱스 (init-scripts/postgres/16, 승인 후 적용)
- 검증: 오류 즉시 중단(최근 5분 0건), 저장 재개는 다음 30분 사이클에서 확인
- 후속: 2026-09-23~25 뉴스 피처 공백(복구 불가) — 모델엔지니어는 결측 처리 필요
- 교훈: '분석은 되는데 저장이 안 되는' 실패는 로그를 grep 해야만 보인다. 이 서비스에도
  DQ 자기신고를 붙여야 한다(현재 실패가 로그에만 남고 메트릭이 없다).


## [리서처] 모니터링 4시간 실명 — exporter 스크랩 타임아웃 + nodata=정상 판정 버그 (2026-09-25 20:19)
- 증상: dq_snapshot 이 17개 메트릭 전부 `없음`(nodata) 인데 **rc=0(정상)** 을 반환. 15:50~20:03 약 4시간.
- 근본원인 1(데이터): postgres-exporter `/metrics` 가 DB 부하 시 **12.7초**
  (`pg_exporter_last_scrape_duration_seconds=12.71`, wall 13.4s)인데 Prometheus 는
  scrape_timeout 을 설정하지 않아 **기본 10초** → 매 스크랩 `context deadline exceeded`
  → `dq_*` 51개 + `market_data_*` 전부 Prometheus 에서 소실.
- 근본원인 2(왜 몰랐나): prometheus.yml 에 **alerting 섹션·Alertmanager 부재** → 규칙 17개가
  평가만 되고 아무 데도 발송되지 않았다. 게다가 `up==0`/`absent()` 규칙이 없어 타겟 다운을
  잡는 규칙 자체가 없었다. 게다가 3번째로, nodata 를 위반으로 세지 않아 **판정이 눈을 감았다**.
- 수정: ① postgres 잡 `scrape_interval 60s / scrape_timeout 45s`(DQ 는 분 단위로 안 변한다.
  부수효과로 exporter 의 DB 부하가 4배 감소: 13초×4회/분 → 1회/분) ② 규칙 3개 신설
  (`PostgresExporterDown`, `DQMetricsAbsent`, `PostgresExporterScrapeSlow` = 타임아웃 접근 사전경고)
  ③ `dq_snapshot.py` 가 nodata 를 **위반**으로 판정 — 기준선은 정상 스냅샷 21개에서 15개 핵심
  메트릭이 21/21 존재했다는 실측. 5분 lookback 덕에 스크랩 1회 누락은 nodata 가 되지 않으므로
  nodata = 진짜 장애다.
- 검증: 타겟 up / 스크랩 4.9~8.0초 / dq_* 51개 복구 / 알림 오탐 0(20규칙 중 활성 1건은 실제 AUC) /
  회귀 테스트 4/4 통과(전체실명·핵심1개소실 → rc=3, 정보용1개소실 → rc=0, 정상 → rc=0).
- 함정 기록: exporter 재기동 직후 ~26초간(DB 커넥션 수립 전) 스크랩이 `connection reset by peer`
  로 실패해 타겟이 잠깐 down 이 된다. 재기동 직후의 down 을 장애로 오독하지 마라.

## [리서처] feature_coverage "마지막 배치" 스코프 버그 — DQ 판정이 부분 재계산에 갈아탄다 (2026-09-25 20:19)
- 발견 경로: 위 수정으로 메트릭이 살아나자 3분 사이에 값이 뒤집혔다(데이터는 그대로):
  `stock_constant_ratio 0.3816→0.625`, `coverage_illusion_max 0.00024→1.0`,
  `null_ratio_max 0.00024→1.0`, `alive/dead 76/97→**16/3**`.
- 원인: `feature_coverage` 는 `feature_name` 이 PK 인 **현재상태 테이블**이다(피처당 1행,
  `computed_at` = 그 피처를 마지막으로 측정한 시각). 그런데 두 쿼리(`dq_feature`, `feature`)가
  `WHERE computed_at = (SELECT MAX(computed_at) ...)` 로 **"마지막 배치"만** 세고 있었다.
  R10 빌더(`build_supply_market_features.py`)가 20:08 에 19개 피처 행을 갱신하자 전역 DQ
  판정이 그 19개로 갈아탔다.
- 특히 위험한 방향: alive/dead 가 16/3 으로 보여 **북극성("살아있는 피처 ≥ 죽은 피처")이
  낙관적으로 왜곡**된다(실제 115/84). 반대 방향(부분집합 때문에 나머지 결함이 안 보임)도 성립한다.
- 수정: 배치 필터 제거 → 전체 테이블(현재상태) 기준. 살아있는 피처만 분모/대상으로 삼는다.
  실측 확정값: 종목상수 0.3391(39/115), 시장레벨 11, alive/dead **115/84**, feature_count 199.
- 임계값 재조정: `착시=MAX`·`결측=MAX` 는 전체 기준 기준선이 0.9998(최악 피처가 분모를 지배)이라
  **문턱을 둘 수 없다** → 정보용으로 강등하고, **개수 형태** 신설(`coverage_illusion_count`=15,
  `null_ratio_high_count`=8)에 문턱을 기준선 **위**(20/12)로 두었다.
  → 알려진 부채로는 조용하고 악화되면 뜬다.
- 검증: exporter 출력이 SQL 실측과 일치(15/8/0.3391/11/199/0.895일), 배치 스코프 패턴 잔존 0,
  알림 오탐 0.

## [리서처] R10 19개 피처 커버리지 — 전부 R10 배치 소속 결함 23건 (2026-09-25 20:19)
- 실측(전체 199개=180 기존 + 19 R10): 착시(naive-honest>0.10) **15개**, 결측>0.90&살아있음 **8개**.
  **180개 기존 피처는 착시 0 / 결측 0** — 결함 23건이 전부 R10 19개 배치에 몰려 있다.
- 수치: short_interest_ratio·days_to_cover·institution_ownership_pct 는 naive=1.0000 /
  honest=0.0000, retail_ownership_pct·foreign_ownership_pct honest=0.0002,
  market_impact_score 0.0009, short_selling_ratio 0.0022, foreign_net_buy 0.0728, momentum_3_12m 0.1401.
- **주의(해석 한계)**: 이 값은 R10 빌더가 **464일 전체 격자**를 분모로 측정한 것이다. 모델 패널
  창(최근 90~250일)에서는 값이 존재하는 구간만 들어가므로 커버리지가 더 높게 나올 수 있다.
  → "R10 피처를 못 쓴다"는 결론은 **금지**(소표본·오분모 함정). 창을 맞춘 재측정이 선행 조건이다.
- 확실한 것: ① 이 19개가 전역 DQ 판정을 지배하고 있었다(위 스코프 버그) ② 횡단면 사용 가능성은
  `stock_unique_median` 이 판정한다 — alive 16개 중 10개가 ≤1(측정 구간 내 종목별 값이 1개).

## [리서처] R9 판정 실명(모니터링 실명) 수리 — 살아있는 피처 17개가 '죽은 피처'로 보고되고 있었다 (2026-09-25 21:17)
- 증상: R9(이벤트 공시 피처 부활)가 `in_progress` 로 멈춰 있었고, `feature_coverage` 의 이벤트 17행은
  `nonzero_ratio = 0` · `window_days = 120` · `computed_at = 2026-09-24 18:11 UTC`(= 09-25 03:11 KST)였다.
- 실체: 같은 시각 라이브 테이블에는 값이 있었다 — `event_features` 214,851행 / 2,703종목,
  `disclosure_count_5d > 0` 인 행 131,285개(`sum=915,152`). `disclosures` 212,861행(2025-01-03~2026-09-23).
  → 스냅샷이 **원천 백필(09-25 14:32 KST) 이전**에 찍힌 값이었다. 데이터는 끝나 있었고 판정만 낡았다.
- 근본원인: `scripts/build_event_features.py` 만 **feature_coverage 를 갱신하지 않았다.**
  형제 빌더(`build_supply_market_features`·`build_macro_features`·`build_financial_ratio_features`)는
  각자 격자 분모로 자기 피처 행을 upsert 하는데 이 빌더만 빠져 있었다(R14 가 예고한 그 결함).
- 수리: `--coverage-only` 모드 + 빌드 후 자동 커버리지 갱신 추가(격자 분모 = `market_data >= 2025-06-16`,
  `null_ratio = 0` — 격자에 행이 없다는 건 '모른다'가 아니라 **이벤트 0건**이라는 빌더의 명시적 의미).
  `--cov-since` 를 `--since` 와 분리했다: 빌드를 좁은 구간으로 재실행할 때 커버리지가 짧은 창으로
  덮여 기준선이 뒤집히는 것을 막는다.
- 검증: 커버리지 스냅샷 실행 실측(119초) → 17/17 nonzero, `alive/dead` **147/52 → 164/35**,
  `stock_constant_ratio` 0.2789 → 0.3293(살아있는 피처가 17 늘고 그중 13개가 종목상수), 착시 변화 0.
  `dq_claim` 자기신고: claimed 17 / persisted 17 / source 1,113,634 (gap 0).
- **분모 함정(다음 사람이 밟지 않도록)**: `event_features` 행수(214,851)를 분모로 세면
  `disclosure_count_5d` 가 0.6111 로 나온다 — 실제 격자분모 값 0.1179 의 **5배 과대평가**다.
  이 테이블은 전부 0 인 행을 저장하지 않기 때문이다(실측 재료화율 0.193).

## [리서처] R16 창(250d) 기준 재측정 — '무효 피처'가 아니라 '분모가 시장 전체'였다 (2026-09-25 21:30)
- 방법: R10 19개를 464일 격자와 최근 250일 창 두 분모로 각각 실측(⋈ market_data, NULL 은 0 으로 세지 않음).
- 창을 좁혀도 순위 불변: momentum_3_12m 0.1356→0.2353, bb_position 0.9020→0.9632,
  relative_strength 0.9649→0.9679, foreign_net_buy 0.0705→0.0784, short_selling_ratio 0.0021→0.0037.
  → **464일 격자 판정이 오분모는 아니었다**(창을 바꿔도 결론이 뒤집히지 않았다).
- 엔지니어 제안 가능(250d): **3개** — momentum_3_12m(uniq_med 62, xsec 0.633), relative_strength(uniq 169),
  bb_position(uniq 169). 시장레벨(계약 #6 위반 → 횡단면 금지) 3개: adr·breadth·total_trading_value(xsec=1.000).
- **핵심 발견 — 수급 4종의 커버리지 0.08 은 피처 결함이 아니라 유니버스 문제**:
  격자 3,970종목 중 수급 값을 가진 종목은 **338개**(null 0.9163). 그 338종목 행 안에서의 nonzero 는
  **0.9370**(선별 문턱 0.044 의 21배)이다. 즉 시장 전체를 분모로 두면 "무효", 338종목으로 좁히면 "강한 피처".
  R3(수급 이력 확장)로 500종목이 되면 시장 분모 커버리지는 0.0784 → 약 0.118 로 올라간다.
- 원천 없음으로 확정된 9개: ownership_pct 3종(ownership 225종목, null 0.9997~1.0),
  short_interest_ratio·days_to_cover(null 1.0), market_impact_score 0.0015,
  momentum_ni·momentum_op(null 0.58~0.61, uniq_med 0 — 재무 원천 부족).

## [리서처] 커버리지 착시 개수 메트릭의 기준선은 '측정 인구'에 비례한다 (2026-09-25 21:17)
- `dq_feature_coverage_illusion_count` 가 15 → **36** 으로 올라 25 문턱에서 위반이 났다.
- 원인은 악화가 아니라 **인구 증가**다: 배치별 착시 = R10 15 / R11 2 / R12 19, 09-24 패널 배치 0.
  측정 피처가 180 → 199개로 늘어난 만큼 절대 개수도 늘었다(같은 피처의 착시가 커진 것이 아니다).
- 조치: 문턱을 실측 기준선 위로 이동(스냅샷 warn 18→40 / breach 25→45, 알림 `>20`→`>45`).
  **경고**: 이건 "울리지 않게 문턱을 올린" 것이 아니라 "인구가 늘면 개수 메트릭의 기준선도 는다"는
  원칙의 적용이다. 개수 메트릭은 스코프 불변량이 아니므로, 다음에 인구를 늘리는 빌더를 투입할 때
  기준선을 다시 실측하고 갱신해야 한다(비율 형태 보조 메트릭은 미구현 — 다음 사이클 후보).
- check 무결성 경고: R13 의 check(`dead <= 78`)는 R9~R12 부활로 dead 가 97 → 35 로 줄어
  **조사 없이도 통과**한다. target 을 35 로 조였고, R13 의 실제 산출물(피처별 원천 판정표)이
  나오기 전에는 이 신호로 done 처리하지 않는다.
