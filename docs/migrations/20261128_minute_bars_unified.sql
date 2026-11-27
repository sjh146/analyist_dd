-- 분봉 두 테이블(KIS minute_bars · Naver minute_close_bars)을 하나로 읽기 위한 뷰 (2026-11-28).
--
-- 왜: 같은 목적의 테이블이 둘로 갈라져 있어 분석 스크립트가 한쪽만 보면 표본을 절반 잃는다.
--   실측 차이 — minute_close_bars만 볼 때 11-10~11-25 12일·4,857종목일,
--   뷰로 합치면 09-23~11-27 24일·8,075종목일(같은 측정에서 +66%).
--   KIS 행이 우선이고, Naver 행은 같은 (종목, 날짜, 시각)이 없을 때만 붙는다.
--
-- 적용: psql -h 127.0.0.1 -p 5434 -U stock_user -d stock_trading -f docs/migrations/20261128_minute_bars_unified.sql

CREATE OR REPLACE VIEW minute_bars_unified AS
SELECT stock_code, trade_date, time, close_price, open_price, high_price, low_price,
       volume, 'kis'::text AS source
  FROM minute_bars
UNION ALL
SELECT n.stock_code, n.trade_date, n.time, n.close_price,
       NULL::numeric, NULL::numeric, NULL::numeric, NULL::numeric, 'naver'::text
  FROM minute_close_bars n
 WHERE NOT EXISTS (
   SELECT 1 FROM minute_bars k
    WHERE k.stock_code = n.stock_code
      AND k.trade_date = n.trade_date
      AND k.time = n.time
 );
