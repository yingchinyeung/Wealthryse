import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

import test_pilot_snapshot as fixtures
from ibkr_etl.pilot_import import file_sha256
from ibkr_etl.snapshot_atomic_import import (
    combined_fingerprint, read_combined_snapshot, stage_combined_snapshot, validate_combined,
)


class SnapshotAtomicImportTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        _, self.snapshot, self.options = fixtures.PilotSnapshotTests()._v3(Path(self.folder.name))
        self.digest = file_sha256(self.snapshot)
        self.bundle = read_combined_snapshot(self.snapshot, expected_sha256=self.digest, **self.options)

    def test_complete_inventory_and_evidence_share_snapshot(self):
        self.assertEqual(len(self.bundle["tables"]), 9)
        self.assertEqual(self.bundle["evidence"]["snapshot_sha256"], self.digest)
        validate_combined(self.bundle)

    def test_external_digest_mismatch(self):
        with self.assertRaisesRegex(ValueError, "digest"):
            read_combined_snapshot(self.snapshot, expected_sha256="0" * 64, **self.options)

    def test_in_memory_value_change_rejected(self):
        self.bundle["tables"]["nav_snapshots"]["rows"][0]["total_text"] = "999"
        with self.assertRaisesRegex(ValueError, "fingerprint"):
            validate_combined(self.bundle)

    def test_rehashed_wrong_account_dates_duplicates_are_still_rejected(self):
        for change in ("account", "date", "duplicate"):
            bundle = copy.deepcopy(self.bundle)
            rows = bundle["tables"]["nav_snapshots"]["rows"]
            if change == "account":
                rows[0]["account_id"] = "OTHER"
            elif change == "date":
                rows[0]["snapshot_date"] = "2025-01-19"
            else:
                rows.append(dict(rows[0]))
            bundle["combined_sha256"] = combined_fingerprint(bundle)
            with self.assertRaises(ValueError):
                validate_combined(bundle)

    def test_missing_or_unrelated_instrument_rejected(self):
        for rows in ([], [{**self.bundle["tables"]["instruments"]["rows"][0], "conid": "OTHER"}]):
            bundle = copy.deepcopy(self.bundle)
            bundle["tables"]["instruments"]["rows"] = rows
            bundle["combined_sha256"] = combined_fingerprint(bundle)
            with self.assertRaisesRegex(ValueError, "Instrument inventory"):
                validate_combined(bundle)

    def test_source_cannot_supply_destination_run_ids(self):
        table = self.bundle["tables"]["nav_snapshots"]
        table["columns"].append("last_seen_run_id")
        table["rows"][0]["last_seen_run_id"] = 1
        self.bundle["combined_sha256"] = combined_fingerprint(self.bundle)
        with self.assertRaisesRegex(ValueError, "column contract"):
            validate_combined(self.bundle)

    def test_missing_table_is_not_silent_empty(self):
        del self.bundle["tables"]["trades"]
        self.bundle["combined_sha256"] = combined_fingerprint(self.bundle)
        with self.assertRaisesRegex(ValueError, "inventory"):
            validate_combined(self.bundle)

    def test_caller_transaction_required(self):
        from psycopg.pq import TransactionStatus
        target = Mock()
        target.info.transaction_status = TransactionStatus.IDLE
        with self.assertRaisesRegex(ValueError, "caller-owned"):
            stage_combined_snapshot(target, self.bundle, tenant_id="test", run_id=1)
        target.transaction.assert_not_called()
        target.commit.assert_not_called()
