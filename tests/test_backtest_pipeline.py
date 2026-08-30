from __future__ import annotations

import json
import statistics
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from unittest import mock

from src.backtest_pipeline import (
    FEATURE_NAMES,
    Phase2BError,
    apply_cost,
    build_evaluation,
    buy_hold_trades,
    compute_group_features,
    context_lookup,
    evaluate_signals,
    periodic_trades,
    publish_bundle,
    read_market_rows,
    signal_trades,
    validate_cached_bundle,
    validate_cached_provenance,
    validate_phase2b_config,
    _mean,
)


UTC = timezone.utc


def context_rows() -> list[dict[str, object]]:
    rows = []
    start = datetime(2020, 12, 30, tzinfo=UTC)
    for index in range(8):
        source = start + timedelta(days=index)
        rows.append({
            "asset": "btc",
            "source_timestamp": source,
            "primary_d1": source + timedelta(days=1),
            "sensitivity_d2": source + timedelta(days=2),
            "context_price_usd": 100.0 + index,
            "context_market_cap_usd": 1000.0 + index * 10,
            "context_tx_count": 200.0 + index,
            "context_active_address_count": 300.0 + index,
        })
    return rows


def market_rows(count: int = 100, *, timeframe: str = "1h", start: datetime | None = None, segment: str = "SEGMENT_001") -> list[dict[str, object]]:
    start = start or datetime(2021, 1, 1, tzinfo=UTC)
    hours = 1 if timeframe == "1h" else 4
    rows = []
    for index in range(count):
        timestamp = start + timedelta(hours=index * hours)
        close = 100.0 + index
        rows.append({
            "split": "development",
            "symbol": "BTCUSDT",
            "timeframe": timeframe,
            "timestamp": timestamp,
            "timestamp_utc": timestamp.strftime("%Y-%m-%dT%H:%M:%S.000000Z"),
            "decision": timestamp + timedelta(hours=hours),
            "decision_time_utc": (timestamp + timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%S.000000Z"),
            "segment_id": segment,
            "open": close - 0.25,
            "high": close + 1.0,
            "low": close - 1.0,
            "close": close,
            "volume": 1000.0 + index,
            "taker_buy_base_volume": 400.0 + index,
        })
    return rows


class FeatureTests(unittest.TestCase):
    def test_all_25_preregistered_features_exist(self) -> None:
        result = compute_group_features(market_rows(), context_rows(), "primary_d1")
        self.assertEqual(25, len(FEATURE_NAMES))
        self.assertTrue(set(FEATURE_NAMES) <= set(result[-1]))

    def test_backward_returns_and_shifted_prior_high(self) -> None:
        result = compute_group_features(market_rows(), context_rows(), "primary_d1")
        for lag in (1, 4, 12, 24):
            self.assertAlmostEqual((100 + lag) / 100 - 1, result[lag][f"past_return_{lag}"])
        self.assertIsNone(result[19]["prior_high_20_shifted"])
        self.assertEqual(120.0, result[20]["prior_high_20_shifted"])

    def test_sma_and_distance_are_backward(self) -> None:
        result = compute_group_features(market_rows(), context_rows(), "primary_d1")
        expected = sum(range(100, 120)) / 20
        self.assertAlmostEqual(expected, result[19]["sma_20"])
        self.assertAlmostEqual(119 / expected - 1, result[19]["close_to_sma20_distance"])
        expected_50 = sum(range(100, 150)) / 50
        self.assertAlmostEqual(expected_50, result[49]["sma_50"])
        self.assertAlmostEqual(result[49]["sma_20"] / expected_50, result[49]["sma_ratio_20_50"])

    def test_wilder_rsi_initialization_and_state(self) -> None:
        result = compute_group_features(market_rows(), context_rows(), "primary_d1")
        self.assertIsNone(result[13]["rsi_14"])
        self.assertEqual(100.0, result[14]["rsi_14"])
        self.assertEqual(100.0, result[50]["rsi_14"])

    def test_wilder_atr_initialization(self) -> None:
        result = compute_group_features(market_rows(), context_rows(), "primary_d1")
        self.assertIsNone(result[13]["atr_14_relative"])
        self.assertAlmostEqual(2 / 114, result[14]["atr_14_relative"])

    def test_sample_standard_deviation_and_volume_zscore(self) -> None:
        result = compute_group_features(market_rows(), context_rows(), "primary_d1")
        self.assertIsNotNone(result[24]["rolling_volatility_24"])
        self.assertIsNotNone(result[72]["rolling_volatility_72"])
        self.assertIsNotNone(result[23]["volume_zscore_24"])
        self.assertIsNone(result[23]["rolling_volatility_24"])
        sample = [1000.0 + value for value in range(24)]
        self.assertAlmostEqual((1023.0 - statistics.mean(sample)) / statistics.stdev(sample), result[23]["volume_zscore_24"])

    def test_taker_feature_is_one_hour_only(self) -> None:
        one = compute_group_features(market_rows(timeframe="1h"), context_rows(), "primary_d1")
        four = compute_group_features(market_rows(timeframe="4h"), context_rows(), "primary_d1")
        self.assertIsNotNone(one[-1]["taker_buy_share_1h"])
        self.assertAlmostEqual((400 + 99) / (1000 + 99), one[-1]["taker_buy_share_1h"])
        self.assertIsNone(four[-1]["taker_buy_share_1h"])

    def test_separate_calls_reset_rolling_state(self) -> None:
        first = compute_group_features(market_rows(80), context_rows(), "primary_d1")
        second = compute_group_features(market_rows(20, start=datetime(2021, 1, 5, tzinfo=UTC), segment="SEGMENT_002"), context_rows(), "primary_d1")
        self.assertIsNotNone(first[-1]["sma_50"])
        self.assertIsNone(second[-1]["sma_50"])
        for field in ("past_return_24", "sma_20", "rsi_14", "atr_14_relative",
                      "rolling_volatility_24", "rolling_volatility_72",
                      "volume_zscore_24", "prior_high_20_shifted",
                      "close_to_sma20_distance"):
            self.assertIsNone(second[0][field], field)
        for field in ("context_price_usd_change", "context_market_cap_usd_change",
                      "context_tx_count_change", "context_active_address_count_change"):
            self.assertIsNone(second[0][field], field)

    def test_d1_and_d2_are_independent_backward_asof_joins(self) -> None:
        rows = market_rows(2, start=datetime(2021, 1, 2, tzinfo=UTC))
        d1 = compute_group_features(rows, context_rows(), "primary_d1")
        d2 = compute_group_features(rows, context_rows(), "sensitivity_d2")
        self.assertNotEqual(d1[0]["context_source_timestamp_utc"], d2[0]["context_source_timestamp_utc"])
        self.assertEqual(102.0, d1[0]["context_price_usd"])
        self.assertEqual(101.0, d2[0]["context_price_usd"])

    def test_context_join_never_selects_future_availability(self) -> None:
        context = context_rows()
        availability = [row["sensitivity_d2"] for row in context]
        decision = datetime(2021, 1, 1, 12, tzinfo=UTC)
        selected = context_lookup(context, availability, "sensitivity_d2", decision)
        self.assertLessEqual(selected["sensitivity_d2"], decision)

    def test_signal_rules_are_exact(self) -> None:
        rows = market_rows(80)
        result = compute_group_features(rows, context_rows(), "primary_d1")
        self.assertEqual(result[12]["past_return_12"] > 0, result[12]["momentum_return_12_positive"])
        self.assertEqual(result[20]["close"] > result[20]["prior_high_20_shifted"], result[20]["breakout_close_above_prior_high_20"])

    def test_candle_shape_and_context_change_formulas(self) -> None:
        result = compute_group_features(market_rows(30, start=datetime(2021, 1, 2, tzinfo=UTC)), context_rows(), "primary_d1")
        row = result[24]
        self.assertAlmostEqual((row["high"] - row["low"]) / row["open"], row["candle_range_relative"])
        self.assertAlmostEqual((row["close"] - row["open"]) / row["open"], row["candle_body_relative"])
        changed = next(value for value in result if value["context_price_usd_change"] is not None)
        for level, change in (
            ("context_price_usd", "context_price_usd_change"),
            ("context_market_cap_usd", "context_market_cap_usd_change"),
            ("context_tx_count", "context_tx_count_change"),
            ("context_active_address_count", "context_active_address_count_change"),
        ):
            self.assertGreater(changed[level], 0)
            self.assertGreater(changed[change], 0)

    def test_all_five_signal_thresholds(self) -> None:
        values = {
            "sma_ratio_20_50": 1.01,
            "past_return_12": 0.001,
            "prior_high_20_shifted": 99.0,
            "rsi_14": 29.999,
            "close_to_sma20_distance": -0.02,
        }
        self.assertTrue(all(evaluate_signals(values, 1.0, 100.0).values()))
        values.update(sma_ratio_20_50=1.0, past_return_12=0.0, prior_high_20_shifted=100.0, rsi_14=30.0, close_to_sma20_distance=-0.019999)
        self.assertFalse(any(evaluate_signals(values, 1.0, 100.0).values()))

    def test_observed_nonconstant_sma_boundary_fixture_has_crossover_one_bar_later(self) -> None:
        prices_text = [
            "12.81", "12.91", "12.91", "13.01", "13.02", "12.9", "12.48", "12.25",
            "12.26", "12.11", "12.2", "12.26", "12.34", "12.27", "12.4", "12.46",
            "12.39", "12.32", "12.45", "12.31", "12.25", "12.24", "12.28", "12.31",
            "12.28", "12.2", "12.27", "12.26", "12.27", "12.29", "12.43", "12.51",
            "12.44", "12.39", "12.45", "12.49", "12.54", "12.48", "12.44", "12.43",
            "12.45", "12.29", "12.36", "12.39", "12.42", "12.35", "12.31", "12.31",
            "12.36", "12.4", "12.41", "12.44",
        ]
        decimal_prices = [Decimal(value) for value in prices_text]
        self.assertGreater(len(set(decimal_prices)), 1)
        self.assertEqual(Decimal("12.411"), sum(decimal_prices[31:51]) / 20)
        self.assertEqual(Decimal("12.411"), sum(decimal_prices[1:51]) / 50)
        self.assertEqual(Decimal("12.4075"), sum(decimal_prices[32:52]) / 20)
        self.assertEqual(Decimal("12.4016"), sum(decimal_prices[2:52]) / 50)
        rows = market_rows(len(prices_text))
        for row, close in zip(rows, map(float, prices_text)):
            row.update(open=close, high=close + 0.01, low=close - 0.01, close=close)
        d1 = compute_group_features(rows, context_rows(), "primary_d1")
        d2 = compute_group_features(rows, context_rows(), "sensitivity_d2")
        for result in (d1, d2):
            self.assertEqual(12.411, result[50]["sma_20"])
            self.assertEqual(12.411, result[50]["sma_50"])
            self.assertFalse(result[50]["trend_sma20_cross_above_sma50"])
            self.assertEqual(12.4075, result[51]["sma_20"])
            self.assertEqual(12.4016, result[51]["sma_50"])
            self.assertTrue(result[51]["trend_sma20_cross_above_sma50"])


class ExecutionTests(unittest.TestCase):
    def featured(self, count: int = 40) -> list[dict[str, object]]:
        rows = market_rows(count)
        for row in rows:
            row["test_signal"] = False
            row["context_variant"] = "primary_d1"
        return rows

    def test_signal_entry_next_open_and_exact_exit_open(self) -> None:
        rows = self.featured()
        rows[3]["test_signal"] = True
        trades, counts = signal_trades(rows, "test_signal", 4)
        self.assertEqual(rows[4]["timestamp_utc"], trades[0]["entry_time_utc"])
        self.assertEqual(rows[8]["timestamp_utc"], trades[0]["exit_time_utc"])
        self.assertEqual(1, counts["executable_signal_count"])

    def test_no_shortened_trade_at_boundary(self) -> None:
        rows = self.featured(10)
        rows[7]["test_signal"] = True
        trades, counts = signal_trades(rows, "test_signal", 4)
        self.assertEqual([], trades)
        self.assertEqual(1, counts["rejected_boundary_count"])

    def test_trade_may_not_cross_split(self) -> None:
        rows = self.featured(12)
        rows[4]["test_signal"] = True
        for row in rows[7:]:
            row["split"] = "validation"
        trades, counts = signal_trades(rows, "test_signal", 4)
        self.assertEqual([], trades)
        self.assertEqual(1, counts["rejected_boundary_count"])

    def test_overlapping_signal_is_rejected(self) -> None:
        rows = self.featured()
        rows[2]["test_signal"] = True
        rows[4]["test_signal"] = True
        trades, counts = signal_trades(rows, "test_signal", 4)
        self.assertEqual(1, len(trades))
        self.assertEqual(1, counts["rejected_overlap_count"])

    def test_cost_formula_is_multiplicative(self) -> None:
        trade = {"entry_open": 100.0, "exit_open": 110.0, "gross_return": 0.1}
        scenario = {"id": "base_30bps", "entry_fee_bps": 10, "exit_fee_bps": 10, "entry_slippage_bps": 5, "exit_slippage_bps": 5}
        result = apply_cost(trade, scenario)
        expected = 110 * .9995 * .999 / (100 * 1.0005 * 1.001) - 1
        self.assertAlmostEqual(expected, result["net_return"])

    def test_periodic_baseline_uses_first_utc_week_open(self) -> None:
        rows = self.featured(200)
        trades, _ = periodic_trades(rows, 4)
        self.assertEqual(rows[0]["timestamp_utc"], trades[0]["entry_time_utc"])
        self.assertEqual(rows[4]["timestamp_utc"], trades[0]["exit_time_utc"])

    def test_four_hour_holding_one_exits_after_one_full_bar(self) -> None:
        rows = market_rows(10, timeframe="4h")
        for row in rows:
            row["test_signal"] = False; row["context_variant"] = "primary_d1"
        rows[2]["test_signal"] = True
        trades, _ = signal_trades(rows, "test_signal", 1)
        self.assertEqual(rows[3]["timestamp_utc"], trades[0]["entry_time_utc"])
        self.assertEqual(rows[4]["timestamp_utc"], trades[0]["exit_time_utc"])

    def test_buy_hold_uses_first_and_last_open_inside_split_segment(self) -> None:
        rows = self.featured(10)
        trades = buy_hold_trades(rows)
        self.assertEqual(1, len(trades))
        self.assertEqual(rows[0]["timestamp_utc"], trades[0]["entry_time_utc"])
        self.assertEqual(rows[-1]["timestamp_utc"], trades[0]["exit_time_utc"])

    def test_always_flat_cells_have_no_trades_or_exposure(self) -> None:
        rows = compute_group_features(market_rows(80), context_rows(), "primary_d1")
        phase2a = json.loads((Path(__file__).resolve().parents[1] / "config/backtest.json").read_text(encoding="utf-8"))
        _, _, results, _ = build_evaluation({("synthetic", "primary_d1"): rows}, phase2a)
        flat = [row for row in results if row["strategy_id"] == "always_flat"]
        self.assertTrue(flat)
        self.assertTrue(all(row["trade_count"] == 0 and row["exposure_hours"] == 0 for row in flat))


class CacheTests(unittest.TestCase):
    def test_cached_provenance_accepts_exact_payload(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            report = Path(temporary)
            provenance = {"phase2b_config_sha256": "a", "backtest_pipeline_sha256": "b",
                          "implementation_policy_id": "policy", "float_serialization_rule": ".17g",
                          "sma_calculation_rule": "math.fsum(window)/window_length"}
            (report / "input_output_hashes.json").write_text(json.dumps({"provenance": provenance}), encoding="utf-8")
            validate_cached_provenance(report, provenance)

    def test_cached_provenance_rejects_code_config_and_numeric_policy_changes(self) -> None:
        original = {"phase2b_config_sha256": "config", "backtest_pipeline_sha256": "code",
                    "implementation_policy_id": "policy", "float_serialization_rule": ".17g",
                    "sma_calculation_rule": "math.fsum(window)/window_length",
                    "phase2a_contract_sha256": "phase2a", "protected_inputs": {},
                    "final_test_status": "SEALED_NOT_EVALUATED"}
        with tempfile.TemporaryDirectory() as temporary:
            report = Path(temporary)
            for field in ("phase2b_config_sha256", "backtest_pipeline_sha256",
                          "implementation_policy_id", "float_serialization_rule",
                          "sma_calculation_rule", "phase2a_contract_sha256",
                          "final_test_status"):
                with self.subTest(field=field):
                    changed = dict(original); changed[field] = "changed"
                    (report / "input_output_hashes.json").write_text(json.dumps({"provenance": original}), encoding="utf-8")
                    with self.assertRaises(Phase2BError):
                        validate_cached_provenance(report, changed)

    def test_byte_identical_cache_is_accepted_read_only(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            generated_data = root / "gd"; generated_report = root / "gr"
            cached_data = root / "cd"; cached_report = root / "cr"
            for directory in (generated_data, generated_report, cached_data, cached_report): directory.mkdir()
            for directory in (generated_data, cached_data): (directory / "a.csv").write_bytes(b"x\n1\n")
            for directory in (generated_report, cached_report): (directory / "b.json").write_bytes(b"{}\n")
            before = ((cached_data / "a.csv").stat().st_mtime_ns, (cached_report / "b.json").stat().st_mtime_ns)
            validate_cached_bundle(generated_data, generated_report, cached_data, cached_report)
            after = ((cached_data / "a.csv").stat().st_mtime_ns, (cached_report / "b.json").stat().st_mtime_ns)
            self.assertEqual(before, after)

    def test_cache_mismatch_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            directories = [root / name for name in ("gd", "gr", "cd", "cr")]
            for directory in directories: directory.mkdir()
            (directories[0] / "a").write_bytes(b"one")
            (directories[2] / "a").write_bytes(b"two")
            with self.assertRaises(Phase2BError):
                validate_cached_bundle(*directories)

    def test_partial_cache_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            data = root / "data"; report = root / "report"; data.mkdir()
            with self.assertRaises(Phase2BError):
                validate_cached_bundle(root / "generated_data", root / "generated_report", data, report)

    def test_publish_refuses_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            generated_data = root / "gd"; generated_report = root / "gr"; data = root / "data"; report = root / "report"
            generated_data.mkdir(); generated_report.mkdir(); data.mkdir()
            with self.assertRaises(Phase2BError):
                publish_bundle(generated_data, generated_report, data, report)

    def test_second_promotion_failure_rolls_back_first(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            generated_data = root / "gd"; generated_report = root / "gr"; data = root / "data"; report = root / "report"
            generated_data.mkdir(); generated_report.mkdir()
            real_replace = __import__("os").replace
            calls = 0
            def replacement(source, destination):
                nonlocal calls
                calls += 1
                if calls == 2:
                    raise OSError("synthetic promotion failure")
                return real_replace(source, destination)
            with mock.patch("src.backtest_pipeline.os.replace", side_effect=replacement):
                with self.assertRaises(OSError):
                    publish_bundle(generated_data, generated_report, data, report)
            self.assertFalse(data.exists())
            self.assertTrue(generated_data.exists())
            self.assertFalse(report.exists())


class SealedSplitIntegrationTests(unittest.TestCase):
    def test_real_phase2b_config_is_offline_and_seals_final_test(self) -> None:
        root = Path(__file__).resolve().parents[1]
        config = json.loads((root / "config/backtest_phase2b.json").read_text(encoding="utf-8"))
        phase2a = validate_phase2b_config(config, root)
        self.assertFalse(config["restrictions"]["network_access"])
        self.assertFalse(config["restrictions"]["evaluate_final_test"])
        self.assertEqual("NOT_EVALUATED", config["gate_2_status"])
        self.assertEqual("phase2b_fsum_float17_provenance_v2", config["policy"]["implementation_policy_id"])
        self.assertEqual("math.fsum(window)/window_length", config["policy"]["sma_calculation_rule"])
        final_test = next(value for value in phase2a["splits"] if value["id"] == "final_test")
        self.assertEqual(65790, final_test["expected_rows"]["total"])

    def test_real_final_test_is_counted_but_never_returned_for_features(self) -> None:
        root = Path(__file__).resolve().parents[1]
        phase2a = json.loads((root / "config/backtest.json").read_text(encoding="utf-8"))
        groups, quality = read_market_rows(root, phase2a)
        self.assertEqual(65790, quality["sealed_rows_recognized_for_key_integrity_only"])
        self.assertEqual({"development", "validation"}, {row["split"] for rows in groups.values() for row in rows})
        self.assertEqual(79470, sum(len(rows) for rows in groups.values()))


if __name__ == "__main__":
    unittest.main()
