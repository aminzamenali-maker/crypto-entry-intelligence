"""Phase 1C-B: reproduzierbares SQLite-Modell auf geprüften Processed-Daten.

Die Pipeline arbeitet vollständig offline. Sie validiert sämtliche Eingaben,
baut die Datenbank zunächst als temporäres Artefakt und veröffentlicht sie nur
nach bestandenen Integritäts- und Qualitätsprüfungen.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import math
import os
import shutil
import sqlite3
import sys
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence

from src.full_import import IntegrityError, SafetyError, load_config, sha256_file
from src.processed_pipeline import (
    MANIFEST_FIELDS,
    PROCESSED_1H_FIELDS,
    PROCESSED_4H_FIELDS,
    read_strict_csv,
)


SQL_SCHEMA_VERSION = 1
SQL_POLICY_ID = "phase1c_b_sqlite_model_v1"
DATABASE_FILENAME = "crypto_entry_intelligence.sqlite"
DATABASE_RELATIVE_PATH = "data/processed/full_import/sql/crypto_entry_intelligence.sqlite"
REPORT_ROOT = "reports/sql"
EXPECTED_ROWS = {"1h": 116_208, "4h": 29_052}
EXPECTED_ASSET_ROWS = {
    (symbol, "1h"): 38_736
    for symbol in ("BTCUSDT", "ETHUSDT", "SOLUSDT")
} | {
    (symbol, "4h"): 9_684
    for symbol in ("BTCUSDT", "ETHUSDT", "SOLUSDT")
}
EXPECTED_SOURCE_ARTIFACTS = {
    "market_context_1h": (
        "data/processed/full_import/market_context_1h.csv",
        "canonical_market_context_1h_v1",
        116_208,
        "7468ce970381e34fc60a8227fb1594dee5435e88f5521f06ed82bfa15f5ce805",
    ),
    "market_context_4h": (
        "data/processed/full_import/market_context_4h.csv",
        "canonical_market_context_4h_v1",
        29_052,
        "ab2ff44340b295d140db9fa1cb81cf5690dc7d78a44392599381c1d2e7edc91b",
    ),
    "data_dictionary": (
        "reports/processed/PHASE1C_DATA_DICTIONARY.md",
        "phase1c_data_dictionary_v1",
        None,
        "d447384445ab3201b029c2558d5036b09f79e3110c14c92a52bee59560c0b8a2",
    ),
    "join_quality_summary": (
        "reports/processed/join_quality_summary.json",
        "phase1c_join_quality_v1",
        1,
        "a55be1175c2741c77608bc882aa5c7a80d0e1f408e7acdded42c275a8f660dec",
    ),
    "phase1c_quality_report": (
        "reports/processed/PHASE1C_QUALITY_REPORT.md",
        "phase1c_quality_report_v1",
        None,
        "12530fdd00b64b31831a2bde680eab4eec246546c2d6d0228652e348d7d77024",
    ),
}
ASSETS = (
    (1, "BTCUSDT", "BTC", "USDT"),
    (2, "ETHUSDT", "ETH", "USDT"),
    (3, "SOLUSDT", "SOL", "USDT"),
)
SEGMENTS = (
    (1, "SEGMENT_001", "2021-01", "2021-01", 1, "Start; danach sind 2021-02 bis 2021-04 ausgeschlossen."),
    (2, "SEGMENT_002", "2021-05", "2021-07", 3, "Neustart nach Lücke; danach sind 2021-08 bis 2021-09 ausgeschlossen."),
    (3, "SEGMENT_003", "2021-10", "2021-11", 2, "Neustart nach Lücke; danach ist 2021-12 ausgeschlossen."),
    (4, "SEGMENT_004", "2022-01", "2023-02", 14, "Neustart nach Lücke; danach ist 2023-03 ausgeschlossen."),
    (5, "SEGMENT_005", "2023-04", "2025-12", 33, "Neustart nach letzter Quellenlücke; Ende des Projektzeitraums."),
)
EXCLUDED_MONTHS = {
    "2021-02", "2021-03", "2021-04", "2021-08", "2021-09", "2021-12", "2023-03"
}
MANIFEST_OUTPUT_FIELDS = (
    "artifact_id", "artifact_path", "artifact_type", "schema_version",
    "row_count", "sha256", "logical_fingerprint",
)


@dataclass(frozen=True)
class ValidatedSqlInputs:
    rows_1h: list[dict[str, str]]
    rows_4h: list[dict[str, str]]
    source_hashes: dict[str, str]
    manifest_hash: str


@dataclass(frozen=True)
class SqlBuildResult:
    status: str
    database_path: Path
    database_sha256: str
    logical_fingerprint: str
    quality: dict[str, Any]


def _inside_project(project_root: Path, path: Path) -> Path:
    root = project_root.resolve()
    resolved = path.resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise SafetyError(f"Pfad liegt ausserhalb des Projekts: {path}") from exc
    return resolved


def _canonical_json_bytes(payload: Any) -> bytes:
    return (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _canonical_csv_bytes(fields: Sequence[str], rows: Iterable[dict[str, Any]]) -> bytes:
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=list(fields), lineterminator="\n", extrasaction="raise")
    writer.writeheader()
    for row in rows:
        writer.writerow(row)
    return buffer.getvalue().encode("utf-8")


def _finite_number(value: str, field: str, *, positive: bool = False) -> float:
    if value == "":
        raise IntegrityError(f"Nullwert in Pflichtfeld {field}.")
    try:
        number = float(value)
    except ValueError as exc:
        raise IntegrityError(f"Nichtnumerischer Wert in {field}.") from exc
    if not math.isfinite(number) or number < 0 or (positive and number <= 0):
        raise IntegrityError(f"Unzulaessiger Wert in {field}.")
    return number


def _validate_manifest(project_root: Path) -> tuple[dict[str, dict[str, str]], str]:
    path = project_root / "reports/processed/processed_manifest.csv"
    rows = read_strict_csv(path, MANIFEST_FIELDS)
    if len(rows) != len(EXPECTED_SOURCE_ARTIFACTS):
        raise IntegrityError("Processed-Manifest muss exakt fuenf Artefakte enthalten.")
    by_id = {row["artifact_id"]: row for row in rows}
    if len(by_id) != len(rows) or set(by_id) != set(EXPECTED_SOURCE_ARTIFACTS):
        raise IntegrityError("Processed-Manifest besitzt fehlende, doppelte oder unerwartete Artefakte.")
    for artifact_id, (relative, schema_id, count, digest) in EXPECTED_SOURCE_ARTIFACTS.items():
        row = by_id[artifact_id]
        expected_count = "" if count is None else str(count)
        if (row["artifact_path"], row["schema_id"], row["row_count"], row["sha256"]) != (
            relative, schema_id, expected_count, digest
        ):
            raise IntegrityError(f"Manifestvertrag weicht fuer {artifact_id} ab.")
        artifact = _inside_project(project_root, project_root / relative)
        if not artifact.is_file() or sha256_file(artifact) != digest:
            raise IntegrityError(f"Hashnachweis fehlt oder weicht ab: {artifact_id}")
    return by_id, sha256_file(path)


def _validate_join_evidence(project_root: Path) -> None:
    report_path = project_root / "reports/processed/join_quality_summary.json"
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise IntegrityError("Join-Qualitaetsnachweis ist nicht lesbar.") from exc
    expected = {
        "schema_id": "phase1c_join_quality_v1",
        "gate_1": "NOT_EVALUATED",
        "phase1b_execution_status": "COMPLETED_WITH_SOURCE_ANOMALIES",
    }
    if any(report.get(key) != value for key, value in expected.items()):
        raise IntegrityError("Join-Qualitaetsnachweis besitzt einen unerwarteten Status.")
    global_quality = report.get("global", {})
    leakage = report.get("leakage_checks", {})
    if (
        global_quality.get("input_rows") != 145_260
        or global_quality.get("output_rows") != 145_260
        or global_quality.get("matched_rows") != 145_260
        or global_quality.get("unmatched_rows") != 0
        or global_quality.get("duplicate_primary_keys_after_join") != 0
        or global_quality.get("available_from_after_decision_violations") != 0
        or leakage.get("future_context_rows") != 0
        or leakage.get("rows_crossing_excluded_months") != 0
        or len(report.get("segments", [])) != 5
    ):
        raise IntegrityError("Join-Qualitaetsnachweis besteht die Pflichtwerte nicht.")
    gate_text = (project_root / "reports/full_import/GATE1_ACCEPTANCE_CRITERIA.md").read_text(encoding="utf-8")
    if "| G1-10 |" not in gate_text or "| PASS |" not in next(
        (line for line in gate_text.splitlines() if line.startswith("| G1-10 |")), ""
    ):
        raise IntegrityError("G1-10 ist im Gate-Bericht nicht als PASS belegt.")
    if "| G1-12 |" not in gate_text or "| NOT_EVALUATED |" not in next(
        (line for line in gate_text.splitlines() if line.startswith("| G1-12 |")), ""
    ):
        raise IntegrityError("G1-12 muss vor unabhaengiger Abnahme NOT_EVALUATED sein.")


def _expected_segment_for_month(month: str) -> str | None:
    for _, segment_id, start, end, _, _ in SEGMENTS:
        if start <= month <= end:
            return segment_id
    return None


def _validate_rows(rows: list[dict[str, str]], timeframe: str) -> None:
    if len(rows) != EXPECTED_ROWS[timeframe]:
        raise IntegrityError(f"Unerwartete {timeframe}-Zeilenzahl.")
    seen: set[tuple[str, str, str]] = set()
    asset_counts: dict[str, int] = {symbol: 0 for _, symbol, _, _ in ASSETS}
    for row in rows:
        if any(value == "" for value in row.values()):
            raise IntegrityError("Processed-Pflichtfelder duerfen nicht leer sein.")
        symbol = row["symbol"]
        if symbol not in asset_counts or row["timeframe"] != timeframe:
            raise IntegrityError("Unerwartetes Asset oder Zeitfenster in Processed.")
        key = (symbol, timeframe, row["timestamp_utc"])
        if key in seen:
            raise IntegrityError("Doppelter Processed-Primaerschluessel.")
        seen.add(key)
        asset_counts[symbol] += 1
        for timestamp_field in (
            "timestamp_utc", "close_time_utc", "decision_time_utc",
            "context_source_timestamp_utc", "context_available_from_utc_d1",
            "context_available_from_utc_d2",
        ):
            value = row[timestamp_field]
            if len(value) != 27 or not value.endswith("Z"):
                raise IntegrityError(f"Nichtkanonischer UTC-Zeitstempel: {timestamp_field}")
        if row["decision_time_utc"] <= row["close_time_utc"]:
            raise IntegrityError("Entscheidungszeit liegt nicht nach Kerzenschluss.")
        if row["context_available_from_utc_d1"] > row["decision_time_utc"]:
            raise IntegrityError("Zukunftskontext in Processed erkannt.")
        if row["context_available_from_utc_d2"] <= row["context_available_from_utc_d1"]:
            raise IntegrityError("D+2 ist nicht getrennt nach D+1 erhalten.")
        month = row["timestamp_utc"][:7]
        if month in EXCLUDED_MONTHS or _expected_segment_for_month(month) != row["segment_id"]:
            raise IntegrityError("Ausgeschlossener Monat oder falsches Segment in Processed.")
        if row["market_quality_status"] != "accepted_phase1b_complete_month":
            raise IntegrityError("Unerwarteter Marktqualitaetsstatus.")
        if row["context_match_status"] != "matched_d1_asof":
            raise IntegrityError("Unerwarteter Kontext-Matchstatus.")
        if row["context_source"] != "coin_metrics_community_api" or row["context_asset"] != "btc":
            raise IntegrityError("Unerwartete Kontextquelle.")
        prices = [_finite_number(row[name], name, positive=True) for name in ("open", "high", "low", "close")]
        if prices[1] < max(prices[0], prices[3]) or prices[2] > min(prices[0], prices[3]):
            raise IntegrityError("Unplausible OHLC-Beziehung.")
        for name in (
            "volume", "quote_asset_volume", "number_of_trades", "context_price_usd",
            "context_market_cap_usd", "context_tx_count", "context_active_address_count",
            "context_age_seconds",
        ):
            _finite_number(row[name], name)
        if timeframe == "1h":
            _finite_number(row["taker_buy_base_volume"], "taker_buy_base_volume")
            _finite_number(row["taker_buy_quote_volume"], "taker_buy_quote_volume")
        elif row["constituent_rows"] != "4":
            raise IntegrityError("4h-Zeile besteht nicht aus exakt vier 1h-Zeilen.")
    for symbol, count in asset_counts.items():
        if count != EXPECTED_ASSET_ROWS[(symbol, timeframe)]:
            raise IntegrityError(f"Unerwartete Zeilenzahl fuer {symbol}|{timeframe}.")


def validate_inputs(project_root: Path, config_path: Path) -> ValidatedSqlInputs:
    """Alle Phase-1C-A-Eingaben validieren, ohne SQL-Ausgaben zu mutieren."""

    project_root = project_root.resolve()
    config_path = _inside_project(project_root, config_path)
    config = load_config(config_path, project_root)
    if tuple(config["binance"]["assets"]) != tuple(symbol for _, symbol, _, _ in ASSETS):
        raise IntegrityError("SQL-Assetumfang widerspricht der sicheren Konfiguration.")
    _, manifest_hash = _validate_manifest(project_root)
    _validate_join_evidence(project_root)
    processed_root = _inside_project(project_root, project_root / config["paths"]["processed_root"])
    path_1h = processed_root / "market_context_1h.csv"
    path_4h = processed_root / "market_context_4h.csv"
    rows_1h = read_strict_csv(path_1h, PROCESSED_1H_FIELDS)
    rows_4h = read_strict_csv(path_4h, PROCESSED_4H_FIELDS)
    _validate_rows(rows_1h, "1h")
    _validate_rows(rows_4h, "4h")
    source_hashes = {
        "market_context_1h.csv": sha256_file(path_1h),
        "market_context_4h.csv": sha256_file(path_4h),
        "processed_manifest.csv": manifest_hash,
        "join_quality_summary.json": sha256_file(project_root / "reports/processed/join_quality_summary.json"),
        "PHASE1C_DATA_DICTIONARY.md": sha256_file(project_root / "reports/processed/PHASE1C_DATA_DICTIONARY.md"),
        "PHASE1C_QUALITY_REPORT.md": sha256_file(project_root / "reports/processed/PHASE1C_QUALITY_REPORT.md"),
    }
    return ValidatedSqlInputs(rows_1h, rows_4h, source_hashes, manifest_hash)


def _fact_tuple(row: dict[str, str], key: int) -> tuple[Any, ...]:
    asset_key = {symbol: asset_key for asset_key, symbol, _, _ in ASSETS}[row["symbol"]]
    segment_key = {segment_id: segment_key for segment_key, segment_id, *_ in SEGMENTS}[row["segment_id"]]
    one_hour = row["timeframe"] == "1h"
    return (
        key, asset_key, row["symbol"], row["timeframe"], row["timestamp_utc"], row["close_time_utc"],
        row["decision_time_utc"], segment_key, row["segment_id"], float(row["open"]), float(row["high"]),
        float(row["low"]), float(row["close"]), float(row["volume"]), float(row["quote_asset_volume"]),
        int(row["number_of_trades"]), float(row["taker_buy_base_volume"]) if one_hour else None,
        float(row["taker_buy_quote_volume"]) if one_hour else None,
        None if one_hour else int(row["constituent_rows"]), row["market_source"], row["market_timestamp_unit"],
        row["market_quality_status"], row["context_match_status"], row["context_source"], row["context_asset"],
        row["context_source_timestamp_utc"], row["context_available_from_utc_d1"],
        row["context_available_from_utc_d2"], float(row["context_price_usd"]),
        float(row["context_market_cap_usd"]), float(row["context_tx_count"]),
        float(row["context_active_address_count"]), int(row["context_age_seconds"]),
    )


INSERT_FACT_SQL = """
INSERT INTO fact_market_context VALUES (
    ?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?
)
"""


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


def _database_quality_payload(
    connection: sqlite3.Connection,
    counts: dict[str, int],
    checks: list[tuple[str, int, str]],
) -> dict[str, Any]:
    tables = ("pipeline_metadata", "dim_asset", "dim_segment", "fact_market_context")
    views = (
        "vw_market_context_1h", "vw_market_context_4h", "vw_asset_timeframe_coverage",
        "vw_segment_coverage", "vw_context_freshness", "vw_data_quality_checks",
    )
    schemas = {
        table: [
            {"name": row[1], "type": row[2], "not_null": bool(row[3]), "primary_key_position": row[5]}
            for row in connection.execute(f"PRAGMA table_info({table})")
        ]
        for table in tables
    }
    by_asset_timeframe = {
        f"{symbol}|{timeframe}": count
        for symbol, timeframe, count in connection.execute(
            "SELECT symbol,timeframe,COUNT(*) FROM fact_market_context GROUP BY symbol,timeframe ORDER BY symbol,timeframe"
        )
    }
    segment_assignments = [
        {
            "symbol": symbol, "timeframe": timeframe, "segment_id": segment_id,
            "row_count": count, "first_timestamp_utc": first, "last_timestamp_utc": last,
        }
        for symbol, timeframe, segment_id, count, first, last in connection.execute(
            "SELECT symbol,timeframe,segment_id,row_count,first_timestamp_utc,last_timestamp_utc "
            "FROM vw_segment_coverage ORDER BY symbol,timeframe,segment_id"
        )
    ]
    required_null_count = connection.execute(
        "SELECT COUNT(*) FROM fact_market_context WHERE "
        "symbol IS NULL OR timeframe IS NULL OR timestamp_utc IS NULL OR close_time_utc IS NULL "
        "OR decision_time_utc IS NULL OR segment_id IS NULL OR open IS NULL OR high IS NULL OR low IS NULL "
        "OR close IS NULL OR volume IS NULL OR quote_asset_volume IS NULL OR number_of_trades IS NULL "
        "OR market_source IS NULL OR market_timestamp_unit IS NULL OR market_quality_status IS NULL "
        "OR context_match_status IS NULL OR context_source IS NULL OR context_asset IS NULL "
        "OR context_source_timestamp_utc IS NULL OR context_available_from_utc_d1 IS NULL "
        "OR context_available_from_utc_d2 IS NULL OR context_price_usd IS NULL "
        "OR context_market_cap_usd IS NULL OR context_tx_count IS NULL "
        "OR context_active_address_count IS NULL OR context_age_seconds IS NULL"
    ).fetchone()[0]
    return {
        "integrity_check": connection.execute("PRAGMA integrity_check").fetchone()[0],
        "foreign_key_violation_count": len(list(connection.execute("PRAGMA foreign_key_check"))),
        "tables": list(tables),
        "views": list(views),
        "schemas": schemas,
        "global_row_counts": {"fact_total": counts["fact_market_context"], "1h": counts["vw_market_context_1h"], "4h": counts["vw_market_context_4h"]},
        "by_asset_timeframe": by_asset_timeframe,
        "primary_key_duplicate_count": next(count for name, count, _ in checks if name == "primary_key_duplicates"),
        "required_null_count": required_null_count,
        "allowed_statuses": {
            "market_quality_status": [row[0] for row in connection.execute("SELECT DISTINCT market_quality_status FROM fact_market_context ORDER BY 1")],
            "context_match_status": [row[0] for row in connection.execute("SELECT DISTINCT context_match_status FROM fact_market_context ORDER BY 1")],
        },
        "future_context_violation_count": next(count for name, count, _ in checks if name == "future_context"),
        "segment_count": counts["dim_segment"],
        "segment_assignments": segment_assignments,
        "data_quality_checks": [
            {"check_name": name, "violation_count": count, "check_status": status}
            for name, count, status in sorted(checks)
        ],
        "object_row_counts": counts,
    }


def inspect_database(database_path: Path) -> tuple[str, dict[str, Any]]:
    uri = f"file:{database_path.as_posix()}?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        foreign_key_violations = list(connection.execute("PRAGMA foreign_key_check"))
        checks = list(connection.execute(
            "SELECT check_name, violation_count, check_status FROM vw_data_quality_checks ORDER BY check_name"
        ))
        fingerprint, counts = logical_database_fingerprint(connection)
        quality = _database_quality_payload(connection, counts, checks)
        quality["integrity_check"] = integrity
        quality["foreign_key_violation_count"] = len(foreign_key_violations)
        return fingerprint, quality
    finally:
        connection.close()


def _create_database(temp_path: Path, project_root: Path, validated: ValidatedSqlInputs) -> tuple[str, dict[str, Any]]:
    schema_sql = (project_root / "sql/001_schema.sql").read_text(encoding="utf-8")
    views_sql = (project_root / "sql/002_views.sql").read_text(encoding="utf-8")
    connection = sqlite3.connect(temp_path)
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.executescript(schema_sql)
        connection.executescript(views_sql)
        connection.execute("BEGIN IMMEDIATE")
        connection.executemany("INSERT INTO dim_asset VALUES (?,?,?,?)", ASSETS)
        connection.executemany("INSERT INTO dim_segment VALUES (?,?,?,?,?,?)", SEGMENTS)
        metadata = {
            "sql_policy_id": SQL_POLICY_ID,
            "sql_schema_version": str(SQL_SCHEMA_VERSION),
            "gate_1": "NOT_EVALUATED",
            "g1_10": "PASS",
            "g1_12": "NOT_EVALUATED",
            **{f"source_sha256:{key}": value for key, value in validated.source_hashes.items()},
        }
        connection.executemany("INSERT INTO pipeline_metadata VALUES (?,?)", sorted(metadata.items()))
        all_rows = sorted(validated.rows_1h + validated.rows_4h, key=lambda row: (row["symbol"], row["timeframe"], row["timestamp_utc"]))
        connection.executemany(INSERT_FACT_SQL, (_fact_tuple(row, key) for key, row in enumerate(all_rows, 1)))
        connection.commit()
        if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise IntegrityError("SQLite-Integritaetspruefung fehlgeschlagen.")
        if list(connection.execute("PRAGMA foreign_key_check")):
            raise IntegrityError("SQLite-Fremdschluesselpruefung fehlgeschlagen.")
        checks = list(connection.execute("SELECT check_name, violation_count, check_status FROM vw_data_quality_checks"))
        if len(checks) != 5 or any(row[1] != 0 or row[2] != "PASS" for row in checks):
            raise IntegrityError("Mindestens eine SQL-Qualitaetspruefung ist fehlgeschlagen.")
        fingerprint, counts = logical_database_fingerprint(connection)
        quality = _database_quality_payload(connection, counts, checks)
        return fingerprint, quality
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def _report_artifacts(
    project_root: Path,
    database_sha256: str,
    logical_fingerprint: str,
    quality: dict[str, Any],
    source_hashes: dict[str, str],
) -> dict[str, bytes]:
    summary = {
        "schema_id": "phase1c_sql_quality_v1",
        "sql_schema_version": SQL_SCHEMA_VERSION,
        "sql_policy_id": SQL_POLICY_ID,
        "build_status": "CREATED",
        "cache_policy": "LOGICALLY_IDENTICAL_READ_ONLY_REUSE",
        "database_path": DATABASE_RELATIVE_PATH,
        "database_sha256": database_sha256,
        "logical_fingerprint": logical_fingerprint,
        "source_hashes": source_hashes,
        "input_validation": {
            "manifest_valid": True,
            "schemas_exact": True,
            "primary_key_duplicates": 0,
            "future_context_rows": 0,
            "excluded_month_rows": 0,
            "segment_count": 5,
            "g1_10": "PASS",
        },
        "quality": quality,
        "gate_1": "NOT_EVALUATED",
        "g1_12": "NOT_EVALUATED",
        "g1_13": "NOT_EVALUATED",
    }
    sql_hashes = {
        "schema_sql": sha256_file(project_root / "sql/001_schema.sql"),
        "views_sql": sha256_file(project_root / "sql/002_views.sql"),
    }
    manifest_rows = [
        {
            "artifact_id": "sqlite_database", "artifact_path": DATABASE_RELATIVE_PATH,
            "artifact_type": "sqlite_database", "schema_version": SQL_SCHEMA_VERSION,
            "row_count": 145_260, "sha256": database_sha256, "logical_fingerprint": logical_fingerprint,
        }
    ] + [
        {
            "artifact_id": key, "artifact_path": f"sql/{'001_schema.sql' if key == 'schema_sql' else '002_views.sql'}",
            "artifact_type": "sql_script", "schema_version": SQL_SCHEMA_VERSION,
            "row_count": "", "sha256": digest, "logical_fingerprint": logical_fingerprint,
        }
        for key, digest in sql_hashes.items()
    ]
    report = f"""# Phase 1C-B SQL-Qualitaetsbericht

## Ergebnis

Der einmalige Offline-Aufbau erzeugte ein reproduzierbares SQLite-Datenmodell mit **145.260** Faktenzeilen. Build-Status: **`CREATED`**. Alle relationalen und fachlichen Prüfungen bestanden. Der logische Fingerprint lautet `{logical_fingerprint}`. Der zusätzliche Datei-Hash lautet `{database_sha256}`.

Die Datenbank ist cache-validierbar: Ein erneuter Lauf darf eine logisch identische Datenbank read-only wiederverwenden. Eine abweichende vorhandene Datenbank wird nicht überschrieben.

## Nachgewiesene Mengen

| Objekt | Zeilen |
|---|---:|
| `fact_market_context` | 145.260 |
| `vw_market_context_1h` | 116.208 |
| `vw_market_context_4h` | 29.052 |
| Assets | 3 |
| Segmente | 5 |

Primärschlüsselduplikate, Join-Verluste, Zukunftskontext, Statusverletzungen und Fremdschlüsselverletzungen: jeweils **0**. `PRAGMA integrity_check` meldet `ok`.

## Methodische Grenze

Das Modell enthält weder Renditen noch Indikatoren, Signale, Positionen oder Backtests. Phase 1C-B liefert technische Evidenz für G1-12; bis zur unabhängigen Abnahme bleibt G1-12 `NOT_EVALUATED`. G1-13 und Gate 1 bleiben ebenfalls `NOT_EVALUATED`.

## Einfache Erklärung

SQLite ist hier eine kontrollierte Analyseschicht über den bereits geprüften CSV-Dateien. Schlüssel und Regeln verhindern doppelte Kerzen, falsche Asset- oder Segmentzuordnungen und Zukunftskontext. Der logische Fingerprint prüft die Daten und das Schema unabhängig von technisch veränderlichen Datenbankbytes.
"""
    dictionary = """# Phase 1C-B SQL-Datenwoerterbuch

## Tabellen

- `dim_asset`: exakt drei Handelspaare mit stabilem technischem Schlüssel.
- `dim_segment`: exakt fünf lückenfreie Analyseabschnitte; die Beschreibung dokumentiert jeden Reset an einer ausgeschlossenen Monatsgrenze.
- `fact_market_context`: eine Zeile je `(symbol, timeframe, timestamp_utc)`; Marktkerze, Entscheidungszeit, Segment, Quelle und D+1-/D+2-Kontext.
- `pipeline_metadata`: Policy-, Schema-, Gate- und Quellhashbindung ohne absolute Pfade oder Zugangsdaten.

Der eindeutige Geschäftsschlüssel `(symbol, timeframe, timestamp_utc)` verhindert doppelte Kerzen. Fremdschlüssel verbinden jede Faktenzeile ausschließlich mit einem erlaubten Asset und einem der fünf dokumentierten Segmente.

## Kern-Views

- `vw_market_context_1h` und `vw_market_context_4h`: geprüfte Zeitschnittansichten.
- `vw_asset_timeframe_coverage`: Menge, erste/letzte Zeit und Segmentzahl je Asset und Zeitrahmen.
- `vw_segment_coverage`: dieselben Nachweise je Segment.
- `vw_context_freshness`: minimale, maximale und mittlere Kontextalterung in Stunden.
- `vw_data_quality_checks`: Duplikate, Join-Abdeckung, Zukunftskontext, Statusdomänen und Fremdschlüssel.

## Schutzregeln

UTC-Zeiten bleiben kanonische ISO-Z-Werte. D+1 muss spätestens zur Entscheidungszeit verfügbar sein; D+2 bleibt getrennt. 4h-Zeilen müssen genau vier 1h-Bestandteile besitzen, während Taker-Felder ausschließlich für 1h gefüllt sind. Ausgeschlossene Monate werden bereits vor dem SQL-Aufbau abgelehnt.

Das festbreite Format `YYYY-MM-DDTHH:MM:SS.ffffffZ` ist lexikografisch zugleich chronologisch sortierbar. SQL ergänzt keine fehlenden Zeilen und setzt keine Zeitreihe über eine Segmentgrenze fort.

Der physische Datei-Hash schützt die konkret erzeugten SQLite-Bytes. Der stabile logische Fingerprint schützt zusätzlich Schemaobjekte, View-Definitionen, sortierte Tabellenzeilen, Objektzählungen und relevante PRAGMA-Einstellungen. Er ist deshalb der maßgebliche Reproduzierbarkeitsnachweis, wenn SQLite intern andere, aber fachlich gleichwertige Bytes erzeugt.
"""
    return {
        "sql_quality_summary.json": _canonical_json_bytes(summary),
        "sql_manifest.csv": _canonical_csv_bytes(MANIFEST_OUTPUT_FIELDS, manifest_rows),
        "PHASE1C_SQL_REPORT.md": report.encode("utf-8"),
        "SQL_DATA_DICTIONARY.md": dictionary.encode("utf-8"),
    }


def _validate_cached_database(
    database_path: Path,
    quality: dict[str, Any],
    validated: ValidatedSqlInputs,
) -> None:
    """Vorhandene Datenbank unabhängig von ihren Berichten an den Vertrag binden."""

    expected_counts = {
        "fact_total": 145_260,
        "1h": 116_208,
        "4h": 29_052,
    }
    if quality.get("integrity_check") != "ok":
        raise IntegrityError("Vorhandene SQLite-Datenbank besteht integrity_check nicht.")
    if quality.get("global_row_counts") != expected_counts:
        raise IntegrityError("Vorhandene SQLite-Datenbank besitzt unerwartete Zeilenzahlen.")
    if quality.get("by_asset_timeframe") != {
        f"{symbol}|{timeframe}": count
        for (symbol, timeframe), count in EXPECTED_ASSET_ROWS.items()
    }:
        raise IntegrityError("Vorhandene SQLite-Datenbank besitzt eine falsche Asset-Zeitrahmen-Matrix.")
    checks = quality.get("data_quality_checks", [])
    expected_check_names = {
        "foreign_key_integrity",
        "future_context",
        "join_coverage",
        "primary_key_duplicates",
        "status_domains",
    }
    if (
        quality.get("foreign_key_violation_count") != 0
        or quality.get("primary_key_duplicate_count") != 0
        or quality.get("required_null_count") != 0
        or quality.get("future_context_violation_count") != 0
        or quality.get("segment_count") != 5
        or quality.get("allowed_statuses") != {
            "market_quality_status": ["accepted_phase1b_complete_month"],
            "context_match_status": ["matched_d1_asof"],
        }
        or {check.get("check_name") for check in checks} != expected_check_names
        or any(
            check.get("check_status") != "PASS" or check.get("violation_count") != 0
            for check in checks
        )
    ):
        raise IntegrityError("Vorhandene SQLite-Datenbank besteht die Pflichtqualitaet nicht.")
    uri = f"file:{database_path.as_posix()}?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    try:
        metadata = dict(connection.execute("SELECT metadata_key, metadata_value FROM pipeline_metadata"))
    finally:
        connection.close()
    expected_metadata = {
        "sql_policy_id": SQL_POLICY_ID,
        "sql_schema_version": str(SQL_SCHEMA_VERSION),
        "gate_1": "NOT_EVALUATED",
        "g1_10": "PASS",
        "g1_12": "NOT_EVALUATED",
        **{f"source_sha256:{key}": value for key, value in validated.source_hashes.items()},
    }
    if metadata != expected_metadata:
        raise IntegrityError("SQLite-Metadaten stimmen nicht mit Policy und validierten Quellen ueberein.")


def _validate_cached_reports(report_dir: Path, expected_artifacts: dict[str, bytes]) -> None:
    """Das gesamte Berichtsbündel gegen unabhängig erzeugte Bytes prüfen."""

    expected_names = set(expected_artifacts)
    if not report_dir.is_dir():
        raise IntegrityError("SQL-Berichtsverzeichnis fehlt.")
    entries = list(report_dir.iterdir())
    if (
        len(entries) != len(expected_names)
        or any(not path.is_file() or path.is_symlink() for path in entries)
        or {path.name for path in entries} != expected_names
    ):
        raise IntegrityError("Cache-Berichte fehlen oder ihr Umfang weicht ab.")
    for name in sorted(expected_names):
        try:
            actual = (report_dir / name).read_bytes()
        except OSError as exc:
            raise IntegrityError(f"Cache-Bericht ist nicht lesbar: {name}") from exc
        if actual != expected_artifacts[name]:
            raise IntegrityError(f"Cache-Bericht weicht bytegenau ab: {name}")


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


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Offline-SQL-Modell fuer Phase 1C-B erstellen.")
    parser.add_argument("--config", default="config/full_import.json")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    project_root = Path.cwd().resolve()
    try:
        result = build_sql_model(project_root, project_root / args.config)
    except (IntegrityError, SafetyError, OSError, sqlite3.Error, ValueError) as exc:
        print(f"SQL-AUFBAU FEHLGESCHLAGEN: {exc}", file=sys.stderr)
        return 1
    print(f"SQL-AUFBAU {result.status}: 145260 Zeilen, logischer Fingerprint {result.logical_fingerprint}")
    print("G1-12: NOT_EVALUATED; Gate 1: NOT_EVALUATED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
