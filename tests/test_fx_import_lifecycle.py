import hashlib
import unittest
from unittest.mock import MagicMock, patch

from psycopg.pq import TransactionStatus

from ibkr_etl.fx_evidence_bundle import build_bundle
from ibkr_etl.fx_import_lifecycle import CONTRACT_KEY, RECEIPT_KEY, RUN_TYPE, import_contract, stage_import_run
from ibkr_etl.util import canonical_json, sha256_text

TENANT = "00000000-0000-0000-0000-000000000001"
ARTIFACT = "a" * 64


def fixture():
    content = b'<FlexStatement accountId="A" toDate="20250120"><ConversionRates><ConversionRate fromCurrency="HKD" toCurrency="USD" rate="0.128"/></ConversionRates></FlexStatement>'
    run = dict(tenant_id=TENANT, run_id=10, status="success", completed_at="2025-01-21T00:00:00Z",
               raw_sha256=hashlib.sha256(content).hexdigest())
    bundle = build_bundle(tenant_id=TENANT, account_id="A", source_namespace="v11-trial",
                          source_snapshot_sha256="b" * 64, archives=[(run, content)], legacy_rows=[])
    contract = import_contract(bundle, expected_bundle_sha256=bundle["bundle_sha256"], artifact_sha256=ARTIFACT)
    destination = dict(run_id=12, tenant_id=TENANT, run_type=RUN_TYPE, status="running",
                       raw_sha256=ARTIFACT, requested_from="2025-01-20", requested_to="2025-01-20",
                       reconciliation_json=canonical_json({CONTRACT_KEY: contract, "unrelated": "preserved"}))
    staged = dict(receipt={"bundle_sha256": bundle["bundle_sha256"], "execution_authorized": False},
                  inserted={"runs": 1}, group_seconds={}, group_queries={})
    return bundle, contract, destination, staged


class FxImportLifecycleTests(unittest.TestCase):
    def connection(self, run, readback=None, tenant=TENANT, owner=True):
        c = MagicMock()
        c.info.transaction_status = TransactionStatus.INTRANS
        cursor = c.cursor.return_value.__enter__.return_value
        cursor.rowcount = 1
        cursor.execute.return_value.fetchone.side_effect = [
            {"tenant": tenant}, {"exists": 1} if owner else None, run, readback]
        return c, cursor

    def apply(self, c, b, **kwargs):
        return stage_import_run(c, b, expected_bundle_sha256=b["bundle_sha256"],
                                artifact_sha256=ARTIFACT, run_id=12, **kwargs)

    def test_contract_binds_scope_artifacts_dates_and_non_authority(self):
        b, contract, _, _ = fixture()
        self.assertEqual(contract["account_id"], "A")
        self.assertEqual(contract["artifact_sha256"], ARTIFACT)
        self.assertEqual(contract["source_snapshot_sha256"], "b" * 64)
        self.assertEqual(contract["counts"]["versions"], 1)
        self.assertFalse(contract["execution_authorized"])
        self.assertEqual(contract["source_completeness"], "unverified")
        with self.assertRaises(ValueError):
            import_contract(b, expected_bundle_sha256="f" * 64, artifact_sha256=ARTIFACT)

    def test_wrong_run_bindings_rejected_before_evidence_writes(self):
        b, _, run, staged = fixture()
        changes = [{"status": "success"}, {"run_type": "daily"}, {"raw_sha256": "f" * 64},
                   {"requested_from": "2025-01-19"}, {"requested_to": "2025-01-21"},
                   {"reconciliation_json": "{}"}, {"reconciliation_json": "[]"}]
        for change in changes:
            with self.subTest(change=change), patch("ibkr_etl.fx_import_lifecycle.stage_evidence", return_value=staged) as stage:
                c, _ = self.connection({**run, **change})
                with self.assertRaises(ValueError): self.apply(c, b)
                stage.assert_not_called()
                c.commit.assert_not_called()

    def test_contract_cannot_be_reused_for_another_account_or_bundle(self):
        b, contract, run, _ = fixture()
        for change in ({"account_id": "B"}, {"bundle_sha256": "f" * 64}, {"source_namespace": "other"}):
            c, _ = self.connection({**run, "reconciliation_json": canonical_json({CONTRACT_KEY: {**contract, **change}})})
            with self.assertRaisesRegex(ValueError, "pre-bound"): self.apply(c, b)

    def test_tenant_and_ownership_guards(self):
        b, _, run, _ = fixture()
        for options in ({"tenant": "other"}, {"owner": False}):
            c, _ = self.connection(run, **options)
            with self.assertRaises(ValueError): self.apply(c, b)

    def test_requires_transaction_and_valid_destination(self):
        b, _, run, _ = fixture()
        c, _ = self.connection(run)
        c.info.transaction_status = TransactionStatus.IDLE
        with self.assertRaises(ValueError): self.apply(c, b)
        c.cursor.assert_not_called()
        for run_id in (0, -1, True):
            with self.assertRaises(ValueError):
                stage_import_run(c, b, expected_bundle_sha256=b["bundle_sha256"], artifact_sha256=ARTIFACT, run_id=run_id)

    def test_late_receipt_conflict_is_inside_savepoint(self):
        b, contract, run, staged = fixture()
        c, _ = self.connection({**run, "reconciliation_json": canonical_json({CONTRACT_KEY: contract, RECEIPT_KEY: {"wrong": True}})})
        with patch("ibkr_etl.fx_import_lifecycle.stage_evidence", return_value=staged):
            with self.assertRaisesRegex(ValueError, "receipt differs"): self.apply(c, b)
        exit_call = c.transaction.return_value.__exit__.call_args
        self.assertIs(exit_call.args[0], ValueError)
        c.commit.assert_not_called()

    def test_success_preserves_state_and_never_marks_run_success(self):
        b, _, run, staged = fixture()
        c, cursor = self.connection(run)
        # Generate readback from the actual bound UPDATE payload, not an assumed receipt.
        def readback():
            update = next(call for call in cursor.execute.call_args_list
                          if not isinstance(call.args[0], str) and call.args[0].as_string().startswith("UPDATE"))
            return {**run, "reconciliation_json": update.args[1][0]}
        responses = iter([{"tenant": TENANT}, {"exists": 1}, run])
        cursor.execute.return_value.fetchone.side_effect = lambda: next(responses, None) or readback()
        with patch("ibkr_etl.fx_import_lifecycle.stage_evidence", return_value=staged):
            result = self.apply(c, b)
        self.assertEqual(result["status"], "evidence_staged")
        self.assertFalse(result["receipt"]["execution_authorized"])
        self.assertFalse(result["receipt"]["reconciliation_authorized"])
        self.assertIn('"unrelated":"preserved"', readback()["reconciliation_json"])
        c.commit.assert_not_called()

    def test_readback_detects_unexpected_run_transition(self):
        b, _, run, staged = fixture()
        c, _ = self.connection(run, {**run, "status": "success"})
        with patch("ibkr_etl.fx_import_lifecycle.stage_evidence", return_value=staged):
            with self.assertRaisesRegex(ValueError, "readback differs"): self.apply(c, b)

    def test_repeat_preserves_exact_receipt_without_update(self):
        b, contract, run, staged = fixture()
        receipt = {"contract_version": 1, "run_id": 12, "contract_sha256": sha256_text(canonical_json(contract)),
                   "evidence": staged["receipt"], "state": "evidence_staged", "source_completeness": "unverified",
                   "execution_authorized": False, "reconciliation_authorized": False}
        run["reconciliation_json"] = canonical_json({CONTRACT_KEY: contract, RECEIPT_KEY: receipt})
        staged["inserted"] = {"runs": 0}
        c, cursor = self.connection(run, run)
        with patch("ibkr_etl.fx_import_lifecycle.stage_evidence", return_value=staged):
            result = self.apply(c, b)
        self.assertEqual(result["receipt"], receipt)
        self.assertEqual(result["inserted"], {"runs": 0})
        self.assertFalse(any(not isinstance(call.args[0], str) and call.args[0].as_string().startswith("UPDATE")
                             for call in cursor.execute.call_args_list))

    def test_missing_run_and_failed_receipt_update_are_rejected(self):
        b, _, run, staged = fixture()
        c, _ = self.connection(None)
        with self.assertRaisesRegex(ValueError, "Destination run"): self.apply(c, b)
        c, cursor = self.connection(run)
        cursor.rowcount = 0
        with patch("ibkr_etl.fx_import_lifecycle.stage_evidence", return_value=staged):
            with self.assertRaisesRegex(ValueError, "update failed"): self.apply(c, b)
        self.assertIs(c.transaction.return_value.__exit__.call_args.args[0], ValueError)


if __name__ == "__main__":
    unittest.main()
