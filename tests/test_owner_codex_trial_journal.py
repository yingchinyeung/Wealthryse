"""Synthetic contract tests, not cloud durability/protection/identity evidence."""
from dataclasses import asdict, replace
import hashlib
import json
import threading
import unittest

from ibkr_etl.owner_codex_trial_journal import (
    DurableTrialJournal, JOURNAL_NAMESPACE, MAX_RECORD_BYTES, MAX_SLOTS, key_for_slot,
)
from ibkr_etl.owner_codex_trial_lifecycle import (
    CleanupReview, ExecutionContext, LifecycleCoordinator, LifecycleError, MIGRATION_SHA256, Phase,
    PhaseAcceptance, PhaseOutcome, ProtectedReceipt, REQUIRED_SOURCE_FILES,
    ProtectedReadback, ReviewedRegistration, TrialMode, TrialPlan, _record, digest,
)


def plan(mode=TrialMode.SYNTHETIC, operation="11111111-1111-4111-8111-111111111111"):
    source = tuple((path, MIGRATION_SHA256 if path.startswith("migrations/") else "a" * 64)
        for path in REQUIRED_SOURCE_FILES)
    registration = ReviewedRegistration(source, digest(source), "SYNTH_INSTALLER",
        "22222222-2222-4222-8222-222222222222")
    return TrialPlan(operation, registration, "b" * 64, "c" * 64, mode)


def receipt(value):
    return ProtectedReceipt(digest(value), "d" * 64)


def intent(p, reservation):
    return receipt(_record(p, reservation, "intent"))


def acceptance(p, reservation, supplied_intent=None, status="passed"):
    supplied_intent = intent(p, reservation) if supplied_intent is None else supplied_intent
    outcome = PhaseOutcome(p.plan_sha256, reservation.phase, reservation.fence,
        status, "e" * 64, p.registration.installer_identity, True, True,
        p.mode is TrialMode.LIVE_DISPOSABLE)
    outcome_receipt = receipt(_record(p, reservation, "outcome", intent=supplied_intent, outcome=outcome))
    return PhaseAcceptance(reservation, supplied_intent, outcome, outcome_receipt)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
        ensure_ascii=True, allow_nan=False).encode()


def rehash(value):
    body = {key: item for key, item in value.items() if key != "record_sha256"}
    value["record_sha256"] = hashlib.sha256(canonical(body)).hexdigest()
    return canonical(value)


class MemoryBackend:
    supports_live_execution = False

    def __init__(self):
        self.data, self.reads, self.creates = {}, [], []
        self.lock = threading.Lock()
        self.after_create = None
        self.override_ack = None
        self.read_fault = None

    def read(self, key):
        self.reads.append(key)
        if self.read_fault is not None:
            return self.read_fault(key)
        with self.lock:
            return self.data.get(key)

    def create_if_absent(self, key, value):
        with self.lock:
            self.creates.append((key, value))
            if key in self.data:
                return False
            self.data[key] = value
        hook, self.after_create = self.after_create, None
        if hook is not None:
            hook(key, value)
        return True if self.override_ack is None else self.override_ack


class NoAccessBackend:
    def __getattribute__(self, _name):
        raise AssertionError("Source-only must not inspect any backend property")


class SyntheticAudit:
    """Explicit synthetic proof fixture, never an authentication attestation."""
    def retain(self, record):
        return receipt(record)

    def readback(self, record, retained):
        return ProtectedReadback(retained, digest(record), record["producer_principal_id"],
            record["producer_resource_id"], record["current_identity"], True, True, False)

    def review_cleanup(self, _plan, _review):
        return True


class SyntheticExecutor:
    def __init__(self):
        self.executions = []

    def verify_context(self, p, _phase):
        return ExecutionContext(p.plan_sha256, p.registration.producer_principal_id,
            p.registration.producer_resource_id, p.registration.installer_identity, True, False)

    def execute_phase(self, p, phase, claim):
        self.executions.append(phase)
        return PhaseOutcome(p.plan_sha256, phase, claim.reservation.fence,
            "passed", "e" * 64, p.registration.installer_identity, True, True, False)


class JournalTests(unittest.TestCase):
    def setUp(self):
        self.backend, self.p = MemoryBackend(), plan()
        self.store = DurableTrialJournal(self.backend)

    def assert_code(self, code, function, *args, **kwargs):
        with self.assertRaises(LifecycleError) as caught:
            function(*args, **kwargs)
        self.assertEqual(caught.exception.code, code)
        self.assertEqual(str(caught.exception), code)

    def finish(self, phase, previous=(), review=None, status="passed", store=None):
        store = self.store if store is None else store
        reservation = store.reserve_phase(self.p, phase, previous, review)
        supplied_intent = intent(self.p, reservation)
        claim = store.claim_phase(reservation, supplied_intent)
        result = acceptance(self.p, reservation, supplied_intent, status)
        self.assertIs(store.complete_phase(claim, result), True)
        return result

    def mutate(self, slot, mutator):
        key = key_for_slot(slot)
        raw = json.loads(self.backend.data[key])
        mutator(raw)
        self.backend.data[key] = rehash(raw)

    def test_constants_and_fixed_key_oracle(self):
        self.assertEqual(JOURNAL_NAMESPACE, "owner_codex_restricted_trial_v1")
        self.assertEqual(MAX_SLOTS, 10)
        self.assertEqual(MAX_RECORD_BYTES, 16384)
        self.assertEqual(key_for_slot(1), f"owner_codex_restricted_trial_v1/{self.p.target.scope_sha256}/01")
        self.assertTrue(key_for_slot(10).endswith("/10"))
        for bad in (0, 11, True, False, 1.0, "1", None):
            self.assert_code("invalid_contract", key_for_slot, bad)

    def test_construction_and_source_only_are_inert(self):
        store = DurableTrialJournal(NoAccessBackend())
        p = plan(TrialMode.SOURCE_ONLY)
        self.assertIsNone(store.reserve_phase(p, Phase.SETUP))
        self.assertIs(store.quarantine(p, Phase.SETUP, "execution_uncertain"), False)

    def test_live_gate_false_and_nonboolean_flags(self):
        p = plan(TrialMode.LIVE_DISPOSABLE)
        for flag in (False, 1, "true", None):
            self.backend.supports_live_execution = flag
            self.assert_code("context_unverified", self.store.reserve_phase, p, Phase.SETUP)
            self.assertEqual(self.backend.reads, [])
            self.assertEqual(self.backend.creates, [])

    def test_live_gate_exception_is_sanitized(self):
        class BadFlag(MemoryBackend):
            @property
            def supports_live_execution(self):
                raise RuntimeError("SECRET_PROVIDER_TEXT")
        store = DurableTrialJournal(BadFlag())
        self.assert_code("context_unverified", store.reserve_phase, plan(TrialMode.LIVE_DISPOSABLE), Phase.SETUP)

    def test_setup_commits_exact_three_slots_and_original_objects(self):
        result = self.finish(Phase.SETUP)
        snapshot = self.store.snapshot()
        self.assertEqual(snapshot.status, "completed")
        self.assertEqual(snapshot.phase, Phase.SETUP)
        self.assertEqual(snapshot.acceptance_sha256s, (result.acceptance_sha256,))
        self.assertEqual(len(snapshot.record_sha256s), 3)
        self.assertIs(snapshot.quarantined, False)
        self.assertEqual(list(self.backend.data), [key_for_slot(i) for i in (1, 2, 3)])
        self.assertTrue(all(len(value) <= MAX_RECORD_BYTES for value in self.backend.data.values()))

    def test_full_chain_has_fixed_fences_and_separate_review(self):
        setup = self.finish(Phase.SETUP)
        run = self.finish(Phase.RUN, (setup,))
        review = CleanupReview(self.p.plan_sha256, setup.acceptance_sha256, run.acceptance_sha256, "f" * 64)
        cleanup = self.finish(Phase.CLEANUP, (setup, run), review)
        self.assertEqual([item.reservation.fence for item in (setup, run, cleanup)], [1, 2, 3])
        self.assertEqual(len(self.store.snapshot().record_sha256s), 9)
        self.assertEqual(len(self.store.snapshot().acceptance_sha256s), 3)
        self.assertIsNone(self.store.reserve_phase(self.p, Phase.CLEANUP, (setup, run), review))
        self.assertIs(self.store.quarantine(self.p, Phase.CLEANUP, "execution_uncertain"), True)
        self.assertEqual(len(self.store.snapshot().record_sha256s), 10)

    def test_fresh_store_can_continue_committed_chain_only_with_exact_previous(self):
        setup = self.finish(Phase.SETUP)
        fresh = DurableTrialJournal(self.backend)
        self.assert_code("phase_order_invalid", fresh.reserve_phase, self.p, Phase.RUN)
        run = self.finish(Phase.RUN, (setup,), store=fresh)
        self.assertEqual(fresh.snapshot().acceptance_sha256s, (setup.acceptance_sha256, run.acceptance_sha256))

    def test_definitive_failed_run_can_be_reviewed_for_cleanup(self):
        setup = self.finish(Phase.SETUP)
        run = self.finish(Phase.RUN, (setup,), status="failed")
        review = CleanupReview(self.p.plan_sha256, setup.acceptance_sha256, run.acceptance_sha256, "f" * 64)
        self.assertIsNotNone(self.store.reserve_phase(self.p, Phase.CLEANUP, (setup, run), review))

    def test_failed_setup_never_authorizes_run_or_cleanup(self):
        setup = self.finish(Phase.SETUP, status="failed")
        self.assert_code("phase_order_invalid", self.store.reserve_phase, self.p, Phase.RUN, (setup,))
        self.assertEqual(len(self.backend.data), 3)

    def test_cleanup_review_wrong_chain_denied_without_write(self):
        setup = self.finish(Phase.SETUP)
        run = self.finish(Phase.RUN, (setup,))
        review = CleanupReview(self.p.plan_sha256, "0" * 64, run.acceptance_sha256, "f" * 64)
        self.assert_code("cleanup_review_required", self.store.reserve_phase, self.p, Phase.CLEANUP, (setup, run), review)
        self.assertEqual(len(self.backend.data), 6)

    def test_review_not_permitted_for_noncleanup_phase(self):
        review = CleanupReview(self.p.plan_sha256, "a" * 64, "b" * 64, "f" * 64)
        self.assert_code("cleanup_review_required", self.store.reserve_phase, self.p, Phase.SETUP, (), review)

    def test_claim_requires_original_identity_not_equal_reconstructed_object(self):
        reservation = self.store.reserve_phase(self.p, Phase.SETUP)
        equivalent = replace(reservation)
        self.assertEqual(equivalent, reservation)
        self.assert_code("claim_unverified", self.store.claim_phase, equivalent, intent(self.p, reservation))
        self.assertIsNotNone(self.store.claim_phase(reservation, intent(self.p, reservation)))

    def test_claim_requires_same_instance(self):
        reservation = self.store.reserve_phase(self.p, Phase.SETUP)
        fresh = DurableTrialJournal(self.backend)
        self.assert_code("claim_unverified", fresh.claim_phase, reservation, intent(self.p, reservation))
        self.assertIsNone(fresh.reserve_phase(self.p, Phase.SETUP))

    def test_claim_consumes_permit_before_invalid_intent(self):
        reservation = self.store.reserve_phase(self.p, Phase.SETUP)
        self.assert_code("intent_unverified", self.store.claim_phase, reservation, ProtectedReceipt("0" * 64, "d" * 64))
        self.assert_code("claim_unverified", self.store.claim_phase, reservation, intent(self.p, reservation))
        self.assertEqual(len(self.backend.data), 1)

    def test_reservation_mutation_denied_and_permit_consumed(self):
        reservation = self.store.reserve_phase(self.p, Phase.SETUP)
        object.__setattr__(reservation, "fence", 2)
        self.assert_code("claim_unverified", self.store.claim_phase, reservation, intent(self.p, reservation))
        object.__setattr__(reservation, "fence", 1)
        self.assert_code("claim_unverified", self.store.claim_phase, reservation, intent(self.p, reservation))

    def test_complete_requires_original_claim_and_same_instance(self):
        reservation = self.store.reserve_phase(self.p, Phase.SETUP)
        claim = self.store.claim_phase(reservation, intent(self.p, reservation))
        value = acceptance(self.p, reservation)
        self.assert_code("claim_unverified", self.store.complete_phase, replace(claim), value)
        self.assert_code("claim_unverified", DurableTrialJournal(self.backend).complete_phase, claim, value)
        self.assertIs(self.store.complete_phase(claim, value), True)
        self.assert_code("claim_unverified", self.store.complete_phase, claim, value)

    def test_bad_completion_consumes_original_claim_without_retry(self):
        reservation = self.store.reserve_phase(self.p, Phase.SETUP)
        claim = self.store.claim_phase(reservation, intent(self.p, reservation))
        value = acceptance(self.p, reservation)
        bad = replace(value, outcome_receipt=ProtectedReceipt("0" * 64, "d" * 64))
        self.assert_code("outcome_unverified", self.store.complete_phase, claim, bad)
        self.assert_code("claim_unverified", self.store.complete_phase, claim, value)
        self.assertEqual(len(self.backend.data), 2)

    def test_completion_requires_verified_closure_and_identity(self):
        for field, value in (("effects_complete_verified", False),
                            ("owned_connections_closed_verified", False),
                            ("current_identity", "OTHER_INSTALLER"),
                            ("live_sql_executed", True)):
            with self.subTest(field=field):
                backend, store = MemoryBackend(), None
                store = DurableTrialJournal(backend)
                r = store.reserve_phase(self.p, Phase.SETUP)
                c = store.claim_phase(r, intent(self.p, r))
                good = acceptance(self.p, r)
                changed = replace(good.outcome, **{field: value})
                bad = replace(good, outcome=changed, outcome_receipt=receipt(_record(self.p, r,
                    "outcome", intent=good.intent, outcome=changed)))
                self.assert_code("outcome_unverified", store.complete_phase, c, bad)
                self.assertEqual(len(backend.data), 2)

    def test_equal_preexisting_reserve_never_authorizes(self):
        first = self.store.reserve_phase(self.p, Phase.SETUP)
        self.assertIsNotNone(first)
        self.assertIsNone(DurableTrialJournal(self.backend).reserve_phase(self.p, Phase.SETUP))
        self.assertEqual(len(self.backend.creates), 1)

    def test_false_ack_after_create_never_authorizes_even_exact_bytes(self):
        self.backend.override_ack = False
        self.assertIsNone(self.store.reserve_phase(self.p, Phase.SETUP))
        self.assertEqual(self.store.snapshot().status, "reserved")
        self.assertIsNone(self.store.reserve_phase(self.p, Phase.SETUP))
        self.assertEqual(len(self.backend.creates), 1)

    def test_nonboolean_ack_never_authorizes(self):
        for flag in (1, "true", [], object()):
            backend = MemoryBackend()
            backend.override_ack = flag
            store = DurableTrialJournal(backend)
            self.assert_code("store_uncertain", store.reserve_phase, self.p, Phase.SETUP)
            self.assertIsNone(DurableTrialJournal(backend).reserve_phase(self.p, Phase.SETUP))

    def test_lost_reserve_ack_is_sanitized_and_restart_cannot_repeat(self):
        self.backend.after_create = lambda *_: (_ for _ in ()).throw(RuntimeError("SECRET_PROVIDER_TEXT"))
        self.assert_code("store_uncertain", self.store.reserve_phase, self.p, Phase.SETUP)
        self.assertIsNone(DurableTrialJournal(self.backend).reserve_phase(self.p, Phase.SETUP))
        other = plan(operation="33333333-3333-4333-8333-333333333333")
        self.assertIsNone(DurableTrialJournal(self.backend).reserve_phase(other, Phase.SETUP))
        self.assertEqual(len(self.backend.creates), 1)

    def test_lost_claim_ack_consumes_permit_and_blocks_restart(self):
        reservation = self.store.reserve_phase(self.p, Phase.SETUP)
        self.backend.after_create = lambda *_: (_ for _ in ()).throw(RuntimeError("SECRET"))
        self.assert_code("store_uncertain", self.store.claim_phase, reservation, intent(self.p, reservation))
        self.assert_code("claim_unverified", self.store.claim_phase, reservation, intent(self.p, reservation))
        self.assertEqual(DurableTrialJournal(self.backend).snapshot().status, "claimed")
        self.assertIsNone(DurableTrialJournal(self.backend).reserve_phase(self.p, Phase.SETUP))

    def test_lost_completion_ack_never_retries_original_claim(self):
        r = self.store.reserve_phase(self.p, Phase.SETUP)
        c = self.store.claim_phase(r, intent(self.p, r))
        self.backend.after_create = lambda *_: (_ for _ in ()).throw(RuntimeError("SECRET"))
        self.assert_code("store_uncertain", self.store.complete_phase, c, acceptance(self.p, r))
        self.assert_code("claim_unverified", self.store.complete_phase, c, acceptance(self.p, r))
        fresh = DurableTrialJournal(self.backend)
        self.assertEqual(fresh.snapshot().status, "completed")
        self.assert_code("phase_order_invalid", fresh.reserve_phase, self.p, Phase.RUN)

    def test_concurrent_quarantine_after_ack_prevents_returned_reserve_permit(self):
        other = DurableTrialJournal(self.backend)
        self.backend.after_create = lambda *_: other.quarantine(self.p, Phase.SETUP, "execution_uncertain")
        self.assert_code("store_uncertain", self.store.reserve_phase, self.p, Phase.SETUP)
        self.assertIs(self.store.snapshot().quarantined, True)
        self.assertEqual(self.store._reservations, {})

    def test_quarantine_blocks_pending_claim_and_new_operation(self):
        r = self.store.reserve_phase(self.p, Phase.SETUP)
        self.assertIs(self.store.quarantine(self.p, Phase.SETUP, "execution_uncertain"), True)
        self.assert_code("claim_unverified", self.store.claim_phase, r, intent(self.p, r))
        self.assertIsNone(self.store.reserve_phase(self.p, Phase.SETUP))
        other = plan(operation="33333333-3333-4333-8333-333333333333")
        self.assertIsNone(DurableTrialJournal(self.backend).reserve_phase(other, Phase.SETUP))
        self.assertIs(self.store.quarantine(other, Phase.SETUP, "store_uncertain"), False)

    def test_quarantine_after_claim_blocks_completion_but_does_not_claim_stopped(self):
        r = self.store.reserve_phase(self.p, Phase.SETUP)
        c = self.store.claim_phase(r, intent(self.p, r))
        self.assertIs(self.store.quarantine(self.p, Phase.SETUP, "execution_uncertain"), True)
        self.assert_code("outcome_unverified", self.store.complete_phase, c, acceptance(self.p, r))
        self.assertNotIn("process_stopped", asdict(self.store.snapshot()))

    def test_lost_quarantine_ack_can_only_confirm_sticky_metadata(self):
        self.backend.after_create = lambda *_: (_ for _ in ()).throw(RuntimeError("SECRET"))
        self.assertIs(self.store.quarantine(self.p, Phase.SETUP, "store_uncertain"), True)
        self.assertIs(self.store.quarantine(self.p, Phase.SETUP, "execution_uncertain"), True)
        self.assertEqual(len(self.backend.creates), 1)
        self.assertIsNone(self.store.reserve_phase(self.p, Phase.SETUP))

    def test_quarantine_cas_reconciliation_is_bounded_and_metadata_only(self):
        class LoseAll(MemoryBackend):
            def create_if_absent(self, key, value):
                self.creates.append((key, value))
                return False
        backend = LoseAll()
        store = DurableTrialJournal(backend)
        self.assert_code("store_uncertain", store.quarantine, self.p, Phase.SETUP, "execution_uncertain")
        self.assertEqual(len(backend.creates), 10)
        self.assertTrue(all(key == key_for_slot(1) for key, _ in backend.creates))
        self.assertEqual(store._reservations, {})
        self.assertEqual(store._claims, {})

    def test_all_slots_scanned_for_gaps_before_parse(self):
        self.backend.data[key_for_slot(2)] = b"invalid"
        self.assert_code("store_uncertain", self.store.snapshot)
        self.assertEqual(self.backend.reads, [key_for_slot(i) for i in range(1, 11)])
        self.assert_code("store_uncertain", self.store.quarantine, self.p, Phase.SETUP, "store_uncertain")
        self.assertEqual(self.backend.creates, [])

    def test_trailing_record_after_quarantine_is_rejected(self):
        self.store.quarantine(self.p, Phase.SETUP, "store_uncertain")
        self.backend.data[key_for_slot(2)] = self.backend.data[key_for_slot(1)]
        self.assert_code("store_uncertain", self.store.snapshot)

    def test_strict_bytes_size_and_encoding(self):
        for bad in (b"", b"x" * (MAX_RECORD_BYTES + 1), "{}", bytearray(b"{}"), b"\xff"):
            self.backend.data = {key_for_slot(1): bad}
            self.assert_code("store_uncertain", self.store.snapshot)

    def test_duplicate_keys_whitespace_nan_and_trailing_data_rejected(self):
        self.store.reserve_phase(self.p, Phase.SETUP)
        valid = self.backend.data[key_for_slot(1)]
        for bad in (b" " + valid, valid + b"\n", valid + b"{}", valid.replace(b'"version":1', b'"version":NaN'),
                    valid.replace(b'"version":1', b'"version":1,"version":1')):
            self.backend.data[key_for_slot(1)] = bad
            self.assert_code("store_uncertain", self.store.snapshot)

    def test_rehashed_wrong_record_fields_and_types_rejected(self):
        self.store.reserve_phase(self.p, Phase.SETUP)
        valid = self.backend.data[key_for_slot(1)]
        changes = (("version", True), ("slot", True), ("slot", 2), ("namespace", "another"),
            ("scope_sha256", "0" * 64), ("parent_sha256", "0" * 64), ("plan_sha256", "0" * 64),
            ("event", "execute"), ("extra", "secret"))
        for field, value in changes:
            self.backend.data[key_for_slot(1)] = valid
            self.mutate(1, lambda raw, f=field, v=value: raw.__setitem__(f, v))
            self.assert_code("store_uncertain", self.store.snapshot)

    def test_rehashed_payload_fence_or_unknown_fields_rejected(self):
        self.store.reserve_phase(self.p, Phase.SETUP)
        valid = self.backend.data[key_for_slot(1)]
        for bad in (2, True, 100):
            self.backend.data[key_for_slot(1)] = valid
            self.mutate(1, lambda raw: raw["payload"]["reservation"].__setitem__("fence", bad))
            self.assert_code("store_uncertain", self.store.snapshot)
        self.backend.data[key_for_slot(1)] = valid
        self.mutate(1, lambda raw: raw["payload"].__setitem__("untrusted_prompt", "do something"))
        self.assert_code("store_uncertain", self.store.snapshot)

    def test_rehashed_nested_source_mode_or_target_mutations_rejected(self):
        self.store.reserve_phase(self.p, Phase.SETUP)
        valid = self.backend.data[key_for_slot(1)]
        mutators = (
            lambda raw: raw["payload"]["plan"].__setitem__("mode", "source_only"),
            lambda raw: raw["payload"]["plan"]["target"].__setitem__("database", "production"),
            lambda raw: raw["payload"]["plan"]["registration"]["files"].append(["raw_ibkr.xml", "0" * 64]),
            lambda raw: raw["payload"]["plan"]["registration"].__setitem__("credentials", "secret"),
        )
        for mutate in mutators:
            self.backend.data[key_for_slot(1)] = valid
            self.mutate(1, mutate)
            self.assert_code("store_uncertain", self.store.snapshot)

    def test_corrupt_read_never_authorizes_or_guesses_quarantine_slot(self):
        self.backend.read_fault = lambda _key: (_ for _ in ()).throw(RuntimeError("SECRET"))
        self.assert_code("store_uncertain", self.store.reserve_phase, self.p, Phase.SETUP)
        self.assert_code("store_uncertain", self.store.quarantine, self.p, Phase.SETUP, "store_uncertain")
        self.assertEqual(self.backend.creates, [])

    def test_dataclass_mutations_and_foreign_types_are_revalidated(self):
        broken = plan()
        object.__setattr__(broken.registration, "files", None)
        self.assert_code("invalid_contract", self.store.reserve_phase, broken, Phase.SETUP)
        self.assert_code("invalid_contract", self.store.reserve_phase, asdict(self.p), Phase.SETUP)
        self.assert_code("invalid_contract", self.store.reserve_phase, self.p, "setup")
        self.assert_code("invalid_contract", self.store.quarantine, self.p, Phase.SETUP, "SECRET_PROVIDER_TEXT")
        self.assertEqual(self.backend.creates, [])

    def test_scope_cas_race_different_operations_has_one_winner(self):
        barrier = threading.Barrier(4)
        results = []
        def contender(index):
            store = DurableTrialJournal(self.backend)
            p = plan(operation=f"{index:08d}-1111-4111-8111-111111111111")
            barrier.wait()
            results.append(store.reserve_phase(p, Phase.SETUP))
        workers = [threading.Thread(target=contender, args=(i,)) for i in range(1, 5)]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(10)
            self.assertFalse(worker.is_alive())
        self.assertEqual(sum(value is not None for value in results), 1)
        self.assertEqual(len(self.backend.data), 1)

    def test_backend_lie_exact_post_create_readback_denies(self):
        class NoCommit(MemoryBackend):
            def create_if_absent(self, key, value):
                self.creates.append((key, value))
                return True
        store = DurableTrialJournal(NoCommit())
        self.assert_code("store_uncertain", store.reserve_phase, self.p, Phase.SETUP)
        self.assertEqual(store._reservations, {})

    def test_live_flag_can_be_revoked_before_claim(self):
        p = plan(TrialMode.LIVE_DISPOSABLE)
        self.backend.supports_live_execution = True  # Synthetic fixture, not real qualification.
        r = self.store.reserve_phase(p, Phase.SETUP)
        self.backend.supports_live_execution = False
        self.assert_code("context_unverified", self.store.claim_phase, r, intent(p, r))
        self.backend.supports_live_execution = True
        self.assert_code("claim_unverified", self.store.claim_phase, r, intent(p, r))
        self.assertEqual(len(self.backend.data), 1)

    def test_source_only_coordinator_never_invokes_store(self):
        coordinator = LifecycleCoordinator(NoAccessBackend(), NoAccessBackend(), NoAccessBackend())
        result = coordinator.advance(plan(TrialMode.SOURCE_ONLY), Phase.SETUP)
        self.assertEqual(result.status, "not_executed")
        self.assertIs(result.production_actions_authorized, False)

    def test_synthetic_coordinator_uses_original_permits_and_reviewed_chain(self):
        audit, executor = SyntheticAudit(), SyntheticExecutor()
        coordinator = LifecycleCoordinator(self.store, audit, executor)
        setup = coordinator.advance(self.p, Phase.SETUP)
        self.assertEqual(setup.status, "passed")
        fresh = LifecycleCoordinator(DurableTrialJournal(self.backend), audit, executor)
        run = fresh.advance(self.p, Phase.RUN, previous=(setup.acceptance,))
        self.assertEqual(run.status, "passed")
        review = CleanupReview(self.p.plan_sha256, setup.acceptance.acceptance_sha256,
            run.acceptance.acceptance_sha256, "f" * 64)
        cleanup = fresh.advance(self.p, Phase.CLEANUP, previous=(setup.acceptance, run.acceptance),
            cleanup_review=review)
        self.assertEqual(cleanup.status, "passed")
        self.assertEqual(executor.executions, [Phase.SETUP, Phase.RUN, Phase.CLEANUP])
        self.assertEqual(len(self.store.snapshot().record_sha256s), 9)
        self.assertIs(cleanup.production_actions_authorized, False)
        self.assertIs(cleanup.live_phase_execution_verified, False)

    def test_coordinator_claim_ack_loss_quarantines_without_execution_or_replay(self):
        class LoseClaim(MemoryBackend):
            def create_if_absent(self, key, value):
                result = super().create_if_absent(key, value)
                if result and key.endswith("/02"):
                    raise RuntimeError("SECRET_RESPONSE_LOST")
                return result
        backend, executor = LoseClaim(), SyntheticExecutor()
        coordinator = LifecycleCoordinator(DurableTrialJournal(backend), SyntheticAudit(), executor)
        result = coordinator.advance(self.p, Phase.SETUP)
        self.assertEqual((result.status, result.code), ("needs_reconciliation", "claim_unverified"))
        self.assertEqual(executor.executions, [])
        fresh = DurableTrialJournal(backend)
        self.assertIs(fresh.snapshot().quarantined, True)
        retry = LifecycleCoordinator(fresh, SyntheticAudit(), executor).advance(self.p, Phase.SETUP)
        self.assertEqual((retry.status, retry.code), ("not_executed", "scope_already_consumed"))
        self.assertEqual(executor.executions, [])


if __name__ == "__main__":
    unittest.main()
