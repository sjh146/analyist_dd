## 뉴스 귀속·매크로 커버리지 (2026-11-16 실측)

감성 7피처 전멸 · 이벤트 12/18 죽음의 실체는 "모델이 약해서"가 아니라 **귀속 파이프라인이 잘려 있어서**다.

- **`news_event_backfill.py` 의 `WHERE source='google_news'` 가 대상 0건**: 수집기는 `news_analysis.source` 에
  언론사명(매일경제/이데일리/서울경제/한국경제신문)을 저장한다 → 백필 대상이 **항상 0건**이었고 그래서
  news_events 가 최근 기사로 자라지 못했다. `source <> 'sns'` 로 바꾸면 347건이 보인다.
- **종목 귀속 유니버스가 curated 50 + turnover 200 = 최대 250종목**: 같은 기사에서도 귀속이 잘린다.
  전 종목명 사전(4,350)으로 넓히면 기사 347건 기준 **46 → 62종목(+35%)**, news_events 종목 410 → 424.
  (`--[no-]universe-all` 스위치, 기본 전 종목)
- **뉴스 보존 2일**(`cleanup_news_data.sh`)은 원천을 이틀만에 지워 재처리·백필을 불가능하게 만든다 → 90일로 늘렸다.
  단 `news_analysis.embedding` 이 NULL 인 것은 정상이다(임베딩은 `news_events.embedding` 에 7,655/12,140 저장).
- **매크로 피처는 `macro_indicators` 가 아니라 `macro_features`(날짜별 wide)** 를 읽는다. 후자는 347행(944거래일 중 37%)뿐이었다
  → `python3 scripts/build_macro_features.py --since 2023-01-01` 한 번으로 **944행(전 구간)** 이 된다(50초).
  `macro_backfill.py` 는 FX·WTI 가 소스(FRED) 발행 지연으로 며칠 뒤처지는 것이 정상이다.
- **`us_market_data` 가 영원히 0행인 이유**: `yfinance-collector` 의 `run_830am_batch()` 가 `import yfinance` 하는데
  `requirements.txt` 에 yfinance 가 없었다. yfinance 를 추가해도 pypi ReadTimeout/BuildKit lease 로 **이미지 빌드가 실패**하므로,
  컨테이너에 이미 있는 `requests` 로 Yahoo chart API 를 치는 `scripts/collect_us_market.py` 를 쓴다
  (크로 `40 7 * * 2-6`, 호스트 python3 + psycopg2 로 실행, POSTGRES_HOST=127.0.0.1:5434).
  실측 0행 → 151행(6심볼, NASDAQ/SOX/SP500/VIX/USDKRW 30일 + KOSPI200 1일). `^KS200` 은 1일치만 준다.
  소비 피처는 `feature_engine/us_market_features.py`(7피처, **env `US_MARKET_FEATURES=1` 일 때만** 이름/값 추가 —
  OFF 면 199피처로 종전과 동일, ON 이면 206). 켜면 챔피언 재학습+패널 재빌드 선행.
- **`foreign_investor_futures` 는 writer 자체가 없다**: KIS 수집기는 domestic-stock quotation 만 구현
  (investor-trade-by-stock-daily 등) 이고 선물 투자자별 매매동향 TR 이 없다. `krx_derivatives` 는
  가격·거래량·OI 만 있어 대체 불가 → **KIS 선물옵션 투자자별 TR 또는 KRX 정보데이터시스템을 새로 붙여야 한다**
  (스키마: trade_date, product_type, net_buy_contracts, cumulative_position).
- **패널 재빌드는 감성이 쌓인 뒤에**: `panel_200liq_ffill_2605`(22,382×217) 실측 — event 20열 평균비영 0.081,
  sentiment 7열 **0.0007**, news 2열 0.001, sns 22열 0.827, theme 3열 0.474. 즉 감성 원천이 33~49종목/일이라
  지금 재빌드해도 새 피처는 0 이다. `stock_sentiment` 커버리지가 늘어난 뒤(예: 패널 유니버스의 60%×30일) 재빌드하라.
- **기준선 함정**: 같은 라벨(h5·분위0.30·top30)의 walk-forward AUC 가 **49종목 패널 0.5405 vs 200종목 유동성 패널 0.5107±0.1059**다.
  좁은 유니버스 수치를 기준선으로 쓰면 넓은 유니버스에서 뒤집힌다 — 판정은 200종목 패널에서.
