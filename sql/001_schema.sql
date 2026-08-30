PRAGMA foreign_keys = ON;
PRAGMA user_version = 1;
PRAGMA application_id = 1146311763;

CREATE TABLE pipeline_metadata (
    metadata_key TEXT PRIMARY KEY,
    metadata_value TEXT NOT NULL
) WITHOUT ROWID;

CREATE TABLE dim_asset (
    asset_key INTEGER PRIMARY KEY,
    symbol TEXT NOT NULL UNIQUE CHECK (symbol IN ('BTCUSDT', 'ETHUSDT', 'SOLUSDT')),
    base_asset TEXT NOT NULL CHECK (base_asset IN ('BTC', 'ETH', 'SOL')),
    quote_asset TEXT NOT NULL CHECK (quote_asset = 'USDT'),
    UNIQUE (asset_key, symbol)
);

CREATE TABLE dim_segment (
    segment_key INTEGER PRIMARY KEY,
    segment_id TEXT NOT NULL UNIQUE CHECK (segment_id GLOB 'SEGMENT_00[1-5]'),
    start_month TEXT NOT NULL CHECK (length(start_month) = 7),
    end_month TEXT NOT NULL CHECK (length(end_month) = 7 AND end_month >= start_month),
    valid_month_count INTEGER NOT NULL CHECK (valid_month_count > 0),
    boundary_description TEXT NOT NULL,
    UNIQUE (segment_key, segment_id)
);

CREATE TABLE fact_market_context (
    market_context_key INTEGER PRIMARY KEY,
    asset_key INTEGER NOT NULL,
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL CHECK (timeframe IN ('1h', '4h')),
    timestamp_utc TEXT NOT NULL CHECK (length(timestamp_utc) = 27 AND substr(timestamp_utc, -1) = 'Z'),
    close_time_utc TEXT NOT NULL CHECK (length(close_time_utc) = 27 AND substr(close_time_utc, -1) = 'Z'),
    decision_time_utc TEXT NOT NULL CHECK (length(decision_time_utc) = 27 AND substr(decision_time_utc, -1) = 'Z'),
    segment_key INTEGER NOT NULL,
    segment_id TEXT NOT NULL,
    open REAL NOT NULL CHECK (open > 0),
    high REAL NOT NULL CHECK (high > 0 AND high >= open AND high >= close AND high >= low),
    low REAL NOT NULL CHECK (low > 0 AND low <= open AND low <= close AND low <= high),
    close REAL NOT NULL CHECK (close > 0),
    volume REAL NOT NULL CHECK (volume >= 0),
    quote_asset_volume REAL NOT NULL CHECK (quote_asset_volume >= 0),
    number_of_trades INTEGER NOT NULL CHECK (number_of_trades >= 0),
    taker_buy_base_volume REAL CHECK (taker_buy_base_volume >= 0),
    taker_buy_quote_volume REAL CHECK (taker_buy_quote_volume >= 0),
    constituent_rows INTEGER CHECK (constituent_rows = 4),
    market_source TEXT NOT NULL,
    market_timestamp_unit TEXT NOT NULL CHECK (market_timestamp_unit IN ('ms', 'us')),
    market_quality_status TEXT NOT NULL CHECK (market_quality_status = 'accepted_phase1b_complete_month'),
    context_match_status TEXT NOT NULL CHECK (context_match_status = 'matched_d1_asof'),
    context_source TEXT NOT NULL CHECK (context_source = 'coin_metrics_community_api'),
    context_asset TEXT NOT NULL CHECK (context_asset = 'btc'),
    context_source_timestamp_utc TEXT NOT NULL CHECK (length(context_source_timestamp_utc) = 27 AND substr(context_source_timestamp_utc, -1) = 'Z'),
    context_available_from_utc_d1 TEXT NOT NULL CHECK (length(context_available_from_utc_d1) = 27 AND substr(context_available_from_utc_d1, -1) = 'Z'),
    context_available_from_utc_d2 TEXT NOT NULL CHECK (length(context_available_from_utc_d2) = 27 AND substr(context_available_from_utc_d2, -1) = 'Z'),
    context_price_usd REAL NOT NULL CHECK (context_price_usd >= 0),
    context_market_cap_usd REAL NOT NULL CHECK (context_market_cap_usd >= 0),
    context_tx_count REAL NOT NULL CHECK (context_tx_count >= 0),
    context_active_address_count REAL NOT NULL CHECK (context_active_address_count >= 0),
    context_age_seconds INTEGER NOT NULL CHECK (context_age_seconds >= 0),
    UNIQUE (symbol, timeframe, timestamp_utc),
    FOREIGN KEY (asset_key, symbol) REFERENCES dim_asset (asset_key, symbol),
    FOREIGN KEY (segment_key, segment_id) REFERENCES dim_segment (segment_key, segment_id),
    CHECK (decision_time_utc > close_time_utc),
    CHECK (context_available_from_utc_d1 <= decision_time_utc),
    CHECK (context_available_from_utc_d2 > context_available_from_utc_d1),
    CHECK (
        (timeframe = '1h' AND taker_buy_base_volume IS NOT NULL AND taker_buy_quote_volume IS NOT NULL AND constituent_rows IS NULL)
        OR
        (timeframe = '4h' AND taker_buy_base_volume IS NULL AND taker_buy_quote_volume IS NULL AND constituent_rows = 4)
    )
);

CREATE INDEX idx_fact_asset_timeframe_timestamp
    ON fact_market_context (asset_key, timeframe, timestamp_utc);
CREATE INDEX idx_fact_segment_timeframe_timestamp
    ON fact_market_context (segment_key, timeframe, timestamp_utc);
CREATE INDEX idx_fact_context_availability
    ON fact_market_context (context_available_from_utc_d1, decision_time_utc);
