"""Automatisierte Tests fuer zentrale Berechnungen des Datenpiloten."""

from __future__ import annotations

import tempfile
import unittest
import zipfile
from pathlib import Path

import pandas as pd

from src.data_pilot import (
    aggregate_1h_to_4h,
    assess_binance_file,
    assess_coinmetrics_context,
    build_gate_decision,
    compare_timeframes,
    infer_unix_unit,
    join_context_without_lookahead,
    parse_binance_archive,
)


class TimestampParsingTests(unittest.TestCase):
    def test_infers_milliseconds_and_microseconds(self) -> None:
        milliseconds = pd.Series([1704067200000, 1704070800000])
        microseconds = pd.Series([1735689600000000, 1735693200000000])

        self.assertEqual(infer_unix_unit(milliseconds), "ms")
        self.assertEqual(infer_unix_unit(microseconds), "us")

    def test_archive_parser_normalizes_both_timestamp_units_to_utc(self) -> None:
        project_root = Path(__file__).resolve().parents[1]
        temporary_parent = project_root / "data" / "interim"
        temporary_parent.mkdir(parents=True, exist_ok=True)

        samples = [
            ("2024-01", "ms", 1704067200000, 1704070799999),
            ("2025-01", "us", 1735689600000000, 1735693199999999),
        ]
        with tempfile.TemporaryDirectory(dir=temporary_parent) as directory:
            temp_path = Path(directory)
            for month, expected_unit, open_time, close_time in samples:
                archive_path = temp_path / f"BTCUSDT-1h-{month}.zip"
                row = [
                    open_time,
                    100.0,
                    110.0,
                    90.0,
                    105.0,
                    10.0,
                    close_time,
                    1000.0,
                    20,
                    5.0,
                    500.0,
                    0,
                ]
                csv_text = ",".join(str(value) for value in row) + "\n"
                with zipfile.ZipFile(
                    archive_path, mode="w", compression=zipfile.ZIP_DEFLATED
                ) as archive:
                    archive.writestr(
                        f"BTCUSDT-1h-{month}.csv", csv_text.encode("utf-8")
                    )

                parsed = parse_binance_archive(
                    archive_path, "BTCUSDT", "1h", month
                )

                self.assertEqual(parsed.loc[0, "timestamp_unit"], expected_unit)
                self.assertEqual(str(parsed.loc[0, "timestamp_utc"].tz), "UTC")
                self.assertEqual(
                    parsed.loc[0, "timestamp_utc"].hour,
                    0,
                )


class QualityRuleTests(unittest.TestCase):
    @staticmethod
    def complete_january_frame() -> pd.DataFrame:
        timestamps = pd.date_range(
            "2024-01-01", "2024-02-01", freq="1h", inclusive="left", tz="UTC"
        )
        return pd.DataFrame(
            {
                "symbol": "BTCUSDT",
                "timeframe": "1h",
                "timestamp_utc": timestamps,
                "open_time_utc": timestamps,
                "close_time_utc": timestamps
                + pd.Timedelta(hours=1)
                - pd.Timedelta(milliseconds=1),
                "open": 100.0,
                "high": 110.0,
                "low": 90.0,
                "close": 105.0,
                "volume": 10.0,
                "quote_asset_volume": 1000.0,
                "number_of_trades": 20,
                "taker_buy_base_volume": 5.0,
                "taker_buy_quote_volume": 500.0,
                "pilot_month": "2024-01",
                "source": "binance_public_data",
                "timestamp_unit": "ms",
            }
        )

    def test_complete_month_passes_quality_rules(self) -> None:
        result = assess_binance_file(
            self.complete_january_frame(),
            symbol="BTCUSDT",
            timeframe="1h",
            month="2024-01",
            interval_seconds=3600,
            source_file="synthetic.zip",
        )

        self.assertTrue(result["quality_pass"])
        self.assertEqual(result["rows"], 744)
        self.assertEqual(result["missing_intervals"], 0)

    def test_impossible_high_is_detected(self) -> None:
        frame = self.complete_january_frame()
        frame.loc[10, "high"] = 80.0

        result = assess_binance_file(
            frame,
            symbol="BTCUSDT",
            timeframe="1h",
            month="2024-01",
            interval_seconds=3600,
            source_file="synthetic.zip",
        )

        self.assertFalse(result["quality_pass"])
        self.assertEqual(result["ohlc_bound_violations"], 1)

    def test_complete_but_shifted_month_fails_exact_boundary_checks(self) -> None:
        frame = self.complete_january_frame()
        shift = pd.Timedelta(hours=1)
        frame["timestamp_utc"] = frame["timestamp_utc"] + shift
        frame["open_time_utc"] = frame["open_time_utc"] + shift
        frame["close_time_utc"] = frame["close_time_utc"] + shift

        result = assess_binance_file(
            frame,
            symbol="BTCUSDT",
            timeframe="1h",
            month="2024-01",
            interval_seconds=3600,
            source_file="synthetic_shifted.zip",
        )

        self.assertEqual(result["rows"], result["expected_rows"])
        self.assertEqual(result["unexpected_spacing_events"], 0)
        self.assertEqual(result["timestamp_alignment_errors"], 0)
        self.assertEqual(result["month_start_mismatch"], 1)
        self.assertEqual(result["last_candle_open_mismatch"], 1)
        self.assertEqual(result["month_end_mismatch"], 1)
        self.assertEqual(result["timestamps_outside_month"], 1)
        self.assertFalse(result["quality_pass"])


class TimeframeAggregationTests(unittest.TestCase):
    def test_four_hour_ohlcv_uses_first_max_min_last_and_sums(self) -> None:
        timestamps = pd.date_range(
            "2024-01-01", periods=4, freq="1h", tz="UTC"
        )
        one_hour = pd.DataFrame(
            {
                "timestamp_utc": timestamps,
                "open": [100.0, 101.0, 99.0, 105.0],
                "high": [103.0, 104.0, 106.0, 108.0],
                "low": [98.0, 97.0, 96.0, 100.0],
                "close": [101.0, 99.0, 105.0, 107.0],
                "volume": [1.0, 2.0, 3.0, 4.0],
                "quote_asset_volume": [100.0, 200.0, 300.0, 400.0],
                "number_of_trades": [10, 20, 30, 40],
                "close_time_utc": timestamps
                + pd.Timedelta(hours=1)
                - pd.Timedelta(milliseconds=1),
            }
        )

        result = aggregate_1h_to_4h(one_hour)

        self.assertEqual(len(result), 1)
        self.assertEqual(result.loc[0, "open"], 100.0)
        self.assertEqual(result.loc[0, "high"], 108.0)
        self.assertEqual(result.loc[0, "low"], 96.0)
        self.assertEqual(result.loc[0, "close"], 107.0)
        self.assertEqual(result.loc[0, "volume"], 10.0)
        self.assertEqual(result.loc[0, "number_of_trades"], 100)
        self.assertEqual(result.loc[0, "constituent_rows"], 4)

    def test_direct_and_aggregated_four_hour_candles_are_compared(self) -> None:
        timestamps = pd.date_range(
            "2024-01-01", periods=4, freq="1h", tz="UTC"
        )
        one_hour = pd.DataFrame(
            {
                "symbol": "BTCUSDT",
                "timeframe": "1h",
                "pilot_month": "2024-01",
                "timestamp_utc": timestamps,
                "open": [100.0, 101.0, 102.0, 103.0],
                "high": [102.0, 103.0, 104.0, 105.0],
                "low": [99.0, 100.0, 101.0, 102.0],
                "close": [101.0, 102.0, 103.0, 104.0],
                "volume": [1.0, 2.0, 3.0, 4.0],
                "quote_asset_volume": [100.0, 200.0, 300.0, 400.0],
                "number_of_trades": [10, 20, 30, 40],
                "close_time_utc": timestamps
                + pd.Timedelta(hours=1)
                - pd.Timedelta(milliseconds=1),
            }
        )
        direct = pd.DataFrame(
            {
                "symbol": ["BTCUSDT"],
                "timeframe": ["4h"],
                "pilot_month": ["2024-01"],
                "timestamp_utc": [timestamps[0]],
                "open": [100.0],
                "high": [105.0],
                "low": [99.0],
                "close": [104.0],
                "volume": [10.0],
                "quote_asset_volume": [1000.0],
                "number_of_trades": [100],
                "close_time_utc": [
                    timestamps[0]
                    + pd.Timedelta(hours=4)
                    - pd.Timedelta(milliseconds=1)
                ],
            }
        )

        matching = compare_timeframes([one_hour, direct])
        self.assertTrue(matching.loc[0, "timeframe_consistency_pass"])
        self.assertEqual(matching.loc[0, "value_mismatches"], 0)

        direct_with_error = direct.copy()
        direct_with_error.loc[0, "close"] = 999.0
        mismatching = compare_timeframes([one_hour, direct_with_error])
        self.assertFalse(mismatching.loc[0, "timeframe_consistency_pass"])
        self.assertEqual(mismatching.loc[0, "value_mismatches"], 1)


class PointInTimeJoinTests(unittest.TestCase):
    def test_context_is_joined_only_after_conservative_availability_time(self) -> None:
        market = pd.DataFrame(
            {
                "symbol": ["BTCUSDT", "BTCUSDT"],
                "timeframe": ["1h", "1h"],
                "timestamp_utc": pd.to_datetime(
                    ["2024-01-01T00:00:00Z", "2024-01-02T00:00:00Z"],
                    utc=True,
                ),
                "close_time_utc": pd.to_datetime(
                    ["2024-01-01T00:59:59Z", "2024-01-02T00:59:59Z"],
                    utc=True,
                ),
            }
        )
        context = pd.DataFrame(
            {
                "asset": ["btc", "btc"],
                "source_timestamp_utc": pd.to_datetime(
                    ["2023-12-31T00:00:00Z", "2024-01-01T00:00:00Z"],
                    utc=True,
                ),
                "available_from_utc": pd.to_datetime(
                    ["2024-01-01T00:00:00Z", "2024-01-02T00:00:00Z"],
                    utc=True,
                ),
                "PriceUSD": [42000.0, 43000.0],
            }
        )

        joined, summary = join_context_without_lookahead(market, context)

        self.assertEqual(joined.iloc[0]["PriceUSD"], 42000.0)
        self.assertEqual(joined.iloc[1]["PriceUSD"], 43000.0)
        self.assertEqual(summary["future_context_rows"], 0)
        self.assertEqual(summary["join_row_loss"], 0)
        self.assertTrue(summary["join_pass"])


class CoinMetricsQualityTests(unittest.TestCase):
    @staticmethod
    def source_config() -> dict[str, object]:
        return {
            "asset": "btc",
            "metrics": [
                "PriceUSD",
                "CapMrktCurUSD",
                "TxCnt",
                "AdrActCnt",
            ],
            "frequency": "1d",
            "start_time": "2024-01-01",
            "end_time": "2024-01-03",
            "availability_lag_days": 1,
        }

    @staticmethod
    def complete_context_frame() -> pd.DataFrame:
        timestamps = pd.date_range(
            "2024-01-01", "2024-01-03", freq="1D", tz="UTC"
        )
        return pd.DataFrame(
            {
                "asset": "btc",
                "source_timestamp_utc": timestamps,
                "available_from_utc": timestamps + pd.Timedelta(days=1),
                "PriceUSD": [42000.0, 43000.0, 44000.0],
                "CapMrktCurUSD": [8.2e11, 8.4e11, 8.6e11],
                "TxCnt": [500000.0, 510000.0, 520000.0],
                "AdrActCnt": [800000.0, 810000.0, 820000.0],
            }
        )

    def test_exact_dates_and_finite_nonnegative_metrics_pass(self) -> None:
        result = assess_coinmetrics_context(
            self.complete_context_frame(),
            self.source_config(),
            source_file="synthetic.json",
        )

        self.assertTrue(result["quality_pass"])
        self.assertEqual(result["start_date_mismatch"], 0)
        self.assertEqual(result["end_date_mismatch"], 0)
        self.assertEqual(result["metric_non_finite_count"], 0)
        self.assertEqual(result["negative_metric_value_count"], 0)

    def test_shifted_dates_fail_exact_start_and_end_checks(self) -> None:
        frame = self.complete_context_frame()
        frame["source_timestamp_utc"] += pd.Timedelta(days=1)
        frame["available_from_utc"] += pd.Timedelta(days=1)

        result = assess_coinmetrics_context(
            frame,
            self.source_config(),
            source_file="synthetic_shifted.json",
        )

        self.assertEqual(result["rows"], result["expected_rows"])
        self.assertEqual(result["unexpected_spacing_events"], 0)
        self.assertEqual(result["start_date_mismatch"], 1)
        self.assertEqual(result["end_date_mismatch"], 1)
        self.assertEqual(result["timestamps_outside_range"], 1)
        self.assertFalse(result["quality_pass"])

    def test_non_finite_and_negative_metrics_fail(self) -> None:
        frame = self.complete_context_frame()
        frame.loc[0, "PriceUSD"] = float("inf")
        frame.loc[1, "TxCnt"] = -1.0

        result = assess_coinmetrics_context(
            frame,
            self.source_config(),
            source_file="synthetic_invalid_values.json",
        )

        self.assertEqual(result["metric_non_finite_count"], 1)
        self.assertEqual(result["negative_metric_value_count"], 1)
        self.assertFalse(result["quality_pass"])


class GateDecisionTests(unittest.TestCase):
    @staticmethod
    def valid_inputs() -> dict[str, object]:
        return {
            "quality": pd.DataFrame({"quality_pass": [True]}),
            "manifest": pd.DataFrame(
                {
                    "source": ["Binance Public Data"],
                    "provider_checksum_match": [True],
                }
            ),
            "context_quality": {"quality_pass": True},
            "timeframe_comparison": pd.DataFrame(
                {"timeframe_consistency_pass": [True]}
            ),
            "join_summary": {"join_pass": True},
            "candidates": pd.DataFrame(
                {
                    "source": [
                        "Binance Public Data",
                        "Coin Metrics Community API",
                    ]
                }
            ),
            "history_boundaries": pd.DataFrame(
                {"coverage_pass": [True, True]}
            ),
        }

    @staticmethod
    def decide(inputs: dict[str, object]) -> dict[str, object]:
        return build_gate_decision(
            inputs["quality"],
            inputs["manifest"],
            inputs["context_quality"],
            inputs["timeframe_comparison"],
            inputs["join_summary"],
            inputs["candidates"],
            inputs["history_boundaries"],
        )

    def test_gate_passes_with_all_evidence_groups_valid(self) -> None:
        passed = self.decide(self.valid_inputs())

        self.assertTrue(passed["passed"])
        self.assertTrue(all(passed["criteria"].values()))

    def test_each_gate_criterion_has_an_independent_failure_case(self) -> None:
        def fail_primary(inputs: dict[str, object]) -> None:
            inputs["quality"].loc[0, "quality_pass"] = False

        def fail_supplement(inputs: dict[str, object]) -> None:
            inputs["context_quality"]["quality_pass"] = False

        def fail_alignment(inputs: dict[str, object]) -> None:
            inputs["join_summary"]["join_pass"] = False

        def fail_timeframe(inputs: dict[str, object]) -> None:
            inputs["timeframe_comparison"].loc[
                0, "timeframe_consistency_pass"
            ] = False

        def fail_documentation(inputs: dict[str, object]) -> None:
            inputs["candidates"] = pd.DataFrame(
                {"source": ["Binance Public Data"]}
            )

        def fail_boundaries(inputs: dict[str, object]) -> None:
            inputs["history_boundaries"].loc[1, "coverage_pass"] = False

        cases = {
            "primaere_marktquelle_reproduzierbar": fail_primary,
            "ergaenzende_quelle_reproduzierbar": fail_supplement,
            "zeitlich_ausgerichtet_ohne_zukunftsdaten": fail_alignment,
            "zeitrahmen_konsistent": fail_timeframe,
            "quellenvergleich_dokumentiert": fail_documentation,
            "empfohlene_zeitraumgrenzen_erreichbar": fail_boundaries,
        }

        for criterion, fail_case in cases.items():
            with self.subTest(criterion=criterion):
                inputs = self.valid_inputs()
                fail_case(inputs)
                failed = self.decide(inputs)

                self.assertFalse(failed["passed"])
                self.assertFalse(failed["criteria"][criterion])
                self.assertEqual(
                    [
                        name
                        for name, value in failed["criteria"].items()
                        if not value
                    ],
                    [criterion],
                )


if __name__ == "__main__":
    unittest.main()
