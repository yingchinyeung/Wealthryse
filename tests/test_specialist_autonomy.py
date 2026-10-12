import hashlib
import unittest
from datetime import datetime, timedelta, timezone

from ibkr_etl.agent_governance import REGISTRY
from ibkr_etl.specialist_autonomy import (ACTION_CATALOG, PROHIBITED,
    assess_standing_action, build_daily_activity_report, verify_retained_ibkr_sources)
from ibkr_etl.util import canonical_json

START = datetime(2026, 10, 2, tzinfo=timezone.utc)
ZERO = "0" * 64


def event(sequence, previous, phase="succeeded"):
    data = {"sequence": sequence, "event_id": f"event-{sequence}",
        "occurred_at": (START + timedelta(minutes=sequence)).isoformat(),
        "role": "platform_operations", "action": "recover_pipeline",
        "operation_id": "operation-one", "phase": phase,
        "evidence_sha256": "a" * 64, "cost_microusd": 10,
        "previous_sha256": previous}
    data["sha256"] = hashlib.sha256(canonical_json(data).encode()).hexdigest()
    return data


class SpecialistAutonomyTests(unittest.TestCase):
    def test_all_six_specialists_have_standing_analysis_authority(self):
        spec = ACTION_CATALOG["inspect_and_analyze"]
        self.assertEqual(spec.roles, frozenset(REGISTRY))
        for role in REGISTRY:
            decision = assess_standing_action(role, "inspect_and_analyze", spec.prerequisites)
            self.assertTrue(decision["standing_authorization"])
            self.assertFalse(decision["repeat_owner_approval_required"])
            self.assertTrue(decision["ready_for_executor_validation"])
            self.assertFalse(decision["execution_authorized"])

    def test_broader_operational_catalogue_uses_standing_authority(self):
        for action, spec in ACTION_CATALOG.items():
            for role in spec.roles:
                decision = assess_standing_action(role, action, spec.prerequisites)
                self.assertFalse(decision["repeat_owner_approval_required"])
                self.assertTrue(decision["ready_for_executor_validation"])

    def test_no_repeated_approval_but_missing_controls_block_executor(self):
        result = assess_standing_action("platform_operations", "deploy_verified_release", frozenset())
        self.assertTrue(result["standing_authorization"])
        self.assertFalse(result["repeat_owner_approval_required"])
        self.assertIn("source_protection_verified", result["missing_controls"])
        self.assertIn("audit_intent_persisted", result["missing_controls"])
        self.assertFalse(result["ready_for_executor_validation"])

    def test_source_audit_destruction_and_trading_never_catalogued(self):
        for role in REGISTRY:
            for action in PROHIBITED:
                result = assess_standing_action(role, action, frozenset())
                self.assertFalse(result["standing_authorization"])
                self.assertFalse(result["execution_authorized"])
                self.assertEqual(result["reason"], "prohibited")

    def test_unknown_actions_and_roles_fail_closed(self):
        self.assertFalse(assess_standing_action("owner", "deploy_verified_release", frozenset())["standing_authorization"])
        self.assertFalse(assess_standing_action("platform_operations", "run_any_shell", frozenset())["standing_authorization"])

    def test_unchanged_sources_with_new_ibkr_version_keep_history(self):
        result = verify_retained_ibkr_sources({"source/version1": "a" * 64},
            {"source/version1": "a" * 64, "source/version2": "b" * 64})
        self.assertTrue(result["unchanged"])
        self.assertEqual(result["new_versions"], 1)
        self.assertFalse(result["provenance_verified"])

    def test_changed_deleted_and_replaced_originals_detected(self):
        before = {"original": "a" * 64}
        for after in ({"original": "b" * 64}, {"replacement": "a" * 64}):
            self.assertFalse(verify_retained_ibkr_sources(before, after)["unchanged"])

    def test_empty_or_malformed_inventory_is_not_evidence(self):
        for before, after in (({}, {}), ({"a": "bad"}, {"a": "bad"}),
                ({"a": "a" * 64}, {})):
            with self.assertRaises(ValueError):
                verify_retained_ibkr_sources(before, after)

    def digest(self, events, last=None, last_sequence=None):
        return build_daily_activity_report(events, period_start=START,
            period_end=START + timedelta(days=1), first_sequence=1,
            previous_sha256=ZERO, last_sequence=len(events) if last_sequence is None else last_sequence,
            last_sha256=last if last is not None else events[-1]["sha256"] if events else ZERO)

    def test_daily_report_includes_attempts_failures_rollbacks_all_roles(self):
        events, previous = [], ZERO
        for sequence, phase in enumerate(("started", "failed", "rolled_back", "denied", "cancelled", "succeeded"), 1):
            item = event(sequence, previous, phase)
            events.append(item)
            previous = item["sha256"]
        report = self.digest(events)
        self.assertEqual(report["event_count"], 6)
        self.assertEqual(report["cost_microusd"], 60)
        self.assertEqual(len(report["events"]), 6)
        self.assertEqual(set(report["role_counts"]), set(REGISTRY))
        self.assertTrue(report["ledger_slice_verified"])
        self.assertFalse(report["all_system_actions_captured"])
        self.assertEqual(report["delivery_status"], "not_sent")

    def test_no_activity_report_requires_matching_independent_checkpoints(self):
        self.assertEqual(self.digest([])["event_count"], 0)
        with self.assertRaises(ValueError):
            self.digest([], last="f" * 64)

    def test_missing_tail_or_middle_event_cannot_produce_successful_report(self):
        first = event(1, ZERO)
        second = event(2, first["sha256"])
        with self.assertRaises(ValueError):
            self.digest([first], last=second["sha256"], last_sequence=2)
        third = event(3, second["sha256"])
        with self.assertRaises(ValueError):
            self.digest([first, third], last=third["sha256"], last_sequence=3)

    def test_altered_out_of_order_duplicate_and_wrong_checkpoint_rejected(self):
        first = event(1, ZERO)
        second = event(2, first["sha256"])
        for rows, digest in (([{**first, "phase": "failed"}], first["sha256"]),
                ([second, first], second["sha256"]), ([first, first], first["sha256"]),
                ([first], "f" * 64)):
            with self.assertRaises(ValueError):
                self.digest(rows, last=digest)

    def test_raw_prompts_and_secrets_not_accepted_as_digest_fields(self):
        item = event(1, ZERO)
        for field in ("prompt", "raw_response", "password", "sql", "source_values"):
            with self.assertRaises(ValueError):
                self.digest([{**item, field: "sensitive"}])

    def test_outside_period_or_naive_timestamp_rejected(self):
        item = event(1, ZERO)
        for timestamp in (START.replace(tzinfo=None).isoformat(), (START - timedelta(seconds=1)).isoformat()):
            with self.assertRaises(ValueError):
                self.digest([{**item, "occurred_at": timestamp}])


if __name__ == "__main__":
    unittest.main()
