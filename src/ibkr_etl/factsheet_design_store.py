"""Storage for factsheet designs built by the design agents: files, drafts, versions and edit proposals.

Every change to a design is a new version (the reader's draft, each accepted
edit, each undo, each answered question); nothing is overwritten, so undo is
"make the parent current again" and the audit trail is the version list. The
uploaded template and examples are kept by SHA-256, once each.

SQL here uses portable ``?`` placeholders (the website passes its
``PostgresConnection`` adapter; the tests use SQLite).
"""
from __future__ import annotations

import json
import uuid
from typing import Any, Mapping

from .util import canonical_json, utc_now_iso

STATUSES = ("review", "failed", "submitted", "archived")
SOURCES = ("reader", "builder", "editor", "undo", "answer")


class DesignStoreError(ValueError):
    pass


def _row(row: Any) -> dict[str, Any] | None:
    return None if row is None else dict(row)


def _json(value: Any) -> Any:
    return json.loads(value) if isinstance(value, str) else value


def put_file(connection, tenant_id: str, sha256: str, content: bytes, content_type: str = "application/pdf") -> None:
    found = connection.execute("SELECT sha256 FROM factsheet_design_files WHERE tenant_id=? AND sha256=?",
                               (tenant_id, sha256)).fetchone()
    if found is None:
        connection.execute("""INSERT INTO factsheet_design_files(tenant_id, sha256, content_type, byte_count, content,
            created_at) VALUES (?,?,?,?,?,?)""", (tenant_id, sha256, content_type, len(content), content, utc_now_iso()))


def get_file(connection, tenant_id: str, sha256: str) -> bytes:
    row = connection.execute("SELECT content FROM factsheet_design_files WHERE tenant_id=? AND sha256=?",
                             (tenant_id, sha256)).fetchone()
    if row is None:
        raise DesignStoreError("The design's file is missing")
    content = dict(row)["content"]
    return bytes(content)


def create_draft(connection, tenant_id: str, subject_id: str, *, title: str, template_sha256: str,
                 example_sha256: str | None, more_examples: list[str], build: Mapping[str, Any],
                 spec: Mapping[str, Any], spec_sha256: str, actor: str, status: str) -> str:
    if status not in STATUSES:
        raise DesignStoreError("Unknown status")
    draft_id = str(uuid.uuid4())
    now = utc_now_iso()
    connection.execute("""INSERT INTO factsheet_design_drafts(draft_id, tenant_id, subject_id, title, template_sha256,
        example_sha256, more_examples_json, status, current_version, build_json, created_by, created_at, updated_at)
        VALUES (?,?,?,?,?,?,?,?,1,?,?,?,?)""",
                       (draft_id, tenant_id, subject_id, title, template_sha256, example_sha256,
                        canonical_json(more_examples), status, canonical_json(build), actor, now, now))
    _insert_version(connection, draft_id, 1, spec, spec_sha256, None, "builder", "Drafted from the template",
                    [], build.get("checks"), actor)
    return draft_id


def _insert_version(connection, draft_id: str, version: int, spec: Mapping[str, Any], spec_sha256: str,
                    parent: int | None, source: str, summary: str, changes: list[str], checks: Any, actor: str) -> None:
    if source not in SOURCES:
        raise DesignStoreError("Unknown version source")
    connection.execute("""INSERT INTO factsheet_design_versions(draft_id, version, spec_json, spec_sha256,
        parent_version, source, summary, changes_json, checks_json, created_by, created_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                       (draft_id, version, canonical_json(spec), spec_sha256, parent, source, summary[:500],
                        canonical_json(changes), canonical_json(checks), actor, utc_now_iso()))


def draft(connection, tenant_id: str, draft_id: str) -> dict[str, Any] | None:
    row = _row(connection.execute("SELECT * FROM factsheet_design_drafts WHERE tenant_id=? AND draft_id=?",
                                  (tenant_id, str(draft_id))).fetchone())
    if row is None:
        return None
    row["build"] = _json(row.pop("build_json"))
    row["more_examples"] = _json(row.pop("more_examples_json"))
    return row


def drafts(connection, tenant_id: str, subject_id: str) -> list[dict[str, Any]]:
    rows = connection.execute("""SELECT draft_id, subject_id, title, status, current_version, created_at, updated_at
        FROM factsheet_design_drafts WHERE tenant_id=? AND subject_id=? AND status<>'archived'
        ORDER BY created_at DESC LIMIT 50""", (tenant_id, subject_id)).fetchall()
    return [dict(r) for r in rows]


def versions(connection, draft_id: str) -> list[dict[str, Any]]:
    rows = connection.execute("""SELECT version, spec_sha256, parent_version, source, summary, changes_json,
        created_by, created_at FROM factsheet_design_versions WHERE draft_id=? ORDER BY version""",
                              (str(draft_id),)).fetchall()
    out = []
    for row in rows:
        item = dict(row)
        item["changes"] = _json(item.pop("changes_json"))
        out.append(item)
    return out


def version(connection, draft_id: str, number: int) -> dict[str, Any]:
    row = _row(connection.execute("SELECT * FROM factsheet_design_versions WHERE draft_id=? AND version=?",
                                  (str(draft_id), int(number))).fetchone())
    if row is None:
        raise DesignStoreError("That version does not exist")
    row["spec"] = _json(row.pop("spec_json"))
    row["changes"] = _json(row.pop("changes_json"))
    row["checks"] = _json(row.pop("checks_json"))
    return row


def add_version(connection, tenant_id: str, draft_id: str, *, spec: Mapping[str, Any], spec_sha256: str,
                source: str, summary: str, changes: list[str], checks: Any, actor: str,
                expected_version: int) -> int:
    """A new current version on top of ``expected_version``; refuses if someone else moved the draft first."""
    current = draft(connection, tenant_id, draft_id)
    if current is None:
        raise DesignStoreError("Design not found")
    if current["status"] not in ("review", "failed"):
        raise DesignStoreError("This design was submitted; start a new draft to change it")
    if int(current["current_version"]) != int(expected_version):
        raise DesignStoreError("The design changed since you opened it. Reload and try again.")
    latest = connection.execute("SELECT MAX(version) AS v FROM factsheet_design_versions WHERE draft_id=?",
                                (str(draft_id),)).fetchone()
    number = int(dict(latest)["v"]) + 1
    _insert_version(connection, str(draft_id), number, spec, spec_sha256, int(expected_version), source, summary,
                    changes, checks, actor)
    updated = connection.execute("""UPDATE factsheet_design_drafts SET current_version=?, updated_at=?
        WHERE tenant_id=? AND draft_id=? AND current_version=?""",
                                 (number, utc_now_iso(), tenant_id, str(draft_id), int(expected_version)))
    if getattr(updated, "rowcount", 1) == 0:
        raise DesignStoreError("The design changed since you opened it. Reload and try again.")
    return number


def set_status(connection, tenant_id: str, draft_id: str, status: str) -> None:
    if status not in STATUSES:
        raise DesignStoreError("Unknown status")
    connection.execute("UPDATE factsheet_design_drafts SET status=?, updated_at=? WHERE tenant_id=? AND draft_id=?",
                       (status, utc_now_iso(), tenant_id, str(draft_id)))


def add_proposal(connection, draft_id: str, base_version: int, request: str, proposal: Mapping[str, Any],
                 spec: Mapping[str, Any] | None, actor: str) -> str:
    proposal_id = str(uuid.uuid4())
    connection.execute("""INSERT INTO factsheet_design_proposals(proposal_id, draft_id, base_version, request,
        proposal_json, spec_json, status, created_by, created_at) VALUES (?,?,?,?,?,?,'pending',?,?)""",
                       (proposal_id, str(draft_id), int(base_version), request[:2000], canonical_json(proposal),
                        canonical_json(spec) if spec is not None else None, actor, utc_now_iso()))
    return proposal_id


def proposal(connection, draft_id: str, proposal_id: str) -> dict[str, Any] | None:
    row = _row(connection.execute("SELECT * FROM factsheet_design_proposals WHERE draft_id=? AND proposal_id=?",
                                  (str(draft_id), str(proposal_id))).fetchone())
    if row is None:
        return None
    row["proposal"] = _json(row.pop("proposal_json"))
    row["spec"] = _json(row.pop("spec_json")) if row.get("spec_json") else None
    row.pop("spec_json", None)
    return row


def recent_requests(connection, draft_id: str, limit: int = 6) -> list[dict[str, str]]:
    rows = connection.execute("""SELECT request, status, proposal_json FROM factsheet_design_proposals
        WHERE draft_id=? ORDER BY created_at DESC LIMIT ?""", (str(draft_id), int(limit))).fetchall()
    out = []
    for row in reversed([dict(r) for r in rows]):
        summary = (_json(row["proposal_json"]) or {}).get("summary", "")
        out.append({"request": row["request"], "outcome": row["status"], "summary": summary})
    return out


def close_proposal(connection, draft_id: str, proposal_id: str, status: str) -> None:
    if status not in ("accepted", "discarded"):
        raise DesignStoreError("Unknown proposal status")
    connection.execute("""UPDATE factsheet_design_proposals SET status=? WHERE draft_id=? AND proposal_id=?
        AND status='pending'""", (status, str(draft_id), str(proposal_id)))


def update_build(connection, tenant_id: str, draft_id: str, build: Mapping[str, Any]) -> None:
    """Record what the owner answered (the build's ``answered`` list); the build itself is never re-run."""
    connection.execute("UPDATE factsheet_design_drafts SET build_json=?, updated_at=? WHERE tenant_id=? AND draft_id=?",
                       (canonical_json(build), utc_now_iso(), tenant_id, str(draft_id)))
