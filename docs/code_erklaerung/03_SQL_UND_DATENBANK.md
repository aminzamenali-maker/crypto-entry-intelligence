# 03 - SQL und Datenbank

## Rolle von SQL im Projekt

SQL speichert die bereits geprueften Processed-Daten in einem reproduzierbaren SQLite-Modell. Die Datenbank fuegt keine fehlenden Daten hinzu und berechnet in dieser Phase keine Signale oder Positionen.

## `sql/001_schema.sql` - Tabellen und Integritaetsregeln

Originaldatei:

```sql
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

```


### Einfach erklaert

- `pipeline_metadata`: technische Metadaten zum Build
- `dim_asset`: genau BTCUSDT, ETHUSDT und SOLUSDT
- `dim_segment`: die zusammenhaengenden gueltigen Zeitsegmente
- `fact_market_context`: zentrale Faktentabelle mit Marktkerze + zeitlich passendem Kontext

Besonders wichtig sind die SQL-Checks direkt im Schema:

```sql
CHECK (decision_time_utc > close_time_utc)
CHECK (context_available_from_utc_d1 <= decision_time_utc)
CHECK (context_available_from_utc_d2 > context_available_from_utc_d1)
UNIQUE (symbol, timeframe, timestamp_utc)
```

Damit werden zentrale Projektregeln nicht nur in Python, sondern zusaetzlich auf Datenbankebene abgesichert.

## `sql/002_views.sql` - lesbare Analyse-Views

Originaldatei:

```sql
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

```


Die Views vereinfachen typische Pruefungen: getrennte 1h/4h-Sichten, Abdeckung je Asset/Zeitrahmen, Segmentabdeckung, Kontextalter und maschinenlesbare Datenqualitaetschecks.

## `src/sql_pipeline.py` - SQLite reproduzierbar bauen

### Logischer Fingerprint

Originalausschnitt, Zeilen 331-370:

```python
def logical_database_fingerprint(connection: sqlite3.Connection) -> tuple[str, dict[str, int]]:
    """Logischen Inhalt statt instabiler SQLite-Dateibytes fingerprinten."""

    objects = [
        {"type": row[0], "name": row[1], "table": row[2], "sql": row[3]}
        for row in connection.execute(
            "SELECT type, name, tbl_name, sql FROM sqlite_schema "
            "WHERE name NOT LIKE 'sqlite_%' ORDER BY type, name"
        )
    ]
    table_hashes: dict[str, str] = {}
    counts: dict[str, int] = {}
    for table in ("pipeline_metadata", "dim_asset", "dim_segment", "fact_market_context"):
        columns = [row[1] for row in connection.execute(f"PRAGMA table_info({table})")]
        order = ", ".join(f'"{column}"' for column in columns)
        digest = hashlib.sha256()
        count = 0
        for row in connection.execute(f"SELECT * FROM {table} ORDER BY {order}"):
            digest.update(json.dumps(list(row), ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
            digest.update(b"\n")
            count += 1
        table_hashes[table] = digest.hexdigest()
        counts[table] = count
    for view in (
        "vw_market_context_1h", "vw_market_context_4h", "vw_asset_timeframe_coverage",
        "vw_segment_coverage", "vw_context_freshness", "vw_data_quality_checks",
    ):
        counts[view] = connection.execute(f"SELECT COUNT(*) FROM {view}").fetchone()[0]
    payload = {
        "schema_objects": objects,
        "table_hashes": table_hashes,
        "object_row_counts": counts,
        "pragmas": {
            "application_id": connection.execute("PRAGMA application_id").fetchone()[0],
            "foreign_keys": connection.execute("PRAGMA foreign_keys").fetchone()[0],
            "user_version": connection.execute("PRAGMA user_version").fetchone()[0],
        },
    }
    digest = hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    return digest, counts
```


Der physische SQLite-Dateihash kann sich durch interne Speichertechnik veraendern. Deshalb berechnet das Projekt zusaetzlich einen **logischen Fingerprint** aus Schema, Views, sortierten Inhalten und relevanten Einstellungen.

### Build-Ablauf

Originalausschnitt, Zeilen 700-766:

```python
def build_sql_model(project_root: Path, config_path: Path) -> SqlBuildResult:
    """Validieren, temporär bauen, prüfen und ohne Überschreiben publizieren."""

    project_root = project_root.resolve()
    validated = validate_inputs(project_root, config_path)
    database_path = _inside_project(project_root, project_root / DATABASE_RELATIVE_PATH)
    report_dir = _inside_project(project_root, project_root / REPORT_ROOT)
    if database_path.exists() != report_dir.exists():
        raise SafetyError("SQL-Ausgaben sind unvollstaendig; keine Mutation erlaubt.")
    if database_path.exists():
        existing_fingerprint, existing_quality = inspect_database(database_path)
        existing_hash = sha256_file(database_path)
        _validate_cached_database(database_path, existing_quality, validated)
        expected_reports = _report_artifacts(
            project_root,
            existing_hash,
            existing_fingerprint,
            existing_quality,
            validated.source_hashes,
        )
        _validate_cached_reports(report_dir, expected_reports)
        return SqlBuildResult(
            "CACHED_VALID",
            database_path,
            existing_hash,
            existing_fingerprint,
            existing_quality,
        )
    database_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = database_path.parent / f".{DATABASE_FILENAME}.{uuid.uuid4().hex}.part"
    if temp_path.exists():
        raise SafetyError("Unerwartete temporaere SQL-Datei.")
    try:
        logical_fingerprint, quality = _create_database(temp_path, project_root, validated)
        current_hashes = {
            "market_context_1h.csv": sha256_file(project_root / "data/processed/full_import/market_context_1h.csv"),
            "market_context_4h.csv": sha256_file(project_root / "data/processed/full_import/market_context_4h.csv"),
            "processed_manifest.csv": sha256_file(project_root / "reports/processed/processed_manifest.csv"),
            "join_quality_summary.json": sha256_file(project_root / "reports/processed/join_quality_summary.json"),
            "PHASE1C_DATA_DICTIONARY.md": sha256_file(project_root / "reports/processed/PHASE1C_DATA_DICTIONARY.md"),
            "PHASE1C_QUALITY_REPORT.md": sha256_file(project_root / "reports/processed/PHASE1C_QUALITY_REPORT.md"),
        }
        if current_hashes != validated.source_hashes:
            raise IntegrityError("Eingaben haben sich waehrend des SQL-Aufbaus veraendert.")
        temp_hash = sha256_file(temp_path)
        if report_dir.exists():
            raise SafetyError("Berichtsverzeichnis existiert ohne Datenbank.")
        report_artifacts = _report_artifacts(project_root, temp_hash, logical_fingerprint, quality, validated.source_hashes)
        report_parent = report_dir.parent
        report_parent.mkdir(parents=True, exist_ok=True)
        temp_report_dir = report_parent / f".sql.{uuid.uuid4().hex}.part"
        temp_report_dir.mkdir()
        try:
            for name, content in report_artifacts.items():
                (temp_report_dir / name).write_bytes(content)
            os.link(temp_path, database_path)
            try:
                temp_report_dir.rename(report_dir)
            except Exception:
                database_path.unlink(missing_ok=True)
                raise
        finally:
            if temp_report_dir.exists():
                shutil.rmtree(temp_report_dir)
        return SqlBuildResult("CREATED", database_path, temp_hash, logical_fingerprint, quality)
    finally:
        temp_path.unlink(missing_ok=True)
```


**Einfach erklaert:** Erst Eingaben und Nachweise validieren, dann die Datenbank in einer temporaeren Datei bauen, danach fachlich pruefen und nur dann publizieren. Eine unpassende vorhandene Datenbank wird nicht still ersetzt.
