# Power-BI-Datenvertrag Phase 1C-C

## Vertragsstatus

Version: `powerbi_model_v2`. Dieser Vertrag beschreibt einen lokalen, deterministischen CSV-Import. Er baut kein Dashboard und keine `.pbix`-Datei. G1-13 und Gate 1 bleiben bis zur unabhaengigen Abnahme `NOT_EVALUATED`.

## Sternschema

| Tabelle | Rolle | Koernung | Primaerschluessel | Zeilen |
|---|---|---|---|---:|
| `fact_market_context_eda.csv` | Fakt | eine akzeptierte, vollstaendig geschlossene Kerze je Asset und Zeitrahmen | `market_context_key` (Geschaeftsschluessel: Asset + Zeitrahmen + `timestamp_utc`) | 145260 |
| `dim_asset.csv` | Dimension | ein Asset | `asset_key` | 3 |
| `dim_segment.csv` | Dimension | ein gemeinsames gueltiges Zeitsegment | `segment_key` | 5 |
| `dim_calendar.csv` | Dimension | jeder Kalendertag im vollstaendigen Projektscope 2021-01-01 bis 2025-12-31 | `date_key` | 1826 |
| `dim_timeframe.csv` | Dimension | ein Zeitrahmen | `timeframe_key` | 2 |

## Beziehungen

| Von | Nach | Kardinalitaet | Filterrichtung | Aktiv |
|---|---|---|---|---|
| `dim_asset[asset_key]` | `fact_market_context_eda[asset_key]` | 1:n | Dimension zu Fakt | Ja |
| `dim_segment[segment_key]` | `fact_market_context_eda[segment_key]` | 1:n | Dimension zu Fakt | Ja |
| `dim_calendar[date_key]` | `fact_market_context_eda[date_key]` | 1:n | Dimension zu Fakt | Ja |
| `dim_timeframe[timeframe_key]` | `fact_market_context_eda[timeframe_key]` | 1:n | Dimension zu Fakt | Ja |

Bidirektionale Beziehungen sind nicht erlaubt. Alle vier Fremdschluessel sind obligatorisch und ohne verwaiste Werte.

## Faktspalten und Datentypen

Die feste Spaltenreihenfolge lautet: `market_context_key, asset_key, segment_key, date_key, timeframe_key, symbol, timeframe, timestamp_utc, close_time_utc, decision_time_utc, segment_id, open, high, low, close, volume, quote_asset_volume, number_of_trades, taker_buy_base_volume, taker_buy_quote_volume, constituent_rows, market_source, market_quality_status, context_match_status, context_source_timestamp_utc, context_available_from_utc_d1, context_available_from_utc_d2, context_price_usd, context_market_cap_usd, context_tx_count, context_active_address_count, context_age_hours, context_age_since_d1_hours, calendar_year, calendar_month, candle_body_return, candle_range, upper_wick_relative, lower_wick_relative, taker_buy_share, close_to_close_return`.

| Spaltengruppe | Power-BI-Typ | Sichtbarkeit | Nullregel |
|---|---|---|---|
| Schluessel (`*_key`) | Ganze Zahl | technische Schluessel ausblenden | nie NULL |
| UTC-Zeitfelder | Datum/Uhrzeit nach Import als UTC | `timestamp_utc` sichtbar, technische Verfuegbarkeitsfelder bei Bedarf | nie NULL |
| OHLC, Volumen, Kontextmetriken | Dezimalzahl | sichtbar | nie NULL |
| `number_of_trades`, `constituent_rows` | Ganze Zahl | sichtbar | `constituent_rows` nur 4h |
| Taker-Buy-Felder und `taker_buy_share` | Dezimalzahl | sichtbar | nur 1h beziehungsweise Nenner > 0 |
| deskriptive Kerzenfelder | Dezimalzahl | sichtbar | Close-to-close an Segmentstart oder Luecke NULL |
| `context_age_hours` | Dezimalzahl in Stunden | sichtbar | `decision_time_utc - context_source_timestamp_utc`; nie NULL |
| `context_age_since_d1_hours` | Dezimalzahl in Stunden | sichtbar | `decision_time_utc - context_available_from_utc_d1`; nie NULL |
| Herkunfts- und Statusfelder | Text | technische Felder standardmaessig ausblenden | nie NULL |

## Vollstaendige Kalenderdimension

`dim_calendar.csv` enthaelt lueckenlos genau 1.826 Tage. Die 212 Tage der sieben ausgeschlossenen Monate bleiben auf Zeitachsen sichtbar und tragen `is_excluded_month = 1`, `is_accepted_date = 0`, einen Ausschlussstatus und einen Ausschlussgrund. Die uebrigen 1.614 Tage tragen `is_accepted_date = 1`. Ausgeschlossene Tage besitzen keine Faktzeilen. `year_month_sort` sortiert `month_label`; `month_name_sort` sortiert `month_name_de`.

## Sortierung und Zeitzone

Faktzeilen sind nach `symbol`, `timeframe`, `timestamp_utc` stabil sortiert. Dimensionen verwenden ihre jeweilige `*_sort`-Spalte. Alle Zeitfelder sind kanonische ISO-8601-Zeitstempel in UTC. CSV-Dateien verwenden UTF-8 und LF.

## Schutzgrenzen

Die Faktentabelle enthaelt keine ausgeschlossenen Monate und keinen Zukunftskontext. `close_to_close_return` wird nur innerhalb desselben Assets, Zeitrahmens und Segments bei exakt 1h beziehungsweise 4h Abstand berechnet. Es gibt keine Forward Returns, Labels, Signale, Positionen oder Performancekennzahlen.
