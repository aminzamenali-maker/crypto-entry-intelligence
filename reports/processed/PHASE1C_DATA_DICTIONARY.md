# Phase 1C-A Datenwoerterbuch

Schema: `phase1c_data_dictionary_v1`. Policy: `phase1c_a_canonical_asof_d1_v1`.

## Tabellenkoernung und Primaerschluessel

Eine Zeile entspricht genau einem Asset und einer vollstaendigen Binance-Kerze. Die Tabellen `market_context_1h.csv` und `market_context_4h.csv` sind getrennt. Der zusammengesetzte Primaerschluessel lautet `(symbol, timeframe, timestamp_utc)`.

## CSV-Vertrag

UTF-8, Komma als Trennzeichen, LF-Zeilenenden, Punkt als Dezimalzeichen, keine Tausendertrennzeichen. UTC wird timezone-aware als `YYYY-MM-DDTHH:MM:SS.ffffffZ` serialisiert. Ein Nullwert ist ein leeres CSV-Feld; in den realen Ausgaben gibt es wegen der vollstaendigen D+1-Matches keine Kontext-Nullwerte.

## Gemeinsame Felder

| Feld | Typ | Fachliche Bedeutung |
|---|---|---|
| `symbol` | Text | Assetpaar; BTCUSDT, ETHUSDT oder SOLUSDT. |
| `timeframe` | Text | Kanonischer Zeitrahmen 1h oder 4h. |
| `timestamp_utc` | UTC-Zeit | Beginn der vollstaendigen Marktkerze. |
| `close_time_utc` | UTC-Zeit | Letzter in der Quelle enthaltener Zeitpunkt der Kerze. |
| `decision_time_utc` | UTC-Zeit | Erster Zeitpunkt nach vollstaendig abgeschlossenem Kerzenschluss. |
| `segment_id` | Text | Gemeinsames zusammenhaengendes Zeitsegment; Reset nach jeder Monatsluecke. |
| `open/high/low/close` | Dezimal | Handelbare Binance-Spot-OHLC-Werte; keine Heikin-Ashi-Preise. |
| `volume` | Dezimal | Gehandeltes Basisasset-Volumen. |
| `quote_asset_volume` | Dezimal | Gehandeltes Quote-Asset-Volumen. |
| `number_of_trades` | Ganzzahl | Anzahl Trades in der Kerze. |
| `market_source` | Text | Binance-Interimherkunft beziehungsweise vollstaendige 1h-Ableitung. |
| `market_timestamp_unit` | Text | ms bis 2024-12, us ab 2025-01. |
| `market_quality_status` | Text | Nur akzeptierte vollstaendige Phase-1B-Monate. |
| `context_match_status` | Text | matched_d1_asof oder unmatched. |
| `context_source` | Text | Coin Metrics Community API bei einem Match. |
| `context_asset` | Text | Kontextasset btc. |
| `context_source_timestamp_utc` | UTC-Zeit | Quelltag des verwendeten Kontextwertes. |
| `context_available_from_utc_d1` | UTC-Zeit | Konservativ angenommene D+1-Verfuegbarkeit; muss <= decision_time sein. |
| `context_available_from_utc_d2` | UTC-Zeit | Separat erhaltener D+2-Zeitpunkt fuer spaetere Sensitivitaet. |
| `context_price_usd` | Dezimal | Coin-Metrics-Metrik PriceUSD. |
| `context_market_cap_usd` | Dezimal | Coin-Metrics-Metrik CapMrktCurUSD. |
| `context_tx_count` | Dezimal | Coin-Metrics-Metrik TxCnt. |
| `context_active_address_count` | Dezimal | Coin-Metrics-Metrik AdrActCnt. |
| `context_age_seconds` | Ganzzahl | decision_time minus Kontext-Quellzeitpunkt in Sekunden. |

## Nur 1h

| Feld | Typ | Fachliche Bedeutung |
|---|---|---|
| `taker_buy_base_volume` | Dezimal | Taker-Buy-Volumen im Basisasset. |
| `taker_buy_quote_volume` | Dezimal | Taker-Buy-Volumen im Quote-Asset. |

## Nur 4h

| Feld | Typ | Fachliche Bedeutung |
|---|---|---|
| `constituent_rows` | Ganzzahl | Exakt vier vollstaendige aufeinanderfolgende 1h-Kerzen. |

## Leakage- und Lueckenregel

Der D+1-Kontext wird nicht ueber den Quelltag verbunden, sondern nur dann, wenn `context_available_from_utc_d1 <= decision_time_utc` gilt. D+1 ist eine konservative methodische Annahme, keine bestaetigte historische Veroeffentlichungsgarantie. D+2 bleibt fuer eine spaetere Sensitivitaet erhalten.

Ueber Segmentgrenzen duerfen spaeter keine Renditen, rollenden Indikatoren, Signale oder Positionen fortgefuehrt werden. Phase 1C-A berechnet davon noch nichts.
