"""Synthetic contract tests: no database, network, protected archive or model."""
from dataclasses import asdict, replace
import copy
import unittest
import uuid

from ibkr_etl.owner_codex_trial_lifecycle import (
    CAPABILITY, DATABASE, LOGIN, MIGRATION_SHA256, PRODUCER_RESOURCE, REQUIRED_SOURCE_FILES,
    CleanupReview, ExecutionContext, LifecycleCoordinator, LifecycleError, Phase,
    PhaseAcceptance, PhaseClaim, PhaseOutcome, PhaseReservation, ProtectedReadback,
    ProtectedReceipt, ReviewedRegistration, TrialDecision, TrialMode, TrialPlan,
    TrialTarget, digest)


PRINCIPAL = "00000000-0000-0000-0000-000000000302"
INSTALLER = "synthetic_installer"


def plan(mode=TrialMode.SYNTHETIC):
    files = tuple((path, MIGRATION_SHA256 if path.endswith("059_owner_codex_tasks.sql")
                   else digest({"synthetic_path": path})) for path in REQUIRED_SOURCE_FILES)
    return TrialPlan(str(uuid.uuid4()), ReviewedRegistration(files, digest(files), INSTALLER, PRINCIPAL),
        digest("synthetic-baseline"), digest("synthetic-boundary"), mode)


class MemoryStore:
    """Test-only atomic stand-in, deliberately NOT a deployed durable store."""
    def __init__(self, trace):
        self.trace, self.operation, self.reservations, self.acceptances = trace, None, {}, []
        self.claims, self.quarantined, self.fault = {}, False, None

    def reserve_phase(self, value, phase, previous, cleanup_review):
        self.trace.append("reserve:" + phase.value)
        if self.quarantined or phase in self.reservations:
            return None
        if self.operation is not None and self.operation != value.plan_sha256:
            return None  # The FIXED target is consumed even under a new UUID/source.
        if tuple(self.acceptances) != previous or list(Phase)[len(self.acceptances)] is not phase:
            raise ValueError("synthetic incorrect durable previous chain")
        self.operation = value.plan_sha256
        reservation = PhaseReservation(value.plan_sha256, value.target.scope_sha256, phase,
            len(self.reservations) + 1, digest([a.acceptance_sha256 for a in previous]),
            None if cleanup_review is None else digest(asdict(cleanup_review)))
        self.reservations[phase] = reservation
        if self.fault == "reserve":
            raise ValueError("synthetic lost response after durable reservation")
        if self.fault == "reservation_scope":
            return replace(reservation, scope_sha256=digest("wrong-scope"))
        if self.fault == "reservation_previous":
            return replace(reservation, previous_sha256=digest("wrong-chain"))
        return reservation

    def claim_phase(self, reservation, intent):
        self.trace.append("claim:" + reservation.phase.value)
        if self.reservations.get(reservation.phase) != reservation or reservation.phase in self.claims:
            raise ValueError("synthetic claim CAS failed")
        claim = PhaseClaim(reservation, intent.receipt_sha256, True)
        self.claims[reservation.phase] = claim
        if self.fault == "claim":
            raise ValueError("synthetic lost response after durable claim")
        if self.fault == "claim_intent":
            return replace(claim, intent_receipt_sha256=digest("wrong-intent"))
        return claim

    def complete_phase(self, claim, acceptance):
        self.trace.append("complete:" + claim.reservation.phase.value)
        if self.claims.get(claim.reservation.phase) != claim:
            raise ValueError("synthetic completion CAS failed")
        self.acceptances.append(acceptance)
        if self.fault == "complete":
            raise ValueError("synthetic lost response after durable completion")
        return 1 if self.fault == "complete_bool_alias" else True

    def quarantine(self, value, phase, code):
        self.trace.append("quarantine:" + phase.value)
        self.quarantined = True
        if self.fault == "quarantine":
            raise ValueError("synthetic quarantine unavailable")
        return True


class SyntheticArchive:
    def __init__(self, trace):
        self.trace, self.records, self.fault = trace, {}, None

    def retain(self, record):
        self.trace.append("retain:" + record["kind"])
        sha = digest(record)
        receipt = ProtectedReceipt(sha, digest({"synthetic_reference": sha}))
        if sha in self.records:
            raise ValueError("synthetic create-only archive duplicate")
        self.records[sha] = copy.deepcopy(record)
        if self.fault == "retain_" + record["kind"]:
            raise ValueError("synthetic lost archive response")
        if self.fault == "receipt_" + record["kind"]:
            return replace(receipt, record_sha256=digest("wrong-record"))
        return receipt

    def readback(self, record, receipt):
        self.trace.append("readback:" + record["kind"])
        if self.records.get(receipt.record_sha256) != record:
            raise ValueError("synthetic exact bytes mismatch")
        proof = ProtectedReadback(receipt, digest(record), PRINCIPAL, PRODUCER_RESOURCE,
            INSTALLER, True, True, False)
        if self.fault == "readback_" + record["kind"]:
            raise ValueError("synthetic independent read unavailable")
        replacements = {
            "producer": {"producer_principal_id": str(uuid.uuid4())},
            "resource": {"producer_resource_id": PRODUCER_RESOURCE + "/changed"},
            "identity": {"current_identity": LOGIN},
            "binding": {"binding_sha256": digest("wrong-binding")},
            "unprotected": {"protected_archive_verified": False},
            "unauthenticated": {"authenticated_producer_verified": False},
            "forged_live": {"live_verified": True},
        }
        if self.fault in replacements:
            return replace(proof, **replacements[self.fault])
        return proof

    def review_cleanup(self, value, review):
        self.trace.append("review:cleanup")
        return self.fault != "cleanup_review"


class SyntheticExecutor:
    def __init__(self, trace):
        self.trace, self.calls, self.fault, self.result_status = trace, [], None, "passed"
        self.context_calls = 0

    def verify_context(self, value, phase):
        self.trace.append("context:" + phase.value)
        self.context_calls += 1
        context = ExecutionContext(value.plan_sha256, PRINCIPAL, PRODUCER_RESOURCE, INSTALLER, True, False)
        if self.fault == "context" or self.fault == "late_context" and self.context_calls == 2:
            return replace(context, independently_verified=False)
        if self.fault == "context_identity":
            return replace(context, current_identity=CAPABILITY)
        if self.fault == "context_source":
            return replace(context, plan_sha256=digest("changed-source"))
        return context

    def execute_phase(self, value, phase, claim):
        self.trace.append("execute:" + phase.value)
        self.calls.append((value, phase, claim))
        if self.fault == "execute":
            raise ValueError("synthetic unknown effects; sensitive provider text must not leak")
        result = PhaseOutcome(value.plan_sha256, phase, claim.reservation.fence, self.result_status,
            digest({"synthetic_result": phase.value}), INSTALLER, True, True, False)
        replacements = {
            "unknown_effects": {"effects_complete_verified": False},
            "unclosed": {"owned_connections_closed_verified": False},
            "outcome_fence": {"fence": claim.reservation.fence + 1},
            "outcome_source": {"plan_sha256": digest("wrong-source")},
            "outcome_identity": {"current_identity": LOGIN},
            "outcome_live": {"live_sql_executed": True},
        }
        return replace(result, **replacements[self.fault]) if self.fault in replacements else result


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.trace = []
        self.store = MemoryStore(self.trace)
        self.audit = SyntheticArchive(self.trace)
        self.executor = SyntheticExecutor(self.trace)
        self.coordinator = LifecycleCoordinator(self.store, self.audit, self.executor)
        self.plan = plan()

    def setup(self):
        result = self.coordinator.advance(self.plan, Phase.SETUP)
        self.assertEqual("passed", result.status)
        return result.acceptance

    def accepted_run(self, setup):
        result = self.coordinator.advance(self.plan, Phase.RUN, previous=(setup,))
        self.assertEqual(self.executor.result_status, result.status)
        return result.acceptance

    def review(self, setup, run):
        return CleanupReview(self.plan.plan_sha256, setup.acceptance_sha256, run.acceptance_sha256,
            digest("synthetic separately reviewed cleanup"))

    def test_source_only_calls_no_adapters(self):
        for phase in Phase:
            result = self.coordinator.advance(replace(self.plan, mode=TrialMode.SOURCE_ONLY), phase)
            self.assertEqual("not_executed", result.status)
            self.assertIsNone(result.acceptance)
            self.assertFalse(result.live_phase_execution_verified)
        self.assertEqual([], self.trace)

    def test_complete_synthetic_lifecycle_and_order(self):
        setup = self.setup()
        self.assertEqual(["context:setup", "reserve:setup", "retain:intent", "readback:intent",
            "claim:setup", "context:setup", "execute:setup", "context:setup", "retain:outcome",
            "readback:outcome", "complete:setup"], self.trace)
        run = self.accepted_run(setup)
        result = self.coordinator.advance(self.plan, Phase.CLEANUP, previous=(setup, run),
            cleanup_review=self.review(setup, run))
        self.assertEqual("passed", result.status)
        self.assertFalse(result.live_phase_execution_verified)
        self.assertFalse(result.automatic_retry_authorized)
        self.assertFalse(result.production_actions_authorized)
        self.assertEqual(0, result.agents_activated)
        self.assertEqual([Phase.SETUP, Phase.RUN, Phase.CLEANUP], [v[1] for v in self.executor.calls])

    def test_same_phase_replay_does_not_execute_again(self):
        self.setup()
        result = self.coordinator.advance(self.plan, Phase.SETUP)
        self.assertEqual("scope_already_consumed", result.code)
        self.assertEqual(1, len(self.executor.calls))

    def test_new_uuid_and_coordinator_cannot_evade_consumed_scope(self):
        self.setup()
        other = LifecycleCoordinator(self.store, self.audit, self.executor)
        result = other.advance(replace(self.plan, operation_id=str(uuid.uuid4())), Phase.SETUP)
        self.assertEqual("not_executed", result.status)
        self.assertEqual("scope_already_consumed", result.code)
        self.assertEqual(1, len(self.executor.calls))

    def test_missing_previous_denies_run(self):
        result = self.coordinator.advance(self.plan, Phase.RUN)
        self.assertEqual("needs_reconciliation", result.status)
        self.assertEqual([], self.executor.calls)

    def test_previous_from_other_plan_denied(self):
        setup = self.setup()
        result = self.coordinator.advance(replace(self.plan, baseline_sha256=digest("changed-baseline")),
            Phase.RUN, previous=(setup,))
        self.assertEqual("needs_reconciliation", result.status)
        self.assertEqual(1, len(self.executor.calls))

    def test_wrong_chain_binding_denied(self):
        setup = self.setup()
        forged = replace(setup, reservation=replace(setup.reservation, previous_sha256=digest("wrong")))
        result = self.coordinator.advance(self.plan, Phase.RUN, previous=(forged,))
        self.assertEqual("needs_reconciliation", result.status)
        self.assertEqual(1, len(self.executor.calls))

    def test_previous_archive_reverified_not_merely_typed(self):
        setup = self.setup()
        self.audit.records[setup.outcome_receipt.record_sha256]["phase"] = "cleanup"
        result = self.coordinator.advance(self.plan, Phase.RUN, previous=(setup,))
        self.assertEqual("needs_reconciliation", result.status)
        self.assertEqual(1, len(self.executor.calls))

    def test_cleanup_requires_explicit_bound_review(self):
        setup = self.setup()
        run = self.accepted_run(setup)
        result = self.coordinator.advance(self.plan, Phase.CLEANUP, previous=(setup, run))
        self.assertEqual("needs_reconciliation", result.status)
        self.assertEqual(2, len(self.executor.calls))

    def test_modified_cleanup_review_denied(self):
        for key in ("plan_sha256", "setup_acceptance_sha256", "run_acceptance_sha256"):
            with self.subTest(key=key):
                self.setUp()
                setup = self.setup()
                run = self.accepted_run(setup)
                review = replace(self.review(setup, run), **{key: digest("wrong")})
                result = self.coordinator.advance(self.plan, Phase.CLEANUP, previous=(setup, run), cleanup_review=review)
                self.assertEqual("needs_reconciliation", result.status)
                self.assertEqual(2, len(self.executor.calls))

    def test_cleanup_independent_review_denied(self):
        setup = self.setup()
        run = self.accepted_run(setup)
        self.audit.fault = "cleanup_review"
        result = self.coordinator.advance(self.plan, Phase.CLEANUP, previous=(setup, run),
            cleanup_review=self.review(setup, run))
        self.assertEqual("needs_reconciliation", result.status)
        self.assertEqual(2, len(self.executor.calls))

    def test_known_failed_run_can_be_explicitly_cleaned(self):
        setup = self.setup()
        self.executor.result_status = "failed"
        run = self.accepted_run(setup)
        self.executor.result_status = "passed"
        result = self.coordinator.advance(self.plan, Phase.CLEANUP, previous=(setup, run),
            cleanup_review=self.review(setup, run))
        self.assertEqual("passed", result.status)

    def test_failed_setup_is_not_complete_scope_cleanup_authority(self):
        self.executor.result_status = "failed"
        setup = self.coordinator.advance(self.plan, Phase.SETUP).acceptance
        result = self.coordinator.advance(self.plan, Phase.RUN, previous=(setup,))
        self.assertEqual("needs_reconciliation", result.status)
        self.assertEqual(1, len(self.executor.calls))

    def test_context_and_claim_failures_prevent_executor(self):
        for source, fault in (("executor", "context"), ("executor", "late_context"),
            ("executor", "context_identity"), ("executor", "context_source"),
            ("store", "claim"), ("store", "claim_intent"), ("store", "reservation_scope"),
            ("store", "reservation_previous")):
            getattr(self, source).fault = fault
            result = self.coordinator.advance(self.plan, Phase.SETUP)
            self.assertEqual("needs_reconciliation", result.status, fault)
            self.assertEqual([], self.executor.calls, fault)
            self.setUp()

    def test_intent_archive_unknown_or_forged_never_executes(self):
        for fault in ("retain_intent", "receipt_intent", "readback_intent", "producer", "resource",
                      "identity", "binding", "unprotected", "unauthenticated", "forged_live"):
            with self.subTest(fault=fault):
                self.setUp()
                self.audit.fault = fault
                result = self.coordinator.advance(self.plan, Phase.SETUP)
                self.assertEqual("needs_reconciliation", result.status)
                self.assertEqual([], self.executor.calls)
                self.assertTrue(self.store.quarantined)

    def test_unknown_execution_and_bad_result_quarantine(self):
        for fault in ("execute", "unknown_effects", "unclosed", "outcome_fence", "outcome_source",
                      "outcome_identity", "outcome_live"):
            with self.subTest(fault=fault):
                self.setUp()
                self.executor.fault = fault
                result = self.coordinator.advance(self.plan, Phase.SETUP)
                self.assertEqual("needs_reconciliation", result.status)
                self.assertEqual("execution_uncertain", result.code)
                self.assertIsNone(result.acceptance)
                self.assertTrue(self.store.quarantined)
                self.assertNotIn("sensitive", str(result))

    def test_uncertain_outcome_retention_does_not_commit_or_repeat(self):
        for fault in ("retain_outcome", "receipt_outcome", "readback_outcome"):
            with self.subTest(fault=fault):
                self.setUp()
                self.audit.fault = fault
                result = self.coordinator.advance(self.plan, Phase.SETUP)
                self.assertEqual("needs_reconciliation", result.status)
                self.assertEqual([], self.store.acceptances)
                other = LifecycleCoordinator(self.store, self.audit, self.executor)
                self.assertEqual("scope_already_consumed", other.advance(self.plan, Phase.SETUP).code)
                self.assertEqual(1, len(self.executor.calls))

    def test_lost_store_response_stops_even_if_committed(self):
        for fault in ("reserve", "complete", "complete_bool_alias"):
            with self.subTest(fault=fault):
                self.setUp()
                self.store.fault = fault
                result = self.coordinator.advance(self.plan, Phase.SETUP)
                self.assertEqual("needs_reconciliation", result.status)
                self.assertEqual("store_uncertain", result.code)
                calls = len(self.executor.calls)
                other = LifecycleCoordinator(self.store, self.audit, self.executor)
                other.advance(self.plan, Phase.SETUP)
                self.assertEqual(calls, len(self.executor.calls))

    def test_unknown_quarantine_persistence_keeps_instance_stopped(self):
        self.executor.fault, self.store.fault = "execute", "quarantine"
        result = self.coordinator.advance(self.plan, Phase.SETUP)
        self.assertEqual("needs_reconciliation", result.status)
        result = self.coordinator.advance(self.plan, Phase.SETUP)
        self.assertEqual("coordinator_quarantined", result.code)
        self.assertEqual(1, len(self.executor.calls))

    def test_live_mode_cannot_use_synthetic_context_or_receipts(self):
        result = self.coordinator.advance(replace(self.plan, mode=TrialMode.LIVE_DISPOSABLE), Phase.SETUP)
        self.assertEqual("context_unverified", result.code)
        self.assertFalse(result.live_phase_execution_verified)
        self.assertEqual([], self.executor.calls)

    def test_fixed_registry_and_exact_hashes(self):
        r = self.plan.registration
        for files in (r.files[:-1], r.files + (("extra.py", digest("extra")),), tuple(reversed(r.files)),
            tuple((p, digest("wrong-migration") if p.endswith("059_owner_codex_tasks.sql") else h) for p, h in r.files)):
            with self.subTest(files=files):
                with self.assertRaises(LifecycleError):
                    replace(r, files=files, bundle_sha256=digest(files))
        with self.assertRaises(LifecycleError):
            replace(r, bundle_sha256=digest("wrong-bundle"))
        with self.assertRaises(LifecycleError):
            replace(r, producer_resource_id=PRODUCER_RESOURCE + "/other")

    def test_fixed_target_and_untrusted_identity_rejected(self):
        for key, value in (("database", "production"), ("host", "other"), ("login", "admin")):
            with self.assertRaises(LifecycleError):
                replace(TrialTarget(), **{key: value})
        for identity in (LOGIN, CAPABILITY, "secret;SQL", "", 0):
            with self.assertRaises(LifecycleError):
                replace(self.plan.registration, installer_identity=identity)

    def test_strict_types_and_explicit_acknowledgements(self):
        r = PhaseReservation(self.plan.plan_sha256, self.plan.target.scope_sha256, Phase.SETUP, 1, digest([]), None)
        for fence in (True, 0, -1, 1000001, "1"):
            with self.assertRaises(LifecycleError):
                replace(r, fence=fence)
        for flag in (False, 1, None):
            with self.assertRaises(LifecycleError):
                PhaseClaim(r, digest("intent"), flag)
        with self.assertRaises(LifecycleError):
            replace(self.plan, mode="live_disposable")
        with self.assertRaises(LifecycleError):
            TrialDecision("not_executed", None, TrialMode.SYNTHETIC, Phase.SETUP, agents_activated=False)
        with self.assertRaises(LifecycleError):
            TrialDecision("not_executed", None, TrialMode.SYNTHETIC, Phase.SETUP, live_phase_execution_verified=True)

    def test_protected_records_bind_source_identity_phase_result_and_fence(self):
        accepted = self.setup()
        records = list(self.audit.records.values())
        intent, outcome = records
        for record in records:
            self.assertEqual(self.plan.operation_id, record["operation_id"])
            self.assertEqual(asdict(self.plan.target), record["target"])
            self.assertEqual(asdict(self.plan.registration), record["source"])
            self.assertEqual(INSTALLER, record["current_identity"])
            self.assertEqual(PRINCIPAL, record["producer_principal_id"])
            self.assertEqual("setup", record["phase"])
            self.assertEqual(accepted.reservation.fence, record["fence"])
            self.assertEqual(0, record["source_rows_written"])
            self.assertEqual(0, record["model_calls"])
        self.assertIsNone(intent["outcome"])
        self.assertEqual(accepted.intent.receipt_sha256, outcome["intent_receipt_sha256"])
        self.assertEqual(accepted.outcome.result_sha256, outcome["outcome"]["result_sha256"])

    def test_returned_frozen_contracts_are_revalidated(self):
        mutations = (
            ("store", "reserve_phase", "fence", True),
            ("store", "claim_phase", "durable_cas_verified", 1),
            ("audit", "retain", "record_sha256", "not-a-sha"),
            ("audit", "readback", "protected_archive_verified", 1),
            ("audit", "readback", "authenticated_producer_verified", 1),
            ("executor", "verify_context", "independently_verified", 1),
            ("executor", "execute_phase", "effects_complete_verified", 1),
            ("executor", "execute_phase", "fence", True),
        )
        for adapter_name, method, field, value in mutations:
            with self.subTest(method=method, field=field):
                self.setUp()
                adapter = getattr(self, adapter_name)
                original = getattr(adapter, method)
                def mutated(*args, original=original, field=field, value=value):
                    returned = original(*args)
                    object.__setattr__(returned, field, value)
                    return returned
                setattr(adapter, method, mutated)
                result = self.coordinator.advance(self.plan, Phase.SETUP)
                self.assertEqual("needs_reconciliation", result.status)
                self.assertIsNone(result.acceptance)
                if method != "execute_phase":
                    self.assertEqual([], self.executor.calls)

    def test_archive_cannot_mutate_submitted_record_into_accepted_scope(self):
        original = self.audit.retain
        def altered(record):
            record["target"]["database"] = "production"
            return original(record)
        self.audit.retain = altered
        result = self.coordinator.advance(self.plan, Phase.SETUP)
        self.assertEqual("needs_reconciliation", result.status)
        self.assertEqual("intent_unverified", result.code)
        self.assertEqual([], self.executor.calls)

    def test_parent_chain_frozen_mutation_is_not_accepted(self):
        setup = self.setup()
        object.__setattr__(setup.reservation, "fence", True)
        result = self.coordinator.advance(self.plan, Phase.RUN, previous=(setup,))
        self.assertEqual("needs_reconciliation", result.status)
        self.assertEqual(1, len(self.executor.calls))

    def test_live_phase_flag_never_means_full_trial_or_agents(self):
        # A strict typed future-adapter acknowledgement is exercised here only
        # as a synthetic fixture; no live SQL/archive/authentication was tested.
        real_context = self.executor.verify_context
        real_readback = self.audit.readback
        real_execute = self.executor.execute_phase
        self.executor.verify_context = lambda *a: replace(real_context(*a), live_verified=True)
        self.audit.readback = lambda *a: replace(real_readback(*a), live_verified=True)
        self.executor.execute_phase = lambda *a: replace(real_execute(*a), live_sql_executed=True)
        result = self.coordinator.advance(replace(self.plan, mode=TrialMode.LIVE_DISPOSABLE), Phase.SETUP)
        self.assertEqual("passed", result.status)
        self.assertTrue(result.live_phase_execution_verified)
        self.assertEqual([Phase.SETUP], [call[1] for call in self.executor.calls])
        self.assertNotIn("live_trial_verified", asdict(result))
        self.assertFalse(result.production_actions_authorized)
        self.assertEqual(0, result.agents_activated)

    def test_decision_cannot_claim_different_phase_or_result_status(self):
        setup = self.setup()
        with self.assertRaises(LifecycleError):
            TrialDecision("passed", None, TrialMode.SYNTHETIC, Phase.RUN, setup)
        with self.assertRaises(LifecycleError):
            TrialDecision("failed", None, TrialMode.SYNTHETIC, Phase.SETUP, setup)


if __name__ == "__main__":
    unittest.main()
