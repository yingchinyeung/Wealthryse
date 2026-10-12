"""One synthetic protected-storage journal probe; NEVER PostgreSQL or model work.

prepare freezes QA/source/baseline; run retains an exclusive local protected intent
before writes in ONE fixed probe namespace; verify is independently read-only.
No resource/job/permission/schedule changes, credential fallback, automatic
redispatch, raw-source access, production journal or runtime activation.
An interrupted journal is reconciled read-only, not replayed under a new
operation. The local intent file is not a cross-host authorization lock; fixed
remote journal slots provide consumed-scope protection once reservation starts.
"""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from datetime import datetime, timezone
import argparse
import hashlib
from importlib.metadata import version
import json
import re
import sys
import threading
import time

from azure.identity import AzureCliCredential
import requests

import deploy_owner_task_postgres_trial as deploy
import qualify_owner_task_restricted_boundary as boundary
import verify_owner_task_source_checkpoint as qa
from ibkr_etl.owner_codex_trial_journal import DurableTrialJournal, MAX_SLOTS, key_for_slot
from ibkr_etl.owner_codex_trial_journal_azure import SyntheticAzureJournalBackend, PROBE_PREFIX, SCOPE
from ibkr_etl.owner_codex_trial_lifecycle import (
    LifecycleError, MIGRATION_SHA256, REQUIRED_SOURCE_FILES, Phase, PhaseAcceptance,
    PhaseOutcome, ProtectedReceipt, ReviewedRegistration, TrialMode, TrialPlan, _record, digest)


FOLDER = deploy.ROOT / ".work/owner-task-journal-20261004"
OPERATION = "c7e8dd81-8b02-4447-97e8-331f2e11a83f"
COMPETING_OPERATION = "f21d70b5-4d56-4cb5-afd0-441e52d9c40c"
SYNTHETIC_PRINCIPAL = "00000000-0000-0000-0000-000000000302"
OWNER = "anthony.ying@wealthryse.ai"


def sources():
    return boundary.source_manifest()  # All code/tests/SQL, excluding narrative updates.


def runtime_descriptor():
    # Recorded compatibility context, NOT installed-byte/dependency certification.
    return {"python": sys.version.split()[0], "versions": {name: version(name) for name in
            ("azure-core", "azure-storage-blob", "azure-identity", "requests")},
        "requirements_lock_sha256": hashlib.sha256((deploy.ROOT / "requirements.lock").read_bytes()).hexdigest()}


def checked_owner():
    account = deploy.az("account", "show")
    if (account.get("id") != deploy.SUB or account.get("user", {}).get("name", "").casefold() != OWNER
            or account.get("user", {}).get("type") != "user"):
        raise ValueError("registered_owner_subscription_required")


def backend():
    credential = AzureCliCredential(process_timeout=10)
    token = credential.get_token("https://management.azure.com/.default")
    deadline = time.monotonic() + 180

    def arm():
        if time.monotonic() > deadline or token.expires_on < time.time() + 30:
            raise LifecycleError("store_uncertain")
        response = requests.get("https://management.azure.com" + SCOPE + "?api-version=2023-05-01",
            headers={"Authorization": "Bearer " + token.token}, timeout=(5, 10), allow_redirects=False)
        if response.status_code != 200:
            raise LifecycleError("store_uncertain")
        return response.json()

    return SyntheticAzureJournalBackend(credential, arm)


def synthetic_plan(operation, source):
    # Synthetic identity and boundary descriptors are NOT a caller/SQL authority
    # claim. The entire concrete source set is frozen separately in the preflight.
    files = tuple((path, source[path]) for path in REQUIRED_SOURCE_FILES)
    if dict(files)["migrations/postgres/059_owner_codex_tasks.sql"] != MIGRATION_SHA256:
        raise ValueError("reviewed_migration_source_required")
    registration = ReviewedRegistration(files, digest(files), "synthetic_installer", SYNTHETIC_PRINCIPAL)
    return TrialPlan(operation, registration, digest("synthetic-no-sql-baseline"),
        digest("synthetic-no-sql-boundary"), TrialMode.SYNTHETIC)


def descriptors(plan, reservation):
    # Exact typed metadata only: these are not protected SQL intent/outcome
    # receipts or claims that setup, inference or cleanup actually occurred.
    intent = ProtectedReceipt(digest(_record(plan, reservation, "intent")), digest("synthetic-intent-reference"))
    outcome = PhaseOutcome(plan.plan_sha256, reservation.phase, reservation.fence, "passed",
        digest("synthetic-no-effect-result"), plan.registration.installer_identity, True, True, False)
    receipt = ProtectedReceipt(digest(_record(plan, reservation, "outcome", intent=intent, outcome=outcome)),
        digest("synthetic-outcome-reference"))
    return intent, PhaseAcceptance(reservation, intent, outcome, receipt)


def validate_result(result, source):
    fixed = {"status": "passed_synthetic_storage_only", "competing_processes": False,
        "competing_thread_clients": 2, "unambiguous_reserve_winners": 1,
        "lost_claim_ack_injected_after_commit": True, "lost_claim_ack_count": 1,
        "conditional_create_attempts": 2, "conditional_create_winners": 1,
        "conditional_create_losers": 1, "fresh_store_reissue_blocked": True,
        "process_restart_verified": False, "reconstructed_claim_blocked": True,
        "quarantine_committed": True, "authenticated_trial_caller_verified": False,
        "restricted_login_verified": False, "source_rows_read": 0, "source_rows_written": 0,
        "sql_calls": 0, "model_calls": 0, "agents_activated": 0,
        "production_journal_used": False, "probe_prefix": PROBE_PREFIX}
    if (type(result) is not dict or set(result) != set(fixed) | {"snapshot", "winning_operation_id"}
            or any(type(result[k]) is not type(v) or result[k] != v for k, v in fixed.items())
            or type(result["winning_operation_id"]) is not str
            or result["winning_operation_id"] not in (OPERATION, COMPETING_OPERATION)):
        raise ValueError("exact_synthetic_probe_result_required")
    snapshot = json.loads(json.dumps(result["snapshot"]))
    if (type(snapshot) is not dict or set(snapshot) != {"record_sha256s", "plan_sha256", "acceptance_sha256s",
            "phase", "status", "quarantined"} or snapshot["phase"] != Phase.RUN.value
            or snapshot["status"] != "quarantined" or snapshot["quarantined"] is not True
            or snapshot["plan_sha256"] != synthetic_plan(result["winning_operation_id"], source).plan_sha256
            or type(snapshot["record_sha256s"]) is not list or len(snapshot["record_sha256s"]) != 6
            or type(snapshot["acceptance_sha256s"]) is not list or len(snapshot["acceptance_sha256s"]) != 1
            or any(type(sha) is not str or re.fullmatch("[0-9a-f]{64}", sha) is None
                for sha in snapshot["record_sha256s"] + snapshot["acceptance_sha256s"])):
        raise ValueError("exact_synthetic_quarantined_snapshot_required")


def validate_intent(intent, prepared, raw_preflight, source):
    expected = {"operation_id": OPERATION, "sources": source,
        "preflight_receipt": raw_preflight["protected_receipt"], "probe_prefix": PROBE_PREFIX,
        "synthetic_only": True, "automatic_retry": False, "sql_calls": 0, "model_calls": 0}
    if (type(intent) is not dict or set(intent) != set(expected) or digest(intent) != digest(expected)
            or prepared.get("sources") != source):
        raise ValueError("exact_synthetic_probe_intent_required")


def probe(source):
    plan = synthetic_plan(OPERATION, source)
    other = synthetic_plan(COMPETING_OPERATION, source)
    gate = threading.Barrier(2)
    attempts, lock = [], threading.Lock()

    class RaceBackend:
        supports_live_execution = False

        def __init__(self):
            self.inner = backend()

        def read(self, key):
            return self.inner.read(key)

        def create_if_absent(self, key, payload):
            if key != key_for_slot(1):
                return self.inner.create_if_absent(key, payload)
            gate.wait(timeout=30)
            created = self.inner.create_if_absent(key, payload)
            with lock:
                attempts.append(created)
            return created

    stores = (DurableTrialJournal(RaceBackend()), DurableTrialJournal(RaceBackend()))
    if any(store.snapshot().record_sha256s for store in stores):
        raise ValueError("fixed_probe_scope_consumed_no_redispatch")
    with ThreadPoolExecutor(max_workers=2) as pool:
        calls = (pool.submit(stores[0].reserve_phase, plan, Phase.SETUP, (), None),
                 pool.submit(stores[1].reserve_phase, other, Phase.SETUP, (), None))
        values = tuple(call.result(timeout=180) for call in calls)
    winners = [i for i, value in enumerate(values) if value is not None]
    if len(winners) != 1 or len(attempts) != 2 or sum(value is True for value in attempts) != 1 or sum(value is False for value in attempts) != 1:
        raise ValueError("exactly_one_synthetic_reservation_required")
    i = winners[0]
    winner_plan = (plan, other)[i]
    store, reservation = stores[i], values[i]
    intent, accepted = descriptors(winner_plan, reservation)
    claim = store.claim_phase(reservation, intent)
    if store.complete_phase(claim, accepted) is not True:
        raise ValueError("synthetic_completion_not_committed")
    class LostClaimAckBackend:
        supports_live_execution = False

        def __init__(self):
            self.inner, self.armed, self.fired = backend(), False, 0

        def read(self, key):
            return self.inner.read(key)

        def create_if_absent(self, key, payload):
            created = self.inner.create_if_absent(key, payload)
            if self.armed and created is True:
                if key != key_for_slot(5):
                    raise ValueError("exact_synthetic_run_claim_fault_required")
                self.fired += 1
                raise LifecycleError("store_uncertain")
            return created

    lost = LostClaimAckBackend()
    fresh = DurableTrialJournal(lost)
    run = fresh.reserve_phase(winner_plan, Phase.RUN, (accepted,), None)
    if run is None:
        raise ValueError("synthetic_next_phase_not_reserved")
    run_intent, _ = descriptors(winner_plan, run)
    lost.armed = True
    try:
        fresh.claim_phase(run, run_intent)
    except LifecycleError:
        pass
    else:
        raise ValueError("synthetic_lost_ack_not_injected")
    restored = DurableTrialJournal(backend())
    committed = restored.snapshot()
    if (lost.fired != 1 or len(committed.record_sha256s) != 5
            or committed.status != "claimed" or committed.phase is not Phase.RUN):
        raise ValueError("committed_synthetic_lost_claim_required")
    if restored.reserve_phase(winner_plan, Phase.RUN, (accepted,), None) is not None:
        raise ValueError("restart_must_not_reissue_reservation")
    try:
        restored.claim_phase(run, run_intent)
    except LifecycleError:
        pass
    else:
        raise ValueError("restart_must_not_reconstruct_claim")
    if restored.quarantine(winner_plan, Phase.RUN, "execution_uncertain") is not True:
        raise ValueError("synthetic_quarantine_not_committed")
    if DurableTrialJournal(backend()).reserve_phase(plan, Phase.SETUP, (), None) is not None:
        raise ValueError("quarantine_must_block_other_operation")
    snapshot = DurableTrialJournal(backend()).snapshot()
    result = {"status": "passed_synthetic_storage_only", "snapshot": asdict(snapshot),
        "winning_operation_id": winner_plan.operation_id, "competing_processes": False,
        "competing_thread_clients": 2, "unambiguous_reserve_winners": 1,
        "lost_claim_ack_injected_after_commit": True, "lost_claim_ack_count": lost.fired,
        "conditional_create_attempts": 2, "conditional_create_winners": 1,
        "conditional_create_losers": 1, "fresh_store_reissue_blocked": True,
        "process_restart_verified": False,
        "reconstructed_claim_blocked": True, "quarantine_committed": True,
        "authenticated_trial_caller_verified": False, "restricted_login_verified": False,
        "source_rows_read": 0, "source_rows_written": 0, "sql_calls": 0, "model_calls": 0,
        "agents_activated": 0, "production_journal_used": False, "probe_prefix": PROBE_PREFIX}
    validate_result(result, source)
    return result


def main():
    p = argparse.ArgumentParser()
    p.add_argument("stage", choices=("prepare", "run", "inspect", "verify"))
    args = p.parse_args()
    deploy.FOLDER = FOLDER
    checked_owner()
    FOLDER.mkdir(parents=True, exist_ok=True)
    source, runtime = sources(), runtime_descriptor()
    if args.stage == "prepare":
        if (FOLDER / "preflight.json").exists():
            raise ValueError("journal_preflight_reserved_no_overwrite")
        local = qa.run_local_checks()
        if sources() != source or runtime_descriptor() != runtime:
            raise ValueError("journal_sources_changed_during_qa")
        current = deploy.baseline()
        if current.get("job_inventory_complete") is not True or len(current.get("other_jobs", {})) != 34:
            raise ValueError("complete_unchanged_baseline_required")
        if DurableTrialJournal(backend()).snapshot().record_sha256s:
            raise ValueError("journal_probe_scope_already_consumed")
        result = deploy.save({"status": "prepared_synthetic_storage_only", "sources": source,
            "runtime": runtime, "local_checks": local, "baseline": current, "operation_id": OPERATION,
            "probe_prefix": PROBE_PREFIX, "automatic_retry": False, "sql_calls": 0, "model_calls": 0},
            "preflight.json", "journal-preflight")
        deploy.protected(result, "journal-preflight")
        if sources() != source or runtime_descriptor() != runtime or deploy.baseline() != current:
            raise ValueError("journal_preflight_changed_during_retention")
        print(json.dumps({"status": result["status"], "tests": local["test_count"],
            "protected_receipt": result["protected_receipt"]}), flush=True)
        return
    raw = json.loads((FOLDER / "preflight.json").read_text(encoding="utf-8"))
    prepared = deploy.protected(raw, "journal-preflight")
    if (prepared["sources"] != source or prepared.get("runtime") != runtime
            or prepared.get("status") != "prepared_synthetic_storage_only"
            or prepared.get("operation_id") != OPERATION or prepared.get("probe_prefix") != PROBE_PREFIX):
        raise ValueError("exact_journal_preflight_required")
    if deploy.baseline() != prepared["baseline"]:
        raise ValueError("journal_live_baseline_changed")
    if args.stage == "inspect":
        intent_raw = json.loads((FOLDER / "intent.json").read_text(encoding="utf-8"))
        validate_intent(deploy.protected(intent_raw, "journal-intent"), prepared, raw, source)
        snapshot = asdict(DurableTrialJournal(backend()).snapshot())
        if (sources() != source or runtime_descriptor() != runtime
                or deploy.baseline() != prepared["baseline"]):
            raise ValueError("journal_inspection_context_changed")
        print(json.dumps({"status": "observed_synthetic_storage_only", "snapshot": snapshot,
            "probe_acceptance_claimed": False, "redispatch_authorized": False,
            "sql_calls": 0, "model_calls": 0, "agents_activated": 0}), flush=True)
        return
    if args.stage == "run":
        if (FOLDER / "intent.json").exists():
            raise ValueError("journal_intent_consumed_inspect_no_redispatch")
        intent = deploy.save({"operation_id": OPERATION, "sources": source,
            "preflight_receipt": raw["protected_receipt"], "probe_prefix": PROBE_PREFIX,
            "synthetic_only": True, "automatic_retry": False, "sql_calls": 0, "model_calls": 0},
            "intent.json", "journal-intent")
        validate_intent(deploy.protected(intent, "journal-intent"), prepared, raw, source)
        if sources() != source or runtime_descriptor() != runtime or deploy.baseline() != prepared["baseline"]:
            raise ValueError("journal_source_or_baseline_changed_after_intent")
        result = probe(source)
        if sources() != source or runtime_descriptor() != runtime or deploy.baseline() != prepared["baseline"]:
            raise ValueError("journal_source_or_baseline_changed_after_probe")
        saved = deploy.save({"result": result, "sources": source,
            "intent_receipt": intent["protected_receipt"], "baseline_unchanged": True,
            "checked_at_utc": datetime.now(timezone.utc).isoformat()}, "acceptance.json", "journal-acceptance")
        deploy.protected(saved, "journal-acceptance")
        print(json.dumps({"status": result["status"], "snapshot": result["snapshot"],
            "protected_receipt": saved["protected_receipt"], "sql_calls": 0, "model_calls": 0}), flush=True)
        return
    accepted_raw = json.loads((FOLDER / "acceptance.json").read_text(encoding="utf-8"))
    accepted = deploy.protected(accepted_raw, "journal-acceptance")
    intent_raw = json.loads((FOLDER / "intent.json").read_text(encoding="utf-8"))
    validate_intent(deploy.protected(intent_raw, "journal-intent"), prepared, raw, source)
    if (accepted["sources"] != source or accepted["intent_receipt"] != intent_raw["protected_receipt"]
            or accepted.get("baseline_unchanged") is not True
            or accepted.get("result", {}).get("status") != "passed_synthetic_storage_only"):
        raise ValueError("exact_completed_journal_probe_required")
    validate_result(accepted["result"], source)
    snapshot = asdict(DurableTrialJournal(backend()).snapshot())
    if (digest(snapshot) != digest(accepted["result"]["snapshot"]) or sources() != source
            or runtime_descriptor() != runtime or deploy.baseline() != prepared["baseline"]):
        raise ValueError("independent_journal_snapshot_mismatch")
    print(json.dumps({"status": "verified_synthetic_storage_only", "snapshot": snapshot,
        "protected_receipt": accepted_raw["protected_receipt"], "baseline_unchanged": True,
        "owner_runtime_active": False, "six_specialist_runtimes_active": False}), flush=True)


if __name__ == "__main__":
    main()
