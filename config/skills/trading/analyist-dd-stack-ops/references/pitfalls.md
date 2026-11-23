## 함정 (전부 실측)

- **Docker 29는 ro 부모 바인드 안에 하위 마운트포인트를 만들 수 없다.** job-runner의 `./services/strategy-agents:/opt/strategy-agents:ro` + `./config/strategies:/opt/strategy-agents/config/strategies:ro` 조합은 `runc: mkdirat ... read-only file system`으로 컨테이너 생성 실패 → 부모를 rw로 바꾼다.
- 스크립트가 `docker-compose`(v1)를 호출하면 `printf '#!/bin/sh\nexec docker compose "$@"\n' > /usr/local/bin/docker-compose && chmod 755` 심을 만든다.
- **`usermod -aG docker`는 이미 열린 셸에 적용되지 않는다** → 그 세션의 docker 명령은 `sg docker -c 'docker ...'`로 실행한다.
- 네이버 금융이 2026-09 SPA로 전환(302) → `finance.naver.com/sise/sise_market_sum` 파서는 **0종목**을 조용히 반환한다.
  대체: `https://m.stock.naver.com/api/stocks/marketValue/{KOSPI|KOSDAQ}?page=N&pageSize=100` JSON, 100건/페이지 · `totalCount`로 종료 판단 · ETF/ETN 제외는 `stockEndType != 'stock'`(KOSPI는 이 필터로 2,484행→945종목).
- `scripts/data_gap.py`는 `LOOKBACK_DAYS=10`만 보므로 **빈 DB나 10일보다 오래된 공백을 '공실 없음(정상)'으로 오보**한다. `SELECT count(*), max(trade_date) FROM market_data` 선점검이 필요.
- WSL 호스트 python3는 PEP 668 → `apt-get install -y python3-psycopg2 python3-pandas python3-pytest python3-requests`로 해결(venv 없이 스크립트·테스트 실행).
- KIS 백필은 **종목당 1콜로 최대 100일** 범위 조회(기간별시세). 일자별 반복은 낭비다.
  호스트에서 `cd services/kis-collector && python3 ../../scripts/kis_backfill_range.py` (진행파일 재개, `KIS_MAX_CALLS` 기본 2500, 5초 딜레이).
  2,678종목 × 1년(3콜/종목 = 8,034콜) ≈ 12시간 → 밤 3~4회로 분할.
- KIS 수집기 유니버스는 `market_data`에서 나온다 → **빈 DB에서는 아무것도 수집하지 않는다**. 먼저 `stocks`를 시딩하고 구간 백필 러너(=`stocks` 기준)를 써야 한다.
- **market_data의 1년 일봉은 yfinance-collector 컨테이너가 채운 것**이다(부팅 시 전 종목 수집, 종목당 약 244행). 이름과 달리 이 컨테이너는 **pykrx(KRX 레거시 엔드포인트)**를 쓰므로 Yahoo가 아니다 — 그래서 KRX OpenAPI와 값이 미세하게 다르고 일부 종목 OHLC가 여러 날 동결되기도 한다(실측: SK하이닉스 17일). KRX OpenAPI는 날짜당 2콜이므로 **1년 전체 재수집이 494콜 ≈ 55분**에 끝난다 — `krx_daily.py --from <시작> --to <끝>`(진행파일 이름이 달라야 재수집이 된다).
- **시세 적재 위생 3중 방어 (2026-09-23 적용)**: ① 보조 수집기(pykrx)는 장 마감(15:40 KST) 전이면 end_date를 D-1로 클램프하고, `trade_date >= 오늘(KST)` 행은 저장을 거부하며, `ON CONFLICT DO NOTHING`으로 **기본값이 덮어쓰기 금지**(`YF_MARKET_DATA_OVERWRITE=1`로만 복귀)다. ② 공식 경로(KRX OpenAPI 2콜/일)가 우선. ③ 피처 조회는 `app/feature_engine/market_data_filter.MARKET_DATA_VALID`로 거래정지 행(시가·고가·저가가 모두 0)을 제외한다.
  - 이 방어가 없으면 부팅 직후 수집이 장중 부분봉을 넣어 **20:00 파이프라인이 부분 봉을 학습에 쓰고**, DB 최신일이 당일로 올라가 증분 조건(`start = DB max + 1`)이 막힌다(실측 2,562행).
  - KRX 원본은 거래정지/무거래 종목에도 행을 준다 — 시가·고가·저가 0, 거래량 0, 기준가만 종가로 들어온다(28,282행 / 579종목). 소비 측에서 제외해야 ATR·수익률이 망가지지 않는다.
- **KIS 키가 .env에 있으면 `daily_bars.sh`가 KIS 경로(종목당 1콜 = 2,770콜 ≈ 2.5h)를 택한다** → 20:00 파이프라인이 부분 적재를 읽는다. 2026-09-23부터 KRX 우선(2콜) + KRX 차단 시 KIS 폴백으로 바꿔 두었다(config/cron/host-daily_bars.sh).
- **네이버 종목토론방도 SPA 전환으로 깨졌다**(2026-09). `finance.naver.com/item/board.nhn?code=` 는 302 → `stock.naver.com/domestic/stock/<code>/discussion`, body 0바이트다.
  현행 수집 경로(실측 확정): `GET https://stock.naver.com/api/community/discussion/posts/by-item?discussionType=domesticStock&itemCode=<code>&isHolderOnly=false&excludesItemNews=false&isItemNewsOnly=false&isCleanbotPassedOnly=false&pageSize=100[&offset=<cursor>]`.
  응답은 `{offset,pageSize,posts[],lastOffset}` 이고 `offset` 은 전역 게시글 id 의 음수값(최신=`-9223372036854775807`)이라 임의 과거로 점프할 수 있다(≈5만 id/일). 글별 필드: `id,writer.profileId,writer.nickname,writtenAt,title,contentSwReplacedButImg`.
  `recommendCount/commentCount/viewCount` 는 **목록 응답에서 항상 0** → `GET .../posts/reactions?postIds=1,2,3`(보조), `GET .../posts/comment-counts?postIds=...` 로 따로 채워야 attention/author_quality 가 비영이 된다.
  **함정**: 응답 본문에 사용자 입력발 잘못된 이스케이프(`\d`)와 원시 제어문자가 섞여 `json.loads`(기본)이 실패한다 → `re.sub(r'\\(?!["\\/bfnrtu])', r'\\\\', t)` 후 `json.loads(t, strict=False)`. `isItemNewsOnly=true` 면 뉴스만 오고 토론글이 0건이다.
- **`market_data.trading_value` 는 최신 거래일에 대부분 NULL** 이다(실측: 2,568행 중 4행만 값). 거래대금 상위 유니버스를 최신일로 뽑으면 임의 종목이 나온다 → `count(trading_value) >= 1000` 인 최신 날짜를 기준일로 쓴다(보조 수집기분은 기입 전).
- `scripts/*.py` 를 컨테이너에서 `python scripts/x.py` 로 실행하면 `sys.path[0]` 이 `scripts/` 라 `import app...` 이 **ModuleNotFoundError** 다 → `sys.path.insert(0, "/app")`.
- `data_gap.py`의 휴장/공실 프로브는 **KIS 전용**이다 — KIS 키가 없으면 공실을 휴장으로도 공실로도 판정하지 못하고 '공실 없음'으로 흘린다.
- 분봉 크론(`kis_minute.sh`)은 KIS 당일분봉만 쓴다 → **당일 데이터만 존재**(과거 백필 불가) + 페이지당 30행이라 종목당 14콜. 그래서 우선순위 유니버스(`data/minute_universe.json`, 거래대금 상위 300)로 좁혀 돌린다.
