CREATE VIEW vw_market_context_1h AS
SELECT * FROM fact_market_context WHERE timeframe = '1h';

CREATE VIEW vw_market_context_4h AS
SELECT * FROM fact_market_context WHERE timeframe = '4h';

CREATE VIEW vw_asset_timeframe_coverage AS
SELECT symbol, timeframe, COUNT(*) AS row_count,
       MIN(timestamp_utc) AS first_timestamp_utc,
       MAX(timestamp_utc) AS last_timestamp_utc,
       COUNT(DISTINCT segment_id) AS segment_count
FROM fact_market_context
GROUP BY symbol, timeframe;

CREATE VIEW vw_segment_coverage AS
SELECT symbol, timeframe, segment_id, COUNT(*) AS row_count,
       MIN(timestamp_utc) AS first_timestamp_utc,
       MAX(timestamp_utc) AS last_timestamp_utc
FROM fact_market_context
GROUP BY symbol, timeframe, segment_id;

CREATE VIEW vw_context_freshness AS
SELECT symbol, timeframe,
       MIN(context_age_seconds) / 3600.0 AS minimum_context_age_hours,
       MAX(context_age_seconds) / 3600.0 AS maximum_context_age_hours,
       AVG(context_age_seconds) / 3600.0 AS average_context_age_hours
FROM fact_market_context
GROUP BY symbol, timeframe;

CREATE VIEW vw_data_quality_checks AS
SELECT 'primary_key_duplicates' AS check_name,
       COUNT(*) AS violation_count,
       CASE WHEN COUNT(*) = 0 THEN 'PASS' ELSE 'FAIL' END AS check_status
FROM (
    SELECT symbol, timeframe, timestamp_utc
    FROM fact_market_context
    GROUP BY symbol, timeframe, timestamp_utc
    HAVING COUNT(*) > 1
)
UNION ALL
SELECT 'join_coverage', COUNT(*), CASE WHEN COUNT(*) = 0 THEN 'PASS' ELSE 'FAIL' END
FROM fact_market_context WHERE context_match_status <> 'matched_d1_asof'
UNION ALL
SELECT 'future_context', COUNT(*), CASE WHEN COUNT(*) = 0 THEN 'PASS' ELSE 'FAIL' END
FROM fact_market_context WHERE context_available_from_utc_d1 > decision_time_utc
UNION ALL
SELECT 'status_domains', COUNT(*), CASE WHEN COUNT(*) = 0 THEN 'PASS' ELSE 'FAIL' END
FROM fact_market_context
WHERE market_quality_status <> 'accepted_phase1b_complete_month'
   OR context_match_status <> 'matched_d1_asof'
UNION ALL
SELECT 'foreign_key_integrity', COUNT(*), CASE WHEN COUNT(*) = 0 THEN 'PASS' ELSE 'FAIL' END
FROM fact_market_context AS f
LEFT JOIN dim_asset AS a ON a.asset_key = f.asset_key AND a.symbol = f.symbol
LEFT JOIN dim_segment AS s ON s.segment_key = f.segment_key AND s.segment_id = f.segment_id
WHERE a.asset_key IS NULL OR s.segment_key IS NULL;
