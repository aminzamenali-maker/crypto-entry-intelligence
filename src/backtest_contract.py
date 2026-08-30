from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter
from datetime import datetime
from hashlib import sha256
from pathlib import Path, PurePosixPath
from typing import Any, Iterable


EXPECTED_ASSETS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
EXPECTED_TIMEFRAMES = ["1h", "4h"]
EXPECTED_BASELINES = ["always_flat", "segment_buy_and_hold", "periodic_entry_baseline"]
EXPECTED_SIGNALS = [
    "trend_sma20_cross_above_sma50",
    "momentum_return_12_positive",
    "breakout_close_above_prior_high_20",
    "mean_reversion_rsi14_below_30",
    "mean_reversion_close_2pct_below_sma20",
]
EXPECTED_COSTS = {
    "low_20bps": (5, 5, 5, 5, 20),
    "base_30bps": (10, 10, 5, 5, 30),
    "high_50bps": (15, 15, 10, 10, 50),
}
EXPECTED_SPLITS = [
    ("development", "2021-01-01T00:00:00Z", "2023-01-01T00:00:00Z", True),
    ("validation", "2023-01-01T00:00:00Z", "2024-01-01T00:00:00Z", True),
    ("final_test", "2024-01-01T00:00:00Z", "2026-01-01T00:00:00Z", False),
]
EXPECTED_EXCLUDED_MONTHS = [
    "2021-02",
    "2021-03",
    "2021-04",
    "2021-08",
    "2021-09",
    "2021-12",
    "2023-03",
]
EXPECTED_CONTEXT_VARIANTS = {
    "primary_d1": ("available_from_utc_d1", 1, "available_from_utc_d1 <= decision_time_utc"),
    "sensitivity_d2": ("available_from_utc_d2", 2, "available_from_utc_d2 <= decision_time_utc"),
}
REQUIRED_FEATURE_METADATA = {
    "name",
    "formula",
    "input_fields",
    "timeframes",
    "lookback",
    "minimum_history",
    "available_at",
    "null_policy",
    "segment_reset",
    "leakage_risk",
    "purpose",
}
ONE_HOUR_FIELDS = [
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
    "taker_buy_base_volume",
    "taker_buy_quote_volume",
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
]
FOUR_HOUR_FIELDS = [
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
    "constituent_rows",
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
]
CONTEXT_FIELDS = [
    "asset",
    "source_timestamp_utc",
    "available_from_utc_d1",
    "available_from_utc_d2",
    "PriceUSD",
    "CapMrktCurUSD",
    "TxCnt",
    "AdrActCnt",
]
ALLOWED_REPORT_FILES = {
    "FEATURE_SIGNAL_DICTIONARY.md",
    "GATE2_ACCEPTANCE_CRITERIA.md",
    "PHASE2_METHOD_PLAN.md",
}


class ContractError(RuntimeError):
    """The preregistered Phase-2A contract is incomplete or unsafe."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ContractError(message)


def sha256_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_utc(value: str) -> datetime:
    require(value.endswith("Z"), f"UTC timestamp must end in Z: {value}")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    require(parsed.utcoffset() is not None, f"timezone missing: {value}")
    return parsed


def safe_project_path(project_root: Path, relative: str) -> Path:
    require(isinstance(relative, str) and relative != "", "project path must be a non-empty string")
    require("\\" not in relative, f"project path must use forward slashes: {relative}")
    candidate = PurePosixPath(relative)
    require(not candidate.is_absolute(), f"absolute project path is forbidden: {relative}")
    require(not (candidate.parts and candidate.parts[0].endswith(":")), f"drive-qualified project path is forbidden: {relative}")
    require(".." not in candidate.parts, f"parent traversal is forbidden: {relative}")
    resolved_root = project_root.resolve()
    resolved = (resolved_root / Path(*candidate.parts)).resolve()
    try:
        resolved.relative_to(resolved_root)
    except ValueError as exc:
        raise ContractError(f"project path escapes root: {relative}") from exc
    return resolved


def load_config(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        payload = json.load(handle)
    require(isinstance(payload, dict), "configuration root must be an object")
    return payload


def _all_strings(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _all_strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _all_strings(item)


def validate_restrictions(config: dict[str, Any]) -> None:
    restrictions = config.get("restrictions", {})
    expected_true = ["historical_data_only"]
    expected_false = [
        "network_access",
        "write_outputs",
        "calculate_features",
        "calculate_signals",
        "generate_positions",
        "run_backtest",
        "run_machine_learning",
    ]
    for key in expected_true:
        require(restrictions.get(key) is True, f"restriction {key} must be true")
    for key in expected_false:
        require(restrictions.get(key) is False, f"restriction {key} must be false")
    require(restrictions.get("allowed_hosts") == [], "allowed_hosts must be empty")
    require(not any("://" in value for value in _all_strings(config)), "URLs and hosts are forbidden in Phase 2A")


def validate_market_and_execution(config: dict[str, Any]) -> None:
    market = config.get("market", {})
    require(market.get("venue") == "binance_spot", "venue must be Binance Spot")
    require(market.get("assets") == EXPECTED_ASSETS, "assets must match the preregistered order")
    require(market.get("position_mode") == "long_flat", "position mode must be long/flat")
    require(market.get("short_allowed") is False, "short positions are forbidden")
    require(market.get("leverage") == 1, "leverage must equal one")
    require(market.get("funding_bps") == 0, "spot funding must equal zero")
    require(market.get("max_concurrent_positions_per_asset") == 1, "only one position per asset is allowed")
    require(market.get("evaluate_assets_separately") is True, "assets must be evaluated separately")
    require(market.get("report_aggregate_results") is True, "aggregate reporting must also be planned")

    timeframes = config.get("timeframes", [])
    require([item.get("id") for item in timeframes] == EXPECTED_TIMEFRAMES, "timeframes must be exactly 1h and 4h")
    expected = {
        "1h": ("primary", 1, 4, [12, 24]),
        "4h": ("robustness", 4, 1, [3, 6]),
    }
    for item in timeframes:
        values = (item.get("role"), item.get("bar_hours"), item.get("primary_holding_bars"), item.get("sensitivity_holding_bars"))
        require(values == expected[item["id"]], f"timeframe contract mismatch: {item['id']}")

    execution = config.get("execution", {})
    require(execution.get("signal_information_cutoff") == "decision_time_utc_after_complete_bar_t", "signal must use a completed bar")
    require(execution.get("entry_price") == "open_of_next_complete_bar_t_plus_1", "entry must use next-bar open")
    require(execution.get("same_signal_bar_close_execution_allowed") is False, "same-bar close execution is forbidden")
    require(execution.get("exit_price") == "open_after_exact_holding_bars", "exit must follow the fixed full-bar horizon")
    require(execution.get("mix_timeframe_execution_prices") is False, "timeframe prices must never be mixed")
    require(execution.get("require_full_horizon_inside_segment") is True, "full horizon must fit inside the segment")
    require(execution.get("boundary_safety_exit") == "last_available_open_inside_segment", "segment safety exit is incomplete")
    require(execution.get("intrabar_stop_loss_take_profit") is False, "intrabar stop/take-profit is outside Core")
    require(execution.get("heikin_ashi_execution_price_allowed") is False, "Heikin-Ashi execution prices are forbidden")


def validate_segment_policy(config: dict[str, Any]) -> None:
    policy = config.get("segment_policy", {})
    require(policy.get("group_keys") == ["symbol", "timeframe", "segment_id"], "rolling group keys are incomplete")
    for key in ["reset_all_rolling_state", "require_full_minimum_history", "common_asset_availability_mask"]:
        require(policy.get(key) is True, f"segment rule {key} must be true")
    for key in [
        "allow_feature_across_segment",
        "allow_signal_with_incomplete_history",
        "allow_position_across_segment",
        "allow_trade_touching_excluded_month",
    ]:
        require(policy.get(key) is False, f"segment rule {key} must be false")
    require(policy.get("excluded_months") == EXPECTED_EXCLUDED_MONTHS, "excluded months must remain exact")


def validate_costs(config: dict[str, Any]) -> None:
    costs = config.get("costs", {})
    require(costs.get("units") == "basis_points", "cost unit must be basis points")
    require(costs.get("primary_scenario") == "base_30bps", "30 bps must be primary")
    scenarios = costs.get("scenarios", [])
    require([item.get("id") for item in scenarios] == list(EXPECTED_COSTS), "cost scenarios must be exactly 20, 30 and 50 bps")
    for item in scenarios:
        values = tuple(item.get(key) for key in ["entry_fee_bps", "exit_fee_bps", "entry_slippage_bps", "exit_slippage_bps", "round_trip_bps"])
        require(values == EXPECTED_COSTS[item["id"]], f"cost components mismatch: {item['id']}")
        require(all(value > 0 for value in values), f"all costs must be positive: {item['id']}")
        require(sum(values[:4]) == values[4], f"round-trip cost does not equal components: {item['id']}")
    require(costs.get("gross_and_net_reported_separately") is True, "gross and net results must be separate")
    require(costs.get("free_primary_evaluation_allowed") is False, "free primary evaluation is forbidden")


def validate_baselines_signals_features(config: dict[str, Any]) -> None:
    baselines = config.get("baselines", [])
    require([item.get("id") for item in baselines] == EXPECTED_BASELINES, "baseline set is not exact")
    for item in baselines:
        require(item.get("uses_market_indicator") is False, f"baseline uses indicator: {item.get('id')}")
        require(item.get("uses_randomness") is False, f"baseline uses randomness: {item.get('id')}")

    signals = config.get("signals", [])
    require([item.get("id") for item in signals] == EXPECTED_SIGNALS, "signal variants are not preregistered exactly")
    family_counts = Counter(item.get("family") for item in signals)
    require(set(family_counts) == {"trend_momentum", "breakout", "mean_reversion"}, "signal families are incomplete")
    require(all(count <= 2 for count in family_counts.values()), "a signal family has more than two variants")
    for item in signals:
        require(item.get("timeframes") == EXPECTED_TIMEFRAMES, f"signal timeframes are incomplete: {item.get('id')}")
        require(item.get("parameter_selection_periods") == ["development", "validation"], "signals may not be selected on final test")

    declared_metadata = config.get("feature_metadata_fields")
    require(set(declared_metadata or []) == REQUIRED_FEATURE_METADATA, "feature metadata field contract is incomplete")
    features = config.get("features", [])
    require(features, "at least one feature must be preregistered")
    names = [item.get("name") for item in features]
    require(len(names) == len(set(names)), "feature names must be unique")
    forbidden = set(config.get("forbidden_feature_fields", []))
    required_forbidden = {"forward_return", "gross_return", "net_return", "positive_net_outcome", "exit_price"}
    require(required_forbidden <= forbidden, "forbidden future or outcome fields are incomplete")
    for item in features:
        require(set(item) == REQUIRED_FEATURE_METADATA, f"feature metadata is not exact: {item.get('name')}")
        require(item["timeframes"] and set(item["timeframes"]) <= set(EXPECTED_TIMEFRAMES), f"invalid feature timeframe: {item['name']}")
        require(item["minimum_history"] >= 1, f"minimum history must be positive: {item['name']}")
        require(item["segment_reset"] == "symbol_timeframe_segment_id", f"feature does not reset by segment: {item['name']}")
        require(item["input_fields"] and not (set(item["input_fields"]) & forbidden), f"future or outcome field used as feature input: {item['name']}")
        require(item["available_at"] in {"decision_time_utc", "selected_context_availability"}, f"invalid availability: {item['name']}")
    feature_names = set(names)
    for signal in signals:
        require(set(signal.get("required_features", [])) <= feature_names, f"signal feature is missing: {signal['id']}")


def validate_targets_splits_context(config: dict[str, Any]) -> None:
    targets = config.get("targets_and_metrics", [])
    target_ids = {item.get("id") for item in targets}
    require(
        {
            "gross_return",
            "net_return",
            "positive_net_outcome",
            "maximum_adverse_excursion",
            "maximum_favorable_excursion",
            "trade_count",
            "exposure_hours",
        } == target_ids,
        "target and evaluation contract is incomplete",
    )
    require(all(item.get("role") != "feature" for item in targets), "future values may never be features")
    mae = next(item for item in targets if item["id"] == "maximum_adverse_excursion")
    mfe = next(item for item in targets if item["id"] == "maximum_favorable_excursion")
    require("intrabar order is unknown" in mae["definition"], "MAE must state unknown intrabar order")
    require("intrabar order is unknown" in mfe["definition"], "MFE must state unknown intrabar order")

    splits = config.get("splits", [])
    require(len(splits) == len(EXPECTED_SPLITS), "exactly three temporal splits are required")
    previous_end = None
    for item, expected in zip(splits, EXPECTED_SPLITS):
        split_id, start_text, end_text, selection_allowed = expected
        require(item.get("id") == split_id, f"split order mismatch: {split_id}")
        require(item.get("start_inclusive_utc") == start_text, f"split start mismatch: {split_id}")
        require(item.get("end_exclusive_utc") == end_text, f"split end mismatch: {split_id}")
        require(item.get("parameter_selection_allowed") is selection_allowed, f"selection policy mismatch: {split_id}")
        start = parse_utc(start_text)
        end = parse_utc(end_text)
        require(start < end, f"invalid split interval: {split_id}")
        if previous_end is not None:
            require(start == previous_end, f"split gap or overlap before {split_id}")
        previous_end = end
    require(splits[-1].get("evaluate_once_after_method_approval") is True, "final test must be evaluated once after approval")

    variants = config.get("context_variants", [])
    require([item.get("id") for item in variants] == list(EXPECTED_CONTEXT_VARIANTS), "D1 and D2 variants must be separate and exact")
    for item in variants:
        expected = EXPECTED_CONTEXT_VARIANTS[item["id"]]
        values = (item.get("availability_field"), item.get("lag_days"), item.get("join_rule"))
        require(values == expected, f"context variant mismatch: {item['id']}")
        require(item.get("recompute_asof_join") is True, f"context join must be recomputed: {item['id']}")
    d2 = next(item for item in variants if item["id"] == "sensitivity_d2")
    require(d2.get("shift_primary_values_allowed") is False, "D2 may not shift D1 values")


def _configured_paths(config: dict[str, Any]) -> Iterable[str]:
    source = config.get("source_contract", {})
    for item in source.get("canonical_tables", []):
        yield item.get("path", "")
    yield source.get("context_table", {}).get("path", "")
    protected = config.get("protected_phase1_artifacts", {})
    for item in protected.get("individual_files", []):
        yield item.get("path", "")
    for group in protected.get("groups", []):
        yield from group.get("roots", [])
        yield from group.get("files", [])


def validate_paths(config: dict[str, Any], project_root: Path) -> None:
    paths = list(_configured_paths(config))
    require(paths and all(paths), "source and protection paths must be complete")
    for relative in paths:
        safe_project_path(project_root, relative)
    forbidden_phase2_roots = [
        project_root / "data/raw/backtest",
        project_root / "data/interim/backtest",
        project_root / "data/processed/backtest",
    ]
    require(not any(path.exists() for path in forbidden_phase2_roots), "Phase-2 data output exists during Phase 2A")
    report_root = project_root / "reports/backtest"
    if report_root.exists():
        unexpected = [path.name for path in report_root.iterdir() if path.is_file() and path.name not in ALLOWED_REPORT_FILES]
        require(not unexpected, f"unexpected Phase-2A report outputs: {unexpected}")


def validate_method_contract(config: dict[str, Any], project_root: Path) -> None:
    require(config.get("schema_version") == 1, "backtest contract schema must equal one")
    require(config.get("phase") == "2A_METHOD_PREREGISTRATION", "phase must remain planning-only")
    require(config.get("gate_2_status") == "NOT_EVALUATED", "Gate 2 must remain NOT_EVALUATED")
    expected_question = (
        "Liefern vorab definierte, ausschließlich aus zum Entscheidungszeitpunkt verfügbaren Informationen "
        "gebildete Einstiegssignale nach realistischen Transaktionskosten einen messbaren Informationswert "
        "gegenüber einfachen Baselines?"
    )
    require(config.get("research_question") == expected_question, "research question is not exact")
    validate_restrictions(config)
    validate_market_and_execution(config)
    validate_segment_policy(config)
    validate_costs(config)
    validate_baselines_signals_features(config)
    validate_targets_splits_context(config)
    validate_paths(config, project_root)


def group_fingerprint(project_root: Path, roots: list[str], files: list[str]) -> tuple[int, int, str]:
    candidates: dict[str, Path] = {}
    for relative_root in roots:
        root = safe_project_path(project_root, relative_root)
        require(root.is_dir(), f"protected root is missing: {relative_root}")
        for path in root.rglob("*"):
            if path.is_file():
                candidates[path.relative_to(project_root).as_posix()] = path
    for relative in files:
        path = safe_project_path(project_root, relative)
        require(path.is_file(), f"protected file is missing: {relative}")
        candidates[path.relative_to(project_root).as_posix()] = path
    ordered = sorted(candidates.items(), key=lambda item: item[0].lower())
    rows = [f"{relative}|{sha256_file(path)}" for relative, path in ordered]
    total_bytes = sum(path.stat().st_size for _, path in ordered)
    fingerprint = sha256("\n".join(rows).encode("utf-8")).hexdigest()
    return len(ordered), total_bytes, fingerprint


def validate_protected_artifacts(config: dict[str, Any], project_root: Path) -> dict[str, Any]:
    protected = config["protected_phase1_artifacts"]
    individual_results: dict[str, str] = {}
    for item in protected["individual_files"]:
        path = safe_project_path(project_root, item["path"])
        require(path.is_file(), f"protected file is missing: {item['path']}")
        actual = sha256_file(path)
        require(actual == item["sha256"], f"protected file hash mismatch: {item['id']}")
        individual_results[item["id"]] = actual
    group_results: dict[str, dict[str, Any]] = {}
    for group in protected["groups"]:
        count, total_bytes, fingerprint = group_fingerprint(project_root, group["roots"], group["files"])
        require(count == group["expected_file_count"], f"protected file count mismatch: {group['id']}")
        require(total_bytes == group["expected_total_bytes"], f"protected byte count mismatch: {group['id']}")
        require(fingerprint == group["expected_fingerprint"], f"protected fingerprint mismatch: {group['id']}")
        group_results[group["id"]] = {
            "file_count": count,
            "total_bytes": total_bytes,
            "fingerprint": fingerprint,
        }
    return {"individual_files": individual_results, "groups": group_results}


def _split_for_timestamp(timestamp: datetime, splits: list[dict[str, Any]]) -> str:
    matching = [
        item["id"]
        for item in splits
        if parse_utc(item["start_inclusive_utc"]) <= timestamp < parse_utc(item["end_exclusive_utc"])
    ]
    require(len(matching) == 1, f"timestamp is outside exactly one split: {timestamp.isoformat()}")
    return matching[0]


def validate_source_tables(config: dict[str, Any], project_root: Path) -> dict[str, Any]:
    counts: Counter[tuple[str, str, str]] = Counter()
    table_results: dict[str, dict[str, Any]] = {}
    splits = config["splits"]
    expected_headers = {"1h": ONE_HOUR_FIELDS, "4h": FOUR_HOUR_FIELDS}
    for table in config["source_contract"]["canonical_tables"]:
        timeframe = table["timeframe"]
        path = safe_project_path(project_root, table["path"])
        require(path.is_file(), f"canonical table is missing: {table['path']}")
        require(sha256_file(path) == table["sha256"], f"canonical table hash mismatch: {timeframe}")
        row_count = 0
        with path.open(newline="", encoding="utf-8") as handle:
            reader = csv.reader(handle)
            header = next(reader, None)
            require(header == expected_headers[timeframe], f"canonical header mismatch: {timeframe}")
            index = {name: position for position, name in enumerate(header)}
            for physical_row in reader:
                require(len(physical_row) == len(header), f"physical CSV width mismatch: {timeframe} row {row_count + 2}")
                symbol = physical_row[index["symbol"]]
                actual_timeframe = physical_row[index["timeframe"]]
                require(symbol in EXPECTED_ASSETS, f"unexpected asset in {timeframe}: {symbol}")
                require(actual_timeframe == timeframe, f"timeframe field mismatch in {table['path']}")
                timestamp = parse_utc(physical_row[index["timestamp_utc"]])
                split_id = _split_for_timestamp(timestamp, splits)
                counts[(split_id, timeframe, symbol)] += 1
                counts[(split_id, timeframe, "ALL")] += 1
                counts[(split_id, "ALL", "ALL")] += 1
                row_count += 1
        require(row_count == table["row_count"], f"canonical row count mismatch: {timeframe}")
        table_results[timeframe] = {"path": table["path"], "row_count": row_count, "sha256": table["sha256"]}

    split_results: dict[str, Any] = {}
    for split in splits:
        split_id = split["id"]
        actual = {
            "1h": counts[(split_id, "1h", "ALL")],
            "4h": counts[(split_id, "4h", "ALL")],
            "total": counts[(split_id, "ALL", "ALL")],
            "by_asset_timeframe": {
                f"{symbol}|{timeframe}": counts[(split_id, timeframe, symbol)]
                for timeframe in EXPECTED_TIMEFRAMES
                for symbol in EXPECTED_ASSETS
            },
        }
        require({key: actual[key] for key in ["1h", "4h", "total"]} == split["expected_rows"], f"split row counts mismatch: {split_id}")
        split_results[split_id] = actual

    context = config["source_contract"]["context_table"]
    context_path = safe_project_path(project_root, context["path"])
    require(context_path.is_file(), "Coin Metrics context table is missing")
    require(sha256_file(context_path) == context["sha256"], "Coin Metrics context hash mismatch")
    with context_path.open(newline="", encoding="utf-8") as handle:
        reader = csv.reader(handle)
        header = next(reader, None)
        require(header == CONTEXT_FIELDS, "Coin Metrics context header mismatch")
        context_rows = list(reader)
    require(all(len(row) == len(CONTEXT_FIELDS) for row in context_rows), "Coin Metrics physical CSV width mismatch")
    require(len(context_rows) == context["row_count"], "Coin Metrics context row count mismatch")
    require(context_rows[0][0] == "btc", "Coin Metrics context asset must be btc")
    first_d2 = datetime.fromisoformat(context_rows[0][3])
    require(first_d2 <= parse_utc(splits[0]["start_inclusive_utc"]), "D2 context does not cover the first market decision")
    return {
        "canonical_tables": table_results,
        "coinmetrics_context": {"path": context["path"], "row_count": len(context_rows), "sha256": context["sha256"]},
        "split_rows": split_results,
    }


def validate_planned_evaluation_cells(config: dict[str, Any]) -> dict[str, int]:
    dimensions = len(EXPECTED_ASSETS) * len(EXPECTED_TIMEFRAMES) * len(EXPECTED_CONTEXT_VARIANTS) * len(EXPECTED_COSTS)
    horizon_bound = len(EXPECTED_SIGNALS) + 1
    non_horizon_bound = len(EXPECTED_BASELINES) - 1
    primary = dimensions * (horizon_bound + non_horizon_bound)
    additional = dimensions * horizon_bound * 2
    total = primary + additional
    expected = config.get("planned_evaluation_cells", {})
    require(expected.get("primary_horizon") == primary, "primary planned evaluation count mismatch")
    require(expected.get("additional_horizon_sensitivities") == additional, "sensitivity evaluation count mismatch")
    require(expected.get("total_including_horizon_sensitivities") == total, "total planned evaluation count mismatch")
    return {"primary_horizon": primary, "additional_horizon_sensitivities": additional, "total": total}


def run_contract(config_path: Path) -> dict[str, Any]:
    resolved_config = config_path.resolve()
    require(resolved_config.is_file(), f"configuration is missing: {config_path}")
    project_root = resolved_config.parent.parent.resolve()
    require(resolved_config.parent == project_root / "config", "configuration must be directly under project config/")
    config = load_config(resolved_config)
    validate_method_contract(config, project_root)
    protected = validate_protected_artifacts(config, project_root)
    source = validate_source_tables(config, project_root)
    planned = validate_planned_evaluation_cells(config)
    return {
        "status": "PHASE2A_CONTRACT_VALID",
        "phase": config["phase"],
        "gate_2": "NOT_EVALUATED",
        "network_access": False,
        "files_written": 0,
        "features_calculated": 0,
        "signals_calculated": 0,
        "positions_generated": 0,
        "backtests_run": 0,
        "protected_phase1": protected,
        "source_validation": source,
        "planned_evaluation_cells": planned,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Validate the offline Phase-2A backtest preregistration contract.")
    parser.add_argument("--config", required=True, type=Path, help="Project-relative Phase-2A JSON configuration")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = run_contract(args.config)
    except (ContractError, OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"PHASE2A CONTRACT ERROR: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
