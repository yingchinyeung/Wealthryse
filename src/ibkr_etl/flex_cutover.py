"""Move the v1.1-fed accounts onto v2's own daily Flex pull, and back.

Today account-sync imports v1.1's export for every subject whose current
daily_ingestion registration says source "existing_v11". The cutover:

- records v1.1's Flex token (a Key Vault reference only, never the value) and
  query as an active, scheduled v2 Flex source;
- records the accounts in one statement v1.1 already downloaded, so no IBKR
  request and no live rows are needed;
- binds each moved subject to that source from the effective date;
- closes the subject's existing_v11 registration the day before and adds an
  ibkr_flex one from the effective date, in the format subject_activation uses.

Dates are data dates, as the dispatcher's run_date is. With
INSTITUTIONAL_DISPATCH_RUN_DATE=previous_hkt_day the pull for date X runs on
X + 1, so a switch on day D uses effective date D - 1.

Revert disables the ibkr_flex registration, re-opens the existing_v11 one,
retires the binding and stops the schedule. Nothing is deleted, so the role
behind database-approval-url (no DELETE grant) can run both directions.
Connections are psycopg connections with a dict row factory and the tenant
set; the caller commits.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from .flex_discovery import recipe_summary
from .flex_sources import discovered_account_summaries
from .parser import parse_flex_xml
from .util import canonical_json, sha256_text, utc_now_iso


V11_SOURCE = "existing_v11"
FLEX_SOURCE = "ibkr_flex"
CHANNEL = "v11_flex_cutover"
AUDIT_ACTOR = "v11-flex-cutover"
LOCK_KEY = "wealthryse-v11-flex-cutover"
# The credential broker's names; the worker only reads this prefix.
SECRET_NAME = re.compile(r"^ibkr-flex-source-([0-9a-f]{32})$")
QUERY_ID = re.compile(r"^[0-9]{1,20}$")
FINGERPRINT = re.compile(r"^[0-9a-f]{16}$")
IDENTITY_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class CutoverRefused(RuntimeError):
    """The database or the inputs are not in a state the cutover can change safely."""

    def __init__(self, plan: dict[str, Any]):
        super().__init__("; ".join(plan["problems"]) or "Cutover refused")
        self.plan = plan


@dataclass(frozen=True)
class CutoverRequest:
    tenant_id: str
    effective_date: date
    token_secret_name: str
    query_id: str
    statement: bytes
    approver_sha256: str | None = None
    subject_ids: tuple[str, ...] = ()
    token_fingerprint: str | None = None
    credential_display_name: str = "IBKR v1.1 Flex token"
    query_display_name: str = "IBKR v1.1 daily query"
    statement_name: str = field(default="statement.xml")


def identity_sha256(value: str) -> str:
    """The digest the website stores for a signed-in identity."""

    candidate = value.strip().lower()
    if not candidate:
        raise ValueError("An approver identity is required")
    return hashlib.sha256(candidate.encode("utf-8")).hexdigest()


def registration_configuration(start_date: str, source: str) -> tuple[str, str]:
    configuration = canonical_json({"start_date": start_date, "source": source})
    return configuration, sha256_text(configuration)


def _json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    return json.loads(value) if value else {}


def _lock(connection) -> None:
    connection.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (LOCK_KEY,))


def _v11_targets(connection, tenant_id: str) -> list[str]:
    """The subjects account-sync imports today (its own registry query)."""

    rows = connection.execute(
        """
        SELECT r.subject_id
        FROM subject_operating_registrations r
        JOIN platform_subjects s
          ON s.tenant_id = r.tenant_id AND s.subject_id = r.subject_id
        WHERE r.tenant_id = %s AND r.capability = 'daily_ingestion'
          AND r.operating_state IN ('preview', 'active')
          AND r.effective_to IS NULL
          AND s.subject_type = 'physical_account'
          AND r.configuration_json ->> 'source' = %s
        ORDER BY r.subject_id
        """,
        (tenant_id, V11_SOURCE),
    ).fetchall()
    return [str(row["subject_id"]) for row in rows]


def _statement_accounts(statement: bytes) -> tuple[str, list[dict[str, Any]], dict]:
    report = parse_flex_xml(statement)
    accounts = discovered_account_summaries(report)
    if not accounts or len(accounts) > 250:
        raise ValueError("The statement names no usable account")
    return hashlib.sha256(statement).hexdigest(), accounts, recipe_summary(statement)


def _moved_on(connection, tenant_id: str, effective_date: date) -> list[str]:
    rows = connection.execute(
        """
        SELECT DISTINCT subject_id FROM subject_operating_registrations
        WHERE tenant_id = %s AND capability = 'daily_ingestion'
          AND effective_from = %s AND configuration_json ->> 'source' = %s
        ORDER BY subject_id
        """,
        (tenant_id, effective_date, FLEX_SOURCE),
    ).fetchall()
    return [str(row["subject_id"]) for row in rows]


def _subject_state(connection, tenant_id: str, subject_id: str, *, lock: bool) -> dict[str, Any]:
    # A dry run may use a read-only login, which cannot take row locks.
    for_update = "FOR UPDATE" if lock else ""
    subject = connection.execute(
        """
        SELECT subject_id, subject_type, lifecycle_status, source_account_id
        FROM platform_subjects WHERE tenant_id = %s AND subject_id = %s
        """,
        (tenant_id, subject_id),
    ).fetchone()
    registrations = connection.execute(
        f"""
        SELECT registration_id, operating_state, effective_from, effective_to,
               configuration_json, configuration_sha256, activation_approval_id
        FROM subject_operating_registrations
        WHERE tenant_id = %s AND subject_id = %s AND capability = 'daily_ingestion'
        ORDER BY effective_from DESC
        {for_update}
        """,
        (tenant_id, subject_id),
    ).fetchall()
    bindings = connection.execute(
        f"""
        SELECT flex_source_id, source_account_id, effective_from, effective_to,
               is_primary, status
        FROM subject_flex_source_bindings
        WHERE tenant_id = %s AND subject_id = %s
        ORDER BY effective_from DESC
        {for_update}
        """,
        (tenant_id, subject_id),
    ).fetchall()
    return {
        "subject": None if subject is None else dict(subject),
        "registrations": [dict(row) for row in registrations],
        "bindings": [dict(row) for row in bindings],
    }


def _source(row: dict[str, Any]) -> str | None:
    return _json_object(row["configuration_json"]).get("source")


def _credential_and_query(connection, request: CutoverRequest, credential_id: uuid.UUID, *, lock: bool):
    for_update = "FOR UPDATE" if lock else ""
    credential = connection.execute(
        f"""
        SELECT credential_id, display_name, key_vault_secret_name, token_fingerprint, status
        FROM ibkr_flex_credentials WHERE tenant_id = %s AND credential_id = %s
        {for_update}
        """,
        (request.tenant_id, credential_id),
    ).fetchone()
    named = connection.execute(
        """
        SELECT credential_id FROM ibkr_flex_credentials
        WHERE tenant_id = %s AND (display_name = %s OR key_vault_secret_name = %s)
          AND credential_id <> %s
        """,
        (request.tenant_id, request.credential_display_name, request.token_secret_name, credential_id),
    ).fetchone()
    query = connection.execute(
        f"""
        SELECT flex_source_id, status, schedule_enabled
        FROM ibkr_flex_queries
        WHERE tenant_id = %s AND credential_id = %s AND query_id = %s
        {for_update}
        """,
        (request.tenant_id, credential_id, request.query_id),
    ).fetchone()
    return (
        None if credential is None else dict(credential),
        named,
        None if query is None else dict(query),
    )


def plan_cutover(
    connection, request: CutoverRequest, *, today: date, lock: bool = False
) -> dict[str, Any]:
    """Read-only check and plan. Refusal reasons go in "problems"."""

    problems: list[str] = []
    warnings: list[str] = []
    match = SECRET_NAME.fullmatch(request.token_secret_name.strip())
    if not match:
        problems.append("The token secret name must look like ibkr-flex-source-<32 hex>")
    if not QUERY_ID.fullmatch(request.query_id):
        problems.append("The Flex query ID must be digits only")
    if request.token_fingerprint is not None and not FINGERPRINT.fullmatch(request.token_fingerprint):
        problems.append("The token fingerprint must be 16 lower-case hex characters")
    if request.approver_sha256 is not None and not IDENTITY_SHA256.fullmatch(request.approver_sha256):
        problems.append("The approver digest is invalid")
    if request.effective_date > today:
        problems.append("The effective date is in the future; run the cutover on the switch day")
    try:
        statement_sha256, accounts, recipe = _statement_accounts(request.statement)
    except ValueError as error:
        problems.append(f"The statement cannot be read: {error}")
        statement_sha256, accounts, recipe = None, [], {"status": "unchecked", "problems": []}
    statement_accounts = {str(item["source_account_id"]): item for item in accounts}
    plan: dict[str, Any] = {
        "status": "refused",
        "effective_date": request.effective_date.isoformat(),
        "closes_existing_v11_on": (request.effective_date - timedelta(days=1)).isoformat(),
        "first_dispatch_day": {
            "previous_hkt_day": (request.effective_date + timedelta(days=1)).isoformat(),
            "current_hkt_day": request.effective_date.isoformat(),
        },
        "statement": {
            "name": request.statement_name,
            "sha256": statement_sha256,
            "accounts": sorted(statement_accounts),
            "recipe": recipe,
        },
        "problems": problems,
        "warnings": warnings,
        "subjects": [],
    }
    if not match:
        return plan
    credential_id = uuid.UUID(match.group(1))
    credential, named, query = _credential_and_query(connection, request, credential_id, lock=lock)
    plan["credential"] = {
        "credential_id": str(credential_id),
        "key_vault_secret_name": request.token_secret_name,
        "action": "insert" if credential is None else (
            "keep" if credential["status"] == "active" else "activate"
        ),
    }
    plan["query"] = {
        "flex_source_id": None if query is None else str(query["flex_source_id"]),
        "query_id_sha256": sha256_text(request.query_id),
        "action": "insert" if query is None else (
            "keep" if query["status"] == "active" and query["schedule_enabled"] else "activate"
        ),
    }
    if named is not None:
        problems.append("Another credential already uses this display name or secret name")
    if credential is not None:
        if credential["key_vault_secret_name"] != request.token_secret_name:
            problems.append("The existing credential row references a different secret")
        if credential["status"] in {"suspended", "expired", "revoked"}:
            problems.append(f"The credential is {credential['status']}")
    if query is not None and query["status"] in {"suspended", "retired"}:
        problems.append(f"The Flex query source is {query['status']}")
    flex_source_id = None if query is None else str(query["flex_source_id"])

    v11_targets = _v11_targets(connection, request.tenant_id)
    requested = sorted({item.strip().upper() for item in request.subject_ids if item.strip()})
    # By default: what account-sync imports today, plus what an earlier run of
    # this cutover already moved, so a re-run is a no-op.
    subject_ids = requested or sorted(
        set(v11_targets) | set(_moved_on(connection, request.tenant_id, request.effective_date))
    )
    if not subject_ids:
        problems.append("No subject is fed by the v1.1 export and none was named")
    moved_accounts: set[str] = set()
    for subject_id in subject_ids:
        state = _subject_state(connection, request.tenant_id, subject_id, lock=lock)
        entry: dict[str, Any] = {"subject_id": subject_id}
        plan["subjects"].append(entry)
        subject = state["subject"]
        if subject is None:
            problems.append(f"{subject_id}: the subject does not exist")
            continue
        if subject["subject_type"] != "physical_account":
            problems.append(f"{subject_id}: only physical accounts can move to a Flex source")
            continue
        account_id = str(subject["source_account_id"] or subject_id).upper()
        entry["source_account_id"] = account_id
        if account_id not in statement_accounts:
            problems.append(f"{subject_id}: account {account_id} is not in the statement")
        moved_accounts.add(account_id)
        current = [row for row in state["registrations"] if row["effective_to"] is None]
        flex_at_date = next(
            (row for row in state["registrations"]
             if row["effective_from"] == request.effective_date and _source(row) == FLEX_SOURCE),
            None,
        )
        open_bindings = [
            row for row in state["bindings"]
            if row["is_primary"] and row["effective_to"] is None and row["status"] in {"active", "pending"}
        ]
        if len(current) != 1:
            problems.append(f"{subject_id}: no single current daily_ingestion registration")
            continue
        registration = current[0]
        source = _source(registration)
        entry["operating_state"] = registration["operating_state"]
        if source == FLEX_SOURCE:
            closed_v11 = next(
                (row for row in state["registrations"]
                 if _source(row) == V11_SOURCE
                 and row["effective_to"] == request.effective_date - timedelta(days=1)),
                None,
            )
            bound = [
                row for row in open_bindings
                if flex_source_id and str(row["flex_source_id"]) == flex_source_id
                and row["effective_from"] == request.effective_date
            ]
            if registration["effective_from"] == request.effective_date and closed_v11 and len(bound) == 1:
                entry["action"] = "already_moved"
            else:
                problems.append(f"{subject_id}: already on a Flex registration with other settings")
            continue
        if source != V11_SOURCE:
            problems.append(f"{subject_id}: its daily data does not come from the v1.1 export ({source})")
            continue
        if subject_id not in v11_targets:
            problems.append(f"{subject_id}: account-sync does not import it today")
            continue
        if registration["effective_from"] >= request.effective_date:
            problems.append(f"{subject_id}: its existing_v11 registration starts on or after the effective date")
            continue
        if open_bindings:
            problems.append(f"{subject_id}: it already has an open primary Flex binding")
            continue
        if flex_at_date is not None and flex_at_date["operating_state"] != "disabled":
            problems.append(f"{subject_id}: an ibkr_flex registration from the effective date already exists")
            continue
        configuration = _json_object(registration["configuration_json"])
        start_date = configuration.get("start_date")
        if not isinstance(start_date, str):
            problems.append(f"{subject_id}: its registration has no start_date")
            continue
        if registration["operating_state"] != "active":
            warnings.append(
                f"{subject_id}: {registration['operating_state']} subjects move too, but the "
                "dispatcher plans work only for active ones, and the website's activation "
                "still writes an existing_v11 registration"
            )
        entry.update(
            {
                "action": "move",
                "old_registration_id": str(registration["registration_id"]),
                "start_date": start_date,
                "binding_status": "active" if registration["operating_state"] == "active" else "pending",
                "reactivates_registration_id": (
                    None if flex_at_date is None else str(flex_at_date["registration_id"])
                ),
            }
        )
    unbound = sorted(set(statement_accounts) - moved_accounts)
    if unbound:
        warnings.append(
            "The daily pull loads every account in the statement; these are not moved: "
            + ", ".join(unbound)
        )
    if request.effective_date < today - timedelta(days=1):
        warnings.append("The effective date is more than one day back; account-sync must already be stopped")
    if recipe.get("status") == "fail":
        warnings.append("The statement does not match v2's Flex recipe; see statement.recipe")
    if problems:
        return plan
    moving = [item for item in plan["subjects"] if item.get("action") == "move"]
    unchanged_source = plan["credential"]["action"] == "keep" and plan["query"]["action"] == "keep"
    plan["status"] = "already_applied" if not moving and unchanged_source else "ready"
    return plan


def _audit(connection, tenant_id: str, event_type: str, details: dict[str, Any]) -> None:
    connection.execute(
        """
        INSERT INTO audit_events(
            tenant_id, occurred_at, actor_type, actor_id, event_type,
            subject_id, outcome, details_json
        ) VALUES (%s, %s, 'system', %s, %s, NULL, 'success', %s)
        """,
        (tenant_id, utc_now_iso(), AUDIT_ACTOR, event_type, canonical_json(details)),
    )


def apply_cutover(connection, request: CutoverRequest, *, today: date) -> dict[str, Any]:
    """Write the cutover in the caller's transaction. Raises CutoverRefused."""

    if request.approver_sha256 is None:
        raise ValueError("Applying the cutover requires an approver")
    _lock(connection)
    plan = plan_cutover(connection, request, today=today, lock=True)
    if plan["status"] == "refused":
        raise CutoverRefused(plan)
    if plan["status"] == "already_applied":
        return plan
    tenant_id = request.tenant_id
    approver = request.approver_sha256
    credential_id = uuid.UUID(plan["credential"]["credential_id"])
    metadata = {"submission_channel": CHANNEL, "statement_sha256": plan["statement"]["sha256"]}
    if plan["credential"]["action"] == "insert":
        connection.execute(
            """
            INSERT INTO ibkr_flex_credentials(
                credential_id, tenant_id, display_name, key_vault_secret_name,
                token_fingerprint, status, last_validated_at,
                created_by_identity_sha256, approved_by_identity_sha256,
                metadata_json, created_at, updated_at
            ) VALUES (%s, %s, %s, %s, %s, 'active', now(), %s, %s, %s::jsonb, now(), now())
            """,
            (
                credential_id, tenant_id, request.credential_display_name,
                request.token_secret_name, request.token_fingerprint, approver, approver,
                canonical_json(metadata),
            ),
        )
    elif plan["credential"]["action"] == "activate":
        connection.execute(
            """
            UPDATE ibkr_flex_credentials
            SET status = 'active', last_validated_at = now(),
                approved_by_identity_sha256 = %s, updated_at = now()
            WHERE tenant_id = %s AND credential_id = %s
            """,
            (approver, tenant_id, credential_id),
        )
    if plan["query"]["action"] == "insert":
        flex_source_id = uuid.uuid4()
        connection.execute(
            """
            INSERT INTO ibkr_flex_queries(
                flex_source_id, tenant_id, credential_id, query_id, display_name,
                schedule_enabled, schedule_time_zone, schedule_hour, schedule_minute,
                status, metadata_json, created_at, updated_at
            ) VALUES (%s, %s, %s, %s, %s, true, 'Asia/Hong_Kong', 16, 20,
                      'active', %s::jsonb, now(), now())
            """,
            (
                flex_source_id, tenant_id, credential_id, request.query_id,
                request.query_display_name,
                canonical_json({**metadata, "recipe_status": plan["statement"]["recipe"].get("status")}),
            ),
        )
        plan["query"]["flex_source_id"] = str(flex_source_id)
    else:
        flex_source_id = uuid.UUID(plan["query"]["flex_source_id"])
        connection.execute(
            """
            UPDATE ibkr_flex_queries
            SET status = 'active', schedule_enabled = true, updated_at = now()
            WHERE tenant_id = %s AND flex_source_id = %s
            """,
            (tenant_id, flex_source_id),
        )
    moved = {item.get("source_account_id") for item in plan["subjects"]}
    for account in discovered_account_summaries(parse_flex_xml(request.statement)):
        account_id = str(account["source_account_id"])
        connection.execute(
            """
            INSERT INTO ibkr_flex_source_accounts(
                tenant_id, flex_source_id, source_account_id, account_type,
                base_currency, account_name, discovery_status, row_counts_json,
                first_seen_at, last_seen_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb, now(), now())
            ON CONFLICT (tenant_id, flex_source_id, source_account_id) DO UPDATE SET
                account_type = EXCLUDED.account_type,
                base_currency = EXCLUDED.base_currency,
                account_name = EXCLUDED.account_name,
                row_counts_json = EXCLUDED.row_counts_json,
                discovery_status = CASE WHEN EXCLUDED.discovery_status = 'selected'
                    THEN 'selected' ELSE ibkr_flex_source_accounts.discovery_status END,
                last_seen_at = now()
            """,
            (
                tenant_id, flex_source_id, account_id, account["account_type"],
                account["base_currency"], account["account_name"],
                "selected" if account_id in moved else "discovered",
                canonical_json(
                    {
                        "rows": account["row_count"],
                        "sections": account["section_counts"],
                        "currencies": account["observed_currencies"],
                    }
                ),
            ),
        )
    effective = request.effective_date
    for item in plan["subjects"]:
        if item.get("action") != "move":
            continue
        closed = connection.execute(
            """
            UPDATE subject_operating_registrations
            SET effective_to = %s, changed_by_identity_sha256 = %s, updated_at = now()
            WHERE tenant_id = %s AND registration_id = %s AND effective_to IS NULL
            """,
            (effective - timedelta(days=1), approver, tenant_id, item["old_registration_id"]),
        )
        if closed.rowcount != 1:
            raise RuntimeError(f"{item['subject_id']}: the existing_v11 registration changed")
        old = connection.execute(
            "SELECT activation_approval_id FROM subject_operating_registrations WHERE registration_id = %s",
            (item["old_registration_id"],),
        ).fetchone()
        configuration, digest = registration_configuration(item["start_date"], FLEX_SOURCE)
        connection.execute(
            """
            INSERT INTO subject_operating_registrations(
                registration_id, tenant_id, subject_id, capability,
                operating_state, effective_from, configuration_json,
                configuration_sha256, activation_approval_id,
                changed_by_identity_sha256, created_at, updated_at
            ) VALUES (%s, %s, %s, 'daily_ingestion', %s, %s, %s::jsonb, %s, %s, %s, now(), now())
            ON CONFLICT (tenant_id, subject_id, capability, effective_from)
            DO UPDATE SET operating_state = EXCLUDED.operating_state, effective_to = NULL,
                          configuration_json = EXCLUDED.configuration_json,
                          configuration_sha256 = EXCLUDED.configuration_sha256,
                          activation_approval_id = EXCLUDED.activation_approval_id,
                          changed_by_identity_sha256 = EXCLUDED.changed_by_identity_sha256,
                          updated_at = now()
            """,
            (
                uuid.uuid4(), tenant_id, item["subject_id"], item["operating_state"],
                effective, configuration, digest, old["activation_approval_id"], approver,
            ),
        )
        connection.execute(
            """
            INSERT INTO subject_flex_source_bindings(
                tenant_id, subject_id, flex_source_id, source_account_id,
                effective_from, is_primary, status, created_at, updated_at
            ) VALUES (%s, %s, %s, %s, %s, true, %s, now(), now())
            ON CONFLICT (tenant_id, subject_id, flex_source_id, source_account_id, effective_from)
            DO UPDATE SET status = EXCLUDED.status, is_primary = true,
                          effective_to = NULL, updated_at = now()
            """,
            (
                tenant_id, item["subject_id"], flex_source_id, item["source_account_id"],
                effective, item["binding_status"],
            ),
        )
    plan["status"] = "applied"
    _audit(
        connection,
        tenant_id,
        "v11_accounts_moved_to_flex",
        {
            "approved_by_identity_sha256": approver,
            "effective_date": plan["effective_date"],
            "credential": plan["credential"],
            "query": plan["query"],
            "statement_sha256": plan["statement"]["sha256"],
            "subjects": plan["subjects"],
            "warnings": plan["warnings"],
        },
    )
    return plan


def plan_revert(
    connection,
    *,
    tenant_id: str,
    effective_date: date,
    subject_ids: tuple[str, ...] = (),
    lock: bool = False,
) -> dict[str, Any]:
    """Read-only plan to restore the existing_v11 registrations the cutover closed."""

    problems: list[str] = []
    warnings: list[str] = []
    closed_on = effective_date - timedelta(days=1)
    candidates = sorted({item.strip().upper() for item in subject_ids if item.strip()}) or _moved_on(
        connection, tenant_id, effective_date
    )
    plan: dict[str, Any] = {
        "status": "refused",
        "effective_date": effective_date.isoformat(),
        "problems": problems,
        "warnings": warnings,
        "subjects": [],
        "flex_source_ids": [],
    }
    if not candidates:
        problems.append("No subject was moved to the Flex path on this effective date")
    sources: set[str] = set()
    for subject_id in candidates:
        state = _subject_state(connection, tenant_id, subject_id, lock=lock)
        entry: dict[str, Any] = {"subject_id": subject_id}
        plan["subjects"].append(entry)
        flex = next(
            (row for row in state["registrations"]
             if row["effective_from"] == effective_date and _source(row) == FLEX_SOURCE),
            None,
        )
        v11 = next(
            (row for row in state["registrations"]
             if _source(row) == V11_SOURCE and row["effective_to"] in (closed_on, None)),
            None,
        )
        if flex is None or v11 is None:
            problems.append(f"{subject_id}: no cutover on this effective date to revert")
            continue
        bindings = [
            row for row in state["bindings"]
            if row["effective_from"] == effective_date and row["is_primary"]
        ]
        if flex["effective_to"] is not None and v11["effective_to"] is None:
            entry["action"] = "already_reverted"
            continue
        if flex["effective_to"] is not None or v11["effective_to"] != closed_on:
            problems.append(f"{subject_id}: its registrations changed after the cutover")
            continue
        entry.update(
            {
                "action": "revert",
                "flex_registration_id": str(flex["registration_id"]),
                "v11_registration_id": str(v11["registration_id"]),
                "flex_source_ids": sorted({str(row["flex_source_id"]) for row in bindings}),
            }
        )
        sources.update(entry["flex_source_ids"])
    plan["flex_source_ids"] = sorted(sources)
    if sources:
        pulls = connection.execute(
            """
            SELECT COUNT(*) AS n FROM institutional_work_items
            WHERE tenant_id = %s AND work_kind = 'flex_statement_request'
              AND status = 'succeeded' AND flex_source_id = ANY(%s::uuid[])
            """,
            (tenant_id, sorted(sources)),
        ).fetchone()
        if int(pulls["n"]):
            warnings.append(
                f"{int(pulls['n'])} Flex pull(s) already loaded rows; account-sync's "
                "row-count check can fail on rows v1.1 does not have"
            )
    if problems:
        return plan
    reverting = [item for item in plan["subjects"] if item.get("action") == "revert"]
    plan["status"] = "ready" if reverting else "already_reverted"
    return plan


def apply_revert(
    connection,
    *,
    tenant_id: str,
    effective_date: date,
    approver_sha256: str,
    subject_ids: tuple[str, ...] = (),
) -> dict[str, Any]:
    if not IDENTITY_SHA256.fullmatch(approver_sha256 or ""):
        raise ValueError("Reverting the cutover requires an approver")
    _lock(connection)
    plan = plan_revert(
        connection, tenant_id=tenant_id, effective_date=effective_date,
        subject_ids=subject_ids, lock=True,
    )
    if plan["status"] == "refused":
        raise CutoverRefused(plan)
    if plan["status"] == "already_reverted":
        return plan
    for item in plan["subjects"]:
        if item.get("action") != "revert":
            continue
        connection.execute(
            """
            UPDATE subject_operating_registrations
            SET operating_state = 'disabled', effective_to = effective_from,
                changed_by_identity_sha256 = %s, updated_at = now()
            WHERE tenant_id = %s AND registration_id = %s AND effective_to IS NULL
            """,
            (approver_sha256, tenant_id, item["flex_registration_id"]),
        )
        reopened = connection.execute(
            """
            UPDATE subject_operating_registrations
            SET effective_to = NULL, changed_by_identity_sha256 = %s, updated_at = now()
            WHERE tenant_id = %s AND registration_id = %s
            """,
            (approver_sha256, tenant_id, item["v11_registration_id"]),
        )
        if reopened.rowcount != 1:
            raise RuntimeError(f"{item['subject_id']}: the existing_v11 registration changed")
        connection.execute(
            """
            UPDATE subject_flex_source_bindings
            SET status = 'retired', effective_to = effective_from, updated_at = now()
            WHERE tenant_id = %s AND subject_id = %s AND effective_from = %s
              AND effective_to IS NULL
            """,
            (tenant_id, item["subject_id"], effective_date),
        )
    stopped: list[str] = []
    for flex_source_id in plan["flex_source_ids"]:
        remaining = connection.execute(
            """
            SELECT COUNT(*) AS n FROM subject_flex_source_bindings
            WHERE tenant_id = %s AND flex_source_id = %s
              AND status = 'active' AND effective_to IS NULL
            """,
            (tenant_id, flex_source_id),
        ).fetchone()
        if int(remaining["n"]):
            continue
        connection.execute(
            """
            UPDATE ibkr_flex_queries SET schedule_enabled = false, updated_at = now()
            WHERE tenant_id = %s AND flex_source_id = %s
            """,
            (tenant_id, flex_source_id),
        )
        connection.execute(
            """
            UPDATE institutional_work_items
            SET status = 'cancelled', completed_at = now(), updated_at = now(),
                error_code = 'cutover_reverted',
                error_message = 'The v1.1 export feeds these accounts again'
            WHERE tenant_id = %s AND flex_source_id = %s AND status = 'queued'
            """,
            (tenant_id, flex_source_id),
        )
        stopped.append(flex_source_id)
    plan["status"] = "reverted"
    plan["schedules_stopped"] = stopped
    _audit(
        connection,
        tenant_id,
        "v11_accounts_returned_to_export",
        {
            "approved_by_identity_sha256": approver_sha256,
            "effective_date": plan["effective_date"],
            "subjects": plan["subjects"],
            "schedules_stopped": stopped,
            "warnings": plan["warnings"],
        },
    )
    return plan
