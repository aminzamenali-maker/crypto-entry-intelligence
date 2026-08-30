# 04 - Signale, Backtest und finaler Test

## `src/backtest_contract.py` - erst die Methode festschreiben

Bevor der Backtest rechnen darf, prueft diese Datei die Methode gegen `config/backtest.json`: Assets, Zeitrahmen, Kosten, Signale, Features, Splits, Baselines, geschuetzte Pfade und die Regel, dass der finale Test nicht zur Parameterauswahl verwendet werden darf.

Originalausschnitt `validate_method_contract`, Zeilen 396-412:

```python
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
```


Der eigentliche Contract-Run bleibt read-only:

```python
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
```


---

## `src/backtest_pipeline.py` - Features, Signale, Trades und Ergebnisse

### Die fuenf Signalregeln stehen direkt im Code

Originalausschnitt, Zeilen 331-339:

```python
def evaluate_signals(values: Mapping[str, Any], previous_sma_ratio: float | None, close: float) -> dict[str, bool]:
    """Evaluate exactly the five preregistered, closed-bar signal rules."""
    return {
        "trend_sma20_cross_above_sma50": values["sma_ratio_20_50"] is not None and previous_sma_ratio is not None and values["sma_ratio_20_50"] > 1 and previous_sma_ratio <= 1,
        "momentum_return_12_positive": values["past_return_12"] is not None and values["past_return_12"] > 0,
        "breakout_close_above_prior_high_20": values["prior_high_20_shifted"] is not None and close > values["prior_high_20_shifted"],
        "mean_reversion_rsi14_below_30": values["rsi_14"] is not None and values["rsi_14"] < 30,
        "mean_reversion_close_2pct_below_sma20": values["close_to_sma20_distance"] is not None and values["close_to_sma20_distance"] <= -0.02,
    }
```


### In Alltagssprache

| Signal-ID | Regel |
|---|---|
| `trend_sma20_cross_above_sma50` | SMA20 kreuzt SMA50 von unten nach oben |
| `momentum_return_12_positive` | Kurs liegt hoeher als vor 12 Kerzen |
| `breakout_close_above_prior_high_20` | Schlusskurs liegt ueber dem vorherigen 20-Kerzen-Hoch |
| `mean_reversion_rsi14_below_30` | RSI14 liegt unter 30 |
| `mean_reversion_close_2pct_below_sma20` | Kurs liegt mindestens 2 % unter SMA20 |

### Features werden nur rueckblickend berechnet

Ausschnitt aus `compute_group_features`, Zeilen 342-419:

```python
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
# ... Ausschnitt gekuerzt; Originalfunktion ist laenger ...
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
```


Wichtige Punkte:

- Renditen verwenden nur vergangene Kurse.
- SMA20 und SMA50 benoetigen vollstaendige Fenster.
- `prior_high_20_shifted` nimmt die 20 **vorherigen** Hochs, nicht die aktuelle Kerze.
- RSI und ATR werden zustandsbehaftet innerhalb des Segments aufgebaut.
- Kontext wird mit einem rueckwaertigen As-of-Lookup gewaehlt.
- Jeder Funktionsaufruf pro Gruppe startet den rollenden Zustand neu.

### Vom Signal zum simulierten Trade

Originalausschnitt `signal_trades`, Zeilen 452-471:

```python
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
```


### Kommentierte Lesefassung

```python
# Signal entsteht nach einer abgeschlossenen Kerze t.
if not row[signal]:
    continue

# Einstieg nicht auf derselben Kerze, sondern am naechsten Open.
entry_index = index + 1

# Ausstieg nach exakt der vorregistrierten Haltedauer.
exit_index = index + holding + 1

# Trade verwerfen, wenn er Segment- oder Splitgrenzen ueberschreiten wuerde.
if boundary_problem:
    continue

# Ueberlappende Positionen derselben Zelle werden nicht gleichzeitig geoeffnet.
next_free_signal_index = exit_index
```

### Handelskosten

Originalausschnitt, Zeilen 503-508:

```python
def apply_cost(trade: Mapping[str, Any], scenario: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(trade)
    entry = float(trade["entry_open"]); exit_price = float(trade["exit_open"])
    net = exit_price * (1 - scenario["exit_slippage_bps"] / 10000) * (1 - scenario["exit_fee_bps"] / 10000) / (entry * (1 + scenario["entry_slippage_bps"] / 10000) * (1 + scenario["entry_fee_bps"] / 10000)) - 1
    result.update(cost_scenario=scenario["id"], net_return=net, positive_net_outcome=net > 0)
    return result
```


Die Kostenformel ist multiplikativ und beruecksichtigt Slippage und Gebuehren an Ein- und Ausstieg. Dadurch wird nicht nur eine pauschale Zahl vom Bruttoergebnis abgezogen.

### Pipeline-Schutz

Ausschnitt `run_pipeline`, Zeilen 820-847:

```python
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
```


Der Phase-2B-Lauf wertet nur Development und Validation aus; `final_test` bleibt in dieser Stufe versiegelt.

---

## `src/final_test_once.py` - 2024-2025 genau einmal

Der finale Test hat einen eigenen Runner und eine eigene Konfiguration. Vor dem Start werden Git-Zustand, Hashes, Methode, Manifest, bestehende Outputs und ein exakter Bestaetigungstoken geprueft.

Originalausschnitt `run_once`, Zeilen 573-637:

```python
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
```


### Warum dieser Mechanismus so streng ist

Sobald der finale Test startet, wird ein Startzustand exklusiv geschrieben. Bei Erfolg entstehen Receipt und Bundle-Snapshot; bei Fehler wird ein `FAILED_CLOSED`-Status festgehalten. Automatische Wiederholung ist explizit verboten. So kann der Final-Test nicht unbemerkt mehrfach ausprobiert werden, bis ein besseres Ergebnis erscheint.
