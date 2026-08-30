from __future__ import annotations

import copy
import csv
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from src.backtest_pipeline import SIGNAL_IDS, load_json
from src.final_test_once import (
    FINAL_SPLIT,
    FINAL_STATUS_PREPARED,
    FinalTestError,
    _atomic_json,
    _bundle_snapshot,
    _create_start_state,
    _manifest_rows,
    build_final_evaluation,
    preflight,
    run_once,
    validate_final_config,
)


ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config/final_test_once.json"
PHASE2A_PATH = ROOT / "config/backtest.json"


class FinalTestPreparationContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = load_json(CONFIG_PATH)

    def test_real_config_is_prepared_but_not_executed(self) -> None:
        self.assertEqual(FINAL_STATUS_PREPARED, self.config["status"])
        self.assertEqual(FINAL_SPLIT, self.config["evaluation"]["split"])
        self.assertFalse(self.config["restrictions"]["network_access"])
        self.assertFalse(self.config["restrictions"]["parameter_optimization"])
        self.assertFalse(self.config["restrictions"]["automatic_retry_after_start"])
        self.assertFalse(self.config["evaluation"]["allow_post_result_parameter_change"])

    def test_real_config_binds_exact_method_commit_and_files(self) -> None:
        self.assertEqual("648a74198a97e4e57d839a05db2af55fd1229190", self.config["approved_method_commit"])
        self.assertEqual(12, len(self.config["method"]["protected_files"]))
        _, summary = validate_final_config(self.config, ROOT, check_git=False)
        self.assertEqual("FINAL_TEST_PREFLIGHT_VALID", summary["status"])
        self.assertEqual(17, summary["phase2b_manifest"]["manifest_entries"])
        self.assertEqual(18, summary["phase2b_manifest"]["bundle_files"])

    def test_read_only_preflight_calculates_no_final_result(self) -> None:
        before = {
            relative: (ROOT / relative).exists()
            for relative in (
                self.config["output"]["data_root"],
                self.config["output"]["report_root"],
                self.config["output"]["state_path"],
                self.config["output"]["receipt_path"],
            )
        }
        summary = preflight(CONFIG_PATH, check_git=False)
        after = {relative: (ROOT / relative).exists() for relative in before}
        self.assertEqual(before, after)
        self.assertEqual(0, summary["writes"])
        self.assertEqual(0, summary["features_evaluated"])
        self.assertEqual(0, summary["trades_evaluated"])
        self.assertEqual(0, summary["metrics_evaluated"])

    def test_changed_method_hash_fails_closed(self) -> None:
        changed = copy.deepcopy(self.config)
        changed["method"]["protected_files"]["src/backtest_pipeline.py"] = "0" * 64
        with self.assertRaisesRegex(FinalTestError, "protected method file changed"):
            validate_final_config(changed, ROOT, check_git=False)

    def test_changed_final_scope_fails_closed(self) -> None:
        changed = copy.deepcopy(self.config)
        changed["evaluation"]["expected_rows"]["total"] += 1
        with self.assertRaisesRegex(FinalTestError, "wrong final-test row contract"):
            validate_final_config(changed, ROOT, check_git=False)

    def test_existing_start_state_blocks_preflight(self) -> None:
        changed = copy.deepcopy(self.config)
        with tempfile.TemporaryDirectory(dir=ROOT) as temporary:
            rel = Path(temporary).relative_to(ROOT).as_posix()
            changed["output"] = {
                "data_root": f"{rel}/data",
                "report_root": f"{rel}/reports",
                "state_path": f"{rel}/state.json",
                "receipt_path": f"{rel}/receipt.json",
            }
            (ROOT / changed["output"]["state_path"]).write_text("{}\n", encoding="utf-8")
            with self.assertRaisesRegex(FinalTestError, "start state already exists"):
                validate_final_config(changed, ROOT, check_git=False)

    def test_wrong_confirmation_is_rejected_before_state_creation(self) -> None:
        state = ROOT / self.config["output"]["state_path"]
        self.assertFalse(state.exists())
        with self.assertRaisesRegex(FinalTestError, "confirmation token"):
            run_once(CONFIG_PATH, "WRONG")
        self.assertFalse(state.exists())

    def test_source_contains_no_network_client_import(self) -> None:
        source = (ROOT / "src/final_test_once.py").read_text(encoding="utf-8")
        for forbidden in ("import requests", "from requests", "urllib.request", "httpx", "socket"):
            self.assertNotIn(forbidden, source)


class FinalEvaluationUnitTests(unittest.TestCase):
    @staticmethod
    def _row(index: int, split: str = FINAL_SPLIT) -> dict[str, object]:
        timestamp = datetime(2024, 1, 1, tzinfo=timezone.utc) + timedelta(hours=index)
        row: dict[str, object] = {
            "split": split,
            "symbol": "BTCUSDT",
            "timeframe": "1h",
            "context_variant": "primary_d1",
            "segment_id": "SEGMENT_TEST",
            "timestamp": timestamp,
            "timestamp_utc": timestamp.strftime("%Y-%m-%dT%H:%M:%S.000000Z"),
            "decision_time_utc": (timestamp + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%S.000000Z"),
            "open": 100.0 + index,
            "high": 101.0 + index,
            "low": 99.0 + index,
            "close": 100.5 + index,
        }
        row.update({signal: False for signal in SIGNAL_IDS})
        return row

    def test_final_evaluation_materializes_all_registered_cells(self) -> None:
        phase2a = load_json(PHASE2A_PATH)
        groups = {("synthetic", "primary_d1"): [self._row(0), self._row(1)]}
        trades, frequencies, results, aggregates = build_final_evaluation(groups, phase2a)
        self.assertEqual(720, len(results))
        self.assertEqual(240, len(aggregates))
        self.assertTrue(all(row["split"] == FINAL_SPLIT for row in results))
        self.assertTrue(all(row["split"] == FINAL_SPLIT for row in aggregates))
        self.assertTrue(all(row["split"] == FINAL_SPLIT for row in trades))
        self.assertTrue(all(row["split"] == FINAL_SPLIT for row in frequencies))

    def test_non_final_feature_group_is_rejected(self) -> None:
        phase2a = load_json(PHASE2A_PATH)
        groups = {("synthetic", "primary_d1"): [self._row(0, "validation")]}
        with self.assertRaisesRegex(FinalTestError, "non-final row"):
            build_final_evaluation(groups, phase2a)


class FinalPublicationSafetyTests(unittest.TestCase):
    def test_start_state_is_exclusive(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "state.json"
            _create_start_state(path, {"status": "started"})
            with self.assertRaisesRegex(FinalTestError, "automatic retry forbidden"):
                _create_start_state(path, {"status": "again"})

    def test_atomic_json_leaves_no_temporary_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "receipt.json"
            _atomic_json(path, {"status": "ok"})
            self.assertEqual({"status": "ok"}, json.loads(path.read_text(encoding="utf-8")))
            self.assertEqual([], list(Path(temporary).glob("*.tmp")))
            self.assertEqual([], list(Path(temporary).glob(".*.tmp")))

    def test_bundle_snapshot_is_stable_and_content_sensitive(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            data = root / "data"
            reports = root / "reports"
            data.mkdir()
            reports.mkdir()
            (data / "a.csv").write_text("x\n1\n", encoding="utf-8")
            (reports / "b.json").write_text("{}\n", encoding="utf-8")
            first = _bundle_snapshot(data, reports)
            second = _bundle_snapshot(data, reports)
            self.assertEqual(first, second)
            (reports / "b.json").write_text('{"x":1}\n', encoding="utf-8")
            self.assertNotEqual(first, _bundle_snapshot(data, reports))

    def test_manifest_rows_are_complete_and_exclude_manifest_itself(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            data = root / "data"
            reports = root / "reports"
            data.mkdir()
            reports.mkdir()
            (data / "a.csv").write_text("x\n1\n2\n", encoding="utf-8")
            (reports / "b.json").write_text("{}\n", encoding="utf-8")
            (reports / "final_test_manifest.csv").write_text("ignored\n", encoding="utf-8")
            rows = _manifest_rows(data, reports, {"data_root": "data/out", "report_root": "reports/out"})
            self.assertEqual(2, len(rows))
            csv_row = next(row for row in rows if row["artifact_path"].endswith("a.csv"))
            self.assertEqual(2, csv_row["row_count"])
            self.assertFalse(any(row["artifact_path"].endswith("final_test_manifest.csv") for row in rows))


if __name__ == "__main__":
    unittest.main()
