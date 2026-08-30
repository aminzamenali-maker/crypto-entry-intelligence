# 05 - EDA und Power-BI-Pipeline

## `src/eda_powerbi_pipeline.py`

Diese Datei liest die bereits gepruefte SQLite-Datenbank read-only, berechnet beschreibende Kennzahlen, erzeugt Tabellen und Visualisierungsartefakte und erstellt den Datenvertrag fuer Power BI.

## Deskriptive Statistik

Originalausschnitt, Zeilen 261-294:

```python
def descriptive_statistics(values: Iterable[float | None], total_count: int | None = None) -> dict[str, Any]:
    finite = []
    observed = 0
    for value in values:
        observed += 1
        if value is None:
            continue
        number = float(value)
        if not math.isfinite(number):
            raise IntegrityError("Nicht-endlicher EDA-Wert.")
        finite.append(number)
    finite.sort()
    denominator = observed if total_count is None else total_count
    if denominator < len(finite):
        raise IntegrityError("Statistik-Nenner ist kleiner als die Wertanzahl.")
    if not finite:
        return {
            "count": 0, "mean": None, "std": None, "min": None, "q25": None,
            "median": None, "q75": None, "max": None, "null_count": denominator,
        }
    result = {
        "count": len(finite),
        "mean": statistics.fmean(finite),
        "std": statistics.stdev(finite) if len(finite) > 1 else 0.0,
        "min": finite[0],
        "q25": _quantile(finite, 0.25),
        "median": _quantile(finite, 0.50),
        "q75": _quantile(finite, 0.75),
        "max": finite[-1],
        "null_count": denominator - len(finite),
    }
    if not (result["min"] <= result["q25"] <= result["median"] <= result["q75"] <= result["max"]):
        raise IntegrityError("Quantile sind nicht monoton.")
    return result
```


Die Funktion berechnet Anzahl, Mittelwert, Standardabweichung, Minimum, Quartile, Median, Maximum und Nullwerte. Nicht-endliche Werte wie `NaN` oder `inf` werden nicht still akzeptiert.

## Abgeleitete EDA-Werte

Originalausschnitt, Zeilen 519-551:

```python
def _derived_values(row: Mapping[str, Any], previous: Mapping[str, Any] | None) -> dict[str, float | None]:
    open_price = float(row["open"])
    high = float(row["high"])
    low = float(row["low"])
    close = float(row["close"])
    body = close / open_price - 1.0
    candle_range = high / low - 1.0
    upper_wick = (high - max(open_price, close)) / open_price
    lower_wick = (min(open_price, close) - low) / open_price
    taker_share = None
    if row["taker_buy_quote_volume"] is not None and float(row["quote_asset_volume"]) > 0:
        taker_share = float(row["taker_buy_quote_volume"]) / float(row["quote_asset_volume"])
    close_return = None
    if previous is not None:
        same_group = (
            previous["symbol"] == row["symbol"]
            and previous["timeframe"] == row["timeframe"]
            and previous["segment_id"] == row["segment_id"]
        )
        interval_hours = 1 if row["timeframe"] == "1h" else 4
        exact_interval = (
            _parse_utc(str(row["timestamp_utc"])) - _parse_utc(str(previous["timestamp_utc"]))
        ).total_seconds() == interval_hours * 3600
        if same_group and exact_interval:
            close_return = close / float(previous["close"]) - 1.0
    return {
        "candle_body_return": body,
        "candle_range": candle_range,
        "upper_wick_relative": upper_wick,
        "lower_wick_relative": lower_wick,
        "taker_buy_share": taker_share,
        "close_to_close_return": close_return,
    }
```


Besonders wichtig: `close_to_close_return` wird nur berechnet, wenn vorherige und aktuelle Zeile zum selben Asset, selben Zeitrahmen und selben Segment gehoeren **und** der Zeitabstand exakt 1h bzw. 4h ist. Eine Datenluecke wird also nicht als normale Rendite behandelt.

## Build und Publikation

Originalausschnitt `run_pipeline`, Zeilen 1735-1787:

```python
def run_pipeline(project_root: Path, config_path: Path, *, refresh_existing: bool = False) -> PipelineResult:
    project_root = project_root.resolve()
    evidence = validate_inputs(project_root, config_path)
    report_root, contract_root, export_root, cache_exists = _existing_output_state(project_root)
    temp_parent = _inside_project(project_root, project_root / "data/processed/full_import")
    temp_root = temp_parent / f".phase1c_c.{uuid.uuid4().hex}.part"
    if temp_root.exists():
        raise SafetyError("Unerwartetes temporaeres Phase-1C-C-Verzeichnis.")
    temp_root.mkdir(parents=False)
    try:
        temp_report, temp_contract, temp_export, table_counts, export_counts = _generate_bundle(temp_root, evidence)
        protected_before_publication = _snapshot_files(Path(path) for path in evidence.protected_hashes)
        if protected_before_publication != evidence.protected_hashes:
            raise IntegrityError("Eingaben oder fruehere Buildnachweise wurden vor der Veroeffentlichung veraendert.")
        if sha256_file(evidence.database_path) != evidence.database_sha256:
            raise IntegrityError("SQLite-Datenbank wurde vor der Veroeffentlichung veraendert.")
        if cache_exists:
            byteidentical = _same_directory(temp_report, report_root) and _same_directory(temp_export, export_root)
            byteidentical = byteidentical and all(
                (temp_contract / name).read_bytes() == (contract_root / name).read_bytes()
                for name in CONTRACT_FILES
            )
            if byteidentical:
                status = "CACHED_VALID"
            elif not refresh_existing:
                raise IntegrityError("Vorhandene Phase-1C-C-Ausgaben weichen bytegenau ab; expliziter kontrollierter Refresh erforderlich.")
            else:
                _refresh_bundle(
                    temp_report, temp_contract, temp_export,
                    report_root, contract_root, export_root,
                    temp_root / "backup",
                )
                status = "REFRESHED"
        else:
            _publish_bundle(temp_report, temp_contract, temp_export, report_root, contract_root, export_root)
            status = "CREATED"

        protected_after = _snapshot_files(Path(path) for path in evidence.protected_hashes)
        if protected_after != evidence.protected_hashes:
            raise IntegrityError("Fruehere Buildnachweise wurden waehrend Phase 1C-C veraendert.")
        if sha256_file(evidence.database_path) != evidence.database_sha256:
            raise IntegrityError("SQLite-Datenbank wurde waehrend Phase 1C-C veraendert.")
        export_hashes = {name: sha256_file(export_root / name) for name in EXPORT_FILES}
        return PipelineResult(
            status=status, report_root=report_root, export_root=export_root,
            database_sha256=evidence.database_sha256,
            logical_fingerprint=evidence.logical_fingerprint,
            fact_rows=export_counts["fact_market_context_eda.csv"], export_hashes=export_hashes,
            gate_statuses=dict(evidence.gate_statuses),
        )
    finally:
        if temp_root.exists():
            shutil.rmtree(temp_root)
```


Die Pipeline validiert vor und nach der Erzeugung geschuetzte Hashes und den SQLite-Hash. Vorhandene identische Bundles werden als `CACHED_VALID` akzeptiert; ein abweichendes Bundle verlangt einen expliziten kontrollierten Refresh.

## Was fuer Power BI entsteht

Die Pipeline erzeugt unter anderem:

- Fakt-Export fuer Markt-/Kontextdaten
- Kalender- und Dimensionstabellen
- EDA-Tabellen
- Qualitaetsinformationen
- Datenvertrag fuer Beziehungen
- Measure-Vertrag
- Manifestdateien
- reproduzierbare SVG-Grafiken fuer Berichte

Power BI ist damit nicht direkt von Rohdateien abhaengig, sondern bekommt eine vorher kontrollierte, dokumentierte Datenbasis.
