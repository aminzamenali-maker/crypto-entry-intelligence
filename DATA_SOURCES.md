# Datenquellen- und Tabellenkonzept

## Status

Der Datenpilot wurde erfolgreich abgeschlossen. Für den Core
wurden zwei Quellen ausgewählt:

- Binance Public Data als primäre Quelle für historische OHLCV-Daten.
- Coin Metrics Community API als tägliche BTC-Kontextquelle.

Die Auswahl ist in `reports/data_pilot/DATA_PILOT_REPORT.md` dokumentiert.

Der vollständige Phase-1B-Import wurde mit
`COMPLETED_WITH_SOURCE_ANOMALIES` abgeschlossen. Das Teilurteil lautete
`PASS_WITH_DOCUMENTED_SOURCE_ANOMALIES`. Anschließend wurden in Phase 1C der
Processed-Join, das SQL-Modell, die EDA-Ausgaben und der Power-BI-Datenvertrag
unabhängig geprüft. G1-10, G1-12 und G1-13 erhielten `PASS`. Gate 1 ist damit
formal als `PASS_WITH_DOCUMENTED_SOURCE_ANOMALIES` abgeschlossen.

Die dokumentierten Quellenanomalien bleiben dabei bestehen. Sie wurden nicht
aufgefüllt oder verborgen. Vor einer öffentlichen Veröffentlichung werden die
Nutzungsbedingungen erneut geprüft. Rohdaten bleiben lokal und werden nicht in
Git veröffentlicht.

## Benötigte Datenrollen

| Rolle | Mindestfelder | Zweck | Status |
|---|---|---|---|
| Marktpreise/OHLCV | UTC-Zeit, Symbol, Open, High, Low, Close, Volumen | Kerzen, Renditen, Indikatoren, Ausführung | Core: Binance Public Data |
| BTC-Markt-/Netzwerkkontext | täglicher Referenzpreis, Marktkapitalisierung, Transaktionen, aktive Adressen | übergeordneter Kontext und Quellenintegration | Core: Coin Metrics Community API |
| Derivatekontext | Funding, Open Interest oder vergleichbare Größe | Futures-Kontext und Kosten | vorerst nicht Core; nur nach erneutem Gate |
| Gesamtmarktkontext | Marktbreite oder Gesamtvolumen | Regime und Umfeld | Advanced/Bonus |
| Makro-/Risikokontext | ausgewählte, zeitlich verfügbare externe Reihe | Robustheitsanalyse | Stretch: FRED zurückgestellt |

## Ausgewählte Quellen und Reserven

| Quelle | Rolle | Pilotresultat | Entscheidung |
|---|---|---|---|
| Binance Public Data | historische Spot-OHLCV | Pilot bestanden; Vollimport mit 180/180 Archiven, 180/180 CHECKSUM-Dateien und 180/180 passenden Anbieterprüfsummen; 21 Asset-Monate mit dokumentierten Quellenanomalien ausgeschlossen | primäre Core-Quelle |
| Coin Metrics Community API | täglicher BTC-Kontext | Pilot bestanden; Vollimport mit 1.828/1.828 Tagen, exakten Grenzen und bestandener Werteprüfung | ergänzende Core-Quelle |
| Coinbase Exchange Candles | Cross-Exchange-Plausibilisierung | HTTP 200; maximal 300 Kerzen je Anfrage, mögliche Lücken und strengere Nutzungsbedingungen | Reserve für kleine Stichproben |
| CoinGecko Keyless API | Marktbreite/Marktkapitalisierung | HTTP 401 für festen Pilotzeitraum 2024; historische Keyless-Abfrage auf 365 Tage begrenzt | für Core verworfen |
| FRED | Makro-/Risikokontext | CSV erreichbar; offizielle API benötigt Schlüssel, Reihenrechte und Revisionen separat zu prüfen | Stretch |

Der vollständige Kriterienvergleich steht in
`reports/data_pilot/source_candidate_comparison.csv`.

## Verbindlicher Core-Umfang

- Assets: `BTCUSDT`, `ETHUSDT`, `SOLUSDT`
- OHLCV-Zeitraum: `2021-01-01T00:00:00Z` bis ausschließlich
  `2026-01-01T00:00:00Z`
- Rohintervall: `1h`
- Robustheitsintervall: `4h`, reproduzierbar aus vollständigen 1h-Kerzen
  aggregiert
- Coin-Metrics-Kontext: einschließlich `2020-12-30` bis `2025-12-31`. Der
  zusätzliche 30. Dezember 2020 ermöglicht bereits zum Marktstart sowohl die
  primäre D+1-Zuordnung als auch die strengere D+2-Sensitivität. Beide Varianten
  wurden später im Backtest getrennt berechnet.
- Coin-Metrics-Felder: `PriceUSD`, `CapMrktCurUSD`, `TxCnt`, `AdrActCnt`

Die Start- und Endgrenzen wurden zuerst ohne Vollimport in
`reports/data_pilot/history_boundary_probes.csv` und anschließend für alle
180 Monate im realen Qualitätsbericht bestätigt beziehungsweise bei den 21
ausgeschlossenen Asset-Monaten einzeln erklärt.

## Zeit- und Verfügbarkeitsstandard

- Alle Zeitstempel werden als UTC gespeichert.
- Für Binance Public Data Spot gilt verbindlich
  `binance_spot_ms_before_2025_us_from_2025`: Monate bis einschließlich
  2024-12 verwenden Millisekunden, Monate ab 2025-01 Mikrosekunden. Die
  erwartete Einheit kommt aus dem kontrollierten Monatsauftrag und wird gegen
  jede Open- und Close-Time geprüft.
- Der gültige Kerzenschluss ist bei Millisekunden
  `open_time + 1 Stunde - 1 Millisekunde`, bei Mikrosekunden
  `open_time + 1 Stunde - 1 Mikrosekunde`.
- `timestamp_utc` ist der Beginn der OHLCV-Kerze und Teil des Primärschlüssels.
- `close_time_utc` ist der früheste Entscheidungszeitpunkt für die
  abgeschlossene Kerze.
- Der Coin-Metrics-Tageswert für Kalendertag D erhält
  `available_from_utc = D + 1 Tag, 00:00 UTC`.
- D+1 00:00 UTC ist eine konservative methodische Annahme und keine bestätigte
  Garantie des tatsächlichen historischen Veröffentlichungszeitpunkts. Im
  späteren Backtest wurde zusätzlich D+2 00:00 UTC als getrennte
  Sensitivitätsvariante verwendet.
- Kontext wird per rückwärts gerichtetem as-of-Join verbunden. Dabei muss
  `available_from_utc <= close_time_utc` gelten.
- Fehlende Kerzen oder Kontexttage werden markiert; sie werden nicht
  stillschweigend ergänzt.

## Kanonischer Binance-1h-Interimvertrag

Der Parser verwendet intern 20 Spalten, damit Zeitstempeleinheit und
Quellenintegrität vollständig geprüft werden können. Die gespeicherte
1h-Interimdatei enthält dagegen nur den stabilen Marktvertrag
`binance_1h_market_v1` mit dieser exakten Reihenfolge:

```text
symbol
timeframe
timestamp_utc
close_time_utc
open
high
low
close
volume
quote_asset_volume
number_of_trades
taker_buy_base_volume
taker_buy_quote_volume
source
timestamp_unit
```

Die fünf internen Prüffelder `timestamp_policy_id`,
`expected_timestamp_unit`, `observed_open_timestamp_unit`,
`observed_close_timestamp_unit` und `timestamp_unit_errors` bleiben in der
Monatsqualität und im autoritativen Checkpoint. Sie werden nicht zusätzlich in
jede Marktzeile geschrieben. So bleibt das 1h-Interimschema einheitlich, ohne
die Qualitätsprüfung zu schwächen. Eine vorhandene Interimdatei mit 20 Spalten
oder einer anderen abweichenden Struktur wird nicht automatisch angepasst oder
überschrieben. Der Lauf bricht in diesem Fall sicher ab (`fail-closed`).

## Körnung der finalen Analysetabellen

Eine Zeile repräsentiert:

```text
eine vollständig abgeschlossene Kerze × ein Symbol × ein Analysezeitpunkt
```

Primärschlüssel:

```text
(symbol, timeframe, timestamp_utc)
```

## Verbindliche Verfügbarkeitsmaske für Phase 1C

Die Processed-Tabelle darf nur Zeitpunkte verwenden, die nach dem
konservativen Monatsausschluss für alle drei Assets gemeinsam verfügbar sind.
Die Maske wird damit aus der Schnittmenge der zulässigen Zeitpunkte von
`BTCUSDT`, `ETHUSDT` und `SOLUSDT` gebildet.

- Keine Rendite, kein Indikator, kein Signal und keine Position darf über eine
  ausgeschlossene Monatsgrenze fortgeführt werden.
- Rollende Zustände werden nach jeder Lücke zurückgesetzt und erst nach erneut
  aufgebauter Mindesthistorie wieder freigegeben.
- Die gemeinsame Maske wird vor Feature-, Signal- und Positionsberechnung
  angewendet und bleibt als Qualitätsfeld nachvollziehbar.
- Jede Abdeckungsanalyse berichtet 88,39 % akzeptierte und 11,61 %
  konservativ ausgeschlossene Zeit.
- Die Raw-Quellenlücke von 42 Stunden wird getrennt vom vollständigen
  Ausschluss von 21 Asset-Monaten dargestellt.

Diese Regeln wurden in Phase 1C umgesetzt und unabhängig geprüft. G1-10,
G1-12 und G1-13 sind `PASS`. Gate 1 ist mit dem Gesamturteil
`PASS_WITH_DOCUMENTED_SOURCE_ANOMALIES` abgeschlossen.

## Spaltengruppen der Analyse

- Identität: `symbol`, `timeframe`, `timestamp_utc`
- OHLCV: `open`, `high`, `low`, `close`, `volume`
- Rendite/Volatilität: Returns, True Range, ATR, rollende Volatilität
- Trend: gleitende Maße, Marktstruktur, optional Heikin-Ashi-Zustand
- Volumen: relatives Volumen und rollende Volumenmerkmale
- Momentum: begründete, nicht redundante Merkmale
- Kontext: Bitcoin-/Gesamtmarkt-/Derivatevariablen
- Signal: einzelne Bedingungen und kombinierte Regelvarianten
- Ergebnis: Exit-Grund, Haltedauer, Brutto-/Nettoergebnis, Kosten
- Validierung: Zeitraumrolle, Marktregime und Datenqualitätsflags

## Datenqualitätsregeln

- Zeitstempel eindeutig und streng aufsteigend je Symbol/Zeitrahmen
- keine doppelten Primärschlüssel
- `high >= max(open, close, low)` und `low <= min(open, close, high)`
- positive Preise und nichtnegative Volumina
- erwartete Intervallabstände werden geprüft
- fehlende Kerzen werden markiert, nicht stillschweigend erfunden
- externe Merkmale nur mit zum damaligen Zeitpunkt verfügbaren Werten verbinden
- Zeilenverluste durch Joins werden quantitativ protokolliert
- Rohdaten erhalten Quelle, Abrufdatum und Prüfsumme

## Belegte Binance-Quellenanomalien im Vollimport

Das Kalender-Soll beträgt unverändert 131.472 1h-Zeilen. Die 180 Archive
bestanden die Prüfsummenprüfung und enthalten zusammen 131.430 Zeilen. Damit
fehlen in der Rohquelle tatsächlich 42 Asset-Stunden. 159 Asset-Monate sind
vollständig gültig. Weitere 21 Asset-Monate aus sieben Kalendermonaten wurden
nach der konservativen Kontinuitätsregel vollständig ausgeschlossen:

```text
2021-02, 2021-03, 2021-04, 2021-08, 2021-09, 2021-12, 2023-03
```

Jeder dieser Monate betrifft `BTCUSDT`, `ETHUSDT` und `SOLUSDT`. Dokumentiert
sind 96 Anomaliezeilen und 24 zusammenhängende Intervalle. Akzeptiert wurden
116.208 1h-Zeilen und 29.052 4h-Zeilen. Das entspricht 88,39 % zeitlicher
Abdeckung. 11,61 % wurden konservativ ausgeschlossen.

Wichtig ist die Trennung: Die 11,61 % beziehen sich auf vollständig
ausgeschlossene Monate. Die tatsächliche Lücke in den Rohdaten beträgt nur 42
Asset-Stunden.

### Beispiel: Binance-Unterbrechung 2021-02

Der erste kontrollierte Phase-1B-Lauf wurde beim echten Binance-Archiv
`BTCUSDT-1h-2021-02.zip` beendet. Die Anbieter-Prüfsumme stimmt, das ZIP und
das 12-Spalten-Schema sind lesbar. Die Datei enthält aber 671 statt 672
Stundenzeilen:

- Die Kerze mit Beginn `2021-02-11T04:00:00Z` fehlt.
- Die Kerze ab `2021-02-11T03:00:00Z` endet bereits um
  `2021-02-11T03:40:54.773Z` statt um `03:59:59.999Z`.
- Die nächste Kerze beginnt um `2021-02-11T05:00:00Z`.
- Preise, Volumina, Trade-Anzahlen, OHLC-Beziehungen und übrige Zeitgrenzen
  zeigen in diesem Archiv keine weiteren Fehler.

Binance veröffentlichte für diesen Zeitraum einen Hinweis auf eine
vorübergehende Systemwartung, bei der Spot- und Margin-Handel ausgesetzt
wurden. Eine weitere Meldung nennt die Wiederaufnahme um 05:00 UTC. Die
beobachtete Lücke passt zeitlich dazu. Zusätzlich beschreibt ein GitHub-Issue
im offiziellen Datenrepository genau die fehlende 04:00-Kerze.

Diese Hinweise werden nicht als endgültiger Beweis für die Ursache verwendet.
Im Projekt wird deshalb nur die beobachtete Abweichung als
`source_continuity_anomaly` dokumentiert. Der Vollimport bestätigt die
Februar-Abweichung unabhängig für alle drei Assets.

Verwendete Quellen, geprüft am 27. Juli 2026:

- Binance-Wartungshinweis:
  https://www.binance.com/en-IN/support/announcement/detail/7eee583e3d2346d5ac78682ac8ec9a48
- Binance-Wiederaufnahme:
  https://www.binance.com/en/support/announcement/detail/aad7639a0ed9424bad585b508a61a433
- Exakte Dateibeobachtung im offiziellen Repository:
  https://github.com/binance/binance-public-data/issues/365

Methodische Folge: Die fehlende 04:00-Kerze wird weder erfunden noch
interpoliert, vorwärts gefüllt oder von einer anderen Börse übernommen. Der
betroffene Monat erzeugt keine Interimdatei. Auch die verkürzte Kerze und
unvollständige Vierergruppen werden nicht für 4h-Kerzen, Indikatoren oder den
Backtest verwendet. Die Abweichungen stehen getrennt in
`reports/full_import/source_anomalies.csv`.

Eine Quellenlücke kann einen realen Zustand der damaligen Handelsverfügbarkeit
abbilden und ist deshalb nicht automatisch ein beschädigter Download. Umgekehrt
bedeutet eine korrekt heruntergeladene Datei mit passender Prüfsumme nicht, dass
der Marktzeitraum vollständig ist. Deshalb wurden jedes Asset und jeder Monat
einzeln geprüft. Diese dokumentierten Abweichungen wurden später in der
Gate-1-Bewertung berücksichtigt.

## Evidenz- und Wiederanlaufstandard für den Vollimport

Für den abgeschlossenen Phase-1B-Lauf ist
`reports/full_import/execution_checkpoint.json` die autoritative
Zustandsquelle. Generation 185 enthält die Nachweise zu Rohmanifest,
Binance-Monatsqualität, Anomalien, Interimdateien und Coin-Metrics-Seiten.
`scope_id` und ein SHA-256-Fingerprint binden Scope und Konfiguration an diesen
Stand.

Checkpoint-Schema 4 bindet zusätzlich:

- `timestamp_policy_id`
- die Anomalie-Policy `source_anomalies_all_cached_pairs_v1`
- die 1h-Interim-Schema-ID `binance_1h_market_v1`
- den Verarbeitungsrichtlinien-Fingerprint
  `ab2b62be100a23ca06fd0337ca56b6d33ce290531dd976561de90d99abb551da`

Ein unbekannter oder abweichender Checkpoint wird nicht fortgesetzt.

Es gibt genau eine Legacy-Ausnahme. Sie gilt für den alten Fingerprint
`9e75207e0b5a5655366c9513a253adf2325d0f126622774ca2974c0de4533e46`
und den vollständig belegten Schema-4-/Generation-2-Status `HARD_FAILURE` bei
`binance BTCUSDT 2021-01`. Vor einer Übernahme im Arbeitsspeicher werden unter
anderem geprüft:

- Scope, Konfiguration, Richtlinien und Fehlerstatus
- Checkpointzählungen und Evidenzfelder
- die Hashes aller vier Projektionen
- genau vier Raw-Dateien und genau zwei Interimdateien
- Januar-ZIP und CHECKSUM
- die Januar-Monatsqualität
- die bytegenaue 15-Spalten-1h-Projektion
- die unveränderte 4h-Ableitung
- alle vier aus Februar-Raw plus CHECKSUM neu berechneten Anomaliezeilen

Jede andere Generation, Policy, Struktur oder Dateiabweichung führt vor einer
Änderung zum sicheren Abbruch. Die bestehende Checkpointdatei wird dabei nicht
verändert. Eine beschädigte Legacy-Projektion wird ebenfalls nicht automatisch
repariert.

Erst ein separat genehmigtes normales Checkpointschreiben darf eine neue
Policy speichern. `policy_migration` dokumentiert dabei den alten und neuen
Fingerprint, das Quellschema, die Quellgeneration und die neue
Interim-Schema-ID. Absolute lokale Pfade, Benutzername, Run-ID und
Änderungszeiten gehören nicht zu diesem Migrationsnachweis.

Öffentliche kanonische Binance-Objekt- und CHECKSUM-URLs dürfen als
Herkunftsnachweis in Manifest und Checkpoint stehen. Sie sind keine
Zugangsdaten. Coin-Metrics-Cursor, vollständige Paging-URLs, sensible
Queryparameter und Zugangsdaten werden dagegen nicht gespeichert. Neue lokale
Fehlermeldungen verwenden projektrelative Pfade.

Jede vollständig gespeicherte und als JSON geprüfte Coin-Metrics-Seite wird im
Checkpoint mit Seitenschlüssel, Seitennummer, lokalem Pfad, SHA-256,
Zeilenzahl und Cache-Status festgehalten. Wenn eine spätere Seite fehlschlägt,
bleiben die Nachweise der bereits gespeicherten Seiten erhalten. Beim
Wiederanlauf wird eine vorhandene Seite gelesen, geprüft und mit dem Checkpoint
verglichen. Sie wird nicht überschrieben.

Aus dem Checkpoint werden vier Ausführungsprojektionen erzeugt:

- `raw_manifest.csv`
- `binance_quality_summary.csv`
- `source_anomalies.csv`
- `coinmetrics_quality_summary.json`

Jede Datei wird einzeln atomar ersetzt. Es gibt jedoch keine gemeinsame
Transaktion über alle vier Dateien. Deshalb enthält der Checkpoint die
Generationskennung und den erwarteten SHA-256 jeder Projektion. Fehlende,
unvollständige oder abweichende Projektionen werden beim Wiederanlauf erkannt
und aus dem letzten autoritativen Zustand neu erzeugt.

Die bereits fachlich geprüfte `source_anomalies.csv` mit vier Februar-Befunden
darf schon vor dem ersten realen Checkpoint vorhanden sein. Sie bestimmt aber
niemals selbst den Prüfumfang. Der Prüfumfang kommt immer aus der sicheren
Konfiguration.

Für jedes vollständig vorhandene Raw-/CHECKSUM-Paar werden geprüft:

- Archivname
- gespeicherter Anbieterprüfwert
- Archiv-SHA
- ZIP-Struktur und Schema
- Zeitstempeleinheit
- Monatsqualität

Danach wird die vollständig neu berechnete kanonische Anomaliemenge feldgenau
mit der physischen CSV verglichen. Der Lauf stoppt vor Änderungen, wenn zum
Beispiel Anomaliezeilen fehlen, zusätzliche oder doppelte Zeilen vorhanden
sind, das physische CSV-Schema abweicht oder eine Header-only-Datei trotz
belegter Anomalien vorliegt. Nur nach vollständiger Übereinstimmung ist
`validated_preexisting_csv` zulässig. Die Reihenfolge der Zeilen darf
abweichen und wird sicher kanonisch normalisiert.

Fehlt die CSV, können belegte Anomalien ohne Änderung der Raw-Dateien mit
`recomputed_from_cached_raw` neu aufgebaut werden. Die lokale CHECKSUM beweist
nicht kryptografisch die Identität des Anbieters. Sie zeigt zusammen mit dem
dokumentierten ursprünglichen Download nur, dass Archiv und gespeicherter
Anbieterprüfwert weiterhin übereinstimmen.

Coin-Metrics-Fehler werden getrennt als `coinmetrics_page_fetch`,
`coinmetrics_page_parse`, `coinmetrics_page_persist`,
`coinmetrics_aggregate_quality`, `coinmetrics_interim_write` oder
`coinmetrics_completed` protokolliert. Versuchte Seiten und vollständig
gesicherte Seiten bleiben getrennt. Dadurch kann eine fehlgeschlagene
Gesamtprüfung nicht fälschlich als vollständig gespeicherte Seite erscheinen.

Die Zählungen unterscheiden dauerhaft zwischen:

- vollständigem kalenderbasiertem Scope
- Sollzeilen der bereits sicher geprüften Monate
- beobachteten Raw-Zeilen
- akzeptierten Interimzeilen
- übersprungenen Raw-Zeilen aus Kontinuitätsmonaten
- Befundzeilen, betroffenen Asset-Monaten und zusammenhängenden Zeitintervallen

Für Januar und den beobachteten Februar ergeben die Offline-Regressionen 1.416
erwartete und 1.415 beobachtete 1h-Rohzeilen, 744 akzeptierte 1h-Interimzeilen,
671 übersprungene Februar-Rohzeilen sowie 354 erwartete und 186 akzeptierte
4h-Zeilen. Die Scope-Sollwerte bleiben unverändert 131.472 beziehungsweise
32.868.

## Quellenlog-Vorlage

| Quelle | URL/Endpoint | Felder | Zeitraum | Abrufdatum | Lizenz/Nutzung | Datei/Prüfsumme |
|---|---|---|---|---|---|---|
| Binance Public Data | `https://data.binance.vision/data/spot/monthly/klines/{symbol}/1h/{symbol}-1h-{YYYY-MM}.zip` | Open-Zeit, OHLCV, Schlusszeit, Quote-Volumen, Trade-Anzahl, Taker-Volumen | Pilot: Januar 2024 und Januar 2025; Vollimport: 2021-2025 | Pilot 2026-07-27; Vollimport 2026-08-01 | Offizielles Repository nennt MIT; Attribution und erneute Prüfung vor Veröffentlichung; Rohdaten nicht in Git | `reports/data_pilot/raw_manifest.csv`; `reports/full_import/raw_manifest.csv`; 180/180 offizielle und lokale SHA-256 stimmen |
| Coin Metrics Community API | `https://community-api.coinmetrics.io/v4/timeseries/asset-metrics` | `PriceUSD`, `CapMrktCurUSD`, `TxCnt`, `AdrActCnt` | Pilot: 2023-12-31 bis 2025-01-31; Vollimport: 2020-12-30 bis 2025-12-31 einschließlich | Pilot 2026-07-27; Vollimport 2026-08-01 | laut offizieller Dokumentation schlüsselloser Community-Zugriff für nicht-kommerzielle Nutzung unter Creative-Commons-Bedingungen; Attribution erforderlich | Pilot: `reports/data_pilot/raw_manifest.csv`; Vollimport: `reports/full_import/raw_manifest.csv` und `coinmetrics_quality_summary.json`; lokale SHA-256 der Raw-Seite |

## Externe Quellenprüfung

Alle Seiten wurden am 27. Juli 2026 abgerufen.

| Zweck | Offizielle Quelle |
|---|---|
| Binance-Dateischema, Intervalle, Zeitstempeleinheit, Prüfsummen und Lizenzhinweis | https://github.com/binance/binance-public-data/blob/master/README.md |
| Coin-Metrics-Endpunkt, UTC-Konvention, Paging und Rate Limits | https://docs.coinmetrics.io/api/v4/ |
| Coin-Metrics-Community-Zugriff und Nutzungshinweis | https://docs.coinmetrics.io/api |
| Coinbase-Candle-Schema, mögliche Lücken und 300-Kerzen-Grenze | https://docs.cdp.coinbase.com/api-reference/exchange-api/rest-api/products/get-product-candles |
| Coinbase-Nutzungsbedingungen als Grund für Reserve-Status | https://www.coinbase.com/legal/developer-platform/terms-of-service |
| CoinGecko-Keyless-Limits und Endpunkte | https://docs.coingecko.com/docs/keyless-public-api |
| FRED-API-Schlüssel und Beobachtungsparameter | https://fred.stlouisfed.org/docs/api/fred/series_observations.html |
| FRED-Nutzungsbedingungen und Rechte einzelner Reihen | https://fred.stlouisfed.org/docs/api/terms_of_use.html |

## Auswahl-Gate

Eine Quelle wird erst bestätigt, wenn ein Pilot mindestens folgende Nachweise liefert:

- reproduzierbarer Download
- verständliches Schema
- ausreichende historische Abdeckung
- dokumentierte Qualität
- sinnvoller Join mit den übrigen Quellen
- keine erkennbaren Nutzungs- oder Veröffentlichungsprobleme

**Ergebnis:** Gate 0 ist bestanden. Der technische Nachweis ist
`reports/data_pilot/gate0_decision.json`. Die rechtliche Aussage ist bewusst
begrenzt: Die dokumentierten Bedingungen erscheinen für das interne,
nicht-kommerzielle Ausbildungsprojekt nutzbar; eine öffentliche Weitergabe von
Rohdaten ist nicht vorgesehen und wird vorab erneut geprüft.

**Phase 1B:** Der kontrollierte Vollimport wurde mit
`COMPLETED_WITH_SOURCE_ANOMALIES` abgeschlossen. Alle 180 Binance-Monate und
1.828 Coin-Metrics-Tage wurden geprüft. Das Phase-1B-Teilurteil lautet
`PASS_WITH_DOCUMENTED_SOURCE_ANOMALIES`. Die Zählungs- und Qualitätsevidenz
steht in `reports/full_import/FULL_IMPORT_PLAN.md` und
`reports/full_import/GATE1_ACCEPTANCE_CRITERIA.md`.

**Phase 1C:** Processed-Join, SQL, EDA und Power-BI-Datenvertrag
wurden anschließend unabhängig geprüft. G1-10, G1-12 und G1-13 sind `PASS`.
Damit ist Gate 1 insgesamt als `PASS_WITH_DOCUMENTED_SOURCE_ANOMALIES`
abgeschlossen.
