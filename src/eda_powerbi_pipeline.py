"""Phase 1C-C: reproduzierbare EDA- und Power-BI-Ausgaben.

Die Pipeline arbeitet ausschliesslich offline und liest die abgenommene
SQLite-Datenbank unveraenderlich. Alle Ausgaben werden zuerst in einem
temporaeren Verzeichnis erzeugt, vollstaendig geprueft und erst danach ohne
Ueberschreiben veroeffentlicht. Ein vorhandener Cache wird bytegenau geprueft.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import html
import io
import json
import math
import os
import re
import shutil
import sqlite3
import statistics
import sys
import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping, Sequence

from src.full_import import IntegrityError, SafetyError, load_config, sha256_file
from src.sql_pipeline import (
    DATABASE_RELATIVE_PATH,
    EXCLUDED_MONTHS,
    EXPECTED_ASSET_ROWS,
    EXPECTED_ROWS,
    REPORT_ROOT as SQL_REPORT_ROOT,
    SEGMENTS,
    build_sql_model,
    inspect_database,
)


EDA_POLICY_ID = "phase1c_c_descriptive_eda_powerbi_v2"
EDA_SCHEMA_VERSION = 2
EXPECTED_DATABASE_SHA256 = "7f2e5deadd2c3c3e3f1820266f7f7b680def14d6ecda62c8dbbf5a11d9f0033e"
EXPECTED_LOGICAL_FINGERPRINT = "cbf6d93ebb86a591764a4e07327152cba24c2033c9bed57b5bd14e69abf1e367"
EXPECTED_TOTAL_ROWS = 145_260
REPORT_RELATIVE_PATH = "reports/eda"
POWERBI_CONTRACT_ROOT = "powerbi"
POWERBI_EXPORT_RELATIVE_PATH = "data/processed/full_import/powerbi"
GATE_REPORT_RELATIVE_PATH = "reports/full_import/GATE1_ACCEPTANCE_CRITERIA.md"
EXPECTED_SEGMENT_IDS = tuple(f"SEGMENT_{number:03d}" for number in range(1, 6))
RAW_GAP_HOURS = 42
EXCLUDED_CALENDAR_HOURS = 15_264
ACCEPTED_COVERAGE_PERCENT = 88.39
EXCLUDED_COVERAGE_PERCENT = 11.61
EXPECTED_CURRENT_GATE_STATUSES = {
    "G1-01": "PASS",
    "G1-02": "PASS",
    "G1-03": "PASS_WITH_DOCUMENTED_SOURCE_ANOMALIES",
    "G1-04": "PASS",
    "G1-05": "PASS_WITH_DOCUMENTED_SOURCE_ANOMALIES",
    "G1-06": "PASS_WITH_DOCUMENTED_SOURCE_ANOMALIES",
    "G1-07": "PASS",
    "G1-08": "PASS",
    "G1-09": "PASS",
    "G1-10": "PASS",
    "G1-11": "PASS",
    "G1-12": "PASS",
    "G1-13": "PASS",
}
EXPECTED_CURRENT_GATE_1_STATUS = "PASS_WITH_DOCUMENTED_SOURCE_ANOMALIES"

TABLE_FILES = (
    "coverage_by_asset_timeframe_year_segment.csv",
    "descriptive_stats_by_asset_timeframe.csv",
    "annual_activity.csv",
    "segment_comparison.csv",
    "context_metrics_summary.csv",
    "context_age_summary.csv",
    "gaps_and_exclusions.csv",
)
FIGURE_FILES = (
    "annual_row_coverage.svg",
    "annual_median_close.svg",
    "annual_quote_volume.svg",
    "return_distribution_1h.svg",
    "context_age_by_timeframe.svg",
    "segment_coverage.svg",
)
CONTRACT_FILES = (
    "POWER_BI_DATA_CONTRACT.md",
    "POWER_BI_MEASURES.md",
    "powerbi_model_manifest.csv",
)
EXPORT_FILES = (
    "fact_market_context_eda.csv",
    "dim_asset.csv",
    "dim_segment.csv",
    "dim_calendar.csv",
    "dim_timeframe.csv",
)

FACT_EXPORT_FIELDS = (
    "market_context_key", "asset_key", "segment_key", "date_key", "timeframe_key",
    "symbol", "timeframe", "timestamp_utc", "close_time_utc", "decision_time_utc",
    "segment_id", "open", "high", "low", "close", "volume", "quote_asset_volume",
    "number_of_trades", "taker_buy_base_volume", "taker_buy_quote_volume",
    "constituent_rows", "market_source", "market_quality_status", "context_match_status",
    "context_source_timestamp_utc", "context_available_from_utc_d1",
    "context_available_from_utc_d2", "context_price_usd", "context_market_cap_usd",
    "context_tx_count", "context_active_address_count", "context_age_hours",
    "context_age_since_d1_hours",
    "calendar_year", "calendar_month", "candle_body_return", "candle_range",
    "upper_wick_relative", "lower_wick_relative", "taker_buy_share",
    "close_to_close_return",
)
CALENDAR_EXPORT_FIELDS = (
    "date_key", "calendar_date", "calendar_year", "calendar_quarter",
    "quarter_label", "calendar_month", "day_of_month", "month_name_de",
    "month_label", "year_month_sort", "month_name_sort", "is_accepted_date",
    "is_excluded_month", "exclusion_status", "exclusion_reason",
)
PROHIBITED_FIELD_TOKENS = (
    "forward_return", "lead", "label", "signal", "position"
)
METRICS = (
    "close_price_usd", "candle_body_return", "candle_range", "upper_wick_relative",
    "lower_wick_relative", "base_volume", "quote_volume", "trade_count",
    "taker_buy_share", "close_to_close_return", "context_age_hours",
    "context_age_since_d1_hours",
)
CONTEXT_METRICS = (
    "context_price_usd", "context_market_cap_usd", "context_tx_count",
    "context_active_address_count",
)


@dataclass(frozen=True)
class InputEvidence:
    database_path: Path
    database_sha256: str
    logical_fingerprint: str
    sql_cache_status: str
    quality: dict[str, Any]
    gate_statuses: dict[str, str]
    protected_hashes: dict[str, str]


@dataclass(frozen=True)
class PipelineResult:
    status: str
    report_root: Path
    export_root: Path
    database_sha256: str
    logical_fingerprint: str
    fact_rows: int
    export_hashes: dict[str, str]
    gate_statuses: dict[str, str]


@dataclass
class CoverageAccumulator:
    row_count: int = 0
    first_timestamp_utc: str = ""
    last_timestamp_utc: str = ""

    def add(self, timestamp_utc: str) -> None:
        self.row_count += 1
        if not self.first_timestamp_utc or timestamp_utc < self.first_timestamp_utc:
            self.first_timestamp_utc = timestamp_utc
        if not self.last_timestamp_utc or timestamp_utc > self.last_timestamp_utc:
            self.last_timestamp_utc = timestamp_utc


@dataclass
class AnnualAccumulator:
    row_count: int = 0
    volume_sum: float = 0.0
    quote_volume_sum: float = 0.0
    trade_count_sum: int = 0
    closes: list[float] | None = None

    def __post_init__(self) -> None:
        if self.closes is None:
            self.closes = []

    def add(self, volume: float, quote_volume: float, trades: int, close: float) -> None:
        self.row_count += 1
        self.volume_sum += volume
        self.quote_volume_sum += quote_volume
        self.trade_count_sum += trades
        assert self.closes is not None
        self.closes.append(close)


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


def _canonical_csv_bytes(fields: Sequence[str], rows: Iterable[Mapping[str, Any]]) -> bytes:
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=list(fields), lineterminator="\n", extrasaction="raise")
    writer.writeheader()
    for row in rows:
        writer.writerow({field: _csv_value(row.get(field)) for field in fields})
    return buffer.getvalue().encode("utf-8")


def _csv_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, float):
        if not math.isfinite(value):
            raise IntegrityError("Nicht-endlicher Wert darf nicht serialisiert werden.")
        return format(value, ".15g")
    return str(value)


def _format_de_number(value: float | int, decimals: int = 0) -> str:
    raw = f"{value:,.{decimals}f}"
    return raw.replace(",", "_").replace(".", ",").replace("_", ".")


def _parse_utc(value: str) -> datetime:
    if len(value) != 27 or not value.endswith("Z"):
        raise IntegrityError(f"Nicht-kanonischer UTC-Zeitstempel: {value}")
    parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    if parsed.tzinfo != timezone.utc:
        raise IntegrityError(f"Zeitstempel ist nicht UTC: {value}")
    return parsed


def _quantile(sorted_values: Sequence[float], probability: float) -> float | None:
    if not sorted_values:
        return None
    if not 0.0 <= probability <= 1.0:
        raise ValueError("Quantilwahrscheinlichkeit ausserhalb [0,1].")
    position = (len(sorted_values) - 1) * probability
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return sorted_values[lower]
    weight = position - lower
    return sorted_values[lower] * (1.0 - weight) + sorted_values[upper] * weight


def descriptive_statistics(values: Iterable[float | None], total_count: int | None = None) -> dict[str, Any]:
    finite = []
    observed = 0
    for value in values:
        observed += 1
        if value is None:
            continue
        number = float(value)
        if not math.isfinite(number):
            raise IntegrityError("Nicht-endlicher EDA-Wert.")
        finite.append(number)
    finite.sort()
    denominator = observed if total_count is None else total_count
    if denominator < len(finite):
        raise IntegrityError("Statistik-Nenner ist kleiner als die Wertanzahl.")
    if not finite:
        return {
            "count": 0, "mean": None, "std": None, "min": None, "q25": None,
            "median": None, "q75": None, "max": None, "null_count": denominator,
        }
    result = {
        "count": len(finite),
        "mean": statistics.fmean(finite),
        "std": statistics.stdev(finite) if len(finite) > 1 else 0.0,
        "min": finite[0],
        "q25": _quantile(finite, 0.25),
        "median": _quantile(finite, 0.50),
        "q75": _quantile(finite, 0.75),
        "max": finite[-1],
        "null_count": denominator - len(finite),
    }
    if not (result["min"] <= result["q25"] <= result["median"] <= result["q75"] <= result["max"]):
        raise IntegrityError("Quantile sind nicht monoton.")
    return result


def _independent_logical_fingerprint(connection: sqlite3.Connection) -> tuple[str, dict[str, int]]:
    """Eigenstaendige Neuberechnung ohne Verwendung des SQL-Berichtscaches."""

    objects = [
        {"type": row[0], "name": row[1], "table": row[2], "sql": row[3]}
        for row in connection.execute(
            "SELECT type,name,tbl_name,sql FROM sqlite_schema "
            "WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name"
        )
    ]
    table_hashes: dict[str, str] = {}
    counts: dict[str, int] = {}
    for table in ("pipeline_metadata", "dim_asset", "dim_segment", "fact_market_context"):
        columns = [row[1] for row in connection.execute(f"PRAGMA table_info({table})")]
        if not columns:
            raise IntegrityError(f"Pflichttabelle fehlt: {table}")
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
    digest = hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return digest, counts


def _current_gate_statuses(report_path: Path) -> dict[str, str]:
    text = report_path.read_text(encoding="utf-8")
    marker = "## Gate-1-Teilmatrix"
    if text.count(marker) != 1:
        raise IntegrityError("Aktuelle Gate-1-Teilmatrix fehlt oder ist mehrfach vorhanden.")
    section = text.split(marker, 1)[1]
    section = section.split("\n## ", 1)[0]
    statuses: dict[str, str] = {}
    for line in section.splitlines():
        if not line.startswith("| G1-"):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) != 4:
            raise IntegrityError("Gate-1-Teilmatrix besitzt ein unerwartetes Schema.")
        gate_id = cells[0]
        if gate_id not in EXPECTED_CURRENT_GATE_STATUSES:
            raise IntegrityError(f"Gate-1-Teilmatrix enthaelt ein unbekanntes Kriterium: {gate_id}")
        if gate_id in statuses:
            raise IntegrityError(f"Gate-1-Teilmatrix enthaelt ein doppeltes Kriterium: {gate_id}")
        statuses[gate_id] = cells[3]
    if set(statuses) != set(EXPECTED_CURRENT_GATE_STATUSES):
        missing = sorted(set(EXPECTED_CURRENT_GATE_STATUSES) - set(statuses))
        raise IntegrityError(f"Gate-1-Teilmatrix ist unvollstaendig: fehlend={missing}")
    if statuses != EXPECTED_CURRENT_GATE_STATUSES:
        raise IntegrityError(f"Aktuelle Gate-Matrix ist fuer Phase 1C-C nicht freigegeben: {statuses}")
    overall_matches = re.findall(
        r"^\*\*Gesamtstatus Gate 1: `([^`]+)`\.\*\*$",
        text,
        flags=re.MULTILINE,
    )
    if overall_matches != [EXPECTED_CURRENT_GATE_1_STATUS]:
        raise IntegrityError(f"Gesamtstatus Gate 1 ist fehlend, mehrfach oder widerspruechlich: {overall_matches}")
    return {
        **{key: statuses[key] for key in sorted(statuses)},
        "Gate 1": EXPECTED_CURRENT_GATE_1_STATUS,
    }


def _snapshot_files(paths: Iterable[Path]) -> dict[str, str]:
    return {path.as_posix(): sha256_file(path) for path in sorted(paths, key=lambda item: item.as_posix())}


def _protected_evidence_paths(project_root: Path) -> list[Path]:
    paths = [
        project_root / "data/processed/full_import/market_context_1h.csv",
        project_root / "data/processed/full_import/market_context_4h.csv",
        project_root / "reports/full_import/raw_manifest.csv",
        project_root / "reports/full_import/binance_quality_summary.csv",
        project_root / "reports/full_import/source_anomalies.csv",
        project_root / "reports/full_import/coinmetrics_quality_summary.json",
        project_root / "reports/full_import/execution_checkpoint.json",
        project_root / "reports/processed/join_quality_summary.json",
        project_root / "reports/processed/processed_manifest.csv",
        project_root / "reports/processed/PHASE1C_QUALITY_REPORT.md",
        project_root / "reports/processed/PHASE1C_DATA_DICTIONARY.md",
    ]
    paths.extend(sorted(path for path in (project_root / "data/raw/full_import").rglob("*") if path.is_file()))
    paths.extend(sorted(path for path in (project_root / "data/interim/full_import").rglob("*") if path.is_file()))
    sql_root = project_root / SQL_REPORT_ROOT
    paths.extend(sorted(path for path in sql_root.iterdir() if path.is_file()))
    return paths


def validate_inputs(project_root: Path, config_path: Path) -> InputEvidence:
    """Alle Eingaben read-only und vor jeder Ausgabemutation validieren."""

    project_root = project_root.resolve()
    config_path = _inside_project(project_root, config_path)
    config = load_config(config_path, project_root)
    if tuple(config["binance"]["assets"]) != ("BTCUSDT", "ETHUSDT", "SOLUSDT"):
        raise IntegrityError("Assetumfang der sicheren Konfiguration weicht ab.")
    database_path = _inside_project(project_root, project_root / DATABASE_RELATIVE_PATH)
    sql_report_root = _inside_project(project_root, project_root / SQL_REPORT_ROOT)
    if not database_path.is_file() or not sql_report_root.is_dir():
        raise IntegrityError("Abgenommener SQL-Cache ist unvollstaendig; kein Neuaufbau erlaubt.")
    protected_paths = _protected_evidence_paths(project_root)
    if any(not path.is_file() for path in protected_paths):
        raise IntegrityError("Ein frueherer Buildnachweis fehlt.")
    protected_before = _snapshot_files(protected_paths)
    database_hash_before = sha256_file(database_path)
    if database_hash_before != EXPECTED_DATABASE_SHA256:
        raise IntegrityError("SQLite-Dateihash weicht von der Abnahme ab.")

    uri = f"file:{database_path.as_posix()}?mode=ro&immutable=1"
    connection = sqlite3.connect(uri, uri=True)
    try:
        connection.execute("PRAGMA query_only = ON")
        connection.execute("PRAGMA foreign_keys = ON")
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        foreign_key_violations = list(connection.execute("PRAGMA foreign_key_check"))
        independent_fingerprint, counts = _independent_logical_fingerprint(connection)
        row_count = connection.execute("SELECT COUNT(*) FROM fact_market_context").fetchone()[0]
        matrix = {
            (symbol, timeframe): count
            for symbol, timeframe, count in connection.execute(
                "SELECT symbol,timeframe,COUNT(*) FROM fact_market_context "
                "GROUP BY symbol,timeframe ORDER BY symbol,timeframe"
            )
        }
        duplicate_count = connection.execute(
            "SELECT COUNT(*) FROM (SELECT symbol,timeframe,timestamp_utc FROM fact_market_context "
            "GROUP BY symbol,timeframe,timestamp_utc HAVING COUNT(*) > 1)"
        ).fetchone()[0]
        future_count = connection.execute(
            "SELECT COUNT(*) FROM fact_market_context "
            "WHERE context_available_from_utc_d1 > decision_time_utc"
        ).fetchone()[0]
        segment_ids = tuple(
            row[0] for row in connection.execute("SELECT segment_id FROM dim_segment ORDER BY segment_key")
        )
        excluded_count = connection.execute(
            "SELECT COUNT(*) FROM fact_market_context WHERE substr(timestamp_utc,1,7) IN ("
            + ",".join("?" for _ in EXCLUDED_MONTHS) + ")",
            tuple(sorted(EXCLUDED_MONTHS)),
        ).fetchone()[0]
        orphan_count = connection.execute(
            "SELECT COUNT(*) FROM fact_market_context AS f "
            "LEFT JOIN dim_asset AS a ON a.asset_key=f.asset_key AND a.symbol=f.symbol "
            "LEFT JOIN dim_segment AS s ON s.segment_key=f.segment_key AND s.segment_id=f.segment_id "
            "WHERE a.asset_key IS NULL OR s.segment_key IS NULL"
        ).fetchone()[0]
    finally:
        connection.close()

    if independent_fingerprint != EXPECTED_LOGICAL_FINGERPRINT:
        raise IntegrityError("Unabhaengig berechneter SQL-Fingerprint weicht ab.")
    if integrity != "ok" or foreign_key_violations:
        raise IntegrityError("SQLite-Integritaet oder Fremdschluesselpruefung fehlgeschlagen.")
    if row_count != EXPECTED_TOTAL_ROWS or counts.get("fact_market_context") != EXPECTED_TOTAL_ROWS:
        raise IntegrityError("SQL-Faktenzeilenzahl weicht ab.")
    if matrix != EXPECTED_ASSET_ROWS:
        raise IntegrityError("SQL-Asset-/Zeitrahmen-Matrix weicht ab.")
    if duplicate_count or future_count or excluded_count or orphan_count:
        raise IntegrityError("SQL-Schluessel-, Leakage- oder Ausschlusspruefung fehlgeschlagen.")
    if segment_ids != EXPECTED_SEGMENT_IDS:
        raise IntegrityError("SQL-Segmentvertrag weicht ab.")

    gate_statuses = _current_gate_statuses(project_root / GATE_REPORT_RELATIVE_PATH)
    sql_result = build_sql_model(project_root, config_path)
    if sql_result.status != "CACHED_VALID":
        raise IntegrityError("SQL-Basis wurde nicht ausschliesslich als CACHED_VALID gelesen.")
    independently_reported_fingerprint, quality = inspect_database(database_path)
    if independently_reported_fingerprint != independent_fingerprint:
        raise IntegrityError("Zwei unabhaengige Fingerprintpfade widersprechen sich.")
    if quality.get("integrity_check") != "ok" or quality.get("future_context_violation_count") != 0:
        raise IntegrityError("SQL-Qualitaetspruefung ist nicht bestanden.")
    if sha256_file(database_path) != database_hash_before:
        raise IntegrityError("SQLite-Datei wurde waehrend der Eingabepruefung veraendert.")
    if _snapshot_files(protected_paths) != protected_before:
        raise IntegrityError("Fruehere Buildnachweise wurden waehrend der Eingabepruefung veraendert.")
    return InputEvidence(
        database_path=database_path,
        database_sha256=database_hash_before,
        logical_fingerprint=independent_fingerprint,
        sql_cache_status=sql_result.status,
        quality=quality,
        gate_statuses=gate_statuses,
        protected_hashes=protected_before,
    )


def _readonly_connection(database_path: Path) -> sqlite3.Connection:
    uri = f"file:{database_path.as_posix()}?mode=ro&immutable=1"
    connection = sqlite3.connect(uri, uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only = ON")
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def _fact_rows(connection: sqlite3.Connection) -> Iterator[sqlite3.Row]:
    return iter(connection.execute(
        "SELECT * FROM fact_market_context ORDER BY symbol,timeframe,timestamp_utc"
    ))


def _derived_values(row: Mapping[str, Any], previous: Mapping[str, Any] | None) -> dict[str, float | None]:
    open_price = float(row["open"])
    high = float(row["high"])
    low = float(row["low"])
    close = float(row["close"])
    body = close / open_price - 1.0
    candle_range = high / low - 1.0
    upper_wick = (high - max(open_price, close)) / open_price
    lower_wick = (min(open_price, close) - low) / open_price
    taker_share = None
    if row["taker_buy_quote_volume"] is not None and float(row["quote_asset_volume"]) > 0:
        taker_share = float(row["taker_buy_quote_volume"]) / float(row["quote_asset_volume"])
    close_return = None
    if previous is not None:
        same_group = (
            previous["symbol"] == row["symbol"]
            and previous["timeframe"] == row["timeframe"]
            and previous["segment_id"] == row["segment_id"]
        )
        interval_hours = 1 if row["timeframe"] == "1h" else 4
        exact_interval = (
            _parse_utc(str(row["timestamp_utc"])) - _parse_utc(str(previous["timestamp_utc"]))
        ).total_seconds() == interval_hours * 3600
        if same_group and exact_interval:
            close_return = close / float(previous["close"]) - 1.0
    return {
        "candle_body_return": body,
        "candle_range": candle_range,
        "upper_wick_relative": upper_wick,
        "lower_wick_relative": lower_wick,
        "taker_buy_share": taker_share,
        "close_to_close_return": close_return,
    }


def _context_age_values(row: Mapping[str, Any]) -> tuple[float, float]:
    decision_time = _parse_utc(str(row["decision_time_utc"]))
    source_time = _parse_utc(str(row["context_source_timestamp_utc"]))
    available_d1 = _parse_utc(str(row["context_available_from_utc_d1"]))
    source_age_hours = (decision_time - source_time).total_seconds() / 3600.0
    context_age_since_d1_hours = (
        decision_time - available_d1
    ).total_seconds() / 3600.0
    recorded_source_age_hours = float(row["context_age_seconds"]) / 3600.0
    if not math.isclose(source_age_hours, recorded_source_age_hours, abs_tol=1e-9):
        raise IntegrityError("SQL-Kontextalter widerspricht Decision Time minus Quellzeitpunkt.")
    if source_age_hours < 0 or context_age_since_d1_hours < 0:
        raise IntegrityError("Kontextalter darf nicht negativ sein.")
    return source_age_hours, context_age_since_d1_hours


def _fact_export_row(row: Mapping[str, Any], derived: Mapping[str, Any]) -> dict[str, Any]:
    timestamp = str(row["timestamp_utc"])
    date_key = int(timestamp[0:4] + timestamp[5:7] + timestamp[8:10])
    context_age_hours, context_age_since_d1_hours = _context_age_values(row)
    return {
        "market_context_key": row["market_context_key"],
        "asset_key": row["asset_key"],
        "segment_key": row["segment_key"],
        "date_key": date_key,
        "timeframe_key": 1 if row["timeframe"] == "1h" else 4,
        "symbol": row["symbol"],
        "timeframe": row["timeframe"],
        "timestamp_utc": timestamp,
        "close_time_utc": row["close_time_utc"],
        "decision_time_utc": row["decision_time_utc"],
        "segment_id": row["segment_id"],
        "open": row["open"], "high": row["high"], "low": row["low"], "close": row["close"],
        "volume": row["volume"], "quote_asset_volume": row["quote_asset_volume"],
        "number_of_trades": row["number_of_trades"],
        "taker_buy_base_volume": row["taker_buy_base_volume"],
        "taker_buy_quote_volume": row["taker_buy_quote_volume"],
        "constituent_rows": row["constituent_rows"],
        "market_source": row["market_source"],
        "market_quality_status": row["market_quality_status"],
        "context_match_status": row["context_match_status"],
        "context_source_timestamp_utc": row["context_source_timestamp_utc"],
        "context_available_from_utc_d1": row["context_available_from_utc_d1"],
        "context_available_from_utc_d2": row["context_available_from_utc_d2"],
        "context_price_usd": row["context_price_usd"],
        "context_market_cap_usd": row["context_market_cap_usd"],
        "context_tx_count": row["context_tx_count"],
        "context_active_address_count": row["context_active_address_count"],
        "context_age_hours": context_age_hours,
        "context_age_since_d1_hours": context_age_since_d1_hours,
        "calendar_year": int(timestamp[0:4]),
        "calendar_month": int(timestamp[5:7]),
        **derived,
    }


def _write_csv(path: Path, fields: Sequence[str], rows: Iterable[Mapping[str, Any]]) -> int:
    count = 0
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(fields), lineterminator="\n", extrasaction="raise")
        writer.writeheader()
        for row in rows:
            writer.writerow({field: _csv_value(row.get(field)) for field in fields})
            count += 1
    return count


def _write_fact_and_collect(connection: sqlite3.Connection, export_path: Path) -> dict[str, Any]:
    metric_values: dict[tuple[str, str, str], list[float | None]] = defaultdict(list)
    coverage: dict[tuple[str, str, int, str], CoverageAccumulator] = defaultdict(CoverageAccumulator)
    annual: dict[tuple[str, str, int], AnnualAccumulator] = defaultdict(AnnualAccumulator)
    segment_values: dict[tuple[str, str, str], dict[str, Any]] = defaultdict(
        lambda: {"coverage": CoverageAccumulator(), "closes": [], "volumes": [], "ranges": [], "returns": []}
    )
    context_rows: dict[str, tuple[float, float, float, float]] = {}
    context_age_values: dict[tuple[str, str], list[float]] = defaultdict(list)
    context_age_since_d1_values: dict[tuple[str, str], list[float]] = defaultdict(list)
    calendar_dates: set[str] = set()
    group_counts: dict[tuple[str, str], int] = defaultdict(int)
    return_null_count = 0
    segment_start_null_count = 0
    previous: Mapping[str, Any] | None = None
    total = 0

    with export_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(FACT_EXPORT_FIELDS), lineterminator="\n")
        writer.writeheader()
        for row in _fact_rows(connection):
            derived = _derived_values(row, previous)
            export_row = _fact_export_row(row, derived)
            writer.writerow({field: _csv_value(export_row[field]) for field in FACT_EXPORT_FIELDS})
            total += 1
            symbol = str(row["symbol"])
            timeframe = str(row["timeframe"])
            segment_id = str(row["segment_id"])
            timestamp = str(row["timestamp_utc"])
            year = int(timestamp[:4])
            calendar_dates.add(timestamp[:10])
            group_counts[(symbol, timeframe)] += 1
            coverage[(symbol, timeframe, year, segment_id)].add(timestamp)
            annual[(symbol, timeframe, year)].add(
                float(row["volume"]), float(row["quote_asset_volume"]), int(row["number_of_trades"]), float(row["close"])
            )
            segment_bucket = segment_values[(symbol, timeframe, segment_id)]
            segment_bucket["coverage"].add(timestamp)
            segment_bucket["closes"].append(float(row["close"]))
            segment_bucket["volumes"].append(float(row["volume"]))
            segment_bucket["ranges"].append(float(derived["candle_range"]))
            segment_bucket["returns"].append(derived["close_to_close_return"])

            context_key = str(row["context_source_timestamp_utc"])
            context_tuple = (
                float(row["context_price_usd"]), float(row["context_market_cap_usd"]),
                float(row["context_tx_count"]), float(row["context_active_address_count"]),
            )
            if context_key in context_rows and context_rows[context_key] != context_tuple:
                raise IntegrityError("Coin-Metrics-Kontext ist fuer denselben Quellzeitpunkt widerspruechlich.")
            context_rows[context_key] = context_tuple
            age_hours = float(row["context_age_seconds"]) / 3600.0
            age_since_d1_hours = float(export_row["context_age_since_d1_hours"])
            context_age_values[(symbol, timeframe)].append(age_hours)
            context_age_since_d1_values[(symbol, timeframe)].append(age_since_d1_hours)

            values = {
                "close_price_usd": float(row["close"]),
                "candle_body_return": derived["candle_body_return"],
                "candle_range": derived["candle_range"],
                "upper_wick_relative": derived["upper_wick_relative"],
                "lower_wick_relative": derived["lower_wick_relative"],
                "base_volume": float(row["volume"]),
                "quote_volume": float(row["quote_asset_volume"]),
                "trade_count": float(row["number_of_trades"]),
                "taker_buy_share": derived["taker_buy_share"],
                "close_to_close_return": derived["close_to_close_return"],
                "context_age_hours": age_hours,
                "context_age_since_d1_hours": age_since_d1_hours,
            }
            for metric, value in values.items():
                metric_values[(symbol, timeframe, metric)].append(value)
            if derived["close_to_close_return"] is None:
                return_null_count += 1
                if previous is None or previous["symbol"] != symbol or previous["timeframe"] != timeframe or previous["segment_id"] != segment_id:
                    segment_start_null_count += 1
            previous = row

    return {
        "total_rows": total,
        "metric_values": metric_values,
        "coverage": coverage,
        "annual": annual,
        "segment_values": segment_values,
        "context_rows": context_rows,
        "context_age_values": context_age_values,
        "context_age_since_d1_values": context_age_since_d1_values,
        "calendar_dates": calendar_dates,
        "group_counts": group_counts,
        "return_null_count": return_null_count,
        "segment_start_null_count": segment_start_null_count,
    }


def _coverage_rows(collected: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for (symbol, timeframe, year, segment_id), item in sorted(collected["coverage"].items()):
        interval = 1 if timeframe == "1h" else 4
        expected = int((_parse_utc(item.last_timestamp_utc) - _parse_utc(item.first_timestamp_utc)).total_seconds() / 3600 / interval) + 1
        rows.append({
            "symbol": symbol, "timeframe": timeframe, "calendar_year": year,
            "segment_id": segment_id, "row_count": item.row_count,
            "first_timestamp_utc": item.first_timestamp_utc,
            "last_timestamp_utc": item.last_timestamp_utc,
            "expected_rows_between_observed_bounds": expected,
            "coverage_within_observed_bounds_percent": 100.0 * item.row_count / expected,
        })
    return rows


def _descriptive_rows(collected: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for (symbol, timeframe, metric), values in sorted(collected["metric_values"].items()):
        rows.append({"symbol": symbol, "timeframe": timeframe, "metric": metric, **descriptive_statistics(values)})
    return rows


def _annual_rows(collected: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for (symbol, timeframe, year), item in sorted(collected["annual"].items()):
        assert item.closes is not None
        rows.append({
            "symbol": symbol, "timeframe": timeframe, "calendar_year": year,
            "row_count": item.row_count, "base_volume_sum": item.volume_sum,
            "quote_volume_sum": item.quote_volume_sum, "trade_count_sum": item.trade_count_sum,
            "base_volume_mean": item.volume_sum / item.row_count,
            "quote_volume_mean": item.quote_volume_sum / item.row_count,
            "trade_count_mean": item.trade_count_sum / item.row_count,
            "close_median_usd": descriptive_statistics(item.closes)["median"],
        })
    return rows


def _segment_rows(collected: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for (symbol, timeframe, segment_id), item in sorted(collected["segment_values"].items()):
        coverage: CoverageAccumulator = item["coverage"]
        close_stats = descriptive_statistics(item["closes"])
        volume_stats = descriptive_statistics(item["volumes"])
        range_stats = descriptive_statistics(item["ranges"])
        return_stats = descriptive_statistics(item["returns"])
        rows.append({
            "symbol": symbol, "timeframe": timeframe, "segment_id": segment_id,
            "row_count": coverage.row_count, "first_timestamp_utc": coverage.first_timestamp_utc,
            "last_timestamp_utc": coverage.last_timestamp_utc,
            "close_min_usd": close_stats["min"], "close_max_usd": close_stats["max"],
            "base_volume_median": volume_stats["median"], "candle_range_median": range_stats["median"],
            "close_to_close_return_std": return_stats["std"],
            "close_to_close_return_null_count": return_stats["null_count"],
        })
    return rows


def _context_metric_rows(collected: Mapping[str, Any]) -> list[dict[str, Any]]:
    by_metric = {metric: [] for metric in CONTEXT_METRICS}
    for values in collected["context_rows"].values():
        for index, metric in enumerate(CONTEXT_METRICS):
            by_metric[metric].append(values[index])
    return [
        {"context_asset": "btc", "observation_grain": "unique_context_source_timestamp", "metric": metric,
         **descriptive_statistics(values)}
        for metric, values in sorted(by_metric.items())
    ]


def _context_age_rows(collected: Mapping[str, Any]) -> list[dict[str, Any]]:
    source_age_rows = [
        {"symbol": symbol, "timeframe": timeframe, "metric": "context_age_hours", **descriptive_statistics(values)}
        for (symbol, timeframe), values in sorted(collected["context_age_values"].items())
    ]
    d1_age_rows = [
        {"symbol": symbol, "timeframe": timeframe, "metric": "context_age_since_d1_hours", **descriptive_statistics(values)}
        for (symbol, timeframe), values in sorted(collected["context_age_since_d1_values"].items())
    ]
    return sorted(source_age_rows + d1_age_rows, key=lambda row: (row["symbol"], row["timeframe"], row["metric"]))


def _validate_context_age_controls(collected: Mapping[str, Any]) -> dict[str, dict[str, float]]:
    expected = {
        ("context_age_hours", "1h"): {"min": 24.0, "median": 35.5, "max": 47.0},
        ("context_age_hours", "4h"): {"min": 24.0, "median": 34.0, "max": 44.0},
        ("context_age_since_d1_hours", "1h"): {"min": 0.0, "median": 11.5, "max": 23.0},
        ("context_age_since_d1_hours", "4h"): {"min": 0.0, "median": 10.0, "max": 20.0},
    }
    sources = {
        "context_age_hours": collected["context_age_values"],
        "context_age_since_d1_hours": collected["context_age_since_d1_values"],
    }
    controls: dict[str, dict[str, float]] = {}
    for (metric, timeframe), expected_stats in expected.items():
        observed_per_asset = []
        for symbol in ("BTCUSDT", "ETHUSDT", "SOLUSDT"):
            stats = descriptive_statistics(sources[metric][(symbol, timeframe)])
            observed = {key: float(stats[key]) for key in ("min", "median", "max")}
            if any(not math.isclose(observed[key], expected_stats[key], abs_tol=1e-9) for key in expected_stats):
                raise IntegrityError(f"Kontextalter-Kontrollwerte weichen ab: {metric}|{symbol}|{timeframe}|{observed}")
            observed_per_asset.append(observed)
        if any(item != observed_per_asset[0] for item in observed_per_asset[1:]):
            raise IntegrityError(f"Kontextalter unterscheidet sich unerwartet zwischen Assets: {metric}|{timeframe}")
        controls[f"{metric}|{timeframe}"] = observed_per_asset[0]
    return controls


def _gap_rows() -> list[dict[str, Any]]:
    rows = [
        {"category": "actual_source_gap_hours", "value": RAW_GAP_HOURS, "unit": "asset-hours", "scope": "real missing Binance source rows"},
        {"category": "conservative_excluded_calendar_hours", "value": EXCLUDED_CALENDAR_HOURS, "unit": "asset-hours", "scope": "21 excluded asset-months"},
        {"category": "accepted_temporal_coverage", "value": ACCEPTED_COVERAGE_PERCENT, "unit": "percent", "scope": "159 accepted asset-months"},
        {"category": "excluded_temporal_coverage", "value": EXCLUDED_COVERAGE_PERCENT, "unit": "percent", "scope": "21 excluded asset-months"},
        {"category": "accepted_asset_months", "value": 159, "unit": "asset-months", "scope": "three assets"},
        {"category": "excluded_asset_months", "value": 21, "unit": "asset-months", "scope": "seven calendar months across three assets"},
        {"category": "accepted_1h_rows", "value": EXPECTED_ROWS["1h"], "unit": "rows", "scope": "SQL fact"},
        {"category": "accepted_4h_rows", "value": EXPECTED_ROWS["4h"], "unit": "rows", "scope": "SQL fact"},
    ]
    rows.extend(
        {"category": "excluded_calendar_month", "value": month, "unit": "YYYY-MM", "scope": "all three assets"}
        for month in sorted(EXCLUDED_MONTHS)
    )
    return rows


def _xml(value: Any) -> str:
    return html.escape(str(value), quote=True)


PALETTE = ("#2463A7", "#D49A18", "#D56A2B", "#6E7F35", "#B14F80")
LINE_STYLES = ("", "8 4", "3 3", "10 3 2 3", "2 4")


def _svg_frame(title: str, subtitle: str, x_label: str, y_label: str, body: str, source_note: str) -> bytes:
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" width="960" height="600" viewBox="0 0 960 600" '
        f'data-source="{_xml(source_note)}">\n'
        f'<title>{_xml(title)}</title>\n<desc>{_xml(subtitle)}. Quelle: {_xml(source_note)}</desc>\n'
        '<rect width="960" height="600" fill="#FAFBFC"/>\n'
        f'<text x="70" y="42" font-family="Arial" font-size="22" fill="#20242A">{_xml(title)}</text>\n'
        f'<text x="70" y="66" font-family="Arial" font-size="13" fill="#59616B">{_xml(subtitle)}</text>\n'
        '<line x1="90" y1="500" x2="900" y2="500" stroke="#303840" stroke-width="1.2"/>\n'
        '<line x1="90" y1="95" x2="90" y2="500" stroke="#303840" stroke-width="1.2"/>\n'
        + body +
        f'<text x="495" y="552" text-anchor="middle" font-family="Arial" font-size="13" fill="#303840">{_xml(x_label)}</text>\n'
        f'<text x="22" y="300" text-anchor="middle" transform="rotate(-90 22 300)" font-family="Arial" font-size="13" fill="#303840">{_xml(y_label)}</text>\n'
        f'<text x="70" y="582" font-family="Arial" font-size="11" fill="#59616B">Quelle: {_xml(source_note)} | Ausgeschlossen: 7 Monate je Asset; 88,39 % akzeptierte Abdeckung.</text>\n'
        '</svg>\n'
    ).encode("utf-8")


def _svg_bar_chart(title: str, subtitle: str, categories: Sequence[str], series: Mapping[str, Sequence[float]], y_label: str) -> bytes:
    maximum = max((value for values in series.values() for value in values), default=1.0) or 1.0
    plot_height = 385.0
    plot_width = 790.0
    group_width = plot_width / max(len(categories), 1)
    bar_width = min(36.0, group_width * 0.72 / max(len(series), 1))
    parts = []
    for grid_index in range(5):
        value = maximum * grid_index / 4
        y = 500 - plot_height * grid_index / 4
        parts.append(f'<line x1="90" y1="{y:.2f}" x2="900" y2="{y:.2f}" stroke="#D9DEE4" stroke-width="1"/>')
        parts.append(f'<text x="82" y="{y + 4:.2f}" text-anchor="end" font-family="Arial" font-size="10" fill="#59616B">{_xml(format(value, ".3g"))}</text>')
    for category_index, category in enumerate(categories):
        center = 100 + group_width * (category_index + 0.5)
        parts.append(f'<text x="{center:.2f}" y="520" text-anchor="middle" font-family="Arial" font-size="11" fill="#303840">{_xml(category)}</text>')
        for series_index, (label, values) in enumerate(series.items()):
            value = float(values[category_index])
            height = plot_height * value / maximum
            x = center - len(series) * bar_width / 2 + series_index * bar_width
            y = 500 - height
            parts.append(f'<rect x="{x:.2f}" y="{y:.2f}" width="{bar_width - 2:.2f}" height="{height:.2f}" fill="{PALETTE[series_index % len(PALETTE)]}" stroke="#303840" stroke-width="0.5"/>')
    for index, label in enumerate(series):
        x = 630 + (index % 3) * 105
        y = 85 + (index // 3) * 18
        parts.append(f'<rect x="{x}" y="{y - 10}" width="12" height="10" fill="{PALETTE[index % len(PALETTE)]}" stroke="#303840" stroke-width="0.5"/>')
        parts.append(f'<text x="{x + 17}" y="{y}" font-family="Arial" font-size="10" fill="#303840">{_xml(label)}</text>')
    return _svg_frame(title, subtitle, "Kalenderjahr / Kategorie", y_label, "\n".join(parts) + "\n", "SQLite fact_market_context, Phase 1C-B")


def _svg_line_chart(title: str, subtitle: str, categories: Sequence[str], series: Mapping[str, Sequence[float]], y_label: str) -> bytes:
    all_values = [float(value) for values in series.values() for value in values]
    minimum = min(all_values, default=0.0)
    maximum = max(all_values, default=1.0)
    if maximum == minimum:
        maximum = minimum + 1.0
    plot_height = 385.0
    plot_width = 790.0
    parts = []
    for grid_index in range(5):
        value = minimum + (maximum - minimum) * grid_index / 4
        y = 500 - plot_height * grid_index / 4
        parts.append(f'<line x1="90" y1="{y:.2f}" x2="900" y2="{y:.2f}" stroke="#D9DEE4" stroke-width="1"/>')
        parts.append(f'<text x="82" y="{y + 4:.2f}" text-anchor="end" font-family="Arial" font-size="10" fill="#59616B">{_xml(format(value, ".3g"))}</text>')
    tick_step = max(1, math.ceil(len(categories) / 9))
    for index, category in enumerate(categories):
        x = 100 + plot_width * index / max(len(categories) - 1, 1)
        if index % tick_step == 0 or index == len(categories) - 1:
            parts.append(f'<text x="{x:.2f}" y="520" text-anchor="middle" font-family="Arial" font-size="11" fill="#303840">{_xml(category)}</text>')
    for series_index, (label, values) in enumerate(series.items()):
        points = []
        for index, value in enumerate(values):
            x = 100 + plot_width * index / max(len(categories) - 1, 1)
            y = 500 - plot_height * (float(value) - minimum) / (maximum - minimum)
            points.append(f"{x:.2f},{y:.2f}")
        dash = LINE_STYLES[series_index % len(LINE_STYLES)]
        dash_attr = f' stroke-dasharray="{dash}"' if dash else ""
        color = PALETTE[series_index % len(PALETTE)]
        parts.append(f'<polyline points="{" ".join(points)}" fill="none" stroke="{color}" stroke-width="2.2"{dash_attr}/>')
        for point in points:
            x, y = point.split(",")
            parts.append(f'<circle cx="{x}" cy="{y}" r="3" fill="#FAFBFC" stroke="{color}" stroke-width="1.5"/>')
        legend_x = 630 + (series_index % 3) * 105
        legend_y = 85 + (series_index // 3) * 18
        parts.append(f'<line x1="{legend_x}" y1="{legend_y - 4}" x2="{legend_x + 14}" y2="{legend_y - 4}" stroke="{color}" stroke-width="2"{dash_attr}/>')
        parts.append(f'<text x="{legend_x + 18}" y="{legend_y}" font-family="Arial" font-size="10" fill="#303840">{_xml(label)}</text>')
    return _svg_frame(title, subtitle, "Kalenderjahr / Bin", y_label, "\n".join(parts) + "\n", "SQLite fact_market_context, Phase 1C-B")


def _histogram(values: Sequence[float | None], lower: float = -0.05, upper: float = 0.05, bins: int = 40) -> list[float]:
    counts = [0.0] * bins
    for value in values:
        if value is None:
            continue
        clipped = min(max(float(value), lower), math.nextafter(upper, lower))
        index = min(bins - 1, max(0, int((clipped - lower) / (upper - lower) * bins)))
        counts[index] += 1.0
    return counts


def _complete_calendar_rows(fact_dates: set[str]) -> list[dict[str, Any]]:
    month_names = (
        "Januar", "Februar", "Maerz", "April", "Mai", "Juni",
        "Juli", "August", "September", "Oktober", "November", "Dezember",
    )
    start = date(2021, 1, 1)
    end = date(2025, 12, 31)
    rows: list[dict[str, Any]] = []
    current = start
    expected_accepted_dates: set[str] = set()
    while current <= end:
        date_text = current.isoformat()
        month = date_text[:7]
        excluded = month in EXCLUDED_MONTHS
        if not excluded:
            expected_accepted_dates.add(date_text)
        rows.append({
            "date_key": int(date_text.replace("-", "")),
            "calendar_date": date_text,
            "calendar_year": current.year,
            "calendar_quarter": (current.month - 1) // 3 + 1,
            "quarter_label": f"{current.year}-Q{(current.month - 1) // 3 + 1}",
            "calendar_month": current.month,
            "day_of_month": current.day,
            "month_name_de": month_names[current.month - 1],
            "month_label": f"{current.year}-{current.month:02d}",
            "year_month_sort": current.year * 100 + current.month,
            "month_name_sort": current.month,
            "is_accepted_date": not excluded,
            "is_excluded_month": excluded,
            "exclusion_status": "excluded_source_continuity_month" if excluded else "accepted_phase1c_common_mask",
            "exclusion_reason": "conservative_full_month_exclusion_after_source_anomaly" if excluded else "",
        })
        current += timedelta(days=1)
    if len(rows) != 1_826 or rows[0]["calendar_date"] != "2021-01-01" or rows[-1]["calendar_date"] != "2025-12-31":
        raise IntegrityError("Vollstaendige Kalenderdimension besitzt falsche Grenzen oder Zeilenzahl.")
    if sum(1 for row in rows if row["is_excluded_month"]) != 212:
        raise IntegrityError("Kalenderdimension besitzt nicht exakt 212 Ausschlusstage.")
    if fact_dates != expected_accepted_dates:
        raise IntegrityError("Fakt-Datumsmenge stimmt nicht exakt mit allen akzeptierten Kalendertagen ueberein.")
    return rows


def _write_dimensions(connection: sqlite3.Connection, export_root: Path, calendar_dates: set[str]) -> dict[str, int]:
    asset_rows = [
        {"asset_key": row[0], "symbol": row[1], "base_asset": row[2], "quote_asset": row[3], "asset_sort": row[0]}
        for row in connection.execute("SELECT asset_key,symbol,base_asset,quote_asset FROM dim_asset ORDER BY asset_key")
    ]
    segment_rows = [
        {"segment_key": row[0], "segment_id": row[1], "start_month": row[2], "end_month": row[3],
         "valid_month_count": row[4], "boundary_description": row[5], "segment_sort": row[0]}
        for row in connection.execute("SELECT * FROM dim_segment ORDER BY segment_key")
    ]
    calendar_rows = _complete_calendar_rows(calendar_dates)
    timeframe_rows = [
        {"timeframe_key": 1, "timeframe": "1h", "interval_hours": 1, "timeframe_sort": 1},
        {"timeframe_key": 4, "timeframe": "4h", "interval_hours": 4, "timeframe_sort": 2},
    ]
    counts = {
        "dim_asset.csv": _write_csv(export_root / "dim_asset.csv", ("asset_key", "symbol", "base_asset", "quote_asset", "asset_sort"), asset_rows),
        "dim_segment.csv": _write_csv(export_root / "dim_segment.csv", ("segment_key", "segment_id", "start_month", "end_month", "valid_month_count", "boundary_description", "segment_sort"), segment_rows),
        "dim_calendar.csv": _write_csv(export_root / "dim_calendar.csv", CALENDAR_EXPORT_FIELDS, calendar_rows),
        "dim_timeframe.csv": _write_csv(export_root / "dim_timeframe.csv", ("timeframe_key", "timeframe", "interval_hours", "timeframe_sort"), timeframe_rows),
    }
    return counts


def _table_artifacts(collected: Mapping[str, Any]) -> tuple[dict[str, bytes], dict[str, int], dict[str, list[dict[str, Any]]]]:
    tables = {
        "coverage_by_asset_timeframe_year_segment.csv": (
            ("symbol", "timeframe", "calendar_year", "segment_id", "row_count", "first_timestamp_utc", "last_timestamp_utc", "expected_rows_between_observed_bounds", "coverage_within_observed_bounds_percent"),
            _coverage_rows(collected),
        ),
        "descriptive_stats_by_asset_timeframe.csv": (
            ("symbol", "timeframe", "metric", "count", "mean", "std", "min", "q25", "median", "q75", "max", "null_count"),
            _descriptive_rows(collected),
        ),
        "annual_activity.csv": (
            ("symbol", "timeframe", "calendar_year", "row_count", "base_volume_sum", "quote_volume_sum", "trade_count_sum", "base_volume_mean", "quote_volume_mean", "trade_count_mean", "close_median_usd"),
            _annual_rows(collected),
        ),
        "segment_comparison.csv": (
            ("symbol", "timeframe", "segment_id", "row_count", "first_timestamp_utc", "last_timestamp_utc", "close_min_usd", "close_max_usd", "base_volume_median", "candle_range_median", "close_to_close_return_std", "close_to_close_return_null_count"),
            _segment_rows(collected),
        ),
        "context_metrics_summary.csv": (
            ("context_asset", "observation_grain", "metric", "count", "mean", "std", "min", "q25", "median", "q75", "max", "null_count"),
            _context_metric_rows(collected),
        ),
        "context_age_summary.csv": (
            ("symbol", "timeframe", "metric", "count", "mean", "std", "min", "q25", "median", "q75", "max", "null_count"),
            _context_age_rows(collected),
        ),
        "gaps_and_exclusions.csv": (
            ("category", "value", "unit", "scope"), _gap_rows(),
        ),
    }
    artifacts = {name: _canonical_csv_bytes(fields, rows) for name, (fields, rows) in tables.items()}
    counts = {name: len(rows) for name, (_, rows) in tables.items()}
    materialized = {name: rows for name, (_, rows) in tables.items()}
    return artifacts, counts, materialized


def _figure_artifacts(collected: Mapping[str, Any], tables: Mapping[str, list[dict[str, Any]]]) -> dict[str, bytes]:
    annual = tables["annual_activity.csv"]
    years = [str(year) for year in range(2021, 2026)]
    row_series = {
        timeframe: [sum(row["row_count"] for row in annual if row["timeframe"] == timeframe and row["calendar_year"] == year) for year in range(2021, 2026)]
        for timeframe in ("1h", "4h")
    }
    median_series = {
        symbol: [next(row["close_median_usd"] for row in annual if row["symbol"] == symbol and row["timeframe"] == "1h" and row["calendar_year"] == year) for year in range(2021, 2026)]
        for symbol in ("BTCUSDT", "ETHUSDT", "SOLUSDT")
    }
    quote_series = {
        symbol: [next(row["quote_volume_sum"] for row in annual if row["symbol"] == symbol and row["timeframe"] == "1h" and row["calendar_year"] == year) for year in range(2021, 2026)]
        for symbol in ("BTCUSDT", "ETHUSDT", "SOLUSDT")
    }
    histogram_series = {
        symbol: _histogram(collected["metric_values"][(symbol, "1h", "close_to_close_return")])
        for symbol in ("BTCUSDT", "ETHUSDT", "SOLUSDT")
    }
    bins = [f"{(-5 + index * 0.25):.2f}" for index in range(40)]
    age_rows = tables["context_age_summary.csv"]
    age_series = {
        statistic: [
            statistics.fmean(
                row[statistic] for row in age_rows
                if row["timeframe"] == timeframe and row["metric"] == "context_age_hours"
            )
            for timeframe in ("1h", "4h")
        ] for statistic in ("q25", "median", "q75")
    }
    segment_rows = tables["segment_comparison.csv"]
    segment_categories = list(EXPECTED_SEGMENT_IDS)
    segment_series = {
        timeframe: [sum(row["row_count"] for row in segment_rows if row["timeframe"] == timeframe and row["segment_id"] == segment) for segment in segment_categories]
        for timeframe in ("1h", "4h")
    }
    return {
        "annual_row_coverage.svg": _svg_bar_chart("Jaehrliche Zeilenabdeckung", "Akzeptierte SQL-Zeilen ueber drei Assets", years, row_series, "Zeilen"),
        "annual_median_close.svg": _svg_line_chart("Jaehrlicher Median des Schlusskurses", "1h-Kerzen; Median je Asset und Kalenderjahr", years, median_series, "USDT je Coin"),
        "annual_quote_volume.svg": _svg_line_chart("Jaehrliches Quote-Volumen", "1h-Kerzen; Summe je Asset und Kalenderjahr", years, quote_series, "USDT"),
        "return_distribution_1h.svg": _svg_line_chart("Verteilung der 1h Close-to-close-Renditen", "40 Bins von -5 % bis +5 %; Randwerte in Randbins, Segmentstarts bleiben NULL", bins, histogram_series, "Zeilen je Bin"),
        "context_age_by_timeframe.svg": _svg_bar_chart("Kontextalter seit Quellzeitpunkt", "Decision Time minus Coin-Metrics-Quellzeitpunkt; Quartile und Median ueber Assets", ("1h", "4h"), age_series, "Stunden"),
        "segment_coverage.svg": _svg_bar_chart("Zeilen je gueltigem Segment", "Summe ueber BTCUSDT, ETHUSDT und SOLUSDT", segment_categories, segment_series, "Zeilen"),
    }


def _export_manifest_rows(export_root: Path, evidence: InputEvidence, export_counts: Mapping[str, int]) -> list[dict[str, Any]]:
    roles = {
        "fact_market_context_eda.csv": ("fact", "powerbi_fact_market_context_eda_v2"),
        "dim_asset.csv": ("dimension", "powerbi_dim_asset_v1"),
        "dim_segment.csv": ("dimension", "powerbi_dim_segment_v1"),
        "dim_calendar.csv": ("dimension", "powerbi_dim_calendar_v2"),
        "dim_timeframe.csv": ("dimension", "powerbi_dim_timeframe_v1"),
    }
    return [
        {
            "filename": name, "table_role": roles[name][0], "schema_id": roles[name][1],
            "row_count": export_counts[name], "sha256": sha256_file(export_root / name),
            "source_database_logical_fingerprint": evidence.logical_fingerprint,
            "creation_status": "CREATED",
        }
        for name in EXPORT_FILES
    ]


def _data_contract(export_counts: Mapping[str, int]) -> bytes:
    return f"""# Power-BI-Datenvertrag Phase 1C-C

## Vertragsstatus

Version: `powerbi_model_v2`. Dieser Vertrag beschreibt einen lokalen, deterministischen CSV-Import. Er baut kein Dashboard und keine `.pbix`-Datei. G1-13 und Gate 1 bleiben bis zur unabhaengigen Abnahme `NOT_EVALUATED`.

## Sternschema

| Tabelle | Rolle | Koernung | Primaerschluessel | Zeilen |
|---|---|---|---|---:|
| `fact_market_context_eda.csv` | Fakt | eine akzeptierte, vollstaendig geschlossene Kerze je Asset und Zeitrahmen | `market_context_key` (Geschaeftsschluessel: Asset + Zeitrahmen + `timestamp_utc`) | {export_counts['fact_market_context_eda.csv']} |
| `dim_asset.csv` | Dimension | ein Asset | `asset_key` | {export_counts['dim_asset.csv']} |
| `dim_segment.csv` | Dimension | ein gemeinsames gueltiges Zeitsegment | `segment_key` | {export_counts['dim_segment.csv']} |
| `dim_calendar.csv` | Dimension | jeder Kalendertag im vollstaendigen Projektscope 2021-01-01 bis 2025-12-31 | `date_key` | {export_counts['dim_calendar.csv']} |
| `dim_timeframe.csv` | Dimension | ein Zeitrahmen | `timeframe_key` | {export_counts['dim_timeframe.csv']} |

## Beziehungen

| Von | Nach | Kardinalitaet | Filterrichtung | Aktiv |
|---|---|---|---|---|
| `dim_asset[asset_key]` | `fact_market_context_eda[asset_key]` | 1:n | Dimension zu Fakt | Ja |
| `dim_segment[segment_key]` | `fact_market_context_eda[segment_key]` | 1:n | Dimension zu Fakt | Ja |
| `dim_calendar[date_key]` | `fact_market_context_eda[date_key]` | 1:n | Dimension zu Fakt | Ja |
| `dim_timeframe[timeframe_key]` | `fact_market_context_eda[timeframe_key]` | 1:n | Dimension zu Fakt | Ja |

Bidirektionale Beziehungen sind nicht erlaubt. Alle vier Fremdschluessel sind obligatorisch und ohne verwaiste Werte.

## Faktspalten und Datentypen

Die feste Spaltenreihenfolge lautet: `{', '.join(FACT_EXPORT_FIELDS)}`.

| Spaltengruppe | Power-BI-Typ | Sichtbarkeit | Nullregel |
|---|---|---|---|
| Schluessel (`*_key`) | Ganze Zahl | technische Schluessel ausblenden | nie NULL |
| UTC-Zeitfelder | Datum/Uhrzeit nach Import als UTC | `timestamp_utc` sichtbar, technische Verfuegbarkeitsfelder bei Bedarf | nie NULL |
| OHLC, Volumen, Kontextmetriken | Dezimalzahl | sichtbar | nie NULL |
| `number_of_trades`, `constituent_rows` | Ganze Zahl | sichtbar | `constituent_rows` nur 4h |
| Taker-Buy-Felder und `taker_buy_share` | Dezimalzahl | sichtbar | nur 1h beziehungsweise Nenner > 0 |
| deskriptive Kerzenfelder | Dezimalzahl | sichtbar | Close-to-close an Segmentstart oder Luecke NULL |
| `context_age_hours` | Dezimalzahl in Stunden | sichtbar | `decision_time_utc - context_source_timestamp_utc`; nie NULL |
| `context_age_since_d1_hours` | Dezimalzahl in Stunden | sichtbar | `decision_time_utc - context_available_from_utc_d1`; nie NULL |
| Herkunfts- und Statusfelder | Text | technische Felder standardmaessig ausblenden | nie NULL |

## Vollstaendige Kalenderdimension

`dim_calendar.csv` enthaelt lueckenlos genau 1.826 Tage. Die 212 Tage der sieben ausgeschlossenen Monate bleiben auf Zeitachsen sichtbar und tragen `is_excluded_month = 1`, `is_accepted_date = 0`, einen Ausschlussstatus und einen Ausschlussgrund. Die uebrigen 1.614 Tage tragen `is_accepted_date = 1`. Ausgeschlossene Tage besitzen keine Faktzeilen. `year_month_sort` sortiert `month_label`; `month_name_sort` sortiert `month_name_de`.

## Sortierung und Zeitzone

Faktzeilen sind nach `symbol`, `timeframe`, `timestamp_utc` stabil sortiert. Dimensionen verwenden ihre jeweilige `*_sort`-Spalte. Alle Zeitfelder sind kanonische ISO-8601-Zeitstempel in UTC. CSV-Dateien verwenden UTF-8 und LF.

## Schutzgrenzen

Die Faktentabelle enthaelt keine ausgeschlossenen Monate und keinen Zukunftskontext. `close_to_close_return` wird nur innerhalb desselben Assets, Zeitrahmens und Segments bei exakt 1h beziehungsweise 4h Abstand berechnet. Es gibt keine Forward Returns, Labels, Signale, Positionen oder Performancekennzahlen.
""".encode("utf-8")


def _measure_contract() -> bytes:
    return """# Power-BI-Measures Phase 1C-C

## Feldklassen

- Basisspalten: OHLC, Volumen, Tradeanzahl, Zeitfelder, Segment und Coin-Metrics-Kontext aus der geprueften SQL-Basis.
- Berechnete Exportspalten: Kerzenkoerper, Kerzenspanne, Schatten, Taker-Buy-Anteil, Kontextalter und strikt segmentgebundene Close-to-close-Rendite.
- Measures: ausschliesslich deskriptive Aggregationen im aktuellen Power-BI-Filterkontext.

## Geplante Measures

| Measure | DAX | Format | Filterkontext und Interpretation |
|---|---|---|---|
| Marktzeilen | `COUNTROWS(fact_market_context_eda)` | Ganze Zahl | Anzahl akzeptierter Kerzen im aktuellen Asset-, Zeitraum-, Datums- und Segmentfilter |
| Fruehester Zeitpunkt | `MIN(fact_market_context_eda[timestamp_utc])` | `yyyy-mm-dd hh:mm` | erste enthaltene UTC-Kerze im aktuellen Filter |
| Spaetester Zeitpunkt | `MAX(fact_market_context_eda[timestamp_utc])` | `yyyy-mm-dd hh:mm` | letzte enthaltene UTC-Kerze im aktuellen Filter |
| Durchschnitt Basisvolumen | `AVERAGE(fact_market_context_eda[volume])` | Dezimalzahl | arithmetisches Mittel des Basisvolumens; Einheit ist assetabhaengig |
| Median Basisvolumen | `MEDIAN(fact_market_context_eda[volume])` | Dezimalzahl | robuster Mittelpunkt des Basisvolumens |
| Durchschnitt Tradeanzahl | `AVERAGE(fact_market_context_eda[number_of_trades])` | Dezimalzahl | mittlere Trades je Kerze |
| Durchschnitt Kerzenspanne | `AVERAGE(fact_market_context_eda[candle_range])` | `0.0000%` | mittlere relative High-Low-Spanne, rein deskriptiv |
| Durchschnitt Kerzenkoerper | `AVERAGE(fact_market_context_eda[candle_body_return])` | `0.0000%` | mittlere relative Open-Close-Aenderung, keine Empfehlung |
| Durchschnitt Kontextalter seit Quellzeitpunkt | `AVERAGE(fact_market_context_eda[context_age_hours])` | `0.00 h` | `decision_time_utc - context_source_timestamp_utc`; 1h-Kontrollbereich 24-47 h |
| Durchschnitt Kontextalter seit D+1 | `AVERAGE(fact_market_context_eda[context_age_since_d1_hours])` | `0.00 h` | `decision_time_utc - context_available_from_utc_d1`; 1h-Kontrollbereich 0-23 h |
| Globale akzeptierte Scope-Abdeckung | `DIVIDE(CALCULATE(COUNTROWS(dim_calendar), REMOVEFILTERS(dim_calendar), dim_calendar[is_accepted_date] = TRUE()), CALCULATE(COUNTROWS(dim_calendar), REMOVEFILTERS(dim_calendar)))` | `0.00%` | immer 88,39 % des gesamten Projektscopes; bewusst unabhaengig von Kalender-, Asset- und Visualfiltern |
| Globale ausgeschlossene Scope-Abdeckung | `DIVIDE(CALCULATE(COUNTROWS(dim_calendar), REMOVEFILTERS(dim_calendar), dim_calendar[is_excluded_month] = TRUE()), CALCULATE(COUNTROWS(dim_calendar), REMOVEFILTERS(dim_calendar)))` | `0.00%` | immer 11,61 % des gesamten Projektscopes; bewusst unabhaengig von Kalender-, Asset- und Visualfiltern |
| Akzeptierte Abdeckung im Kalenderfilter | `DIVIDE(CALCULATE(COUNTROWS(dim_calendar), dim_calendar[is_accepted_date] = TRUE()), COUNTROWS(dim_calendar))` | `0.00%` | reagiert auf Jahr, Quartal, Monat und Datum; ein Assetfilter aendert sie wegen der gemeinsamen Assetmaske und einseitiger Beziehungen bewusst nicht |
| Ausgeschlossene Abdeckung im Kalenderfilter | `DIVIDE(CALCULATE(COUNTROWS(dim_calendar), dim_calendar[is_excluded_month] = TRUE()), COUNTROWS(dim_calendar))` | `0.00%` | reagiert auf Jahr, Quartal, Monat und Datum; ausgeschlossene Monate bleiben trotz fehlender Fakten sichtbar |
| Segmentanzahl | `DISTINCTCOUNT(fact_market_context_eda[segment_key])` | Ganze Zahl | Anzahl gueltiger Segmente im aktuellen Filter |

Keine Measure stellt Renditeperformance, Signalqualitaet oder eine Tradingempfehlung dar.
""".encode("utf-8")


def _eda_dictionary(table_counts: Mapping[str, int], figure_count: int) -> bytes:
    return f"""# Phase-1C-C EDA-Datenwoerterbuch

## Koernungen

| Datei | Koernung | Zeilen |
|---|---|---:|
| `coverage_by_asset_timeframe_year_segment.csv` | Asset x Zeitrahmen x Kalenderjahr x Segment | {table_counts['coverage_by_asset_timeframe_year_segment.csv']} |
| `descriptive_stats_by_asset_timeframe.csv` | Asset x Zeitrahmen x Kennzahl | {table_counts['descriptive_stats_by_asset_timeframe.csv']} |
| `annual_activity.csv` | Asset x Zeitrahmen x Kalenderjahr | {table_counts['annual_activity.csv']} |
| `segment_comparison.csv` | Asset x Zeitrahmen x Segment | {table_counts['segment_comparison.csv']} |
| `context_metrics_summary.csv` | eindeutiger Coin-Metrics-Quelltag x Kontextkennzahl, danach aggregiert | {table_counts['context_metrics_summary.csv']} |
| `context_age_summary.csv` | Asset x Zeitrahmen x Kontextalterdefinition | {table_counts['context_age_summary.csv']} |
| `gaps_and_exclusions.csv` | dokumentierte Qualitaets- oder Ausschlussaussage | {table_counts['gaps_and_exclusions.csv']} |

## Deskriptive Felder

- `candle_body_return = close / open - 1`
- `candle_range = high / low - 1`
- `upper_wick_relative = (high - max(open, close)) / open`
- `lower_wick_relative = (min(open, close) - low) / open`
- `taker_buy_share = taker_buy_quote_volume / quote_asset_volume`, nur bei Nenner > 0 und nur fuer 1h
- `context_age_hours = decision_time_utc - context_source_timestamp_utc`; Alter seit dem Coin-Metrics-Quellzeitpunkt, nicht seit D+1
- `context_age_since_d1_hours = decision_time_utc - context_available_from_utc_d1`; getrenntes Alter seit angenommener D+1-Verfuegbarkeit
- `close_to_close_return = close / vorheriger_close - 1`, nur bei gleichem Asset, Zeitrahmen und Segment sowie exakt 1h/4h Abstand

## Kalenderdimension

Der Power-BI-Export besitzt eine lueckenlose Kalenderdimension von 2021-01-01 bis 2025-12-31 mit 1.826 Tagen. 1.614 Tage sind akzeptiert; 212 Tage aus sieben ausgeschlossenen Monaten bleiben als ausgeschlossene Kalendertage ohne Faktzeilen sichtbar.

## Statistiken

Jede Kennzahl berichtet Anzahl, Mittelwert, Stichproben-Standardabweichung, Minimum, lineare 25-/50-/75-Prozent-Quantile, Maximum und Nullanzahl. Extremwerte werden weder entfernt noch winsorisiert.

## Abbildungen

Es entstehen {figure_count} deterministische SVG-Dateien. Jede enthaelt Titel, Achsen, Einheit, Quelle und Ausschlusshinweis. Die Renditeverteilung schneidet keine Werte ab: Werte ausserhalb +/-5 % werden fuer die Darstellung in den jeweiligen Randbin gezaehlt; die Tabellen behalten die unveraenderten Werte.
""".encode("utf-8")


def _eda_report(collected: Mapping[str, Any], tables: Mapping[str, list[dict[str, Any]]]) -> bytes:
    descriptive = tables["descriptive_stats_by_asset_timeframe.csv"]
    annual = tables["annual_activity.csv"]
    context = {row["metric"]: row for row in tables["context_metrics_summary.csv"]}
    age = {
        (row["timeframe"], row["metric"]): row
        for row in tables["context_age_summary.csv"]
        if row["symbol"] == "BTCUSDT"
    }
    range_medians = {
        (row["symbol"], row["timeframe"]): row["median"]
        for row in descriptive if row["metric"] == "candle_range"
    }
    volume_medians = {
        (row["symbol"], row["timeframe"]): row["median"]
        for row in descriptive if row["metric"] == "quote_volume"
    }
    return f"""# Phase 1C-C: reproduzierbare deskriptive EDA

## Technische Zusammenfassung

Die EDA basiert ausschliesslich auf der abgenommenen SQLite-Tabelle mit **{_format_de_number(collected['total_rows'])}** Zeilen: **{_format_de_number(EXPECTED_ROWS['1h'])}** 1h- und **{_format_de_number(EXPECTED_ROWS['4h'])}** 4h-Kerzen. Alle sechs Asset-Zeitrahmen-Gruppen besitzen dieselben fuenf gueltigen Segmente. Zukunftskontext, ausgeschlossene Monate, Primaerschluesselduplikate und verwaiste Fremdschluessel: jeweils **0**. Diese Phase beschreibt Daten; sie bewertet keine Strategie.

## Die Abdeckung ist gross, aber absichtlich nicht lueckenlos

Die akzeptierte zeitliche Abdeckung betraegt **88,39 %**. **11,61 %** beziehungsweise **15.264 Asset-Kalenderstunden** wurden konservativ ausgeschlossen. Davon getrennt ist die tatsaechliche Quellenluecke von nur **42 Asset-Stunden**. Die strengere Monatsregel verhindert, dass spaetere Zeitreihenzustaende ueber unsichere Grenzen fortgesetzt werden.

Die Power-BI-Kalenderdimension zeigt den gesamten Scope lueckenlos mit **1.826 Tagen**. Darin sind **1.614 akzeptierte Tage** und **212 ausgeschlossene Tage** eindeutig markiert. Die ausgeschlossenen Tage bleiben auf Zeitachsen sichtbar, besitzen aber weiterhin keine Faktzeilen.

![Jaehrliche Zeilenabdeckung](figures/annual_row_coverage.svg)

Die Grafik zeigt ausschliesslich akzeptierte SQL-Zeilen. Unterschiede zwischen Jahren entstehen vor allem durch die sieben vollstaendig ausgeschlossenen Kalendermonate und die unterschiedliche Zahl von Stunden pro Jahr, nicht durch nachtraegliches Auffuellen.

## Preise, Volumen und Aktivitaet unterscheiden sich deutlich nach Asset und Zeitrahmen

Die Preisniveaus sind wegen unterschiedlicher Coin-Einheiten nicht direkt als relative Leistung vergleichbar. Fuer 1h liegt der Median der Kerzenspanne bei BTCUSDT bei **{_format_de_number(100 * range_medians[('BTCUSDT','1h')], 4)} %**, bei ETHUSDT bei **{_format_de_number(100 * range_medians[('ETHUSDT','1h')], 4)} %** und bei SOLUSDT bei **{_format_de_number(100 * range_medians[('SOLUSDT','1h')], 4)} %**. Die robusten Medianwerte sind fuer diese schiefen Verteilungen aussagekraeftiger als alleinige Mittelwerte.

![Jaehrlicher Median des Schlusskurses](figures/annual_median_close.svg)

Die Linien zeigen jaehrliche Mediane, keine Rendite- oder Performancebewertung. Unterschiedliche Preisniveaus und Einheiten bleiben sichtbar und werden nicht normalisiert.

![Jaehrliches Quote-Volumen](figures/annual_quote_volume.svg)

Das Quote-Volumen ist in USDT vergleichbar. Die 1h-Summen vermeiden eine Doppelzaehlung zwischen 1h- und den daraus aggregierten 4h-Kerzen. Als robuste Querschnittswerte betragen die Median-Quote-Volumina je 1h-Kerze BTCUSDT **{_format_de_number(volume_medians[('BTCUSDT','1h')], 2)}**, ETHUSDT **{_format_de_number(volume_medians[('ETHUSDT','1h')], 2)}** und SOLUSDT **{_format_de_number(volume_medians[('SOLUSDT','1h')], 2)}** USDT.

## 4h-Kerzen verdichten vier 1h-Kerzen und sind deshalb nicht unabhaengig

Die 4h-Tabelle enthaelt **{_format_de_number(EXPECTED_ROWS['4h'])}** Zeilen gegenueber **{_format_de_number(EXPECTED_ROWS['1h'])}** 1h-Zeilen. Volumen und Tradeanzahl je 4h-Kerze sind Aggregationen derselben Marktaktivitaet; Vergleiche der Kerzenverteilungen duerfen deshalb nicht als zwei unabhaengige Stichproben interpretiert werden.

![Verteilung der 1h-Renditen](figures/return_distribution_1h.svg)

Close-to-close-Renditen sind rein deskriptiv und nur bei exakt benachbarten Kerzen desselben Segments berechnet. **{collected['return_null_count']}** Werte bleiben NULL, darunter alle **{collected['segment_start_null_count']}** Asset-Zeitrahmen-Segmentstarts. Es gibt keine Berechnung ueber eine Segmentgrenze oder Zeitluecke.

## Die fuenf Segmente bleiben methodisch getrennt

![Zeilen je Segment](figures/segment_coverage.svg)

Laengere Segmente enthalten erwartungsgemaess mehr Zeilen. Die gemeinsame Assetmaske stellt sicher, dass BTCUSDT, ETHUSDT und SOLUSDT fuer jeden akzeptierten Monat gemeinsam vorhanden sind. Spaetere Indikator- oder Modellzustaende muessen an jedem Segmentstart neu beginnen.

## Coin-Metrics-Kontext ist punkt-in-der-Zeit verfuegbar

Die Kontextverteilung wird auf **{_format_de_number(context['context_price_usd']['count'])} eindeutigen Quellzeitpunkten** ausgewertet, damit dieselbe taegliche BTC-Beobachtung nicht durch drei Assets und intraday Kerzen mehrfach gewichtet wird. Der Median des BTC-Kontextpreises liegt bei **{_format_de_number(context['context_price_usd']['median'], 2)} USD**, der Median der aktiven Adressen bei **{_format_de_number(context['context_active_address_count']['median'])}**.

![Kontextalter seit Quellzeitpunkt nach Zeitrahmen](figures/context_age_by_timeframe.svg)

`context_age_hours` misst `decision_time_utc - context_source_timestamp_utc`: bei 1h **{_csv_value(age[('1h','context_age_hours')]['min'])}-{_csv_value(age[('1h','context_age_hours')]['max'])} Stunden**, Median **{_csv_value(age[('1h','context_age_hours')]['median'])}**; bei 4h **{_csv_value(age[('4h','context_age_hours')]['min'])}-{_csv_value(age[('4h','context_age_hours')]['max'])} Stunden**, Median **{_csv_value(age[('4h','context_age_hours')]['median'])}**. Das getrennte `context_age_since_d1_hours` misst `decision_time_utc - context_available_from_utc_d1`: 1h **{_csv_value(age[('1h','context_age_since_d1_hours')]['min'])}-{_csv_value(age[('1h','context_age_since_d1_hours')]['max'])} Stunden**, Median **{_csv_value(age[('1h','context_age_since_d1_hours')]['median'])}**; 4h **{_csv_value(age[('4h','context_age_since_d1_hours')]['min'])}-{_csv_value(age[('4h','context_age_since_d1_hours')]['max'])} Stunden**, Median **{_csv_value(age[('4h','context_age_since_d1_hours')]['median'])}**. Beide Bezugsgrössen bleiben getrennt. D+2 bleibt separat erhalten.

## Scope, Kennzahlen und Vergleichsbasis

Analysiert werden BTCUSDT, ETHUSDT und SOLUSDT von Januar 2021 bis Dezember 2025 in 1h und 4h. Koernung ist eine akzeptierte, vollstaendig geschlossene Kerze. Alle Preis- und Volumenfelder stammen aus Binance Public Data; der Tageskontext stammt aus Coin Metrics und ist mit der konservativen D+1-00:00-UTC-Regel verbunden.

## Methodik und Reproduzierbarkeit

Die Pipeline prueft vor jeder Ausgabe Datenbankhash, unabhaengigen logischen Fingerprint, SQL-Berichtscache, Integritaet, Zeilenzahlen, Matrix, Schluessel, Segmente, Ausschlussmonate und Gate-Status. Danach erzeugt sie CSV und SVG temporaer, prueft Manifeste und Beziehungen und publiziert ohne Ueberschreiben. Ein Wiederanlauf gilt nur bei vollstaendig byteidentischem Cache als `CACHED_VALID`.

## Grenzen und Robustheitspruefungen

- Die Ergebnisse sind deskriptiv; sie beweisen keine Ursache und keine Handelbarkeit.
- Extremwerte bleiben erhalten. Die grafische Renditeverteilung buendelt lediglich Werte ausserhalb +/-5 % in Randbins.
- D+1 00:00 UTC ist eine konservative Annahme und muss spaeter separat gegen D+2 geprueft werden.
- 4h ist aus 1h aggregiert; beide Zeitrahmen sind nicht unabhaengig.
- Vollstaendige Monatsausgrenzung ist strenger als die reale 42-Stunden-Quellenluecke.

## Empfohlener naechster Schritt

Nach unabhaengiger Abnahme koennen diese Exporte in Power BI geladen und Beziehungen, Datentypen, Sortierung, Filterrichtung und Measures gegen den Vertrag geprueft werden. Erst danach darf G1-13 bewertet werden. Eine Signal- oder Backtestphase ist nicht Bestandteil dieses Auftrags.

## Offene Fragen

- Stimmen die geplanten DAX-Measures im spaeteren Power-BI-Modell byte- und filterlogisch mit den EDA-Tabellen ueberein?
- Bleiben Segmentgrenzen in allen spaeteren Zeitreihenberechnungen wirksam?
- Wie stark aendert die spaetere D+2-Sensitivitaet rein deskriptive Kontextzusammenhaenge?
""".encode("utf-8")


def _eda_manifest_rows(report_root: Path, table_counts: Mapping[str, int], evidence: InputEvidence) -> list[dict[str, Any]]:
    rows = []
    for path in sorted((path for path in report_root.rglob("*") if path.is_file()), key=lambda item: item.as_posix()):
        if path.name == "eda_manifest.csv":
            continue
        relative = path.relative_to(report_root.parent.parent).as_posix()
        if path.parent.name == "tables":
            role = "eda_table"
            schema_id = f"phase1c_c_{path.stem}_v2"
            row_count: Any = table_counts[path.name]
        elif path.parent.name == "figures":
            role = "eda_figure"
            schema_id = "deterministic_svg_v1"
            row_count = ""
        elif path.name == "eda_quality_summary.json":
            role = "quality_report"
            schema_id = "phase1c_c_quality_v2"
            row_count = 1
        else:
            role = "documentation"
            schema_id = f"phase1c_c_{path.stem.lower()}_v2"
            row_count = ""
        rows.append({
            "artifact_path": relative, "artifact_role": role, "schema_id": schema_id,
            "row_count": row_count, "sha256": sha256_file(path),
            "source_database_logical_fingerprint": evidence.logical_fingerprint,
            "creation_status": "CREATED",
        })
    return rows


def _validate_csv_header(path: Path, expected: Sequence[str]) -> int:
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.reader(handle)
        header = next(reader, None)
        if header != list(expected):
            raise IntegrityError(f"CSV-Spaltenreihenfolge weicht ab: {path.name}")
        count = 0
        for row in reader:
            if len(row) != len(expected):
                raise IntegrityError(f"CSV-Zeile besitzt falsche Spaltenzahl: {path.name}")
            count += 1
    if b"\r\n" in path.read_bytes():
        raise IntegrityError(f"CSV verwendet nicht ausschliesslich LF: {path.name}")
    return count


def _validate_generated_bundle(
    report_root: Path,
    contract_root: Path,
    export_root: Path,
    evidence: InputEvidence,
    table_counts: Mapping[str, int],
    export_counts: Mapping[str, int],
) -> None:
    expected_report_files = {
        "PHASE1C_EDA_REPORT.md", "EDA_DATA_DICTIONARY.md", "eda_quality_summary.json", "eda_manifest.csv",
        *(f"tables/{name}" for name in TABLE_FILES),
        *(f"figures/{name}" for name in FIGURE_FILES),
    }
    actual_report_files = {
        path.relative_to(report_root).as_posix() for path in report_root.rglob("*") if path.is_file()
    }
    if actual_report_files != expected_report_files:
        raise IntegrityError("EDA-Ausgabebundle ist unvollstaendig oder enthaelt unbekannte Dateien.")
    if {path.name for path in contract_root.iterdir() if path.is_file()} != set(CONTRACT_FILES):
        raise IntegrityError("Power-BI-Vertragsbundle weicht ab.")
    if {path.name for path in export_root.iterdir() if path.is_file()} != set(EXPORT_FILES):
        raise IntegrityError("Power-BI-Exportbundle weicht ab.")

    fact_count = _validate_csv_header(export_root / "fact_market_context_eda.csv", FACT_EXPORT_FIELDS)
    if fact_count != EXPECTED_TOTAL_ROWS or fact_count != export_counts["fact_market_context_eda.csv"]:
        raise IntegrityError("Power-BI-Faktzeilenzahl weicht von SQL ab.")
    for token in PROHIBITED_FIELD_TOKENS:
        if any(token in field.lower() for field in FACT_EXPORT_FIELDS):
            raise IntegrityError(f"Verbotenes Faktfeld: {token}")

    manifest_fields = (
        "filename", "table_role", "schema_id", "row_count", "sha256",
        "source_database_logical_fingerprint", "creation_status",
    )
    with (contract_root / "powerbi_model_manifest.csv").open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if tuple(reader.fieldnames or ()) != manifest_fields:
            raise IntegrityError("Power-BI-Manifest besitzt ein falsches Schema.")
        manifest_rows = list(reader)
    if [row["filename"] for row in manifest_rows] != list(EXPORT_FILES):
        raise IntegrityError("Power-BI-Manifestumfang oder Sortierung weicht ab.")
    for row in manifest_rows:
        path = export_root / row["filename"]
        if (
            row["sha256"] != sha256_file(path)
            or int(row["row_count"]) != export_counts[row["filename"]]
            or row["source_database_logical_fingerprint"] != evidence.logical_fingerprint
            or row["creation_status"] != "CREATED"
        ):
            raise IntegrityError(f"Power-BI-Manifest widerspricht Export: {row['filename']}")

    dimension_headers = {
        "dim_asset.csv": ("asset_key", "symbol", "base_asset", "quote_asset", "asset_sort"),
        "dim_segment.csv": ("segment_key", "segment_id", "start_month", "end_month", "valid_month_count", "boundary_description", "segment_sort"),
        "dim_calendar.csv": CALENDAR_EXPORT_FIELDS,
        "dim_timeframe.csv": ("timeframe_key", "timeframe", "interval_hours", "timeframe_sort"),
    }
    for name, fields in dimension_headers.items():
        if _validate_csv_header(export_root / name, fields) != export_counts[name]:
            raise IntegrityError(f"Dimensionszaehlung weicht ab: {name}")

    with (export_root / "dim_calendar.csv").open("r", encoding="utf-8", newline="") as handle:
        calendar_rows = list(csv.DictReader(handle))
    calendar_keys = [row["date_key"] for row in calendar_rows]
    excluded_calendar_rows = [row for row in calendar_rows if row["is_excluded_month"] == "1"]
    if (
        len(calendar_rows) != 1_826
        or calendar_rows[0]["calendar_date"] != "2021-01-01"
        or calendar_rows[-1]["calendar_date"] != "2025-12-31"
        or len(calendar_keys) != len(set(calendar_keys))
        or len(excluded_calendar_rows) != 212
        or {row["calendar_date"][:7] for row in excluded_calendar_rows} != set(EXCLUDED_MONTHS)
        or any(row["is_accepted_date"] != "0" for row in excluded_calendar_rows)
    ):
        raise IntegrityError("Vollstaendige Power-BI-Kalenderdimension besteht die Pflichtpruefungen nicht.")
    for previous, current in zip(calendar_rows, calendar_rows[1:]):
        if date.fromisoformat(current["calendar_date"]) - date.fromisoformat(previous["calendar_date"]) != timedelta(days=1):
            raise IntegrityError("Power-BI-Kalenderdimension besitzt eine Tagesluecke.")

    keys: dict[str, set[str]] = {}
    for name, key in (("dim_asset.csv", "asset_key"), ("dim_segment.csv", "segment_key"), ("dim_calendar.csv", "date_key"), ("dim_timeframe.csv", "timeframe_key")):
        with (export_root / name).open("r", encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle))
        values = [row[key] for row in rows]
        if len(values) != len(set(values)) or any(not value for value in values):
            raise IntegrityError(f"Dimension besitzt doppelte oder leere Schluessel: {name}")
        keys[key] = set(values)
    seen_fact_keys: set[str] = set()
    excluded = set(EXCLUDED_MONTHS)
    with (export_root / "fact_market_context_eda.csv").open("r", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            key = row["market_context_key"]
            if key in seen_fact_keys:
                raise IntegrityError("Power-BI-Faktschluessel ist nicht eindeutig.")
            seen_fact_keys.add(key)
            for foreign_key in ("asset_key", "segment_key", "date_key", "timeframe_key"):
                if row[foreign_key] not in keys[foreign_key]:
                    raise IntegrityError(f"Verwaister Power-BI-Fremdschluessel: {foreign_key}")
            if row["timestamp_utc"][:7] in excluded:
                raise IntegrityError("Power-BI-Export enthaelt ausgeschlossenen Monat.")
            for field in ("timestamp_utc", "close_time_utc", "decision_time_utc", "context_source_timestamp_utc", "context_available_from_utc_d1", "context_available_from_utc_d2"):
                _parse_utc(row[field])
            if row["context_available_from_utc_d1"] > row["decision_time_utc"]:
                raise IntegrityError("Power-BI-Export enthaelt Zukunftskontext.")

    for name in FIGURE_FILES:
        payload = (report_root / "figures" / name).read_text(encoding="utf-8")
        for required in ("<title>", "Quelle:", "Ausgeschlossen:", "data-source=", "<line"):
            if required not in payload:
                raise IntegrityError(f"SVG-Pflichtelement fehlt in {name}: {required}")
    quality = json.loads((report_root / "eda_quality_summary.json").read_text(encoding="utf-8"))
    if (
        quality.get("source_validation", {}).get("database_sha256") != evidence.database_sha256
        or quality.get("source_validation", {}).get("logical_fingerprint") != evidence.logical_fingerprint
        or quality.get("gate_status", {}).get("G1-13") != "NOT_EVALUATED"
        or quality.get("gate_status", {}).get("Gate 1") != "NOT_EVALUATED"
    ):
        raise IntegrityError("EDA-Qualitaetsbericht widerspricht der validierten Basis.")
    project_text = str(report_root.parent.parent.resolve()).encode("utf-8")
    for root in (report_root, contract_root, export_root):
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            payload = path.read_bytes()
            if project_text in payload or b"PRIVATE KEY" in payload or b"api_key" in payload.lower():
                raise SafetyError(f"Ausgabe enthaelt absoluten Pfad oder Zugangsdatenhinweis: {path.name}")


def _generate_bundle(temp_root: Path, evidence: InputEvidence) -> tuple[Path, Path, Path, dict[str, int], dict[str, int]]:
    report_root = temp_root / "reports" / "eda"
    table_root = report_root / "tables"
    figure_root = report_root / "figures"
    contract_root = temp_root / "powerbi"
    export_root = temp_root / "export"
    table_root.mkdir(parents=True)
    figure_root.mkdir()
    contract_root.mkdir()
    export_root.mkdir()

    connection = _readonly_connection(evidence.database_path)
    try:
        collected = _write_fact_and_collect(connection, export_root / "fact_market_context_eda.csv")
        if collected["total_rows"] != EXPECTED_TOTAL_ROWS:
            raise IntegrityError("EDA-Ausgabe besitzt unerwartete Faktzeilenzahl.")
        if collected["group_counts"] != EXPECTED_ASSET_ROWS:
            raise IntegrityError("EDA-Zaehlungen sind je Asset nicht additiv.")
        if collected["segment_start_null_count"] != 30 or collected["return_null_count"] != 30:
            raise IntegrityError("Close-to-close-Rendite ist an Segmentgrenzen oder Luecken falsch.")
        context_age_controls = _validate_context_age_controls(collected)
        export_counts = {"fact_market_context_eda.csv": collected["total_rows"]}
        export_counts.update(_write_dimensions(connection, export_root, collected["calendar_dates"]))
    finally:
        connection.close()

    table_artifacts, table_counts, tables = _table_artifacts(collected)
    for name, payload in table_artifacts.items():
        (table_root / name).write_bytes(payload)
    figure_artifacts = _figure_artifacts(collected, tables)
    for name, payload in figure_artifacts.items():
        (figure_root / name).write_bytes(payload)

    manifest_rows = _export_manifest_rows(export_root, evidence, export_counts)
    manifest_fields = (
        "filename", "table_role", "schema_id", "row_count", "sha256",
        "source_database_logical_fingerprint", "creation_status",
    )
    (contract_root / "powerbi_model_manifest.csv").write_bytes(_canonical_csv_bytes(manifest_fields, manifest_rows))
    (contract_root / "POWER_BI_DATA_CONTRACT.md").write_bytes(_data_contract(export_counts))
    (contract_root / "POWER_BI_MEASURES.md").write_bytes(_measure_contract())

    quality_payload = {
        "build_status": "CREATED",
        "coverage": {
            "accepted_percent": ACCEPTED_COVERAGE_PERCENT,
            "excluded_percent": EXCLUDED_COVERAGE_PERCENT,
            "actual_source_gap_hours": RAW_GAP_HOURS,
            "conservative_excluded_asset_calendar_hours": EXCLUDED_CALENDAR_HOURS,
            "accepted_asset_months": 159,
            "excluded_asset_months": 21,
            "excluded_calendar_months": sorted(EXCLUDED_MONTHS),
        },
        "eda": {
            "fact_rows": collected["total_rows"],
            "rows_1h": EXPECTED_ROWS["1h"], "rows_4h": EXPECTED_ROWS["4h"],
            "asset_timeframe_counts": {f"{symbol}|{timeframe}": count for (symbol, timeframe), count in sorted(collected["group_counts"].items())},
            "segment_count": 5,
            "close_to_close_null_count": collected["return_null_count"],
            "segment_start_null_count": collected["segment_start_null_count"],
            "future_context_violations": 0, "excluded_month_rows": 0,
            "primary_key_duplicates": 0, "orphan_foreign_keys": 0,
            "table_count": len(TABLE_FILES), "figure_count": len(FIGURE_FILES),
            "context_unique_source_timestamps": len(collected["context_rows"]),
            "context_age_controls": context_age_controls,
            "extreme_value_policy": "retained_without_winsorization",
        },
        "gate_status": {"G1-13": "NOT_EVALUATED", "Gate 1": "NOT_EVALUATED"},
        "policy_id": EDA_POLICY_ID,
        "powerbi_export": {
            "fact_rows": export_counts["fact_market_context_eda.csv"],
            "dimension_rows": {name: export_counts[name] for name in EXPORT_FILES if name != "fact_market_context_eda.csv"},
            "files": manifest_rows,
            "relationship_policy": "unique_dimension_1_to_many_single_direction_v1",
        },
        "schema_version": EDA_SCHEMA_VERSION,
        "source_validation": {
            "database_sha256": evidence.database_sha256,
            "logical_fingerprint": evidence.logical_fingerprint,
            "sql_cache_status": evidence.sql_cache_status,
            "integrity_check": evidence.quality["integrity_check"],
            "G1-10": evidence.gate_statuses["G1-10"],
            "G1-12": evidence.gate_statuses["G1-12"],
        },
    }
    (report_root / "eda_quality_summary.json").write_bytes(_canonical_json_bytes(quality_payload))
    (report_root / "EDA_DATA_DICTIONARY.md").write_bytes(_eda_dictionary(table_counts, len(FIGURE_FILES)))
    (report_root / "PHASE1C_EDA_REPORT.md").write_bytes(_eda_report(collected, tables))
    eda_manifest_fields = (
        "artifact_path", "artifact_role", "schema_id", "row_count", "sha256",
        "source_database_logical_fingerprint", "creation_status",
    )
    eda_manifest_rows = _eda_manifest_rows(report_root, table_counts, evidence)
    (report_root / "eda_manifest.csv").write_bytes(_canonical_csv_bytes(eda_manifest_fields, eda_manifest_rows))
    _validate_generated_bundle(report_root, contract_root, export_root, evidence, table_counts, export_counts)
    return report_root, contract_root, export_root, table_counts, export_counts


def _same_directory(expected: Path, actual: Path) -> bool:
    if not expected.is_dir() or not actual.is_dir():
        return False
    expected_files = {path.relative_to(expected).as_posix(): path for path in expected.rglob("*") if path.is_file()}
    actual_files = {path.relative_to(actual).as_posix(): path for path in actual.rglob("*") if path.is_file()}
    if set(expected_files) != set(actual_files):
        return False
    return all(expected_files[name].read_bytes() == actual_files[name].read_bytes() for name in expected_files)


def _existing_output_state(project_root: Path) -> tuple[Path, Path, Path, bool]:
    report_root = _inside_project(project_root, project_root / REPORT_RELATIVE_PATH)
    contract_root = _inside_project(project_root, project_root / POWERBI_CONTRACT_ROOT)
    export_root = _inside_project(project_root, project_root / POWERBI_EXPORT_RELATIVE_PATH)
    contract_exists = all((contract_root / name).is_file() for name in CONTRACT_FILES)
    contract_partial = any((contract_root / name).exists() for name in CONTRACT_FILES)
    states = (report_root.exists(), export_root.exists(), contract_exists)
    if contract_partial and not contract_exists:
        raise SafetyError("Power-BI-Vertragsausgaben sind unvollstaendig; kein Ueberschreiben erlaubt.")
    if any(states) and not all(states):
        raise SafetyError("Phase-1C-C-Ausgaben sind unvollstaendig; kein Ueberschreiben erlaubt.")
    return report_root, contract_root, export_root, all(states)


def _publish_bundle(
    temp_report: Path, temp_contract: Path, temp_export: Path,
    report_root: Path, contract_root: Path, export_root: Path,
) -> None:
    created_contracts: list[Path] = []
    report_published = False
    export_published = False
    try:
        report_root.parent.mkdir(parents=True, exist_ok=True)
        export_root.parent.mkdir(parents=True, exist_ok=True)
        contract_root.mkdir(parents=True, exist_ok=True)
        if report_root.exists() or export_root.exists() or any((contract_root / name).exists() for name in CONTRACT_FILES):
            raise SafetyError("Zielausgaben erschienen waehrend der Erzeugung; kein Ueberschreiben.")
        temp_report.rename(report_root)
        report_published = True
        temp_export.rename(export_root)
        export_published = True
        for name in CONTRACT_FILES:
            target = contract_root / name
            os.link(temp_contract / name, target)
            created_contracts.append(target)
    except Exception:
        for path in created_contracts:
            path.unlink(missing_ok=True)
        if export_published and export_root.exists():
            shutil.rmtree(export_root)
        if report_published and report_root.exists():
            shutil.rmtree(report_root)
        raise


def _refresh_bundle(
    temp_report: Path, temp_contract: Path, temp_export: Path,
    report_root: Path, contract_root: Path, export_root: Path,
    backup_root: Path,
) -> None:
    expected_report_files = {
        "PHASE1C_EDA_REPORT.md", "EDA_DATA_DICTIONARY.md", "eda_quality_summary.json", "eda_manifest.csv",
        *(f"tables/{name}" for name in TABLE_FILES),
        *(f"figures/{name}" for name in FIGURE_FILES),
    }
    actual_report_files = {
        path.relative_to(report_root).as_posix() for path in report_root.rglob("*") if path.is_file()
    }
    actual_export_files = {path.name for path in export_root.iterdir() if path.is_file()}
    if actual_report_files != expected_report_files or actual_export_files != set(EXPORT_FILES):
        raise SafetyError("Vorhandener Phase-1C-C-Cache besitzt einen unerwarteten Dateiumfang.")
    if any(not (contract_root / name).is_file() for name in CONTRACT_FILES):
        raise SafetyError("Vorhandener Power-BI-Vertrag ist unvollstaendig.")

    backup_report = backup_root / "report"
    backup_export = backup_root / "export"
    backup_contract = backup_root / "contract"
    backup_root.mkdir()
    backup_contract.mkdir()
    moved_contracts: list[str] = []
    report_backed_up = False
    export_backed_up = False
    new_report_published = False
    new_export_published = False
    new_contracts: list[Path] = []
    try:
        report_root.rename(backup_report)
        report_backed_up = True
        export_root.rename(backup_export)
        export_backed_up = True
        for name in CONTRACT_FILES:
            (contract_root / name).rename(backup_contract / name)
            moved_contracts.append(name)

        temp_report.rename(report_root)
        new_report_published = True
        temp_export.rename(export_root)
        new_export_published = True
        for name in CONTRACT_FILES:
            target = contract_root / name
            os.link(temp_contract / name, target)
            new_contracts.append(target)
    except Exception:
        for path in new_contracts:
            path.unlink(missing_ok=True)
        if new_export_published and export_root.exists():
            shutil.rmtree(export_root)
        if new_report_published and report_root.exists():
            shutil.rmtree(report_root)
        for name in reversed(moved_contracts):
            backup = backup_contract / name
            if backup.exists():
                backup.rename(contract_root / name)
        if export_backed_up and backup_export.exists():
            backup_export.rename(export_root)
        if report_backed_up and backup_report.exists():
            backup_report.rename(report_root)
        raise
    shutil.rmtree(backup_root)


def run_pipeline(project_root: Path, config_path: Path, *, refresh_existing: bool = False) -> PipelineResult:
    project_root = project_root.resolve()
    evidence = validate_inputs(project_root, config_path)
    report_root, contract_root, export_root, cache_exists = _existing_output_state(project_root)
    temp_parent = _inside_project(project_root, project_root / "data/processed/full_import")
    temp_root = temp_parent / f".phase1c_c.{uuid.uuid4().hex}.part"
    if temp_root.exists():
        raise SafetyError("Unerwartetes temporaeres Phase-1C-C-Verzeichnis.")
    temp_root.mkdir(parents=False)
    try:
        temp_report, temp_contract, temp_export, table_counts, export_counts = _generate_bundle(temp_root, evidence)
        protected_before_publication = _snapshot_files(Path(path) for path in evidence.protected_hashes)
        if protected_before_publication != evidence.protected_hashes:
            raise IntegrityError("Eingaben oder fruehere Buildnachweise wurden vor der Veroeffentlichung veraendert.")
        if sha256_file(evidence.database_path) != evidence.database_sha256:
            raise IntegrityError("SQLite-Datenbank wurde vor der Veroeffentlichung veraendert.")
        if cache_exists:
            byteidentical = _same_directory(temp_report, report_root) and _same_directory(temp_export, export_root)
            byteidentical = byteidentical and all(
                (temp_contract / name).read_bytes() == (contract_root / name).read_bytes()
                for name in CONTRACT_FILES
            )
            if byteidentical:
                status = "CACHED_VALID"
            elif not refresh_existing:
                raise IntegrityError("Vorhandene Phase-1C-C-Ausgaben weichen bytegenau ab; expliziter kontrollierter Refresh erforderlich.")
            else:
                _refresh_bundle(
                    temp_report, temp_contract, temp_export,
                    report_root, contract_root, export_root,
                    temp_root / "backup",
                )
                status = "REFRESHED"
        else:
            _publish_bundle(temp_report, temp_contract, temp_export, report_root, contract_root, export_root)
            status = "CREATED"

        protected_after = _snapshot_files(Path(path) for path in evidence.protected_hashes)
        if protected_after != evidence.protected_hashes:
            raise IntegrityError("Fruehere Buildnachweise wurden waehrend Phase 1C-C veraendert.")
        if sha256_file(evidence.database_path) != evidence.database_sha256:
            raise IntegrityError("SQLite-Datenbank wurde waehrend Phase 1C-C veraendert.")
        export_hashes = {name: sha256_file(export_root / name) for name in EXPORT_FILES}
        return PipelineResult(
            status=status, report_root=report_root, export_root=export_root,
            database_sha256=evidence.database_sha256,
            logical_fingerprint=evidence.logical_fingerprint,
            fact_rows=export_counts["fact_market_context_eda.csv"], export_hashes=export_hashes,
            gate_statuses=dict(evidence.gate_statuses),
        )
    finally:
        if temp_root.exists():
            shutil.rmtree(temp_root)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Offline-EDA und Power-BI-Datenvertrag fuer Phase 1C-C.")
    parser.add_argument("--config", default="config/full_import.json")
    parser.add_argument(
        "--refresh-existing", action="store_true",
        help="Ersetzt ausschliesslich einen vollstaendigen vorhandenen Phase-1C-C-Cache mit Rollback-Schutz.",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    project_root = Path.cwd().resolve()
    try:
        result = run_pipeline(project_root, project_root / args.config, refresh_existing=args.refresh_existing)
    except (IntegrityError, SafetyError, OSError, sqlite3.Error, ValueError) as exc:
        print(f"PHASE 1C-C FEHLGESCHLAGEN: {exc}", file=sys.stderr)
        return 1
    print(
        f"PHASE 1C-C {result.status}: {result.fact_rows} Faktenzeilen, "
        f"SQL-Fingerprint {result.logical_fingerprint}"
    )
    print(f"G1-13: {result.gate_statuses['G1-13']}")
    print(f"Gate 1: {result.gate_statuses['Gate 1']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
