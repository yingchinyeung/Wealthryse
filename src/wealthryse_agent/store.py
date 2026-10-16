"""Conversation, run and inbox storage (the agent_writer role).

This role cannot read portfolio data; it only stores what agents produced.
"""
from __future__ import annotations

import json
import uuid
from typing import Any, Sequence

from ibkr_etl.db import PostgresConnection

INBOX_DECISIONS = frozenset({"read", "approved", "dismissed"})


class Store:
    def __init__(self, connection: PostgresConnection, tenant_id: str) -> None:
        self.db = connection
        self.tenant_id = tenant_id

    def _commit(self) -> None:
        self.db.commit()

    # Conversations ---------------------------------------------------------

    def create_conversation(self, user: str, account_id: str, title: str) -> str:
        conversation_id = str(uuid.uuid4())
        self.db.execute(
            "INSERT INTO agent.conversations(conversation_id, tenant_id, user_object_id, subject_id, title) "
            "VALUES (?::uuid, ?::uuid, ?, ?, ?)",
            (conversation_id, self.tenant_id, user.lower(), account_id, title[:120] or "New conversation"),
        )
        self._commit()
        return conversation_id

    def conversation(self, conversation_id: str, user: str) -> dict[str, Any] | None:
        try:
            uuid.UUID(conversation_id)
        except ValueError:
            return None
        row = self.db.execute(
            "SELECT conversation_id::text AS conversation_id, subject_id, title, created_at, updated_at "
            "FROM agent.conversations WHERE conversation_id = ?::uuid AND tenant_id = ?::uuid "
            "AND user_object_id = ? AND archived_at IS NULL",
            (conversation_id, self.tenant_id, user.lower()),
        ).fetchone()
        self.db.rollback()
        return dict(row) if row else None

    def conversations(self, user: str, account_id: str) -> list[dict[str, Any]]:
        rows = self.db.execute(
            "SELECT conversation_id::text AS conversation_id, title, updated_at FROM agent.conversations "
            "WHERE tenant_id = ?::uuid AND user_object_id = ? AND subject_id = ? AND archived_at IS NULL "
            "ORDER BY updated_at DESC LIMIT 50",
            (self.tenant_id, user.lower(), account_id),
        ).fetchall()
        self.db.rollback()
        return [dict(r) for r in rows]

    def messages(self, conversation_id: str) -> list[dict[str, Any]]:
        rows = self.db.execute(
            "SELECT message_id, role, content, citations, status, created_at FROM agent.messages "
            "WHERE conversation_id = ?::uuid ORDER BY message_id", (conversation_id,),
        ).fetchall()
        self.db.rollback()
        return [_split_visuals(dict(r)) for r in rows]

    # Memory ----------------------------------------------------------------

    def memories(self, user: str) -> list[dict[str, Any]]:
        rows = self.db.execute(
            "SELECT key, value, source, updated_at FROM agent.memories WHERE tenant_id = ?::uuid "
            "AND user_object_id = ? ORDER BY key", (self.tenant_id, user.lower()),
        ).fetchall()
        self.db.rollback()
        return [dict(r) for r in rows]

    def remember(self, user: str, key: str, value: str, source: str) -> None:
        self.db.execute(
            "INSERT INTO agent.memories(tenant_id, user_object_id, key, value, source) VALUES (?::uuid, ?, ?, ?, ?) "
            "ON CONFLICT (tenant_id, user_object_id, key) DO UPDATE SET value = EXCLUDED.value, "
            "source = EXCLUDED.source, updated_at = now()",
            (self.tenant_id, user.lower(), key, value, source),
        )
        self._commit()

    def forget(self, user: str, key: str | None = None) -> int:
        """Delete one preference, or all of them when key is None."""
        row = self.db.execute(
            "WITH gone AS (DELETE FROM agent.memories WHERE tenant_id = ?::uuid AND user_object_id = ? "
            "AND (?::text IS NULL OR key = ?) RETURNING 1) SELECT COUNT(*) AS n FROM gone",
            (self.tenant_id, user.lower(), key, key),
        ).fetchone()
        self._commit()
        return int(row["n"])

    def add_message(self, conversation_id: str, role: str, content: str, *, citations: Sequence[Any] = (),
                    status: str = "ok", correlation_id: str | None = None,
                    usage: dict[str, int] | None = None) -> int:
        row = self.db.execute(
            "INSERT INTO agent.messages(conversation_id, role, content, citations, status, correlation_id, usage) "
            "VALUES (?::uuid, ?, ?, ?::jsonb, ?, ?, ?::jsonb) RETURNING message_id",
            (conversation_id, role, content, json.dumps(list(citations), default=str), status, correlation_id,
             json.dumps(usage or {})),
        ).fetchone()
        self.db.execute("UPDATE agent.conversations SET updated_at = now() WHERE conversation_id = ?::uuid",
                        (conversation_id,))
        self._commit()
        return int(row["message_id"])

    def archive(self, conversation_id: str, user: str) -> bool:
        cursor = self.db.execute(
            "UPDATE agent.conversations SET archived_at = now() WHERE conversation_id = ?::uuid "
            "AND tenant_id = ?::uuid AND user_object_id = ? AND archived_at IS NULL RETURNING conversation_id",
            (conversation_id, self.tenant_id, user.lower()),
        )
        changed = cursor.fetchone() is not None
        self._commit()
        return changed

    def feedback(self, message_id: int, user: str, rating: int, comment: str | None) -> bool:
        owned = self.db.execute(
            "SELECT 1 FROM agent.messages m JOIN agent.conversations c ON c.conversation_id = m.conversation_id "
            "WHERE m.message_id = ? AND m.role = 'assistant' AND c.user_object_id = ? AND c.tenant_id = ?::uuid",
            (message_id, user.lower(), self.tenant_id),
        ).fetchone()
        if not owned:
            self.db.rollback()
            return False
        self.db.execute(
            "INSERT INTO agent.feedback(message_id, user_object_id, rating, comment) VALUES (?, ?, ?, ?) "
            "ON CONFLICT (message_id, user_object_id) DO UPDATE SET rating = excluded.rating, comment = excluded.comment",
            (message_id, user.lower(), rating, (comment or "")[:1000] or None),
        )
        self._commit()
        return True

    # Budget ----------------------------------------------------------------

    def tokens_used_today(self) -> int:
        """Chat and the daily jobs. The month-end team has its own monthly budget."""
        row = self.db.execute(
            """
            SELECT COALESCE(SUM((m.usage->>'input_tokens')::bigint + (m.usage->>'output_tokens')::bigint), 0)
                 + COALESCE((SELECT SUM((r.usage->>'input_tokens')::bigint + (r.usage->>'output_tokens')::bigint)
                             FROM agent.runs r WHERE r.tenant_id = ?::uuid AND r.started_at >= date_trunc('day', now())
                               AND r.job_name <> 'month_team'), 0)
                   AS used
            FROM agent.messages m JOIN agent.conversations c ON c.conversation_id = m.conversation_id
            WHERE c.tenant_id = ?::uuid AND m.created_at >= date_trunc('day', now()) AND (m.usage->>'input_tokens') IS NOT NULL
            """,
            (self.tenant_id, self.tenant_id),
        ).fetchone()
        self.db.rollback()
        return int(row["used"] or 0)

    # Evaluation ------------------------------------------------------------

    def eval_cases(self) -> list[dict[str, Any]]:
        rows = self.db.execute(
            "SELECT case_id, subject_id, question, expected_tools, must_refuse FROM agent.eval_cases ORDER BY case_id"
        ).fetchall()
        self.db.rollback()
        return [dict(r) for r in rows]

    # Runs and inbox --------------------------------------------------------

    def start_run(self, job_name: str, correlation_id: str) -> int:
        row = self.db.execute(
            "INSERT INTO agent.runs(tenant_id, job_name, correlation_id, status) VALUES (?::uuid, ?, ?, 'running') "
            "RETURNING run_id", (self.tenant_id, job_name, correlation_id),
        ).fetchone()
        self._commit()
        return int(row["run_id"])

    def finish_run(self, run_id: int, *, status: str, items: int, usage: dict[str, int], error_code: str | None = None) -> None:
        self.db.execute(
            "UPDATE agent.runs SET status = ?, completed_at = now(), items_written = ?, usage = ?::jsonb, error_code = ? "
            "WHERE run_id = ?", (status, items, json.dumps(usage), error_code, run_id),
        )
        self._commit()

    def inbox_exists(self, kind: str, account_id: str, period_key: str) -> bool:
        row = self.db.execute(
            "SELECT 1 FROM agent.inbox WHERE tenant_id = ?::uuid AND kind = ? AND subject_id = ? AND period_key = ?",
            (self.tenant_id, kind, account_id, period_key),
        ).fetchone()
        self.db.rollback()
        return row is not None

    def add_inbox(self, *, kind: str, account_id: str, period_key: str, title: str, body: str,
                  citations: Sequence[Any], run_id: int | None) -> bool:
        row = self.db.execute(
            "INSERT INTO agent.inbox(tenant_id, kind, subject_id, period_key, title, body, citations, run_id) "
            "VALUES (?::uuid, ?, ?, ?, ?, ?, ?::jsonb, ?) ON CONFLICT (tenant_id, kind, subject_id, period_key) DO NOTHING "
            "RETURNING item_id",
            (self.tenant_id, kind, account_id, period_key, title[:200], body, json.dumps(list(citations), default=str), run_id),
        ).fetchone()
        self._commit()
        return row is not None

    def queue_first_connection_check(self, flex_source_id: str) -> str | None:
        """Queue a never-checked IBKR login's first check; None when the database says it is not eligible."""
        row = self.db.execute("SELECT agent.queue_first_connection_check(?::uuid) AS request_id",
                              (flex_source_id,)).fetchone()
        self._commit()
        return str(row["request_id"]) if row and row["request_id"] else None

    def add_connection_report(self, *, connection_id: str, flex_source_id: str, key: str, state: str, title: str,
                              body: str, owner_steps: Sequence[str], validation_request_id: str | None,
                              run_id: int | None) -> bool:
        row = self.db.execute(
            "INSERT INTO agent.connection_reports(tenant_id, connection_id, flex_source_id, report_key, state, title, "
            "body, owner_steps, validation_request_id, run_id) VALUES (?::uuid, ?::uuid, ?::uuid, ?, ?, ?, ?, ?::jsonb, "
            "?::uuid, ?) ON CONFLICT (tenant_id, flex_source_id, report_key) DO NOTHING RETURNING report_id",
            (self.tenant_id, connection_id, flex_source_id, key[:120], state, title[:200], body[:2000],
             json.dumps(list(owner_steps)), validation_request_id, run_id),
        ).fetchone()
        self._commit()
        return row is not None

    def inbox(self, accounts: Sequence[str], status: str | None = None) -> list[dict[str, Any]]:
        if not accounts:
            return []
        rows = self.db.execute(
            "SELECT item_id, kind, subject_id, period_key, title, body, citations, status, created_at, decided_at "
            "FROM agent.inbox WHERE tenant_id = ?::uuid AND subject_id = ANY(?) AND (?::text IS NULL OR status = ?) "
            "ORDER BY created_at DESC LIMIT 100",
            (self.tenant_id, list(accounts), status, status),
        ).fetchall()
        self.db.rollback()
        return [dict(r) for r in rows]

    def decide(self, item_id: int, accounts: Sequence[str], decision: str, user_sha256: str) -> bool:
        if decision not in INBOX_DECISIONS:
            return False
        row = self.db.execute(
            "UPDATE agent.inbox SET status = ?, decided_at = now(), decided_by_sha256 = ? "
            "WHERE item_id = ? AND tenant_id = ?::uuid AND subject_id = ANY(?) RETURNING item_id",
            (decision, user_sha256, item_id, self.tenant_id, list(accounts)),
        ).fetchone()
        self._commit()
        return row is not None

    # Export agent ----------------------------------------------------------

    def add_export_check(self, *, user: str, account_id: str, plan: dict[str, Any], fingerprint: str, status: str,
                         note: str, note_checked: bool, concerns: Sequence[str], checks: Sequence[Any],
                         pack_item_id: int | None, correlation_id: str) -> str:
        check_id = str(uuid.uuid4())
        self.db.execute(
            "INSERT INTO agent.export_checks(check_id, tenant_id, user_object_id, subject_id, plan, fingerprint, status, "
            "note, note_checked, concerns, checks, pack_item_id, correlation_id) VALUES (?::uuid, ?::uuid, ?, ?, ?::jsonb, "
            "?, ?, ?, ?, ?::jsonb, ?::jsonb, ?, ?)",
            (check_id, self.tenant_id, user.lower(), account_id, json.dumps(plan, default=str), fingerprint, status,
             note[:6000], note_checked, json.dumps(list(concerns)), json.dumps(list(checks), default=str), pack_item_id,
             correlation_id),
        )
        self._commit()
        return check_id

    def export_check(self, check_id: str, user: str) -> dict[str, Any] | None:
        try:
            uuid.UUID(check_id)
        except ValueError:
            return None
        row = self.db.execute(
            "SELECT check_id::text AS check_id, subject_id, plan, fingerprint, status, note, note_checked, concerns, "
            "checks, pack_item_id, created_at, released_at, released_after_review FROM agent.export_checks "
            "WHERE check_id = ?::uuid AND tenant_id = ?::uuid AND user_object_id = ?",
            (check_id, self.tenant_id, user.lower()),
        ).fetchone()
        self.db.rollback()
        return _json_columns(dict(row), "plan", "concerns", "checks") if row else None

    def release_export_check(self, check_id: str, user: str) -> bool:
        """Record that the person downloaded the files; a failed export can't be released."""
        row = self.db.execute(
            "UPDATE agent.export_checks SET released_at = COALESCE(released_at, now()), "
            "released_after_review = released_after_review OR status = 'review' "
            "WHERE check_id = ?::uuid AND tenant_id = ?::uuid AND user_object_id = ? AND status <> 'fail' "
            "RETURNING check_id", (check_id, self.tenant_id, user.lower()),
        ).fetchone()
        self._commit()
        return row is not None

    def save_export_pack(self, *, user: str, account_id: str, name: str, schedule: str, plan: dict[str, Any]) -> str:
        pack_id = str(uuid.uuid4())
        self.db.execute(
            "INSERT INTO agent.export_packs(pack_id, tenant_id, user_object_id, subject_id, name, schedule, plan) "
            "VALUES (?::uuid, ?::uuid, ?, ?, ?, ?, ?::jsonb)",
            (pack_id, self.tenant_id, user.lower(), account_id, name[:80], schedule, json.dumps(plan, default=str)),
        )
        self._commit()
        return pack_id

    def export_packs(self, user: str, account_id: str | None = None) -> list[dict[str, Any]]:
        rows = self.db.execute(
            "SELECT pack_id::text AS pack_id, subject_id, name, schedule, plan, created_at FROM agent.export_packs "
            "WHERE tenant_id = ?::uuid AND user_object_id = ? AND (?::text IS NULL OR subject_id = ?) "
            "AND archived_at IS NULL ORDER BY created_at DESC LIMIT 50",
            (self.tenant_id, user.lower(), account_id, account_id),
        ).fetchall()
        self.db.rollback()
        return [_json_columns(dict(r), "plan") for r in rows]

    def export_pack(self, pack_id: str, user: str) -> dict[str, Any] | None:
        try:
            uuid.UUID(pack_id)
        except ValueError:
            return None
        row = self.db.execute(
            "SELECT pack_id::text AS pack_id, subject_id, name, schedule, plan, created_at FROM agent.export_packs "
            "WHERE pack_id = ?::uuid AND tenant_id = ?::uuid AND user_object_id = ? AND archived_at IS NULL",
            (pack_id, self.tenant_id, user.lower()),
        ).fetchone()
        self.db.rollback()
        return _json_columns(dict(row), "plan") if row else None

    def archive_export_pack(self, pack_id: str, user: str) -> bool:
        try:
            uuid.UUID(pack_id)
        except ValueError:
            return False
        row = self.db.execute(
            "UPDATE agent.export_packs SET archived_at = now() WHERE pack_id = ?::uuid AND tenant_id = ?::uuid "
            "AND user_object_id = ? AND archived_at IS NULL RETURNING pack_id",
            (pack_id, self.tenant_id, user.lower()),
        ).fetchone()
        self._commit()
        return row is not None

    def active_export_packs(self) -> list[dict[str, Any]]:
        """Every saved pack, for the export_packs job (which checks each account against its own grants)."""
        rows = self.db.execute(
            "SELECT pack_id::text AS pack_id, user_object_id, subject_id, name, schedule, created_at "
            "FROM agent.export_packs WHERE tenant_id = ?::uuid AND archived_at IS NULL ORDER BY created_at",
            (self.tenant_id,),
        ).fetchall()
        self.db.rollback()
        return [dict(r) for r in rows]

    def purge(self, days: int) -> int:
        row = self.db.execute("SELECT agent.purge_conversations(?) AS removed", (days,)).fetchone()
        self._commit()
        return int(row["removed"])


def _json_columns(row: dict[str, Any], *names: str) -> dict[str, Any]:
    for name in names:
        if isinstance(row.get(name), str):
            row[name] = json.loads(row[name])
    return row


def _split_visuals(message: dict[str, Any]) -> dict[str, Any]:
    """Charts and tables are saved alongside citations as {"visual": ...} entries."""
    entries = message.get("citations") or []
    if isinstance(entries, str):
        entries = json.loads(entries)
    message["citations"] = [c for c in entries if not (isinstance(c, dict) and "visual" in c)]
    message["visuals"] = [c["visual"] for c in entries if isinstance(c, dict) and "visual" in c]
    return message
