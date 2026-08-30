# Projektplan bis zur Abgabe

## Leitprinzip

Zuerst ein vollständiger, belastbarer Core. Danach nur Erweiterungen, die den Abgabetermin und die Verständlichkeit nicht gefährden.

## Phase 0 - Fundament und Datenpilot


**Status:** abgeschlossen. Prüfnachweis:
`reports/data_pilot/gate0_decision.json` und
`reports/data_pilot/DATA_PILOT_REPORT.md`.

- offizielle Anforderungen und Projektregeln festhalten
- Forschungsfrage, Hypothesen und Kernumfang bestätigen
- Kandidaten für Datenquellen vergleichen
- kleinen Datenpilot laden und Qualität prüfen
- Zeitstempel-, Symbol- und Tabellenstandard festlegen
- SQL-Datenmodell entwerfen

**Gate 0 - bestanden:** Binance Public Data und Coin Metrics
Community API wurden reproduzierbar geladen, per Qualitätsregeln geprüft und
ohne Zukunftsdaten verbunden. Der Pilot ist noch kein Vollimport.

## Phase 1 - Datenpipeline und EDA


**Status:** Phase 1A und Phase 1B sind abgeschlossen. Phase
1C-A wurde anschließend ausschließlich offline umgesetzt und unabhängig mit
`PASS` abgenommen. Der nach fünf Offline-Härtungsrunden
genau einmal freigegebene kontrollierte Wiederanlauf endete mit
`COMPLETED_WITH_SOURCE_ANOMALIES`. Die Offline-Abnahme erteilt Phase 1B das
Teilurteil `PASS_WITH_DOCUMENTED_SOURCE_ANOMALIES`.

Der vollständige Import belegt 180/180 Binance-Archive, 180/180 CHECKSUM-
Dateien und 180/180 passende Anbieterprüfsummen. Das unveränderte Raw-Soll von
131.472 1h-Zeilen steht 131.430 Raw-Zeilen gegenüber; die reale Quellenlücke
beträgt damit 42 Stunden. Akzeptiert wurden 116.208 1h- und 29.052 4h-Zeilen
aus 159 gültigen Asset-Monaten. 21 Asset-Monate aus sieben Kalendermonaten bei
allen drei Assets wurden konservativ vollständig ausgeschlossen. Die
Evidenz umfasst 96 Anomaliezeilen und 24 Intervalle. Coin Metrics bestand mit
1.828/1.828 Tagen.

Phase 1C-A erzeugte getrennte kanonische 1h- und 4h-Processed-Tabellen mit
116.208 beziehungsweise 29.052 Zeilen. Alle 145.260 Marktzeilen erhielten
einen D+1-Kontext mit `available_from_utc_d1 <= decision_time_utc`; es gibt
null Join-Verluste, null Zukunftsverletzungen und null Primärschlüssel-
duplikate. D+2 bleibt getrennt für eine spätere Sensitivität erhalten.

Phase 1C-B bildet diese geprüften CSV-Dateien offline in SQLite ab. Die
Dimensionen für drei Assets und fünf Segmente sowie die Faktentabelle mit
145.260 Zeilen und sechs Kern-Views wurden reproduzierbar erzeugt. Alle
Schlüssel-, Fremdschlüssel-, Status-, Nullwert- und Zukunftskontextprüfungen
bestanden. Die unabhängige Offline-Abnahme bestätigt Phase 1C-B und G1-12 mit
`PASS`. Der logische Fingerprint und der vollständig bytegenaue,
fail-closed Berichtscache sind unter `reports/sql/` nachgewiesen.

Phase 1C-C erzeugt auf dieser abgenommenen SQL-Basis eine reproduzierbare,
rein deskriptive EDA sowie einen versionierten Power-BI-Datenvertrag. Der
einmalige Offline-Lauf lieferte sieben kleine EDA-Tabellen, sechs
deterministische SVG-Abbildungen und einen lokalen, von Git ignorierten
Sternschemaexport mit 145.260 Faktzeilen sowie Asset-, Segment-, Kalender- und
Zeitrahmendimension. Die Kalenderdimension umfasst lückenlos 1.826 Tage vom
2021-01-01 bis 2025-12-31 und hält die 212 Tage der sieben ausgeschlossenen
Monate ohne Fakten sichtbar. Kontextalter seit Quellzeitpunkt und seit D+1 sind
getrennte Felder; globale und kalenderfilterabhängige Abdeckungs-Measures sind
eindeutig benannt. Alle 30 Asset-Zeitrahmen-Segmentstarts besitzen eine
leere Close-to-close-Rendite; es gibt keine Berechnung über Lücken. Die
technische Evidenz liegt unter `reports/eda/` und `powerbi/`. Die unabhängige
Offline-Abnahme bestätigt Phase 1C-C und G1-13 mit `PASS`.

Die identische 53-Monatsmaske der drei Assets bildet fünf gemeinsame
Zeitsegmente. Nach D023 und D024 dürfen Renditen, Indikatoren, Signale oder
Positionen nie über diese Segmentgrenzen fortgeführt werden. 88,39 %
akzeptierte und 11,61 % ausgeschlossene zeitliche Abdeckung sowie die reale
Raw-Lücke von 42 Stunden und der vollständige Monatsausschluss bleiben
getrennt berichtet. Phase 1C-A berechnet noch keine Features oder
Handelsergebnisse.

- vollständige Rohdaten beschaffen
- Raw/Interim/Processed-Pipeline entwickeln
- SQL-Schema und Kern-Views aufbauen
- Datenqualitätsbericht erzeugen
- finale Tabellenkörnung bestätigen
- erste explorative Analyse erstellen
- Power-BI-Datenvertrag festlegen

**Gate 1: `PASS_WITH_DOCUMENTED_SOURCE_ANOMALIES`.** Phase 1C-A, G1-10,
Phase 1C-B, G1-12, Phase 1C-C und G1-13 sind unabhängig mit `PASS`
abgenommen. Ausschließlich G1-03, G1-05 und G1-06 behalten wegen der offen
dokumentierten Binance-Quellenkontinuitätsabweichungen den Status
`PASS_WITH_DOCUMENTED_SOURCE_ANOMALIES`; alle übrigen Gate-1-Kriterien sind
`PASS`. Damit ist Gate 1 abgeschlossen. Die aktuelle Matrix steht in
`reports/full_import/GATE1_ACCEPTANCE_CRITERIA.md`; die Phase-1C-A-Evidenz in
`reports/processed/PHASE1C_QUALITY_REPORT.md`, die neue EDA-Evidenz in
`reports/eda/PHASE1C_EDA_REPORT.md`.

## Phase 2 - Signale, Baselines und Backtesting


**Status:** Phase 2A, Phase 2B und Gate 2 sind abgeschlossen.
Der freigegebene Methodenstand ist mit Commit
`648a74198a97e4e57d839a05db2af55fd1229190` gebunden. Der finale Test
2024–2025 wurde danach mit dem fail-closed Einmal-Runner genau einmal
 ausgeführt und endete mit `FINAL_TEST_COMPLETED_EXACTLY_ONCE`, Exit-Code 0.
Er umfasst 65.790 Markt-Eingabezeilen, 131.580 Featurezeilen, 219.624
Trade-/Kostenzeilen, 720 Detail- und 240 Aggregatergebnisse. Alle 13
Manifesteinträge, 14 Bundle-Dateien und der Snapshot
`c9366bf6a050df7c5701194f5ba8dbfd9e3199aff1b1ee82ca02a4b93f9d4d8e`
wurden nach dem Lauf bestätigt. Es gab null Parameteränderungen nach Gate 2
und keinen Wiederholungsversuch. Die technische Nachlaufprüfung ist `PASS`;
die abschließende Interpretation ist dokumentiert. ML wird zugunsten von
Power BI, Präsentation und Abgabequalität nicht begonnen.

Der vorregistrierte Core verwendet Binance Spot, Long/Flat, 1h primär und 4h
als Robustheitsprüfung. Signale entstehen erst nach Kerzenschluss, Einstieg
frühestens am nächsten Open. Der primäre Horizont entspricht auf beiden
Zeitrahmen ungefähr vier Stunden. 30 bp Round Trip sind das Hauptszenario;
20/50 bp sind Sensitivitäten. Alle Zustände werden je
`(symbol, timeframe, segment_id)` zurückgesetzt. Entwicklung 2021–2022 und
Validierung 2023 dürfen Methoden festlegen; der finale Test 2024–2025 wird
erst nach Freigabe genau einmal ausgewertet. D+1 und D+2 erfordern getrennte
As-of-Joins.

- Indikatoren und Signale ohne Zukunftsinformationen berechnen
- Baselines definieren
- konservative Ausführungslogik implementieren
- Gebühren, Slippage und gegebenenfalls Funding berücksichtigen
- Varianten- und Ablationstests durchführen
- zentrale Kennzahlen und Ergebnis-Views erzeugen

**Gate 2 – `PASS`:** Alle Kriterien G2-01 bis G2-16
wurden mit realer Evidenz einzeln bestanden. Der unabhängige Prüfbericht steht
in `reports/backtest/phase2b_method/PHASE2B_INDEPENDENT_ACCEPTANCE_REPORT.md`.
Der freigegebene finale Einmallauf wurde abgeschlossen. Die
Methode bleibt unverändert; die Ergebnisse dürfen nur noch interpretiert,
visualisiert und präsentiert werden.

## Phase 3 - Validierung und Power BI


**Status:** Die zeitliche Out-of-Sample-Prüfung und die
Kostensensitivität sind abgeschlossen. Kein Signal-Horizont war in
Development, Validation und finalem Test gleichzeitig netto positiv. Das
Power-BI-Dashboard wurde auf der geprüften Datenbasis erstellt und gespeichert.
Machine Learning wurde entsprechend der dokumentierten Scope-Entscheidung nicht
begonnen.

Abgeschlossen sind:

- zeitliche Out-of-Sample- und Marktphasenprüfung
- Sensitivitätsanalyse
- Laden und Modellieren der Power-BI-Daten
- geprüfte Beziehungen und zentrale DAX-Kennzahlen
- Filter- und Segmentprüfung
- Dashboard mit fünf Berichtsseiten
- Darstellung der zentralen Ergebnisse, Kostenwirkung und Datenqualität

Noch offen sind die abschließende Dokumentationsprüfung, die Präsentation und
die finale Abgabevorbereitung.

**Gate 3 – noch final zu prüfen:** Das fertige Dashboard muss im abschließenden
Abgabecheck die Forschungsfragen verständlich beantworten und jede Hauptaussage
mit den geprüften Daten belegen.

## Phase 4 - Präsentation und Abgabequalität


- PowerPoint für 20 bis 30 Minuten erstellen
- vollständige Projektdateien aufräumen
- Reproduzierbarkeit von einem sauberen Start prüfen
- End-to-End-Test und Ergebnisabgleich durchführen
- Testpräsentationen und Zeitmessung
- finale Abgabe-Checkliste durchführen

**Gate 4:** Alle Anforderungen sind nachgewiesen, die Abgabedateien öffnen fehlerfrei, die Präsentation liegt im Zeitfenster und die zentralen Entscheidungen und Ergebnisse sind nachvollziehbar dokumentiert.
