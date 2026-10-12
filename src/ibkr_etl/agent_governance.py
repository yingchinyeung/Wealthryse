"""Phase-one observation registry and preflight, not an execution authority.

Grants must eventually be loaded from an authenticated, revocation-aware server
store. Never construct one from model output or browser-supplied claims. This
module neither issues grants nor calls tools; existing endpoint authorization,
database isolation and tool validation remain mandatory.
"""
from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from types import MappingProxyType

POLICY_VERSION = "agentic-observation-v1"


@dataclass(frozen=True)
class AgentDefinition:
    role: str
    label: str
    subject_tools: frozenset[str]
    resource_tools: frozenset[str] = frozenset()


ANALYTICS = frozenset({"get_portfolio_summary", "get_performance_series", "get_holdings",
    "compare_with_benchmarks", "get_risk_metrics", "get_data_freshness", "get_sector_allocation"})
DEFINITIONS = (
    AgentDefinition("data_analyst", "Data Analyst", ANALYTICS),
    AgentDefinition("data_scientist", "Data Scientist", frozenset({"get_performance_series",
        "compare_with_benchmarks", "get_risk_metrics", "get_data_freshness"})),
    AgentDefinition("data_engineer", "Data Engineer", frozenset({"get_data_freshness"})),
    AgentDefinition("financial_analyst", "Financial Analyst", ANALYTICS),
    AgentDefinition("platform_operations", "Platform Operations", frozenset(),
                    frozenset({"inspect_resource_metadata"})),
    AgentDefinition("software_maintenance", "Software Maintenance", frozenset(),
                    frozenset({"inspect_resource_metadata"})),
)
REGISTRY = MappingProxyType({item.role: item for item in DEFINITIONS})


@dataclass(frozen=True)
class ObservationGrant:
    tenant_id: str
    principal_id: str
    role: str
    subject_ids: frozenset[str]
    resource_ids: frozenset[str]
    tool_names: frozenset[str]
    expires_at: datetime
    enabled: bool = False
    policy_version: str = POLICY_VERSION
    background_allowed: bool = False


@dataclass(frozen=True)
class ObservationRequest:
    tenant_id: str
    principal_id: str
    tool_name: str
    subject_id: str | None = None
    resource_id: str | None = None
    # Interactive adapters supply the current user's exact entitlement sets;
    # None is not authority for background work; mode must explicitly allow it.
    # An empty set denies all, not a wildcard.
    user_subject_ids: frozenset[str] | None = None
    user_resource_ids: frozenset[str] | None = None
    mode: str = "interactive"


def resource_id(value: str) -> str:
    """Validate an exact ARM resource ID. Reject URLs, group scopes and wildcards."""
    if (not isinstance(value, str) or value != value.strip() or len(value) > 2048
            or re.search(r"[\s*?%#\\]", value)):
        raise ValueError("Exact ARM resource ID required")
    parts = value.split("/")
    if (len(parts) < 9 or len(parts) % 2 != 1 or parts[0] != ""
            or parts[1].lower() != "subscriptions" or parts[3].lower() != "resourcegroups"
            or parts[5].lower() != "providers"
            or any(not part or part in {".", ".."} for part in parts[1:])
            or not re.fullmatch(r"[0-9a-fA-F-]{36}", parts[2])):
        raise ValueError("Exact ARM resource ID required")
    if str(uuid.UUID(parts[2])) != parts[2].lower():
        raise ValueError("Canonical subscription ID required")
    return value.lower()


def _decision(eligible: bool, reason: str) -> dict:
    return {"policy_version": POLICY_VERSION, "eligible_for_adapter_check": eligible,
            "reason": reason, "execution_authorized": False, "mutation_authorized": False}


def preflight_observation(grant: ObservationGrant, request: ObservationRequest, *,
                          now: datetime, kill_switch: bool = True) -> dict:
    """Review exact scope only; a positive result is never an execution permit."""
    if kill_switch is not False:
        return _decision(False, "kill_switch")
    if grant.enabled is not True or grant.policy_version != POLICY_VERSION:
        return _decision(False, "grant_disabled_or_policy_changed")
    if (now.tzinfo is None or now.utcoffset() is None or grant.expires_at.tzinfo is None
            or grant.expires_at.utcoffset() is None or now >= grant.expires_at):
        return _decision(False, "grant_expired_or_clock_invalid")
    if (not grant.tenant_id or not grant.principal_id or grant.tenant_id != request.tenant_id
            or grant.principal_id != request.principal_id):
        return _decision(False, "identity_mismatch")
    definition = REGISTRY.get(grant.role)
    if not definition or request.tool_name not in grant.tool_names:
        return _decision(False, "tool_not_registered")
    if bool(request.subject_id) == bool(request.resource_id):
        return _decision(False, "single_scope_required")
    if request.mode == "background":
        if grant.background_allowed is not True:
            return _decision(False, "background_authority_missing")
    elif request.mode == "interactive":
        if ((request.subject_id and request.user_subject_ids is None)
                or (request.resource_id and request.user_resource_ids is None)):
            return _decision(False, "user_scope_required")
    else:
        return _decision(False, "unknown_invocation_mode")
    if request.subject_id:
        if (request.tool_name not in definition.subject_tools or request.subject_id == "*"
                or request.subject_id not in grant.subject_ids):
            return _decision(False, "subject_scope_denied")
        if request.user_subject_ids is not None and request.subject_id not in request.user_subject_ids:
            return _decision(False, "user_scope_denied")
    else:
        if request.tool_name not in definition.resource_tools:
            return _decision(False, "resource_tool_denied")
        try:
            target = resource_id(request.resource_id)
            if target not in {resource_id(item) for item in grant.resource_ids}:
                return _decision(False, "resource_scope_denied")
            if request.user_resource_ids is not None and target not in {resource_id(item) for item in request.user_resource_ids}:
                return _decision(False, "user_scope_denied")
        except ValueError:
            return _decision(False, "invalid_resource_scope")
    return _decision(True, "requires_authenticated_read_only_adapter")


def planning_inventory(capture: dict, *, now: datetime, subscription_id: str,
                       resource_group: str, max_age_hours: int = 48) -> dict:
    """Use metadata only. Unreviewed inventory never grants resource authority."""
    captured_at = datetime.fromisoformat(capture["captured_at"])
    if (now.tzinfo is None or captured_at.tzinfo is None
            or not 0 <= (now - captured_at).total_seconds() <= max_age_hours * 3600
            or capture.get("read_only") is not True
            or capture.get("resources", {}).get("readable") is not True):
        raise ValueError("Fresh verified read-only inventory required")
    resources = []
    seen = set()
    scope = f"/subscriptions/{subscription_id}/resourceGroups/{resource_group}/providers/"
    resource_id(scope + "Microsoft.App/jobs/scope-validation")
    for row in capture["resources"]["data"]:
        identity = resource_id(row["id"])
        if not identity.startswith(scope.lower()):
            raise ValueError("Resource outside the registered group")
        if identity in seen:
            raise ValueError("Duplicate resource inventory")
        seen.add(identity)
        resources.append({"resource_id": identity, "resource_type": row["type"],
                          "coverage": "unreviewed", "execution_authorized": False,
                          "mutation_authorized": False})
    if not resources:
        raise ValueError("Empty resource inventory")
    return {"schema": "wealthryse-agent-planning-inventory-v1", "policy_version": POLICY_VERSION,
        "captured_at": captured_at.astimezone(timezone.utc).isoformat(), "runtime_deployed": False,
        "agents": [{"role": d.role, "label": d.label, "state": "planned_disabled",
                    "subject_tools": sorted(d.subject_tools), "resource_tools": sorted(d.resource_tools),
                    "identity_registered": False} for d in DEFINITIONS],
        "resources": sorted(resources, key=lambda r: r["resource_id"]),
        "execution_authorized": False, "mutation_authorized": False}
