\pset pager off
\echo == supply_market_features: non-zero coverage, panel window (2025-07-31 .. 2026-09-23) ==
SELECT
  count(*) AS rows,
  count(DISTINCT stock_code) AS codes,
  round(100.0*sum((institution_net_buy_5d IS NOT NULL AND institution_net_buy_5d<>0)::int)/count(*),2) AS inst_net5d,
  round(100.0*sum((foreign_net_buy_5d IS NOT NULL AND foreign_net_buy_5d<>0)::int)/count(*),2) AS for_net5d,
  round(100.0*sum((foreign_ownership_pct IS NOT NULL AND foreign_ownership_pct<>0)::int)/count(*),2) AS for_own,
  round(100.0*sum((institution_ownership_pct IS NOT NULL AND institution_ownership_pct<>0)::int)/count(*),2) AS inst_own,
  round(100.0*sum((retail_ownership_pct IS NOT NULL AND retail_ownership_pct<>0)::int)/count(*),2) AS ret_own,
  round(100.0*sum((short_interest_ratio IS NOT NULL AND short_interest_ratio<>0)::int)/count(*),2) AS short_int,
  round(100.0*sum((short_selling_ratio IS NOT NULL AND short_selling_ratio<>0)::int)/count(*),2) AS short_sel,
  round(100.0*sum((days_to_cover IS NOT NULL AND days_to_cover<>0)::int)/count(*),2) AS dtc,
  round(100.0*sum((momentum_3_12m IS NOT NULL AND momentum_3_12m<>0)::int)/count(*),2) AS mom312,
  round(100.0*sum((relative_strength IS NOT NULL AND relative_strength<>0)::int)/count(*),2) AS rel_str,
  round(100.0*sum((bb_position IS NOT NULL AND bb_position<>0)::int)/count(*),2) AS bb_pos,
  round(100.0*sum((market_breadth IS NOT NULL AND market_breadth<>0)::int)/count(*),2) AS breadth,
  round(100.0*sum((krx_advance_decline_ratio IS NOT NULL AND krx_advance_decline_ratio<>0)::int)/count(*),2) AS adr
FROM supply_market_features
WHERE trade_date BETWEEN DATE '2025-07-31' AND DATE '2026-09-23';

\echo == same, restricted to the 49-stock production panel codes ==
WITH u AS (
  SELECT DISTINCT stock_code FROM supply_market_features
  WHERE trade_date BETWEEN DATE '2025-07-31' AND DATE '2026-09-23'
  ORDER BY stock_code LIMIT 49
)
SELECT count(*) AS rows,
  round(100.0*sum((momentum_3_12m IS NOT NULL AND momentum_3_12m<>0)::int)/count(*),2) AS mom312,
  round(100.0*sum((foreign_net_buy_5d IS NOT NULL AND foreign_net_buy_5d<>0)::int)/count(*),2) AS for_net5d,
  round(100.0*sum((institution_net_buy_5d IS NOT NULL AND institution_net_buy_5d<>0)::int)/count(*),2) AS inst_net5d,
  round(100.0*sum((short_selling_ratio IS NOT NULL AND short_selling_ratio<>0)::int)/count(*),2) AS short_sel,
  round(100.0*sum((relative_strength IS NOT NULL AND relative_strength<>0)::int)/count(*),2) AS rel_str
FROM supply_market_features s JOIN u ON u.stock_code = s.stock_code
WHERE s.trade_date BETWEEN DATE '2025-07-31' AND DATE '2026-09-23';
