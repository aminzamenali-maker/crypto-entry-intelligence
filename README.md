# DSP-Abschlussprojekt: Crypto Entry Intelligence

## Projektstatus

- Verantwortlich: Mohammad Amin Zamen Ali
- Offizieller Arbeitstitel: **Datenbasierte Analyse, Validierung und Visualisierung von Einstiegssignalen im Kryptowährungshandel**
- Gate 0: bestanden
- Gate 1: `PASS_WITH_DOCUMENTED_SOURCE_ANOMALIES`
- Gate 2: `PASS`
- Finaler Test 2024-2025: genau einmal abgeschlossen und technisch nachgeprüft
- Kernergebnis: Keines der getesteten Signal-Horizont-Paare war in Development, Validation und finalem Test gleichzeitig netto positiv. Die Handelskosten reduzieren die Bruttoergebnisse deutlich.
- Machine Learning: bewusst nicht begonnen; H5 bleibt `NOT_EVALUATED`
- Power BI: Dashboard mit fünf Berichtsseiten erstellt und gespeichert

## Geprüfter Datenpilot

Gate 0 wurde mit einem kleinen, reproduzierbaren Pilot bestanden. Der spätere
vollständige Phase-1B-Import wurde separat ausgeführt und offline abgenommen.

- Assets: `BTCUSDT`, `ETHUSDT`, `SOLUSDT`
- Primärer Zeitrahmen: `1h`
- Robustheits-Zeitrahmen: `4h`, aus vollständigen 1h-Kerzen abgeleitet
- Empfohlener Core-Zeitraum: 1. Januar 2021 bis ausschließlich 1. Januar 2026
- Primärquelle: Binance Public Data
- Ergänzende Quelle: Coin Metrics Community API
- Ergebnis: 12/12 Marktdateien und 398/398 Kontexttage bestanden; null
  Join-Verluste und null verwendete Zukunftszeilen

Der ausführliche Nachweis liegt unter
`reports/data_pilot/DATA_PILOT_REPORT.md`.

### Pilot reproduzieren

```powershell
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
python -m src.data_pilot --config config/data_pilot.json
```

Rohdaten werden unter `data/raw/pilot/` lokal abgelegt, per SHA-256
protokolliert und nicht in Git gespeichert. Ein erneuter Lauf verwendet
vorhandene Rohdateien unverändert.

### Vollimport und Offline-Abnahme

Der kontrollierte Phase-1B-Vollimport endete mit
`COMPLETED_WITH_SOURCE_ANOMALIES`. Die fünf Ausführungsberichte stimmen mit
Checkpoint-Generation 185 überein. Phase 1B ist damit
`PASS_WITH_DOCUMENTED_SOURCE_ANOMALIES`. Phase 1C-A und G1-10, Phase 1C-B und
G1-12 sowie Phase 1C-C und G1-13 sind unabhängig `PASS`. Gate 1 ist damit als
`PASS_WITH_DOCUMENTED_SOURCE_ANOMALIES` abgeschlossen.

Der Import belegt:

- 180/180 Binance-Archive und 180/180 CHECKSUM-Dateien,
- 131.472 Raw-Sollzeilen, 131.430 Raw-Istzeilen und Delta -42,
- 116.208 akzeptierte 1h- und 29.052 akzeptierte 4h-Zeilen,
- 159 gültige und 21 ausgeschlossene Asset-Monate,
- sieben betroffene Kalendermonate bei allen drei Assets,
- 96 Anomaliezeilen und 24 zusammenhängende Intervalle,
- 1.828/1.828 bestandene Coin-Metrics-Tage.

Die akzeptierte zeitliche Abdeckung beträgt 88,39 %. 11,61 % wurden durch den
vollständigen konservativen Monatsausschluss verworfen. Diese Zahl darf nicht
mit der tatsächlichen Raw-Quellenlücke von nur 42 Stunden verwechselt werden.

```powershell
python -m src.full_import --config config/full_import.json --dry-run
```

Der Dry-Run plant 180 Binance-Monatsarchive, 180 Prüfsummendateien,
131.472 erwartete 1h-Zeilen und 32.868 daraus abgeleitete 4h-Zeilen. Er schreibt
atomar ausschließlich die beiden Planungsberichte `download_plan.csv` und
`dry_run_summary.json`. Er verwendet kein Netzwerk, erzeugt weder Raw-,
Interim- noch Processed-Dateien und verändert keinen der fünf
Ausführungsberichte. Details:
`reports/full_import/FULL_IMPORT_PLAN.md`.

Die gehärtete Logik trennt kaputte Dateien von intakten Anbieterdateien mit
einer Quellenunterbrechung. Betroffene Monate werden nicht interpoliert oder
aufgefüllt, erzeugen keine Interimkerzen und sind vollständig in
`reports/full_import/source_anomalies.csv` dokumentiert. Ein erneuter
Vollimport braucht einen eigenen ausdrücklichen Auftrag.

Im abgeschlossenen Lauf ist `execution_checkpoint.json` die autoritative
Quelle. Sie enthält nicht nur Zähler, sondern die vollständigen
Manifest-, Monatsqualitäts-, Anomalie-, Interim- und Coin-Metrics-Nachweise
sowie Scope-, Konfigurations-, Run- und Generationskennung. Die vier
Teilberichte sind daraus reproduzierbare Projektionen. Jede Datei wird einzeln
atomar ersetzt; die Gruppe ist keine Mehrdatei-Transaktion. Nach einem
Abbruch erkennt der Wiederanlauf unvollständige oder fremde Projektionen über
ihre Hashes und stellt sie aus der letzten bestätigten Checkpoint-Generation
wieder her. Der im unveränderten Checkpoint gespeicherte technische
Buildzeitstatus `NOT_EVALUATED` bleibt historische Evidenz; die spätere
unabhängige Gesamtbewertung von Gate 1 ist
`PASS_WITH_DOCUMENTED_SOURCE_ANOMALIES`.

Die dritte Härtungsrunde legt die offizielle Binance-Grenze verbindlich fest:
bis Dezember 2024 Millisekunden, ab Januar 2025 Mikrosekunden. Die passende
Schlusszeit endet eine Millisekunde beziehungsweise eine Mikrosekunde vor der
nächsten Stunde. Checkpoint-Schema 3 bindet diese Richtlinie. Eine vorhandene
`source_anomalies.csv` wird offline aus dem sicheren Raw-Archiv und seiner
CHECKSUM vollständig neu berechnet und feldgenau verglichen.
Coin-Metrics-Fehlerphasen, alle vier Berichtsprojektionen und die Zählungen für
BTCUSDT, ETHUSDT und SOLUSDT sind separat regressionsgetestet.

Die vierte Härtungsrunde hebt den Checkpoint auf Schema 4. Nicht die
`source_anomalies.csv`, sondern alle aus der sicheren Konfiguration
abgeleiteten vollständigen Raw-/CHECKSUM-Paare bestimmen nun den Prüfumfang.
Jede physische CSV-Zeile muss exakt dem Schema entsprechen. Eine fehlende CSV
wird bei belegten Cache-Anomalien vollständig als
`recomputed_from_cached_raw` rekonstruiert; `validated_preexisting_csv` ist nur
nach einem vollständigen Gesamtvergleich erlaubt.

Die fünfte Härtungsrunde trennt interne Qualitätsprüfung und stabilen
Marktvertrag. Der Parser führt weiterhin 20 Spalten; die 1h-Interimdatei
`binance_1h_market_v1` enthält jedoch immer genau diese 15 Spalten:

```text
symbol, timeframe, timestamp_utc, close_time_utc, open, high, low, close,
volume, quote_asset_volume, number_of_trades, taker_buy_base_volume,
taker_buy_quote_volume, source, timestamp_unit
```

Die fünf Zeitstempel-Nachweisfelder bleiben in Monatsqualität und Checkpoint.
Schema-ID und Feldvertrag sind im neuen Verarbeitungspolicy-Fingerprint
gebunden. Nur der exakt belegte alte Schema-4-/Generation-2-Fehlerzustand darf
nach vollständiger read-only Prüfung in-memory übernommen werden; jeder
andere alte oder widersprüchliche Zustand stoppt vor Mutation. Öffentliche
kanonische Binance-URLs bleiben als Herkunftsnachweis zulässig,
Coin-Metrics-Cursor, vollständige Paging-URLs und sensible Parameter nicht.

Für Phase 1C gilt verbindlich: Keine Rendite, kein Indikator, kein Signal und
keine Position darf eine ausgeschlossene Monatsgrenze überbrücken. Rollende
Zustände werden nach jeder Lücke zurückgesetzt. Für alle drei Assets gilt
dieselbe 53-Monatsmaske. Abdeckungsberichte nennen 88,39 %
akzeptierte und 11,61 % ausgeschlossene Zeit getrennt von der tatsächlichen
Raw-Lücke von 42 Stunden.

### Phase 1C-A: kanonische Processed-Tabellen

Phase 1C-A validiert vor jeder Ausgabe den autoritativen Phase-1B-Checkpoint,
seine vier Projektionen, alle 159 akzeptierten 1h-/4h-Interimpaare und die
1.828 Coin-Metrics-Tage. Danach entstehen deterministisch:

- `data/processed/full_import/market_context_1h.csv` mit 116.208 Zeilen,
- `data/processed/full_import/market_context_4h.csv` mit 29.052 Zeilen,
- Datenwörterbuch, Join-Qualitätsbericht und Processed-Manifest unter
  `reports/processed/`.

Eine Zeile entspricht einem Asset und einer vollständigen Kerze. Der
Primärschlüssel ist `(symbol, timeframe, timestamp_utc)`. Die
`decision_time_utc` liegt bei alten Millisekundenkerzen genau 1 ms und bei
Mikrosekundenkerzen genau 1 µs nach `close_time_utc`. Der D+1-As-of-Join nutzt
nur Kontext mit `available_from_utc_d1 <= decision_time_utc`; D+2 bleibt als
separates Feld für die spätere Sensitivität erhalten.

Die reale Ausgabe besitzt 145.260 von 145.260 D+1-Matches, null Join-Verluste,
null Zukunftsverletzungen und null Primärschlüsselduplikate. Die gemeinsame
53-Monatsmaske bildet fünf Segmente: `2021-01`, `2021-05` bis `2021-07`,
`2021-10` bis `2021-11`, `2022-01` bis `2023-02` und `2023-04` bis `2025-12`.
Phase 1C-A berechnet noch keine Renditen, Indikatoren, Signale oder Positionen.

```powershell
python -B -m src.processed_pipeline --config config/full_import.json
```

Vorhandene byteidentische Ausgaben werden validiert wiederverwendet;
abweichende Dateien bleiben unverändert und führen zu einem harten Fehler.
Die unabhängige Offline-Abnahme bestätigt Phase 1C-A und G1-10 mit `PASS`.
Phase 1C-B und G1-12 sowie Phase 1C-C und G1-13 sind unabhängig mit `PASS`
abgenommen. Gate 1 ist mit den dokumentierten Quellenanomalien abgeschlossen.

### Phase 1C-B: reproduzierbares SQLite-Modell

Phase 1C-B validiert beide Processed-Tabellen und ihre Manifest- und
Join-Nachweise vollständig, bevor eine SQL-Datei verändert werden darf. Der
Offline-Aufbau verwendet ausschließlich die Python-Standardbibliothek
`sqlite3`:

```powershell
python -B -m src.sql_pipeline --config config/full_import.json
```

Die lokale, von Git ignorierte Datenbank enthält die Dimensionen `dim_asset`
und `dim_segment` sowie `fact_market_context` mit 145.260 Zeilen. Ein
Primär- beziehungsweise Eindeutigkeitsschlüssel verhindert doppelte Kerzen;
Fremdschlüssel erzwingen gültige Asset- und Segmentzuordnungen. Sechs
Kern-Views liefern 1h-/4h-Daten, Abdeckung, Segmentabdeckung, Kontextalter und
maschinenlesbare Qualitätschecks. SQL ergänzt keine fehlenden Daten und
berechnet keine Renditen, Indikatoren, Signale oder Positionen.

Der physische SQLite-Dateihash kann sich durch interne Speichertechnik ändern.
Darum ist der reproduzierbare Nachweis der logische Fingerprint aus Schema,
View-Definitionen, sortierten Tabellenzeilen, Zählungen und relevanten
SQLite-Einstellungen. Der aktuelle Fingerprint ist
`cbf6d93ebb86a591764a4e07327152cba24c2033c9bed57b5bd14e69abf1e367`.
Die vollständige Berichtscachevalidierung erzeugt alle vier erwarteten
Berichte aus unabhängig neu geprüfter Datenbankqualität, Quellhashes und
SQL-Skripthashes im Arbeitsspeicher und vergleicht sie bytegenau. Manipulationen
stoppen fail-closed ohne Reparatur oder Mutation. SQL ergänzt keine fehlenden
Daten; die fünf Segmentgrenzen und der Leakage-Schutz bleiben erhalten.

Details stehen unter `reports/sql/`. Die unabhängige Offline-Abnahme bestätigt
Phase 1C-B und G1-12 mit `PASS`. Die spätere Phase-1C-C-Abnahme bestätigt
G1-13 ebenfalls mit `PASS`.

### Phase 1C-C: reproduzierbare EDA und Power-BI-Datenvertrag

Phase 1C-C validiert vor jeder Ausgabe den SQLite-Dateihash, den unabhängig
neu berechneten logischen Fingerprint, den read-only SQL-Cache, alle Schlüssel,
die sechs Asset-Zeitrahmen-Zählungen, fünf Segmente, Ausschlussmonate und die
aktuelle Gate-Matrix. Der Offline-Befehl lautet:

```powershell
python -B -m src.eda_powerbi_pipeline --config config/full_import.json
```

Der kontrollierte Offline-Lauf erzeugte sieben kleine EDA-Tabellen und sechs
deterministische SVG-Grafiken unter `reports/eda/`. Der lokale, von Git
ignorierte Power-BI-Export enthält 145.260 Faktzeilen sowie eindeutige Asset-,
Segment-, Kalender- und Zeitrahmendimensionen. Der Kalender bildet den gesamten
Scope vom 1. Januar 2021 bis 31. Dezember 2025 mit 1.826 lückenlosen Tagen ab;
212 Tage der sieben ausgeschlossenen Monate bleiben ohne Faktzeilen sichtbar.
Der versionierte Daten- und Measure-Vertrag liegt unter `powerbi/`. Alle
Beziehungen sind 1:n mit einseitiger Filterung von der Dimension zur
Faktentabelle.

Close-to-close-Renditen dienen ausschließlich der Beschreibung und bleiben an
allen 30 Asset-Zeitrahmen-Segmentstarts NULL. Es gibt keine Forward Returns,
Labels, Signale, Positionen, Backtests oder Performancekennzahlen. Die reale
42-Stunden-Quellenlücke bleibt getrennt von 15.264 konservativ ausgeschlossenen
Asset-Kalenderstunden sowie 88,39 % akzeptierter und 11,61 % ausgeschlossener
Abdeckung. `context_age_hours` misst das Alter seit dem Coin-Metrics-
Quellzeitpunkt; `context_age_since_d1_hours` misst getrennt das Alter seit der
konservativ angenommenen D+1-Verfügbarkeit. Der Measure-Vertrag trennt globale
Scope-Abdeckung ausdrücklich von kalenderfilterabhängiger Abdeckung. Phase
1C-C und G1-13 sind unabhängig mit `PASS` abgenommen. 5/5 Power-BI- und 16/16
EDA-Manifestzuordnungen stimmen; der reale Wiederaufruf ist `CACHED_VALID`.
Processed-, Join-, SQL-, EDA- und Power-BI-Vertragsstufe besitzen keine offene
Qualitätsabweichung. Gate 1 ist formal als
`PASS_WITH_DOCUMENTED_SOURCE_ANOMALIES` abgeschlossen, weil G1-03, G1-05 und
G1-06 die transparent dokumentierten Binance-Quellenabweichungen behalten.

### Power-BI-Dashboard – abgeschlossen

Das Power-BI-Dashboard wurde auf Basis der geprüften Datenexporte und der finalen Backtestergebnisse erstellt. Die Datei liegt unter `powerbi/DSP_Crypto_Entry_Intelligence.pbix`.

Der Bericht besteht aus fünf Seiten:

1. `01 – Ergebnisübersicht` – zentrale Kennzahlen, Kostenwirkung und Ergebnisvergleich
2. `02 – Signalvergleich` – Nettorendite, Trefferquote und Anzahl der Trades je Signal
3. `03 – Stabilität & Baselines` – Vergleich über Development, Validation und Final Test sowie Baselines
4. `04 – Marktumfeld` – Kurs, Volumen, Kerzenverteilung und ausgewählte Marktkennzahlen
5. `05 – Datenqualität` – geprüfte Marktzeilen, Kontext-Matching und ausgeschlossene Monate

Die Ergebnisdarstellung bleibt bewusst neutral. Das Dashboard zeigt auch die negativen Nettoergebnisse nach Kosten und stellt keine Profitabilität in Aussicht.

### Phase 2A: Methodenplanung ohne Backtest

Phase 2A friert Forschungsfrage, Baselines, fünf erklärbare Signalvarianten,
Featuremetadaten, Next-Open-Ausführung, Segmentregeln, Kosten und zeitliche
Splits vor Kenntnis von Ergebnissen ein. D+1 und D+2 sind getrennte
rückwärtsgerichtete As-of-Verträge. Der Offline-Prüfer validiert zusätzlich
die unveränderten Phase-1-Hashes und die real verfügbaren Splitzeilen:

```powershell
python -B -m src.backtest_contract --config config/backtest.json
```

Er schreibt keine Datei und berechnet keine Features, Signale, Positionen,
Trades oder Performancewerte. Der Methodenplan steht unter
`reports/backtest/`. Die spätere Phase-2B-Umsetzung wurde unabhängig abgenommen; Gate 2 ist `PASS`.
Die vorregistrierte Kriterien-Datei bleibt als geschützter historischer Vertrag unverändert; die reale PASS-Matrix steht im unabhängigen Abnahmebericht.

### Phase 2B: unabhängig abgenommen, Gate 2 bestanden

Der erste Offline-Lauf wurde wegen eines um eine Stunde verschobenen
SMA-Crossovers abgelehnt und vollständig unter einem ignorierten
Quarantänepfad erhalten. Das korrigierte Paket wurde danach genau einmal neu
erzeugt und als `CACHED_VALID` bestätigt.

Das Paket wurde in einer isolierten Offline-Kopie unabhängig abgenommen. Alle 79.470 Development-/Validation-Marktzeilen, 158.940
Featurezeilen, 264.624 Trade-/Kostenzeilen, 1.440 Detailergebnisse und 480
Aggregatzeilen wurden ohne fachliche Abweichung nachvollzogen. Alle 345 Tests
bestanden. G2-01 bis G2-16 und damit Phase 2B sowie Gate 2 besitzen den Status
`PASS`.

Der finale Test 2024–2025 wurde nach der Methodenfreigabe genau einmal
ausgeführt. Er erzeugte 131.580 Featurezeilen, 219.624
Trade-/Kostenzeilen, 720 Detail- und 240 Aggregatergebnisse. Der technische
Status lautet `FINAL_TEST_COMPLETED_EXACTLY_ONCE`; ein zweiter Lauf und
nachträgliche ParameterÄnderungen sind verboten. Die Nachlaufprüfung und
die Ergebnisinterpretation stehen unter `reports/backtest/final_test_method/`.

### Finaler Einmallauf abgeschlossen

Die schreibgeschützte Vorprüfung bestand zuerst mit
`FINAL_TEST_PREFLIGHT_VALID`. Danach wurde der geschützte `--execute`-Modus
mit dem exakten Bestätigungscode genau einmal gestartet. Der Lauf endete mit
`FINAL_TEST_COMPLETED_EXACTLY_ONCE`, Exit-Code 0 und dem Bundle-Snapshot
`c9366bf6a050df7c5701194f5ba8dbfd9e3199aff1b1ee82ca02a4b93f9d4d8e`.

Die 14 Bundle-Dateien umfassen 65.790 Markt-Eingabezeilen, 131.580
Featurezeilen, 219.624 Trade-/Kostenzeilen, 720 Detail- und 240
Aggregatergebnisse. Alle Manifest-Hashes und Zeilenzahlen wurden nach dem Lauf
neu geprüft. Ein zweiter Lauf ist durch den dauerhaften Startstatus verboten.
Nachweise: `FINAL_TEST_EXECUTION_RECEIPT.json`,
`FINAL_TEST_POST_RUN_VALIDATION_REPORT.md` und
`FINAL_TEST_INTERPRETATION_REPORT.md`.

## Ziel

Das Projekt untersucht mit historischen Daten, ob klar definierte Einstiegssignale unter realistischen Annahmen einen messbaren Informationswert besitzen. Es geht nicht darum, sichere Gewinne oder den einen perfekten Einstieg vorherzusagen. Das Ergebnis soll methodisch sauber, reproduzierbar, verständlich und als Bewerbungsportfolio nutzbar sein.

## Werkzeugkette

1. **Python** für Datenbeschaffung, Qualitätsprüfung, Feature Engineering, Statistik und Backtesting.
2. **SQL** für strukturierte Speicherung, Views und prüfbare Analyseabfragen.
3. **Power BI** für das interaktive Ergebnis-Dashboard.
4. **PowerPoint** für die Abschlusspräsentation.

Damit werden mehr als die vorgeschriebenen zwei Tools eingesetzt.

## Zentrale Projektdokumente

Für die fachliche Einordnung des Projekts sind vor allem diese Dateien relevant:

1. `admin/OFFICIAL_REQUIREMENTS.md` - zusammengefasste DSP-Vorgaben
2. `PROJECT_CHARTER.md` - Ziel, Forschungsfragen und Projektgrenzen
3. `PROJECT_PLAN.md` - Phasen, Meilensteine und Termine
4. `REQUIREMENTS_TRACEABILITY.md` - Nachweis der DSP-Anforderungen
5. `DECISIONS.md` - wichtige fachliche Entscheidungen und offene Punkte
6. `DATA_SOURCES.md` - Datenquellen, Datenqualität und Tabellenkonzept

## Ordnerstruktur

```text
dsp_crypto_entry_intelligence/
├── admin/          Offizielle Anforderungen und organisatorische Nachweise
├── config/         Reproduzierbare Konfigurationen, keine Geheimnisse
├── data/
│   ├── raw/        Unveränderte Quelldaten, nicht in Git speichern
│   ├── interim/    Zwischenergebnisse
│   └── processed/  Finale Analyse- und Power-BI-Tabellen
├── notebooks/      Explorative Analysen
├── powerbi/        PBIX-Datei, DAX-Katalog und Dashboard-Dokumentation
├── presentation/   PowerPoint
├── reports/        Qualitäts-, Modell- und Backtesting-Berichte
├── sql/            Schema, Views und Analyseabfragen
├── src/            Wiederverwendbarer Python-Code
└── tests/          Daten-, Feature-, Backtest- und Leakage-Tests
```

## Projektablauf in Kurzform

1. Forschungsfrage und Kernumfang festlegen.
2. Datenquellen in einem kleinen Pilot prüfen.
3. Datenpipeline, Datenqualität, SQL-Modell und EDA aufbauen.
4. Signale und Baselines definieren und den Backtest zeitlich getrennt validieren.
5. Den finalen Test nach Methodenfreigabe genau einmal ausführen und auswerten.
6. Die geprüften Ergebnisse in Power BI visualisieren.
7. Präsentation und finale Abgabe auf den dokumentierten Ergebnissen aufbauen.

## Wichtiger Grundsatz

Komplexität entsteht durch methodische Tiefe, saubere Validierung und gute Integration der Werkzeuge - nicht durch möglichst viele unvollständige Funktionen.
