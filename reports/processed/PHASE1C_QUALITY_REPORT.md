# Phase 1C-A Qualitaetsbericht

## Unabhängiges Abnahmeurteil

**Phase 1C-A: `PASS`. G1-10: `PASS`.**

Die unabhängige Offline-Abnahme bestätigt die kanonischen Processed-Tabellen
und den leakage-sicheren D+1-As-of-Join. Gate 1 bleibt `NOT_EVALUATED`, weil
G1-12 und G1-13 noch offen sind; SQL, EDA und Power BI sind nicht Bestandteil
von Phase 1C-A.

## Mengen und Abdeckung

- 1h Eingabe/Ausgabe: 116208 / 116208
- 4h Eingabe/Ausgabe: 29052 / 29052
- D+1-Matches: 145260 von 145260 (100.00 %)
- Join-Verluste: 0
- Join-Aufblähung: 0
- Primärschlüsselduplikate: 0
- Nullwerte: 0
- Zukunftskontext-Verletzungen: 0
- Gemeinsame gueltige Monate: 53
- Gemeinsame Segmente: 5

## Quellenluecke und konservativer Ausschluss

Die Raw-Quelle besitzt gegenueber dem Kalender-Soll 42 tatsaechlich fehlende Stunden. Wegen der verbindlichen Monatspolicy wurden jedoch 21 Asset-Monate beziehungsweise 15.264 Kalenderstunden vollstaendig ausgeschlossen. Das entspricht 11,61 % konservativ ausgeschlossener und 88,39 % akzeptierter Abdeckung. Diese beiden Sachverhalte werden nicht gleichgesetzt.

## Leakage-Schutz

`decision_time_utc` liegt exakt nach Kerzenschluss: bei ms-Daten plus 1 ms, bei us-Daten plus 1 us. Der Join verwendet ausschliesslich den neuesten Kontext mit `available_from_utc_d1 <= decision_time_utc`. Der D+2-Zeitpunkt bleibt separat gespeichert und wurde noch nicht als Sensitivitaet ausgewertet.

Die gemeinsame Asset-Maske und Segment-IDs verhindern eine spaetere unbemerkte Fortsetzung ueber ausgeschlossene Monatsgrenzen. Rolling Features, Renditen, Signale und Positionen wurden nicht berechnet.

Die unabhängige Prüfung bestätigte zusätzlich, dass jede Processed-Marktzeile
ihrer Interimzeile entspricht und sämtliche Kontextzuordnungen mit einem
separat berechneten rückwärtsgerichteten As-of-Join übereinstimmen. In jeder
Zeile gilt `available_from_utc_d1 <= decision_time_utc`; D+2 bleibt als
eigenständiges Feld erhalten. Ausgeschlossene Monate kommen nicht in
Processed vor. Alle Manifesthashes sowie Raw-, Interim- und
Phase-1B-Schutzwerte blieben unverändert.

## Segmentgrenzen

| Segment | Erster Monat | Letzter Monat | Gueltige Monate |
|---|---|---|---:|
| SEGMENT_001 | 2021-01 | 2021-01 | 1 |
| SEGMENT_002 | 2021-05 | 2021-07 | 3 |
| SEGMENT_003 | 2021-10 | 2021-11 | 2 |
| SEGMENT_004 | 2022-01 | 2023-02 | 14 |
| SEGMENT_005 | 2023-04 | 2025-12 | 33 |

## Noch offen

G1-10 ist unabhängig `PASS`. G1-12 bleibt `NOT_EVALUATED`, weil SQL-Schema
und Kern-Views fehlen. G1-13 bleibt `NOT_EVALUATED`, weil EDA und der
Power-BI-Datenvertrag fehlen. Gate 1 insgesamt bleibt `NOT_EVALUATED`.
