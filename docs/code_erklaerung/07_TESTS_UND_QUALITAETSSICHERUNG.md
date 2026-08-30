# 07 - Tests und Qualitaetssicherung

## Testbestand in der hochgeladenen Projekt-ZIP

Die Testdateien enthalten zusammen **359 `test_*`-Methoden** (statische Zaehling der vorliegenden Python-Testfunktionen).

| Testdatei | `test_*`-Methoden |
|---|---:|
| `tests/test_backtest_contract.py` | 42 |
| `tests/test_backtest_pipeline.py` | 32 |
| `tests/test_data_pilot.py` | 13 |
| `tests/test_eda_powerbi_pipeline.py` | 65 |
| `tests/test_final_test_once.py` | 14 |
| `tests/test_full_import.py` | 133 |
| `tests/test_processed_pipeline.py` | 19 |
| `tests/test_sql_pipeline.py` | 41 |

> Hinweis: Ein kompletter Testlauf wurde bei der Erstellung dieser Dokumentation gestartet, lief in der verfuegbaren Ausfuehrungszeit jedoch nicht bis zum Ende. Deshalb wird hier bewusst **kein neuer PASS-Gesamtstatus behauptet**. Die Tabelle beschreibt den Testbestand der Dateien.

## Was die Tests absichern

### `test_data_pilot.py`

- Millisekunden- und Mikrosekunden-Zeitstempel
- exakte Monatsgrenzen
- OHLCV-Plausibilitaet
- 4h-Aggregation aus vier 1h-Kerzen
- Kontextjoin ohne Zukunftsdaten
- Gate-0-Teilbedingungen

### `test_full_import.py`

Dies ist die groesste Testsuite. Sie prueft unter anderem:

- Dry-Run vs. Execute-Schutz
- Checksum-Fehler und kaputte ZIPs
- Timestamp-Policy vor/nach 2025
- Quellenanomalien
- Checkpoint-Recovery
- atomare Schreibvorgaenge und No-Overwrite
- Fortsetzen nach Teilfehlern
- Schema- und Policy-Migrationen
- deterministische Evidenz und Fail-Closed-Verhalten

### `test_processed_pipeline.py`

- D+1-As-of-Join ohne Zukunft
- separate D+2-Verfuegbarkeit
- gemeinsame 53-Monatsmaske
- Segmentresets nach Luecken
- exakte 1h/4h-Zeilenzahlen
- keine Signal-/Positionsfelder in Phase 1C-A
- byteidentischer Cache / kein Overwrite

### `test_sql_pipeline.py`

- Tabellen und Business-Key
- Fremdschluessel
- exakte Counts
- keine Zukunftskontexte
- ausgeschlossene Monate fehlen wirklich
- Datenqualitaets-View
- stabiler logischer Fingerprint
- Cache- und Reportintegritaet

### `test_backtest_contract.py`

- exakt drei Assets und zwei Zeitrahmen
- Long/Flat, keine Shorts, kein Funding
- Einstieg am naechsten Open
- 20/30/50 bp Kosten
- Segmentresets
- fuenf exakte Signale
- Features ohne Zukunftsfelder
- zeitliche Splits und versiegelter Final Test

### `test_backtest_pipeline.py`

- alle 25 vorregistrierten Features
- SMA, RSI, ATR, Volatilitaet, Z-Score
- exakte fuenf Signalregeln
- beobachteter SMA-Grenzfall
- naechstes Open / exakte Haltedauer
- keine Split-/Segmentueberschreitung
- keine ueberlappenden Positionen
- multiplikative Kostenformel
- Baselines
- Cache- und Provenienzschutz

### `test_final_test_once.py`

- Final-Test-Konfiguration
- geschuetzte Methodenhashes
- exklusiver Startzustand
- genau-einmal-Logik
- Manifest und Bundle-Snapshot
- atomare Receipts / keine stillen Retries

### `test_eda_powerbi_pipeline.py`

- deskriptive Statistik
- Null-/Outlier-Verhalten
- Renditen nur bei lueckenlosen Intervallen
- Datenvertrag fuer Power BI
- Measure-Vertrag
- Exporte, SVGs, Manifeste
- Cache/Refresh-Sicherheit
- reale SQL-/Export-Konsistenz

## Wie man die Teststrategie einem Tutor erklaert

> Die Tests pruefen nicht nur einzelne Formeln. Ein grosser Teil prueft Sicherheitsregeln: keine Zukunftsdaten, keine unbemerkten Ueberschreibungen, keine fehlerhaften Zeitstempel, keine Daten ueber Luecken hinweg und keine nachtraegliche Veraenderung des finalen Tests. Dadurch wird die Pipeline nicht nur rechnerisch, sondern auch methodisch abgesichert.
