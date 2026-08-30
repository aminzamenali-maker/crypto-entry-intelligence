# Finaler Test 2024–2025 - technische Nachlaufprüfung

## Ergebnis

**Technischer Status:** `PASS`
**Finaler Test:** `FINAL_TEST_COMPLETED_EXACTLY_ONCE`
**Automatischer oder zweiter Lauf:** nicht erfolgt und nicht erlaubt
**Parameteränderungen nach Gate 2:** `0`

Der vorregistrierte finale Zeitraum wurde mit dem freigegebenen Methodenstand genau einmal ausgewertet. Der Lauf startete am `2026-08-04T05:01:23.257061Z` und endete am `2026-08-04T05:02:37.613368Z`. Die gemessene Laufzeit betrug 74.356 Sekunden.

## Gebundener Projektstand

- Methoden-Commit: `648a74198a97e4e57d839a05db2af55fd1229190`
- Ausführungs-HEAD: `d4bc6dc149705ddcb881bdddf3d4fda4d0b373be`
- Final-Konfiguration: `7983d955168afed2ad449f6d525880972e18cfdc8170cb497f9de79b528e3a1e`
- Einmal-Runner: `189bab4de61f273e61cf62956ca4353ed00970b9b764b625fbfb2150492f845c`
- Bundle-Snapshot: `c9366bf6a050df7c5701194f5ba8dbfd9e3199aff1b1ee82ca02a4b93f9d4d8e`
- Netzwerkzugriff, Live-Orders, Short, Hebel, Funding, ML und Parameteroptimierung: deaktiviert

## Erzeugter Umfang

| Element | Anzahl |
|---|---:|
| Markt-Eingabezeilen | 65.790 |
| Featurezeilen D+1 und D+2 | 131.580 |
| Trade-/Kostenzeilen | 219.624 |
| Detailergebniszellen | 720 |
| Aggregierte Ergebniszellen | 240 |
| Dateien im finalen Bundle | 14 |

## Manifest- und Integritätsprüfung

- Manifest-Einträge: 13/13 gültig
- Bundle-Dateien inklusive Manifest: 14/14
- Bundle-Gesamtgröße: 162.364.781 Bytes
- Neu berechneter Snapshot: `c9366bf6a050df7c5701194f5ba8dbfd9e3199aff1b1ee82ca02a4b93f9d4d8e`
- Snapshot in der Laufquittung: `c9366bf6a050df7c5701194f5ba8dbfd9e3199aff1b1ee82ca02a4b93f9d4d8e`
- Laufstatus und Laufquittung feldgenau identisch: `true`
- Hash-, Zeilen- oder Dateifehler: 0
- `.part`-Dateien: 0

Alle 13 Manifesteintraege wurden gegen die tatsächlichen Dateien neu geprüft. Die SHA-256-Werte und CSV-Zeilenanzahlen stimmen vollständig. Der Bundle-Snapshot entspricht exakt der Laufquittung.

## Laufumgebung

- Python: `3.13.5 (main, May  5 2026, 21:05:52) [GCC 14.2.0]`
- Plattform: `Linux-6.12.13-x86_64-with-glibc2.41`
- Pandas-Import im Einmal-Runner oder Backtest-Core: `false`

Der Auswertungspfad verwendet für den finalen Lauf die Python-Standardbibliothek und die bereits hashgebundenen Projektmodule. Die Ergebnisdateien wurden nach dem Lauf nicht verändert.

## Entscheidung

Der einmalige finale Test ist technisch abgeschlossen und sein Bundle ist konsistent, vollständig und durch Manifest, Snapshot, Startstatus und Laufquittung belegt. Ein zweiter Lauf ist unzulässig. Die Ergebnisse dürfen nun deskriptiv interpretiert und für Power BI sowie die Präsentation verwendet werden; nachträgliche Parameterwahl oder Optimierung bleibt verboten.
