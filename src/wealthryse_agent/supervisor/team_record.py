"""The month-end team's runs in the supervisor's task record (agent.tasks), with the team unchanged.

After each run of the month_team job, one tree: the supervisor (source 'job'), the month-end
coordinator as the reporting team's lead, and under it one row for each team agent that acted on
an account in this run (data, analyst, writer, reviewer, visual QA), from the steps the team
already writes under the run's correlation id. The rows say what happened; they decide nothing,
and a record that cannot be written is logged and dropped, never raised into the team's run.
"""
from __future__ import annotations

import logging
import uuid
from collections import Counter
from typing import Any, Mapping, Sequence

from . import registry

LOGGER = logging.getLogger("wealthryse_agent.supervisor.team_record")
LEAD = "month_end_coordinator"
# The team's agent names (agent.month_steps) and their registry members; the coordinator's own steps
# are counted on the lead's row.
MEMBERS = {"data": "month_end_data", "analyst": "month_end_analyst", "writer": "month_end_writer",
           "reviewer": "month_end_reviewer", "visual_qa": "month_end_visual_qa"}
# A step's outcome as a task status: a reviewer's objection is a rejection, a hold waits for Ying.
STATUS = {"pass": "done", "skipped": "done", "objection": "rejected", "blocked": "needs_input",
          "waiting": "blocked", "failed": "failed"}
# One run covers every account; keep the tree well inside one write.
MAX_ROWS = 200


def _add_usage(total: dict[str, int], used: Mapping[str, Any] | None) -> None:
    for key, value in (used or {}).items():
        if isinstance(value, (int, float)):
            total[key] = total.get(key, 0) + int(value)


def month_team_tree(steps: Sequence[Mapping[str, Any]], *, correlation_id: str, live: bool, mode: str, period: str,
                    counts: Mapping[str, int], usage: Mapping[str, int],
                    error_code: str | None = None) -> list[dict[str, Any]]:
    """The rows for one month_team run, parents first."""
    root_id, lead_id = str(uuid.uuid4()), str(uuid.uuid4())
    common = {"root_id": root_id, "mode": "live" if live else "watch", "source": "job",
              "correlation_id": correlation_id[:64], "conversation_id": None}
    lead = registry.BY_NAME[LEAD]
    accounts = sorted({str(step["subject_id"]) for step in steps})
    coordinator_steps = Counter(str(step["outcome"]) for step in steps if step["agent_name"] == "coordinator")
    rows = [
        {**common, "task_id": root_id, "parent_id": None, "level": "supervisor", "team": None, "role": "supervisor",
         "autonomy": lead.autonomy, "status": "failed" if error_code else "done", "subject_id": None,
         "usage": {k: int(v) for k, v in usage.items()},
         "detail": {"job": "month_team", "mode": mode, "period": period, "stages": dict(counts),
                    "accounts": len(accounts), **({"error_code": error_code} if error_code else {})}},
        {**common, "task_id": lead_id, "parent_id": root_id, "level": "lead", "team": lead.team, "role": lead.name,
         "autonomy": lead.autonomy, "status": "failed" if error_code else "done", "subject_id": None,
         "detail": {"coordinator_steps": dict(coordinator_steps)}},
    ]
    acted: dict[tuple[str, str], dict[str, Any]] = {}
    for step in steps:
        member = MEMBERS.get(str(step["agent_name"]))
        if member is None:
            continue
        key = (str(step["subject_id"]), member)
        entry = acted.setdefault(key, {"outcomes": Counter(), "last": "", "usage": {}, "stage": "",
                                       "period_end": str(step["period_end"])[:10]})
        entry["outcomes"][str(step["outcome"])] += 1
        entry["last"] = str(step["outcome"])
        entry["stage"] = str(step["stage"])
        _add_usage(entry["usage"], step.get("usage"))
    for (subject_id, member_name), entry in sorted(acted.items())[:MAX_ROWS - len(rows)]:
        member = registry.BY_NAME[member_name]
        rows.append({**common, "task_id": str(uuid.uuid4()), "parent_id": lead_id,
                     "level": "checker" if member.kind == "checker" else "specialist", "team": member.team,
                     "role": member.name, "autonomy": member.autonomy, "status": STATUS.get(entry["last"], "done"),
                     "subject_id": subject_id if len(subject_id) <= 64 else None,
                     "pinned_as_of": entry["period_end"], "usage": entry["usage"],
                     "detail": {"stage": entry["stage"], "outcomes": dict(entry["outcomes"])}})
    return rows


def record_month_team(store: Any, correlation_id: str, *, live: bool, mode: str, period: str,
                      counts: Mapping[str, int], usage: Mapping[str, int], error_code: str | None = None) -> bool:
    """Write the run's tree. Never raises; False when nothing was written."""
    try:
        if not store.tasks_ready():
            return False
        steps = store.month_team_steps(correlation_id)
        rows = month_team_tree(steps, correlation_id=correlation_id, live=live, mode=mode, period=period,
                               counts=counts, usage=usage, error_code=error_code)
        written = bool(store.add_tasks(rows))
        if not written:
            LOGGER.warning("the month-end team's task record was refused")
        return written
    except Exception:
        LOGGER.warning("the month-end team's task record could not be written", exc_info=True)
        try:
            store.db.rollback()
        except Exception:
            pass
        return False
