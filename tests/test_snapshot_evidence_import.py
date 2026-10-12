import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from ibkr_etl.pilot_import import file_sha256
from ibkr_etl.snapshot_evidence_import import (
    bundle_fingerprint, import_financial_evidence, read_evidence_snapshot, same_payload, validate_bundle,
)
from ibkr_etl.util import canonical_json
import test_pilot_snapshot as fixtures


class SnapshotEvidenceImportTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.source, self.snapshot, self.options = fixtures.PilotSnapshotTests()._v3(Path(self.folder.name))
        self.digest = file_sha256(self.snapshot)
        self.bundle = read_evidence_snapshot(self.snapshot, expected_sha256=self.digest, **self.options)

    def test_load_checks_external_digest_and_exact_scope(self):
        self.assertEqual(len(self.bundle["rows"]), 2)
        self.assertFalse(self.bundle["summary"]["reconciliation_authorized"])
        for digest, account in [("0" * 64, self.options["account_id"]), (self.digest, "OTHER")]:
            with self.assertRaises((ValueError, RuntimeError)):
                read_evidence_snapshot(self.snapshot, expected_sha256=digest,
                                       account_id=account, start_date=self.options["start_date"])

    def test_changed_memory_bundle_is_rejected(self):
        self.bundle["rows"][0]["currency"] = "OTHER"
        with self.assertRaisesRegex(ValueError, "fingerprint"):
            validate_bundle(self.bundle)

    def test_rehashed_bundle_still_checks_content_and_account(self):
        for field, value in [("account_id", "OTHER"), ("row_sha256", "bad"), ("report_date", "2020-01-01")]:
            bundle = copy.deepcopy(self.bundle)
            bundle["rows"][0][field] = value
            bundle["bundle_sha256"] = bundle_fingerprint(bundle)
            with self.assertRaises((ValueError, RuntimeError)):
                validate_bundle(bundle)

    def test_missing_source_lineage_is_not_importable(self):
        row = self.bundle["rows"][0]
        row["source_lineage_json"] = canonical_json({
            "first_seen": {"source_run_id": 1, "run": None},
            "last_seen": {"source_run_id": 1, "run": None},
        })
        self.bundle["summary"]["incomplete_lineage_rows"] = 1
        self.bundle["bundle_sha256"] = bundle_fingerprint(self.bundle)
        with self.assertRaisesRegex(ValueError, "lineage review"):
            validate_bundle(self.bundle)

    def test_undated_evidence_is_preserved_but_not_importable(self):
        self.bundle["rows"][0]["report_date"] = None
        self.bundle["summary"]["undated_rows"] = 1
        self.bundle["bundle_sha256"] = bundle_fingerprint(self.bundle)
        with self.assertRaisesRegex(ValueError, "date/lineage review"):
            validate_bundle(self.bundle)

    def test_empty_evidence_remains_unverified(self):
        self.bundle["rows"] = []
        self.bundle["summary"].update(preserved_rows=0, category_counts={"FX": 0, "Transfers": 0})
        self.bundle["bundle_sha256"] = bundle_fingerprint(self.bundle)
        validate_bundle(self.bundle)
        self.assertEqual(self.bundle["summary"]["source_completeness"], "unverified")

    def test_no_connection_or_auto_commit_is_performed_without_outer_transaction(self):
        from psycopg.pq import TransactionStatus
        target = Mock()
        target.info.transaction_status = TransactionStatus.IDLE
        with self.assertRaisesRegex(ValueError, "caller-owned transaction"):
            import_financial_evidence(target, self.bundle,
                                      tenant_id="00000000-0000-0000-0000-000000000001", run_id=3)
        target.transaction.assert_not_called()
        target.commit.assert_not_called()

    def test_payload_comparison_checks_values_not_just_hash(self):
        source = self.bundle["rows"][0]
        same = dict(source, first_seen_run_id=500, last_seen_run_id=501)
        self.assertTrue(same_payload(same, source))
        same["data_json"] = '{"amount":"different"}'
        self.assertFalse(same_payload(same, source))
        self.assertFalse(same_payload(dict(source, currency="different"), source))

    def test_failed_validation_never_starts_database_transaction(self):
        target = Mock()
        self.bundle["summary"]["reconciliation_authorized"] = True
        self.bundle["bundle_sha256"] = bundle_fingerprint(self.bundle)
        with self.assertRaisesRegex(ValueError, "summary mismatch"):
            import_financial_evidence(target, self.bundle,
                                      tenant_id="00000000-0000-0000-0000-000000000001", run_id=3)
        target.transaction.assert_not_called()
