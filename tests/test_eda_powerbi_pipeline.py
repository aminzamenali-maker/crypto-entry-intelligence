"""Offline-Tests fuer Phase 1C-C EDA und Power-BI-Vertrag."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import sqlite3
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from src.eda_powerbi_pipeline import (
    ACCEPTED_COVERAGE_PERCENT,
    CONTRACT_FILES,
    CALENDAR_EXPORT_FIELDS,
    EDA_POLICY_ID,
    EXCLUDED_CALENDAR_HOURS,
    EXPECTED_ASSET_ROWS,
    EXPECTED_DATABASE_SHA256,
    EXPECTED_LOGICAL_FINGERPRINT,
    EXPECTED_TOTAL_ROWS,
    EXPORT_FILES,
    FACT_EXPORT_FIELDS,
    FIGURE_FILES,
    PipelineResult,
    PROHIBITED_FIELD_TOKENS,
    RAW_GAP_HOURS,
    TABLE_FILES,
    _canonical_csv_bytes,
    _current_gate_statuses,
    _complete_calendar_rows,
    _context_age_values,
    _data_contract,
    _derived_values,
    _gap_rows,
    _histogram,
    _independent_logical_fingerprint,
    _measure_contract,
    _parse_utc,
    _publish_bundle,
    _refresh_bundle,
    _same_directory,
    _svg_bar_chart,
    _validate_csv_header,
    _write_dimensions,
    descriptive_statistics,
    main,
    validate_inputs,
)
from src.full_import import IntegrityError, SafetyError, sha256_file
from src.sql_pipeline import DATABASE_RELATIVE_PATH, EXCLUDED_MONTHS


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = PROJECT_ROOT / "config/full_import.json"
DATABASE_PATH = PROJECT_ROOT / DATABASE_RELATIVE_PATH
REPORT_ROOT = PROJECT_ROOT / "reports/eda"
EXPORT_ROOT = PROJECT_ROOT / "data/processed/full_import/powerbi"
CONTRACT_ROOT = PROJECT_ROOT / "powerbi"

FINAL_GATE_STATUSES = {
    "G1-01": "PASS",
    "G1-02": "PASS",
    "G1-03": "PASS_WITH_DOCUMENTED_SOURCE_ANOMALIES",
    "G1-04": "PASS",
    "G1-05": "PASS_WITH_DOCUMENTED_SOURCE_ANOMALIES",
    "G1-06": "PASS_WITH_DOCUMENTED_SOURCE_ANOMALIES",
    "G1-07": "PASS",
    "G1-08": "PASS",
    "G1-09": "PASS",
    "G1-10": "PASS",
    "G1-11": "PASS",
    "G1-12": "PASS",
    "G1-13": "PASS",
}
FINAL_GATE_1_STATUS = "PASS_WITH_DOCUMENTED_SOURCE_ANOMALIES"


def _row(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "symbol": "BTCUSDT", "timeframe": "1h", "segment_id": "SEGMENT_001",
        "timestamp_utc": "2021-01-01T01:00:00.000000Z",
        "open": 100.0, "high": 110.0, "low": 90.0, "close": 105.0,
        "quote_asset_volume": 1000.0, "taker_buy_quote_volume": 400.0,
    }
    row.update(overrides)
    return row


class DescriptiveMathTests(unittest.TestCase):
    def test_statistics_include_all_required_fields(self) -> None:
        result = descriptive_statistics([1.0, 2.0, 3.0, None])
        self.assertEqual(
            set(result), {"count", "mean", "std", "min", "q25", "median", "q75", "max", "null_count"}
        )
        self.assertEqual(result["count"], 3)
        self.assertEqual(result["null_count"], 1)

    def test_quantiles_are_monotonic(self) -> None:
        result = descriptive_statistics([8.0, -2.0, 4.0, 4.0, 100.0])
        self.assertLessEqual(result["min"], result["q25"])
        self.assertLessEqual(result["q25"], result["median"])
        self.assertLessEqual(result["median"], result["q75"])
        self.assertLessEqual(result["q75"], result["max"])

    def test_extreme_values_are_retained(self) -> None:
        result = descriptive_statistics([1.0, 2.0, 1_000_000.0])
        self.assertEqual(result["max"], 1_000_000.0)
        self.assertGreater(result["mean"], 300_000)

    def test_non_finite_value_is_rejected(self) -> None:
        with self.assertRaises(IntegrityError):
            descriptive_statistics([1.0, float("nan")])

    def test_empty_statistics_report_nulls(self) -> None:
        result = descriptive_statistics([None, None])
        self.assertEqual(result["count"], 0)
        self.assertEqual(result["null_count"], 2)
        self.assertIsNone(result["median"])

    def test_explicit_total_count_controls_null_count(self) -> None:
        result = descriptive_statistics([1.0, 2.0], total_count=5)
        self.assertEqual(result["null_count"], 3)

    def test_invalid_total_count_is_rejected(self) -> None:
        with self.assertRaises(IntegrityError):
            descriptive_statistics([1.0, 2.0], total_count=1)

    def test_histogram_keeps_outliers_in_edge_bins(self) -> None:
        counts = _histogram([-1.0, -0.01, 0.01, 1.0], bins=10)
        self.assertEqual(sum(counts), 4)
        self.assertEqual(counts[0], 1)
        self.assertEqual(counts[-1], 1)


class LeakageRuleTests(unittest.TestCase):
    def test_segment_start_return_is_null(self) -> None:
        self.assertIsNone(_derived_values(_row(), None)["close_to_close_return"])

    def test_exact_one_hour_return_is_allowed(self) -> None:
        previous = _row(timestamp_utc="2021-01-01T00:00:00.000000Z", close=100.0)
        current = _row(close=105.0)
        self.assertAlmostEqual(_derived_values(current, previous)["close_to_close_return"], 0.05)

    def test_time_gap_return_is_null(self) -> None:
        previous = _row(timestamp_utc="2021-01-01T00:00:00.000000Z")
        current = _row(timestamp_utc="2021-01-01T02:00:00.000000Z")
        self.assertIsNone(_derived_values(current, previous)["close_to_close_return"])

    def test_segment_change_return_is_null(self) -> None:
        previous = _row(timestamp_utc="2021-01-01T00:00:00.000000Z", segment_id="SEGMENT_001")
        current = _row(segment_id="SEGMENT_002")
        self.assertIsNone(_derived_values(current, previous)["close_to_close_return"])

    def test_asset_change_return_is_null(self) -> None:
        previous = _row(timestamp_utc="2021-01-01T00:00:00.000000Z", symbol="ETHUSDT")
        self.assertIsNone(_derived_values(_row(), previous)["close_to_close_return"])

    def test_exact_four_hour_return_is_allowed(self) -> None:
        previous = _row(timeframe="4h", timestamp_utc="2021-01-01T00:00:00.000000Z", close=100.0)
        current = _row(timeframe="4h", timestamp_utc="2021-01-01T04:00:00.000000Z", close=110.0)
        self.assertAlmostEqual(_derived_values(current, previous)["close_to_close_return"], 0.10)

    def test_taker_share_requires_positive_denominator(self) -> None:
        values = _derived_values(_row(quote_asset_volume=0.0), None)
        self.assertIsNone(values["taker_buy_share"])

    def test_fact_contract_contains_no_prohibited_field(self) -> None:
        for field in FACT_EXPORT_FIELDS:
            self.assertFalse(any(token in field.lower() for token in PROHIBITED_FIELD_TOKENS), field)

    def test_context_age_definitions_are_distinct_and_correct(self) -> None:
        row = {
            "decision_time_utc": "2021-01-02T11:30:00.000000Z",
            "context_source_timestamp_utc": "2021-01-01T00:00:00.000000Z",
            "context_available_from_utc_d1": "2021-01-02T00:00:00.000000Z",
            "context_age_seconds": 35.5 * 3600,
        }
        source_age, d1_age = _context_age_values(row)
        self.assertEqual(source_age, 35.5)
        self.assertEqual(d1_age, 11.5)

    def test_context_age_rejects_sql_definition_conflict(self) -> None:
        row = {
            "decision_time_utc": "2021-01-02T11:30:00.000000Z",
            "context_source_timestamp_utc": "2021-01-01T00:00:00.000000Z",
            "context_available_from_utc_d1": "2021-01-02T00:00:00.000000Z",
            "context_age_seconds": 11.5 * 3600,
        }
        with self.assertRaises(IntegrityError):
            _context_age_values(row)


class SerializationAndFigureTests(unittest.TestCase):
    def test_csv_has_fixed_order_utf8_and_lf(self) -> None:
        payload = _canonical_csv_bytes(("a", "b"), ({"a": "ä", "b": 2},))
        self.assertEqual(payload, "a,b\nä,2\n".encode("utf-8"))
        self.assertNotIn(b"\r\n", payload)

    def test_csv_validator_rejects_wrong_column_order(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "bad.csv"
            path.write_text("b,a\n2,1\n", encoding="utf-8", newline="")
            with self.assertRaises(IntegrityError):
                _validate_csv_header(path, ("a", "b"))

    def test_canonical_utc_requires_fixed_z_format(self) -> None:
        parsed = _parse_utc("2021-01-01T00:00:00.000000Z")
        self.assertEqual(parsed.tzinfo, timezone.utc)
        with self.assertRaises(IntegrityError):
            _parse_utc("2021-01-01T00:00:00+00:00")

    def test_svg_is_deterministic(self) -> None:
        kwargs = dict(title="Titel", subtitle="Untertitel", categories=("A", "B"), series={"X": (1.0, 2.0)}, y_label="Zeilen")
        self.assertEqual(_svg_bar_chart(**kwargs), _svg_bar_chart(**kwargs))

    def test_svg_contains_title_axes_unit_and_source(self) -> None:
        text = _svg_bar_chart("Titel", "Untertitel", ("A",), {"X": (1.0,)}, "Stunden").decode("utf-8")
        for token in ("<title>Titel</title>", "Kalenderjahr / Kategorie", "Stunden", "Quelle:", "Ausgeschlossen:"):
            self.assertIn(token, text)

    def test_gap_table_distinguishes_real_and_conservative_gap(self) -> None:
        rows = {row["category"]: row["value"] for row in _gap_rows()}
        self.assertEqual(rows["actual_source_gap_hours"], RAW_GAP_HOURS)
        self.assertEqual(rows["conservative_excluded_calendar_hours"], EXCLUDED_CALENDAR_HOURS)
        self.assertEqual(rows["accepted_temporal_coverage"], ACCEPTED_COVERAGE_PERCENT)

    def test_powerbi_contract_defines_only_single_direction_one_to_many(self) -> None:
        text = _data_contract({name: 1 for name in EXPORT_FILES}).decode("utf-8")
        self.assertEqual(text.count("| 1:n | Dimension zu Fakt | Ja |"), 4)
        self.assertIn("Bidirektionale Beziehungen sind nicht erlaubt", text)

    def test_measure_contract_contains_required_descriptive_measures(self) -> None:
        text = _measure_contract().decode("utf-8")
        for name in ("Marktzeilen", "Fruehester Zeitpunkt", "Median Basisvolumen", "Durchschnitt Kontextalter", "Akzeptierte Abdeckung", "Segmentanzahl"):
            self.assertIn(name, text)
        self.assertNotIn("Sharpe", text)

    def test_coverage_measures_separate_global_and_calendar_filter_semantics(self) -> None:
        text = _measure_contract().decode("utf-8")
        self.assertIn("Globale akzeptierte Scope-Abdeckung", text)
        self.assertIn("REMOVEFILTERS(dim_calendar)", text)
        self.assertIn("Akzeptierte Abdeckung im Kalenderfilter", text)
        self.assertIn("COUNTROWS(dim_calendar)", text)
        self.assertIn("Assetfilter", text)


class CurrentGateStatusContractTests(unittest.TestCase):
    @staticmethod
    def report_text(
        statuses: dict[str, str] | None = None,
        overall: str = FINAL_GATE_1_STATUS,
        extra_rows: tuple[str, ...] = (),
        extra_overall_lines: tuple[str, ...] = (),
    ) -> str:
        values = FINAL_GATE_STATUSES if statuses is None else statuses
        rows = [
            f"| {gate_id} | Kriterium | Evidenz | {status} |"
            for gate_id, status in values.items()
        ]
        return "\n".join((
            "# Gate-1-Abnahmevertrag",
            "",
            f"**Gesamtstatus Gate 1: `{overall}`.**",
            *extra_overall_lines,
            "",
            "## Gate-1-Teilmatrix",
            "",
            "| ID | Kriterium | Reale Evidenz | Status |",
            "|---|---|---|---|",
            *rows,
            *extra_rows,
            "",
            "## Folgebereich",
            "",
        ))

    def assert_rejected_without_mutation(self, text: str) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            report = root / "gate.md"
            sentinel = root / "sentinel.bin"
            report.write_text(text, encoding="utf-8", newline="")
            sentinel.write_bytes(b"unchanged")
            before = {
                path.name: (path.read_bytes(), path.stat().st_mtime_ns)
                for path in root.iterdir()
            }
            with self.assertRaises(IntegrityError):
                _current_gate_statuses(report)
            after = {
                path.name: (path.read_bytes(), path.stat().st_mtime_ns)
                for path in root.iterdir()
            }
            self.assertEqual(after, before)

    def test_final_current_gate_matrix_is_accepted_exactly(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            report = Path(temp) / "gate.md"
            report.write_text(self.report_text(), encoding="utf-8", newline="")
            actual = _current_gate_statuses(report)
        self.assertEqual({key: actual[key] for key in FINAL_GATE_STATUSES}, FINAL_GATE_STATUSES)
        self.assertEqual(actual["Gate 1"], FINAL_GATE_1_STATUS)

    def test_g1_13_regression_to_not_evaluated_is_rejected(self) -> None:
        statuses = dict(FINAL_GATE_STATUSES)
        statuses["G1-13"] = "NOT_EVALUATED"
        self.assert_rejected_without_mutation(self.report_text(statuses))

    def test_anomaly_criterion_wrong_status_is_rejected(self) -> None:
        for wrong_status in ("PASS", "NOT_EVALUATED"):
            with self.subTest(wrong_status=wrong_status):
                statuses = dict(FINAL_GATE_STATUSES)
                statuses["G1-03"] = wrong_status
                self.assert_rejected_without_mutation(self.report_text(statuses))

    def test_missing_gate_criterion_is_rejected(self) -> None:
        statuses = dict(FINAL_GATE_STATUSES)
        del statuses["G1-09"]
        self.assert_rejected_without_mutation(self.report_text(statuses))

    def test_duplicate_or_unknown_gate_row_is_rejected(self) -> None:
        rows = (
            "| G1-13 | Doppelt | Evidenz | PASS |",
            "| G1-14 | Unbekannt | Evidenz | PASS |",
        )
        for row in rows:
            with self.subTest(row=row):
                self.assert_rejected_without_mutation(self.report_text(extra_rows=(row,)))

    def test_wrong_missing_or_duplicate_overall_status_is_rejected(self) -> None:
        cases = (
            self.report_text(overall="NOT_EVALUATED"),
            self.report_text(overall="PASS"),
            self.report_text(extra_overall_lines=(f"**Gesamtstatus Gate 1: `{FINAL_GATE_1_STATUS}`.**",)),
            self.report_text().replace(f"**Gesamtstatus Gate 1: `{FINAL_GATE_1_STATUS}`.**\n", ""),
        )
        for text in cases:
            with self.subTest(text=text[:80]):
                self.assert_rejected_without_mutation(text)


class ConsoleStatusTests(unittest.TestCase):
    def test_console_uses_validated_current_gate_matrix(self) -> None:
        result = PipelineResult(
            status="CACHED_VALID",
            report_root=REPORT_ROOT,
            export_root=EXPORT_ROOT,
            database_sha256=EXPECTED_DATABASE_SHA256,
            logical_fingerprint=EXPECTED_LOGICAL_FINGERPRINT,
            fact_rows=EXPECTED_TOTAL_ROWS,
            export_hashes={},
            gate_statuses={**FINAL_GATE_STATUSES, "Gate 1": FINAL_GATE_1_STATUS},
        )
        with patch("src.eda_powerbi_pipeline.run_pipeline", return_value=result):
            with patch("sys.stdout", new_callable=io.StringIO) as output:
                exit_code = main(["--config", "config/full_import.json"])
        self.assertEqual(exit_code, 0)
        self.assertIn("G1-13: PASS\n", output.getvalue())
        self.assertIn(f"Gate 1: {FINAL_GATE_1_STATUS}\n", output.getvalue())
        self.assertNotIn("G1-13: NOT_EVALUATED", output.getvalue())


class AtomicAndCacheTests(unittest.TestCase):
    def _directories(self, root: Path) -> tuple[Path, Path, Path, Path, Path, Path]:
        temporary = root / "temporary"
        report = temporary / "report"
        contract = temporary / "contract"
        export = temporary / "export"
        targets = root / "targets"
        report_target = targets / "report"
        contract_target = targets / "contract"
        export_target = targets / "export"
        for path in (report, contract, export):
            path.mkdir(parents=True)
        for name in CONTRACT_FILES:
            (contract / name).write_text(name, encoding="utf-8")
        (report / "known.txt").write_text("report", encoding="utf-8")
        (export / "known.txt").write_text("export", encoding="utf-8")
        return report, contract, export, report_target, contract_target, export_target

    def test_publish_creates_expected_groups(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            paths = self._directories(Path(temp))
            _publish_bundle(*paths)
            self.assertTrue(paths[3].is_dir())
            self.assertTrue(paths[5].is_dir())
            self.assertEqual({path.name for path in paths[4].iterdir()}, set(CONTRACT_FILES))

    def test_publish_never_overwrites_existing_target(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            paths = self._directories(Path(temp))
            paths[3].mkdir(parents=True)
            sentinel = paths[3] / "sentinel.txt"
            sentinel.write_text("keep", encoding="utf-8")
            with self.assertRaises(SafetyError):
                _publish_bundle(*paths)
            self.assertEqual(sentinel.read_text(encoding="utf-8"), "keep")
            self.assertFalse(paths[5].exists())

    def test_publish_rolls_back_on_contract_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            paths = self._directories(Path(temp))
            with patch("src.eda_powerbi_pipeline.os.link", side_effect=OSError("synthetic")):
                with self.assertRaises(OSError):
                    _publish_bundle(*paths)
            self.assertFalse(paths[3].exists())
            self.assertFalse(paths[5].exists())

    def test_same_directory_accepts_byteidentical_tree(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            left, right = Path(temp) / "left", Path(temp) / "right"
            (left / "nested").mkdir(parents=True)
            (right / "nested").mkdir(parents=True)
            (left / "nested/a.txt").write_bytes(b"same")
            (right / "nested/a.txt").write_bytes(b"same")
            self.assertTrue(_same_directory(left, right))

    def test_same_directory_rejects_changed_file_without_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            left, right = Path(temp) / "left", Path(temp) / "right"
            left.mkdir(); right.mkdir()
            (left / "a.txt").write_bytes(b"expected")
            target = right / "a.txt"
            target.write_bytes(b"changed")
            before = sha256_file(target)
            self.assertFalse(_same_directory(left, right))
            self.assertEqual(sha256_file(target), before)

    def test_same_directory_rejects_unknown_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            left, right = Path(temp) / "left", Path(temp) / "right"
            left.mkdir(); right.mkdir()
            (left / "a.txt").write_bytes(b"same")
            (right / "a.txt").write_bytes(b"same")
            (right / "unknown.txt").write_bytes(b"keep")
            self.assertFalse(_same_directory(left, right))

    def _complete_refresh_directories(self, root: Path) -> tuple[Path, Path, Path, Path, Path, Path, Path]:
        temp_report, temp_contract, temp_export = root / "new_report", root / "new_contract", root / "new_export"
        report_target, contract_target, export_target = root / "report", root / "contract", root / "export"
        report_files = {
            "PHASE1C_EDA_REPORT.md", "EDA_DATA_DICTIONARY.md", "eda_quality_summary.json", "eda_manifest.csv",
            *(f"tables/{name}" for name in TABLE_FILES),
            *(f"figures/{name}" for name in FIGURE_FILES),
        }
        for directory in (temp_report, temp_contract, temp_export, report_target, contract_target, export_target):
            directory.mkdir(parents=True)
        for relative in report_files:
            for base, payload in ((temp_report, b"new"), (report_target, b"old")):
                path = base / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(payload)
        for name in CONTRACT_FILES:
            (temp_contract / name).write_bytes(b"new")
            (contract_target / name).write_bytes(b"old")
        for name in EXPORT_FILES:
            (temp_export / name).write_bytes(b"new")
            (export_target / name).write_bytes(b"old")
        return temp_report, temp_contract, temp_export, report_target, contract_target, export_target, root / "backup"

    def test_controlled_refresh_replaces_only_complete_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            paths = self._complete_refresh_directories(Path(temp))
            _refresh_bundle(*paths)
            self.assertEqual((paths[3] / "PHASE1C_EDA_REPORT.md").read_bytes(), b"new")
            self.assertEqual((paths[4] / CONTRACT_FILES[0]).read_bytes(), b"new")
            self.assertEqual((paths[5] / EXPORT_FILES[0]).read_bytes(), b"new")
            self.assertFalse(paths[6].exists())

    def test_controlled_refresh_rolls_back_on_contract_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            paths = self._complete_refresh_directories(Path(temp))
            with patch("src.eda_powerbi_pipeline.os.link", side_effect=OSError("synthetic")):
                with self.assertRaises(OSError):
                    _refresh_bundle(*paths)
            self.assertEqual((paths[3] / "PHASE1C_EDA_REPORT.md").read_bytes(), b"old")
            self.assertEqual((paths[4] / CONTRACT_FILES[0]).read_bytes(), b"old")
            self.assertEqual((paths[5] / EXPORT_FILES[0]).read_bytes(), b"old")


class SyntheticDimensionTests(unittest.TestCase):
    @staticmethod
    def accepted_dates() -> set[str]:
        current = date(2021, 1, 1)
        end = date(2025, 12, 31)
        values: set[str] = set()
        while current <= end:
            if current.strftime("%Y-%m") not in EXCLUDED_MONTHS:
                values.add(current.isoformat())
            current += timedelta(days=1)
        return values

    def test_complete_calendar_has_exact_scope_boundaries_and_continuity(self) -> None:
        rows = _complete_calendar_rows(self.accepted_dates())
        self.assertEqual(len(rows), 1_826)
        self.assertEqual(rows[0]["calendar_date"], "2021-01-01")
        self.assertEqual(rows[-1]["calendar_date"], "2025-12-31")
        self.assertEqual(len({row["date_key"] for row in rows}), 1_826)
        self.assertTrue(all(
            date.fromisoformat(current["calendar_date"]) - date.fromisoformat(previous["calendar_date"]) == timedelta(days=1)
            for previous, current in zip(rows, rows[1:])
        ))

    def test_complete_calendar_marks_exactly_212_days_in_seven_excluded_months(self) -> None:
        rows = _complete_calendar_rows(self.accepted_dates())
        excluded = [row for row in rows if row["is_excluded_month"] == 1]
        self.assertEqual(len(excluded), 212)
        self.assertEqual({row["calendar_date"][:7] for row in excluded}, set(EXCLUDED_MONTHS))
        self.assertTrue(all(row["is_accepted_date"] == 0 for row in excluded))

    def test_dimensions_have_unique_keys_and_stable_order(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            connection = sqlite3.connect(":memory:")
            connection.execute("CREATE TABLE dim_asset(asset_key INTEGER,symbol TEXT,base_asset TEXT,quote_asset TEXT)")
            connection.execute("CREATE TABLE dim_segment(segment_key INTEGER,segment_id TEXT,start_month TEXT,end_month TEXT,valid_month_count INTEGER,boundary_description TEXT)")
            connection.executemany("INSERT INTO dim_asset VALUES(?,?,?,?)", [(1,"BTCUSDT","BTC","USDT"),(2,"ETHUSDT","ETH","USDT")])
            connection.execute("INSERT INTO dim_segment VALUES(1,'SEGMENT_001','2021-01','2021-01',1,'start')")
            root = Path(temp)
            counts = _write_dimensions(connection, root, self.accepted_dates())
            connection.close()
            self.assertEqual(counts["dim_calendar.csv"], 1_826)
            with (root / "dim_calendar.csv").open(encoding="utf-8", newline="") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(tuple(rows[0]), CALENDAR_EXPORT_FIELDS)
            self.assertEqual(rows[0]["date_key"], "20210101")
            self.assertEqual(rows[-1]["date_key"], "20251231")
            self.assertEqual(len({row["date_key"] for row in rows}), 1_826)


class RealInputValidationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.database_hash_before = sha256_file(DATABASE_PATH)
        cls.evidence = validate_inputs(PROJECT_ROOT, CONFIG_PATH)
        cls.database_hash_after = sha256_file(DATABASE_PATH)

    def test_confirmed_database_sha(self) -> None:
        self.assertEqual(self.evidence.database_sha256, EXPECTED_DATABASE_SHA256)

    def test_confirmed_independent_logical_fingerprint(self) -> None:
        self.assertEqual(self.evidence.logical_fingerprint, EXPECTED_LOGICAL_FINGERPRINT)

    def test_sql_cache_is_read_only_cached_valid(self) -> None:
        self.assertEqual(self.evidence.sql_cache_status, "CACHED_VALID")
        self.assertEqual(self.database_hash_before, self.database_hash_after)

    def test_confirmed_sql_row_counts(self) -> None:
        self.assertEqual(self.evidence.quality["global_row_counts"], {"fact_total": 145260, "1h": 116208, "4h": 29052})
        self.assertEqual(self.evidence.quality["by_asset_timeframe"], {f"{symbol}|{timeframe}": count for (symbol,timeframe),count in EXPECTED_ASSET_ROWS.items()})

    def test_current_gate_matrix_is_final_and_independently_accepted(self) -> None:
        self.assertEqual(self.evidence.gate_statuses["G1-10"], "PASS")
        self.assertEqual(self.evidence.gate_statuses["G1-12"], "PASS")
        self.assertEqual(self.evidence.gate_statuses["G1-13"], "PASS")
        self.assertEqual(self.evidence.gate_statuses["Gate 1"], FINAL_GATE_1_STATUS)

    def test_no_future_context_or_key_violation(self) -> None:
        self.assertEqual(self.evidence.quality["future_context_violation_count"], 0)
        self.assertEqual(self.evidence.quality["primary_key_duplicate_count"], 0)
        self.assertEqual(self.evidence.quality["foreign_key_violation_count"], 0)

    def test_real_database_has_no_excluded_month(self) -> None:
        connection = sqlite3.connect(f"file:{DATABASE_PATH.as_posix()}?mode=ro&immutable=1", uri=True)
        try:
            count = connection.execute(
                "SELECT COUNT(*) FROM fact_market_context WHERE substr(timestamp_utc,1,7) IN (" + ",".join("?" for _ in EXCLUDED_MONTHS) + ")",
                tuple(sorted(EXCLUDED_MONTHS)),
            ).fetchone()[0]
        finally:
            connection.close()
        self.assertEqual(count, 0)


@unittest.skipUnless(REPORT_ROOT.is_dir() and EXPORT_ROOT.is_dir(), "Reale Phase-1C-C-Ausgaben entstehen erst im kontrollierten Lauf.")
class RealOutputContractTests(unittest.TestCase):
    def test_all_required_eda_artifacts_exist(self) -> None:
        for name in TABLE_FILES:
            self.assertTrue((REPORT_ROOT / "tables" / name).is_file())
        for name in FIGURE_FILES:
            self.assertTrue((REPORT_ROOT / "figures" / name).is_file())

    def test_powerbi_fact_rows_match_sql(self) -> None:
        self.assertEqual(_validate_csv_header(EXPORT_ROOT / "fact_market_context_eda.csv", FACT_EXPORT_FIELDS), EXPECTED_TOTAL_ROWS)

    def test_export_manifest_matches_all_files(self) -> None:
        with (CONTRACT_ROOT / "powerbi_model_manifest.csv").open(encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle))
        self.assertEqual([row["filename"] for row in rows], list(EXPORT_FILES))
        for row in rows:
            self.assertEqual(row["sha256"], sha256_file(EXPORT_ROOT / row["filename"]))
            self.assertEqual(row["source_database_logical_fingerprint"], EXPECTED_LOGICAL_FINGERPRINT)

    def test_dimension_keys_are_unique(self) -> None:
        for filename, field in (("dim_asset.csv","asset_key"),("dim_segment.csv","segment_key"),("dim_calendar.csv","date_key"),("dim_timeframe.csv","timeframe_key")):
            with (EXPORT_ROOT / filename).open(encoding="utf-8", newline="") as handle:
                values = [row[field] for row in csv.DictReader(handle)]
            self.assertEqual(len(values), len(set(values)))

    def test_calendar_is_complete_and_exclusions_remain_visible(self) -> None:
        with (EXPORT_ROOT / "dim_calendar.csv").open(encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle))
        self.assertEqual(len(rows), 1_826)
        self.assertEqual(rows[0]["calendar_date"], "2021-01-01")
        self.assertEqual(rows[-1]["calendar_date"], "2025-12-31")
        self.assertEqual(len({row["date_key"] for row in rows}), 1_826)
        excluded = [row for row in rows if row["is_excluded_month"] == "1"]
        self.assertEqual(len(excluded), 212)
        self.assertEqual({row["calendar_date"][:7] for row in excluded}, set(EXCLUDED_MONTHS))
        self.assertTrue(all(row["is_accepted_date"] == "0" for row in excluded))
        self.assertTrue(all(
            date.fromisoformat(current["calendar_date"]) - date.fromisoformat(previous["calendar_date"]) == timedelta(days=1)
            for previous, current in zip(rows, rows[1:])
        ))

    def test_no_orphan_foreign_keys(self) -> None:
        dimension_keys = {}
        for filename, field in (("dim_asset.csv","asset_key"),("dim_segment.csv","segment_key"),("dim_calendar.csv","date_key"),("dim_timeframe.csv","timeframe_key")):
            with (EXPORT_ROOT / filename).open(encoding="utf-8", newline="") as handle:
                dimension_keys[field] = {row[field] for row in csv.DictReader(handle)}
        with (EXPORT_ROOT / "fact_market_context_eda.csv").open(encoding="utf-8", newline="") as handle:
            for row in csv.DictReader(handle):
                for field, values in dimension_keys.items():
                    self.assertIn(row[field], values)

    def test_no_excluded_month_or_future_context_in_export(self) -> None:
        with (EXPORT_ROOT / "fact_market_context_eda.csv").open(encoding="utf-8", newline="") as handle:
            for row in csv.DictReader(handle):
                self.assertNotIn(row["timestamp_utc"][:7], EXCLUDED_MONTHS)
                self.assertLessEqual(row["context_available_from_utc_d1"], row["decision_time_utc"])

    def test_context_age_fields_match_timestamps_and_control_ranges(self) -> None:
        controls: dict[tuple[str, str], list[float]] = {
            (metric, timeframe): []
            for metric in ("context_age_hours", "context_age_since_d1_hours")
            for timeframe in ("1h", "4h")
        }
        with (EXPORT_ROOT / "fact_market_context_eda.csv").open(encoding="utf-8", newline="") as handle:
            for row in csv.DictReader(handle):
                source_age = (
                    _parse_utc(row["decision_time_utc"]) - _parse_utc(row["context_source_timestamp_utc"])
                ).total_seconds() / 3600.0
                d1_age = (
                    _parse_utc(row["decision_time_utc"]) - _parse_utc(row["context_available_from_utc_d1"])
                ).total_seconds() / 3600.0
                self.assertAlmostEqual(float(row["context_age_hours"]), source_age)
                self.assertAlmostEqual(float(row["context_age_since_d1_hours"]), d1_age)
                controls[("context_age_hours", row["timeframe"])].append(source_age)
                controls[("context_age_since_d1_hours", row["timeframe"])].append(d1_age)
        expected = {
            ("context_age_hours", "1h"): (24.0, 35.5, 47.0),
            ("context_age_hours", "4h"): (24.0, 34.0, 44.0),
            ("context_age_since_d1_hours", "1h"): (0.0, 11.5, 23.0),
            ("context_age_since_d1_hours", "4h"): (0.0, 10.0, 20.0),
        }
        for key, (minimum, median, maximum) in expected.items():
            stats = descriptive_statistics(controls[key])
            self.assertEqual((stats["min"], stats["median"], stats["max"]), (minimum, median, maximum))

    def test_measure_contract_documents_global_and_filter_dependent_coverage(self) -> None:
        text = (CONTRACT_ROOT / "POWER_BI_MEASURES.md").read_text(encoding="utf-8")
        self.assertIn("Globale akzeptierte Scope-Abdeckung", text)
        self.assertIn("REMOVEFILTERS(dim_calendar)", text)
        self.assertIn("Akzeptierte Abdeckung im Kalenderfilter", text)
        self.assertIn("Assetfilter", text)

    def test_segment_starts_and_gaps_have_null_return(self) -> None:
        previous = None
        null_count = 0
        with (EXPORT_ROOT / "fact_market_context_eda.csv").open(encoding="utf-8", newline="") as handle:
            for row in csv.DictReader(handle):
                new_group = previous is None or any(previous[key] != row[key] for key in ("symbol","timeframe","segment_id"))
                if new_group:
                    self.assertEqual(row["close_to_close_return"], "")
                    null_count += 1
                previous = row
        self.assertEqual(null_count, 30)

    def test_quality_summary_preserves_historical_build_time_gate_status(self) -> None:
        payload = json.loads((REPORT_ROOT / "eda_quality_summary.json").read_text(encoding="utf-8"))
        self.assertEqual(payload["policy_id"], EDA_POLICY_ID)
        self.assertEqual(payload["gate_status"], {"G1-13": "NOT_EVALUATED", "Gate 1": "NOT_EVALUATED"})

    def test_figures_are_svg_with_required_metadata(self) -> None:
        for name in FIGURE_FILES:
            text = (REPORT_ROOT / "figures" / name).read_text(encoding="utf-8")
            self.assertIn("<title>", text)
            self.assertIn("Quelle:", text)
            self.assertIn("Ausgeschlossen:", text)

    def test_report_bundle_has_no_part_file(self) -> None:
        self.assertFalse(any(path.name.endswith(".part") for path in PROJECT_ROOT.rglob("*")))


if __name__ == "__main__":
    unittest.main()
