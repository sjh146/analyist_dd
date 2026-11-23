---
name: analyist-dd-stack-ops
description: "Use when rebuilding the analyist_dd Docker+ML stack."
---

# analyist_dd 스택 재구축·운영

analyist_dd = 한국 주식 수집(일봉/분봉) → ML(챔피언 앙상블 xgb+lgb+cat) → 스크리너 → 전략 파이프라인.
저장소는 `origin https://github.com/sjh146/analyist_dd.git` 이고 **기본 브랜치가 `master`** 다(`main` 아님 — `git push origin main` 은 refspec 오류).
런타임 = Ubuntu 호스트의 Docker Compose 17컨테이너 + **호스트** 크론(KIS 수집기·백필은 컨테이너 밖).
구 프로덕션 경로는 `/home/dduckbeagy/analyist_dd` (스크립트에 하드코딩되어 있음).

## 재구축 순서 (WSL 재현 실측)

1. Docker 설치 — WSL은 sudo 암호가 없다. interop root로 스크립트 실행:
   `/mnt/c/Windows/System32/wsl.exe -u root -- bash /home/<user>/docker_install.sh`
   내용: `apt-get install -y docker.io docker-compose-v2` + `systemctl enable --now docker` + `usermod -aG docker <user>`.
   (Ubuntu 24.04 → Docker Engine 29 + compose v2; 설치 3분, 이미지 총 ~26GB, 빌드 ~15분.)
2. 저장소는 **ext4**에 둔다(`git clone <local-경로> /home/<user>/analyist_dd`). 그 다음 스크립트 호환용 심볼릭:
   `mkdir -p /home/dduckbeagy && ln -sfn /home/<user>/analyist_dd /home/dduckbeagy/analyist_dd`
3. `.env` 확보(`.env.example` 기반). **`POSTGRES_HOST_PORT=5434`** 필수 — 호스트 스크립트·크론이 5434로 붙는다.
   비밀번호는 무작위 생성해 파일에만 기록하고 transcript에 남기지 않는다. API 키(KIS/DART/ECOS/DeepSeek)는 사용자가 채운다.
4. `docker compose build && docker compose up -d` (컨테이너 이름은 `stock_*`).
5. 유니버스 시딩(키 불필요): `docker exec stock_news_analyzer python /app/scripts/init_stocks.py --all`.
6. 크론: `.env`를 source하고 `POSTGRES_HOST=127.0.0.1`을 export하는 래퍼를 만들고 `/etc/cron.d/<name>`에 등록(사용자 root).

- 상세: `references/pitfalls.md` — 함정 (전부 실측)

## 크론 표준 (프로덕션 재현)

| 시각(KST) | 작업 |
|---|---|
| 03:30 매일 | cleanup_news_data.sh |
| 04:15 화~토 | data_gap.py backfill (1일) |
| 07:50 평일 | data_gap.py report |
| 08:30 평일 | swing-pipeline.sh (→ job-runner `run_swing_job.py` → `reports/swing_latest.json`) |
| 08:40 평일 | 피드 발행(feed_export → data/feed) |
| 18:55 평일 | KIS 일봉 전 종목 |
| 20:00 평일 | full_pipeline_dd.sh (후보 학습 → 승격 게이트 → 백테스트) |
| 20:30 평일 | 피드 재발행 |
| 23:00 평일 | KIS 분봉 |

## KRX OpenAPI 대체 경로 (KIS 키 없을 때)

일봉 수집이 KIS에만 묶여 있으면 자격증명이 없는 동안 수집이 멈춘다. `scripts/krx_daily.py`는
공식 KRX OpenAPI(일별매매정보)로 그 자리를 대신한다: **날짜 1개 = 시장 2콜**
(`sto/stk_bydd_trd`, `sto/ksq_bydd_trd`)로 그날 전 종목이 `OutBlock_1`에 오므로 1년치가 약 524콜(≈30분)이다.

- **차단 신호 구분**: 키 없음 = `{"respMsg":"Unauthorized Key"}`, 키 있음·서비스 미승인 = `{"respMsg":"Unauthorized API Call"}` (둘 다 HTTP 401). 후자는 openapi.krx.co.kr 마이페이지에서 **서비스 이용 신청**(주식 > 유가증권/코스닥 일별매매정보)이 필요하다는 뜻이다.
- **호스트**: `https://data-dbg.krx.co.kr/svc/apis` 는 JSON을 돌려주고, `https://data.krx.co.kr/svc/apis/...` 는 같은 경로에 404(HTML)를 준다 → 후보 목록으로 첫 JSON 응답 호스트를 골라 상태파일에 캐시한다.
- **절대 쓰면 안 되는 것**: 과거 IP 차단을 유발한 스크래핑 엔드포인트 `comm/bldAttendant/getJsonData.cmd`(현재 이 IP대역은 403). 공식 `/svc/apis` 만 쓴다.
- **차단 예방 기본값** (env로만 조정): 호출 간 3.0s+지터 0.5s, 실행 간 최소 60초(상태파일 공유), 실행당 상한 600콜, 날짜별 진행파일 재개, 401/403/429/5xx·respCode≠200 → 재시도 없이 즉시 종료(exit 3).
- **휴장 자동 처리**: 휴장일은 응답 행이 오되 가격이 빈값이므로 이를 휴장으로 판단해 `data/krx_holidays.json`(data_gap.py와 공유)에 기록한다.
- 일봉 수집 크론은 디스패처(`daily_bars.sh`)로 KIS 자격증명 유무에 따라 KIS↔KRX를 자동 선택하고, `data_gap.py backfill`도 같은 규칙으로 폴백한다.

## 피처 부활 · 라벨 설계 실측 (2026-09-24, WSL 재구축본)

AUC 를 올리는 순서는 **라벨 → 데이터 → 피처**다. 피처를 채우는 것만으로는 오르지 않는다.

- **라벨 설계가 지배적**: 절대(1일) 방향 0.5133 → 시장상대(cross-sectional 중앙값) 0.5270
  → 분위 라벨(상위 q=0.3 =1 / 하위 0.3 =0, **중간 40% 제외**) + 호라이즌 연장 0.57~0.60.
  같은 피처셋에서 라벨만 바꿔 +0.06.
- **단, 고정 분할 값은 믿지 마라 (중요)**: 위 0.57~0.60 은 분할 60/20/20 에서만 나온다.
  420일 패널로 확장창 walk-forward(5폴드)를 돌리면:
  - h=5 + 분위 + top30: **0.5405 ± 0.0331** (폴드 0.519 / 0.536 / 0.547 / 0.593 / 0.507) — 최고
  - h=8 + 분위 + top30: **0.4977** (폴드 전부 0.49~0.50) = 사실상 랜덤
  - h=8 + top40 0.4946, h=6 + top40 0.5283, h=8 + 저학습률 0.5089
  → **8일 호라이즌의 0.60 은 테스트 구간이 상승장이었던 운**이다. 호라이즌을 늘릴수록
  고정 분할에서는 좋아 보이고 walk-forward 에서는 무너진다(과적합 신호).
  판단은 항상 **확장창 walk-forward 의 폴드 평균**으로 하라. 도구: `scripts/wf_wave.py`.
- 현재 이 스택에서 로버스트하게 얻을 수 있는 1일방향 AUC 는 **0.54 수준**이다. 0.6 은 아직 없다.

### 후보 스크리닝 임계값 함정 (swing 레그가 0건이 되는 이유)
- 배포 챔피언의 확률은 0.33~0.55 구간에 몰려 **절대 임계값 0.65 를 절대 넘지 않는다**
  → swing 후보가 항상 0건, `batch_type=raw_fallback` 로 떨어진다. 모델이 약해서지 버그가 아니다.
- 더 심각한 함정: **피처가 비면 모델 출력이 상수로 붕괴**한다(실측 2026-09-23: 후보 20건 중
  17건의 confidence 가 정확히 0.5081 동일). 그 상수값이 순위 상위로 올라와 후보를 오염시킨다.
  피처를 되살린 뒤(비영 피처 중앙값 92/173)에는 30/30 종목이 서로 다른 확률을 낸다.
  진단 도구: `scripts/swing_degenerate_probe.py` (종목별 확률·비영 피처 수 출력).
- 처방: 절대 임계값 대신 **순위 기반 top-N**(선별된 유니버스 내) + 상수 붕괴 감지 가드.
  단 모델 AUC 0.54 수준이므로 이 레그는 '고확신 신호'가 아니라 후보 생성기로 취급하라.
- **유니버스 규모가 붕괴의 진짜 원인**: 유니버스를 1,727종목(SPAC 제외+신선도 필터만)으로
  두면 후보 20건이 **전부 같은 상수**(실측 0.5122)가 되어 그 상수값이 순위 상위를 독차지한다.
  붕괴는 저유동·저데이터 종목에 집중된다 → **유동성 상위 300**(20거래일 평균 거래대금) +
  SPAC 제외 + 최근 7일 시세 필수로 좁히면 300종목 전부 서로 다른 확률(고유값 20/20,
  0.4033~0.4307)이 나온다. `swing_screener.get_kosdaq_stocks(pg, top_liquidity=300)`.
- **붕괴 감지 가드는 '전략 비우기'가 아니라 '붕괴 항목만 제외'**: 전부 비우면 close
  전략까지 함께 사라져 조용히 무동작이 된다.
- **품질 게이트**: 모델이 하락 우위로 평가한 후보(confidence < 0.5)는 매수 대상이 아니다.
  순위만으로 자르면 '가장 덜 약세'인 종목이 뽑혀 매수 신호로 오해된다 →
  `feed_export.py --min-swing-confidence 0.5`(기본) 로 발행 전에 제거한다. 그 결과 swing 레그는
  모델이 매수 우위를 못 내는 날 0건이 되는데, 이는 의도된 안전 동작이다.
- **피처 희석은 주범이 아니다**: 181피처 전체 0.5267 ≈ curated 57피처 0.5270. 근거 없이 피처를 줄이지 마라.
  다만 단변량 edge 상위 30~40개로 줄이면 +0.005 정도 이득(top30 0.5727 > top40 0.5645 > top50 0.5702 > top20 0.5649).
- **뉴스/감성/이벤트/SNS 피처는 무정보**: 데이터를 다 채워도 단변량 |AUC-0.5| edge 평균이
  감성 0.0030 / 이벤트 0.0014 / SNS 0.0015 (매크로 0.0348, 테마·사이클 0.0161 대비).
  → 뉴스 수집·DeepSeek 감성 분석에 비용을 쓰기 전에 이 사실을 먼저 검증하라. 측정 도구: `scripts/feature_diagnosis.py`.
- **누수(purge)**: h일 호라이즌 라벨은 시간순 분할 경계에서 다음 블록 가격을 쓴다.
  각 블록 마지막 h거래일 행을 버려야 한다(purge). h=1 에서는 차이 0.001, h=5 에서 0.005,
  호라이즌이 길수록 커진다. purge 없이 측정하면 없는 성능을 보고하게 된다.
- **단일 분할 금지**: 테스트 행이 700개 수준이라 분할(60/20/20 vs 70/15/15 vs 50/25/25)에 따라
  AUC 가 ±0.03 흔들린다. 실측: 같은 설정이 0.6015 / 0.5704 / 0.5463. **3개 이상 분할 평균**으로 판단하라.

### 피처가 '살아 있는데 0' 인 구조적 원인 (전부 실측)
- `FeaturePipeline` 이 학습 경로에서 `neo4j_conn=None` → 그래프 피처(theme/twin/cycle)가 **구조적 0**.
  env(NEO4J_URI/USER/PASSWORD)로 자동 연결하게 하면 7/7 부활.
- 리더 호출부에 `date` 를 넘기지 않으면(=`get_all_features(stock_code, conn)` 형태) 매크로·이벤트·SNS 가
  '현재 시점' 값으로 고정돼 상수 또는 look-ahead 가 된다. as-of 로 넘겨야 날짜별 값이 생긴다.
- `NewsEventFeatures` 는 **date 객체**를 받는다(문자열을 넘기면 `datetime.combine` TypeError 로 전 피처 0).
- `stock_vectorizer` 는 20:00 스케줄 작업이 **메모리에 든 옛 코드**로 임베딩을 덮어쓴다. 코드를 고쳤으면 컨테이너를 재기동해야 유지된다.
- writer 가 아예 없는 테이블: `macro_indicators`, `sns_post_features`.
- `foreign_institutional` 은 KIS 수급 수집기로 채워졌다(2026-09-24): `scripts/kis_supply_backfill.py`
  (최신 30영업일) + `scripts/kis_supply_extend_history.py`(FHPTJ04160001 로 60~90영업일까지 과거 연장).
  지분율 3피처는 별도 `ownership` 테이블에서 읽는다(`feature_pipeline.py` §12~14) — KIS 는 외국인 지분율
  스냅샷(`hts_frgn_ehrt`)만 주고 **기관 지분율 필드는 없다**(NULL → 리더 0.0, `retail` = 100 − 외국인).
  수급 리더(`market_features.get_supply_features`)는 `date` 를 안 받으면 **테이블 최신 행을 항상 집어와**
  조회일을 바꿔도 값이 같다(상수) → as-of 필터를 넣어야 날짜별 값이 생긴다.
  리더만 있고 writer 가 없는 테이블은 영원히 0 이다.
- 자격증명 블로커: `ECOS_API_KEY` 가 **빈 값**(금리·CPI·PPI 등 9피처), `KRX_ID/KRX_PW` 없음 + KRX OpenAPI
  공매도·프로그램·파생 서비스 미승인(약 14피처). 이 둘은 코드로 못 푼다.

## trader-agent 피드 연동 (종목 전달)

analyist_dd 가 뽑은 종목을 trader-agent 로 넘기는 경로. 계약 원본은 trader-agent 의 `FEED_CONTRACT.md`,
이 저장소 사본은 `docs/TRADER_INTEGRATION.md`.

- 발행: `scripts/feed_export.py` → `data/feed/screener_latest.json`(원자적 교체).
  입력 `reports/close_latest.json`·`swing_latest.json`. 가격 누락은 market_data 최근 종가로 보정.
- 서빙: `scripts/feed_server.py` + `/etc/systemd/system/analyist-feed.service` (0.0.0.0:8090, `/screener_latest.json`·`/health`).
- 크론: 08:40 스윙 발행 / 14:40 종가 잡+발행 / 20:30 재발행 (`/etc/cron.d/analyist_dd`).
- 검증: Windows 쪽 `tools/check_feed.py`(브리지 불필요, 소비자 파서로 파싱), 게이트 검증은
  `run_trader_core.py scan --dry-run`(브리지 필요) · 포트폴리오 제약을 빼고 보고 싶으면 `--mock`.
- **함정**: ① 계약이 보는 `generated_at` 은 '발행 시각'이라, 산출물이 낡았어도 신선하게 보인다
  → 발행기의 `--max-source-age-days`(기본 3일) 가드가 필요하다. ② 스크리너가 대체 경로(`batch_type=raw_fallback`)로
  돌면 점수가 한 값에 몰려(실측 17/20 동일) 선별이 무작위가 된다 → 발행기의 점수 퇴화 가드(동일값 80%)가
  그 전략을 비운다. ③ WSL2 에서 Windows→WSL 은 `localhost:<port>` 로 되지만,
  WSL→Windows 는 안 된다(브리지 헬스는 반드시 Windows 쪽에서 확인). ④ tailnet IP 경유 대용량 응답은
  멈춘다(소형 `/health`는 통과) — 같은 노트북이면 localhost 를 쓴다.

## 뉴스 이벤트 피처 부활

`news_event_extraction`/`news_events` 가 비면 `news_event_features.py` 의 `event_*_5d` 18개와
`theme_exposure_5d` 가 전부 상수 0이 된다(추출기가 감성/진위만 저장했던 탓). 절차·함정은
`references/news-event-feature-revival.md`, 실행은 `scripts/news_event_backfill.py`
(`--stage extract|cluster|verify|revert`). 핵심 함정 3개: URL 내 `CB` 오탐, 한국어 합성어
(`화재`⊂`문화재`) 오탐, 클러스터키 seq 미할당으로 클러스터가 덮어써지는 버그.

## SNS 피처 학습 연결 (실측 함정)

`feature_engine/sns_feature_bundle.py` 가 리더(sns_features/sns_lag_features)를 감싸
파이프라인에 붙이는 어댑터다. `feature_pipeline.py` 패치는 `docs/SNS_PIPELINE_PATCH.md`.

- **리더 SQL 버그**: `SnsFeatures.compute_for_stock` 이 `sns_posts.trade_date` 를 SELECT
  했다 → 그 컬럼은 없다(`posted_at` timestamp). 예외가 fail-open 으로 삼켜져 **항상 []** 를
  반환한다 → `posted_at::date AS trade_date` 로 고칠 것.
- **trade_date 파라미터가 클래스를 가린다**: `get_all_features(stock_code, date=None)`
  안에서 `isinstance(x, date)` 는 파라미터(str)를 참조해 `TypeError` 다.
  `datetime.strptime(str(date)[:10], "%Y-%m-%d").date()` 로 파싱한다.
- **커서 깊이의 경제학**: `lastOffset` 을 진행파일에 저장해 재개하면 같은 예산으로 깊이가
  계속 는다. 다만 게시글 밀도가 종목당 2.4~250건/일로 100배 차이 → '얇은 종목 우선'이
  아니라 **게시글 밀도 낮은(싼) 종목 우선**으로 정렬해야 일(day) 커버리지 이득이 최대다.
  요청은 페이지 1 + 보강 2 = 3/100건이므로 **최근 N일만 보강**(기본 15일)하면 같은
  시간에 깊이가 3배. 대신 미보강 과거일은 `author_quality_score` 가 0(엄밀히 4e-9)이 되어
  '최근인가' 대리변수가 된다.
- **비영 비율 측정 주의**: `post_author_quality` 의 `_EPS` 때문에 미보강 일자 값이
  정확히 0 이 아니라 `4.23e-09` 다. `!= 0.0` 으로 세면 0.922, 허용오차 1e-6 으로 세면
  0.371 (DB 는 numeric(6,4) 라 0.0000 저장). 커버리지 지표는 반드시 tol 기준으로.
- **`kalman_activity` off-by-one**: `KalmanSmoother.smooth` 반환이 n-1 길이라 마지막 날은
  값이 없다 → 종전엔 0.0. 날짜 기준 조회는 조회일이 항상 윈도우 마지막이라 피처가 통째로
  상수 0 이 된다 → 마지막 가용 스무딩 값으로 채운다.
- **시차(lead/lag) 16개는 SNS 커버 13일 미만이면 전부 0** (`MIN_PAIRS=8` + lag ±5).
  커버리지가 곧 피처 품질이다.

## 모델 승격 게이트 (2026-09-24 교체)

**단일 시드 `auc.txt` 를 비교 기준으로 쓰면 승격이 잠긴다.** 실측: 같은 코드·같은 데이터로
학습한 후보가 0.4365 / 0.5790 / **0.6131** 로 요동쳤고(분할·시드 운), 마지막 운값 0.6131 이
`app/models/champion/auc.txt` 에 남아 기준선이 되면서 walk-forward 0.54~0.58 로 개선된 후보가
영원히 승격되지 않았다(예: champion_cand_fair 0.5513 → 거부).

현행 `app/training/champion_promote.py` 규칙:
- 후보 지표 = `auc_mean`(다중 시드 평균) 있으면 그것, 없으면 `ensemble_auc`.
- 챔피언 기준선 = `champion/robust_auc.json` 있으면 그 값, 없으면(레거시)
  `min(auc.txt, --legacy-baseline-cap 0.55)` — 운값은 상한까지만 인정.
- 승격 시 `robust_auc.json` 을 챔피언 디렉터리에 기록 → 다음 비교가 동일 기준.
- `--max-std`(기본 0.05): 다중 시드 std 가 큰 후보는 거부.
- 검증은 항상 `--dry-run`(챔피언 무변경) + `--summary-out /tmp/...` 로. 실측 4건:
  0.5056 → kept(하한), 0.5513 → would_promote(잠금 해제), robust 0.60 → kept(우선 적용),
  auc_std 0.09 → kept(std 초과).

**배포 챔피언 성능을 말할 때**: `auc.txt` 값(0.6131)은 단일 분할·단일 시드 운값이다.
같은 챔피언 경로의 다른 학습 기록 0.5790 / 0.4365, OOS 백테스트 0.5251~0.5803,
확장창 walk-forward 0.5405 — 즉 **실제 기대치는 0.52~0.55**.

## 크론 스크립트 함정 (08:30 스윙이 조용히 죽던 원인)

- `set -euo pipefail` 스크립트에서 **조건문 밖의 bare `docker-compose exec`** 는 실패 시 그 자리에서
  스크립트를 죽인다(로그는 단계 헤더에서 끊긴다). `if ...; then` 안이거나 `|| true` 를 붙일 것.
- 스크립트가 쓰는 로그/증거 디렉터리(`.omo/evidence` 등)를 **먼저 mkdir** 하지 않으면
  로테이션 `find` 가 exit 1 → `set -e` 로 즉사한다.
- **피드가 실제로 읽는 파일을 갱신하는지 확인하라**: 스윙 단계가 CSV 만 복사하고
  `reports/swing_latest.json` 을 안 쓰면 피드의 swing 레그는 3일 뒤 가드에 걸려 사라진다.
  정답 경로는 job-runner 컨테이너의 `run_swing_job.py`(스크리너 실행 + JSON 기록).
- `run_swing_job.py` 의 up/down 판정이 **없는 키 `prob`** 를 봐서 전부 up 으로 집계되던 버그 — CSV 는 `confidence` 를 쓴다.
- 실행 중인 셸 스크립트를 **편집하면 bash 가 바이트 오프셋으로 재읽기** 해 오작동할 수 있다 →
  예약 실행 직전(예: 20:00 파이프라인)에는 편집을 피하거나 완료 후에 반영하라.

## 도커 데몬은 WSL 부팅 후 자동 기동되지 않는다 (실측 2026-11-16)

- **interop 세션에서 띄운 dockerd 는 수십 초 뒤 같이 죽는다**: `wsl.exe -u root -- sh -c 'setsid nohup dockerd &'`
  로 시작한 데몬은 그 interop 세션이 끝날 때(≈20초) SIGTERM 을 받고 `Processing signal 'terminated'` → `Daemon shutdown complete`
  로 끝난다. nohup·setsid 로도 안 살아남는다. Windows 쪽 detached 프로세스로 띄워야 유지된다:
  `powershell -NoProfile -Command "Start-Process wsl.exe -ArgumentList '-d','Ubuntu-24.04','-u','root','--','/usr/bin/dockerd','-H','unix:///var/run/docker.sock','--containerd=/run/containerd/containerd.sock' -WindowStyle Hidden"`
- **데몬을 잘못 띄우면 '스택 전멸'처럼 보인다 — 판단 전에 `/var/lib/docker/containers` 를 세라**:
  containerd-snapshotter 를 끈 채(또는 플래그 없이) 뜬 데몬은 기존 containerd 상태를 못 읽어 `docker ps -a` 에
  방금 만든 postgres 1개, `docker images` 에 3개만 보여준다(실측). 디스크에는 컨테이너 디렉터리 18개·18G 가
  그대로 있고 `docker info` 는 `containers=1` 을 보고한다 → 데이터 유실로 오진하기 쉽다. 올바른 플래그로
  재기동하면 17개 컨테이너·17개 이미지가 그대로 돌아오고 restart 정책으로 자동 기동된다(검증: 17 Up · api_gateway 200).

## Docker 재기동 후 스택 복구 (2026-11-16 실측)

systemd 없이 WSL이 재부팅되면(`/etc/wsl.conf`에 `[boot] systemd=true`가 없으면 PID1=`init`) cron·docker·게이트웨이가 **전부 자동 기동되지 않는다**. 그 상태에서 `docker ps`가 이상하게 보이는 경우가 있다.

- **증상**: `docker ps -a`가 컨테이너 17개를 보여주는데 `NAMES`가 **빈칸**이고 `docker inspect <id>`는 `no such object`. 데몬 로그에 `RWLayer of container ... is unexpectedly nil`.
  원인은 Docker 29의 **containerd 이미지 스토어**(`containerd-snapshotter=true` 기본)로 기동돼 **overlay2로 만든 기존 컨테이너/이미지를 읽지 못하는 것**이다. 재기동을 반복해도 고쳐지지 않는다.
- **수리**: `/etc/docker/daemon.json`에 `{"features": {"containerd-snapshotter": false}}` 를 쓰고 dockerd 재시작 → `docker info`의 `Storage Driver: overlay2` 확인. 스토어를 오가면 **이미지 태그가 사라져**(`docker images` 비어 있음) `docker compose up -d --build`로 다시 빌드해야 한다(~15분). **named volume은 그대로**라 `postgres` 데이터는 보존된다(3.2M행 실측 확인).
- **데몬을 세션 하위로 띄우지 마라**: Hermes 터미널에서 `wsl.exe -u root -- dockerd`를 백그라운드로 돌리면 세션 정리 때 함께 죽는다(`Cannot connect to the Docker daemon`). `setsid dockerd`로 분리하고, systemd 대신 **cron keepalive**(`* * * * * root /usr/local/bin/start-docker.sh`, 스크립트가 `pgrep -x dockerd`로 없으면 기동)로 상주시킨다. feed 서버(8090)도 같은 이유로 사용자 crontab keepalive가 필요하다(파일 로그가 root 소유면 `Permission denied` — `chown jhshi`).

## 게이트웨이가 조용히 죽어 크론 틱을 끊는 문제 (2026-11-21 실측)

증상은 "역할 틱이 몇 시간마다 unknown 으로 끊긴다"로 나타난다 — `~/.hermes/cron/executions.db` 의
`status=unknown` + `error="Scheduler restarted after this execution's owner exited before a durable terminal state"`.
이걸 크론 코드 결함으로 읽지 말고 **게이트웨이 생존**을 먼저 본다.

- 게이트웨이는 liveness watchdog 을 돌린다. 이벤트 루프가 연속 3회 응답하지 않으면 스택을 덤프하고
  **exit 75** 로 스스로 끝난다(`gateway.log` 의 `CRITICAL gateway.shutdown_watchdog`). 그 시점 로그에
  `lifecycle_ledger: ... exited UNCLEANLY ... suspected_oom=True` 와 `last_mem` 이 남는다.
- 실측 원인은 메모리 고갈이었다: `mem_available=162MB/8,086,124KB`, 스왑 2GB **전량 사용**, RSS 236MB.
  WSL 상한은 호스트의 50%(15.9GB → 7.9GB)이고 17컨테이너가 postgres 1.1G·news 1.0G·xgboost 0.76G·
  neo4j 0.65G·grafana 0.55G·jenkins 0.4G 를 상시 점유한다. ML 주기 실행이 겹치면 여유가 수백 MB 로 떨어진다.
- 재기동 주체는 `stack-boot.sh`(크론)뿐이라 **점검 주기만큼 공백**이 생긴다(5분 주기 → 최대 5분).
  처방 4개를 함께 넣는다: ① `/mnt/c/Users/<user>/.wslconfig` 에 `memory=12GB`·`swap=8GB`(`wsl --shutdown` 후 적용)
  ② 즉시 쓸 `/swapfile2` 4GB(`ensure-swap.sh`, `stack-boot.sh` 가 없으면 만든다) ③ 스택 점검을 **1분 주기**로
  (`/etc/cron.d/analyist-stack-keepalive`) ④ `scripts/system_hygiene.py` 에 `mem_avail` 800MB 경고 / 300MB 위반 +
  스왑 85% 경고. 사본은 `config/cron/host-stack-boot.sh`·`host-ensure-swap.sh`.
- 진단 순서: `grep -E "shutdown_watchdog|lifecycle_ledger" ~/.hermes/logs/gateway.log | tail` →
  마지막 `last_mem` → `free -m` + `docker stats --no-stream` 로 상위 점유자 확인.

## docker 이미지/컨테이너 메타데이터 유실 후 재빌드 (2026-11-21 실측)

WSL 재부팅 뒤 `docker ps -a` 에 컨테이너가 남아 있는데 **이름이 비고** `docker inspect` 는 `no such object`,
`docker images` 는 5개만 보이는 상태가 된다. `/var/lib/containerd` 에는 26GB·blob 250개가 그대로 있다 →
데이터 유실이 아니라 **이미지/컨테이너 메타데이터 유실**이다. 볼륨은 스토어와 무관하게 보존된다(7개 4.6GB,
`market_data` 3.24M행 확인). 복구는 재빌드다.

- **재빌드가 buildkit 기본 네트워크에서 멈춘다** — `curl`/`pip` 가 TLS 단계에서 0% CPU 로 무응답(실측 8분).
  같은 순간 호스트·일반 컨테이너의 egress 는 정상(`wget -T 15 https://github.com` 200)이다.
  `docker build --network=host` 는 buildx 가 없어 실패하고, `DOCKER_BUILDKIT=1 docker build` 도 같다 →
  **compose 서비스의 `build:` 블록에 `network: host` 를 넣고** `docker compose build <svc>` 로 돌린다(9개 서비스 전부).
- **`pkill -f "docker compose up -d --build ..."` 금지** — 패턴이 실행 중인 셸 자신의 명령줄과도 일치해
  셸이 SIGTERM(exit −15)으로 죽고 뒤따르는 검증이 사라진다(실측 2회). `ps -eo pid,args | grep -F <문자열>`
  으로 pid 를 찾아 그 pid 만 죽인다.
- 검증: `docker ps` 17개 + `curl --noproxy '*' 127.0.0.1:8000/health`(`/9090/-/healthy`·`3000/api/health`) 200.
- 재발 방지: `/etc/docker/daemon.json` 을 명시적으로 둔다(`{"features":{"containerd-snapshotter":false},
  "log-driver":"json-file","log-opts":{"max-size":"50m","max-file":"3"}}`). 파일이 없으면 기동 기본값이
  바뀌어 스토어가 어긋날 수 있다.

## full_pipeline_dd.sh 수동 실행 함정 (2026-11-16 실측)

- **사본을 `/tmp`에 두면 첫 단계에서 죽는다**: 스크립트 첫 줄이 `cd "$(dirname "$0")/.."` 라서 `/tmp/x.sh`는 cwd가 `/`가 되고 `reports/phase_*.log: No such file or directory`로 즉사한다. 수정본은 **`scripts/` 안에 두고** 실행한 뒤 지운다.
- **Phase 1-1(yfinance)이 전체연도 재수집으로 4~5시간 걸릴 수 있다**: 실측 16종목/분 × 4,350종목 = 270분. `market_data`가 최신 거래일까지 연속(예: 11-02~11-13 끊김 없음)이면 이 단계는 **오늘 중복 작업**이므로 건너뛰고 1-3부터 돌리는 편이 맞다. 단계 호출부(`run_docker_phase stock_yfinance_collector /tmp/phase_1_1.py 7200`)를 echo로 치환하면 된다.
  KRX OpenAPI는 **T+1 발행**이라 월요일 저녁에 최신일이 금요일인 것은 정상이다 — '오늘자 데이터 없음'을 장애로 오보하지 마라.
- **수동 실행은 root가 아니라 jhshi로 돌면 승격 토큰 쓰기가 막힌다**: `services/xgboost-ml/app/models/promote_live_score_gate.json`이 root 소유(644)면 `gate-live-score`가 `PermissionError(13)`을 남기고 초크포인트가 승격을 거부한다. `chown jhshi`로 해결.

- 상세: `references/news-macro-coverage.md` — 뉴스 귀속·매크로 커버리지 (2026-11-16 실측)

## 역할 협력 도구 (2026-11-16 신규)

- `scripts/contract_check.py` — 생산자/소비자 경계 6계약(DATA-1 시세, FEAT-1 피처생존, NEWS-1 귀속, NEWS-2 기사가용성,
  FEED-1 피드 신선도/가격/신호나이, FEED-2 소비자 확률문턱=MT116)을 PASS/FAIL 판정, 결과는
  `data/reports/contract_check_latest.json`. 크론 `10 8,13,20 * * *`(root). FAIL 에는 담당 역할이 붙는다.
- `scripts/role_bus.py` — `data/reports/role_bus.jsonl` append-only. `claim`(TTL 선점, 충돌 시 exit 3)·`handoff`·`result`.
  틱 스크립트에 claim/trap release/post result 3줄만 넣으면 중복 작업이 사라진다.
- 설계·로드맵: `docs/QUANT_ROLE_COLLAB.md`(경계를 코드로 검사 · 하루 한 목표 one_thing.json · 교차 검증 · 전이 알림만).

## 스택 상주·부팅 안정화 (2026-11-17, systemd 없이)

PID1 이 init 인 WSL 에서는 재부팅 후 **cron·dockerd·feed(8090)·게이트웨이가 모두 자동 기동되지 않는다**.
`/usr/local/bin/stack-boot.sh`(root)가 4개를 idempotent 하게 점검·기동하고, 세 경로로 호출된다.

1. **Windows 로그온 예약작업 `analyist-stack-boot`** — `wsl.exe -d Ubuntu-24.04 -u root -- /usr/local/bin/stack-boot.sh`
   (RunLevel Highest, AtLogOn). 등록 스크립트는 `tools/_register_stack_boot.ps1`(Windows 사본 레포).
   이게 없으면 재부팅 후 아무도 cron 을 깨우지 않아 keepalive 크론들도 같이 죽는다(닭-달걀).
2. **root crontab `@reboot`** — stack-boot.sh (cron 이 살아나면 자동 재진입).
3. **`/etc/cron.d/analyist-stack-keepalive`** — 5분마다 stack-boot.sh (개별 서비스 사망 자동 복구).
   docker 는 `docker-keepalive`(매분, start-docker.sh), feed 는 사용자 crontab 이 따로 보강한다.
- **로그 위생**: `/etc/cron.d/analyist-log-hygiene` 이 04:40 에 `trim-docker-logs.sh` 로 100MB 초과
  컨테이너 json 로그를 자른다(실측 578MB→34MB). daemon.json 에 log-opts(50m×3)를 넣어도 **그 이전에
  만들어진 컨테이너는 회전이 없다** — 그래서 별도 정리가 필요하다.
- **계약검사 경보**: Hermes 크론 `analyist_dd 계약검사 경보`(`no_agent`, `~/.hermes/scripts/contract_tick.sh`,
  08:20·13:20·20:20 평일, Discord) — 위반이 **새로 생겼을 때만** 출력(6시간 리마인드), 정상이면 침묵.
- **파이프라인 phase 1-1 신선도 게이트**: yfinance 전종목 재수집은 16종목/분 = 4.5시간이라 20:00 창을
  통째로 잡아먹는다(실측). 18:55 KRX 크론이 이미 적재하므로 `market_data` 최신일이 오늘-4일 이상이면
  건너뛰고, 낡았을 때만 폴백 실행한다(`PIPELINE_FORCE_YF=1` 로 강제).

## 보유 테제 판정 · 손실 피드백 (2026-11-17 신규)

`position_theses` / `thesis_verdicts` / `scripts/thesis_review.py` 는 있었지만 **행이 0건**이었다
(테제는 ackman 같은 테제형 진입에만 생성). swing 보유는 가설·반증조건 없이 손절/익절로만 관리됐다.

- `scripts/thesis_verdicts.py --seed --judge --status`
  · 보유는 **브로커 `/positions`** 에서 받는다(`trader_fills` 는 청산 거래만 적재 → open 0행 실측).
    WSL 에서는 Windows `curl.exe --noproxy '*'` 서브프로세스로 8100 을 친다(직접 호출은 000/502).
  · 현재가는 네이버 모바일 API(`m.stock.naver.com/api/stock/<code>/basic`) → 실패 시 market_data 최근 종가.
  · 판정 = 0.55*가격 + 0.20*상대강도(유니버스 평균 대비) + 0.15*이벤트(부정/긍정) + 0.10*시간(계획 5일 초과).
    verdict ∈ {강화,유지,약화,손상,파기}, score ∈ [-1,1], **진입가 -7% 이하면 즉시 파기**.
  · `thesis_verdicts` 는 append-only 트리거(UPDATE/DELETE 금지) + `UNIQUE(thesis_id, verdict_date)`
    → `ON CONFLICT DO NOTHING` 으로 하루 1회.
- `scripts/loss_feedback.py --days N [--quiet-if-none]`
  · 손실 청산을 **원인별로 분류**: rule_gap(손실폭 -7% 인데 stop 사유 없음) → timing(보유<2h·reconcile)
    → signal(ml_prob 없음/<0.55) → market_beta(그날 유니버스 -0.5% 이하 & 상대 ±1%p) → execution(owner_exit).
  · `trade_loss_reviews` 테이블 + `data/reports/loss_reviews.jsonl`(키 fill_date+code+exit_ts 중복 방지),
    원인별 담당(engineer/trader/researcher)에 `role_bus.py handoff` 로 인계. **-100원 미만은 노이즈로 컷.**
  · Decimal 은 `json.dumps(..., default=str)` 로 직렬화(안 하면 TypeError).
- 자동화: Hermes 크론 `보유 테제 판정·손실 피드백`(`no_agent`, `~/.hermes/scripts/thesis_loss_tick.sh`,
  평일 16:50, Discord) — 판정이 바뀌거나 새 손실이 있을 때만 출력(상태파일 비교).

## 근거 기반 판정 — evidence 팩 (2026-11-17)

테제 판정과 손실 사후분석은 **같은 근거 팩**을 쓴다(`scripts/evidence.py`). 같은 사실을 두 곳에서 다르게 말하지 않기 위해서다.

- 소스 8개(전부 as-of, 조회일 이후 데이터 금지): `price`(1/5/20일·유니버스·**업종** 상대·거래량비) ·
  `supply`(`foreign_institutional` 외국인·기관 5일) · `news`(`news_events`, 긍정/부정 분류) ·
  `disclosure`(`disclosures` 14일, 유상증자·CB·소송 등은 감점) · `financial`(`financial_statements` YoY·ROE·PER·PBR·부채)
  · `macro`(`macro_features` 금리·환율·CPI·PPI·WTI·스프레드) · `us`(`us_market_data` 나스닥·SOX·VIX) · `sector`(업종 평균).
- 반환: `{"score": -1..1, "blocks": {...}, "evidence": [{"source","delta","note"}]}` + `narrative()` 한국어 한 줄.
- 테제 판정 = **0.5×진입대비 손익 + 0.5×근거점수** (가격만 보면 이미 빠진 뒤에야 반응한다).
  `thesis_verdicts` 는 불변 트리거라 근거는 **`thesis_evidence`(신규, UNIQUE(thesis_id, asof), upsert)** 에 스냅샷한다.
  `thesis_evidence` 의 UNIQUE 제약을 DROP 하면 `ON CONFLICT` 가 깨진다(실측 오류) — 건드리지 말 것.
- 손실 사후분석: 청산일 as-of 근거를 `trade_loss_reviews.detail.evidence` 에 넣고, stdout 에 `왜:` 문단을 찍는다.
  분류 규칙에 `rel5_pct`(청산일 기준 5거래일 상대 약세) 추가 — 손절(`stop`) 청산은 설계지만, 상대 약세 ≤ -3%p 이면
  원인은 **시장 베타가 아니라 종목 선택**이다.
- 실측 예: 푸드나무(청산 -6,831원) = 5일 상대 -4.4%p + **유상증자 결정 공시** + ROE -48.2% + 미장 급락(SOX -3.83%) → signal.
  현대무벡스(-5,390원) = 5일 상대 **-16.0%p**(시장 +7.3%) → 종목 고유 폭락이지만 손절 미작동으로 rule_gap.
- 뉴스 커버리지가 아직 얇아 `언론: 이벤트 0건(정보 없음)` 이 자주 나온다 — 18:45 추출 크론으로 쌓는 중이며,
  0건을 '부정 뉴스 없음'으로 읽으면 안 된다(정보 없음으로 표기된다).

## .env 변경은 컨테이너 재생성으로만 반영된다 (2026-11-17 실측)

`env_file: .env` 를 쓰는 서비스라도 아래는 **모두 env 를 다시 읽지 않는다**:
- `docker restart <c>` — 생성 시점 env 고정
- `docker compose up -d <svc>` — 설정 해시 불변이라 "up-to-date" 로 skip
- `docker compose up -d --force-recreate <svc>` — 이번 케이스에서도 재생성 안 됨

**확실한 방법**: `docker rm -f <container>` → `docker compose up -d --no-build <service>`

**함정 2개**:
1. `docker compose <cmd> <name>` 의 인자는 **서비스명**이다. `stock_xgboost_ml` 은 컨테이너명이라 `no such service` 로 실패하고, 그 사이 `rm -f` 로 지운 컨테이너는 **내려간 채 남는다** → 반드시 `docker ps | wc -l` 로 17개 확인.
2. 이 환경에서 `docker compose up -d` 는 런타임이 long-lived 로 오탐해 차단한다 → background=true 로 실행하고 별도 호출로 검증.

검증: `docker exec <c> printenv VAR1 VAR2` (값) + `docker inspect <c> --format '{{.Created}}'` (재생성 시각).

## 검증 체크리스트

- `sg docker -c 'docker compose ps'` — 17개 Up, RestartCount 0
- `curl -s -o /dev/null -w '%{http_code}' 127.0.0.1:8000/health`(api-gateway) / `9090/-/healthy` / `3000/api/health`
- `docker exec stock_postgres psql -U stock_user -d stock_trading -tAc "SELECT market, count(*) FROM stocks GROUP BY market"`
- 내부 API는 `X-API-Key: $API_GATEWAY_KEY`, `/internal/*`는 `X-Internal-Api-Key`(둘 다 .env에 넣어야 401/503이 사라진다)
- `/api/v1/stocks` 200 = DB 왕복 정상(스키마 29테이블 + pgvector는 init-scripts가 생성)

## 루트 `pytest` 가 수집 단계에서 전멸하던 문제 (실측 2026-10-03 → 591 passed 로 복구)

1. **conftest 모듈명 충돌**: `tests/conftest.py` 와 `services/trade-executor/tests/conftest.py` 가
   같은 `tests.conftest` 로 잡혀 ImportPathMismatchError → `pytest.ini` 에 `testpaths = tests`
   (서비스 테스트는 `pytest services/<svc>/tests/ -q` 로 각자 실행).
2. **`app` 네임스페이스 선점**: `services/` 12곳이 각각 `app/` 패키지를 갖는다. 먼저 import 한 쪽이
   `sys.modules['app']` 을 선점하면 이후 모듈이 `No module named 'app.training'` /
   `'app.feature_engine'` 으로 죽는다 — **단독 실행은 통과, 전체 실행만 실패**해 추적이 어렵다.
   범인 예: `tests/test_ackman_strategy.py`(→ services/strategy-agents). 수리: `tests/_app_path.py`
   `force_app(service)`(캐시 purge + 대상 경로 최우선 + importlib 캐시 무효화)를 `app.*` 를 쓰는 모듈이
   import 직전에 호출. 진단법: 임시 `pytest_collectstart` 훅에서 `sys.modules['app'].__path__` 출력.
3. **서비스 전용 의존성/실스택**: `tests/test_etl/*` 는 polars 필요 → `pytest.importorskip("polars")`
   로 호스트 skip(컨테이너에선 실행). 실 스택이 필요한 `tests/test_e2e_pipeline.py` 는 `e2e_live`
   마커 + `addopts = -m "not e2e_live"` 로 기본 수집 제외(명시 실행 `pytest -m e2e_live`).
   **E2E 는 수 분~수십 분 걸린다 — 툴 타임아웃에 걸려도 스택을 건드리진 않는다(확인함).**

검증: 루트 `pytest` → 591 passed · 7 skipped · 8 deselected(≈6초).
