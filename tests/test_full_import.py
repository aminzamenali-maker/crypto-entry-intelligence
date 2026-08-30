"""Vollstaendig offline laufende Tests fuer die Phase-1A-Importplanung."""

from __future__ import annotations

import copy
import csv
import hashlib
import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

import pandas as pd

import src.full_import as full_import_module
from src.data_pilot import infer_unix_unit
from src.full_import import (
    ANOMALY_EVIDENCE_POLICY_ID,
    BINANCE_INTERIM_1H_FIELDS,
    BINANCE_INTERIM_1H_SCHEMA_ID,
    CHECKPOINT_SCHEMA_VERSION,
    EXECUTE_CONFIRMATION,
    EXECUTION_REPORT_FILES,
    LEGACY_PROCESSING_POLICY_FINGERPRINT,
    REQUIRED_ASSETS,
    SOURCE_ANOMALY_FIELDS,
    TIMESTAMP_POLICY_ID,
    BinanceTask,
    ConfigurationError,
    IntegrityError,
    PartialInterimError,
    SafetyError,
    aggregate_complete_1h_to_4h,
    aggregate_execution_counts,
    build_source_anomaly_rows,
    build_binance_tasks,
    build_download_plan,
    build_dry_run_summary,
    dataframe_csv_bytes,
    execute_full_import,
    expected_binance_timestamp_unit,
    inclusive_day_count,
    inspect_binance_cache,
    load_or_initialize_authoritative_state,
    main,
    month_sequence,
    normalize_coinmetrics_records,
    parse_binance_archive,
    parse_provider_checksum_text,
    persist_authoritative_state,
    processing_policy_fingerprint,
    process_binance_task,
    project_binance_interim_1h,
    recover_report_projections,
    render_dict_rows_csv,
    run_binance_stage,
    safe_project_path,
    sha256_bytes,
    validate_binance_month,
    validate_coinmetrics_next_url,
    validate_config,
    write_bytes_atomic_no_overwrite,
    write_coinmetrics_interim_context,
    write_dry_run_artifacts,
    write_generated_file_cached,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = REPOSITORY_ROOT / "config" / "full_import.json"


def fresh_config() -> dict[str, object]:
    """Unabhaengige Kopie der verbindlichen Testkonfiguration liefern."""

    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def write_temp_config(project_root: Path) -> Path:
    """Konfiguration innerhalb eines temporaeren Testprojekts anlegen."""

    path = project_root / "config" / "full_import.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(fresh_config(), indent=2) + "\n", encoding="utf-8"
    )
    return path


def sample_task(
    *,
    month: str = "2021-01",
    symbol: str = "BTCUSDT",
    archive_path: str | None = None,
) -> BinanceTask:
    """Kleinen Cache-Auftrag fuer Dateisicherheitstests bauen."""

    start = pd.Timestamp(f"{month}-01", tz="UTC")
    next_start = start + pd.offsets.MonthBegin(1)
    expected_1h_rows = int((next_start - start) / pd.Timedelta(hours=1))
    if archive_path is None:
        archive_path = (
            "data/raw/full_import/binance/spot/monthly/klines/"
            f"{symbol}/1h/{symbol}-1h-{month}.zip"
        )
    return BinanceTask(
        symbol=symbol,
        month=month,
        interval="1h",
        expected_1h_rows=expected_1h_rows,
        expected_4h_rows=expected_1h_rows // 4,
        archive_url=(
            "https://data.binance.vision/data/spot/monthly/klines/"
            f"{symbol}/1h/{symbol}-1h-{month}.zip"
        ),
        checksum_url=(
            "https://data.binance.vision/data/spot/monthly/klines/"
            f"{symbol}/1h/{symbol}-1h-{month}.zip.CHECKSUM"
        ),
        archive_path=archive_path,
        checksum_path=f"{archive_path}.CHECKSUM",
    )


def minimal_1h_frame(timestamps: pd.DatetimeIndex) -> pd.DataFrame:
    """OHLCV-Testframe fuer die strenge 4h-Aggregation erzeugen."""

    return pd.DataFrame(
        {
            "symbol": "BTCUSDT",
            "timestamp_utc": timestamps,
            "close_time_utc": timestamps
            + pd.Timedelta(hours=1)
            - pd.Timedelta(milliseconds=1),
            "open": range(100, 100 + len(timestamps)),
            "high": range(102, 102 + len(timestamps)),
            "low": range(99, 99 + len(timestamps)),
            "close": range(101, 101 + len(timestamps)),
            "volume": 1.0,
            "quote_asset_volume": 100.0,
            "number_of_trades": 10,
            "taker_buy_base_volume": 0.5,
            "taker_buy_quote_volume": 50.0,
            "timestamp_unit": "ms",
        }
    )


def complete_month_frame(
    unit: str = "ms", month: str = "2021-01"
) -> pd.DataFrame:
    """Vollstaendigen, gueltigen Testmonat als normalisierte Daten bauen."""

    start = pd.Timestamp(f"{month}-01", tz="UTC")
    next_start = start + pd.offsets.MonthBegin(1)
    timestamps = pd.date_range(
        start, next_start, freq="1h", inclusive="left"
    )
    resolution = (
        pd.Timedelta(milliseconds=1)
        if unit == "ms"
        else pd.Timedelta(microseconds=1)
    )
    return pd.DataFrame(
        {
            "symbol": "BTCUSDT",
            "timeframe": "1h",
            "timestamp_utc": timestamps,
            "close_time_utc": timestamps + pd.Timedelta(hours=1) - resolution,
            "open": 100.0,
            "high": 110.0,
            "low": 90.0,
            "close": 105.0,
            "volume": 10.0,
            "quote_asset_volume": 1000.0,
            "number_of_trades": 20.0,
            "taker_buy_base_volume": 5.0,
            "taker_buy_quote_volume": 500.0,
            "source": "binance_public_data",
            "timestamp_unit": unit,
        }
    )


def february_2021_continuity_frame() -> pd.DataFrame:
    """Die beobachtete 2021-02-Unterbrechung synthetisch exakt nachbilden."""

    frame = complete_month_frame(month="2021-02")
    shortened = frame["timestamp_utc"].eq(
        pd.Timestamp("2021-02-11T03:00:00Z")
    )
    frame.loc[shortened, "close_time_utc"] = pd.Timestamp(
        "2021-02-11T03:40:54.773Z"
    )
    missing = frame["timestamp_utc"].eq(
        pd.Timestamp("2021-02-11T04:00:00Z")
    )
    return frame.loc[~missing].reset_index(drop=True)


def write_binance_archive(
    root: Path,
    *,
    unit: str = "ms",
    column_count: int = 12,
    task: BinanceTask | None = None,
    normalized: pd.DataFrame | None = None,
) -> tuple[Path, BinanceTask]:
    """Synthetisches Monatsarchiv ohne Netzwerk erzeugen."""

    task = task or sample_task()
    normalized = (
        normalized
        if normalized is not None
        else complete_month_frame(unit, task.month)
    )
    divisor = 1_000_000 if unit == "ms" else 1_000
    raw = pd.DataFrame(
        {
            0: normalized["timestamp_utc"].astype("int64") // divisor,
            1: normalized["open"],
            2: normalized["high"],
            3: normalized["low"],
            4: normalized["close"],
            5: normalized["volume"],
            6: normalized["close_time_utc"].astype("int64") // divisor,
            7: normalized["quote_asset_volume"],
            8: normalized["number_of_trades"],
            9: normalized["taker_buy_base_volume"],
            10: normalized["taker_buy_quote_volume"],
            11: 0,
        }
    )
    if column_count < 12:
        raw = raw.iloc[:, :column_count]
    elif column_count > 12:
        raw[12] = "unexpected"
    archive_path = root / task.archive_path
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    csv_bytes = raw.to_csv(
        index=False, header=False, lineterminator="\n"
    ).encode("utf-8")
    with zipfile.ZipFile(
        archive_path, mode="w", compression=zipfile.ZIP_DEFLATED
    ) as archive:
        archive.writestr(
            f"{task.symbol}-1h-{task.month}.csv", csv_bytes
        )
    return archive_path, task


class ScopePlanningTests(unittest.TestCase):
    def test_month_sequence_has_exactly_60_months_and_correct_boundaries(
        self,
    ) -> None:
        months = month_sequence(
            "2021-01-01T00:00:00Z", "2026-01-01T00:00:00Z"
        )

        self.assertEqual(len(months), 60)
        self.assertEqual(months[0], "2021-01")
        self.assertEqual(months[-1], "2025-12")

    def test_month_sequence_rejects_non_month_boundary(self) -> None:
        with self.assertRaises(ConfigurationError):
            month_sequence(
                "2021-01-01T01:00:00Z", "2026-01-01T00:00:00Z"
            )

    def test_builds_180_unique_binance_tasks(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            tasks = build_binance_tasks(fresh_config(), root)

        self.assertEqual(len(tasks), 180)
        self.assertEqual(
            len({(task.symbol, task.month) for task in tasks}), 180
        )

    def test_binance_plan_contains_exactly_360_http_objects(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            rows = build_download_plan(fresh_config(), root)

        binance_rows = [
            row for row in rows if row["source"] == "Binance Public Data"
        ]
        self.assertEqual(len(binance_rows), 360)
        self.assertEqual(
            {row["object_type"] for row in binance_rows},
            {"archive", "checksum"},
        )

    def test_no_direct_4h_download_is_planned(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            tasks = build_binance_tasks(fresh_config(), root)

        self.assertTrue(all(task.interval == "1h" for task in tasks))
        self.assertTrue(all("/4h/" not in task.archive_url for task in tasks))
        self.assertTrue(all("/1h/" in task.archive_url for task in tasks))

    def test_calendar_row_counts_cover_normal_and_leap_years(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            tasks = build_binance_tasks(fresh_config(), Path(directory))
        indexed = {(task.symbol, task.month): task for task in tasks}

        self.assertEqual(indexed[("BTCUSDT", "2023-02")].expected_1h_rows, 672)
        self.assertEqual(indexed[("BTCUSDT", "2024-02")].expected_1h_rows, 696)
        self.assertEqual(indexed[("BTCUSDT", "2024-02")].expected_4h_rows, 174)

    def test_expected_1h_rows_are_43824_per_asset_and_131472_total(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            tasks = build_binance_tasks(fresh_config(), Path(directory))
        totals = {
            symbol: sum(
                task.expected_1h_rows
                for task in tasks
                if task.symbol == symbol
            )
            for symbol in ("BTCUSDT", "ETHUSDT", "SOLUSDT")
        }

        self.assertEqual(set(totals.values()), {43824})
        self.assertEqual(sum(totals.values()), 131472)

    def test_expected_derived_4h_rows_are_10956_per_asset_and_32868_total(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            tasks = build_binance_tasks(fresh_config(), Path(directory))
        totals = {
            symbol: sum(
                task.expected_4h_rows
                for task in tasks
                if task.symbol == symbol
            )
            for symbol in ("BTCUSDT", "ETHUSDT", "SOLUSDT")
        }

        self.assertEqual(set(totals.values()), {10956})
        self.assertEqual(sum(totals.values()), 32868)

    def test_coinmetrics_scope_has_exactly_1828_inclusive_days(self) -> None:
        self.assertEqual(
            inclusive_day_count("2020-12-30", "2025-12-31"), 1828
        )

    def test_d1_primary_and_d2_sensitivity_are_separate(self) -> None:
        config = fresh_config()
        coinmetrics = config["coinmetrics"]

        self.assertEqual(coinmetrics["primary_availability_lag_days"], 1)
        self.assertEqual(
            coinmetrics["sensitivity_availability_lag_days"], 2
        )
        self.assertNotEqual(
            coinmetrics["primary_availability_lag_days"],
            coinmetrics["sensitivity_availability_lag_days"],
        )

    def test_download_plan_is_stably_sorted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            rows = build_download_plan(fresh_config(), Path(directory))
        keys = [
            (
                row["source"],
                row["symbol_or_asset"],
                row["period"],
                row["object_type"],
            )
            for row in rows
        ]

        self.assertEqual(keys, sorted(keys))
        self.assertEqual(len(rows), 361)


class ConfigurationSafetyTests(unittest.TestCase):
    def test_safe_paths_remain_inside_project(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result = safe_project_path(
                root,
                "data/raw/full_import",
                required_prefix="data/raw",
            )

            self.assertTrue(result.is_relative_to(root.resolve()))

    def test_traversal_and_absolute_paths_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaises(SafetyError):
                safe_project_path(root, "../outside")
            with self.assertRaises(SafetyError):
                safe_project_path(root, str(root.parent / "outside"))

    def test_conflicting_no_overwrite_setting_is_rejected(self) -> None:
        config = fresh_config()
        config["safety"]["no_overwrite_raw"] = False
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ConfigurationError):
                validate_config(config, Path(directory))

    def test_conflicting_expected_count_is_rejected(self) -> None:
        config = fresh_config()
        config["expected"]["binance_archive_tasks"] = 179
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ConfigurationError):
                validate_config(config, Path(directory))

    def test_wrong_execute_confirmation_aborts_before_network(self) -> None:
        with tempfile.TemporaryDirectory() as directory, mock.patch(
            "src.full_import.build_session"
        ) as session_builder:
            exit_code = main(
                [
                    "--execute",
                    "--confirm-scope",
                    "WRONG_SCOPE",
                ],
                project_root=Path(directory),
            )

        self.assertEqual(exit_code, 1)
        session_builder.assert_not_called()

    def test_confirmation_without_execute_is_rejected_before_network(self) -> None:
        with tempfile.TemporaryDirectory() as directory, mock.patch(
            "src.full_import.build_session"
        ) as session_builder:
            exit_code = main(
                ["--confirm-scope", EXECUTE_CONFIRMATION],
                project_root=Path(directory),
            )

        self.assertEqual(exit_code, 1)
        session_builder.assert_not_called()

    def test_dry_run_never_creates_network_session(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config_path = write_temp_config(root)
            with mock.patch(
                "src.full_import.build_session"
            ) as session_builder:
                exit_code = main(
                    [
                        "--config",
                        str(config_path.relative_to(root)),
                        "--dry-run",
                    ],
                    project_root=root,
                )

        self.assertEqual(exit_code, 0)
        session_builder.assert_not_called()

    def test_default_mode_is_offline_dry_run(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config_path = write_temp_config(root)
            with mock.patch(
                "src.full_import.build_session"
            ) as session_builder:
                exit_code = main(
                    ["--config", str(config_path.relative_to(root))],
                    project_root=root,
                )

        self.assertEqual(exit_code, 0)
        session_builder.assert_not_called()

    def test_dry_run_creates_only_report_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = fresh_config()
            write_dry_run_artifacts(config, root)

            self.assertFalse((root / "data").exists())
            files = sorted(
                path.relative_to(root).as_posix()
                for path in root.rglob("*")
                if path.is_file()
            )

        self.assertEqual(
            files,
            [
                "reports/full_import/download_plan.csv",
                "reports/full_import/dry_run_summary.json",
            ],
        )

    def test_unsafe_coinmetrics_next_page_url_is_rejected(self) -> None:
        endpoint = (
            "https://community-api.coinmetrics.io/"
            "v4/timeseries/asset-metrics"
        )
        with self.assertRaises(SafetyError):
            validate_coinmetrics_next_url(
                "https://attacker.example/v4/timeseries/asset-metrics?page=2",
                endpoint,
            )


class CacheAndIntegrityTests(unittest.TestCase):
    def test_existing_raw_file_is_not_overwritten(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "raw.bin"
            destination.write_bytes(b"original")

            with self.assertRaises(FileExistsError):
                write_bytes_atomic_no_overwrite(destination, b"replacement")

            self.assertEqual(destination.read_bytes(), b"original")

    def test_valid_cache_is_recognized(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            task = sample_task()
            archive = root / task.archive_path
            checksum = root / task.checksum_path
            archive.parent.mkdir(parents=True, exist_ok=True)
            archive.write_bytes(b"immutable archive")
            checksum.write_text(
                f"{sha256_bytes(b'immutable archive')}  archive.zip\n",
                encoding="utf-8",
            )

            result = inspect_binance_cache(task, root)

        self.assertEqual(result["status"], "cached_valid")
        self.assertTrue(result["archive_exists"])
        self.assertTrue(result["checksum_exists"])

    def test_missing_planned_download_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            result = inspect_binance_cache(sample_task(), Path(directory))

        self.assertEqual(result["status"], "missing_planned_download")
        self.assertFalse(result["archive_exists"])
        self.assertFalse(result["checksum_exists"])

    def test_checksum_mismatch_is_a_hard_error_and_preserves_raw(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            task = sample_task()
            archive = root / task.archive_path
            checksum = root / task.checksum_path
            archive.parent.mkdir(parents=True, exist_ok=True)
            archive.write_bytes(b"corrupt but preserved")
            checksum.write_text(f"{'0' * 64}  archive.zip\n", encoding="utf-8")

            with self.assertRaises(IntegrityError):
                inspect_binance_cache(task, root)

            self.assertEqual(archive.read_bytes(), b"corrupt but preserved")
            self.assertEqual(
                checksum.read_text(encoding="utf-8"),
                f"{'0' * 64}  archive.zip\n",
            )

    def test_invalid_provider_checksum_is_rejected(self) -> None:
        for invalid in ("", "xyz", "g" * 64):
            with self.subTest(invalid=invalid):
                with self.assertRaises(IntegrityError):
                    parse_provider_checksum_text(invalid)


class TransformationTests(unittest.TestCase):
    def test_complete_four_hour_group_is_aggregated(self) -> None:
        timestamps = pd.date_range(
            "2021-01-01T00:00:00Z", periods=4, freq="1h"
        )
        result = aggregate_complete_1h_to_4h(minimal_1h_frame(timestamps))

        self.assertEqual(len(result), 1)
        self.assertEqual(result.loc[0, "constituent_rows"], 4)
        self.assertEqual(result.loc[0, "open"], 100)
        self.assertEqual(result.loc[0, "close"], 104)
        self.assertEqual(result.loc[0, "volume"], 4.0)

    def test_incomplete_or_irregular_1h_group_never_creates_fake_4h(
        self,
    ) -> None:
        incomplete = pd.to_datetime(
            [
                "2021-01-01T00:00:00Z",
                "2021-01-01T01:00:00Z",
                "2021-01-01T03:00:00Z",
            ],
            utc=True,
        )
        irregular_four = pd.to_datetime(
            [
                "2021-01-01T00:00:00Z",
                "2021-01-01T01:00:00Z",
                "2021-01-01T01:00:00Z",
                "2021-01-01T03:00:00Z",
            ],
            utc=True,
        )

        self.assertTrue(
            aggregate_complete_1h_to_4h(
                minimal_1h_frame(incomplete)
            ).empty
        )
        self.assertTrue(
            aggregate_complete_1h_to_4h(
                minimal_1h_frame(irregular_four)
            ).empty
        )

    def test_shortened_1h_candle_excludes_affected_4h_bucket(self) -> None:
        timestamps = pd.date_range(
            "2021-02-11T00:00:00Z", periods=8, freq="1h"
        )
        frame = minimal_1h_frame(timestamps)
        frame.loc[3, "close_time_utc"] = pd.Timestamp(
            "2021-02-11T03:40:54.773Z"
        )

        result = aggregate_complete_1h_to_4h(frame)

        self.assertEqual(len(result), 1)
        self.assertEqual(
            result.loc[0, "timestamp_utc"],
            pd.Timestamp("2021-02-11T04:00:00Z"),
        )

    def test_millisecond_and_microsecond_timestamps_remain_supported(
        self,
    ) -> None:
        self.assertEqual(
            infer_unix_unit(pd.Series([1609459200000, 1609462800000])),
            "ms",
        )
        self.assertEqual(
            infer_unix_unit(
                pd.Series([1735689600000000, 1735693200000000])
            ),
            "us",
        )

    def test_coinmetrics_exact_boundaries_and_values_are_checked(self) -> None:
        config = fresh_config()
        timestamps = pd.date_range(
            "2020-12-30", "2025-12-31", freq="1D", tz="UTC"
        )
        records = [
            {
                "asset": "btc",
                "time": timestamp.isoformat(),
                "PriceUSD": "1.0",
                "CapMrktCurUSD": "2.0",
                "TxCnt": "3.0",
                "AdrActCnt": "4.0",
            }
            for timestamp in timestamps
        ]

        _, quality = normalize_coinmetrics_records(records, config)
        self.assertTrue(quality["quality_pass"])
        self.assertEqual(quality["rows"], 1828)

        shifted = copy.deepcopy(records)
        for record in shifted:
            record["time"] = (
                pd.Timestamp(record["time"]) + pd.Timedelta(days=1)
            ).isoformat()
        _, shifted_quality = normalize_coinmetrics_records(shifted, config)
        self.assertFalse(shifted_quality["quality_pass"])
        self.assertFalse(shifted_quality["start_match"])
        self.assertFalse(shifted_quality["end_match"])

        invalid = copy.deepcopy(records)
        invalid[0]["PriceUSD"] = "inf"
        invalid[1]["TxCnt"] = "-1"
        _, invalid_quality = normalize_coinmetrics_records(invalid, config)
        self.assertFalse(invalid_quality["quality_pass"])
        self.assertEqual(invalid_quality["non_finite_metric_values"], 1)
        self.assertEqual(invalid_quality["negative_metric_values"], 1)


class HardenedBinanceQualityTests(unittest.TestCase):
    def test_observed_february_gap_is_a_source_continuity_anomaly(
        self,
    ) -> None:
        quality = validate_binance_month(
            february_2021_continuity_frame(),
            sample_task(month="2021-02"),
        )

        self.assertTrue(quality["source_integrity_pass"])
        self.assertFalse(quality["continuity_pass"])
        self.assertTrue(quality["value_quality_pass"])
        self.assertFalse(quality["quality_pass"])
        self.assertEqual(
            quality["processing_status"], "source_continuity_anomaly"
        )
        self.assertEqual(quality["rows"], 671)
        self.assertEqual(quality["expected_rows"], 672)
        self.assertEqual(quality["row_delta"], -1)
        self.assertEqual(quality["missing_open_time_count"], 1)
        self.assertEqual(
            json.loads(quality["missing_open_times_utc"]),
            ["2021-02-11T04:00:00+00:00"],
        )
        self.assertEqual(quality["unexpected_open_time_count"], 0)
        self.assertEqual(quality["spacing_errors"], 1)
        self.assertEqual(
            json.loads(quality["spacing_anomalies"]),
            [
                {
                    "previous_open_utc": "2021-02-11T03:00:00+00:00",
                    "current_open_utc": "2021-02-11T05:00:00+00:00",
                    "gap_seconds": 7200.0,
                }
            ],
        )
        self.assertEqual(quality["candle_close_time_errors"], 1)
        self.assertEqual(
            json.loads(quality["close_time_anomalies"]),
            [
                {
                    "open_utc": "2021-02-11T03:00:00+00:00",
                    "actual_close_utc": (
                        "2021-02-11T03:40:54.773000+00:00"
                    ),
                    "expected_close_utc": (
                        "2021-02-11T03:59:59.999000+00:00"
                    ),
                }
            ],
        )
        anomalies = build_source_anomaly_rows(quality)
        self.assertEqual(len(anomalies), 4)
        self.assertEqual(
            {row["anomaly_type"] for row in anomalies},
            {
                "row_count_mismatch",
                "missing_open_time",
                "irregular_hour_spacing",
                "candle_close_time_mismatch",
            },
        )

    def test_wrong_binance_column_count_raises_integrity_error(self) -> None:
        for column_count in (11, 13):
            with self.subTest(column_count=column_count):
                with tempfile.TemporaryDirectory() as directory:
                    archive, task = write_binance_archive(
                        Path(directory), column_count=column_count
                    )
                    with self.assertRaises(IntegrityError):
                        parse_binance_archive(archive, task)

    def test_close_time_far_in_future_fails(self) -> None:
        frame = complete_month_frame()
        frame.loc[frame.index[-1], "close_time_utc"] += pd.Timedelta(days=10)

        quality = validate_binance_month(frame, sample_task())

        self.assertFalse(quality["quality_pass"])
        self.assertEqual(quality["month_end_mismatch"], 1)
        self.assertEqual(quality["close_times_outside_month"], 1)
        self.assertEqual(quality["candle_close_time_errors"], 1)

    def test_wrong_last_month_end_fails(self) -> None:
        frame = complete_month_frame()
        frame.loc[frame.index[-1], "close_time_utc"] -= pd.Timedelta(hours=1)

        quality = validate_binance_month(frame, sample_task())

        self.assertFalse(quality["quality_pass"])
        self.assertEqual(quality["month_end_mismatch"], 1)
        self.assertEqual(quality["candle_close_time_errors"], 1)

    def test_nan_in_quote_volume_fails(self) -> None:
        frame = complete_month_frame()
        frame.loc[10, "quote_asset_volume"] = float("nan")

        quality = validate_binance_month(frame, sample_task())

        self.assertFalse(quality["quality_pass"])
        self.assertEqual(quality["missing_numeric_values"], 1)

    def test_infinite_taker_quote_volume_fails(self) -> None:
        frame = complete_month_frame()
        frame.loc[10, "taker_buy_quote_volume"] = float("inf")

        quality = validate_binance_month(frame, sample_task())

        self.assertTrue(quality["source_integrity_pass"])
        self.assertTrue(quality["continuity_pass"])
        self.assertFalse(quality["value_quality_pass"])
        self.assertFalse(quality["quality_pass"])
        self.assertEqual(quality["processing_status"], "quality_quarantine")
        self.assertEqual(quality["non_finite_value_count"], 1)

    def test_negative_trade_count_fails(self) -> None:
        frame = complete_month_frame()
        frame.loc[10, "number_of_trades"] = -1

        quality = validate_binance_month(frame, sample_task())

        self.assertFalse(quality["quality_pass"])
        self.assertEqual(quality["negative_trade_count_rows"], 1)

    def test_negative_taker_volumes_fail(self) -> None:
        for column in (
            "taker_buy_base_volume",
            "taker_buy_quote_volume",
        ):
            with self.subTest(column=column):
                frame = complete_month_frame()
                frame.loc[10, column] = -1

                quality = validate_binance_month(frame, sample_task())

                self.assertFalse(quality["quality_pass"])
                self.assertEqual(quality["negative_volume_rows"], 1)

    def test_taker_volumes_above_totals_fail(self) -> None:
        cases = (
            ("taker_buy_base_volume", "volume"),
            ("taker_buy_quote_volume", "quote_asset_volume"),
        )
        for taker_column, total_column in cases:
            with self.subTest(taker_column=taker_column):
                frame = complete_month_frame()
                frame.loc[10, taker_column] = frame.loc[10, total_column] + 1

                quality = validate_binance_month(frame, sample_task())

                self.assertFalse(quality["quality_pass"])
        self.assertEqual(
            validate_binance_month(
                complete_month_frame().assign(
                    taker_buy_base_volume=11.0
                ),
                sample_task(),
            )["taker_base_exceeds_total_rows"],
            744,
        )

    def test_non_integer_trade_count_fails(self) -> None:
        frame = complete_month_frame()
        frame.loc[10, "number_of_trades"] = 20.5

        quality = validate_binance_month(frame, sample_task())

        self.assertFalse(quality["quality_pass"])
        self.assertEqual(quality["non_integer_trade_count_rows"], 1)

    def test_valid_millisecond_month_passes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            archive, task = write_binance_archive(Path(directory), unit="ms")
            frame = parse_binance_archive(archive, task)
            quality = validate_binance_month(frame, task)

        self.assertTrue(quality["quality_pass"])
        self.assertEqual(quality["timestamp_unit"], "ms")
        self.assertEqual(
            quality["actual_month_end_utc"],
            "2021-01-31T23:59:59.999000+00:00",
        )

    def test_valid_microsecond_month_passes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            task = sample_task(month="2025-01")
            archive, task = write_binance_archive(
                Path(directory), unit="us", task=task
            )
            frame = parse_binance_archive(archive, task)
            quality = validate_binance_month(frame, task)

        self.assertTrue(quality["quality_pass"])
        self.assertEqual(quality["timestamp_unit"], "us")
        self.assertEqual(
            quality["actual_month_end_utc"],
            "2025-01-31T23:59:59.999999+00:00",
        )

    def test_wrong_coinmetrics_asset_fails(self) -> None:
        config = fresh_config()
        timestamps = pd.date_range(
            "2020-12-30", "2025-12-31", freq="1D", tz="UTC"
        )
        records = [
            {
                "asset": "eth",
                "time": timestamp.isoformat(),
                "PriceUSD": "1",
                "CapMrktCurUSD": "2",
                "TxCnt": "3",
                "AdrActCnt": "4",
            }
            for timestamp in timestamps
        ]

        _, quality = normalize_coinmetrics_records(records, config)

        self.assertFalse(quality["quality_pass"])
        self.assertEqual(quality["asset_mismatch_count"], 1828)


class InterimResumeTests(unittest.TestCase):
    @staticmethod
    def run_month(root: Path) -> dict[str, object]:
        with mock.patch(
            "src.full_import.parse_binance_archive",
            return_value=complete_month_frame(),
        ):
            return process_binance_task(
                sample_task(), fresh_config(), root
            )

    @staticmethod
    def interim_paths(root: Path) -> tuple[Path, Path]:
        month_root = (
            root
            / "data"
            / "interim"
            / "full_import"
            / "binance"
            / "BTCUSDT"
        )
        return (
            month_root / "1h" / "BTCUSDT-1h-2021-01.csv",
            month_root / "4h" / "BTCUSDT-4h-2021-01.csv",
        )

    def test_generated_file_is_created_then_reused(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "deterministic.csv"

            first = write_generated_file_cached(destination, b"same\n")
            before = destination.read_bytes()
            second = write_generated_file_cached(destination, b"same\n")

            self.assertEqual(first, "created")
            self.assertEqual(second, "cached_valid")
            self.assertEqual(destination.read_bytes(), before)

    def test_second_identical_month_run_reuses_both_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = self.run_month(root)
            one_hour, four_hour = self.interim_paths(root)
            before = {
                path: (
                    hashlib.sha256(path.read_bytes()).hexdigest(),
                    path.stat().st_mtime_ns,
                )
                for path in (one_hour, four_hour)
            }

            second = self.run_month(root)
            after = {
                path: (
                    hashlib.sha256(path.read_bytes()).hexdigest(),
                    path.stat().st_mtime_ns,
                )
                for path in (one_hour, four_hour)
            }

        self.assertEqual(first["interim_1h_status"], "created")
        self.assertEqual(first["interim_4h_status"], "created")
        self.assertEqual(second["interim_1h_status"], "cached_valid")
        self.assertEqual(second["interim_4h_status"], "cached_valid")
        self.assertEqual(before, after)

    def test_valid_existing_1h_and_missing_4h_are_resumed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            one_hour, _ = self.interim_paths(root)
            one_hour.parent.mkdir(parents=True, exist_ok=True)
            one_hour.write_bytes(dataframe_csv_bytes(complete_month_frame()))

            result = self.run_month(root)

        self.assertEqual(result["interim_1h_status"], "cached_valid")
        self.assertEqual(result["interim_4h_status"], "created")

    def test_missing_1h_and_valid_existing_4h_are_resumed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _, four_hour = self.interim_paths(root)
            four_hour.parent.mkdir(parents=True, exist_ok=True)
            derived = aggregate_complete_1h_to_4h(complete_month_frame())
            four_hour.write_bytes(dataframe_csv_bytes(derived))

            result = self.run_month(root)

        self.assertEqual(result["interim_1h_status"], "created")
        self.assertEqual(result["interim_4h_status"], "cached_valid")

    def test_different_existing_interim_file_is_preserved_and_fails(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            one_hour, four_hour = self.interim_paths(root)
            one_hour.parent.mkdir(parents=True, exist_ok=True)
            one_hour.write_bytes(b"manipulated\n")
            before = one_hour.read_bytes()

            with self.assertRaises(IntegrityError):
                self.run_month(root)

            self.assertEqual(one_hour.read_bytes(), before)
            self.assertFalse(four_hour.exists())

    def test_coinmetrics_interim_context_is_reused(self) -> None:
        context = pd.DataFrame(
            {
                "asset": ["btc"],
                "source_timestamp_utc": pd.to_datetime(
                    ["2020-12-30T00:00:00Z"], utc=True
                ),
                "PriceUSD": [1.0],
            }
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = write_coinmetrics_interim_context(
                context, fresh_config(), root
            )
            path = root / first["path"]
            before = path.read_bytes()
            second = write_coinmetrics_interim_context(
                context, fresh_config(), root
            )
            after = path.read_bytes()

        self.assertEqual(first["status"], "created")
        self.assertEqual(second["status"], "cached_valid")
        self.assertEqual(after, before)


class SourceContinuityResumeTests(unittest.TestCase):
    def test_anomalous_month_creates_no_interim_or_synthetic_rows(
        self,
    ) -> None:
        task = sample_task(month="2021-02")
        frame = february_2021_continuity_frame()
        with tempfile.TemporaryDirectory() as directory, mock.patch(
            "src.full_import.parse_binance_archive",
            return_value=frame,
        ):
            root = Path(directory)
            result = process_binance_task(task, fresh_config(), root)

            self.assertFalse((root / "data" / "interim").exists())

        self.assertEqual(len(frame), 671)
        self.assertEqual(result["derived_4h_rows"], 0)
        self.assertEqual(
            result["interim_1h_status"],
            "skipped_source_continuity_anomaly",
        )
        self.assertEqual(
            result["interim_4h_status"],
            "skipped_source_continuity_anomaly",
        )

    def test_cached_anomalous_february_continues_with_march_offline(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            tasks = [
                sample_task(month="2021-01"),
                sample_task(month="2021-02"),
                sample_task(month="2021-03"),
            ]
            for task, frame in (
                (tasks[0], complete_month_frame(month="2021-01")),
                (tasks[1], february_2021_continuity_frame()),
                (tasks[2], complete_month_frame(month="2021-03")),
            ):
                archive, _ = write_binance_archive(
                    root, task=task, normalized=frame
                )
                checksum = root / task.checksum_path
                checksum.write_text(
                    f"{sha256_bytes(archive.read_bytes())}  "
                    f"{archive.name}\n",
                    encoding="utf-8",
                )
            session = mock.Mock()
            session.get.side_effect = AssertionError(
                "Cache-Neustart darf kein Netzwerk verwenden."
            )
            report_root = root / "reports" / "full_import"

            first = run_binance_stage(
                tasks=tasks,
                config=fresh_config(),
                project_root=root,
                session=session,
                report_root=report_root,
                timeout_seconds=1,
            )
            protected_paths = sorted(
                [
                    *(
                        root / "data" / "raw" / "full_import"
                    ).rglob("*"),
                    *(
                        root / "data" / "interim" / "full_import"
                    ).rglob("*"),
                ]
            )
            before = {
                path: (
                    sha256_bytes(path.read_bytes()),
                    path.stat().st_mtime_ns,
                )
                for path in protected_paths
                if path.is_file()
            }
            second = run_binance_stage(
                tasks=tasks,
                config=fresh_config(),
                project_root=root,
                session=session,
                report_root=report_root,
                timeout_seconds=1,
            )
            after = {
                path: (
                    sha256_bytes(path.read_bytes()),
                    path.stat().st_mtime_ns,
                )
                for path in protected_paths
                if path.is_file()
            }
            checkpoint = json.loads(
                (
                    report_root / "execution_checkpoint.json"
                ).read_text(encoding="utf-8")
            )
            anomaly_rows = pd.read_csv(
                report_root / "source_anomalies.csv"
            )
            february_interim = (
                root
                / "data"
                / "interim"
                / "full_import"
                / "binance"
                / "BTCUSDT"
                / "1h"
                / "BTCUSDT-1h-2021-02.csv"
            )
            february_interim_exists = february_interim.exists()

        session.get.assert_not_called()
        self.assertEqual(before, after)
        self.assertEqual(len(first["quality"]), 3)
        self.assertEqual(len(second["quality"]), 3)
        self.assertEqual(
            second["last_safe_completed_task"], "BTCUSDT 2021-03"
        )
        self.assertEqual(
            second["quality"][0]["interim_1h_status"], "cached_valid"
        )
        self.assertEqual(
            second["quality"][1]["processing_status"],
            "source_continuity_anomaly",
        )
        self.assertTrue(
            second["quality"][1]["provider_checksum_match"]
        )
        self.assertFalse(second["quality"][1]["quality_pass"])
        self.assertEqual(second["quality"][2]["processing_status"], "valid")
        self.assertEqual(
            second["quality"][2]["interim_1h_status"], "cached_valid"
        )
        self.assertEqual(len(second["anomalies"]), 4)
        self.assertEqual(len(anomaly_rows), 4)
        self.assertEqual(checkpoint["status"], "IN_PROGRESS")
        self.assertEqual(checkpoint["gate_1"], "NOT_EVALUATED")
        self.assertEqual(checkpoint["continuity_anomaly_months"], 1)
        self.assertEqual(checkpoint["interim_skipped"], 2)
        self.assertFalse(february_interim_exists)

    def test_hard_failure_writes_checkpoint_and_stops(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            report_root = root / "reports" / "full_import"
            with mock.patch(
                "src.full_import.ensure_binance_task",
                side_effect=IntegrityError("checksum mismatch"),
            ):
                with self.assertRaises(IntegrityError):
                    run_binance_stage(
                        tasks=[sample_task()],
                        config=fresh_config(),
                        project_root=root,
                        session=mock.Mock(),
                        report_root=report_root,
                        timeout_seconds=1,
                    )
            checkpoint = json.loads(
                (
                    report_root / "execution_checkpoint.json"
                ).read_text(encoding="utf-8")
            )
            quality_header = (
                report_root / "binance_quality_summary.csv"
            ).read_text(encoding="utf-8")

        self.assertEqual(checkpoint["status"], "HARD_FAILURE")
        self.assertEqual(checkpoint["gate_1"], "NOT_EVALUATED")
        self.assertEqual(checkpoint["checked_months"], 0)
        self.assertIn("IntegrityError: checksum mismatch", checkpoint["error"])
        self.assertTrue(quality_header.startswith("symbol,month,"))

    def test_value_error_is_reported_as_quarantine_before_hard_stop(
        self,
    ) -> None:
        invalid = complete_month_frame()
        invalid.loc[10, "taker_buy_quote_volume"] = float("inf")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            report_root = root / "reports" / "full_import"
            with mock.patch(
                "src.full_import.ensure_binance_task", return_value=[]
            ), mock.patch(
                "src.full_import.parse_binance_archive",
                return_value=invalid,
            ):
                with self.assertRaises(IntegrityError):
                    run_binance_stage(
                        tasks=[sample_task()],
                        config=fresh_config(),
                        project_root=root,
                        session=mock.Mock(),
                        report_root=report_root,
                        timeout_seconds=1,
                    )
            checkpoint = json.loads(
                (
                    report_root / "execution_checkpoint.json"
                ).read_text(encoding="utf-8")
            )
            quality = pd.read_csv(
                report_root / "binance_quality_summary.csv"
            )
            interim_exists = (root / "data" / "interim").exists()

        self.assertEqual(checkpoint["status"], "HARD_FAILURE")
        self.assertEqual(checkpoint["checked_months"], 1)
        self.assertEqual(checkpoint["interim_quarantined"], 2)
        self.assertEqual(
            quality.loc[0, "processing_status"], "quality_quarantine"
        )
        self.assertEqual(quality.loc[0, "non_finite_value_count"], 1)
        self.assertFalse(interim_exists)

    def test_final_checkpoint_reports_completed_with_source_anomalies(
        self,
    ) -> None:
        state = {
            "manifest": [],
            "quality": [
                {
                    "symbol": "BTCUSDT",
                    "month": "2021-02",
                    "rows": 671,
                    "expected_rows": 672,
                    "expected_4h_rows": 168,
                    "derived_4h_rows": 0,
                    "missing_open_times_utc": (
                        '["2021-02-11T04:00:00+00:00"]'
                    ),
                    "unexpected_open_times_utc": "[]",
                    "spacing_anomalies": (
                        '[{"previous_open_utc":'
                        '"2021-02-11T03:00:00+00:00",'
                        '"current_open_utc":'
                        '"2021-02-11T05:00:00+00:00",'
                        '"gap_seconds":7200.0}]'
                    ),
                    "close_time_anomalies": (
                        '[{"open_utc":"2021-02-11T03:00:00+00:00",'
                        '"actual_close_utc":'
                        '"2021-02-11T03:40:54.773000+00:00",'
                        '"expected_close_utc":'
                        '"2021-02-11T03:59:59.999000+00:00"}]'
                    ),
                    "processing_status": "source_continuity_anomaly",
                    "interim_1h_status": (
                        "skipped_source_continuity_anomaly"
                    ),
                    "interim_4h_status": (
                        "skipped_source_continuity_anomaly"
                    ),
                    "provider_checksum_match": True,
                }
            ],
            "anomalies": [
                {
                    "source": "Binance Public Data",
                    "symbol": "BTCUSDT",
                    "month": "2021-02",
                    "anomaly_type": "missing_open_time",
                    "expected_value": "2021-02-11T04:00:00+00:00",
                    "actual_value": "",
                    "details": "Keine synthetische Kerze erzeugt.",
                    "source_integrity_pass": True,
                    "continuity_pass": False,
                    "quality_pass": False,
                    "processing_status": "source_continuity_anomaly",
                }
            ],
            "last_safe_completed_task": "BTCUSDT 2021-02",
        }
        with tempfile.TemporaryDirectory() as directory, mock.patch(
            "src.full_import.build_binance_tasks", return_value=[]
        ), mock.patch(
            "src.full_import.build_session", return_value=mock.Mock()
        ), mock.patch(
            "src.full_import.run_binance_stage", return_value=state
        ), mock.patch(
            "src.full_import.download_coinmetrics_pages",
            return_value=([], []),
        ), mock.patch(
            "src.full_import.normalize_coinmetrics_records",
            return_value=(
                pd.DataFrame({"asset": ["btc"]}),
                {"quality_pass": True},
            ),
        ), mock.patch(
            "src.full_import.write_coinmetrics_interim_context",
            return_value={"status": "cached_valid"},
        ):
            root = Path(directory)
            result = execute_full_import(
                fresh_config(),
                root,
                confirmation=EXECUTE_CONFIRMATION,
            )
            checkpoint = json.loads(
                (
                    root
                    / "reports"
                    / "full_import"
                    / "execution_checkpoint.json"
                ).read_text(encoding="utf-8")
            )

        self.assertEqual(
            result["execution_status"],
            "COMPLETED_WITH_SOURCE_ANOMALIES",
        )
        self.assertEqual(
            checkpoint["status"], "COMPLETED_WITH_SOURCE_ANOMALIES"
        )
        self.assertEqual(checkpoint["gate_1"], "NOT_EVALUATED")


class HardenedDryRunTests(unittest.TestCase):
    @staticmethod
    def plan(root: Path) -> tuple[
        dict[str, object], list[BinanceTask], list[dict[str, object]]
    ]:
        config = fresh_config()
        return (
            config,
            build_binance_tasks(config, root),
            build_download_plan(config, root),
        )

    def test_direct_4h_plan_row_is_detected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config, tasks, plan_rows = self.plan(root)
            plan_rows[0] = {
                **plan_rows[0],
                "interval": "4h",
                "url": str(plan_rows[0]["url"]).replace("/1h/", "/4h/"),
                "local_path": str(plan_rows[0]["local_path"]).replace(
                    "/1h/", "/4h/"
                ),
            }

            summary = build_dry_run_summary(
                config, tasks, plan_rows, root
            )

        self.assertEqual(summary["counts"]["direct_4h_downloads"], 1)
        self.assertFalse(summary["checks"]["no_direct_4h_downloads"])
        self.assertFalse(summary["central_checks_passed"])

    def test_traversal_and_absolute_plan_paths_are_detected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for unsafe in (
                "../escape.zip",
                str(root.parent / "absolute.zip"),
            ):
                with self.subTest(unsafe=unsafe):
                    config, tasks, plan_rows = self.plan(root)
                    plan_rows[0] = {
                        **plan_rows[0],
                        "local_path": unsafe,
                    }

                    summary = build_dry_run_summary(
                        config, tasks, plan_rows, root
                    )

                    self.assertGreater(
                        summary["counts"]["unsafe_path_count"], 0
                    )
                    self.assertFalse(
                        summary["checks"]["safe_project_paths"]
                    )

    def test_duplicate_tasks_are_detected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config, tasks, plan_rows = self.plan(root)
            tasks.append(tasks[0])

            summary = build_dry_run_summary(
                config, tasks, plan_rows, root
            )

        self.assertEqual(summary["counts"]["duplicate_task_count"], 1)
        self.assertFalse(summary["checks"]["no_duplicate_tasks"])
        self.assertFalse(summary["central_checks_passed"])

    def test_duplicate_urls_and_target_paths_are_detected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config, tasks, plan_rows = self.plan(root)
            plan_rows[1] = {
                **plan_rows[1],
                "url": plan_rows[0]["url"],
                "local_path": plan_rows[0]["local_path"],
            }

            summary = build_dry_run_summary(
                config, tasks, plan_rows, root
            )

        self.assertEqual(summary["counts"]["duplicate_url_count"], 1)
        self.assertEqual(
            summary["counts"]["duplicate_target_path_count"], 1
        )
        self.assertFalse(summary["checks"]["no_duplicate_urls"])
        self.assertFalse(summary["checks"]["no_duplicate_paths"])

    def test_failed_central_check_aborts_before_reports_are_written(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config, tasks, plan_rows = self.plan(root)
            plan_rows[0] = {**plan_rows[0], "interval": "4h"}

            with self.assertRaises(IntegrityError):
                write_dry_run_artifacts(
                    config,
                    root,
                    tasks=tasks,
                    plan_rows=plan_rows,
                )

            self.assertFalse((root / "reports").exists())

    def test_direct_execute_call_without_confirmation_is_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as directory, mock.patch(
            "src.full_import.build_session"
        ) as session_builder:
            with self.assertRaises(SafetyError):
                execute_full_import({}, Path(directory))

            session_builder.assert_not_called()
            self.assertEqual(list(Path(directory).rglob("*")), [])

    def test_direct_execute_call_with_wrong_confirmation_is_blocked(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory, mock.patch(
            "src.full_import.build_session"
        ) as session_builder:
            with self.assertRaises(SafetyError):
                execute_full_import(
                    {},
                    Path(directory),
                    confirmation="WRONG_SCOPE",
                )

            session_builder.assert_not_called()
            self.assertEqual(list(Path(directory).rglob("*")), [])


class DeterminismTests(unittest.TestCase):
    @staticmethod
    def artifact_hashes(root: Path) -> dict[str, str]:
        return {
            path.relative_to(root).as_posix(): hashlib.sha256(
                path.read_bytes()
            ).hexdigest()
            for path in sorted((root / "reports").rglob("*"))
            if path.is_file()
        }

    def test_dry_run_is_byte_identical_when_repeated(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = fresh_config()
            first = write_dry_run_artifacts(config, root)
            first_hashes = self.artifact_hashes(root)
            second = write_dry_run_artifacts(config, root)
            second_hashes = self.artifact_hashes(root)

        self.assertEqual(first, second)
        self.assertEqual(first_hashes, second_hashes)

    def test_dry_run_summary_has_exact_counts_and_gate_not_evaluated(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = fresh_config()
            tasks = build_binance_tasks(config, root)
            plan_rows = build_download_plan(config, root)
            summary = build_dry_run_summary(
                config, tasks, plan_rows, root
            )

        self.assertEqual(summary["counts"]["binance_archive_tasks"], 180)
        self.assertEqual(summary["counts"]["binance_http_objects"], 360)
        self.assertEqual(summary["expected_rows"]["binance_1h_total"], 131472)
        self.assertEqual(summary["expected_rows"]["derived_4h_total"], 32868)
        self.assertEqual(summary["expected_rows"]["coinmetrics_daily"], 1828)
        self.assertEqual(summary["gate_1"], "NOT_EVALUATED")
        self.assertFalse(summary["network_used"])
        self.assertEqual(summary["counts"]["direct_4h_downloads"], 0)
        self.assertTrue(summary["checks"]["no_direct_4h_downloads"])
        self.assertTrue(summary["checks"]["safe_project_paths"])
        self.assertTrue(summary["central_checks_passed"])


class SecondHardeningRegressionTests(unittest.TestCase):
    @staticmethod
    def seed_cached_task(
        root: Path, task: BinanceTask, frame: pd.DataFrame
    ) -> None:
        archive, _ = write_binance_archive(
            root, task=task, normalized=frame
        )
        checksum = root / task.checksum_path
        checksum.write_text(
            f"{sha256_bytes(archive.read_bytes())}  {archive.name}\n",
            encoding="utf-8",
        )

    @staticmethod
    def file_fingerprints(root: Path) -> dict[str, tuple[str, int]]:
        return {
            path.relative_to(root).as_posix(): (
                sha256_bytes(path.read_bytes()),
                path.stat().st_mtime_ns,
            )
            for path in sorted(root.rglob("*"))
            if path.is_file()
        }

    @staticmethod
    def read_checkpoint(report_root: Path) -> dict[str, object]:
        return json.loads(
            (report_root / "execution_checkpoint.json").read_text(
                encoding="utf-8"
            )
        )

    @staticmethod
    def january_february_state() -> dict[str, object]:
        january = validate_binance_month(
            complete_month_frame(month="2021-01"),
            sample_task(month="2021-01"),
        )
        january.update(
            {
                "derived_4h_rows": 186,
                "interim_1h_file": "jan-1h.csv",
                "interim_4h_file": "jan-4h.csv",
                "interim_1h_status": "created",
                "interim_4h_status": "created",
                "provider_checksum_match": True,
            }
        )
        february = validate_binance_month(
            february_2021_continuity_frame(),
            sample_task(month="2021-02"),
        )
        february.update(
            {
                "derived_4h_rows": 0,
                "interim_1h_file": "feb-1h.csv",
                "interim_4h_file": "feb-4h.csv",
                "interim_1h_status": "skipped_source_continuity_anomaly",
                "interim_4h_status": "skipped_source_continuity_anomaly",
                "provider_checksum_match": True,
            }
        )
        return {
            "manifest": [],
            "quality": [january, february],
            "anomalies": build_source_anomaly_rows(february),
            "partial_interim": [],
            "coinmetrics_pages": [],
            "coinmetrics_quality": None,
            "anomaly_provenance": {"mode": "synthetic_test"},
            "last_safe_completed_task": "BTCUSDT 2021-02",
        }

    def test_dry_run_changes_only_two_planning_reports_and_preserves_five_sentinels(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config_path = write_temp_config(root)
            report_root = root / "reports" / "full_import"
            report_root.mkdir(parents=True)
            execution_names = [
                *EXECUTION_REPORT_FILES,
                "execution_checkpoint.json",
            ]
            before = {}
            for index, name in enumerate(execution_names):
                content = f"sentinel-{index}\n".encode("utf-8")
                (report_root / name).write_bytes(content)
                before[name] = content
            with mock.patch(
                "src.full_import.build_session",
                side_effect=AssertionError("Dry-Run darf kein Netzwerk bauen."),
            ), mock.patch(
                "src.full_import.write_report_atomic",
                wraps=full_import_module.write_report_atomic,
            ) as atomic_write:
                exit_code = main(
                    ["--config", str(config_path), "--dry-run"],
                    project_root=root,
                )
                atomically_written = [
                    call.args[0].name for call in atomic_write.call_args_list
                ]
            after = {
                name: (report_root / name).read_bytes()
                for name in execution_names
            }
            report_names = {
                path.name for path in report_root.iterdir() if path.is_file()
            }
            data_exists = (root / "data").exists()

        self.assertEqual(exit_code, 0)
        self.assertEqual(before, after)
        self.assertEqual(
            atomically_written,
            ["download_plan.csv", "dry_run_summary.json"],
        )
        self.assertEqual(
            report_names,
            {
                *execution_names,
                "download_plan.csv",
                "dry_run_summary.json",
            },
        )
        self.assertFalse(data_exists)

    def test_early_restart_failure_preserves_all_existing_evidence(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            task = sample_task()
            self.seed_cached_task(root, task, complete_month_frame())
            report_root = root / "reports" / "full_import"
            run_binance_stage(
                tasks=[task],
                config=fresh_config(),
                project_root=root,
                session=mock.Mock(),
                report_root=report_root,
                timeout_seconds=1,
            )
            before_checkpoint = self.read_checkpoint(report_root)
            before_files = self.file_fingerprints(root / "data")
            with mock.patch(
                "src.full_import.ensure_binance_task",
                side_effect=IntegrityError("early restart failure"),
            ):
                with self.assertRaises(IntegrityError):
                    run_binance_stage(
                        tasks=[task],
                        config=fresh_config(),
                        project_root=root,
                        session=mock.Mock(),
                        report_root=report_root,
                        timeout_seconds=1,
                    )
            after_checkpoint = self.read_checkpoint(report_root)
            after_files = self.file_fingerprints(root / "data")

        self.assertEqual(before_files, after_files)
        self.assertEqual(
            before_checkpoint["evidence"]["raw_manifest"],
            after_checkpoint["evidence"]["raw_manifest"],
        )
        self.assertEqual(
            before_checkpoint["evidence"]["binance_monthly_quality"],
            after_checkpoint["evidence"]["binance_monthly_quality"],
        )
        self.assertEqual(after_checkpoint["execution_status"], "HARD_FAILURE")
        self.assertEqual(
            after_checkpoint["last_error"]["affected_task"],
            "binance BTCUSDT 2021-01",
        )

    def test_projection_failure_is_recovered_from_authoritative_checkpoint(
        self,
    ) -> None:
        for target in EXECUTION_REPORT_FILES:
            with self.subTest(target=target), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                report_root = root / "reports" / "full_import"
                state = load_or_initialize_authoritative_state(
                    config=fresh_config(), report_root=report_root
                )
                original_write = full_import_module.write_report_atomic

                def fail_projection(path: Path, content: bytes) -> None:
                    if path.name == target:
                        raise OSError(f"fault before {target}")
                    original_write(path, content)

                with mock.patch(
                    "src.full_import.write_report_atomic",
                    side_effect=fail_projection,
                ):
                    with self.assertRaises(OSError):
                        persist_authoritative_state(
                            config=fresh_config(),
                            state=state,
                            report_root=report_root,
                            status="IN_PROGRESS",
                        )
                checkpoint = self.read_checkpoint(report_root)
                generation = checkpoint["generation_id"]
                evidence_before = copy.deepcopy(checkpoint["evidence"])
                existing_before = {
                    name: sha256_bytes((report_root / name).read_bytes())
                    for name in EXECUTION_REPORT_FILES
                    if (report_root / name).is_file()
                }
                self.assertFalse((report_root / target).exists())
                recovered = load_or_initialize_authoritative_state(
                    config=fresh_config(), report_root=report_root
                )
                checkpoint_after = self.read_checkpoint(report_root)
                actual_hashes = {
                    name: sha256_bytes((report_root / name).read_bytes())
                    for name in EXECUTION_REPORT_FILES
                }

                self.assertEqual(recovered["_generation_id"], generation)
                self.assertEqual(checkpoint_after["generation_id"], generation)
                self.assertEqual(checkpoint_after["evidence"], evidence_before)
                for name, digest in existing_before.items():
                    self.assertEqual(actual_hashes[name], digest)
                self.assertEqual(
                    actual_hashes,
                    checkpoint["report_generation"]["projection_hashes"],
                )
                self.assertEqual(
                    len(
                        {
                            tuple(sorted(row.items()))
                            for row in recovered["anomalies"]
                        }
                    ),
                    len(recovered["anomalies"]),
                )
                self.assertFalse((root / "data").exists())

    def test_checkpoint_generation_mismatch_fails_before_projection_mutation(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            report_root = root / "reports" / "full_import"
            state = load_or_initialize_authoritative_state(
                config=fresh_config(), report_root=report_root
            )
            persist_authoritative_state(
                config=fresh_config(),
                state=state,
                report_root=report_root,
                status="IN_PROGRESS",
            )
            checkpoint_path = report_root / "execution_checkpoint.json"
            checkpoint = self.read_checkpoint(report_root)
            checkpoint["report_generation"]["generation_id"] += 1
            checkpoint_path.write_text(
                json.dumps(checkpoint), encoding="utf-8"
            )
            before = {
                name: (report_root / name).read_bytes()
                for name in EXECUTION_REPORT_FILES
            }
            with self.assertRaises(IntegrityError):
                load_or_initialize_authoritative_state(
                    config=fresh_config(), report_root=report_root
                )
            after = {
                name: (report_root / name).read_bytes()
                for name in EXECUTION_REPORT_FILES
            }

        self.assertEqual(before, after)

    def test_checkpoint_schema_scope_and_config_are_validated_fail_closed(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            report_root = root / "reports" / "full_import"
            config = fresh_config()
            state = load_or_initialize_authoritative_state(
                config=config, report_root=report_root
            )
            persist_authoritative_state(
                config=config,
                state=state,
                report_root=report_root,
                status="IN_PROGRESS",
            )
            before = {
                name: (report_root / name).read_bytes()
                for name in EXECUTION_REPORT_FILES
            }
            changed_config = fresh_config()
            changed_config["expected"]["coinmetrics_daily_rows"] += 1
            with self.assertRaises(IntegrityError):
                load_or_initialize_authoritative_state(
                    config=changed_config, report_root=report_root
                )
            changed_scope = fresh_config()
            changed_scope["scope_id"] = "DIFFERENT_SCOPE"
            with self.assertRaises(IntegrityError):
                load_or_initialize_authoritative_state(
                    config=changed_scope, report_root=report_root
                )
            checkpoint_path = report_root / "execution_checkpoint.json"
            checkpoint = self.read_checkpoint(report_root)
            checkpoint["checkpoint_schema_version"] = 999
            checkpoint_path.write_text(
                json.dumps(checkpoint), encoding="utf-8"
            )
            with self.assertRaises(IntegrityError):
                load_or_initialize_authoritative_state(
                    config=fresh_config(), report_root=report_root
                )
            after = {
                name: (report_root / name).read_bytes()
                for name in EXECUTION_REPORT_FILES
            }

        self.assertEqual(before, after)

    def test_coinmetrics_page_two_failure_preserves_page_one_checkpoint(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            report_root = root / "reports" / "full_import"
            state = load_or_initialize_authoritative_state(
                config=fresh_config(), report_root=report_root
            )
            endpoint = fresh_config()["coinmetrics"]["endpoint"]
            full_paging_url = (
                f"{endpoint}?next_page_token=secret_cursor"
                "&sensitive_query=secret_query_value"
            )
            page_one = {
                "data": [{"asset": "btc", "time": "2021-01-01"}],
                "next_page_url": full_paging_url,
            }
            first_response = mock.Mock(
                status_code=200,
                content=json.dumps(page_one).encode("utf-8"),
                text="",
            )
            second_response = mock.Mock(
                status_code=503,
                content=b"",
                text=(
                    "temporary error api_key=secret_api_key "
                    "access_token=secret_access_token"
                ),
            )
            session = mock.Mock()
            session.get.side_effect = [first_response, second_response]
            with mock.patch(
                "src.full_import.build_binance_tasks", return_value=[]
            ), mock.patch(
                "src.full_import.build_session", return_value=session
            ), mock.patch(
                "src.full_import.run_binance_stage", return_value=state
            ):
                with self.assertRaises(full_import_module.FullImportError):
                    execute_full_import(
                        fresh_config(),
                        root,
                        confirmation=EXECUTE_CONFIRMATION,
                    )
            checkpoint_text = (
                report_root / "execution_checkpoint.json"
            ).read_text(encoding="utf-8")
            checkpoint = json.loads(checkpoint_text)
            page_path = (
                root
                / "data"
                / "raw"
                / "full_import"
                / "coinmetrics"
                / "pages"
                / "page_00001.json"
            )
            page_exists = page_path.exists()
            report_texts = {
                "execution_checkpoint.json": checkpoint_text,
                **{
                    name: (report_root / name).read_text(
                        encoding="utf-8"
                    )
                    for name in EXECUTION_REPORT_FILES
                },
            }

        self.assertTrue(page_exists)
        self.assertEqual(len(checkpoint["evidence"]["coinmetrics_pages"]), 1)
        self.assertEqual(
            checkpoint["evidence"]["coinmetrics_pages"][0]["row_count"], 1
        )
        self.assertEqual(
            checkpoint["last_safe_completed_task"],
            "coinmetrics page 00001",
        )
        self.assertEqual(
            checkpoint["last_error"]["affected_task"],
            "coinmetrics page 00002",
        )
        self.assertEqual(
            checkpoint["last_error"]["phase"], "coinmetrics_page_fetch"
        )
        self.assertEqual(
            checkpoint["coinmetrics_progress"],
            {
                "phase": "coinmetrics_page_fetch",
                "pages_attempted": 2,
                "pages_completed": 1,
            },
        )
        self.assertEqual(session.get.call_count, 2)
        self.assertEqual(
            {
                row["url"]
                for row in checkpoint["evidence"]["raw_manifest"]
            },
            {endpoint},
        )
        forbidden_values = (
            full_paging_url,
            "secret_cursor",
            "secret_query_value",
            "secret_api_key",
            "secret_access_token",
            "next_page_token",
            "sensitive_query",
            "api_key",
            "access_token",
        )
        for name, report_text in report_texts.items():
            for forbidden in forbidden_values:
                with self.subTest(report=name, forbidden=forbidden):
                    self.assertNotIn(forbidden, report_text)

    def test_coinmetrics_aggregate_failure_is_not_mislabeled_as_page_two(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            report_root = root / "reports" / "full_import"
            state = load_or_initialize_authoritative_state(
                config=fresh_config(), report_root=report_root
            )
            response = mock.Mock(
                status_code=200,
                content=json.dumps(
                    {
                        "data": [
                            {
                                "asset": "btc",
                                "time": "2021-01-01",
                                "PriceUSD": "1",
                                "CapMrktCurUSD": "2",
                                "TxCnt": "3",
                                "AdrActCnt": "4",
                            }
                        ],
                        "next_page_url": None,
                    }
                ).encode("utf-8"),
            )
            session = mock.Mock()
            session.get.return_value = response
            with mock.patch(
                "src.full_import.build_binance_tasks", return_value=[]
            ), mock.patch(
                "src.full_import.build_session", return_value=session
            ), mock.patch(
                "src.full_import.run_binance_stage", return_value=state
            ):
                with self.assertRaises(IntegrityError):
                    execute_full_import(
                        fresh_config(),
                        root,
                        confirmation=EXECUTE_CONFIRMATION,
                    )
            checkpoint = self.read_checkpoint(report_root)

        self.assertEqual(session.get.call_count, 1)
        self.assertEqual(
            checkpoint["last_safe_completed_task"],
            "coinmetrics page 00001",
        )
        self.assertEqual(
            checkpoint["last_error"]["affected_task"],
            "coinmetrics aggregate_quality",
        )
        self.assertEqual(
            checkpoint["failed_task"], "coinmetrics aggregate_quality"
        )
        self.assertEqual(
            checkpoint["last_error"]["phase"],
            "coinmetrics_aggregate_quality",
        )
        self.assertEqual(
            checkpoint["coinmetrics_progress"]["pages_attempted"], 1
        )
        self.assertEqual(
            checkpoint["coinmetrics_progress"]["pages_completed"], 1
        )
        self.assertEqual(
            len(checkpoint["evidence"]["coinmetrics_pages"]), 1
        )
        self.assertEqual(
            len(checkpoint["evidence"]["raw_manifest"]), 1
        )
        self.assertNotIn("page 00002", json.dumps(checkpoint))

    def test_coinmetrics_interim_write_failure_has_its_own_phase(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            report_root = root / "reports" / "full_import"
            state = load_or_initialize_authoritative_state(
                config=fresh_config(), report_root=report_root
            )
            response = mock.Mock(
                status_code=200,
                content=json.dumps(
                    {
                        "data": [{"asset": "btc", "time": "2021-01-01"}],
                        "next_page_url": None,
                    }
                ).encode("utf-8"),
            )
            session = mock.Mock()
            session.get.return_value = response
            with mock.patch(
                "src.full_import.build_binance_tasks", return_value=[]
            ), mock.patch(
                "src.full_import.build_session", return_value=session
            ), mock.patch(
                "src.full_import.run_binance_stage", return_value=state
            ), mock.patch(
                "src.full_import.normalize_coinmetrics_records",
                return_value=(
                    pd.DataFrame({"asset": ["btc"]}),
                    {"quality_pass": True},
                ),
            ), mock.patch(
                "src.full_import.write_coinmetrics_interim_context",
                side_effect=OSError("injected final interim write failure"),
            ):
                with self.assertRaises(OSError):
                    execute_full_import(
                        fresh_config(),
                        root,
                        confirmation=EXECUTE_CONFIRMATION,
                    )
            checkpoint = self.read_checkpoint(report_root)

        self.assertEqual(session.get.call_count, 1)
        self.assertEqual(
            checkpoint["last_safe_completed_task"],
            "coinmetrics page 00001",
        )
        self.assertEqual(
            checkpoint["last_error"]["affected_task"],
            "coinmetrics interim_write",
        )
        self.assertEqual(
            checkpoint["failed_task"], "coinmetrics interim_write"
        )
        self.assertEqual(
            checkpoint["last_error"]["phase"],
            "coinmetrics_interim_write",
        )
        self.assertEqual(
            checkpoint["coinmetrics_progress"]["pages_attempted"], 1
        )
        self.assertEqual(
            checkpoint["coinmetrics_progress"]["pages_completed"], 1
        )
        self.assertNotIn("page 00002", json.dumps(checkpoint))

    def test_stale_coinmetrics_projection_is_replaced_for_active_generation(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            report_root = root / "reports" / "full_import"
            state = load_or_initialize_authoritative_state(
                config=fresh_config(), report_root=report_root
            )
            checkpoint = persist_authoritative_state(
                config=fresh_config(),
                state=state,
                report_root=report_root,
                status="IN_PROGRESS",
            )
            coinmetrics_path = (
                report_root / "coinmetrics_quality_summary.json"
            )
            coinmetrics_path.write_text(
                '{"generation_id": 999, "quality": {"quality_pass": true}}',
                encoding="utf-8",
            )
            load_or_initialize_authoritative_state(
                config=fresh_config(), report_root=report_root
            )
            projection = json.loads(
                coinmetrics_path.read_text(encoding="utf-8")
            )

        self.assertEqual(
            projection["generation_id"], checkpoint["generation_id"]
        )
        self.assertEqual(
            projection["projection_status"],
            "not_available_for_generation",
        )
        self.assertIsNone(projection["quality"])

    def test_january_february_aggregate_counts_keep_scope_expectations(
        self,
    ) -> None:
        counts = aggregate_execution_counts(
            fresh_config(), self.january_february_state()
        )

        expected_global = {
            "scope_expected_1h_rows": 131472,
            "completed_months_expected_1h_rows": 1416,
            "observed_raw_1h_rows": 1415,
            "accepted_interim_1h_rows": 744,
            "skipped_anomalous_raw_1h_rows": 671,
            "raw_1h_row_delta": -1,
            "accepted_1h_row_delta": -672,
            "scope_expected_4h_rows": 32868,
            "completed_months_expected_4h_rows": 354,
            "accepted_interim_4h_rows": 186,
            "accepted_4h_row_delta": -168,
            "source_anomaly_rows": 4,
            "continuity_anomaly_months": 1,
            "continuity_anomaly_intervals": 1,
            "interim_created": 2,
            "interim_cached_valid": 0,
            "interim_skipped": 2,
            "interim_quarantined": 0,
        }
        expected_btc = {
            **expected_global,
            "scope_expected_1h_rows": 43824,
            "scope_expected_4h_rows": 10956,
        }
        expected_empty_asset = {
            key: 0 for key in expected_global
        }
        expected_empty_asset["scope_expected_1h_rows"] = 43824
        expected_empty_asset["scope_expected_4h_rows"] = 10956

        for field, value in expected_global.items():
            self.assertEqual(counts[field], value, field)
        self.assertEqual(counts["per_asset"]["BTCUSDT"], expected_btc)
        self.assertEqual(counts["per_asset"]["ETHUSDT"], expected_empty_asset)
        self.assertEqual(counts["per_asset"]["SOLUSDT"], expected_empty_asset)
        for field in expected_global:
            self.assertEqual(
                sum(
                    counts["per_asset"][asset][field]
                    for asset in ("BTCUSDT", "ETHUSDT", "SOLUSDT")
                ),
                counts[field],
                field,
            )

    def test_multi_asset_counts_have_nonzero_accepted_eth_and_sol_values(
        self,
    ) -> None:
        qualities = []
        statuses = ("created", "cached_valid", "created")
        for asset, status in zip(
            ("BTCUSDT", "ETHUSDT", "SOLUSDT"), statuses
        ):
            row = validate_binance_month(
                complete_month_frame(month="2021-01"),
                sample_task(month="2021-01", symbol=asset),
            )
            row.update(
                {
                    "derived_4h_rows": 186,
                    "interim_1h_status": status,
                    "interim_4h_status": status,
                }
            )
            qualities.append(row)
        state = {
            "quality": qualities,
            "partial_interim": [],
            "anomalies": [],
        }

        counts = aggregate_execution_counts(fresh_config(), state)

        additive = (
            "scope_expected_1h_rows",
            "completed_months_expected_1h_rows",
            "observed_raw_1h_rows",
            "accepted_interim_1h_rows",
            "skipped_anomalous_raw_1h_rows",
            "raw_1h_row_delta",
            "accepted_1h_row_delta",
            "scope_expected_4h_rows",
            "completed_months_expected_4h_rows",
            "accepted_interim_4h_rows",
            "accepted_4h_row_delta",
            "source_anomaly_rows",
            "continuity_anomaly_months",
            "continuity_anomaly_intervals",
            "interim_created",
            "interim_cached_valid",
            "interim_skipped",
            "interim_quarantined",
        )
        for asset in ("ETHUSDT", "SOLUSDT"):
            self.assertEqual(
                counts["per_asset"][asset]["accepted_interim_1h_rows"], 744
            )
            self.assertEqual(
                counts["per_asset"][asset]["accepted_interim_4h_rows"], 186
            )
        for field in additive:
            self.assertEqual(
                sum(counts["per_asset"][asset][field] for asset in REQUIRED_ASSETS),
                counts[field],
                field,
            )

    def test_four_february_findings_form_one_month_and_one_interval(
        self,
    ) -> None:
        counts = aggregate_execution_counts(
            fresh_config(), self.january_february_state()
        )

        self.assertEqual(counts["source_anomaly_rows"], 4)
        self.assertEqual(counts["continuity_anomaly_months"], 1)
        self.assertEqual(counts["continuity_anomaly_intervals"], 1)

    def test_clean_synthetic_execution_finishes_with_completed_status(
        self,
    ) -> None:
        state = {
            "manifest": [],
            "quality": [],
            "anomalies": [],
            "last_safe_completed_task": "",
        }
        with tempfile.TemporaryDirectory() as directory, mock.patch(
            "src.full_import.build_binance_tasks", return_value=[]
        ), mock.patch(
            "src.full_import.build_session", return_value=mock.Mock()
        ), mock.patch(
            "src.full_import.run_binance_stage", return_value=state
        ), mock.patch(
            "src.full_import.download_coinmetrics_pages",
            return_value=([], []),
        ), mock.patch(
            "src.full_import.normalize_coinmetrics_records",
            return_value=(
                pd.DataFrame({"asset": ["btc"]}),
                {"quality_pass": True},
            ),
        ), mock.patch(
            "src.full_import.write_coinmetrics_interim_context",
            return_value={
                "path": "data/interim/full_import/coinmetrics/context.csv",
                "status": "created",
            },
        ):
            root = Path(directory)
            result = execute_full_import(
                fresh_config(),
                root,
                confirmation=EXECUTE_CONFIRMATION,
            )
            checkpoint = self.read_checkpoint(
                root / "reports" / "full_import"
            )

        self.assertEqual(result["execution_status"], "COMPLETED")
        self.assertEqual(checkpoint["execution_status"], "COMPLETED")
        self.assertEqual(checkpoint["gate_1"], "NOT_EVALUATED")

    def test_timestamp_unit_classification_is_source_integrity_failure(
        self,
    ) -> None:
        frame = complete_month_frame()
        frame["timestamp_unit"] = "ns"
        quality = validate_binance_month(frame, sample_task())

        self.assertFalse(quality["source_integrity_pass"])
        self.assertFalse(quality["quality_pass"])
        self.assertEqual(
            quality["processing_status"], "source_integrity_failure"
        )
        self.assertNotIn(
            quality["processing_status"],
            {"source_continuity_anomaly", "quality_quarantine"},
        )

    def test_parser_classifies_mixed_units_as_source_integrity_failure(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive_path, task = write_binance_archive(root)
            with zipfile.ZipFile(archive_path) as archive:
                member = archive.namelist()[0]
                with archive.open(member) as handle:
                    raw = pd.read_csv(handle, header=None)
            raw.loc[0, 0] *= 1000
            raw.loc[0, 6] *= 1000
            with zipfile.ZipFile(
                archive_path, mode="w", compression=zipfile.ZIP_DEFLATED
            ) as archive:
                archive.writestr(
                    member,
                    raw.to_csv(
                        index=False, header=False, lineterminator="\n"
                    ).encode("utf-8"),
                )

            frame = parse_binance_archive(archive_path, task)
            quality = validate_binance_month(frame, task)

        self.assertFalse(quality["source_integrity_pass"])
        self.assertFalse(quality["quality_pass"])
        self.assertEqual(
            quality["processing_status"], "source_integrity_failure"
        )
        self.assertEqual(build_source_anomaly_rows(quality), [])

    def test_missing_0400_candle_never_enters_an_accepted_or_derived_set(
        self,
    ) -> None:
        frame = february_2021_continuity_frame()
        missing = pd.Timestamp("2021-02-11T04:00:00Z")
        with tempfile.TemporaryDirectory() as directory, mock.patch(
            "src.full_import.parse_binance_archive", return_value=frame
        ), mock.patch(
            "src.full_import.aggregate_complete_1h_to_4h"
        ) as aggregate, mock.patch(
            "src.full_import.write_generated_file_cached"
        ) as writer:
            root = Path(directory)
            result = process_binance_task(
                sample_task(month="2021-02"), fresh_config(), root
            )

        self.assertNotIn(missing, set(frame["timestamp_utc"]))
        self.assertEqual(result["derived_4h_rows"], 0)
        self.assertEqual(
            result["interim_1h_status"],
            "skipped_source_continuity_anomaly",
        )
        aggregate.assert_not_called()
        writer.assert_not_called()
        self.assertFalse((root / "data" / "interim").exists())

    def test_partial_1h_write_is_checkpointed_and_resumed_without_overwrite(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            task = sample_task()
            self.seed_cached_task(root, task, complete_month_frame())
            report_root = root / "reports" / "full_import"
            raw_before = self.file_fingerprints(
                root / "data" / "raw" / "full_import"
            )
            original_write = full_import_module.write_generated_file_cached

            def fail_four_hour(
                path: Path,
                content: bytes,
                *,
                error_path: str | None = None,
            ) -> str:
                if path.parent.name == "4h":
                    raise OSError("injected 4h write failure")
                return original_write(
                    path,
                    content,
                    error_path=error_path,
                )

            with mock.patch(
                "src.full_import.write_generated_file_cached",
                side_effect=fail_four_hour,
            ):
                with self.assertRaises(PartialInterimError):
                    run_binance_stage(
                        tasks=[task],
                        config=fresh_config(),
                        project_root=root,
                        session=mock.Mock(),
                        report_root=report_root,
                        timeout_seconds=1,
                    )
            checkpoint = self.read_checkpoint(report_root)
            one_hour = (
                root
                / "data"
                / "interim"
                / "full_import"
                / "binance"
                / "BTCUSDT"
                / "1h"
                / "BTCUSDT-1h-2021-01.csv"
            )
            one_hour_before = (
                sha256_bytes(one_hour.read_bytes()),
                one_hour.stat().st_mtime_ns,
            )
            resumed = run_binance_stage(
                tasks=[task],
                config=fresh_config(),
                project_root=root,
                session=mock.Mock(),
                report_root=report_root,
                timeout_seconds=1,
            )
            one_hour_after = (
                sha256_bytes(one_hour.read_bytes()),
                one_hour.stat().st_mtime_ns,
            )
            raw_after = self.file_fingerprints(
                root / "data" / "raw" / "full_import"
            )

        self.assertEqual(checkpoint["execution_status"], "HARD_FAILURE")
        self.assertEqual(
            checkpoint["evidence"]["partial_interim_outputs"][0][
                "interim_1h_status"
            ],
            "created",
        )
        self.assertEqual(
            checkpoint["evidence"]["partial_interim_outputs"][0][
                "interim_4h_status"
            ],
            "write_failed",
        )
        self.assertEqual(
            checkpoint["aggregate_counts"][
                "completed_months_expected_1h_rows"
            ],
            0,
        )
        self.assertEqual(
            checkpoint["aggregate_counts"]["observed_raw_1h_rows"], 744
        )
        self.assertEqual(
            checkpoint["aggregate_counts"]["accepted_interim_1h_rows"],
            744,
        )
        self.assertEqual(one_hour_before, one_hour_after)
        self.assertEqual(raw_before, raw_after)
        self.assertEqual(
            resumed["quality"][0]["interim_1h_status"], "cached_valid"
        )
        self.assertEqual(
            resumed["quality"][0]["interim_4h_status"], "created"
        )
        self.assertEqual(resumed["partial_interim"], [])

    def test_preexisting_source_anomalies_are_adopted_and_not_duplicated(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            task = sample_task(month="2021-02")
            frame = february_2021_continuity_frame()
            quality = validate_binance_month(frame, task)
            anomalies = build_source_anomaly_rows(quality)
            report_root = root / "reports" / "full_import"
            report_root.mkdir(parents=True)
            (report_root / "source_anomalies.csv").write_bytes(
                render_dict_rows_csv(anomalies, SOURCE_ANOMALY_FIELDS)
            )
            self.seed_cached_task(root, task, frame)
            first = run_binance_stage(
                tasks=[task],
                config=fresh_config(),
                project_root=root,
                session=mock.Mock(),
                report_root=report_root,
                timeout_seconds=1,
            )
            second = run_binance_stage(
                tasks=[task],
                config=fresh_config(),
                project_root=root,
                session=mock.Mock(),
                report_root=report_root,
                timeout_seconds=1,
            )
            checkpoint = self.read_checkpoint(report_root)
            with (report_root / "source_anomalies.csv").open(
                "r", encoding="utf-8", newline=""
            ) as handle:
                stored = list(csv.DictReader(handle))

        self.assertEqual(len(first["anomalies"]), 4)
        self.assertEqual(len(second["anomalies"]), 4)
        self.assertEqual(len(stored), 4)
        self.assertEqual(
            checkpoint["evidence"]["source_anomaly_provenance"]["mode"],
            "validated_preexisting_csv",
        )
        self.assertEqual(CHECKPOINT_SCHEMA_VERSION, 4)


class ThirdHardeningRegressionTests(unittest.TestCase):
    @staticmethod
    def rewrite_archive(
        archive_path: Path, transform: object
    ) -> None:
        with zipfile.ZipFile(archive_path) as archive:
            member = archive.namelist()[0]
            with archive.open(member) as handle:
                raw = pd.read_csv(handle, header=None)
        transformed = transform(raw.copy())
        with zipfile.ZipFile(
            archive_path, mode="w", compression=zipfile.ZIP_DEFLATED
        ) as archive:
            archive.writestr(
                member,
                transformed.to_csv(
                    index=False, header=False, lineterminator="\n"
                ).encode("utf-8"),
            )

    @classmethod
    def timestamp_quality(
        cls,
        *,
        month: str,
        unit: str,
        transform: object | None = None,
    ) -> tuple[pd.DataFrame, dict[str, object]]:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            task = sample_task(month=month)
            archive, _ = write_binance_archive(
                root, task=task, unit=unit
            )
            if transform is not None:
                cls.rewrite_archive(archive, transform)
            frame = parse_binance_archive(archive, task)
            quality = validate_binance_month(frame, task)
        return frame, quality

    def assert_unit_failure(self, quality: dict[str, object]) -> None:
        self.assertFalse(quality["source_integrity_pass"])
        self.assertFalse(quality["quality_pass"])
        self.assertEqual(
            quality["processing_status"], "source_integrity_failure"
        )
        self.assertNotIn(
            quality["processing_status"],
            {"source_continuity_anomaly", "quality_quarantine"},
        )
        self.assertEqual(build_source_anomaly_rows(quality), [])

    def test_timestamp_policy_boundary_is_month_driven(self) -> None:
        self.assertEqual(expected_binance_timestamp_unit("2024-12"), "ms")
        self.assertEqual(expected_binance_timestamp_unit("2025-01"), "us")
        self.assertEqual(
            TIMESTAMP_POLICY_ID,
            "binance_spot_ms_before_2025_us_from_2025",
        )

    def test_december_2024_milliseconds_are_valid(self) -> None:
        _, quality = self.timestamp_quality(month="2024-12", unit="ms")
        self.assertTrue(quality["quality_pass"])
        self.assertEqual(quality["expected_timestamp_unit"], "ms")
        self.assertEqual(quality["observed_open_timestamp_unit"], "ms")
        self.assertEqual(quality["observed_close_timestamp_unit"], "ms")
        self.assertEqual(
            quality["actual_month_end_utc"],
            "2024-12-31T23:59:59.999000+00:00",
        )

    def test_december_2024_microseconds_are_rejected(self) -> None:
        _, quality = self.timestamp_quality(month="2024-12", unit="us")
        self.assert_unit_failure(quality)
        self.assertEqual(quality["expected_timestamp_unit"], "ms")
        self.assertEqual(quality["observed_open_timestamp_unit"], "us")
        self.assertEqual(quality["observed_close_timestamp_unit"], "us")

    def test_january_2025_microseconds_are_valid(self) -> None:
        _, quality = self.timestamp_quality(month="2025-01", unit="us")
        self.assertTrue(quality["quality_pass"])
        self.assertEqual(quality["expected_timestamp_unit"], "us")
        self.assertEqual(quality["observed_open_timestamp_unit"], "us")
        self.assertEqual(quality["observed_close_timestamp_unit"], "us")
        self.assertEqual(
            quality["actual_month_end_utc"],
            "2025-01-31T23:59:59.999999+00:00",
        )

    def test_january_2025_milliseconds_are_rejected(self) -> None:
        _, quality = self.timestamp_quality(month="2025-01", unit="ms")
        self.assert_unit_failure(quality)
        self.assertEqual(quality["expected_timestamp_unit"], "us")
        self.assertEqual(quality["observed_open_timestamp_unit"], "ms")
        self.assertEqual(quality["observed_close_timestamp_unit"], "ms")

    def test_mixed_open_units_are_rejected(self) -> None:
        def mutate(raw: pd.DataFrame) -> pd.DataFrame:
            raw.loc[0, 0] *= 1000
            return raw

        _, quality = self.timestamp_quality(
            month="2024-12", unit="ms", transform=mutate
        )
        self.assert_unit_failure(quality)
        self.assertEqual(
            quality["observed_open_timestamp_unit"], "mixed:ms|us"
        )

    def test_mixed_close_units_are_rejected(self) -> None:
        def mutate(raw: pd.DataFrame) -> pd.DataFrame:
            raw.loc[0, 6] *= 1000
            return raw

        _, quality = self.timestamp_quality(
            month="2024-12", unit="ms", transform=mutate
        )
        self.assert_unit_failure(quality)
        self.assertEqual(
            quality["observed_close_timestamp_unit"], "mixed:ms|us"
        )

    def test_open_ms_and_close_us_are_rejected(self) -> None:
        def mutate(raw: pd.DataFrame) -> pd.DataFrame:
            raw.loc[:, 6] *= 1000
            return raw

        _, quality = self.timestamp_quality(
            month="2024-12", unit="ms", transform=mutate
        )
        self.assert_unit_failure(quality)
        self.assertEqual(quality["observed_open_timestamp_unit"], "ms")
        self.assertEqual(quality["observed_close_timestamp_unit"], "us")

    def test_nanosecond_timestamps_are_rejected(self) -> None:
        def mutate(raw: pd.DataFrame) -> pd.DataFrame:
            raw.loc[:, 0] *= 1_000_000
            raw.loc[:, 6] *= 1_000_000
            return raw

        _, quality = self.timestamp_quality(
            month="2024-12", unit="ms", transform=mutate
        )
        self.assert_unit_failure(quality)
        self.assertEqual(
            quality["observed_open_timestamp_unit"], "unsupported"
        )
        self.assertEqual(
            quality["observed_close_timestamp_unit"], "unsupported"
        )

    def test_wrong_microsecond_close_precision_is_detected(self) -> None:
        def mutate(raw: pd.DataFrame) -> pd.DataFrame:
            raw.loc[:, 6] = raw.loc[:, 0] + 3_600_000_000 - 1_000
            return raw

        _, quality = self.timestamp_quality(
            month="2025-01", unit="us", transform=mutate
        )
        self.assertTrue(quality["source_integrity_pass"])
        self.assertFalse(quality["continuity_pass"])
        self.assertEqual(quality["candle_close_time_errors"], 744)
        self.assertEqual(
            quality["processing_status"], "source_continuity_anomaly"
        )

    def test_utc_normalization_does_not_shift_an_hour(self) -> None:
        frame_2024, _ = self.timestamp_quality(
            month="2024-12", unit="ms"
        )
        frame_2025, _ = self.timestamp_quality(
            month="2025-01", unit="us"
        )
        self.assertEqual(
            frame_2024.loc[0, "timestamp_utc"],
            pd.Timestamp("2024-12-01T00:00:00Z"),
        )
        self.assertEqual(
            frame_2025.loc[0, "timestamp_utc"],
            pd.Timestamp("2025-01-01T00:00:00Z"),
        )

    def test_4h_aggregation_accepts_valid_2024_ms_data(self) -> None:
        frame, quality = self.timestamp_quality(
            month="2024-12", unit="ms"
        )
        aggregated = aggregate_complete_1h_to_4h(frame)
        self.assertTrue(quality["quality_pass"])
        self.assertEqual(len(aggregated), 186)

    def test_4h_aggregation_accepts_valid_2025_us_data(self) -> None:
        frame, quality = self.timestamp_quality(
            month="2025-01", unit="us"
        )
        aggregated = aggregate_complete_1h_to_4h(frame)
        self.assertTrue(quality["quality_pass"])
        self.assertEqual(len(aggregated), 186)

    def test_schema_three_checkpoint_is_rejected_under_schema_four(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            report_root = root / "reports" / "full_import"
            state = load_or_initialize_authoritative_state(
                config=fresh_config(), report_root=report_root
            )
            persist_authoritative_state(
                config=fresh_config(),
                state=state,
                report_root=report_root,
                status="IN_PROGRESS",
            )
            checkpoint_path = report_root / "execution_checkpoint.json"
            checkpoint = json.loads(
                checkpoint_path.read_text(encoding="utf-8")
            )
            checkpoint["checkpoint_schema_version"] = 3
            checkpoint_path.write_text(
                json.dumps(checkpoint), encoding="utf-8"
            )
            projections_before = {
                name: (report_root / name).read_bytes()
                for name in EXECUTION_REPORT_FILES
            }

            with self.assertRaises(IntegrityError):
                load_or_initialize_authoritative_state(
                    config=fresh_config(), report_root=report_root
                )
            projections_after = {
                name: (report_root / name).read_bytes()
                for name in EXECUTION_REPORT_FILES
            }

        self.assertEqual(CHECKPOINT_SCHEMA_VERSION, 4)
        self.assertEqual(projections_before, projections_after)

    def test_checkpoint_timestamp_policy_mismatch_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            report_root = root / "reports" / "full_import"
            state = load_or_initialize_authoritative_state(
                config=fresh_config(), report_root=report_root
            )
            persist_authoritative_state(
                config=fresh_config(),
                state=state,
                report_root=report_root,
                status="IN_PROGRESS",
            )
            checkpoint_path = report_root / "execution_checkpoint.json"
            checkpoint = json.loads(
                checkpoint_path.read_text(encoding="utf-8")
            )
            checkpoint["timestamp_policy_id"] = "obsolete-policy"
            checkpoint_path.write_text(
                json.dumps(checkpoint), encoding="utf-8"
            )
            projections_before = {
                name: (report_root / name).read_bytes()
                for name in EXECUTION_REPORT_FILES
            }

            with self.assertRaises(IntegrityError):
                load_or_initialize_authoritative_state(
                    config=fresh_config(), report_root=report_root
                )
            projections_after = {
                name: (report_root / name).read_bytes()
                for name in EXECUTION_REPORT_FILES
            }

        self.assertEqual(projections_before, projections_after)

    @staticmethod
    def file_fingerprints(root: Path) -> dict[str, tuple[str, int]]:
        if not root.exists():
            return {}
        return {
            path.relative_to(root).as_posix(): (
                sha256_bytes(path.read_bytes()),
                path.stat().st_mtime_ns,
            )
            for path in sorted(root.rglob("*"))
            if path.is_file()
        }

    @staticmethod
    def anomaly_fixture(
        root: Path,
        *,
        symbol: str = "BTCUSDT",
        month: str = "2021-02",
        frame: pd.DataFrame | None = None,
    ) -> dict[str, object]:
        task = sample_task(month=month, symbol=symbol)
        actual_frame = (
            frame.copy()
            if frame is not None
            else february_2021_continuity_frame()
        )
        archive, _ = write_binance_archive(
            root,
            task=task,
            unit=expected_binance_timestamp_unit(month),
            normalized=actual_frame,
        )
        checksum = root / task.checksum_path
        checksum.write_text(
            f"{sha256_bytes(archive.read_bytes())}  {archive.name}\n",
            encoding="utf-8",
        )
        quality = validate_binance_month(actual_frame, task)
        return {
            "root": root,
            "task": task,
            "archive": archive,
            "checksum": checksum,
            "rows": build_source_anomaly_rows(quality),
        }

    @staticmethod
    def write_anomaly_csv(context: dict[str, object]) -> Path:
        root = context["root"]
        report_root = root / "reports" / "full_import"
        report_root.mkdir(parents=True, exist_ok=True)
        path = report_root / "source_anomalies.csv"
        path.write_bytes(
            render_dict_rows_csv(
                context["rows"], SOURCE_ANOMALY_FIELDS
            )
        )
        return path

    def assert_anomaly_evidence_rejected(
        self, mutate: object
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            context = self.anomaly_fixture(root)
            mutate(context)
            source_path = self.write_anomaly_csv(context)
            source_before = source_path.read_bytes()
            data_before = self.file_fingerprints(root / "data")
            report_before = self.file_fingerprints(root / "reports")
            with mock.patch(
                "src.full_import.get_response_bytes",
                side_effect=AssertionError("Kein Netzwerk erlaubt."),
            ):
                with self.assertRaises(IntegrityError):
                    load_or_initialize_authoritative_state(
                        config=fresh_config(),
                        report_root=root / "reports" / "full_import",
                    )
            data_after = self.file_fingerprints(root / "data")
            report_after = self.file_fingerprints(root / "reports")
            source_after = source_path.read_bytes()
            checkpoint_exists = (
                root
                / "reports"
                / "full_import"
                / "execution_checkpoint.json"
            ).exists()
            interim_exists = (root / "data" / "interim").exists()

        self.assertEqual(data_before, data_after)
        self.assertEqual(report_before, report_after)
        self.assertEqual(source_before, source_after)
        self.assertFalse(checkpoint_exists)
        self.assertFalse(interim_exists)

    def test_rejects_manipulated_actual_value(self) -> None:
        def mutate(context: dict[str, object]) -> None:
            context["rows"][0]["actual_value"] = "999999"

        self.assert_anomaly_evidence_rejected(mutate)

    def test_rejects_manipulated_expected_value(self) -> None:
        def mutate(context: dict[str, object]) -> None:
            context["rows"][0]["expected_value"] = "999999"

        self.assert_anomaly_evidence_rejected(mutate)

    def test_rejects_additional_fifth_finding(self) -> None:
        def mutate(context: dict[str, object]) -> None:
            extra = dict(context["rows"][0])
            extra["details"] = "zusätzliche unbelegte Zeile"
            context["rows"].append(extra)

        self.assert_anomaly_evidence_rejected(mutate)

    def test_rejects_missing_finding(self) -> None:
        def mutate(context: dict[str, object]) -> None:
            context["rows"].pop()

        self.assert_anomaly_evidence_rejected(mutate)

    def test_rejects_duplicate_finding(self) -> None:
        def mutate(context: dict[str, object]) -> None:
            context["rows"].append(dict(context["rows"][0]))

        self.assert_anomaly_evidence_rejected(mutate)

    def test_rejects_unapproved_asset_as_one_invalid_group(self) -> None:
        def mutate(context: dict[str, object]) -> None:
            context["rows"][0]["symbol"] = "EVILUSDT"

        self.assert_anomaly_evidence_rejected(mutate)

    def test_rejects_syntactically_invalid_month(self) -> None:
        def mutate(context: dict[str, object]) -> None:
            context["rows"][0]["month"] = "2099-99"

        self.assert_anomaly_evidence_rejected(mutate)

    def test_rejects_valid_but_out_of_scope_month(self) -> None:
        def mutate(context: dict[str, object]) -> None:
            context["rows"][0]["month"] = "2020-12"

        self.assert_anomaly_evidence_rejected(mutate)

    def test_rejects_missing_raw_archive(self) -> None:
        def mutate(context: dict[str, object]) -> None:
            context["archive"].unlink()

        self.assert_anomaly_evidence_rejected(mutate)

    def test_rejects_missing_checksum_file(self) -> None:
        def mutate(context: dict[str, object]) -> None:
            context["checksum"].unlink()

        self.assert_anomaly_evidence_rejected(mutate)

    def test_rejects_wrong_checksum(self) -> None:
        def mutate(context: dict[str, object]) -> None:
            context["checksum"].write_text(
                f"{'0' * 64}  {context['archive'].name}\n",
                encoding="utf-8",
            )

        self.assert_anomaly_evidence_rejected(mutate)

    def test_rejects_wrong_archive_name_in_checksum(self) -> None:
        def mutate(context: dict[str, object]) -> None:
            digest = sha256_bytes(context["archive"].read_bytes())
            context["checksum"].write_text(
                f"{digest}  other.zip\n", encoding="utf-8"
            )

        self.assert_anomaly_evidence_rejected(mutate)

    def test_rejects_damaged_zip_even_with_matching_checksum(self) -> None:
        def mutate(context: dict[str, object]) -> None:
            context["archive"].write_bytes(b"not-a-zip")
            digest = sha256_bytes(context["archive"].read_bytes())
            context["checksum"].write_text(
                f"{digest}  {context['archive'].name}\n",
                encoding="utf-8",
            )

        self.assert_anomaly_evidence_rejected(mutate)

    def test_rejects_raw_result_different_from_csv_claim(self) -> None:
        def mutate(context: dict[str, object]) -> None:
            frame = complete_month_frame(month="2021-02")
            frame = frame.loc[
                frame["timestamp_utc"].ne(
                    pd.Timestamp("2021-02-11T05:00:00Z")
                )
            ].reset_index(drop=True)
            write_binance_archive(
                context["root"],
                task=context["task"],
                normalized=frame,
            )
            digest = sha256_bytes(context["archive"].read_bytes())
            context["checksum"].write_text(
                f"{digest}  {context['archive'].name}\n",
                encoding="utf-8",
            )

        self.assert_anomaly_evidence_rejected(mutate)

    def test_rejects_wrong_processing_status(self) -> None:
        def mutate(context: dict[str, object]) -> None:
            context["rows"][0]["processing_status"] = "valid"

        self.assert_anomaly_evidence_rejected(mutate)

    def test_rejects_wrong_quality_boolean(self) -> None:
        def mutate(context: dict[str, object]) -> None:
            context["rows"][0]["quality_pass"] = True

        self.assert_anomaly_evidence_rejected(mutate)

    def test_csv_traversal_text_never_drives_file_access(self) -> None:
        def mutate(context: dict[str, object]) -> None:
            context["rows"][0]["details"] = (
                "../../../../outside-sensitive-file.txt"
            )

        self.assert_anomaly_evidence_rejected(mutate)

    def test_shuffled_canonical_evidence_is_safely_normalized(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            context = self.anomaly_fixture(root)
            context["rows"] = list(reversed(context["rows"]))
            source_path = self.write_anomaly_csv(context)
            source_before = source_path.read_bytes()

            state = load_or_initialize_authoritative_state(
                config=fresh_config(),
                report_root=root / "reports" / "full_import",
            )
            source_after = source_path.read_bytes()

        self.assertEqual(len(state["anomalies"]), 4)
        self.assertEqual(
            [row["anomaly_type"] for row in state["anomalies"]],
            [
                "row_count_mismatch",
                "missing_open_time",
                "irregular_hour_spacing",
                "candle_close_time_mismatch",
            ],
        )
        self.assertEqual(source_before, source_after)
        self.assertEqual(
            state["anomaly_provenance"]["mode"],
            "validated_preexisting_csv",
        )

    def test_multiple_valid_asset_months_are_adopted_deterministically(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            btc = self.anomaly_fixture(root, symbol="BTCUSDT")
            eth = self.anomaly_fixture(root, symbol="ETHUSDT")
            context = {
                "root": root,
                "rows": [*eth["rows"], *btc["rows"]],
            }
            self.write_anomaly_csv(context)

            state = load_or_initialize_authoritative_state(
                config=fresh_config(),
                report_root=root / "reports" / "full_import",
            )

        self.assertEqual(len(state["anomalies"]), 8)
        self.assertEqual(
            [(row["symbol"], row["anomaly_type"]) for row in state["anomalies"]],
            sorted(
                [
                    (row["symbol"], row["anomaly_type"])
                    for row in state["anomalies"]
                ],
                key=lambda value: (
                    value[0],
                    {
                        "row_count_mismatch": 0,
                        "missing_open_time": 1,
                        "irregular_hour_spacing": 3,
                        "candle_close_time_mismatch": 4,
                    }[value[1]],
                ),
            ),
        )
        self.assertEqual(
            state["anomaly_provenance"]["verified_asset_months"], 2
        )


class FourthHardeningRegressionTests(unittest.TestCase):
    @staticmethod
    def anomaly_fixture(
        root: Path,
        *,
        symbol: str = "BTCUSDT",
    ) -> dict[str, object]:
        return ThirdHardeningRegressionTests.anomaly_fixture(
            root, symbol=symbol
        )

    @staticmethod
    def write_anomaly_csv(context: dict[str, object]) -> Path:
        return ThirdHardeningRegressionTests.write_anomaly_csv(context)

    @staticmethod
    def fingerprints(root: Path) -> dict[str, tuple[str, int]]:
        return ThirdHardeningRegressionTests.file_fingerprints(root)

    def assert_rejected_without_mutation(self, root: Path) -> None:
        data_before = self.fingerprints(root / "data")
        reports_before = self.fingerprints(root / "reports")
        with mock.patch(
            "src.full_import.get_response_bytes",
            side_effect=AssertionError("Kein Netzwerk erlaubt."),
        ):
            with self.assertRaises(IntegrityError):
                load_or_initialize_authoritative_state(
                    config=fresh_config(),
                    report_root=root / "reports" / "full_import",
                )
        data_after = self.fingerprints(root / "data")
        reports_after = self.fingerprints(root / "reports")
        self.assertEqual(data_before, data_after)
        self.assertEqual(reports_before, reports_after)
        self.assertFalse(
            (
                root
                / "reports"
                / "full_import"
                / "execution_checkpoint.json"
            ).exists()
        )
        self.assertFalse((root / "data" / "interim").exists())

    def test_header_only_csv_is_rejected_when_cached_raw_has_anomaly(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            context = self.anomaly_fixture(root)
            context["rows"] = []
            self.write_anomaly_csv(context)
            self.assert_rejected_without_mutation(root)

    def test_removing_complete_asset_month_group_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            btc = self.anomaly_fixture(root, symbol="BTCUSDT")
            self.anomaly_fixture(root, symbol="ETHUSDT")
            context = {"root": root, "rows": btc["rows"]}
            self.write_anomaly_csv(context)
            self.assert_rejected_without_mutation(root)

    def test_unnamed_extra_csv_value_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            context = self.anomaly_fixture(root)
            path = self.write_anomaly_csv(context)
            records = list(
                csv.reader(io.StringIO(path.read_text(encoding="utf-8")))
            )
            records[1].append("UNNAMED_EXTRA_VALUE")
            buffer = io.StringIO(newline="")
            writer = csv.writer(buffer, lineterminator="\n")
            writer.writerows(records)
            path.write_text(buffer.getvalue(), encoding="utf-8")
            self.assert_rejected_without_mutation(root)

    def test_too_few_csv_columns_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            context = self.anomaly_fixture(root)
            path = self.write_anomaly_csv(context)
            records = list(
                csv.reader(io.StringIO(path.read_text(encoding="utf-8")))
            )
            records[1].pop()
            buffer = io.StringIO(newline="")
            writer = csv.writer(buffer, lineterminator="\n")
            writer.writerows(records)
            path.write_text(buffer.getvalue(), encoding="utf-8")
            self.assert_rejected_without_mutation(root)

    def test_missing_csv_reconstructs_all_cached_raw_anomalies(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.anomaly_fixture(root)
            data_before = self.fingerprints(root / "data")
            report_root = root / "reports" / "full_import"

            state = load_or_initialize_authoritative_state(
                config=fresh_config(), report_root=report_root
            )

            data_after = self.fingerprints(root / "data")
            source_exists = (report_root / "source_anomalies.csv").exists()
            checkpoint_exists = (
                report_root / "execution_checkpoint.json"
            ).exists()

        self.assertEqual(data_before, data_after)
        self.assertEqual(len(state["anomalies"]), 4)
        self.assertEqual(
            state["anomaly_provenance"]["mode"],
            "recomputed_from_cached_raw",
        )
        self.assertNotEqual(
            state["anomaly_provenance"]["mode"],
            "validated_preexisting_csv",
        )
        self.assertEqual(
            state["anomaly_provenance"]["anomaly_evidence_policy_id"],
            ANOMALY_EVIDENCE_POLICY_ID,
        )
        self.assertFalse(source_exists)
        self.assertFalse(checkpoint_exists)

    def test_canonical_february_csv_is_read_only_accepted_in_temp_project(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            context = self.anomaly_fixture(root)
            source_path = self.write_anomaly_csv(context)
            before = {
                path: (sha256_bytes(path.read_bytes()), path.stat().st_mtime_ns)
                for path in (
                    source_path,
                    context["archive"],
                    context["checksum"],
                )
            }

            state = load_or_initialize_authoritative_state(
                config=fresh_config(),
                report_root=root / "reports" / "full_import",
            )

            after = {
                path: (sha256_bytes(path.read_bytes()), path.stat().st_mtime_ns)
                for path in before
            }
        self.assertEqual(before, after)
        self.assertEqual(len(state["anomalies"]), 4)
        self.assertEqual(
            state["anomaly_provenance"]["mode"],
            "validated_preexisting_csv",
        )
        self.assertEqual(
            state["anomaly_provenance"]["verified_cached_months"], 1
        )

    def test_anomaly_evidence_policy_conflict_stops_before_mutation(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            report_root = root / "reports" / "full_import"
            state = load_or_initialize_authoritative_state(
                config=fresh_config(), report_root=report_root
            )
            persist_authoritative_state(
                config=fresh_config(),
                state=state,
                report_root=report_root,
                status="IN_PROGRESS",
            )
            checkpoint_path = report_root / "execution_checkpoint.json"
            checkpoint = json.loads(
                checkpoint_path.read_text(encoding="utf-8")
            )
            checkpoint["anomaly_evidence_policy_id"] = "obsolete-policy"
            checkpoint_path.write_text(
                json.dumps(checkpoint), encoding="utf-8"
            )
            projections_before = {
                name: (report_root / name).read_bytes()
                for name in EXECUTION_REPORT_FILES
            }

            with self.assertRaises(IntegrityError):
                load_or_initialize_authoritative_state(
                    config=fresh_config(), report_root=report_root
                )
            projections_after = {
                name: (report_root / name).read_bytes()
                for name in EXECUTION_REPORT_FILES
            }

        self.assertEqual(projections_before, projections_after)
        self.assertEqual(CHECKPOINT_SCHEMA_VERSION, 4)


class FifthHardeningRegressionTests(unittest.TestCase):
    AUDIT_FIELDS = (
        "timestamp_policy_id",
        "expected_timestamp_unit",
        "observed_open_timestamp_unit",
        "observed_close_timestamp_unit",
        "timestamp_unit_errors",
    )

    @staticmethod
    def fingerprints(root: Path) -> dict[str, tuple[str, int]]:
        return ThirdHardeningRegressionTests.file_fingerprints(root)

    @staticmethod
    def write_valid_raw_pair(
        root: Path,
        *,
        month: str = "2021-01",
        unit: str = "ms",
    ) -> tuple[BinanceTask, Path, Path]:
        task = sample_task(month=month)
        archive, _ = write_binance_archive(
            root,
            task=task,
            unit=unit,
        )
        checksum = root / task.checksum_path
        checksum.write_text(
            f"{sha256_bytes(archive.read_bytes())}  {archive.name}\n",
            encoding="utf-8",
        )
        return task, archive, checksum

    @classmethod
    def legacy_fixture(
        cls,
        root: Path,
    ) -> dict[str, object]:
        config = fresh_config()
        report_root = root / "reports" / "full_import"
        january_task, january_archive, january_checksum = (
            cls.write_valid_raw_pair(root)
        )
        january_result = process_binance_task(
            january_task,
            config,
            root,
        )
        february_context = (
            ThirdHardeningRegressionTests.anomaly_fixture(root)
        )
        ThirdHardeningRegressionTests.write_anomaly_csv(
            february_context
        )
        state = load_or_initialize_authoritative_state(
            config=config,
            report_root=report_root,
            project_root=root,
        )
        session = mock.Mock()
        state["manifest"] = full_import_module.ensure_binance_task(
            january_task,
            root,
            session,
            timeout_seconds=1,
        )
        session.get.assert_not_called()
        persist_authoritative_state(
            config=config,
            state=state,
            report_root=report_root,
            status="IN_PROGRESS",
        )
        persist_authoritative_state(
            config=config,
            state=state,
            report_root=report_root,
            status="HARD_FAILURE",
            error=IntegrityError(
                "Vorhandene erzeugte Datei weicht vom deterministischen "
                "Inhalt ab und bleibt unveraendert: "
                f"{root / january_result['interim_1h_file']}"
            ),
            affected_task="binance BTCUSDT 2021-01",
        )
        checkpoint_path = report_root / "execution_checkpoint.json"
        checkpoint = json.loads(
            checkpoint_path.read_text(encoding="utf-8")
        )
        checkpoint["processing_policy_fingerprint"] = (
            LEGACY_PROCESSING_POLICY_FINGERPRINT
        )
        checkpoint.pop("binance_interim_1h_schema_id", None)
        checkpoint.pop("policy_migration", None)
        checkpoint_path.write_bytes(
            full_import_module.canonical_json(checkpoint).encode("utf-8")
        )
        return {
            "config": config,
            "report_root": report_root,
            "checkpoint_path": checkpoint_path,
            "january_task": january_task,
            "january_archive": january_archive,
            "january_checksum": january_checksum,
            "january_result": january_result,
            "february_context": february_context,
        }

    def test_parser_quality_checkpoint_and_interim_keep_separate_contracts(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = fresh_config()
            report_root = root / "reports" / "full_import"
            task, archive, _ = self.write_valid_raw_pair(root)
            internal = parse_binance_archive(archive, task)

            self.assertEqual(len(internal.columns), 20)
            self.assertTrue(
                set(self.AUDIT_FIELDS).issubset(internal.columns)
            )
            projected = project_binance_interim_1h(internal)
            self.assertEqual(
                tuple(projected.columns),
                BINANCE_INTERIM_1H_FIELDS,
            )
            self.assertTrue(
                set(self.AUDIT_FIELDS).isdisjoint(projected.columns)
            )
            duplicate = internal.copy()
            duplicate.insert(
                len(duplicate.columns),
                "open",
                duplicate["open"],
                allow_duplicates=True,
            )
            with self.assertRaises(IntegrityError):
                project_binance_interim_1h(duplicate)

            quality = process_binance_task(task, config, root)
            for field in self.AUDIT_FIELDS:
                self.assertIn(field, quality)
            state = load_or_initialize_authoritative_state(
                config=config,
                report_root=report_root,
                project_root=root,
            )
            state["quality"] = [quality]
            persist_authoritative_state(
                config=config,
                state=state,
                report_root=report_root,
                status="IN_PROGRESS",
            )
            checkpoint = json.loads(
                (report_root / "execution_checkpoint.json").read_text(
                    encoding="utf-8"
                )
            )
            stored_quality = checkpoint["evidence"][
                "binance_monthly_quality"
            ][0]

        for field in self.AUDIT_FIELDS:
            self.assertEqual(stored_quality[field], quality[field])
        self.assertEqual(
            checkpoint["binance_interim_1h_schema_id"],
            BINANCE_INTERIM_1H_SCHEMA_ID,
        )
        self.assertEqual(
            checkpoint["processing_policy_fingerprint"],
            processing_policy_fingerprint(),
        )

    def test_ms_and_us_months_share_exact_15_column_interim_contract(
        self,
    ) -> None:
        cases = (("2024-12", "ms"), ("2025-01", "us"))
        for month, unit in cases:
            with self.subTest(month=month, unit=unit):
                with tempfile.TemporaryDirectory() as directory:
                    root = Path(directory)
                    task, archive, _ = self.write_valid_raw_pair(
                        root,
                        month=month,
                        unit=unit,
                    )
                    internal = parse_binance_archive(archive, task)
                    result = process_binance_task(
                        task,
                        fresh_config(),
                        root,
                    )
                    interim = pd.read_csv(
                        root / result["interim_1h_file"]
                    )

                self.assertEqual(len(internal.columns), 20)
                self.assertEqual(
                    tuple(interim.columns),
                    BINANCE_INTERIM_1H_FIELDS,
                )
                self.assertEqual(
                    set(interim["timestamp_unit"].unique()),
                    {unit},
                )
                self.assertTrue(
                    set(self.AUDIT_FIELDS).isdisjoint(interim.columns)
                )
                self.assertEqual(result["interim_1h_status"], "created")
                self.assertEqual(result["interim_4h_status"], "created")

    def test_existing_20_column_interim_is_rejected_without_overwrite(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            task, archive, _ = self.write_valid_raw_pair(root)
            internal = parse_binance_archive(archive, task)
            one_hour_path = (
                root
                / "data"
                / "interim"
                / "full_import"
                / "binance"
                / task.symbol
                / "1h"
                / f"{task.symbol}-1h-{task.month}.csv"
            )
            one_hour_path.parent.mkdir(parents=True, exist_ok=True)
            one_hour_path.write_bytes(dataframe_csv_bytes(internal))
            before = (
                sha256_bytes(one_hour_path.read_bytes()),
                one_hour_path.stat().st_mtime_ns,
            )

            with self.assertRaises(IntegrityError) as caught:
                process_binance_task(task, fresh_config(), root)

            after = (
                sha256_bytes(one_hour_path.read_bytes()),
                one_hour_path.stat().st_mtime_ns,
            )
            four_hour_path = (
                root
                / "data"
                / "interim"
                / "full_import"
                / "binance"
                / task.symbol
                / "4h"
                / f"{task.symbol}-4h-{task.month}.csv"
            )
            four_hour_exists = four_hour_path.exists()

        self.assertEqual(before, after)
        self.assertFalse(four_hour_exists)
        self.assertIn(
            "data/interim/full_import/binance/BTCUSDT/1h/"
            "BTCUSDT-1h-2021-01.csv",
            str(caught.exception),
        )
        self.assertNotIn(str(root), str(caught.exception))

    def test_january_contract_reuses_1h_and_4h_without_mutation(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            task, _, _ = self.write_valid_raw_pair(root)
            first = process_binance_task(task, fresh_config(), root)
            paths = [
                root / first["interim_1h_file"],
                root / first["interim_4h_file"],
            ]
            before = {
                path: (
                    sha256_bytes(path.read_bytes()),
                    path.stat().st_mtime_ns,
                )
                for path in paths
            }

            second = process_binance_task(task, fresh_config(), root)
            after = {
                path: (
                    sha256_bytes(path.read_bytes()),
                    path.stat().st_mtime_ns,
                )
                for path in paths
            }

        self.assertEqual(first["interim_1h_status"], "created")
        self.assertEqual(first["interim_4h_status"], "created")
        self.assertEqual(second["interim_1h_status"], "cached_valid")
        self.assertEqual(second["interim_4h_status"], "cached_valid")
        self.assertEqual(before, after)

    def test_exact_legacy_checkpoint_loads_read_only_in_memory(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            context = self.legacy_fixture(root)
            checkpoint = json.loads(
                context["checkpoint_path"].read_text(encoding="utf-8")
            )
            portable_prefix = (
                Path(root.anchor) / "anderer-benutzer" / "projektkopie"
            )
            portable_target = (
                portable_prefix
                / context["january_result"]["interim_1h_file"]
            )
            portable_message = (
                "Vorhandene erzeugte Datei weicht vom deterministischen "
                "Inhalt ab und bleibt unveraendert: "
                f"{portable_target}"
            )
            checkpoint["last_error"]["message"] = portable_message
            checkpoint["error"] = f"IntegrityError: {portable_message}"
            context["checkpoint_path"].write_bytes(
                full_import_module.canonical_json(checkpoint).encode("utf-8")
            )
            before = self.fingerprints(root)

            state = load_or_initialize_authoritative_state(
                config=context["config"],
                report_root=context["report_root"],
                project_root=root,
            )

            after = self.fingerprints(root)

        self.assertEqual(before, after)
        self.assertTrue(state["_legacy_policy_migration_pending"])
        self.assertEqual(len(state["anomalies"]), 4)
        self.assertEqual(
            state["_policy_migration"][
                "source_processing_policy_fingerprint"
            ],
            LEGACY_PROCESSING_POLICY_FINGERPRINT,
        )
        self.assertEqual(
            state["_policy_migration"][
                "target_processing_policy_fingerprint"
            ],
            processing_policy_fingerprint(),
        )
        self.assertEqual(
            state["_policy_migration"]["source_generation_id"],
            2,
        )

    def test_first_persistence_after_legacy_uses_new_policy_and_schema(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            context = self.legacy_fixture(root)
            state = load_or_initialize_authoritative_state(
                config=context["config"],
                report_root=context["report_root"],
                project_root=root,
            )

            persist_authoritative_state(
                config=context["config"],
                state=state,
                report_root=context["report_root"],
                status="IN_PROGRESS",
            )
            checkpoint = json.loads(
                context["checkpoint_path"].read_text(encoding="utf-8")
            )
            projection_hashes = checkpoint["report_generation"][
                "projection_hashes"
            ]
            actual_hashes = {
                name: sha256_bytes(
                    (context["report_root"] / name).read_bytes()
                )
                for name in EXECUTION_REPORT_FILES
            }
            reloaded = load_or_initialize_authoritative_state(
                config=context["config"],
                report_root=context["report_root"],
                project_root=root,
            )

        self.assertEqual(checkpoint["generation_id"], 3)
        self.assertEqual(
            checkpoint["processing_policy_fingerprint"],
            processing_policy_fingerprint(),
        )
        self.assertEqual(
            checkpoint["binance_interim_1h_schema_id"],
            BINANCE_INTERIM_1H_SCHEMA_ID,
        )
        self.assertEqual(
            checkpoint["policy_migration"],
            state["_policy_migration"],
        )
        self.assertEqual(projection_hashes, actual_hashes)
        self.assertEqual(len(checkpoint["evidence"]["source_anomalies"]), 4)
        self.assertEqual(len(reloaded["anomalies"]), 4)
        self.assertFalse(reloaded["_legacy_policy_migration_pending"])

    def test_persisted_policy_migration_rejects_extra_or_future_data(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            context = self.legacy_fixture(root)
            state = load_or_initialize_authoritative_state(
                config=context["config"],
                report_root=context["report_root"],
                project_root=root,
            )
            persist_authoritative_state(
                config=context["config"],
                state=state,
                report_root=context["report_root"],
                status="IN_PROGRESS",
            )
            original_checkpoint = json.loads(
                context["checkpoint_path"].read_text(encoding="utf-8")
            )
            mutations = (
                "extra_field",
                "wrong_prior_generation",
                "future_generation",
            )
            for mutation in mutations:
                with self.subTest(mutation=mutation):
                    checkpoint = copy.deepcopy(original_checkpoint)
                    if mutation == "extra_field":
                        checkpoint["policy_migration"][
                            "next_page_url"
                        ] = "https://example.invalid/?cursor=secret"
                    elif mutation == "wrong_prior_generation":
                        checkpoint["policy_migration"][
                            "source_generation_id"
                        ] = 1
                    else:
                        checkpoint["policy_migration"][
                            "source_generation_id"
                        ] = checkpoint["generation_id"]
                    context["checkpoint_path"].write_bytes(
                        full_import_module.canonical_json(
                            checkpoint
                        ).encode("utf-8")
                    )
                    before = self.fingerprints(root)

                    with self.assertRaises(IntegrityError):
                        load_or_initialize_authoritative_state(
                            config=context["config"],
                            report_root=context["report_root"],
                            project_root=root,
                        )

                    after = self.fingerprints(root)
                    self.assertEqual(before, after)

    def test_legacy_contract_conflicts_stop_without_mutation(
        self,
    ) -> None:
        cases = (
            "unknown_fingerprint",
            "wrong_status",
            "damaged_projection",
            "wrong_error_message",
            "modified_manifest_row",
        )
        for case in cases:
            with self.subTest(case=case):
                with tempfile.TemporaryDirectory() as directory:
                    root = Path(directory)
                    context = self.legacy_fixture(root)
                    checkpoint_path = context["checkpoint_path"]
                    if case == "damaged_projection":
                        projection = (
                            context["report_root"] / "raw_manifest.csv"
                        )
                        projection.write_bytes(
                            projection.read_bytes() + b"tampered"
                        )
                    else:
                        checkpoint = json.loads(
                            checkpoint_path.read_text(encoding="utf-8")
                        )
                        if case == "unknown_fingerprint":
                            checkpoint[
                                "processing_policy_fingerprint"
                            ] = "f" * 64
                        elif case == "wrong_status":
                            checkpoint["status"] = "IN_PROGRESS"
                            checkpoint["execution_status"] = "IN_PROGRESS"
                        elif case == "wrong_error_message":
                            checkpoint["last_error"][
                                "message"
                            ] = "unbelegter oder sensibler Text"
                            checkpoint["error"] = (
                                "IntegrityError: "
                                "unbelegter oder sensibler Text"
                            )
                        else:
                            checkpoint["evidence"]["raw_manifest"][0][
                                "row_count"
                            ] = "744"
                            manifest_bytes = render_dict_rows_csv(
                                checkpoint["evidence"]["raw_manifest"],
                                full_import_module.MANIFEST_FIELDS,
                            )
                            (
                                context["report_root"] / "raw_manifest.csv"
                            ).write_bytes(manifest_bytes)
                            checkpoint["report_generation"][
                                "projection_hashes"
                            ]["raw_manifest.csv"] = sha256_bytes(
                                manifest_bytes
                            )
                        checkpoint_path.write_bytes(
                            full_import_module.canonical_json(
                                checkpoint
                            ).encode("utf-8")
                        )
                    before = self.fingerprints(root)

                    with self.assertRaises(IntegrityError):
                        load_or_initialize_authoritative_state(
                            config=context["config"],
                            report_root=context["report_root"],
                            project_root=root,
                        )

                    after = self.fingerprints(root)

                self.assertEqual(before, after)

    def test_persisted_conflict_path_is_relative_and_public_urls_remain(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = fresh_config()
            task, archive, _ = self.write_valid_raw_pair(root)
            internal = parse_binance_archive(archive, task)
            one_hour_path = (
                root
                / "data"
                / "interim"
                / "full_import"
                / "binance"
                / task.symbol
                / "1h"
                / f"{task.symbol}-1h-{task.month}.csv"
            )
            one_hour_path.parent.mkdir(parents=True, exist_ok=True)
            one_hour_path.write_bytes(dataframe_csv_bytes(internal))
            before = (
                sha256_bytes(one_hour_path.read_bytes()),
                one_hour_path.stat().st_mtime_ns,
            )
            report_root = root / "reports" / "full_import"
            session = mock.Mock()

            with self.assertRaises(IntegrityError):
                run_binance_stage(
                    tasks=[task],
                    config=config,
                    project_root=root,
                    session=session,
                    report_root=report_root,
                    timeout_seconds=1,
                )

            checkpoint_text = (
                report_root / "execution_checkpoint.json"
            ).read_text(encoding="utf-8")
            checkpoint = json.loads(checkpoint_text)
            message = checkpoint["last_error"]["message"]
            urls = {
                row["url"]
                for row in checkpoint["evidence"]["raw_manifest"]
            }
            after = (
                sha256_bytes(one_hour_path.read_bytes()),
                one_hour_path.stat().st_mtime_ns,
            )

        session.get.assert_not_called()
        self.assertEqual(before, after)
        self.assertIn(
            "data/interim/full_import/binance/BTCUSDT/1h/"
            "BTCUSDT-1h-2021-01.csv",
            message,
        )
        self.assertNotIn(str(root), message)
        self.assertNotIn(root.as_posix(), message)
        if root.drive:
            self.assertNotIn(root.drive, message)
        self.assertEqual(urls, {task.archive_url, task.checksum_url})
        self.assertTrue(all("?" not in url for url in urls))
        self.assertNotIn("next_page_token", checkpoint_text)
        self.assertNotIn("secret_cursor", checkpoint_text)

    def test_atomic_create_failure_persists_only_relative_path(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = fresh_config()
            task, _, _ = self.write_valid_raw_pair(root)
            report_root = root / "reports" / "full_import"
            session = mock.Mock()
            original_promote = (
                full_import_module.atomic_promote_no_overwrite
            )

            def fail_only_for_interim(
                temp_path: Path,
                destination: Path,
                *,
                error_path: str | None = None,
            ) -> None:
                if "data/interim/full_import" in destination.as_posix():
                    raise OSError(
                        f"simulierter lokaler Fehler: {destination}"
                    )
                original_promote(
                    temp_path,
                    destination,
                    error_path=error_path,
                )

            with mock.patch(
                "src.full_import.atomic_promote_no_overwrite",
                side_effect=fail_only_for_interim,
            ):
                with self.assertRaises(SafetyError):
                    run_binance_stage(
                        tasks=[task],
                        config=config,
                        project_root=root,
                        session=session,
                        report_root=report_root,
                        timeout_seconds=1,
                    )

            checkpoint_text = (
                report_root / "execution_checkpoint.json"
            ).read_text(encoding="utf-8")
            checkpoint = json.loads(checkpoint_text)
            message = checkpoint["last_error"]["message"]
            part_files = list(root.rglob("*.part"))

        session.get.assert_not_called()
        self.assertEqual(
            checkpoint["last_error"]["type"],
            "SafetyError",
        )
        self.assertIn(
            "data/interim/full_import/binance/BTCUSDT/1h/"
            "BTCUSDT-1h-2021-01.csv",
            message,
        )
        self.assertNotIn(str(root), message)
        self.assertNotIn(root.as_posix(), message)
        if root.drive:
            self.assertNotIn(root.drive, message)
        self.assertEqual(part_files, [])


if __name__ == "__main__":
    unittest.main()
