# Phase-1C-C EDA-Datenwoerterbuch

## Koernungen

| Datei | Koernung | Zeilen |
|---|---|---:|
| `coverage_by_asset_timeframe_year_segment.csv` | Asset x Zeitrahmen x Kalenderjahr x Segment | 48 |
| `descriptive_stats_by_asset_timeframe.csv` | Asset x Zeitrahmen x Kennzahl | 72 |
| `annual_activity.csv` | Asset x Zeitrahmen x Kalenderjahr | 30 |
| `segment_comparison.csv` | Asset x Zeitrahmen x Segment | 30 |
| `context_metrics_summary.csv` | eindeutiger Coin-Metrics-Quelltag x Kontextkennzahl, danach aggregiert | 4 |
| `context_age_summary.csv` | Asset x Zeitrahmen x Kontextalterdefinition | 12 |
| `gaps_and_exclusions.csv` | dokumentierte Qualitaets- oder Ausschlussaussage | 15 |

## Deskriptive Felder

- `candle_body_return = close / open - 1`
- `candle_range = high / low - 1`
- `upper_wick_relative = (high - max(open, close)) / open`
- `lower_wick_relative = (min(open, close) - low) / open`
- `taker_buy_share = taker_buy_quote_volume / quote_asset_volume`, nur bei Nenner > 0 und nur fuer 1h
- `context_age_hours = decision_time_utc - context_source_timestamp_utc`; Alter seit dem Coin-Metrics-Quellzeitpunkt, nicht seit D+1
- `context_age_since_d1_hours = decision_time_utc - context_available_from_utc_d1`; getrenntes Alter seit angenommener D+1-Verfuegbarkeit
- `close_to_close_return = close / vorheriger_close - 1`, nur bei gleichem Asset, Zeitrahmen und Segment sowie exakt 1h/4h Abstand

## Kalenderdimension

Der Power-BI-Export besitzt eine lueckenlose Kalenderdimension von 2021-01-01 bis 2025-12-31 mit 1.826 Tagen. 1.614 Tage sind akzeptiert; 212 Tage aus sieben ausgeschlossenen Monaten bleiben als ausgeschlossene Kalendertage ohne Faktzeilen sichtbar.

## Statistiken

Jede Kennzahl berichtet Anzahl, Mittelwert, Stichproben-Standardabweichung, Minimum, lineare 25-/50-/75-Prozent-Quantile, Maximum und Nullanzahl. Extremwerte werden weder entfernt noch winsorisiert.

## Abbildungen

Es entstehen 6 deterministische SVG-Dateien. Jede enthaelt Titel, Achsen, Einheit, Quelle und Ausschlusshinweis. Die Renditeverteilung schneidet keine Werte ab: Werte ausserhalb +/-5 % werden fuer die Darstellung in den jeweiligen Randbin gezaehlt; die Tabellen behalten die unveraenderten Werte.
