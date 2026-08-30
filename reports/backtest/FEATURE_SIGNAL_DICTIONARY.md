# Phase 2A – Feature- und Signalwörterbuch

## Verbindliche Regeln

Jedes Feature wird nach `(symbol, timeframe, segment_id)` berechnet und verlangt
die vollständige Mindesthistorie. Die Kerze `t` ist erst am
`decision_time_utc` verfügbar. Zukunftsreturns, Exitpreise, Ergebnisse, MAE
und MFE sind keine Features. Kontextfeatures werden für D+1 und D+2 durch zwei
getrennte rückwärtsgerichtete As-of-Joins gebildet.

## Marktfeatures

| Name | Formel/Kernregel | Zeitrahmen | Lookback; Mindesthistorie | Nullwerte | Zweck und Hauptrisiko |
|---|---|---|---|---|---|
| `past_return_1` | `close_t / close_t-1 - 1` | 1h, 4h | 1; 2 Kerzen | nur Warm-up | kurzes Momentum; Lag muss im Segment bleiben |
| `past_return_4` | `close_t / close_t-4 - 1` | 1h, 4h | 4; 5 | nur Warm-up | rückwärts gerichtetes Momentum |
| `past_return_12` | `close_t / close_t-12 - 1` | 1h, 4h | 12; 13 | nur Warm-up | feste Momentumvariante |
| `past_return_24` | `close_t / close_t-24 - 1` | 1h, 4h | 24; 25 | nur Warm-up | längere Sensitivität |
| `sma_20` | Mittel der letzten 20 Close einschließlich `t` | 1h, 4h | 20; 20 | nur Warm-up | kurzer Trend; kein zentriertes Fenster |
| `sma_50` | Mittel der letzten 50 Close einschließlich `t` | 1h, 4h | 50; 50 | nur Warm-up | langer Trend |
| `sma_ratio_20_50` | SMA20 / SMA50 | 1h, 4h | 50; 50 | nur Warm-up | skalenfreier Trendvergleich |
| `rsi_14` | Wilder-RSI aus 14 vergangenen Close-Änderungen, Startmittel arithmetisch, danach Alpha 1/14 | 1h, 4h | 14 Returns; 15 Kerzen | nur Warm-up; bei null Verlust und positivem Gewinn 100 | Mean Reversion; Wilder-Zustand resetten |
| `atr_14_relative` | Wilder-Mittel aus True Range / Close | 1h, 4h | 14 TR; 15 Kerzen | nur Warm-up | relative Volatilität; vorheriger Close im Segment |
| `rolling_volatility_24` | Stichproben-Std. (`ddof=1`) der letzten 24 Ein-Kerzen-Returns | 1h, 4h | 24 Returns; 25 Kerzen | nur Warm-up | kurze realisierte Volatilität |
| `rolling_volatility_72` | Stichproben-Std. (`ddof=1`) der letzten 72 Ein-Kerzen-Returns | 1h, 4h | 72 Returns; 73 Kerzen | nur Warm-up | längere realisierte Volatilität |
| `volume_zscore_24` | `(Volumen - 24er-Mittel) / 24er-Stichproben-Std.` | 1h, 4h | 24; 24 | Warm-up oder Standardabweichung 0 | relative Aktivität |
| `candle_range_relative` | `(High - Low) / Open` | 1h, 4h | 1; 1 | bei positiven Opens verboten | Kerzenspanne |
| `candle_body_relative` | `(Close - Open) / Open` | 1h, 4h | 1; 1 | bei positiven Opens verboten | signierter Kerzenkörper |
| `taker_buy_share_1h` | Taker-Buy-Basisvolumen / Volumen | nur 1h | 1; 1 | nur bei Volumen 0 | Kaufaktivitätsanteil; nicht in 4h verfügbar |
| `prior_high_20_shifted` | Maximum der 20 Highs von `t-20` bis `t-1` | 1h, 4h | 20 frühere; 21 Kerzen | nur Warm-up | Breakout; zwingend um eine Kerze verschoben |
| `close_to_sma20_distance` | Close / SMA20 - 1 | 1h, 4h | 20; 20 | nur Warm-up | Abstand für Mean Reversion |

## Kontextfeatures

Die Rohfelder sind `PriceUSD`, `CapMrktCurUSD`, `TxCnt` und `AdrActCnt` aus
Coin Metrics. Die Namen im späteren Featurevertrag lauten:

- `context_price_usd`
- `context_market_cap_usd`
- `context_tx_count`
- `context_active_address_count`

Sie verwenden jeweils nur den neuesten Kontextwert, dessen D+1- oder D+2-
Verfügbarkeit nicht nach `decision_time_utc` liegt. Nach einem gültigen Join
sind Nullwerte nicht erlaubt.

Zusätzlich sind vier rückwärtsgerichtete Änderungen vorgesehen:

- `context_price_usd_change`
- `context_market_cap_usd_change`
- `context_tx_count_change`
- `context_active_address_count_change`

Formel: aktueller verfügbarer Wert geteilt durch den vorherigen **verschiedenen**
Kontextwert innerhalb desselben Marktsegments minus 1. Am ersten verschiedenen
Kontextwert eines Segments und bei einem Nenner von null bleibt das Feature
leer. Es wird niemals mit einem späteren Quelltag verglichen.

## Signalvarianten

| ID | Familie | Bedingung nach Schluss von Kerze t | Frühester Einstieg |
|---|---|---|---|
| `trend_sma20_cross_above_sma50` | Trend/Momentum | SMA20/SMA50 > 1 und vorher höchstens 1 | Open `t+1` |
| `momentum_return_12_positive` | Trend/Momentum | `past_return_12 > 0` | Open `t+1` |
| `breakout_close_above_prior_high_20` | Breakout | Close über verschobenem vorherigem 20er-Hoch | Open `t+1` |
| `mean_reversion_rsi14_below_30` | Mean Reversion | RSI14 < 30 | Open `t+1` |
| `mean_reversion_close_2pct_below_sma20` | Mean Reversion | Close/SMA20 - 1 ≤ -0,02 | Open `t+1` |

Diese Varianten sind Hypothesen, keine Empfehlungen und keine
Gewinnversprechen. Ein negatives Ergebnis ist zulässig und fachlich
aussagekräftig.

## Spätere Ziel- und Bewertungsfelder

`gross_return`, `net_return`, `positive_net_outcome`, MAE, MFE, Tradeanzahl
und Expositionsdauer dürfen erst nach der Feature- und Signalzeile entstehen.
Sie werden nie zurück in eine Featureberechnung gespeist. Die zeitliche
Reihenfolge von High und Low innerhalb einer Kerze ist unbekannt; daher gibt
es im Core keine intrabar Stop-/Take-Profit-Ausführung.
