
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
