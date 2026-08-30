# 00 - Code-Ueberblick

## Das Projekt in einem Satz

Das Projekt nimmt historische Krypto-Marktdaten, prueft deren Qualitaet, baut daraus eine saubere Analysebasis, berechnet vorab festgelegte Signale, simuliert historische Trades mit Kosten und stellt die Ergebnisse anschliessend in Power BI dar.

## Datenfluss

```text
Datenquellen
  |-- Binance Public Data: BTCUSDT / ETHUSDT / SOLUSDT, 1h
  `-- Coin Metrics: taeglicher BTC-Kontext
          |
          v
src/data_pilot.py
  kleiner Quellen- und Qualitaetstest (Gate 0)
          |
          v
src/full_import.py
  kontrollierter Vollimport 2021-2025
          |
          v
src/processed_pipeline.py
  gemeinsame Monatsmaske + Segmente + D+1-Kontextjoin
          |
          v
src/sql_pipeline.py + sql/*.sql
  reproduzierbares SQLite-Modell
          |
          +-------------------------> src/eda_powerbi_pipeline.py
          |                            EDA + Power-BI-Export
          |
          v
src/backtest_contract.py
  prueft die vorregistrierte Backtest-Methode
          |
          v
src/backtest_pipeline.py
  Features -> 5 Signale -> Trades -> Kosten -> Ergebnisse
          |
          v
src/final_test_once.py
  finaler Test 2024-2025 genau einmal
          |
          v
Power BI / Praesentation
```

## Produktive Python-Dateien

| Datei | Zeilen | Funktionen | Klassen |
|---|---:|---:|---:|
| `src/__init__.py` | 1 | 0 | 0 |
| `src/backtest_contract.py` | 593 | 23 | 1 |
| `src/backtest_pipeline.py` | 868 | 41 | 2 |
| `src/data_pilot.py` | 1,381 | 28 | 2 |
| `src/eda_powerbi_pipeline.py` | 1,818 | 56 | 4 |
| `src/final_test_once.py` | 665 | 19 | 2 |
| `src/full_import.py` | 3,927 | 81 | 6 |
| `src/processed_pipeline.py` | 1,169 | 27 | 3 |
| `src/sql_pipeline.py` | 789 | 20 | 2 |

## Die wichtigsten Schutzprinzipien im Code

- **Keine Zukunftsinformationen:** Kontext wird nur verwendet, wenn er zum Entscheidungszeitpunkt bereits verfuegbar war.
- **Keine kuenstliche Auffuellung:** problematische Monate werden ausgeschlossen statt interpoliert.
- **No-Overwrite:** vorhandene autoritative Ausgaben werden nicht still ueberschrieben.
- **Zeitliche Trennung:** Development, Validation und Final Test werden getrennt behandelt.
- **Final Test genau einmal:** ein Status-/Receipt-Mechanismus verhindert stilles Wiederholen.
- **Reproduzierbarkeit:** Hashes, Manifeste, feste Schemata und deterministische Sortierung werden intensiv geprueft.
- **Keine Live-Trading-Logik:** das Projekt arbeitet historisch und offline; keine Orders, kein Hebel, keine Shorts.

## Wie man die Dateien beim Erklaeren zusammenfasst

> `data_pilot.py` prueft zuerst, ob die Quellen geeignet sind. `full_import.py` fuehrt danach den kontrollierten Vollimport aus. `processed_pipeline.py` erstellt die saubere Analysebasis und verhindert Zukunftsinformationen beim Kontextjoin. `sql_pipeline.py` speichert diese Basis reproduzierbar in SQLite. `backtest_contract.py` prueft die festgelegte Methode, `backtest_pipeline.py` berechnet Signale und Backtests, `final_test_once.py` fuehrt den geschuetzten finalen Test aus, und `eda_powerbi_pipeline.py` bereitet die Daten fuer Analyse und Power BI auf.
