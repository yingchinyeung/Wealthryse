from __future__ import annotations

import os
import sys
import uuid
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = PROJECT_ROOT / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from ibkr_etl import lineage  # noqa: E402
from ibkr_etl.config import load_settings  # noqa: E402
from ibkr_etl.db import DEFAULT_TENANT_ID, connect  # noqa: E402
from ibkr_etl.flex_client import FlexClient, FlexError, is_lockout_error  # noqa: E402
from ibkr_etl.flex_discovery import ValidationBlocked, _authorize_send, _secret_value  # noqa: E402
from ibkr_etl.operating_registry import (  # noqa: E402
    claim_next_work,
    complete_work,
    dead_letter_exhausted_and_blocked_work,
    fail_work,
)
from ibkr_etl.service import ingest_content  # noqa: E402
from ibkr_etl.util import canonical_json  # noqa: E402


def _required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def _work_source(connection, flex_source_id: str) -> dict:
    row = connection.execute(
        """
        SELECT q.flex_source_id, q.query_id, q.credential_id,
               q.schedule_time_zone, q.schedule_hour, q.schedule_minute,
               c.key_vault_secret_name
        FROM ibkr_flex_queries q
        JOIN ibkr_flex_credentials c
          ON c.tenant_id = q.tenant_id AND c.credential_id = q.credential_id
        WHERE q.flex_source_id = ? AND q.status = 'active'
          AND q.schedule_enabled AND c.status = 'active'
        """,
        (flex_source_id,),
    ).fetchone()
    if row is None:
        raise RuntimeError("The queued Flex source is no longer active")
    return dict(row)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _send_block(source: dict, now: datetime) -> str | None:
    """Why no IBKR request may be sent now, or None.

    Nothing is sent unless INSTITUTIONAL_FLEX_SENDS_ENABLED=true, and never
    before the source's scheduled time on that day.
    """
    if os.environ.get("INSTITUTIONAL_FLEX_SENDS_ENABLED", "").strip().lower() != "true":
        return "institutional_flex_sends_disabled"
    local = now.astimezone(ZoneInfo(str(source["schedule_time_zone"])))
    scheduled = local.replace(
        hour=int(source["schedule_hour"]), minute=int(source["schedule_minute"]),
        second=0, microsecond=0,
    )
    if local < scheduled:
        return "before_scheduled_send_time"
    return None


def _update_payload(connection, work_item_id: str, evidence: dict) -> None:
    connection.execute(
        """
        UPDATE institutional_work_items
        SET payload_json = payload_json || ?::jsonb, updated_at = now()
        WHERE work_item_id = ?
        """,
        (canonical_json({"result": evidence}), work_item_id),
    )


# Silver tables load_report writes for each Flex record type (see loader.load_report).
SILVER_BY_RECORD_TYPE = (
    (lambda kind: kind == "trade", "silver.trades"),
    (lambda kind: "cashtransaction" in kind, "silver.cash_transactions"),
    (lambda kind: "corporateaction" in kind, "silver.corporate_actions"),
    (lambda kind: "openposition" in kind, "silver.position_snapshots"),
    (lambda kind: "cashreportcurrency" in kind or kind == "cashreport", "silver.cash_snapshots"),
    (lambda kind: "netassetvalue" in kind or "equitysummarybyreportdate" in kind, "silver.nav_snapshots"),
    (lambda kind: "conversionrate" in kind or kind in {"fxrate", "currencyconversionrate"}, "silver.fx_rates"),
    (lambda kind: kind == "fxtransaction", "silver.fx_transactions"),
)


def _record_statement(result: dict) -> None:
    """The statement this run read and the tables it loaded, for its lineage record
    (a no-op unless recording is on)."""
    record = lineage.current()
    if not record.active:
        return
    try:
        ingestion_run_id = result.get("run_id")
        record.append("ingestion_run_ids", ingestion_run_id)
        blob = lineage.blob_dataset(result.get("raw_path"))
        if blob:
            record.input(blob, sha256=result.get("raw_sha256"), ingestion_run_id=ingestion_run_id)
        counts = (result.get("reconciliation") or {}).get("record_types") or {}
        rows = sum(int(value) for key, value in counts.items() if key != "legacy_source_keys_reused")
        record.output("bronze.raw_reports", rows=1)
        record.output("bronze.flex_rows", rows=rows)
        record.output("silver.accounts")
        record.output("silver.instruments")
        for record_type, count in counts.items():
            kind = str(record_type).lower()
            for matches, table in SILVER_BY_RECORD_TYPE:
                if matches(kind):
                    record.output(table, rows=int(count))
                    break
    except Exception:  # noqa: BLE001 - lineage never fails the job
        pass


def _request_statement(connection, claim, *, database_url: str, tenant_id: str) -> dict:
    source = _work_source(connection, str(claim.flex_source_id))
    blocked = _send_block(source, _now())
    if blocked:
        # Ends as any blocked send: no lock, no circuit change, no IBKR request.
        raise ValidationBlocked(blocked)
    request = {**source, "flex_source_id": claim.flex_source_id}
    # Commits the send record before the request, so a crash cannot lose it.
    _authorize_send(connection, tenant_id, request)
    token = ""
    try:
        token = _secret_value(
            _required("FLEX_TOKEN_VAULT_URI"),
            str(source["key_vault_secret_name"]),
            _required("AZURE_CLIENT_ID"),
        )
        run_date = date.fromisoformat(str(claim.payload["run_date"]))
        lookback = max(2, int(os.environ.get("INSTITUTIONAL_FLEX_LOOKBACK_DAYS", "14")))
        start_date = run_date - timedelta(days=lookback - 1)
        settings = replace(
            load_settings(),
            flex_token=token,
            flex_query_id=str(source["query_id"]),
            database_url=database_url,
            tenant_id=tenant_id,
        )
        report = FlexClient(settings).request_report(start_date, run_date)
        token = ""
        result = ingest_content(
            settings,
            report.content,
            run_type="institutional-flex-daily",
            requested_from=start_date,
            requested_to=run_date,
            reference_code=report.reference_code,
            content_type=report.content_type,
        )
        connection.execute(
            """
            UPDATE ibkr_flex_queries
            SET last_success_at = now(), updated_at = now()
            WHERE flex_source_id = ?
            """,
            (claim.flex_source_id,),
        )
        connection.execute(
            """
            UPDATE ibkr_credential_circuit_state
            SET last_error_at = NULL, last_error_phase = NULL,
                last_error_code = NULL, consecutive_failures = 0,
                blocked_until = NULL, blocked_reason = NULL,
                requires_manual_reset = false, updated_at = now()
            WHERE credential_id = ?
            """,
            (source["credential_id"],),
        )
        _record_statement(result)
        return {
            "ingestion_run_id": result["run_id"],
            "requested_from": start_date.isoformat(),
            "requested_to": run_date.isoformat(),
            "raw_path": result["raw_path"],
        }
    finally:
        token = ""
        if request.get("lock_key"):
            try:
                connection.execute(
                    "SELECT pg_advisory_unlock(hashtext(?))", (request["lock_key"],)
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise


def _subject_evidence(connection, subject_id: str) -> dict:
    row = connection.execute(
        """
        SELECT MIN(snapshot_date) AS first_nav_date,
               MAX(snapshot_date) AS latest_nav_date,
               COUNT(DISTINCT snapshot_date) AS nav_observations,
               (SELECT COUNT(*) FROM position_snapshots WHERE account_id = ?) AS position_rows
        FROM nav_snapshots
        WHERE account_id = ? AND asset_class = 'TOTAL'
        """,
        (subject_id, subject_id),
    ).fetchone()
    evidence = dict(row) if row is not None else {}
    if not evidence.get("latest_nav_date"):
        raise RuntimeError(f"No imported NAV rows exist for {subject_id}")
    return evidence


def _process_non_request(connection, claim) -> dict:
    if claim.work_kind not in {"source_partition", "account_import"}:
        raise RuntimeError("This ingestion worker cannot certify downstream analytics completion")
    if not claim.subject_id:
        raise RuntimeError("Downstream Flex work has no subject")
    evidence = _subject_evidence(connection, claim.subject_id)
    if claim.work_kind == "account_import":
        # A subject can have more than one onboarding case (an existing_v11 case and an
        # ibkr_flex one, for example), so this joins rather than expecting a single case.
        connection.execute(
            """
            UPDATE account_onboarding_checks AS checks
            SET status = 'pass',
                message = 'The IBKR data import and row-count reconciliation passed.',
                evidence_json = ?::jsonb, checked_at = now()
            FROM account_onboarding_cases AS cases
            WHERE checks.tenant_id = cases.tenant_id
              AND checks.case_id = cases.case_id
              AND checks.check_id = 'data_import_reconciled'
              AND cases.target_subject_id = ?
              AND cases.status <> 'cancelled'
            """,
            (canonical_json(evidence), claim.subject_id),
        )
    return evidence


def main() -> int:
    if os.environ.get("DEPLOYMENT_ENVIRONMENT", "").lower() != "internal-production":
        raise RuntimeError("Institutional Flex worker is restricted to internal production")
    database_url = _required("DATABASE_URL")
    tenant_id = os.environ.get("PORTFOLIO_TENANT_ID", DEFAULT_TENANT_ID)
    worker_id = f"institutional-flex-{uuid.uuid4()}"
    connection = connect(Path("/tmp/unused.sqlite3"), database_url, tenant_id)
    completed: list[dict] = []
    try:
        dead_letter_exhausted_and_blocked_work(connection, tenant_id=tenant_id)
        connection.commit()
        while True:
            claim = claim_next_work(
                connection,
                tenant_id=tenant_id,
                worker_id=worker_id,
                lease_seconds=1800,
                allowed_kinds=(
                    "flex_statement_request", "source_partition",
                    "account_import",
                ),
            )
            connection.commit()
            if claim is None:
                break
            try:
                if claim.work_kind == "flex_statement_request":
                    evidence = _request_statement(
                        connection, claim, database_url=database_url, tenant_id=tenant_id
                    )
                else:
                    evidence = _process_non_request(connection, claim)
                _update_payload(connection, claim.work_item_id, evidence)
                complete_work(
                    connection,
                    tenant_id=tenant_id,
                    work_item_id=claim.work_item_id,
                    worker_id=worker_id,
                )
                connection.commit()
                completed.append({"work_item_id": claim.work_item_id, "kind": claim.work_kind})
            except (FlexError, ValidationBlocked) as error:
                connection.rollback()
                lockout = isinstance(error, FlexError) and (
                    error.lockout or is_lockout_error(error.code, str(error))
                )
                # A preflight block sent nothing; never weaken the circuit it found.
                if claim.flex_source_id and isinstance(error, FlexError):
                    source = _work_source(connection, str(claim.flex_source_id))
                    connection.execute(
                        """
                        UPDATE ibkr_credential_circuit_state
                        SET last_error_at = now(), last_error_phase = ?, last_error_code = ?,
                            consecutive_failures = consecutive_failures + 1,
                            blocked_until = CASE WHEN ? THEN NULL ELSE now() + interval '30 minutes' END,
                            blocked_reason = CASE WHEN ? THEN 'lockout-or-credentials' ELSE 'transient-failure' END,
                            requires_manual_reset = ?, updated_at = now()
                        WHERE credential_id = ?
                        """,
                        (
                            getattr(error, "phase", "preflight"),
                            getattr(error, "code", None) or type(error).__name__,
                            lockout, lockout, lockout, source["credential_id"],
                        ),
                    )
                fail_work(
                    connection,
                    tenant_id=tenant_id,
                    claim=claim,
                    worker_id=worker_id,
                    error_code=getattr(error, "code", None) or type(error).__name__,
                    error_message=str(error),
                )
                connection.commit()
                raise
            except Exception as error:
                connection.rollback()
                fail_work(
                    connection,
                    tenant_id=tenant_id,
                    claim=claim,
                    worker_id=worker_id,
                    error_code=type(error).__name__,
                    error_message=str(error),
                )
                connection.commit()
                raise
        print(canonical_json({"status": "success", "completed": completed}))
        return 0
    finally:
        connection.close()


if __name__ == "__main__":
    raise SystemExit(lineage.run_main("ibkrv2stg-institutional-daily", main))
