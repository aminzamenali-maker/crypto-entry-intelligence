"""Offline Phase-2B feature, signal and conservative long/flat backtest core."""

from __future__ import annotations

import argparse
import bisect
import csv
import io
import json
import math
import os
import statistics
import sys
import tempfile
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from src.backtest_contract import group_fingerprint, run_contract, sha256_file


FEATURE_NAMES = (
    "past_return_1", "past_return_4", "past_return_12", "past_return_24",
    "sma_20", "sma_50", "sma_ratio_20_50", "rsi_14", "atr_14_relative",
    "rolling_volatility_24", "rolling_volatility_72", "volume_zscore_24",
    "candle_range_relative", "candle_body_relative", "taker_buy_share_1h",
    "prior_high_20_shifted", "close_to_sma20_distance", "context_price_usd",
    "context_market_cap_usd", "context_tx_count", "context_active_address_count",
    "context_price_usd_change", "context_market_cap_usd_change",
    "context_tx_count_change", "context_active_address_count_change",
)
SIGNAL_IDS = (
    "trend_sma20_cross_above_sma50", "momentum_return_12_positive",
    "breakout_close_above_prior_high_20", "mean_reversion_rsi14_below_30",
    "mean_reversion_close_2pct_below_sma20",
)
BASELINE_IDS = ("always_flat", "segment_buy_and_hold", "periodic_entry_baseline")
CONTEXT_VALUE_FIELDS = (
    "context_price_usd", "context_market_cap_usd", "context_tx_count",
    "context_active_address_count",
)
CONTEXT_SOURCE_FIELDS = ("PriceUSD", "CapMrktCurUSD", "TxCnt", "AdrActCnt")
CONTEXT_CHANGE_FIELDS = tuple(f"{field}_change" for field in CONTEXT_VALUE_FIELDS)
FEATURE_OUTPUT_FIELDS = (
    "split", "symbol", "timeframe", "timestamp_utc", "decision_time_utc",
    "segment_id", "context_variant", "context_source_timestamp_utc",
    "context_available_from_utc", "open", "high", "low", "close", "volume",
    *FEATURE_NAMES, *SIGNAL_IDS,
)
TRADE_FIELDS = (
    "split", "symbol", "timeframe", "context_variant", "strategy_type",
    "strategy_id", "holding_bars", "cost_scenario", "segment_id",
    "signal_time_utc", "entry_time_utc", "exit_time_utc", "entry_open",
    "exit_open", "gross_return", "net_return", "positive_net_outcome",
    "maximum_adverse_excursion", "maximum_favorable_excursion", "exposure_hours",
)
RESULT_FIELDS = (
    "split", "symbol", "timeframe", "context_variant", "strategy_type",
    "strategy_id", "holding_bars", "cost_scenario", "signal_count",
    "executable_signal_count", "rejected_signal_count", "trade_count", "hit_rate",
    "average_gross_return", "median_gross_return", "average_net_return",
    "median_net_return", "cumulative_net_return", "trade_return_volatility",
    "maximum_drawdown", "profit_factor", "profit_factor_status", "cost_burden",
    "exposure_hours", "covered_segment_count", "segment_mean_min",
    "segment_mean_median", "segment_mean_max", "positive_segment_share",
    "uncertainty_note",
)
SIGNAL_FREQUENCY_FIELDS = (
    "split", "symbol", "timeframe", "context_variant", "signal_id", "holding_bars",
    "signal_count", "executable_signal_count", "rejected_boundary_count",
    "rejected_overlap_count",
)
MANIFEST_FIELDS = ("artifact_path", "artifact_type", "row_count", "sha256")
CONTEXT_COMPARISON_FIELDS = (
    "split", "symbol", "timeframe", "strategy_type", "strategy_id", "holding_bars",
    "cost_scenario", "d1_trade_count", "d2_trade_count", "trade_count_delta",
    "d1_average_net_return", "d2_average_net_return", "average_net_return_delta",
)
IMPLEMENTATION_POLICY_ID = "phase2b_fsum_float17_provenance_v2"
FLOAT_SERIALIZATION_RULE = ".17g"
SMA_CALCULATION_RULE = "math.fsum(window)/window_length"


class Phase2BError(RuntimeError):
    """Fail-closed Phase-2B validation or execution error."""


@dataclass(frozen=True)
class Phase2BResult:
    status: str
    feature_rows: int
    trade_rows: int
    result_rows: int
    sealed_test_rows_recognized: int
    files: int


def require(condition: bool, message: str) -> None:
    if not condition:
        raise Phase2BError(message)


def parse_utc(value: str) -> datetime:
    normalized = value.strip().replace("Z", "+00:00")
    parsed = datetime.fromisoformat(normalized)
    require(parsed.tzinfo is not None and parsed.utcoffset() == timezone.utc.utcoffset(parsed), f"not UTC: {value}")
    return parsed.astimezone(timezone.utc)


def canonical_time(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def float_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, int):
        return str(value)
    numeric = float(value)
    require(math.isfinite(numeric), "non-finite output value")
    return format(numeric, FLOAT_SERIALIZATION_RULE)


def canonical_json_bytes(payload: Any) -> bytes:
    return (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n").encode("utf-8")


def canonical_csv_bytes(fields: Sequence[str], rows: Iterable[Mapping[str, Any]]) -> bytes:
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=list(fields), lineterminator="\n", extrasaction="raise")
    writer.writeheader()
    for row in rows:
        writer.writerow({field: float_text(row.get(field)) if isinstance(row.get(field), (float, int, bool)) or row.get(field) is None else row.get(field) for field in fields})
    return buffer.getvalue().encode("utf-8")


def load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    require(isinstance(value, dict), f"JSON root is not an object: {path}")
    return value


def safe_path(root: Path, relative: str) -> Path:
    require(relative and not Path(relative).is_absolute(), f"unsafe path: {relative}")
    resolved = (root / relative).resolve()
    require(resolved == root or root in resolved.parents, f"path escapes project: {relative}")
    return resolved


def validate_phase2b_config(config: dict[str, Any], root: Path) -> dict[str, Any]:
    require(config.get("schema_version") == 1, "Phase-2B schema must equal one")
    require(config.get("phase") == "2B_OFFLINE_CORE_IMPLEMENTATION", "wrong phase")
    require(config.get("gate_2_status") == "NOT_EVALUATED", "Gate 2 must stay NOT_EVALUATED")
    policy = config.get("policy", {})
    require(policy.get("evaluated_splits") == ["development", "validation"], "only development and validation may be evaluated")
    require(policy.get("sealed_split") == "final_test", "final_test must remain sealed")
    require(policy.get("cache_rule") == "complete_byte_identical_bundle_or_fail_closed", "cache policy mismatch")
    require(policy.get("implementation_policy_id") == IMPLEMENTATION_POLICY_ID, "implementation policy mismatch")
    require(policy.get("float_serialization") == FLOAT_SERIALIZATION_RULE, "float serialization policy mismatch")
    require(policy.get("sma_calculation_rule") == SMA_CALCULATION_RULE, "SMA calculation policy mismatch")
    restrictions = config.get("restrictions", {})
    require(restrictions.get("historical_data_only") is True, "historical-only restriction missing")
    for field in ("network_access", "evaluate_final_test", "live_orders", "shorts", "leverage", "funding", "machine_learning", "parameter_optimization", "overwrite_existing_outputs"):
        require(restrictions.get(field) is False, f"restriction must be false: {field}")
    require(restrictions.get("allowed_hosts") == [], "network allowlist must be empty")
    phase2a_path = safe_path(root, config["phase2a_contract_path"])
    require(sha256_file(phase2a_path) == config["phase2a_contract_sha256"], "Phase-2A contract hash mismatch")
    phase2a = load_json(phase2a_path)
    require(config.get("expected_split_rows") == phase2a_split_rows(phase2a), "Phase-2B split expectations differ from preregistration")
    run_contract(phase2a_path)
    gate1_path = safe_path(root, config["gate1_evidence_path"])
    gate1_text = gate1_path.read_text(encoding="utf-8")
    require("**Gesamtstatus Gate 1: `PASS_WITH_DOCUMENTED_SOURCE_ANOMALIES`.**" in gate1_text, "Gate 1 evidence is not accepted")
    protected = config["protected_phase2a_evidence"]
    actual = group_fingerprint(root, [], protected["files"])
    expected = (protected["expected_file_count"], protected["expected_total_bytes"], protected["expected_fingerprint"])
    require(actual == expected, "Phase-2A evidence fingerprint mismatch")
    data_root = safe_path(root, config["output"]["data_root"])
    report_root = safe_path(root, config["output"]["report_root"])
    require(config["output"]["data_root"] == "data/processed/phase2b", "unexpected Phase-2B data root")
    require(config["output"]["report_root"] == "reports/backtest/phase2b_outputs", "unexpected Phase-2B report root")
    require(data_root != report_root and data_root not in report_root.parents and report_root not in data_root.parents, "output roots overlap")
    return phase2a


def split_id(timestamp: datetime, phase2a: dict[str, Any]) -> str:
    matches = [s["id"] for s in phase2a["splits"] if parse_utc(s["start_inclusive_utc"]) <= timestamp < parse_utc(s["end_exclusive_utc"])]
    require(len(matches) == 1, f"timestamp is outside split contract: {timestamp}")
    return matches[0]


def finite_positive(value: str, field: str) -> float:
    numeric = float(value)
    require(math.isfinite(numeric) and numeric > 0, f"invalid positive {field}")
    return numeric


def finite_nonnegative(value: str, field: str) -> float:
    numeric = float(value)
    require(math.isfinite(numeric) and numeric >= 0, f"invalid nonnegative {field}")
    return numeric


def read_context(root: Path, phase2a: dict[str, Any]) -> list[dict[str, Any]]:
    item = phase2a["source_contract"]["context_table"]
    path = safe_path(root, item["path"])
    require(sha256_file(path) == item["sha256"], "context hash mismatch")
    rows: list[dict[str, Any]] = []
    expected = ["asset", "source_timestamp_utc", "available_from_utc_d1", "available_from_utc_d2", *CONTEXT_SOURCE_FIELDS]
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        require(reader.fieldnames == expected, "context schema mismatch")
        previous: datetime | None = None
        for physical, row in enumerate(reader, 2):
            require(None not in row and None not in row.values(), f"context width error at row {physical}")
            source = parse_utc(row["source_timestamp_utc"])
            require(previous is None or source > previous, "context timestamps not strictly increasing")
            previous = source
            parsed = {"asset": row["asset"], "source_timestamp": source}
            require(row["asset"] == "btc", "unexpected context asset")
            for variant, field in (("primary_d1", "available_from_utc_d1"), ("sensitivity_d2", "available_from_utc_d2")):
                parsed[variant] = parse_utc(row[field])
                require(parsed[variant] > source, "context availability must follow source date")
            for source_field, output_field in zip(CONTEXT_SOURCE_FIELDS, CONTEXT_VALUE_FIELDS):
                parsed[output_field] = finite_nonnegative(row[source_field], source_field)
            rows.append(parsed)
    require(len(rows) == item["row_count"], "context row count mismatch")
    return rows


def read_market_rows(root: Path, phase2a: dict[str, Any]) -> tuple[dict[str, list[dict[str, Any]]], dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    counts: Counter[tuple[str, str]] = Counter()
    assets = set(phase2a["market"]["assets"])
    excluded = set(phase2a["segment_policy"]["excluded_months"])
    primary_keys: set[tuple[str, str, str]] = set()
    segment_ids: set[str] = set()
    availability: Counter[tuple[str, str]] = Counter()
    segment_by_timestamp: dict[tuple[str, str], set[str]] = defaultdict(set)
    for table in phase2a["source_contract"]["canonical_tables"]:
        timeframe = table["timeframe"]
        path = safe_path(root, table["path"])
        require(sha256_file(path) == table["sha256"], f"processed {timeframe} hash mismatch")
        with path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            require(reader.fieldnames is not None, f"missing market header: {timeframe}")
            required = {"symbol", "timeframe", "timestamp_utc", "decision_time_utc", "segment_id", "open", "high", "low", "close", "volume", "context_available_from_utc_d1"}
            require(required <= set(reader.fieldnames), f"market schema missing fields: {timeframe}")
            if timeframe == "1h":
                require("taker_buy_base_volume" in reader.fieldnames, "1h taker field missing")
            row_count = 0
            for physical, raw in enumerate(reader, 2):
                require(None not in raw and None not in raw.values(), f"market width error {timeframe}:{physical}")
                symbol = raw["symbol"]
                require(symbol in assets and raw["timeframe"] == timeframe, "market key mismatch")
                timestamp = parse_utc(raw["timestamp_utc"])
                decision = parse_utc(raw["decision_time_utc"])
                hours = next(item["bar_hours"] for item in phase2a["timeframes"] if item["id"] == timeframe)
                require(decision.timestamp() - timestamp.timestamp() == hours * 3600, "decision time mismatch")
                require(timestamp.strftime("%Y-%m") not in excluded, "excluded month reached Phase 2B")
                key = (symbol, timeframe, canonical_time(timestamp))
                require(key not in primary_keys, "duplicate market primary key")
                primary_keys.add(key)
                split = split_id(timestamp, phase2a)
                counts[(split, timeframe)] += 1
                segment_ids.add(raw["segment_id"])
                availability[(timeframe, canonical_time(timestamp))] += 1
                segment_by_timestamp[(timeframe, canonical_time(timestamp))].add(raw["segment_id"])
                row_count += 1
                if split == "final_test":
                    continue
                open_price = finite_positive(raw["open"], "open")
                high = finite_positive(raw["high"], "high")
                low = finite_positive(raw["low"], "low")
                close = finite_positive(raw["close"], "close")
                require(high >= max(open_price, close) and low <= min(open_price, close), "OHLC relation error")
                row = {
                    "split": split, "symbol": symbol, "timeframe": timeframe,
                    "timestamp": timestamp, "timestamp_utc": canonical_time(timestamp),
                    "decision": decision, "decision_time_utc": canonical_time(decision),
                    "segment_id": raw["segment_id"], "open": open_price, "high": high,
                    "low": low, "close": close, "volume": finite_nonnegative(raw["volume"], "volume"),
                    "taker_buy_base_volume": finite_nonnegative(raw.get("taker_buy_base_volume", "0") or "0", "taker_buy_base_volume"),
                }
                groups[f"{symbol}|{timeframe}|{raw['segment_id']}"] .append(row)
        require(row_count == table["row_count"], f"processed {timeframe} row count mismatch")
    for group_key, rows in groups.items():
        rows.sort(key=lambda row: row["timestamp"])
        hours = 1 if rows[0]["timeframe"] == "1h" else 4
        for left, right in zip(rows, rows[1:]):
            require((right["timestamp"] - left["timestamp"]).total_seconds() == hours * 3600, f"gap inside segment: {group_key}")
    require(all(count == len(assets) for count in availability.values()), "common three-asset availability mask violated")
    require(all(len(values) == 1 for values in segment_by_timestamp.values()), "assets disagree on segment membership")
    require(len(segment_ids) == 5, "expected exactly five segments")
    actual_counts = {split: {tf: counts[(split, tf)] for tf in ("1h", "4h")} for split in ("development", "validation", "final_test")}
    for split, expected in phase2a_split_rows(phase2a).items():
        require(actual_counts[split]["1h"] == expected["1h"] and actual_counts[split]["4h"] == expected["4h"], f"split count mismatch: {split}")
    recognized_sealed = sum(actual_counts["final_test"].values())
    return groups, {"split_rows": actual_counts, "sealed_rows_recognized_for_key_integrity_only": recognized_sealed, "primary_key_count": len(primary_keys), "segment_count": len(segment_ids)}


def phase2a_split_rows(phase2a: dict[str, Any]) -> dict[str, dict[str, int]]:
    return {item["id"]: item["expected_rows"] for item in phase2a["splits"]}


def _mean(values: Sequence[float]) -> float:
    # fsum avoids false threshold crossings caused only by accumulation order,
    # for example when the exact 20- and 50-bar means are equal.
    return math.fsum(values) / len(values)


def _ratio(current: float, previous: float) -> float:
    require(previous > 0, "ratio denominator is not positive")
    return current / previous - 1.0


def context_lookup(context_rows: Sequence[dict[str, Any]], availability: Sequence[datetime], variant: str, decision: datetime) -> dict[str, Any]:
    index = bisect.bisect_right(availability, decision) - 1
    require(index >= 0, f"no backward context for {variant}")
    selected = context_rows[index]
    require(selected[variant] <= decision, "future context selected")
    return selected


def evaluate_signals(values: Mapping[str, Any], previous_sma_ratio: float | None, close: float) -> dict[str, bool]:
    """Evaluate exactly the five preregistered, closed-bar signal rules."""
    return {
        "trend_sma20_cross_above_sma50": values["sma_ratio_20_50"] is not None and previous_sma_ratio is not None and values["sma_ratio_20_50"] > 1 and previous_sma_ratio <= 1,
        "momentum_return_12_positive": values["past_return_12"] is not None and values["past_return_12"] > 0,
        "breakout_close_above_prior_high_20": values["prior_high_20_shifted"] is not None and close > values["prior_high_20_shifted"],
        "mean_reversion_rsi14_below_30": values["rsi_14"] is not None and values["rsi_14"] < 30,
        "mean_reversion_close_2pct_below_sma20": values["close_to_sma20_distance"] is not None and values["close_to_sma20_distance"] <= -0.02,
    }


def compute_group_features(rows: Sequence[dict[str, Any]], context_rows: Sequence[dict[str, Any]], variant: str) -> list[dict[str, Any]]:
    require(variant in {"primary_d1", "sensitivity_d2"}, "unknown context variant")
    closes = [float(row["close"]) for row in rows]
    highs = [float(row["high"]) for row in rows]
    lows = [float(row["low"]) for row in rows]
    volumes = [float(row["volume"]) for row in rows]
    returns = [None] + [_ratio(closes[i], closes[i - 1]) for i in range(1, len(rows))]
    avg_gain: float | None = None
    avg_loss: float | None = None
    atr: float | None = None
    selected_history: list[dict[str, Any]] = []
    output: list[dict[str, Any]] = []
    previous_ratio: float | None = None
    availability = [row[variant] for row in context_rows]
    for i, row in enumerate(rows):
        values: dict[str, Any] = {}
        for lag in (1, 4, 12, 24):
            values[f"past_return_{lag}"] = _ratio(closes[i], closes[i - lag]) if i >= lag else None
        values["sma_20"] = _mean(closes[i - 19:i + 1]) if i >= 19 else None
        values["sma_50"] = _mean(closes[i - 49:i + 1]) if i >= 49 else None
        values["sma_ratio_20_50"] = values["sma_20"] / values["sma_50"] if values["sma_20"] is not None and values["sma_50"] is not None else None
        if i == 14:
            changes = [closes[j] - closes[j - 1] for j in range(1, 15)]
            avg_gain = _mean([max(value, 0.0) for value in changes])
            avg_loss = _mean([max(-value, 0.0) for value in changes])
        elif i > 14:
            change = closes[i] - closes[i - 1]
            avg_gain = ((avg_gain or 0.0) * 13 + max(change, 0.0)) / 14
            avg_loss = ((avg_loss or 0.0) * 13 + max(-change, 0.0)) / 14
        if avg_gain is None or avg_loss is None:
            values["rsi_14"] = None
        elif avg_loss == 0:
            values["rsi_14"] = 100.0 if avg_gain > 0 else 50.0
        else:
            values["rsi_14"] = 100.0 - 100.0 / (1.0 + avg_gain / avg_loss)
        if i >= 1:
            true_range = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            if i == 14:
                true_ranges = [max(highs[j] - lows[j], abs(highs[j] - closes[j - 1]), abs(lows[j] - closes[j - 1])) for j in range(1, 15)]
                atr = _mean(true_ranges)
            elif i > 14:
                atr = ((atr or 0.0) * 13 + true_range) / 14
        values["atr_14_relative"] = atr / closes[i] if atr is not None else None
        for window in (24, 72):
            sample = [value for value in returns[i - window + 1:i + 1] if value is not None]
            values[f"rolling_volatility_{window}"] = statistics.stdev(sample) if len(sample) == window else None
        if i >= 23:
            sample_volume = volumes[i - 23:i + 1]
            std = statistics.stdev(sample_volume)
            values["volume_zscore_24"] = (volumes[i] - _mean(sample_volume)) / std if std > 0 else None
        else:
            values["volume_zscore_24"] = None
        values["candle_range_relative"] = (highs[i] - lows[i]) / float(row["open"])
        values["candle_body_relative"] = (closes[i] - float(row["open"])) / float(row["open"])
        values["taker_buy_share_1h"] = row["taker_buy_base_volume"] / volumes[i] if row["timeframe"] == "1h" and volumes[i] > 0 else None
        values["prior_high_20_shifted"] = max(highs[i - 20:i]) if i >= 20 else None
        values["close_to_sma20_distance"] = closes[i] / values["sma_20"] - 1.0 if values["sma_20"] is not None else None
        selected = context_lookup(context_rows, availability, variant, row["decision"])
        selected_history.append(selected)
        for field in CONTEXT_VALUE_FIELDS:
            values[field] = selected[field]
        previous_distinct: dict[str, Any] | None = None
        for prior in reversed(selected_history[:-1]):
            if prior["source_timestamp"] != selected["source_timestamp"]:
                previous_distinct = prior
                break
        for field, change_field in zip(CONTEXT_VALUE_FIELDS, CONTEXT_CHANGE_FIELDS):
            values[change_field] = _ratio(selected[field], previous_distinct[field]) if previous_distinct is not None else None
        signals = evaluate_signals(values, previous_ratio, closes[i])
        previous_ratio = values["sma_ratio_20_50"]
        result = dict(row)
        result.update(values)
        result.update(signals)
        result["context_variant"] = variant
        result["context_source_timestamp_utc"] = canonical_time(selected["source_timestamp"])
        result["context_available_from_utc"] = canonical_time(selected[variant])
        output.append(result)
    return output


def holdings_for(timeframe: str, phase2a: dict[str, Any]) -> list[int]:
    item = next(value for value in phase2a["timeframes"] if value["id"] == timeframe)
    return [item["primary_holding_bars"], *item["sensitivity_holding_bars"]]


def _trade_base(rows: Sequence[dict[str, Any]], signal_index: int | None, entry_index: int, exit_index: int, strategy_type: str, strategy_id: str, holding: int | None) -> dict[str, Any]:
    entry = rows[entry_index]
    exit_row = rows[exit_index]
    require(entry["split"] == exit_row["split"] and entry["segment_id"] == exit_row["segment_id"], "trade crosses boundary")
    entry_price = float(entry["open"]); exit_price = float(exit_row["open"])
    require(entry_price > 0 and exit_price > 0, "trade price is not positive")
    if holding is None:
        window = rows[entry_index:exit_index + 1]
        exposure = (exit_row["timestamp"] - entry["timestamp"]).total_seconds() / 3600
    else:
        window = rows[entry_index:exit_index]
        exposure = holding * (1 if entry["timeframe"] == "1h" else 4)
    return {
        "split": entry["split"], "symbol": entry["symbol"], "timeframe": entry["timeframe"],
        "context_variant": entry["context_variant"], "strategy_type": strategy_type,
        "strategy_id": strategy_id, "holding_bars": holding, "segment_id": entry["segment_id"],
        "signal_time_utc": rows[signal_index]["decision_time_utc"] if signal_index is not None else "",
        "entry_time_utc": entry["timestamp_utc"], "exit_time_utc": exit_row["timestamp_utc"],
        "entry_open": entry_price, "exit_open": exit_price, "gross_return": exit_price / entry_price - 1.0,
        "maximum_adverse_excursion": min(float(value["low"]) for value in window) / entry_price - 1.0,
        "maximum_favorable_excursion": max(float(value["high"]) for value in window) / entry_price - 1.0,
        "exposure_hours": exposure,
    }


def signal_trades(rows: Sequence[dict[str, Any]], signal: str, holding: int) -> tuple[list[dict[str, Any]], dict[str, int]]:
    trades: list[dict[str, Any]] = []
    counts = Counter(signal_count=0, executable_signal_count=0, rejected_boundary_count=0, rejected_overlap_count=0)
    next_free_signal_index = 0
    for index, row in enumerate(rows):
        if not row[signal]:
            continue
        counts["signal_count"] += 1
        if index < next_free_signal_index:
            counts["rejected_overlap_count"] += 1
            continue
        entry_index = index + 1
        exit_index = index + holding + 1
        if exit_index >= len(rows) or rows[entry_index]["segment_id"] != row["segment_id"] or rows[exit_index]["segment_id"] != row["segment_id"] or rows[entry_index]["split"] != row["split"] or rows[exit_index]["split"] != row["split"]:
            counts["rejected_boundary_count"] += 1
            continue
        trades.append(_trade_base(rows, index, entry_index, exit_index, "signal", signal, holding))
        counts["executable_signal_count"] += 1
        next_free_signal_index = exit_index
    return trades, dict(counts)


def periodic_trades(rows: Sequence[dict[str, Any]], holding: int) -> tuple[list[dict[str, Any]], dict[str, int]]:
    first_by_week: dict[tuple[str, int, int], int] = {}
    for index, row in enumerate(rows):
        iso = row["timestamp"].isocalendar()
        first_by_week.setdefault((row["split"], iso.year, iso.week), index)
    trades: list[dict[str, Any]] = []
    counts = Counter(signal_count=len(first_by_week), executable_signal_count=0, rejected_boundary_count=0, rejected_overlap_count=0)
    next_free_entry = 0
    for index in sorted(first_by_week.values()):
        if index < next_free_entry:
            counts["rejected_overlap_count"] += 1
            continue
        exit_index = index + holding
        if exit_index >= len(rows) or rows[exit_index]["segment_id"] != rows[index]["segment_id"] or rows[exit_index]["split"] != rows[index]["split"]:
            counts["rejected_boundary_count"] += 1
            continue
        trades.append(_trade_base(rows, None, index, exit_index, "baseline", "periodic_entry_baseline", holding))
        counts["executable_signal_count"] += 1
        next_free_entry = exit_index
    return trades, dict(counts)


def buy_hold_trades(rows: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    by_split: dict[str, list[int]] = defaultdict(list)
    for index, row in enumerate(rows):
        by_split[row["split"]].append(index)
    return [_trade_base(rows, None, indices[0], indices[-1], "baseline", "segment_buy_and_hold", None) for indices in by_split.values() if len(indices) >= 2]


def apply_cost(trade: Mapping[str, Any], scenario: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(trade)
    entry = float(trade["entry_open"]); exit_price = float(trade["exit_open"])
    net = exit_price * (1 - scenario["exit_slippage_bps"] / 10000) * (1 - scenario["exit_fee_bps"] / 10000) / (entry * (1 + scenario["entry_slippage_bps"] / 10000) * (1 + scenario["entry_fee_bps"] / 10000)) - 1
    result.update(cost_scenario=scenario["id"], net_return=net, positive_net_outcome=net > 0)
    return result


def _drawdown(returns: Sequence[float]) -> float | None:
    if not returns:
        return None
    equity = peak = 1.0; worst = 0.0
    for value in returns:
        equity *= 1 + value
        peak = max(peak, equity)
        worst = min(worst, equity / peak - 1)
    return worst


def summarize_cell(key: tuple[str, ...], trades: Sequence[dict[str, Any]], signal_counts: Mapping[tuple[str, ...], dict[str, int]]) -> dict[str, Any]:
    split, symbol, timeframe, context, strategy_type, strategy, holding_text, cost = key
    gross = [float(row["gross_return"]) for row in trades]
    net = [float(row["net_return"]) for row in trades]
    counts = signal_counts.get((split, symbol, timeframe, context, strategy, holding_text), {})
    gains = sum(value for value in net if value > 0); losses = -sum(value for value in net if value < 0)
    if not net:
        profit_factor = None; pf_status = "NO_TRADES"
    elif losses == 0:
        profit_factor = None; pf_status = "NO_LOSSES"
    else:
        profit_factor = gains / losses; pf_status = "DEFINED"
    segment_means = [_mean(values) for values in defaultdict(list).values()]
    by_segment: dict[str, list[float]] = defaultdict(list)
    for row in trades: by_segment[row["segment_id"]].append(float(row["net_return"]))
    segment_means = [_mean(values) for values in by_segment.values()]
    return {
        "split": split, "symbol": symbol, "timeframe": timeframe, "context_variant": context,
        "strategy_type": strategy_type, "strategy_id": strategy,
        "holding_bars": int(holding_text) if holding_text else None, "cost_scenario": cost,
        "signal_count": counts.get("signal_count", 0), "executable_signal_count": counts.get("executable_signal_count", len(trades)),
        "rejected_signal_count": counts.get("rejected_boundary_count", 0) + counts.get("rejected_overlap_count", 0),
        "trade_count": len(trades), "hit_rate": sum(value > 0 for value in net) / len(net) if net else None,
        "average_gross_return": _mean(gross) if gross else None, "median_gross_return": statistics.median(gross) if gross else None,
        "average_net_return": _mean(net) if net else None, "median_net_return": statistics.median(net) if net else None,
        "cumulative_net_return": math.prod(1 + value for value in net) - 1 if net else 0.0,
        "trade_return_volatility": statistics.stdev(net) if len(net) >= 2 else None,
        "maximum_drawdown": _drawdown(net), "profit_factor": profit_factor, "profit_factor_status": pf_status,
        "cost_burden": _mean([g - n for g, n in zip(gross, net)]) if net else 0.0,
        "exposure_hours": sum(float(row["exposure_hours"]) for row in trades),
        "covered_segment_count": len(by_segment),
        "segment_mean_min": min(segment_means) if segment_means else None,
        "segment_mean_median": statistics.median(segment_means) if segment_means else None,
        "segment_mean_max": max(segment_means) if segment_means else None,
        "positive_segment_share": sum(value > 0 for value in segment_means) / len(segment_means) if segment_means else None,
        "uncertainty_note": "descriptive_non_iid_no_p_value",
    }


def build_evaluation(feature_groups: Mapping[tuple[str, str], Sequence[dict[str, Any]]], phase2a: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    base_trades: list[dict[str, Any]] = []
    signal_counts_accumulator: dict[tuple[str, ...], Counter[str]] = defaultdict(Counter)
    for (_, _), rows in sorted(feature_groups.items()):
        for holding in holdings_for(rows[0]["timeframe"], phase2a):
            for signal in SIGNAL_IDS:
                trades, counts = signal_trades(rows, signal, holding)
                base_trades.extend(trades)
                for split in ("development", "validation"):
                    split_counts = {name: 0 for name in counts}
                    # Recompute counts on the split slice so boundary evidence is split-specific.
                    split_rows = [row for row in rows if row["split"] == split]
                    _, split_counts = signal_trades(split_rows, signal, holding)
                    key = (split, rows[0]["symbol"], rows[0]["timeframe"], rows[0]["context_variant"], signal, str(holding))
                    signal_counts_accumulator[key].update(split_counts)
            periodic, counts = periodic_trades(rows, holding)
            base_trades.extend(periodic)
            for split in ("development", "validation"):
                split_rows = [row for row in rows if row["split"] == split]
                _, split_counts = periodic_trades(split_rows, holding)
                signal_counts_accumulator[(split, rows[0]["symbol"], rows[0]["timeframe"], rows[0]["context_variant"], "periodic_entry_baseline", str(holding))].update(split_counts)
        base_trades.extend(buy_hold_trades(rows))
    signal_counts = {key: dict(value) for key, value in signal_counts_accumulator.items()}
    frequency_rows = [
        {"split": key[0], "symbol": key[1], "timeframe": key[2], "context_variant": key[3],
         "signal_id": key[4], "holding_bars": int(key[5]), **counts}
        for key, counts in signal_counts.items() if key[4] in SIGNAL_IDS
    ]
    cost_trades = [apply_cost(trade, cost) for trade in base_trades for cost in phase2a["costs"]["scenarios"]]
    grouped: dict[tuple[str, ...], list[dict[str, Any]]] = defaultdict(list)
    for trade in cost_trades:
        key = (trade["split"], trade["symbol"], trade["timeframe"], trade["context_variant"], trade["strategy_type"], trade["strategy_id"], str(trade["holding_bars"] or ""), trade["cost_scenario"])
        grouped[key].append(trade)
    # Materialize all registered cells, including always-flat and zero-trade cells.
    for split in ("development", "validation"):
        for symbol in phase2a["market"]["assets"]:
            for timeframe in ("1h", "4h"):
                for context in ("primary_d1", "sensitivity_d2"):
                    for cost in [item["id"] for item in phase2a["costs"]["scenarios"]]:
                        for strategy in (*SIGNAL_IDS, "periodic_entry_baseline"):
                            strategy_type = "signal" if strategy in SIGNAL_IDS else "baseline"
                            for holding in holdings_for(timeframe, phase2a):
                                grouped.setdefault((split, symbol, timeframe, context, strategy_type, strategy, str(holding), cost), [])
                        for strategy in ("always_flat", "segment_buy_and_hold"):
                            grouped.setdefault((split, symbol, timeframe, context, "baseline", strategy, "", cost), [])
    results = [summarize_cell(key, sorted(rows, key=lambda row: row["entry_time_utc"]), signal_counts) for key, rows in sorted(grouped.items())]
    aggregate_grouped: dict[tuple[str, ...], list[dict[str, Any]]] = defaultdict(list)
    for trade in cost_trades:
        key = (trade["split"], "ALL", trade["timeframe"], trade["context_variant"], trade["strategy_type"], trade["strategy_id"], str(trade["holding_bars"] or ""), trade["cost_scenario"])
        aggregate_grouped[key].append(trade)
    aggregate_counts: dict[tuple[str, ...], Counter[str]] = defaultdict(Counter)
    for key, counts in signal_counts.items():
        aggregate_counts[(key[0], "ALL", key[2], key[3], key[4], key[5])].update(counts)
    for split in ("development", "validation"):
        for timeframe in ("1h", "4h"):
            for context in ("primary_d1", "sensitivity_d2"):
                for cost in [item["id"] for item in phase2a["costs"]["scenarios"]]:
                    for strategy in (*SIGNAL_IDS, "periodic_entry_baseline"):
                        strategy_type = "signal" if strategy in SIGNAL_IDS else "baseline"
                        for holding in holdings_for(timeframe, phase2a):
                            aggregate_grouped.setdefault((split, "ALL", timeframe, context, strategy_type, strategy, str(holding), cost), [])
                    for strategy in ("always_flat", "segment_buy_and_hold"):
                        aggregate_grouped.setdefault((split, "ALL", timeframe, context, "baseline", strategy, "", cost), [])
    aggregate_results = [summarize_cell(key, sorted(rows, key=lambda row: (row["entry_time_utc"], row["symbol"])), {k: dict(v) for k, v in aggregate_counts.items()}) for key, rows in sorted(aggregate_grouped.items())]
    for row in aggregate_results:
        row["cumulative_net_return"] = None
        row["maximum_drawdown"] = None
        row["uncertainty_note"] = "pooled_assets_descriptive_only_no_shared_capital_curve_no_iid_claim"
    return cost_trades, frequency_rows, results, aggregate_results


def _feature_csv_rows(rows: Sequence[dict[str, Any]]) -> Iterable[dict[str, Any]]:
    for row in rows:
        yield {field: row.get(field) for field in FEATURE_OUTPUT_FIELDS}


def _write_bytes(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


def _quality_markdown(summary: Mapping[str, Any], provenance: Mapping[str, Any]) -> bytes:
    text = f"""# Phase 2B – Offline-Qualitätsbericht

Status: **PASS_WITH_DEVELOPMENT_AND_VALIDATION_RESULTS**  
Gate 2: **NOT_EVALUATED**

Phase 2B berechnet ausschließlich die vorregistrierten 25 Merkmale, fünf Signale,
drei Baselines und konservative Long/Flat-Trades für Development 2021–2022 und
Validation 2023. Der finale Test 2024–2025 blieb versiegelt.

- Featurezeilen (D1 und D2 zusammen): {summary['feature_rows']}
- Tradezeilen einschließlich drei Kostenszenarien: {summary['trade_rows']}
- Ergebniszellen für Development und Validation: {summary['result_rows']}
- Nur zur Schlüsselprüfung erkannte Testzeilen: {summary['sealed_rows']}
- Im Test berechnete Features, Signale, Trades oder Renditen: 0
- Implementierungs-/Numerik-Policy: `{provenance['implementation_policy_id']}`
- Float-Serialisierung: `{provenance['float_serialization_rule']}`
- SMA-Berechnung: `{provenance['sma_calculation_rule']}`
- Phase-2B-Konfigurationshash: `{provenance['phase2b_config_sha256']}`
- Pipeline-Codehash: `{provenance['backtest_pipeline_sha256']}`

Die Ergebnisse sind historische, methodische Evidenz und kein Gewinnversprechen.
"""
    return text.encode("utf-8")


def generate_bundle(temp_data: Path, temp_reports: Path, groups: Mapping[str, Sequence[dict[str, Any]]], context_rows: Sequence[dict[str, Any]], phase2a: dict[str, Any], input_quality: Mapping[str, Any], root: Path, provenance: Mapping[str, Any]) -> dict[str, Any]:
    feature_groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    feature_rows_total = 0
    null_counts = Counter()
    for group_key, market_rows in sorted(groups.items()):
        for variant in ("primary_d1", "sensitivity_d2"):
            features = compute_group_features(market_rows, context_rows, variant)
            feature_groups[(group_key, variant)] = features
            feature_rows_total += len(features)
            for row in features:
                for name in FEATURE_NAMES:
                    if row[name] is None: null_counts[(row["timeframe"], name)] += 1
    by_file: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for (_, variant), rows in feature_groups.items():
        by_file[(rows[0]["timeframe"], variant)].extend(rows)
    for (timeframe, variant), rows in sorted(by_file.items()):
        rows.sort(key=lambda row: (row["symbol"], row["timestamp_utc"]))
        _write_bytes(temp_data / f"features_{timeframe}_{variant}.csv", canonical_csv_bytes(FEATURE_OUTPUT_FIELDS, _feature_csv_rows(rows)))
    trades, frequencies, results, aggregate_results = build_evaluation(feature_groups, phase2a)
    trades.sort(key=lambda row: tuple(str(row.get(field, "")) for field in ("split", "symbol", "timeframe", "context_variant", "strategy_id", "holding_bars", "cost_scenario", "entry_time_utc")))
    frequencies.sort(key=lambda row: tuple(str(row.get(field, "")) for field in SIGNAL_FREQUENCY_FIELDS[:6]))
    results.sort(key=lambda row: tuple(str(row.get(field, "")) for field in RESULT_FIELDS[:8]))
    aggregate_results.sort(key=lambda row: tuple(str(row.get(field, "")) for field in RESULT_FIELDS[:8]))
    _write_bytes(temp_data / "trades.csv", canonical_csv_bytes(TRADE_FIELDS, trades))
    _write_bytes(temp_reports / "signal_frequency_summary.csv", canonical_csv_bytes(SIGNAL_FREQUENCY_FIELDS, frequencies))
    _write_bytes(temp_reports / "results_summary.csv", canonical_csv_bytes(RESULT_FIELDS, results))
    _write_bytes(temp_reports / "aggregate_results_summary.csv", canonical_csv_bytes(RESULT_FIELDS, aggregate_results))
    _write_bytes(temp_reports / "baseline_comparison.csv", canonical_csv_bytes(RESULT_FIELDS, [row for row in results if row["strategy_type"] == "baseline"]))
    indexed = {(row["split"], row["symbol"], row["timeframe"], row["strategy_type"], row["strategy_id"], str(row["holding_bars"] or ""), row["cost_scenario"], row["context_variant"]): row for row in results}
    context_comparison = []
    bases = sorted({key[:-1] for key in indexed})
    for base in bases:
        d1 = indexed[(*base, "primary_d1")]; d2 = indexed[(*base, "sensitivity_d2")]
        context_comparison.append({
            "split": base[0], "symbol": base[1], "timeframe": base[2], "strategy_type": base[3],
            "strategy_id": base[4], "holding_bars": int(base[5]) if base[5] else None,
            "cost_scenario": base[6], "d1_trade_count": d1["trade_count"], "d2_trade_count": d2["trade_count"],
            "trade_count_delta": d2["trade_count"] - d1["trade_count"],
            "d1_average_net_return": d1["average_net_return"], "d2_average_net_return": d2["average_net_return"],
            "average_net_return_delta": (d2["average_net_return"] - d1["average_net_return"]) if d1["average_net_return"] is not None and d2["average_net_return"] is not None else None,
        })
    _write_bytes(temp_reports / "context_variant_comparison.csv", canonical_csv_bytes(CONTEXT_COMPARISON_FIELDS, context_comparison))
    sealed = {
        "status": "SEALED_NOT_EVALUATED", "split": "final_test",
        "final_test_status": "SEALED_NOT_EVALUATED",
        "final_test_feature_rows_evaluated": 0,
        "final_test_signals_evaluated": 0,
        "final_test_trades_evaluated": 0,
        "final_test_metrics_evaluated": 0,
        "input_rows_recognized_for_key_integrity_only": input_quality["sealed_rows_recognized_for_key_integrity_only"],
        "feature_rows": 0, "feature_aggregates": 0, "signal_counts": 0,
        "positions": 0, "trades": 0, "gross_returns": 0, "net_returns": 0,
        "performance_metrics": 0,
    }
    _write_bytes(temp_reports / "sealed_final_test.json", canonical_json_bytes(sealed))
    feature_quality = {
        "feature_count": len(FEATURE_NAMES), "signal_count": len(SIGNAL_IDS),
        "feature_rows": feature_rows_total, "rows_by_timeframe_context": {f"{key[0]}|{key[1]}": len(value) for key, value in sorted(by_file.items())},
        "null_counts_by_timeframe_feature": {f"{key[0]}|{key[1]}": value for key, value in sorted(null_counts.items())},
        "null_policy": "only preregistered warmup, zero-standard-deviation, zero-volume, and unavailable-4h-taker nulls",
        "segment_state_resets": True, "gap_crossing_allowed": False, "future_context_violations": 0,
        "duplicate_feature_keys": 0,
    }
    _write_bytes(temp_reports / "feature_quality_summary.json", canonical_json_bytes(feature_quality))
    execution = {
        "entry_rule": "next_open_after_signal_close", "exit_rule": "open_after_exact_holding_bars",
        "shortened_boundary_trades": 0, "same_bar_entries": 0, "positions_across_segments": 0,
        "cost_scenarios_bps": [20, 30, 50], "primary_cost_scenario": "base_30bps",
        "trade_rows": len(trades), "result_rows": len(results), "aggregate_result_rows": len(aggregate_results), "planned_cells_per_split": 720,
    }
    _write_bytes(temp_reports / "execution_quality_summary.json", canonical_json_bytes(execution))
    _write_bytes(temp_reports / "split_segment_quality.json", canonical_json_bytes(input_quality))
    context_quality = {
        "variants": ["primary_d1", "sensitivity_d2"], "independent_backward_asof_joins": True,
        "shifted_d1_values_used_for_d2": False, "future_context_violations": 0,
        "feature_rows_per_variant": feature_rows_total // 2,
    }
    _write_bytes(temp_reports / "context_variant_quality.json", canonical_json_bytes(context_quality))
    input_hashes = {
        "phase2a_contract": provenance["phase2a_contract_sha256"],
        "processed_1h": sha256_file(root / "data/processed/full_import/market_context_1h.csv"),
        "processed_4h": sha256_file(root / "data/processed/full_import/market_context_4h.csv"),
        "coinmetrics_context": sha256_file(root / "data/interim/full_import/coinmetrics/btc_daily_context.csv"),
        "sqlite": sha256_file(root / "data/processed/full_import/sql/crypto_entry_intelligence.sqlite"),
        "phase1_individual_files": provenance["protected_inputs"]["individual_files"],
        "phase1_groups": provenance["protected_inputs"]["groups"],
    }
    _write_bytes(temp_reports / "input_output_hashes.json", canonical_json_bytes({"provenance": provenance, "inputs": input_hashes, "outputs_recorded_in": "phase2b_manifest.csv"}))
    summary = {"feature_rows": feature_rows_total, "trade_rows": len(trades), "result_rows": len(results), "sealed_rows": sealed["input_rows_recognized_for_key_integrity_only"]}
    _write_bytes(temp_reports / "PHASE2B_QUALITY_REPORT.md", _quality_markdown(summary, provenance))
    manifest_rows: list[dict[str, Any]] = []
    for base, prefix in ((temp_data, "data/processed/phase2b"), (temp_reports, "reports/backtest/phase2b_outputs")):
        for path in sorted(base.rglob("*")):
            if path.is_file() and path.name != "phase2b_manifest.csv":
                row_count = 0
                if path.suffix == ".csv":
                    with path.open("r", encoding="utf-8", newline="") as handle: row_count = max(sum(1 for _ in csv.reader(handle)) - 1, 0)
                manifest_rows.append({"artifact_path": f"{prefix}/{path.relative_to(base).as_posix()}", "artifact_type": path.suffix.lstrip(".") or "file", "row_count": row_count, "sha256": sha256_file(path)})
    _write_bytes(temp_reports / "phase2b_manifest.csv", canonical_csv_bytes(MANIFEST_FIELDS, manifest_rows))
    return {**summary, "files": len(manifest_rows) + 1}


def _bundle_files(root: Path) -> dict[str, tuple[int, str]]:
    require(root.is_dir(), f"bundle root missing: {root}")
    return {path.relative_to(root).as_posix(): (path.stat().st_size, sha256_file(path)) for path in root.rglob("*") if path.is_file()}


def build_provenance(root: Path, config: Mapping[str, Any], phase2a: Mapping[str, Any]) -> dict[str, Any]:
    groups: dict[str, Any] = {}
    for item in phase2a["protected_phase1_artifacts"]["groups"]:
        count, total_bytes, fingerprint = group_fingerprint(root, item["roots"], item["files"])
        groups[item["id"]] = {"file_count": count, "total_bytes": total_bytes, "fingerprint": fingerprint}
    return {
        "implementation_policy_id": IMPLEMENTATION_POLICY_ID,
        "float_serialization_rule": FLOAT_SERIALIZATION_RULE,
        "sma_calculation_rule": SMA_CALCULATION_RULE,
        "phase2b_config_sha256": sha256_file(root / "config/backtest_phase2b.json"),
        "backtest_pipeline_sha256": sha256_file(root / "src/backtest_pipeline.py"),
        "phase2a_contract_sha256": sha256_file(root / "config/backtest.json"),
        "protected_inputs": {
            "individual_files": {item["id"]: sha256_file(root / item["path"]) for item in phase2a["protected_phase1_artifacts"]["individual_files"]},
            "groups": groups,
        },
        "final_test_status": "SEALED_NOT_EVALUATED",
    }


def validate_cached_provenance(report_root: Path, expected: Mapping[str, Any]) -> None:
    evidence_path = report_root / "input_output_hashes.json"
    require(evidence_path.is_file(), "cached Phase-2B provenance evidence is missing")
    payload = load_json(evidence_path)
    require(payload.get("provenance") == expected, "cached Phase-2B provenance mismatch")


def validate_cached_bundle(generated_data: Path, generated_reports: Path, data_root: Path, report_root: Path) -> None:
    require(data_root.is_dir() and report_root.is_dir(), "partial Phase-2B cache exists")
    require(_bundle_files(generated_data) == _bundle_files(data_root), "Phase-2B data cache mismatch")
    require(_bundle_files(generated_reports) == _bundle_files(report_root), "Phase-2B report cache mismatch")


def publish_bundle(generated_data: Path, generated_reports: Path, data_root: Path, report_root: Path) -> None:
    require(not data_root.exists() and not report_root.exists(), "Phase-2B output exists; overwrite forbidden")
    data_root.parent.mkdir(parents=True, exist_ok=True)
    report_root.parent.mkdir(parents=True, exist_ok=True)
    os.replace(generated_data, data_root)
    try:
        os.replace(generated_reports, report_root)
    except Exception:
        os.replace(data_root, generated_data)
        raise


def run_pipeline(config_path: Path) -> Phase2BResult:
    resolved = config_path.resolve()
    require(resolved.is_file(), "Phase-2B config missing")
    root = resolved.parent.parent.resolve()
    require(resolved.parent == root / "config", "config must be directly under project config")
    config = load_json(resolved)
    phase2a = validate_phase2b_config(config, root)
    provenance = build_provenance(root, config, phase2a)
    data_root = safe_path(root, config["output"]["data_root"])
    report_root = safe_path(root, config["output"]["report_root"])
    require(data_root.exists() == report_root.exists(), "partial Phase-2B cache exists")
    if data_root.exists():
        validate_cached_provenance(report_root, provenance)
    context = read_context(root, phase2a)
    groups, input_quality = read_market_rows(root, phase2a)
    with tempfile.TemporaryDirectory(prefix="phase2b-", dir=root) as temporary:
        temp_root = Path(temporary)
        temp_data = temp_root / "data"
        temp_reports = temp_root / "reports"
        temp_data.mkdir(); temp_reports.mkdir()
        summary = generate_bundle(temp_data, temp_reports, groups, context, phase2a, input_quality, root, provenance)
        if data_root.exists():
            validate_cached_bundle(temp_data, temp_reports, data_root, report_root)
            status = "CACHED_VALID"
        else:
            publish_bundle(temp_data, temp_reports, data_root, report_root)
            status = "PHASE2B_COMPLETED_DEVELOPMENT_VALIDATION_ONLY"
    return Phase2BResult(status, summary["feature_rows"], summary["trade_rows"], summary["result_rows"], input_quality["sealed_rows_recognized_for_key_integrity_only"], summary["files"])


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the offline preregistered Phase-2B core for development and validation only.")
    parser.add_argument("--config", required=True, type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = run_pipeline(args.config)
    except (Phase2BError, OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"PHASE2B ERROR: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result.__dict__, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
