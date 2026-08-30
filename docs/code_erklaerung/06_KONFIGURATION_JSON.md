# 06 - JSON-Konfigurationen erklaert

## Warum JSON nicht direkt kommentiert wird

Standard-JSON kennt keine Kommentare. Deshalb bleiben die Originaldateien unveraendert und werden hier feldweise erklaert.

## `config/data_pilot.json`

**Zweck:** kleiner reproduzierbarer Quellenpilot fuer Gate 0.

| Bereich | Bedeutung |
|---|---|
| `assets` | BTCUSDT, ETHUSDT, SOLUSDT und ihre Vergleichsrollen |
| `timeframes` | 1h = 3600 s, 4h = 14400 s |
| `pilot_months` | kleine Testmonate statt sofort Vollimport |
| `recommended_history` | Core-Zeitraum 2021-01-01 bis vor 2026-01-01 |
| `binance` | primaere OHLCV-Quelle |
| `coinmetrics` | taeglicher BTC-Kontext + D+1/D+2-Verfuegbarkeit |
| `candidate_sources` | dokumentierter Vergleich weiterer Quellen |

## `config/full_import.json`

**Zweck:** Vollimport exakt festlegen.

Wichtige Werte:

- Assets: `BTCUSDT, ETHUSDT, SOLUSDT`
- Download: `1h`; `4h` wird abgeleitet
- Zeitraum: `2021-01-01T00:00:00Z` bis vor `2026-01-01T00:00:00Z`
- Erwartete Binance-Auftraege: `180`
- Erwartete 1h-Rawzeilen: `131,472`
- Sicherheitsmodus: `dry-run`
- No-Overwrite Raw/Interim/Processed: jeweils `true`

## `config/backtest.json`

**Zweck:** Phase 2A - die Methode vor dem Backtest festschreiben.

### Markt und Ausfuehrung

- Spot, Long/Flat, keine Shorts, Hebel 1
- 1h primaer; 4h Robustheit
- Einstieg: naechstes Open nach vollstaendig abgeschlossener Signalkerze
- Ausstieg: Open nach exakt der Haltedauer
- kein Same-Bar-Close-Einstieg
- kein Ueberqueren von Segmentgrenzen

### Kosten

| Szenario | Round Trip |
|---|---:|
| `low_20bps` | 20 bp = ca. 0,20 % |
| `base_30bps` | 30 bp = ca. 0,30 % |
| `high_50bps` | 50 bp = ca. 0,50 % |

### Signale

Die JSON-Datei enthaelt exakt dieselben fuenf Signalideen wie der Python-Code. Das ist wichtig: Konfiguration und Implementierung koennen gegeneinander getestet werden.

### Zeitliche Splits

Development, Validation und Final Test sind getrennt. Der Final Test darf nicht zur Parameterauswahl verwendet werden.

## `config/backtest_phase2b.json`

**Zweck:** Offline-Implementierung des vorregistrierten Backtests.

Wichtige Schutzregeln:

- Netzwerk aus
- Final Test noch nicht auswerten
- kein ML / keine Optimierung
- keine bestehenden Outputs ueberschreiben
- Development + Validation werden ausgewertet
- `final_test` bleibt versiegelt
- Float-Ausgabe `.17g`
- SMA-Regel: `math.fsum(window)/window_length`
- Cache nur als vollstaendiges byteidentisches Bundle akzeptieren

## `config/final_test_once.json`

**Zweck:** Final-Test 2024-2025 exakt einmal kontrolliert ausfuehren.

Wichtige Felder:

- `confirmation_token`: exakter manueller Freigabetext
- `require_clean_git`: sauberer Git-Zustand vorgeschrieben
- `require_no_remote`: kein Remote erlaubt
- geschuetzte Methodendateien mit SHA-256
- erwarteter Final-Split: 65.790 Marktzeilen
- keine Parameterveraenderung nach Ergebnis
- kein automatischer Retry nach Start

## Warum diese Konfigurationsdateien fachlich wichtig sind

Viele wichtige Entscheidungen stehen nicht versteckt im Python-Code, sondern explizit in JSON: Zeitraum, Assets, Kosten, Haltedauern, Splits und Verbote. Dadurch kann ein Tutor die Methode lesen, ohne zuerst tausende Python-Zeilen verstehen zu muessen.
