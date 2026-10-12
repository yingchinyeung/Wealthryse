"""Owner-authorized high-autonomy policy foundations, not a live executor.

Immutable IBKR originals and complete independent audit evidence are deployment
requirements. No model-supplied declaration can establish these properties.
"""
from __future__ import annotations

import hashlib
import re
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from types import MappingProxyType

from .agent_governance import REGISTRY
from .util import canonical_json

POLICY_VERSION = "specialist-standing-autonomy-v1"


@dataclass(frozen=True)
class StandingAction:
    roles: frozenset[str]
    prerequisites: frozenset[str]


BASE = frozenset({"authenticated_identity", "registered_exact_scope", "source_protection_verified",
    "audit_intent_persisted", "stop_control_clear", "capacity_budget_available"})
WRITE = BASE | {"typed_executor_verified", "idempotency_reserved", "rollback_or_recovery_verified",
    "independent_validation_passed"}
ALL_ROLES = frozenset(REGISTRY)
ACTION_CATALOG = MappingProxyType({
    "inspect_and_analyze": StandingAction(ALL_ROLES, BASE),
    "rebuild_derived_data": StandingAction(frozenset({"data_engineer", "financial_analyst"}), WRITE),
    "research_in_sandbox": StandingAction(frozenset({"data_scientist"}), WRITE),
    "generate_reports": StandingAction(frozenset({"data_analyst", "financial_analyst"}), WRITE),
    "release_validated_reports": StandingAction(frozenset({"financial_analyst"}), WRITE | {"report_identity_financial_visual_checks_passed"}),
    "recover_pipeline": StandingAction(frozenset({"data_engineer", "platform_operations"}), WRITE | {"flex_circuit_breaker_preserved"}),
    "maintain_infrastructure": StandingAction(frozenset({"platform_operations"}), WRITE | {"source_and_audit_ancestors_protected"}),
    "scale_registered_service": StandingAction(frozenset({"platform_operations"}), WRITE | {"capacity_within_registered_bounds"}),
    "prepare_software_change": StandingAction(frozenset({"software_maintenance"}), WRITE),
    "deploy_verified_release": StandingAction(frozenset({"software_maintenance", "platform_operations"}), WRITE | {
        "independent_release_evidence_passed", "staging_acceptance_passed", "schema_compatibility_verified",
        "source_and_audit_ancestors_protected"}),
    "rollback_verified_release": StandingAction(frozenset({"platform_operations"}), WRITE | {"schema_compatibility_verified"}),
})

# These defeat the owner's source-integrity/audit requirements or are outside
# system operations. Keep them outside the executor catalogue, even for owners.
PROHIBITED = frozenset({"overwrite_ibkr_source", "delete_ibkr_source", "rewrite_ibkr_received_values",
    "replace_ibkr_values_with_estimates", "disable_source_protection", "delete_source_ancestor",
    "erase_audit_history", "disable_audit", "self_expand_permissions", "trade", "transfer_funds"})


def assess_standing_action(role: str, action: str, verified_controls: frozenset[str]) -> dict:
    """Planning check only. Controls must later come from independent services.

Standing authorization replaces repeated approval prompts for catalogued work;
it does not turn unimplemented/unverified tools into safe production executors.
"""
    definition = ACTION_CATALOG.get(action)
    authorized = bool(definition and role in definition.roles and action not in PROHIBITED)
    missing = sorted(definition.prerequisites - verified_controls) if authorized else []
    return {"policy_version": POLICY_VERSION, "standing_authorization": authorized,
        "repeat_owner_approval_required": not authorized,
        "ready_for_executor_validation": authorized and not missing,
        "missing_controls": missing, "execution_authorized": False,
        "reason": "prohibited" if action in PROHIBITED else "registered_policy" if authorized else "unregistered_action_or_role"}


def verify_retained_ibkr_sources(before: dict[str, str], after: dict[str, str]) -> dict:
    """Compare trusted COMPLETE immutable-version manifests, not AI claims.

Keys identify exact received source versions; values are byte SHA-256 digests.
New IBKR arrivals may be added under new IDs. Existing versions must remain.
Storage inventory completeness and IBKR provenance require separate attestation.
"""
    if not before or not after:
        raise ValueError("Complete nonempty source manifests required")
    for manifest in (before, after):
        if any(not isinstance(key, str) or not key or not isinstance(value, str)
               or not re.fullmatch(r"[0-9a-f]{64}", value) for key, value in manifest.items()):
            raise ValueError("Invalid immutable source manifest")
    missing = sorted(set(before) - set(after))
    changed = sorted(key for key in before if key in after and before[key] != after[key])
    return {"unchanged": not missing and not changed, "retained_versions": len(before),
        "missing_versions": missing, "changed_versions": changed,
        "new_versions": len(set(after) - set(before)),
        "provenance_verified": False}


EVENT_FIELDS = frozenset({"sequence", "event_id", "occurred_at", "role", "action",
    "operation_id", "phase", "evidence_sha256", "cost_microusd", "previous_sha256", "sha256"})
PHASES = frozenset({"started", "succeeded", "failed", "denied", "cancelled", "rolled_back"})


def _hash(value: dict) -> str:
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()


def build_daily_activity_report(events: list[dict], *, period_start: datetime,
    period_end: datetime, first_sequence: int, previous_sha256: str,
    last_sequence: int, last_sha256: str) -> dict:
    """Verify a daily ledger slice against independently stored boundary hashes.

No free text, credentials, source values or hidden reasoning enter this digest.
Detailed decisions/inputs/outputs are linked by protected evidence hashes. This
does not itself store events, attest coverage, send email or schedule delivery.
"""
    if (period_start.utcoffset() is None or period_end.utcoffset() is None
            or period_start >= period_end or type(first_sequence) is not int
            or type(last_sequence) is not int or first_sequence < 1
            or last_sequence < first_sequence - 1
            or not re.fullmatch(r"[0-9a-f]{64}", previous_sha256)
            or not re.fullmatch(r"[0-9a-f]{64}", last_sha256)):
        raise ValueError("Invalid period or ledger checkpoint")
    if len(events) != last_sequence - first_sequence + 1:
        raise ValueError("Incomplete daily audit slice")
    previous = previous_sha256
    identities = set()
    details = []
    costs = 0
    counts = Counter()
    role_counts = Counter()
    for sequence, event in enumerate(events, first_sequence):
        if set(event) != EVENT_FIELDS:
            raise ValueError("Unexpected audit fields")
        if (type(event["sequence"]) is not int or event["sequence"] != sequence
                or event["previous_sha256"] != previous or event["event_id"] in identities
                or not re.fullmatch(r"[0-9a-f]{64}", event["evidence_sha256"])
                or event["role"] not in REGISTRY or event["phase"] not in PHASES
                or not re.fullmatch(r"[a-z][a-z0-9_]{0,79}", event["action"])
                or any(not re.fullmatch(r"[A-Za-z0-9-]{1,80}", event[key]) for key in ("event_id", "operation_id"))
                or type(event["cost_microusd"]) is not int or event["cost_microusd"] < 0):
            raise ValueError("Invalid audit event or broken chain")
        occurred = datetime.fromisoformat(event["occurred_at"])
        if occurred.utcoffset() is None or not period_start <= occurred < period_end:
            raise ValueError("Audit event outside report period")
        if event["sha256"] != _hash({k: v for k, v in event.items() if k != "sha256"}):
            raise ValueError("Audit content changed")
        previous = event["sha256"]
        identities.add(event["event_id"])
        counts[event["phase"]] += 1
        role_counts[event["role"]] += 1
        costs += event["cost_microusd"]
        details.append({k: event[k] for k in ("event_id", "occurred_at", "role", "action",
            "operation_id", "phase", "evidence_sha256", "cost_microusd")})
    if previous != last_sha256:
        raise ValueError("Daily ledger checkpoint mismatch")
    return {"policy_version": POLICY_VERSION, "period_start": period_start.isoformat(),
        "period_end": period_end.isoformat(), "event_count": len(details),
        "phase_counts": dict(counts), "role_counts": {role: role_counts[role] for role in REGISTRY},
        "cost_microusd": costs, "events": details, "last_sha256": last_sha256,
        "ledger_slice_verified": True, "all_system_actions_captured": False,
        "delivery_status": "not_sent"}
