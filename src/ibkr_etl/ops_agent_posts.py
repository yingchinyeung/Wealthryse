"""The operations agent's diagnoses, as the platform shows them.

The operations agent (wealthryse_agent.operations) investigates the autonomous
administrator's open incidents and writes each diagnosis to
agent.ops_investigations. It cannot write platform tables, so the platform
does the showing: the administrator posts each new diagnosis into its system
updates conversation (under its own lock, so message numbering stays in
order), and the System health page lists open incidents with the latest
diagnosis for each.

Both read the agent table through the platform role's grant
(scripts/install_agent_schema.py --platform-reader). Until that grant and the
table exist, both quietly show nothing from the agent.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Callable

LOGGER = logging.getLogger(__name__)

# Diagnoses older than this are not posted: an incident that resolved long ago
# needs no message now.
POST_WINDOW = "2 days"
MAX_POSTS_PER_RUN = 10

_UNPOSTED = f"""
    SELECT i.investigation_id, i.incident_id, i.incident_key, i.status, i.diagnosis, i.pull_request
    FROM agent.ops_investigations i
    WHERE i.tenant_id = app_tenant_id()
      AND i.created_at >= now() - interval '{POST_WINDOW}'
      AND NOT EXISTS (
          SELECT 1 FROM admin_chat_messages m
          WHERE m.conversation_id = %s
            AND m.response_json ->> 'investigation_id' = i.investigation_id::text
      )
    ORDER BY i.investigation_id
    LIMIT {MAX_POSTS_PER_RUN}
"""

_OPEN_WITH_DIAGNOSIS = """
    SELECT s.incident_id, s.incident_key, s.check_id, s.severity, s.status, s.description,
           s.first_seen_at, s.last_seen_at, s.occurrence_count,
           d.status AS diagnosis_status, d.diagnosis, d.pull_request, d.created_at AS diagnosed_at
    FROM system_incidents s
    LEFT JOIN LATERAL (
        SELECT i.status, i.diagnosis, i.pull_request, i.created_at
        FROM agent.ops_investigations i
        WHERE i.tenant_id = s.tenant_id AND i.incident_id = s.incident_id
          AND i.episode_started_at = s.first_seen_at
        ORDER BY i.investigation_id DESC LIMIT 1
    ) d ON true
    WHERE s.status IN ('open', 'acknowledged')
    ORDER BY CASE s.severity WHEN 'critical' THEN 0 WHEN 'warning' THEN 1 ELSE 2 END,
             s.last_seen_at DESC
    LIMIT 20
"""

_OPEN_ONLY = """
    SELECT incident_id, incident_key, check_id, severity, status, description,
           first_seen_at, last_seen_at, occurrence_count
    FROM system_incidents
    WHERE status IN ('open', 'acknowledged')
    ORDER BY CASE severity WHEN 'critical' THEN 0 WHEN 'warning' THEN 1 ELSE 2 END,
             last_seen_at DESC
    LIMIT 20
"""


def _json(value: Any) -> Any:
    if isinstance(value, (str, bytes)):
        try:
            return json.loads(value)
        except ValueError:
            return None
    return value


def _agent_rows(connection, query: str, params: tuple = ()) -> list[dict] | None:
    """Rows from a query on the agent table, or None when it is not readable yet.

    Runs in a savepoint, so a missing table or grant leaves the caller's
    transaction usable.
    """
    import psycopg

    try:
        with connection.transaction():
            return list(connection.execute(query, params).fetchall())
    except (psycopg.errors.UndefinedTable, psycopg.errors.InvalidSchemaName,
            psycopg.errors.InsufficientPrivilege) as error:
        LOGGER.info("operations agent diagnoses are not readable yet: %s", type(error).__name__)
        return None


def investigation_message(row: dict) -> tuple[str, dict]:
    """The admin chat message for one investigation: its text and structured response."""
    diagnosis = _json(row.get("diagnosis")) or {}
    pull_request = _json(row.get("pull_request"))
    summary = str(diagnosis.get("summary") or "").strip()
    cause = str(diagnosis.get("likely_cause") or "").strip()
    steps = [str(step).strip() for step in diagnosis.get("owner_steps") or [] if str(step).strip()]
    diagnosed = row.get("status") == "diagnosed"
    lead = (f"Operations agent diagnosis for {row['incident_key']}: " if diagnosed
            else f"The operations agent could not pin down the cause of {row['incident_key']}: ")
    parts = [lead + (summary or "no summary was produced.")]
    if cause:
        parts.append(f"Likely cause: {cause}")
    if steps:
        parts.append("What to do: " + " ".join(f"{n}. {step}" for n, step in enumerate(steps, 1)))
    if pull_request and pull_request.get("url"):
        parts.append(f"I opened a draft pull request with a proposed fix for review: {pull_request['url']}")
    limitations = ["The operations agent reads telemetry only; it changed nothing."]
    if not diagnosed:
        limitations.append("The evidence did not support a confident cause.")
    if diagnosis.get("unread_sources"):
        limitations.append("Some telemetry could not be read: " + ", ".join(map(str, diagnosis["unread_sources"])) + ".")
    if pull_request:
        limitations.append("The draft pull request has not been run or reviewed; a person must review it before merging.")
    response = {
        "title": "Incident diagnosis" if diagnosed else "Incident diagnosis inconclusive",
        "message": "\n\n".join(parts[1:]) or summary,
        "status_points": [
            {"text": str(item.get("finding") or ""), "source_id": str(item.get("source_id") or "")}
            for item in diagnosis.get("evidence") or [] if item.get("finding")
        ],
        "proposed_action_type": "none",
        "action_reason": "",
        "requires_owner_approval": False,
        "limitations": limitations,
        "proactive": True,
        "investigation_id": int(row["investigation_id"]),
        "confidence": diagnosis.get("confidence"),
        "owner_steps": steps,
        "pull_request": ({"number": pull_request.get("number"), "url": pull_request.get("url")}
                         if pull_request else None),
    }
    return "\n\n".join(parts), response


def post_new_diagnoses(connection, conversation_id: str,
                       append: Callable[[str, str, dict, int | None], None]) -> int:
    """Post each diagnosis not yet in the updates conversation; returns how many were posted."""
    rows = _agent_rows(connection, _UNPOSTED, (conversation_id,))
    for row in rows or []:
        content, response = investigation_message(row)
        append(content, "proactive_update", response, int(row["incident_id"]))
    return len(rows or [])


_UNPOSTED_CONNECTION_REPORTS = f"""
    SELECT r.report_id, r.state, r.title, r.body, r.owner_steps
    FROM agent.connection_reports r
    WHERE r.tenant_id = app_tenant_id()
      AND r.created_at >= now() - interval '{POST_WINDOW}'
      AND NOT EXISTS (
          SELECT 1 FROM admin_chat_messages m
          WHERE m.conversation_id = %s
            AND m.response_json ->> 'connection_report_id' = r.report_id::text
      )
    ORDER BY r.report_id
    LIMIT {MAX_POSTS_PER_RUN}
"""


def connection_report_message(row: dict) -> tuple[str, dict]:
    """The admin chat message for one connection agent report."""
    steps = [str(step).strip() for step in _json(row.get("owner_steps")) or [] if str(step).strip()]
    parts = [str(row["body"]).strip()]
    if steps:
        parts.append("What to do: " + " ".join(f"{n}. {step}" for n, step in enumerate(steps, 1)))
    response = {
        "title": str(row["title"]),
        "message": "\n\n".join(parts),
        "status_points": [],
        "proposed_action_type": "none",
        "action_reason": "",
        "requires_owner_approval": False,
        "limitations": ["The connection agent only queues a login's first check; every later check is yours, "
                        "because IBKR locks a token after repeated failures."],
        "proactive": True,
        "connection_report_id": int(row["report_id"]),
        "connection_state": str(row["state"]),
        "owner_steps": steps,
    }
    return "\n\n".join(parts), response


def post_connection_reports(connection, conversation_id: str,
                            append: Callable[[str, str, dict, int | None], None]) -> int:
    """Post each connection agent report not yet in the updates conversation; returns how many."""
    rows = _agent_rows(connection, _UNPOSTED_CONNECTION_REPORTS, (conversation_id,))
    for row in rows or []:
        content, response = connection_report_message(row)
        append(content, "proactive_update", response, None)
    return len(rows or [])


def open_incidents(connection) -> list[dict]:
    """Open and acknowledged incidents, each with the agent's latest diagnosis when there is one."""
    rows = _agent_rows(connection, _OPEN_WITH_DIAGNOSIS)
    if rows is None:
        rows = [dict(row, diagnosis=None, diagnosis_status=None, pull_request=None, diagnosed_at=None)
                for row in connection.execute(_OPEN_ONLY).fetchall()]
    incidents = []
    for row in rows:
        diagnosis = _json(row.get("diagnosis")) or None
        pull_request = _json(row.get("pull_request")) or None
        incidents.append({
            "incident_key": row["incident_key"],
            "check_id": row["check_id"],
            "severity": row["severity"],
            "status": row["status"],
            "description": row["description"],
            "first_seen_at": row["first_seen_at"],
            "last_seen_at": row["last_seen_at"],
            "occurrence_count": row["occurrence_count"],
            "diagnosis": None if diagnosis is None else {
                "status": row.get("diagnosis_status"),
                "summary": diagnosis.get("summary"),
                "likely_cause": diagnosis.get("likely_cause"),
                "confidence": diagnosis.get("confidence"),
                "owner_steps": diagnosis.get("owner_steps") or [],
                "evidence": diagnosis.get("evidence") or [],
                "diagnosed_at": str(row.get("diagnosed_at") or ""),
                "pull_request_url": (pull_request or {}).get("url"),
            },
        })
    return incidents
