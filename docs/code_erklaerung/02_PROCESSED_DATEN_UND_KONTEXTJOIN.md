# 02 - Processed-Daten und Kontextjoin

## `src/processed_pipeline.py`

Diese Stufe macht aus den akzeptierten Interimdaten die kanonische Analysebasis. Sie verbindet Markt- und Kontextdaten und setzt nach Datenluecken klare Segmentgrenzen.

### Warum Segmente?

Wenn Monate ausgeschlossen wurden, darf ein rollender Indikator, eine Rendite oder spaeter ein Trade nicht ueber diese Luecke hinweg rechnen. Deshalb werden zusammenhaengende gueltige Monate zu Segmenten zusammengefasst.

Originalausschnitt `build_month_segments`, Zeilen 314-336:

```python
def build_month_segments(allowed_months: Sequence[str]) -> tuple[dict[str, str], list[dict[str, Any]]]:
    """Gemeinsame Monatsmaske in zusammenhaengende Segmente zerlegen."""

    ordered = sorted(set(allowed_months))
    if len(ordered) != len(allowed_months):
        raise IntegrityError("Zulaessige Monatsmaske enthaelt Duplikate.")
    mapping: dict[str, str] = {}
    segments: list[dict[str, Any]] = []
    for month in ordered:
        if not segments or month != _next_month(segments[-1]["last_month"]):
            segments.append(
                {
                    "segment_id": f"SEGMENT_{len(segments) + 1:03d}",
                    "first_month": month,
                    "last_month": month,
                    "allowed_month_count": 1,
                }
            )
        else:
            segments[-1]["last_month"] = month
            segments[-1]["allowed_month_count"] += 1
        mapping[month] = segments[-1]["segment_id"]
    return mapping, segments
```


**Merksatz:** Eine Datenluecke beendet ein Segment. Nach der Luecke beginnt der Zustand neu.

### D+1-As-of-Join

Originalausschnitt `asof_join_d1`, Zeilen 714-762:

```python
def asof_join_d1(
    market_rows: Sequence[dict[str, Any]], contexts: Sequence[ContextRow]
) -> list[dict[str, str]]:
    """Nur bereits verfuegbaren D+1-Kontext an Marktzeilen haengen."""

    ordered_contexts = sorted(contexts, key=lambda row: row.available_d1)
    available = [row.available_d1 for row in ordered_contexts]
    if len(set(available)) != len(available):
        raise IntegrityError("Coin-Metrics-D+1-Verfuegbarkeit ist nicht eindeutig.")
    joined: list[dict[str, str]] = []
    for market in market_rows:
        decision_time = market["_decision_time"]
        index = bisect.bisect_right(available, decision_time) - 1
        output = {key: str(value) for key, value in market.items() if not key.startswith("_")}
        if index < 0:
            context_values = {
                "context_match_status": "unmatched",
                "context_source": "",
                "context_asset": "",
                "context_source_timestamp_utc": "",
                "context_available_from_utc_d1": "",
                "context_available_from_utc_d2": "",
                "context_price_usd": "",
                "context_market_cap_usd": "",
                "context_tx_count": "",
                "context_active_address_count": "",
                "context_age_seconds": "",
            }
        else:
            context = ordered_contexts[index]
            if context.available_d1 > decision_time:
                raise IntegrityError("As-of-Join wuerde Zukunftskontext verwenden.")
            age_seconds = int((decision_time - context.source_timestamp).total_seconds())
            context_values = {
                "context_match_status": "matched_d1_asof",
                "context_source": "coin_metrics_community_api",
                "context_asset": context.asset,
                "context_source_timestamp_utc": format_utc(context.source_timestamp),
                "context_available_from_utc_d1": format_utc(context.available_d1),
                "context_available_from_utc_d2": format_utc(context.available_d2),
                "context_price_usd": context.price_usd,
                "context_market_cap_usd": context.market_cap_usd,
                "context_tx_count": context.tx_count,
                "context_active_address_count": context.active_address_count,
                "context_age_seconds": str(age_seconds),
            }
        output.update(context_values)
        joined.append(output)
    return joined
```


### Kommentierte Lesefassung

```python
# Linke Seite: Marktkerzen mit ihrem Entscheidungszeitpunkt.
left = ...

# Rechte Seite: Coin-Metrics-Kontext, sortiert nach dem Zeitpunkt,
# ab dem der Tageswert konservativ als verfuegbar gilt.
right = ...

# Rueckwaerts-Join: Nimm nur den juengsten Kontext,
# dessen Verfuegbarkeit nicht in der Zukunft liegt.
joined = pd.merge_asof(
    left,
    right,
    left_on="decision_time_utc",
    right_on="context_available_from_utc_d1",
    direction="backward",
    allow_exact_matches=True,
)
```

D+2 wird als separate Verfuegbarkeitsvariante mitgefuehrt. Sie wird nicht einfach aus einem bereits verbundenen D+1-Wert verschoben.

### Ausgabeerzeugung

Originalausschnitt aus `build_phase1c_outputs`, Zeilen 1047-1133:

```python
def build_phase1c_outputs(project_root: Path, config_path: Path) -> BuildResult:
    """Validieren, deterministisch bauen und atomar erstellen/wiederverwenden."""

    root = project_root.resolve()
    validated = validate_phase1b_inputs(root, config_path)
    processed_root = safe_project_path(
        root,
        validated.config["paths"]["processed_root"],
        required_prefix="data/processed",
    )
    report_dir = safe_project_path(root, "reports/processed", required_prefix="reports")
    table_1h = processed_root / "market_context_1h.csv"
    table_4h = processed_root / "market_context_4h.csv"
    dictionary_path = report_dir / "PHASE1C_DATA_DICTIONARY.md"
    join_report_path = report_dir / "join_quality_summary.json"
    quality_report_path = report_dir / "PHASE1C_QUALITY_REPORT.md"
    manifest_path = report_dir / "processed_manifest.csv"
    destinations = (
        table_1h,
        table_4h,
        dictionary_path,
        join_report_path,
        quality_report_path,
        manifest_path,
    )
    _ensure_known_output_scope(
        project_root=root,
        processed_root=processed_root,
        report_dir=report_dir,
        destinations=destinations,
    )

    joined = {
        timeframe: asof_join_d1(validated.market_rows[timeframe], validated.contexts)
        for timeframe in ("1h", "4h")
# ... Ausschnitt gekuerzt; Originalfunktion ist laenger ...
    for artifact_id, path, artifact_type, schema_id, row_count in artifact_metadata:
        manifest_rows.append(
            {
                "artifact_id": artifact_id,
                "artifact_path": project_relative(path, root),
                "artifact_type": artifact_type,
                "schema_id": schema_id,
                "row_count": row_count,
                "sha256": sha256_bytes(bytes_by_path[path]),
                "source_checkpoint": "reports/full_import/execution_checkpoint.json",
                "source_checkpoint_sha256": validated.checkpoint_sha256,
                "source_checkpoint_generation_id": validated.checkpoint["generation_id"],
                "source_checkpoint_run_id": validated.checkpoint["run_id"],
                "phase1c_policy_id": PHASE1C_POLICY_ID,
                "phase1c_policy_fingerprint": policy_fingerprint,
            }
        )
    bytes_by_path[manifest_path] = canonical_csv_bytes(MANIFEST_FIELDS, manifest_rows)

    statuses: dict[str, str] = {}
    for destination in destinations:
        statuses[project_relative(destination, root)] = write_generated_file_cached(
            destination,
            bytes_by_path[destination],
            error_path=project_relative(destination, root),
        )
    artifacts = [
        {
            "path": project_relative(path, root),
            "sha256": sha256_bytes(bytes_by_path[path]),
        }
        for path in destinations
    ]
    return BuildResult(statuses=statuses, summary=summary, artifacts=artifacts)
```


Die Funktion validiert zuerst alle Eingaben, erzeugt die neuen Dateien in einem temporaeren Bereich und publiziert nur einen vollstaendigen, konsistenten Satz. Bereits vorhandene byteidentische Ausgaben koennen wiederverwendet werden; abweichende bestehende Dateien werden nicht ueberschrieben.

## Ergebnis dieser Stufe

- `market_context_1h.csv`: kanonische 1h-Marktdaten + Kontext
- `market_context_4h.csv`: kanonische 4h-Marktdaten + Kontext
- Segment-ID pro Zeile
- `decision_time_utc`
- D+1- und D+2-Verfuegbarkeitsinformationen
- Qualitaets- und Manifestnachweise

## Was noch nicht berechnet wird

Diese Stufe berechnet bewusst noch keine Trading-Signale, Positionen oder Backtest-Ergebnisse. Sie stellt nur die saubere, zeitlich kontrollierte Basis bereit.
