# Gate-1-Abnahmevertrag

## Aktueller Status

**Gesamtstatus Gate 1: `PASS_WITH_DOCUMENTED_SOURCE_ANOMALIES`.**

**Phase-1B-Teilurteil vom 1. August 2026:
`PASS_WITH_DOCUMENTED_SOURCE_ANOMALIES`.**

**Unabhängiges Phase-1C-A-Abnahmeurteil vom 1. August 2026: `PASS`.**

**Unabhängiges Phase-1C-B-Abnahmeurteil vom 1. August 2026: `PASS`.**

**Unabhängiges Phase-1C-C-Abnahmeurteil vom 1. August 2026: `PASS`.**

Der kontrollierte historische Vollimport endete mit
`COMPLETED_WITH_SOURCE_ANOMALIES`. Der autoritative Checkpoint der Generation
185 und seine vier materialisierten Projektionen wurden anschließend offline
und read-only geprüft. G1-01 bis G1-09 sowie G1-11 besitzen damit reale
Phase-1B-Nachweise. Die anschließende unabhängige Offline-Abnahme bestätigt
die kanonischen Processed-Tabellen und den leakage-sicheren D+1-As-of-Join;
G1-10 ist deshalb `PASS`. Die unabhängige Offline-Abnahme von Phase 1C-B
bestätigt SQL-Schema, Fakt-/Dimensionstabellen, sechs Kern-Views und die
vollständige fail-closed Berichtscachevalidierung; G1-12 ist deshalb `PASS`.
Die unabhängige Offline-Abnahme von Phase 1C-C bestätigt die EDA-Ausgaben und
den Power-BI-Datenvertrag; G1-13 ist deshalb `PASS`. Ausschließlich G1-03,
G1-05 und G1-06 behalten wegen der dokumentierten Binance-
Quellenkontinuitätsabweichungen den Status
`PASS_WITH_DOCUMENTED_SOURCE_ANOMALIES`. Alle übrigen Kriterien sind `PASS`.
Gate 1 ist damit abgeschlossen.

Der Gate-1-Abschluss beginnt weder Phase 2 noch Backtesting automatisch. Dafür
ist weiterhin ein ausdrücklicher Folgeauftrag erforderlich.

## Reale Importbasis

| Kennzahl | Nachgewiesener Wert |
|---|---:|
| Binance-Monatsarchive | 180/180 |
| Binance-CHECKSUM-Dateien | 180/180 |
| Bestandene Anbieterprüfsummen | 180/180 |
| Kalender-Soll Raw 1h | 131.472 |
| Beobachtetes Raw-Ist 1h | 131.430 |
| Raw-Delta | -42 |
| Akzeptierte 1h-Interimzeilen | 116.208 |
| Akzeptierte 4h-Interimzeilen | 29.052 |
| Gültige Asset-Monate | 159 |
| Ausgeschlossene Asset-Monate | 21 |
| Betroffene Kalendermonate je Asset | 7 |
| Anomaliezeilen | 96 |
| Zusammenhängende Anomalieintervalle | 24 |
| Bestandene Coin-Metrics-Tage | 1.828 |
| Akzeptierte zeitliche Abdeckung | 88,39 % |
| Konservativ ausgeschlossene Abdeckung | 11,61 % |

Die sieben betroffenen Kalendermonate sind bei `BTCUSDT`, `ETHUSDT` und
`SOLUSDT` identisch: `2021-02`, `2021-03`, `2021-04`, `2021-08`, `2021-09`,
`2021-12` und `2023-03`. Der Raw-Quelle fehlen insgesamt nur 42 Stunden.
Die deutlich größere ausgeschlossene Abdeckung entsteht durch die
konservative Regel, jeden betroffenen Asset-Monat vollständig aus dem
Interim auszuschließen. Beide Effekte werden deshalb getrennt berichtet.

## Unabhängig abgenommene Phase-1C-A-Basis

| Kennzahl | Unabhängig bestätigter Wert |
|---|---:|
| Processed-1h-Zeilen | 116.208 |
| Processed-4h-Zeilen | 29.052 |
| Join-Zeilen gesamt | 145.260 |
| D+1-Matches | 145.260 / 145.260 |
| Join-Verluste | 0 |
| Join-Aufblähung | 0 |
| Primärschlüsselduplikate | 0 |
| Nullwerte | 0 |
| Zukunftskontext-Verletzungen | 0 |
| Gemeinsame zulässige Kalendermonate | 53 |
| Gemeinsame Segmente | 5 |

Jede Processed-Marktzeile entspricht ihrer Interimzeile. Sämtliche
Kontextzuordnungen stimmen mit einem unabhängig berechneten
rückwärtsgerichteten As-of-Join überein; in jeder Zeile gilt
`available_from_utc_d1 <= decision_time_utc`. D+2 bleibt getrennt erhalten.
Ausgeschlossene Monate kommen nicht in Processed vor. Die gemeinsame
Assetmaske, fünf Segmente ohne innere Zeitlücken, alle Manifesthashes sowie
unveränderte Raw-, Interim- und Phase-1B-Nachweise wurden unabhängig bestätigt.

## Unabhängig abgenommene Phase-1C-B-Basis

| Kennzahl | Unabhängig bestätigter Wert |
|---|---:|
| SQLite-Faktenzeilen | 145.260 |
| 1h-Zeilen | 116.208 |
| 4h-Zeilen | 29.052 |
| Faktenzeilen je Asset | 48.420 |
| Assets | 3 |
| Segmente | 5 |
| Kern-Views | 6 |
| Primärschlüsselduplikate | 0 |
| Fremdschlüsselverletzungen | 0 |
| Pflichtfeld-Nullwerte | 0 |
| Zukunftskontextverletzungen | 0 |
| Zeilen aus ausgeschlossenen Monaten | 0 |

`PRAGMA integrity_check` meldet `ok`; jede SQL-Faktenzeile stimmt mit ihrer
Processed-Quelle überein. Der unabhängig bestätigte logische
Datenbankfingerprint lautet
`cbf6d93ebb86a591764a4e07327152cba24c2033c9bed57b5bd14e69abf1e367`.
Der reale read-only Cachetest liefert `CACHED_VALID`. Alle vier SQL-Berichte
werden deterministisch im Arbeitsspeicher neu erzeugt und bytegenau geprüft.
Veränderte, fehlende, zusätzliche oder umsortierte Evidenz sowie geänderte
SQL-Skripte stoppen fail-closed, ohne Datenbank oder Berichtsbündel zu ändern.

### Unveränderlicher technischer Buildzeitstatus

Die SQL-Buildgeneration entstand vor der unabhängigen Gate-Abnahme. Der
folgende technische Status ist deshalb ausschließlich der unveränderliche
Eingabevertrag des Cacheartefakts und ausdrücklich **nicht** die aktuelle
Gate-1-Matrix:

| ID | Bedeutung | Buildzeitstatus |
|---|---|---|
| G1-12 | Status bei Erzeugung der technischen SQL-Evidenz | NOT_EVALUATED |

Die nachfolgende aktuelle Gate-1-Matrix enthält das spätere unabhängige
Abnahmeurteil `PASS`.

## Unabhängig abgenommene Phase-1C-C-Basis

| Kennzahl | Unabhängig bestätigter Wert |
|---|---:|
| Faktenzeilen gesamt | 145.260 |
| 1h-Zeilen | 116.208 |
| 4h-Zeilen | 29.052 |
| Assets | 3 |
| Segmente | 5 |
| Leere Close-to-close-Renditen an Segmentstarts | 30 |
| Berechnungen über Lücken oder Segmentgrenzen | 0 |
| Faktenzeilen aus ausgeschlossenen Monaten | 0 |
| Zukunftskontextverletzungen | 0 |
| Kalendertage 2021-01-01 bis 2025-12-31 | 1.826 |
| Akzeptierte Kalendertage | 1.614 |
| Sichtbar markierte Ausschlusstage | 212 |
| Betroffene Ausschlussmonate | 7 |
| Doppelte Schlüssel | 0 |
| Verwaiste Fremdschlüssel | 0 |
| Power-BI-Manifestzuordnungen | 5/5 korrekt |
| EDA-Manifestzuordnungen | 16/16 korrekt |
| Realer Cachetest | CACHED_VALID |
| Automatisierte Tests | 264/264, keine übersprungen |

Das Kontextalter ist nach Bezugszeitpunkt getrennt. Die Werte stehen jeweils
für Minimum, Median und Maximum:

| Feld | 1h | 4h |
|---|---:|---:|
| `context_age_hours` seit Coin-Metrics-Quellzeitpunkt | 24 / 35,5 / 47 Stunden | 24 / 34 / 44 Stunden |
| `context_age_since_d1_hours` seit D+1-Verfügbarkeit | 0 / 11,5 / 23 Stunden | 0 / 10 / 20 Stunden |

Alle 25 Phase-1C-C-Ausgaben unter `reports/eda/`, `powerbi/` und dem lokalen
Power-BI-Export wurden bei der unabhängigen Abnahme nach SHA-256, Dateigröße
und Änderungszeit unverändert bestätigt. Processed-, Join-, SQL-, EDA- und
Power-BI-Vertragsstufe besitzen keine offene Qualitätsabweichung. Die
historischen Buildzeitstatus in den hashgebundenen Artefakten wurden nicht
nachträglich geändert.

## Gate-1-Teilmatrix

| ID | Kriterium | Reale Evidenz | Status |
|---|---|---|---|
| G1-01 | Alle 180 Binance-Monatsarchive und 180 CHECKSUM-Dateien liegen vor. | `raw_manifest.csv` enthält 180 Archive und 180 CHECKSUM-Objekte für drei Assets und 60 Monate. | PASS |
| G1-02 | Jede Binance-Anbieterprüfsumme stimmt mit dem lokalen SHA-256 überein. | 180/180 Archive besitzen `provider_checksum_match = true`; alle Paare wurden offline erneut geprüft. | PASS |
| G1-03 | Tatsächliche 1h-Zeilen entsprechen dem unveränderten Kalender-Soll oder jede Abweichung ist einzeln erklärt. | Soll 131.472, Raw-Ist 131.430, Delta -42. Alle Abweichungen stehen in 96 kanonischen Evidenzzeilen; Sollwerte wurden nicht abgesenkt. | PASS_WITH_DOCUMENTED_SOURCE_ANOMALIES |
| G1-04 | Primärschlüssel und Zeitstempel enthalten keine Duplikate. | 180/180 Monatsprüfungen melden `duplicate_timestamps = 0`; alle 159 akzeptierten 1h-Dateien wurden erneut deterministisch geprüft. | PASS |
| G1-05 | Start, Ende, UTC-Ausrichtung, Kerzenschlusszeiten und 1h-Abstände sind exakt oder jede Quellenunterbrechung ist einzeln ausgewiesen. | 159 Monate bestehen alle Grenz-, Einheits-, Alignment- und Abstandsregeln. 21 Asset-Monate mit belegten Zeitabweichungen sind vollständig dokumentiert und ausgeschlossen. | PASS_WITH_DOCUMENTED_SOURCE_ANOMALIES |
| G1-06 | Datenlücken werden erkannt und nicht stillschweigend aufgefüllt. | Alle 180 Raw-/CHECKSUM-Paare wurden unabhängig von der CSV neu geprüft. Für 21 Asset-Monate entstanden weder 1h- noch 4h-Interimdaten; keine Interpolation, Ersatzbörse oder synthetische Kerze. | PASS_WITH_DOCUMENTED_SOURCE_ANOMALIES |
| G1-07 | OHLC-Beziehungen sind logisch; Preise sind positiv und Volumen ist nichtnegativ. | Global null OHLC-Fehler, null nichtpositive Preiszeilen, null negative Volumenzeilen sowie null nicht-endliche Werte in den akzeptierten Daten. | PASS |
| G1-08 | Jede 4h-Kerze besteht aus vier vollständigen, gültigen und aufeinanderfolgenden 1h-Kerzen. | 29.052 akzeptierte 4h-Zeilen stimmen bytegenau mit der Ableitung aus 116.208 akzeptierten 1h-Zeilen überein. Anomaliemonate erzeugten keine 4h-Datei. | PASS |
| G1-09 | Coin Metrics deckt 2020-12-30 bis 2025-12-31 vollständig ab; Werte sind endlich und nichtnegativ. | 1.828/1.828 Tage, exakte Grenzen, Asset ausschließlich `btc`, null Duplikate, null Tageslücken, null nicht-endliche und null negative Metrikwerte. | PASS |
| G1-10 | Kein Join verwendet Zukunftsinformation; Join-Verluste und fehlender Kontext sind quantifiziert. | Die unabhängige Offline-Abnahme bestätigt 145.260/145.260 rückwärtsgerichtete D+1-As-of-Matches, null Verluste, null Aufblähung, null Nullwerte und null Zukunftsverletzungen. Für jede Zeile gilt `available_from_utc_d1 <= decision_time_utc`; D+2 bleibt getrennt. | PASS |
| G1-11 | Raw-, Interim- und spätere Processed-Stufen sind reproduzierbar; ein Wiederanlauf verändert keine vorhandene Raw- oder gültige Interimdatei. | Phase-1B-Teilnachweis bestanden: Checkpoint-Schema 4, vollständige Policy-Migration, übereinstimmende Projektionshashes, 15-Spalten-Vertrag, 180 Monatsprüfungen, Januar-Dateien nach Hash und Änderungszeit unverändert, keine `.part`-Dateien. `Processed = 0` war vor Phase 1C der erwartete Checkpointzustand; die späteren Processed- und SQL-Inhalte sind nun zusätzlich unter G1-10 und G1-12 unabhängig bestanden. | PASS |
| G1-12 | Die finale Analysetabelle besitzt mehr als 10.000 gültige Zeilen und SQL-Schema/Kern-Views sind reproduzierbar. | Die unabhängige Offline-Abnahme bestätigt 145.260 Faktenzeilen, drei Assets, fünf Segmente, sechs Kern-Views, vollständige Übereinstimmung mit Processed sowie null Duplikate, Fremdschlüssel-, Nullwert-, Status-, Ausschlussmonat- oder Zukunftsverletzungen. Logischer Fingerprint `cbf6d93ebb86a591764a4e07327152cba24c2033c9bed57b5bd14e69abf1e367`; der bytegenaue Berichtscache ist fail-closed gehärtet und real als `CACHED_VALID` bestätigt. | PASS |
| G1-13 | EDA-Ausgaben und Power-BI-Datenvertrag basieren auf der geprüften Tabelle. | Die unabhängige Offline-Abnahme bestätigt 145.260 Faktzeilen, sieben EDA-Tabellen, sechs deterministische SVGs, eindeutige Dimensionen, vier gültige 1:n-Beziehungen, null ausgeschlossene Fakten, null Zukunftskontext und 30 leere Renditen an Segmentstarts. Der lückenlose Kalender enthält 1.826 Tage, davon 1.614 akzeptiert und 212 in sieben Ausschlussmonaten sichtbar markiert. Quellalter und Alter seit D+1 sowie globale und kalenderfilterabhängige Abdeckung sind getrennt definiert. 5/5 Power-BI- und 16/16 EDA-Manifestzuordnungen stimmen; der reale Cachetest ist `CACHED_VALID`. | PASS |

## Verbindliche Phase-1C-Regeln

Für jede Processed-Tabelle, jedes Feature und jede spätere Simulation gilt:

1. Über ausgeschlossene Monatsgrenzen hinweg werden keine Renditen,
   Indikatoren, Signale oder Positionen berechnet beziehungsweise fortgeführt.
2. Rollende Zeitreihenzustände werden nach jeder Lücke vor der ersten wieder
   zulässigen Kerze zurückgesetzt und benötigen erneut ihre vollständige
   historische Mindestlänge.
3. `BTCUSDT`, `ETHUSDT` und `SOLUSDT` teilen dieselbe Verfügbarkeitsmaske; ein
   Analysezeitpunkt ist nur innerhalb eines für alle drei Assets akzeptierten
   Monats zulässig.
4. Berichte nennen sowohl 88,39 % akzeptierte zeitliche Abdeckung als auch
   11,61 % konservativ ausgeschlossene Abdeckung.
5. Die tatsächliche Raw-Quellenlücke von insgesamt 42 Stunden wird getrennt
   vom vollständigen Ausschluss von 21 Asset-Monaten beziehungsweise 15.264
   Kalenderstunden dargestellt.

Diese Regeln verhindern, dass rollende Berechnungen oder offene Positionen
eine Datenlücke unsichtbar überbrücken. Gemeinsame Maske, fünf lückenfreie
Segmente und Segmentwechsel wurden in Phase 1C-A unabhängig abgenommen.
Spätere Rolling Features, Renditen, Signale und Positionen müssen diese
Grenzen weiterhin verbindlich beachten.

## Checkpoint- und Berichtsbindung

Der geprüfte Checkpoint besitzt:

- `checkpoint_schema_version = 4`,
- `generation_id = 185`,
- `execution_status = COMPLETED_WITH_SOURCE_ANOMALIES`,
- `gate_1 = NOT_EVALUATED`,
- `binance_interim_1h_schema_id = binance_1h_market_v1`,
- den Processing-Policy-Fingerprint
  `ab2b62be100a23ca06fd0337ca56b6d33ce290531dd976561de90d99abb551da`,
- eine vollständige Migration vom alten Generation-2-Fingerprint,
- die erwarteten SHA-256-Werte aller vier Berichtsprojektionen.

Der Checkpointwert `gate_1 = NOT_EVALUATED` ist der unveränderliche technische
Buildzeitstatus der Phase-1B-Generation und nicht das spätere unabhängige
Gesamturteil. Das aktuelle Gate-1-Urteil steht ausschließlich in der obigen
Teilmatrix und den übergeordneten Statusdokumenten.

`raw_manifest.csv`, `binance_quality_summary.csv`, `source_anomalies.csv` und
`coinmetrics_quality_summary.json` stimmen bytegenau mit diesen gespeicherten
Projektionshashes überein. Der Checkpoint bleibt die autoritative Quelle.

## Entscheidungsregel

Das Phase-1B-Teilurteil lautet
`PASS_WITH_DOCUMENTED_SOURCE_ANOMALIES`, weil der historische Import
vollständig, reproduzierbar und ohne synthetische Reparatur abgeschlossen ist
und jede Quellenabweichung einen prüfbaren Nachweis besitzt.

Phase 1C-A und G1-10, Phase 1C-B und G1-12 sowie Phase 1C-C und G1-13 sind
unabhängig mit `PASS` bestanden. Gate 1 ist formal mit
`PASS_WITH_DOCUMENTED_SOURCE_ANOMALIES` abgeschlossen. Die Einschränkung
stammt ausschließlich aus G1-03, G1-05 und G1-06: Die Binance-
Quellenabweichungen wurden nicht aufgefüllt oder verborgen, sondern durch den
konservativen Ausschluss von 21 Asset-Monaten sichtbar erhalten. Die reale
Quellenlücke von 42 Asset-Stunden bleibt getrennt von 88,39 % akzeptierter und
11,61 % konservativ ausgeschlossener zeitlicher Abdeckung dokumentiert.

## Spätere Power-BI-Dashboardarbeit

Der abgenommene G1-13-Vertrag bildet die geprüfte Datenbasis. In einem
separaten späteren Auftrag sind die Exporte in Power BI zu laden, Datentypen
und Sortierung zu konfigurieren, vier 1:n-Beziehungen herzustellen, die
vertraglich festgelegten DAX-Measures umzusetzen, Filter- und Segmentverhalten
zu prüfen sowie Dashboard und `.pbix` zu erstellen. Diese Arbeit wurde nicht
begonnen.

Für Schlusskursvergleiche zwischen BTC, ETH und SOL sind Small Multiples, eine
logarithmische Skala oder eine indexierte Entwicklung empfehlenswert. Eine
gemeinsame lineare Preisachse würde ETH und SOL gegenüber BTC optisch stark
zusammendrücken. Diese Empfehlung ist nicht blockierend.

## Einfache Erklärung für die Präsentation

Gate 1 ist abgeschlossen. Die einzigen Einschränkungen sind bekannte Lücken in
historischen Binance-Quelldaten. Sie wurden nicht erfunden, aufgefüllt oder
versteckt; stattdessen wurden 21 betroffene Asset-Monate vollständig aus der
Analyse ausgeschlossen. Processed-Join, SQL, EDA und Power-BI-Datenvertrag
sind unabhängig bestanden. Das spätere interaktive Power-BI-Dashboard baut auf
dieser geprüften Grundlage auf, wurde aber noch nicht begonnen.
