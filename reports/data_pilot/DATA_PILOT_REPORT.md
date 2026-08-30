# Datenquellen-Pilot und Gate-0-Entscheidung

## Lauf

- Pilot-ID: `gate0_data_sources_2026-07-27`
- Ausgefuehrt (UTC): `2026-07-27T12:11:04+00:00`
- Umfang: 3 Assets, 2 Zeitrahmen, 2 getrennte Monatsstichproben
- Stichproben: 2024-01, 2025-01
- Rohdatenregel: vorhandene Rohdateien werden wiederverwendet und niemals ueberschrieben

## Objektives Ergebnis

**Gate 0: BESTANDEN.**

| Kriterium | Ergebnis |
| --- | --- |
| primaere_marktquelle_reproduzierbar | PASS |
| ergaenzende_quelle_reproduzierbar | PASS |
| zeitlich_ausgerichtet_ohne_zukunftsdaten | PASS |
| zeitrahmen_konsistent | PASS |
| quellenvergleich_dokumentiert | PASS |
| empfohlene_zeitraumgrenzen_erreichbar | PASS |

Binance: 12 von 12 Monatsdateien bestanden alle
Qualitaetsregeln und die offiziellen SHA-256-Pruefsummen. Coin Metrics:
398 von 398 erwarteten
Tageswerten, Qualitaet `PASS`.
Die Binance-Dateien beginnen und enden exakt an den erwarteten UTC-Monatsgrenzen.
Coin Metrics beginnt exakt bei 2023-12-31T00:00:00+00:00 und endet
exakt bei 2025-01-31T00:00:00+00:00; nicht-endliche oder negative
Metrikwerte wurden nicht akzeptiert.
Der zeitlich konservative Join deckt 100.00 %
der 5580 Marktzeilen ab, verliert
0 Zeilen und nutzt in
0 Faellen Zukunftsdaten.

## Kandidatenvergleich

Jedes Kriterium zaehlt 0 oder 1. Der Live-HTTP-Status ist ein technischer
Erreichbarkeitstest, der Score beruecksichtigt zusaetzlich Historie,
Reproduzierbarkeit, Integritaet, Core-Nutzen, Limits und Nutzungsbedingungen.

| source | probe_http_status | score | max_score | fit_pct | decision | main_limit |
| --- | --- | --- | --- | --- | --- | --- |
| Binance Public Data | 200 | 7 | 7 | 100.0 | auswaehlen: primaere Quelle | Exchange-spezifische Sicht; Archivdateien koennen spaeter korrigiert werden. |
| Coin Metrics Community API | 200 | 6 | 7 | 85.7 | auswaehlen: ergaenzende Quelle | Taegliche Werte koennen revidiert werden; Rohsnapshot und lokaler SHA-256 sind deshalb Pflicht. |
| Coinbase Exchange Candles | 200 | 4 | 7 | 57.1 | Reserve, nicht Core | Maximal 300 Kerzen je Anfrage, dokumentierte Datenluecken und restriktivere Bedingungen fuer Speicherung/Weitergabe. |
| FRED | 200 | 3 | 7 | 42.9 | Stretch zurueckstellen | Offizielle API verlangt einen Schluessel; Revisionszeitpunkt und Rechte der einzelnen Reihe muessen separat geprueft werden. |
| CoinGecko Keyless API | 401 | 2 | 7 | 28.6 | fuer Core verwerfen | Keyless-Zugriff ist im Pilot fuer historische Abfragen auf die letzten 365 Tage begrenzt. |

## Assetvergleich O001

`mean_hourly_quote_volume_usdt` und `median_hourly_trades` sind nur
Liquiditaets-Proxys der beiden Pilotmonate, keine Renditekennzahlen.

| symbol | pilot_rows | mean_hourly_quote_volume_usdt | median_hourly_trades | quality_pass_rate_pct | total_missing_intervals | quote_volume_rank |
| --- | --- | --- | --- | --- | --- | --- |
| BTCUSDT | 1488 | 99,280,855 | 82507.5 | 100.0 | 0 | 1 |
| ETHUSDT | 1488 | 53,932,429 | 52052.5 | 100.0 | 0 | 2 |
| SOLUSDT | 1488 | 39,813,915 | 40847.5 | 100.0 | 0 | 3 |

**Empfehlung:** BTCUSDT, ETHUSDT und SOLUSDT gemeinsam verwenden. Alle drei
bestanden die Qualitaetspruefung. BTC dient als Referenzmarkt; ETH und SOL
erzeugen einen sinnvollen Vergleich zwischen etabliertem und juengerem
Kryptomarkt. Der Pilot beweist Liquiditaet nicht fuer jeden Tag des
Zielzeitraums; die Vollpipeline muss deshalb dieselben Regeln je Monat erneut
anwenden.

## Zeitrahmenvergleich O002

| symbol | pilot_month | direct_4h_rows | aggregated_4h_rows | missing_or_extra_rows | value_mismatches | timeframe_consistency_pass |
| --- | --- | --- | --- | --- | --- | --- |
| BTCUSDT | 2024-01 | 186 | 186 | 0 | 0 | True |
| BTCUSDT | 2025-01 | 186 | 186 | 0 | 0 | True |
| ETHUSDT | 2024-01 | 186 | 186 | 0 | 0 | True |
| ETHUSDT | 2025-01 | 186 | 186 | 0 | 0 | True |
| SOLUSDT | 2024-01 | 186 | 186 | 0 | 0 | True |
| SOLUSDT | 2025-01 | 186 | 186 | 0 | 0 | True |

6 von 6 Vergleichen bestanden.

**Empfehlung:** 1h als primaeren Zeitrahmen verwenden und 4h als
Robustheits-Zeitrahmen aus den geprueften 1h-Rohkerzen ableiten. 1h liefert
genuegend Beobachtungen fuer zeitlich getrennte Entwicklung, Validierung und
Test; 4h prueft, ob Resultate auch bei weniger Marktgeraeusch bestehen. Die
Ableitung aus 1h verhindert doppelte Downloadlogik und wurde gegen die
offiziellen 4h-Dateien validiert.

## Zeitraumempfehlung

**2021-01-01T00:00:00Z bis ausschliesslich 2026-01-01T00:00:00Z.**
Damit werden die vollstaendigen Kalenderjahre 2021 bis 2025 verwendet. Dieser
Zeitraum umfasst unterschiedliche Marktphasen, ist fuer SOL gemeinsam
verfuegbar und laesst das unvollstaendige Jahr 2026 zunaechst ausserhalb des
Core-Datensatzes. Die spaetere zeitliche Aufteilung wird erst mit der
Validierungsentscheidung festgelegt; es findet kein zufaelliges Mischen statt.
Bei lueckenloser Abdeckung sind etwa 131,472 primaere 1h-Zeilen
und 32,868 daraus abgeleitete 4h-Zeilen zu erwarten. Das ist
nur eine Planungsschaetzung; die Vollpipeline muss die tatsaechliche Zahl und
alle Luecken berichten.

Die Start- und Endgrenzen wurden ohne Vollimport ueber kleine Metadaten- bzw.
Ein-Tages-Abfragen geprueft:

| source | symbol_or_asset | boundary | probe_date_or_month | http_status | coverage_pass |
| --- | --- | --- | --- | --- | --- |
| Binance Public Data | BTCUSDT | start | 2021-01 | 200 | True |
| Binance Public Data | BTCUSDT | end | 2025-12 | 200 | True |
| Binance Public Data | ETHUSDT | start | 2021-01 | 200 | True |
| Binance Public Data | ETHUSDT | end | 2025-12 | 200 | True |
| Binance Public Data | SOLUSDT | start | 2021-01 | 200 | True |
| Binance Public Data | SOLUSDT | end | 2025-12 | 200 | True |
| Coin Metrics Community API | btc | start | 2020-12-31 | 200 | True |
| Coin Metrics Community API | btc | end | 2025-12-31 | 200 | True |

## Quellenempfehlung O003

- Primaer: **Binance Public Data** fuer historische Spot-OHLCV. Gruende:
  Monatsdateien, feste URLs, offizielle Pruefsummen, ausreichende Felder und
  beide getesteten Zeitrahmen.
- Ergaenzend: **Coin Metrics Community API** fuer taeglichen BTC-Referenzpreis,
  Marktkapitalisierung und Netzwerkaktivitaet. Der Rohsnapshot erhaelt einen
  lokalen SHA-256 und Attribution.
- Reserve: Coinbase nur fuer spaetere Stichprobenkontrollen zwischen Exchanges.
  CoinGecko Keyless ist wegen der bestaetigten 365-Tage-Grenze fuer den
  empfohlenen Zeitraum ungeeignet. FRED bleibt Stretch.

## Zeitstandard und Look-ahead-Schutz

- Alle Zeitpunkte sind UTC.
- `timestamp_utc` ist der Kerzenbeginn; eine Zeile darf erst bei
  `close_time_utc` fuer Signale verwendet werden.
- Ein Coin-Metrics-Tageswert D gilt konservativ erst ab D+1 00:00 UTC als
  verfuegbar.
- D+1 00:00 UTC ist eine konservative methodische Annahme, keine bestaetigte
  historische Publikationsgarantie. Eine spaetere Sensitivitaetspruefung muss
  den strengeren Ansatz D+2 00:00 UTC vergleichen.
- Der as-of-Join verbindet nur Kontext mit
  `available_from_utc <= close_time_utc`.
- Maximales Kontextalter im Pilot: 24.00 Stunden.

## Grenzen

- Zwei Monate sind ein technischer Pilot, keine vollstaendige Marktanalyse.
- Aktivitaets- und Marktkapitalisierungswerte koennen vom Anbieter revidiert
  werden. Deshalb werden Rohsnapshot, Abrufzeit und lokale Pruefsumme
  dokumentiert.
- Nutzungsbedingungen koennen sich aendern. Vor einer oeffentlichen
  Datenweitergabe werden sie erneut geprueft; rohe Anbieterdateien bleiben
  ausserhalb von Git.
- Der Pilot trifft keine Aussage ueber Profitabilitaet und ist keine
  Trading-Empfehlung.

## Einfache Erklaerung fuer die Praesentation

Wir haben noch nicht den grossen Datensatz geladen. Zuerst wurden kleine,
fest definierte Datenpakete getestet. Dabei wurden zwei Kalenderjahre
absichtlich beruehrt, weil Binance ab 2025 eine andere Zeitstempeleinheit
verwendet. Alle Kerzen waren vollstaendig, logisch und per Pruefsumme
unveraendert. Danach wurden 1h-Kerzen zu 4h-Kerzen zusammengebaut und mit den
offiziellen 4h-Kerzen verglichen. Schliesslich wurde Tageskontext erst einen
Tag spaeter verbunden, damit keine Information aus der Zukunft in eine
Entscheidung gelangt. Deshalb kann Gate 0 objektiv entschieden werden.
