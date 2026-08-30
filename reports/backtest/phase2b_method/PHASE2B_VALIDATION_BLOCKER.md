# Phase 2B – Validierungsblocker nach dem einmaligen Reallauf

## Abschlussnachtrag vom 3. August 2026

Der hier dokumentierte historische Blocker wurde durch Quarantäne, kontrollierte Neuerzeugung und unabhängige Offline-Abnahme vollständig geschlossen. Aktueller Status: Phase 2B `PASS`, Gate 2 `PASS`, finaler Test `SEALED_NOT_EVALUATED`. Maßgeblicher Nachweis ist `PHASE2B_INDEPENDENT_ACCEPTANCE_REPORT.md`. Der nachfolgende Bericht bleibt als unveränderte Fehler- und Reaktionshistorie erhalten.

## Status

**Phase 2B ist nicht abgenommen. Gate 2 bleibt `NOT_EVALUATED`.**

**Nachtrag zur kontrollierten Neuerzeugung:** Das hier dokumentierte ungültige
Bündel wurde inzwischen vollständig und unverändert quarantänisiert. Ein
separat freigegebener Lauf erzeugte mit der korrigierten Numerik ein neues
Bündel; der genau eine Cachelauf bestätigte es als `CACHED_VALID`. Der frühere
Blocker `BLOCKED_PENDING_CONTROLLED_REBUILD` ist technisch behoben. Die
unabhängige fachliche Abnahme bleibt offen; deshalb bleiben Phase 2B und Gate 2
`NOT_EVALUATED`. Aktueller Nachweis:
`reports/backtest/phase2b_method/PHASE2B_REBUILD_REPORT.md`.

Der genau einmal freigegebene Offline-Lauf erzeugte zwar 158.940
Featurezeilen, 264.624 kostenbewertete Tradezeilen und 1.440 Ergebniszellen für
Development und Validation. Die anschließende unabhängige feldgenaue Prüfung
fand jedoch einen numerischen Grenzfall im SMA-Crossover. Deshalb sind die
vorhandenen Ergebnisdateien unter `data/processed/phase2b/` und
`reports/backtest/phase2b_outputs/` **nicht als fachlich validierte Evidenz zu
verwenden**.

## Gefundene Abweichung

Für SOLUSDT 1h in `SEGMENT_004` waren SMA20 und SMA50 am
2022-12-18 18:00 UTC dezimal exakt gleich (`12.411`). Die normale binäre
Float-Summierung berechnete intern ungefähr `12.411000000000001` gegenüber
`12.410999999999994`. Dadurch wurde der Crossover um eine Stunde verschoben:

- 18:00 UTC: Signal fälschlich wahr statt falsch;
- 19:00 UTC: Signal fälschlich falsch statt wahr;
- beide Abweichungen liegen getrennt in D+1 und D+2 vor, also vier
  Featurezeilen insgesamt.

Die übrigen vier Signalformeln stimmten über alle Featurezeilen feldgenau. Die
Splitzahlen, Testversiegelung, Kontextverfügbarkeit, Manifesthashes und
Tradepreisprovenienz waren bis zum Abbruch der Vollprüfung konsistent. Wegen
des Signalfehlers werden daraus dennoch keine Performanceaussagen freigegeben.

## Korrektur und Schutzreaktion

Der Code verwendet nun `math.fsum` für stabile Mittelwerte und serialisiert
Floats round-trip-sicher mit 17 signifikanten Stellen. Ein synthetischer
Regressionstest deckt die exakte SMA-Gleichheit ab. Danach bestanden 343/343
Tests ohne Überspringung.

Der genau eine erlaubte Cachelauf berechnete das korrigierte Bündel nur
temporär neu und stoppte erwartungsgemäß fail-closed mit
`Phase-2B data cache mismatch`. Er überschrieb keine vorhandene Datei. Der
Byte-, Größen- und Änderungszeit-Snapshot der 18 vorhandenen Ausgabedateien
blieb `f1c9dcdfa19e67e74241ea2d7e9a6199907f8fe768a23652282f202fcde09d45`.

## Erforderliche Freigabe

Ein fachlich gültiger Abschluss benötigt einen neuen ausdrücklichen Auftrag,
der das kontrollierte Entfernen oder Quarantänisieren des ausschließlich in
diesem fehlgeschlagenen Lauf erzeugten Phase-2B-Bündels und genau eine neue
Erzeugung mit dem korrigierten Code erlaubt. Ohne diese Freigabe bleiben die
vorhandenen Ausgaben unverändert, Phase 2B unvollständig und alle
Gate-2-Kriterien `NOT_EVALUATED`.
