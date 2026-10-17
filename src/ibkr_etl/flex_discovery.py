from __future__ import annotations

import hashlib
import json
import os
import urllib.parse
import urllib.request
import uuid
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import psycopg
from azure.identity import ManagedIdentityCredential
from psycopg.rows import dict_row

from .config import load_settings
from .flex_client import FlexClient, FlexError, is_lockout_error
from .flex_recipe import check_flex_recipe
from .flex_sources import discovered_account_summaries
from .parser import parse_flex_xml
from .service import ingest_content


VAULT_SCOPE = "https://vault.azure.net/.default"


class ValidationBlocked(RuntimeError):
    """A safety control blocked validation before another IBKR request was allowed."""


def _required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def _secret_value(vault_uri: str, secret_name: str, client_id: str) -> str:
    parsed = urllib.parse.urlparse(vault_uri.rstrip("/"))
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or not parsed.hostname.endswith(".vault.azure.net")
    ):
        raise RuntimeError("Flex token vault URI is invalid")
    if not secret_name.startswith("ibkr-flex-source-"):
        raise RuntimeError("Flex token secret reference is outside the dedicated prefix")
    token = ManagedIdentityCredential(client_id=client_id).get_token(VAULT_SCOPE).token
    request = urllib.request.Request(
        f"{vault_uri.rstrip('/')}/secrets/{secret_name}?api-version=7.4",
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=15) as response:
        payload = json.load(response)
    value = str(payload.get("value") or "").strip()
    if not value:
        raise RuntimeError("Flex token secret is empty")
    return value


def recipe_summary(content: bytes) -> dict[str, object]:
    """What the statement lacks against the platform's Flex recipe: section names and messages only.

    Never raises, so a statement the check cannot read never stops discovery.
    """
    try:
        result = check_flex_recipe(content)
    except Exception as error:  # noqa: BLE001 - the check is advisory here
        return {"status": "unchecked", "reason": str(error)[:200], "problems": []}
    problems: dict[tuple[str, str, str], dict[str, object]] = {}
    for account, findings in sorted(result.account_findings.items()):
        for finding in (*result.statement_findings, *findings):
            if finding.status == "pass":
                continue
            key = (finding.section, finding.status, finding.message)
            item = problems.setdefault(key, {"section": finding.section, "label": finding.label,
                                             "status": finding.status, "message": finding.message,
                                             "accounts": []})
            if account not in item["accounts"]:
                item["accounts"].append(account)
    ordered = sorted(problems.values(), key=lambda p: (p["status"] != "fail", str(p["label"])))
    return {"status": result.status, "problems": ordered[:20]}


def _claim_request(connection, tenant_id: str):
    with connection.transaction():
        row = connection.execute(
            """
            SELECT r.validation_request_id, r.flex_source_id,
                   r.requested_from, r.requested_to,
                   q.query_id, q.status AS source_status,
                   q.credential_id, c.key_vault_secret_name,
                   c.status AS credential_status
            FROM ibkr_flex_source_validation_requests r
            JOIN ibkr_flex_queries q
              ON q.tenant_id = r.tenant_id AND q.flex_source_id = r.flex_source_id
            JOIN ibkr_flex_credentials c
              ON c.tenant_id = q.tenant_id AND c.credential_id = q.credential_id
            WHERE r.tenant_id = %s AND r.status = 'pending'
            ORDER BY r.created_at
            FOR UPDATE OF r SKIP LOCKED
            LIMIT 1
            """,
            (tenant_id,),
        ).fetchone()
        if row is None:
            return None
        if row["source_status"] not in {"pending_validation", "validated"}:
            connection.execute(
                """
                UPDATE ibkr_flex_source_validation_requests
                SET status = 'blocked', error_phase = 'preflight',
                    error_code = 'source_not_validatable', completed_at = now()
                WHERE tenant_id = %s AND validation_request_id = %s
                """,
                (tenant_id, row["validation_request_id"]),
            )
            return None
        connection.execute(
            """
            UPDATE ibkr_flex_source_validation_requests
            SET status = 'running', attempt_count = 1, started_at = now()
            WHERE tenant_id = %s AND validation_request_id = %s
            """,
            (tenant_id, row["validation_request_id"]),
        )
        return dict(row)


def _authorize_send(connection, tenant_id: str, request: dict) -> None:
    credential_id = request["credential_id"]
    lock_key = f"ibkr-flex-credential:{credential_id}"
    locked = connection.execute(
        "SELECT pg_try_advisory_lock(hashtext(%s))", (lock_key,)
    ).fetchone()[0]
    if not locked:
        raise ValidationBlocked("credential_operation_active")
    request["lock_key"] = lock_key
    with connection.transaction():
        circuit = connection.execute(
            """
            SELECT last_send_at, blocked_until, requires_manual_reset
            FROM ibkr_credential_circuit_state
            WHERE tenant_id = %s AND credential_id = %s
            FOR UPDATE
            """,
            (tenant_id, credential_id),
        ).fetchone()
        now = datetime.now(timezone.utc)
        if circuit:
            if circuit["requires_manual_reset"]:
                raise ValidationBlocked("credential_manual_reset_required")
            if circuit["blocked_until"] and circuit["blocked_until"] > now:
                raise ValidationBlocked("credential_cooling_down")
            if circuit["last_send_at"] and circuit["last_send_at"] + timedelta(seconds=10) > now:
                raise ValidationBlocked("credential_used_too_recently")
        connection.execute(
            """
            INSERT INTO ibkr_credential_circuit_state(
                tenant_id, credential_id, active_flex_source_id,
                last_send_at, consecutive_failures, requires_manual_reset, updated_at
            ) VALUES (%s, %s, %s, now(), 0, false, now())
            ON CONFLICT (tenant_id, credential_id) DO UPDATE SET
                active_flex_source_id = EXCLUDED.active_flex_source_id,
                last_send_at = EXCLUDED.last_send_at,
                updated_at = now()
            """,
            (tenant_id, credential_id, request["flex_source_id"]),
        )
    # The send record must outlive a crash or rollback during the request.
    connection.commit()


def _complete_failure(
    connection, tenant_id: str, request: dict, *, phase: str, code: str, lockout: bool
) -> None:
    with connection.transaction():
        connection.execute(
            """
            UPDATE ibkr_credential_circuit_state
            SET last_error_at = now(), last_error_phase = %s, last_error_code = %s,
                consecutive_failures = consecutive_failures + 1,
                blocked_until = CASE WHEN %s THEN NULL ELSE now() + interval '30 minutes' END,
                blocked_reason = CASE WHEN %s THEN 'lockout-or-credentials' ELSE 'transient-failure' END,
                requires_manual_reset = %s, updated_at = now()
            WHERE tenant_id = %s AND credential_id = %s
            """,
            (phase, code, lockout, lockout, lockout, tenant_id, request["credential_id"]),
        )
        connection.execute(
            """
            UPDATE ibkr_flex_source_validation_requests
            SET status = %s, error_phase = %s, error_code = %s, completed_at = now()
            WHERE tenant_id = %s AND validation_request_id = %s
            """,
            (
                "blocked" if lockout else "failed",
                phase,
                code,
                tenant_id,
                request["validation_request_id"],
            ),
        )
        connection.execute(
            """
            UPDATE ibkr_flex_source_runs
            SET status = %s, error_phase = %s, error_code = %s, completed_at = now()
            WHERE tenant_id = %s AND source_run_id = %s
            """,
            (
                "blocked" if lockout else "failed",
                phase,
                code,
                tenant_id,
                request["source_run_id"],
            ),
        )


def _complete_preflight_block(connection, tenant_id: str, request: dict, code: str) -> None:
    """Close the one-attempt request without weakening an existing circuit state."""

    with connection.transaction():
        connection.execute(
            """
            UPDATE ibkr_flex_source_validation_requests
            SET status = 'blocked', error_phase = 'preflight', error_code = %s,
                completed_at = now()
            WHERE tenant_id = %s AND validation_request_id = %s
            """,
            (code, tenant_id, request["validation_request_id"]),
        )
        connection.execute(
            """
            UPDATE ibkr_flex_source_runs
            SET status = 'blocked', error_phase = 'preflight', error_code = %s,
                completed_at = now()
            WHERE tenant_id = %s AND source_run_id = %s
            """,
            (code, tenant_id, request["source_run_id"]),
        )


def run_one_validation() -> dict[str, object]:
    database_url = _required("DATABASE_URL")
    tenant_id = _required("PORTFOLIO_TENANT_ID")
    vault_uri = _required("FLEX_TOKEN_VAULT_URI")
    client_id = _required("AZURE_CLIENT_ID")
    with psycopg.connect(database_url, row_factory=dict_row) as connection:
        connection.execute("SELECT set_config('app.tenant_id', %s, false)", (tenant_id,))
        request = _claim_request(connection, tenant_id)
        if request is None:
            return {"status": "idle", "message": "No pending validation request"}
        source_run_id = uuid.uuid4()
        request["source_run_id"] = source_run_id
        with connection.transaction():
            connection.execute(
                """
                INSERT INTO ibkr_flex_source_runs(
                    source_run_id, tenant_id, flex_source_id, requested_from,
                    requested_to, status, correlation_id, started_at
                ) VALUES (%s, %s, %s, %s, %s, 'started', %s, now())
                """,
                (
                    source_run_id,
                    tenant_id,
                    request["flex_source_id"],
                    request["requested_from"],
                    request["requested_to"],
                    str(request["validation_request_id"]),
                ),
            )
        try:
            _authorize_send(connection, tenant_id, request)
            token = _secret_value(vault_uri, request["key_vault_secret_name"], client_id)
            settings = replace(
                load_settings(),
                flex_token=token,
                flex_query_id=str(request["query_id"]),
                database_url=database_url,
                tenant_id=tenant_id,
            )
            report = FlexClient(settings).request_report(
                request["requested_from"], request["requested_to"]
            )
            token = ""
            parsed = parse_flex_xml(report.content)
            accounts = discovered_account_summaries(parsed)
            if not accounts or len(accounts) > 250:
                raise RuntimeError("invalid_discovered_account_count")
            recipe = recipe_summary(report.content)
            report_sha256 = hashlib.sha256(report.content).hexdigest()
            reference_sha256 = hashlib.sha256(
                report.reference_code.encode("utf-8")
            ).hexdigest()
            ingestion = ingest_content(
                settings,
                report.content,
                run_type="flex-source-validation-import",
                requested_from=request["requested_from"],
                requested_to=request["requested_to"],
                reference_code=report.reference_code,
                content_type=report.content_type,
            )
            with connection.transaction():
                for account in accounts:
                    account_id = str(account["source_account_id"])
                    connection.execute(
                        """
                        INSERT INTO ibkr_flex_source_accounts(
                            tenant_id, flex_source_id, source_account_id,
                            account_type, base_currency, account_name,
                            discovery_status, row_counts_json,
                            first_seen_at, last_seen_at
                        ) VALUES (%s, %s, %s, %s, %s, %s, 'discovered', %s::jsonb, now(), now())
                        ON CONFLICT (tenant_id, flex_source_id, source_account_id) DO UPDATE SET
                            account_type = EXCLUDED.account_type,
                            base_currency = EXCLUDED.base_currency,
                            account_name = EXCLUDED.account_name,
                            row_counts_json = EXCLUDED.row_counts_json,
                            last_seen_at = now()
                        """,
                        (
                            tenant_id,
                            request["flex_source_id"],
                            account_id,
                            account["account_type"],
                            account["base_currency"],
                            account["account_name"],
                            json.dumps(
                                {
                                    "rows": account["row_count"],
                                    "sections": account["section_counts"],
                                    "currencies": account["observed_currencies"],
                                },
                                sort_keys=True,
                            ),
                        ),
                    )
                    connection.execute(
                        """
                        INSERT INTO ibkr_flex_source_run_accounts(
                            tenant_id, source_run_id, source_account_id,
                            status, row_counts_json, completed_at
                        ) VALUES (%s, %s, %s, 'discovered', %s::jsonb, now())
                        """,
                        (
                            tenant_id,
                            source_run_id,
                            account_id,
                            json.dumps({"rows": account["row_count"]}),
                        ),
                    )
                connection.execute(
                    """
                    UPDATE ibkr_flex_queries
                    SET status = 'validated', last_reference_code_sha256 = %s,
                        last_success_at = now(), updated_at = now()
                    WHERE tenant_id = %s AND flex_source_id = %s
                    """,
                    (reference_sha256, tenant_id, request["flex_source_id"]),
                )
                connection.execute(
                    """
                    UPDATE ibkr_flex_credentials
                    SET status = 'active', last_validated_at = now(), updated_at = now()
                    WHERE tenant_id = %s AND credential_id = %s
                    """,
                    (tenant_id, request["credential_id"]),
                )
                connection.execute(
                    """
                    UPDATE ibkr_credential_circuit_state
                    SET last_error_at = NULL, last_error_phase = NULL,
                        last_error_code = NULL, consecutive_failures = 0,
                        blocked_until = NULL, blocked_reason = NULL,
                        requires_manual_reset = false, updated_at = now()
                    WHERE tenant_id = %s AND credential_id = %s
                    """,
                    (tenant_id, request["credential_id"]),
                )
                connection.execute(
                    """
                    UPDATE ibkr_flex_source_runs
                    SET status = 'succeeded', reference_code_sha256 = %s,
                        report_sha256 = %s, discovered_account_count = %s,
                        processed_account_count = %s, completed_at = now()
                    WHERE tenant_id = %s AND source_run_id = %s
                    """,
                    (
                        reference_sha256,
                        report_sha256,
                        len(accounts),
                        len(accounts),
                        tenant_id,
                        source_run_id,
                    ),
                )
                connection.execute(
                    """
                    UPDATE ibkr_flex_source_validation_requests
                    SET status = 'succeeded', result_json = %s::jsonb,
                        completed_at = now()
                    WHERE tenant_id = %s AND validation_request_id = %s
                    """,
                    (
                        json.dumps(
                            {
                                "discovered_accounts": [
                                    account["source_account_id"] for account in accounts
                                ],
                                "account_count": len(accounts),
                                "report_sha256": report_sha256,
                                "ingestion_run_id": ingestion["run_id"],
                                "reconciliation": ingestion["reconciliation"],
                                "recipe": recipe,
                            },
                            sort_keys=True,
                        ),
                        tenant_id,
                        request["validation_request_id"],
                    ),
                )
            return {
                "status": "succeeded",
                "flex_source_id": str(request["flex_source_id"]),
                "discovered_account_count": len(accounts),
                "ingestion_run_id": ingestion["run_id"],
            }
        except ValidationBlocked as error:
            _complete_preflight_block(connection, tenant_id, request, str(error))
            raise
        except FlexError as error:
            lockout = error.lockout or is_lockout_error(error.code, str(error))
            _complete_failure(
                connection,
                tenant_id,
                request,
                phase=error.phase,
                code=error.code or "flex_error",
                lockout=lockout,
            )
            raise
        except Exception as error:
            _complete_failure(
                connection,
                tenant_id,
                request,
                phase="validation",
                code=str(error)[:80],
                lockout=False,
            )
            raise
        finally:
            token = ""
            if request.get("lock_key"):
                connection.rollback()
                connection.execute(
                    "SELECT pg_advisory_unlock(hashtext(%s))", (request["lock_key"],)
                )
                connection.commit()
