"""Fail-closed one-time evaluator for the preregistered 2024-2025 final test."""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import subprocess
import sys
import tempfile
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from src.backtest_contract import sha256_file
from src.backtest_pipeline import (
    BASELINE_IDS,
    CONTEXT_COMPARISON_FIELDS,
    FEATURE_NAMES,
    FEATURE_OUTPUT_FIELDS,
    MANIFEST_FIELDS,
    RESULT_FIELDS,
    SIGNAL_FREQUENCY_FIELDS,
    SIGNAL_IDS,
    TRADE_FIELDS,
    _feature_csv_rows,
    apply_cost,
    build_provenance,
    buy_hold_trades,
    canonical_csv_bytes,
    canonical_json_bytes,
    canonical_time,
    compute_group_features,
    finite_nonnegative,
    finite_positive,
    holdings_for,
    load_json,
    parse_utc,
    periodic_trades,
    phase2a_split_rows,
    publish_bundle,
    read_context,
    safe_path,
    signal_trades,
    split_id,
    summarize_cell,
    validate_cached_provenance,
    validate_phase2b_config,
)


FINAL_SPLIT = "final_test"
FINAL_STATUS_PREPARED = "PREPARED_NOT_EXECUTED"
FINAL_STATUS_COMPLETED = "FINAL_TEST_COMPLETED_EXACTLY_ONCE"
FINAL_STATUS_FAILED = "FINAL_TEST_STARTED_AND_FAILED_CLOSED"


class FinalTestError(RuntimeError):
    """Fail-closed final-test validation or execution error."""


@dataclass(frozen=True)
class FinalTestResult:
    status: str
    input_rows: int
    feature_rows: int
    trade_rows: int
    result_rows: int
    aggregate_result_rows: int
    files: int


def require(condition: bool, message: str) -> None:
    if not condition:
        raise FinalTestError(message)


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _run_git(root: Path, *args: str, check: bool = True) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if check and completed.returncode != 0:
        raise FinalTestError(f"git {' '.join(args)} failed: {completed.stderr.strip()}")
    return completed.stdout.strip()


def _validate_git_state(root: Path, config: Mapping[str, Any]) -> dict[str, Any]:
    branch = _run_git(root, "branch", "--show-current")
    head = _run_git(root, "rev-parse", "HEAD")
    status = _run_git(root, "status", "--porcelain=v1", "--untracked-files=all")
    remotes = _run_git(root, "remote")
    expected_branch = str(config["expected_branch"])
    method_commit = str(config["approved_method_commit"])
    require(branch == expected_branch, f"wrong branch: {branch}")
    require(len(head) == 40, "invalid Git HEAD")
    ancestor = subprocess.run(
        ["git", "merge-base", "--is-ancestor", method_commit, head],
        cwd=root,
        check=False,
        capture_output=True,
    )
    require(ancestor.returncode == 0, "approved method commit is not an ancestor of HEAD")
    restrictions = config["restrictions"]
    if restrictions["require_clean_git"]:
        require(status == "", "Git working tree must be completely clean")
    if restrictions["require_no_remote"]:
        require(remotes == "", "Git remote must remain absent")
    return {"branch": branch, "head": head, "working_tree_clean": status == "", "remote_count": 0 if not remotes else len(remotes.splitlines())}


def _validate_method_files(root: Path, config: Mapping[str, Any]) -> dict[str, str]:
    protected = config["method"]["protected_files"]
    require(isinstance(protected, dict) and protected, "protected method files missing")
    actual: dict[str, str] = {}
    for relative, expected in sorted(protected.items()):
        path = safe_path(root, relative)
        require(path.is_file(), f"protected method file missing: {relative}")
        digest = sha256_file(path)
        require(digest == expected, f"protected method file changed: {relative}")
        actual[relative] = digest
    report = safe_path(root, config["method"]["gate2_acceptance_report"])
    report_text = report.read_text(encoding="utf-8")
    require("**Phase 2B:** `PASS`" in report_text, "Phase 2B acceptance missing")
    require("**Gate 2:** `PASS`" in report_text, "Gate 2 acceptance missing")
    require("Finaler Test: **weiterhin versiegelt und nicht ausgewertet**" in report_text, "accepted report does not preserve the final-test seal")
    return actual


def _validate_phase2b_manifest(root: Path, manifest_path: Path) -> dict[str, Any]:
    require(manifest_path.is_file(), "Phase-2B manifest missing")
    seen: set[str] = set()
    total_bytes = 0
    with manifest_path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        require(reader.fieldnames == list(MANIFEST_FIELDS), "Phase-2B manifest schema mismatch")
        rows = list(reader)
    require(len(rows) == 17, "Phase-2B manifest must contain 17 artifacts")
    for row in rows:
        relative = row["artifact_path"]
        require(relative not in seen, "duplicate Phase-2B manifest path")
        seen.add(relative)
        path = safe_path(root, relative)
        require(path.is_file(), f"Phase-2B artifact missing: {relative}")
        require(sha256_file(path) == row["sha256"], f"Phase-2B artifact hash mismatch: {relative}")
        if path.suffix == ".csv":
            with path.open("r", encoding="utf-8", newline="") as csv_handle:
                row_count = max(sum(1 for _ in csv.reader(csv_handle)) - 1, 0)
            require(row_count == int(row["row_count"]), f"Phase-2B row count mismatch: {relative}")
        total_bytes += path.stat().st_size
    total_bytes += manifest_path.stat().st_size
    return {"manifest_entries": len(rows), "bundle_files": len(rows) + 1, "bundle_total_bytes": total_bytes}


def validate_final_config(config: dict[str, Any], root: Path, *, check_git: bool) -> tuple[dict[str, Any], dict[str, Any]]:
    require(config.get("schema_version") == 1, "final-test schema must equal one")
    require(config.get("phase") == "2C_FINAL_TEST_ONCE_PREPARATION", "wrong final-test phase")
    require(config.get("status") == FINAL_STATUS_PREPARED, "final-test config is not in prepared state")
    restrictions = config.get("restrictions", {})
    require(restrictions.get("historical_data_only") is True, "historical-only restriction missing")
    for field in (
        "network_access", "live_orders", "shorts", "leverage", "funding",
        "machine_learning", "parameter_optimization", "overwrite_existing_outputs",
        "automatic_retry_after_start",
    ):
        require(restrictions.get(field) is False, f"restriction must be false: {field}")
    require(restrictions.get("allowed_hosts") == [], "network allowlist must be empty")
    require(restrictions.get("require_clean_git") is True, "clean Git must be required")
    require(restrictions.get("require_no_remote") is True, "no-remote rule must be required")

    evaluation = config.get("evaluation", {})
    require(evaluation.get("split") == FINAL_SPLIT, "only final_test may be evaluated")
    require(evaluation.get("start_inclusive_utc") == "2024-01-01T00:00:00Z", "wrong final-test start")
    require(evaluation.get("end_exclusive_utc") == "2026-01-01T00:00:00Z", "wrong final-test end")
    require(evaluation.get("expected_rows") == {"1h": 52632, "4h": 13158, "total": 65790}, "wrong final-test row contract")
    require(evaluation.get("contexts") == ["primary_d1", "sensitivity_d2"], "wrong context variants")
    require(evaluation.get("evaluate_all_preregistered_signals") is True, "all signals must remain fixed")
    require(evaluation.get("evaluate_all_preregistered_horizons") is True, "all horizons must remain fixed")
    require(evaluation.get("evaluate_all_preregistered_costs") is True, "all costs must remain fixed")
    require(evaluation.get("allow_post_result_parameter_change") is False, "post-result changes must be forbidden")

    method_files = _validate_method_files(root, config)
    phase2b_path = safe_path(root, config["method"]["phase2b_config"])
    phase2b = load_json(phase2b_path)
    phase2a = validate_phase2b_config(phase2b, root)
    phase2b_provenance = build_provenance(root, phase2b, phase2a)
    validate_cached_provenance(safe_path(root, phase2b["output"]["report_root"]), phase2b_provenance)
    manifest_info = _validate_phase2b_manifest(root, safe_path(root, config["method"]["phase2b_manifest"]))

    final_split = next((item for item in phase2a["splits"] if item["id"] == FINAL_SPLIT), None)
    require(final_split is not None, "final_test split missing from preregistration")
    require(final_split["parameter_selection_allowed"] is False, "final_test may not select parameters")
    require(final_split["evaluate_once_after_method_approval"] is True, "one-time evaluation contract missing")
    require(final_split["expected_rows"] == evaluation["expected_rows"], "final-test row expectations differ from preregistration")

    output = config["output"]
    data_root = safe_path(root, output["data_root"])
    report_root = safe_path(root, output["report_root"])
    state_path = safe_path(root, output["state_path"])
    receipt_path = safe_path(root, output["receipt_path"])
    require(data_root != report_root and data_root not in report_root.parents and report_root not in data_root.parents, "final output roots overlap")
    require(not data_root.exists(), "final-test data output already exists")
    require(not report_root.exists(), "final-test report output already exists")
    require(not state_path.exists(), "final-test start state already exists; automatic retry forbidden")
    require(not receipt_path.exists(), "final-test receipt already exists")
    part_files = [path for path in root.rglob("*.part") if path.is_file()]
    require(not part_files, "part files exist before final-test execution")

    git_info = _validate_git_state(root, config) if check_git else {}
    return phase2a, {
        "status": "FINAL_TEST_PREFLIGHT_VALID",
        "method_commit": config["approved_method_commit"],
        "method_file_count": len(method_files),
        "phase2b_manifest": manifest_info,
        "expected_final_rows": evaluation["expected_rows"],
        "git": git_info,
        "writes": 0,
        "features_evaluated": 0,
        "trades_evaluated": 0,
        "metrics_evaluated": 0,
    }


def read_final_market_rows(root: Path, phase2a: dict[str, Any]) -> tuple[dict[str, list[dict[str, Any]]], dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    counts: Counter[tuple[str, str]] = Counter()
    assets = set(phase2a["market"]["assets"])
    excluded = set(phase2a["segment_policy"]["excluded_months"])
    primary_keys: set[tuple[str, str, str]] = set()
    availability: Counter[tuple[str, str]] = Counter()
    segments: set[str] = set()

    for table in phase2a["source_contract"]["canonical_tables"]:
        timeframe = table["timeframe"]
        path = safe_path(root, table["path"])
        require(sha256_file(path) == table["sha256"], f"processed {timeframe} hash mismatch")
        with path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            require(reader.fieldnames is not None, f"missing market header: {timeframe}")
            required = {
                "symbol", "timeframe", "timestamp_utc", "decision_time_utc", "segment_id",
                "open", "high", "low", "close", "volume", "context_available_from_utc_d1",
            }
            require(required <= set(reader.fieldnames), f"market schema missing fields: {timeframe}")
            if timeframe == "1h":
                require("taker_buy_base_volume" in reader.fieldnames, "1h taker field missing")
            physical_rows = 0
            for physical, raw in enumerate(reader, 2):
                require(None not in raw and None not in raw.values(), f"market width error {timeframe}:{physical}")
                symbol = raw["symbol"]
                require(symbol in assets and raw["timeframe"] == timeframe, "market key mismatch")
                timestamp = parse_utc(raw["timestamp_utc"])
                decision = parse_utc(raw["decision_time_utc"])
                bar_hours = next(item["bar_hours"] for item in phase2a["timeframes"] if item["id"] == timeframe)
                require(decision.timestamp() - timestamp.timestamp() == bar_hours * 3600, "decision time mismatch")
                require(timestamp.strftime("%Y-%m") not in excluded, "excluded month reached final test")
                key = (symbol, timeframe, canonical_time(timestamp))
                require(key not in primary_keys, "duplicate market primary key")
                primary_keys.add(key)
                split = split_id(timestamp, phase2a)
                counts[(split, timeframe)] += 1
                availability[(timeframe, canonical_time(timestamp))] += 1
                physical_rows += 1
                open_price = finite_positive(raw["open"], "open")
                high = finite_positive(raw["high"], "high")
                low = finite_positive(raw["low"], "low")
                close = finite_positive(raw["close"], "close")
                require(high >= max(open_price, close) and low <= min(open_price, close), "OHLC relation error")
                segment = raw["segment_id"]
                if split == FINAL_SPLIT:
                    segments.add(segment)
                groups[f"{symbol}|{timeframe}|{segment}"].append({
                    "split": split,
                    "symbol": symbol,
                    "timeframe": timeframe,
                    "timestamp": timestamp,
                    "timestamp_utc": canonical_time(timestamp),
                    "decision": decision,
                    "decision_time_utc": canonical_time(decision),
                    "segment_id": segment,
                    "open": open_price,
                    "high": high,
                    "low": low,
                    "close": close,
                    "volume": finite_nonnegative(raw["volume"], "volume"),
                    "taker_buy_base_volume": finite_nonnegative(raw.get("taker_buy_base_volume", "0") or "0", "taker_buy_base_volume"),
                })
            require(physical_rows == table["row_count"], f"processed {timeframe} row count mismatch")

    for group_key, rows in groups.items():
        rows.sort(key=lambda row: row["timestamp"])
        hours = 1 if rows[0]["timeframe"] == "1h" else 4
        for left, right in zip(rows, rows[1:]):
            require((right["timestamp"] - left["timestamp"]).total_seconds() == hours * 3600, f"gap inside final segment: {group_key}")
    require(all(value == len(assets) for value in availability.values()), "common three-asset availability mask violated")
    actual = {split: {tf: counts[(split, tf)] for tf in ("1h", "4h")} for split in ("development", "validation", FINAL_SPLIT)}
    for split, expected in phase2a_split_rows(phase2a).items():
        require(actual[split]["1h"] == expected["1h"] and actual[split]["4h"] == expected["4h"], f"split count mismatch: {split}")
    final_rows = actual[FINAL_SPLIT]["1h"] + actual[FINAL_SPLIT]["4h"]
    require(final_rows == 65790, "final-test input row count mismatch")
    return groups, {"split_rows": actual, "final_input_rows": final_rows, "final_segment_count": len(segments), "primary_key_count": len(primary_keys)}


def build_final_evaluation(feature_groups: Mapping[tuple[str, str], Sequence[dict[str, Any]]], phase2a: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    base_trades: list[dict[str, Any]] = []
    signal_counts_accumulator: dict[tuple[str, ...], Counter[str]] = defaultdict(Counter)

    for (_, _), rows in sorted(feature_groups.items()):
        require(rows and all(row["split"] == FINAL_SPLIT for row in rows), "non-final row reached final evaluation")
        symbol = rows[0]["symbol"]
        timeframe = rows[0]["timeframe"]
        context = rows[0]["context_variant"]
        for holding in holdings_for(timeframe, phase2a):
            for signal in SIGNAL_IDS:
                trades, counts = signal_trades(rows, signal, holding)
                base_trades.extend(trades)
                signal_counts_accumulator[(FINAL_SPLIT, symbol, timeframe, context, signal, str(holding))].update(counts)
            periodic, periodic_counts = periodic_trades(rows, holding)
            base_trades.extend(periodic)
            signal_counts_accumulator[(FINAL_SPLIT, symbol, timeframe, context, "periodic_entry_baseline", str(holding))].update(periodic_counts)
        base_trades.extend(buy_hold_trades(rows))

    signal_counts = {key: dict(value) for key, value in signal_counts_accumulator.items()}
    frequencies = [
        {
            "split": key[0], "symbol": key[1], "timeframe": key[2], "context_variant": key[3],
            "signal_id": key[4], "holding_bars": int(key[5]), **counts,
        }
        for key, counts in signal_counts.items() if key[4] in SIGNAL_IDS
    ]
    cost_trades = [apply_cost(trade, scenario) for trade in base_trades for scenario in phase2a["costs"]["scenarios"]]
    require(all(trade["split"] == FINAL_SPLIT for trade in cost_trades), "non-final trade produced")

    grouped: dict[tuple[str, ...], list[dict[str, Any]]] = defaultdict(list)
    for trade in cost_trades:
        key = (
            trade["split"], trade["symbol"], trade["timeframe"], trade["context_variant"],
            trade["strategy_type"], trade["strategy_id"], str(trade["holding_bars"] or ""), trade["cost_scenario"],
        )
        grouped[key].append(trade)

    for symbol in phase2a["market"]["assets"]:
        for timeframe in ("1h", "4h"):
            for context in ("primary_d1", "sensitivity_d2"):
                for cost in [item["id"] for item in phase2a["costs"]["scenarios"]]:
                    for strategy in (*SIGNAL_IDS, "periodic_entry_baseline"):
                        strategy_type = "signal" if strategy in SIGNAL_IDS else "baseline"
                        for holding in holdings_for(timeframe, phase2a):
                            grouped.setdefault((FINAL_SPLIT, symbol, timeframe, context, strategy_type, strategy, str(holding), cost), [])
                    for strategy in ("always_flat", "segment_buy_and_hold"):
                        grouped.setdefault((FINAL_SPLIT, symbol, timeframe, context, "baseline", strategy, "", cost), [])

    results = [summarize_cell(key, sorted(rows, key=lambda row: row["entry_time_utc"]), signal_counts) for key, rows in sorted(grouped.items())]
    require(len(results) == 720, "final-test detail result cell count mismatch")

    aggregate_grouped: dict[tuple[str, ...], list[dict[str, Any]]] = defaultdict(list)
    for trade in cost_trades:
        key = (
            FINAL_SPLIT, "ALL", trade["timeframe"], trade["context_variant"], trade["strategy_type"],
            trade["strategy_id"], str(trade["holding_bars"] or ""), trade["cost_scenario"],
        )
        aggregate_grouped[key].append(trade)
    aggregate_counts: dict[tuple[str, ...], Counter[str]] = defaultdict(Counter)
    for key, counts in signal_counts.items():
        aggregate_counts[(FINAL_SPLIT, "ALL", key[2], key[3], key[4], key[5])].update(counts)
    for timeframe in ("1h", "4h"):
        for context in ("primary_d1", "sensitivity_d2"):
            for cost in [item["id"] for item in phase2a["costs"]["scenarios"]]:
                for strategy in (*SIGNAL_IDS, "periodic_entry_baseline"):
                    strategy_type = "signal" if strategy in SIGNAL_IDS else "baseline"
                    for holding in holdings_for(timeframe, phase2a):
                        aggregate_grouped.setdefault((FINAL_SPLIT, "ALL", timeframe, context, strategy_type, strategy, str(holding), cost), [])
                for strategy in ("always_flat", "segment_buy_and_hold"):
                    aggregate_grouped.setdefault((FINAL_SPLIT, "ALL", timeframe, context, "baseline", strategy, "", cost), [])
    aggregate_results = [
        summarize_cell(key, sorted(rows, key=lambda row: (row["entry_time_utc"], row["symbol"])), {k: dict(v) for k, v in aggregate_counts.items()})
        for key, rows in sorted(aggregate_grouped.items())
    ]
    require(len(aggregate_results) == 240, "final-test aggregate result cell count mismatch")
    for row in aggregate_results:
        row["cumulative_net_return"] = None
        row["maximum_drawdown"] = None
        row["uncertainty_note"] = "pooled_assets_descriptive_only_no_shared_capital_curve_no_iid_claim"
    return cost_trades, frequencies, results, aggregate_results


def _write_bytes(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


def _bundle_snapshot(data_root: Path, report_root: Path) -> str:
    lines: list[str] = []
    for base, prefix in ((data_root, "data"), (report_root, "reports")):
        for path in sorted(item for item in base.rglob("*") if item.is_file()):
            lines.append(f"{prefix}/{path.relative_to(base).as_posix()}|{path.stat().st_size}|{sha256_file(path)}")
    return sha256(("\n".join(lines) + "\n").encode("utf-8")).hexdigest()


def _manifest_rows(data_root: Path, report_root: Path, output: Mapping[str, str]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for base, prefix in ((data_root, output["data_root"]), (report_root, output["report_root"])):
        for path in sorted(item for item in base.rglob("*") if item.is_file()):
            if path.name == "final_test_manifest.csv":
                continue
            row_count = 0
            if path.suffix == ".csv":
                with path.open("r", encoding="utf-8", newline="") as handle:
                    row_count = max(sum(1 for _ in csv.reader(handle)) - 1, 0)
            rows.append({
                "artifact_path": f"{prefix}/{path.relative_to(base).as_posix()}",
                "artifact_type": path.suffix.lstrip(".") or "file",
                "row_count": row_count,
                "sha256": sha256_file(path),
            })
    return rows


def generate_final_bundle(
    temp_data: Path,
    temp_reports: Path,
    groups: Mapping[str, Sequence[dict[str, Any]]],
    context_rows: Sequence[dict[str, Any]],
    phase2a: dict[str, Any],
    input_quality: Mapping[str, Any],
    root: Path,
    config: Mapping[str, Any],
    git_info: Mapping[str, Any],
) -> dict[str, Any]:
    feature_groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    by_file: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    null_counts: Counter[tuple[str, str]] = Counter()
    feature_rows = 0
    for group_key, market_rows in sorted(groups.items()):
        for variant in ("primary_d1", "sensitivity_d2"):
            all_features = compute_group_features(market_rows, context_rows, variant)
            features = [row for row in all_features if row["split"] == FINAL_SPLIT]
            if not features:
                continue
            feature_groups[(group_key, variant)] = features
            feature_rows += len(features)
            by_file[(features[0]["timeframe"], variant)].extend(features)
            for row in features:
                for name in FEATURE_NAMES:
                    if row[name] is None:
                        null_counts[(row["timeframe"], name)] += 1
    require(feature_rows == 131580, "final-test feature row count mismatch")

    for (timeframe, variant), rows in sorted(by_file.items()):
        rows.sort(key=lambda row: (row["symbol"], row["timestamp_utc"]))
        _write_bytes(temp_data / f"features_{timeframe}_{variant}.csv", canonical_csv_bytes(FEATURE_OUTPUT_FIELDS, _feature_csv_rows(rows)))

    trades, frequencies, results, aggregate_results = build_final_evaluation(feature_groups, phase2a)
    trades.sort(key=lambda row: tuple(str(row.get(field, "")) for field in ("split", "symbol", "timeframe", "context_variant", "strategy_id", "holding_bars", "cost_scenario", "entry_time_utc")))
    frequencies.sort(key=lambda row: tuple(str(row.get(field, "")) for field in SIGNAL_FREQUENCY_FIELDS[:6]))
    results.sort(key=lambda row: tuple(str(row.get(field, "")) for field in RESULT_FIELDS[:8]))
    aggregate_results.sort(key=lambda row: tuple(str(row.get(field, "")) for field in RESULT_FIELDS[:8]))

    _write_bytes(temp_data / "trades.csv", canonical_csv_bytes(TRADE_FIELDS, trades))
    _write_bytes(temp_reports / "signal_frequency_summary.csv", canonical_csv_bytes(SIGNAL_FREQUENCY_FIELDS, frequencies))
    _write_bytes(temp_reports / "results_summary.csv", canonical_csv_bytes(RESULT_FIELDS, results))
    _write_bytes(temp_reports / "aggregate_results_summary.csv", canonical_csv_bytes(RESULT_FIELDS, aggregate_results))
    _write_bytes(temp_reports / "baseline_comparison.csv", canonical_csv_bytes(RESULT_FIELDS, [row for row in results if row["strategy_type"] == "baseline"]))

    indexed = {
        (row["split"], row["symbol"], row["timeframe"], row["strategy_type"], row["strategy_id"], str(row["holding_bars"] or ""), row["cost_scenario"], row["context_variant"]): row
        for row in results
    }
    context_comparison: list[dict[str, Any]] = []
    for base in sorted({key[:-1] for key in indexed}):
        d1 = indexed[(*base, "primary_d1")]
        d2 = indexed[(*base, "sensitivity_d2")]
        context_comparison.append({
            "split": base[0], "symbol": base[1], "timeframe": base[2], "strategy_type": base[3],
            "strategy_id": base[4], "holding_bars": int(base[5]) if base[5] else None,
            "cost_scenario": base[6], "d1_trade_count": d1["trade_count"], "d2_trade_count": d2["trade_count"],
            "trade_count_delta": d2["trade_count"] - d1["trade_count"],
            "d1_average_net_return": d1["average_net_return"], "d2_average_net_return": d2["average_net_return"],
            "average_net_return_delta": (d2["average_net_return"] - d1["average_net_return"]) if d1["average_net_return"] is not None and d2["average_net_return"] is not None else None,
        })
    _write_bytes(temp_reports / "context_variant_comparison.csv", canonical_csv_bytes(CONTEXT_COMPARISON_FIELDS, context_comparison))

    phase2b_config = load_json(root / "config/backtest_phase2b.json")
    phase2b_provenance = build_provenance(root, phase2b_config, phase2a)
    provenance = {
        "approved_method_commit": config["approved_method_commit"],
        "execution_head": git_info["head"],
        "final_runner_sha256": sha256_file(root / "src/final_test_once.py"),
        "final_config_sha256": sha256_file(root / "config/final_test_once.json"),
        "phase2b_provenance": phase2b_provenance,
        "protected_method_files": config["method"]["protected_files"],
        "parameter_changes_after_gate2": 0,
        "network_access": False,
    }
    quality = {
        "status": FINAL_STATUS_COMPLETED,
        "split": FINAL_SPLIT,
        "input_rows": input_quality["final_input_rows"],
        "feature_rows": feature_rows,
        "trade_rows": len(trades),
        "result_rows": len(results),
        "aggregate_result_rows": len(aggregate_results),
        "feature_count": len(FEATURE_NAMES),
        "signal_count": len(SIGNAL_IDS),
        "baseline_count": len(BASELINE_IDS),
        "contexts": ["primary_d1", "sensitivity_d2"],
        "cost_scenarios_bps": [20, 30, 50],
        "future_context_violations": 0,
        "post_result_parameter_changes": 0,
        "interpretation_status": "NOT_YET_INTERPRETED",
        "null_counts_by_timeframe_feature": {f"{key[0]}|{key[1]}": value for key, value in sorted(null_counts.items())},
    }
    _write_bytes(temp_reports / "final_test_quality_summary.json", canonical_json_bytes(quality))
    _write_bytes(temp_reports / "input_output_hashes.json", canonical_json_bytes({"provenance": provenance, "outputs_recorded_in": "final_test_manifest.csv"}))
    report = f"""# Finaler Test 2024–2025 – technischer Laufbericht\n\nStatus: **{FINAL_STATUS_COMPLETED}**\n\nDer vorregistrierte finale Test wurde mit dem nach Gate 2 festgeschriebenen Methodenstand genau einmal berechnet. Dieser Bericht enthält noch keine nachträgliche Auswahl oder Interpretation.\n\n- Eingabezeilen: {input_quality['final_input_rows']}\n- Featurezeilen D+1 und D+2 zusammen: {feature_rows}\n- Trade-/Kostenzeilen: {len(trades)}\n- Detailergebniszellen: {len(results)}\n- Aggregierte Ergebniszellen: {len(aggregate_results)}\n- Methoden-Commit: `{config['approved_method_commit']}`\n- Ausführungs-HEAD: `{git_info['head']}`\n- Parameteränderungen nach Gate 2: 0\n- Netzwerkzugriff, Live-Orders, Short, Hebel, Funding und ML: deaktiviert\n\nDie Ergebnisse sind historische Evidenz und keine Garantie oder Handlungsempfehlung.\n"""
    _write_bytes(temp_reports / "FINAL_TEST_QUALITY_REPORT.md", report.encode("utf-8"))

    manifest_rows = _manifest_rows(temp_data, temp_reports, config["output"])
    _write_bytes(temp_reports / "final_test_manifest.csv", canonical_csv_bytes(MANIFEST_FIELDS, manifest_rows))
    return {
        "input_rows": input_quality["final_input_rows"],
        "feature_rows": feature_rows,
        "trade_rows": len(trades),
        "result_rows": len(results),
        "aggregate_result_rows": len(aggregate_results),
        "files": len(manifest_rows) + 1,
    }


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.parent / f".{path.name}.{os.getpid()}.tmp"
    try:
        temp.write_bytes(canonical_json_bytes(payload))
        os.replace(temp, path)
    finally:
        if temp.exists():
            temp.unlink()


def _create_start_state(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("xb") as handle:
            handle.write(canonical_json_bytes(payload))
    except FileExistsError as exc:
        raise FinalTestError("final-test start state already exists; automatic retry forbidden") from exc


def preflight(config_path: Path, *, check_git: bool = True) -> dict[str, Any]:
    resolved = config_path.resolve()
    require(resolved.is_file(), "final-test config missing")
    root = resolved.parent.parent.resolve()
    require(resolved.parent == root / "config", "config must be directly under project config")
    config = load_json(resolved)
    _, summary = validate_final_config(config, root, check_git=check_git)
    return summary


def run_once(config_path: Path, confirmation: str) -> FinalTestResult:
    resolved = config_path.resolve()
    require(resolved.is_file(), "final-test config missing")
    root = resolved.parent.parent.resolve()
    config = load_json(resolved)
    require(confirmation == config.get("confirmation_token"), "exact one-time confirmation token required")
    phase2a, preflight_summary = validate_final_config(config, root, check_git=True)
    git_info = preflight_summary["git"]
    output = config["output"]
    data_root = safe_path(root, output["data_root"])
    report_root = safe_path(root, output["report_root"])
    state_path = safe_path(root, output["state_path"])
    receipt_path = safe_path(root, output["receipt_path"])
    started = utc_now()
    start_payload = {
        "status": "FINAL_TEST_STARTED_EXACTLY_ONCE",
        "started_at_utc": started,
        "approved_method_commit": config["approved_method_commit"],
        "execution_head": git_info["head"],
        "automatic_retry_allowed": False,
    }
    _create_start_state(state_path, start_payload)
    try:
        context = read_context(root, phase2a)
        groups, input_quality = read_final_market_rows(root, phase2a)
        with tempfile.TemporaryDirectory(prefix="final-test-once-", dir=root) as temporary:
            temp_root = Path(temporary)
            temp_data = temp_root / "data"
            temp_reports = temp_root / "reports"
            temp_data.mkdir()
            temp_reports.mkdir()
            summary = generate_final_bundle(temp_data, temp_reports, groups, context, phase2a, input_quality, root, config, git_info)
            publish_bundle(temp_data, temp_reports, data_root, report_root)
        completed = utc_now()
        bundle_snapshot = _bundle_snapshot(data_root, report_root)
        receipt = {
            "status": FINAL_STATUS_COMPLETED,
            "started_at_utc": started,
            "completed_at_utc": completed,
            "approved_method_commit": config["approved_method_commit"],
            "execution_head": git_info["head"],
            "final_config_sha256": sha256_file(resolved),
            "final_runner_sha256": sha256_file(root / "src/final_test_once.py"),
            "bundle_snapshot_sha256": bundle_snapshot,
            "summary": summary,
            "automatic_retry_allowed": False,
            "post_result_parameter_changes_allowed": False,
        }
        _atomic_json(receipt_path, receipt)
        _atomic_json(state_path, receipt)
        return FinalTestResult(FINAL_STATUS_COMPLETED, summary["input_rows"], summary["feature_rows"], summary["trade_rows"], summary["result_rows"], summary["aggregate_result_rows"], summary["files"])
    except Exception as exc:
        failure = {
            **start_payload,
            "status": FINAL_STATUS_FAILED,
            "failed_at_utc": utc_now(),
            "error_type": type(exc).__name__,
            "error": str(exc),
            "automatic_retry_allowed": False,
        }
        try:
            _atomic_json(state_path, failure)
        finally:
            pass
        raise


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Preflight or execute the preregistered final test exactly once.")
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--execute", action="store_true", help="Irreversibly start the one-time final test.")
    parser.add_argument("--confirm", default="", help="Exact confirmation token required with --execute.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.execute:
            result = run_once(args.config, args.confirm)
            payload: Mapping[str, Any] = result.__dict__
        else:
            require(args.confirm == "", "--confirm is only allowed with --execute")
            payload = preflight(args.config, check_git=True)
    except (FinalTestError, OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"FINAL TEST ERROR: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
