"""The go-live readiness pack for a new-onboarding case.

The go-live coordinator (Agentic AI integration map, "Frontier onboarding")
puts everything Ying needs for the last two approvals in one place instead of
raw numbers: every check of every stage so far with its evidence and who
recorded it, the warnings already acknowledged, how the approved account terms
compare with what the intake agent read in the client's documents, and exactly
what typing GO LIVE will switch on. It also says, in plain words, what still
stands in the way.

It is read-only and computed from the case as it is now, so it is never stale;
its digest changes whenever anything in it does. The case page shows it above
the GO LIVE box (built by the onboarding thread).
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping

from .onboarding_cases import (
    APPROVAL_LABELS, CHECK_LABELS, PRE_LIVE_STAGES, STAGE_LABELS, case_status, checks_for, missing_approvals,
    unacknowledged_warnings,
)
from .onboarding_v2_web import _account_terms, _display, _evidence, _load_case
from .subject_activation import operating_capabilities

# The approved account terms the intake agent's draft is compared with.
COMPARED_TERMS = {"display_name": "Name", "reporting_currency": "Reporting currency",
                  "history_start_date": "History start date"}

_CAPABILITY_WORDS = {
    "daily_ingestion": "Daily data from {source} from {start_date}",
    "benchmark_refresh": "Daily benchmark prices from {start_date}",
    "sector_refresh": "Sector data (needs the external data approval already given)",
    "month_end_reporting": "Month-end factsheets, each waiting for your release",
    "analyst_assurance": "The daily analyst assurance checks",
    "viewer_directory": "The account appears to the people you give access to",
}


def _json(value: Any) -> Any:
    if isinstance(value, (str, bytes)):
        try:
            return json.loads(value)
        except ValueError:
            return None
    return value


def _latest_draft(connection, tenant_id: str, case_id: str) -> dict | None:
    """The intake agent's newest draft, or None when there is none or it is not readable yet."""
    import psycopg

    try:
        with connection.transaction():
            row = connection.execute(
                "SELECT outcome, summary, terms, created_at FROM agent.onboarding_term_drafts "
                "WHERE tenant_id = %s AND case_id = %s ORDER BY draft_id DESC LIMIT 1", (tenant_id, case_id),
            ).fetchone()
    except (psycopg.errors.UndefinedTable, psycopg.errors.InvalidSchemaName, psycopg.errors.InsufficientPrivilege):
        return None
    return dict(row) if row else None


def terms_comparison(approved: Mapping[str, Any], draft: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    """Each approval term beside what the intake agent read in the documents."""
    terms = (_json(draft.get("terms")) or {}) if draft else {}
    rows = []
    for key, label in COMPARED_TERMS.items():
        drafted = terms.get(key) or {}
        value = drafted.get("value")
        source = next(iter(drafted.get("sources") or []), None)
        if not draft:
            state = "no_draft"
        elif value in (None, ""):
            state = "not_in_documents"
        else:
            state = "matches" if str(value).strip().casefold() == str(approved.get(key, "")).strip().casefold() \
                else "differs"
        rows.append({"term": key, "label": label, "approved": approved.get(key), "documents": value,
                     "state": state, "source": source})
    return rows


def go_live_effects(row: Mapping[str, Any]) -> list[str]:
    """What typing GO LIVE switches on for this case, in plain words."""
    kind = str(row.get("source_kind") or "broker_account")
    subject_type = "physical_account" if kind == "broker_account" else kind
    source = str(row.get("broker") or "derived_calculation")
    start = str(row["history_start_date"])
    effects = ["The account becomes live, and its website pages switch on."]
    for capability, configuration in operating_capabilities(subject_type=subject_type, source_name=source,
                                                            history_start_date=start, administrative_master=False):
        words = _CAPABILITY_WORDS.get(capability, capability.replace("_", " ").capitalize())
        named = "the derived calculation" if source == "derived_calculation" else source.replace("_", " ").upper()
        effects.append(words.format(**{**configuration, "source": named}))
    if kind == "broker_account":
        effects.append("Its broker data source and scheduled download switch on.")
    effects.append("Nothing is sent to anyone: the first month-end still waits for your release.")
    return effects


def readiness_pack(connection, tenant_id: str, case_id: str) -> dict[str, Any]:
    row, checks, approvals, case = _load_case(connection, tenant_id, case_id)
    by_id = {str(item["check_id"]): item for item in checks}
    stages = []
    for stage in PRE_LIVE_STAGES:
        items = []
        for check_id in checks_for(case.source_kind, stage):
            check = by_id.get(check_id)
            evidence = _evidence(check) if check else {}
            items.append({
                "check_id": check_id, "label": CHECK_LABELS[check_id],
                "status": str(check["status"]) if check else "pending",
                "message": str(check["message"]) if check else "Not run yet.",
                "recorded_by": "agent" if evidence.get("recorded_by") == "agent" else "platform",
                "checked_at": str(check["checked_at"]) if check and check.get("checked_at") else None,
                "warning_acknowledged": bool(check and check.get("warning_acknowledged_at")),
                "evidence": evidence,
            })
        stages.append({"stage": stage, "label": STAGE_LABELS[stage], "checks": items})
    approved_at = {str(item["approval_kind"]): str(item["approved_at"]) for item in approvals}
    comparison = terms_comparison(_account_terms(row), _latest_draft(connection, tenant_id, case_id))

    blockers = []
    if case.stage not in PRE_LIVE_STAGES:
        blockers.append(f"The case is past go-live ({STAGE_LABELS[case.stage]}).")
    else:
        for stage in stages[:PRE_LIVE_STAGES.index(case.stage) + 1]:
            for item in stage["checks"]:
                if item["status"] in ("pending", "fail"):
                    verb = "has not run yet" if item["status"] == "pending" else "failed"
                    blockers.append(f"{item['label']} {verb}: {item['message']}")
        blockers.extend(f"The warning on {CHECK_LABELS[c]} is not acknowledged yet."
                        for c in unacknowledged_warnings(case))
        blockers.extend(f"{APPROVAL_LABELS[a]} is not approved yet." for a in missing_approvals(case)
                        if a != "go_live")
    differs = [r["label"] for r in comparison if r["state"] == "differs"]
    pack = {
        "case_id": case.case_id,
        "display_name": row["display_name"],
        "account": _display(case),
        "source_kind": case.source_kind,
        "reporting_currency": row["reporting_currency"],
        "history_start_date": str(row["history_start_date"]),
        "stage": case.stage,
        "status": case_status(case),
        "ready": not blockers,
        "blockers": blockers,
        "notes": ([f"The approved {', '.join(differs).lower()} differ from the client's documents; "
                   "check the terms table before going live."] if differs else []),
        "stages": stages,
        "approvals": [{"kind": kind, "label": label, "approved_at": approved_at.get(kind)}
                      for kind, label in APPROVAL_LABELS.items()],
        "acknowledged_warnings": [item["label"] for stage in stages for item in stage["checks"]
                                  if item["warning_acknowledged"]],
        "terms_vs_documents": comparison,
        "go_live_switches_on": go_live_effects(row),
        "after_go_live": ("After go-live the agents watch the account's first daily runs and post anything "
                          "unusual in the agent inbox, then hand it to the month-end team for its first close."),
    }
    pack["pack_sha256"] = hashlib.sha256(json.dumps(pack, sort_keys=True, default=str).encode()).hexdigest()
    return pack
