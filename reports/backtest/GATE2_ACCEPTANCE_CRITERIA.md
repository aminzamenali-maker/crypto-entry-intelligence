# Gate 2 – Abnahmekriterien für Signale und Core-Backtest

## Aktueller Status

**Gate 2: `NOT_EVALUATED`.** Phase 2A hat ausschließlich Methode,
Konfiguration und Prüfvertrag vorregistriert. Kein Kriterium mit späterer
Feature-, Signal-, Trade- oder Ergebnisevidenz kann jetzt bestanden werden.

## Matrix

| ID | Abnahmekriterium | Erforderlicher objektiver Nachweis | Status |
|---|---|---|---|
| G2-01 | Eingaben entsprechen unverändert der abgenommenen Phase-1-Basis. | Hashes der zwei Processed-Tabellen, SQLite und Gruppenfingerprints für Raw, Interim sowie Phase-1B/C-Nachweise stimmen. | NOT_EVALUATED |
| G2-02 | Featuretabellen sind reproduzierbar, haben eindeutige Schlüssel und exakt dokumentierte Schemata. | Manifest, Zeilenzahlen, Hashes, Nullwert- und Duplikatprüfung. | NOT_EVALUATED |
| G2-03 | Kein Feature verwendet Zukunftsinformationen. | Negativtests für Forward-Felder, zentrierte Fenster, nicht verschobene Breakouts und künftigen Kontext. | NOT_EVALUATED |
| G2-04 | Alle Rolling-Zustände beginnen je Asset, Zeitrahmen und Segment neu. | Segmentstart- und Lückentests; vollständige `min_periods`; keine Werte während Warm-up. | NOT_EVALUATED |
| G2-05 | D+1 und D+2 werden unabhängig und leakage-sicher verbunden. | Zwei neue As-of-Joins mit jeweiliger Verfügbarkeitsmaske; keine Verschiebung bereits gejointer D+1-Werte. | NOT_EVALUATED |
| G2-06 | Signale entstehen erst nach vollständigem Schluss von Kerze t. | Zeitstempeltests mit `decision_time_utc`; Einstieg nie vor Open `t+1`. | NOT_EVALUATED |
| G2-07 | Ausführungen verwenden handelbare OHLC-Preise und mischen 1h/4h nicht. | Entry-/Exit-Preisprovenienz je Trade; keine Heikin-Ashi-Ausführung. | NOT_EVALUATED |
| G2-08 | Kein Trade berührt einen Ausschlussmonat oder überschreitet eine Segmentgrenze. | Negativtests an allen fünf Segmentenden und sieben Ausschlussmonaten; null Verstöße. | NOT_EVALUATED |
| G2-09 | Primäre Haltedauer ist vergleichbar: 1h×4 und 4h×1; 12h/24h nur Sensitivität. | Tradevertrag und exakte Barabstandsprüfungen. | NOT_EVALUATED |
| G2-10 | Kosten sind vollständig und größer null. | Brutto/Netto getrennt; 20/30/50 bp exakt; 30 bp primär; Komponentenprüfung je Trade. | NOT_EVALUATED |
| G2-11 | Alle drei Baselines sind fair, deterministisch und reproduzierbar. | `always_flat`, segmentweises Buy-and-Hold und periodischer Einstieg mit identischem Markt-, Segment- und Kostenvertrag. | NOT_EVALUATED |
| G2-12 | Entwicklung, Validierung und finaler Test bleiben strikt zeitlich getrennt. | Keine Überlappung; Parameterprovenienz nur Entwicklung/Validierung; finaler Test genau einmal nach Freigabe. | NOT_EVALUATED |
| G2-13 | Ergebniskennzahlen sind vollständig und plausibel. | Brutto-/Nettorendite, positives Nettoergebnis, MAE, MFE, Tradeanzahl, Exposition; OHLC-Intrabargrenze dokumentiert. | NOT_EVALUATED |
| G2-14 | Ergebnisse werden nach Asset, Zeitrahmen, Segment, Kalenderphase und aggregiert berichtet. | Exportierbare Tabellen mit vollständiger Gruppendeckung und Abstimmung auf Tradeebene. | NOT_EVALUATED |
| G2-15 | Signalqualität, Performance, Unsicherheit und praktische Nutzbarkeit werden getrennt beurteilt. | Bericht mit Baseline-Differenzen, Stichprobengrößen, Streuung/Stabilität und Kosten-/Abdeckungsgrenzen; keine Gewinnversprechen. | NOT_EVALUATED |
| G2-16 | Der vollständige Core-Lauf ist offline reproduzierbar und fail-closed. | Wiederholung erzeugt byteidentische Manifeste/Ergebnisse; Manipulations- und Abbruchtests; keine Mutation der Phase-1-Basis. | NOT_EVALUATED |

## Gate-Entscheidungsregel

Gate 2 darf erst entschieden werden, wenn G2-01 bis G2-16 einzeln mit realer
Evidenz bewertet wurden. Ein positiver Renditewert ist keine Gate-Bedingung.
Ein methodisch sauberer negativer Befund kann Gate 2 bestehen. Jeder Fehler bei
Leakage, Segmentgrenzen, Ausführungspreisen, Kosten oder Testtrennung führt
mindestens für das betroffene Kriterium zu `FAIL` und verhindert ein
Gesamt-`PASS`.

Nach Gate 2 ist gemäß `AGENTS.md` ein eigener Prüfbericht erforderlich. Ohne
ausdrücklichen Auftrag beginnen weder Phase 3 noch Machine Learning.
