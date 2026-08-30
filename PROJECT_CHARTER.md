# Projektauftrag

## 1. Identität

- Teilnehmer: Mohammad Amin Zamen Ali
- Projekttyp: eigenständiges Einzelprojekt
- Deutscher Arbeitstitel: **Datenbasierte Analyse, Validierung und Visualisierung von Einstiegssignalen im Kryptowährungshandel**
- Portfolio-Untertitel: **Crypto Entry Intelligence - Multi-Source Analytics, Backtesting und Power BI**
- Fachgebiet: Data Analytics / Finance / Kryptowährungsmärkte

## 2. Ausgangslage

Das Projekt untersucht historische Kryptowährungsdaten, um klar definierte Einstiegssignale datenbasiert zu prüfen. Dabei geht es nicht darum, im Nachhinein eine möglichst gute Strategie zu finden. Entscheidend ist, ob die Ergebnisse auch unter realistischen Kosten, getrennten Zeiträumen und ohne Nutzung zukünftiger Informationen bestehen bleiben.

Dafür werden historische Marktdaten mit zusätzlichem BTC-Marktkontext verbunden. Die Signale werden mit transparenten Baselines verglichen und über getrennte Entwicklungs-, Validierungs- und Testzeiträume bewertet.

## 3. Datenquellen

Für die finale Core-Analyse wurden zwei Datenquellen verwendet:

| Quelle | Verwendung im Projekt |
|---|---|
| Binance Public Data | Historische Spot-OHLCV-Daten für `BTCUSDT`, `ETHUSDT` und `SOLUSDT`. Primärer Zeitrahmen ist `1h`; `4h` wird aus vollständigen 1h-Kerzen abgeleitet. |
| Coin Metrics Community API | Täglicher BTC-Kontext mit `PriceUSD`, `CapMrktCurUSD`, `TxCnt` und `AdrActCnt`. Der Kontext wird mit zeitlich kontrollierten D+1- und D+2-Regeln verbunden. |

Im Datenpilot wurden zusätzlich Coinbase Exchange Candles, CoinGecko Keyless API und FRED geprüft. Diese Quellen wurden nicht als Core-Datenquellen der finalen Analyse verwendet. Die Auswahl, Qualitätsprüfungen und dokumentierten Quellenanomalien stehen ausführlich in `DATA_SOURCES.md` und den zugehörigen Qualitätsberichten.

## 4. Zentrale Forschungsfrage

**Unter welchen historischen Marktbedingungen liefern klar definierte Kombinationen aus Preis-, Trend-, Volumen-, Volatilitäts- und Marktkontextsignalen einen messbaren zusätzlichen Informationswert für Krypto-Einstiege gegenüber einfachen Baselines - nach Kosten und ohne Nutzung zukünftiger Informationen?**

## 5. Teilfragen

1. Verbessern kombinierte Signale die Trefferquote und den Erwartungswert gegenüber einem Einzelsignal oder einer einfachen Baseline?
2. Wie stabil sind die Ergebnisse über verschiedene Zeiträume, Marktphasen und ausgewählte Kryptowährungen?
3. Welchen zusätzlichen Nutzen liefern relatives Volumen und übergeordneter Marktkontext?
4. Wie stark verändern Gebühren, Slippage und Funding die Ergebnisse?
5. Kann ein einfaches, zeitlich validiertes Machine-Learning-Modell die Wahrscheinlichkeit eines günstigen Ausgangs besser einschätzen als eine transparente Regelstrategie?
6. Welche Merkmale tragen tatsächlich zur Entscheidung bei und welche erzeugen nur unnötige Komplexität?

## 6. Vorläufige Hypothesen

- **H1:** Eine regelbasierte Kombination mehrerer bestätigter Signale erzielt außerhalb der Entwicklungsperiode bessere risikoadjustierte Kennzahlen als transparente Baselines.
- **H2:** Relatives Volumen liefert nur in Verbindung mit Trend- und Volatilitätskontext einen stabilen Zusatznutzen.
- **H3:** Ergebnisse unterscheiden sich deutlich zwischen Marktregimen; eine einzige unveränderte Regel ist nicht in allen Phasen gleich geeignet.
- **H4:** Realistische Handelskosten reduzieren scheinbare Bruttoergebnisse wesentlich.
- **H5:** Ein einfaches erklärbares Modell kann komplexere Modelle bei zeitlicher Out-of-Sample-Prüfung erreichen oder übertreffen.

Diese Hypothesen dürfen widerlegt werden. Auch ein negatives Ergebnis ist fachlich verwertbar, wenn Methode und Ergebnis nachvollziehbar dokumentiert sind.

**Finaler Scope-Hinweis:** Machine Learning wurde für den finalen Core bewusst nicht begonnen. H5 bleibt deshalb `NOT_EVALUATED`. Diese Entscheidung ist in `DECISIONS.md` dokumentiert.

## 7. Umfang

### Im Core enthalten

- mindestens zwei, geplant drei Werkzeuge: Python, SQL und Power BI
- mehr als 10.000 Zeilen in der finalen Analysetabelle
- mindestens zwei sinnvoll integrierte Datenquellen
- historische OHLCV- und Kontextdaten
- Datenqualitätsprüfung und reproduzierbare Pipeline
- explorative Datenanalyse
- transparente Baselines
- regelbasierte Signale und Backtesting
- Gebühren- und Slippage-Szenarien
- zeitliche Out-of-Sample-Validierung
- Power-BI-Dashboard
- 20- bis 30-minütige Präsentation
- vollständige Abgabedateien und GitHub-fähige Dokumentation

### Advanced, nur nach bestandenem Core

- Machine-Learning-Klassifikation mit Time-Series-Splits
- erklärbare Merkmalsbeiträge
- Marktregime-Analyse
- robuste Sensitivitäts- und Parameterstabilitätsanalyse

Machine Learning wurde im finalen Projekt nicht begonnen. Die übrigen Punkte bleiben als ursprüngliche Erweiterungsoptionen dokumentiert.

### Stretch, nur bei ausreichender Zeit

- Cross-Exchange- oder zusätzliche Asset-Validierung
- Walk-forward-Optimierung mit streng getrennten Zeitfenstern
- Monte-Carlo- oder Bootstrap-Unsicherheitsanalyse
- interaktive Szenarioansicht über den Pflichtumfang hinaus

## 8. Nicht im Umfang

- Live-Trading oder automatische Orderausführung
- Gewinnversprechen oder persönliche Anlageberatung
- Hochfrequenzhandel
- Deep Learning ohne klaren Zusatznutzen
- Optimierung auf eine einzelne beeindruckende Gewinnkurve
- unkontrollierte Übernahme proprietärer Indikatorlogik

## 9. Geplante Ergebnisse

- reproduzierbare Python-Pipeline
- SQL-Schema, Views und Analyseabfragen
- finale Analyse- und Power-BI-Tabellen
- Power-BI-Dashboard
- PowerPoint-Präsentation
- Tests und Qualitätsberichte
- Quellen- und Methodennachweis
- GitHub-Portfolio mit README und verständlicher Projektgeschichte

## 10. Erfolgskriterien

Erfolg bedeutet nicht zwingend Profitabilität. Das Projekt ist erfolgreich, wenn:

- alle offiziellen DSP-Anforderungen nachweisbar erfüllt sind,
- der Datensatz und die Ergebnisse reproduzierbar sind,
- Datenlecks und unrealistische Backtest-Annahmen kontrolliert werden,
- Baselines und Varianten fair verglichen werden,
- Grenzen verständlich erklärt werden,
- Dashboard und Präsentation die Forschungsfrage klar beantworten,
- die zentralen Schritte, Entscheidungen und Kennzahlen anhand der Projektdateien nachvollziehbar sind.
