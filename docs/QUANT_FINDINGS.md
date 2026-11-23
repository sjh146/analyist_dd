
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

## [리서처 R23] 크론 직행 러너 자기신고 — 배선 완료, '배선'과 '실제 행'을 구분한다  (2026-10-01 21:1x)

- **결과**: 배선 커밋 `0534c97` 이후 첫 감사 실측 `scripts/r23_claim_audit.py` = **있음 9개 경로 · 누락 0 · rc=0**
  (16:00 회차는 rc=2 · 7개 러너 미배선 — 배선이 그 뒤에 들어갔다).
- **실증(휴리스틱을 근거로 삼지 않음)**: `dq_runner_claim` 실측 — `krx_daily`(source=8037/claimed=8037/persisted=110) ·
  `krx_derivatives_collect`(1155/93/30) · `kis_short_selling_backfill`(16391/15947/281) ·
  `kis_program_trading_collect`(12/12/2) · `macro_backfill`(662/662/10) · `kis_supply_backfill`(7500/7491/639) ·
  `dart_disclosure_backfill`(307/297/36) — **7개 중 6개는 크론 실행에서 실제 행까지 확인**.
  `kis_app.main:daily` 는 `--job daily --date 20260925 --limit 3` 멱등 재실행으로
  `[claim] kis_app.main:daily -> market_data source=0 claimed=0 persisted=0` 실증(휴장일 → 원시행 0을 세지 않음, 거짓 parse_failure 없음).
- **남은 관측 공백 2개**: `kis_app.main:minute`(마지막 크론 09-30 23:00 · 배선 이전) ·
  `data_gap_backfill`(마지막 크론 10-01 06:10 · 배선 이전) → 다음 크론(23:00 / 04:15)에서 자동 관측된다.
  휴리스틱이 0 을 돌려주는 것과 **행이 남는 것**은 다른 명제이므로, 후속 항목 **R27** 을 신설해
  `dq_runner_claim` 의 14일 내 행 존재로 판정한다(현재 실측 **2** = 위 두 러너).
- **모델 영향**: 없음(수집 감시). 다만 이 2개 러너의 적재량은 검증되지 않은 상태다.

## [리서처 R25] DART 공시 정기 러너 — 승인 요청 해소(스케줄 실재·오늘 실행)  (2026-10-01 21:1x)

- **결과**: 승인 요청(needs)을 닫고 **상시 감시 항목으로 전환**(recurring, check = 평일 공시 접수일 7일 이내 / 주말 통과).
- **근거 ①스케줄 실재**: `/etc/cron.d/analyist_dd` L33-37 — `# --- 2026-10-01 추가 (R25): DART 공시 인덱스(disclosures) 백필 ---`
  + 평일 18:10 `dart_disclosures.sh`(B/D/E/I) + 토 18:10 `--regular`(분기형 A). 래퍼 mtime 10-01 17:09 · 크론 mtime 17:10.
- **근거 ②오늘 실행 실측**: `reports/cron_dart_disclosures.log` 18:10:03~18:10:17 — 콜 5 / 소스 307행 / 적재시도 297 /
  실제 삽입 36 / 테이블 델타 36(214,457→214,493).
- **근거 ③데이터**: `disclosures` 214,493행 · `max(rcept_dt)=2026-10-01`(종전 정지일 09-23 → 공백 해소).
- **판정 check 실측**: `SELECT CASE WHEN isodow>=6 THEN 0 WHEN max(rcept_dt) >= CURRENT_DATE-7 THEN 0 ELSE 1 END` = **0**.
- **모델 영향**: 있음(재무 피처 기간유형·공시 지연) — `XR1` 로 이미 인계된 경로가 이제 매일 신선하게 공급된다.

## [리서처 R14] feature_coverage 신선도 — 진척 정지(원인은 엔지니어 소유 캐시)  (2026-10-01 21:1x)

- **실측**: `feature_coverage` 199행 · `min(computed_at)=2026-09-24 13:48Z` · `max=2026-09-30 07:01Z` →
  배치별 행수 **09-24: 128 / 09-25: 17 / 09-28: 16 / 09-29: 19 / 09-30: 19**. check = **0(미달)**.
- **원인(실측)**: 128행은 패널 경유(`window_days` 120이 102행 + 20이 26행)이고, 그 소스는
  `app/models/exp_panel/panel.pkl` **mtime 2026-09-23 22:02**(= 엔지니어 소유, 8일 경과). 나머지 71행은
  R9·R10·R11·R12 빌더가 자기 coverage 를 갱신해 신선하다 — 즉 **계열별로 갈라졌다**.
- **하지 않은 것**: 캐시가 낡은 상태로 `feature_coverage_report.py` 를 재실행하면 **같은 값에 새 타임스탬프**만 붙어
  check 가 cosmetically 통과한다(‘테이블을 덮어써야 통과하는 check’ 함정). 그래서 재실행하지 않았다.
- **해소 경로(엔지니어 소유)**: exp_panel 캐시를 현재 DB 로 재빌드 → 리서처가 같은 분모로 1회 실행.
  `XR14` 항목은 아직 엔지니어 백로그에 없다(R14 가 partial 이라 구동기의 자동 인계를 타지 못했다).

## [리서처 R25] DART 공시 정기 러너 — 스케줄 확인 완료(10-01), 신선도 상시 감시  (2026-10-02 00:01)
- 결과: R25: 0 <= 0 → 충족
- 판정: 충족
- 근거: 실측 2026-10-01 16:1x: disclosures max(rcept_dt)=2026-09-23 · 212,861행에서 정지(09-24~10-01 결손; 09-23 333행/일 → 이후 0행). 원인: 백필 스크립트를 부르는 항목이 /etc/cron.d/analyist_dd · jhshi crontab · Hermes 크론 어디에도 없다(수동 실행만). 네트워크는 원인이 아님 — DART list.json 도달 200(direct·proxy 동일)·DART_API_KEY 설정됨. 서비스 내 DartCollector(services/news-analyzer/app/collectors/dart_collector.py)는 이를 import 하는 코드가 레포에 0곳 = 미배선 죽은 코드. 조치(2026-10-01 이 세션): 백필 2회 실행 → 타입 A 110행 + B/D/E/I 1,413행 삽입, 테이블 212,861→214,384, max(rcept_dt)=2026-10-01 확인.
- 엔지니어 백로그: `XR25` (command·대조군 기입 필요)

## [모델엔지니어 CG63] 재무 as-of 누수 전수 수리 — 팩터·품질 점수 경로가 date 를 받지 않았다  (2026-10-02 07:1x)

- **발견(누수 게이트 ②·③ 동시 위반)**: `factor_features.FactorFeatures.get_all_factors` 와
  `scorer.QualityScorer.get_f_score` 는 **date 인자가 아예 없어** `financial_statements` 를
  `ORDER BY report_date DESC LIMIT n` 으로 읽었다 → 과거 행에도 빌드 시점 최신 보고서가 들어간다
  (최대 10개월 룩어헤드). 2026-09-25 의 `patch_panel_asof.py` 는 **company_features 컬럼만** 교체했고,
  팩터·품질 컬럼은 패치 밖이었다.
- **실측(신규 `scripts/_panel_leak_probe.py`)**: 종목당 유니크값(중앙)이 **1 = 종목 상수**인 컬럼 —
  `value_per` `value_pbr` `value_psr` `value_pcr` `value_ncav` `quality_roa` `quality_f_score`
  `quality_asset_growth` `quality_score`. panel_420_asofpatch · panel_150u · panel_995 전부 동일.
  같은 패널에서 company_features 컬럼은 3~5(패치됨) → 대비가 명확하다.
- **패널 스크린과의 연결**: 최고 단일피처 AUC 3개가 정확히 이 컬럼들(quality_roa 0.5534 ·
  quality_score 0.5507 · value_per 0.5454)이고, 선별(edge top30)이 누수 컬럼을 학습에 넣었다.
- **e2e 실측(`scripts/_asof_pipeline_smoke.py`, 종목 000020 = 보고서 4건 2023-12~2026-06)**:
  수리 전에는 2023-01-02 행에도 2026-06-30 보고서 값이 들어갔다. 수리 후 같은 행은
  value_per 10.7863 → 0.0 · value_psr 1.0179 → 0.0 · quality_roa 0.9884 → 0.0 ·
  quality_score 0.75 → 0.5 (그 시점엔 어떤 보고서도 알 수 없음). `date=None` 경로는 10/10 컬럼이
  최신일과 동일 → **추론 경로 무영향**.
- **수리**: `asof_report_predicate()`(연간 90일·그 밖 45일 — company_features 와 같은 규칙) 신설 +
  5개 getter 로 date 전파 + 시총을 `cap_now × close(date)/close_now` 로 환산 + scorer 에 date 추가 +
  `feature_pipeline` 이 두 호출에 date 전달. `patch_panel_asof.py --also-factors` 로 기존 패널의
  팩터·품질 컬럼만 재계산(스모크: value_psr/value_ev_ebit 유니크 1→8, quality_score 1→2).
- **검증**: `_asof_financials_test.py` **17/17 PASS**(SQL 계약 · 지연 산술 · 라이브 단조성 ·
  '최초 보고서 시점엔 아무 행도 안 나옴' · 시총 환산) · `_asof_pipeline_smoke.py` OK ·
  기존 회귀 30종 PASS. 커밋 ef0a3d0 이후.
- **모델 영향**: **큼** — 지금까지의 모든 AUC(등록 기준선 0.5406 · 최고 arm 0.5727 · CG1~CG62)는
  누수 컬럼이 선별에 들어간 패널 위의 값이다. 재측정은 `CG64`(수리 패널 3-arm) → 이동폭이 +0.02 이상이면
  **기준선 재등록(사람 승인)** 이 필요하다. 남은 한계: quality_score 의 market_cap 항목 1/8 은 현재값 근사 유지.

## [모델엔지니어 CG64] as-of 수리 후 기준선 재측정 — 누수 제거는 결론을 바꾸지 않았다  (2026-10-02 07:16)

- **측정**: 수리 패널(panel_420_asof2, 재무·팩터·품질 컬럼 as-of 재계산, 13,609행) · 5폴드×5시드 · 같은 런 · 5.0분.
  - 게이트 OFF 기준 `LS_quant_q30_h5` **0.5374 ± 0.0305** [0.5239, 0.4968, 0.5623, 0.5819, 0.5221]
  - 게이트 ON 대조군 `CO_core30_h5` **0.5350 ± 0.0189** [0.5244, 0.5032, 0.5544, 0.5493, 0.5440]
  - 스무딩+depth1(배포 가능 조합) `CO_smooth_d1_h5` **0.5512 ± 0.0218** [0.5691, 0.5331, 0.5450, 0.5833, 0.5256]
- **판정**: 짝 Δ(arm − 대조군) = **+0.0162** (폴드별 +0.0447/+0.0299/−0.0094/+0.0340/−0.0184 → 3/5 양(+)) →
  사전문턱 +0.02 **미달 = 노이즈**.
- **수리 전과 대조**(같은 config, panel_420_asofpatch · CG26/CG27): 대조군 0.5365±0.0188 · arm Δ+0.0144 →
  이동폭 **대조군 −0.0015 · arm Δ +0.0018** (둘 다 잡음 이하).
- **의미**: 재무 as-of 누수(CG63)는 **측정 정합성 결함**이었지만 AUC 를 부풀리지 않았다 →
  '누수 컬럼이 top30 선별을 지배해 성능이 과대평가됐다'는 가설은 기각. 기준선(0.5406) 재등록 요청은 철회하고
  기존 결론(배포 가능 축에 +0.02 레버 없음)을 유지한다. 다만 패널 재현성·as-of 정합은 회복됐다
  (같은 (종목·날짜) 값이 빌드 시점에 따라 달라지지 않는다).
- **모델 영향**: 지금 배포된 챔피언의 학습 피처가 다음 재학습부터 시점정합 값을 갖는다(과거 행 값이 달라짐) →
  재학습 후 짝 A/B(CG58/CG61 방식)로 배포 모델 비교를 다시 해야 한다.

## [트레이더 장마감] 2026-10-02 진입 0건 — close 는 과열 상한, swing 은 후보 0 (브로커 실측)  (2026-10-02 15:20)

- **체결·성과(브로커 기준)**: 오늘 체결 **0건** · 청산 **0건** · 실현 **0원**.
  브리지 `/balance` 계좌 783247576 equity=cash=**526,243원** · positions 0 · working orders 0
  (CpTd6032 '오늘 매도' 0건 — 저널 청산 0건과 일치, status_snapshot 대조 불일치 0).
  실계좌 전환(09-28) 이후 누적 실현은 3건 **-1,446원**(승률 33.3%, fees=0 → net 미확정, T1).
- **왜 안 샀나(게이트 순서대로, 마지막 사이클 15:13 기준)**:
  ① **close = HEAT 상한 발화** — 발행 리스트 20건의 **상위10 평균 일변동 +17.05%** > 상한 +15.0%
  (`r1_max_avg_pct`) → 스크리너 전체 차단(`HEAT block close`), PRE filter 20→0
  (전량 blocked · 창 탈락 0 · 가격 탈락 0 = 창은 열려 있었고 차단만이 원인).
  ② **swing = 후보 0건** — 유니버스 300종목 최대 confidence **0.4733**(09-30 유니버스 0.6558 →
  하루 만에 분포가 통째로 내려감, batch_type=raw_fallback · auc 0.5548) → 발행측 문턱 0.5 미달.
  즉 오늘은 '게이트가 막았다'가 아니라 **모델이 살 후보를 내지 않았고, 낸 close 리스트는 이미 상단**이었다.
- **신호 시점 주의(해석용)**: 피드의 `day_change_pct` 는 **신호일(10-01)의 등락**이다 —
  263600 실측(DB 10-01 종가 4,410 vs 09-30 3,395 = +29.9%) 이 피드값 29.9 와 일치.
  따라서 상한은 '선정 시점에 이미 얼마나 올랐는가'를 재며, 당일 장중 등락이 아니다.
- **피드 계약**: 위반 0. close 20건 `close_price` 누락 0 · signal_date 2026-10-01 · score_kind=screener
  (min 85.9 / median 85.9 / max 90.0). **T8 프로브**(지정가 vs 최신 종가 편차, 판정 ≤1%):
  13:26 틱 **20.86% (failed)** → 15:16 내 재측정 **0.00%** — 그 사이 14:40 close_job 재발행이 있었다.
  발행물/DB 중 어느 쪽이 움직였는지는 **미규명(열린 항목)**.
- **리서처 환류**: ① **R26**(needs_approval, 'close 스크리너가 상한가 종목을 후보에 넣는다')의 소비자측
  실증 — 리스트 상위10 평균이 트레이더 상한을 넘어 **전략이 하루 통째로 닫혔다**(차단 20/20, 진입 0).
  ② **MT116**(swing 챔피언 교체 10-01 21:26 후 raw_fallback 퇴화) 실증 — 오늘 swing 유니버스 중
  confidence ≥0.5 가 **0건**. 두 항목 다 기존 등록건이라 새 항목을 만들지 않고 증거만 붙인다.
- **트레이더측 수리(자율 · 커밋 15adbf6 → origin/main)**: 진입 0건의 **사유가 runner.log 에 남지 않던**
  결함 수리 — `report.reasons` 는 프로세스 종료 시에만 출력돼 장중 로그가 "monitor: no open positions"
  만 찍혔고(사유는 매 사이클 덮어써지는 loop_state.json 에만 존재), 그날 왜 안 샀는지가 아무 데도
  남지 않았다. `_log_cycle_reasons`(사유 집합 변경 시 1회 · 날짜 바뀌면 재출력) + 회귀 2건,
  전체 459 passed.
- **운영 사실**: WSL 호스트가 오늘 11:13 재부팅 → 브리지 11:19:47 · 루프 11:20:21 기동(감독이 연결
  확인 후 자동 시작). 개장 08:30~11:20 약 3시간은 루프가 없었다(보유 0이라 노출은 없음). 사유는
  Creon 업데이터가 파일 점검 중이었고 그 뒤 사람이 로그인(브리지 감독 로그 실측).

## 2026-10-02 — as-of 수리(CG63)가 드러낸 '값 피처' 소스 공백과 그 수리 (CG65/CG66)

- **CG65(수리 패널 스크린)**: panel_420_asof2(13,609행×210피처·49종목·281일) → 누수 0 · 종목상수
  **30**(수리 전 48) · 시장레벨 44(41) · 무정보 **127/210**(불변). 최고피처가 종목상수 재무에서
  시간가변 변동성군(atr_pct 0.4417·rank_volatility_20d 0.4470)으로 바뀌었다 → 수리는 구조를 바꿨지만
  **정보량은 그대로**(무정보 60%).
- **후속 진단(전수 비영률 대조)**: 수리로 죽은 컬럼 = value_pbr 0.960→0.094 · quality_roe 0.920→0.090 ·
  value_per 0.698→0.069 · quality_earnings_volatility 0.981→0.512. 그리고
  value_per≡per_current · value_pbr≡pbr_current · quality_roe≡roe 가 **비트 동일**(np.array_equal)
  = 수리 후 중복 피처.
- **원인(DB 실측)**: `financial_statements.per/pbr/roe` 컬럼이 **2026 보고서 행에만** 값이 있다
  (per 2023 0건·2024 0건·2025 0건·2026 1,633/2,588 · pbr 2,509 · roe 2,332). as-of 정직 조회에서는
  과거 구간이 전부 NULL → 0. 즉 수리 전 커버리지 0.70~0.96 은 **룩어헤드**였고, '종목상수 재무가
  최고 AUC' 는 누수 지문이었다(CG63 진단 확증).
- **수리(CG66)**: `factor_features._value_factors/_quality_factors` 에 **저장값이 0 일 때만**
  as-of 시총 ÷ as-of 재무(ni_ttm·total_equity)로 계산해 채우는 대체를 추가(저장값 우선 →
  date=None 추론 경로 비트 동일 실측). `patch_panel_asof --also-factors` 로 panel_420_asof3 생성.
  검증 `_asof_value_fallback_test.py` 10/10 PASS · `_asof_financials_test.py` 17/17 PASS ·
  `_asof_pipeline_smoke.py` OK(date=None 10/10 동일).
- **결과**: 커버리지 회복(value_per 0.767·value_pbr 0.960·quality_roe 0.958)·중복 해소.
  그러나 모델은 **Δ0.0000**(CO_smooth_d1_h5 0.5512±0.0218, CO_core30_h5 0.5350±0.0189 —
  CG64 와 동일, LS_quant 만 +0.0007) → 사전문턱 미달 **노이즈**. 회복 피처가 top30 선별에
  실질 영향이 없다 = 현 피처풀 포화 진단 재확인.
- **남은 레버**: 값 피처의 역사를 채우려면 원천 백필이 필요하다. `financial_ratio_features`
  (PIT·rcept_dt, 2025-06-16~2026-09-29, 패널 49종목 중 40종목)가 대안 소스로 존재하나
  구간이 패널 창보다 짧다 → 리서처 핸드오프(XR12 재무비율 부활·XR25 DART 러너)와 함께 다뤄야 한다.

## [트레이더 환류 holiday-selfheal-removes-real-holiday] [트레이더 환류] 휴장일이 캘린더에서 삭제된다 — data_gap 자가치유가 tr_day_yn 오독으로 진짜 휴장(2026-10-09 한글날) 제거  (2026-10-09 08:24)
- 근거: 실측(2026-10-09 04:15 cron_data_gap_backfill.log:26): '휴장 캘린더 정정: 2026-10-09 제거 (KIS 국내휴장일조회 = 거래일)'. 즉 한글날(금, KRX 휴장)이 캘린더에서 삭제됐다. 뿌리: scripts/data_gap.py kis_is_trading_day() 가 'opnd_yn=="Y" or tr_day_yn=="Y"' 로 판정하는데, KIS CTCA0903R 의 tr_day_yn 은 휴장·주말에도 'Y'(실측: 10-03토/10-04일/10-05대체공휴일/10-09한글날/10-10토/10-11일/09-25추석 전부 tr_day_yn=Y, 반면 이 날들 opnd_yn=N·bzdy_yn=N). 그래서 find_gaps() 의 자가치유 블록('if today_s in holidays and kis_is_trading_day(today_s): holidays.discard(today_s)')이 R24 를 고치려고 넣은 것인데, 진짜 휴장일을 매번 거래일로 오독해 **휴장 당일 아침에 캘린더에서 지운다**. 검증(직접 KIS 호출): 20261009 opnd_yn=N tr_day_yn=Y bzdy_yn=N / 20261008 거래일 opnd_yn=Y tr_day_yn=Y bzdy_yn=Y / 20261012 거래일 opnd_yn=Y bzdy_yn=Y. 정정안: 판정을 opnd_yn=='Y'(또는 bzdy_yn=='Y') 단독으로. 현재 worktree 의 data/krx_holidays.json 은 HEAD 대비 2026-10-09 가 빠져 있다(git diff). 트레이더 영향: feed_export 휴장 가드가 오늘(휴장일)을 거래일로 보아 피드를 발행했고, trader_core/profiles.py:185 는 'exchange holidays are not'(주말만 skip) 이라 루프를 켜면 휴장일에 주문을 시도한다.
- 리서처 백로그: `T33`

## [트레이더 환류 swing-limit-reference-price-stale] swing 피드의 close_price(전일 종가)가 주문 지정가로 쓰여 갭업일에 체결 불가 주문이 된다  (2026-11-10 15:15)
- 근거: 2026-11-10: swing 후보 240810 의 close_price=66,100(직전 세션 11-09 종가)이 그대로 지정가가 됐고, 당일 시가 71,100·저가 66,700 이라 체결 0. 같은 회차 swing 20건 중 19건은 R3 드리프트 밴드(3%)에서 탈락, 통과 1건(319400)은 슬롯에서 거부 → 피드 유래 체결 0건. 발행측에 '주문 참조가격이 언제 것인지'(signal_date) 와 함께 당일 기준가(base) 또는 갱신 시각을 실어 주면 소비자가 지정가를 전일 종가가 아니라 당일 기준가 근처로 잡을 수 있다. 리서처 확인 요청: (1) close_price 를 전일 종가로 보내는 것이 계약상 의도인가 (2) swing 신호의 유효 지평(h5) 안에서 지정가 기준가를 어떻게 정할 것인가 — 데이터로 답이 나오는 항목.
- 리서처 백로그: `T39`

## [리서처 R33] 4주 호스트 오프라인(10-11~11-09) 복구 — 수급 공백 60일 백필 (일봉은 yfinance 스윕이 선행)  (2026-11-10 17:35)
- 결과: R33: 939 >= 600 → 충족
- 판정: 충족
- 근거: 실측 2026-11-09 17:5x: market_data 최신 10-08(3,123,894행), foreign_institutional 최신 10-08(148,344행), minute_bars 최신 10-08. dq_runner_claim 마지막 run_at 10-10 19:13Z. dq_snapshot 18:00 → breach 3(신선도 20거래일·착시 62·결측 30). 수급 저장 가드가 '그 날짜의 market_data 행이 있어야 저장'이므로 **일봉 복구 후에** 돌려야 한다(선행 조건을 command 가 검사, 미충족 시 rc=3).
- 엔지니어 백로그: `XR33` (command·대조군 기입 필요)

## [리서처 R35] 일봉 공백 복구 러너 재개 + 상시 감시 (2026-10-12~11-06)  (2026-11-10 21:11)
- 결과: R35: 3840 >= 3600 → 충족
- 판정: 충족
- 근거: 실측 2026-11-09 18:16: progress 104/4,349 · net_guard kis 182콜(3.0s 간격, 차단 0) · 11-06 2,706종목(10-08 3,837 대비 70%) · 실패 0. 18:13 백필 프로세스 pid 교체 관측(재기동 loop).
- 엔지니어 백로그: `XR35` (command·대조군 기입 필요)

## [리서처 R37] 당일 일봉 적재 이중 실패(18:55 KRX/KIS + 20:00 yfinance) — 재시도 경로 없음, 신선도 사각지대 수리  (2026-11-11 21:20)
- 결과: 2026-11-11 `market_data` 당일 0행(최신 11-10 3,726종목) → 21:14 naver 1콜/종목 백필로 복구 진행.
- 판정: **수집 실패(일시 DNS) + 구조 결함 2건(재시도 부재·신선도 사각지대)**. 데이터측 수리·복구 완료, 스케줄 승인 대기.
- 근거: 18:55 KRX `Temporary failure in name resolution`(exit=3) · KIS 폴백 `Could not resolve host` + 30s×5 → 중단. 20:00 phase 1-1 로그 251줄(2,510/4,349)에서 절단, `yfinance DONE`·`[claim]` 부재 = timeout 3,600s. 21:13 네트워크 정상(200/0.32s). 신선도는 0(정상) — 당일을 항상 분모에서 뺐기 때문(수리 `c0bd9c2`, 회귀 18 passed). 복구 로그 `data/reports/naver_backfill_20261111.log`.
- 엔지니어 백로그: 없음(데이터·스케줄 소관). 승인: `docs/QUANT_RESEARCH_BACKLOG.json` `R37`.

## [리서처 R17] SNS/kalman 26피처 결측 0.937 은 '결함'이 아니라 원천 커버리지 구조다 (측정)  (2026-11-11 21:2x)
- 결과: `sns_post_features` distinct 309종목(전체 4,349의 7.1%) · 최신 거래일 2026-09-30(42일 정체) · 최근일 246종목. kalman_* 4개 동일(null 0.937).
- 판정: 수집 확대로 0.93 은 개선되지 않는다 — 모델 피처로는 ① 커버 종목 한정 ② 서빙 제외(CG164/CG183 `--drop-serving-blind-features`) 중 선택이 필요하다.
- 근거: `feature_coverage` 21:2x 실측(SNS 22개 null 0.930~0.937, kalman 4개 0.937) + `sns_post_features` trade_date 별 종목수(246/231/198…) + `sns_posts` 최신 2026-09-30 12:35Z.
- 엔지니어 백로그: 없음(항목 등록은 리뷰보드 판단 — 피처 계약 변경은 승인 사안).

## [리서처 R38] 커버리지 착시 피처 수 62 는 데이터 악화가 아니라 '창 정의 혼합'이다  (2026-11-12 02:0x)
- 결과: `dq_feature_coverage_illusion_count` 41 → **62** (warn 45 / breach 50), `dq_feature_null_ratio_high_count` 30 → 35 (warn 32). 증가분 +21/+5 는 2026-11-12 00:14:39·00:15:08 KST 에 **35행이 window_days=513 으로 재계산**된 것과 정확히 일치한다.
- 창별 분해(실 DB): 120일 131행 → 착시 26 · null90 26 / 511일 19행 → 15 · 4 / 512일 17행 → 0 · 0 / 513일 35행 → 21 · 5. 합계 62 · 35.
- 판정: **측정 인구 문제**(모델 데이터는 그대로). 북극성 불변 — 살아있는 횡단면 피처 166 · feature_count 202 · dead 11. 같은 진동 전례: 10-11 06:01 26→62(CG165 3빌더 479일 창) → 11-09 22:00 41 복귀.
- 근거: `feature_coverage` computed_at 3군(11-09 21:16 120일 131행 · 11-09 21:46 511일 19행 · 11-11 19:10 512일 17행 · 11-12 00:14~15 513일 35행). 513일 창 35행의 착시 상위 = value_ncav 1.000 · value_pfcr 0.951 · per_current/value_per 0.909 · value_ev_ebit 0.901.
- 엔지니어 백로그: 없음(빌더 창 정의·알림 인구 변경은 리뷰보드 승인). 승인 항목: `R38` (A: 빌더가 120거래일 창만 기록 [권고] / B: 창별 메트릭 분리 / C: 문턱 62 위로).

## [엔지니어 CG210] 승격 돈 게이트의 순기대 표본이 학습구간과 겹친다 — 후보 양(+) 헤드라인의 93%가 in-sample  (2026-11-12 22:35)
- 결과: 같은 표본(close_gate_probe trades.csv 2,251행·113세션·2026-05-26~11-09·top3·익일시가·수수료 왕복 0.21%p·당일등락<25%)을 모델 학습경계로 자름.
  · **챔피언**(173피처, 경계 2026-10-01): 전체 −0.3192%p(t −1.70) · in-sample 88세션 −0.1612%p(t −0.74) · **OOS 25세션 −0.8754%p(t −2.51)**, OOS 양세션 28.0%, 앞/뒤 절반 −0.8913/−0.8608
  · **champion_cand**(199피처, 경계 2026-10-28 fit_end): 전체 +0.0114%p(t 0.06·중앙 −0.2908) · in-sample 105세션 +0.0447%p(t 0.24) · **OOS 8세션 −0.4259%p(t −0.66)**, OOS 양세션 12.5%
- 판정: **측정 완료 — 승격 근거 부정**. ① 후보의 양(+) 헤드라인은 표본의 93%(105/113)가 학습구간이라 나온 값이고 OOS 는 음수 ② 배포 챔피언은 미학습 구간에서 유의하게 돈을 잃는다(t −2.51) ③ 같은 프로토콜 값이 87세션 −0.182%p → 113세션 −0.3192%p 로 악화 — 표본이 늘어난 방향(최근 구간 포함)으로 갈수록 나빠진다.
- 근거 파일: `reports/overnight/expectancy_is_oos_split.json` · 로그 `data/reports/me_cycle/logs/cg209_expectancy_split_20261112c.log` · 백로그 `CG210`.
- 한계: 표본은 여전히 '게이트가 막은 후보 풀'의 가정 수익이다(CG191 의 gate_open 0/113). 즉 이 숫자는 실체결 손익이 아니라 **모델 순위가 그 풀에서 만든 가정 성과**다. 표본 정의 변경은 리뷰보드 승인 대상.

## [리서처 R41] 시장패턴 원장(19:40)이 일봉 적재 완료 전에 '그날 시장'을 기록한다 — 재실행으로 정정하되, 완료 전 기록을 검출하는 검사를 상시 감시  (2026-11-13 22:01)
- 결과: R41: 0 <= 0 → 충족
- 판정: 충족
- 근거: 2026-11-13 21:4x 실측: (a) 18:55 KRX 중단 사유 = `[Errno -3] Temporary failure in name resolution`(11-09·11-11·11-13 3/5 거래일 발생, 11-10·11-12 정상 — 같은 시각대의 이름해석 간헐 장애). KIS 폴백은 18:56:07 시작해 21:12 에도 2591/3979 진행 중 → 19:40 크론이 부분 시장을 읽었다. (b) 원장 실측 왜곡: 11-13 기록= scored 819 · trade_value 12.25조 · chg_mean 0.072% · chg_p90 2.433 vs **재실행 후** scored 3805 · trade_value 29.32조 · chg_mean 0.401% · chg_p90 3.35 → 거래대금이 2.4배 작게, 평균등락이 5.6배 작게 기록돼 있었다(전일 11-12: 3828 / 22.53조 / 1.803%). (c) 과거에도 같은 형태 3회(scored 161·1134·819). (d) 검사 스크립트 실행: 수리 전 `ratio=0.220 → 공백 1건(rc=1)`, 재기록 후 `ratio=1.022 → 충족(rc=0)`.
- 엔지니어 백로그: `XR41` (command·대조군 기입 필요)

## [리서처 R38] 착시 62→26 은 '개선'이 아니라 창 인구가 우연히 120으로 통일된 것 (2026-11-14 16:0x)
- 결과(실 DB read-only): `feature_coverage` **202행 전부 window_days=120**, 단일 `computed_at` 2026-11-13 21:11:40 KST. 그 결과 `dq_feature_coverage_illusion_count` 62→**26**, `dq_feature_null_ratio_high_count` 35→**26**, 살아 190(횡단면 165)/죽은 12 (11-12: 164/35).
- 판정: 값이 좋아진 게 아니라 **모집단이 바뀐 것**이다. writer 는 여전히 `build_supply_market_features.py:452` 에서 window_days = 격자 스팬(최대−최소 trade_date)로 기록하므로, 야간 파이프라인이 장창 격자를 만드는 날 62 로 되돌아간다. 북극성(살아 190)도 같은 이유로 11-12 값과 직접 비교 불가.
- 근거: 위 SQL(window_days 분포·computed_at 단일값) + DQ 스냅샷 16:00:21 (breach 0 / warn 0, illusion 26). 26 은 R38 A안(모델 창 120 고정) 적용 시의 예측값과 정확히 일치.
- 엔지니어 백로그: 없음(빌더 창 정의 = 리뷰보드 승인). 승인 항목 `R38` 에 근거 추가.

## [리서처 R41] 원장 커버리지 검사가 '없는 세션'과 '거래대금 0'을 통과시켰다 — 판정 창을 30세션으로 넓히고 21세션 누락을 백필했다  (2026-11-16 00:45)
- 결과(실 DB read-only + 원장): `market_pattern_ledger.jsonl` **22세션** vs DB 마감봉 보유 **25세션** → 누락 21건(10-12~11-06 20세션 + 11-12). DB 에는 그 세션들이 정상 존재(마감봉 3,719~3,730개/일). 11-10 행은 `scored 3726`(=DB 거래량>0 봉수, 정상)인데 `trade_value_sum = 0` — DB 실값 20.735조.
- 원인: 원장은 `--today`(하루 1건)만 배선돼 있어 호스트 4주 오프라인 중 지나간 세션을 아무도 채우지 않는다. 11-10 의 거래대금 0 은 `trading_value` 적재 전에 기록된 흔적(같은 항목의 원 결함)인데, 종전 검사는 **마지막 레코드의 scored 만** DB 봉수와 비교해 둘 다 볼 수 없었다 = 실패할 수 없는 check.
- 수리(자율): ① `market_pattern_ledger.py --backfill 60` 멱등 재실행 → 원장 60세션(08-18~11-13), 거래대금 0/null 세션 **0건**. ② `scripts/market_pattern_coverage_check.py` 를 창 판정으로 확장 — 창 30세션에 대해 누락 · 부분기록(scored < DB×0.9) · 거래대금 결손(원장 0/null 또는 DB×0.9 미만)을 각각 센다. 회귀 13건.
- 검증(고치면 통과): 수리 전 원장 사본 → `창 30세션 / 원장 22세션 → 미달 (거래대금 1 · 누락 21) | 공백 22건`(rc=1) / 수리 후 실 원장 → `창 30세션 / 원장 60세션 → 충족 | 공백 0건`(rc=0, 0.9초).
- 판정: **데이터 결함 검출 완료**. 다만 이건 시장패턴 원장 한 곳이고, 같은 '마지막 1건만 보는' 형태가 다른 감시 check 에 남아 있는지는 미확인(별도 조사 항목).
- 한계: 원장 파일이 git 추적 상태라 다른 역할의 `git checkout` 한 번이면 백필분이 되돌아간다 — 런타임 산출물화는 소유자 확인 후(승인 아님, 조율 사항). 순기대 영향은 측정하지 않았다(이 원장은 피처 입력이 아니라 관측 기록).

## [리서처 R40] 수급 공백의 원인은 시각이 아니라 '크론 소실'이었다 — root crontab 부재로 11-14 이후 KIS 수급 0회 실행 (2026-11-17 00:5x)
- 결과(실측): `foreign_institutional` 날짜별 행수 11-10 **814** · 11-11 **728** · 11-12 **559** · 11-13 **13** · 11-16 **0**. 종목별 자기 시세 기준 최신일 지연 = 최대 **34거래일**, 3거래일↑ **584/1,143종목**. 같은 시각 DB 최신 일봉은 11-16(3,718행).
- 원인(실측 3종): ① `/var/spool/cron/crontabs/` 에 `jhshi` 만 존재, `crontab -l`(root) = 'no crontab for root' ② root 소유 로그 마지막 쓰기 = `cron_kis_supply.log` 11-13 16:47 / `cron_kis_daily.log` 11-13 22:25 / `cron_data_gap_backfill.log` 11-14 04:15 ③ 그 뒤 수급 신규 적재가 회전 규모(250종목/일)에 못 미침. → **일봉은 살아 있고(yfinance 컨테이너 자체 스윕 4,350종목) KIS 수급 경로만 죽었다** — 신선도·지연 지표는 '수집이 도는' 전제라 이 사실을 말해주지 못했다.
- 수리(자율 = '누락 스케줄 배선'): 사용자 crontab 에 `30 19 * * 1-5 kis_supply.sh` 추가(R40 이 권고한 19:30 — 18:56 일봉 완료 후·20:00 파이프라인 전). 실경로 검증: 같은 러너 `--top 5 --days 5` 즉시 실행 → as-of **2026-11-16** · 신규 **10행** · rc=0.
- 감시 추가: `scripts/supply_schedule_guard.py` (결함수 = 스케줄 부재 + cron 데몬 미실행, 회귀 `tests/test_supply_schedule_guard.py` 6건) 로 R40 check 교체 — "지연이 늘었다"가 아니라 "스케줄이 없다"를 직접 판정한다.
- 한계: root crontab 이 사라진 원인은 미규명(11-14~11-16 사이). 그 파일에 있던 **다른 root 잡들(18:55 KIS 일봉 폴백·20:00 evening pipeline·04:15 data_gap 백필·08:30 swing pipeline·03:30 news cleanup)** 도 함께 멈춘 상태로 추정 — 일봉·피드 발행은 Hermes 크론/컨테이너 경로가 대체하고 있으나, 복구 범위 결정은 사람 승인 항목으로 올린다.

## [리서처 R38] 착시 63 은 4번째 재발 — 창 변종이 42일로 늘었고 '정의 문제'가 확정됐다 (2026-11-18 04:48)
- 결과(실 DB read-only, 창별 분해): `feature_coverage` 202행 = 창120 **131행**(착시 26 · 결측90%↑ 26 · computed_at 11-13 12:11) + 창519 **52행**(착시 21 · null90 0 · 11-17 18:13) + 창42 **19행**(착시 16 · null90 2 · 11-17 11:15). 합계 = 스냅샷 `dq_feature_coverage_illusion_count` **63**(문턱 50 위반) · `non_model_window_rows` **71** · `window_variants_count` **3** — 메트릭과 SQL 분해가 정확히 일치.
- 판정: 데이터가 나빠진 게 아니라 **모집단 정의**가 판정을 흔든다. 모델 창(120일)만 세면 착시 **26 < 50**(통과)이고 이 값은 11-12·11-13·11-14 와 동일하다. 11-13 21:11 전량 재계산이 202행을 120일로 통일했던 상태는 5일 만에 야간 빌드 2회로 되돌아갔다.
- 신규 사실: 창 변종이 42일로 늘었다(종전 511~514 · 2일). 42일 창 19행 = 수급·모멘텀·시장폭 계열, 519일 창 52행 = 재무·밸류·거시·이벤트 계열 → writer 가 하나가 아니라는 R38 진단이 변종 증가로 재확인됐다.
- 엔지니어 백로그: 없음(빌더 창 정의·알림 인구 변경 = 리뷰보드 승인). 승인 항목 `R38` 을 **2줄로 압축**(결정 요청 A/B/C + 최신 실측)하고 나머지 근거는 evidence 로 이관 — 매 틱 5줄 반복되던 노출을 줄였다.

## [리서처 R40 후속] 수급 러너가 매 평일 20:00 스택 재기동에 걸려 DB 커넥션이 죽는다 — 재연결 배선으로 수리 (2026-11-18 07:0x)
- 결과(실측): 11-17 19:10 수급 러너가 **260/800종목**을 적재한 20:00:0x 에 `stock_postgres` 가 재기동(`StartedAt 2026-11-17T11:00:16Z` = 20:00:16 KST) → 남은 11종목이 `InterfaceError: connection already closed` 로 전부 실패하고, **요약 조회(`conn.cursor()`)에서 traceback 으로 종료**돼 '완료:' 줄도 `[claim]` 자기신고도 남지 않았다(로그 12줄). 같은 시각 재기동된 컨테이너 9개(api_gateway·postgres·xgboost_ml·yfinance·krx 등, exit 0·restarts 0 = 우아한 재생성).
- 원인: 평일 20:00 `evening_pipeline` 의 `docker compose up -d` 가 컨테이너를 갈아끼운다(코드 주석 `model_engineer_cycle.py:4578` 에 같은 진단이 이미 있다). 수급 러너는 19:10 시작 → 약 54분 소요라 **매 평일 실행이 20:00 을 넘긴다** = 구조적 재발 조건. 대조군: 같은 재기동에서 `kis_daily` 는 726/3980 한 종목만 실패하고 3,836봉까지 완주(`fail=1`) — 그 경로는 이미 재연결한다.
- 수리(자율, 자기 소유 코드): `scripts/kis_supply_backfill.py` — ① 수집 루프에서 `DB_ERR`(Interface/Operational)면 **재연결 후 계속**(죽은 종목은 진행파일에 안 적어 다음 실행이 재개) ② 요약·자기신고도 재연결 후 재시도, 불가하면 자기신고만 생략하고 **rc=3(다음 실행에서 재개)** — traceback(rc=1) 아님 ③ `_record_claims` DB 오류가 수집 보고를 깨지 않게 건너뜀. 회귀 4건 `tests/test_kis_supply_reconnect_after_db_restart.py`(관련 39건 포함 통과).
- 실경로 검증: `--stocks 005930 --days 5` 재수집 rc=0 · 자기신고 `source=30 / claimed=30 / persisted=1` 3값 출력 확인(검증 행은 `dq_runner_claim` 에서 삭제해 지표 오염 없음).
- 남은 구멍(수치): `foreign_institutional` 11-17 = **14종목**(11-16 799 · 11-13 805 · 11-12 816). 회전 250종목/일이면 11-17 충전에 3거래일+. 종목별 거래일 지연 최대 **38거래일**, 3일↑ **363/1,168**. 단축은 R21 승인(호출 상한 400종목/일) 사항.
- 한계: 재기동 원인이 '사람이 띄운 compose' 인지 evening_pipeline 크론인지 로그로 확정하지 못했다(dockerd/syslog 에 해당 시각 기록 없음). 시각 자체를 피하려면 수급 크론을 20:20 이후로 옮기는 안이 있으나 **크론 시각 변경 = 리뷰보드 승인**이라 올리지 않고 여기 근거만 남긴다.


## [트레이더 T-20261118b] 단타 첫날 2건 ±0.05% — 손실은 어제 이월 스윙 청산분이고, 저널 청산가가 브로커 체결가와 4건 모두 어긋났다 (2026-11-18 15:1x)
- 결과(브로커·저널 대조, 실측): 오늘 청산 **4건** · 저널 net **-5,255.5원**(gross -4,972 · 수수료 283.5) / 브로커 자체 필드 매도실현손익 합계 **-5천원**(이 필드는 천원 단위 절사 — 과거 클로봇 -18 = -18,446원으로 단위 확인) / 브로커 평균매수·매도단가로 재계산한 gross **-5,747원**.
- 경로별: **daytrading 2건**(021320 +29원 gross · 143240 -41원 gross, 둘 다 max_hold 시간청산) / **swing 2건**(11-16·11-17 이월분, 11:27 사용자 지시 '단타 전면 전환'으로 시장가 전량청산, net -5,243.5). 오늘 신규 진입 = 2건(둘 다 daytrading), 미청산 0.
- 신규 결함①(청산가): 저널 청산가가 브로커 **평균매도단가**와 4건 전부 다르다 — 033100 35,700 vs 35,649(+51) · 096530 23,450 vs 23,400(+50) · 021320 5,570 vs 5,548(+22) · 143240 15,990 vs 15,946(+44) → 저널 gross **+775원 과대**(손실 과소). swing 2건은 exit_reason 에 '체결가=주문시점 bid 추정'이라 자백하고, daytrading 2건은 사유 없이 견적가가 들어갔다. 수리 방향: 청산가를 브로커 체결 기준으로 기록(부분/전량 수동청산 레시피와 동일 규칙) — 주문경로 수정이라 보호 작업으로 큐잉.
- 신규 결함②(단타 수수료): daytrading 청산행 `fees=0`(swing 은 142.8/140.7 기록) → fills summary `fees_known=false`, net == gross. 실측 왕복비용 0.21%면 오늘 단타 2건 ≈ **290원 누락**(net 이 실제보다 좋게 보인다).
- 환류(→ 리서처): ① close 리스트 top10 평균 day_change **+17.68%** → HEAT ceiling 15% 발동, close 경로 전일 차단(가드가 실제로 발동하는 것을 확인 — 입력은 `day_change_pct` 로 정상 해석). ② **swing 후보 20건 전부 edge_score ≤2**(19건이 1) → edge 게이트(min 4/5)가 swing 을 **100%** 막는다: 스윙 부진은 점수 문턱이 아니라 엣지 팩터가 안 뜨는 문제. ③ daytrading edge≥5 는 20건 중 **1건**(021320)뿐 — 사용자 지시로 문턱이 5가 되면 하루 후보가 1개로 줄어 회전이 사실상 멈춘다. ④ 최근 12영업일 스크리너별: swing n=8 승률 12.5% net **-22,942원** / daytrading n=2 net -12원(표본 미달).
- 판정·한계: 누적 net **-38,591원** · 기대값 **-2,757원/건** · 승률 21.4%(계기판). 표본 2건 < 5 → **켈리 사전확률 미기록**: `build_screener_stats.py`(비 dry-run) 실행 결과 swing b=1.172 p=0.431 → f* **-0.0267 ≤ 0**, daytrading 측정 배치 자체가 없어 켈리 파일은 쓰이지 않고 근거 파일만 갱신됐다(`screener_stats_measured.json` 15:16).

## [리서처 R41] 11-16 시장 데이터의 거래대금이 99.97% NULL 이었다 — 원장 검사가 DB 를 진실로 삼아 침묵한 사각지대 (2026-11-18 16:5x)
- 결과(실 DB read-only): `market_data` 2026-11-16 = **3,725행 · trading_value NULL 3,724(99.97%) · 거래대금 합 0.001조**(정상일 21~37조, 11-17 35.301조). NULL 기준선은 다른 모든 날짜 22~30%(850~1,167행) → 11-16 만 구조적 결손. 90% 초과 결손 날짜는 테이블 전체에서 **11-16 하나**.
- 원인: 11-16 은 root crontab 소실(R42)로 KIS 일봉 경로가 0회 돌았고, yfinance 스윕(`services/yfinance-collector`)만 적재했다 — 그 INSERT 는 `trading_value` 컬럼을 아예 쓰지 않고(7컬럼), 거래량>0 종목만 남겨 행수도 3,725(<3,836)였다. KIS `ON CONFLICT DO UPDATE SET trading_value=EXCLUDED...` 가 정상 경로였는데 그 경로가 없었다.
- 수리(자율 = 수집 경로 복구, 승인 불요): `scripts/krx_daily.py --from 20261116 --to 20261116` **2콜** → 2,675종목 upsert(`[claim] source=2675 claimed=2675 persisted=111`) → 11-16 **3,836행 · NULL 1,161(30.3%, 기준선) · 거래대금 21.087조**. 이어 `market_pattern_ledger.py --backfill 6` 로 원장 11-12 정정(21.281조 → 26.812조) → R41 check **0 (창 30세션 충족)**.
- 검사 확장(자기 소유 코드): `scripts/market_pattern_coverage_check.py` 에 결함 종류 `원천결손` 추가 — 창 내 세션의 `trading_value` NULL 비율 ≥0.50 이면 결함으로 세고, 그 세션의 원장↔DB 거래대금 비교는 건너뛴다(양쪽 다 ~0 이면 비율 1.0 = 자기참조 거짓 통과). 회귀 3건 추가(`tests/test_market_pattern_coverage_check.py` 총 **16건 통과**).
- 한계: 11-16 은 **거래대금 외 지표(시/고/저/종가·거래량)는 yfinance 값**이라 소스가 섞여 있다(가격 소스 혼합은 미해결 — R37 계열). 그리고 check 는 30세션 창만 보므로 그보다 오래된 원천결손은 여전히 안 보인다(창 확장은 별도 판단).

## [리서처 R21 후속] KIS OPSQ0008 을 '비일시적'로 분류해 종목을 버리고 있었다 — 재시도 배선으로 실패율 41.6% → 1.0% (2026-11-18 21:0x)
- 결과(실측): 11-18 수급 러너 2회 — 16:20 `ok=247/fail=14`(5.4%) · 19:10 `ok=191/fail=136`(**41.6%**). 오류는 전부 `[OPSQ0008] 호출 후처리(MCI전송) 오류`(rt_cd=1, http=200).
- 원인: `kis_client._call_with_retry` 는 rate-limit·토큰 오류만 재시도하고 나머지는 '비일시적'로 **즉시 raise** → 실패 종목은 그 실행에서 영구 탈락하고 다음 회전(≈3거래일)까지 수급이 동결된다. 20:50 재프로브(4종목)에서 3종목이 정상 수집 = 실제로는 일시적.
- 수리(자율, 자기 소유 코드): `INTERNAL_TRANSIENT_CODES={'OPSQ0008'}` + 상한 2회(2s·4s) 재시도. 상한을 두는 이유는 고착 종목에서 쿼터를 태우지 않기 위해서다. 회귀 5건 `tests/test_kis_client_internal_retry.py`(관련 40건 통과).
- 실경로 검증(수치): ① 039490 은 수정 전 2회 연속 실패(19:10·20:48) → 수정 후 **재시도 1회로 성공**. ② `--top 800 --days 5` 예산 200콜 실행 = **ok=100 fail=1(1.0%)**(수정 전 같은 경로 41.6%), 재시도 로그 뒤 성공 사례 다수(078340·085670). ③ 자기신고 `foreign_institutional source=3030 claimed=3024 persisted=80` → 지연0 종목 **509 → 589(+80)**.
- 남은 구멍(수치): 회전 밖 동결군은 이 수리로 줄지 않았다 — 5~9일 141 · 10일+ 185(최대 30거래일) = **326종목**(11-13 실측 199 → 증가). 범위 확대·우선 스윕은 R21 승인(일일 호출 한도 미확인 R20) 사항.
- 부수 수리: 크론 래퍼 `kis_supply.sh` 의 `tail -20` 이 136건 실패 목록을 잘라 진단 불가(로그에 실패 줄 7건만 남음) → `tail -400`.


## [모델엔지니어] 11-19 챌린저(champion_cand) 승격 판정 = 보류 — 단일분할 0.5939 는 다중폴드 0.4624 와 정면 충돌 (2026-11-19 22:47)
- 후보: `app/models/champion_cand` 2026-11-19 11:50 재학습 · n_rows 11,868(학습 9,494/검증 2,374) · 데이터창 **2026-08-21~11-19(약 3개월)**
- 단일분할: ensemble_auc **0.5939**(xgboost 0.4799 · lightgbm 0.5486 · catboost 0.6181) · deployed_substance 0.5564 · val 16일
- 같은 후보의 다중폴드(`robust_oos.json`, 80종목·h5 rel·topk3·수수료 0.21%p·익일시가): **0.4624**(폴드 [0.4418, 0.4746, 0.4708] · std 0.0146 · 3폴드) · 순기대 **+0.0277%p**(t **0.16** · 113세션/1,771거래) · 앞/뒤 반쪽 **+0.1094 / −0.0525 (unstable)**
- 챔피언 동일 프로토콜: robust_oos **0.4908** · 순기대 **−0.182%p**(t −0.81) → **후보가 AUC 로 −0.0284 열세**
- 게이트: 기본 `--dry-run` = `would_promote`(0.5939 > 0.5513) / 돈 게이트(`--require-expectancy --require-robust`) = **`kept_incumbent`**(사유: 분할표본 불안정). 채택한 판정 = **승격 금지**
- 왜 기본 게이트를 따르지 않았나: 하드규칙 1(단일분할 AUC 는 판정 근거가 아니다) + CG170(게이트 AUC 다리가 프로토콜 혼용: 후보=단일분할·기준선=단일분할이나 후보의 실제 OOS 는 다중폴드 0.4624). catboost 단독 0.6181 이 xgb 0.4799 와 0.14 벌어진 것도 단일 시드·2,374행 val 의 잡음으로 보는 것이 맞다.
- 부수 관찰: 후보의 학습 데이터창이 3개월뿐이라 챔피언(장기 창)과 학습량이 다르다 — 같은 후보로 '데이터 축'을 논하려면 창을 맞춘 재학습이 선행 조건이다.

## [모델엔지니어] 배포경로 전방 돈 지표 — h=5 top10 이 체결가능 풀평균을 −1.74%p/세션 하회 (2026-11-19 22:4x)
- 출처 `services/xgboost-ml/reports/overnight/forward_scorecard.json`(06:36 생성 · 예측 108,898행 · 모델버전 champ-20261002 + v1.0)
- h=5: 11날짜 · 필터 후 8,565행(kept 20.9%) · top10 net **−4.5793%p** vs 풀 net **−2.8390%p** → **Δ −1.7403%p · sd 3.3713 · t −1.71** · 반쪽 [−2.7425, −0.9051] · 양수 날짜 5/11
- h=1: 15날짜 · 11,836행 · top10 +0.1281%p vs 풀 +0.0587%p → Δ +0.0694 · t **0.11** · 반쪽 [−0.3143, +0.4051] → **노이즈**
- 측정 주의: 두 창 모두 pooled AUC(0.574/0.607)와 날짜별 AUC 평균(**0.517/0.504**)의 격차가 0.06~0.10 → pooled 값은 날짜 간 수준차를 섞은 값이라 선별력 근거로 쓰면 안 된다(원장에 이 money 블록 기록은 2026-10-08 CG137 이후 없음 → **CG365 신규 등록**, n_dates 20 도달 시 판정)
- 해석 한계: h=5 절대 손실(−4.58%p)의 상당 부분은 하락장 국면 효과다. 판정 대상은 **Δ(top10−풀)** 이며 그 값이 −1.74%p·t −1.71 로 '이득'이 아니라 '손실 후보'다.

## [리서처 R43] 피처 빌더의 로드 창이 좁아져 momentum_3_12m 이 조용히 죽는다 — 선행 이력 분리(수리) + 모델창 생존 상시 감시  (2026-11-20 00:50)
- 결과: R43: 0 >= 0 → 충족
- 판정: 충족
- 근거: 실측 2026-11-19 21:1x — cron/supply_features.sh(20:15)가 --since 45일 전으로 돌면서 window_days=43 창의 momentum_3_12m nonzero=0.0000(전부 NULL). supply_market_features 11-03~11-18 nz 0 (10-05 이후 재빌드분). 수리 = price_history_start() 로 가격 이력만 since-400일 로드 후 격자 하한 복원. 재실행 실측: momentum_3_12m nz 0.8910(11-18 3,475/3,729), bb_position 0.3822→0.9966, relative_strength 0.9673→0.9998 (첫날 NaN·20일 창 절단도 함께 해소). 회귀 tests/test_supply_features_momentum_lookback.py 5건.
- 엔지니어 백로그: `XR43` (command·대조군 기입 필요)

## [리서처 R41] 18:47 틱이 적재 전 부분 스냅샷으로 '전일 세션'을 덮어썼다 — 시각이 아니라 내용으로 막는 가드 (2026-11-20 21:2x)
- 증상(실측): 18:47 리서처 틱 실행 후 R41 check = `1 <= 0 → 미달`. 같은 check 를 21:1x 에 다시 돌리면 `0 <= 0 → 충족`.
- 원인: R41 command `market_pattern_ledger.py --today` 는 **DB 최신 날짜**를 기록한다. 리서처 틱은 18:47, 일봉 적재(`daily_bars.sh`)는 18:55 → 18:47 시점 11-19 는 yfinance 스윕분만 든 상태였고, 그 값이 기존 정상 기록을 덮었다. 11-19: scored **3832→3722** · trade_value_sum **17.988조→null** · breadth **64.7→70.9** · adr **2.24→2.72**. 19:40 원장 크론(Hermes job `10212cee483b`)이 재기록해 53분 만에 복구 — 즉 **결함이 53분 존재**했고 그동안 엔지니어 틱·피드가 그 값을 '그날 시장'으로 읽을 수 있었다.
- 수리(자율, 자기 소유 코드): `scripts/market_pattern_ledger.py::_looks_partial(new, prev)` — 봉수가 기존×0.98 미만이거나, 거래대금이 있는 기록을 0/null 로 덮으려 하면 **덮지 않고 skip**(`--force` 로만 강제). 시각 하드코딩을 피한 이유: 적재 시각은 경로마다 다르고(18:55 KRX / KIS 폴백 3.5h) 시계 가드는 다른 경로에서 다시 샌다. 상향(부분→완전)·동일 재실행은 통과 → 멱등 계약 유지.
- 회귀: `tests/test_market_pattern_ledger_partial_guard.py` 8건 + 기존 `test_market_pattern_coverage_check.py` 20건 = **28 passed**.
- 실경로 검증(수치): ① 정상 데이터 `--today` → `changed sessions: []`(64세션 불변, 오탐 0) ② 실 DB + 18:47 로그값 주입 → `[guard] 부분 스냅샷으로 기존 기록 덮어쓰기 차단: scored 3722 < 기존 3832 x 0.98` 후 11-19 = **3832 · 17.988조 · breadth 64.7 보존**.
- 남은 것: 19:40 원장 크론이 유일한 정상 기록 경로인데 root crontab 소실(R42) 계열 사고에 노출된다 → R42 승인 항목에 병기. R43 은 20:15 슬롯이 얇은 최신일을 회수함을 재확인(11-19 3,722행 · momentum 3,475).

## [리서처 R44] sns_post_features 5거래일 동결 — writer 는 있었는데 스케줄이 없었다 (2026-11-20 21:2x)
- 증상(실측): `sns_post_features` 최신 `trade_date` = **2026-11-13**(created_at 11-13 12:06). 11-16~11-20 **5거래일 결번**. 원천 `sns_posts` 는 최신 11-20 09:05 로 정상 → **원천이 아니라 가공 누락**이다.
- 원인: `scripts/sns_features_writer.py`(sns_posts → 종목·일 집계, 멱등 upsert)는 11-13 에 만들어졌지만 **크론·구동기·래퍼 어디에도 배선이 0건**(`grep -rn` 실측). 원천 수집은 정상이라 0행·파서 실패·신선도 어떤 신호에도 안 걸린다 — 스킬에 적힌 '스케줄 존재를 결함수로 판정하는 check 가 없으면 수집 정지는 어디에도 안 나온다'(R40 교훈)의 재현이다.
- 조치 1(자율·백필): `--lookback-days 9` 1회 실행 → **2,532행 upsert**(게시글 92,364건/종목 379) → 11-12~11-20 전부 채워짐. 실측 11-20 = 235종목 · attention 평균 **0.496** · 게시글 **52.6**건/종목(11-14·15 주말 158~169종목으로 자연 감소).
- 조치 2(자율·누락 스케줄 배선): 사용자 crontab `20 18 * * 1-5 /home/jhshi/cron/sns_features.sh`(sns_collect 17:30 뒤·야간 패널 빌드 전) + 래퍼 `scripts/sns_features_cron.py`(claim_start/finish 내장 → 컨테이너 러너도 3값 자기신고).
- 자기신고 실측: `[claim] sns_features_writer -> sns_post_features source=92364 claimed=2532 persisted=0` — 재실행 no-op(=이미 적재) 정상 경로.
- 신선도 check 신설: `sns_post_features` 가 `market_data` 마감일 대비 뒤진 거래일 수 ≤ 1 (현재 **0**).
- 교훈: **writer/스크립트의 존재 ≠ 스케줄의 존재.** 새 writer 를 만들면 같은 커밋에 스케줄·자기신고·신선도 check 를 함께 넣어라. 이 축은 R17(SNS 원천 커버리지 250종목)과 다른 문제다.

## [리서처 R44] sns_post_features 가 5거래일 동결 — writer 는 있는데 스케줄이 없었다 (일일 배선 + 신선도 상시 감시)  (2026-11-21 00:48)
- 결과: R44: 0 <= 1 → 충족
- 판정: 충족
- 근거: [실측 2026-11-20 21:1x] ① `SELECT max(trade_date) FROM sns_post_features` = **2026-11-13**(created_at 최대 11-13 12:06) — 11-16~11-20 5거래일 결번. ② `sns_posts` 최신 11-20 09:05(건수 정상) → 원천 문제 아님. ③ `grep -rn build_sns_attention_features /home/jhshi/cron /etc/cron.d` = 0건, writer 참조 크론 0건. ④ 수리 후 실측: writer 를 `--lookback-days 9` 로 1회 실행 → **2,532행 upsert**(게시글 92,364건 · 종목 379) → 11-12~11-20 9일 전부 채워짐(11-20 = 235종목 · attention 평균 0.496 · 게시글 52.6). ⑤ 자기신고 `[claim] sns_features_writer -> sns_post_features source=92364 claimed=2532 persisted=0`(재실행 no-op = 정상). ⑥ 스케줄 배선: 사용자 crontab `20 18 * * 1-5 /home/jhshi/cron/sns_features.sh`(sns_collect 17:30 뒤 · 야간 피처/패널 빌드 전) + 래퍼 `scripts/sns_features_cron.py`(claim_start/finish 내장).
- 엔지니어 백로그: `XR44` (command·대조군 기입 필요)

## [트레이더 환류 close-signal-date-frozen-outliers] close 피드 후보 3/20 이 84~300일 낡은 signal_date 를 달고 발행돼 소비자 신선도 게이트에서 차단된다  (2026-11-23 15:27)
- 근거: 2026-11-23 14:40 발행본 close 후보 20건 중 3건의 signal_date 가 2026-01-27 · 2026-02-03 · 2026-08-31 (각 300·293·84일 낡음). 소비자 후보별 신선도 게이트(close 4일)에서 이 3건은 발행 즉시 차단된다. 스크리너 단위 lag 프로브(feed_signal_lag=3.0일)는 다수값만 봐서 이런 소수 이상치를 못 잡는다. 재현: python3 -c "import json,collections;d=json.load(open('data/feed/screener_latest.json'));print(collections.Counter(c.get('signal_date') for c in d['candidates']['close']['items']))"
- 리서처 백로그: `T49`

## [리서처 R47] 수급 잡이 두 스케줄에 걸려 하루 두 번 돈다 — 19:10(cron.d) + 19:19(사용자 크론)  (2026-11-23 16:55)
- 근거: 사용자 크론 스풀 mtime 11-20 21:16(`10 19 * * 1-5 …/kis_supply.sh`) + `/etc/cron.d/analyist_dd:28` 11-23 13:20 수정(`10 19 * * 1-5 …/kis_supply.sh`, .bak 판본은 16:20). 로그 헤더 계열은 11-17~11-20 에 16:20·19:10 두 번, **19:19 실행 0건**.
- 영향: 실행 1회 ≈27분이라 매 평일 19:19 실행이 19:10 실행과 겹친다 → 같은 800종목을 두 프로세스가 동시에 받아 KIS 호출 2배 · 진행파일 동시 쓰기.
- 수리(자율): 래퍼 `/home/jhshi/cron/kis_supply.sh` 에 flock 단일 인스턴스 가드 — 겹치면 나중 실행이 `이미 실행 중 — 양보(중복 스케줄)` 1줄 남기고 rc=0(멱등 재수집이라 손실 없음). 검증: 잠금 쥔 채 실행 → **rc=0 · 0.01초 · KIS 호출 0회**.
- 상시 감시 신설: 백로그 `R47`(당일 실제 실행 횟수 ≤ 1, 양보 줄 제외) — 가드가 사라지거나 중복 스케줄이 되살아나면 잡힌다.
- 남은 결정(승인): 중복 스케줄 한 줄 제거 — 권고는 **사용자 크론 19:19 삭제, 19:10 유지**(R40 의 '일봉 이후 게시' 조건 충족).

## [리서처 R48] KRX OpenAPI 는 당일봉을 안 준다 — 직전 거래일 봉이 '아침 몇 시'부터 발행되는지 시각별 실측 (07:30 로더 가능성 판정)  (2026-11-23 22:47)
- 결과: [조사]R48: 500 >= 2 → 충족 | 증거: {"probed_at": "2026-11-23", "target_session": "2026-11-20", "min_rows": 500, "markets": {"KOSPI": 935, "KOSDAQ": 1836}} | R48 직전거래일(2026-11-20) 봉 가용 마켓수 2 (문턱 마켓당 500행)
- 판정: 조사완료
- 근거: [실측 2026-11-23 21:1x] ① `SELECT max(trade_date) FROM market_data` = 2026-11-20 (당일 11-23 은 거래일인데 0행). ② 직접 프로브: KRX data-dbg `stk_bydd_trd?basDd=20261123` → OutBlock_1 **0행**(21:16), `basDd=20261120` → **935행**(KOSPI) / KOSDAQ 1836행. ③ 같은 시각 `data.krx.co.kr`(운영 호스트)는 404(이 키 미승인) → dbg 호스트가 유일 경로. ④ 18:55 러너 로그도 구간 2026-11-16~2026-11-22 = 어제까지로 잘려 있다(`target_range`: `end >= today → today-1`). ⑤ 결론(현재까지): 당일봉 미발행은 **소스 한계 확정**, T-1 아침 가용성은 **미판정** — 04·06 틱에서 이 프로브를 돌려 판정한다.
- 엔지니어 백로그: `XR48` (command·대조군 기입 필요)

## [리서처 R49] feature_coverage 131행이 11-18 측정으로 5일 정체 — dq_feature_oldest_age_days 4.998, 5일 문턱 직전 (재측정 경로 + 낡은 패널 쓰기 금지 가드)  (2026-11-24 00:48)
- 결과: R49: 131 <= 0 → 미달
- 판정: 미달
- 근거: [실측 2026-11-23 21:1x] ① `SELECT computed_at, count(*) FROM feature_coverage GROUP BY 1` → 11-23 11:16(19행·수급 빌더) / 11-23 10:10(17행·이벤트 빌더) / 11-22 17:26·17:28(35행) / **11-18 12:14(131행)**. ② `dq_feature_oldest_age_days` = 4.998 (Prometheus, 21:1x). ③ `docker exec stock_xgboost_ml cat /app/app/models/exp_panel/meta.json` → last_trade_date 2026-10-06, built_at 10-06T13:44. ④ 수리: `scripts/feature_coverage_report.py` 에 패널 신선도 가드 추가(`panel_staleness` / `--max-panel-age-days` 기본 10 / `--allow-stale`) — 컨테이너 실행 실측: '패널 캐시 마지막 거래일이 48일 전 (한도 10일) — 쓰기 생략(exit 3)' 출력 확인. 회귀 3건(tests/test_feature_coverage_stale_panel.py).
- 엔지니어 백로그: `XR49` (command·대조군 기입 필요)
- [수리·재측정 2026-11-24 01:06] 원인 2건: (1) 호스트 실행 시 sys.path 후보가 '/app'·cwd 뿐 → `ModuleNotFoundError: No module named 'app'`(R49 rc=1) → `_app_roots()` 에 repo/services/xgboost-ml 추가, (2) `--ignore-cache` 신선 재빌드에도 캐시 낡음 가드가 걸려 exit 3 → 가드를 캐시 경로에만 적용. `--ignore-cache --stocks 60 --days 120` rc=0 실측: 196행 computed_at 11-24 01:06, stale5 137→**6**, dq_feature_non_model_window_rows 71→**0**, 창 가짓수 3→**1**, 생존 178/죽은 24. 잔여 6행 = rank_*(스크리너 `compute_cross_sectional_ranks` 경로 산출 — 이 스크립트로 갱신 불가) → XR49 로 핸드오프. 표본 주의: 60종목 표본으로 갱신해 죽은 피처 12→24(희소 피처 표본 의존).
- [반복 방지 2026-11-24 01:20] `--skip-if-fresh-hours 20` 추가: 같은 창(window_days) 측정의 90%↑가 N시간 내에 이미 갱신됐으면 재빌드를 생략(exit 0, json `skipped:true`). 이유 — check_target(<=0)이 rank_* 6행 때문에 0 이 되지 않아 매 틱 25분 빌드를 반복하면 DB 부하만 쌓인다. 실측 skip 판정 196/202행 → 1초 완료(빌드 25분 대체). 회귀 8/8 PASS.
