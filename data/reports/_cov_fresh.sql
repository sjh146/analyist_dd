\pset pager off
\echo == 피처 원천 신선도(오늘 10-01 에 쓰였는가) ==
SELECT 'market_data' t, max(trade_date)::text AS max_date, count(*) AS n FROM market_data
UNION ALL SELECT 'supply_market_features', max(trade_date)::text, count(*) FROM supply_market_features
UNION ALL SELECT 'supply_market_features.computed_at', max(computed_at)::text, count(*) FROM supply_market_features
UNION ALL SELECT 'event_features', max(event_date)::text, count(*) FROM event_features
UNION ALL SELECT 'event_features.created_at', max(created_at)::text, count(*) FROM event_features;
\echo == 오늘(10-01) 생성된 행 ==
SELECT 'supply_market_features' t, count(*) FROM supply_market_features WHERE computed_at >= DATE '2026-10-01'
UNION ALL SELECT 'event_features', count(*) FROM event_features WHERE created_at >= DATE '2026-10-01'
UNION ALL SELECT 'market_data', count(*) FROM market_data WHERE created_at >= DATE '2026-10-01';
