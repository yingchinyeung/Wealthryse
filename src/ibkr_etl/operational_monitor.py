"""Read-only health policy. Findings never authorize retries or data changes.

Absolute freshness deliberately remains unverified without an approved source
calendar. Upstream/downstream date parity is useful evidence, not proof of
numerical correctness or source-value correction propagation.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone


REQUIRED_SECTIONS = frozenset({"schema", "capture", "subjects", "registrations", "benchmarks",
    "fx", "risk_free", "work_counts", "work", "outbox_counts", "outbox", "inventory_complete", "complete"})


def assemble_evidence(records):
    """Accept reordered/duplicate delivery, reject conflicting or missing rows."""
    values, lengths, rows = {}, {}, {}
    for record in records:
        section = record["section"]
        if section not in REQUIRED_SECTIONS:
            raise ValueError("Unknown evidence section")
        if "index" in record:
            index = record["index"]
            if type(index) is not int or not 0 <= index < 100000:
                raise ValueError("Invalid evidence row index")
            target, key, value = rows.setdefault(section, {}), index, record["row"]
        elif "length" in record:
            target, key, value = lengths, section, record["length"]
            if type(value) is not int or not 0 <= value <= 100000:
                raise ValueError("Invalid evidence length")
        else:
            target, key, value = values, section, record["value"]
        if key in target and target[key] != value:
            raise ValueError("Conflicting duplicate evidence")
        target[key] = value
    for section, length in lengths.items():
        if section in values or set(rows.get(section, {})) != set(range(length)):
            raise ValueError("Incomplete or mixed evidence section")
        values[section] = [rows[section][i] for i in range(length)] if length else []
    if set(rows) - set(lengths) or not REQUIRED_SECTIONS.issubset(values) or values["complete"] is not True:
        raise ValueError("Operational evidence is incomplete")
    return values


def instant(value):
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    if parsed.tzinfo is None:
        raise ValueError("Operational timestamps must include their timezone")
    return parsed.astimezone(timezone.utc)


def queue_state(row, *, now, paused=False, backlog_minutes=30, lease_grace_minutes=5):
    """Classify work without treating scheduled backoff/dependency waits as delay."""
    now = instant(now)
    status = row["status"]
    if status in {"published", "succeeded", "cancelled"}:
        return "terminal"
    if paused:
        return "paused_or_not_active"
    if status in {"dead_letter", "failed"}:
        return status
    if status in {"running", "dispatching"}:
        if not row.get("lease_expires_at"):
            return "missing_lease"
        return ("expired_lease" if now >= instant(row["lease_expires_at"]) +
                timedelta(minutes=lease_grace_minutes) else "running")
    if status not in {"queued", "pending"}:
        return "unknown_state"
    available = instant(row["available_at"])
    if available > now:
        return "scheduled_or_backoff"
    if row.get("has_dependency"):
        dependency = row.get("dependency_status")
        if dependency is None:
            return "missing_dependency"
        if dependency in {"failed", "dead_letter", "cancelled"}:
            return "blocked_by_failed_dependency"
        if dependency != "succeeded":
            return "waiting_for_dependency"
        if not row.get("dependency_completed_at"):
            return "missing_dependency_completion"
        # Do not count the time spent waiting for a valid predecessor.
        available = max(available, instant(row["dependency_completed_at"]))
    return ("overdue_ready_work" if now >= available + timedelta(minutes=backlog_minutes)
            else "ready_within_grace")


def risk_coverage(subject, *, today):
    if subject["lifecycle_status"] != "active":
        return "not_active"
    nav_raw = subject.get("nav_date")
    risk_raw = subject.get("risk_usable_date", subject.get("risk_pass_date"))
    if not nav_raw:
        return "missing_nav"
    nav = date.fromisoformat(str(nav_raw))
    risk = date.fromisoformat(str(risk_raw)) if risk_raw else None
    if nav > today or (risk and risk > today):
        return "future_observation"
    if risk is None:
        return "no_usable_risk_snapshot"
    if risk < nav:
        return "risk_behind_nav"
    if risk > nav:
        return "risk_ahead_of_nav"
    return "dates_aligned" if subject.get("risk_pass_date") == str(nav) else "dates_aligned_with_quality_warning"


ATTENTION_STATES = frozenset({
    "dead_letter", "failed", "missing_lease", "expired_lease", "unknown_state",
    "missing_dependency", "blocked_by_failed_dependency", "missing_dependency_completion",
    "overdue_ready_work", "missing_nav", "future_observation", "no_usable_risk_snapshot",
    "risk_behind_nav", "risk_ahead_of_nav",
})


def monitor_summary(assessment, calendar_result):
    """Aggregate the checked scopes without hiding benchmark failures."""
    if assessment.get("status") not in {"needs_review", "no_findings_in_checked_scope"}:
        raise ValueError("Known operational assessment status required")
    if calendar_result.get("mode") != "shadow" or not isinstance(calendar_result.get("checks"), list):
        raise ValueError("Complete shadow calendar assessment required")
    checks = calendar_result["checks"]
    attention = []
    warnings = 0
    known_states = {"missing", "future_observation", "unexpected_non_session",
                    "behind_expected_session", "current_for_scheduled_cycle", "calendar_unverified"}
    for row in checks:
        if not isinstance(row, dict) or row.get("state") not in known_states:
            raise ValueError("Known benchmark freshness state required")
        if row.get("quality") == "warn":
            warnings += 1
        if row["state"] != "current_for_scheduled_cycle" or row.get("quality") not in {"pass", "warn"}:
            attention.append(row)
    return {"status": "needs_review" if assessment["status"] == "needs_review" or attention
                       else "no_findings_in_checked_scope",
            "benchmark_checked": len(checks), "benchmark_attention": len(attention),
            "benchmark_quality_warnings": warnings,
            "benchmark_scope": "checked" if checks else "not_checked",
            "financial_reconciliation": "not_certified"}


def assess(evidence):
    if evidence.get("schema") != "operational-evidence-v1":
        raise ValueError("Unsupported operational evidence schema")
    if evidence["capture"]["read_only"] != "on":
        raise ValueError("Probe must be database-enforced read-only")
    now = instant(evidence["capture"]["captured_at"])
    today = date.fromisoformat(str(evidence["capture"]["hkt_date"]))
    subjects = {s["subject_id"]: s for s in evidence["subjects"]}
    coverage = [{"subject_id": s["subject_id"], "nav_date": s.get("nav_date"),
                 "risk_pass_date": s.get("risk_pass_date"),
                 "risk_usable_date": s.get("risk_usable_date", s.get("risk_pass_date")),
                 "state": risk_coverage(s, today=today)} for s in subjects.values()]
    work = []
    for row in evidence["work"]:
        subject = subjects.get(row.get("subject_id"))
        state = queue_state(row, now=now,
                            paused=bool(subject and subject["lifecycle_status"] != "active"))
        work.append({"work_item_id": row["work_item_id"], "work_kind": row["work_kind"],
                     "subject_id": row.get("subject_id"), "state": state})
    outbox = [{"event_id": row["event_id"], "state": queue_state(row, now=now)}
              for row in evidence["outbox"]]
    findings = [r for r in coverage + work + outbox if r["state"] in ATTENTION_STATES]
    return {"captured_at": now.isoformat(), "inventory_complete": evidence["inventory_complete"],
            "status": "needs_review" if findings or not evidence["inventory_complete"]
                      else "no_findings_in_checked_scope",
            "risk_date_coverage": coverage, "work": work, "outbox": outbox,
            "findings": findings,
            "absolute_market_freshness": "unverified_calendar_policy_required",
            "limits": ["Date alignment does not prove value correctness or freshness.",
                       "Queue scope/maintenance and source cooldown require review before paging.",
                       "Risk lag may be transient during the daily pipeline; not an automatic incident.",
                       "Outbox has no dead-letter transition timestamp; do not invent dead-letter age."]}
