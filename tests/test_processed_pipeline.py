from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from pathlib import Path
from unittest.mock import patch

from src.full_import import IntegrityError, sha256_file, write_generated_file_cached
from src.processed_pipeline import (
    PROCESSED_1H_FIELDS,
    PROCESSED_4H_FIELDS,
    ContextRow,
    _duplicate_count,
    asof_join_d1,
    build_join_summary,
    build_month_segments,
    build_phase1c_outputs,
    decision_time_from_close,
    validate_phase1b_inputs,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def utc(
    year: int,
    month: int,
    day: int,
    hour: int = 0,
    minute: int = 0,
    second: int = 0,
    microsecond: int = 0,
) -> datetime:
    return datetime(
        year,
        month,
        day,
        hour,
        minute,
        second,
        microsecond,
        tzinfo=timezone.utc,
    )


def market_row(decision_time: datetime) -> dict[str, object]:
    timestamp = decision_time - timedelta(hours=1)
    return {
        "symbol": "BTCUSDT",
        "timeframe": "1h",
        "timestamp_utc": timestamp.strftime("%Y-%m-%dT%H:%M:%S.000000Z"),
        "close_time_utc": (decision_time - timedelta(milliseconds=1)).strftime(
            "%Y-%m-%dT%H:%M:%S.%fZ"
        ),
        "decision_time_utc": decision_time.strftime("%Y-%m-%dT%H:%M:%S.000000Z"),
        "segment_id": "SEGMENT_001",
        "open": "1",
        "high": "1",
        "low": "1",
        "close": "1",
        "volume": "1",
        "quote_asset_volume": "1",
        "number_of_trades": "1",
        "taker_buy_base_volume": "0.5",
        "taker_buy_quote_volume": "0.5",
        "market_source": "binance_public_data",
        "market_timestamp_unit": "ms",
        "market_quality_status": "accepted_phase1b_complete_month",
        "_timestamp": timestamp,
        "_decision_time": decision_time,
    }


def context(source_day: datetime) -> ContextRow:
    return ContextRow(
        asset="btc",
        source_timestamp=source_day,
        available_d1=source_day + timedelta(days=1),
        available_d2=source_day + timedelta(days=2),
        price_usd="10",
        market_cap_usd="100",
        tx_count="5",
        active_address_count="7",
    )


def tree_fingerprint(relative_root: str) -> tuple[int, str]:
    files = sorted(
        (path for path in (PROJECT_ROOT / relative_root).rglob("*") if path.is_file()),
        key=lambda path: path.relative_to(PROJECT_ROOT).as_posix().lower(),
    )
    rows = [
        f"{path.relative_to(PROJECT_ROOT).as_posix()}|{sha256_file(path)}"
        for path in files
    ]
    return len(files), sha256("\n".join(rows).encode("utf-8")).hexdigest()


class Phase1CJoinUnitTests(unittest.TestCase):
    def test_d1_asof_join_never_uses_future_information(self) -> None:
        contexts = [context(utc(2020, 12, 31)), context(utc(2021, 1, 1))]
        row = market_row(utc(2021, 1, 1, 23))
        joined = asof_join_d1([row], contexts)[0]
        self.assertEqual(joined["context_source_timestamp_utc"], "2020-12-31T00:00:00.000000Z")
        self.assertLessEqual(joined["context_available_from_utc_d1"], joined["decision_time_utc"])

    def test_context_becomes_visible_exactly_at_d1_availability(self) -> None:
        contexts = [context(utc(2021, 1, 1))]
        before = asof_join_d1([market_row(utc(2021, 1, 1, 23))], contexts)[0]
        at_boundary = asof_join_d1([market_row(utc(2021, 1, 2, 0))], contexts)[0]
        self.assertEqual(before["context_match_status"], "unmatched")
        self.assertEqual(at_boundary["context_match_status"], "matched_d1_asof")

    def test_d2_availability_remains_a_separate_field(self) -> None:
        joined = asof_join_d1(
            [market_row(utc(2021, 1, 2, 0))], [context(utc(2021, 1, 1))]
        )[0]
        self.assertEqual(joined["context_available_from_utc_d1"], "2021-01-02T00:00:00.000000Z")
        self.assertEqual(joined["context_available_from_utc_d2"], "2021-01-03T00:00:00.000000Z")

    def test_decision_time_for_millisecond_candle(self) -> None:
        close_time = utc(2021, 1, 1, 0, 59, 59, 999_000)
        self.assertEqual(
            decision_time_from_close(close_time, "ms"),
            utc(2021, 1, 1, 1),
        )

    def test_decision_time_for_microsecond_candle(self) -> None:
        close_time = utc(2025, 1, 1, 0, 59, 59, 999_999)
        self.assertEqual(
            decision_time_from_close(close_time, "us"),
            utc(2025, 1, 1, 1),
        )

    def test_duplicate_primary_keys_are_detected(self) -> None:
        row = asof_join_d1([market_row(utc(2021, 1, 2))], [context(utc(2021, 1, 1))])[0]
        self.assertEqual(_duplicate_count([row, dict(row)]), 1)

    def test_shared_month_mask_builds_deterministic_segments(self) -> None:
        mapping, segments = build_month_segments(
            ["2021-01", "2021-05", "2021-06", "2021-10", "2022-01"]
        )
        self.assertEqual([row["segment_id"] for row in segments], [
            "SEGMENT_001", "SEGMENT_002", "SEGMENT_003", "SEGMENT_004"
        ])
        self.assertEqual(mapping["2021-05"], mapping["2021-06"])

    def test_segment_changes_after_every_month_gap(self) -> None:
        mapping, _ = build_month_segments(["2021-01", "2021-03", "2021-04", "2021-06"])
        self.assertNotEqual(mapping["2021-01"], mapping["2021-03"])
        self.assertEqual(mapping["2021-03"], mapping["2021-04"])
        self.assertNotEqual(mapping["2021-04"], mapping["2021-06"])


class RealPhase1CInputTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.validated = validate_phase1b_inputs(
            PROJECT_ROOT, PROJECT_ROOT / "config" / "full_import.json"
        )
        cls.joined = {
            timeframe: asof_join_d1(cls.validated.market_rows[timeframe], cls.validated.contexts)
            for timeframe in ("1h", "4h")
        }
        cls.summary = build_join_summary(
            joined_by_timeframe=cls.joined,
            validated=cls.validated,
        )

    def test_common_asset_mask_has_exactly_53_allowed_months(self) -> None:
        self.assertEqual(len(self.validated.allowed_months), 53)
        for timeframe in ("1h", "4h"):
            sets = {
                asset: {
                    row["timestamp_utc"]
                    for row in self.validated.market_rows[timeframe]
                    if row["symbol"] == asset
                }
                for asset in ("BTCUSDT", "ETHUSDT", "SOLUSDT")
            }
            self.assertEqual(sets["BTCUSDT"], sets["ETHUSDT"])
            self.assertEqual(sets["BTCUSDT"], sets["SOLUSDT"])

    def test_no_market_row_crosses_a_segment_boundary(self) -> None:
        for timeframe, interval in (("1h", timedelta(hours=1)), ("4h", timedelta(hours=4))):
            for asset in ("BTCUSDT", "ETHUSDT", "SOLUSDT"):
                rows = [
                    row
                    for row in self.validated.market_rows[timeframe]
                    if row["symbol"] == asset
                ]
                for previous, current in zip(rows, rows[1:]):
                    gap = current["_timestamp"] - previous["_timestamp"]
                    if current["segment_id"] == previous["segment_id"]:
                        self.assertEqual(gap, interval)
                    else:
                        self.assertGreater(gap, interval)

    def test_excluded_months_never_enter_processed_rows(self) -> None:
        excluded = set(self.validated.excluded_months)
        self.assertEqual(len(excluded), 7)
        for rows in self.validated.market_rows.values():
            observed = {row["timestamp_utc"][:7] for row in rows}
            self.assertTrue(observed.isdisjoint(excluded))

    def test_real_1h_and_4h_counts_are_exact(self) -> None:
        self.assertEqual(len(self.validated.market_rows["1h"]), 116_208)
        self.assertEqual(len(self.validated.market_rows["4h"]), 29_052)
        self.assertEqual(len(self.joined["1h"]), 116_208)
        self.assertEqual(len(self.joined["4h"]), 29_052)

    def test_global_and_assetwise_join_counts_are_additive(self) -> None:
        self.assertEqual(self.summary["global"]["input_rows"], 145_260)
        self.assertEqual(self.summary["global"]["matched_rows"], 145_260)
        self.assertEqual(self.summary["global"]["unmatched_rows"], 0)
        self.assertEqual(self.summary["global"]["available_from_after_decision_violations"], 0)
        self.assertEqual(
            sum(group["output_rows"] for group in self.summary["by_asset"].values()),
            self.summary["global"]["output_rows"],
        )
        self.assertEqual(set(self.summary["by_asset_timeframe"]), {
            "BTCUSDT|1h", "BTCUSDT|4h", "ETHUSDT|1h", "ETHUSDT|4h",
            "SOLUSDT|1h", "SOLUSDT|4h"
        })

    def test_no_rolling_return_signal_or_position_fields_exist(self) -> None:
        forbidden_tokens = ("return", "rolling", "indicator", "signal", "position")
        for field in (*PROCESSED_1H_FIELDS, *PROCESSED_4H_FIELDS):
            self.assertFalse(any(token in field.lower() for token in forbidden_tokens))


class Phase1COutputSafetyTests(unittest.TestCase):
    def test_atomic_create_leaves_no_part_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "nested" / "artifact.csv"
            status = write_generated_file_cached(destination, b"a,b\n1,2\n")
            self.assertEqual(status, "created")
            self.assertEqual(destination.read_bytes(), b"a,b\n1,2\n")
            self.assertEqual(list(Path(temporary).rglob("*.part")), [])

    def test_byte_identical_rerun_is_reused(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "artifact.csv"
            content = b"a,b\n1,2\n"
            self.assertEqual(write_generated_file_cached(destination, content), "created")
            first_hash = sha256_file(destination)
            self.assertEqual(write_generated_file_cached(destination, content), "cached_valid")
            self.assertEqual(sha256_file(destination), first_hash)

    def test_different_existing_processed_file_is_not_overwritten(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "artifact.csv"
            destination.write_bytes(b"existing")
            before = destination.read_bytes()
            with self.assertRaises(IntegrityError):
                write_generated_file_cached(destination, b"different")
            self.assertEqual(destination.read_bytes(), before)

    def test_invalid_phase1b_checkpoint_stops_before_output(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config_path = root / "config" / "full_import.json"
            config_path.parent.mkdir(parents=True)
            config_path.write_bytes((PROJECT_ROOT / "config" / "full_import.json").read_bytes())
            with patch(
                "src.processed_pipeline.load_authoritative_checkpoint",
                side_effect=IntegrityError("ungueltiger Checkpoint"),
            ):
                with self.assertRaises(IntegrityError):
                    build_phase1c_outputs(root, config_path)
            self.assertFalse((root / "data" / "processed" / "full_import").exists())
            self.assertFalse((root / "reports" / "processed").exists())

    def test_phase1b_and_input_tree_hashes_are_unchanged(self) -> None:
        expected_reports = {
            "raw_manifest.csv": "1bd3a4292bf5bcd212fd1fd57a29d50aa8b664b110337b6ad326e53c73de853e",
            "binance_quality_summary.csv": "49a78f815c910c71403cbfada1d019a92d525eefeddbad2f209bf7be3196dc81",
            "source_anomalies.csv": "a424f6379e92635ff3b26f5faee95b8238640dc070b7422248affd09141c2b20",
            "coinmetrics_quality_summary.json": "1886781ddfcc2164b7a826326801071376b911136ef3b120fa896b613cc73ee3",
            "execution_checkpoint.json": "b9e9acbb2deb2839c401769fa8ef0189344b6e8ad81c33ffb47310eb93b63a7e",
        }
        for name, expected in expected_reports.items():
            self.assertEqual(sha256_file(PROJECT_ROOT / "reports" / "full_import" / name), expected)
        self.assertEqual(
            tree_fingerprint("data/raw/full_import"),
            (361, "0cb03f47844d0073701c255e9eedd893a6672158f39579c80348ac4d1b8b62e7"),
        )
        self.assertEqual(
            tree_fingerprint("data/interim/full_import"),
            (319, "14b92e6195e857417b71ebc2a9873a1b3a172d22e4fdcd1e6cbdcc5458686198"),
        )


if __name__ == "__main__":
    unittest.main()
