# 01 - Datenpilot und Vollimport

## `src/data_pilot.py` - erst klein pruefen, dann gross laden

Der Datenpilot ist ein kontrollierter Vorabtest. Er prueft mit wenigen Monaten, ob Binance und Coin Metrics technisch und fachlich fuer das Projekt geeignet sind. Er kontrolliert unter anderem Zeitstempel, OHLCV-Werte, erwartete Zeilenzahlen, Checksum-Nachweise, 1h/4h-Konsistenz und den zeitlich sicheren Kontextjoin.

### 4h-Kerzen entstehen nur aus vier vollstaendigen 1h-Kerzen

Originalausschnitt aus `src/data_pilot.py`, Zeilen 451-472:

```python
def aggregate_1h_to_4h(frame: pd.DataFrame) -> pd.DataFrame:
    """Vier abgeschlossene 1h-Kerzen zu einer handelbaren 4h-Kerze aggregieren."""

    if frame.empty:
        return frame.copy()
    ordered = frame.sort_values("timestamp_utc").copy()
    ordered["bucket_utc"] = ordered["timestamp_utc"].dt.floor("4h")
    grouped = ordered.groupby("bucket_utc", sort=True, observed=True)
    complete = grouped.filter(lambda group: len(group) == 4)
    complete_grouped = complete.groupby("bucket_utc", sort=True, observed=True)
    aggregated = complete_grouped.agg(
        open=("open", "first"),
        high=("high", "max"),
        low=("low", "min"),
        close=("close", "last"),
        volume=("volume", "sum"),
        quote_asset_volume=("quote_asset_volume", "sum"),
        number_of_trades=("number_of_trades", "sum"),
        close_time_utc=("close_time_utc", "max"),
        constituent_rows=("timestamp_utc", "size"),
    )
    return aggregated.reset_index().rename(columns={"bucket_utc": "timestamp_utc"})
```


### Einfache Lesefassung mit Kommentaren

```python
# Daten zuerst zeitlich sortieren.
ordered = frame.sort_values("timestamp_utc").copy()

# Jede 1h-Kerze einem 4-Stunden-Block zuordnen.
ordered["bucket_utc"] = ordered["timestamp_utc"].dt.floor("4h")

# Nur Gruppen behalten, die wirklich aus exakt 4 Stunden bestehen.
# Eine unvollstaendige 4h-Kerze wird also nicht erfunden.
complete = grouped.filter(lambda group: len(group) == 4)

# OHLCV-Regel fuer die neue 4h-Kerze:
# open  = erster Open
# high  = hoechstes High
# low   = niedrigstes Low
# close = letzter Close
# Volumen und Trades werden addiert.
aggregated = complete_grouped.agg(...)
```

**Warum wichtig?** Eine fehlende 1h-Kerze darf nicht unsichtbar in einer scheinbar vollstaendigen 4h-Kerze verschwinden.

### Kontextjoin ohne Blick in die Zukunft

Originalausschnitt, Zeilen 829-881:

```python
def join_context_without_lookahead(
    market: pd.DataFrame, context: pd.DataFrame
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Nur zum Kerzenschluss bereits verfuegbaren Tageskontext verbinden."""

    left = market.copy()
    left["decision_time_utc"] = left["close_time_utc"]
    left = left.sort_values("decision_time_utc")
    right = context.sort_values("available_from_utc")
    joined = pd.merge_asof(
        left,
        right,
        left_on="decision_time_utc",
        right_on="available_from_utc",
        direction="backward",
        allow_exact_matches=True,
    )
    context_available = joined["available_from_utc"].notna()
    future_rows = int(
        (
            context_available
            & (joined["available_from_utc"] > joined["decision_time_utc"])
        ).sum()
    )
    staleness_hours = (
        joined.loc[context_available, "decision_time_utc"]
        - joined.loc[context_available, "available_from_utc"]
    ).dt.total_seconds() / 3600
    coverage = float(context_available.mean()) if len(joined) else 0.0
    summary = {
        "market_rows_before_join": int(len(market)),
        "rows_after_join": int(len(joined)),
        "joined_context_rows": int(context_available.sum()),
        "join_coverage_pct": round(coverage * 100, 6),
        "join_row_loss": int(len(market) - len(joined)),
        "future_context_rows": future_rows,
        "max_context_staleness_hours": (
            round(float(staleness_hours.max()), 6)
            if not staleness_hours.empty
            else math.nan
        ),
        "availability_rule": (
            "Coin-Metrics-Tageswert D ist ab D+1 00:00 UTC verfuegbar; "
            "as-of-Join auf Kerzenschluss."
        ),
    }
    summary["join_pass"] = bool(
        len(joined) == len(market)
        and coverage == 1.0
        and future_rows == 0
        and summary["max_context_staleness_hours"] < 48
    )
    return joined, summary
```


**Einfach erklaert:** `merge_asof(... direction="backward")` sucht fuer jede Marktkerze den letzten Kontextwert, der bereits verfuegbar war. Danach prueft der Code zusaetzlich, dass `available_from_utc` niemals spaeter als `decision_time_utc` liegt.

### Gate-0-Entscheidung

Originalausschnitt, Zeilen 965-1014:

```python
def build_gate_decision(
    quality: pd.DataFrame,
    manifest: pd.DataFrame,
    context_quality: dict[str, Any],
    timeframe_comparison: pd.DataFrame,
    join_summary: dict[str, Any],
    candidates: pd.DataFrame,
    history_boundaries: pd.DataFrame,
) -> dict[str, Any]:
    """Gate 0 strikt aus pruefbaren Teilkriterien entscheiden."""

    binance_manifest = manifest[manifest["source"] == "Binance Public Data"]
    criteria = {
        "primaere_marktquelle_reproduzierbar": bool(
            not quality.empty
            and quality["quality_pass"].all()
            and not binance_manifest.empty
            and binance_manifest["provider_checksum_match"].eq(True).all()
        ),
        "ergaenzende_quelle_reproduzierbar": bool(
            context_quality["quality_pass"]
        ),
        "zeitlich_ausgerichtet_ohne_zukunftsdaten": bool(
            join_summary["join_pass"]
        ),
        "zeitrahmen_konsistent": bool(
            not timeframe_comparison.empty
            and timeframe_comparison["timeframe_consistency_pass"].all()
        ),
        "quellenvergleich_dokumentiert": bool(
            {"Binance Public Data", "Coin Metrics Community API"}.issubset(
                set(candidates["source"])
            )
        ),
        "empfohlene_zeitraumgrenzen_erreichbar": bool(
            not history_boundaries.empty
            and history_boundaries["coverage_pass"].all()
        ),
    }
    return {
        "gate": "Gate 0",
        "evaluated_at_utc": utc_now_iso(),
        "criteria": criteria,
        "passed": all(criteria.values()),
        "decision": (
            "bestanden: Vollimport darf als naechstes Arbeitspaket geplant werden"
            if all(criteria.values())
            else "nicht bestanden: Vollimport bleibt gesperrt"
        ),
    }
```


Gate 0 besteht nur, wenn alle Teilkriterien wahr sind. Ein einzelner guter Quellencheck reicht nicht.

---

## `src/full_import.py` - kontrollierter Vollimport 2021-2025

Diese Datei ist die groesste produktive Python-Datei des Projekts. Sie plant und verarbeitet die monatlichen Binance-Archive, validiert Anbieter-Checksummen, erkennt Quellenanomalien, erzeugt nur aus akzeptierten Monaten Interimdaten und speichert einen autoritativen Checkpoint.

### 180 feste Binance-Monatsauftraege

Originalausschnitt, Zeilen 597-644:

```python
def build_binance_tasks(
    config: dict[str, Any], project_root: Path
) -> list[BinanceTask]:
    """Alle 180 Monatsauftraege in stabiler Reihenfolge erzeugen."""

    validate_config(config, project_root)
    binance = config["binance"]
    raw_root = safe_project_path(
        project_root, config["paths"]["raw_root"], required_prefix="data/raw"
    )
    base_url = binance["base_url"].rstrip("/")
    tasks: list[BinanceTask] = []
    months = month_sequence(
        binance["start_utc"], binance["end_exclusive_utc"]
    )
    for symbol in binance["assets"]:
        for month in months:
            filename = f"{symbol}-1h-{month}.zip"
            archive = (
                raw_root
                / "binance"
                / "spot"
                / "monthly"
                / "klines"
                / symbol
                / "1h"
                / filename
            )
            checksum = archive.with_name(f"{filename}.CHECKSUM")
            archive_url = f"{base_url}/{symbol}/1h/{filename}"
            tasks.append(
                BinanceTask(
                    symbol=symbol,
                    month=month,
                    interval="1h",
                    expected_1h_rows=expected_month_rows(
                        month, ONE_HOUR_SECONDS
                    ),
                    expected_4h_rows=expected_month_rows(
                        month, FOUR_HOUR_SECONDS
                    ),
                    archive_url=archive_url,
                    checksum_url=f"{archive_url}.CHECKSUM",
                    archive_path=project_relative(archive, project_root),
                    checksum_path=project_relative(checksum, project_root),
                )
            )
    return sorted(tasks, key=lambda item: (item.symbol, item.month))
```


Bei drei Assets und 60 Monaten entstehen 180 klar definierte Monatsauftraege. Die stabile Sortierung sorgt dafuer, dass derselbe Input immer in derselben Reihenfolge verarbeitet wird.

### Zeitstempel-Grenze Dezember 2024 / Januar 2025

Originalausschnitt, Zeilen 373-380:

```python
def expected_binance_timestamp_unit(month: str) -> str:
    """Verbindliche Binance-Spot-Einheit ausschließlich aus dem Monat ableiten."""

    try:
        parsed = date.fromisoformat(f"{month}-01")
    except (TypeError, ValueError) as exc:
        raise ConfigurationError(f"Ungueltiger Monat: {month}") from exc
    return "ms" if parsed < date(2025, 1, 1) else "us"
```


Bis Dezember 2024 erwartet das Projekt Millisekunden (`ms`), ab Januar 2025 Mikrosekunden (`us`). Diese Regel verhindert, dass unterschiedlich skalierte Zeitstempel still falsch interpretiert werden.

### No-Overwrite beim Veroeffentlichen

Originalausschnitt, Zeilen 1049-1072:

```python
def atomic_promote_no_overwrite(
    temp_path: Path,
    destination: Path,
    *,
    error_path: str | None = None,
) -> None:
    """Temp-Datei atomar verlinken und ein vorhandenes Ziel nie ersetzen."""

    persisted_path = error_path or destination.as_posix()
    if destination.exists():
        raise FileExistsError(
            f"Zieldatei existiert bereits: {persisted_path}"
        )
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.link(temp_path, destination)
    except FileExistsError:
        raise
    except OSError as exc:
        raise SafetyError(
            "Atomare No-Overwrite-Promotion ist auf diesem Dateisystem "
            f"fehlgeschlagen: {persisted_path}"
        ) from exc
    temp_path.unlink()
```


**Einfach erklaert:** Existiert eine autoritative Datei bereits, bricht der Code ab. Neue Daten werden zuerst als Temp-Datei erzeugt und erst danach atomar an den Zielort gesetzt. Dadurch wird ein bestehender Nachweis nicht still ueberschrieben.

## Wichtige Aufgaben von `full_import.py`

- Konfiguration und sichere Projektpfade validieren
- 180 Binance-Monatsarchive + 180 CHECKSUM-Dateien planen
- Cache und Provider-Checksum pruefen
- Binance-ZIPs lesen und Zeitstempel normalisieren
- OHLCV- und Kontinuitaetsregeln je Monat pruefen
- Quellenanomalien dokumentieren
- nur vollstaendige akzeptierte 1h-Monate weiterreichen
- daraus vollstaendige 4h-Kerzen bilden
- Coin-Metrics-Seiten kontrolliert laden und normalisieren
- Checkpoint und Berichtsprojektionen reproduzierbar halten
- abgebrochene oder widerspruechliche Zustaende fail-closed behandeln

## Was die Datei bewusst nicht tut

Sie interpoliert keine fehlenden Kerzen, startet keinen Backtest und veraendert keine bereits vorhandenen autoritativen Ausgaben ohne explizit erlaubten Pfad.
