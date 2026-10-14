"""The team's run, step and objection records (the agent_writer role).

Like the main store, this role cannot read portfolio data: it keeps what the
agents concluded, with the figures and checks behind each conclusion, never
the files themselves.
"""
from __future__ import annotations

import json
from typing import Any, Sequence

from ibkr_etl.db import PostgresConnection

JSON_COLUMNS = frozenset({"figures", "commentary", "package", "verdicts", "usage"})
RUN_COLUMNS = JSON_COLUMNS | {"stage", "data_verdict", "commentary_rounds", "commentary_status", "attempts", "last_error"}
_RUN_SELECT = (
    "SELECT month_run_id, subject_id, period_end::text AS period_end, mode, stage, data_verdict, figures, commentary, "
    "commentary_rounds, commentary_status, package, verdicts, usage, attempts, last_error, created_at, updated_at "
    "FROM agent.month_runs"
)


def _json(value: Any) -> Any:
    return json.loads(value) if isinstance(value, str) else value


def _run(row: Any) -> dict[str, Any]:
    run = dict(row)
    for column in JSON_COLUMNS:
        run[column] = _json(run.get(column))
    return run


class MonthStore:
    def __init__(self, connection: PostgresConnection, tenant_id: str) -> None:
        self.db = connection
        self.tenant_id = tenant_id

    # Runs ------------------------------------------------------------------

    def run(self, account_id: str, period_end: str, mode: str) -> dict[str, Any]:
        """The account's run for the month, created at the data stage the first time."""
        self.db.execute(
            "INSERT INTO agent.month_runs(tenant_id, subject_id, period_end, mode) VALUES (?::uuid, ?, ?::date, ?) "
            "ON CONFLICT (tenant_id, subject_id, period_end, mode) DO NOTHING",
            (self.tenant_id, account_id, period_end, mode),
        )
        row = self.db.execute(
            _RUN_SELECT + " WHERE tenant_id = ?::uuid AND subject_id = ? AND period_end = ?::date AND mode = ?",
            (self.tenant_id, account_id, period_end, mode),
        ).fetchone()
        self.db.commit()
        return _run(row)

    def update_run(self, month_run_id: int, **fields: Any) -> None:
        unknown = set(fields) - RUN_COLUMNS
        if unknown:
            raise ValueError("Unknown month run fields: " + ", ".join(sorted(unknown)))
        if not fields:
            return
        assignments = ", ".join(f"{k} = ?::jsonb" if k in JSON_COLUMNS else f"{k} = ?" for k in fields)
        values = [json.dumps(v, default=str) if k in JSON_COLUMNS else v for k, v in fields.items()]
        self.db.execute(
            f"UPDATE agent.month_runs SET {assignments}, updated_at = now() WHERE month_run_id = ? AND tenant_id = ?::uuid",
            (*values, month_run_id, self.tenant_id),
        )
        self.db.commit()

    def retry_stopped(self, mode: str, first: str, last: str) -> int:
        """Clear the failure count on runs the coordinator stopped, so the next wake tries again."""
        cursor = self.db.execute(
            "UPDATE agent.month_runs SET attempts = 0, last_error = NULL, updated_at = now() WHERE tenant_id = ?::uuid "
            "AND mode = ? AND period_end BETWEEN ?::date AND ?::date AND attempts > 0 RETURNING month_run_id",
            (self.tenant_id, mode, first, last),
        )
        count = len(cursor.fetchall())
        self.db.commit()
        return count

    def previous_commentary(self, account_id: str, period_end: str) -> dict[str, Any] | None:
        """Last month's agreed text, for tone only."""
        row = self.db.execute(
            "SELECT commentary FROM agent.month_runs WHERE tenant_id = ?::uuid AND subject_id = ? AND period_end < ?::date "
            "AND mode = 'shadow' AND commentary_status IN ('agreed', 'writer_upheld') AND commentary IS NOT NULL "
            "ORDER BY period_end DESC LIMIT 1",
            (self.tenant_id, account_id, period_end),
        ).fetchone()
        self.db.rollback()
        return _json(row["commentary"]) if row else None

    # Steps and objections --------------------------------------------------

    def add_step(self, month_run_id: int, agent_name: str, outcome: str, summary: str, *, round: int = 1,
                 evidence: dict[str, Any] | None = None, usage: dict[str, int] | None = None,
                 correlation_id: str | None = None) -> int:
        row = self.db.execute(
            "INSERT INTO agent.month_steps(month_run_id, agent_name, round, outcome, summary, evidence, usage, correlation_id) "
            "VALUES (?, ?, ?, ?, ?, ?::jsonb, ?::jsonb, ?) RETURNING step_id",
            (month_run_id, agent_name, round, outcome, summary[:600], json.dumps(evidence or {}, default=str),
             json.dumps(usage or {}), correlation_id),
        ).fetchone()
        self.db.commit()
        return int(row["step_id"])

    def add_objection(self, month_run_id: int, step_id: int, *, raised_by: str, target: str, message: str,
                      page: int | None = None, region: dict[str, float] | None = None) -> int:
        row = self.db.execute(
            "INSERT INTO agent.month_objections(month_run_id, step_id, raised_by, target, page, region, message) "
            "VALUES (?, ?, ?, ?, ?, ?::jsonb, ?) RETURNING objection_id",
            (month_run_id, step_id, raised_by, target, page, json.dumps(region) if region else None, message[:600]),
        ).fetchone()
        self.db.commit()
        return int(row["objection_id"])

    def objections(self, month_run_id: int, status: str | None = None) -> list[dict[str, Any]]:
        rows = self.db.execute(
            "SELECT objection_id, step_id, raised_by, target, page, region, message, status, resolution, created_at, "
            "resolved_at FROM agent.month_objections WHERE month_run_id = ? AND (?::text IS NULL OR status = ?) "
            "ORDER BY objection_id",
            (month_run_id, status, status),
        ).fetchall()
        self.db.rollback()
        return [{**dict(r), "region": _json(r["region"])} for r in rows]

    def settle_objections(self, month_run_id: int, targets: Sequence[str], status: str, resolution: str) -> int:
        cursor = self.db.execute(
            "UPDATE agent.month_objections SET status = ?, resolution = ?, resolved_at = now() "
            "WHERE month_run_id = ? AND target = ANY(?) AND status = 'open' RETURNING objection_id",
            (status, resolution[:600], month_run_id, list(targets)),
        )
        settled = len(cursor.fetchall())
        self.db.commit()
        return settled

    # Inbox -----------------------------------------------------------------

    def card_status(self, kind: str, account_id: str, period_key: str) -> str | None:
        row = self.db.execute(
            "SELECT status FROM agent.inbox WHERE tenant_id = ?::uuid AND kind = ? AND subject_id = ? AND period_key = ?",
            (self.tenant_id, kind, account_id, period_key),
        ).fetchone()
        self.db.rollback()
        return str(row["status"]) if row else None

    def put_card(self, *, kind: str, account_id: str, period_key: str, title: str, body: str,
                 citations: Sequence[Any] = (), reopen: bool = False) -> None:
        """Write the account's card for the month, or refresh it (back to new when ``reopen``)."""
        self.db.execute(
            "INSERT INTO agent.inbox(tenant_id, kind, subject_id, period_key, title, body, citations) "
            "VALUES (?::uuid, ?, ?, ?, ?, ?, ?::jsonb) ON CONFLICT (tenant_id, kind, subject_id, period_key) DO UPDATE "
            "SET title = excluded.title, body = excluded.body, citations = excluded.citations, "
            "status = CASE WHEN ? THEN 'new' ELSE agent.inbox.status END, "
            "decided_at = CASE WHEN ? THEN NULL ELSE agent.inbox.decided_at END",
            (self.tenant_id, kind, account_id, period_key, title[:200], body, json.dumps(list(citations), default=str),
             reopen, reopen),
        )
        self.db.commit()

    # Budget ----------------------------------------------------------------

    def tokens_used_this_month(self) -> int:
        row = self.db.execute(
            "SELECT COALESCE(SUM((usage->>'input_tokens')::bigint + (usage->>'output_tokens')::bigint), 0) AS used "
            "FROM agent.runs WHERE tenant_id = ?::uuid AND job_name = 'month_team' "
            "AND started_at >= date_trunc('month', now()) AND (usage->>'input_tokens') IS NOT NULL",
            (self.tenant_id,),
        ).fetchone()
        self.db.rollback()
        return int(row["used"] or 0)

    # Website reads ---------------------------------------------------------

    def runs(self, accounts: Sequence[str], period_end: str | None = None, mode: str = "shadow") -> list[dict[str, Any]]:
        if not accounts:
            return []
        rows = self.db.execute(
            _RUN_SELECT + " WHERE tenant_id = ?::uuid AND subject_id = ANY(?) AND mode = ? "
            "AND (?::date IS NULL OR period_end = ?::date) ORDER BY period_end DESC, subject_id LIMIT 500",
            (self.tenant_id, list(accounts), mode, period_end, period_end),
        ).fetchall()
        counts = {int(r["month_run_id"]): int(r["open"]) for r in self.db.execute(
            "SELECT month_run_id, COUNT(*) AS open FROM agent.month_objections WHERE status = 'open' "
            "AND month_run_id = ANY(?) GROUP BY month_run_id", ([int(r["month_run_id"]) for r in rows],),
        ).fetchall()} if rows else {}
        self.db.rollback()
        listed = []
        for row in rows:
            run = _run(row)
            listed.append({key: run[key] for key in (
                "month_run_id", "subject_id", "period_end", "mode", "stage", "data_verdict", "commentary_status",
                "commentary_rounds", "attempts", "last_error", "updated_at")}
                | {"open_objections": counts.get(int(run["month_run_id"]), 0),
                   "verdicts": run["verdicts"], "files": (run["package"] or {}).get("files", [])})
        return listed

    def run_detail(self, account_id: str, period_end: str, mode: str = "shadow") -> dict[str, Any] | None:
        row = self.db.execute(
            _RUN_SELECT + " WHERE tenant_id = ?::uuid AND subject_id = ? AND period_end = ?::date AND mode = ?",
            (self.tenant_id, account_id, period_end, mode),
        ).fetchone()
        if row is None:
            self.db.rollback()
            return None
        run = _run(row)
        steps = self.db.execute(
            "SELECT step_id, agent_name, round, outcome, summary, evidence, usage, created_at FROM agent.month_steps "
            "WHERE month_run_id = ? ORDER BY step_id", (run["month_run_id"],),
        ).fetchall()
        self.db.rollback()
        run["steps"] = [{**dict(s), "evidence": _json(s["evidence"]), "usage": _json(s["usage"])} for s in steps]
        run["objections"] = self.objections(int(run["month_run_id"]))
        return run

    def team_status(self) -> dict[str, Any]:
        """The latest month_team job run and this month's token use, for the Month-end page."""
        row = self.db.execute(
            "SELECT status, error_code, started_at, completed_at FROM agent.runs WHERE tenant_id = ?::uuid "
            "AND job_name = 'month_team' ORDER BY run_id DESC LIMIT 1", (self.tenant_id,),
        ).fetchone()
        self.db.rollback()
        return {"latest_job": dict(row) if row else None, "tokens_this_month": self.tokens_used_this_month()}
