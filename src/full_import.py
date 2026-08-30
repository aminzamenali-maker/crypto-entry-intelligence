"""Offline planbare Vollimport-Pipeline fuer historische Kryptodaten.

Der Standardmodus ist ein rein lokaler Dry-Run. Netzwerkzugriffe sind nur
moeglich, wenn sowohl ``--execute`` als auch die exakte Scope-Bestaetigung
angegeben werden. Der in Phase 1A erlaubte Dry-Run erzeugt ausschliesslich
deterministische Planartefakte unter ``reports/full_import``.
"""

from __future__ import annotations

import argparse
import calendar
import csv
import hashlib
import io
import json
import math
import os
import sys
import uuid
import zipfile
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Sequence
from urllib.parse import urlencode, urlparse

import pandas as pd
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from src.data_pilot import BINANCE_COLUMNS, NUMERIC_BINANCE_COLUMNS


EXECUTE_CONFIRMATION = "FULL_IMPORT_2021_2025"
GATE_1_STATUS = "NOT_EVALUATED"
CHECKPOINT_SCHEMA_VERSION = 4
TIMESTAMP_POLICY_ID = "binance_spot_ms_before_2025_us_from_2025"
ANOMALY_EVIDENCE_POLICY_ID = "source_anomalies_all_cached_pairs_v1"
BINANCE_INTERIM_1H_SCHEMA_ID = "binance_1h_market_v1"
LEGACY_PROCESSING_POLICY_FINGERPRINT = (
    "9e75207e0b5a5655366c9513a253adf2325d0f126622774ca2974c0de4533e46"
)
ONE_HOUR_SECONDS = 3600
FOUR_HOUR_SECONDS = 4 * ONE_HOUR_SECONDS
REQUIRED_ASSETS = ("BTCUSDT", "ETHUSDT", "SOLUSDT")
REQUIRED_METRICS = ("PriceUSD", "CapMrktCurUSD", "TxCnt", "AdrActCnt")
BINANCE_INTERIM_1H_FIELDS = (
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
    "taker_buy_base_volume",
    "taker_buy_quote_volume",
    "source",
    "timestamp_unit",
)
EXECUTION_REPORT_FILES = (
    "raw_manifest.csv",
    "binance_quality_summary.csv",
    "source_anomalies.csv",
    "coinmetrics_quality_summary.json",
)
BINANCE_VALUE_COLUMNS = (
    "open",
    "high",
    "low",
    "close",
    "volume",
    "quote_asset_volume",
    "number_of_trades",
    "taker_buy_base_volume",
    "taker_buy_quote_volume",
)
BINANCE_PRICE_COLUMNS = ("open", "high", "low", "close")
BINANCE_VOLUME_COLUMNS = (
    "volume",
    "quote_asset_volume",
    "taker_buy_base_volume",
    "taker_buy_quote_volume",
)
MANIFEST_FIELDS = (
    "source",
    "object_type",
    "symbol_or_asset",
    "period_or_page",
    "url",
    "raw_file",
    "retrieved_at_utc",
    "bytes",
    "sha256",
    "row_count",
    "provider_checksum",
    "provider_checksum_match",
    "cache_status",
)
SOURCE_ANOMALY_FIELDS = (
    "source",
    "symbol",
    "month",
    "anomaly_type",
    "expected_value",
    "actual_value",
    "details",
    "source_integrity_pass",
    "continuity_pass",
    "quality_pass",
    "processing_status",
)
BINANCE_QUALITY_FIELDS = (
    "symbol",
    "month",
    "timestamp_policy_id",
    "expected_timestamp_unit",
    "observed_open_timestamp_unit",
    "observed_close_timestamp_unit",
    "timestamp_unit",
    "timestamp_resolution",
    "rows",
    "expected_rows",
    "expected_4h_rows",
    "row_delta",
    "expected_month_start_utc",
    "actual_month_start_utc",
    "expected_last_open_utc",
    "actual_last_open_utc",
    "expected_month_end_utc",
    "actual_month_end_utc",
    "month_start_mismatch",
    "last_open_mismatch",
    "month_end_mismatch",
    "timestamp_unit_errors",
    "open_times_outside_month",
    "close_times_outside_month",
    "open_alignment_errors",
    "candle_close_time_errors",
    "duplicate_timestamps",
    "not_strictly_increasing",
    "spacing_errors",
    "missing_numeric_values",
    "non_finite_value_count",
    "ohlc_errors",
    "non_positive_price_rows",
    "negative_volume_rows",
    "negative_trade_count_rows",
    "non_integer_trade_count_rows",
    "taker_base_exceeds_total_rows",
    "taker_quote_exceeds_total_rows",
    "missing_open_time_count",
    "missing_open_times_utc",
    "unexpected_open_time_count",
    "unexpected_open_times_utc",
    "spacing_anomalies",
    "close_time_anomalies",
    "source_integrity_pass",
    "continuity_pass",
    "value_quality_pass",
    "quality_pass",
    "processing_status",
    "derived_4h_rows",
    "interim_1h_file",
    "interim_4h_file",
    "interim_1h_status",
    "interim_4h_status",
    "provider_checksum_match",
)


class FullImportError(RuntimeError):
    """Basisklasse fuer kontrolliert gemeldete Pipelinefehler."""


class ConfigurationError(FullImportError):
    """Die Konfiguration ist unvollstaendig, widerspruechlich oder unsicher."""


class SafetyError(FullImportError):
    """Eine Sicherheits- oder Unveraenderlichkeitsregel wurde verletzt."""


class IntegrityError(FullImportError):
    """Eine Datei oder ein Anbieter-Hash ist nicht integer."""


class PartialInterimError(IntegrityError):
    """Ein Monatsauftrag besitzt eine sichere, aber unvollständige Ausgabe."""

    def __init__(self, message: str, evidence: dict[str, Any]) -> None:
        super().__init__(message)
        self.evidence = evidence


@dataclass(frozen=True)
class BinanceTask:
    """Deterministischer Download- und Verarbeitungsauftrag fuer einen Monat."""

    symbol: str
    month: str
    interval: str
    expected_1h_rows: int
    expected_4h_rows: int
    archive_url: str
    checksum_url: str
    archive_path: str
    checksum_path: str


def canonical_json(payload: Any) -> str:
    """Stabiles JSON ohne Laufzeitstempel erzeugen."""

    return (
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
        + "\n"
    )


def sha256_bytes(content: bytes) -> str:
    """SHA-256 fuer Bytes liefern."""

    return hashlib.sha256(content).hexdigest()


def sha256_file(path: Path) -> str:
    """SHA-256 einer Datei blockweise berechnen."""

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def parse_utc_timestamp(value: str, field_name: str) -> datetime:
    """Einen expliziten UTC-Zeitpunkt ohne lokale Zeitzonenannahme lesen."""

    if not isinstance(value, str) or not value.endswith("Z"):
        raise ConfigurationError(f"{field_name} muss mit Z explizit UTC sein.")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ConfigurationError(
            f"{field_name} ist kein gueltiger ISO-8601-Zeitpunkt: {value}"
        ) from exc
    if parsed.tzinfo != timezone.utc:
        raise ConfigurationError(f"{field_name} muss UTC verwenden.")
    return parsed


def parse_iso_date(value: str, field_name: str) -> date:
    """Ein reines Kalenderdatum streng lesen."""

    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise ConfigurationError(
            f"{field_name} ist kein gueltiges ISO-Datum: {value}"
        ) from exc


def safe_project_path(
    project_root: Path,
    configured_path: str,
    *,
    required_prefix: str | None = None,
) -> Path:
    """Nur relative, traversal-freie Pfade innerhalb des Projekts erlauben."""

    if not isinstance(configured_path, str) or not configured_path.strip():
        raise ConfigurationError("Konfigurierter Pfad darf nicht leer sein.")
    candidate = Path(configured_path)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise SafetyError(f"Unsicherer Projektpfad: {configured_path}")
    root = project_root.resolve()
    resolved = (root / candidate).resolve()
    try:
        relative = resolved.relative_to(root)
    except ValueError as exc:
        raise SafetyError(
            f"Pfad liegt ausserhalb des Projekts: {configured_path}"
        ) from exc
    if required_prefix is not None:
        prefix = Path(required_prefix)
        try:
            relative.relative_to(prefix)
        except ValueError as exc:
            raise SafetyError(
                f"Pfad {configured_path} liegt nicht unter {required_prefix}."
            ) from exc
    return resolved


def project_relative(path: Path, project_root: Path) -> str:
    """Projekt-relativen Pfad mit stabilen Schraegstrichen liefern."""

    try:
        return path.resolve().relative_to(project_root.resolve()).as_posix()
    except ValueError as exc:
        raise SafetyError(f"Pfad liegt ausserhalb des Projekts: {path}") from exc


def add_month_start(value: date) -> date:
    """Zum ersten Tag des Folgemonats wechseln."""

    if value.month == 12:
        return date(value.year + 1, 1, 1)
    return date(value.year, value.month + 1, 1)


def month_sequence(start_utc: str, end_exclusive_utc: str) -> list[str]:
    """UTC-Kalendermonate in einer halb-offenen Zeitspanne erzeugen."""

    start = parse_utc_timestamp(start_utc, "binance.start_utc")
    end = parse_utc_timestamp(
        end_exclusive_utc, "binance.end_exclusive_utc"
    )
    if start >= end:
        raise ConfigurationError("Binance-Zeitraum ist leer oder negativ.")
    if (
        start.day,
        start.hour,
        start.minute,
        start.second,
        start.microsecond,
    ) != (1, 0, 0, 0, 0):
        raise ConfigurationError(
            "binance.start_utc muss exakt am UTC-Monatsanfang liegen."
        )
    if (
        end.day,
        end.hour,
        end.minute,
        end.second,
        end.microsecond,
    ) != (1, 0, 0, 0, 0):
        raise ConfigurationError(
            "binance.end_exclusive_utc muss exakt am UTC-Monatsanfang liegen."
        )

    current = start.date()
    months: list[str] = []
    while current < end.date():
        months.append(current.strftime("%Y-%m"))
        current = add_month_start(current)
    return months


def expected_month_rows(month: str, interval_seconds: int) -> int:
    """Erwartete Kalenderzeilen fuer einen ganzen UTC-Monat berechnen."""

    try:
        year_text, month_text = month.split("-")
        year = int(year_text)
        month_number = int(month_text)
        days = calendar.monthrange(year, month_number)[1]
    except (AttributeError, TypeError, ValueError) as exc:
        raise ConfigurationError(f"Ungueltiger Monat: {month}") from exc
    seconds = days * 24 * 60 * 60
    if interval_seconds <= 0 or seconds % interval_seconds:
        raise ConfigurationError(
            "Monatslaenge ist nicht durch das Intervall teilbar."
        )
    return seconds // interval_seconds


def expected_binance_timestamp_unit(month: str) -> str:
    """Verbindliche Binance-Spot-Einheit ausschließlich aus dem Monat ableiten."""

    try:
        parsed = date.fromisoformat(f"{month}-01")
    except (TypeError, ValueError) as exc:
        raise ConfigurationError(f"Ungueltiger Monat: {month}") from exc
    return "ms" if parsed < date(2025, 1, 1) else "us"


def processing_policy_fingerprint() -> str:
    """Stabile fachliche Regeln zusätzlich zur Laufzeitkonfiguration binden."""

    policy = {
        "timestamp_policy_id": TIMESTAMP_POLICY_ID,
        "anomaly_evidence_policy_id": ANOMALY_EVIDENCE_POLICY_ID,
        "binance_interim_1h_schema_id": BINANCE_INTERIM_1H_SCHEMA_ID,
        "binance_interim_1h_fields": list(BINANCE_INTERIM_1H_FIELDS),
        "binance_timestamp_boundary_utc": "2025-01-01T00:00:00Z",
        "before_boundary_unit": "ms",
        "from_boundary_unit": "us",
    }
    return sha256_bytes(canonical_json(policy).encode("utf-8"))


def expected_rows_between(
    start_utc: str, end_exclusive_utc: str, interval_seconds: int
) -> int:
    """Zeilenanzahl fuer eine halb-offene UTC-Zeitspanne berechnen."""

    start = parse_utc_timestamp(start_utc, "start_utc")
    end = parse_utc_timestamp(end_exclusive_utc, "end_exclusive_utc")
    seconds = int((end - start).total_seconds())
    if seconds <= 0 or seconds % interval_seconds:
        raise ConfigurationError(
            "Zeitspanne ist nicht positiv durch das Intervall teilbar."
        )
    return seconds // interval_seconds


def inclusive_day_count(start_date: str, end_date: str) -> int:
    """Kalendertage mit beiden Grenzen zaehlen."""

    start = parse_iso_date(start_date, "start_date")
    end = parse_iso_date(end_date, "end_date")
    if start > end:
        raise ConfigurationError("Taeglicher Zeitraum ist negativ.")
    return (end - start).days + 1


def load_config(config_path: Path, project_root: Path) -> dict[str, Any]:
    """JSON laden und vor jeder weiteren Aktion vollstaendig validieren."""

    root = project_root.resolve()
    path = config_path if config_path.is_absolute() else root / config_path
    try:
        path.resolve().relative_to(root)
    except ValueError as exc:
        raise SafetyError("Konfiguration liegt ausserhalb des Projekts.") from exc
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigurationError(
            f"Konfiguration kann nicht gelesen werden: {path}"
        ) from exc
    validate_config(config, root)
    return config


def validate_config(config: dict[str, Any], project_root: Path) -> None:
    """Den verbindlichen Phase-1-Scope und alle Sicherheitswerte pruefen."""

    required_sections = {
        "schema_version",
        "scope_id",
        "project_timezone",
        "user_agent",
        "binance",
        "coinmetrics",
        "paths",
        "network",
        "safety",
        "expected",
    }
    missing = sorted(required_sections.difference(config))
    if missing:
        raise ConfigurationError(
            "Fehlende Konfigurationsbereiche: " + ", ".join(missing)
        )
    if config["schema_version"] != 1:
        raise ConfigurationError("Nur schema_version 1 wird unterstuetzt.")
    if config["scope_id"] != EXECUTE_CONFIRMATION:
        raise ConfigurationError("scope_id widerspricht der Ausfuehrungssperre.")
    if config["project_timezone"] != "UTC":
        raise ConfigurationError("Interne Projektzeitzone muss UTC sein.")
    if not str(config["user_agent"]).strip():
        raise ConfigurationError("Ein nachvollziehbarer User-Agent ist Pflicht.")

    binance = config["binance"]
    if tuple(binance.get("assets", ())) != REQUIRED_ASSETS:
        raise ConfigurationError(
            "Binance-Assets muessen BTCUSDT, ETHUSDT, SOLUSDT sein."
        )
    if binance.get("market") != "spot":
        raise ConfigurationError("Nur historischer Binance-Spotmarkt ist erlaubt.")
    if binance.get("download_interval") != "1h":
        raise ConfigurationError("Es duerfen nur 1h-Rohkerzen geladen werden.")
    if binance.get("download_interval_seconds") != ONE_HOUR_SECONDS:
        raise ConfigurationError("1h muss exakt 3600 Sekunden entsprechen.")
    if binance.get("derived_intervals") != ["4h"]:
        raise ConfigurationError("4h muss der einzige abgeleitete Zeitrahmen sein.")
    if binance.get("start_utc") != "2021-01-01T00:00:00Z":
        raise ConfigurationError("Unerwarteter Binance-Startzeitpunkt.")
    if binance.get("end_exclusive_utc") != "2026-01-01T00:00:00Z":
        raise ConfigurationError("Unerwartetes exklusives Binance-Ende.")
    parsed_base = urlparse(str(binance.get("base_url", "")))
    if (
        parsed_base.scheme != "https"
        or parsed_base.netloc != "data.binance.vision"
        or not parsed_base.path.endswith("/data/spot/monthly/klines")
    ):
        raise ConfigurationError("Unerwartete Binance-Basis-URL.")

    coinmetrics = config["coinmetrics"]
    endpoint = urlparse(str(coinmetrics.get("endpoint", "")))
    if (
        endpoint.scheme != "https"
        or endpoint.netloc != "community-api.coinmetrics.io"
        or endpoint.path
        != "/v4/timeseries/asset-metrics"
    ):
        raise ConfigurationError("Unerwarteter Coin-Metrics-Endpunkt.")
    if coinmetrics.get("asset") != "btc":
        raise ConfigurationError("Coin Metrics muss das Asset btc verwenden.")
    if coinmetrics.get("frequency") != "1d":
        raise ConfigurationError("Coin-Metrics-Frequenz muss 1d sein.")
    if coinmetrics.get("start_date_inclusive") != "2020-12-30":
        raise ConfigurationError("Coin-Metrics-Start muss 2020-12-30 sein.")
    if coinmetrics.get("end_date_inclusive") != "2025-12-31":
        raise ConfigurationError("Coin-Metrics-Ende muss 2025-12-31 sein.")
    if tuple(coinmetrics.get("metrics", ())) != REQUIRED_METRICS:
        raise ConfigurationError("Coin-Metrics-Felder widersprechen dem Scope.")
    if coinmetrics.get("primary_availability_lag_days") != 1:
        raise ConfigurationError("Primaere Verfuegbarkeitsannahme muss D+1 sein.")
    if coinmetrics.get("sensitivity_availability_lag_days") != 2:
        raise ConfigurationError("Sensitivitaetsannahme muss D+2 sein.")
    if int(coinmetrics.get("page_size", 0)) <= 0:
        raise ConfigurationError("Coin-Metrics-page_size muss positiv sein.")
    if int(coinmetrics.get("max_pages", 0)) <= 0:
        raise ConfigurationError("Coin-Metrics-max_pages muss positiv sein.")

    paths = config["paths"]
    path_rules = {
        "raw_root": "data/raw",
        "interim_root": "data/interim",
        "processed_root": "data/processed",
        "report_root": "reports",
    }
    resolved_paths: list[Path] = []
    for name, prefix in path_rules.items():
        if name not in paths:
            raise ConfigurationError(f"Fehlender Pfad: paths.{name}")
        resolved_paths.append(
            safe_project_path(
                project_root,
                paths[name],
                required_prefix=prefix,
            )
        )
    if len(set(resolved_paths)) != len(resolved_paths):
        raise ConfigurationError("Daten- und Berichtspfade muessen verschieden sein.")

    network = config["network"]
    if int(network.get("timeout_seconds", 0)) <= 0:
        raise ConfigurationError("Netzwerk-Timeout muss positiv sein.")
    if int(network.get("retry_total", -1)) < 0:
        raise ConfigurationError("retry_total darf nicht negativ sein.")
    if float(network.get("backoff_factor", -1)) < 0:
        raise ConfigurationError("backoff_factor darf nicht negativ sein.")

    safety = config["safety"]
    if safety.get("default_mode") != "dry-run":
        raise ConfigurationError("Standardmodus muss dry-run sein.")
    if safety.get("execute_confirmation") != EXECUTE_CONFIRMATION:
        raise ConfigurationError("Falscher Bestaetigungstext in der Konfiguration.")
    for key in (
        "no_overwrite_raw",
        "no_overwrite_interim",
        "no_overwrite_processed",
    ):
        if safety.get(key) is not True:
            raise ConfigurationError(f"safety.{key} muss true sein.")

    months = month_sequence(
        binance["start_utc"], binance["end_exclusive_utc"]
    )
    one_hour_per_asset = sum(
        expected_month_rows(month, ONE_HOUR_SECONDS) for month in months
    )
    four_hour_per_asset = sum(
        expected_month_rows(month, FOUR_HOUR_SECONDS) for month in months
    )
    calculated = {
        "months_per_asset": len(months),
        "binance_archive_tasks": len(months) * len(REQUIRED_ASSETS),
        "binance_checksum_files": len(months) * len(REQUIRED_ASSETS),
        "binance_http_objects": len(months) * len(REQUIRED_ASSETS) * 2,
        "binance_1h_rows_per_asset": one_hour_per_asset,
        "binance_1h_rows_total": one_hour_per_asset * len(REQUIRED_ASSETS),
        "derived_4h_rows_per_asset": four_hour_per_asset,
        "derived_4h_rows_total": four_hour_per_asset * len(REQUIRED_ASSETS),
        "coinmetrics_daily_rows": inclusive_day_count(
            coinmetrics["start_date_inclusive"],
            coinmetrics["end_date_inclusive"],
        ),
    }
    for key, value in calculated.items():
        if config["expected"].get(key) != value:
            raise ConfigurationError(
                f"expected.{key}={config['expected'].get(key)!r}, "
                f"berechnet wurde {value}."
            )


def build_binance_tasks(
    config: dict[str, Any], project_root: Path
) -> list[BinanceTask]:
    """Alle 180 Monatsauftraege in stabiler Reihenfolge erzeugen."""

    validate_config(config, project_root)
    binance = config["binance"]
    raw_root = safe_project_path(
        project_root, config["paths"]["raw_root"], required_prefix="data/raw"
    )
    base_url = binance["base_url"].rstrip("/")
    tasks: list[BinanceTask] = []
    months = month_sequence(
        binance["start_utc"], binance["end_exclusive_utc"]
    )
    for symbol in binance["assets"]:
        for month in months:
            filename = f"{symbol}-1h-{month}.zip"
            archive = (
                raw_root
                / "binance"
                / "spot"
                / "monthly"
                / "klines"
                / symbol
                / "1h"
                / filename
            )
            checksum = archive.with_name(f"{filename}.CHECKSUM")
            archive_url = f"{base_url}/{symbol}/1h/{filename}"
            tasks.append(
                BinanceTask(
                    symbol=symbol,
                    month=month,
                    interval="1h",
                    expected_1h_rows=expected_month_rows(
                        month, ONE_HOUR_SECONDS
                    ),
                    expected_4h_rows=expected_month_rows(
                        month, FOUR_HOUR_SECONDS
                    ),
                    archive_url=archive_url,
                    checksum_url=f"{archive_url}.CHECKSUM",
                    archive_path=project_relative(archive, project_root),
                    checksum_path=project_relative(checksum, project_root),
                )
            )
    return sorted(tasks, key=lambda item: (item.symbol, item.month))


def coinmetrics_initial_url(config: dict[str, Any]) -> str:
    """Deterministische erste Coin-Metrics-Paging-URL erzeugen."""

    source = config["coinmetrics"]
    query = urlencode(
        [
            ("assets", source["asset"]),
            ("metrics", ",".join(source["metrics"])),
            ("frequency", source["frequency"]),
            ("start_time", source["start_date_inclusive"]),
            ("end_time", source["end_date_inclusive"]),
            ("page_size", str(source["page_size"])),
        ]
    )
    return f"{source['endpoint']}?{query}"


def build_download_plan(
    config: dict[str, Any], project_root: Path
) -> list[dict[str, Any]]:
    """Binance-HTTP-Objekte plus logischen Coin-Metrics-Pagingauftrag planen."""

    rows: list[dict[str, Any]] = []
    for task in build_binance_tasks(config, project_root):
        common = {
            "source": "Binance Public Data",
            "symbol_or_asset": task.symbol,
            "period": task.month,
            "interval": task.interval,
            "expected_rows": task.expected_1h_rows,
        }
        rows.append(
            {
                **common,
                "object_type": "archive",
                "url": task.archive_url,
                "local_path": task.archive_path,
            }
        )
        rows.append(
            {
                **common,
                "object_type": "checksum",
                "url": task.checksum_url,
                "local_path": task.checksum_path,
            }
        )

    raw_root = safe_project_path(
        project_root, config["paths"]["raw_root"], required_prefix="data/raw"
    )
    page_pattern = raw_root / "coinmetrics" / "pages" / "page_{page:05d}.json"
    rows.append(
        {
            "source": "Coin Metrics Community API",
            "symbol_or_asset": config["coinmetrics"]["asset"],
            "period": (
                f"{config['coinmetrics']['start_date_inclusive']}"
                f"..{config['coinmetrics']['end_date_inclusive']}"
            ),
            "interval": config["coinmetrics"]["frequency"],
            "object_type": "paged_dataset",
            "url": coinmetrics_initial_url(config),
            "local_path": project_relative(page_pattern, project_root),
            "expected_rows": config["expected"]["coinmetrics_daily_rows"],
        }
    )
    return sorted(
        rows,
        key=lambda row: (
            row["source"],
            row["symbol_or_asset"],
            row["period"],
            row["object_type"],
        ),
    )


def render_csv(rows: Sequence[dict[str, Any]]) -> str:
    """CSV mit stabiler Spaltenfolge und LF-Zeilenenden rendern."""

    fields = (
        "source",
        "symbol_or_asset",
        "period",
        "interval",
        "object_type",
        "url",
        "local_path",
        "expected_rows",
    )
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(
        buffer, fieldnames=fields, lineterminator="\n", extrasaction="raise"
    )
    writer.writeheader()
    writer.writerows(rows)
    return buffer.getvalue()


def _contains_direct_4h(interval: str, url: str, local_path: str) -> bool:
    """Direkten 4h-Bezug aus allen drei planbaren Merkmalen erkennen."""

    normalized_url = urlparse(str(url)).path.lower().replace("\\", "/")
    normalized_path = str(local_path).lower().replace("\\", "/")
    return (
        str(interval).lower() == "4h"
        or "/4h/" in normalized_url
        or "/4h/" in normalized_path
    )


def _path_is_safe(project_root: Path, configured_path: str) -> bool:
    """Einen geplanten Rohpfad tatsaechlich gegen die Pfadregeln pruefen."""

    try:
        safe_project_path(
            project_root, configured_path, required_prefix="data/raw"
        )
    except (ConfigurationError, SafetyError, TypeError):
        return False
    return True


def build_dry_run_summary(
    config: dict[str, Any],
    tasks: Sequence[BinanceTask],
    plan_rows: Sequence[dict[str, Any]],
    project_root: Path,
) -> dict[str, Any]:
    """Alle zentralen Dry-Run-Pruefungen aus den Planobjekten berechnen."""

    task_keys = [
        (task.symbol, task.month, task.interval) for task in tasks
    ]
    plan_urls = [str(row.get("url", "")) for row in plan_rows]
    plan_paths = [str(row.get("local_path", "")) for row in plan_rows]
    binance_rows = [
        row
        for row in plan_rows
        if row.get("source") == "Binance Public Data"
    ]
    archive_rows = [
        row for row in binance_rows if row.get("object_type") == "archive"
    ]
    checksum_rows = [
        row for row in binance_rows if row.get("object_type") == "checksum"
    ]
    duplicate_task_count = len(task_keys) - len(set(task_keys))
    duplicate_url_count = len(plan_urls) - len(set(plan_urls))
    duplicate_path_count = len(plan_paths) - len(set(plan_paths))
    direct_4h_task_count = sum(
        _contains_direct_4h(
            task.interval, task.archive_url, task.archive_path
        )
        or _contains_direct_4h(
            task.interval, task.checksum_url, task.checksum_path
        )
        for task in tasks
    )
    direct_4h_plan_row_count = sum(
        _contains_direct_4h(
            str(row.get("interval", "")),
            str(row.get("url", "")),
            str(row.get("local_path", "")),
        )
        for row in plan_rows
    )
    task_paths = [
        path
        for task in tasks
        for path in (task.archive_path, task.checksum_path)
    ]
    unsafe_paths = [
        path
        for path in [*task_paths, *plan_paths]
        if not _path_is_safe(project_root, path)
    ]
    expected = config["expected"]
    counts = {
        "assets": len(config["binance"]["assets"]),
        "months_per_asset": len(
            month_sequence(
                config["binance"]["start_utc"],
                config["binance"]["end_exclusive_utc"],
            )
        ),
        "binance_archive_tasks": len(tasks),
        "binance_archive_files": len(archive_rows),
        "binance_checksum_files": len(checksum_rows),
        "binance_http_objects": len(binance_rows),
        "unique_binance_tasks": len(set(task_keys)),
        "unique_plan_urls": len(set(plan_urls)),
        "unique_plan_paths": len(set(plan_paths)),
        "duplicate_task_count": duplicate_task_count,
        "duplicate_url_count": duplicate_url_count,
        "duplicate_target_path_count": duplicate_path_count,
        "direct_4h_task_count": direct_4h_task_count,
        "direct_4h_downloads": direct_4h_plan_row_count,
        "unsafe_path_count": len(unsafe_paths),
    }
    expected_counts_match = bool(
        counts["binance_archive_tasks"]
        == expected["binance_archive_tasks"]
        and counts["binance_archive_files"]
        == expected["binance_archive_tasks"]
        and counts["binance_checksum_files"]
        == expected["binance_checksum_files"]
        and counts["binance_http_objects"]
        == expected["binance_http_objects"]
    )
    checks = {
        "expected_counts_match": expected_counts_match,
        "no_duplicate_tasks": duplicate_task_count == 0,
        "no_duplicate_urls": duplicate_url_count == 0,
        "no_duplicate_paths": duplicate_path_count == 0,
        "no_direct_4h_tasks": direct_4h_task_count == 0,
        "no_direct_4h_downloads": direct_4h_plan_row_count == 0,
        "safe_project_paths": len(unsafe_paths) == 0,
        "raw_no_overwrite": config["safety"]["no_overwrite_raw"] is True,
    }
    return {
        "schema_version": 1,
        "scope_id": config["scope_id"],
        "mode": "dry-run",
        "network_used": False,
        "gate_1": GATE_1_STATUS,
        "scope": {
            "assets": config["binance"]["assets"],
            "market": config["binance"]["market"],
            "download_interval": config["binance"]["download_interval"],
            "derived_intervals": config["binance"]["derived_intervals"],
            "start_utc": config["binance"]["start_utc"],
            "end_exclusive_utc": config["binance"]["end_exclusive_utc"],
        },
        "counts": counts,
        "expected_rows": {
            "binance_1h_per_asset": expected["binance_1h_rows_per_asset"],
            "binance_1h_total": expected["binance_1h_rows_total"],
            "derived_4h_per_asset": expected["derived_4h_rows_per_asset"],
            "derived_4h_total": expected["derived_4h_rows_total"],
            "coinmetrics_daily": expected["coinmetrics_daily_rows"],
        },
        "coinmetrics": {
            "asset": config["coinmetrics"]["asset"],
            "frequency": config["coinmetrics"]["frequency"],
            "start_date_inclusive": config["coinmetrics"][
                "start_date_inclusive"
            ],
            "end_date_inclusive": config["coinmetrics"]["end_date_inclusive"],
            "metrics": config["coinmetrics"]["metrics"],
            "primary_availability": "D+1 00:00 UTC",
            "sensitivity_availability": "D+2 00:00 UTC",
        },
        "checks": checks,
        "central_checks_passed": all(checks.values()),
        "next_gate": (
            "Gate 1 wird erst nach einem separat erlaubten, vollstaendig "
            "geprueften Import bewertet."
        ),
    }


def write_dry_run_artifacts(
    config: dict[str, Any],
    project_root: Path,
    *,
    tasks: Sequence[BinanceTask] | None = None,
    plan_rows: Sequence[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Nur die zwei deterministischen, versionierbaren Planartefakte schreiben."""

    actual_tasks = (
        list(tasks)
        if tasks is not None
        else build_binance_tasks(config, project_root)
    )
    actual_plan_rows = (
        list(plan_rows)
        if plan_rows is not None
        else build_download_plan(config, project_root)
    )
    summary = build_dry_run_summary(
        config, actual_tasks, actual_plan_rows, project_root
    )
    if not summary["central_checks_passed"]:
        failed = sorted(
            name
            for name, passed in summary["checks"].items()
            if not passed
        )
        raise IntegrityError(
            "Dry-Run-Zentralpruefung fehlgeschlagen: " + ", ".join(failed)
        )
    report_root = safe_project_path(
        project_root,
        config["paths"]["report_root"],
        required_prefix="reports",
    )
    report_root.mkdir(parents=True, exist_ok=True)
    write_report_atomic(
        report_root / "download_plan.csv",
        render_csv(actual_plan_rows).encode("utf-8"),
    )
    write_report_atomic(
        report_root / "dry_run_summary.json",
        canonical_json(summary).encode("utf-8"),
    )
    return summary


def parse_provider_checksum_text(text: str) -> str:
    """Genau einen plausiblen SHA-256 aus Anbietertext lesen."""

    parts = text.strip().split()
    if not parts:
        raise IntegrityError("Leere Binance-CHECKSUM-Datei.")
    candidate = parts[0].lower()
    if len(candidate) != 64 or any(
        character not in "0123456789abcdef" for character in candidate
    ):
        raise IntegrityError("Ungueltiger Binance-SHA-256.")
    return candidate


def parse_provider_checksum_file(path: Path) -> str:
    """Anbieter-Hash aus einer unveraenderten Rohdatei lesen."""

    return parse_provider_checksum_text(path.read_text(encoding="utf-8"))


def parse_exact_provider_checksum(
    text: str, expected_archive_name: str
) -> str:
    """Genau eine SHA-256-Zeile für genau das erwartete Archiv prüfen."""

    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if len(lines) != 1:
        raise IntegrityError(
            "Binance-CHECKSUM muss genau eine nichtleere Zeile enthalten."
        )
    parts = lines[0].split()
    if len(parts) != 2:
        raise IntegrityError(
            "Binance-CHECKSUM muss genau SHA-256 und Archivname enthalten."
        )
    provider_hash = parse_provider_checksum_text(parts[0])
    referenced_name = parts[1].removeprefix("*")
    if referenced_name != expected_archive_name:
        raise IntegrityError(
            "Binance-CHECKSUM verweist nicht auf das erwartete Archiv."
        )
    return provider_hash


def inspect_binance_cache(
    task: BinanceTask, project_root: Path
) -> dict[str, Any]:
    """Resume-Zustand lesen; bestehende Dateien niemals veraendern."""

    archive = safe_project_path(
        project_root, task.archive_path, required_prefix="data/raw"
    )
    checksum = safe_project_path(
        project_root, task.checksum_path, required_prefix="data/raw"
    )
    archive_exists = archive.is_file()
    checksum_exists = checksum.is_file()
    if not archive_exists and not checksum_exists:
        return {
            "status": "missing_planned_download",
            "archive_exists": False,
            "checksum_exists": False,
        }
    if checksum_exists:
        provider_hash = parse_provider_checksum_file(checksum)
    else:
        provider_hash = ""
    if archive_exists and checksum_exists:
        actual_hash = sha256_file(archive)
        if actual_hash != provider_hash:
            raise IntegrityError(
                "Bestehendes Archiv ist korrupt und bleibt unveraendert: "
                f"{task.archive_path}"
            )
        return {
            "status": "cached_valid",
            "archive_exists": True,
            "checksum_exists": True,
            "sha256": actual_hash,
            "provider_checksum": provider_hash,
        }
    return {
        "status": (
            "missing_checksum" if archive_exists else "missing_archive"
        ),
        "archive_exists": archive_exists,
        "checksum_exists": checksum_exists,
        "provider_checksum": provider_hash,
    }


def atomic_promote_no_overwrite(
    temp_path: Path,
    destination: Path,
    *,
    error_path: str | None = None,
) -> None:
    """Temp-Datei atomar verlinken und ein vorhandenes Ziel nie ersetzen."""

    persisted_path = error_path or destination.as_posix()
    if destination.exists():
        raise FileExistsError(
            f"Zieldatei existiert bereits: {persisted_path}"
        )
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.link(temp_path, destination)
    except FileExistsError:
        raise
    except OSError as exc:
        raise SafetyError(
            "Atomare No-Overwrite-Promotion ist auf diesem Dateisystem "
            f"fehlgeschlagen: {persisted_path}"
        ) from exc
    temp_path.unlink()


def write_bytes_atomic_no_overwrite(
    destination: Path,
    content: bytes,
    *,
    error_path: str | None = None,
) -> None:
    """Bytes via exklusive Temp-Datei und atomare Promotion schreiben."""

    persisted_path = error_path or destination.as_posix()
    if destination.exists():
        raise FileExistsError(
            f"Zieldatei existiert bereits: {persisted_path}"
        )
    temporary = destination.with_name(
        f".{destination.name}.{uuid.uuid4().hex}.part"
    )
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        with temporary.open("xb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        atomic_promote_no_overwrite(
            temporary,
            destination,
            error_path=persisted_path,
        )
    except FileExistsError:
        raise
    except OSError as exc:
        raise SafetyError(
            "Atomares No-Overwrite-Schreiben ist fehlgeschlagen: "
            f"{persisted_path}"
        ) from exc
    finally:
        if temporary.exists():
            temporary.unlink()


def write_generated_file_cached(
    destination: Path,
    content: bytes,
    *,
    error_path: str | None = None,
) -> str:
    """Deterministische Ausgabedatei neu erstellen oder unveraendert nutzen."""

    expected_hash = sha256_bytes(content)
    persisted_path = error_path or destination.as_posix()

    def existing_status() -> str:
        if not destination.is_file():
            raise IntegrityError(
                f"Vorhandenes Ausgabeziel ist keine Datei: {persisted_path}"
            )
        actual_hash = sha256_file(destination)
        if actual_hash != expected_hash:
            raise IntegrityError(
                "Vorhandene erzeugte Datei weicht vom deterministischen "
                f"Inhalt ab und bleibt unveraendert: {persisted_path}"
            )
        return "cached_valid"

    if destination.exists():
        return existing_status()
    try:
        write_bytes_atomic_no_overwrite(
            destination,
            content,
            error_path=persisted_path,
        )
    except FileExistsError:
        return existing_status()
    return "created"


def build_session(config: dict[str, Any]) -> requests.Session:
    """HTTP-Sitzung erst nach bestandener CLI-Sicherheitssperre erstellen."""

    network = config["network"]
    retries = int(network["retry_total"])
    retry = Retry(
        total=retries,
        connect=retries,
        read=retries,
        status=retries,
        backoff_factor=float(network["backoff_factor"]),
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset({"GET"}),
        respect_retry_after_header=True,
    )
    session = requests.Session()
    session.headers.update(
        {"User-Agent": config["user_agent"], "Accept": "*/*"}
    )
    session.mount("https://", HTTPAdapter(max_retries=retry))
    return session


def get_response_bytes(
    session: requests.Session, url: str, timeout_seconds: int
) -> bytes:
    """Ein historisches HTTP-Objekt vollstaendig und fehlersensitiv lesen."""

    parsed = urlparse(url)
    safe_url = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
    try:
        response = session.get(url, timeout=timeout_seconds)
    except requests.RequestException as exc:
        raise FullImportError(
            f"Netzwerkfehler fuer {safe_url}; Details werden aus "
            "Sicherheitsgründen nicht protokolliert."
        ) from exc
    if response.status_code != 200:
        raise FullImportError(
            f"HTTP {response.status_code} fuer {safe_url}; "
            "Antworttext wird aus Sicherheitsgründen nicht protokolliert."
        )
    return response.content


def ensure_binance_task(
    task: BinanceTask,
    project_root: Path,
    session: requests.Session,
    timeout_seconds: int,
) -> list[dict[str, Any]]:
    """Einen Monatscache fortsetzen, pruefen und niemals ueberschreiben."""

    archive = safe_project_path(
        project_root, task.archive_path, required_prefix="data/raw"
    )
    checksum = safe_project_path(
        project_root, task.checksum_path, required_prefix="data/raw"
    )
    initial = inspect_binance_cache(task, project_root)

    if not checksum.exists():
        checksum_bytes = get_response_bytes(
            session, task.checksum_url, timeout_seconds
        )
        parse_provider_checksum_text(checksum_bytes.decode("utf-8"))
        write_bytes_atomic_no_overwrite(
            checksum,
            checksum_bytes,
            error_path=task.checksum_path,
        )
    provider_hash = parse_provider_checksum_file(checksum)

    if not archive.exists():
        archive_bytes = get_response_bytes(
            session, task.archive_url, timeout_seconds
        )
        actual_hash = sha256_bytes(archive_bytes)
        if actual_hash != provider_hash:
            raise IntegrityError(
                f"Download-Hash stimmt fuer {task.archive_url} nicht; "
                "es wurde kein Archiv gespeichert."
            )
        write_bytes_atomic_no_overwrite(
            archive,
            archive_bytes,
            error_path=task.archive_path,
        )
    actual_hash = sha256_file(archive)
    if actual_hash != provider_hash:
        raise IntegrityError(
            "Archiv-Hash stimmt nicht; Datei bleibt unveraendert: "
            f"{task.archive_path}"
        )

    cache_status = (
        "cached_valid"
        if initial["status"] == "cached_valid"
        else "downloaded_or_resumed"
    )
    common = {
        "source": "Binance Public Data",
        "symbol_or_asset": task.symbol,
        "period_or_page": task.month,
        "retrieved_at_utc": datetime.now(timezone.utc).isoformat(
            timespec="seconds"
        ),
        "provider_checksum": provider_hash,
        "provider_checksum_match": True,
        "cache_status": cache_status,
    }
    return [
        {
            **common,
            "object_type": "archive",
            "url": task.archive_url,
            "raw_file": task.archive_path,
            "bytes": archive.stat().st_size,
            "sha256": actual_hash,
            "row_count": "",
        },
        {
            **common,
            "object_type": "checksum",
            "url": task.checksum_url,
            "raw_file": task.checksum_path,
            "bytes": checksum.stat().st_size,
            "sha256": sha256_file(checksum),
            "row_count": "",
        },
    ]


def validate_coinmetrics_next_url(next_url: str, endpoint: str) -> None:
    """Paging auf denselben offiziellen HTTPS-Endpunkt begrenzen."""

    parsed = urlparse(next_url)
    expected = urlparse(endpoint)
    if (
        parsed.scheme != "https"
        or parsed.netloc != expected.netloc
        or parsed.path != expected.path
    ):
        safe_url = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
        raise SafetyError(
            f"Unsichere Coin-Metrics-Paging-URL: {safe_url}"
        )


def download_coinmetrics_pages(
    config: dict[str, Any],
    project_root: Path,
    session: requests.Session,
    on_page_completed: Callable[
        [dict[str, Any], list[dict[str, Any]]], None
    ]
    | None = None,
    on_phase: Callable[[str, int], None] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Alle API-Seiten unveraendert speichern und Paging fortsetzen."""

    source = config["coinmetrics"]
    raw_root = safe_project_path(
        project_root, config["paths"]["raw_root"], required_prefix="data/raw"
    )
    page_root = raw_root / "coinmetrics" / "pages"
    next_url: str | None = coinmetrics_initial_url(config)
    page_number = 1
    records: list[dict[str, Any]] = []
    manifest: list[dict[str, Any]] = []
    while next_url:
        if page_number > int(source["max_pages"]):
            raise FullImportError("Coin-Metrics-max_pages wurde ueberschritten.")
        validate_coinmetrics_next_url(next_url, source["endpoint"])
        page_path = page_root / f"page_{page_number:05d}.json"
        if on_phase is not None:
            on_phase("coinmetrics_page_fetch", page_number)
        if page_path.exists():
            content = page_path.read_bytes()
            cache_status = "cached_existing"
        else:
            content = get_response_bytes(
                session, next_url, int(config["network"]["timeout_seconds"])
            )
            cache_status = "downloaded"
        if on_phase is not None:
            on_phase("coinmetrics_page_parse", page_number)
        try:
            payload = json.loads(content)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise IntegrityError(
                "Ungueltige Coin-Metrics-JSON-Seite: "
                f"{project_relative(page_path, project_root)}"
            ) from exc
        page_records = payload.get("data")
        if not isinstance(page_records, list):
            raise IntegrityError(
                "Coin-Metrics-Seite ohne data-Liste: "
                f"{project_relative(page_path, project_root)}"
            )
        candidate = payload.get("next_page_url")
        if candidate is not None and not isinstance(candidate, str):
            raise IntegrityError("next_page_url muss Text oder null sein.")
        if on_phase is not None:
            on_phase("coinmetrics_page_persist", page_number)
        if not page_path.exists():
            write_bytes_atomic_no_overwrite(
                page_path,
                content,
                error_path=project_relative(page_path, project_root),
            )
        records.extend(page_records)
        endpoint = urlparse(source["endpoint"])
        manifest_row = {
            "source": "Coin Metrics Community API",
            "object_type": "raw_page",
            "symbol_or_asset": source["asset"],
            "period_or_page": str(page_number),
            "url": f"{endpoint.scheme}://{endpoint.netloc}{endpoint.path}",
            "raw_file": project_relative(page_path, project_root),
            "retrieved_at_utc": datetime.now(timezone.utc).isoformat(
                timespec="seconds"
            ),
            "bytes": page_path.stat().st_size,
            "sha256": sha256_file(page_path),
            "row_count": len(page_records),
            "provider_checksum": "",
            "provider_checksum_match": "",
            "cache_status": cache_status,
        }
        manifest.append(manifest_row)
        if on_page_completed is not None:
            on_page_completed(dict(manifest_row), list(page_records))
        next_url = candidate or None
        page_number += 1
    return records, manifest


def parse_binance_archive(
    archive_path: Path, task: BinanceTask
) -> pd.DataFrame:
    """Eine Monats-ZIP nach der verbindlichen Monatseinheit lesen."""

    with zipfile.ZipFile(archive_path) as archive:
        members = [
            member for member in archive.namelist() if member.endswith(".csv")
        ]
        if len(members) != 1:
            raise IntegrityError(
                f"{task.archive_path} enthaelt nicht genau eine CSV-Datei."
            )
        try:
            with archive.open(members[0]) as handle:
                frame = pd.read_csv(handle, header=None)
        except pd.errors.ParserError as exc:
            raise IntegrityError(
                f"Ungueltige Binance-Spaltenstruktur: {task.archive_path}"
            ) from exc
    if len(frame.columns) != len(BINANCE_COLUMNS):
        raise IntegrityError(
            f"{task.archive_path} enthaelt {len(frame.columns)} statt exakt "
            f"{len(BINANCE_COLUMNS)} Binance-Spalten."
        )
    frame.columns = BINANCE_COLUMNS
    for column in NUMERIC_BINANCE_COLUMNS:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    expected_unit = expected_binance_timestamp_unit(task.month)

    def classify(value: Any) -> str:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return "unsupported"
        if not math.isfinite(number) or not number.is_integer():
            return "unsupported"
        magnitude = abs(number)
        if 10**12 <= magnitude < 10**14:
            return "ms"
        if 10**15 <= magnitude < 10**17:
            return "us"
        return "unsupported"

    open_units = frame["open_time_raw"].map(classify)
    close_units = frame["close_time_raw"].map(classify)

    def observed_label(units: pd.Series) -> str:
        distinct = sorted(set(units.astype(str)))
        return distinct[0] if len(distinct) == 1 else "mixed:" + "|".join(distinct)

    observed_open = observed_label(open_units)
    observed_close = observed_label(close_units)
    timestamp_unit_errors = int(open_units.ne(expected_unit).sum())
    timestamp_unit_errors += int(close_units.ne(expected_unit).sum())
    timestamp_unit_errors += int(
        (open_units != close_units).sum()
    )
    frame["timestamp_utc"] = pd.to_datetime(
        frame["open_time_raw"], unit=expected_unit, utc=True, errors="coerce"
    )
    frame["close_time_utc"] = pd.to_datetime(
        frame["close_time_raw"], unit=expected_unit, utc=True, errors="coerce"
    )
    frame["symbol"] = task.symbol
    frame["timeframe"] = "1h"
    frame["source"] = "binance_public_data"
    frame["timestamp_policy_id"] = TIMESTAMP_POLICY_ID
    frame["expected_timestamp_unit"] = expected_unit
    frame["observed_open_timestamp_unit"] = observed_open
    frame["observed_close_timestamp_unit"] = observed_close
    frame["timestamp_unit_errors"] = timestamp_unit_errors
    frame["timestamp_unit"] = expected_unit
    ordered_columns = [
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
        "taker_buy_base_volume",
        "taker_buy_quote_volume",
        "source",
        "timestamp_policy_id",
        "expected_timestamp_unit",
        "observed_open_timestamp_unit",
        "observed_close_timestamp_unit",
        "timestamp_unit_errors",
        "timestamp_unit",
    ]
    return frame[ordered_columns].reset_index(drop=True)


def validate_binance_month(
    frame: pd.DataFrame, task: BinanceTask
) -> dict[str, Any]:
    """Schema, Monatszeiten und alle relevanten Binance-Werte pruefen."""

    if frame.empty:
        raise IntegrityError(f"Leerer Binance-Monat: {task.symbol} {task.month}")
    required_columns = {
        "timestamp_utc",
        "close_time_utc",
        "timestamp_unit",
        *BINANCE_VALUE_COLUMNS,
    }
    missing_columns = sorted(required_columns.difference(frame.columns))
    if missing_columns:
        raise IntegrityError(
            "Normalisiertes Binance-Schema unvollstaendig: "
            + ", ".join(missing_columns)
        )
    ordered = frame.reset_index(drop=True)
    start = pd.Timestamp(f"{task.month}-01", tz="UTC")
    next_start = start + pd.offsets.MonthBegin(1)
    expected_last_open = next_start - pd.Timedelta(hours=1)
    expected_timestamp_unit = expected_binance_timestamp_unit(task.month)
    units = set(ordered["timestamp_unit"].dropna().astype(str))
    timestamp_unit = next(iter(units)) if len(units) == 1 else ""
    resolution_by_unit = {
        "ms": pd.Timedelta(milliseconds=1),
        "us": pd.Timedelta(microseconds=1),
    }
    resolution = resolution_by_unit.get(timestamp_unit)
    observed_open_timestamp_unit = str(
        ordered.get(
            "observed_open_timestamp_unit",
            pd.Series([timestamp_unit]),
        ).iloc[0]
    )
    observed_close_timestamp_unit = str(
        ordered.get(
            "observed_close_timestamp_unit",
            pd.Series([timestamp_unit]),
        ).iloc[0]
    )
    precomputed_unit_errors = int(
        pd.to_numeric(
            ordered.get("timestamp_unit_errors", pd.Series([0])),
            errors="coerce",
        )
        .fillna(1)
        .max()
    )
    policy_ids = set(
        ordered.get(
            "timestamp_policy_id", pd.Series([TIMESTAMP_POLICY_ID])
        )
        .dropna()
        .astype(str)
    )
    declared_expected_units = set(
        ordered.get(
            "expected_timestamp_unit",
            pd.Series([expected_timestamp_unit]),
        )
        .dropna()
        .astype(str)
    )
    timestamp_unit_errors = precomputed_unit_errors + int(
        len(units) != 1
        or timestamp_unit not in resolution_by_unit
        or timestamp_unit != expected_timestamp_unit
        or observed_open_timestamp_unit != expected_timestamp_unit
        or observed_close_timestamp_unit != expected_timestamp_unit
        or policy_ids != {TIMESTAMP_POLICY_ID}
        or declared_expected_units != {expected_timestamp_unit}
    )
    resolution = resolution_by_unit.get(expected_timestamp_unit)
    if resolution is None:
        resolution = pd.Timedelta(0)
    expected_month_end = next_start - resolution
    actual_start = ordered["timestamp_utc"].iloc[0]
    actual_last_open = ordered["timestamp_utc"].iloc[-1]
    actual_month_end = ordered["close_time_utc"].iloc[-1]

    numeric = ordered[list(BINANCE_VALUE_COLUMNS)]
    missing_numeric_values = int(numeric.isna().sum().sum())
    non_finite_value_count = 0
    for column in BINANCE_VALUE_COLUMNS:
        non_null = numeric[column].dropna()
        non_finite_value_count += int(
            sum(not math.isfinite(float(value)) for value in non_null)
        )
    ohlc_errors = int(
        (
            (ordered["high"] < ordered[["open", "close", "low"]].max(axis=1))
            | (
                ordered["low"]
                > ordered[["open", "close", "high"]].min(axis=1)
            )
        ).sum()
    )
    time_differences = ordered["timestamp_utc"].diff().dropna()
    duplicate_timestamps = int(ordered["timestamp_utc"].duplicated().sum())
    not_strictly_increasing = int(
        time_differences.le(pd.Timedelta(0)).sum()
    )
    spacing_errors = int(time_differences.ne(pd.Timedelta(hours=1)).sum())
    open_alignment_errors = int(
        ordered["timestamp_utc"].ne(
            ordered["timestamp_utc"].dt.floor("1h")
        ).sum()
    )
    expected_close_times = (
        ordered["timestamp_utc"] + pd.Timedelta(hours=1) - resolution
    )
    close_error_mask = ordered["close_time_utc"].ne(expected_close_times)
    candle_close_time_errors = int(close_error_mask.sum())
    open_times_outside_month = int(
        (
            ordered["timestamp_utc"].lt(start)
            | ordered["timestamp_utc"].ge(next_start)
        ).sum()
    )
    close_times_outside_month = int(
        (
            ordered["close_time_utc"].lt(start)
            | ordered["close_time_utc"].ge(next_start)
        ).sum()
    )
    trade_values = ordered["number_of_trades"]
    finite_trade_mask = trade_values.notna() & trade_values.map(
        lambda value: math.isfinite(float(value))
    )
    non_integer_trade_rows = int(
        (
            finite_trade_mask
            & trade_values.mod(1).abs().gt(1e-12)
        ).sum()
    )
    expected_open_times = pd.date_range(
        start, next_start, freq="1h", inclusive="left"
    )
    actual_open_times = pd.DatetimeIndex(ordered["timestamp_utc"])
    missing_open_times = expected_open_times.difference(actual_open_times)
    unexpected_open_times = actual_open_times.difference(expected_open_times)
    spacing_anomalies: list[dict[str, Any]] = []
    for index in time_differences.index[
        time_differences.ne(pd.Timedelta(hours=1))
    ]:
        spacing_anomalies.append(
            {
                "previous_open_utc": ordered.loc[
                    index - 1, "timestamp_utc"
                ].isoformat(),
                "current_open_utc": ordered.loc[
                    index, "timestamp_utc"
                ].isoformat(),
                "gap_seconds": float(time_differences.loc[index].total_seconds()),
            }
        )
    close_time_anomalies = [
        {
            "open_utc": ordered.loc[index, "timestamp_utc"].isoformat(),
            "actual_close_utc": ordered.loc[
                index, "close_time_utc"
            ].isoformat(),
            "expected_close_utc": expected_close_times.loc[index].isoformat(),
        }
        for index in ordered.index[close_error_mask]
    ]
    result = {
        "symbol": task.symbol,
        "month": task.month,
        "timestamp_policy_id": TIMESTAMP_POLICY_ID,
        "expected_timestamp_unit": expected_timestamp_unit,
        "observed_open_timestamp_unit": observed_open_timestamp_unit,
        "observed_close_timestamp_unit": observed_close_timestamp_unit,
        "timestamp_unit": timestamp_unit,
        "timestamp_resolution": (
            timestamp_unit if timestamp_unit in resolution_by_unit else ""
        ),
        "rows": int(len(ordered)),
        "expected_rows": task.expected_1h_rows,
        "expected_4h_rows": task.expected_4h_rows,
        "row_delta": int(len(ordered) - task.expected_1h_rows),
        "expected_month_start_utc": start.isoformat(),
        "actual_month_start_utc": actual_start.isoformat(),
        "expected_last_open_utc": expected_last_open.isoformat(),
        "actual_last_open_utc": actual_last_open.isoformat(),
        "expected_month_end_utc": expected_month_end.isoformat(),
        "actual_month_end_utc": actual_month_end.isoformat(),
        "month_start_mismatch": int(actual_start != start),
        "last_open_mismatch": int(actual_last_open != expected_last_open),
        "month_end_mismatch": int(actual_month_end != expected_month_end),
        "timestamp_unit_errors": timestamp_unit_errors,
        "open_times_outside_month": open_times_outside_month,
        "close_times_outside_month": close_times_outside_month,
        "open_alignment_errors": open_alignment_errors,
        "candle_close_time_errors": candle_close_time_errors,
        "duplicate_timestamps": duplicate_timestamps,
        "not_strictly_increasing": not_strictly_increasing,
        "spacing_errors": spacing_errors,
        "missing_numeric_values": missing_numeric_values,
        "non_finite_value_count": non_finite_value_count,
        "ohlc_errors": ohlc_errors,
        "non_positive_price_rows": int(
            ordered[list(BINANCE_PRICE_COLUMNS)].le(0).any(axis=1).sum()
        ),
        "negative_volume_rows": int(
            ordered[list(BINANCE_VOLUME_COLUMNS)].lt(0).any(axis=1).sum()
        ),
        "negative_trade_count_rows": int(trade_values.lt(0).sum()),
        "non_integer_trade_count_rows": non_integer_trade_rows,
        "taker_base_exceeds_total_rows": int(
            ordered["taker_buy_base_volume"].gt(ordered["volume"]).sum()
        ),
        "taker_quote_exceeds_total_rows": int(
            ordered["taker_buy_quote_volume"]
            .gt(ordered["quote_asset_volume"])
            .sum()
        ),
        "missing_open_time_count": int(len(missing_open_times)),
        "missing_open_times_utc": json.dumps(
            [timestamp.isoformat() for timestamp in missing_open_times],
            ensure_ascii=False,
            separators=(",", ":"),
        ),
        "unexpected_open_time_count": int(len(unexpected_open_times)),
        "unexpected_open_times_utc": json.dumps(
            [timestamp.isoformat() for timestamp in unexpected_open_times],
            ensure_ascii=False,
            separators=(",", ":"),
        ),
        "spacing_anomalies": json.dumps(
            spacing_anomalies,
            ensure_ascii=False,
            separators=(",", ":"),
        ),
        "close_time_anomalies": json.dumps(
            close_time_anomalies,
            ensure_ascii=False,
            separators=(",", ":"),
        ),
    }
    continuity_error_fields = (
        "month_start_mismatch",
        "last_open_mismatch",
        "month_end_mismatch",
        "open_times_outside_month",
        "close_times_outside_month",
        "candle_close_time_errors",
        "spacing_errors",
        "missing_open_time_count",
        "unexpected_open_time_count",
    )
    integrity_error_fields = ("timestamp_unit_errors",)
    value_error_fields = (
        "open_alignment_errors",
        "duplicate_timestamps",
        "not_strictly_increasing",
        "missing_numeric_values",
        "non_finite_value_count",
        "ohlc_errors",
        "non_positive_price_rows",
        "negative_volume_rows",
        "negative_trade_count_rows",
        "non_integer_trade_count_rows",
        "taker_base_exceeds_total_rows",
        "taker_quote_exceeds_total_rows",
    )
    result["source_integrity_pass"] = bool(
        all(result[field] == 0 for field in integrity_error_fields)
    )
    result["continuity_pass"] = bool(
        result["rows"] == result["expected_rows"]
        and all(result[field] == 0 for field in continuity_error_fields)
    )
    result["value_quality_pass"] = bool(
        all(result[field] == 0 for field in value_error_fields)
    )
    result["quality_pass"] = bool(
        result["source_integrity_pass"]
        and result["continuity_pass"]
        and result["value_quality_pass"]
    )
    if not result["source_integrity_pass"]:
        result["processing_status"] = "source_integrity_failure"
    elif not result["value_quality_pass"]:
        result["processing_status"] = "quality_quarantine"
    elif not result["continuity_pass"]:
        result["processing_status"] = "source_continuity_anomaly"
    else:
        result["processing_status"] = "valid"
    return result


def aggregate_complete_1h_to_4h(frame: pd.DataFrame) -> pd.DataFrame:
    """Nur vier vollstaendige und zeilenweise gueltige 1h-Kerzen nutzen."""

    if frame.empty:
        return pd.DataFrame()
    required = {
        "symbol",
        "timestamp_utc",
        "close_time_utc",
        "timestamp_unit",
        *BINANCE_VALUE_COLUMNS,
    }
    missing = sorted(required.difference(frame.columns))
    if missing:
        raise IntegrityError(
            "4h-Aggregation fehlen gepruefte 1h-Felder: "
            + ", ".join(missing)
        )
    ordered = frame.sort_values("timestamp_utc").copy()
    ordered["bucket_utc"] = ordered["timestamp_utc"].dt.floor("4h")
    resolutions = ordered["timestamp_unit"].map(
        {
            "ms": pd.Timedelta(milliseconds=1),
            "us": pd.Timedelta(microseconds=1),
        }
    )
    valid_rows = (
        resolutions.notna()
        & ordered["timestamp_utc"].eq(
            ordered["timestamp_utc"].dt.floor("1h")
        )
        & ordered["close_time_utc"].eq(
            ordered["timestamp_utc"] + pd.Timedelta(hours=1) - resolutions
        )
    )
    numeric = ordered[list(BINANCE_VALUE_COLUMNS)]
    valid_rows &= numeric.notna().all(axis=1)
    for column in BINANCE_VALUE_COLUMNS:
        valid_rows &= ordered[column].map(
            lambda value: math.isfinite(float(value))
        )
    valid_rows &= ordered[list(BINANCE_PRICE_COLUMNS)].gt(0).all(axis=1)
    valid_rows &= ordered[list(BINANCE_VOLUME_COLUMNS)].ge(0).all(axis=1)
    valid_rows &= ordered["number_of_trades"].ge(0)
    valid_rows &= ordered["number_of_trades"].mod(1).abs().le(1e-12)
    valid_rows &= ordered["high"].ge(
        ordered[["open", "close", "low"]].max(axis=1)
    )
    valid_rows &= ordered["low"].le(
        ordered[["open", "close", "high"]].min(axis=1)
    )
    valid_rows &= ordered["taker_buy_base_volume"].le(ordered["volume"])
    valid_rows &= ordered["taker_buy_quote_volume"].le(
        ordered["quote_asset_volume"]
    )
    ordered["row_quality_pass"] = valid_rows
    complete_parts: list[pd.DataFrame] = []
    for bucket, group in ordered.groupby("bucket_utc", sort=True):
        expected = pd.date_range(
            bucket, periods=4, freq="1h", tz="UTC"
        )
        actual = pd.DatetimeIndex(group["timestamp_utc"])
        if (
            len(group) == 4
            and actual.equals(expected)
            and bool(group["row_quality_pass"].all())
        ):
            complete_parts.append(group)
    if not complete_parts:
        return pd.DataFrame(
            columns=[
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
            ]
        )
    complete = pd.concat(complete_parts, ignore_index=True)
    grouped = complete.groupby("bucket_utc", sort=True)
    result = grouped.agg(
        symbol=("symbol", "first"),
        timestamp_utc=("bucket_utc", "first"),
        close_time_utc=("close_time_utc", "max"),
        open=("open", "first"),
        high=("high", "max"),
        low=("low", "min"),
        close=("close", "last"),
        volume=("volume", "sum"),
        quote_asset_volume=("quote_asset_volume", "sum"),
        number_of_trades=("number_of_trades", "sum"),
        constituent_rows=("timestamp_utc", "size"),
    ).reset_index(drop=True)
    result.insert(1, "timeframe", "4h")
    result["source"] = "derived_from_complete_1h"
    return result


def normalize_coinmetrics_records(
    records: Sequence[dict[str, Any]], config: dict[str, Any]
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Tageswerte exakt abgrenzen und finite, nichtnegative Werte verlangen."""

    source = config["coinmetrics"]
    frame = pd.DataFrame(records)
    required = {"asset", "time", *source["metrics"]}
    missing = sorted(required.difference(frame.columns))
    if missing:
        raise IntegrityError(
            "Coin-Metrics-Schema unvollstaendig: " + ", ".join(missing)
        )
    frame["source_timestamp_utc"] = pd.to_datetime(
        frame["time"], utc=True, errors="raise"
    )
    for metric in source["metrics"]:
        frame[metric] = pd.to_numeric(frame[metric], errors="coerce")
    frame["available_from_utc_d1"] = frame["source_timestamp_utc"] + pd.Timedelta(
        days=source["primary_availability_lag_days"]
    )
    frame["available_from_utc_d2"] = frame["source_timestamp_utc"] + pd.Timedelta(
        days=source["sensitivity_availability_lag_days"]
    )
    frame = frame[
        [
            "asset",
            "source_timestamp_utc",
            "available_from_utc_d1",
            "available_from_utc_d2",
            *source["metrics"],
        ]
    ].sort_values("source_timestamp_utc").reset_index(drop=True)
    expected_start = pd.Timestamp(source["start_date_inclusive"], tz="UTC")
    expected_end = pd.Timestamp(source["end_date_inclusive"], tz="UTC")
    asset_mismatch_count = int(
        frame["asset"].isna().sum()
        + (
            frame["asset"].notna()
            & frame["asset"].astype(str).ne(source["asset"])
        ).sum()
    )
    values = frame[source["metrics"]]
    non_finite = int(
        (~values.apply(lambda column: column.map(math.isfinite))).sum().sum()
    )
    negative = int(values.lt(0).sum().sum())
    quality = {
        "rows": int(len(frame)),
        "expected_rows": config["expected"]["coinmetrics_daily_rows"],
        "expected_asset": source["asset"],
        "asset_mismatch_count": asset_mismatch_count,
        "start_match": bool(
            not frame.empty
            and frame["source_timestamp_utc"].iloc[0] == expected_start
        ),
        "end_match": bool(
            not frame.empty
            and frame["source_timestamp_utc"].iloc[-1] == expected_end
        ),
        "duplicate_timestamps": int(
            frame["source_timestamp_utc"].duplicated().sum()
        ),
        "spacing_errors": int(
            frame["source_timestamp_utc"]
            .diff()
            .dropna()
            .ne(pd.Timedelta(days=1))
            .sum()
        ),
        "non_finite_metric_values": non_finite,
        "negative_metric_values": negative,
    }
    quality["quality_pass"] = bool(
        quality["rows"] == quality["expected_rows"]
        and quality["start_match"]
        and quality["end_match"]
        and all(
            quality[field] == 0
            for field in (
                "asset_mismatch_count",
                "duplicate_timestamps",
                "spacing_errors",
                "non_finite_metric_values",
                "negative_metric_values",
            )
        )
    )
    return frame, quality


def dataframe_csv_bytes(frame: pd.DataFrame) -> bytes:
    """DataFrame als stabiles UTF-8-CSV mit LF serialisieren."""

    return frame.to_csv(index=False, lineterminator="\n").encode("utf-8")


def project_binance_interim_1h(frame: pd.DataFrame) -> pd.DataFrame:
    """Nur den stabilen Marktvertrag in die 1h-Interimdatei übernehmen."""

    if not frame.columns.is_unique:
        raise IntegrityError(
            "Binance-1h-Interimprojektion enthält doppelte Spaltennamen."
        )
    missing = [
        field for field in BINANCE_INTERIM_1H_FIELDS if field not in frame.columns
    ]
    if missing:
        raise IntegrityError(
            "Binance-1h-Interimprojektion ist unvollständig: "
            + ", ".join(missing)
        )
    projected = frame.loc[:, list(BINANCE_INTERIM_1H_FIELDS)].copy()
    if tuple(projected.columns) != BINANCE_INTERIM_1H_FIELDS:
        raise IntegrityError(
            "Binance-1h-Interimprojektion verletzt den kanonischen Vertrag."
        )
    return projected


def write_coinmetrics_interim_context(
    context: pd.DataFrame,
    config: dict[str, Any],
    project_root: Path,
) -> dict[str, str]:
    """Deterministischen Coin-Metrics-Kontext erstellen oder wiederverwenden."""

    interim_root = safe_project_path(
        project_root,
        config["paths"]["interim_root"],
        required_prefix="data/interim",
    )
    context_path = interim_root / "coinmetrics" / "btc_daily_context.csv"
    status = write_generated_file_cached(
        context_path,
        dataframe_csv_bytes(context),
        error_path=project_relative(context_path, project_root),
    )
    return {
        "path": project_relative(context_path, project_root),
        "status": status,
    }


def process_binance_task(
    task: BinanceTask, config: dict[str, Any], project_root: Path
) -> dict[str, Any]:
    """Einen geprueften Monat in getrennte 1h-/4h-Interimdateien schreiben."""

    archive = safe_project_path(
        project_root, task.archive_path, required_prefix="data/raw"
    )
    frame_1h = parse_binance_archive(archive, task)
    quality = validate_binance_month(frame_1h, task)
    interim_root = safe_project_path(
        project_root,
        config["paths"]["interim_root"],
        required_prefix="data/interim",
    )
    month_root = interim_root / "binance" / task.symbol
    one_hour_path = month_root / "1h" / f"{task.symbol}-1h-{task.month}.csv"
    four_hour_path = month_root / "4h" / f"{task.symbol}-4h-{task.month}.csv"
    if not quality["source_integrity_pass"]:
        return {
            **quality,
            "derived_4h_rows": 0,
            "interim_1h_file": project_relative(one_hour_path, project_root),
            "interim_4h_file": project_relative(four_hour_path, project_root),
            "interim_1h_status": "rejected_source_integrity",
            "interim_4h_status": "rejected_source_integrity",
        }
    if not quality["value_quality_pass"]:
        return {
            **quality,
            "derived_4h_rows": 0,
            "interim_1h_file": project_relative(one_hour_path, project_root),
            "interim_4h_file": project_relative(four_hour_path, project_root),
            "interim_1h_status": "quarantined_value_quality",
            "interim_4h_status": "quarantined_value_quality",
        }
    if not quality["continuity_pass"]:
        return {
            **quality,
            "derived_4h_rows": 0,
            "interim_1h_file": project_relative(one_hour_path, project_root),
            "interim_4h_file": project_relative(four_hour_path, project_root),
            "interim_1h_status": "skipped_source_continuity_anomaly",
            "interim_4h_status": "skipped_source_continuity_anomaly",
        }

    frame_4h = aggregate_complete_1h_to_4h(frame_1h)
    if len(frame_4h) != task.expected_4h_rows:
        raise IntegrityError(
            f"Unvollstaendige 4h-Ableitung: {task.symbol} {task.month}"
        )
    one_hour_status = write_generated_file_cached(
        one_hour_path,
        dataframe_csv_bytes(project_binance_interim_1h(frame_1h)),
        error_path=project_relative(one_hour_path, project_root),
    )
    try:
        four_hour_status = write_generated_file_cached(
            four_hour_path,
            dataframe_csv_bytes(frame_4h),
            error_path=project_relative(four_hour_path, project_root),
        )
    except Exception as exc:
        evidence = {
            **quality,
            "derived_4h_rows": 0,
            "interim_1h_file": project_relative(one_hour_path, project_root),
            "interim_4h_file": project_relative(four_hour_path, project_root),
            "interim_1h_status": one_hour_status,
            "interim_4h_status": "write_failed",
        }
        raise PartialInterimError(
            "4h-Interimschreiben fehlgeschlagen, nachdem die 1h-Ausgabe "
            f"sicher geschrieben wurde: {task.symbol} {task.month}",
            evidence,
        ) from exc
    return {
        **quality,
        "derived_4h_rows": int(len(frame_4h)),
        "interim_1h_file": project_relative(one_hour_path, project_root),
        "interim_4h_file": project_relative(four_hour_path, project_root),
        "interim_1h_status": one_hour_status,
        "interim_4h_status": four_hour_status,
    }


def render_dict_rows_csv(
    rows: Sequence[dict[str, Any]], fieldnames: Sequence[str]
) -> bytes:
    """Dictionary-Zeilen mit fester Spaltenfolge stabil als CSV rendern."""

    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(
        buffer,
        fieldnames=fieldnames,
        lineterminator="\n",
        extrasaction="raise",
    )
    writer.writeheader()
    writer.writerows(rows)
    return buffer.getvalue().encode("utf-8")


def write_report_atomic(destination: Path, content: bytes) -> None:
    """Nur vorgesehene Berichte atomar aktualisieren."""

    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(
        f".{destination.name}.{uuid.uuid4().hex}.part"
    )
    try:
        with temporary.open("xb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
    finally:
        if temporary.exists():
            temporary.unlink()


def build_source_anomaly_rows(
    quality: dict[str, Any],
) -> list[dict[str, Any]]:
    """Nur tatsaechlich gemessene Kontinuitaetsabweichungen ausgeben."""

    if quality.get("processing_status") != "source_continuity_anomaly":
        return []
    common = {
        "source": "Binance Public Data",
        "symbol": quality["symbol"],
        "month": quality["month"],
        "source_integrity_pass": quality["source_integrity_pass"],
        "continuity_pass": quality["continuity_pass"],
        "quality_pass": quality["quality_pass"],
        "processing_status": quality["processing_status"],
    }
    rows: list[dict[str, Any]] = []
    if quality["rows"] != quality["expected_rows"]:
        rows.append(
            {
                **common,
                "anomaly_type": "row_count_mismatch",
                "expected_value": quality["expected_rows"],
                "actual_value": quality["rows"],
                "details": f"row_delta={quality['row_delta']}",
            }
        )
    for timestamp in json.loads(quality["missing_open_times_utc"]):
        rows.append(
            {
                **common,
                "anomaly_type": "missing_open_time",
                "expected_value": timestamp,
                "actual_value": "",
                "details": "Keine synthetische Kerze erzeugt.",
            }
        )
    for timestamp in json.loads(quality["unexpected_open_times_utc"]):
        rows.append(
            {
                **common,
                "anomaly_type": "unexpected_open_time",
                "expected_value": "",
                "actual_value": timestamp,
                "details": "Unerwarteter Kerzenbeginn in Anbieterdatei.",
            }
        )
    for anomaly in json.loads(quality["spacing_anomalies"]):
        rows.append(
            {
                **common,
                "anomaly_type": "irregular_hour_spacing",
                "expected_value": "3600 seconds",
                "actual_value": f"{anomaly['gap_seconds']} seconds",
                "details": json.dumps(
                    anomaly, ensure_ascii=False, separators=(",", ":")
                ),
            }
        )
    for anomaly in json.loads(quality["close_time_anomalies"]):
        rows.append(
            {
                **common,
                "anomaly_type": "candle_close_time_mismatch",
                "expected_value": anomaly["expected_close_utc"],
                "actual_value": anomaly["actual_close_utc"],
                "details": f"open_utc={anomaly['open_utc']}",
            }
        )
    return rows


def config_fingerprint(config: dict[str, Any]) -> str:
    """Die vollständige, geheimnisfreie Importkonfiguration stabil binden."""

    return sha256_bytes(canonical_json(config).encode("utf-8"))


def _manifest_key(row: dict[str, Any]) -> tuple[str, str, str, str]:
    return (
        str(row.get("source", "")),
        str(row.get("object_type", "")),
        str(row.get("symbol_or_asset", "")),
        str(row.get("period_or_page", "")),
    )


def _quality_key(row: dict[str, Any]) -> tuple[str, str]:
    return str(row.get("symbol", "")), str(row.get("month", ""))


def _anomaly_key(row: dict[str, Any]) -> tuple[str, ...]:
    return tuple(
        str(row.get(field, ""))
        for field in SOURCE_ANOMALY_FIELDS
    )


def _anomaly_sort_key(row: dict[str, Any]) -> tuple[Any, ...]:
    type_order = {
        "row_count_mismatch": 0,
        "missing_open_time": 1,
        "unexpected_open_time": 2,
        "irregular_hour_spacing": 3,
        "candle_close_time_mismatch": 4,
    }
    return (
        str(row.get("symbol", "")),
        str(row.get("month", "")),
        type_order.get(str(row.get("anomaly_type", "")), 99),
        _anomaly_key(row),
    )


def _upsert_manifest(
    rows: list[dict[str, Any]], row: dict[str, Any]
) -> None:
    """Ein Rohobjekt deduplizieren und Hashänderungen fail-closed behandeln."""

    key = _manifest_key(row)
    for index, existing in enumerate(rows):
        if _manifest_key(existing) != key:
            continue
        for field in ("raw_file", "sha256", "bytes", "row_count"):
            old = str(existing.get(field, ""))
            new = str(row.get(field, ""))
            if old and new and old != new:
                raise IntegrityError(
                    f"Rohobjekt {key} widerspricht dem Checkpoint bei {field}."
                )
        stable = dict(row)
        stable["retrieved_at_utc"] = existing.get(
            "retrieved_at_utc", row.get("retrieved_at_utc", "")
        )
        rows[index] = stable
        return
    rows.append(dict(row))


def _upsert_by_key(
    rows: list[dict[str, Any]],
    row: dict[str, Any],
    key_function: Callable[[dict[str, Any]], tuple[Any, ...]],
) -> None:
    key = key_function(row)
    for index, existing in enumerate(rows):
        if key_function(existing) == key:
            rows[index] = dict(row)
            return
    rows.append(dict(row))


def _merge_anomalies(
    existing: list[dict[str, Any]], new_rows: Sequence[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Befunde stabil deduplizieren, ohne gemessene Zeilen zu verlieren."""

    merged = [dict(row) for row in existing]
    keys = {_anomaly_key(row) for row in merged}
    for row in new_rows:
        key = _anomaly_key(row)
        if key not in keys:
            merged.append(dict(row))
            keys.add(key)
    return merged


def _read_preexisting_source_anomalies(
    report_root: Path,
    *,
    config: dict[str, Any],
    project_root: Path,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Gesamte lokale Cacheevidenz unabhängig von der CSV neu berechnen."""

    path = report_root / "source_anomalies.csv"
    rows: list[dict[str, Any]] = []
    if path.exists():
        try:
            with path.open("r", encoding="utf-8", newline="") as handle:
                reader = csv.reader(handle)
                header = next(reader, None)
                if tuple(header or ()) != SOURCE_ANOMALY_FIELDS:
                    raise IntegrityError(
                        "Vorbestehende source_anomalies.csv hat ein "
                        "unerwartetes Schema."
                    )
                for record in reader:
                    if len(record) != len(SOURCE_ANOMALY_FIELDS):
                        raise IntegrityError(
                            "Vorbestehende source_anomalies.csv hat in "
                            f"CSV-Zeile {reader.line_num} nicht exakt "
                            f"{len(SOURCE_ANOMALY_FIELDS)} Spalten."
                        )
                    row = dict(zip(SOURCE_ANOMALY_FIELDS, record))
                    if (
                        tuple(row.keys()) != SOURCE_ANOMALY_FIELDS
                        or any(value is None for value in row.values())
                    ):
                        raise IntegrityError(
                            "Vorbestehende source_anomalies.csv enthält "
                            "fehlende oder zusätzliche Felder."
                        )
                    rows.append(row)
        except (OSError, UnicodeDecodeError, csv.Error) as exc:
            raise IntegrityError(
                "Vorbestehende source_anomalies.csv ist nicht sicher lesbar."
            ) from exc

    allowed_assets = set(config["binance"]["assets"])
    allowed_months = set(
        month_sequence(
            config["binance"]["start_utc"],
            config["binance"]["end_exclusive_utc"],
        )
    )
    for row in rows:
        if row["source"] != "Binance Public Data":
            raise IntegrityError(
                "Vorbestehende source_anomalies.csv hat eine "
                "unzulässige Quelle."
            )
        if row["symbol"] not in allowed_assets:
            raise IntegrityError(
                "Vorbestehende source_anomalies.csv enthält ein "
                "nicht erlaubtes Asset."
            )
        try:
            expected_month_rows(row["month"], ONE_HOUR_SECONDS)
        except ConfigurationError as exc:
            raise IntegrityError(
                "Vorbestehende source_anomalies.csv enthält einen "
                "syntaktisch ungültigen Monat."
            ) from exc
        if row["month"] not in allowed_months:
            raise IntegrityError(
                "Vorbestehende source_anomalies.csv enthält einen "
                "Monat außerhalb des Importumfangs."
            )
        if row["processing_status"] != "source_continuity_anomaly":
            raise IntegrityError(
                "Vorbestehende Anomalie besitzt einen unzulässigen Status."
            )
        for field in (
            "source_integrity_pass",
            "continuity_pass",
            "quality_pass",
        ):
            if row[field] not in {"True", "False"}:
                raise IntegrityError(
                    f"Vorbestehende Anomalie hat ungültiges Bool-Feld {field}."
                )
            row[field] = row[field] == "True"
    if len({_anomaly_key(row) for row in rows}) != len(rows):
        raise IntegrityError(
            "Vorbestehende source_anomalies.csv enthält doppelte Zeilen."
        )

    tasks = build_binance_tasks(config, project_root)
    complete_cached_tasks: list[BinanceTask] = []
    for task in tasks:
        archive_path = safe_project_path(
            project_root, task.archive_path, required_prefix="data/raw"
        )
        checksum_path = safe_project_path(
            project_root, task.checksum_path, required_prefix="data/raw"
        )
        if archive_path.is_file() and checksum_path.is_file():
            complete_cached_tasks.append(task)

    recomputed: list[dict[str, Any]] = []
    for task in complete_cached_tasks:
        symbol = task.symbol
        month = task.month
        archive_path = safe_project_path(
            project_root, task.archive_path, required_prefix="data/raw"
        )
        checksum_path = safe_project_path(
            project_root, task.checksum_path, required_prefix="data/raw"
        )
        try:
            provider_hash = parse_exact_provider_checksum(
                checksum_path.read_text(encoding="utf-8"),
                archive_path.name,
            )
            actual_hash = sha256_file(archive_path)
            if actual_hash != provider_hash:
                raise IntegrityError(
                    "Raw-Archiv und gespeicherter Anbieterprüfwert "
                    "stimmen nicht überein."
                )
            frame = parse_binance_archive(archive_path, task)
            quality = validate_binance_month(frame, task)
        except (
            OSError,
            UnicodeDecodeError,
            zipfile.BadZipFile,
            pd.errors.ParserError,
        ) as exc:
            raise IntegrityError(
                "Anomalieevidenz ist wegen einer unlesbaren lokalen "
                f"Quelldatei nicht belegbar: {symbol} {month}."
            ) from exc
        if (
            not quality["source_integrity_pass"]
            or not quality["value_quality_pass"]
        ):
            raise IntegrityError(
                "Anomalieevidenz ist wegen einer Raw-Integritäts- oder "
                f"Wertqualitätsverletzung nicht belegbar: {symbol} {month}."
            )
        recomputed.extend(build_source_anomaly_rows(quality))

    canonical = sorted(recomputed, key=_anomaly_sort_key)
    anomalous_groups = {
        (row["symbol"], row["month"]) for row in canonical
    }
    common_provenance = {
        "source_file": "source_anomalies.csv",
        "verified_cached_months": len(complete_cached_tasks),
        "verified_asset_months": len(anomalous_groups),
        "timestamp_policy_id": TIMESTAMP_POLICY_ID,
        "anomaly_evidence_policy_id": ANOMALY_EVIDENCE_POLICY_ID,
    }
    if not path.exists():
        if canonical:
            return canonical, {
                **common_provenance,
                "mode": "recomputed_from_cached_raw",
                "sha256": "",
                "rows": len(canonical),
                "source_rows": 0,
            }
        return [], {
            **common_provenance,
            "mode": "none",
            "sha256": "",
            "rows": 0,
            "source_rows": 0,
        }

    submitted = sorted(rows, key=_anomaly_sort_key)
    if [_anomaly_key(row) for row in submitted] != [
        _anomaly_key(row) for row in canonical
    ]:
        raise IntegrityError(
            "Vorbestehende source_anomalies.csv stimmt nicht vollständig "
            "mit der Gesamt-Neuberechnung aller vollständigen lokalen "
            "Raw/CHECKSUM-Paare überein."
        )
    return canonical, {
        **common_provenance,
        "mode": "validated_preexisting_csv",
        "sha256": sha256_file(path),
        "rows": len(rows),
        "source_rows": len(rows),
    }


def _new_authoritative_state(
    report_root: Path,
    *,
    config: dict[str, Any],
    project_root: Path,
) -> dict[str, Any]:
    legacy_reports = [
        report_root / name
        for name in (
            "raw_manifest.csv",
            "binance_quality_summary.csv",
            "coinmetrics_quality_summary.json",
        )
        if (report_root / name).exists()
    ]
    if legacy_reports:
        raise IntegrityError(
            "Ausführungsberichte ohne autoritativen Checkpoint erfordern "
            "eine explizite Migration: "
            + ", ".join(path.name for path in legacy_reports)
        )
    anomalies, provenance = _read_preexisting_source_anomalies(
        report_root, config=config, project_root=project_root
    )
    return {
        "manifest": [],
        "quality": [],
        "anomalies": anomalies,
        "partial_interim": [],
        "coinmetrics_pages": [],
        "coinmetrics_quality": None,
        "anomaly_provenance": provenance,
        "last_safe_completed_task": "",
        "_run_id": uuid.uuid4().hex,
        "_generation_id": 0,
        "_execution_status": "IN_PROGRESS",
        "_last_error": None,
        "_policy_migration": None,
        "_legacy_policy_migration_pending": False,
        "_coinmetrics_phase": "",
        "_coinmetrics_pages_attempted": 0,
    }


def _interim_status_evidence(
    state: dict[str, Any],
) -> list[dict[str, Any]]:
    evidence = []
    for quality in [*state["quality"], *state["partial_interim"]]:
        evidence.append(
            {
                "symbol": quality["symbol"],
                "month": quality["month"],
                "interim_1h_file": quality.get("interim_1h_file", ""),
                "interim_1h_status": quality.get("interim_1h_status", ""),
                "interim_4h_file": quality.get("interim_4h_file", ""),
                "interim_4h_status": quality.get("interim_4h_status", ""),
                "task_complete": quality in state["quality"],
            }
        )
    return evidence


def _quality_continuity_interval_count(
    quality: dict[str, Any],
) -> int:
    """Gemessene Zeitbereiche vereinigen, ohne eine Ursache zu behaupten."""

    if quality.get("processing_status") != "source_continuity_anomaly":
        return 0
    intervals: list[tuple[pd.Timestamp, pd.Timestamp]] = []
    for value in json.loads(quality.get("missing_open_times_utc", "[]")):
        start = pd.Timestamp(value)
        intervals.append((start, start + pd.Timedelta(hours=1)))
    for value in json.loads(quality.get("unexpected_open_times_utc", "[]")):
        start = pd.Timestamp(value)
        intervals.append((start, start + pd.Timedelta(hours=1)))
    for row in json.loads(quality.get("spacing_anomalies", "[]")):
        previous = pd.Timestamp(row["previous_open_utc"])
        current = pd.Timestamp(row["current_open_utc"])
        intervals.append((previous + pd.Timedelta(hours=1), current))
    for row in json.loads(quality.get("close_time_anomalies", "[]")):
        start = pd.Timestamp(row["open_utc"])
        intervals.append((start, start + pd.Timedelta(hours=1)))
    if not intervals:
        return 1
    merged: list[list[pd.Timestamp]] = []
    for start, end in sorted(intervals, key=lambda value: value[0]):
        if not merged or start > merged[-1][1]:
            merged.append([start, end])
        else:
            merged[-1][1] = max(merged[-1][1], end)
    return len(merged)


def aggregate_execution_counts(
    config: dict[str, Any], state: dict[str, Any]
) -> dict[str, Any]:
    """Soll, Raw-Ist und akzeptierte Istwerte ohne Vermischung ausweisen."""

    expected = config["expected"]
    qualities = state["quality"]
    observed_rows = [*qualities, *state["partial_interim"]]
    all_status_rows = observed_rows
    interim_statuses = [
        row.get(field, "")
        for row in all_status_rows
        for field in ("interim_1h_status", "interim_4h_status")
    ]
    counts: dict[str, Any] = {
        "scope_expected_1h_rows": expected["binance_1h_rows_total"],
        "completed_months_expected_1h_rows": sum(
            int(row["expected_rows"]) for row in qualities
        ),
        "observed_raw_1h_rows": sum(
            int(row["rows"]) for row in observed_rows
        ),
        "accepted_interim_1h_rows": sum(
            int(row["rows"])
            for row in observed_rows
            if row.get("interim_1h_status") in {"created", "cached_valid"}
        ),
        "skipped_anomalous_raw_1h_rows": sum(
            int(row["rows"])
            for row in qualities
            if row.get("processing_status") == "source_continuity_anomaly"
        ),
        "scope_expected_4h_rows": expected["derived_4h_rows_total"],
        "completed_months_expected_4h_rows": sum(
            int(row["expected_4h_rows"]) for row in qualities
        ),
        "accepted_interim_4h_rows": sum(
            int(row["derived_4h_rows"])
            for row in qualities
            if row.get("interim_4h_status") in {"created", "cached_valid"}
        ),
        "source_anomaly_rows": len(state["anomalies"]),
        "continuity_anomaly_months": sum(
            row.get("processing_status") == "source_continuity_anomaly"
            for row in qualities
        ),
        "continuity_anomaly_intervals": sum(
            _quality_continuity_interval_count(row) for row in qualities
        ),
        "interim_created": interim_statuses.count("created"),
        "interim_cached_valid": interim_statuses.count("cached_valid"),
        "interim_skipped": interim_statuses.count(
            "skipped_source_continuity_anomaly"
        ),
        "interim_quarantined": interim_statuses.count(
            "quarantined_value_quality"
        ),
    }
    counts["raw_1h_row_delta"] = (
        counts["observed_raw_1h_rows"]
        - counts["completed_months_expected_1h_rows"]
    )
    counts["accepted_1h_row_delta"] = (
        counts["accepted_interim_1h_rows"]
        - counts["completed_months_expected_1h_rows"]
    )
    counts["accepted_4h_row_delta"] = (
        counts["accepted_interim_4h_rows"]
        - counts["completed_months_expected_4h_rows"]
    )
    per_asset: dict[str, dict[str, int]] = {}
    for asset in REQUIRED_ASSETS:
        rows = [row for row in qualities if row["symbol"] == asset]
        observed_asset_rows = [
            row for row in observed_rows if row["symbol"] == asset
        ]
        asset_interim_statuses = [
            row.get(field, "")
            for row in observed_asset_rows
            for field in ("interim_1h_status", "interim_4h_status")
        ]
        asset_counts = {
            "scope_expected_1h_rows": int(
                expected["binance_1h_rows_per_asset"]
            ),
            "completed_months_expected_1h_rows": sum(
                int(row["expected_rows"]) for row in rows
            ),
            "observed_raw_1h_rows": sum(
                int(row["rows"]) for row in observed_asset_rows
            ),
            "accepted_interim_1h_rows": sum(
                int(row["rows"])
                for row in observed_asset_rows
                if row.get("interim_1h_status")
                in {"created", "cached_valid"}
            ),
            "skipped_anomalous_raw_1h_rows": sum(
                int(row["rows"])
                for row in rows
                if row.get("processing_status")
                == "source_continuity_anomaly"
            ),
            "scope_expected_4h_rows": int(
                expected["derived_4h_rows_per_asset"]
            ),
            "completed_months_expected_4h_rows": sum(
                int(row["expected_4h_rows"]) for row in rows
            ),
            "accepted_interim_4h_rows": sum(
                int(row["derived_4h_rows"])
                for row in rows
                if row.get("interim_4h_status")
                in {"created", "cached_valid"}
            ),
            "source_anomaly_rows": sum(
                row.get("symbol") == asset for row in state["anomalies"]
            ),
            "continuity_anomaly_months": sum(
                row.get("processing_status") == "source_continuity_anomaly"
                for row in rows
            ),
            "continuity_anomaly_intervals": sum(
                _quality_continuity_interval_count(row) for row in rows
            ),
            "interim_created": asset_interim_statuses.count("created"),
            "interim_cached_valid": asset_interim_statuses.count(
                "cached_valid"
            ),
            "interim_skipped": asset_interim_statuses.count(
                "skipped_source_continuity_anomaly"
            ),
            "interim_quarantined": asset_interim_statuses.count(
                "quarantined_value_quality"
            ),
        }
        asset_counts["raw_1h_row_delta"] = (
            asset_counts["observed_raw_1h_rows"]
            - asset_counts["completed_months_expected_1h_rows"]
        )
        asset_counts["accepted_1h_row_delta"] = (
            asset_counts["accepted_interim_1h_rows"]
            - asset_counts["completed_months_expected_1h_rows"]
        )
        asset_counts["accepted_4h_row_delta"] = (
            asset_counts["accepted_interim_4h_rows"]
            - asset_counts["completed_months_expected_4h_rows"]
        )
        per_asset[asset] = asset_counts
    counts["per_asset"] = per_asset
    return counts


def _coinmetrics_projection(
    state: dict[str, Any], *, generation_id: int
) -> bytes:
    quality = state["coinmetrics_quality"]
    payload = {
        "checkpoint_schema_version": CHECKPOINT_SCHEMA_VERSION,
        "scope_id": state["_scope_id"],
        "run_id": state["_run_id"],
        "generation_id": generation_id,
        "projection_status": (
            "available" if quality is not None else "not_available_for_generation"
        ),
        "quality": quality,
    }
    return canonical_json(payload).encode("utf-8")


def _report_projection_bytes(
    state: dict[str, Any], *, generation_id: int
) -> dict[str, bytes]:
    return {
        "raw_manifest.csv": render_dict_rows_csv(
            state["manifest"], MANIFEST_FIELDS
        ),
        "binance_quality_summary.csv": render_dict_rows_csv(
            state["quality"], BINANCE_QUALITY_FIELDS
        ),
        "source_anomalies.csv": render_dict_rows_csv(
            state["anomalies"], SOURCE_ANOMALY_FIELDS
        ),
        "coinmetrics_quality_summary.json": _coinmetrics_projection(
            state, generation_id=generation_id
        ),
    }


def execution_checkpoint(
    *,
    config: dict[str, Any],
    state: dict[str, Any],
    status: str,
    generation_id: int,
    projection_hashes: dict[str, str],
    error: Exception | str | None = None,
    affected_task: str = "",
) -> dict[str, Any]:
    """Vollständigen autoritativen und wiederherstellbaren Zustand erzeugen."""

    aggregates = aggregate_execution_counts(config, state)
    if error is None:
        last_error = None
        error_text = ""
    elif isinstance(error, Exception):
        last_error = {
            "type": type(error).__name__,
            "message": str(error),
            "affected_task": affected_task,
            "phase": state.get("_coinmetrics_phase", ""),
        }
        error_text = f"{type(error).__name__}: {error}"
    else:
        last_error = {
            "type": "FullImportError",
            "message": str(error),
            "affected_task": affected_task,
            "phase": state.get("_coinmetrics_phase", ""),
        }
        error_text = str(error)
    return {
        "checkpoint_schema_version": CHECKPOINT_SCHEMA_VERSION,
        "schema_version": CHECKPOINT_SCHEMA_VERSION,
        "timestamp_policy_id": TIMESTAMP_POLICY_ID,
        "anomaly_evidence_policy_id": ANOMALY_EVIDENCE_POLICY_ID,
        "binance_interim_1h_schema_id": BINANCE_INTERIM_1H_SCHEMA_ID,
        "processing_policy_fingerprint": processing_policy_fingerprint(),
        "scope_id": config["scope_id"],
        "config_fingerprint": config_fingerprint(config),
        "run_id": state["_run_id"],
        "generation_id": generation_id,
        "execution_status": status,
        "status": status,
        "gate_1": GATE_1_STATUS,
        "last_safe_completed_task": state["last_safe_completed_task"],
        "failed_task": affected_task if error is not None else "",
        "evidence": {
            "raw_manifest": state["manifest"],
            "binance_monthly_quality": state["quality"],
            "source_anomalies": state["anomalies"],
            "interim_output_status": _interim_status_evidence(state),
            "partial_interim_outputs": state["partial_interim"],
            "coinmetrics_pages": state["coinmetrics_pages"],
            "coinmetrics_quality": state["coinmetrics_quality"],
            "source_anomaly_provenance": state["anomaly_provenance"],
        },
        "aggregate_counts": aggregates,
        "report_generation": {
            "generation_id": generation_id,
            "projection_hashes": projection_hashes,
        },
        "coinmetrics_progress": {
            "phase": state.get("_coinmetrics_phase", ""),
            "pages_attempted": int(
                state.get("_coinmetrics_pages_attempted", 0)
            ),
            "pages_completed": len(state["coinmetrics_pages"]),
        },
        "last_error": last_error,
        "error": error_text,
        "policy_migration": state.get("_policy_migration"),
        "checked_raw_objects": len(state["manifest"]),
        "provider_checksums_passed": sum(
            row["object_type"] == "archive"
            and row["provider_checksum_match"] is True
            for row in state["manifest"]
        ),
        "checked_months": len(state["quality"]),
        **{
            key: value
            for key, value in aggregates.items()
            if key != "per_asset"
        },
    }


def persist_authoritative_state(
    *,
    config: dict[str, Any],
    state: dict[str, Any],
    report_root: Path,
    status: str,
    error: Exception | str | None = None,
    affected_task: str = "",
) -> dict[str, Any]:
    """Checkpoint zuerst sichern, danach seine vier Projektionen materialisieren."""

    generation_id = int(state["_generation_id"]) + 1
    state["_scope_id"] = config["scope_id"]
    projections = _report_projection_bytes(
        state, generation_id=generation_id
    )
    projection_hashes = {
        name: sha256_bytes(content) for name, content in projections.items()
    }
    checkpoint = execution_checkpoint(
        config=config,
        state=state,
        status=status,
        generation_id=generation_id,
        projection_hashes=projection_hashes,
        error=error,
        affected_task=affected_task,
    )
    write_report_atomic(
        report_root / "execution_checkpoint.json",
        canonical_json(checkpoint).encode("utf-8"),
    )
    state["_generation_id"] = generation_id
    state["_execution_status"] = status
    state["_last_error"] = checkpoint["last_error"]
    state["_legacy_policy_migration_pending"] = False
    for name in EXECUTION_REPORT_FILES:
        write_report_atomic(report_root / name, projections[name])
    return checkpoint


def _state_from_checkpoint(
    checkpoint: dict[str, Any],
) -> dict[str, Any]:
    evidence = checkpoint["evidence"]
    return {
        "manifest": [dict(row) for row in evidence["raw_manifest"]],
        "quality": [
            dict(row) for row in evidence["binance_monthly_quality"]
        ],
        "anomalies": [dict(row) for row in evidence["source_anomalies"]],
        "partial_interim": [
            dict(row) for row in evidence["partial_interim_outputs"]
        ],
        "coinmetrics_pages": [
            dict(row) for row in evidence["coinmetrics_pages"]
        ],
        "coinmetrics_quality": evidence["coinmetrics_quality"],
        "anomaly_provenance": dict(
            evidence["source_anomaly_provenance"]
        ),
        "last_safe_completed_task": checkpoint["last_safe_completed_task"],
        "_run_id": checkpoint["run_id"],
        "_generation_id": checkpoint["generation_id"],
        "_execution_status": checkpoint["execution_status"],
        "_last_error": checkpoint["last_error"],
        "_policy_migration": checkpoint.get("policy_migration"),
        "_legacy_policy_migration_pending": False,
        "_scope_id": checkpoint["scope_id"],
        "_coinmetrics_phase": checkpoint.get("coinmetrics_progress", {}).get(
            "phase", ""
        ),
        "_coinmetrics_pages_attempted": int(
            checkpoint.get("coinmetrics_progress", {}).get(
                "pages_attempted", len(evidence["coinmetrics_pages"])
            )
        ),
    }


def _validate_policy_migration_provenance(
    migration: dict[str, Any] | None,
    *,
    checkpoint_generation: int,
) -> None:
    """Eine bereits persistierte Policy-Migration streng binden."""

    if migration is None:
        return
    if not isinstance(migration, dict):
        raise IntegrityError("Checkpoint-Policy-Migration ist ungültig.")
    expected = {
        "migration_id": "legacy_schema4_hard_failure_to_binance_1h_market_v1",
        "source_processing_policy_fingerprint": (
            LEGACY_PROCESSING_POLICY_FINGERPRINT
        ),
        "target_processing_policy_fingerprint": processing_policy_fingerprint(),
        "source_schema": "checkpoint_schema_4",
        "source_checkpoint_schema_version": CHECKPOINT_SCHEMA_VERSION,
        "binance_interim_1h_schema_id": BINANCE_INTERIM_1H_SCHEMA_ID,
    }
    if set(migration) != {*expected, "source_generation_id"}:
        raise IntegrityError(
            "Checkpoint-Policy-Migration besitzt unerwartete Felder."
        )
    for field, value in expected.items():
        if migration.get(field) != value:
            raise IntegrityError(
                f"Checkpoint-Policy-Migration widerspricht Feld {field}."
            )
    source_generation = migration.get("source_generation_id")
    if (
        not isinstance(source_generation, int)
        or isinstance(source_generation, bool)
        or source_generation != 2
        or source_generation >= checkpoint_generation
    ):
        raise IntegrityError(
            "Checkpoint-Policy-Migration besitzt keine gültige Quellgeneration."
        )


def _validate_legacy_checkpoint_for_migration(
    *,
    checkpoint: dict[str, Any],
    state: dict[str, Any],
    config: dict[str, Any],
    report_root: Path,
    project_root: Path,
) -> None:
    """Nur den exakt bekannten Schema-4-HARD_FAILURE read-only übernehmen."""

    if checkpoint.get("binance_interim_1h_schema_id") not in {None, ""}:
        raise IntegrityError(
            "Legacy-Checkpoint besitzt eine unerwartete Interim-Schema-ID."
        )
    if checkpoint.get("policy_migration") is not None:
        raise IntegrityError(
            "Legacy-Checkpoint enthält bereits eine Policy-Migration."
        )
    if (
        checkpoint.get("status") != "HARD_FAILURE"
        or checkpoint.get("execution_status") != "HARD_FAILURE"
    ):
        raise IntegrityError(
            "Legacy-Checkpoint ist nicht der erwartete HARD_FAILURE-Zustand."
        )
    if checkpoint.get("failed_task") != "binance BTCUSDT 2021-01":
        raise IntegrityError(
            "Legacy-Checkpoint besitzt nicht den erwarteten Fehlerauftrag."
        )
    if checkpoint.get("last_safe_completed_task") != "":
        raise IntegrityError(
            "Legacy-Checkpoint besitzt bereits einen sicheren Monatsauftrag."
        )
    if int(checkpoint.get("generation_id", 0)) != 2:
        raise IntegrityError(
            "Legacy-Checkpoint besitzt nicht die erwartete Quellgeneration."
        )
    run_id = checkpoint.get("run_id")
    if (
        not isinstance(run_id, str)
        or len(run_id) != 32
        or any(character not in "0123456789abcdef" for character in run_id)
    ):
        raise IntegrityError(
            "Legacy-Checkpoint besitzt keine gültige Run-Kennung."
        )
    last_error = checkpoint.get("last_error")
    if (
        not isinstance(last_error, dict)
        or set(last_error)
        != {"type", "message", "affected_task", "phase"}
        or last_error.get("type") != "IntegrityError"
        or last_error.get("affected_task") != "binance BTCUSDT 2021-01"
        or last_error.get("phase") != ""
        or checkpoint.get("error")
        != f"IntegrityError: {last_error.get('message', '')}"
    ):
        raise IntegrityError(
            "Legacy-Checkpoint besitzt nicht den erwarteten Fehlernachweis."
        )

    evidence = checkpoint["evidence"]
    expected_evidence_fields = {
        "raw_manifest",
        "binance_monthly_quality",
        "source_anomalies",
        "interim_output_status",
        "partial_interim_outputs",
        "coinmetrics_pages",
        "coinmetrics_quality",
        "source_anomaly_provenance",
    }
    if set(evidence) != expected_evidence_fields:
        raise IntegrityError(
            "Legacy-Checkpoint besitzt unerwartete Evidenzfelder."
        )
    empty_evidence = (
        "binance_monthly_quality",
        "interim_output_status",
        "partial_interim_outputs",
        "coinmetrics_pages",
    )
    if any(evidence.get(field) != [] for field in empty_evidence):
        raise IntegrityError(
            "Legacy-Checkpoint enthält unerwartete Verarbeitungsfortschritte."
        )
    if evidence.get("coinmetrics_quality") is not None:
        raise IntegrityError(
            "Legacy-Checkpoint enthält bereits Coin-Metrics-Qualität."
        )
    coinmetrics_progress = checkpoint.get("coinmetrics_progress", {})
    if (
        coinmetrics_progress.get("phase", "") != ""
        or int(coinmetrics_progress.get("pages_attempted", 0)) != 0
        or int(coinmetrics_progress.get("pages_completed", 0)) != 0
    ):
        raise IntegrityError(
            "Legacy-Checkpoint enthält unerwarteten Coin-Metrics-Fortschritt."
        )

    projection_hashes = checkpoint["report_generation"]["projection_hashes"]
    template = execution_checkpoint(
        config=config,
        state=state,
        status="HARD_FAILURE",
        generation_id=2,
        projection_hashes=projection_hashes,
        error=IntegrityError(last_error["message"]),
        affected_task="binance BTCUSDT 2021-01",
    )
    expected_checkpoint_fields = set(template) - {
        "binance_interim_1h_schema_id",
        "policy_migration",
    }
    if set(checkpoint) != expected_checkpoint_fields:
        raise IntegrityError(
            "Legacy-Checkpoint besitzt unerwartete Strukturfelder."
        )
    if set(checkpoint["report_generation"]) != {
        "generation_id",
        "projection_hashes",
    }:
        raise IntegrityError(
            "Legacy-Checkpoint besitzt unerwartete Berichtsmetadaten."
        )
    if set(checkpoint["coinmetrics_progress"]) != {
        "phase",
        "pages_attempted",
        "pages_completed",
    }:
        raise IntegrityError(
            "Legacy-Checkpoint besitzt unerwartete Coin-Metrics-Felder."
        )
    for name in EXECUTION_REPORT_FILES:
        path = report_root / name
        if not path.is_file() or sha256_file(path) != projection_hashes[name]:
            raise IntegrityError(
                f"Legacy-Projektion ist nicht bytegenau belegt: {name}"
            )

    tasks = build_binance_tasks(config, project_root)
    january_task = next(
        (
            task
            for task in tasks
            if task.symbol == "BTCUSDT" and task.month == "2021-01"
        ),
        None,
    )
    if january_task is None:
        raise IntegrityError(
            "Legacy-Migration findet den erwarteten Januar-Auftrag nicht."
        )
    february_task = next(
        (
            task
            for task in tasks
            if task.symbol == "BTCUSDT" and task.month == "2021-02"
        ),
        None,
    )
    if february_task is None:
        raise IntegrityError(
            "Legacy-Migration findet den erwarteten Februar-Auftrag nicht."
        )
    january_archive = safe_project_path(
        project_root,
        january_task.archive_path,
        required_prefix="data/raw",
    )
    january_checksum = safe_project_path(
        project_root,
        january_task.checksum_path,
        required_prefix="data/raw",
    )
    if not january_archive.is_file() or not january_checksum.is_file():
        raise IntegrityError(
            "Legacy-Migration benötigt das vollständige Januar-Rawpaar."
        )
    raw_root = safe_project_path(
        project_root,
        config["paths"]["raw_root"],
        required_prefix="data/raw",
    )
    expected_raw_files = {
        january_archive.resolve(),
        january_checksum.resolve(),
        safe_project_path(
            project_root,
            february_task.archive_path,
            required_prefix="data/raw",
        ).resolve(),
        safe_project_path(
            project_root,
            february_task.checksum_path,
            required_prefix="data/raw",
        ).resolve(),
    }
    actual_raw_files = {
        path.resolve() for path in raw_root.rglob("*") if path.is_file()
    }
    if actual_raw_files != expected_raw_files:
        raise IntegrityError(
            "Legacy-Migration findet nicht exakt die vier belegten Raw-Dateien."
        )
    provider_hash = parse_exact_provider_checksum(
        january_checksum.read_text(encoding="utf-8"),
        january_archive.name,
    )
    archive_hash = sha256_file(january_archive)
    if archive_hash != provider_hash:
        raise IntegrityError(
            "Legacy-Migration verwirft das ungültige Januar-Rawpaar."
        )

    manifest = evidence.get("raw_manifest")
    if not isinstance(manifest, list) or len(manifest) != 2:
        raise IntegrityError(
            "Legacy-Checkpoint besitzt nicht das erwartete Januar-Manifest."
        )
    manifest_by_type = {row.get("object_type"): row for row in manifest}
    if set(manifest_by_type) != {"archive", "checksum"}:
        raise IntegrityError(
            "Legacy-Checkpoint besitzt unerwartete Manifestobjekte."
        )
    if any(set(row) != set(MANIFEST_FIELDS) for row in manifest):
        raise IntegrityError(
            "Legacy-Checkpoint besitzt unerwartete Manifestfelder."
        )
    expected_manifest = {
        "archive": {
            "url": january_task.archive_url,
            "raw_file": january_task.archive_path,
            "bytes": january_archive.stat().st_size,
            "sha256": archive_hash,
            "row_count": "",
        },
        "checksum": {
            "url": january_task.checksum_url,
            "raw_file": january_task.checksum_path,
            "bytes": january_checksum.stat().st_size,
            "sha256": sha256_file(january_checksum),
            "row_count": "",
        },
    }
    retrieved_values: set[str] = set()
    for object_type, expected in expected_manifest.items():
        row = manifest_by_type[object_type]
        retrieved_at = row.get("retrieved_at_utc")
        try:
            parsed_retrieved_at = datetime.fromisoformat(str(retrieved_at))
        except ValueError as exc:
            raise IntegrityError(
                "Legacy-Checkpoint besitzt keine gültige Manifestzeit."
            ) from exc
        if (
            row.get("source") != "Binance Public Data"
            or row.get("symbol_or_asset") != "BTCUSDT"
            or row.get("period_or_page") != "2021-01"
            or row.get("provider_checksum") != provider_hash
            or row.get("provider_checksum_match") is not True
            or row.get("cache_status") != "cached_valid"
            or parsed_retrieved_at.utcoffset() != timedelta(0)
            or any(row.get(field) != value for field, value in expected.items())
        ):
            raise IntegrityError(
                "Legacy-Checkpoint besitzt widersprüchliche Januar-Manifestevidenz."
            )
        retrieved_values.add(str(retrieved_at))
    if len(retrieved_values) != 1:
        raise IntegrityError(
            "Legacy-Checkpoint besitzt widersprüchliche Manifestzeiten."
        )

    frame_1h = parse_binance_archive(january_archive, january_task)
    january_quality = validate_binance_month(frame_1h, january_task)
    if not january_quality["quality_pass"]:
        raise IntegrityError(
            "Legacy-Migration verwirft die Januar-Monatsqualität."
        )
    interim_root = safe_project_path(
        project_root,
        config["paths"]["interim_root"],
        required_prefix="data/interim",
    )
    month_root = interim_root / "binance" / "BTCUSDT"
    one_hour_path = month_root / "1h" / "BTCUSDT-1h-2021-01.csv"
    four_hour_path = month_root / "4h" / "BTCUSDT-4h-2021-01.csv"
    legacy_error_prefix = (
        "Vorhandene erzeugte Datei weicht vom deterministischen Inhalt ab "
        "und bleibt unveraendert: "
    )
    legacy_message = last_error["message"]
    legacy_path_text = (
        legacy_message.removeprefix(legacy_error_prefix)
        if legacy_message.startswith(legacy_error_prefix)
        else ""
    )
    normalized_legacy_path = legacy_path_text.replace("\\", "/")
    expected_relative_suffix = project_relative(
        one_hour_path,
        project_root,
    )
    if (
        not legacy_path_text
        or not Path(legacy_path_text).is_absolute()
        or not normalized_legacy_path.endswith(
            f"/{expected_relative_suffix}"
        )
        or any(character in legacy_path_text for character in "\r\n?#")
    ):
        raise IntegrityError(
            "Legacy-Checkpoint besitzt nicht den belegten "
            "Januar-Vertragsfehler."
        )
    expected_1h = dataframe_csv_bytes(project_binance_interim_1h(frame_1h))
    expected_4h_frame = aggregate_complete_1h_to_4h(frame_1h)
    expected_4h = dataframe_csv_bytes(expected_4h_frame)
    if (
        not one_hour_path.is_file()
        or one_hour_path.read_bytes() != expected_1h
    ):
        raise IntegrityError(
            "Legacy-Migration verwirft die vorhandene Januar-1h-Interimdatei."
        )
    if (
        len(expected_4h_frame) != january_task.expected_4h_rows
        or not four_hour_path.is_file()
        or four_hour_path.read_bytes() != expected_4h
    ):
        raise IntegrityError(
            "Legacy-Migration verwirft die vorhandene Januar-4h-Interimdatei."
        )
    actual_interim_files = {
        path.resolve() for path in interim_root.rglob("*") if path.is_file()
    }
    if actual_interim_files != {
        one_hour_path.resolve(),
        four_hour_path.resolve(),
    }:
        raise IntegrityError(
            "Legacy-Migration findet nicht exakt die zwei belegten "
            "Interimdateien."
        )

    canonical_anomalies, canonical_provenance = (
        _read_preexisting_source_anomalies(
            report_root,
            config=config,
            project_root=project_root,
        )
    )
    if (
        len(canonical_anomalies) != 4
        or any(
            set(row) != set(SOURCE_ANOMALY_FIELDS)
            for row in state["anomalies"]
        )
        or canonical_provenance.get("verified_cached_months") != 2
        or canonical_provenance.get("verified_asset_months") != 1
        or [_anomaly_key(row) for row in canonical_anomalies]
        != [_anomaly_key(row) for row in state["anomalies"]]
        or canonical_provenance != state["anomaly_provenance"]
    ):
        raise IntegrityError(
            "Legacy-Migration kann die Februar-Anomalieevidenz nicht bestätigen."
        )


def load_authoritative_checkpoint(
    *,
    config: dict[str, Any],
    report_root: Path,
    project_root: Path | None = None,
) -> tuple[dict[str, Any], dict[str, Any]] | None:
    """Checkpoint vor jeder Mutation vollständig und fail-closed validieren."""

    path = report_root / "execution_checkpoint.json"
    if not path.exists():
        return None
    try:
        checkpoint = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise IntegrityError(
            "Autoritativer Ausführungscheckpoint ist nicht lesbar."
        ) from exc
    if checkpoint.get("checkpoint_schema_version") != CHECKPOINT_SCHEMA_VERSION:
        raise IntegrityError("Unvereinbare Checkpoint-Schemaversion.")
    if checkpoint.get("schema_version") != CHECKPOINT_SCHEMA_VERSION:
        raise IntegrityError("Unvereinbare redundante Checkpoint-Schemaversion.")
    if checkpoint.get("timestamp_policy_id") != TIMESTAMP_POLICY_ID:
        raise IntegrityError("Checkpoint und Zeitstempelrichtlinie widersprechen sich.")
    if (
        checkpoint.get("anomaly_evidence_policy_id")
        != ANOMALY_EVIDENCE_POLICY_ID
    ):
        raise IntegrityError(
            "Checkpoint und Anomalieevidenzrichtlinie widersprechen sich."
        )
    if checkpoint.get("scope_id") != config["scope_id"]:
        raise IntegrityError("Checkpoint und Scope stimmen nicht überein.")
    if checkpoint.get("config_fingerprint") != config_fingerprint(config):
        raise IntegrityError(
            "Checkpoint und aktuelle Konfiguration stimmen nicht überein."
        )
    generation = checkpoint.get("generation_id")
    report_generation = checkpoint.get("report_generation", {})
    if report_generation.get("generation_id") != generation:
        raise IntegrityError(
            "Checkpoint und Berichtsgeneration stimmen nicht überein."
        )
    required_evidence = {
        "raw_manifest",
        "binance_monthly_quality",
        "source_anomalies",
        "interim_output_status",
        "partial_interim_outputs",
        "coinmetrics_pages",
        "coinmetrics_quality",
        "source_anomaly_provenance",
    }
    evidence = checkpoint.get("evidence")
    if not isinstance(evidence, dict) or not required_evidence.issubset(evidence):
        raise IntegrityError("Checkpoint-Evidenz ist unvollständig.")
    hashes = report_generation.get("projection_hashes")
    if not isinstance(hashes, dict) or set(hashes) != set(
        EXECUTION_REPORT_FILES
    ):
        raise IntegrityError("Checkpoint-Projektionshashes sind unvollständig.")
    state = _state_from_checkpoint(checkpoint)
    projections = _report_projection_bytes(
        state, generation_id=int(generation)
    )
    computed_hashes = {
        name: sha256_bytes(content) for name, content in projections.items()
    }
    if computed_hashes != hashes:
        raise IntegrityError(
            "Checkpoint-Inhalt und gespeicherte Projektionshashes "
            "widersprechen sich."
        )
    computed_aggregates = aggregate_execution_counts(config, state)
    if checkpoint.get("aggregate_counts") != computed_aggregates:
        raise IntegrityError(
            "Checkpoint-Evidenz und aggregierte Zählungen widersprechen sich."
        )
    for field, value in computed_aggregates.items():
        if field != "per_asset" and checkpoint.get(field) != value:
            raise IntegrityError(
                f"Checkpoint besitzt eine widersprüchliche Zählung: {field}."
            )
    if (
        checkpoint.get("checked_raw_objects") != len(state["manifest"])
        or checkpoint.get("checked_months") != len(state["quality"])
        or checkpoint.get("provider_checksums_passed")
        != sum(
            row["object_type"] == "archive"
            and row["provider_checksum_match"] is True
            for row in state["manifest"]
        )
    ):
        raise IntegrityError(
            "Checkpoint besitzt widersprüchliche Verarbeitungszähler."
        )
    if (
        checkpoint.get("status") != checkpoint.get("execution_status")
        or checkpoint.get("gate_1") != GATE_1_STATUS
    ):
        raise IntegrityError(
            "Checkpoint besitzt einen widersprüchlichen Status."
        )
    checkpoint_policy = checkpoint.get("processing_policy_fingerprint")
    if checkpoint_policy == LEGACY_PROCESSING_POLICY_FINGERPRINT:
        if project_root is None:
            raise IntegrityError(
                "Legacy-Checkpoint benötigt einen sicher bestimmten Projektroot."
            )
        _validate_legacy_checkpoint_for_migration(
            checkpoint=checkpoint,
            state=state,
            config=config,
            report_root=report_root,
            project_root=project_root,
        )
        state["_policy_migration"] = {
            "migration_id": (
                "legacy_schema4_hard_failure_to_binance_1h_market_v1"
            ),
            "source_processing_policy_fingerprint": (
                LEGACY_PROCESSING_POLICY_FINGERPRINT
            ),
            "target_processing_policy_fingerprint": (
                processing_policy_fingerprint()
            ),
            "source_schema": "checkpoint_schema_4",
            "source_checkpoint_schema_version": CHECKPOINT_SCHEMA_VERSION,
            "source_generation_id": int(checkpoint["generation_id"]),
            "binance_interim_1h_schema_id": BINANCE_INTERIM_1H_SCHEMA_ID,
        }
        state["_legacy_policy_migration_pending"] = True
    else:
        if checkpoint_policy != processing_policy_fingerprint():
            raise IntegrityError(
                "Checkpoint und Verarbeitungsrichtlinien widersprechen sich."
            )
        if (
            checkpoint.get("binance_interim_1h_schema_id")
            != BINANCE_INTERIM_1H_SCHEMA_ID
        ):
            raise IntegrityError(
                "Checkpoint und Binance-1h-Interimschema widersprechen sich."
            )
        _validate_policy_migration_provenance(
            checkpoint.get("policy_migration"),
            checkpoint_generation=int(checkpoint["generation_id"]),
        )
    return state, checkpoint


def recover_report_projections(
    *,
    state: dict[str, Any],
    checkpoint: dict[str, Any],
    report_root: Path,
) -> list[str]:
    """Fehlende oder fremde Projektionen aus dem Checkpoint wiederherstellen."""

    projections = _report_projection_bytes(
        state, generation_id=int(checkpoint["generation_id"])
    )
    repaired: list[str] = []
    for name in EXECUTION_REPORT_FILES:
        path = report_root / name
        expected_hash = checkpoint["report_generation"][
            "projection_hashes"
        ][name]
        actual_hash = sha256_file(path) if path.is_file() else ""
        if actual_hash != expected_hash:
            write_report_atomic(path, projections[name])
            repaired.append(name)
    return repaired


def load_or_initialize_authoritative_state(
    *,
    config: dict[str, Any],
    report_root: Path,
    project_root: Path | None = None,
) -> dict[str, Any]:
    configured_report = Path(config["paths"]["report_root"])
    actual_project_root = (
        project_root.resolve() if project_root is not None else report_root.resolve()
    )
    if project_root is None:
        for _ in configured_report.parts:
            actual_project_root = actual_project_root.parent
    expected_report_root = safe_project_path(
        actual_project_root,
        config["paths"]["report_root"],
        required_prefix="reports",
    )
    if expected_report_root != report_root.resolve():
        raise SafetyError("Berichtsroot stimmt nicht mit der Konfiguration überein.")
    loaded = load_authoritative_checkpoint(
        config=config,
        report_root=report_root,
        project_root=actual_project_root,
    )
    if loaded is None:
        state = _new_authoritative_state(
            report_root,
            config=config,
            project_root=actual_project_root,
        )
        state["_scope_id"] = config["scope_id"]
        return state
    state, checkpoint = loaded
    if not state.get("_legacy_policy_migration_pending", False):
        recover_report_projections(
            state=state, checkpoint=checkpoint, report_root=report_root
        )
    return state


def run_binance_stage(
    *,
    tasks: Sequence[BinanceTask],
    config: dict[str, Any],
    project_root: Path,
    session: requests.Session,
    report_root: Path,
    timeout_seconds: int,
) -> dict[str, Any]:
    """Monate idempotent prüfen und jeden sicheren Zustand autoritativ sichern."""

    state = load_or_initialize_authoritative_state(
        config=config,
        report_root=report_root,
        project_root=project_root,
    )
    persist_authoritative_state(
        config=config,
        state=state,
        report_root=report_root,
        status="IN_PROGRESS",
    )
    for task in tasks:
        task_label = f"binance {task.symbol} {task.month}"
        try:
            manifest_rows = ensure_binance_task(
                task,
                project_root,
                session,
                timeout_seconds=timeout_seconds,
            )
            for row in manifest_rows:
                _upsert_manifest(state["manifest"], row)
            quality = process_binance_task(task, config, project_root)
            quality["provider_checksum_match"] = True
        except PartialInterimError as exc:
            _upsert_by_key(
                state["partial_interim"], exc.evidence, _quality_key
            )
            persist_authoritative_state(
                config=config,
                state=state,
                report_root=report_root,
                status="HARD_FAILURE",
                error=exc,
                affected_task=task_label,
            )
            raise
        except Exception as exc:
            persist_authoritative_state(
                config=config,
                state=state,
                report_root=report_root,
                status="HARD_FAILURE",
                error=exc,
                affected_task=task_label,
            )
            raise
        _upsert_by_key(state["quality"], quality, _quality_key)
        state["anomalies"] = _merge_anomalies(
            state["anomalies"], build_source_anomaly_rows(quality)
        )
        state["partial_interim"] = [
            row
            for row in state["partial_interim"]
            if _quality_key(row) != _quality_key(quality)
        ]
        if quality["processing_status"] in {
            "quality_quarantine",
            "source_integrity_failure",
        }:
            exc = IntegrityError(
                "Binance-Monat erfordert einen harten Stopp: "
                f"{task.symbol} {task.month} "
                f"({quality['processing_status']})"
            )
            persist_authoritative_state(
                config=config,
                state=state,
                report_root=report_root,
                status="HARD_FAILURE",
                error=exc,
                affected_task=task_label,
            )
            raise exc
        state["last_safe_completed_task"] = f"{task.symbol} {task.month}"
        persist_authoritative_state(
            config=config,
            state=state,
            report_root=report_root,
            status="IN_PROGRESS",
        )
    return state


def execute_full_import(
    config: dict[str, Any],
    project_root: Path,
    confirmation: str = "",
) -> dict[str, Any]:
    """Separat freizugebenden Vollimport ausführen; nie im Dry-Run aufrufen."""

    if confirmation != EXECUTE_CONFIRMATION:
        raise SafetyError(
            "Direkte Vollimport-Ausfuehrung ist nur mit dem exakten "
            f"Bestaetigungstext {EXECUTE_CONFIRMATION} erlaubt."
        )
    tasks = build_binance_tasks(config, project_root)
    session = build_session(config)
    timeout = int(config["network"]["timeout_seconds"])
    report_root = safe_project_path(
        project_root,
        config["paths"]["report_root"],
        required_prefix="reports",
    )
    report_root.mkdir(parents=True, exist_ok=True)
    state = run_binance_stage(
        tasks=tasks,
        config=config,
        project_root=project_root,
        session=session,
        report_root=report_root,
        timeout_seconds=timeout,
    )
    state.setdefault("partial_interim", [])
    state.setdefault("coinmetrics_pages", [])
    state.setdefault("coinmetrics_quality", None)
    state.setdefault(
        "anomaly_provenance",
        {
            "mode": "none",
            "source_file": "source_anomalies.csv",
            "sha256": "",
            "rows": 0,
        },
    )
    state.setdefault("_run_id", uuid.uuid4().hex)
    state.setdefault("_generation_id", 0)
    state.setdefault("_execution_status", "IN_PROGRESS")
    state.setdefault("_last_error", None)
    state.setdefault("_scope_id", config["scope_id"])
    state.setdefault("_coinmetrics_phase", "")
    state.setdefault("_coinmetrics_pages_attempted", 0)

    def checkpoint_coinmetrics_page(
        manifest_row: dict[str, Any],
        page_records: list[dict[str, Any]],
    ) -> None:
        _upsert_manifest(state["manifest"], manifest_row)
        page_number = int(manifest_row["period_or_page"])
        page_evidence = {
            "page_key": f"coinmetrics:{page_number:05d}",
            "page_number": page_number,
            "raw_file": manifest_row["raw_file"],
            "sha256": manifest_row["sha256"],
            "row_count": len(page_records),
            "cache_status": manifest_row["cache_status"],
        }
        _upsert_by_key(
            state["coinmetrics_pages"],
            page_evidence,
            lambda row: (row["page_key"],),
        )
        state["last_safe_completed_task"] = (
            f"coinmetrics page {page_number:05d}"
        )
        persist_authoritative_state(
            config=config,
            state=state,
            report_root=report_root,
            status="IN_PROGRESS",
        )

    def mark_coinmetrics_phase(phase: str, page_number: int) -> None:
        state["_coinmetrics_phase"] = phase
        if phase == "coinmetrics_page_fetch":
            state["_coinmetrics_pages_attempted"] = max(
                int(state["_coinmetrics_pages_attempted"]), page_number
            )

    try:
        coinmetrics_records, _ = download_coinmetrics_pages(
            config,
            project_root,
            session,
            on_page_completed=checkpoint_coinmetrics_page,
            on_phase=mark_coinmetrics_phase,
        )
        state["_coinmetrics_phase"] = "coinmetrics_aggregate_quality"
        context, context_quality = normalize_coinmetrics_records(
            coinmetrics_records, config
        )
        if not context_quality["quality_pass"]:
            raise IntegrityError("Coin-Metrics-Qualität fehlgeschlagen.")
        state["_coinmetrics_phase"] = "coinmetrics_interim_write"
        context_interim = write_coinmetrics_interim_context(
            context, config, project_root
        )
        state["coinmetrics_quality"] = {
            **context_quality,
            "interim_file": context_interim.get("path", ""),
            "interim_status": context_interim["status"],
        }
        state["last_safe_completed_task"] = "coinmetrics object btc"
        state["_coinmetrics_phase"] = "coinmetrics_completed"
        final_status = (
            "COMPLETED_WITH_SOURCE_ANOMALIES"
            if state["anomalies"]
            else "COMPLETED"
        )
        persist_authoritative_state(
            config=config,
            state=state,
            report_root=report_root,
            status=final_status,
        )
    except Exception as exc:
        phase = state.get("_coinmetrics_phase", "")
        if phase == "coinmetrics_aggregate_quality":
            affected_task = "coinmetrics aggregate_quality"
        elif phase == "coinmetrics_interim_write":
            affected_task = "coinmetrics interim_write"
        else:
            affected_page = max(
                1, int(state.get("_coinmetrics_pages_attempted", 0))
            )
            affected_task = f"coinmetrics page {affected_page:05d}"
        persist_authoritative_state(
            config=config,
            state=state,
            report_root=report_root,
            status="HARD_FAILURE",
            error=exc,
            affected_task=affected_task,
        )
        raise
    aggregates = aggregate_execution_counts(config, state)
    return {
        "mode": "execute",
        "scope_id": config["scope_id"],
        "binance_tasks_processed": len(tasks),
        "coinmetrics_rows_processed": len(context),
        "coinmetrics_interim_status": context_interim["status"],
        "source_continuity_anomaly_months": aggregates[
            "continuity_anomaly_months"
        ],
        "execution_status": final_status,
        "gate_1": GATE_1_STATUS,
    }


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """CLI mit Dry-Run-Standard und expliziter Doppelsperre lesen."""

    parser = argparse.ArgumentParser(
        description="Historischen Vollimport offline planen oder separat ausfuehren."
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("config/full_import.json"),
        help="Projekt-relativer JSON-Konfigurationspfad.",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--dry-run",
        action="store_true",
        help="Nur deterministische Planberichte schreiben; kein Netzwerk.",
    )
    mode.add_argument(
        "--execute",
        action="store_true",
        help="Vollimport ausfuehren; benoetigt zusaetzliche Bestaetigung.",
    )
    parser.add_argument(
        "--confirm-scope",
        default="",
        help=f"Bei --execute exakt {EXECUTE_CONFIRMATION} angeben.",
    )
    return parser.parse_args(argv)


def validate_execution_request(args: argparse.Namespace) -> str:
    """Falsche oder fehlende Ausfuehrungsbestaetigung vor Netzwerk abbrechen."""

    if args.execute:
        if args.confirm_scope != EXECUTE_CONFIRMATION:
            raise SafetyError(
                "--execute ist nur mit --confirm-scope "
                f"{EXECUTE_CONFIRMATION} erlaubt."
            )
        return "execute"
    if args.confirm_scope:
        raise SafetyError("--confirm-scope ist ohne --execute unzulaessig.")
    return "dry-run"


def main(
    argv: Sequence[str] | None = None,
    *,
    project_root: Path | None = None,
) -> int:
    """CLI-Einstieg; Dry-Run erzeugt garantiert keine Netzwerksitzung."""

    args = parse_args(argv)
    root = (
        project_root.resolve()
        if project_root is not None
        else Path(__file__).resolve().parents[1]
    )
    try:
        mode = validate_execution_request(args)
        config = load_config(args.config, root)
        if mode == "execute":
            result = execute_full_import(
                config, root, confirmation=args.confirm_scope
            )
            print(canonical_json(result), end="")
            return 0
        summary = write_dry_run_artifacts(config, root)
        print(
            f"DRY-RUN OK: {summary['counts']['binance_archive_tasks']} "
            "Binance-Monatsauftraege, kein Netzwerk."
        )
        print("Gate 1: NOT_EVALUATED")
        return 0
    except (
        FullImportError,
        OSError,
        ValueError,
        requests.RequestException,
    ) as exc:
        print(f"VOLLIMPORT FEHLGESCHLAGEN: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
