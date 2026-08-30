"""Offline-Regressionstests fuer Phase 1C-B."""

from __future__ import annotations

import csv
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from src.full_import import IntegrityError, sha256_file
from src.processed_pipeline import MANIFEST_FIELDS, PROCESSED_1H_FIELDS, read_strict_csv
from src.sql_pipeline import (
    ASSETS,
    EXPECTED_ASSET_ROWS,
    INSERT_FACT_SQL,
    SEGMENTS,
    SqlBuildResult,
    ValidatedSqlInputs,
    _validate_manifest,
    _validate_rows,
    build_sql_model,
    inspect_database,
    logical_database_fingerprint,
    validate_inputs,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class RealSqlModelTests(unittest.TestCase):
    """Reale Eingaben und vorhandene Datenbank ausschliesslich read-only."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.validated = validate_inputs(PROJECT_ROOT, PROJECT_ROOT / "config/full_import.json")
        cls.database = PROJECT_ROOT / "data/processed/full_import/sql/crypto_entry_intelligence.sqlite"
        cls.logical_fingerprint, cls.build_quality = inspect_database(cls.database)
        cls.connection = sqlite3.connect(f"file:{cls.database.as_posix()}?mode=ro", uri=True)
        cls.connection.execute("PRAGMA foreign_keys=ON")
        cls.source_hashes_after = {
            "market_context_1h.csv": sha256_file(PROJECT_ROOT / "data/processed/full_import/market_context_1h.csv"),
            "market_context_4h.csv": sha256_file(PROJECT_ROOT / "data/processed/full_import/market_context_4h.csv"),
        }

    @classmethod
    def tearDownClass(cls) -> None:
        cls.connection.close()

    def test_fact_has_exact_total_and_timeframe_counts(self) -> None:
        self.assertEqual(145_260, self.connection.execute("SELECT COUNT(*) FROM fact_market_context").fetchone()[0])
        self.assertEqual({"1h": 116_208, "4h": 29_052}, dict(self.connection.execute(
            "SELECT timeframe, COUNT(*) FROM fact_market_context GROUP BY timeframe"
        )))

    def test_sql_schema_contains_required_tables_and_business_key(self) -> None:
        objects = {row[0]: row[1] for row in self.connection.execute(
            "SELECT name,type FROM sqlite_schema WHERE name IN ('dim_asset','dim_segment','fact_market_context')"
        )}
        self.assertEqual({"dim_asset": "table", "dim_segment": "table", "fact_market_context": "table"}, objects)
        indexes = list(self.connection.execute("PRAGMA index_list(fact_market_context)"))
        self.assertTrue(any(row[2] == 1 for row in indexes))

    def test_each_asset_timeframe_count_is_exact(self) -> None:
        actual = {(row[0], row[1]): row[2] for row in self.connection.execute(
            "SELECT symbol, timeframe, COUNT(*) FROM fact_market_context GROUP BY symbol, timeframe"
        )}
        self.assertEqual(EXPECTED_ASSET_ROWS, actual)

    def test_primary_key_grain_is_unique(self) -> None:
        duplicates = self.connection.execute(
            "SELECT COUNT(*) FROM (SELECT symbol,timeframe,timestamp_utc FROM fact_market_context "
            "GROUP BY symbol,timeframe,timestamp_utc HAVING COUNT(*)>1)"
        ).fetchone()[0]
        self.assertEqual(0, duplicates)

    def test_foreign_keys_are_valid_and_enabled_by_loader(self) -> None:
        self.assertEqual(1, self.connection.execute("PRAGMA foreign_keys").fetchone()[0])
        self.assertEqual(0, self.build_quality["foreign_key_violation_count"])
        self.assertEqual([], list(self.connection.execute("PRAGMA foreign_key_check")))

    def test_dimensions_are_exact(self) -> None:
        self.assertEqual(list(ASSETS), list(self.connection.execute("SELECT * FROM dim_asset ORDER BY asset_key")))
        self.assertEqual(list(SEGMENTS), list(self.connection.execute("SELECT * FROM dim_segment ORDER BY segment_key")))

    def test_one_hour_taker_fields_are_complete(self) -> None:
        self.assertEqual(0, self.connection.execute(
            "SELECT COUNT(*) FROM fact_market_context WHERE timeframe='1h' "
            "AND (taker_buy_base_volume IS NULL OR taker_buy_quote_volume IS NULL OR constituent_rows IS NOT NULL)"
        ).fetchone()[0])

    def test_four_hour_constituent_contract_is_exact(self) -> None:
        self.assertEqual(0, self.connection.execute(
            "SELECT COUNT(*) FROM fact_market_context WHERE timeframe='4h' "
            "AND (constituent_rows<>4 OR taker_buy_base_volume IS NOT NULL OR taker_buy_quote_volume IS NOT NULL)"
        ).fetchone()[0])

    def test_status_domains_are_exact(self) -> None:
        self.assertEqual(
            [("accepted_phase1b_complete_month", "matched_d1_asof", 145_260)],
            list(self.connection.execute(
                "SELECT market_quality_status,context_match_status,COUNT(*) FROM fact_market_context "
                "GROUP BY market_quality_status,context_match_status"
            )),
        )

    def test_all_timestamps_are_canonical_utc_z(self) -> None:
        fields = (
            "timestamp_utc", "close_time_utc", "decision_time_utc",
            "context_source_timestamp_utc", "context_available_from_utc_d1",
            "context_available_from_utc_d2",
        )
        for field in fields:
            count = self.connection.execute(
                f"SELECT COUNT(*) FROM fact_market_context WHERE length({field})<>27 OR substr({field},-1)<>'Z'"
            ).fetchone()[0]
            self.assertEqual(0, count, field)

    def test_no_future_context_exists(self) -> None:
        self.assertEqual(0, self.connection.execute(
            "SELECT COUNT(*) FROM fact_market_context WHERE context_available_from_utc_d1>decision_time_utc"
        ).fetchone()[0])

    def test_excluded_months_are_absent(self) -> None:
        excluded = ("2021-02", "2021-03", "2021-04", "2021-08", "2021-09", "2021-12", "2023-03")
        placeholders = ",".join("?" for _ in excluded)
        self.assertEqual(0, self.connection.execute(
            f"SELECT COUNT(*) FROM fact_market_context WHERE substr(timestamp_utc,1,7) IN ({placeholders})", excluded
        ).fetchone()[0])

    def test_coverage_views_have_expected_grain(self) -> None:
        self.assertEqual(116_208, self.connection.execute("SELECT COUNT(*) FROM vw_market_context_1h").fetchone()[0])
        self.assertEqual(29_052, self.connection.execute("SELECT COUNT(*) FROM vw_market_context_4h").fetchone()[0])
        self.assertEqual(6, self.connection.execute("SELECT COUNT(*) FROM vw_asset_timeframe_coverage").fetchone()[0])
        self.assertEqual(30, self.connection.execute("SELECT COUNT(*) FROM vw_segment_coverage").fetchone()[0])
        self.assertEqual(6, self.connection.execute("SELECT COUNT(*) FROM vw_context_freshness").fetchone()[0])

    def test_data_quality_view_passes_every_check(self) -> None:
        checks = list(self.connection.execute(
            "SELECT check_name,violation_count,check_status FROM vw_data_quality_checks ORDER BY check_name"
        ))
        self.assertEqual(5, len(checks))
        self.assertTrue(all(count == 0 and status == "PASS" for _, count, status in checks))

    def test_database_contains_no_feature_signal_or_position_columns(self) -> None:
        columns = {row[1].lower() for row in self.connection.execute("PRAGMA table_info(fact_market_context)")}
        forbidden = {"return", "returns", "indicator", "signal", "position", "pnl", "target"}
        self.assertTrue(columns.isdisjoint(forbidden))

    def test_logical_fingerprint_is_stable_for_same_connection(self) -> None:
        again, counts = logical_database_fingerprint(self.connection)
        self.assertEqual(self.logical_fingerprint, again)
        self.assertEqual(145_260, counts["fact_market_context"])

    def test_source_csv_hashes_are_unchanged(self) -> None:
        self.assertEqual(
            {
                "market_context_1h.csv": "7468ce970381e34fc60a8227fb1594dee5435e88f5521f06ed82bfa15f5ce805",
                "market_context_4h.csv": "ab2ff44340b295d140db9fa1cb81cf5690dc7d78a44392599381c1d2e7edc91b",
            },
            self.source_hashes_after,
        )


class SqlInputFailureTests(unittest.TestCase):
    @staticmethod
    def _synthetic_1h_row() -> dict[str, str]:
        values = {field: "1" for field in PROCESSED_1H_FIELDS}
        values.update({
            "symbol": "BTCUSDT", "timeframe": "1h",
            "timestamp_utc": "2021-01-01T00:00:00.000000Z",
            "close_time_utc": "2021-01-01T00:59:59.999000Z",
            "decision_time_utc": "2021-01-01T01:00:00.000000Z",
            "segment_id": "SEGMENT_001", "open": "1", "high": "1", "low": "1", "close": "1",
            "market_source": "binance_public_data", "market_timestamp_unit": "ms",
            "market_quality_status": "accepted_phase1b_complete_month",
            "context_match_status": "matched_d1_asof", "context_source": "coin_metrics_community_api",
            "context_asset": "btc", "context_source_timestamp_utc": "2020-12-31T00:00:00.000000Z",
            "context_available_from_utc_d1": "2021-01-01T00:00:00.000000Z",
            "context_available_from_utc_d2": "2021-01-02T00:00:00.000000Z",
        })
        return values

    def test_schema_mismatch_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bad.csv"
            path.write_text("wrong,header\n1,2\n", encoding="utf-8")
            with self.assertRaises(IntegrityError):
                read_strict_csv(path, PROCESSED_1H_FIELDS)

    def test_duplicate_input_key_is_rejected(self) -> None:
        row = self._synthetic_1h_row()
        rows = [row] * 116_208
        with self.assertRaises(IntegrityError):
            _validate_rows(rows, "1h")

    def test_future_context_input_is_rejected(self) -> None:
        rows = [self._synthetic_1h_row()] * 116_208
        rows[0] = dict(rows[0])
        rows[0]["context_available_from_utc_d1"] = "2099-01-01T00:00:00.000000Z"
        with self.assertRaises(IntegrityError):
            _validate_rows(rows, "1h")

    def test_manifest_hash_mismatch_stops_validation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            artifact = root / "synthetic/market_context_1h.csv"
            artifact.parent.mkdir(parents=True)
            artifact.write_text("synthetic", encoding="utf-8")
            manifest = root / "reports/processed/processed_manifest.csv"
            manifest.parent.mkdir(parents=True)
            with manifest.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=MANIFEST_FIELDS, lineterminator="\n")
                writer.writeheader()
                writer.writerow({
                    "artifact_id": "market_context_1h", "artifact_path": "synthetic/market_context_1h.csv",
                    "artifact_type": "processed_table", "schema_id": "synthetic_schema", "row_count": "1",
                    "sha256": "0" * 64, "source_checkpoint": "synthetic", "source_checkpoint_sha256": "0" * 64,
                    "source_checkpoint_generation_id": "1", "source_checkpoint_run_id": "synthetic",
                    "phase1c_policy_id": "synthetic", "phase1c_policy_fingerprint": "0" * 64,
                })
            expected = {"market_context_1h": ("synthetic/market_context_1h.csv", "synthetic_schema", 1, "0" * 64)}
            with mock.patch("src.sql_pipeline.EXPECTED_SOURCE_ARTIFACTS", expected):
                with self.assertRaises(IntegrityError):
                    _validate_manifest(root)


def _fixture_hashes(root: Path) -> dict[str, str]:
    paths = {
        "market_context_1h.csv": root / "data/processed/full_import/market_context_1h.csv",
        "market_context_4h.csv": root / "data/processed/full_import/market_context_4h.csv",
        "processed_manifest.csv": root / "reports/processed/processed_manifest.csv",
        "join_quality_summary.json": root / "reports/processed/join_quality_summary.json",
        "PHASE1C_DATA_DICTIONARY.md": root / "reports/processed/PHASE1C_DATA_DICTIONARY.md",
        "PHASE1C_QUALITY_REPORT.md": root / "reports/processed/PHASE1C_QUALITY_REPORT.md",
    }
    for path in paths.values():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(path.name, encoding="utf-8")
    return {name: sha256_file(path) for name, path in paths.items()}


def _empty_database(path: Path, project_root: Path, _: ValidatedSqlInputs):
    connection = sqlite3.connect(path)
    try:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.executescript((project_root / "sql/001_schema.sql").read_text(encoding="utf-8"))
        connection.executescript((project_root / "sql/002_views.sql").read_text(encoding="utf-8"))
        connection.executemany("INSERT INTO dim_asset VALUES (?,?,?,?)", ASSETS)
        connection.executemany("INSERT INTO dim_segment VALUES (?,?,?,?,?,?)", SEGMENTS)
        connection.execute("INSERT INTO pipeline_metadata VALUES ('fixture','valid')")
        connection.commit()
    finally:
        connection.close()
    return inspect_database(path)


def _synthetic_fact_row() -> list[object]:
    return [
        1, 1, "BTCUSDT", "1h", "2021-01-01T00:00:00.000000Z",
        "2021-01-01T00:59:59.999000Z", "2021-01-01T01:00:00.000000Z",
        1, "SEGMENT_001", 100.0, 101.0, 99.0, 100.5, 10.0, 1005.0,
        20, 5.0, 502.5, None, "binance_public_data", "ms",
        "accepted_phase1b_complete_month", "matched_d1_asof",
        "coin_metrics_community_api", "btc", "2020-12-31T00:00:00.000000Z",
        "2021-01-01T00:00:00.000000Z", "2021-01-02T00:00:00.000000Z",
        29000.0, 500000000000.0, 300000.0, 1000000.0, 90000,
    ]


class SyntheticConstraintTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temp.name)
        (cls.root / "sql").mkdir()
        shutil.copy2(PROJECT_ROOT / "sql/001_schema.sql", cls.root / "sql/001_schema.sql")
        shutil.copy2(PROJECT_ROOT / "sql/002_views.sql", cls.root / "sql/002_views.sql")
        cls.database = cls.root / "synthetic.sqlite"
        _empty_database(cls.database, cls.root, ValidatedSqlInputs([], [], {}, ""))
        cls.connection = sqlite3.connect(cls.database)
        cls.connection.execute("PRAGMA foreign_keys=ON")
        cls.connection.execute(INSERT_FACT_SQL, _synthetic_fact_row())
        cls.connection.commit()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.connection.close()
        cls.temp.cleanup()

    def test_sql_check_rejects_duplicate_business_key(self) -> None:
        row = _synthetic_fact_row()
        row[0] = 2
        with self.assertRaises(sqlite3.IntegrityError):
            self.connection.execute(INSERT_FACT_SQL, row)
        self.connection.rollback()

    def test_sql_check_rejects_bad_foreign_key(self) -> None:
        row = _synthetic_fact_row()
        row[0], row[1], row[4] = 3, 99, "2021-01-01T02:00:00.000000Z"
        with self.assertRaises(sqlite3.IntegrityError):
            self.connection.execute(INSERT_FACT_SQL, row)
        self.connection.rollback()

    def test_sql_check_rejects_future_context(self) -> None:
        row = _synthetic_fact_row()
        row[0], row[4] = 4, "2021-01-01T02:00:00.000000Z"
        row[26], row[27] = "2021-01-03T00:00:00.000000Z", "2021-01-04T00:00:00.000000Z"
        with self.assertRaises(sqlite3.IntegrityError):
            self.connection.execute(INSERT_FACT_SQL, row)
        self.connection.rollback()

    def test_sql_checks_reject_bad_timeframe_and_status(self) -> None:
        for offset, (column_index, bad_value) in enumerate(((3, "2h"), (21, "unverified"), (22, "unmatched")), 5):
            row = _synthetic_fact_row()
            row[0] = offset
            row[4] = f"2021-01-01T{offset:02d}:00:00.000000Z"
            row[column_index] = bad_value
            with self.assertRaises(sqlite3.IntegrityError):
                self.connection.execute(INSERT_FACT_SQL, row)
            self.connection.rollback()


class PublicationSafetyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "sql").mkdir()
        shutil.copy2(PROJECT_ROOT / "sql/001_schema.sql", self.root / "sql/001_schema.sql")
        shutil.copy2(PROJECT_ROOT / "sql/002_views.sql", self.root / "sql/002_views.sql")
        self.hashes = _fixture_hashes(self.root)
        self.validated = ValidatedSqlInputs([], [], self.hashes, self.hashes["processed_manifest.csv"])

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _build(self) -> SqlBuildResult:
        with mock.patch("src.sql_pipeline.validate_inputs", return_value=self.validated), mock.patch(
            "src.sql_pipeline._validate_cached_database"
        ), mock.patch("src.sql_pipeline._create_database", side_effect=_empty_database):
            return build_sql_model(self.root, self.root / "config/full_import.json")

    def _cached_call(self) -> SqlBuildResult:
        with mock.patch("src.sql_pipeline.validate_inputs", return_value=self.validated), mock.patch(
            "src.sql_pipeline._validate_cached_database"
        ), mock.patch("src.sql_pipeline._create_database") as create_database:
            try:
                return build_sql_model(self.root, self.root / "config/full_import.json")
            finally:
                create_database.assert_not_called()

    def _output_snapshot(self) -> dict[str, tuple[str, int, int]]:
        database = self.root / "data/processed/full_import/sql/crypto_entry_intelligence.sqlite"
        paths = [database] if database.exists() else []
        report_dir = self.root / "reports/sql"
        if report_dir.exists():
            paths.extend(sorted((path for path in report_dir.iterdir() if path.is_file()), key=lambda path: path.name))
        return {
            path.relative_to(self.root).as_posix(): (sha256_file(path), path.stat().st_size, path.stat().st_mtime_ns)
            for path in paths
        }

    def _assert_cache_rejects_without_mutation(self, mutate) -> None:
        self._build()
        mutate()
        before = self._output_snapshot()
        with self.assertRaises(IntegrityError):
            self._cached_call()
        self.assertEqual(before, self._output_snapshot())
        self.assertEqual([], list(self.root.rglob("*.part")))

    def _append_report_bytes(self, name: str, content: bytes = b"\nMANIPULATED") -> None:
        path = self.root / "reports/sql" / name
        path.write_bytes(path.read_bytes() + content)

    def test_atomic_publication_leaves_no_part_files(self) -> None:
        result = self._build()
        self.assertEqual("CREATED", result.status)
        self.assertEqual([], list(self.root.rglob("*.part")))

    def test_logically_identical_cache_is_reused_without_file_changes(self) -> None:
        self._build()
        before = self._output_snapshot()
        second = self._cached_call()
        self.assertEqual("CACHED_VALID", second.status)
        self.assertEqual(before, self._output_snapshot())

    def test_mismatched_existing_database_is_not_overwritten(self) -> None:
        first = self._build()
        connection = sqlite3.connect(first.database_path)
        connection.execute("UPDATE pipeline_metadata SET metadata_value='changed' WHERE metadata_key='fixture'")
        connection.commit()
        connection.close()
        before = sha256_file(first.database_path)
        with self.assertRaises(IntegrityError):
            self._build()
        self.assertEqual(before, sha256_file(first.database_path))

    def test_changed_quality_json_is_rejected_without_mutation(self) -> None:
        self._assert_cache_rejects_without_mutation(
            lambda: self._append_report_bytes("sql_quality_summary.json")
        )

    def test_changed_manifest_is_rejected_without_mutation(self) -> None:
        self._assert_cache_rejects_without_mutation(
            lambda: self._append_report_bytes("sql_manifest.csv")
        )

    def test_changed_markdown_report_is_rejected_without_mutation(self) -> None:
        self._assert_cache_rejects_without_mutation(
            lambda: self._append_report_bytes("PHASE1C_SQL_REPORT.md")
        )

    def test_changed_data_dictionary_is_rejected_without_mutation(self) -> None:
        self._assert_cache_rejects_without_mutation(
            lambda: self._append_report_bytes("SQL_DATA_DICTIONARY.md")
        )

    def test_additional_fifth_report_file_is_rejected_without_mutation(self) -> None:
        self._assert_cache_rejects_without_mutation(
            lambda: (self.root / "reports/sql/unexpected.txt").write_text("synthetic", encoding="utf-8")
        )

    def test_missing_report_file_is_rejected_without_mutation(self) -> None:
        self._assert_cache_rejects_without_mutation(
            lambda: (self.root / "reports/sql/SQL_DATA_DICTIONARY.md").unlink()
        )

    def test_reordered_manifest_rows_are_rejected_without_mutation(self) -> None:
        def mutate() -> None:
            path = self.root / "reports/sql/sql_manifest.csv"
            lines = path.read_bytes().splitlines(keepends=True)
            path.write_bytes(b"".join([lines[0], lines[2], lines[1], *lines[3:]]))
        self._assert_cache_rejects_without_mutation(mutate)

    def test_additional_manifest_row_is_rejected_without_mutation(self) -> None:
        def mutate() -> None:
            path = self.root / "reports/sql/sql_manifest.csv"
            lines = path.read_bytes().splitlines(keepends=True)
            path.write_bytes(b"".join([*lines, lines[1]]))
        self._assert_cache_rejects_without_mutation(mutate)

    def test_manipulated_database_hash_in_manifest_is_rejected_without_mutation(self) -> None:
        def mutate() -> None:
            path = self.root / "reports/sql/sql_manifest.csv"
            database_hash = sha256_file(self.root / "data/processed/full_import/sql/crypto_entry_intelligence.sqlite")
            path.write_text(path.read_text(encoding="utf-8").replace(database_hash, "0" * 64), encoding="utf-8", newline="")
        self._assert_cache_rejects_without_mutation(mutate)

    def test_manipulated_logical_fingerprint_is_rejected_without_mutation(self) -> None:
        def mutate() -> None:
            path = self.root / "reports/sql/sql_quality_summary.json"
            fingerprint, _ = inspect_database(self.root / "data/processed/full_import/sql/crypto_entry_intelligence.sqlite")
            path.write_text(path.read_text(encoding="utf-8").replace(fingerprint, "f" * 64), encoding="utf-8", newline="")
        self._assert_cache_rejects_without_mutation(mutate)

    def test_changed_sql_script_hash_is_rejected_without_mutation(self) -> None:
        def mutate() -> None:
            path = self.root / "sql/001_schema.sql"
            path.write_bytes(path.read_bytes() + b"\n-- synthetic change\n")
        self._assert_cache_rejects_without_mutation(mutate)

    def test_build_error_leaves_no_database_checkpoint_or_reports(self) -> None:
        def failing(path: Path, *_args):
            path.write_bytes(b"partial")
            raise IntegrityError("synthetic")
        with mock.patch("src.sql_pipeline.validate_inputs", return_value=self.validated), mock.patch(
            "src.sql_pipeline._create_database", side_effect=failing
        ):
            with self.assertRaises(IntegrityError):
                build_sql_model(self.root, self.root / "config/full_import.json")
        self.assertFalse((self.root / "data/processed/full_import/sql/crypto_entry_intelligence.sqlite").exists())
        self.assertFalse((self.root / "reports/sql").exists())
        self.assertEqual([], list(self.root.rglob("*.part")))

    def test_input_validation_error_occurs_before_output_mutation(self) -> None:
        before = {name: sha256_file(self.root / path) for name, path in {
            "one": "data/processed/full_import/market_context_1h.csv",
            "four": "data/processed/full_import/market_context_4h.csv",
        }.items()}
        with mock.patch("src.sql_pipeline.validate_inputs", side_effect=IntegrityError("synthetic")):
            with self.assertRaises(IntegrityError):
                build_sql_model(self.root, self.root / "config/full_import.json")
        self.assertEqual(before, {name: sha256_file(self.root / path) for name, path in {
            "one": "data/processed/full_import/market_context_1h.csv",
            "four": "data/processed/full_import/market_context_4h.csv",
        }.items()})
        self.assertFalse((self.root / "reports/sql").exists())


if __name__ == "__main__":
    unittest.main()
