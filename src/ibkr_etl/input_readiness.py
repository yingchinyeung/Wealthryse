"""Input readiness: are a daily job's inputs for today's cycle ready before it starts?

Each daily job after ingestion calls `gate(<job>)` first. INPUT_READINESS_MODE says
what happens:

- off (the default, also when unset): nothing runs. No query, no log line.
- shadow: checks, logs one `INPUT_READINESS {json}` line and always carries on.
- enforce: the same, but a job whose inputs are not ready stops with exit code 75.

The check reads evidence the earlier steps already leave in the database, in one
READ ONLY session (layer_freshness.open_read_only) within a hard time limit, and
judges it with workflow_readiness.readiness(), the dependency policy. The cycle is the
UTC day. institutional-daily asks IBKR for data up to yesterday, so ingestion and
reconciliation must cover yesterday; benchmarks, derived and risk must have completed
today, after the steps they read (date policy built with workflow_dates). Rows in the
shadow evidence ledger, once it has any for the cycle, must agree as well.

Nothing is written. `python -m ibkr_etl.input_readiness <job>` prints a dry run.
"""
from __future__ import annotations

import json
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
POLICY_VERSION = "input-readiness-v1"
SUBJECT = "daily-cycle"  # one platform-wide cycle per tenant
DEFAULT_TENANT_ID = "00000000-0000-0000-0000-000000000001"
TIME_BUDGET_SECONDS = 10.0
DATA_LAG_DAYS = 1  # institutional-daily asks IBKR for data up to yesterday
BATCH_WINDOW = "3 hours"  # one execution of a job writes its rows within this

# The daily jobs after ingestion and the workflow stage each one produces.
JOB_STAGES = {
    "benchmark-daily": "benchmarks_fx",
    "sector-classification": "benchmarks_fx",
    "derived-daily": "derived",
    "risk-snapshots": "risk",
    "crisis-scenarios-daily": "risk",
    "month-end-factsheet": "reports",
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
    # still running means the execution has not finished.
    "derived": ("silver", "public.subject_calculation_runs", f"""
WITH newest AS (SELECT max(coalesce(completed_at, started_at)) AS at FROM subject_calculation_runs),
batch AS (
    SELECT DISTINCT ON (r.subject_id) r.subject_id, r.status
    FROM subject_calculation_runs r, newest n
    WHERE coalesce(r.completed_at, r.started_at) > n.at - interval '{BATCH_WINDOW}'
    ORDER BY r.subject_id, coalesce(r.completed_at, r.started_at) DESC)
SELECT (SELECT max(completed_at) FROM subject_calculation_runs) AS completed_at, NULL AS through_date,
       count(*) AS planned,
       count(*) FILTER (WHERE status IN ('validated', 'published')) AS succeeded,
       count(*) FILTER (WHERE status IN ('failed', 'blocked')) AS failed
FROM batch"""),
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


def judge(name: str, observation: Mapping[str, Any], *, now: datetime) -> dict[str, Any]:
    """One stage's evidence, judged. Plain JSON; `completed_at` is UTC ISO or None."""
    from . import layer_freshness

    layer, target, _ = EVIDENCE[name]
    if "error" in observation:
        return {"state": "unavailable", "error": observation["error"]}
    row = observation.get("row")
    planned = 0 if row is None else _count(row, "planned")
    if row is None or planned == 0:
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


def decide(stage: str, observations: Mapping[str, Mapping[str, Any]], *, now: datetime,
           tenant_id: str = DEFAULT_TENANT_ID, ledger: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """The decision for `stage` from observed evidence. Pure; raises on a bad policy."""
    from .workflow_ledger import digest
    from .workflow_readiness import PREREQUISITES, StageEvidence, readiness

    cycle, _ = cycle_of(now)
    cycle_key = f"daily:{cycle.isoformat()}"
    dates, policy_sha256 = _date_policy(tenant_id, cycle_key, cycle, stage)
    needed = prerequisites(stage)
    details: dict[str, dict[str, Any]] = {}
    evidence: dict[str, StageEvidence] = {}
    extra: list[str] = []
    for name in needed:
        if name not in EVIDENCE:
            details[name] = {"state": "no_source"}
            continue
        detail = judge(name, observations.get(name, {"error": "not measured"}), now=now)
        detail["expected"] = dates[name].isoformat()
        details[name] = detail
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
        "reasons": found, "evidence": details, "ledger": dict(ledger) if ledger is not None else None,
        "date_policy_sha256": policy_sha256, "policy_version": POLICY_VERSION,
    }


def _fetch(connection: Any, sql: str) -> Mapping[str, Any] | None:
    # Each query under a savepoint, so a denied one does not abort the rest.
    with connection.transaction():
        row = connection.execute(sql).fetchone()
    return None if row is None else dict(row)


def observe(connection: Any, stage: str, *, now: datetime, tenant_id: str) -> tuple[dict, dict]:
    """Read the evidence for `stage`'s prerequisites and the ledger. Never raises."""
    from .layer_freshness import _error_label

    observations: dict[str, dict[str, Any]] = {}
    for name in prerequisites(stage):
        if name not in EVIDENCE:
            continue
        try:
            observations[name] = {"row": _fetch(connection, EVIDENCE[name][2])}
        except Exception as error:  # noqa: BLE001 - reported as unavailable
            observations[name] = {"error": _error_label(error)}
    return observations, read_ledger(connection, stage, now=now, tenant_id=tenant_id)


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
          connect: Callable[[str, str], Any] | None = None) -> dict[str, Any]:
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
            decision = decide(stage, {name: {"error": "database not configured"} for name in EVIDENCE},
                              now=now, tenant_id=tenant_id)
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
                observations, ledger = {name: {"error": failure} for name in EVIDENCE}, None
            decision = decide(stage, observations, now=now, tenant_id=tenant_id, ledger=ledger)
        base.update(decision)
    except Exception as error:  # noqa: BLE001 - a broken check is a reason, never a crash
        base.update(ready=False, reasons=[f"check failed: {type(error).__name__}: {error}"[:300]])
    base["elapsed_ms"] = int((monotonic_time.monotonic() - started) * 1000)
    return base


def gate(job: str, *, environ: Mapping[str, str] | None = None, now: datetime | None = None,
         out: Callable[[str], None] | None = None, **options: Any) -> dict[str, Any] | None:
    """Call first in a job's main(). Off: returns None and does nothing else."""
    environ = os.environ if environ is None else environ
    selected, note = mode(environ)
    if selected == OFF:
        return None
    decision = check(job, environ=environ, now=now, **options)
    decision["mode"] = selected
    if note:
        decision["mode_note"] = note
    stop = selected == ENFORCE and not decision.get("ready")
    decision["decision"] = "stop" if stop else "continue"
    line = LOG_PREFIX + json.dumps(decision, sort_keys=True, separators=(",", ":"), default=str)
    try:
        (out or (lambda text: print(text, flush=True)))(line)
    except Exception:  # noqa: BLE001 - logging must not fail the job
        pass
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
