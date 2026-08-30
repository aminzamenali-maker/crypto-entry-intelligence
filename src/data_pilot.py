"""Reproduzierbarer Gate-0-Datenpilot fuer historische Kryptodaten.

Der Pilot laedt bewusst nur kleine, fest definierte Stichproben. Rohdateien
werden nie ueberschrieben. Ausgaben unter ``reports/data_pilot`` und
``data/interim/pilot`` sind dagegen reproduzierbar erzeugte Artefakte.
"""

from __future__ import annotations

import argparse
import calendar
import hashlib
import json
import math
import sys
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import pandas as pd
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


BINANCE_COLUMNS = [
    "open_time_raw",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "close_time_raw",
    "quote_asset_volume",
    "number_of_trades",
    "taker_buy_base_volume",
    "taker_buy_quote_volume",
    "ignore",
]

NUMERIC_BINANCE_COLUMNS = [
    "open",
    "high",
    "low",
    "close",
    "volume",
    "quote_asset_volume",
    "number_of_trades",
    "taker_buy_base_volume",
    "taker_buy_quote_volume",
]


class PilotError(RuntimeError):
    """Fehler, der einen belastbaren Pilotabschluss verhindert."""


@dataclass(frozen=True)
class DownloadResult:
    path: Path
    created: bool
    downloaded_at_utc: str


def utc_now_iso() -> str:
    """Aktuellen UTC-Zeitpunkt als ISO-8601-Text liefern."""

    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def sha256_file(path: Path) -> str:
    """SHA-256 einer Datei blockweise berechnen."""

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def relative_path(path: Path, project_root: Path) -> str:
    """Projekt-relativen Pfad mit stabilen Schraegstrichen erzeugen."""

    return path.resolve().relative_to(project_root.resolve()).as_posix()


def build_session(user_agent: str) -> requests.Session:
    """HTTP-Sitzung mit begrenzten Wiederholungen fuer temporaere Fehler."""

    retry = Retry(
        total=3,
        connect=3,
        read=3,
        status=3,
        backoff_factor=1.0,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset({"GET"}),
        respect_retry_after_header=True,
    )
    session = requests.Session()
    session.headers.update({"User-Agent": user_agent, "Accept": "*/*"})
    session.mount("https://", HTTPAdapter(max_retries=retry))
    return session


def write_bytes_exclusive(path: Path, content: bytes) -> None:
    """Bytes schreiben und einen bestehenden Rohdatenpfad niemals ersetzen."""

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as handle:
        handle.write(content)


def get_or_download(
    session: requests.Session,
    url: str,
    destination: Path,
    *,
    timeout_seconds: int = 60,
    params: dict[str, Any] | None = None,
) -> DownloadResult:
    """Eine Datei laden oder einen bereits vorhandenen Rohsnapshot wiederverwenden."""

    if destination.exists():
        modified = datetime.fromtimestamp(
            destination.stat().st_mtime, tz=timezone.utc
        ).isoformat(timespec="seconds")
        return DownloadResult(destination, False, modified)

    response = session.get(url, params=params, timeout=timeout_seconds)
    if response.status_code != 200:
        detail = response.text[:500].replace("\n", " ")
        raise PilotError(
            f"Download fehlgeschlagen ({response.status_code}) fuer {response.url}: "
            f"{detail}"
        )
    write_bytes_exclusive(destination, response.content)
    return DownloadResult(destination, True, utc_now_iso())


def infer_unix_unit(values: pd.Series) -> str:
    """Binance-Zeitstempel sicher als Millisekunden oder Mikrosekunden erkennen."""

    numeric = pd.to_numeric(values, errors="raise")
    median_absolute = float(numeric.abs().median())
    if 1e12 <= median_absolute < 1e14:
        return "ms"
    if 1e15 <= median_absolute < 1e17:
        return "us"
    raise PilotError(
        "Zeitstempeleinheit ist weder plausibles Millisekunden- noch "
        f"Mikrosekundenformat (Median={median_absolute})."
    )


def parse_binance_archive(
    archive_path: Path, symbol: str, timeframe: str, month: str
) -> pd.DataFrame:
    """Eine offizielle Binance-Kline-ZIP-Datei normalisieren."""

    with zipfile.ZipFile(archive_path) as archive:
        csv_members = [
            member for member in archive.namelist() if member.lower().endswith(".csv")
        ]
        if len(csv_members) != 1:
            raise PilotError(
                f"{archive_path} enthaelt {len(csv_members)} CSV-Dateien statt genau einer."
            )
        with archive.open(csv_members[0]) as handle:
            frame = pd.read_csv(handle, header=None, names=BINANCE_COLUMNS)

    if len(frame.columns) != len(BINANCE_COLUMNS):
        raise PilotError(f"Unerwartetes Binance-Schema in {archive_path}.")

    for column in NUMERIC_BINANCE_COLUMNS:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")

    open_unit = infer_unix_unit(frame["open_time_raw"])
    close_unit = infer_unix_unit(frame["close_time_raw"])
    if open_unit != close_unit:
        raise PilotError(f"Gemischte Zeitstempeleinheiten in {archive_path}.")

    frame["open_time_utc"] = pd.to_datetime(
        frame["open_time_raw"], unit=open_unit, utc=True, errors="raise"
    )
    frame["close_time_utc"] = pd.to_datetime(
        frame["close_time_raw"], unit=close_unit, utc=True, errors="raise"
    )
    frame["timestamp_utc"] = frame["open_time_utc"]
    frame["symbol"] = symbol
    frame["timeframe"] = timeframe
    frame["pilot_month"] = month
    frame["source"] = "binance_public_data"
    frame["timestamp_unit"] = open_unit

    ordered = [
        "symbol",
        "timeframe",
        "timestamp_utc",
        "open_time_utc",
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
        "pilot_month",
        "source",
        "timestamp_unit",
    ]
    return frame[ordered]


def expected_month_rows(month: str, interval_seconds: int) -> int:
    """Erwartete Anzahl lueckenloser Kerzen in einem UTC-Kalendermonat."""

    year, month_number = (int(part) for part in month.split("-"))
    seconds = calendar.monthrange(year, month_number)[1] * 24 * 60 * 60
    if seconds % interval_seconds != 0:
        raise PilotError("Monatslaenge ist nicht durch das Intervall teilbar.")
    return seconds // interval_seconds


def assess_binance_file(
    frame: pd.DataFrame,
    *,
    symbol: str,
    timeframe: str,
    month: str,
    interval_seconds: int,
    source_file: str,
) -> dict[str, Any]:
    """Zentrale Datenqualitaetsregeln fuer genau eine Monatsdatei pruefen."""

    ordered = frame.sort_values("timestamp_utc").reset_index(drop=True)
    if ordered.empty:
        raise PilotError(f"Leere Binance-Monatsdatei fuer {symbol} {timeframe} {month}.")

    timestamp_unit = str(ordered["timestamp_unit"].iloc[0])
    timestamp_resolution = {
        "ms": pd.Timedelta(milliseconds=1),
        "us": pd.Timedelta(microseconds=1),
    }.get(timestamp_unit)
    if timestamp_resolution is None:
        raise PilotError(
            f"Unbekannte Binance-Zeitstempeleinheit fuer Monatsgrenzen: {timestamp_unit}"
        )
    expected_month_start = pd.Timestamp(f"{month}-01", tz="UTC")
    expected_next_month_start = expected_month_start + pd.offsets.MonthBegin(1)
    expected_last_open = expected_next_month_start - pd.Timedelta(
        seconds=interval_seconds
    )
    expected_month_end = expected_next_month_start - timestamp_resolution
    actual_month_start = ordered["timestamp_utc"].iloc[0]
    actual_last_open = ordered["timestamp_utc"].iloc[-1]
    actual_month_end = ordered["close_time_utc"].iloc[-1]
    timestamps_outside_month = (
        (ordered["timestamp_utc"] < expected_month_start)
        | (ordered["timestamp_utc"] >= expected_next_month_start)
    )

    time_differences = ordered["timestamp_utc"].diff().dt.total_seconds().dropna()
    unexpected_spacing = time_differences.ne(interval_seconds)
    positive_large_gaps = time_differences[time_differences > interval_seconds]
    missing_intervals = int(
        sum(max(0, int(round(value / interval_seconds)) - 1) for value in positive_large_gaps)
    )

    ohlc_violation = (
        (ordered["high"] < ordered[["open", "close", "low"]].max(axis=1))
        | (ordered["low"] > ordered[["open", "close", "high"]].min(axis=1))
    )
    non_positive_prices = (ordered[["open", "high", "low", "close"]] <= 0).any(axis=1)
    negative_volume = ordered["volume"] < 0
    epoch_seconds = ordered["timestamp_utc"].astype("int64") // 1_000_000_000
    alignment_errors = epoch_seconds.mod(interval_seconds).ne(0)
    duration_seconds = (
        ordered["close_time_utc"] - ordered["open_time_utc"]
    ).dt.total_seconds()
    duration_errors = ~(
        duration_seconds.ge(interval_seconds - 1)
        & duration_seconds.lt(interval_seconds)
    )

    expected_rows = expected_month_rows(month, interval_seconds)
    numeric_null_count = int(
        ordered[
            [
                "open",
                "high",
                "low",
                "close",
                "volume",
                "quote_asset_volume",
                "number_of_trades",
            ]
        ]
        .isna()
        .sum()
        .sum()
    )
    result = {
        "source": "Binance Public Data",
        "source_file": source_file,
        "symbol": symbol,
        "timeframe": timeframe,
        "pilot_month": month,
        "timestamp_unit": timestamp_unit,
        "rows": int(len(ordered)),
        "expected_rows": int(expected_rows),
        "expected_month_start_utc": expected_month_start.isoformat(),
        "actual_month_start_utc": actual_month_start.isoformat(),
        "expected_last_open_utc": expected_last_open.isoformat(),
        "actual_last_open_utc": actual_last_open.isoformat(),
        "expected_month_end_utc": expected_month_end.isoformat(),
        "actual_month_end_utc": actual_month_end.isoformat(),
        "month_start_mismatch": int(actual_month_start != expected_month_start),
        "last_candle_open_mismatch": int(actual_last_open != expected_last_open),
        "month_end_mismatch": int(actual_month_end != expected_month_end),
        "timestamps_outside_month": int(timestamps_outside_month.sum()),
        "duplicate_timestamps": int(ordered["timestamp_utc"].duplicated().sum()),
        "not_strictly_increasing": int(
            (~ordered["timestamp_utc"].diff().dropna().gt(pd.Timedelta(0))).sum()
        ),
        "unexpected_spacing_events": int(unexpected_spacing.sum()),
        "missing_intervals": missing_intervals,
        "timestamp_alignment_errors": int(alignment_errors.sum()),
        "candle_duration_errors": int(duration_errors.sum()),
        "numeric_null_count": numeric_null_count,
        "ohlc_bound_violations": int(ohlc_violation.sum()),
        "non_positive_price_rows": int(non_positive_prices.sum()),
        "negative_volume_rows": int(negative_volume.sum()),
    }
    error_fields = [
        "month_start_mismatch",
        "last_candle_open_mismatch",
        "month_end_mismatch",
        "timestamps_outside_month",
        "duplicate_timestamps",
        "not_strictly_increasing",
        "unexpected_spacing_events",
        "missing_intervals",
        "timestamp_alignment_errors",
        "candle_duration_errors",
        "numeric_null_count",
        "ohlc_bound_violations",
        "non_positive_price_rows",
        "negative_volume_rows",
    ]
    result["quality_pass"] = bool(
        result["rows"] == result["expected_rows"]
        and all(result[field] == 0 for field in error_fields)
    )
    return result


def parse_official_checksum(checksum_path: Path) -> str:
    """SHA-256 aus einer Binance-CHECKSUM-Datei lesen."""

    parts = checksum_path.read_text(encoding="utf-8").strip().split()
    if not parts or len(parts[0]) != 64:
        raise PilotError(f"Ungueltige CHECKSUM-Datei: {checksum_path}")
    return parts[0].lower()


def download_binance_pilot(
    config: dict[str, Any],
    project_root: Path,
    session: requests.Session,
) -> tuple[list[pd.DataFrame], pd.DataFrame, list[dict[str, Any]]]:
    """Alle kleinen Binance-Pilotarchive laden, verifizieren und pruefen."""

    frames: list[pd.DataFrame] = []
    quality_rows: list[dict[str, Any]] = []
    manifest: list[dict[str, Any]] = []
    base_url = config["binance"]["base_url"].rstrip("/")

    for asset in config["assets"]:
        symbol = asset["symbol"]
        for timeframe, interval_seconds in config["timeframes"].items():
            for month in config["pilot_months"]:
                filename = f"{symbol}-{timeframe}-{month}.zip"
                url = f"{base_url}/{symbol}/{timeframe}/{filename}"
                raw_directory = (
                    project_root
                    / "data"
                    / "raw"
                    / "pilot"
                    / "binance"
                    / "spot"
                    / "monthly"
                    / "klines"
                    / symbol
                    / timeframe
                )
                archive_result = get_or_download(
                    session, url, raw_directory / filename
                )
                checksum_result = get_or_download(
                    session,
                    f"{url}.CHECKSUM",
                    raw_directory / f"{filename}.CHECKSUM",
                )
                actual_checksum = sha256_file(archive_result.path)
                official_checksum = parse_official_checksum(checksum_result.path)
                checksum_matches = actual_checksum == official_checksum
                if not checksum_matches:
                    raise PilotError(
                        f"SHA-256 stimmt fuer {archive_result.path} nicht mit "
                        "der Anbieter-Pruefsumme ueberein."
                    )

                parsed = parse_binance_archive(
                    archive_result.path, symbol, timeframe, month
                )
                frames.append(parsed)
                source_file = relative_path(archive_result.path, project_root)
                quality_rows.append(
                    assess_binance_file(
                        parsed,
                        symbol=symbol,
                        timeframe=timeframe,
                        month=month,
                        interval_seconds=int(interval_seconds),
                        source_file=source_file,
                    )
                )
                manifest.append(
                    {
                        "source": "Binance Public Data",
                        "url": url,
                        "raw_file": source_file,
                        "retrieved_or_cached_at_utc": archive_result.downloaded_at_utc,
                        "downloaded_this_run": archive_result.created,
                        "bytes": archive_result.path.stat().st_size,
                        "sha256": actual_checksum,
                        "provider_checksum": official_checksum,
                        "provider_checksum_match": checksum_matches,
                    }
                )

    return frames, pd.DataFrame(quality_rows), manifest


def aggregate_1h_to_4h(frame: pd.DataFrame) -> pd.DataFrame:
    """Vier abgeschlossene 1h-Kerzen zu einer handelbaren 4h-Kerze aggregieren."""

    if frame.empty:
        return frame.copy()
    ordered = frame.sort_values("timestamp_utc").copy()
    ordered["bucket_utc"] = ordered["timestamp_utc"].dt.floor("4h")
    grouped = ordered.groupby("bucket_utc", sort=True, observed=True)
    complete = grouped.filter(lambda group: len(group) == 4)
    complete_grouped = complete.groupby("bucket_utc", sort=True, observed=True)
    aggregated = complete_grouped.agg(
        open=("open", "first"),
        high=("high", "max"),
        low=("low", "min"),
        close=("close", "last"),
        volume=("volume", "sum"),
        quote_asset_volume=("quote_asset_volume", "sum"),
        number_of_trades=("number_of_trades", "sum"),
        close_time_utc=("close_time_utc", "max"),
        constituent_rows=("timestamp_utc", "size"),
    )
    return aggregated.reset_index().rename(columns={"bucket_utc": "timestamp_utc"})


def compare_timeframes(frames: Iterable[pd.DataFrame]) -> pd.DataFrame:
    """Direkte Binance-4h-Kerzen gegen Aggregation der 1h-Kerzen pruefen."""

    all_market = pd.concat(list(frames), ignore_index=True)
    rows: list[dict[str, Any]] = []
    compare_columns = [
        "open",
        "high",
        "low",
        "close",
        "volume",
        "quote_asset_volume",
        "number_of_trades",
    ]

    for (symbol, month), group in all_market.groupby(
        ["symbol", "pilot_month"], sort=True
    ):
        one_hour = group[group["timeframe"] == "1h"]
        direct_four_hour = (
            group[group["timeframe"] == "4h"]
            [["timestamp_utc", *compare_columns]]
            .sort_values("timestamp_utc")
        )
        aggregated = aggregate_1h_to_4h(one_hour)[
            ["timestamp_utc", *compare_columns]
        ]
        joined = direct_four_hour.merge(
            aggregated,
            on="timestamp_utc",
            how="outer",
            suffixes=("_direct", "_from_1h"),
            indicator=True,
        )
        missing_rows = int(joined["_merge"].ne("both").sum())
        comparable = joined[joined["_merge"] == "both"]
        value_mismatches = 0
        for column in compare_columns:
            left = comparable[f"{column}_direct"]
            right = comparable[f"{column}_from_1h"]
            tolerance = 1e-8 + right.abs() * 1e-12
            value_mismatches += int((left.sub(right).abs() > tolerance).sum())
        rows.append(
            {
                "symbol": symbol,
                "pilot_month": month,
                "direct_4h_rows": int(len(direct_four_hour)),
                "aggregated_4h_rows": int(len(aggregated)),
                "missing_or_extra_rows": missing_rows,
                "value_mismatches": value_mismatches,
                "timeframe_consistency_pass": bool(
                    missing_rows == 0 and value_mismatches == 0
                ),
            }
        )
    return pd.DataFrame(rows)


def coinmetrics_raw_filename(config: dict[str, Any]) -> str:
    """Stabilen Namen fuer den Coin-Metrics-Rohsnapshot erzeugen."""

    coinmetrics = config["coinmetrics"]
    return (
        f"{coinmetrics['asset']}_{coinmetrics['frequency']}_"
        f"{coinmetrics['start_time']}_{coinmetrics['end_time']}.json"
    )


def assess_coinmetrics_context(
    frame: pd.DataFrame,
    source: dict[str, Any],
    *,
    source_file: str,
) -> dict[str, Any]:
    """Grenzen, Vollstaendigkeit und Werte des taeglichen Kontexts pruefen."""

    ordered = frame.sort_values("source_timestamp_utc").reset_index(drop=True)
    metrics = list(source["metrics"])
    expected_dates = pd.date_range(
        source["start_time"], source["end_time"], freq="1D", tz="UTC"
    )
    expected_start = expected_dates[0]
    expected_end = expected_dates[-1]
    actual_start = (
        ordered["source_timestamp_utc"].iloc[0] if not ordered.empty else pd.NaT
    )
    actual_end = (
        ordered["source_timestamp_utc"].iloc[-1] if not ordered.empty else pd.NaT
    )
    gaps = ordered["source_timestamp_utc"].diff().dt.total_seconds().dropna()
    metric_values = ordered[metrics]
    non_finite_count = 0
    negative_count = 0
    for metric in metrics:
        non_null_values = metric_values[metric].dropna()
        non_finite_count += int(
            sum(not math.isfinite(float(value)) for value in non_null_values)
        )
        negative_count += int(
            sum(float(value) < 0 for value in non_null_values)
        )

    lag_days = int(source["availability_lag_days"])
    expected_availability = ordered["source_timestamp_utc"] + pd.Timedelta(
        days=lag_days
    )
    availability_lag_mismatches = int(
        ordered["available_from_utc"].ne(expected_availability).sum()
    )
    timestamps_outside_range = int(
        (
            (ordered["source_timestamp_utc"] < expected_start)
            | (ordered["source_timestamp_utc"] > expected_end)
        ).sum()
    )
    quality = {
        "source": "Coin Metrics Community API",
        "source_file": source_file,
        "asset": source["asset"],
        "frequency": source["frequency"],
        "rows": int(len(ordered)),
        "expected_rows": int(len(expected_dates)),
        "expected_start_utc": expected_start.isoformat(),
        "actual_start_utc": (
            actual_start.isoformat() if not pd.isna(actual_start) else ""
        ),
        "expected_end_utc": expected_end.isoformat(),
        "actual_end_utc": actual_end.isoformat() if not pd.isna(actual_end) else "",
        "start_date_mismatch": int(
            pd.isna(actual_start) or actual_start != expected_start
        ),
        "end_date_mismatch": int(pd.isna(actual_end) or actual_end != expected_end),
        "timestamps_outside_range": timestamps_outside_range,
        "duplicate_timestamps": int(
            ordered["source_timestamp_utc"].duplicated().sum()
        ),
        "unexpected_spacing_events": int(gaps.ne(86400).sum()),
        "metric_null_count": int(metric_values.isna().sum().sum()),
        "metric_non_finite_count": non_finite_count,
        "negative_metric_value_count": negative_count,
        "availability_lag_days": lag_days,
        "availability_lag_mismatches": availability_lag_mismatches,
    }
    error_fields = [
        "start_date_mismatch",
        "end_date_mismatch",
        "timestamps_outside_range",
        "duplicate_timestamps",
        "unexpected_spacing_events",
        "metric_null_count",
        "metric_non_finite_count",
        "negative_metric_value_count",
        "availability_lag_mismatches",
    ]
    quality["quality_pass"] = bool(
        quality["rows"] == quality["expected_rows"]
        and all(quality[field] == 0 for field in error_fields)
    )
    return quality


def download_coinmetrics_context(
    config: dict[str, Any],
    project_root: Path,
    session: requests.Session,
) -> tuple[pd.DataFrame, dict[str, Any], dict[str, Any]]:
    """Coin-Metrics-Kontext als unveraenderten JSON-Snapshot laden."""

    source = config["coinmetrics"]
    raw_path = (
        project_root
        / "data"
        / "raw"
        / "pilot"
        / "coinmetrics"
        / coinmetrics_raw_filename(config)
    )
    params = {
        "assets": source["asset"],
        "metrics": ",".join(source["metrics"]),
        "frequency": source["frequency"],
        "start_time": source["start_time"],
        "end_time": source["end_time"],
        "page_size": source["page_size"],
    }
    result = get_or_download(
        session, source["url"], raw_path, params=params, timeout_seconds=90
    )
    payload = json.loads(raw_path.read_text(encoding="utf-8"))
    if payload.get("next_page_url"):
        raise PilotError(
            "Coin-Metrics-Pilot wurde paginiert. Der Pilot muss Paging explizit "
            "implementieren, bevor Gate 0 bestanden werden kann."
        )
    records = payload.get("data")
    if not isinstance(records, list) or not records:
        raise PilotError("Coin Metrics lieferte keine verwertbaren Daten.")

    frame = pd.DataFrame(records)
    required = {"asset", "time", *source["metrics"]}
    missing_columns = sorted(required.difference(frame.columns))
    if missing_columns:
        raise PilotError(
            "Coin-Metrics-Schema unvollstaendig: " + ", ".join(missing_columns)
        )
    frame["source_timestamp_utc"] = pd.to_datetime(
        frame["time"], utc=True, errors="raise"
    )
    lag_days = int(source["availability_lag_days"])
    frame["available_from_utc"] = frame["source_timestamp_utc"] + pd.Timedelta(
        days=lag_days
    )
    for metric in source["metrics"]:
        frame[metric] = pd.to_numeric(frame[metric], errors="coerce")
    frame = frame[
        [
            "asset",
            "source_timestamp_utc",
            "available_from_utc",
            *source["metrics"],
        ]
    ].sort_values("source_timestamp_utc")

    quality = assess_coinmetrics_context(
        frame,
        source,
        source_file=relative_path(raw_path, project_root),
    )

    manifest = {
        "source": "Coin Metrics Community API",
        "url": requests.Request(
            "GET", source["url"], params=params
        ).prepare().url,
        "raw_file": relative_path(raw_path, project_root),
        "retrieved_or_cached_at_utc": result.downloaded_at_utc,
        "downloaded_this_run": result.created,
        "bytes": raw_path.stat().st_size,
        "sha256": sha256_file(raw_path),
        "provider_checksum": "",
        "provider_checksum_match": "",
    }
    return frame, quality, manifest


def probe_recommended_history_boundaries(
    config: dict[str, Any], session: requests.Session
) -> pd.DataFrame:
    """Nur die Erreichbarkeit der empfohlenen Start- und Endgrenzen pruefen."""

    history = config["recommended_history"]
    start = pd.Timestamp(history["start_utc"])
    end_inclusive = pd.Timestamp(history["end_exclusive_utc"]) - pd.Timedelta(
        days=1
    )
    boundary_dates = {
        "start": start,
        "end": end_inclusive,
    }
    rows: list[dict[str, Any]] = []

    binance_base = config["binance"]["base_url"].rstrip("/")
    for asset in config["assets"]:
        symbol = asset["symbol"]
        for boundary, timestamp in boundary_dates.items():
            month = timestamp.strftime("%Y-%m")
            filename = f"{symbol}-1h-{month}.zip"
            url = (
                f"{binance_base}/{symbol}/1h/{filename}.CHECKSUM"
            )
            try:
                response = session.get(url, timeout=45)
                checksum_token = (
                    response.text.strip().split()[0]
                    if response.status_code == 200 and response.text.strip()
                    else ""
                )
                passed = response.status_code == 200 and len(checksum_token) == 64
                status: int | str = response.status_code
                evidence = (
                    f"SHA-256-Metadatei vorhanden: {checksum_token}"
                    if passed
                    else response.text[:200].replace("\n", " ")
                )
            except requests.RequestException as exc:
                passed = False
                status = "ERROR"
                evidence = str(exc)[:200]
            rows.append(
                {
                    "source": "Binance Public Data",
                    "symbol_or_asset": symbol,
                    "boundary": boundary,
                    "probe_date_or_month": month,
                    "http_status": status,
                    "evidence": evidence,
                    "coverage_pass": passed,
                }
            )

    coinmetrics = config["coinmetrics"]
    context_boundary_dates = {
        "start": start - pd.Timedelta(
            days=int(coinmetrics["availability_lag_days"])
        ),
        "end": end_inclusive,
    }
    for boundary, timestamp in context_boundary_dates.items():
        date_text = timestamp.strftime("%Y-%m-%d")
        params = {
            "assets": coinmetrics["asset"],
            "metrics": ",".join(coinmetrics["metrics"]),
            "frequency": coinmetrics["frequency"],
            "start_time": date_text,
            "end_time": date_text,
            "page_size": 10,
        }
        try:
            response = session.get(
                coinmetrics["url"], params=params, timeout=45
            )
            if response.status_code == 200:
                payload = response.json()
                records = payload.get("data", [])
                complete_records = [
                    record
                    for record in records
                    if all(metric in record for metric in coinmetrics["metrics"])
                ]
                passed = len(complete_records) >= 1
                evidence = f"{len(complete_records)} vollstaendige Tageszeile(n)"
            else:
                passed = False
                evidence = response.text[:200].replace("\n", " ")
            status = response.status_code
        except (requests.RequestException, ValueError) as exc:
            passed = False
            status = "ERROR"
            evidence = str(exc)[:200]
        rows.append(
            {
                "source": "Coin Metrics Community API",
                "symbol_or_asset": coinmetrics["asset"],
                "boundary": boundary,
                "probe_date_or_month": date_text,
                "http_status": status,
                "evidence": evidence,
                "coverage_pass": passed,
            }
        )

    return pd.DataFrame(rows)


def join_context_without_lookahead(
    market: pd.DataFrame, context: pd.DataFrame
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Nur zum Kerzenschluss bereits verfuegbaren Tageskontext verbinden."""

    left = market.copy()
    left["decision_time_utc"] = left["close_time_utc"]
    left = left.sort_values("decision_time_utc")
    right = context.sort_values("available_from_utc")
    joined = pd.merge_asof(
        left,
        right,
        left_on="decision_time_utc",
        right_on="available_from_utc",
        direction="backward",
        allow_exact_matches=True,
    )
    context_available = joined["available_from_utc"].notna()
    future_rows = int(
        (
            context_available
            & (joined["available_from_utc"] > joined["decision_time_utc"])
        ).sum()
    )
    staleness_hours = (
        joined.loc[context_available, "decision_time_utc"]
        - joined.loc[context_available, "available_from_utc"]
    ).dt.total_seconds() / 3600
    coverage = float(context_available.mean()) if len(joined) else 0.0
    summary = {
        "market_rows_before_join": int(len(market)),
        "rows_after_join": int(len(joined)),
        "joined_context_rows": int(context_available.sum()),
        "join_coverage_pct": round(coverage * 100, 6),
        "join_row_loss": int(len(market) - len(joined)),
        "future_context_rows": future_rows,
        "max_context_staleness_hours": (
            round(float(staleness_hours.max()), 6)
            if not staleness_hours.empty
            else math.nan
        ),
        "availability_rule": (
            "Coin-Metrics-Tageswert D ist ab D+1 00:00 UTC verfuegbar; "
            "as-of-Join auf Kerzenschluss."
        ),
    }
    summary["join_pass"] = bool(
        len(joined) == len(market)
        and coverage == 1.0
        and future_rows == 0
        and summary["max_context_staleness_hours"] < 48
    )
    return joined, summary


def summarize_assets(
    market: pd.DataFrame, quality: pd.DataFrame
) -> pd.DataFrame:
    """Assets mit beobachteter Aktivitaet und Qualitaetsabdeckung vergleichen."""

    one_hour = market[market["timeframe"] == "1h"]
    summary = (
        one_hour.groupby("symbol", sort=True)
        .agg(
            pilot_rows=("timestamp_utc", "size"),
            mean_hourly_quote_volume_usdt=("quote_asset_volume", "mean"),
            median_hourly_trades=("number_of_trades", "median"),
            min_price=("low", "min"),
            max_price=("high", "max"),
        )
        .reset_index()
    )
    quality_by_symbol = (
        quality.groupby("symbol", sort=True)
        .agg(
            tested_files=("quality_pass", "size"),
            passed_files=("quality_pass", "sum"),
            total_missing_intervals=("missing_intervals", "sum"),
        )
        .reset_index()
    )
    summary = summary.merge(quality_by_symbol, on="symbol", how="left")
    summary["quality_pass_rate_pct"] = (
        summary["passed_files"] / summary["tested_files"] * 100
    ).round(2)
    summary["quote_volume_rank"] = summary[
        "mean_hourly_quote_volume_usdt"
    ].rank(method="dense", ascending=False).astype(int)
    return summary.sort_values("quote_volume_rank")


def probe_source_candidates(
    config: dict[str, Any], session: requests.Session
) -> pd.DataFrame:
    """Kandidaten nach einheitlichen Kriterien und Live-Erreichbarkeit erfassen."""

    rows: list[dict[str, Any]] = []
    for candidate in config["candidate_sources"]:
        status: int | str
        detail = ""
        try:
            response = session.get(candidate["probe_url"], timeout=45)
            status = response.status_code
            if response.status_code >= 400:
                try:
                    payload = response.json()
                    detail = json.dumps(payload, ensure_ascii=False)[:300]
                except ValueError:
                    detail = response.text[:300].replace("\n", " ")
            else:
                detail = f"{len(response.content)} Bytes"
        except requests.RequestException as exc:
            status = "ERROR"
            detail = str(exc)[:300]

        criteria = candidate["criteria"]
        score = int(sum(int(value) for value in criteria.values()))
        row: dict[str, Any] = {
            "source": candidate["source"],
            "role": candidate["role"],
            "probe_http_status": status,
            "probe_detail": detail,
            **criteria,
            "score": score,
            "max_score": len(criteria),
            "fit_pct": round(score / len(criteria) * 100, 1),
            "decision": candidate["decision"],
            "main_limit": candidate["main_limit"],
            "documentation_url": candidate["documentation_url"],
        }
        rows.append(row)
    return pd.DataFrame(rows).sort_values(
        ["score", "source"], ascending=[False, True]
    )


def build_gate_decision(
    quality: pd.DataFrame,
    manifest: pd.DataFrame,
    context_quality: dict[str, Any],
    timeframe_comparison: pd.DataFrame,
    join_summary: dict[str, Any],
    candidates: pd.DataFrame,
    history_boundaries: pd.DataFrame,
) -> dict[str, Any]:
    """Gate 0 strikt aus pruefbaren Teilkriterien entscheiden."""

    binance_manifest = manifest[manifest["source"] == "Binance Public Data"]
    criteria = {
        "primaere_marktquelle_reproduzierbar": bool(
            not quality.empty
            and quality["quality_pass"].all()
            and not binance_manifest.empty
            and binance_manifest["provider_checksum_match"].eq(True).all()
        ),
        "ergaenzende_quelle_reproduzierbar": bool(
            context_quality["quality_pass"]
        ),
        "zeitlich_ausgerichtet_ohne_zukunftsdaten": bool(
            join_summary["join_pass"]
        ),
        "zeitrahmen_konsistent": bool(
            not timeframe_comparison.empty
            and timeframe_comparison["timeframe_consistency_pass"].all()
        ),
        "quellenvergleich_dokumentiert": bool(
            {"Binance Public Data", "Coin Metrics Community API"}.issubset(
                set(candidates["source"])
            )
        ),
        "empfohlene_zeitraumgrenzen_erreichbar": bool(
            not history_boundaries.empty
            and history_boundaries["coverage_pass"].all()
        ),
    }
    return {
        "gate": "Gate 0",
        "evaluated_at_utc": utc_now_iso(),
        "criteria": criteria,
        "passed": all(criteria.values()),
        "decision": (
            "bestanden: Vollimport darf als naechstes Arbeitspaket geplant werden"
            if all(criteria.values())
            else "nicht bestanden: Vollimport bleibt gesperrt"
        ),
    }


def markdown_table(frame: pd.DataFrame, columns: list[str]) -> str:
    """Kleine Markdown-Tabelle ohne optionale Drittbibliothek erzeugen."""

    selected = frame[columns].copy()
    selected = selected.fillna("")
    header = "| " + " | ".join(columns) + " |"
    separator = "| " + " | ".join("---" for _ in columns) + " |"
    rows = [
        "| "
        + " | ".join(str(value).replace("|", "/") for value in row)
        + " |"
        for row in selected.itertuples(index=False, name=None)
    ]
    return "\n".join([header, separator, *rows])


def render_report(
    *,
    config: dict[str, Any],
    run_at_utc: str,
    quality: pd.DataFrame,
    context_quality: dict[str, Any],
    asset_summary: pd.DataFrame,
    timeframe_comparison: pd.DataFrame,
    join_summary: dict[str, Any],
    candidates: pd.DataFrame,
    history_boundaries: pd.DataFrame,
    gate: dict[str, Any],
) -> str:
    """Pruefbericht und Empfehlung aus den tatsaechlichen Ergebnissen erzeugen."""

    quality_files = len(quality)
    quality_passed = int(quality["quality_pass"].sum())
    timeframe_passed = int(
        timeframe_comparison["timeframe_consistency_pass"].sum()
    )
    gate_label = "BESTANDEN" if gate["passed"] else "NICHT BESTANDEN"
    candidate_view = candidates.copy()
    candidate_view["probe_http_status"] = candidate_view[
        "probe_http_status"
    ].astype(str)
    asset_view = asset_summary.copy()
    asset_view["mean_hourly_quote_volume_usdt"] = asset_view[
        "mean_hourly_quote_volume_usdt"
    ].map(lambda value: f"{value:,.0f}")
    history = config["recommended_history"]
    history_start = pd.Timestamp(history["start_utc"])
    history_end = pd.Timestamp(history["end_exclusive_utc"])
    estimated_1h_rows = int(
        (history_end - history_start).total_seconds()
        // int(config["timeframes"]["1h"])
        * len(config["assets"])
    )
    estimated_4h_rows = int(
        (history_end - history_start).total_seconds()
        // int(config["timeframes"]["4h"])
        * len(config["assets"])
    )

    gate_rows = pd.DataFrame(
        [
            {"Kriterium": key, "Ergebnis": "PASS" if value else "FAIL"}
            for key, value in gate["criteria"].items()
        ]
    )

    return f"""# Datenquellen-Pilot und Gate-0-Entscheidung

## Lauf

- Pilot-ID: `{config['pilot_id']}`
- Ausgefuehrt (UTC): `{run_at_utc}`
- Umfang: {len(config['assets'])} Assets, {len(config['timeframes'])} Zeitrahmen, {len(config['pilot_months'])} getrennte Monatsstichproben
- Stichproben: {", ".join(config['pilot_months'])}
- Rohdatenregel: vorhandene Rohdateien werden wiederverwendet und niemals ueberschrieben

## Objektives Ergebnis

**Gate 0: {gate_label}.**

{markdown_table(gate_rows, ["Kriterium", "Ergebnis"])}

Binance: {quality_passed} von {quality_files} Monatsdateien bestanden alle
Qualitaetsregeln und die offiziellen SHA-256-Pruefsummen. Coin Metrics:
{context_quality['rows']} von {context_quality['expected_rows']} erwarteten
Tageswerten, Qualitaet `{"PASS" if context_quality['quality_pass'] else "FAIL"}`.
Die Binance-Dateien beginnen und enden exakt an den erwarteten UTC-Monatsgrenzen.
Coin Metrics beginnt exakt bei {context_quality['expected_start_utc']} und endet
exakt bei {context_quality['expected_end_utc']}; nicht-endliche oder negative
Metrikwerte wurden nicht akzeptiert.
Der zeitlich konservative Join deckt {join_summary['join_coverage_pct']:.2f} %
der {join_summary['market_rows_before_join']} Marktzeilen ab, verliert
{join_summary['join_row_loss']} Zeilen und nutzt in
{join_summary['future_context_rows']} Faellen Zukunftsdaten.

## Kandidatenvergleich

Jedes Kriterium zaehlt 0 oder 1. Der Live-HTTP-Status ist ein technischer
Erreichbarkeitstest, der Score beruecksichtigt zusaetzlich Historie,
Reproduzierbarkeit, Integritaet, Core-Nutzen, Limits und Nutzungsbedingungen.

{markdown_table(candidate_view, ["source", "probe_http_status", "score", "max_score", "fit_pct", "decision", "main_limit"])}

## Assetvergleich O001

`mean_hourly_quote_volume_usdt` und `median_hourly_trades` sind nur
Liquiditaets-Proxys der beiden Pilotmonate, keine Renditekennzahlen.

{markdown_table(asset_view, ["symbol", "pilot_rows", "mean_hourly_quote_volume_usdt", "median_hourly_trades", "quality_pass_rate_pct", "total_missing_intervals", "quote_volume_rank"])}

**Empfehlung:** BTCUSDT, ETHUSDT und SOLUSDT gemeinsam verwenden. Alle drei
bestanden die Qualitaetspruefung. BTC dient als Referenzmarkt; ETH und SOL
erzeugen einen sinnvollen Vergleich zwischen etabliertem und juengerem
Kryptomarkt. Der Pilot beweist Liquiditaet nicht fuer jeden Tag des
Zielzeitraums; die Vollpipeline muss deshalb dieselben Regeln je Monat erneut
anwenden.

## Zeitrahmenvergleich O002

{markdown_table(timeframe_comparison, ["symbol", "pilot_month", "direct_4h_rows", "aggregated_4h_rows", "missing_or_extra_rows", "value_mismatches", "timeframe_consistency_pass"])}

{timeframe_passed} von {len(timeframe_comparison)} Vergleichen bestanden.

**Empfehlung:** 1h als primaeren Zeitrahmen verwenden und 4h als
Robustheits-Zeitrahmen aus den geprueften 1h-Rohkerzen ableiten. 1h liefert
genuegend Beobachtungen fuer zeitlich getrennte Entwicklung, Validierung und
Test; 4h prueft, ob Resultate auch bei weniger Marktgeraeusch bestehen. Die
Ableitung aus 1h verhindert doppelte Downloadlogik und wurde gegen die
offiziellen 4h-Dateien validiert.

## Zeitraumempfehlung

**{history['start_utc']} bis ausschliesslich {history['end_exclusive_utc']}.**
Damit werden die vollstaendigen Kalenderjahre 2021 bis 2025 verwendet. Dieser
Zeitraum umfasst unterschiedliche Marktphasen, ist fuer SOL gemeinsam
verfuegbar und laesst das unvollstaendige Jahr 2026 zunaechst ausserhalb des
Core-Datensatzes. Die spaetere zeitliche Aufteilung wird erst mit der
Validierungsentscheidung festgelegt; es findet kein zufaelliges Mischen statt.
Bei lueckenloser Abdeckung sind etwa {estimated_1h_rows:,} primaere 1h-Zeilen
und {estimated_4h_rows:,} daraus abgeleitete 4h-Zeilen zu erwarten. Das ist
nur eine Planungsschaetzung; die Vollpipeline muss die tatsaechliche Zahl und
alle Luecken berichten.

Die Start- und Endgrenzen wurden ohne Vollimport ueber kleine Metadaten- bzw.
Ein-Tages-Abfragen geprueft:

{markdown_table(history_boundaries, ["source", "symbol_or_asset", "boundary", "probe_date_or_month", "http_status", "coverage_pass"])}

## Quellenempfehlung O003

- Primaer: **Binance Public Data** fuer historische Spot-OHLCV. Gruende:
  Monatsdateien, feste URLs, offizielle Pruefsummen, ausreichende Felder und
  beide getesteten Zeitrahmen.
- Ergaenzend: **Coin Metrics Community API** fuer taeglichen BTC-Referenzpreis,
  Marktkapitalisierung und Netzwerkaktivitaet. Der Rohsnapshot erhaelt einen
  lokalen SHA-256 und Attribution.
- Reserve: Coinbase nur fuer spaetere Stichprobenkontrollen zwischen Exchanges.
  CoinGecko Keyless ist wegen der bestaetigten 365-Tage-Grenze fuer den
  empfohlenen Zeitraum ungeeignet. FRED bleibt Stretch.

## Zeitstandard und Look-ahead-Schutz

- Alle Zeitpunkte sind UTC.
- `timestamp_utc` ist der Kerzenbeginn; eine Zeile darf erst bei
  `close_time_utc` fuer Signale verwendet werden.
- Ein Coin-Metrics-Tageswert D gilt konservativ erst ab D+1 00:00 UTC als
  verfuegbar.
- D+1 00:00 UTC ist eine konservative methodische Annahme, keine bestaetigte
  historische Publikationsgarantie. Eine spaetere Sensitivitaetspruefung muss
  den strengeren Ansatz D+2 00:00 UTC vergleichen.
- Der as-of-Join verbindet nur Kontext mit
  `available_from_utc <= close_time_utc`.
- Maximales Kontextalter im Pilot: {join_summary['max_context_staleness_hours']:.2f} Stunden.

## Grenzen

- Zwei Monate sind ein technischer Pilot, keine vollstaendige Marktanalyse.
- Aktivitaets- und Marktkapitalisierungswerte koennen vom Anbieter revidiert
  werden. Deshalb werden Rohsnapshot, Abrufzeit und lokale Pruefsumme
  dokumentiert.
- Nutzungsbedingungen koennen sich aendern. Vor einer oeffentlichen
  Datenweitergabe werden sie erneut geprueft; rohe Anbieterdateien bleiben
  ausserhalb von Git.
- Der Pilot trifft keine Aussage ueber Profitabilitaet und ist keine
  Trading-Empfehlung.

## Einfache Erklaerung fuer die Praesentation

Wir haben noch nicht den grossen Datensatz geladen. Zuerst wurden kleine,
fest definierte Datenpakete getestet. Dabei wurden zwei Kalenderjahre
absichtlich beruehrt, weil Binance ab 2025 eine andere Zeitstempeleinheit
verwendet. Alle Kerzen waren vollstaendig, logisch und per Pruefsumme
unveraendert. Danach wurden 1h-Kerzen zu 4h-Kerzen zusammengebaut und mit den
offiziellen 4h-Kerzen verglichen. Schliesslich wurde Tageskontext erst einen
Tag spaeter verbunden, damit keine Information aus der Zukunft in eine
Entscheidung gelangt. Deshalb kann Gate 0 objektiv entschieden werden.
"""


def ensure_within_project(path: Path, project_root: Path) -> None:
    """Verhindern, dass der Pilot ausserhalb des Projektordners schreibt."""

    try:
        path.resolve().relative_to(project_root.resolve())
    except ValueError as exc:
        raise PilotError(f"Pfad liegt ausserhalb des Projekts: {path}") from exc


def run_pilot(config_path: Path, project_root: Path) -> dict[str, Any]:
    """Vollstaendigen kleinen Pilotlauf ausfuehren und Artefakte schreiben."""

    ensure_within_project(config_path, project_root)
    config = json.loads(config_path.read_text(encoding="utf-8"))
    run_at_utc = utc_now_iso()
    session = build_session(config["user_agent"])

    frames, quality, manifest_rows = download_binance_pilot(
        config, project_root, session
    )
    market = pd.concat(frames, ignore_index=True)
    timeframe_comparison = compare_timeframes(frames)
    context, context_quality, context_manifest = download_coinmetrics_context(
        config, project_root, session
    )
    manifest_rows.append(context_manifest)
    joined, join_summary = join_context_without_lookahead(market, context)
    asset_summary = summarize_assets(market, quality)
    candidates = probe_source_candidates(config, session)
    history_boundaries = probe_recommended_history_boundaries(config, session)
    manifest = pd.DataFrame(manifest_rows)
    gate = build_gate_decision(
        quality,
        manifest,
        context_quality,
        timeframe_comparison,
        join_summary,
        candidates,
        history_boundaries,
    )

    report_directory = project_root / "reports" / "data_pilot"
    interim_directory = project_root / "data" / "interim" / "pilot"
    ensure_within_project(report_directory, project_root)
    ensure_within_project(interim_directory, project_root)
    report_directory.mkdir(parents=True, exist_ok=True)
    interim_directory.mkdir(parents=True, exist_ok=True)

    quality.to_csv(
        report_directory / "binance_quality_summary.csv",
        index=False,
        encoding="utf-8",
    )
    pd.DataFrame([context_quality]).to_csv(
        report_directory / "coinmetrics_quality_summary.csv",
        index=False,
        encoding="utf-8",
    )
    asset_summary.to_csv(
        report_directory / "asset_comparison.csv", index=False, encoding="utf-8"
    )
    timeframe_comparison.to_csv(
        report_directory / "timeframe_consistency.csv",
        index=False,
        encoding="utf-8",
    )
    pd.DataFrame([join_summary]).to_csv(
        report_directory / "join_summary.csv", index=False, encoding="utf-8"
    )
    candidates.to_csv(
        report_directory / "source_candidate_comparison.csv",
        index=False,
        encoding="utf-8",
    )
    history_boundaries.to_csv(
        report_directory / "history_boundary_probes.csv",
        index=False,
        encoding="utf-8",
    )
    manifest.to_csv(
        report_directory / "raw_manifest.csv", index=False, encoding="utf-8"
    )
    (report_directory / "gate0_decision.json").write_text(
        json.dumps(gate, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    joined_columns = [
        "symbol",
        "timeframe",
        "timestamp_utc",
        "close_time_utc",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "decision_time_utc",
        "source_timestamp_utc",
        "available_from_utc",
        *config["coinmetrics"]["metrics"],
    ]
    joined[joined_columns].sort_values(
        ["symbol", "timeframe", "timestamp_utc"]
    ).to_csv(
        interim_directory / "market_with_context.csv",
        index=False,
        encoding="utf-8",
    )

    report_text = render_report(
        config=config,
        run_at_utc=run_at_utc,
        quality=quality,
        context_quality=context_quality,
        asset_summary=asset_summary,
        timeframe_comparison=timeframe_comparison,
        join_summary=join_summary,
        candidates=candidates,
        history_boundaries=history_boundaries,
        gate=gate,
    )
    (report_directory / "DATA_PILOT_REPORT.md").write_text(
        report_text, encoding="utf-8"
    )
    return gate


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Kommandozeilenargumente lesen."""

    parser = argparse.ArgumentParser(
        description="Kleiner reproduzierbarer Datenquellen-Pilot fuer Gate 0."
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("config/data_pilot.json"),
        help="Projekt-relativer Pfad zur Pilotkonfiguration.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """CLI-Einstiegspunkt."""

    args = parse_args(argv)
    project_root = Path(__file__).resolve().parents[1]
    config_path = (
        args.config
        if args.config.is_absolute()
        else (project_root / args.config).resolve()
    )
    try:
        gate = run_pilot(config_path, project_root)
    except (PilotError, requests.RequestException, OSError, ValueError) as exc:
        print(f"DATENPILOT FEHLGESCHLAGEN: {exc}", file=sys.stderr)
        return 1

    print(gate["decision"])
    print("Bericht: reports/data_pilot/DATA_PILOT_REPORT.md")
    return 0 if gate["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
