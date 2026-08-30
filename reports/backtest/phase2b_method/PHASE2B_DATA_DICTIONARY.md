# Phase 2B – Datenwörterbuch

## Featuretabellen

Die vier Dateien `features_{1h|4h}_{primary_d1|sensitivity_d2}.csv` besitzen
die Körnung Asset × Zeitrahmen × Segment × Kerzenbeginn × Kontextvariante.
Development und Validation sind enthalten; der finale Test ist ausgeschlossen.

Schlüssel- und Herkunftsfelder sind `split`, `symbol`, `timeframe`,
`timestamp_utc`, `decision_time_utc`, `segment_id`, `context_variant`,
`context_source_timestamp_utc` und `context_available_from_utc`. Es folgen die
handelbaren OHLCV-Felder, exakt 25 vorregistrierte Merkmale und fünf boolesche
Signalfelder. Ein leerer Merkmalswert ist nur während der dokumentierten
Warm-up-Zeit, bei null Standardabweichung, null Volumen oder beim ausschließlich
für 1h definierten Taker-Anteil zulässig.

## Trade-Tabelle

`trades.csv` enthält je tatsächlich ausgeführtem historischen Trade und
Kostenszenario eine Zeile. Wichtige Felder:

- `signal_time_utc`: Informationszeitpunkt nach Schluss der Signalkerze;
- `entry_time_utc`, `entry_open`: nächstes handelbares Open;
- `exit_time_utc`, `exit_open`: Open nach vollständigem Horizont;
- `gross_return`, `net_return`: vor beziehungsweise nach Kosten;
- `maximum_adverse_excursion`, `maximum_favorable_excursion`: beobachtete
  Spanne innerhalb des Haltefensters ohne Intrabar-Reihenfolge;
- `positive_net_outcome`: eins nur bei `net_return > 0`;
- `exposure_hours`: planmäßige Haltedauer.

## Berichte

- `feature_quality_summary.json`: Zeilen, Nullwerte und Leakage-Schutz;
- `signal_frequency_summary.csv`: Signale, ausführbare und abgelehnte Fälle;
- `results_summary.csv`: 720 vorregistrierte Zellen je ausgewertetem Split;
- `aggregate_results_summary.csv`: deskriptiver Asset-Pool ohne gemeinsame
  Kapitalkurve;
- `baseline_comparison.csv`: die drei Baselines;
- `context_variant_comparison.csv`: D+1 gegen unabhängig verbundenes D+2;
- `execution_quality_summary.json`: Ausführung, Kosten und Grenzen;
- `split_segment_quality.json`: Split-, Schlüssel-, Masken- und Segmentprüfung;
- `sealed_final_test.json`: ausschließlich Nullzähler für jede Testauswertung;
- `input_output_hashes.json` und `phase2b_manifest.csv`: Reproduzierbarkeit.

`input_output_hashes.json` enthält außerdem die Policy-ID
`phase2b_fsum_float17_provenance_v2`, `.17g`, die SMA-Regel mit `math.fsum`,
Hashes von Phase-2B-Konfiguration und Pipeline-Code, den Phase-2A-Vertrag,
alle geschützten Eingaben sowie `SEALED_NOT_EVALUATED` für den finalen Test.
