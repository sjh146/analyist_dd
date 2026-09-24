-- 10_feature_revival_objects.sql
-- 죽은 피처 부활용 DB 객체 (2026-09-24, 테마/그래프·시장폭/수급·재무잔여 담당)
--
-- WHY: feature_pipeline._build_advanced_features 는 (수정 금지 파일이라) 아래 테이블/뷰가
-- 없으면 조용히 0.0 을 넣는다. 리더 코드를 건드리지 않고 살릴 수 있는 부분만 DB 객체로 채운다.
--
--  1) stock_prices 뷰 — 스키마에 없는 테이블 이름을 market_data 로 alias.
--     pipeline 의 sector_momentum / market_breadth / days_to_cover 쿼리가 "테이블 없음"
--     예외로 0.0 이 되던 문제를 제거한다(거래정지 행은 MARKET_DATA_VALID 와 동일하게 제외).
--     ※ market_breadth 는 별도 SQL 버그(LAG 가 당일 필터 안에서 계산돼 prev_close 가 항상
--       NULL)가 있어 뷰만으로는 0 이다 → feature_pipeline.py 패치 필요(보고서 참조).
--  2) krx_trading 의 'Total' 행 — KOSPI/KOSDAQ 일별 거래대금 합계(market_data 유래).
--     pipeline 의 krx_total_trading_value 가 읽는 유일한 소스이므로 여기에 채운다.
--     investor_type='Foreign' 행(외국인 수급)은 자격증명 블로커라 건드리지 않는다.
CREATE OR REPLACE VIEW stock_prices AS
SELECT stock_code,
       trade_date,
       open_price,
       high_price,
       low_price,
       close_price,
       volume,
       trading_value
FROM market_data
WHERE NOT (open_price = 0 AND high_price = 0 AND low_price = 0);

COMMENT ON VIEW stock_prices IS
  'feature_pipeline 호환 뷰 — market_data 별칭 (2026-09-24). 실제 테이블 아님(거래정지 행 제외).';
