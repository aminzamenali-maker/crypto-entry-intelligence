# Code-Dokumentation - Crypto Entry Intelligence

Diese Dokumentation erklaert den vorhandenen Python-, SQL- und JSON-Code in einfacher deutscher Sprache, ohne die Originaldateien zu veraendern.

## Wichtig: Originalcode bleibt unveraendert

Die Dateien unter `src/`, `sql/` und `config/` wurden fuer diese Dokumentation **nicht bearbeitet**. Die Erklaerungen liegen ausschliesslich in diesem neuen Dokumentationsordner. Dadurch bleiben die bestehenden SHA-256-Hashes der Code- und Konfigurationsdateien erhalten.

Kommentierte Python-Bloecke in diesen Dokumenten sind **Lesefassungen**: Sie zeigen echte Ausschnitte aus dem Projekt und ergaenzen Erklaerkommentare nur in der Dokumentation. Sie sollen nicht als Ersatz fuer die Originaldateien ausgefuehrt werden.

Bei JSON werden keine Kommentare in die Dateien eingefuegt, weil Standard-JSON keine Kommentare unterstuetzt. Stattdessen werden Felder in Tabellen erklaert.

## Empfohlene Lesereihenfolge

1. `00_CODE_UEBERBLICK.md` - Gesamtbild und Dateilandkarte
2. `01_DATENPILOT_UND_VOLIMPORT.md` - Quellen pruefen und historische Daten laden
3. `02_PROCESSED_DATEN_UND_KONTEXTJOIN.md` - saubere Analysebasis und D+1/D+2-Kontext
4. `03_SQL_UND_DATENBANK.md` - SQLite-Schema und Views
5. `04_SIGNALE_BACKTEST_UND_FINALTEST.md` - Signalregeln, Trades, Kosten und finaler Test
6. `05_POWER_BI_EDA_PIPELINE.md` - EDA und Power-BI-Exporte
7. `06_KONFIGURATION_JSON.md` - Konfigurationsdateien in Alltagssprache
8. `07_TESTS_UND_QUALITAETSSICHERUNG.md` - was die Tests absichern
9. `08_DATEI_FUER_DATEI_REFERENZ.md` - kurze Referenz fuer jede produktive Datei
10. `09_INTEGRITAET_UND_HASHES.md` - Hash-Nachweis des unveraenderten Codes

## Wofuer ist diese Dokumentation gedacht?

Sie hilft Tutoren, Pruefern und spaeter auch dem Autor selbst, schnell zu verstehen:

- welche Datei welche Aufgabe hat,
- wie Daten durch das Projekt fliessen,
- wo Look-ahead verhindert wird,
- wie die fuenf Signale entstehen,
- wie Kosten und Haltedauern angewendet werden,
- warum der finale Test besonders geschuetzt ist,
- wie SQL und Power BI in die Pipeline eingebunden sind,
- und welche Tests die wichtigsten Regeln absichern.
