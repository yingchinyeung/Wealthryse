"""Offline controller checks, no cloud/protected storage/authentication proof."""
import copy
from pathlib import Path
import sys
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import qualify_owner_task_journal as qualify

from ibkr_etl.owner_codex_trial_journal import DurableTrialJournal, key_for_slot
from ibkr_etl.owner_codex_trial_lifecycle import (
    LifecycleError, MIGRATION_SHA256, REQUIRED_SOURCE_FILES, TrialMode, _record, digest)


def source():
    return {path: MIGRATION_SHA256 if path.endswith("059_owner_codex_tasks.sql") else digest(path)
            for path in REQUIRED_SOURCE_FILES}


class SharedMemory:
    """Synthetic atomic backend; not durable, protected or live-qualified."""
    def __init__(self):
        self.values, self.lock, self.uploads = {}, threading.Lock(), 0

    def client(self):
        shared = self

        class Backend:
            supports_live_execution = False

            def read(self, key):
                with shared.lock:
                    return shared.values.get(key)

            def create_if_absent(self, key, value):
                with shared.lock:
                    shared.uploads += 1
                    if key in shared.values:
                        return False
                    shared.values[key] = value
                    return True

        return Backend()


class ProbeControllerTests(unittest.TestCase):
    def setUp(self):
        self.shared = SharedMemory()

    def test_probe_only_creates_synthetic_journal_then_quarantines(self):
        with patch.object(qualify, "backend", side_effect=self.shared.client):
            result = qualify.probe(source())
        self.assertEqual("passed_synthetic_storage_only", result["status"])
        self.assertEqual(1, result["unambiguous_reserve_winners"])
        self.assertEqual(2, result["competing_thread_clients"])
        self.assertIs(False, result["competing_processes"])
        for field in ("source_rows_read", "source_rows_written", "sql_calls", "model_calls", "agents_activated"):
            self.assertIs(type(result[field]), int)
            self.assertEqual(0, result[field])
        for field in ("authenticated_trial_caller_verified", "restricted_login_verified", "production_journal_used"):
            self.assertIs(False, result[field])
        self.assertEqual(6, len(self.shared.values))
        self.assertEqual(result["snapshot"], qualify.asdict(DurableTrialJournal(self.shared.client()).snapshot()))

    def test_probe_cannot_redispatch_existing_fixed_scope(self):
        with patch.object(qualify, "backend", side_effect=self.shared.client):
            qualify.probe(source())
            uploads = self.shared.uploads
            with self.assertRaisesRegex(ValueError, "^fixed_probe_scope_consumed_no_redispatch$"):
                qualify.probe(source())
        self.assertEqual(uploads, self.shared.uploads)

    def test_synthetic_descriptors_exactly_bind_plan_reservation_and_records(self):
        p = qualify.synthetic_plan(qualify.OPERATION, source())
        r = DurableTrialJournal(self.shared.client()).reserve_phase(p, qualify.Phase.SETUP, (), None)
        intent, accepted = qualify.descriptors(p, r)
        self.assertIs(TrialMode.SYNTHETIC, p.mode)
        self.assertEqual(digest(_record(p, r, "intent")), intent.record_sha256)
        self.assertEqual(digest(_record(p, r, "outcome", intent=intent, outcome=accepted.outcome)),
                         accepted.outcome_receipt.record_sha256)
        self.assertIs(False, accepted.outcome.live_sql_executed)

    def test_exact_owner_subscription_identity_required(self):
        accepted = {"id": qualify.deploy.SUB, "user": {"name": qualify.OWNER, "type": "user"}}
        with patch.object(qualify.deploy, "az", return_value=accepted):
            qualify.checked_owner()
        for field, value in (("id", "unregistered"), ("user", {"name": qualify.OWNER, "type": "servicePrincipal"}),
                             ("user", {"name": "different@example.invalid", "type": "user"})):
            rejected = copy.deepcopy(accepted); rejected[field] = value
            with patch.object(qualify.deploy, "az", return_value=rejected):
                with self.assertRaisesRegex(ValueError, "^registered_owner_subscription_required$"):
                    qualify.checked_owner()

    def test_migration_or_operation_changes_rejected_before_storage(self):
        broken = source(); broken["migrations/postgres/059_owner_codex_tasks.sql"] = digest("wrong")
        with self.assertRaisesRegex(ValueError, "^reviewed_migration_source_required$"):
            qualify.synthetic_plan(qualify.OPERATION, broken)
        with self.assertRaises(LifecycleError):
            qualify.synthetic_plan("not-a-uuid", source())
        self.assertEqual(0, self.shared.uploads)

    def test_exact_result_rejects_bool_alias_unknown_fields_or_fake_restart(self):
        with patch.object(qualify, "backend", side_effect=self.shared.client):
            result = qualify.probe(source())
        for field, value in (("conditional_create_winners", True), ("sql_calls", False),
                             ("process_restart_verified", True), ("lost_claim_ack_count", 0),
                             ("extra_claim", "unauthorized")):
            rejected = copy.deepcopy(result); rejected[field] = value
            with self.assertRaisesRegex(ValueError, "^exact_synthetic_probe_result_required$"):
                qualify.validate_result(rejected, source())

    def test_exact_snapshot_rejects_missing_claim_wrong_phase_and_foreign_plan(self):
        with patch.object(qualify, "backend", side_effect=self.shared.client):
            result = qualify.probe(source())
        for field, value in (("record_sha256s", list(result["snapshot"]["record_sha256s"])[:-1]),
                             ("quarantined", 1), ("status", "reserved"), ("phase", "cleanup"),
                             ("plan_sha256", digest("foreign")), ("acceptance_sha256s", [])):
            rejected = copy.deepcopy(result); rejected["snapshot"][field] = value
            with self.assertRaisesRegex(ValueError, "^exact_synthetic_quarantined_snapshot_required$"):
                qualify.validate_result(rejected, source())

    def test_exact_intent_and_preflight_bindings_required(self):
        preflight = {"protected_receipt": {"sha256": digest("synthetic-preflight")}}
        prepared = {"sources": source()}
        intent = {"operation_id": qualify.OPERATION, "sources": source(),
            "preflight_receipt": preflight["protected_receipt"], "probe_prefix": qualify.PROBE_PREFIX,
            "synthetic_only": True, "automatic_retry": False, "sql_calls": 0, "model_calls": 0}
        qualify.validate_intent(intent, prepared, preflight, source())
        for field, value in (("operation_id", qualify.COMPETING_OPERATION), ("synthetic_only", 1),
                             ("sql_calls", False), ("probe_prefix", "production/"),
                             ("preflight_receipt", {"sha256": digest("other")})):
            rejected = copy.deepcopy(intent); rejected[field] = value
            with self.assertRaisesRegex(ValueError, "^exact_synthetic_probe_intent_required$"):
                qualify.validate_intent(rejected, prepared, preflight, source())

    def test_failure_before_fault_commit_cannot_claim_lost_ack_success(self):
        normal = self.shared.client

        def client():
            b = normal(); create = b.create_if_absent

            def unavailable(key, payload):
                if key == key_for_slot(5):
                    raise LifecycleError("store_uncertain")
                return create(key, payload)

            b.create_if_absent = unavailable
            return b

        with patch.object(qualify, "backend", side_effect=client):
            with self.assertRaisesRegex(ValueError, "^committed_synthetic_lost_claim_required$"):
                qualify.probe(source())


class FakeFolder:
    def __init__(self, values):
        self.values = values

    def mkdir(self, **kwargs):
        pass

    def __truediv__(self, key):
        folder = self

        class File:
            def exists(self):
                return key in folder.values

            def read_text(self, **kwargs):
                return qualify.json.dumps(folder.values[key])

        return File()


class MainControllerTests(unittest.TestCase):
    def setUp(self):
        self.source, self.runtime = source(), {"python": "synthetic", "versions": {}}
        self.baseline = {"job_inventory_complete": True, "other_jobs": {str(i): digest(i) for i in range(34)}}
        self.receipt = {"sha256": digest("synthetic-preflight")}
        self.prepared = {"status": "prepared_synthetic_storage_only", "sources": self.source,
            "runtime": self.runtime, "baseline": self.baseline, "operation_id": qualify.OPERATION,
            "probe_prefix": qualify.PROBE_PREFIX, "protected_receipt": self.receipt}
        self.values = {"preflight.json": self.prepared}
        self.folder, self.shared = FakeFolder(self.values), SharedMemory()
        self.patchers = [patch.object(qualify, "FOLDER", self.folder), patch.object(qualify.deploy, "FOLDER", self.folder),
            patch.object(qualify, "checked_owner"), patch.object(qualify, "sources", return_value=self.source),
            patch.object(qualify, "runtime_descriptor", return_value=self.runtime),
            patch.object(qualify.deploy, "baseline", return_value=self.baseline),
            patch.object(qualify.deploy, "protected", side_effect=lambda raw, kind: {k: v for k, v in raw.items() if k != "protected_receipt"}),
            patch.object(qualify, "backend", side_effect=self.shared.client), patch("builtins.print")]
        for p in self.patchers:
            p.start(); self.addCleanup(p.stop)

    def main(self, stage):
        with patch.object(sys, "argv", ["qualify_owner_task_journal.py", stage]):
            qualify.main()

    def intent(self):
        return {"operation_id": qualify.OPERATION, "sources": self.source,
            "preflight_receipt": self.receipt, "probe_prefix": qualify.PROBE_PREFIX,
            "synthetic_only": True, "automatic_retry": False, "sql_calls": 0, "model_calls": 0,
            "protected_receipt": {"sha256": digest("synthetic-intent")}}

    def accepted(self):
        result = qualify.probe(self.source)
        self.values["intent.json"] = self.intent()
        self.values["acceptance.json"] = {"sources": self.source, "result": result,
            "intent_receipt": self.values["intent.json"]["protected_receipt"],
            "baseline_unchanged": True, "protected_receipt": {"sha256": digest("synthetic-accepted")}}

    def test_consumed_local_intent_blocks_probe(self):
        self.values["intent.json"] = self.intent()
        with patch.object(qualify, "probe") as probe:
            with self.assertRaisesRegex(ValueError, "^journal_intent_consumed_inspect_no_redispatch$"):
                self.main("run")
            probe.assert_not_called()
        self.assertEqual(0, self.shared.uploads)

    def test_mismatched_source_or_runtime_preflight_blocks_writes(self):
        for field, value in (("sources", {}), ("runtime", {"python": "different"}),
                             ("operation_id", qualify.COMPETING_OPERATION), ("probe_prefix", "other/")):
            old = self.prepared[field]; self.prepared[field] = value
            with self.assertRaisesRegex(ValueError, "^exact_journal_preflight_required$"):
                self.main("run")
            self.prepared[field] = old
        self.assertEqual(0, self.shared.uploads)

    def test_independent_verify_accepts_json_tuple_roundtrip_without_writes(self):
        self.accepted(); count = self.shared.uploads
        self.main("verify")
        self.assertEqual(count, self.shared.uploads)

    def test_inspect_is_read_only_and_does_not_claim_acceptance(self):
        self.accepted(); count = self.shared.uploads
        with patch("builtins.print") as output:
            self.main("inspect")
        result = qualify.json.loads(output.call_args[0][0])
        self.assertEqual("observed_synthetic_storage_only", result["status"])
        self.assertIs(False, result["probe_acceptance_claimed"])
        self.assertIs(False, result["redispatch_authorized"])
        self.assertEqual(count, self.shared.uploads)

    def test_verify_rejects_modified_intent(self):
        self.accepted(); self.values["intent.json"]["synthetic_only"] = 1
        with self.assertRaisesRegex(ValueError, "^exact_synthetic_probe_intent_required$"):
            self.main("verify")

    def test_verify_rejects_drift_after_snapshot(self):
        self.accepted()
        for helper, initial, changed in (("sources", self.source, {}),
                                         ("runtime_descriptor", self.runtime, {"python": "drift"})):
            with patch.object(qualify, helper, side_effect=[initial, changed]):
                with self.assertRaisesRegex(ValueError, "^independent_journal_snapshot_mismatch$"):
                    self.main("verify")
        with patch.object(qualify.deploy, "baseline", side_effect=[self.baseline, {}]):
            with self.assertRaisesRegex(ValueError, "^independent_journal_snapshot_mismatch$"):
                self.main("verify")

    def test_prepare_existing_preflight_never_runs_qa_or_saves(self):
        with patch.object(qualify.qa, "run_local_checks") as qa, patch.object(qualify.deploy, "save") as save:
            with self.assertRaisesRegex(ValueError, "^journal_preflight_reserved_no_overwrite$"):
                self.main("prepare")
            qa.assert_not_called(); save.assert_not_called()

    def test_run_checks_source_after_protected_intent_before_probe(self):
        def save(value, name, kind):
            return dict(value, protected_receipt={"sha256": digest("synthetic-saved")})

        with patch.object(qualify.deploy, "save", side_effect=save), \
                patch.object(qualify, "sources", side_effect=[self.source, {}]), \
                patch.object(qualify, "probe") as probe:
            with self.assertRaisesRegex(ValueError, "^journal_source_or_baseline_changed_after_intent$"):
                self.main("run")
            probe.assert_not_called()


if __name__ == "__main__":
    unittest.main()
