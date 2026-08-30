# Phase 2B – unabhängige Offline-Abnahme und Gate-2-Entscheidung

## 1. Entscheidung

**Prüfdatum:** 3. August 2026  
**Phase 2B:** `PASS`  
**Gate 2:** `PASS`  
**Finaler Test 2024–2025:** `SEALED_NOT_EVALUATED`

Das korrigierte Development-/Validation-Paket wurde in einer isolierten Kopie
der bereitgestellten Projekt-ZIP unabhängig geprüft. Die Prüfung fand ohne
Netzwerkzugriff, Remote, Push, Commit, Staging, Branchwechsel oder Auswertung
des finalen Testzeitraums statt.

Alle Kriterien G2-01 bis G2-16 besitzen ausreichende reale Evidenz und werden
mit `PASS` bewertet. Ein positives Trading-Ergebnis war keine Gate-Bedingung.
Die überwiegend negativen Nettodurchschnitte nach Kosten werden unverändert als
historischer Befund akzeptiert.

## 2. Verbindlicher Ausgangszustand

Vor der Prüfung wurden bestätigt:

- Branch: `phase/2-signals-backtest`
- HEAD: `eecb59de57d0e03d874019f76251cdd45087f72e`
- lokale Commits: 10
- Staging: leer
- Remotes: keine
- `.part`-Dateien: keine
- Gate 1: `PASS_WITH_DOCUMENTED_SOURCE_ANOMALIES`
- Phase 2B vor Abnahme: `NOT_EVALUATED`
- Gate 2 vor Abnahme: `NOT_EVALUATED`
- finaler Test: `SEALED_NOT_EVALUATED`

Die folgenden fachlichen Änderungen lagen bereits uncommitted vor:

- `config/backtest_phase2b.json`
- `src/backtest_pipeline.py`
- `tests/test_backtest_pipeline.py`
- `reports/backtest/phase2b_method/`
- `reports/backtest/phase2b_outputs/`
- Statusanpassungen in README, Projektplan, Entscheidungen und Traceability

Während dieser Abnahme wurde kein Git-Commit erstellt.

## 3. Prüfumfang und Quellen

Geprüft wurden insbesondere:

1. offizielle DSP-Arbeitszusammenfassung und Projektregeln,
2. Phase-2A-Vertrag und vorregistrierte Features, Signale, Splits und Kosten,
3. Phase-2B-Code und Tests,
4. alle geschützten Phase-1-Eingaben,
5. das gültige 18-Dateien-Phase-2B-Paket,
6. das quarantänisierte ungültige 18-Dateien-Paket,
7. sämtliche Feature-, Signal-, Trade-, Baseline- und Ergebnisdateien,
8. die technische Versiegelung des finalen Testzeitraums.

Die unabhängigen maschinenlesbaren Prüfnachweise liegen unter
`reports/backtest/phase2b_method/independent_evidence/`.

## 4. Hash-, Manifest- und Quarantäneprüfung

Die zentralen Dateien wurden neu gehasht:

| Objekt | Neu berechneter SHA-256 | Ergebnis |
|---|---|---|
| Phase-2B-Konfiguration | `16382dc037b56fa30b5cfabff2dc1f336dab81b13c62cffafcef44f5c391a78e` | stimmt |
| Backtest-Pipeline | `8a8ab18b5575d5366429683cede5253bb3bd4640964671da8d879848d124d28c` | stimmt |
| Phase-2A-Vertrag | `e4deb6b6ad56a8517f86822d85086524c3cd3c29890d9754453163d7f1107f04` | stimmt |
| Processed 1h | `7468ce970381e34fc60a8227fb1594dee5435e88f5521f06ed82bfa15f5ce805` | stimmt |
| Processed 4h | `ab2ff44340b295d140db9fa1cb81cf5690dc7d78a44392599381c1d2e7edc91b` | stimmt |
| SQLite | `7f2e5deadd2c3c3e3f1820266f7f7b680def14d6ecda62c8dbbf5a11d9f0033e` | stimmt |

Auch alle sieben geschützten Dateigruppen stimmen hinsichtlich Dateizahl,
Gesamtbytes und Gruppenfingerprint, einschließlich Raw mit 361 Dateien und
Interim mit 319 Dateien.

Das gültige Paket besitzt:

- 18 Dateien,
- 196.071.683 Bytes,
- 17/17 korrekte Manifesteinträge,
- korrekte SHA-256-Werte und CSV-Zeilenzahlen,
- keine fehlenden, zusätzlichen oder partiellen Dateien.

Die Quarantäne besitzt weiterhin:

- Status `INVALID_NOT_FOR_ANALYSIS`,
- 18 Dateien,
- 174.213.618 Bytes,
- 18/18 korrekte Dateihashes und Größen.

Es wurde keine quarantänisierte Performancezahl als gültige Evidenz verwendet.

## 5. Unabhängige Feature-, Kontext- und Signalprüfung

Die Markt- und Kontextberechnungen wurden außerhalb der produktiven
Pipeline erneut aus den geschützten Eingaben aufgebaut.

Geprüft wurden:

- 79.470 eindeutige Development-/Validation-Marktzeilen,
- 158.940 Featurezeilen für D+1 und D+2,
- 2.701.980 einzelne Marktfeaturewerte,
- 1.271.520 einzelne Kontextfeaturewerte,
- 794.700 Signalentscheidungen,
- alle 25 vorregistrierten Features,
- alle fünf vorregistrierten Signale,
- alle Segment- und Warm-up-Resets,
- beide unabhängigen rückwärtsgerichteten Kontext-Joins.

Ergebnis: **null Abweichungen**.

Die exakte Decimal-Referenz bestätigte insbesondere SOLUSDT 1h in
`SEGMENT_004`:

| Zeitpunkt | SMA20 | SMA50 | Crossover |
|---|---:|---:|---|
| 18.12.2022 18:00 UTC | 12,4110 | 12,4110 | nein |
| 18.12.2022 19:00 UTC | 12,4075 | 12,4016 | ja |

D+1 und D+2 besitzen identische Marktsignale. Ihre Unterschiede liegen nur in
den unabhängig verfügbaren Kontextwerten.

Weitere bestätigte Punkte:

- keine verbotenen Zukunfts- oder Ergebnisfelder in Featuretabellen,
- keine Final-Test-Featurezeilen,
- keine ausgeschlossenen Monatszeilen,
- keine doppelten Feature-Schlüssel,
- keine Zukunftskontextverletzung,
- Breakout ausschließlich gegen das verschobene vorherige 20er-Hoch.

## 6. Unabhängige Trade- und Kostenprüfung

Alle Trades und Baselines wurden aus den validierten Featurezeilen unabhängig
rekonstruiert.

Geprüft wurden:

- 60 getrennte Featuregruppen,
- 88.208 Trades vor Vervielfachung durch Kostenszenarien,
- 264.624 Trade-/Kostenzeilen,
- 360 Signalhäufigkeitszeilen,
- jedes Entry- und Exit-Open,
- jede Haltedauer,
- jede Überlappungsablehnung,
- jede Segment- und Splitgrenze,
- MAE, MFE und Expositionsstunden,
- alle Kostenfälle 20, 30 und 50 Basispunkte,
- `positive_net_outcome`.

Ergebnis: **null Abweichungen**.

Bestätigt wurden insbesondere:

- Signale entstehen nach vollständigem Schluss von Kerze `t`,
- Einstieg am Open der nächsten Kerze,
- Ausstieg nach exakt vollständiger Haltedauer,
- keine Same-Bar-Ausführung,
- keine verkürzten Grenztrades,
- keine Überschreitung von Split oder Segment,
- keine Vermischung von 1h- und 4h-Preisen,
- höchstens eine offene Position je registrierter Strategie-Zelle,
- multiplikative Gebühren- und Slippageformel.

## 7. Ergebnis- und Baselineprüfung

Aus `trades.csv` wurden sämtliche Kennzahlen neu berechnet und mit den
Berichten abgeglichen:

- 1.440/1.440 Detailergebniszeilen stimmen,
- 480/480 aggregierte Ergebniszeilen stimmen,
- 360/360 Baselinezeilen stimmen,
- 720/720 D+1-/D+2-Vergleichszeilen stimmen.

Neu geprüft wurden unter anderem Tradeanzahl, Trefferquote, Brutto- und
Nettomittel, Mediane, kumulierte Zellrendite, Streuung, Drawdown, Profit
Factor, Kostenbelastung, Exposition und Segmentstabilität.

Gepoolte fünf Signale im primären D+1- und 30-bp-Fall:

| Split | TF | Trades | Ø brutto | Ø netto | Trefferquote |
|---|---|---:|---:|---:|---:|
| Development | 1h | 8.919 | +0,0344 % | −0,2652 % | 39,52 % |
| Development | 4h | 4.967 | −0,0459 % | −0,3453 % | 39,58 % |
| Validation | 1h | 4.722 | +0,1059 % | −0,1940 % | 33,91 % |
| Validation | 4h | 2.623 | +0,1385 % | −0,1615 % | 34,88 % |

Diese negativen Nettodurchschnitte sind kein Methodenfehler. Sie zeigen, dass
die untersuchten einfachen Einstiegssignale die konservativen Kosten in den
betrachteten historischen Stichproben nicht stabil überwanden.

`always_flat`, periodischer Einstieg und Segment-Buy-and-Hold wurden ebenfalls
vollständig rekonstruiert. Segment-Buy-and-Hold bleibt wegen seines deutlich
anderen Zeithorizonts eine Marktbaseline und kein direkt vergleichbares
Einstiegssignal.

## 8. Finaler Test und G2-12

Der finale Zeitraum 2024–2025 blieb vollständig versiegelt:

- erkannte Eingabezeilen für Schlüssel- und Bestandsintegrität: 65.790,
- ausgewertete Featurezeilen: 0,
- ausgewertete Signale: 0,
- Positionen und Trades: 0,
- Brutto- und Nettorenditen: 0,
- Performancekennzahlen: 0.

Die verbindlichen Projektunterlagen definieren Gate 2 als Methodenfreigabe,
bevor der finale Test genau einmal geöffnet wird. Daher bewertet G2-12 die
nachgewiesene zeitliche Trennung und technische Versiegelung mit `PASS`.

Dies bedeutet ausdrücklich **nicht**, dass der finale Test bereits bestanden
oder ausgewertet wurde. Sein Status bleibt `SEALED_NOT_EVALUATED`. Die spätere
einmalige Ausführung ist das nächste gesondert kontrollierte Arbeitspaket.

## 9. Automatisierte Tests

Die Syntaxprüfung bestand mit Exit-Code 0. Der vollständige monolithische
Testaufruf überschritt in der isolierten Prüfoberfläche nach 201 bereits
bestandenen Tests und ohne Fehler das 300-Sekunden-Ausführungsfenster. Deshalb
wurden alle Testmodule und die große Full-Import-Suite anschließend getrennt
vollständig ausgeführt.

| Testbereich | Tests | Ergebnis |
|---|---:|---|
| Phase-2A-Vertrag | 42 | bestanden |
| Phase-2B-Pipeline | 32 | bestanden |
| Datenpilot | 13 | bestanden |
| EDA/Power BI | 65 | bestanden |
| Vollimport | 133 | bestanden |
| Processed-Pipeline | 19 | bestanden |
| SQL-Pipeline | 41 | bestanden |
| **Gesamt** | **345** | **345 bestanden, 0 übersprungen** |

Zusätzlich bestanden:

- `git diff --check`
- `git diff --cached --check`
- Cache-, Manipulations-, No-Overwrite- und Rollbacktests
- Provenienztests für Code, Konfiguration und Numerik-Policy

Ein zusätzlicher nicht autoritativer Cachelauf in der isolierten Kopie konnte
innerhalb des 300-Sekunden-Limits nicht abgeschlossen werden. Er erzeugte
keine Meldung, keine `.part`-Datei und keine Änderung an den 18 gültigen
Ausgabedateien. Dieser Umgebungs-Timeout ist kein fachlicher Blocker, weil das
vollständige Datenpaket unabhängig feldgenau rekonstruiert wurde, alle Hashes
stimmen, die Cachetests bestehen und der dokumentierte autoritative zweite
Lauf bereits `CACHED_VALID` lieferte.

## 10. G2-01 bis G2-16

| ID | Status | Zentrale unabhängige Evidenz |
|---|---|---|
| G2-01 | PASS | alle Einzelhashes und sieben geschützten Gruppen stimmen |
| G2-02 | PASS | 158.940 Featurezeilen, eindeutige Schlüssel, exakte Schemata und Manifest |
| G2-03 | PASS | 2.701.980 Marktfeatureprüfungen, keine Zukunftsfelder oder Leakage-Abweichungen |
| G2-04 | PASS | Segmentresets und sämtliche Warm-up-Nullwerte feldgenau bestätigt |
| G2-05 | PASS | 1.271.520 Kontextprüfungen; D+1/D+2 getrennt, null Zukunftskontext |
| G2-06 | PASS | Decision-Time und nächste Open-Ausführung für alle rekonstruierten Trades bestätigt |
| G2-07 | PASS | 264.624 Tradezeilen mit handelbaren OHLC-Preisen feldgenau bestätigt |
| G2-08 | PASS | null ausgeschlossene Monate, Grenz- oder verkürzte Trades |
| G2-09 | PASS | 1h 4/12/24 und 4h 1/3/6 Bars einschließlich Exposition bestätigt |
| G2-10 | PASS | 20/30/50 bp und jede Nettorendite unabhängig neu berechnet |
| G2-11 | PASS | alle drei Baselines deterministisch rekonstruiert |
| G2-12 | PASS | Splits überlappungsfrei; finaler Test technisch versiegelt und unberührt |
| G2-13 | PASS | 1.440 Detail- und 480 Aggregatzeilen mit vollständigen Kennzahlen bestätigt |
| G2-14 | PASS | vollständige Asset-, TF-, Split-, Segment-, Kalender- und Aggregatabdeckung |
| G2-15 | PASS | Signalqualität, Kosten, Stabilität und Nutzungsgrenzen getrennt; kein Gewinnversprechen |
| G2-16 | PASS | vollständige unabhängige Rekonstruktion, Hash-/Manifestprüfung und 345 Tests |

## 10.1 Unveränderliche Vorregistrierung

`reports/backtest/GATE2_ACCEPTANCE_CRITERIA.md` bleibt absichtlich byteidentisch zum vorregistrierten Phase-2A-Stand. Die Datei ist Teil des geschützten Phase-2A-Fingerprints und dokumentiert die Kriterien, bevor Ergebnisse bekannt waren. Die nachträgliche reale Bewertung und der Status `PASS` stehen deshalb ausschließlich in diesem unabhängigen Abnahmebericht, in `DECISIONS.md` und in den aktuellen Statusübersichten. So werden Kriterien und spätere Entscheidung nicht rückwirkend vermischt.

## 11. Formale Freigaben

- Technischer Zustand des korrigierten Pakets: **akzeptiert**
- Fachliche Abnahme Phase 2B: **PASS**
- Gesamtstatus Gate 2: **PASS**
- Finaler Test: **weiterhin versiegelt und nicht ausgewertet**
- Commit: **fachlich empfohlen, aber nicht ausgeführt**
- Nächster Schritt: kontrollierter Commit des freigegebenen Methodenstands,
  danach Vorbereitung und genau eine Ausführung des finalen Tests
- Phase 3/Power BI: nach Sicherung des Methodenstands zulässig
- Machine Learning: weiterhin nicht empfohlen, solange Finaltest, Dashboard,
  Interpretation und Präsentation nicht abgeschlossen sind

## 12. Offene Risiken

1. Der finale Test kann schwächere oder andersartige Ergebnisse liefern. Diese
   dürfen anschließend nicht durch Parameteränderungen optimiert werden.
2. Historische Backtests sind keine Garantie für zukünftige Ergebnisse.
3. Buy-and-Hold ist wegen des längeren Zeithorizonts nur eingeschränkt mit
   kurzfristigen Signaltrades vergleichbar.
4. Der freigegebene Stand liegt weiterhin uncommitted vor und muss vor dem
   Finaltest kontrolliert festgeschrieben werden.
5. Power-BI-Dashboard, abschließende Gesamtinterpretation, Präsentation und
   Abgabekontrolle sind noch offen.

## 13. Einfache Erklärung für Amin

Der schwierige technische Teil ist jetzt geprüft: Die Daten sind unverändert,
die Berechnungen schauen nicht in die Zukunft, die Trades verwenden echte
Open-Preise, die Kosten stimmen und der frühere SMA-Fehler ist im neuen Paket
nicht mehr vorhanden. Alle 345 Tests bestehen. Deshalb dürfen Phase 2B und
Gate 2 jetzt als bestanden gelten.

Die Jahre 2024 und 2025 wurden trotzdem noch nicht ausgewertet. Das ist
absichtlich richtig. Als Nächstes wird der geprüfte Methodenstand fest
gespeichert. Erst danach darf der finale Test genau einmal laufen.
