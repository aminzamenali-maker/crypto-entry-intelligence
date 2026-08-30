# Phase 2B – kontrollierte Neuerzeugung nach SMA-Quarantäne

## Abschlussnachtrag vom 3. August 2026

Die in diesem historischen Neuerzeugungsbericht noch offene unabhängige Prüfung ist inzwischen abgeschlossen. Phase 2B und Gate 2 besitzen den Status `PASS`; der finale Test bleibt `SEALED_NOT_EVALUATED`. Maßgeblicher aktueller Nachweis ist `PHASE2B_INDEPENDENT_ACCEPTANCE_REPORT.md`. Der nachfolgende Text dokumentiert unverändert den Zustand unmittelbar nach der Neuerzeugung.

## Status

**Technischer Status: `REBUILT_PENDING_INDEPENDENT_REVIEW`.**  
**Phase 2B und Gate 2: `NOT_EVALUATED`.**

Das gesperrte erste Bündel wurde ohne Löschen per Verzeichnis-Rename unter
`data/processed/phase2b_quarantine/invalid_sma_boundary_run/` quarantänisiert.
Der vollständige Nachweis steht in `PHASE2B_QUARANTINE_RECORD.json`.

## Numerische Vorprüfung

Eine unabhängige Decimal-Referenz prüfte alle 79.470 Development- und
Validation-Marktzeilen. SMA20, SMA50 und alle fünf Signalbedingungen stimmten
mit dem korrigierten Code überein. Für SOLUSDT 1h in `SEGMENT_004` gilt:

- 2022-12-18 18:00 UTC: SMA20 = SMA50 = 12,411; kein Crossover;
- 2022-12-18 19:00 UTC: SMA20 = 12,4075 und SMA50 = 12,4016; Crossover;
- D+1 und D+2 liefern identische Marktsignale.

Die synthetische Regression verwendet die tatsächlich beobachtete
nichtkonstante 52-Preis-Sequenz, ohne auf lokale Ergebnisdateien zuzugreifen.

## Provenienz

- Policy: `phase2b_fsum_float17_provenance_v2`
- SMA-Regel: `math.fsum(window) / window_length`
- Float-Serialisierung: `.17g`
- Phase-2B-Konfiguration:
  `16382dc037b56fa30b5cfabff2dc1f336dab81b13c62cffafcef44f5c391a78e`
- Pipeline-Code:
  `8a8ab18b5575d5366429683cede5253bb3bd4640964671da8d879848d124d28c`
- unveränderter Phase-2A-Vertrag:
  `e4deb6b6ad56a8517f86822d85086524c3cd3c29890d9754453163d7f1107f04`

Der Cache prüft diese Provenienz vor der Byteprüfung. Eine veränderte
Konfiguration, ein veränderter Pipeline-Code oder eine andere Numerik-Policy
stoppt daher vor einer `CACHED_VALID`-Akzeptanz.

## Neuerzeugung und Cache

Der Befehl

```powershell
python -B -m src.backtest_pipeline --config config/backtest_phase2b.json
```

wurde genau einmal zur Neuerzeugung und danach genau einmal zur
Cachevalidierung ausgeführt. Beide Befehle endeten mit Exit-Code 0. Status der
Neuerzeugung war `PHASE2B_COMPLETED_DEVELOPMENT_VALIDATION_ONLY`, Status des
zweiten Laufs `CACHED_VALID`.

Das gültige Bündel besitzt 18 Dateien, 196.071.683 Bytes und den kombinierten
Hash-/Größen-/mtime-Snapshot
`d2d6c3c90939144536ee263aeb71848e1dde3b44abf2d51d4a3b030993aa2b8a`.
Der Snapshot blieb im Cachelauf identisch.

Die Quarantäne besitzt weiterhin 18 Dateien, 174.213.618 Bytes und den
Pfadsnapshot
`edb9ab532a44795b416dff8a442636e1b6f9aae529ef41187d22392cafbc8a13`.

## Zählungen und Qualität

- Featurezeilen D+1/D+2: 158.940
- Development-Featurezeilen: 98.820
- Validation-Featurezeilen: 60.120
- Trade-/Kostenzeilen: 264.624
- Ergebniszellen: 1.440, davon 720 je Split
- unabhängige D+1-/D+2-Zuordnungen mit unterschiedlichem Kontextdatum: 79.470
- verkürzte Core-Trades: 0
- Zukunftskontextverletzungen: 0
- ausgeschlossene Monatszeilen: 0
- Primärschlüsselduplikate: 0
- Manifest: 17/17 referenzierte Dateien stimmen

Vor dem Lauf bestanden 345/345 Tests ohne Überspringung. Das gültige Bündel
bestand danach zusätzlich die vollständige read-only Inhaltsprüfung.

## Ergebnisgrenze

Bei 30 bp waren die gepoolten durchschnittlichen Nettorenditen der fünf
Signale in Development und Validation auf beiden Zeitrahmen negativ; einzig
der einzelne Validation-4h-Breakout besaß auf Strategieebene einen kleinen
positiven Durchschnitt. Das ist historische Evidenz, kein Gewinnversprechen
und keine Handelsempfehlung. Segment-Buy-and-Hold ist eine Marktbaseline und
nicht mit einem Einstiegssignal gleichzusetzen.

## Versiegelter finaler Test

Der Testzeitraum 2024–2025 wurde ausschließlich mit 65.790 Zeilen für Eingabe-
und Schlüsselintegrität erkannt. Der Nachweis enthält ausdrücklich:

```text
final_test_status = SEALED_NOT_EVALUATED
final_test_feature_rows_evaluated = 0
final_test_signals_evaluated = 0
final_test_trades_evaluated = 0
final_test_metrics_evaluated = 0
```

Eine unabhängige Phase-2B-/Gate-2-Abnahme steht weiterhin aus. Weder finaler
Test, Phase 2C noch Machine Learning wurde begonnen.
