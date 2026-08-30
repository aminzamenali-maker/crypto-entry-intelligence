# Phase 2A – Methodenplan und Vorregistrierung

## Status und Grenze

**Phase 2A: METHOD_PREREGISTERED, noch nicht unabhängig abgenommen.**
**Gate 2: `NOT_EVALUATED`.**

Dieser Schritt legt die Methode fest, bevor Ergebnisse bekannt sind. Es wurden
keine Features, Signale, Positionen, Trades, Backtests oder ML-Modelle
berechnet. Der Offline-Prüfbefehl liest nur Konfiguration, Dateihashes,
CSV-Schemata und Zeilenzahlen.

## Hauptfrage

> Liefern vorab definierte, ausschließlich aus zum Entscheidungszeitpunkt
> verfügbaren Informationen gebildete Einstiegssignale nach realistischen
> Transaktionskosten einen messbaren Informationswert gegenüber einfachen
> Baselines?

Die spätere Antwort wird in vier Ebenen getrennt:

1. **Signalqualität:** Tritt nach einem Signal häufiger oder stärker ein
   günstiger Ausgang ein als bei einer fairen Baseline?
2. **Handelsperformance:** Wie sehen Brutto- und Nettoergebnisse nach Kosten,
   Tradeanzahl und Exposition aus?
3. **Statistische Unsicherheit:** Wie stabil sind Kennzahlen über Assets,
   Zeitrahmen, Segmente und Zeitabschnitte? Einzelwerte sind kein Beweis.
4. **Praktische Nutzbarkeit:** Bleibt ein möglicher Informationswert nach
   Kosten, Lückenregeln und realistischen Ausführungspreisen relevant?

Es gibt keine Aussage über sichere Gewinne, garantierte Einstiege oder eine
„beste“ Strategie.

## Markt- und Positionsvertrag

- Binance-Spotdaten; `BTCUSDT`, `ETHUSDT`, `SOLUSDT`.
- Long/Flat, kein Short, kein Hebel, kein Funding.
- Höchstens eine Position je Asset gleichzeitig.
- Jedes Asset wird getrennt ausgewertet; zusätzlich folgt später eine
  transparente Aggregation.
- `1h` ist der primäre Zeitrahmen. `4h` ist eine getrennte
  Robustheitsprüfung. Ausführungspreise beider Zeitrahmen werden nie gemischt.

## Informations- und Ausführungszeitpunkt

Ein Signal für Kerze `t` darf erst am `decision_time_utc` nach dem vollständigen
Kerzenschluss entstehen. Der früheste Einstieg ist das handelbare Open der
nächsten vollständigen Kerze `t+1`. Eine Ausführung am Schlusskurs derselben
Signalkerze ist verboten. Heikin-Ashi kann gemäß D009 später höchstens ein
Signalmerkmal sein, niemals ein Ausführungspreis; im vorregistrierten Core ist
Heikin-Ashi nicht enthalten.

Die primäre Haltedauer entspricht ungefähr vier Stunden:

| Zeitrahmen | Vollständige Haltekerzen | Geplanter Exit |
|---|---:|---|
| 1h | 4 | Open nach vier vollständigen Folgekerzen |
| 4h | 1 | Open nach einer vollständigen Folgekerze |

Vorregistrierte Sensitivitäten sind 12 und 24 Stunden: bei 1h 12/24 Kerzen,
bei 4h 3/6 Kerzen. Eine neue Position ist nur erlaubt, wenn Einstieg,
vollständige Haltedauer und Exit im selben Segment liegen. So entstehen im
normalen Core keine verkürzten Grenztrades. Die zusätzliche Sicherheitsregel
setzt eine unerwartet noch offene Position spätestens am letzten verfügbaren
Open des Segments auf Flat.

## Segment- und Lückenvertrag

Alle Zeitreihenzustände werden getrennt nach
`(symbol, timeframe, segment_id)` geführt. Es gilt:

- vollständige Mindesthistorie (`min_periods`) für jedes Rolling-Feature,
- Reset nach jeder Segmentlücke,
- kein Feature und kein Signal über eine Segmentgrenze,
- kein Signal während der Aufwärmphase,
- keine Position über eine Segmentgrenze,
- kein Trade darf einen ausgeschlossenen Monat berühren,
- gemeinsame Verfügbarkeitsmaske der drei Assets.

Ausgeschlossen bleiben `2021-02`, `2021-03`, `2021-04`, `2021-08`,
`2021-09`, `2021-12` und `2023-03`. Es werden keine Kerzen erfunden oder
interpoliert.

## Kostenvertrag

Die Werte sind konservative methodische Szenarien, keine Behauptung über ein
bestimmtes historisches Konto.

| Szenario | Entry Fee | Exit Fee | Entry Slippage | Exit Slippage | Round Trip |
|---|---:|---:|---:|---:|---:|
| `low_20bps` | 5 bp | 5 bp | 5 bp | 5 bp | 20 bp |
| `base_30bps` (primär) | 10 bp | 10 bp | 5 bp | 5 bp | 30 bp |
| `high_50bps` | 15 bp | 15 bp | 10 bp | 10 bp | 50 bp |

Brutto- und Nettoergebnisse werden getrennt berichtet. Die Nettoformel belastet
Entry und Exit jeweils mit Slippage und Gebühr. Eine kostenfreie
Hauptauswertung ist ausgeschlossen.

## Baselines

1. `always_flat`: keine Position, keine Trades, null Exposition und null
   Handelsrendite.
2. `segment_buy_and_hold`: Einstieg am ersten verfügbaren Open und Ausstieg am
   letzten verfügbaren Open jedes Segments; keine Verbindung über Lücken.
3. `periodic_entry_baseline`: erster sicherer Einstieg jeder UTC-ISO-Woche,
   ohne Marktindikator, mit gleicher Haltedauer und gleichen Kosten wie die
   Signalvarianten.

Keine Baseline nutzt Zufall. Damit gibt es weder einen nachträglich gewählten
Seed noch die Möglichkeit, nach Kenntnis der Ergebnisse eine absichtlich
schwache Vergleichsstrategie auszuwählen.

## Vorregistrierte Signalvarianten

| Familie | Variante | Feste Regel |
|---|---|---|
| Trend/Momentum | `trend_sma20_cross_above_sma50` | SMA20/SMA50 kreuzt nach oben; vorheriges Verhältnis war höchstens 1 |
| Trend/Momentum | `momentum_return_12_positive` | ausschließlich rückwärts gerichtete 12-Kerzen-Rendite größer 0 |
| Breakout | `breakout_close_above_prior_high_20` | Close über dem um eine Kerze verschobenen Hoch der 20 vorherigen Kerzen |
| Mean Reversion | `mean_reversion_rsi14_below_30` | Wilder-RSI(14) kleiner 30 |
| Mean Reversion | `mean_reversion_close_2pct_below_sma20` | Close mindestens 2 % unter SMA20 |

Pro Familie existieren höchstens zwei Varianten. Es gibt keine umfangreiche
Parametersuche. Varianten und Schwellen dürfen nur anhand Entwicklung und
Validierung beurteilt werden, niemals anhand des finalen Tests.

## Ziele und spätere Bewertungsgrößen

Zukunftswerte sind ausschließlich Ziele oder Bewertungsgrößen, niemals
Features:

- Bruttorendite vom Entry-Open bis zum geplanten Exit-Open,
- Nettorendite nach vollständigem Kostenszenario,
- positives Nettoergebnis als Auswertung und mögliche spätere ML-Zielgröße,
- Maximum Adverse Excursion (MAE),
- Maximum Favorable Excursion (MFE),
- Tradeanzahl und Expositionsstunden.

Bei MAE und MFE ist die Reihenfolge von High und Low innerhalb einer OHLC-Kerze
unbekannt. Der Core verwendet deshalb keine intrabar Stop-Loss- oder
Take-Profit-Logik.

## Zeitliche Aufteilung und reale Zeilenzahlen

Die Grenzen sind halb-offen: Start inklusive, Ende exklusiv. Ausgeschlossene
Monate bleiben ausgeschlossen.

| Split | Zeitraum | 1h | 4h | Gesamt | Parameterauswahl |
|---|---|---:|---:|---:|---|
| Entwicklung | 2021-01-01 bis vor 2023-01-01 | 39.528 | 9.882 | 49.410 | ja |
| Validierung | 2023-01-01 bis vor 2024-01-01 | 24.048 | 6.012 | 30.060 | ja |
| Finaler Test | 2024-01-01 bis vor 2026-01-01 | 52.632 | 13.158 | 65.790 | nein |
| **Gesamt** | 2021–2025 | **116.208** | **29.052** | **145.260** | – |

Je Asset sind die Werte innerhalb eines Zeitrahmens identisch: Entwicklung
13.176/3.294, Validierung 8.016/2.004 und finaler Test 17.544/4.386 für
1h/4h. Der finale Test wird erst nach Methodenfreigabe genau einmal geöffnet.

## D+1- und D+2-Vertrag

- `primary_d1`: neuer rückwärtsgerichteter As-of-Join mit
  `available_from_utc_d1 <= decision_time_utc`.
- `sensitivity_d2`: getrennte Neuberechnung mit
  `available_from_utc_d2 <= decision_time_utc`.

D+2 wird nicht durch Verschieben der bereits verbundenen D+1-Werte simuliert.
Die 1.828 Zeilen der unveränderten Coin-Metrics-Interimtabelle enthalten schon
den 30. Dezember 2020 und decken damit D+2 ab dem Marktstart ab. Beide
Varianten erhalten identische Signal-, Kosten- und Ausführungslogik.

## Geplanter Auswertungsumfang, noch keine Ergebnisse

Für den primären Horizont sind 288 vorregistrierte Vergleichszellen geplant.
Die 12h-/24h-Sensitivitäten ergänzen 432 Zellen; insgesamt sind es 720 Zellen.
Eine Zelle ist nur eine Kombination aus Strategie/Baseline, Asset,
Zeitrahmen, Kontextvariante, Kosten und gegebenenfalls Horizont. Sie ist weder
ein Trade noch ein Ergebnis.

## Geschützte Phase-1-Basis vor Phase 2A

Gruppenfingerprint: SHA-256 über sortierte Zeilen
`projektpfad|datei_sha256`, getrennt durch LF.

| Gruppe | Dateien | Bytes | Fingerprint |
|---|---:|---:|---|
| Raw-Vollimport | 361 | 7.568.270 | `0cb03f47844d0073701c255e9eedd893a6672158f39579c80348ac4d1b8b62e7` |
| Interim-Vollimport | 319 | 26.167.940 | `14b92e6195e857417b71ebc2a9873a1b3a172d22e4fdcd1e6cbdcc5458686198` |
| Phase-1B-Nachweise | 5 | 1.093.624 | `822a47948b1cbd7e90ccf871cca2813ea64f1a74d23ec79929a14f585929166d` |
| Phase-1C-A-Nachweise | 4 | 20.649 | `4f48e5863c92ea2a60e092f2de610eaf7c37f998ae2f2ac9f0d6574e805d4032` |
| Phase-1C-B-Nachweise inklusive SQL | 6 | 29.333 | `260f613662186647b99b4a7a5b64b9b7778a3a2398afaf0202151c21b28e846b` |
| Phase-1C-C-Nachweise | 17 | 80.795 | `2350a8a26044830be93a443b1ed1940cca27c98130e58de8a81bb2f64e9b77ac` |
| Power-BI-Vertrag und Manifest | 3 | 9.082 | `cc72600b743f68f4a861f4c18e6d5025e56327d6bdb2d5eede836b3f6580b850` |

Einzelhashes:

- Processed 1h: `7468ce970381e34fc60a8227fb1594dee5435e88f5521f06ed82bfa15f5ce805`
- Processed 4h: `ab2ff44340b295d140db9fa1cb81cf5690dc7d78a44392599381c1d2e7edc91b`
- SQLite: `7f2e5deadd2c3c3e3f1820266f7f7b680def14d6ecda62c8dbbf5a11d9f0033e`

## Reproduzierbare Offline-Prüfung

```powershell
python -B -m src.backtest_contract --config config/backtest.json
```

Der Befehl gibt ausschließlich JSON auf der Konsole aus. Erwartet werden
`PHASE2A_CONTRACT_VALID`, Gate 2 `NOT_EVALUATED`, null geschriebene Dateien
und null berechnete Features, Signale, Positionen oder Backtests.
