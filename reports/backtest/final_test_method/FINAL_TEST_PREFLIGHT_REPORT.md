# Finaler Test 2024–2025 – schreibgeschützte Vorprüfung

## Ergebnis

**Vorprüfung:** `PASS`
**Runner-Status:** `FINAL_TEST_PREFLIGHT_VALID`
**Finaler Test:** `SEALED_NOT_EVALUATED`
**Auswertungs- oder Schreibvorgänge:** `0`

Die Vorprüfung wurde am 3. August 2026 nach dem Commit des Einmal-Runners
ausgeführt. Der echte `--execute`-Modus wurde nicht aufgerufen.

## Geprüfter Git-Zustand

- Branch: `phase/2-signals-backtest`
- Ausführungs-HEAD: `37906bc09b0a9af23b2cac321842c2bdd7304894`
- freigegebener Methoden-Commit:
  `648a74198a97e4e57d839a05db2af55fd1229190`
- Methoden-Commit ist Vorfahr des Ausführungs-HEAD
- Arbeitsbaum: sauber
- Remotes: 0

## Methoden- und Paketprüfung

- geschützte Methodendateien: 12/12 byteidentisch
- Phase-2B-Manifesteinträge: 17/17 gültig
- vollständiges Phase-2B-Paket: 18 Dateien
- Paketgröße: 196.071.683 Bytes
- Phase-1- und Phase-2B-Provenienz: gültig
- Gate-2-Abnahmebericht: Phase 2B `PASS`, Gate 2 `PASS`

## Finaler Split

Die Vorprüfung bestätigte ausschließlich den erwarteten Bestand:

| Zeitrahmen | Eingabezeilen |
|---|---:|
| 1h | 52.632 |
| 4h | 13.158 |
| Gesamt | 65.790 |

Während der Vorprüfung wurden berechnet beziehungsweise geschrieben:

- finale Featurezeilen: 0
- finale Trades: 0
- finale Kennzahlen: 0
- Dateien: 0

Vor und nach der Prüfung fehlten weiterhin:

- `data/processed/final_test_once/`
- `reports/backtest/final_test_outputs/`
- `data/processed/final_test_run_state.json`
- `reports/backtest/final_test_method/FINAL_TEST_EXECUTION_RECEIPT.json`

## Ausgeführter Befehl

```powershell
python -B -m src.final_test_once --config config/final_test_once.json
```

Ausgabe: `FINAL_TEST_PREFLIGHT_VALID`, Exit-Code 0.

## Entscheidung

Der Einmal-Runner ist technisch bereit. Der finale Test wurde nicht geöffnet
und bleibt `SEALED_NOT_EVALUATED`. Die echte Ausführung benötigt weiterhin
eine neue ausdrückliche Freigabe sowie den getrennten `--execute`-Schalter und
den exakten Bestätigungscode.
