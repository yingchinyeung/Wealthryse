"""The platform side of the onboarding agents, behind ONBOARDING_AGENTS_ENABLED (off by default).

The agents (wealthryse_agent.onboarding_intake, onboarding_reconcile and
onboarding_review) only write their own tables in the agent schema. This module
is what the platform does with them:

- the daily derived refresh records the reconciliation agent's newest results
  as stage 2's four checks (run_reconciliations), and the month-end report job
  records the history reviewer's review as stage 3's agent_review_complete
  check (run_reviews), both through onboarding_v2_web.record_platform_check;
- the case page shows which checks the agent recorded, with their evidence,
  the latest drafted terms beside approval 1, and the securities the agent
  proposes for the security master (case_agent_view);
- the owner uploads the client's documents (mandate, fee terms, side letters)
  to the private intake container, where the intake agent reads them
  (document_blob_name, validate_document).

While the switch is off nothing here reads the agent schema, records a check
or accepts a document. With it on, a missing agent table or a platform role
that cannot read it is treated as "no agent results yet", never an error.
"""

from __future__ import annotations

import json
import os
import posixpath
import re
from typing import Any, Iterable, Mapping

SWITCH_NAME = "ONBOARDING_AGENTS_ENABLED"
INTAKE_CONTAINER = "onboarding-intake"
DOCUMENT_PREFIX = "cases/{case_id}/documents/"
# What the intake agent can read (wealthryse_agent.onboarding_intake.document_text).
DOCUMENT_TYPES = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".txt": "text/plain; charset=utf-8",
    ".md": "text/markdown; charset=utf-8",
}
MAX_DOCUMENT_BYTES = 20 * 1024 * 1024
# The intake agent reads at most this many documents per case (AGENT_INTAKE_MAX_DOCUMENTS's default).
MAX_DOCUMENTS = 20
# Documents are drafted from before approval 1 only; afterwards the terms are approved.
DOCUMENT_STAGES = ("request", "load_and_reconcile")
_SAFE_NAME = re.compile(r"[^A-Za-z0-9._ -]+")
_MAX_PROPOSALS = 200


def agents_enabled(environ: Mapping[str, str] | None = None) -> bool:
    source = os.environ if environ is None else environ
    return source.get(SWITCH_NAME, "").strip().lower() == "true"


def _json(value: Any, default: Any) -> Any:
    if value is None:
        return default
    return json.loads(value) if isinstance(value, str) else value


def _readable(connection, table: str, placeholder: str) -> bool:
    """Whether the agent table exists and this role may read it, without raising either way."""

    schema, _, name = table.partition(".")
    # By OID, so a missing schema or one this role may not use is "no", not an error that aborts the transaction.
    row = connection.execute(
        "SELECT bool_or(has_schema_privilege(n.oid, 'USAGE') AND has_table_privilege(c.oid, 'SELECT')) AS allowed "
        f"FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace "
        f"WHERE n.nspname = {placeholder} AND c.relname = {placeholder}",
        (schema, name),
    ).fetchone()
    return bool(row["allowed"])


# Scheduled recording (platform jobs; ibkr_etl.db connections use ? placeholders) ----------------------------


def run_reconciliations(connection, tenant_id: str, environ: Mapping[str, str] | None = None) -> dict:
    """Stage 2's four checks from the reconciliation agent; called by the daily derived refresh."""

    if not agents_enabled(environ):
        return {"status": "switched_off"}
    if not _readable(connection, "agent.onboarding_reconciliations", "?"):
        connection.rollback()
        return {"status": "no_agent_results"}
    from .onboarding_agent_reconcile import record_agent_reconciliations

    return {"status": "recorded", "cases": record_agent_reconciliations(connection, tenant_id)}


def run_reviews(connection, tenant_id: str, environ: Mapping[str, str] | None = None) -> dict:
    """Stage 3's agent_review_complete check; called by the month-end job after the composite reports."""

    if not agents_enabled(environ):
        return {"status": "switched_off"}
    if not _readable(connection, "agent.onboarding_reviews", "?"):
        connection.rollback()
        return {"status": "no_agent_results"}
    from .onboarding_agent_review import record_agent_reviews

    return {"status": "recorded", "cases": record_agent_reviews(connection, tenant_id)}


# Case page (website reads; psycopg connections use %s placeholders) -------------------------------------------


def agent_checks(checks: Iterable[Mapping[str, Any]]) -> dict[str, dict]:
    """The evidence of each check the agent recorded ("recorded_by": "agent"), by check ID."""

    found = {}
    for check in checks:
        evidence = _json(check.get("evidence_json"), {}) or {}
        if isinstance(evidence, dict) and evidence.get("recorded_by") == "agent":
            found[str(check["check_id"])] = evidence
    return found


def _latest_draft(connection, tenant_id: str, case_id: str) -> dict | None:
    row = connection.execute(
        "SELECT draft_id, outcome, summary, terms, findings, documents, created_at "
        "FROM agent.onboarding_term_drafts WHERE tenant_id = %s AND case_id = %s "
        "ORDER BY created_at DESC, draft_id DESC LIMIT 1",
        (tenant_id, case_id),
    ).fetchone()
    if row is None:
        return None
    return {
        "draft_id": int(row["draft_id"]),
        "outcome": str(row["outcome"]),
        "summary": str(row["summary"]),
        "terms": _json(row["terms"], {}),
        "findings": _json(row["findings"], []),
        "documents": _json(row["documents"], []),
        "drafted_at": str(row["created_at"]),
    }


def case_agent_view(connection, tenant_id: str, case_id: str, checks: Iterable[Mapping[str, Any]],
                    *, environ: Mapping[str, str] | None = None) -> dict | None:
    """What the case page shows from the agents, or None while the switch is off."""

    if not agents_enabled(environ):
        return None
    recorded = agent_checks(checks)
    proposals = list((recorded.get("securities_mapped") or {}).get("proposals") or [])[:_MAX_PROPOSALS]
    draft = None
    if _readable(connection, "agent.onboarding_term_drafts", "%s"):
        draft = _latest_draft(connection, tenant_id, case_id)
    return {
        "checks": {check_id: {k: v for k, v in evidence.items() if k != "proposals"}
                   for check_id, evidence in recorded.items()},
        "term_draft": draft,
        "security_proposals": proposals,
    }


# Case documents (the intake agent's input) ------------------------------------------------------------------


def document_prefix(case_id: str) -> str:
    return DOCUMENT_PREFIX.format(case_id=case_id)


def safe_document_name(filename: str) -> str:
    """The file's own name, kept readable, without paths or characters storage or a reader could misread."""

    base = posixpath.basename(str(filename or "").replace("\\", "/")).strip()
    stem, suffix = posixpath.splitext(base)
    stem = _SAFE_NAME.sub("-", stem).strip(" .-")[:120]
    suffix = suffix.lower()
    if suffix not in DOCUMENT_TYPES:
        raise ValueError("Upload a PDF, Word (.docx) or text document")
    if not stem:
        raise ValueError("The document needs a file name")
    return stem + suffix


def validate_document(filename: str, content: bytes) -> tuple[str, str]:
    """The stored name and content type of an uploaded document, or ValueError."""

    name = safe_document_name(filename)
    if not content:
        raise ValueError("The document is empty")
    if len(content) > MAX_DOCUMENT_BYTES:
        raise ValueError(f"The document is larger than {MAX_DOCUMENT_BYTES // (1024 * 1024)} MB")
    suffix = posixpath.splitext(name)[1]
    if suffix == ".pdf" and not content.startswith(b"%PDF-"):
        raise ValueError("This file is not a PDF")
    if suffix == ".docx" and not content.startswith(b"PK\x03\x04"):
        raise ValueError("This file is not a Word (.docx) document")
    if suffix in (".txt", ".md"):
        try:
            content.decode("utf-8")
        except UnicodeDecodeError:
            raise ValueError("Text documents must be UTF-8") from None
    return name, DOCUMENT_TYPES[suffix]


def document_blob_name(case_id: str, name: str) -> str:
    return document_prefix(case_id) + name


def documents_open(row: Mapping[str, Any], approvals: Iterable[Mapping[str, Any]]) -> bool:
    """Documents are taken while the intake agent can still draft: before approval 1, case not cancelled."""

    return (str(row["stage"]) in DOCUMENT_STAGES and row["status"] != "cancelled"
            and not any(str(a["approval_kind"]) == "account_terms" for a in approvals))
