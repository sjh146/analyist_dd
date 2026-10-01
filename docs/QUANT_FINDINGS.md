
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

## [리서처 R7] 휴장 캘린더의 당일 누락 자동 보완 (가드가 휴장일을 놓치는 문제)  (2026-09-26 04:00)
- 결과: [조사]R7: 0 >= 1 → 미달 | 증거: # KIS 키 없음/미인증/게이트웨이 오류 등 판별 불가 → KRX 2콜 프로브로 대체 | print("KIS 프로브 판별 불가 → KRX 프로브로 대체: {0}".format(d)) | exists, no_data = probe_krx(d)
- 판정: 조사완료
- 근거: 실측 2026-09-25(추석 연휴): 캘린더에 2026-09-24 만 있고 당일 09-25 가 없어, 자율 루프의 휴장 인식 가드가 하루 종일 '장중'으로 오판 → 휴장일 CPU 를 놀렸다. market_data 도 09-24·09-25 모두 0행(거래 없음)이라 휴장이 확실한 날이었다.
- 엔지니어 백로그: `XR7` (command·대조군 기입 필요)

## [리서처] R16 창 기준 실측을 재현 스크립트로 고정 · R7 check 주말 아티팩트 수리 (2026-09-26 04:15)
- **R16 배선 완료**: `scripts/r16_window_coverage.py` (읽기 전용, 실측 14초, JSON `data/reports/r16_window_coverage.json`).
  `feature_coverage` 에는 **쓰지 않는다** — feature_name 이 PK 인 현재상태 테이블이라 464일 값을 250일 창 값으로
  덮으면 창 정의 변경만으로 전역 판정(alive/dead·종목상수)이 뒤집힌다(실측 사고 경로 76/97→16/3).
  기존 check 는 그 덮어쓰기를 요구하는 형태(`WHERE window_days<=250`)였다 → 실측 스크립트 기반 check 로 교체.
- **실측(250일 창, 격자 617,974행 / 3,875종목 / 168거래일, 2026-01-17~09-23)**:
  SELECTABLE **2개** — relative_strength(nz 0.9997, uniq 168, xsec 0.000) · bb_position(0.9948, 168, 0.000).
  조건부 **1개** — momentum_3_12m(nz 0.2444, null 0.7550 이지만 값 보유 행 안에서는 nz 0.998, uniq 62).
  → 09-25 의 "3개" 는 **무조건 2 + 유니버스 조건부 1** 로 갈라진다.
  market_level(계약 #6 횡단면 금지) 3개 — adr·total_trading_value·market_breadth(xsec 1.000).
  원천 부족 13개 — 수급 4종(null 0.9136), ownership 3종(null 0.9996~1.0), short_interest·days_to_cover(null 1.0),
  short_selling_ratio(0.9961), market_impact_score(0.9984), momentum_ni·op(null 0.56~0.59).
- **함정 등록**: `cross_section_constant_ratio` 는 `nunique` 기반이라 **NaN 을 세지 않는다** → 결측 99% 피처는
  '값 1개인 날'이 다수라 xsec≈1.0 이 되고, 이걸 시장레벨로 읽으면 **원천 부재를 시장레벨로 오독**한다
  (실측: institution_ownership_pct null 1.0 / xsec 1.0). 스크립트는 `null>=0.5` 를 먼저 판정한다.
- **R7 check 수리**: 이전 check(`today in expected_dates()`)는 **주말에 구조적으로 0** 이다(캘린더는 평일만 담는다)
  → 2026-09-26 04:00(토) 틱이 "0 >= 1 → 미달"을 기록했다(수정은 이미 들어가 있었다). 교체 후 재검증:
  R7 eval_check = 1 >= 1 충족, R16 eval_check = 2 >= 1 충족. R7 성공 기준은 실측으로 확인 —
  `market_hours()` 가 휴장일 09-24 10:00 / 09-25 14:00 에 False, 정상 거래일 09-23·09-28 10:00 에 True.
  XR7 은 엔지니어 작업이 없으므로 종결 처리(소음 방지).
- **관측(엔지니어 소관, 미수정)**: `stock_xgboost_ml` 에 좀비 3개 누적(pid 2344707·2353926·2374996, 부모 2331621
  = 컨테이너 PID1 `python -m app.main`, 7시간 이상). 자식 reap 누락 — 위생 `warn` 의 유일 원인.
  디스크 4.0% / 댕글링볼륨 0 은 정상.

## [리서처 R16] 창(250d) 재측정 성공 + '컨테이너 좌표 누출' 함정 수리 (2026-09-28 06:02)
- **결과**: R16 rc=0, check **2 >= 1 충족**, 소요 0.2분(읽기 전용). SELECTABLE 2개 —
  `relative_strength`(비영률 0.9997) · `bb_position`(0.9948), 둘 다 선별 문턱 대비 22배.
  나머지는 `universe_missing`(원천 부족) 13개 + `market_level`(계약 #6) 3개 + `universe_limited` 1개.
  `feature_coverage` 에는 아무것도 쓰지 않았다(창 정의가 전역 기준선 alive/dead·종목상수를 뒤집는다).
- **실패 → 수리(같은 틱)**: 06:00 틱의 R16 은 rc=1 `psycopg2.OperationalError: could not translate
  host name "postgres"` 였다. 명령 자체는 정상이었고 **환경이 틀렸다** — 저장소 `.env` 는 컨테이너용
  (`POSTGRES_HOST=postgres`, `POSTGRES_PORT=5432`)이고, 그것을 스스로 읽는 스크립트
  (`scripts/r16_window_coverage.py::env_from_dotenv`)가 호스트에서 컨테이너 서비스명을 물려받았다.
  → 구동기가 자식(스냅샷·check·명령)에 **호스트 포트 매핑(127.0.0.1:5434)** 을 물려주도록 수리
  (`scripts/researcher_cycle.py::_host_db_env`). 명령 안에서 `export POSTGRES_HOST=...` 를 다시 하는
  항목(R3 형태)은 셸 우선순위로 그대로 동작한다. 회귀 5건: `tests/test_researcher_cycle_host_db_env.py`
  (미설정→호스트매핑 / postgres→매핑 / localhost·0.0.0.0→매핑 / 운영자 지정 원격좌표 보존 / 부모 os.environ 불변).
- **잠복 범위**: 같은 함정은 백로그의 모든 'DB 를 읽는' 명령에 있었다(R11 `build_macro_features.py`,
  R12 `build_financial_ratio_features.py` — 승인 대기 아님, 드라이버 수리로 함께 해소).
- 엔지니어 백로그: `XR16` (기존 항목, 중복 생성 안 함)

## [트레이더 환류 screener-attribution] [트레이더 환류] 스크리너별 실현 성과 — 어느 데이터가 기여했는지 확인 필요  (2026-09-28 13:59)
- 근거: 스크리너별 실현: rank_momentum: 30건 -5,149원, : 2건 -9,090원
- 리서처 백로그: `T17`

## [리서처 R11] 거시 피처 16개 부활 (macro_indicators as-of 조인)  (2026-09-28 15:35)
- 결과: [저작실패] 산출물 이미 존재(scripts/build_macro_features.py) — 저작 생략
- 판정: 미달
- 근거: feature_coverage: fx/oil/interest/cpi/ppi/yield/economic/cycle 계열 16개가 nonzero_ratio=0.
- 엔지니어 백로그: `XR11` (command·대조군 기입 필요)

## [엔지니어] 스윕 유니버스와 프로덕션 챔피언 유니버스의 교집합은 **9.5%** — 26사이클 무개선의 구조적 원인 후보 (2026-09-28 16:1x)
- **실측(DB 전용, 패널 빌드 없음·`wf_wave.py --universe-report --limit 200`)**:
  curated 경로(`train_curated._select_universe`, 지금까지 모든 스윕) = **KOSDAQ 200종목·KOSPI 0**,
  prod 경로(`app.training.universe.select_training_universe`, 프로덕션 챔피언 학습기와 같은 함수)
  = **KOSDAQ 141 + KOSPI 59**, 비주식 0 → **교집합 19/200 = 9.5%**.
- **해석**: 지금까지의 모든 Δ 는 챔피언이 학습하는 종목 집합과 **90.5% 다른 종목**에서 측정됐다.
  CG5(같은 config 49종목 +0.0227 → 150종목 −0.0109 부호 반전)·CG11(panel_150u 에서 전 성분 하회)과
  정합한다 — 즉 '승격 관문에서 이득이 깎인다'가 아니라 **측정 표본 자체가 승격 조건을 대표하지
  않는다**. 이것이 무개선 26사이클의 구조적 원인 후보 1순위다.
- **배선(이 역할 소유 파일)**: `wf_wave.select_panel_codes` 신설 + `--universe prod|curated`·
  `--universe-seed`·`--universe-report`·`--panel` 옵션, `wf_label_sweep` 에 전달. 기본값은 현행
  유지(기준선 재현성 보존). `--universe prod` + 기본 패널 파일명은 **거부(rc=2)** — 기준선 패널
  덮어쓰기로 대조군이 사라지는 사고를 막는다(실측 확인).
- **다음**: CG10(panel_prod200.npz 200종목 빌드 ≈15시간) → CG9(생산 트레이너 옵션 + 후보 →
  champion_promote --dry-run). 두 항목 모두 승격 실행이 아니라 **판정만** 한다.
- **CG12 신설·즉시 실행**: 유니버스 크기 단조 추세(게이트 ON 대조군 25/49/75/100/150종목 + 우승
  config 4수준, panel_150u in-run A/B) — 등록 기준선 0.5406 이 소유니버스 낙관 편향인지 검정.
- **백로그 위생**: L5b·L5c(pending 인데 command 없음 → 매 틱 경고)를 needs_setup 으로 내리고
  승인 필요 사유를 명시(팩터/전략 코드는 이 역할 소유가 아님).
- **패널 진단(읽기 전용)**: panel_420_asofpatch 210컬럼 중 **전부 0 인 컬럼 10개**
  (credit_balance_change·days_to_cover·disclosure_count_5d·etf_flow_5d·institution_ownership_pct·
  margin_balance_change·sector_count·sector_momentum·short_interest_ratio·value_ncav).
  거시/시장레벨 컬럼 19개(cpi_yoy·fx_*·interest_rate*·oil_*·ppi_yoy·yield_spread·krx_* 등)는 값이 있다
  → XR11(거시 피처 부활)은 '패널에 없다'가 아니라 '횡단면 상수라 계약 #6상 그대로 투입 금지' 문제다.

## [리서처 R11-후속] 거시 피처 16개는 전부 '시장레벨' — 계약 #6 저촉 (2026-09-28 16:07)
- **실측 근거(SQL, feature_coverage 현재상태 테이블)**: R11 이 살린 16개 전부
  `cross_section_constant_ratio = 1.000` — fx_usd_krw·fx_change_1m/3m·oil_wti·oil_change_1m/3m·
  interest_rate·interest_rate_change_1m/3m·cpi_yoy·ppi_yoy·yield_spread·economic_event_count_7d·
  economic_event_impact·cycle_up·cycle_down. 같은 날 **모든 종목이 같은 값**이다.
- **수치**: 살아있는 피처 164 / 시장레벨(xsec>=0.99 & nonzero>0) 26 / **횡단면 변별력 있는 살아있는
  피처 138**. R11 배치가 시장레벨 26개의 61.5%(16개)를 차지한다.
  (grep 아님 — `SELECT COUNT(*) FILTER (...)` 로 직접 셈. 138 = 164 − 26 로 정확히 상보.)
- **데이터 자체는 정상**: as-of 위반 0(`dq_asof_violation_rows=0`, 빌더 자체 SQL 대조 8일×6피처 위반 0),
  null 0, 격자 3934종목×315일, 단위 확인. **결함은 값이 아니라 '쓸 수 있는 형태'다** —
  계약 #6 에 따라 횡단면 모델 피처로 제안할 수 없다. 쓸 수 있는 유일한 형태는 시장/국면(regime) 시계열.
- **왜 보고하나**: ① R11 의 check(`nonzero_ratio>0 인 피처 수 >= 3`)는 **시장레벨만 살아나도 '충족'**이라
  계약 위반이 진척으로 원장에 기록된다 ② `feature_alive_count` 만 보면 이 16개가 북극성
  (살아있는 피처 >= 죽은 피처)을 낙관 왜곡한다(실측: 164 중 9.8%).
- **조치(이번 틱에 실행 완료)**:
  ① 메트릭 신설 `dq_feature_alive_xsec_count`(시장레벨 제외 살아있는 피처 수, 실측 138)
     — `config/postgres-exporter/queries.yaml`(dq_feature 쿼리 끝 + metrics 끝, **순서 짝** 주의),
     exporter 재기동 후 Prometheus 에서 138 확인, 기존 dq_* 85개 전부 유지(총 86).
  ② `scripts/dq_snapshot.py` SPECS + `scripts/quant_scoreboard.py` 북극성 줄에 반영 →
     `[북극성·리서처] DQ ok | 살아 164 (횡단면 138)/죽은 35` (정보용, 임계값 없음).
  ③ 엔지니어 백로그 `XR11` note 에 '횡단면 피처 제안 금지' 경고 + evidence 를 실수치로 교체.
- **엔지니어 쪽 확인 필요(추정 아님, 실측)**: 최근 야간 스윕 요약(2026-09-28 07:12, 4개 arm)은
  **전부 `exclude_market_level = False`** 로 실행됐다 — 제외 플래그가 켜져 있지 않다. 이 16개가
  패널에 들어가면 날짜별 상수 = 시장 국면 프록시가 되어 횡단면 정보는 0 인데 폴드별 국면 암기
  위험만 더한다. XR11 에서 제외 유지 여부를 확정해야 한다.
- **회귀**: `tests/test_dq_snapshot_*.py`·`test_researcher_cycle_*.py` 36건 PASS.

## [엔지니어] 26사이클 만의 문턱 돌파는 **패널 특이**였다 — 같은 49종목 크기·다른 집합에서 부호 반전 (2026-09-28 16:14)
- **CG12 실측**(rc=0 · 10.3분 · panel_150u · 게이트 ON · 5폴드×5시드 · 24,742행에서 종목 부분집합):
  ```
  UNg_25  0.5276 ±0.0441  (min 0.4737 max 0.5888 승률 0.6)   ← 유동성 상위 25종목
  UNg_49  0.5243 ±0.0398  (0.4602~0.5723, 0.8)
  UNg_75  0.5087 ±0.0240  (0.4673~0.5329, 0.8)
  UNg_100 0.5034 ±0.0249  (0.4606~0.5337, 0.6)
  UNg_150 0.5101 ±0.0145  (0.4846~0.5268, 0.8)
  ```
  판정 **노이즈**(Δ UNg_25 − UNg_150 = +0.0175 < 사전문턱 +0.02) · 단조 하락도 아님(150 에서 반등).
  → '49종목 등록 기준선이 소유니버스 낙관 편향' 가설은 **기각**(25종목 이득은 폴드 std 0.044 안).
- **핵심 부수 판정(문턱 아님·축 종결 근거)**: 같은 런에서 우승 config(rank+스무딩+depth1)를 크기별로
  걸면 `RSg_25 0.4913 / RSg_49 0.4956 / RSg_75 0.4945 / RSg_150 0.4992` — **모든 크기에서 같은 크기
  대조군을 하회**(−0.0363 / −0.0287 / −0.0142 / −0.0109). CG3·CG4·CG6·CG7 이 문턱을 넘긴 +0.0227~+0.0338
  은 **49종목 패널 특이**로 확정 → 이 축은 승격 후보에서 제외하고 닫는다(등록 기준선 Δ+0.0227 주장
  철회는 아니고, '재현 안 됨' 기록).
- **하니스 재현성 확인**: RSg_150 0.4992 / UNg_150 0.5101 이 CG11(07:12) 수치와 **동일** — 측정
  파이프라인이 같은 조건에서 같은 값을 낸다(옛 결과를 새 결과로 오독하는 사고 없음).
- **같은 크기·다른 집합의 노이즈 첫 실측**: 49종목 0.5365(panel_420_asofpatch) vs 0.5243(panel_150u
  첫 49종목) = **−0.0122**. 이것이 U1/UN1/CG5/CG11/CG12 의 부호 반전을 설명하는 크기다 →
  후속 CG13(서로소 30종목 5구간, pending·est 20분)으로 이 산포를 문턱과 직접 비교한다.
- **유니버스 정합 배선 완료(CG10 선행조건)**: DB 실측 교집합 **19/200 = 9.5%**(위 항목 참조).
- **XR11 종결(거부)**: 거시 16개는 전부 시장레벨 — 누수 게이트 ④로 그대로 투입 금지. 패널에 이미
  19개가 있고 정보가 0(MK1 Δ−0.0125). 재개 조건은 국면 조건화 재설계.

## [리서처 R13] 죽은 피처 35개 분해 조사 (원천부재/커버리지/시장레벨/부활가능)  (2026-09-28 18:07)
- 결과: [조사]R13: 0 <= 0 → 충족 | 증거: source_absent  10  credit_balance_change, credit_spread, etf_flow_5d, institution_ownership_pct, margin_balance_change … | JSON: data/reports/r13_dead_feature_audit.json | R13 미분류 0
- 판정: 조사완료
- 근거: 2026-09-28 18:05 실측(스크립트 생성 전 수동 프로브 + 이후 스크립트 재현). dead 35 분해: 부활가능 11(원천 보유: market_data 3,093,148행 종목일봉 → atr_pct/quality_beta/quality_price_volatility_60d, stock_vectors 4,340행·4,340종목 → bayes 4·similarity_std·twin 2, news_analysis authenticity_score 5,575/8,418 → authenticity_avg) / 커버리지부족 7(krx_short_selling 2,393행이지만 **30종목뿐** → short_interest_ratio·days_to_cover, stocks.sector 1,544/4,340(35.6%) → sector_count·sector_momentum, sns_post_features 14,576행·250종목·184일 → sns 3) / 시장레벨 7(krx_derivatives 2,993행·stock_code **컬럼 없음** → basis·basis_change_5d·futures_premium·derivatives_volume, krx_program_trading 196행·시장 단위 → program_trading_ratio, economic_events 291행·10카테고리(전 종목 동일값) → event_macro_5d·event_market_liquidity_5d) / 원천부재 10(신용·융자 테이블 0개 → credit_balance_change·credit_spread·margin_balance_change, ETF 테이블 0개 → etf_flow_5d, 테마 테이블 0개(그래프 전용) → theme 4, financial_statements 에 유동자산·유동부채 컬럼 없음 → value_ncav, ownership.institution_ownership_pct 컬럼은 있으나 100% 결측 → institution_ownership_pct).
- 엔지니어 백로그: `XR13` (command·대조군 기입 필요)

## [리서처 R3] 수급 이력 확장: foreign_institutional 343 → 800종목  (2026-09-28 18:09)
- 결과: R3: 343 >= 500 → 미달
- 판정: 미달
- 근거: DB 실측: foreign_institutional 343종목/87,895행. 유니버스 풀은 2,428종목(250일 이상 + 유동성 1억 이상). 150종목 실험군 0.5727 vs 49종목군 0.5400.
- 엔지니어 백로그: `XR3` (command·대조군 기입 필요)

## [정정] 리서처 R3 자동 인계(XR3)는 무효 — 러너 결함 2건  (2026-09-28 18:10)
- XR3 는 R3 러너가 **1초 만에 rc=2 로 죽은** 실행에서 자동 생성된 인계다. 수급 데이터는 준비되지 않았다.
- 결함 ①: `run_with_claim.py` 위치인자 형태 → 현재 CLI 는 `--runner/--table` 필요.
- 결함 ②: 종목 유니버스 미지정 시 이미 수집된 종목만 대상 → 종목 수가 늘 수 없어 check(>=500)가 구조적으로 통과 불가였다(완료 318 / 남음 0 실측).
- 조치: `data/kis/supply_universe_800.txt`(800코드) 주입 + 래퍼 인자 수정 후 재실행. XR3 는 재실행 성공 전까지 채우지 말 것.

## [리서처 감사] check 결함 5건 — '미달'의 일부는 데이터가 아니라 판정이었다  (2026-09-29 06:25)
- 계기: R10·R12 가 미달로 남아 있었는데 DB 를 직접 읽으면 값이 살아 있었다 → 백로그 **전수 감사기**
  `scripts/r_check_audit.py` 신설(읽기 전용·구동기와 같은 `eval_check` 경로로 23개 항목 재판정).
- 결함 ① **배치 스코프 필터**(R10·R11·R12): check 가 `computed_at=(SELECT MAX(computed_at))`
  = '마지막 배치'를 요구했다. 9/28 06:38 거시 배치 16행이 MAX 를 차지하자 9/25 배치 소속 피처가
  판정에서 빠져 **0(미달)** 로 읽혔다(실측 필터有 0 / 無 5). 필터 제거 후 실측 R10 5/5 · R11 5/5 · R12 5/5.
- 결함 ② **R1 오배치 + 오류 마스킹**: check 가 `financial_statements.rcept_dt` 를 읽는데 그 테이블에
  rcept_dt 컬럼이 없다 → `2>/dev/null || echo 0` 이 오류를 **0 으로 위장**(영구 미달). 실제 소재는
  `disclosures`(212,861행 / 3,052종목 / rcept_dt 100%) → 3052 >= 100 충족.
- 결함 ③ **R14 사각지대**: status=in_progress 는 `next_item()`(pending 만)에도 틱 경고 추출기
  (pending·needs_approval·partial·failed)에도 걸리지 않아 잔여 작업이 **어떤 보고에도 안 나왔다**
  → partial + `blocked_by`(엔지니어 exp_panel 재빌드 대기)로 전환.
- 결함 ④ **R3 분모**: `COUNT(DISTINCT stock_code) FROM foreign_institutional` 은 유니버스 밖 코드를
  포함(실측 892)해 유니버스 800 중 267종목이 비어도 통과한다 → 유니버스 분모 프로브
  `scripts/r3_universe_coverage.py` 로 교체(파일 없으면 항목 정의로 SQL 재생성). 실측 2026-09-29 06:0x:
  유니버스 800 ∩ 수급 533(미커버 267) — check_target 500 은 이미 충족.
- **모델 영향**: '죽었다'로 보였던 R10·R12 피처는 **살아 있었다**. 단 R10 의 5개 중 `market_breadth`
  는 xsec_const=1.000(계약 #6 위반 — 횡단면 부적격), `short_selling_ratio` 는 nz 0.0022(사실상 무효)이므로
  엔지니어 인계 목록에 '유효 피처'로 세면 안 된다. R12 재무 비율은 `financial_ratio_features`
  775,010행 / 2,569종목 / as-of 위반 0 으로 검증됐고, 격자가 9/23 에서 멈춰 있다(시장 최신일 9/28)
  — 다음 실행이 그리드를 갱신한다.

## 트레이더 틱 (2026-09-29 09:4x, 개장 직후 — 실측)

- **저널-브로커 불일치(신규)**: 실계좌 `/balance`(09:26:19) = 보유 1종목 **클로봇(466100) 23주, 매입 531,369원**
  (equity 529,966원의 100% · 현금 14,725원)인데 새 실계좌 저널은 0건 → 매입 주체·시점 미상(사람 확인 필요).
  루프를 켜면 이 포지션이 노출·동시보유 계산에서 빠지므로, 마감 대조(reconcile) 전에 정리 필요.
- **피드 내용 신선도 결함(신규, 트레이더 소유)**: 9/29 08:40 발행물은 파일 mtime 검사로는 통과(위반 0)하지만
  close 후보 20건의 `signal_date=2026-09-23`(경과 6일, 시장 최신 거래일 9/28). `close_price` 가 지정가로
  **그대로** 쓰이므로 낡은 종가 = 낡은 지정가 → 게이트 신설 **T7** + 프로브 `--probe feed_signal_lag`.
  원천(close 스크리너 데이터 격자가 9/23 고정)은 리서처 소유 → **R22** 로 환류.
- **T6 측정 결함 수리(트레이더)**: 누적 카운트(영원히 통과 불가) → 최근 24h 롤링 60분 최대치로 교정.
  실측 37.0회/시간(9/28 19:45~20:52 에 41회, ~99초 간격) = 미달. 9/29 03:00 이후 재기동 1회.
- **브리지 연결 단절(09:27:10, 원인 미상)**: 03:00~09:26:39 connected:true → 09:27:10 "Creon not running"
  → 이후 `/health` connected:false, `/quotes` not_connected, 09:44 python.exe 0개. ncstarter.log 09:35~09:36 에
  `다운로드 오류 (NCFSYS, exitcode=-1)` 반복 — 스타터 업데이트 실패가 로그인을 막는지 확인 필요(사람 화면).

## 트레이더 틱 (2026-09-29 11:2x, 장중 — 실측)

- **실행 경로 복구**: 루프 pid 13840(10:09:48 기동) · `loop_state` 11:25:08 갱신(지연 0.9분) · phase=monitor ·
  halt=False · 실패 0 · 브리지 `/health` connected:true. 09:4x 의 '루프 0개·loop-sup Disabled'는 해소.
- **진입 0 의 실측 원인 3계층**(무거래의 정체): ① close 20/20 이 `signal_date 2026-09-23`(6일)로 후보별 staleness 차단
  (`candidate_max_age_days.close=4`) ② swing `R1 block: avg prob 0.550 < 0.58` ③ 사이징 0주 13건
  (자본 52.7만 × 종목 10% 한도로는 5만원 초과 종목이 0주 — 실측 356860·403870).
  소유: ① 리서처 R22 ② 엔지니어(모델 확률) ③ 트레이더(한도/문턱, 사람 승인).
- **클로봇(466100) 소멸 — 반대 방향의 저널-브로커 불일치**: 09:26~09:51 브로커 보유 23주(매입 531,369원) →
  11:26 `/positions` 0종목 · `/orders` 0건 · equity 527,670원 = 전액 현금. 저널·`runner.log`·`bridge.log` 어디에도
  매도 기록 없음 → **수동 매도 추정(증거 없음)**. equity 529,966 → 527,670 = -2,296원(추정).
  실계좌는 개시(09-28 14:49 저널 리셋) 이후 **진입·청산 0건** = 성과가 아니라 무거래.
- **브리지 감독 churn 지속(실측)**: `bridge.log` 10:20:49~10:59:36 재기동 **25회(~97초 간격)** —
  9/28 저녁과 같은 패턴이 'Creon 로그인 유지(connected:true)' 상태에서도 반복된다. `--probe bridge_churn` = 37.0 > 4(T6 failed).
  지수 백오프가 없으면 로그인 재시도와 겹칠 때 DibServer COM 세션이 꼬일 위험(트레이더 소유, 사람 승인 필요).
- **환류 크론 첫 실행 대기**: `trader_fills_ingest.sh`(jhshi crontab 평일 16:40)는 오늘 신설 → 아직 미실행.
  실계좌 청산 0건이라 스크리너별 실현은 오늘도 측정 불가(모의 아카이브 값만 유효).

## 트레이더 틱 (2026-09-29 15:2x, 마감 직전 — 실측)

- **실계좌 첫 진입 3건(15:03:40~41, close 피드)**: 덱스터 31주·네오리진 20주·디케이락 3주 = 매입 89,236원 /
  평가 88,544원(**미실현 -692원**) · equity 526,943원 · 일일 3/3 소진 · `exit_mode=next_open`(익일 시가 청산).
  실현은 +0원 → 북극성(₩) 판정은 청산 이후로 이월.
- **R22 증상 해소 실측**: 14:40 종가 크론 재계산으로 close `signal_date` 09-23 → 09-28,
  `--probe feed_signal_lag` **6.0 → 1.0**(통과). close 경로가 열린 것이 오늘 진입 3건의 직접 원인.
- **잔여 미달**: fees 0.0(T1 — 청산 0건이라 측정 불가) · bridge_churn 37.0/h(T6) · expectancy NA.
  계약2 미비 `purge`(MT49) 유지, 신규 환류 MT70(R1 문턱)·MT71(켈리 f*<=0).

## [리서처 R10] 수급·시장·모멘텀 피처 19개 부활 (보유 테이블만으로 계산)  (2026-09-29 15:39)
- 결과: [저작생략] 산출물 이미 존재(scripts/build_supply_market_features.py) — 저작 생략 | R10: 5 >= 3 → 충족
- 판정: 충족
- 근거: feature_coverage: institution_*·foreign_*·short_*·momentum_*·krx_*·market_* 계열 19개가 nonzero_ratio=0. 원천 테이블은 각각 수천~수십만 행 적재 완료(실측).
- 엔지니어 백로그: `XR10` (command·대조군 기입 필요)

## [리서처 R21] 수급 지연 감시 판정 모집단 교정 — '테이블 전체' → '회전 유니버스' (2026-09-29 20:0x, 읽기 전용 실측)

- **왜**: check 가 `foreign_institutional` **테이블 전체**를 분모로 세면, 일일 회전(`kis_supply_backfill.liquidity_universe`
  = 유동성 상위 800)이 애초에 담당하지 않는 코드가 MAX 를 지배해 **회전이 완벽히 돌아도 '미달'** 이 뜬다(실측 1,034종목 중 238종목이 회전 밖).
- **실측(같은 시각)**: 회전 유니버스 최대 지연 **1거래일**(785종목 0 / 15종목 1) · 테이블 전체 최대 **3거래일**(회전 밖 11종목이 9/22 정지, 거래정지 008290 제외).
- **모델 유니버스 커버리지 구멍**: R3 유니버스(시총 상위 800) 최대 지연 2거래일 / 뒤처진 43종목. 그중 **220종목이 회전 유니버스 밖**
  (30종목 지연 1, 2종목 지연 2) — 모델이 쓰는 종목인데 일일 수급 수집 경로에 없다. 회전 대상을 합집합(약 1,020종목)으로 넓히려면
  **KIS 일일 호출 한도 확인**이 필요하다(R20 승인 항목에 등록).
- **조치**: ① 프로브/check 를 회전 유니버스 기준으로 교정(모델·전체 수치는 정보 줄로 병기) ② `status_after` 를 recurring='항상 pending'
  으로 고정 — 종전에는 check 미달 → `partial`, 크래시 → `failed` 가 되어 **경보가 뜬 그 순간 감시가 큐에서 사라졌다**
  ③ 구동기가 감시 항목의 rc≠0 을 매 틱 `✗ 실패 — 로그 확인 필요` 로 출력(실패가 조용해지지 않게).
- **검증**: `/usr/bin/python3 -m pytest` 해당 10개 파일 **82 passed** (신설 `tests/test_researcher_cycle_recurring.py` 6건 포함).

## [리서처 R20] 적재 순서로 소실되던 **당일 수급**을 수집기 가드에서 살렸다 (2026-09-29 22:1x, 읽기 전용 실측 + 회귀 7건)

- **결함**: `save_flows._drop_untraded_dates` 의 판정이 "그 날짜 행이 market_data 에 있는가"였다(상장 전 패딩 제거용).
  KIS 수급 크론(16:20)은 일봉 적재(18:55~)보다 먼저 돌므로 **당일 행이 전량 버려졌다** —
  실측 2026-09-29 16:20: 종목 로그 `as-of 2026-09-29` 인데 DB 9/29 행 **0건**(최신 9/28).
  같은 시각 프로브: 회전 유니버스 798종목 중 **792종목이 최신일 미보유**(최대 지연 1거래일).
  회수 경로는 있었지만 **지연**이었다(진행파일 키가 매일 리셋 → D+3~4 재수집). 즉 최신 횡단면의 수급은 D+3~4 였다.
- **수리(승인 불필요 — 크론·스키마 변경 없음, 수집기 코드만)**: 판정을 "그 날짜가 시장에 있는가"가 아니라
  **"시장 일봉의 마지막 적재일보다 뒤인가"** 로 바꿨다. 뒤면 **보존**(적재 순서), 이하면 종전대로 제외(상장 전·거래정지·**휴장**).
  실 DB 프로브: 휴장 9/24·25 와 거래정지 9/28 은 제외(= 없는 날 행을 만들지 않는다) · 상장 전 5/15 제외 · 미적재일은 저장.
- **효과**: 회전 로스터(하루 250종목)가 **D+0** 로 바뀐다(종전 D+3~4). 나머지는 회전 용량 문제라 그대로 —
  회전 확대는 KIS 일일 호출 한도 확인이 필요해 여전히 승인 항목(R20)이다.
- **모델 영향**: 최신일 수급 피처의 NaN 패턴이 바뀐다(당일 행이 존재). 학습 창에는 영향 없음(과거 행은 그대로).
- **검증**: `tests/test_supply_drop_visibility.py` **7 passed**(당일 보존·상장 전 제외·거래정지 제외·휴장 제외·캐시 1회 조회·누적 집계).

## [리서처 R12] 재무 비율 피처 19개 부활 (기간유형 구분 선행)  (2026-09-30 16:02)
- 결과: [저작생략] 산출물 이미 존재(scripts/build_financial_ratio_features.py) — 저작 생략 | R12: 0 <= 1 → 충족
- 판정: 충족
- 근거: feature_coverage: quality_* 9개·value_* 7개 + roe/per_current/pbr_current 가 nonzero_ratio=0. disclosures 백필로 rcept_dt 22,278행 확보(기간유형 구분 근거).
- 엔지니어 백로그: `XR12` (command·대조군 기입 필요)

## [리서처 R21] 수급 지연 상시 감시의 판정 기준이 '기준일 이동'에 흔들렸다 — 수리 (2026-09-30 21:1x, 회귀 7건)

- **증상**: 같은 DB 상태가 다른 판정을 받았다. 20:12 틱(시장 최신일 09-29) 최대 지연 **2 = 충족** →
  21:13 틱(수급 142,996행·최신 09-29 **그대로**, 저녁 파이프라인이 09-30 일봉을 적재해 시장 최신일만 09-30)
  최대 지연 **3 = 미달**. 수집 결함이 아니라 **시계 차이**다 — 수급 러너는 16:20 에 돌고 그 시각 KIS 가 준
  최신일까지만 담는다(실측 09-30 16:20 실행의 as-of = 09-29).
- **원인**: 판정 기준일을 '시장 최신일'로 잡았다. 그 값은 수집 주기와 무관하게(저녁 일봉 적재) 하루 앞으로
  밀리므로, 기준일이 기준을 삼켰다. 교훈은 기존 함정과 동형이다 — **'마땅히 있어야 하는데 없는' 것만 세라**.
- **수리**: 판정 = max(① 회전 유니버스가 *실제로 도달한* 최신 수급일 기준 상대 지연, ② 시장 최신일 − 수급 최신일
  (T+1 정상=1) + 1). ①은 회전 둔화를, ②는 수집 전면 정지를 잡는다 — ①의 기준도 수집이 도달한 곳이라
  멈춘 수집이 ①에서 보이지 않는 구멍을 ②가 덮는다. 수급 최신일 자체가 없으면 **fail-closed**(판정값 99).
- **검증**: 수정 후 실측 — 시장 09-30 / 수급 도달 09-29 · 상대 최대 2 · 정지 신호 2 → 판정 **2 = 충족**
  (20:12 값과 동일해졌다). `tests/test_r21_supply_lag_probe.py` **7 passed**(문턱 경계·기준일 이동 무감·
  당일 수집 무벌점·회전 둔화 발화·수집 정지 발화·수급 최신일 부재 fail-closed·anchor 상한 회귀 방지).
- **모델 영향**: 없음(수집·감시 판정만). 잘못된 '미달' 이 매 저녁 틱마다 원장에 남던 것이 사라진다.

## [리서처 R22] close 스크리너 signal_date 6일 고정 — 해소 (2026-09-30 21:2x, 소비자 프로브로 검증)

- **실측**: `data/feed/screener_latest.json`(generated_at 2026-09-30T20:30:04+09:00) close 20건·swing 20건
  `signal_date` **모두 2026-09-29**(경과 1일). 트레이더 프로브 `--probe feed_signal_lag` = **1.0 ≤ 4 → 충족**
  (종전 6.0 = 미달, 09-23 고정). 발행 로그 두 회차(14:40 close_job · 20:30 feed_publish) 모두 'signal_date 1~1일 전'.
- **의미**: `close_price` 는 주문 지정가로 그대로 쓰이므로 낡은 종가 = 낡은 지정가였다(소비자 후보별 신선도
  게이트가 close 전략을 통째로 차단). 이제 close 전략 경로가 열린다 — 다만 **swing 은 별개 사유로 여전히 차단**
  (평균 확률 0.550 < 문턱 0.58 = 엔지니어/사람 몫, 리서처 아님).
- **재발 감시**: 항목 check 에 소비자 프로브를 넣어(문턱 4 = `candidate_max_age_days.close`) 다음 사이클부터 자동 판정.

## [리서처 R24] [트레이더 환류] data_gap '당일 휴장' 오탐 — 거래일을 휴장 캘린더에 기록 (2026-10-01 실측)  (2026-10-01 15:41)
- 결과: R24: 0 <= 0 → 충족
- 판정: 충족
- 근거: 2026-10-01(거래일) 07:50 `cron_data_gap_report.log` = `휴장 기록: 2026-10-01 (KIS no_data)` → 캘린더 파일에 10-01 기록(07:50~08:53) → 트레이더 T9 등록 커밋(08:28)이 이 값을 '휴장일' 근거 ①로 인용 → **08:53:23 워킹트리에서 삭제(커밋 없음·작성자 미상)**. 실측 반증: KIS `CTCA0903R` `opnd_yn=Y`(개장일) · 005930 `acml_vol` 1,727,775(09:29)→1,854,805(09:30:45) = +127,030주/2분 · 캘린더 26항목에 10-01 없음.
- 엔지니어 백로그: `XR24` (command·대조군 기입 필요)
