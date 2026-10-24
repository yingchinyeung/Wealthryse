"""Input readiness: are a daily job's inputs for today's cycle ready before it starts?

Each daily job after ingestion calls `gate(<job>)` first. INPUT_READINESS_MODE says
what happens:

- off (the default, also when unset): nothing runs. No query, no log line.
- shadow: checks, logs one `INPUT_READINESS {json}` line and always carries on.
- enforce: the same, but a job whose inputs are not ready stops with exit code 75.
  With INPUT_READINESS_WAIT_SECONDS set, it first logs a "wait" line and checks again
  every INPUT_READINESS_RECHECK_SECONDS until the inputs are ready or the wait is over.
  A lineage run that is still running only delays: when the wait is over, the check
  runs once more without it, and the job carries on if the rest is ready.

The check reads evidence the earlier steps already leave in the database, in one
READ ONLY session (layer_freshness.open_read_only) within a hard time limit, and
judges it with workflow_readiness.readiness(), the dependency policy. The cycle is the
UTC day. institutional-daily asks IBKR for data up to yesterday, so ingestion and
reconciliation must cover yesterday; benchmarks, derived and risk must have completed
today, after the steps they read (date policy built with workflow_dates). Rows in the
shadow evidence ledger, once it has any for the cycle, must agree as well. Where a job
leaves lineage records (lineage.py, migration 071), its newest run must not have
crashed; reports are judged by month-end-factsheet's lineage run alone.

Nothing is written. `python -m ibkr_etl.input_readiness <job>` prints a dry run.
"""
from __future__ import annotations

import json
import math
import os
import sys
import threading
import time as monotonic_time
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Callable, Mapping

MODE_ENV = "INPUT_READINESS_MODE"
DATABASE_URL_ENV = "INPUT_READINESS_DATABASE_URL"  # optional override of DATABASE_URL
OFF, SHADOW, ENFORCE = "off", "shadow", "enforce"
OFF_VALUES = frozenset({"", "off", "false", "0", "no", "none", "disabled"})
LOG_PREFIX = "INPUT_READINESS "
EXIT_NOT_READY = 75  # EX_TEMPFAIL: try again once the inputs are there
POLICY_VERSION = "input-readiness-v2"
SUBJECT = "daily-cycle"  # one platform-wide cycle per tenant
DEFAULT_TENANT_ID = "00000000-0000-0000-0000-000000000001"
TIME_BUDGET_SECONDS = 10.0
DATA_LAG_DAYS = 1  # institutional-daily asks IBKR for data up to yesterday
BATCH_WINDOW = "3 hours"  # one execution of a job writes its rows within this
WAIT_ENV = "INPUT_READINESS_WAIT_SECONDS"  # enforce only; 0 (the default) means no wait
RECHECK_ENV = "INPUT_READINESS_RECHECK_SECONDS"
DEFAULT_RECHECK_SECONDS = 60.0
MIN_RECHECK_SECONDS = 5.0
MAX_WAIT_SECONDS = 1800.0
MAX_LISTED_SUBJECTS = 20

# The daily jobs after ingestion and the workflow stage each one produces.
JOB_STAGES = {
    "benchmark-daily": "benchmarks_fx",
    "sector-classification": "benchmarks_fx",
    "derived-daily": "derived",
    "risk-snapshots": "risk",
    "crisis-scenarios-daily": "risk",
    "month-end-factsheet": "reports",
    "analyst-assurance": "assurance",
}


def _work_items_sql(kind: str) -> str:
    # The newest run date institutional-daily queued, and how its items ended.
    return f"""
WITH latest AS (
    SELECT max(payload_json->>'run_date') AS run_date
    FROM institutional_work_items WHERE work_kind = '{kind}')
SELECT max(w.completed_at) AS completed_at, l.run_date AS through_date,
       count(w.work_item_id) AS planned,
       count(w.work_item_id) FILTER (WHERE w.status = 'succeeded') AS succeeded,
       count(w.work_item_id) FILTER (WHERE w.status IN ('failed', 'dead_letter', 'cancelled')) AS failed
FROM latest l
LEFT JOIN institutional_work_items w
  ON w.work_kind = '{kind}' AND w.payload_json->>'run_date' = l.run_date
GROUP BY l.run_date"""


# Each query returns one row: completed_at, through_date (or NULL), planned, succeeded,
# failed. No row, or planned = 0, means there is no evidence.
EVIDENCE: dict[str, tuple[str, str, str]] = {
    # stage: (layer, catalog key, query)
    "ingestion": ("bronze", "public.institutional_work_items", _work_items_sql("flex_statement_request")),
    "reconciliation": ("silver", "public.institutional_work_items", _work_items_sql("account_import")),
    "benchmarks_fx": ("silver", "public.ingestion_runs", """
SELECT completed_at, NULL AS through_date, 1 AS planned,
       CASE WHEN status = 'success' THEN 1 ELSE 0 END AS succeeded,
       CASE WHEN status = 'success' THEN 0 ELSE 1 END AS failed
FROM ingestion_runs
WHERE run_type = 'staging-yfinance-benchmark' AND status <> 'running'
ORDER BY run_id DESC LIMIT 1"""),
    # The newest calculation of each subject in derived-daily's newest execution; one
    # still running means the execution has not finished. A failed or blocked calculation
    # of a subject that is not live (lifecycle not 'active', so risk and reports skip it)
    # is waiting, not counted.
    "derived": ("silver", "public.subject_calculation_runs", f"""
WITH newest AS (SELECT max(coalesce(completed_at, started_at)) AS at FROM subject_calculation_runs),
batch AS (
    SELECT DISTINCT ON (r.subject_id) r.tenant_id, r.subject_id, r.status
    FROM subject_calculation_runs r, newest n
    WHERE coalesce(r.completed_at, r.started_at) > n.at - interval '{BATCH_WINDOW}'
    ORDER BY r.subject_id, coalesce(r.completed_at, r.started_at) DESC),
judged AS (
    SELECT b.subject_id, b.status,
           b.status IN ('failed', 'blocked') AND s.lifecycle_status IS DISTINCT FROM 'active' AS waiting
    FROM batch b
    LEFT JOIN platform_subjects s ON s.tenant_id = b.tenant_id AND s.subject_id = b.subject_id)
SELECT (SELECT max(completed_at) FROM subject_calculation_runs) AS completed_at, NULL AS through_date,
       count(*) FILTER (WHERE NOT waiting) AS planned,
       count(*) FILTER (WHERE NOT waiting AND status IN ('validated', 'published')) AS succeeded,
       count(*) FILTER (WHERE NOT waiting AND status IN ('failed', 'blocked')) AS failed,
       count(*) FILTER (WHERE waiting) AS waiting,
       array_agg(subject_id ORDER BY subject_id) FILTER (WHERE waiting) AS waiting_subjects
FROM judged"""),
    # The risk job stamps every snapshot it checks, changed or not.
    "risk": ("gold", "public.risk_analytics_snapshots", f"""
WITH checks AS (
    SELECT (timings_json->>'source_checked_at')::timestamptz AS checked_at,
           timings_json->>'source_check_status' AS check_status
    FROM risk_analytics_snapshots WHERE timings_json->>'source_checked_at' IS NOT NULL),
newest AS (SELECT max(checked_at) AS at FROM checks)
SELECT n.at AS completed_at, NULL AS through_date, count(c.checked_at) AS planned,
       count(c.checked_at) FILTER (WHERE c.check_status IS DISTINCT FROM 'blocked') AS succeeded,
       count(c.checked_at) FILTER (WHERE c.check_status = 'blocked') AS failed
FROM newest n LEFT JOIN checks c ON c.checked_at > n.at - interval '{BATCH_WINDOW}'
GROUP BY n.at"""),
}
# Stages with data-date evidence; the others are dated by when they completed.
DATA_DATED = frozenset({"ingestion", "reconciliation"})
LEDGER_READABLE_SQL = ("SELECT has_table_privilege(to_regclass('workflow_stage_evidence'), 'SELECT') "
                       "AS readable")

# Jobs whose lineage records (lineage.py; migration 071; LINEAGE_RECORDING=true) show whether
# their newest run has ended, by stage: (key in the stage's evidence, job, the job's
# replicaTimeout in seconds). "run" is the job behind the stage's own evidence; any other
# key is a second job the stage also needs. A run with no end after its replicaTimeout was
# stopped or lost its end record, and is not used.
LINEAGE_JOBS: dict[str, tuple[tuple[str, str, int], ...]] = {
    # institutional-daily loads the Kiwoom accounts after the IBKR work items its table
    # evidence counts, so its run is still open while those accounts' NAV comes in.
    "reconciliation": (("loads", "ibkrv2stg-institutional-daily", 3000),),
    "benchmarks_fx": (("sectors", "ibkrv2stg-sector-classification", 1800),),
    "risk": (("run", "ibkrv2stg-risk-snapshots", 3600),),
    "reports": (("run", "ibkrv2stg-month-end-factsheet", 3600),),
}
# Stages with no table evidence, judged by their lineage run alone. Without a usable run
# they are not checked, and the policy goes on to check the stages before them.
LINEAGE_ONLY = frozenset({"reports"})
# Second jobs that can delay a stage but never fail it: yesterday's sector
# classifications are good enough for a day, and a failed account load is judged by the
# work items and each subject's own checks.
SOFT_KEYS = frozenset({"sectors", "loads"})
# Jobs that run their steps as separate processes, each with its own lineage run: a run
# that ended this recently may be followed by the next step's START, so within the wait
# it holds like an open run. institutional-daily starts the Kiwoom loads seconds after
# the IBKR worker ends.
SETTLE_SECONDS = {"loads": 120}
LINEAGE_KEY = "lineage"  # where observe() puts the lineage runs among the observations
LINEAGE_NAMESPACE = "wealthryse"  # lineage.NAMESPACE
LINEAGE_READABLE_SQL = ("SELECT has_table_privilege(to_regclass('public.lineage_events'), 'SELECT') "
                        "AS readable")
# The newest START of the job, with its end event if there is one.
LINEAGE_RUN_SQL = f"""
SELECT s.run_id::text AS run_id, s.event_time AS started_at, e.event_type AS end_type,
       e.event_time AS ended_at, e.facets->>'exit_code' AS exit_code,
       e.facets->'error'->>'type' AS error_type
FROM public.lineage_events s
LEFT JOIN public.lineage_events e
  ON e.tenant_id = s.tenant_id AND e.run_id = s.run_id AND e.event_type <> 'START'
WHERE s.tenant_id = %s::uuid AND s.job_name = %s AND s.event_type = 'START'
  AND s.job_namespace = '{LINEAGE_NAMESPACE}'
ORDER BY s.event_time DESC LIMIT 1"""


def mode(environ: Mapping[str, str] | None = None) -> tuple[str, str | None]:
    """The mode, and a note when the value was not understood (then shadow)."""
    environ = os.environ if environ is None else environ
    value = str(environ.get(MODE_ENV, "")).strip().lower()
    if value in OFF_VALUES:
        return OFF, None
    if value in (SHADOW, ENFORCE):
        return value, None
    return SHADOW, f"unknown {MODE_ENV} value; ran as shadow"


def stage_for(job: str) -> str:
    from .workflow_readiness import PREREQUISITES

    stage = JOB_STAGES.get(job, job)
    if stage not in PREREQUISITES:
        raise ValueError(f"Unknown job or stage: {job}")
    return stage


def prerequisites(stage: str) -> list[str]:
    """Every stage `stage` depends on, directly or not, in policy order."""
    from .workflow_readiness import PREREQUISITES

    needed: set[str] = set()

    def visit(name: str) -> None:
        for parent in PREREQUISITES[name]:
            if parent not in needed:
                needed.add(parent)
                visit(parent)

    visit(stage)
    return [name for name in PREREQUISITES if name in needed]


def cycle_of(now: datetime) -> tuple[date, datetime]:
    if now.tzinfo is None:
        raise ValueError("Input readiness needs a timezone-aware time")
    current = now.astimezone(timezone.utc)
    return current.date(), datetime.combine(current.date(), time(0), tzinfo=timezone.utc)


def expected_dates(cycle: date) -> dict[str, date]:
    from .workflow_readiness import PREREQUISITES

    data_day = cycle - timedelta(days=DATA_LAG_DAYS)
    return {name: data_day if name in DATA_DATED else cycle for name in PREREQUISITES}


def _date_policy(tenant_id: str, cycle_key: str, cycle: date, stage: str) -> tuple[dict[str, date], str]:
    """The stage's expected dates through workflow_dates, as a persisted policy would give them."""
    from . import workflow_dates
    from .market_calendar import CALENDAR_VERSION
    from .workflow_ledger import digest

    record = workflow_dates.normalized_policy(
        tenant_id=tenant_id, cycle_key=cycle_key, subject_id=SUBJECT, cycle_cutoff=cycle,
        calendar_version=CALENDAR_VERSION, expected_dates=expected_dates(cycle))
    row = {**record, "policy_id": "00000000-0000-0000-0000-000000000000", "cycle_cutoff": cycle,
           "expected_dates_json": record["expected_dates"], "record_sha256": digest(record)}
    dates = workflow_dates.evaluation_dates(row, tenant_id=tenant_id, cycle_key=cycle_key,
                                            subject_id=SUBJECT, stage=stage, expected_date=cycle)
    return dates, row["record_sha256"]


def _count(row: Mapping[str, Any], key: str) -> int:
    try:
        return int(row.get(key) or 0)
    except (TypeError, ValueError):
        return 0


def _before(first: str | None, second: str | None) -> bool:
    """Whether `first` is earlier than `second`; False when either is unknown."""
    if not first or not second:
        return False
    return datetime.fromisoformat(first) < datetime.fromisoformat(second)


def _instant(value: Any) -> datetime:
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).strip())
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def judge(name: str, observation: Mapping[str, Any], *, now: datetime) -> dict[str, Any]:
    """One stage's evidence, judged. Plain JSON; `completed_at` is UTC ISO or None."""
    from . import layer_freshness

    layer, target, _ = EVIDENCE[name]
    if "error" in observation:
        return {"state": "unavailable", "error": observation["error"]}
    row = observation.get("row")
    planned = 0 if row is None else _count(row, "planned")
    waiting = 0 if row is None else _count(row, "waiting")
    # Only subjects that are waiting (not live) means there is nothing to wait for.
    if row is None or (planned == 0 and not waiting):
        return {"state": "missing"}
    succeeded, failed = _count(row, "succeeded"), _count(row, "failed")
    _, cycle_start = cycle_of(now)
    current = now.astimezone(timezone.utc)
    # Due now, and the newest completion must be from this cycle to be fresh.
    rule = layer_freshness.Rule(f"input.{name}", layer, target, layer_freshness.LOADED,
                                current.time(), current - cycle_start)
    result = layer_freshness.evaluate(rule, row.get("completed_at"), now=current)
    detail: dict[str, Any] = {
        "state": result["state"], "completed_at": result["observed"],
        "planned": planned, "succeeded": succeeded, "failed": failed,
    }
    if result.get("error"):
        detail["error"] = result["error"]
    if waiting:
        subjects = [str(item) for item in (row.get("waiting_subjects") or [])]
        detail.update(waiting=waiting, waiting_subjects=subjects[:MAX_LISTED_SUBJECTS])
    if failed:
        detail["status"] = "failed"
    elif succeeded < planned:
        detail.update(state="in_progress", status="running")
    else:
        detail["status"] = "succeeded"
    if name in DATA_DATED:
        detail["through_date"] = None if row.get("through_date") is None else str(row["through_date"])[:10]
    elif result["observed"]:
        detail["through_date"] = result["observed"][:10]
    return detail


def judge_run(job: str, row: Mapping[str, Any] | None, *, now: datetime, limit_seconds: int) -> dict[str, Any]:
    """A job's newest recorded run: running, finished, failed, or not_used (with why)."""
    if row is None:
        return {"job": job, "state": "not_used", "why": "no run recorded"}
    started = _instant(row["started_at"])
    end = row.get("end_type") or None
    detail: dict[str, Any] = {"job": job, "run_id": row.get("run_id"), "started_at": started.isoformat(),
                              "ended_at": None, "end": end}
    current = now.astimezone(timezone.utc)
    if end is None:
        if started > current - timedelta(seconds=limit_seconds):
            detail["state"] = "running"
        else:
            detail.update(state="not_used", why=f"no end recorded within {limit_seconds} seconds")
        return detail
    detail["ended_at"] = _instant(row["ended_at"]).isoformat()
    exit_code, error = row.get("exit_code"), row.get("error_type")
    if exit_code not in (None, ""):
        detail["exit_code"] = exit_code
    if error:
        detail["error"] = error
    _, cycle_start = cycle_of(now)
    if _instant(row["ended_at"]) < cycle_start:
        detail.update(state="not_used", why="newest run ended before this cycle")
    # FAIL from a non-zero return (no exception) means the job ran to its end; the
    # stage's own evidence judges what it wrote. An exception or ABORT means it did not.
    elif end == "COMPLETE" or (end == "FAIL" and exit_code not in (None, "") and not error):
        detail["state"] = "finished"
    else:
        detail["state"] = "failed"
    return detail


def _runs(name: str, lineage: Mapping[str, Any] | None, *, now: datetime,
          hold_open_runs: bool) -> dict[str, dict[str, Any]]:
    """The judged lineage runs for one stage, by evidence key. Without hold_open_runs a
    run still in progress is not used, so the stage is judged as if it had no record."""
    found: dict[str, dict[str, Any]] = {}
    for key, job, limit in LINEAGE_JOBS.get(name, ()):
        if lineage is None:
            found[key] = {"job": job, "state": "not_used", "why": "lineage not read"}
        elif "error" in lineage:
            found[key] = {"job": job, "state": "not_used", "why": f"lineage unavailable ({lineage['error']})"}
        else:
            try:
                found[key] = judge_run(job, (lineage.get("runs") or {}).get(job), now=now, limit_seconds=limit)
            except (KeyError, TypeError, ValueError) as error:
                found[key] = {"job": job, "state": "not_used", "why": f"unreadable record ({type(error).__name__})"}
        if found[key]["state"] == "running" and not hold_open_runs:
            found[key].update(state="not_used",
                              why=f"still running after the wait (started {found[key]['started_at']})")
        settle = SETTLE_SECONDS.get(key)
        if (settle and hold_open_runs and found[key]["state"] in ("finished", "failed")
                and _instant(found[key]["ended_at"]) > now.astimezone(timezone.utc) - timedelta(seconds=settle)):
            found[key].update(state="running", settling=settle)
    return found


def _judge_by_run(run: Mapping[str, Any], cycle: date) -> dict[str, Any]:
    """A lineage-only stage, judged by its run. Without a usable run it is not checked and
    passes on to the stages before it."""
    state = run["state"]
    if state == "running":
        return {"state": "in_progress", "status": "running"}
    if state in ("finished", "failed"):
        return {"state": "fresh", "status": "succeeded" if state == "finished" else "failed",
                "completed_at": run["ended_at"], "through_date": run["ended_at"][:10]}
    return {"state": "not_checked", "status": "succeeded", "completed_at": None,
            "through_date": cycle.isoformat()}


def open_runs(decision: Mapping[str, Any]) -> list[str]:
    """The jobs whose lineage run was still in progress in this decision."""
    return sorted(part["job"] for detail in (decision.get("evidence") or {}).values() if isinstance(detail, Mapping)
                  for part in detail.values()
                  if isinstance(part, Mapping) and part.get("job") and part.get("state") == "running")


def _ended_later(observation: Mapping[str, Any], own: Mapping[str, Any] | None) -> Mapping[str, Any]:
    """The evidence completed when the run that wrote it ended, if that run finished
    after its last row; otherwise unchanged."""
    row = observation.get("row")
    if not own or own.get("state") != "finished" or not row or row.get("completed_at") is None:
        return observation
    try:
        if _instant(own["started_at"]) <= _instant(row["completed_at"]) < _instant(own["ended_at"]):
            return {**observation, "row": {**row, "completed_at": own["ended_at"]}}
    except (KeyError, TypeError, ValueError):
        pass
    return observation


def decide(stage: str, observations: Mapping[str, Mapping[str, Any]], *, now: datetime,
           tenant_id: str = DEFAULT_TENANT_ID, ledger: Mapping[str, Any] | None = None,
           hold_open_runs: bool = True) -> dict[str, Any]:
    """The decision for `stage` from observed evidence. Pure; raises on a bad policy.
    hold_open_runs=False judges as if runs still in progress had left no record."""
    from .workflow_ledger import digest
    from .workflow_readiness import PREREQUISITES, StageEvidence, readiness

    cycle, _ = cycle_of(now)
    cycle_key = f"daily:{cycle.isoformat()}"
    dates, policy_sha256 = _date_policy(tenant_id, cycle_key, cycle, stage)
    needed = prerequisites(stage)
    lineage = observations.get(LINEAGE_KEY)
    details: dict[str, dict[str, Any]] = {}
    evidence: dict[str, StageEvidence] = {}
    extra: list[str] = []
    notes: list[str] = []
    for name in needed:
        if name not in EVIDENCE and name not in LINEAGE_ONLY:
            details[name] = {"state": "no_source"}
            continue
        runs = _runs(name, lineage, now=now, hold_open_runs=hold_open_runs)
        if name in LINEAGE_ONLY:
            detail = _judge_by_run(runs["run"], cycle)
        else:
            observation = _ended_later(observations.get(name, {"error": "not measured"}), runs.get("run"))
            detail = judge(name, observation, now=now)
        detail["expected"] = dates[name].isoformat()
        details[name] = detail
        if detail.get("waiting"):
            notes.append(f"{name}: {detail['waiting']} subject(s) not live (onboarding or not active) "
                         "failed or blocked; waiting, not counted")
        for key, run in runs.items():
            detail[key] = run
            job, state = run["job"], run["state"]
            failed = f"did not finish ({run.get('error') or run.get('end')})"
            if state == "running" and run.get("settling"):
                extra.append(f"{name}: {job} ended at {run['ended_at']}; waiting {run['settling']} seconds "
                             "for its next step")
                if detail.get("status") == "succeeded":
                    detail.update(state="in_progress", status="running")
            elif state == "running":
                extra.append(f"{name}: {job} is still running (started {run['started_at']})")
                if detail.get("status") == "succeeded":
                    detail.update(state="in_progress", status="running")
            elif state == "failed" and key not in SOFT_KEYS:
                extra.append(f"{name}: {job} {failed}")
                if detail.get("status") == "succeeded":
                    detail["status"] = "failed"
            elif state in ("failed", "not_used"):
                if key in SOFT_KEYS or name in LINEAGE_ONLY:
                    judged_by = f"{key if key in SOFT_KEYS else name} not checked"
                else:
                    judged_by = f"judged by {EVIDENCE[name][1]} alone"
                notes.append(f"{name}: {job} {failed if state == 'failed' else run['why']}; {judged_by}")
        if detail["state"] == "unavailable":
            extra.append(f"{name}: evidence unavailable ({detail.get('error', 'unknown')})")
            continue
        if detail["state"] == "future":
            extra.append(f"{name}: completion time is in the future")
        if detail.get("status") not in ("succeeded", "failed") or not detail.get("through_date"):
            continue
        try:
            through = date.fromisoformat(detail["through_date"])
        except ValueError:
            extra.append(f"{name}: unreadable observation date")
            continue
        succeeded = detail["status"] == "succeeded"
        evidence[name] = StageEvidence(SUBJECT, name, "succeeded" if succeeded else "failed", succeeded,
                                       through, digest({"stage": name, **detail}), {})
    # A step that completed before one it reads has not used that step's latest output.
    for name, item in evidence.items():
        hashes = {}
        for parent in PREREQUISITES[name]:
            upstream = evidence.get(parent)
            if upstream is None:
                continue
            ran_before = _before(details[name].get("completed_at"), details[parent].get("completed_at"))
            hashes[parent] = "0" * 64 if ran_before else upstream.output_sha256
        evidence[name] = StageEvidence(item.subject_id, item.stage, item.status, item.validated,
                                       item.through_date, item.output_sha256, hashes)
    notes += _sector_order_notes(details, PREREQUISITES)
    ready, reasons = readiness(stage, subject_id=SUBJECT, expected_date=cycle, evidence=evidence,
                               expected_dates=dates)
    unavailable = {reason.split(":")[0] for reason in extra if "evidence unavailable" in reason}
    found = [r for r in reasons if not (r.endswith("missing completion evidence") and r.split(":")[0] in unavailable)]
    found += extra
    if ledger is not None and ledger.get("rows"):
        found += [f"ledger: {reason}" for reason in ledger.get("reasons", ())]
    elif ledger is not None and ledger.get("error"):
        found.append(f"ledger: unavailable ({ledger['error']})")
    found = sorted(set(found))
    return {
        "stage": stage, "cycle": cycle.isoformat(), "ready": ready and not found,
        "reasons": found, "notes": sorted(set(notes)), "evidence": details,
        "ledger": dict(ledger) if ledger is not None else None,
        "date_policy_sha256": policy_sha256, "policy_version": POLICY_VERSION,
    }


def _sector_order_notes(details: Mapping[str, Mapping[str, Any]], prerequisites_of: Mapping) -> list[str]:
    """Sectors are soft: an order problem with them is a note, never a reason."""
    sectors = (details.get("benchmarks_fx") or {}).get("sectors") or {}
    if sectors.get("state") != "finished":
        return []
    ended, job = sectors.get("ended_at"), sectors.get("job")
    notes = []
    if _before(ended, (details.get("reconciliation") or {}).get("completed_at")):
        notes.append(f"benchmarks_fx: {job} ended before reconciliation; it classified the earlier positions")
    for name, detail in details.items():
        if "benchmarks_fx" in prerequisites_of.get(name, ()) and _before(detail.get("completed_at"), ended):
            notes.append(f"{name}: finished before {job} ended; it used the earlier sector classifications")
    return notes


def _fetch(connection: Any, sql: str, params: tuple | None = None) -> Mapping[str, Any] | None:
    # Each query under a savepoint, so a denied one does not abort the rest.
    with connection.transaction():
        row = (connection.execute(sql) if params is None else connection.execute(sql, params)).fetchone()
    return None if row is None else dict(row)


def observe(connection: Any, stage: str, *, now: datetime, tenant_id: str) -> tuple[dict, dict]:
    """Read the evidence for `stage`'s prerequisites, the lineage runs that bear on them and
    the ledger. Never raises."""
    from .layer_freshness import _error_label

    observations: dict[str, dict[str, Any]] = {}
    jobs: list[str] = []
    for name in prerequisites(stage):
        jobs += [job for _, job, _ in LINEAGE_JOBS.get(name, ())]
        if name not in EVIDENCE:
            continue
        try:
            observations[name] = {"row": _fetch(connection, EVIDENCE[name][2])}
        except Exception as error:  # noqa: BLE001 - reported as unavailable
            observations[name] = {"error": _error_label(error)}
    if jobs:
        observations[LINEAGE_KEY] = read_lineage(connection, jobs, tenant_id=tenant_id)
    return observations, read_ledger(connection, stage, now=now, tenant_id=tenant_id)


def read_lineage(connection: Any, jobs: list[str], *, tenant_id: str) -> dict[str, Any]:
    """Each job's newest recorded run, if lineage_events exists and is readable."""
    from .layer_freshness import _error_label

    try:
        allowed = _fetch(connection, LINEAGE_READABLE_SQL)
        if not allowed or not allowed.get("readable"):
            return {"error": "lineage_events not present or not readable"}
        return {"runs": {job: _fetch(connection, LINEAGE_RUN_SQL, (tenant_id, job)) for job in jobs}}
    except Exception as error:  # noqa: BLE001 - lineage is optional evidence
        return {"error": _error_label(error)}


def read_ledger(connection: Any, stage: str, *, now: datetime, tenant_id: str) -> dict[str, Any]:
    """The shadow ledger's verdict for this cycle, if the table exists, is readable and has rows."""
    from .layer_freshness import _error_label

    try:
        allowed = _fetch(connection, LEDGER_READABLE_SQL)
        if not allowed or not allowed.get("readable"):
            return {"rows": 0, "note": "not present or not readable"}
        from . import workflow_ledger

        cycle, _ = cycle_of(now)
        scope = dict(tenant_id=tenant_id, cycle_key=f"daily:{cycle.isoformat()}", subject_id=SUBJECT)
        with connection.transaction():
            current = workflow_ledger.heads(connection, **scope)
        if not current:
            return {"rows": 0}
        dates, _ = _date_policy(tenant_id, scope["cycle_key"], cycle, stage)
        ready, reasons = workflow_ledger.evaluate_heads(current, **scope, stage=stage, expected_date=cycle,
                                                        expected_dates=dates)
        return {"rows": len(current), "ready": ready, "reasons": list(reasons)}
    except Exception as error:  # noqa: BLE001
        return {"rows": 0, "error": _error_label(error)}


def check(job: str, *, environ: Mapping[str, str] | None = None, now: datetime | None = None,
          time_budget_seconds: float = TIME_BUDGET_SECONDS,
          connect: Callable[[str, str], Any] | None = None, hold_open_runs: bool = True) -> dict[str, Any]:
    """Measure and decide within a hard time limit, connection included. Never raises;
    any problem is a reason, so the decision is then not ready."""
    environ = os.environ if environ is None else environ
    now = now or datetime.now(timezone.utc)
    started = monotonic_time.monotonic()
    base: dict[str, Any] = {"job": job, "checked_at": now.astimezone(timezone.utc).isoformat()}
    try:
        stage = stage_for(job)
        base["stage"] = stage
        database_url = str(environ.get(DATABASE_URL_ENV) or environ.get("DATABASE_URL") or "").strip()
        tenant_id = str(environ.get("PORTFOLIO_TENANT_ID") or DEFAULT_TENANT_ID).strip()
        if not database_url:
            decision = decide(stage, {name: {"error": "database not configured"}
                                      for name in (*EVIDENCE, LINEAGE_KEY)}, now=now, tenant_id=tenant_id,
                              hold_open_runs=hold_open_runs)
        else:
            if connect is None:
                from .layer_freshness import open_read_only as connect
            outcome: list[Any] = []
            done = threading.Event()

            def work() -> None:
                session = None
                try:
                    session = connect(database_url, tenant_id)
                    outcome.append(observe(session, stage, now=now, tenant_id=tenant_id))
                except BaseException as error:  # noqa: BLE001 - reported below
                    from .layer_freshness import _error_label
                    outcome.append(_error_label(error))
                finally:
                    if session is not None:
                        try:
                            session.rollback()
                            session.close()
                        except Exception:  # noqa: BLE001
                            pass
                    done.set()

            threading.Thread(target=work, name="input-readiness", daemon=True).start()
            if not done.wait(time_budget_seconds):
                outcome = [f"TimeoutError (time budget of {time_budget_seconds:g} seconds)"]
            if outcome and isinstance(outcome[0], tuple):
                observations, ledger = outcome[0]
            else:
                failure = outcome[0] if outcome else "no result"
                observations, ledger = {name: {"error": failure} for name in (*EVIDENCE, LINEAGE_KEY)}, None
            decision = decide(stage, observations, now=now, tenant_id=tenant_id, ledger=ledger,
                              hold_open_runs=hold_open_runs)
        base.update(decision)
    except Exception as error:  # noqa: BLE001 - a broken check is a reason, never a crash
        base.update(ready=False, reasons=[f"check failed: {type(error).__name__}: {error}"[:300]])
    base["elapsed_ms"] = int((monotonic_time.monotonic() - started) * 1000)
    return base


def _seconds(environ: Mapping[str, str], name: str, default: float) -> float | None:
    """A number of seconds from the environment; None when it is not one."""
    text = str(environ.get(name, "")).strip()
    if not text:
        return default
    try:
        value = float(text)
    except ValueError:
        return None
    return value if math.isfinite(value) and value >= 0 else None


def wait_policy(environ: Mapping[str, str]) -> tuple[float, float, list[str]]:
    """How long enforce keeps checking before it stops, the pause between checks, and a
    note for each value not taken as given. A value not understood means no wait."""
    notes: list[str] = []
    wait = _seconds(environ, WAIT_ENV, 0.0)
    if wait is None:
        return 0.0, DEFAULT_RECHECK_SECONDS, [f"{WAIT_ENV} not understood; no wait"]
    if wait > MAX_WAIT_SECONDS:
        notes.append(f"{WAIT_ENV} above {MAX_WAIT_SECONDS:g}; waits {MAX_WAIT_SECONDS:g} seconds")
        wait = MAX_WAIT_SECONDS
    interval = _seconds(environ, RECHECK_ENV, DEFAULT_RECHECK_SECONDS)
    if interval is None:
        notes.append(f"{RECHECK_ENV} not understood; rechecks every {DEFAULT_RECHECK_SECONDS:g} seconds")
        interval = DEFAULT_RECHECK_SECONDS
    return wait, max(MIN_RECHECK_SECONDS, interval), notes


def _log(out: Callable[[str], None] | None, decision: Mapping[str, Any]) -> None:
    line = LOG_PREFIX + json.dumps(decision, sort_keys=True, separators=(",", ":"), default=str)
    try:
        (out or (lambda text: print(text, flush=True)))(line)
    except Exception:  # noqa: BLE001 - logging must not fail the job
        pass


def gate(job: str, *, environ: Mapping[str, str] | None = None, now: datetime | None = None,
         out: Callable[[str], None] | None = None, sleep: Callable[[float], None] | None = None,
         **options: Any) -> dict[str, Any] | None:
    """Call first in a job's main(). Off: returns None and does nothing else.

    Only enforce waits: when not ready and INPUT_READINESS_WAIT_SECONDS is above 0, it logs
    a "wait" line, then checks again (each check with its own session and time limit) until
    ready or the wait is over, and logs the final decision. A lineage run still in progress
    only delays: if one is open when the wait is over, it checks once more as if that run had
    left no record, and that check decides."""
    environ = os.environ if environ is None else environ
    selected, note = mode(environ)
    if selected == OFF:
        return None
    decision = check(job, environ=environ, now=now, **options)
    labels: dict[str, Any] = {"mode": selected, **({"mode_note": note} if note else {})}
    if selected == ENFORCE:
        wait, interval, wait_notes = wait_policy(environ)
        first, attempts, waited = decision, 1, 0.0
        if not decision.get("ready") and wait > 0:
            _log(out, {**decision, **labels, "decision": "wait", "wait_seconds": wait,
                       "recheck_seconds": interval})
            pause = sleep or monotonic_time.sleep
            started = monotonic_time.monotonic()
            while waited < wait and monotonic_time.monotonic() - started < wait:
                step = min(interval, wait - waited)
                pause(step)
                waited += step
                attempts += 1
                later = None if now is None else now + timedelta(seconds=waited)
                decision = check(job, environ=environ, now=later, **options)
                if decision.get("ready"):
                    break
        if not decision.get("ready") and open_runs(decision):
            attempts += 1
            later = None if now is None else now + timedelta(seconds=waited)
            decision = check(job, environ=environ, now=later, hold_open_runs=False, **options)
        labels.update(attempts=attempts, waited_seconds=round(waited, 1))
        if attempts > 1:
            labels["first_reasons"] = first.get("reasons", [])
        if wait_notes:
            labels["notes"] = [*decision.get("notes", []), *wait_notes]
    decision.update(labels)
    stop = selected == ENFORCE and not decision.get("ready")
    decision["decision"] = "stop" if stop else "continue"
    _log(out, decision)
    if stop:
        raise SystemExit(EXIT_NOT_READY)
    return decision


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 1:
        print("usage: python -m ibkr_etl.input_readiness <job or stage>", file=sys.stderr)
        print("jobs: " + ", ".join(sorted(JOB_STAGES)), file=sys.stderr)
        return 2
    print(json.dumps(check(argv[0]), indent=1, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
