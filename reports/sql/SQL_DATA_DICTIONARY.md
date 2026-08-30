# Phase 1C-B SQL-Datenwoerterbuch

## Tabellen

- `dim_asset`: exakt drei Handelspaare mit stabilem technischem Schlüssel.
- `dim_segment`: exakt fünf lückenfreie Analyseabschnitte; die Beschreibung dokumentiert jeden Reset an einer ausgeschlossenen Monatsgrenze.
- `fact_market_context`: eine Zeile je `(symbol, timeframe, timestamp_utc)`; Marktkerze, Entscheidungszeit, Segment, Quelle und D+1-/D+2-Kontext.
- `pipeline_metadata`: Policy-, Schema-, Gate- und Quellhashbindung ohne absolute Pfade oder Zugangsdaten.

Der eindeutige Geschäftsschlüssel `(symbol, timeframe, timestamp_utc)` verhindert doppelte Kerzen. Fremdschlüssel verbinden jede Faktenzeile ausschließlich mit einem erlaubten Asset und einem der fünf dokumentierten Segmente.

## Kern-Views

- `vw_market_context_1h` und `vw_market_context_4h`: geprüfte Zeitschnittansichten.
- `vw_asset_timeframe_coverage`: Menge, erste/letzte Zeit und Segmentzahl je Asset und Zeitrahmen.
- `vw_segment_coverage`: dieselben Nachweise je Segment.
- `vw_context_freshness`: minimale, maximale und mittlere Kontextalterung in Stunden.
- `vw_data_quality_checks`: Duplikate, Join-Abdeckung, Zukunftskontext, Statusdomänen und Fremdschlüssel.

## Schutzregeln

UTC-Zeiten bleiben kanonische ISO-Z-Werte. D+1 muss spätestens zur Entscheidungszeit verfügbar sein; D+2 bleibt getrennt. 4h-Zeilen müssen genau vier 1h-Bestandteile besitzen, während Taker-Felder ausschließlich für 1h gefüllt sind. Ausgeschlossene Monate werden bereits vor dem SQL-Aufbau abgelehnt.

Das festbreite Format `YYYY-MM-DDTHH:MM:SS.ffffffZ` ist lexikografisch zugleich chronologisch sortierbar. SQL ergänzt keine fehlenden Zeilen und setzt keine Zeitreihe über eine Segmentgrenze fort.

Der physische Datei-Hash schützt die konkret erzeugten SQLite-Bytes. Der stabile logische Fingerprint schützt zusätzlich Schemaobjekte, View-Definitionen, sortierte Tabellenzeilen, Objektzählungen und relevante PRAGMA-Einstellungen. Er ist deshalb der maßgebliche Reproduzierbarkeitsnachweis, wenn SQLite intern andere, aber fachlich gleichwertige Bytes erzeugt.
