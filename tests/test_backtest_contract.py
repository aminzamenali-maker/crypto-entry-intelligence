from __future__ import annotations

import copy
import io
import json
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from src.backtest_contract import (
    ContractError,
    EXPECTED_ASSETS,
    EXPECTED_CONTEXT_VARIANTS,
    EXPECTED_COSTS,
    EXPECTED_EXCLUDED_MONTHS,
    EXPECTED_SIGNALS,
    EXPECTED_TIMEFRAMES,
    REQUIRED_FEATURE_METADATA,
    load_config,
    main,
    run_contract,
    safe_project_path,
    sha256_file,
    validate_method_contract,
    validate_planned_evaluation_cells,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = PROJECT_ROOT / "config/backtest.json"


def phase1_signature() -> dict[str, str]:
    paths = [
        PROJECT_ROOT / "data/processed/full_import/market_context_1h.csv",
        PROJECT_ROOT / "data/processed/full_import/market_context_4h.csv",
        PROJECT_ROOT / "data/processed/full_import/sql/crypto_entry_intelligence.sqlite",
        PROJECT_ROOT / "reports/full_import/execution_checkpoint.json",
        PROJECT_ROOT / "reports/processed/processed_manifest.csv",
        PROJECT_ROOT / "reports/sql/sql_manifest.csv",
        PROJECT_ROOT / "reports/eda/eda_manifest.csv",
        PROJECT_ROOT / "powerbi/powerbi_model_manifest.csv",
    ]
    return {path.relative_to(PROJECT_ROOT).as_posix(): sha256_file(path) for path in paths}


class BacktestContractConfigurationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = load_config(CONFIG_PATH)

    def assert_invalid(self, mutate) -> None:
        candidate = copy.deepcopy(self.config)
        mutate(candidate)
        with self.assertRaises(ContractError):
            validate_method_contract(candidate, PROJECT_ROOT)

    def test_real_configuration_contract_is_valid(self) -> None:
        validate_method_contract(self.config, PROJECT_ROOT)

    def test_assets_are_exact_and_ordered(self) -> None:
        self.assertEqual(self.config["market"]["assets"], EXPECTED_ASSETS)
        self.assert_invalid(lambda cfg: cfg["market"].update(assets=["BTCUSDT", "ETHUSDT"]))

    def test_timeframes_are_exact_and_separate(self) -> None:
        self.assertEqual([item["id"] for item in self.config["timeframes"]], EXPECTED_TIMEFRAMES)
        self.assert_invalid(lambda cfg: cfg["timeframes"].append({"id": "15m"}))

    def test_long_flat_without_short_or_leverage(self) -> None:
        market = self.config["market"]
        self.assertEqual(market["position_mode"], "long_flat")
        self.assertFalse(market["short_allowed"])
        self.assertEqual(market["leverage"], 1)
        self.assert_invalid(lambda cfg: cfg["market"].update(short_allowed=True))
        self.assert_invalid(lambda cfg: cfg["market"].update(leverage=2))

    def test_spot_contract_has_no_funding(self) -> None:
        self.assertEqual(self.config["market"]["funding_bps"], 0)
        self.assert_invalid(lambda cfg: cfg["market"].update(funding_bps=1))

    def test_next_open_execution_is_mandatory(self) -> None:
        self.assertEqual(self.config["execution"]["entry_price"], "open_of_next_complete_bar_t_plus_1")
        self.assert_invalid(lambda cfg: cfg["execution"].update(entry_price="close_of_signal_bar_t"))

    def test_same_signal_bar_close_execution_is_forbidden(self) -> None:
        self.assertFalse(self.config["execution"]["same_signal_bar_close_execution_allowed"])
        self.assert_invalid(lambda cfg: cfg["execution"].update(same_signal_bar_close_execution_allowed=True))

    def test_primary_holding_periods_equal_four_market_hours(self) -> None:
        values = {item["id"]: item["bar_hours"] * item["primary_holding_bars"] for item in self.config["timeframes"]}
        self.assertEqual(values, {"1h": 4, "4h": 4})
        self.assert_invalid(lambda cfg: cfg["timeframes"][0].update(primary_holding_bars=3))

    def test_timeframe_execution_prices_may_not_be_mixed(self) -> None:
        self.assertFalse(self.config["execution"]["mix_timeframe_execution_prices"])
        self.assert_invalid(lambda cfg: cfg["execution"].update(mix_timeframe_execution_prices=True))

    def test_costs_are_positive_and_primary_is_not_free(self) -> None:
        self.assertFalse(self.config["costs"]["free_primary_evaluation_allowed"])
        self.assertTrue(all(item["round_trip_bps"] > 0 for item in self.config["costs"]["scenarios"]))
        self.assert_invalid(lambda cfg: cfg["costs"]["scenarios"][0].update(entry_fee_bps=0))

    def test_cost_scenarios_are_exactly_20_30_and_50_bps(self) -> None:
        self.assertEqual([item["id"] for item in self.config["costs"]["scenarios"]], list(EXPECTED_COSTS))
        self.assert_invalid(lambda cfg: cfg["costs"]["scenarios"][2].update(round_trip_bps=60))

    def test_cost_components_sum_to_round_trip(self) -> None:
        for scenario in self.config["costs"]["scenarios"]:
            components = sum(scenario[key] for key in ["entry_fee_bps", "exit_fee_bps", "entry_slippage_bps", "exit_slippage_bps"])
            self.assertEqual(components, scenario["round_trip_bps"])

    def test_complete_segment_reset_contract_is_required(self) -> None:
        self.assertEqual(self.config["segment_policy"]["group_keys"], ["symbol", "timeframe", "segment_id"])
        self.assertTrue(self.config["segment_policy"]["reset_all_rolling_state"])
        self.assert_invalid(lambda cfg: cfg["segment_policy"].update(reset_all_rolling_state=False))

    def test_position_may_not_cross_segment_boundary(self) -> None:
        self.assertFalse(self.config["segment_policy"]["allow_position_across_segment"])
        self.assertTrue(self.config["execution"]["require_full_horizon_inside_segment"])
        self.assert_invalid(lambda cfg: cfg["segment_policy"].update(allow_position_across_segment=True))

    def test_excluded_months_and_common_asset_mask_are_exact(self) -> None:
        policy = self.config["segment_policy"]
        self.assertEqual(policy["excluded_months"], EXPECTED_EXCLUDED_MONTHS)
        self.assertTrue(policy["common_asset_availability_mask"])
        self.assertFalse(policy["allow_trade_touching_excluded_month"])
        self.assert_invalid(lambda cfg: cfg["segment_policy"]["excluded_months"].pop())

    def test_baselines_are_deterministic_and_exact(self) -> None:
        self.assertEqual([item["id"] for item in self.config["baselines"]], ["always_flat", "segment_buy_and_hold", "periodic_entry_baseline"])
        self.assertTrue(all(not item["uses_randomness"] for item in self.config["baselines"]))
        self.assert_invalid(lambda cfg: cfg["baselines"][2].update(uses_randomness=True))

    def test_signal_variants_are_exact_and_limited_per_family(self) -> None:
        self.assertEqual([item["id"] for item in self.config["signals"]], EXPECTED_SIGNALS)
        self.assert_invalid(lambda cfg: cfg["signals"].append(copy.deepcopy(cfg["signals"][0])))

    def test_all_feature_metadata_is_complete(self) -> None:
        for feature in self.config["features"]:
            self.assertEqual(set(feature), REQUIRED_FEATURE_METADATA)
        self.assert_invalid(lambda cfg: cfg["features"][0].pop("leakage_risk"))

    def test_every_feature_resets_by_symbol_timeframe_segment(self) -> None:
        self.assertTrue(all(item["segment_reset"] == "symbol_timeframe_segment_id" for item in self.config["features"]))
        self.assert_invalid(lambda cfg: cfg["features"][0].update(segment_reset="symbol_only"))

    def test_feature_minimum_history_must_be_complete(self) -> None:
        self.assertTrue(all(item["minimum_history"] >= 1 for item in self.config["features"]))
        self.assert_invalid(lambda cfg: cfg["features"][0].update(minimum_history=0))

    def test_future_and_outcome_fields_are_forbidden_as_feature_inputs(self) -> None:
        forbidden = set(self.config["forbidden_feature_fields"])
        self.assertIn("forward_return", forbidden)
        self.assertIn("net_return", forbidden)
        self.assert_invalid(lambda cfg: cfg["features"][0]["input_fields"].append("forward_return"))

    def test_breakout_high_is_shifted_by_one_bar(self) -> None:
        feature = next(item for item in self.config["features"] if item["name"] == "prior_high_20_shifted")
        self.assertEqual(feature["lookback"]["unit"], "prior_bars_excluding_t")
        self.assertIn("shifted by exactly one bar", feature["leakage_risk"])

    def test_taker_buy_share_is_only_defined_for_1h(self) -> None:
        feature = next(item for item in self.config["features"] if item["name"] == "taker_buy_share_1h")
        self.assertEqual(feature["timeframes"], ["1h"])

    def test_temporal_splits_are_contiguous_and_non_overlapping(self) -> None:
        splits = self.config["splits"]
        self.assertEqual(splits[0]["end_exclusive_utc"], splits[1]["start_inclusive_utc"])
        self.assertEqual(splits[1]["end_exclusive_utc"], splits[2]["start_inclusive_utc"])
        self.assert_invalid(lambda cfg: cfg["splits"][1].update(start_inclusive_utc="2022-12-01T00:00:00Z"))

    def test_final_test_is_not_allowed_for_parameter_selection(self) -> None:
        final_test = self.config["splits"][-1]
        self.assertFalse(final_test["parameter_selection_allowed"])
        self.assertTrue(final_test["evaluate_once_after_method_approval"])
        self.assert_invalid(lambda cfg: cfg["splits"][-1].update(parameter_selection_allowed=True))

    def test_signals_select_parameters_only_on_development_and_validation(self) -> None:
        self.assertTrue(all(item["parameter_selection_periods"] == ["development", "validation"] for item in self.config["signals"]))
        self.assert_invalid(lambda cfg: cfg["signals"][0]["parameter_selection_periods"].append("final_test"))

    def test_d1_and_d2_are_separate_recomputed_asof_contracts(self) -> None:
        variants = self.config["context_variants"]
        self.assertEqual([item["id"] for item in variants], list(EXPECTED_CONTEXT_VARIANTS))
        self.assertTrue(all(item["recompute_asof_join"] for item in variants))
        self.assertFalse(variants[1]["shift_primary_values_allowed"])
        self.assert_invalid(lambda cfg: cfg["context_variants"][1].update(recompute_asof_join=False))

    def test_d2_may_not_be_simulated_by_shifting_d1_values(self) -> None:
        self.assert_invalid(lambda cfg: cfg["context_variants"][1].update(shift_primary_values_allowed=True))

    def test_safe_project_paths_reject_absolute_and_parent_traversal(self) -> None:
        with self.assertRaises(ContractError):
            safe_project_path(PROJECT_ROOT, "../outside.csv")
        with self.assertRaises(ContractError):
            safe_project_path(PROJECT_ROOT, "C:/Users/example/secret.csv")
        self.assert_invalid(lambda cfg: cfg["source_contract"]["canonical_tables"][0].update(path="../outside.csv"))

    def test_network_access_and_allowed_hosts_are_empty(self) -> None:
        restrictions = self.config["restrictions"]
        self.assertFalse(restrictions["network_access"])
        self.assertEqual(restrictions["allowed_hosts"], [])
        self.assert_invalid(lambda cfg: cfg["restrictions"].update(network_access=True))
        self.assert_invalid(lambda cfg: cfg["restrictions"].update(allowed_hosts=["example.invalid"]))

    def test_url_in_configuration_is_rejected(self) -> None:
        self.assert_invalid(lambda cfg: cfg["baselines"][0].update(definition="https://example.invalid"))

    def test_phase2a_forbids_feature_signal_trade_and_result_generation(self) -> None:
        restrictions = self.config["restrictions"]
        for key in ["calculate_features", "calculate_signals", "generate_positions", "run_backtest", "run_machine_learning"]:
            self.assertFalse(restrictions[key])
        self.assert_invalid(lambda cfg: cfg["restrictions"].update(calculate_features=True))

    def test_planned_evaluation_cell_counts_are_reproducible(self) -> None:
        self.assertEqual(
            validate_planned_evaluation_cells(self.config),
            {"primary_horizon": 288, "additional_horizon_sensitivities": 432, "total": 720},
        )

    def test_gate_2_remains_not_evaluated(self) -> None:
        self.assertEqual(self.config["gate_2_status"], "NOT_EVALUATED")
        self.assert_invalid(lambda cfg: cfg.update(gate_2_status="PASS"))


class BacktestContractReadOnlyIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.before = phase1_signature()
        cls.result = run_contract(CONFIG_PATH)
        cls.after = phase1_signature()

    def test_real_phase1_protected_hashes_validate(self) -> None:
        individual = self.result["protected_phase1"]["individual_files"]
        self.assertEqual(individual["processed_1h"], "7468ce970381e34fc60a8227fb1594dee5435e88f5521f06ed82bfa15f5ce805")
        self.assertEqual(individual["processed_4h"], "ab2ff44340b295d140db9fa1cb81cf5690dc7d78a44392599381c1d2e7edc91b")
        self.assertEqual(individual["sqlite"], "7f2e5deadd2c3c3e3f1820266f7f7b680def14d6ecda62c8dbbf5a11d9f0033e")

    def test_real_raw_and_interim_group_fingerprints_validate(self) -> None:
        groups = self.result["protected_phase1"]["groups"]
        self.assertEqual(groups["raw_full_import"]["file_count"], 361)
        self.assertEqual(groups["interim_full_import"]["file_count"], 319)

    def test_real_split_row_counts_are_exact(self) -> None:
        splits = self.result["source_validation"]["split_rows"]
        self.assertEqual((splits["development"]["1h"], splits["development"]["4h"], splits["development"]["total"]), (39528, 9882, 49410))
        self.assertEqual((splits["validation"]["1h"], splits["validation"]["4h"], splits["validation"]["total"]), (24048, 6012, 30060))
        self.assertEqual((splits["final_test"]["1h"], splits["final_test"]["4h"], splits["final_test"]["total"]), (52632, 13158, 65790))

    def test_coinmetrics_context_supports_independent_d2_join(self) -> None:
        context = self.result["source_validation"]["coinmetrics_context"]
        self.assertEqual(context["row_count"], 1828)
        self.assertEqual(context["sha256"], "e9ea2666a84ba8ef1eb38ea8abdb929602f76b02362ae37905cea888c57889f5")

    def test_contract_run_is_read_only_for_phase1_artifacts(self) -> None:
        self.assertEqual(self.before, self.after)
        self.assertEqual(self.result["files_written"], 0)

    def test_contract_run_calculates_no_features_signals_positions_or_backtests(self) -> None:
        self.assertEqual(self.result["features_calculated"], 0)
        self.assertEqual(self.result["signals_calculated"], 0)
        self.assertEqual(self.result["positions_generated"], 0)
        self.assertEqual(self.result["backtests_run"], 0)

    def test_cli_prints_offline_validation_json_without_writes(self) -> None:
        before = phase1_signature()
        output = io.StringIO()
        with redirect_stdout(output):
            exit_code = main(["--config", str(CONFIG_PATH)])
        payload = json.loads(output.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["status"], "PHASE2A_CONTRACT_VALID")
        self.assertEqual(payload["gate_2"], "NOT_EVALUATED")
        self.assertFalse(payload["network_access"])
        self.assertEqual(before, phase1_signature())

    def test_source_module_imports_no_network_client(self) -> None:
        source = (PROJECT_ROOT / "src/backtest_contract.py").read_text(encoding="utf-8")
        for forbidden in ["import requests", "import urllib", "import socket", "http.client"]:
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
