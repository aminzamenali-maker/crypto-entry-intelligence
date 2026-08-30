# 08 - Datei-fuer-Datei-Referenz

## Python

| Datei | Aufgabe | Zeilen | Funktionen |
|---|---|---:|---:|
| `src/__init__.py` | Kennzeichnet `src` als Python-Paket; enthaelt keine Projektlogik. | 1 | 0 |
| `src/backtest_contract.py` | Prueft die vorregistrierte Backtest-Methode read-only, bevor Ergebnisse berechnet werden. | 593 | 23 |
| `src/backtest_pipeline.py` | Berechnet Features, fuenf Signale, Baselines, Trades, Kosten und Development/Validation-Ergebnisse. | 868 | 41 |
| `src/data_pilot.py` | Kleiner Quellenpilot: Download/Cache, Qualitaetspruefung, 4h-Aggregation, Coin-Metrics-Kontext, Gate 0. | 1,381 | 28 |
| `src/eda_powerbi_pipeline.py` | Deskriptive Analyse, Tabellen/Visual-Artefakte und Power-BI-Daten-/Measure-Vertraege. | 1,818 | 56 |
| `src/final_test_once.py` | Geschuetzter Runner fuer den finalen Zeitraum 2024-2025 genau einmal. | 665 | 19 |
| `src/full_import.py` | Vollimport 2021-2025 mit Checksummen, Anomalieerkennung, Checkpoint, No-Overwrite und Interimdaten. | 3,927 | 81 |
| `src/processed_pipeline.py` | Validiert Phase 1B, baut Segmentmaske und kanonische 1h/4h-Processed-Daten mit D+1/D+2-Kontext. | 1,169 | 27 |
| `src/sql_pipeline.py` | Baut und validiert das reproduzierbare SQLite-Modell aus Processed-Daten. | 789 | 20 |

## SQL

| Datei | Aufgabe |
|---|---|
| `sql/001_schema.sql` | Tabellen, Constraints, Fremdschluessel und Indizes |
| `sql/002_views.sql` | Analyse- und Qualitaets-Views |

## JSON

| Datei | Aufgabe |
|---|---|
| `config/data_pilot.json` | Gate-0-Pilot: Quellen, Testmonate, Zeitrahmen und Kandidatenvergleich. |
| `config/full_import.json` | Vollimport: Zeitraum, Assets, Pfade, Netzwerk- und No-Overwrite-Regeln. |
| `config/backtest.json` | Phase-2A-Vertrag: Markt, Zeitrahmen, Features, Signale, Kosten, Splits und Baselines. |
| `config/backtest_phase2b.json` | Phase-2B-Offline-Implementierung und versiegelter Final Test. |
| `config/final_test_once.json` | Genau-einmal-Finaltest mit Commit-/Hash-/Git-Schutz. |

## Welche Datei sollte ein Tutor zuerst lesen?

1. `config/backtest.json` - Methode in strukturierter Form
2. `src/backtest_pipeline.py` - Umsetzung der Signale und Trades
3. `src/processed_pipeline.py` - Nachweis gegen Zukunftsinformationen und Luecken
4. `sql/001_schema.sql` / `002_views.sql` - Datenmodell
5. `src/eda_powerbi_pipeline.py` - Power-BI-Ausgabe
6. `src/full_import.py` nur dann tiefer, wenn Import-/Qualitaetssicherung im Detail interessiert

## Schnelle Zuordnung typischer Rueckfragen

| Rueckfrage | Datei(en) |
|---|---|
| Wo werden die fuenf Signale definiert? | `config/backtest.json`, `src/backtest_pipeline.py` |
| Wie wird Look-ahead verhindert? | `src/data_pilot.py`, `src/processed_pipeline.py`, `src/backtest_pipeline.py` |
| Wie entstehen 4h-Kerzen? | `src/data_pilot.py`, `src/full_import.py` |
| Wie werden Kosten berechnet? | `config/backtest.json`, `src/backtest_pipeline.py` |
| Wo stehen die Zeit-Splits? | `config/backtest.json` |
| Wie ist SQL aufgebaut? | `sql/001_schema.sql`, `sql/002_views.sql`, `src/sql_pipeline.py` |
| Wie kommt es nach Power BI? | `src/eda_powerbi_pipeline.py`, `powerbi/POWER_BI_DATA_CONTRACT.md` |
| Warum darf Final Test nicht mehrfach laufen? | `config/final_test_once.json`, `src/final_test_once.py` |
