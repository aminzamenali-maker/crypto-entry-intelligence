# Finaler Test 2024–2025 – kontrollierter Einmallauf

## Status

**Vorbereitung:** `PREPARED_NOT_EXECUTED`
**Finaler Test:** `SEALED_NOT_EVALUATED`
**Gate 2:** `PASS`
**Freigegebener Methoden-Commit:** `648a74198a97e4e57d839a05db2af55fd1229190`

Der Runner und seine Konfiguration sind vorbereitet und synthetisch getestet.
Es wurden keine finalen Features, Signale, Trades oder Kennzahlen berechnet.

## Zweck

Der Zeitraum vom 1. Januar 2024 bis vor den 1. Januar 2026 darf nach der
Methodenfreigabe genau einmal mit der vorregistrierten Logik ausgewertet
werden. Alle fünf Signale, drei Haltedauern je Zeitrahmen, beide
Kontextvarianten und alle drei Kostenstufen bleiben unverändert. Es findet
keine nachträgliche Parametersuche statt.

## Dateien

- Runner: `src/final_test_once.py`
- Konfiguration: `config/final_test_once.json`
- Tests: `tests/test_final_test_once.py`
- späterer Datenordner: `data/processed/final_test_once/`
- späterer Berichtsordner: `reports/backtest/final_test_outputs/`
- dauerhafter Laufstatus: `data/processed/final_test_run_state.json`
- spätere Laufquittung: `reports/backtest/final_test_method/FINAL_TEST_EXECUTION_RECEIPT.json`

## Fail-closed-Schutz

Vor einem Lauf werden geprüft:

1. richtiger Branch und sauberer Git-Zustand,
2. Methoden-Commit ist Vorfahr des aktuellen HEAD,
3. zwölf geschützte Methoden- und Nachweisdateien sind byteidentisch,
4. Gate 2 ist im unabhängigen Abnahmebericht `PASS`,
5. alle 17 Phase-2B-Manifesteinträge stimmen mit den echten Dateien überein,
6. Phase-1-Eingaben und Phase-2B-Provenienz sind unverändert,
7. kein Remote und keine `.part`-Datei existieren,
8. kein früherer Laufstatus, keine Quittung und kein Ergebnisordner existieren,
9. der finale Split besitzt exakt 52.632 1h-, 13.158 4h- und insgesamt 65.790 Eingabezeilen,
10. Netzwerk, Live-Orders, Short, Hebel, Funding, ML und Parameteroptimierung sind deaktiviert.

Der echte Lauf erstellt vor dem Lesen und Berechnen der finalen Marktdaten
exklusiv eine dauerhafte Startdatei. Bleibt der Prozess danach hängen oder
schlägt er fehl, ist kein automatischer zweiter Versuch erlaubt. Eine neue
Entscheidung wäre dann zwingend erforderlich.

## Sichere Vorprüfung

Die folgende Vorprüfung schreibt keine Datei und wertet den finalen Test nicht
aus:

```powershell
python -B -m src.final_test_once --config config/final_test_once.json
```

Erwarteter Status: `FINAL_TEST_PREFLIGHT_VALID` mit null Features, Trades und
Kennzahlen.

## Echte Ausführung – derzeit nicht freigegeben

Der echte Befehl ist absichtlich durch zwei getrennte Schalter geschützt:

```powershell
python -B -m src.final_test_once --config config/final_test_once.json --execute --confirm FINAL_TEST_2024_2025_EXACTLY_ONCE
```

Dieser Befehl darf erst nach einer letzten Vorabkontrolle und einer erneuten
klaren Freigabe ausgeführt werden. Danach dürfen Signale, Horizonte,
Kontextvarianten, Kosten oder Interpretationsregeln nicht mehr verändert
werden.

## Geplante Ausgaben

Der Einmallauf erzeugt ausschließlich den finalen Split:

- 131.580 Featurezeilen aus 65.790 Marktzeilen und zwei Kontextvarianten,
- Trade-/Kostenzeilen für alle unveränderten Signal- und Baselinevarianten,
- 720 Detailergebniszellen,
- 240 aggregierte Ergebniszellen,
- Qualitäts-, Provenienz- und Manifestnachweise,
- eine Laufquittung mit Methoden-Commit, Ausführungs-HEAD und Bundle-Snapshot.

Die tatsächliche Tradeanzahl und alle Performancewerte sind bis zum echten
Lauf unbekannt und werden nicht vorweggenommen.

## Einfache Erklärung für Amin

Die Regeln sind jetzt fest gespeichert. Der neue Runner kann nicht einfach
versehentlich den finalen Zeitraum öffnen. Ohne sauberen Projektzustand,
passende Hashes, Gate-2-Nachweis, `--execute` und den langen Bestätigungscode
bricht er ab. Heute wurde nur dieser Schutz vorbereitet; die Ergebnisse für
2024 und 2025 sind weiterhin unbekannt.
