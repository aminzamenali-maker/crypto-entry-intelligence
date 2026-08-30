# Finaler Test 2024–2025 - abschließende Interpretation

## Methodischer Status

Der finale Test wurde nach Gate-2-PASS mit unveränderter Methode genau einmal ausgeführt. Es gab keine Parameteranpassung nach Kenntnis der Ergebnisse. Die folgenden Aussagen sind deskriptive historische Evidenz, keine Gewinnzusage und keine Handlungsempfehlung.

## Zentrale Antwort auf die Forschungsfrage

Die fünf transparenten Einstiegssignale zeigten im finalen Zeitraum vor Kosten im Mittel kleine positive Bewegungen. Diese Bruttoeffekte waren jedoch nicht groß genug, um selbst die niedrige Kostenannahme von 20 Basispunkten stabil zu überwinden. Im Hauptszenario mit 30 Basispunkten waren die gepoolten durchschnittlichen Nettoergebnisse auf 1h und 4h negativ. Kein Signal-Horizont war in Development, Validation und finalem Test gleichzeitig netto positiv.

Damit liefert der Core **keinen belastbaren Nachweis für einen stabilen zusätzlichen Netto-Informationswert der getesteten Einzelregeln nach Kosten**. Das ist ein gültiges negatives Projektergebnis.

## Vergleich über die drei Zeitabschnitte

Der vorregistrierte ungefähr vierstündige Haupthorizont entspricht vier 1h-Bars beziehungsweise einer 4h-Bar. Kontext ist `primary_d1`, Kostenfall ist 30 bp.

| Zeitraum | TF | Bars | Trades | Durchschnitt brutto | Durchschnitt netto | Trefferquote |
|---|---|---:|---:|---:|---:|---:|
| Development 2021-2022 | 1h | 4 | 8.919 | +0,0344 % | -0,2652 % | 39,52 % |
| Development 2021-2022 | 4h | 1 | 4.967 | -0,0459 % | -0,3453 % | 39,58 % |
| Validation 2023 | 1h | 4 | 4.722 | +0,1059 % | -0,1940 % | 33,91 % |
| Validation 2023 | 4h | 1 | 2.623 | +0,1385 % | -0,1615 % | 34,88 % |
| Finaler Test 2024–2025 | 1h | 4 | 11.275 | +0,0143 % | -0,2853 % | 37,63 % |
| Finaler Test 2024–2025 | 4h | 1 | 6.412 | +0,0327 % | -0,2669 % | 38,62 % |

Im finalen Test lagen die durchschnittlichen Bruttobewegungen bei +0,0143 % auf 1h und +0,0327 % auf 4h. Nach 30 bp Kosten wurden daraus -0,2853 % beziehungsweise -0,2669 %.

## Kostensensitivität im finalen Test

| TF | Kosten | Trades | Durchschnitt brutto | Durchschnitt netto | Trefferquote |
|---|---|---:|---:|---:|---:|
| 1h | 20 bp | 11.275 | +0,0143 % | -0,1855 % | 41,43 % |
| 1h | 30 bp | 11.275 | +0,0143 % | -0,2853 % | 37,63 % |
| 1h | 50 bp | 11.275 | +0,0143 % | -0,4845 % | 30,94 % |
| 4h | 20 bp | 6.412 | +0,0327 % | -0,1672 % | 42,39 % |
| 4h | 30 bp | 6.412 | +0,0327 % | -0,2669 % | 38,62 % |
| 4h | 50 bp | 6.412 | +0,0327 % | -0,4662 % | 31,66 % |

Schon bei 20 bp blieb der gepoolte Nettodurchschnitt negativ. Der monotone Rückgang von 20 über 30 auf 50 bp bestätigt, dass Kosten ein zentraler Ergebnistreiber sind.

## Stabilität der einzelnen Signal-Horizonte

Es wurden 30 Signal-Horizont-Kombinationen betrachtet: fünf Signale mal drei Horizonte mal zwei Zeitrahmen.

- Development netto positiv: 0/30
- Validation netto positiv: 13/30
- Finaler Test netto positiv: 3/30
- In allen drei Zeitabschnitten netto positiv: 0/30

Die drei im finalen Test positiven Zellen waren:

| TF | Signal | Bars | Trades | Development netto | Validation netto | Final netto |
|---|---|---:|---:|---:|---:|---:|
| 4h | `trend_sma20_cross_above_sma50` | 6 | 156 | -0,8406 % | -0,4398 % | +0,1897 % |
| 4h | `mean_reversion_rsi14_below_30` | 6 | 165 | -0,2194 % | +0,0787 % | +0,0240 % |
| 4h | `trend_sma20_cross_above_sma50` | 3 | 156 | -0,6216 % | -0,3476 % | +0,0112 % |

Diese Zellen dürfen nicht nachträglich als neue Strategie ausgewählt werden. Insbesondere die beiden SMA-Crossover-Zellen waren in Development und Validation negativ. Sie zeigen Periodenabhängigkeit, aber keine stabile Regel.

## Kontext D+1 gegen D+2

Alle 360 Kontextvergleichszeilen hatten identische Tradezahlen und identische durchschnittliche Nettorenditen. Das ist erwartbar, weil die fünf vorregistrierten Signalbedingungen ausschließlich Marktdaten verwenden. Die Kontextfeatures wurden leakage-sicher bereitgestellt, aber im Core nicht als Signalbedingung eingesetzt.

Daraus folgt: Der Zusatznutzen von relativem Volumen und übergeordnetem Kontext wurde mit diesen fünf Einzelregeln **nicht direkt getestet**. Er bleibt eine klar benannte Grenze beziehungsweise eine optionale spätere Erweiterung, darf aber vor der Abgabe nicht den fertigen Core gefährden.

## Baselines im finalen Test

- `always_flat`: null Trades und Ergebnis null.
- Periodischer Einstieg am ungefähr vierstündigen Horizont: 315 Trades je Zeitrahmen, durchschnittlich -0,3927 % netto.
- Segment-Buy-and-Hold: drei Trades je Zeitrahmen, durchschnittlich +53,1047 % netto auf 1h und +52,7142 % auf 4h.

Buy-and-Hold hielt jedes Asset über den nahezu vollständigen finalen Marktabschnitt. Deshalb ist sein hoher Wert nicht direkt mit vielen kurzfristigen Signaltrades vergleichbar. Er zeigt vor allem, dass der Zeitraum 2024–2025 einen starken positiven Markttrend enthielt, während die kurzfristigen Einstiegsregeln diesen Trend nach Kosten nicht stabil in einen Vorteil umsetzen konnten.

## Bewertung der Hypothesen

- **H1:** nicht unterstützt und im Core nur teilweise operationalisiert. Es wurden fünf Einzelregeln, keine feste Mehrsignal-Kombination, getestet. Kein Signal-Horizont war über alle drei Zeitabschnitte netto positiv.
- **H2:** nicht direkt bewertet. Relatives Volumen und Kontext sind Features, aber keine Bedingungen der fünf Core-Signale.
- **H3:** deskriptiv unterstützt. Die Zahl positiver Zellen sank von 13/30 in Validation auf 3/30 im finalen Test; Development hatte 0/30. Ergebnisse sind deutlich periodenabhängig.
- **H4:** klar unterstützt. Steigende Kosten verschlechtern die Nettowerte systematisch; bereits 20 bp reichen im Pool für negative Durchschnittswerte.
- **H5:** nicht bewertet. Machine Learning wurde nicht begonnen und ist für den fertigen Core nicht erforderlich.

## Grenzen

- historische Stichprobe, keine Zukunftsgarantie
- Long/Flat, keine Shorts und kein Hebel
- keine gemeinsame Portfoliokapitalkurve ohne Kapitalallokationsvertrag
- keine p-Werte; Ergebnisse sind als nicht unabhängige Zeitreihen deskriptiv behandelt
- konservativ ausgeschlossene Quellenmonate bleiben bestehen
- kein direkter Test einer Mehrsignal-Kombination oder eines Kontextfilters
- Buy-and-Hold besitzt einen grundsätzlich anderen Zeithorizont

## Schlussfolgerung für Dashboard und Präsentation

Die stärkste und ehrlichste Projektbotschaft lautet:

> Die Datenpipeline, der Leakage-Schutz und die einmalige Out-of-Sample-Prüfung funktionieren reproduzierbar. Die untersuchten transparenten Einstiegssignale erzeugten im finalen Zeitraum kleine positive Bruttobewegungen, konnten realistische Kosten aber nicht stabil überwinden. Einzelne positive 4h-Zellen waren nicht über Development, Validation und Test stabil. Der wichtigste nachgewiesene Einflussfaktor sind die Handelskosten, nicht ein robust profitables Einzelsignal.

Diese Aussage beantwortet die Forschungsfrage, ohne Ergebnisse zu übertreiben oder nachträglich eine günstige Zelle auszuwählen.
