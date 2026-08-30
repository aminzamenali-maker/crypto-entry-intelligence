# Nachweis der DSP-Anforderungen

Quelle: offizielle Projektbeschreibung.

Die Tabelle zeigt, wie jede offizielle Anforderung im Projekt nachgewiesen wird und welcher Stand aktuell erreicht ist.

| ID | Offizielle Anforderung | Nachweis im Projekt | Status |
|---|---|---|---|
| R01 | Thema frei wählbar; Finanzwesen geeignet | Projektauftrag und spätere Präsentationsfolie zur Motivation | bestätigt |
| R02 | Keine offizielle Themenfreigabe nötig | E-Mail-Antwort archiviert | bestätigt |
| R03 | Projekt eigenständig als Einzelprojekt | Autorenschaft in README, Präsentation und Projektdateien | bestätigt |
| R04 | Mindestens zwei Tools | Python-Pipelines in `src/`, SQLite-Schema und sechs Kern-Views unter `sql/`, Power-BI-Daten- und Measure-Vertrag unter `powerbi/` sowie das fertige Dashboard `powerbi/DSP_Crypto_Entry_Intelligence.pbix` | Python, SQL und Power BI nachgewiesen; Dashboard erstellt |
| R05 | Idealerweise über 10.000 Zeilen im finalen Datensatz | Phase 1C-A erzeugt 116.208 1h- und 29.052 4h-Zeilen. Phase 1C-B übernimmt exakt 145.260 Faktenzeilen in `fact_market_context`; Nachweis in `reports/sql/sql_quality_summary.json` | Processed-Schwelle und G1-12 unabhängig mit `PASS` bestätigt |
| R06 | Eine Datenquelle ausreichend; mehrere bringen Bonus | Binance Public Data und Coin Metrics wurden vollständig importiert und in Phase 1C-A leakage-sicher verbunden. Die unabhängige Abnahme bestätigt 145.260/145.260 D+1-Matches, keine Zeilenverluste, keine Aufblähung und keine Zukunftsverletzungen | Mehrquellen-Processed-Join erfüllt |
| R07 | Alle zum Projekt gehörenden Dateien abgeben | finale Abgabe-Checkliste | offen |
| R08 | Python-Code abgeben, wenn Python genutzt wird | Gate-0-Pilot, Vollimport, Processed-, SQL- und EDA-/Power-BI-Exportpipeline liegen unter `src/`. Dazu kommen `src/backtest_contract.py`, `src/backtest_pipeline.py`, `src/final_test_once.py` und die automatisierten Tests unter `tests/` | Python bis Phase 1C-C unabhängig nachgewiesen; Phase 2A committed; Phase 2B und Gate 2 am 3. August 2026 unabhängig mit `PASS` abgenommen; Methodenstand committed; 14 zusätzliche Einmal-Runner-Tests bestanden; finaler Test am 4. August 2026 genau einmal abgeschlossen und technisch nachgeprüft |
| R09 | SQL- und Power-BI-Dateien abgeben, wenn genutzt | `sql/001_schema.sql`, `sql/002_views.sql`, SQL-Berichte sowie `powerbi/DSP_Crypto_Entry_Intelligence.pbix` und die zugehörigen Power-BI-Dokumente | Dateien vorhanden; finale Abgabe noch ausstehend |
| R10 | Präsentationsdatei abgeben | PowerPoint unter `presentation/` | geplant |
| R11 | Separate Projektdokumentation nicht Pflicht | README sowie Methoden-, Daten- und Ergebnisdokumentation | freiwillig erweitert |
| R12 | Dateien sauber und ordentlich | Geprüfte Ordnerstruktur, getrennte und atomar erzeugte Daten-, SQLite-, EDA- und Power-BI-Ausgaben, hashgebundene Manifeste, logischer SQL-Fingerprint sowie getrennte Anomalie-, Join-, SQL-, EDA-, Quarantäne- und Phase-2B-Nachweise; große Daten bleiben außerhalb Git | Gate 1 abgeschlossen; ungültiges Phase-2B-Bündel unverändert quarantänisiert; gültiges 18-Dateien-Bündel mit Code-, Konfigurations- und Inputprovenienz sowie unabhängiger Gate-2-Abnahme bestätigt; finaler 14-Dateien-Einmallauf durch Quittung, Manifest, Snapshot und `FINAL_TEST_POST_RUN_VALIDATION_REPORT.md` nachgewiesen |
| R13 | Bewertung von Präsentation und Rückfragen | Probepräsentationen | geplant |
| R14 | Präsentation 20 bis 30 Minuten | Zeitmessung bei mindestens zwei Probeläufen | offen |
| R15 | Präsentation enthält Fragestellung, Quellen, Vorgehen, Analysen, Ergebnisse, Visualisierung | Folien-Traceability vor Abgabe | geplant |
| R16 | APIs, Quellen und Bibliotheken frei wählbar, kurz dokumentieren | `DATA_SOURCES.md`, Datenquellen-, Processed-, SQL- und EDA-Datenwörterbücher, Konfigurationen, Pilotvergleich sowie qualitätsgebundene Manifeste | Pilot, Vollimport, Processed-Join, SQL-Modell sowie EDA-/Power-BI-Vertrag unabhängig nachgewiesen |
| R17 | GitHub und Portfolio erlaubt | bereinigtes öffentliches Repository ohne Geheimnisse | geplant |
| R18 | Abgabe bis 24. August 2026 im Teams-Ordner | Uploadnachweis und Checkliste | offen |
| R19 | Präsentation 25. August 2026 ab 09:00 Uhr | Kalendereintrag und finale Datei | bestätigt |
| R20 | Wöchentlicher Status freitags an Verwaltung | Dateien unter `status/` und gesendete E-Mails | laufend |
| R21 | Tägliche Zeiterfassung in Excel | `status/Zeiterfassung_DSP_Abschlussprojekt.xlsx` | in Arbeit |
