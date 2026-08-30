# Phase 2B – Implementierungsvertrag des Offline-Cores

## Zweck und Grenze

Phase 2B setzt ausschließlich den in Phase 2A vorregistrierten Core um. Sie
berechnet 25 Merkmale, fünf feste Signale und drei deterministische Baselines
für Development 2021–2022 und Validation 2023. Der finale Test 2024–2025 bleibt
versiegelt. Es gibt weder Netzwerkzugriff noch Live-Trading, Short-Positionen,
Hebel, Funding, Stops, Machine Learning oder Parameteroptimierung.

## Zeitliche Schutzregeln

- Jede rollende Berechnung gruppiert nach `symbol`, `timeframe` und
  `segment_id`. An jeder Segmentgrenze beginnt der Zustand neu.
- Die Marktinformation der Kerze `t` ist erst zu `decision_time_utc` nutzbar.
  Ein Signal steigt frühestens am Open von `t+1` ein.
- Nach exakt `N` vollständig gehaltenen Kerzen erfolgt der Ausstieg am Open der
  folgenden Kerze. 1h verwendet N = 4, 12 und 24; 4h verwendet N = 1, 3 und 6.
- Fehlt der vollständige Horizont im selben Segment und Split, entsteht kein
  Trade. Ein Trade wird nie verkürzt und nie über eine Lücke fortgeführt.
- D+1 und D+2 werden unabhängig aus der unveränderten Coin-Metrics-Tabelle mit
  `availability <= decision_time_utc` verbunden. D+2 verschiebt niemals schon
  verbundene D+1-Werte.
- Die Testzeilen 2024–2025 werden nur für Eingabehash, Primärschlüssel und
  Sollzählung erkannt. Vor der Featureberechnung werden sie ausgesondert.

## Deterministische Ausführungsdetails

Pro Asset, Zeitrahmen, Kontextvariante, Strategie und Horizont ist höchstens
eine Position gleichzeitig offen. Ein weiteres Signal während dieser Position
wird als Überlappung abgelehnt. Diese Regel verhindert, dass ein häufiges
Signal unbemerkt mehrere gleichzeitige Positionen erzeugt.

Die periodische Baseline verwendet das erste verfügbare Open jeder
UTC-ISO-Woche, sofern der vollständige Horizont im selben Segment und Split
liegt. Segment-Buy-and-Hold verwendet je Segment und Split das erste und letzte
Open. `last_available_open_inside_segment` gilt nur für diese Baseline, niemals
als verkürzter Signal-Exit. `always_flat` hat null Trades und null Exposition.

Die Brutto- und Nettorendite jedes Trades wird getrennt gespeichert. Die
Kostenfälle betragen 20, 30 und 50 Basispunkte; 30 Basispunkte sind primär. Die
Nettorendite verwendet die vorregistrierte multiplikative Gebühren- und
Slippageformel. MAE und MFE beschreiben nur die beobachteten Tiefs und Hochs im
Haltefenster; sie behaupten keine unbekannte Intrabar-Reihenfolge.

## Kennzahlen und Unsicherheit

Eine kumulierte Rendite wird nur innerhalb einer einzelnen Strategie-Zelle mit
der Annahme einer Starteinheit und vollständiger Wiederanlage sequentieller,
nicht überlappender Trades berechnet. Der Drawdown folgt dieser
Nach-Trade-Kapitalkurve. Für den Asset-Pool werden deshalb weder kumulierte
Rendite noch Drawdown ausgewiesen: parallele Assetpositionen hätten ohne
zusätzlichen Kapitalallokationsvertrag keine eindeutige gemeinsame Kurve.

Der Profit Factor ist bei null Verlusttrades oder null Trades bewusst leer.
Unsicherheit wird deskriptiv über Trade-Streuung und die Spannweite der
Segmentmittel dargestellt. Es werden keine IID-Annahme, p-Werte oder
Signifikanzbehauptungen verwendet.

## Cache- und Publikationsregel

Der erste freigegebene Lauf erzeugt Daten- und Berichtsbündel zunächst in einem
temporären Projektordner und veröffentlicht beide ohne Überschreiben. Ein
vorhandener Cache ist nur `CACHED_VALID`, wenn alle erwarteten Dateien ohne
fehlende oder zusätzliche Datei byteidentisch neu berechnet werden. Jede
Abweichung stoppt fail-closed. Ein gültiger Cachelauf verändert weder Bytes noch
Änderungszeiten.

Die verbindliche Implementierungs-/Numerik-Policy heißt
`phase2b_fsum_float17_provenance_v2`. Gleitende Mittelwerte verwenden
`math.fsum(window) / window_length`; Gleitkommazahlen werden mit `.17g`
round-trip-sicher serialisiert. Der Hashnachweis bindet zusätzlich die aktuelle
Phase-2B-Konfiguration, `src/backtest_pipeline.py`, den unveränderten
Phase-2A-Vertrag und sämtliche geschützten Eingaben. Ändert sich einer dieser
Werte, darf ein vorhandenes Bündel nicht als `CACHED_VALID` gelten.

## Einfache Erklärung für Amin

Wir schauen bei jedem Signal nur rückwärts. Erst nachdem eine Kerze wirklich
fertig ist, dürfen wir am nächsten echten Open einsteigen. Fehlt später auch nur
eine notwendige Kerze, verwerfen wir den Trade. Die Jahre 2024 und 2025 bleiben
unangetastet, damit sie später eine faire Schlussprüfung bilden können.
