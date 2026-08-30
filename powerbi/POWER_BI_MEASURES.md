# Power-BI-Measures Phase 1C-C

## Feldklassen

- Basisspalten: OHLC, Volumen, Tradeanzahl, Zeitfelder, Segment und Coin-Metrics-Kontext aus der geprueften SQL-Basis.
- Berechnete Exportspalten: Kerzenkoerper, Kerzenspanne, Schatten, Taker-Buy-Anteil, Kontextalter und strikt segmentgebundene Close-to-close-Rendite.
- Measures: ausschliesslich deskriptive Aggregationen im aktuellen Power-BI-Filterkontext.

## Geplante Measures

| Measure | DAX | Format | Filterkontext und Interpretation |
|---|---|---|---|
| Marktzeilen | `COUNTROWS(fact_market_context_eda)` | Ganze Zahl | Anzahl akzeptierter Kerzen im aktuellen Asset-, Zeitraum-, Datums- und Segmentfilter |
| Fruehester Zeitpunkt | `MIN(fact_market_context_eda[timestamp_utc])` | `yyyy-mm-dd hh:mm` | erste enthaltene UTC-Kerze im aktuellen Filter |
| Spaetester Zeitpunkt | `MAX(fact_market_context_eda[timestamp_utc])` | `yyyy-mm-dd hh:mm` | letzte enthaltene UTC-Kerze im aktuellen Filter |
| Durchschnitt Basisvolumen | `AVERAGE(fact_market_context_eda[volume])` | Dezimalzahl | arithmetisches Mittel des Basisvolumens; Einheit ist assetabhaengig |
| Median Basisvolumen | `MEDIAN(fact_market_context_eda[volume])` | Dezimalzahl | robuster Mittelpunkt des Basisvolumens |
| Durchschnitt Tradeanzahl | `AVERAGE(fact_market_context_eda[number_of_trades])` | Dezimalzahl | mittlere Trades je Kerze |
| Durchschnitt Kerzenspanne | `AVERAGE(fact_market_context_eda[candle_range])` | `0.0000%` | mittlere relative High-Low-Spanne, rein deskriptiv |
| Durchschnitt Kerzenkoerper | `AVERAGE(fact_market_context_eda[candle_body_return])` | `0.0000%` | mittlere relative Open-Close-Aenderung, keine Empfehlung |
| Durchschnitt Kontextalter seit Quellzeitpunkt | `AVERAGE(fact_market_context_eda[context_age_hours])` | `0.00 h` | `decision_time_utc - context_source_timestamp_utc`; 1h-Kontrollbereich 24-47 h |
| Durchschnitt Kontextalter seit D+1 | `AVERAGE(fact_market_context_eda[context_age_since_d1_hours])` | `0.00 h` | `decision_time_utc - context_available_from_utc_d1`; 1h-Kontrollbereich 0-23 h |
| Globale akzeptierte Scope-Abdeckung | `DIVIDE(CALCULATE(COUNTROWS(dim_calendar), REMOVEFILTERS(dim_calendar), dim_calendar[is_accepted_date] = TRUE()), CALCULATE(COUNTROWS(dim_calendar), REMOVEFILTERS(dim_calendar)))` | `0.00%` | immer 88,39 % des gesamten Projektscopes; bewusst unabhaengig von Kalender-, Asset- und Visualfiltern |
| Globale ausgeschlossene Scope-Abdeckung | `DIVIDE(CALCULATE(COUNTROWS(dim_calendar), REMOVEFILTERS(dim_calendar), dim_calendar[is_excluded_month] = TRUE()), CALCULATE(COUNTROWS(dim_calendar), REMOVEFILTERS(dim_calendar)))` | `0.00%` | immer 11,61 % des gesamten Projektscopes; bewusst unabhaengig von Kalender-, Asset- und Visualfiltern |
| Akzeptierte Abdeckung im Kalenderfilter | `DIVIDE(CALCULATE(COUNTROWS(dim_calendar), dim_calendar[is_accepted_date] = TRUE()), COUNTROWS(dim_calendar))` | `0.00%` | reagiert auf Jahr, Quartal, Monat und Datum; ein Assetfilter aendert sie wegen der gemeinsamen Assetmaske und einseitiger Beziehungen bewusst nicht |
| Ausgeschlossene Abdeckung im Kalenderfilter | `DIVIDE(CALCULATE(COUNTROWS(dim_calendar), dim_calendar[is_excluded_month] = TRUE()), COUNTROWS(dim_calendar))` | `0.00%` | reagiert auf Jahr, Quartal, Monat und Datum; ausgeschlossene Monate bleiben trotz fehlender Fakten sichtbar |
| Segmentanzahl | `DISTINCTCOUNT(fact_market_context_eda[segment_key])` | Ganze Zahl | Anzahl gueltiger Segmente im aktuellen Filter |

Keine Measure stellt Renditeperformance, Signalqualitaet oder eine Tradingempfehlung dar.
