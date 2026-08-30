# Crypto Entry Intelligence

**End-to-End Data Analytics Project | Python · SQL · Power BI · Data Quality · Backtesting**

Datenbasierte Analyse, Validierung und Visualisierung von Einstiegssignalen im Kryptowährungshandel.

Dieses Projekt untersucht eine einfache, aber wichtige Frage: **Zeigen transparente Trading-Signale in historischen Daten einen stabilen messbaren Nutzen – auch nach Kosten und in neuen Zeiträumen?**

Der Schwerpunkt liegt nicht auf einer Gewinnversprechung, sondern auf einer **reproduzierbaren Datenpipeline, sauberer Validierung, Leakage-Schutz und nachvollziehbarer Ergebnisinterpretation**.

---

## Projekt auf einen Blick

- **Assets:** BTCUSDT, ETHUSDT, SOLUSDT
- **Zeitraum:** 2021–2025
- **Zeitrahmen:** 1h und 4h
- **Datenquellen:** Binance Public Data + Coin Metrics
- **Core-Signale:** SMA-Crossover, Momentum, Breakout, RSI Mean Reversion, SMA-Abstand
- **Kosten-Szenarien:** 20, 30 und 50 Basispunkte
- **Validierung:** Development → Validation → einmaliger Final Test 2024–2025
- **Tech Stack:** Python, SQLite/SQL, Power BI

---

## Zentrales Ergebnis

Im finalen Test 2024–2025 zeigten die untersuchten Regeln vor Kosten kleine positive Bewegungen. Nach realistischen Handelskosten waren die gepoolten Durchschnittsergebnisse jedoch negativ.

| Finaler Test | 1h | 4h |
|---|---:|---:|
| Durchschnitt brutto | +0,0143 % | +0,0327 % |
| Durchschnitt netto bei 30 bp | -0,2853 % | -0,2669 % |
| Trades | 11.275 | 6.412 |

Von **30 Signal-Horizont-Kombinationen** waren:

- Development netto positiv: **0/30**
- Validation netto positiv: **13/30**
- Finaler Test netto positiv: **3/30**
- in allen drei Zeitabschnitten stabil netto positiv: **0/30**

**Fazit:** Die getesteten Einzelregeln liefern keinen belastbaren Nachweis für einen stabilen zusätzlichen Netto-Informationswert nach Kosten. Dieses negative Ergebnis ist bewusst Teil des Projekts und zeigt, dass die Analyse nicht nachträglich auf ein gewünschtes Resultat optimiert wurde.

➡️ [Ausführliche Interpretation des finalen Tests](reports/backtest/final_test_method/FINAL_TEST_INTERPRETATION_REPORT.md)

---

## Methodischer Ansatz

```text
Historische Daten
      ↓
Datenqualität & Quellenprüfung
      ↓
Processed Data & Leakage-sicherer Kontext
      ↓
SQLite / SQL-Modell
      ↓
EDA & Power-BI-Datenvertrag
      ↓
Feature- und Signalberechnung
      ↓
Backtesting + Handelskosten
      ↓
Development / Validation
      ↓
Einmaliger Final Test 2024–2025
      ↓
Power BI & Ergebnisinterpretation
```

Wichtige methodische Regeln:

- keine Zukunftsinformationen für Entscheidungen
- ausgeschlossene Datenlücken werden nicht interpoliert
- rollende Berechnungen werden an Segmentgrenzen zurückgesetzt
- Handelskosten werden explizit berücksichtigt
- der finale Holdout-Zeitraum wurde mit unveränderter Methode **genau einmal** ausgewertet
- positive Einzelzellen werden nach dem Test nicht nachträglich als neue Strategie ausgewählt

➡️ [Technische Nachlaufprüfung des Final Tests](reports/backtest/final_test_method/FINAL_TEST_POST_RUN_VALIDATION_REPORT.md)

---

## Datenqualität & Reproduzierbarkeit

Die Pipeline enthält mehrere Qualitäts- und Integritätsprüfungen. Unter anderem werden Quellen, Zeitstempel, Vollständigkeit, Primärschlüssel, Hashes, Segmentgrenzen und Output-Manifeste geprüft.

Der vollständige Import dokumentiert **131.430 Raw-Istzeilen**, daraus **116.208 akzeptierte 1h-Zeilen** und **29.052 akzeptierte 4h-Zeilen**. Unvollständige Monate werden konservativ ausgeschlossen und transparent dokumentiert.

Die lokalen Roh- und Processed-Daten sowie Datenbankdateien werden bewusst nicht in Git versioniert. Reproduzierbare Reports, Konfigurationen, SQL, Python-Code und Qualitätsnachweise sind im Repository enthalten.

---

## Power BI

Das Projekt enthält einen mehrseitigen Power-BI-Report zur Analyse von:

- Brutto- vs. Nettoergebnissen
- Signalvergleich
- Stabilität über Zeitabschnitte
- Kosten-Sensitivität
- Marktumfeld
- Datenqualität

➡️ [Power-BI-Datei](powerbi/DSP_Crypto_Entry_Intelligence.pbix)  
➡️ [Power-BI-Datenvertrag](powerbi/POWER_BI_DATA_CONTRACT.md)  
➡️ [Power-BI-Measures](powerbi/POWER_BI_MEASURES.md)

---

## Repository-Struktur

```text
config/        Konfigurationen für Import, Backtest und Final Test
src/           Python-Pipelines und Analyse-Logik
sql/           Datenbankschema und Views
tests/         automatisierte Tests
reports/       Datenqualität, EDA, Backtest und Validierungsnachweise
powerbi/       Power-BI-Datei, Datenvertrag und Measures
presentation/  Projektpräsentation
docs/          verständliche technische Code-Dokumentation
```

Besonders hilfreich für einen schnellen technischen Einstieg:

➡️ [Code-Dokumentation](docs/code_erklaerung/README_CODE_DOKUMENTATION.md)  
➡️ [Feature- und Signal-Dictionary](reports/backtest/FEATURE_SIGNAL_DICTIONARY.md)  
➡️ [SQL-Datenwörterbuch](reports/sql/SQL_DATA_DICTIONARY.md)  
➡️ [Projektpräsentation](presentation/Crypto_Entry_Intelligence_Praesentation.pptx)

---

## Lokaler Einstieg

```powershell
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
```

Weitere reproduzierbare Projektläufe sind in den jeweiligen Reports und Konfigurationsdateien dokumentiert. Große Rohdaten werden nicht im Repository gespeichert.

---

## Nachgewiesene Kompetenzen

**Python · SQL · SQLite · Power BI · Datenbereinigung · Data Quality · EDA · Datenmodellierung · Feature Engineering · Backtesting · Leakage Prevention · Out-of-Sample Validation · Reproduzierbare Pipelines · Ergebnisinterpretation**

---

## Hinweis

Das Projekt ist eine historische Datenanalyse und **keine Trading- oder Anlageempfehlung**. Historische Ergebnisse sind keine Garantie für zukünftige Entwicklungen.

**Autor:** Mohammad Amin Zamenali
