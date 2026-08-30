# Phase 1A/1B: Plan, Härtung und Abnahme des historischen Vollimports

## Status

**Phase 1B ist mit dem Teilurteil
`PASS_WITH_DOCUMENTED_SOURCE_ANOMALIES` abgeschlossen. Der einmalig
freigegebene kontrollierte Wiederanlauf endete am 1. August 2026 mit
`COMPLETED_WITH_SOURCE_ANOMALIES`. Das gesamte Gate 1 bleibt wegen G1-10,
G1-12 und G1-13 `NOT_EVALUATED`; Phase 1C wurde noch nicht begonnen.**

Der Lauf migrierte den vollständig validierten Legacy-Checkpoint kontrolliert,
verwendete die vorhandenen Januar-Dateien als `cached_valid`, bestätigte den
anomalen Februar ohne Interimdateien und verarbeitete danach alle übrigen
Assets und Monate sowie Coin Metrics. Raw- und gültige Interimdateien wurden
nicht überschrieben. Ein weiterer Import oder Wiederanlauf ist weder nötig
noch durch diesen Abschluss freigegeben.

## Reales Phase-1B-Ergebnis

| Kennzahl | Ergebnis |
|---|---:|
| Binance-Archive / CHECKSUM-Dateien | 180 / 180 |
| Bestandene Anbieterprüfsummen | 180/180 |
| Raw-Soll / Raw-Ist 1h | 131.472 / 131.430 |
| Raw-Delta | -42 |
| Akzeptierte 1h-Zeilen | 116.208 |
| Akzeptierte 4h-Zeilen | 29.052 |
| Gültige / ausgeschlossene Asset-Monate | 159 / 21 |
| Anomaliezeilen / Intervalle | 96 / 24 |
| Coin-Metrics-Tage | 1.828/1.828 |
| Akzeptierte / ausgeschlossene Abdeckung | 88,39 % / 11,61 % |

Sieben Kalendermonate sind bei allen drei Assets betroffen: `2021-02`,
`2021-03`, `2021-04`, `2021-08`, `2021-09`, `2021-12` und `2023-03`.
Die Raw-Quellenlücke beträgt global 42 Stunden. Die 11,61 % ausgeschlossene
Abdeckung entsteht nicht durch 15.264 fehlende Raw-Stunden, sondern durch den
konservativen vollständigen Ausschluss jedes betroffenen Asset-Monats.

## Verbindlicher Umfang

### Binance Public Data

- Markt: Spot
- Assets: `BTCUSDT`, `ETHUSDT`, `SOLUSDT`
- Rohintervall: ausschließlich `1h`
- Zeitraum: `2021-01-01T00:00:00Z` bis ausschließlich
  `2026-01-01T00:00:00Z`
- Quelle: monatliche Binance-Archive plus jeweilige `.CHECKSUM`-Datei
- Robustheitsintervall: `4h`, nur aus vollständigen Gruppen von vier
  aufeinanderfolgenden 1h-Kerzen abgeleitet

Fünf vollständige Jahre enthalten 60 Kalendermonate je Asset. Bei drei Assets
sind das:

```text
3 Assets × 60 Monate = 180 Monatsarchive
180 Archive + 180 CHECKSUM-Dateien = 360 HTTP-Objekte
```

Die erwartete 1h-Zeilenanzahl berücksichtigt das Schaltjahr 2024:

```text
2021 bis 2025 = 1.826 Kalendertage
1.826 Tage × 24 Stunden = 43.824 Zeilen je Asset
43.824 × 3 Assets = 131.472 Zeilen insgesamt
```

Da jeder UTC-Tag genau sechs vollständige 4h-Gruppen enthält:

```text
43.824 / 4 = 10.956 4h-Zeilen je Asset
10.956 × 3 Assets = 32.868 4h-Zeilen insgesamt
```

4h wird nicht nochmals heruntergeladen. So gibt es nur eine primäre
Marktdatenbasis, weniger doppelte Downloadlogik und eine klar prüfbare
Ableitung. Unvollständige oder zeitlich unregelmäßige Vierergruppen erzeugen
keine künstliche 4h-Kerze.

### Coin Metrics Community API

- Asset: `btc`
- Frequenz: `1d`
- Zeitraum einschließlich beider Grenzen: `2020-12-30` bis `2025-12-31`
- Erwartete Tageszeilen: `1.828`
- Felder: `PriceUSD`, `CapMrktCurUSD`, `TxCnt`, `AdrActCnt`
- Paging: alle Antwortseiten werden einzeln und unverändert gespeichert

Die zwei zusätzlichen Tage 30. und 31. Dezember 2020 sind technisch nötig,
damit am Marktstart 1. Januar 2021 sowohl die primäre D+1-Annahme als auch die
spätere strengere D+2-Sensitivität geprüft werden können.

D+1 00:00 UTC ist eine konservative methodische Annahme, keine bestätigte
historische Veröffentlichungsgarantie. Die spätere Auswertung muss dieselben
Ergebnisse zusätzlich mit D+2 00:00 UTC vergleichen. Phase 1A führt noch
keinen vollständigen Join und keine Analyse aus.

## Schutz der Rohdaten

Rohdateien werden unverändert aufbewahrt. Für jedes Binance-Archiv wird die
offizielle SHA-256-Prüfsumme geladen und vor der Nutzung mit dem lokalen
SHA-256 verglichen. Eine Abweichung ist ein harter Fehler. Eine bereits
vorhandene oder beschädigte Rohdatei wird weder gelöscht noch überschrieben.
SHA-256 ist dabei wie ein digitaler Fingerabdruck: Schon eine kleine
Dateiänderung führt zu einem anderen Prüfwert.

Neue Dateien werden zuerst als temporäre Datei im Zielordner geschrieben und
danach mit einer atomaren No-Overwrite-Operation freigegeben. Für Wiederanläufe
unterscheidet die Pipeline fehlende, teilweise vorhandene und vollständig
geprüfte Cache-Zustände. Coin-Metrics-Seiten erhalten einen lokalen SHA-256;
ihre JSON-Antworten bleiben unverändert.

Auch deterministisch erzeugte Interimdateien werden nicht überschrieben. Fehlt
eine Datei, wird sie atomar erstellt und erhält den Status `created`. Ist
bereits exakt derselbe Inhalt vorhanden, wird ihr SHA-256 geprüft und sie mit
`cached_valid` wiederverwendet. Abweichender Inhalt ist ein harter Fehler und
bleibt unverändert. Das gilt getrennt für monatliche 1h- und 4h-Dateien sowie
für den Coin-Metrics-Interimkontext. Dadurch funktionieren auch teilweise
fertige Wiederanläufe sicher.

Ein Binance-Archiv muss exakt zwölf Spalten besitzen. Die verbindliche
Richtlinie `binance_spot_ms_before_2025_us_from_2025` verlangt bis
einschließlich 2024-12 Millisekunden und ab 2025-01 Mikrosekunden. Die
Erwartung kommt aus dem Monatsauftrag; Open- und Close-Time werden einzeln
klassifiziert. Gemischte, unterschiedliche, uneindeutige oder nicht
unterstützte Einheiten sind harte Integritätsfehler. Erst danach erfolgt die
UTC-Konvertierung. Der Kerzenschluss ist
`open + 1 Stunde - 1 Millisekunde` beziehungsweise
`open + 1 Stunde - 1 Mikrosekunde`. Zusätzlich werden Anfang, letzter
Kerzenbeginn, Schlusszeit, volle Stunden, Kerzendauer, Reihenfolge, Duplikate
und Abstände geprüft. Neben OHLC und Basisvolumen gehören auch Quote-Volumen,
Trade-Anzahl und beide Taker-Buy-Volumina zur Qualitätsentscheidung. Coin
Metrics muss in jeder Zeile exakt das konfigurierte Asset `btc` enthalten.

Der Parser führt intern weiterhin alle 20 Spalten. Vor der
1h-Interimserialisierung wird jedoch zentral auf den unveränderlichen Vertrag
`BINANCE_INTERIM_1H_SCHEMA_ID = "binance_1h_market_v1"` mit exakt diesen
Feldern projiziert:

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

Die fünf internen Nachweise `timestamp_policy_id`,
`expected_timestamp_unit`, `observed_open_timestamp_unit`,
`observed_close_timestamp_unit` und `timestamp_unit_errors` bleiben in der
Monatsqualität und im Checkpoint. Sie gehören nicht redundant in jede
Marktzeile. Januar 2021 und alle zukünftigen Monate verwenden damit denselben
15-Spalten-Vertrag. Ein bereits vorhandenes identisches 1h- oder 4h-Ergebnis
wird mit Hash- und Zeitvergleich als `cached_valid` wiederverwendet. Eine
20-spaltige oder sonst abweichende vorhandene Interimdatei wird weder
überschrieben noch automatisch migriert.

Das spätere Ausführungsmanifest enthält mindestens Quelle, Objekttyp,
Asset/Periode, URL, lokalen Rohpfad, Abrufzeit, Dateigröße, lokalen SHA-256,
Anbieter-Prüfsumme, Prüfergebnis und Cache-Status.

## Integrität und Quellenkontinuität getrennt behandeln

Ein harter Integritäts- oder Wertefehler beendet den Lauf mit
`HARD_FAILURE`. Dazu gehören insbesondere:

- falsche Anbieter-Prüfsumme oder beschädigtes ZIP,
- unerwartetes Schema, unsicherer Pfad oder Host,
- unzulässige beziehungsweise gemischte Zeitstempeleinheit,
- nicht-endliche oder negative Werte, unlogische OHLC-Beziehungen,
  Duplikate oder andere nicht als Quellenunterbrechung erklärbare Fehler,
- Versuch, vorhandene Raw- oder abweichende Interimdateien zu überschreiben.

Unzulässige, uneindeutige oder gemischte Zeitstempeleinheiten gehören zur
Quellen-/Schema-Integrität. Wird dafür ein Qualitätsobjekt erzeugt, gilt
`source_integrity_pass = false`, `quality_pass = false` und
`processing_status = source_integrity_failure`. Der Fehler darf weder als
Kontinuitätsabweichung noch als gewöhnliche Wertequarantäne weiterlaufen.

Eine checksum-valide Datei kann dagegen eine echte
Quellenkontinuitätsabweichung enthalten, zum Beispiel eine fehlende Stunde,
einen Zwei-Stunden-Abstand oder eine verkürzte Kerzenschlusszeit. Dann gilt:

```text
source_integrity_pass = true
continuity_pass = false
quality_pass = false
processing_status = source_continuity_anomaly
```

Der konkrete Monat wird vollständig im Qualitäts- und Anomaliebericht
protokolliert. Für ihn werden weder 1h- noch 4h-Interimdaten geschrieben. Es
gibt keine Interpolation, Vorwärtsfüllung, Ersatzkerze einer anderen Börse oder
sonstige synthetische Reparatur. Der nächste Monatsauftrag darf weiterlaufen,
damit ein einzelner dokumentierter Handelsunterbruch nicht den Nachweis aller
übrigen Monate verhindert.

Für 4h darf eine Gruppe nur entstehen, wenn alle vier 1h-Zeilen vorhanden,
exakt stündlich ausgerichtet, vollständig geschlossen und in allen
Werteprüfungen gültig sind. Eine verkürzte Kerze oder eine fehlende Stunde
schließt den betroffenen 4h-Block aus.

## Checkpoints und Teilberichte

`execution_checkpoint.json` ist die einzige autoritative Zustandsquelle. Ein
gültiger Checkpoint enthält mindestens:

- `checkpoint_schema_version = 4`, `scope_id`, `config_fingerprint`,
  `timestamp_policy_id`, `anomaly_evidence_policy_id =
  source_anomalies_all_cached_pairs_v1`,
  `binance_interim_1h_schema_id = binance_1h_market_v1` und
  `processing_policy_fingerprint`,
- `run_id`, `generation_id`, `execution_status` und
  `last_safe_completed_task`,
- vollständige Rohmanifest-, Binance-Monatsqualitäts-, Anomalie-,
  Interimstatus- und Coin-Metrics-Seitenevidenz,
- Coin-Metrics-Gesamtqualität, sobald sie für dieselbe Generation vorliegt,
- Gesamtzählungen und dieselben fachlichen Zählungen je Asset,
- letzten Fehler mit Typ, Meldung und betroffenem Auftrag,
- SHA-256 aller vier materialisierten Berichtsprojektionen.

Vor einer Wiederaufnahme werden Schemaversion, Scope, Konfigurations-,
Zeitstempel- und Anomalieübernahmerichtlinie sowie der vollständige
Verarbeitungsrichtlinien-Fingerprint verglichen. Die neue Policy mit dem
15-Spalten-Vertrag hat den Fingerprint
`ab2b62be100a23ca06fd0337ca56b6d33ce290531dd976561de90d99abb551da`.
Jeder unbekannte oder abweichende Checkpoint stoppt vor jeder
Berichtsmutation. Ein gültiger vorhandener Checkpoint wird nie durch einen
leeren Startzustand ersetzt.

Es gibt genau eine eng begrenzte Legacy-Ausnahme. Sie akzeptiert
ausschließlich den alten Fingerprint
`9e75207e0b5a5655366c9513a253adf2325d0f126622774ca2974c0de4533e46`
mit Checkpoint-Schema 4, Generation 2, `HARD_FAILURE`, Fehlerauftrag
`binance BTCUSDT 2021-01` und leerem
`last_safe_completed_task`. Zusätzlich müssen Scope, Konfiguration,
Zeitstempel- und Anomaliepolicy, Fehlernachweis, sämtliche Strukturfelder und
Zählungen, die vier gespeicherten und tatsächlichen Projektionshashes, genau
das Januar-/Februar-Rawset aus vier Dateien, genau die beiden
Januar-Interimdateien, Januar-CHECKSUM, ZIP, Monatsqualität, bytegenaue
15-Spalten-Projektion, unveränderte 4h-Ableitung sowie alle vier aus
Februar-Raw und CHECKSUM neu berechneten Anomaliezeilen stimmen. Es dürfen
keine Monats-, Partial-Interim- oder Coin-Metrics-Fortschritte vorliegen.

Diese Übernahme bleibt read-only und ausschließlich in-memory. Beschädigte
Legacy-Projektionen werden nicht durch Recovery repariert. Erst ein später
separat genehmigtes normales Checkpointschreiben darf die neue Policy
persistieren. Das Feld `policy_migration` bindet dann deterministisch alten
und neuen Fingerprint, Quellschema, Quellgeneration und
`binance_1h_market_v1`. Run-ID, Änderungszeiten, Windows-Benutzername und
absolute lokale Pfade werden nicht hartcodiert.

Der vollständige neue Checkpoint wird zuerst als einzelne Datei atomar
geschrieben. Danach werden daraus nacheinander materialisiert:

- `raw_manifest.csv`,
- `binance_quality_summary.csv`,
- `source_anomalies.csv`,
- `coinmetrics_quality_summary.json`.

Jede Einzeldatei wird über eine temporäre Datei im selben Zielverzeichnis,
`fsync` und atomaren Austausch vollständig ersetzt. Die Gruppe ist
ausdrücklich **nicht transaktional über mehrere Dateien**. Ein Abbruch kann
daher kurzzeitig alte und neue Projektionen mischen. Maßgeblich bleibt die
Generationskennung mit den erwarteten Projektionshashes im Checkpoint. Beim
Wiederanlauf werden fehlende oder abweichende Projektionen erkannt und aus
dem autoritativen Zustand wiederhergestellt.

Eine vollständig gespeicherte und als JSON validierte Coin-Metrics-Seite wird
unmittelbar mit Seitenschlüssel, Seitennummer, lokalem Objekt, SHA-256,
Zeilenzahl und Cache-Status gesichert. Öffentliche kanonische
Binance-Objekt- und CHECKSUM-URLs bleiben im Manifest und Checkpoint als
Herkunftsnachweis zulässig. Coin-Metrics-Paging-/Cursor-Queryparameter,
vollständige Paging-URLs und Antworttexte aus HTTP-Fehlern werden dagegen
nicht in Ausführungsberichte oder Checkpoint übernommen; dasselbe gilt für
Zugangsdaten und sensible Queryparameter. Öffentliche Initialparameter im
offline erzeugten Downloadplan bleiben davon unberührt. Neue lokale
Dateifehler verwenden projektrelative Pfade.
Ein Fehler auf Seite N behält damit die Evidenz der Seiten 1 bis N-1.

Schlägt das 4h-Schreiben nach einer sicheren 1h-Datei fehl, enthält der
Checkpoint Partial-Evidenz mit `interim_1h_status = created` und
`interim_4h_status = write_failed`. Der Monat gilt noch nicht als sicher
abgeschlossen. Beim Wiederanlauf muss die 1h-Datei bytegenau dem
deterministischen Sollinhalt entsprechen; nur dann erhält sie `cached_valid`
und die fehlende 4h-Datei darf entstehen. Abweichender Cacheinhalt bleibt
unverändert und stoppt hart.

Die vor dem ersten realen Checkpoint bereits verifizierte
`source_anomalies.csv` wird fail-closed neu belegt. Die CSV darf den
Prüfumfang nicht selbst bestimmen: Aus allen geplanten Binance-Aufträgen werden
unter den ausschließlich aus der sicheren Konfiguration gebildeten Pfaden alle
vollständigen Raw-/CHECKSUM-Paare ermittelt. Für jedes vollständige Paar werden
CHECKSUM-Archivname und SHA-256, Archiv-SHA, ZIP, Schema, Zeitstempeleinheit und
Monatsqualität offline neu geprüft. Erst daraus entsteht die vollständige
kanonische Anomaliemenge aller bereits vollständig gecachten Monate.

Jede physische CSV-Zeile muss genau die deklarierten
`SOURCE_ANOMALY_FIELDS` besitzen. Zusätzliche unbenannte Werte, zu wenige
Spalten, fehlende Werte oder andere Schlüssel werden abgelehnt. Danach muss die
gesamte CSV feldgenau der neu berechneten Gesamtmenge entsprechen.
Header-only-Dateien trotz belegter Anomalien sowie fehlende Asset-Monat-Gruppen,
einzelne fehlende, zusätzliche, doppelte oder veränderte Zeilen stoppen ohne
Checkpoint- oder Projektionsänderung. Die Eingabereihenfolge darf abweichen und
wird für den Vergleich sicher normalisiert; ausgegeben wird kanonisch sortiert.
Fehlt die CSV, werden belegte Anomalien vollständig mit der Provenienz
`recomputed_from_cached_raw` rekonstruiert. `validated_preexisting_csv` darf
nur nach einem vollständigen Gesamtvergleich vergeben werden. Die lokale
CHECKSUM ist kein kryptografischer Identitätsbeweis des Anbieters, sondern ein
Fortbestandsnachweis zum dokumentierten ursprünglichen Download.

Coin Metrics führt die Phasen `coinmetrics_page_fetch`,
`coinmetrics_page_parse`, `coinmetrics_page_persist`,
`coinmetrics_aggregate_quality`, `coinmetrics_interim_write` und
`coinmetrics_completed`. `pages_attempted` und `pages_completed` werden
getrennt protokolliert. Ein Fehler der zusammengeführten Qualität nach Seite 1
heißt daher `coinmetrics aggregate_quality` und niemals Phantomseite 2.

Fault-Injection deckt den Abbruch vor jeder der vier Projektionen ab. Für alle
vier Fälle bleibt die Checkpointgeneration autoritativ und der Wiederanlauf
rekonstruiert exakt die gespeicherten Hashes ohne Daten- oder Evidenzverlust.

## Zählungsvertrag

| Feld | Eindeutige Bedeutung |
|---|---|
| `scope_expected_1h_rows` | Kalender-Soll des vollständigen Scopes; immer 131.472 |
| `completed_months_expected_1h_rows` | 1h-Soll nur der bereits qualitätsgeprüften Monate |
| `observed_raw_1h_rows` | tatsächlich gelesene Raw-Zeilen einschließlich Kontinuitätsmonaten |
| `accepted_interim_1h_rows` | Zeilen aus `created` oder vollständig validiertem `cached_valid`; eine bereits sichere 1h-Partialausgabe zählt, obwohl ihr Monatsauftrag bis zur 4h-Fortsetzung unvollständig bleibt |
| `skipped_anomalous_raw_1h_rows` | vorhandene Raw-Zeilen vollständig übersprungener Kontinuitätsmonate |
| `raw_1h_row_delta` | beobachtete Raw-Zeilen minus Soll der geprüften Monate |
| `accepted_1h_row_delta` | akzeptierte 1h-Zeilen minus Soll der geprüften Monate |
| `scope_expected_4h_rows` | Kalender-Soll des vollständigen Scopes; immer 32.868 |
| `completed_months_expected_4h_rows` | 4h-Soll nur der bereits qualitätsgeprüften Monate |
| `accepted_interim_4h_rows` | tatsächlich erzeugte oder vollständig validierte 4h-Zeilen |
| `accepted_4h_row_delta` | akzeptierte 4h-Zeilen minus Soll der geprüften Monate |
| `source_anomaly_rows` | einzelne gemessene Befundzeilen |
| `continuity_anomaly_months` | betroffene Asset-Monate |
| `continuity_anomaly_intervals` | deterministisch verbundene Zeitbereiche ohne Ursachenbehauptung |
| `interim_created`, `interim_cached_valid`, `interim_skipped`, `interim_quarantined` | Anzahl einzelner 1h-/4h-Interimaktionen |

Für Januar plus den anomalen Februar lautet der Offline-Regressionsnachweis:
1.416 Soll-1h-Zeilen der geprüften Monate, 1.415 beobachtete Raw-Zeilen,
744 akzeptierte 1h-Zeilen, 671 übersprungene Raw-Zeilen, 354 Soll-4h-Zeilen
und 186 akzeptierte 4h-Zeilen. Vier Befundzeilen entsprechen einem
Asset-Monat und einem zusammenhängenden gemessenen Zeitintervall.
Alle aufgeführten additiven Felder werden zusätzlich je `BTCUSDT`, `ETHUSDT`
und `SOLUSDT` geprüft; ihre Summe muss dem globalen Wert entsprechen. Eine
separate Multi-Asset-Fixture besitzt auch für ETHUSDT und SOLUSDT
nichtnullige akzeptierte 1h- und 4h-Werte.

Der reale Abschlussnachweis ersetzt diese Teilfixture durch den vollständigen
Scope: 131.472 erwartete und 131.430 beobachtete Raw-1h-Zeilen, 116.208
akzeptierte 1h-Zeilen, 29.052 akzeptierte 4h-Zeilen, 15.222 vorhandene aber
wegen Monatskontinuität übersprungene Raw-Zeilen, 96 Befundzeilen, 21
Asset-Monate und 24 Intervalle. Diese Werte gelten global und sind im
Checkpoint zusätzlich je Asset additiv belegt.

## Verbindlicher Übergabevertrag für Phase 1C

Phase 1C darf die ausgeschlossenen Monate nicht wie gewöhnliche, durchgehende
Zeitabschnitte behandeln:

- Über eine ausgeschlossene Monatsgrenze hinweg dürfen keine Renditen,
  Indikatoren, Signale oder Positionen berechnet oder fortgeführt werden.
- Rollende Zeitreihenzustände werden nach jeder Lücke zurückgesetzt. Eine
  Kennzahl wird erst wieder freigegeben, wenn ihre vollständige historische
  Mindestlänge nach der Lücke neu aufgebaut ist.
- Für BTCUSDT, ETHUSDT und SOLUSDT wird eine gemeinsame Verfügbarkeitsmaske
  verwendet. So vergleichen spätere Querschnittsauswertungen stets dieselben
  zulässigen Analysezeitpunkte.
- Jede Analyse berichtet 88,39 % akzeptierte zeitliche Abdeckung und 11,61 %
  konservativ ausgeschlossene Abdeckung.
- Die reale Raw-Quellenlücke von 42 Stunden wird getrennt vom vollständigen
  Ausschluss von 21 Asset-Monaten beziehungsweise 15.264 Kalenderstunden
  dargestellt.

Die gemeinsame Maske, Resetlogik, Join-Verluste und zugehörigen Negativtests
werden erst in Phase 1C implementiert. Sie sind kein bereits bestandener
Phase-1B-Nachweis.

## Kanonische Statuswerte

| Ebene/Feld | Kanonische Werte |
|---|---|
| Ausführung `execution_status` | `IN_PROGRESS`, `COMPLETED`, `COMPLETED_WITH_SOURCE_ANOMALIES`, `HARD_FAILURE` |
| Monatsverarbeitung `processing_status` | `valid`, `source_continuity_anomaly`, `quality_quarantine`, `source_integrity_failure` |
| 1h-/4h-Interimstatus | `created`, `cached_valid`, `skipped_source_continuity_anomaly`, `quarantined_value_quality`, `rejected_source_integrity`, `write_failed` |
| Binance-Cacheprüfung `status` | `missing_planned_download`, `missing_checksum`, `missing_archive`, `cached_valid` |
| Binance-Rohmanifest `cache_status` | `downloaded_or_resumed`, `cached_valid` |
| Coin-Metrics-Seite `cache_status` | `downloaded`, `cached_existing` |
| Coin-Metrics-Projektion `projection_status` | `available`, `not_available_for_generation` |
| Coin-Metrics-Interimstatus | `created`, `cached_valid` |
| Anomalie-Provenienz `mode` | `none`, `validated_preexisting_csv`, `recomputed_from_cached_raw` |
| Gate `gate_1` | `NOT_EVALUATED` |

`quality_quarantine` und `quarantined_value_quality` sind damit bewusst
verschiedene Felder: Der erste Wert klassifiziert den Monat, der zweite die
nicht erzeugte Interimdatei. Gate 1 wird durch keinen Ausführungsstatus
automatisch bestanden.

## Dry-Run und Ausführungssperre

Der sichere Standard ist ein Dry-Run:

```powershell
python -m src.full_import --config config/full_import.json --dry-run
```

Dieser Befehl:

- erzeugt keine HTTP-Sitzung und keine Netzwerkanfrage,
- erstellt keine Raw-, Interim- oder Processed-Datei,
- plant alle URLs und lokalen Pfade in stabiler Reihenfolge,
- berechnet aus den echten Aufgaben und Planzeilen Zählungen, Duplikate,
  direkte 4h-Bezüge und Pfadsicherheit,
- schreibt nur `download_plan.csv` und `dry_run_summary.json` unter
  `reports/full_import`,
- verändert weder `raw_manifest.csv`, `binance_quality_summary.csv`,
  `source_anomalies.csv`, `execution_checkpoint.json` noch
  `coinmetrics_quality_summary.json`,
- bewertet Gate 1 nicht.

Die beiden Planungsberichte sind vorgesehene Dry-Run-Ausgaben und werden
jeweils atomar aktualisiert. Sie sind keine Ausführungsnachweise. Ein
Sentinel-Test bestätigt die Byteidentität aller fünf Ausführungsberichte vor
und nach dem Dry-Run.

Schlägt eine zentrale Planprüfung fehl, bricht der Dry-Run vor der
Berichtserstellung ab und meldet kein irreführendes „OK“.

Eine echte oder fortgesetzte Ausführung ist nur mit beiden Schaltern möglich:

```powershell
python -m src.full_import --config config/full_import.json --execute --confirm-scope FULL_IMPORT_2021_2025
```

Dieser Ausführungsbefehl wurde zuletzt am 1. August 2026 genau einmal separat
freigegeben und endete mit `COMPLETED_WITH_SOURCE_ANOMALIES`. Er verarbeitete
180 Binance-Monatsaufträge und 1.828 Coin-Metrics-Tage. Ein erneuter Lauf ist
nicht Teil der Offline-Abnahme und benötigt wieder einen ausdrücklichen
Auftrag. Eine fehlende oder falsche Bestätigung bricht ab, bevor eine
Netzwerksitzung entstehen kann. Dieselbe Prüfung steht zusätzlich als erste
Aktion direkt in der Ausführungsfunktion; sie kann nicht durch einen direkten
Funktionsaufruf umgangen werden.

## Einfache Erklärung für die Präsentation

Der Vollimport ist wie ein vollständig geprüftes Fahrtenbuch: Alle geplanten
Monate wurden besucht, aber echte Straßensperren wurden nicht mit erfundenen
Strecken übermalt. Stattdessen sind 21 Asset-Monate sichtbar ausgeschlossen.
Der Checkpoint enthält alle bestätigten Nachweise und die Fingerabdrücke der
vier lesbaren Teilberichte. Phase 1B ist damit bestanden, aber mit
dokumentierten Quellenanomalien. Das gesamte Gate 1 bleibt offen, bis Phase 1C
auch Join, Analysetabelle, SQL, EDA und Power-BI-Vertrag geprüft hat.

Der 15-Spalten-Vertrag ist dabei wie ein festes Tabellenformular: Die
Marktdaten behalten immer dieselben Spalten. Die fünf zusätzlichen
Zeitstempelprüfungen werden im Prüfprotokoll aufbewahrt, statt jede einzelne
Marktzeile unnötig zu verbreitern.
