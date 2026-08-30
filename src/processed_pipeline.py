"""Phase 1C-A: kanonische Processed-Tabellen ohne Netzwerkzugriff.

Die Pipeline liest ausschliesslich die geprueften Phase-1B-Interimdateien und
den autoritativen Checkpoint. Alle Eingaben werden fail-closed validiert,
bevor eine Processed- oder Berichtsdatei geschrieben werden darf.
"""

from __future__ import annotations

import argparse
import bisect
import csv
import io
import json
import statistics
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Iterable, Sequence

from src.full_import import (
    BINANCE_INTERIM_1H_FIELDS,
    BINANCE_INTERIM_1H_SCHEMA_ID,
    CHECKPOINT_SCHEMA_VERSION,
    EXECUTION_REPORT_FILES,
    GATE_1_STATUS,
    REQUIRED_ASSETS,
    IntegrityError,
    SafetyError,
    expected_binance_timestamp_unit,
    load_authoritative_checkpoint,
    load_config,
    month_sequence,
    processing_policy_fingerprint,
    project_relative,
    safe_project_path,
    sha256_bytes,
    sha256_file,
    write_generated_file_cached,
)


PHASE1C_POLICY_ID = "phase1c_a_canonical_asof_d1_v1"
PROCESSED_1H_SCHEMA_ID = "canonical_market_context_1h_v1"
PROCESSED_4H_SCHEMA_ID = "canonical_market_context_4h_v1"
JOIN_REPORT_SCHEMA_ID = "phase1c_join_quality_v1"
PROCESSED_MANIFEST_SCHEMA_ID = "phase1c_processed_manifest_v1"
DATA_DICTIONARY_SCHEMA_ID = "phase1c_data_dictionary_v1"
QUALITY_REPORT_SCHEMA_ID = "phase1c_quality_report_v1"
EXPECTED_EXECUTION_STATUS = "COMPLETED_WITH_SOURCE_ANOMALIES"
EXPECTED_VALID_ASSET_MONTHS = 159
EXPECTED_EXCLUDED_ASSET_MONTHS = 21
EXPECTED_ALLOWED_MONTHS = 53
EXPECTED_1H_ROWS = 116_208
EXPECTED_4H_ROWS = 29_052
EXPECTED_COINMETRICS_ROWS = 1_828
RAW_EXPECTED_1H_ROWS = 131_472
RAW_ACTUAL_1H_ROWS = 131_430
RAW_MISSING_HOURS = 42

BINANCE_INTERIM_4H_FIELDS = (
    "symbol",
    "timeframe",
    "timestamp_utc",
    "close_time_utc",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "quote_asset_volume",
    "number_of_trades",
    "constituent_rows",
    "source",
)

COINMETRICS_FIELDS = (
    "asset",
    "source_timestamp_utc",
    "available_from_utc_d1",
    "available_from_utc_d2",
    "PriceUSD",
    "CapMrktCurUSD",
    "TxCnt",
    "AdrActCnt",
)

COMMON_OUTPUT_PREFIX_FIELDS = (
    "symbol",
    "timeframe",
    "timestamp_utc",
    "close_time_utc",
    "decision_time_utc",
    "segment_id",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "quote_asset_volume",
    "number_of_trades",
)

COMMON_OUTPUT_SUFFIX_FIELDS = (
    "market_source",
    "market_timestamp_unit",
    "market_quality_status",
    "context_match_status",
    "context_source",
    "context_asset",
    "context_source_timestamp_utc",
    "context_available_from_utc_d1",
    "context_available_from_utc_d2",
    "context_price_usd",
    "context_market_cap_usd",
    "context_tx_count",
    "context_active_address_count",
    "context_age_seconds",
)

PROCESSED_1H_FIELDS = (
    *COMMON_OUTPUT_PREFIX_FIELDS,
    "taker_buy_base_volume",
    "taker_buy_quote_volume",
    *COMMON_OUTPUT_SUFFIX_FIELDS,
)

PROCESSED_4H_FIELDS = (
    *COMMON_OUTPUT_PREFIX_FIELDS,
    "constituent_rows",
    *COMMON_OUTPUT_SUFFIX_FIELDS,
)

MANIFEST_FIELDS = (
    "artifact_id",
    "artifact_path",
    "artifact_type",
    "schema_id",
    "row_count",
    "sha256",
    "source_checkpoint",
    "source_checkpoint_sha256",
    "source_checkpoint_generation_id",
    "source_checkpoint_run_id",
    "phase1c_policy_id",
    "phase1c_policy_fingerprint",
)

MARKET_NUMERIC_FIELDS = (
    "open",
    "high",
    "low",
    "close",
    "volume",
    "quote_asset_volume",
    "number_of_trades",
)


@dataclass(frozen=True)
class ContextRow:
    asset: str
    source_timestamp: datetime
    available_d1: datetime
    available_d2: datetime
    price_usd: str
    market_cap_usd: str
    tx_count: str
    active_address_count: str


@dataclass
class ValidatedInputs:
    config: dict[str, Any]
    checkpoint: dict[str, Any]
    checkpoint_sha256: str
    market_rows: dict[str, list[dict[str, Any]]]
    contexts: list[ContextRow]
    allowed_months: list[str]
    excluded_months: list[str]
    segments: list[dict[str, Any]]


@dataclass
class BuildResult:
    statuses: dict[str, str]
    summary: dict[str, Any]
    artifacts: list[dict[str, str]]


def parse_utc(value: str, field_name: str) -> datetime:
    """Timezone-aware UTC strikt lesen."""

    if not isinstance(value, str) or not value.strip():
        raise IntegrityError(f"{field_name} fehlt.")
    text = value.strip()
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise IntegrityError(f"{field_name} ist kein ISO-8601-Zeitpunkt.") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise IntegrityError(f"{field_name} muss timezone-aware UTC sein.")
    return parsed.astimezone(timezone.utc)


def format_utc(value: datetime) -> str:
    """UTC mit fester Mikrosekundenpraezision serialisieren."""

    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise IntegrityError("UTC-Serialisierung erhielt naiven Zeitstempel.")
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def decision_time_from_close(close_time: datetime, timestamp_unit: str) -> datetime:
    """Ersten Zeitpunkt unmittelbar nach dem Kerzenschluss liefern."""

    if timestamp_unit == "ms":
        if close_time.microsecond % 1000 != 0:
            raise IntegrityError("Millisekundenkerze besitzt falsche Praezision.")
        return close_time + timedelta(milliseconds=1)
    if timestamp_unit == "us":
        return close_time + timedelta(microseconds=1)
    raise IntegrityError(f"Unbekannte Zeitstempeleinheit: {timestamp_unit}")


def _finite_nonnegative(value: str, field_name: str) -> Decimal:
    try:
        parsed = Decimal(value)
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise IntegrityError(f"{field_name} ist nicht numerisch.") from exc
    if not parsed.is_finite() or parsed < 0:
        raise IntegrityError(f"{field_name} muss endlich und nichtnegativ sein.")
    return parsed


def _finite_positive(value: str, field_name: str) -> Decimal:
    parsed = _finite_nonnegative(value, field_name)
    if parsed <= 0:
        raise IntegrityError(f"{field_name} muss positiv sein.")
    return parsed


def read_strict_csv(path: Path, expected_fields: Sequence[str]) -> list[dict[str, str]]:
    """Physische CSV-Zeilen mit exakt festgelegtem Schema lesen."""

    try:
        with path.open("r", encoding="utf-8", newline="") as handle:
            rows = list(csv.reader(handle))
    except (OSError, UnicodeDecodeError, csv.Error) as exc:
        raise IntegrityError(f"CSV ist nicht lesbar: {path.name}") from exc
    if not rows or tuple(rows[0]) != tuple(expected_fields):
        raise IntegrityError(f"CSV-Schema weicht ab: {path.name}")
    parsed: list[dict[str, str]] = []
    for line_number, physical in enumerate(rows[1:], start=2):
        if len(physical) != len(expected_fields):
            raise IntegrityError(
                f"CSV-Zeile {line_number} besitzt falsche Spaltenzahl: {path.name}"
            )
        if any(value is None for value in physical):
            raise IntegrityError(f"CSV-Zeile {line_number} enthaelt None-Werte.")
        parsed.append(dict(zip(expected_fields, physical, strict=True)))
    return parsed


def canonical_csv_bytes(fields: Sequence[str], rows: Iterable[dict[str, Any]]) -> bytes:
    """Deterministische UTF-8-CSV mit LF und fester Spaltenfolge erzeugen."""

    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(
        buffer,
        fieldnames=list(fields),
        lineterminator="\n",
        extrasaction="raise",
    )
    writer.writeheader()
    for row in rows:
        if set(row) != set(fields):
            raise IntegrityError("Ausgabezeile widerspricht dem kanonischen Schema.")
        writer.writerow(row)
    return buffer.getvalue().encode("utf-8")


def canonical_json_bytes(payload: Any) -> bytes:
    return (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode(
        "utf-8"
    )


def phase1c_policy_fingerprint() -> str:
    payload = {
        "policy_id": PHASE1C_POLICY_ID,
        "primary_join": "latest available_from_utc_d1 <= decision_time_utc",
        "d2_role": "retained_for_later_sensitivity_not_evaluated",
        "decision_time_ms": "close_time_utc + 1 ms",
        "decision_time_us": "close_time_utc + 1 us",
        "segment_rule": "shared_allowed_month_mask_reset_after_every_gap",
        "processed_1h_schema_id": PROCESSED_1H_SCHEMA_ID,
        "processed_1h_fields": list(PROCESSED_1H_FIELDS),
        "processed_4h_schema_id": PROCESSED_4H_SCHEMA_ID,
        "processed_4h_fields": list(PROCESSED_4H_FIELDS),
    }
    return sha256_bytes(canonical_json_bytes(payload))


def _next_month(month: str) -> str:
    parsed = datetime.strptime(month, "%Y-%m")
    if parsed.month == 12:
        return f"{parsed.year + 1:04d}-01"
    return f"{parsed.year:04d}-{parsed.month + 1:02d}"


def build_month_segments(allowed_months: Sequence[str]) -> tuple[dict[str, str], list[dict[str, Any]]]:
    """Gemeinsame Monatsmaske in zusammenhaengende Segmente zerlegen."""

    ordered = sorted(set(allowed_months))
    if len(ordered) != len(allowed_months):
        raise IntegrityError("Zulaessige Monatsmaske enthaelt Duplikate.")
    mapping: dict[str, str] = {}
    segments: list[dict[str, Any]] = []
    for month in ordered:
        if not segments or month != _next_month(segments[-1]["last_month"]):
            segments.append(
                {
                    "segment_id": f"SEGMENT_{len(segments) + 1:03d}",
                    "first_month": month,
                    "last_month": month,
                    "allowed_month_count": 1,
                }
            )
        else:
            segments[-1]["last_month"] = month
            segments[-1]["allowed_month_count"] += 1
        mapping[month] = segments[-1]["segment_id"]
    return mapping, segments


def _expected_interim_path(
    interim_root: Path, symbol: str, timeframe: str, month: str
) -> Path:
    return interim_root / "binance" / symbol / timeframe / f"{symbol}-{timeframe}-{month}.csv"


def _validate_checkpoint_and_quality(
    *,
    config: dict[str, Any],
    checkpoint: dict[str, Any],
    state: dict[str, Any],
    project_root: Path,
    report_root: Path,
    interim_root: Path,
) -> tuple[list[dict[str, Any]], list[str], list[str], Path]:
    if checkpoint.get("checkpoint_schema_version") != CHECKPOINT_SCHEMA_VERSION:
        raise IntegrityError("Phase-1B-Checkpoint besitzt falsches Schema.")
    if checkpoint.get("execution_status") != EXPECTED_EXECUTION_STATUS:
        raise IntegrityError("Phase 1B ist nicht erfolgreich abgeschlossen.")
    if checkpoint.get("gate_1") != GATE_1_STATUS:
        raise IntegrityError("Gate 1 darf vor Phase 1C nicht bewertet sein.")
    if checkpoint.get("processing_policy_fingerprint") != processing_policy_fingerprint():
        raise IntegrityError("Phase-1B-Verarbeitungsrichtlinie weicht ab.")
    if checkpoint.get("binance_interim_1h_schema_id") != BINANCE_INTERIM_1H_SCHEMA_ID:
        raise IntegrityError("Phase-1B-1h-Schema weicht ab.")

    for report_name in EXECUTION_REPORT_FILES:
        expected = checkpoint["report_generation"]["projection_hashes"][report_name]
        actual = sha256_file(report_root / report_name)
        if actual != expected:
            raise IntegrityError(f"Phase-1B-Berichtsprojektion weicht ab: {report_name}")

    aggregates = checkpoint.get("aggregate_counts", {})
    expected_aggregates = {
        "accepted_interim_1h_rows": EXPECTED_1H_ROWS,
        "accepted_interim_4h_rows": EXPECTED_4H_ROWS,
        "continuity_anomaly_months": EXPECTED_EXCLUDED_ASSET_MONTHS,
        "observed_raw_1h_rows": RAW_ACTUAL_1H_ROWS,
        "scope_expected_1h_rows": RAW_EXPECTED_1H_ROWS,
    }
    for field, expected in expected_aggregates.items():
        if aggregates.get(field) != expected:
            raise IntegrityError(f"Checkpoint-Zaehlung weicht ab: {field}")

    quality = state.get("quality")
    if not isinstance(quality, list) or len(quality) != 180:
        raise IntegrityError("Checkpoint muss exakt 180 Monatsqualitaeten enthalten.")
    configured_months = month_sequence(
        config["binance"]["start_utc"], config["binance"]["end_exclusive_utc"]
    )
    expected_keys = {(asset, month) for asset in REQUIRED_ASSETS for month in configured_months}
    quality_by_key: dict[tuple[str, str], dict[str, Any]] = {}
    for row in quality:
        key = (row.get("symbol"), row.get("month"))
        if key not in expected_keys or key in quality_by_key:
            raise IntegrityError("Monatsqualitaet besitzt ungueltigen oder doppelten Scope.")
        quality_by_key[key] = row
    if set(quality_by_key) != expected_keys:
        raise IntegrityError("Monatsqualitaet deckt den konfigurierten Scope nicht ab.")

    valid_rows: list[dict[str, Any]] = []
    valid_months_by_asset: dict[str, set[str]] = {asset: set() for asset in REQUIRED_ASSETS}
    excluded_months_by_asset: dict[str, set[str]] = {asset: set() for asset in REQUIRED_ASSETS}
    expected_files: set[str] = set()
    for key in sorted(quality_by_key):
        row = quality_by_key[key]
        symbol, month = key
        path_1h = _expected_interim_path(interim_root, symbol, "1h", month)
        path_4h = _expected_interim_path(interim_root, symbol, "4h", month)
        expected_rel_1h = project_relative(path_1h, project_root)
        expected_rel_4h = project_relative(path_4h, project_root)
        if row.get("interim_1h_file") != expected_rel_1h or row.get("interim_4h_file") != expected_rel_4h:
            raise IntegrityError("Checkpoint-Interimpfad weicht vom sicheren Sollpfad ab.")
        if row.get("processing_status") == "valid":
            if not (
                row.get("quality_pass") is True
                and row.get("source_integrity_pass") is True
                and row.get("continuity_pass") is True
                and row.get("interim_1h_status") in {"created", "cached_valid"}
                and row.get("interim_4h_status") in {"created", "cached_valid"}
            ):
                raise IntegrityError("Gueltiger Monat besitzt widerspruechliche Evidenz.")
            valid_rows.append(row)
            valid_months_by_asset[symbol].add(month)
            expected_files.update({expected_rel_1h, expected_rel_4h})
        elif row.get("processing_status") == "source_continuity_anomaly":
            if not (
                row.get("quality_pass") is False
                and row.get("source_integrity_pass") is True
                and row.get("continuity_pass") is False
                and row.get("interim_1h_status") == "skipped_source_continuity_anomaly"
                and row.get("interim_4h_status") == "skipped_source_continuity_anomaly"
                and not path_1h.exists()
                and not path_4h.exists()
            ):
                raise IntegrityError("Ausgeschlossener Monat besitzt widerspruechliche Evidenz.")
            excluded_months_by_asset[symbol].add(month)
        else:
            raise IntegrityError("Nicht freigegebener Monatsstatus in Phase-1B-Evidenz.")

    if len(valid_rows) != EXPECTED_VALID_ASSET_MONTHS:
        raise IntegrityError("Es muessen exakt 159 gueltige Asset-Monate vorliegen.")
    if sum(map(len, excluded_months_by_asset.values())) != EXPECTED_EXCLUDED_ASSET_MONTHS:
        raise IntegrityError("Es muessen exakt 21 Asset-Monate ausgeschlossen sein.")
    first_allowed = valid_months_by_asset[REQUIRED_ASSETS[0]]
    if len(first_allowed) != EXPECTED_ALLOWED_MONTHS or any(
        months != first_allowed for months in valid_months_by_asset.values()
    ):
        raise IntegrityError("Die drei Assets besitzen keine identische 53-Monatsmaske.")
    first_excluded = excluded_months_by_asset[REQUIRED_ASSETS[0]]
    if any(months != first_excluded for months in excluded_months_by_asset.values()):
        raise IntegrityError("Die ausgeschlossenen Monate sind nicht assetweit identisch.")

    coin_quality = state.get("coinmetrics_quality")
    if not isinstance(coin_quality, dict):
        raise IntegrityError("Coin-Metrics-Qualitaetsevidenz fehlt.")
    expected_coin_path = interim_root / "coinmetrics" / "btc_daily_context.csv"
    expected_coin_rel = project_relative(expected_coin_path, project_root)
    coin_checks = {
        "quality_pass": True,
        "rows": EXPECTED_COINMETRICS_ROWS,
        "expected_rows": EXPECTED_COINMETRICS_ROWS,
        "start_match": True,
        "end_match": True,
        "duplicate_timestamps": 0,
        "spacing_errors": 0,
        "non_finite_metric_values": 0,
        "negative_metric_values": 0,
        "asset_mismatch_count": 0,
        "interim_file": expected_coin_rel,
    }
    for field, expected in coin_checks.items():
        if coin_quality.get(field) != expected:
            raise IntegrityError(f"Coin-Metrics-Evidenz weicht ab: {field}")
    if coin_quality.get("interim_status") not in {"created", "cached_valid"}:
        raise IntegrityError("Coin-Metrics-Interimstatus ist nicht freigegeben.")
    expected_files.add(expected_coin_rel)

    actual_files = {
        project_relative(path, project_root)
        for path in interim_root.rglob("*")
        if path.is_file()
    }
    if actual_files != expected_files:
        missing = sorted(expected_files - actual_files)
        extra = sorted(actual_files - expected_files)
        raise IntegrityError(
            "Interim-Dateimenge weicht ab; "
            f"fehlend={missing[:3]}, zusaetzlich={extra[:3]}"
        )
    return valid_rows, sorted(first_allowed), sorted(first_excluded), expected_coin_path


def _validate_market_row_values(row: dict[str, str], timeframe: str) -> None:
    values = {field: _finite_nonnegative(row[field], field) for field in MARKET_NUMERIC_FIELDS}
    for field in ("open", "high", "low", "close"):
        _finite_positive(row[field], field)
    if not (
        values["high"] >= max(values["open"], values["close"], values["low"])
        and values["low"] <= min(values["open"], values["close"], values["high"])
    ):
        raise IntegrityError("Unlogische OHLC-Beziehung in Interimdatei.")
    if values["number_of_trades"] != values["number_of_trades"].to_integral_value():
        raise IntegrityError("Trade-Anzahl ist nicht ganzzahlig.")
    if timeframe == "1h":
        taker_base = _finite_nonnegative(row["taker_buy_base_volume"], "taker_buy_base_volume")
        taker_quote = _finite_nonnegative(row["taker_buy_quote_volume"], "taker_buy_quote_volume")
        if taker_base > values["volume"] or taker_quote > values["quote_asset_volume"]:
            raise IntegrityError("Taker-Volumen uebersteigt Gesamtvolumen.")
    elif row["constituent_rows"] != "4":
        raise IntegrityError("4h-Zeile besteht nicht aus exakt vier 1h-Zeilen.")


def _read_market_month(
    *,
    path: Path,
    symbol: str,
    timeframe: str,
    month: str,
    expected_rows: int,
    segment_id: str,
) -> list[dict[str, Any]]:
    fields = BINANCE_INTERIM_1H_FIELDS if timeframe == "1h" else BINANCE_INTERIM_4H_FIELDS
    raw_rows = read_strict_csv(path, fields)
    if len(raw_rows) != expected_rows:
        raise IntegrityError(f"Zeilenzahl weicht vom Checkpoint ab: {path.name}")
    interval_hours = 1 if timeframe == "1h" else 4
    expected_start = datetime.strptime(f"{month}-01", "%Y-%m-%d").replace(tzinfo=timezone.utc)
    unit = expected_binance_timestamp_unit(month)
    tick = timedelta(milliseconds=1) if unit == "ms" else timedelta(microseconds=1)
    output: list[dict[str, Any]] = []
    seen: set[datetime] = set()
    for index, row in enumerate(raw_rows):
        if row["symbol"] != symbol or row["timeframe"] != timeframe:
            raise IntegrityError(f"Symbol oder Zeitrahmen weicht ab: {path.name}")
        if timeframe == "1h":
            if row["timestamp_unit"] != unit or row["source"] != "binance_public_data":
                raise IntegrityError(f"1h-Herkunft oder Einheit weicht ab: {path.name}")
        elif row["source"] != "derived_from_complete_1h":
            raise IntegrityError(f"4h-Herkunft weicht ab: {path.name}")
        timestamp = parse_utc(row["timestamp_utc"], "timestamp_utc")
        close_time = parse_utc(row["close_time_utc"], "close_time_utc")
        expected_timestamp = expected_start + timedelta(hours=interval_hours * index)
        if timestamp != expected_timestamp:
            raise IntegrityError(f"Zeitreihe ist nicht vollstaendig: {path.name}")
        if close_time != timestamp + timedelta(hours=interval_hours) - tick:
            raise IntegrityError(f"Kerzenschluss ist ungueltig: {path.name}")
        decision_time = decision_time_from_close(close_time, unit)
        if decision_time != timestamp + timedelta(hours=interval_hours):
            raise IntegrityError(f"Decision-Time ist nicht der erste Zeitpunkt nach Schluss: {path.name}")
        if timestamp in seen:
            raise IntegrityError(f"Doppelter Primaerschluessel in {path.name}")
        seen.add(timestamp)
        _validate_market_row_values(row, timeframe)
        base = {
            "symbol": symbol,
            "timeframe": timeframe,
            "timestamp_utc": format_utc(timestamp),
            "close_time_utc": format_utc(close_time),
            "decision_time_utc": format_utc(decision_time),
            "segment_id": segment_id,
            "open": row["open"],
            "high": row["high"],
            "low": row["low"],
            "close": row["close"],
            "volume": row["volume"],
            "quote_asset_volume": row["quote_asset_volume"],
            "number_of_trades": row["number_of_trades"],
            "market_source": row["source"],
            "market_timestamp_unit": unit,
            "market_quality_status": "accepted_phase1b_complete_month",
            "_timestamp": timestamp,
            "_decision_time": decision_time,
        }
        if timeframe == "1h":
            base["taker_buy_base_volume"] = row["taker_buy_base_volume"]
            base["taker_buy_quote_volume"] = row["taker_buy_quote_volume"]
        else:
            base["constituent_rows"] = row["constituent_rows"]
        output.append(base)
    return output


def _read_coinmetrics(path: Path, config: dict[str, Any]) -> list[ContextRow]:
    raw_rows = read_strict_csv(path, COINMETRICS_FIELDS)
    if len(raw_rows) != EXPECTED_COINMETRICS_ROWS:
        raise IntegrityError("Coin Metrics muss exakt 1.828 Tageszeilen enthalten.")
    expected_start = datetime.fromisoformat(
        config["coinmetrics"]["start_date_inclusive"] + "T00:00:00+00:00"
    )
    contexts: list[ContextRow] = []
    seen: set[datetime] = set()
    for index, row in enumerate(raw_rows):
        source = parse_utc(row["source_timestamp_utc"], "source_timestamp_utc")
        d1 = parse_utc(row["available_from_utc_d1"], "available_from_utc_d1")
        d2 = parse_utc(row["available_from_utc_d2"], "available_from_utc_d2")
        if row["asset"] != "btc" or source != expected_start + timedelta(days=index):
            raise IntegrityError("Coin-Metrics-Asset, Grenze oder Tagesabstand weicht ab.")
        if d1 != source + timedelta(days=1) or d2 != source + timedelta(days=2):
            raise IntegrityError("Coin-Metrics-D+1-/D+2-Verfuegbarkeit weicht ab.")
        if source in seen:
            raise IntegrityError("Coin Metrics enthaelt doppelte Quelltage.")
        seen.add(source)
        for field in ("PriceUSD", "CapMrktCurUSD", "TxCnt", "AdrActCnt"):
            _finite_nonnegative(row[field], field)
        contexts.append(
            ContextRow(
                asset="btc",
                source_timestamp=source,
                available_d1=d1,
                available_d2=d2,
                price_usd=row["PriceUSD"],
                market_cap_usd=row["CapMrktCurUSD"],
                tx_count=row["TxCnt"],
                active_address_count=row["AdrActCnt"],
            )
        )
    return contexts


def validate_phase1b_inputs(project_root: Path, config_path: Path) -> ValidatedInputs:
    """Alle Phase-1B-Eingaben vor jeder Processed-Mutation validieren."""

    root = project_root.resolve()
    config = load_config(config_path, root)
    interim_root = safe_project_path(
        root, config["paths"]["interim_root"], required_prefix="data/interim"
    )
    report_root = safe_project_path(
        root, config["paths"]["report_root"], required_prefix="reports"
    )
    loaded = load_authoritative_checkpoint(
        config=config, report_root=report_root, project_root=root
    )
    if loaded is None:
        raise IntegrityError("Autoritativer Phase-1B-Checkpoint fehlt.")
    state, checkpoint = loaded
    valid_quality, allowed_months, excluded_months, coin_path = _validate_checkpoint_and_quality(
        config=config,
        checkpoint=checkpoint,
        state=state,
        project_root=root,
        report_root=report_root,
        interim_root=interim_root,
    )
    segment_map, segments = build_month_segments(allowed_months)
    market_rows: dict[str, list[dict[str, Any]]] = {"1h": [], "4h": []}
    for quality in sorted(valid_quality, key=lambda row: (row["symbol"], row["month"])):
        symbol = quality["symbol"]
        month = quality["month"]
        market_rows["1h"].extend(
            _read_market_month(
                path=_expected_interim_path(interim_root, symbol, "1h", month),
                symbol=symbol,
                timeframe="1h",
                month=month,
                expected_rows=int(quality["rows"]),
                segment_id=segment_map[month],
            )
        )
        market_rows["4h"].extend(
            _read_market_month(
                path=_expected_interim_path(interim_root, symbol, "4h", month),
                symbol=symbol,
                timeframe="4h",
                month=month,
                expected_rows=int(quality["derived_4h_rows"]),
                segment_id=segment_map[month],
            )
        )

    expected_totals = {"1h": EXPECTED_1H_ROWS, "4h": EXPECTED_4H_ROWS}
    expected_per_asset = {"1h": 38_736, "4h": 9_684}
    for timeframe, rows in market_rows.items():
        if len(rows) != expected_totals[timeframe]:
            raise IntegrityError(f"{timeframe}-Gesamtzeilenzahl weicht ab.")
        timestamp_sets: dict[str, set[str]] = {}
        for asset in REQUIRED_ASSETS:
            asset_rows = [row for row in rows if row["symbol"] == asset]
            if len(asset_rows) != expected_per_asset[timeframe]:
                raise IntegrityError(f"{timeframe}-Zeilenzahl je Asset weicht ab.")
            timestamp_sets[asset] = {row["timestamp_utc"] for row in asset_rows}
        if any(timestamp_sets[asset] != timestamp_sets[REQUIRED_ASSETS[0]] for asset in REQUIRED_ASSETS[1:]):
            raise IntegrityError(f"{timeframe}-Zeitpunkte verletzen die gemeinsame Asset-Maske.")
        keys = {(row["symbol"], timeframe, row["timestamp_utc"]) for row in rows}
        if len(keys) != len(rows):
            raise IntegrityError(f"{timeframe}-Primaerschluessel sind nicht eindeutig.")
        for asset in REQUIRED_ASSETS:
            ordered = sorted(
                (row for row in rows if row["symbol"] == asset),
                key=lambda row: row["_timestamp"],
            )
            interval = timedelta(hours=1 if timeframe == "1h" else 4)
            for previous, current in zip(ordered, ordered[1:]):
                gap = current["_timestamp"] - previous["_timestamp"]
                same_segment = current["segment_id"] == previous["segment_id"]
                if same_segment and gap != interval:
                    raise IntegrityError("Zeitluecke innerhalb eines Segments erkannt.")
                if not same_segment and gap <= interval:
                    raise IntegrityError("Segmentwechsel ohne echte Zeitluecke erkannt.")
        rows.sort(key=lambda row: (row["_timestamp"], row["symbol"]))

    contexts = _read_coinmetrics(coin_path, config)
    return ValidatedInputs(
        config=config,
        checkpoint=checkpoint,
        checkpoint_sha256=sha256_file(report_root / "execution_checkpoint.json"),
        market_rows=market_rows,
        contexts=contexts,
        allowed_months=allowed_months,
        excluded_months=excluded_months,
        segments=segments,
    )


def asof_join_d1(
    market_rows: Sequence[dict[str, Any]], contexts: Sequence[ContextRow]
) -> list[dict[str, str]]:
    """Nur bereits verfuegbaren D+1-Kontext an Marktzeilen haengen."""

    ordered_contexts = sorted(contexts, key=lambda row: row.available_d1)
    available = [row.available_d1 for row in ordered_contexts]
    if len(set(available)) != len(available):
        raise IntegrityError("Coin-Metrics-D+1-Verfuegbarkeit ist nicht eindeutig.")
    joined: list[dict[str, str]] = []
    for market in market_rows:
        decision_time = market["_decision_time"]
        index = bisect.bisect_right(available, decision_time) - 1
        output = {key: str(value) for key, value in market.items() if not key.startswith("_")}
        if index < 0:
            context_values = {
                "context_match_status": "unmatched",
                "context_source": "",
                "context_asset": "",
                "context_source_timestamp_utc": "",
                "context_available_from_utc_d1": "",
                "context_available_from_utc_d2": "",
                "context_price_usd": "",
                "context_market_cap_usd": "",
                "context_tx_count": "",
                "context_active_address_count": "",
                "context_age_seconds": "",
            }
        else:
            context = ordered_contexts[index]
            if context.available_d1 > decision_time:
                raise IntegrityError("As-of-Join wuerde Zukunftskontext verwenden.")
            age_seconds = int((decision_time - context.source_timestamp).total_seconds())
            context_values = {
                "context_match_status": "matched_d1_asof",
                "context_source": "coin_metrics_community_api",
                "context_asset": context.asset,
                "context_source_timestamp_utc": format_utc(context.source_timestamp),
                "context_available_from_utc_d1": format_utc(context.available_d1),
                "context_available_from_utc_d2": format_utc(context.available_d2),
                "context_price_usd": context.price_usd,
                "context_market_cap_usd": context.market_cap_usd,
                "context_tx_count": context.tx_count,
                "context_active_address_count": context.active_address_count,
                "context_age_seconds": str(age_seconds),
            }
        output.update(context_values)
        joined.append(output)
    return joined


def _duplicate_count(rows: Sequence[dict[str, str]]) -> int:
    keys = [(row["symbol"], row["timeframe"], row["timestamp_utc"]) for row in rows]
    return len(keys) - len(set(keys))


def _summarize_rows(rows: Sequence[dict[str, str]]) -> dict[str, Any]:
    matched = [row for row in rows if row["context_match_status"] == "matched_d1_asof"]
    ages = [int(row["context_age_seconds"]) for row in matched]
    violations = sum(
        parse_utc(row["context_available_from_utc_d1"], "context_available_from_utc_d1")
        > parse_utc(row["decision_time_utc"], "decision_time_utc")
        for row in matched
    )
    source_times = [row["context_source_timestamp_utc"] for row in matched]
    return {
        "input_rows": len(rows),
        "output_rows": len(rows),
        "matched_rows": len(matched),
        "unmatched_rows": len(rows) - len(matched),
        "match_rate_percent": round(100 * len(matched) / len(rows), 6) if rows else 0.0,
        "oldest_context_source_timestamp_utc": min(source_times) if source_times else None,
        "newest_context_source_timestamp_utc": max(source_times) if source_times else None,
        "minimum_context_age_seconds": min(ages) if ages else None,
        "maximum_context_age_seconds": max(ages) if ages else None,
        "median_context_age_seconds": int(statistics.median(ages)) if ages else None,
        "available_from_after_decision_violations": violations,
        "duplicate_primary_keys_before_join": _duplicate_count(rows),
        "duplicate_primary_keys_after_join": _duplicate_count(rows),
        "segment_count": len({row["segment_id"] for row in rows}),
    }


def build_join_summary(
    *,
    joined_by_timeframe: dict[str, list[dict[str, str]]],
    validated: ValidatedInputs,
) -> dict[str, Any]:
    all_rows = joined_by_timeframe["1h"] + joined_by_timeframe["4h"]
    by_asset: dict[str, Any] = {}
    by_timeframe: dict[str, Any] = {}
    by_asset_timeframe: dict[str, Any] = {}
    for asset in REQUIRED_ASSETS:
        by_asset[asset] = _summarize_rows([row for row in all_rows if row["symbol"] == asset])
    for timeframe in ("1h", "4h"):
        by_timeframe[timeframe] = _summarize_rows(joined_by_timeframe[timeframe])
        for asset in REQUIRED_ASSETS:
            key = f"{asset}|{timeframe}"
            by_asset_timeframe[key] = _summarize_rows(
                [row for row in joined_by_timeframe[timeframe] if row["symbol"] == asset]
            )

    segments: list[dict[str, Any]] = []
    for segment in validated.segments:
        entry = dict(segment)
        for timeframe in ("1h", "4h"):
            rows = [
                row
                for row in joined_by_timeframe[timeframe]
                if row["segment_id"] == segment["segment_id"]
            ]
            entry[f"{timeframe}_first_timestamp_utc"] = min(row["timestamp_utc"] for row in rows)
            entry[f"{timeframe}_last_close_time_utc"] = max(row["close_time_utc"] for row in rows)
        segments.append(entry)

    accepted_coverage = round(100 * EXPECTED_1H_ROWS / RAW_EXPECTED_1H_ROWS, 2)
    excluded_coverage = round(100 - accepted_coverage, 2)
    summary = {
        "schema_id": JOIN_REPORT_SCHEMA_ID,
        "phase1c_policy_id": PHASE1C_POLICY_ID,
        "phase1c_policy_fingerprint": phase1c_policy_fingerprint(),
        "source_checkpoint": "reports/full_import/execution_checkpoint.json",
        "source_checkpoint_sha256": validated.checkpoint_sha256,
        "source_checkpoint_generation_id": validated.checkpoint["generation_id"],
        "source_checkpoint_run_id": validated.checkpoint["run_id"],
        "phase1b_execution_status": validated.checkpoint["execution_status"],
        "gate_1": GATE_1_STATUS,
        "primary_join_rule": "latest available_from_utc_d1 <= decision_time_utc",
        "decision_time_rule": {
            "ms": "close_time_utc + 1 ms",
            "us": "close_time_utc + 1 us",
        },
        "d2_sensitivity_status": "PREPARED_NOT_EVALUATED",
        "context_age_definition": "decision_time_utc - context_source_timestamp_utc",
        "coverage": {
            "raw_expected_1h_rows": RAW_EXPECTED_1H_ROWS,
            "raw_actual_1h_rows": RAW_ACTUAL_1H_ROWS,
            "actual_raw_missing_hours": RAW_MISSING_HOURS,
            "accepted_1h_rows": EXPECTED_1H_ROWS,
            "conservatively_excluded_calendar_hours": RAW_EXPECTED_1H_ROWS - EXPECTED_1H_ROWS,
            "accepted_coverage_percent": accepted_coverage,
            "conservatively_excluded_coverage_percent": excluded_coverage,
            "valid_asset_months": EXPECTED_VALID_ASSET_MONTHS,
            "excluded_asset_months": EXPECTED_EXCLUDED_ASSET_MONTHS,
            "shared_allowed_calendar_months": len(validated.allowed_months),
        },
        "allowed_months": validated.allowed_months,
        "excluded_months": validated.excluded_months,
        "segments": segments,
        "global": _summarize_rows(all_rows),
        "by_asset": by_asset,
        "by_timeframe": by_timeframe,
        "by_asset_timeframe": by_asset_timeframe,
        "leakage_checks": {
            "future_context_rows": sum(
                group["available_from_after_decision_violations"]
                for group in by_timeframe.values()
            ),
            "rows_crossing_excluded_months": 0,
            "rolling_features_calculated": False,
            "returns_calculated": False,
            "signals_calculated": False,
            "positions_calculated": False,
            "segment_reset_rule_enforced": True,
            "shared_asset_mask_enforced": True,
        },
    }
    if summary["global"]["available_from_after_decision_violations"] != 0:
        raise IntegrityError("Leakage-Pruefung meldet Zukunftskontext.")
    return summary


def _data_dictionary_markdown() -> bytes:
    common_rows = [
        ("symbol", "Text", "Assetpaar; BTCUSDT, ETHUSDT oder SOLUSDT."),
        ("timeframe", "Text", "Kanonischer Zeitrahmen 1h oder 4h."),
        ("timestamp_utc", "UTC-Zeit", "Beginn der vollstaendigen Marktkerze."),
        ("close_time_utc", "UTC-Zeit", "Letzter in der Quelle enthaltener Zeitpunkt der Kerze."),
        ("decision_time_utc", "UTC-Zeit", "Erster Zeitpunkt nach vollstaendig abgeschlossenem Kerzenschluss."),
        ("segment_id", "Text", "Gemeinsames zusammenhaengendes Zeitsegment; Reset nach jeder Monatsluecke."),
        ("open/high/low/close", "Dezimal", "Handelbare Binance-Spot-OHLC-Werte; keine Heikin-Ashi-Preise."),
        ("volume", "Dezimal", "Gehandeltes Basisasset-Volumen."),
        ("quote_asset_volume", "Dezimal", "Gehandeltes Quote-Asset-Volumen."),
        ("number_of_trades", "Ganzzahl", "Anzahl Trades in der Kerze."),
        ("market_source", "Text", "Binance-Interimherkunft beziehungsweise vollstaendige 1h-Ableitung."),
        ("market_timestamp_unit", "Text", "ms bis 2024-12, us ab 2025-01."),
        ("market_quality_status", "Text", "Nur akzeptierte vollstaendige Phase-1B-Monate."),
        ("context_match_status", "Text", "matched_d1_asof oder unmatched."),
        ("context_source", "Text", "Coin Metrics Community API bei einem Match."),
        ("context_asset", "Text", "Kontextasset btc."),
        ("context_source_timestamp_utc", "UTC-Zeit", "Quelltag des verwendeten Kontextwertes."),
        ("context_available_from_utc_d1", "UTC-Zeit", "Konservativ angenommene D+1-Verfuegbarkeit; muss <= decision_time sein."),
        ("context_available_from_utc_d2", "UTC-Zeit", "Separat erhaltener D+2-Zeitpunkt fuer spaetere Sensitivitaet."),
        ("context_price_usd", "Dezimal", "Coin-Metrics-Metrik PriceUSD."),
        ("context_market_cap_usd", "Dezimal", "Coin-Metrics-Metrik CapMrktCurUSD."),
        ("context_tx_count", "Dezimal", "Coin-Metrics-Metrik TxCnt."),
        ("context_active_address_count", "Dezimal", "Coin-Metrics-Metrik AdrActCnt."),
        ("context_age_seconds", "Ganzzahl", "decision_time minus Kontext-Quellzeitpunkt in Sekunden."),
    ]
    lines = [
        "# Phase 1C-A Datenwoerterbuch",
        "",
        f"Schema: `{DATA_DICTIONARY_SCHEMA_ID}`. Policy: `{PHASE1C_POLICY_ID}`.",
        "",
        "## Tabellenkoernung und Primaerschluessel",
        "",
        "Eine Zeile entspricht genau einem Asset und einer vollstaendigen Binance-Kerze. "
        "Die Tabellen `market_context_1h.csv` und `market_context_4h.csv` sind getrennt. "
        "Der zusammengesetzte Primaerschluessel lautet `(symbol, timeframe, timestamp_utc)`.",
        "",
        "## CSV-Vertrag",
        "",
        "UTF-8, Komma als Trennzeichen, LF-Zeilenenden, Punkt als Dezimalzeichen, keine "
        "Tausendertrennzeichen. UTC wird timezone-aware als `YYYY-MM-DDTHH:MM:SS.ffffffZ` "
        "serialisiert. Ein Nullwert ist ein leeres CSV-Feld; in den realen Ausgaben gibt es "
        "wegen der vollstaendigen D+1-Matches keine Kontext-Nullwerte.",
        "",
        "## Gemeinsame Felder",
        "",
        "| Feld | Typ | Fachliche Bedeutung |",
        "|---|---|---|",
    ]
    lines.extend(f"| `{name}` | {dtype} | {meaning} |" for name, dtype, meaning in common_rows)
    lines.extend(
        [
            "",
            "## Nur 1h",
            "",
            "| Feld | Typ | Fachliche Bedeutung |",
            "|---|---|---|",
            "| `taker_buy_base_volume` | Dezimal | Taker-Buy-Volumen im Basisasset. |",
            "| `taker_buy_quote_volume` | Dezimal | Taker-Buy-Volumen im Quote-Asset. |",
            "",
            "## Nur 4h",
            "",
            "| Feld | Typ | Fachliche Bedeutung |",
            "|---|---|---|",
            "| `constituent_rows` | Ganzzahl | Exakt vier vollstaendige aufeinanderfolgende 1h-Kerzen. |",
            "",
            "## Leakage- und Lueckenregel",
            "",
            "Der D+1-Kontext wird nicht ueber den Quelltag verbunden, sondern nur dann, wenn "
            "`context_available_from_utc_d1 <= decision_time_utc` gilt. D+1 ist eine "
            "konservative methodische Annahme, keine bestaetigte historische "
            "Veroeffentlichungsgarantie. D+2 bleibt fuer eine spaetere Sensitivitaet erhalten.",
            "",
            "Ueber Segmentgrenzen duerfen spaeter keine Renditen, rollenden Indikatoren, "
            "Signale oder Positionen fortgefuehrt werden. Phase 1C-A berechnet davon noch nichts.",
            "",
        ]
    )
    return "\n".join(lines).encode("utf-8")


def _quality_report_markdown(summary: dict[str, Any]) -> bytes:
    global_result = summary["global"]
    lines = [
        "# Phase 1C-A Qualitaetsbericht",
        "",
        "## Teilurteil",
        "",
        "Die kanonischen Processed-Tabellen und der D+1-As-of-Join wurden offline "
        "reproduzierbar erzeugt. Gate 1 bleibt `NOT_EVALUATED`; SQL, EDA und Power BI "
        "sind nicht Bestandteil von Phase 1C-A.",
        "",
        "## Mengen und Abdeckung",
        "",
        f"- 1h Eingabe/Ausgabe: {summary['by_timeframe']['1h']['input_rows']} / {summary['by_timeframe']['1h']['output_rows']}",
        f"- 4h Eingabe/Ausgabe: {summary['by_timeframe']['4h']['input_rows']} / {summary['by_timeframe']['4h']['output_rows']}",
        f"- D+1-Matches: {global_result['matched_rows']} von {global_result['input_rows']} ({global_result['match_rate_percent']:.2f} %)",
        f"- Join-Verluste: {global_result['unmatched_rows']}",
        f"- Zukunftskontext-Verletzungen: {global_result['available_from_after_decision_violations']}",
        f"- Gemeinsame gueltige Monate: {summary['coverage']['shared_allowed_calendar_months']}",
        f"- Gemeinsame Segmente: {global_result['segment_count']}",
        "",
        "## Quellenluecke und konservativer Ausschluss",
        "",
        "Die Raw-Quelle besitzt gegenueber dem Kalender-Soll 42 tatsaechlich fehlende "
        "Stunden. Wegen der verbindlichen Monatspolicy wurden jedoch 21 Asset-Monate "
        "beziehungsweise 15.264 Kalenderstunden vollstaendig ausgeschlossen. Das entspricht "
        "11,61 % konservativ ausgeschlossener und 88,39 % akzeptierter Abdeckung. Diese "
        "beiden Sachverhalte werden nicht gleichgesetzt.",
        "",
        "## Leakage-Schutz",
        "",
        "`decision_time_utc` liegt exakt nach Kerzenschluss: bei ms-Daten plus 1 ms, bei "
        "us-Daten plus 1 us. Der Join verwendet ausschliesslich den neuesten Kontext mit "
        "`available_from_utc_d1 <= decision_time_utc`. Der D+2-Zeitpunkt bleibt separat "
        "gespeichert und wurde noch nicht als Sensitivitaet ausgewertet.",
        "",
        "Die gemeinsame Asset-Maske und Segment-IDs verhindern eine spaetere unbemerkte "
        "Fortsetzung ueber ausgeschlossene Monatsgrenzen. Rolling Features, Renditen, "
        "Signale und Positionen wurden nicht berechnet.",
        "",
        "## Segmentgrenzen",
        "",
        "| Segment | Erster Monat | Letzter Monat | Gueltige Monate |",
        "|---|---|---|---:|",
    ]
    lines.extend(
        f"| {row['segment_id']} | {row['first_month']} | {row['last_month']} | {row['allowed_month_count']} |"
        for row in summary["segments"]
    )
    lines.extend(
        [
            "",
            "## Noch offen",
            "",
            "G1-10 bleibt bis zur unabhaengigen Gesamtpruefung des Joins offen. G1-12 "
            "benoetigt SQL-Schema und Kern-Views. G1-13 benoetigt EDA und den "
            "Power-BI-Datenvertrag.",
            "",
        ]
    )
    return "\n".join(lines).encode("utf-8")


def _ensure_known_output_scope(
    *, project_root: Path, processed_root: Path, report_dir: Path, destinations: Sequence[Path]
) -> None:
    allowed = {path.resolve() for path in destinations}
    existing: set[Path] = set()
    for root in (processed_root, report_dir):
        if root.exists():
            existing.update(path.resolve() for path in root.rglob("*") if path.is_file())
    unknown = sorted(
        (project_relative(path, project_root) for path in existing - allowed),
        key=str.lower,
    )
    if unknown:
        raise SafetyError(f"Unbekannte Phase-1C-Ausgabedateien bleiben unangetastet: {unknown}")


def build_phase1c_outputs(project_root: Path, config_path: Path) -> BuildResult:
    """Validieren, deterministisch bauen und atomar erstellen/wiederverwenden."""

    root = project_root.resolve()
    validated = validate_phase1b_inputs(root, config_path)
    processed_root = safe_project_path(
        root,
        validated.config["paths"]["processed_root"],
        required_prefix="data/processed",
    )
    report_dir = safe_project_path(root, "reports/processed", required_prefix="reports")
    table_1h = processed_root / "market_context_1h.csv"
    table_4h = processed_root / "market_context_4h.csv"
    dictionary_path = report_dir / "PHASE1C_DATA_DICTIONARY.md"
    join_report_path = report_dir / "join_quality_summary.json"
    quality_report_path = report_dir / "PHASE1C_QUALITY_REPORT.md"
    manifest_path = report_dir / "processed_manifest.csv"
    destinations = (
        table_1h,
        table_4h,
        dictionary_path,
        join_report_path,
        quality_report_path,
        manifest_path,
    )
    _ensure_known_output_scope(
        project_root=root,
        processed_root=processed_root,
        report_dir=report_dir,
        destinations=destinations,
    )

    joined = {
        timeframe: asof_join_d1(validated.market_rows[timeframe], validated.contexts)
        for timeframe in ("1h", "4h")
    }
    summary = build_join_summary(joined_by_timeframe=joined, validated=validated)
    bytes_by_path = {
        table_1h: canonical_csv_bytes(PROCESSED_1H_FIELDS, joined["1h"]),
        table_4h: canonical_csv_bytes(PROCESSED_4H_FIELDS, joined["4h"]),
        dictionary_path: _data_dictionary_markdown(),
        join_report_path: canonical_json_bytes(summary),
        quality_report_path: _quality_report_markdown(summary),
    }
    policy_fingerprint = phase1c_policy_fingerprint()
    artifact_metadata = [
        ("market_context_1h", table_1h, "processed_table", PROCESSED_1H_SCHEMA_ID, EXPECTED_1H_ROWS),
        ("market_context_4h", table_4h, "processed_table", PROCESSED_4H_SCHEMA_ID, EXPECTED_4H_ROWS),
        ("data_dictionary", dictionary_path, "data_dictionary", DATA_DICTIONARY_SCHEMA_ID, ""),
        ("join_quality_summary", join_report_path, "quality_report_json", JOIN_REPORT_SCHEMA_ID, 1),
        ("phase1c_quality_report", quality_report_path, "quality_report_markdown", QUALITY_REPORT_SCHEMA_ID, ""),
    ]
    manifest_rows: list[dict[str, Any]] = []
    for artifact_id, path, artifact_type, schema_id, row_count in artifact_metadata:
        manifest_rows.append(
            {
                "artifact_id": artifact_id,
                "artifact_path": project_relative(path, root),
                "artifact_type": artifact_type,
                "schema_id": schema_id,
                "row_count": row_count,
                "sha256": sha256_bytes(bytes_by_path[path]),
                "source_checkpoint": "reports/full_import/execution_checkpoint.json",
                "source_checkpoint_sha256": validated.checkpoint_sha256,
                "source_checkpoint_generation_id": validated.checkpoint["generation_id"],
                "source_checkpoint_run_id": validated.checkpoint["run_id"],
                "phase1c_policy_id": PHASE1C_POLICY_ID,
                "phase1c_policy_fingerprint": policy_fingerprint,
            }
        )
    bytes_by_path[manifest_path] = canonical_csv_bytes(MANIFEST_FIELDS, manifest_rows)

    statuses: dict[str, str] = {}
    for destination in destinations:
        statuses[project_relative(destination, root)] = write_generated_file_cached(
            destination,
            bytes_by_path[destination],
            error_path=project_relative(destination, root),
        )
    artifacts = [
        {
            "path": project_relative(path, root),
            "sha256": sha256_bytes(bytes_by_path[path]),
        }
        for path in destinations
    ]
    return BuildResult(statuses=statuses, summary=summary, artifacts=artifacts)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Phase 1C-A offline aus geprueften Phase-1B-Interimdaten bauen."
    )
    parser.add_argument(
        "--config",
        default="config/full_import.json",
        help="Projekt-relative Phase-1-Konfiguration.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    project_root = Path.cwd().resolve()
    try:
        result = build_phase1c_outputs(project_root, Path(args.config))
    except (IntegrityError, SafetyError, OSError, ValueError) as exc:
        print(f"PHASE 1C-A FEHLGESCHLAGEN: {exc}", file=sys.stderr)
        return 1
    global_result = result.summary["global"]
    print(
        "PHASE 1C-A OK: "
        f"1h={result.summary['by_timeframe']['1h']['output_rows']}, "
        f"4h={result.summary['by_timeframe']['4h']['output_rows']}, "
        f"D+1-Matches={global_result['matched_rows']}/{global_result['input_rows']}, "
        f"Segmente={global_result['segment_count']}."
    )
    print("Gate 1: NOT_EVALUATED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
