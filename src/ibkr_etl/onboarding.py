from __future__ import annotations

import json
import re
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any


ACCOUNT_ID_PATTERN = re.compile(r"^U[0-9]{6,12}$")
ONBOARDING_NAMESPACE = uuid.UUID("89196669-5594-45b7-a575-b2ee889ad15e")
FEATURE_KEYS = (
    "data_ingestion",
    "website_analytics",
    "ai_analyst",
    "factsheet",
    "presentation",
    "viewer_access",
)


@dataclass(frozen=True)
class AccountSyncTarget:
    account_id: str
    start_date: str


def _iso_date(value: object, *, field: str) -> str:
    candidate = str(value or "").strip()
    try:
        parsed = date.fromisoformat(candidate)
    except ValueError as error:
        raise RuntimeError(f"{field} must be an ISO date") from error
    if parsed > date.today():
        raise RuntimeError(f"{field} cannot be in the future")
    return parsed.isoformat()


def parse_account_sync_targets(
    payload: str,
    *,
    fallback_account_id: str,
    fallback_start_date: str,
) -> tuple[AccountSyncTarget, ...]:
    """Parse a bounded, duplicate-free account target list for one shared export."""
    if not payload.strip():
        raw: object = [
            {
                "account_id": fallback_account_id,
                "start_date": fallback_start_date,
            }
        ]
    else:
        try:
            raw = json.loads(payload)
        except json.JSONDecodeError as error:
            raise RuntimeError("ACCOUNT_SYNC_TARGETS_JSON is not valid JSON") from error
    if not isinstance(raw, list) or not raw or len(raw) > 25:
        raise RuntimeError("Account sync targets must contain between 1 and 25 items")

    targets: list[AccountSyncTarget] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, dict) or set(item) != {"account_id", "start_date"}:
            raise RuntimeError(
                "Each account sync target must contain only account_id and start_date"
            )
        account_id = str(item["account_id"]).strip().upper()
        if not ACCOUNT_ID_PATTERN.fullmatch(account_id):
            raise RuntimeError("Account sync target contains an invalid IBKR account ID")
        if account_id in seen:
            raise RuntimeError(f"Duplicate account sync target: {account_id}")
        seen.add(account_id)
        targets.append(
            AccountSyncTarget(
                account_id=account_id,
                start_date=_iso_date(item["start_date"], field="start_date"),
            )
        )
    return tuple(targets)


def onboarding_case_id(
    *, tenant_id: str, source_system: str, source_account_id: str
) -> str:
    return str(
        uuid.uuid5(
            ONBOARDING_NAMESPACE,
            f"{tenant_id}:{source_system}:{source_account_id}",
        )
    )


def discover_v11_accounts(snapshot_path: Path) -> list[dict[str, Any]]:
    """Read account-level coverage only from a validated v1.1 SQLite export."""
    path = snapshot_path.resolve()
    if not path.is_file():
        raise FileNotFoundError(path)
    connection = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise RuntimeError("v1.1 source snapshot integrity check failed")
        account_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(accounts)")
        }
        if not {"account_id", "account_type", "base_currency", "source_json"}.issubset(
            account_columns
        ):
            raise RuntimeError("v1.1 source snapshot account schema is incomplete")

        records: list[dict[str, Any]] = []
        for account in connection.execute(
            "SELECT account_id, account_type, base_currency, source_json "
            "FROM accounts ORDER BY account_id"
        ):
            account_id = str(account["account_id"] or "").strip().upper()
            if not ACCOUNT_ID_PATTERN.fullmatch(account_id):
                raise RuntimeError("v1.1 source snapshot contains an invalid account ID")
            source_json: dict[str, Any] = {}
            try:
                decoded = json.loads(account["source_json"] or "{}")
                if isinstance(decoded, dict):
                    source_json = decoded
            except json.JSONDecodeError:
                pass
            nav = connection.execute(
                "SELECT MIN(snapshot_date), MAX(snapshot_date), "
                "COUNT(DISTINCT snapshot_date) FROM nav_snapshots "
                "WHERE account_id = ? AND asset_class = 'TOTAL'",
                (account_id,),
            ).fetchone()
            row_counts = {
                "nav_days": int(nav[2] or 0),
                "position_rows": int(
                    connection.execute(
                        "SELECT COUNT(*) FROM position_snapshots WHERE account_id = ?",
                        (account_id,),
                    ).fetchone()[0]
                ),
                "trade_rows": int(
                    connection.execute(
                        "SELECT COUNT(*) FROM trades WHERE account_id = ?",
                        (account_id,),
                    ).fetchone()[0]
                ),
                "cash_rows": int(
                    connection.execute(
                        "SELECT COUNT(*) FROM cash_transactions WHERE account_id = ?",
                        (account_id,),
                    ).fetchone()[0]
                ),
            }
            account_type = str(account["account_type"] or "")
            if "master" in account_type.lower():
                eligibility_status = "not_portfolio"
                eligibility_reason = (
                    "Institution master account; retain for a separate administrative-account review."
                )
            elif row_counts["nav_days"] and row_counts["position_rows"]:
                eligibility_status = "eligible"
                eligibility_reason = "Physical client account has NAV and position history."
            else:
                eligibility_status = "review_required"
                eligibility_reason = "Account lacks complete NAV or position coverage."
            records.append(
                {
                    "account_id": account_id,
                    "account_type": account_type,
                    "base_currency": str(account["base_currency"] or ""),
                    "account_name": str(source_json.get("name") or ""),
                    "nav_start_date": nav[0],
                    "nav_end_date": nav[1],
                    "row_counts": row_counts,
                    "eligibility_status": eligibility_status,
                    "eligibility_reason": eligibility_reason,
                }
            )
        return records
    finally:
        connection.close()


def register_source_discoveries(
    connection,
    *,
    tenant_id: str,
    discoveries: list[dict[str, Any]],
    source_export_sha256: str,
) -> None:
    for item in discoveries:
        connection.execute(
            """
            INSERT INTO onboarding_source_accounts(
                tenant_id, source_system, source_account_id, account_type,
                base_currency, account_name, nav_start_date, nav_end_date,
                row_counts_json, source_export_sha256, eligibility_status,
                eligibility_reason, first_discovered_at, last_discovered_at
            ) VALUES (
                %s, 'existing_v11', %s, %s, %s, %s, %s, %s,
                %s::jsonb, %s, %s, %s, now(), now()
            )
            ON CONFLICT (tenant_id, source_system, source_account_id) DO UPDATE SET
                account_type = EXCLUDED.account_type,
                base_currency = EXCLUDED.base_currency,
                account_name = EXCLUDED.account_name,
                nav_start_date = EXCLUDED.nav_start_date,
                nav_end_date = EXCLUDED.nav_end_date,
                row_counts_json = EXCLUDED.row_counts_json,
                source_export_sha256 = EXCLUDED.source_export_sha256,
                eligibility_status = EXCLUDED.eligibility_status,
                eligibility_reason = EXCLUDED.eligibility_reason,
                last_discovered_at = now()
            """,
            (
                tenant_id,
                item["account_id"],
                item["account_type"],
                item["base_currency"],
                item["account_name"],
                item["nav_start_date"],
                item["nav_end_date"],
                json.dumps(item["row_counts"], sort_keys=True),
                source_export_sha256,
                item["eligibility_status"],
                item["eligibility_reason"],
            ),
        )


def ensure_physical_subject(
    connection,
    *,
    tenant_id: str,
    discovery: dict[str, Any],
    lifecycle_status: str,
    history_start_date: str,
) -> None:
    account_id = discovery["account_id"]
    connection.execute(
        """
        INSERT INTO platform_subjects(
            tenant_id, subject_id, subject_type, display_name, source_account_id,
            account_type, base_currency, reporting_currency, history_start_date,
            lifecycle_status, metadata_json, created_at, updated_at
        ) VALUES (
            %s, %s, 'physical_account', %s, %s, %s, %s, %s, %s,
            %s, %s::jsonb, now(), now()
        )
        ON CONFLICT (tenant_id, subject_id) DO UPDATE SET
            source_account_id = EXCLUDED.source_account_id,
            account_type = EXCLUDED.account_type,
            base_currency = EXCLUDED.base_currency,
            updated_at = now()
        """,
        (
            tenant_id,
            account_id,
            account_id,
            account_id,
            discovery["account_type"],
            discovery["base_currency"],
            discovery["base_currency"],
            history_start_date,
            lifecycle_status,
            json.dumps({"source_system": "existing_v11"}, sort_keys=True),
        ),
    )


def ensure_feature_states(
    connection,
    *,
    tenant_id: str,
    subject_id: str,
    active: bool,
) -> None:
    for feature_key in FEATURE_KEYS:
        if active:
            state = "active"
            reason = "Existing approved U16088571 production capability."
        elif feature_key == "data_ingestion":
            state = "preview"
            reason = "Wave 0 historical and daily data reconciliation is in progress."
        else:
            state = "disabled"
            reason = "Owner activation is blocked until the onboarding gates pass."
        connection.execute(
            """
            INSERT INTO subject_feature_states(
                tenant_id, subject_id, feature_key, feature_state, reason, updated_at
            ) VALUES (%s, %s, %s, %s, %s, now())
            ON CONFLICT (tenant_id, subject_id, feature_key) DO NOTHING
            """,
            (tenant_id, subject_id, feature_key, state, reason),
        )


def ensure_wave0_case(
    connection,
    *,
    tenant_id: str,
    discovery: dict[str, Any],
    start_date: str,
) -> str:
    account_id = discovery["account_id"]
    case_id = onboarding_case_id(
        tenant_id=tenant_id,
        source_system="existing_v11",
        source_account_id=account_id,
    )
    connection.execute(
        """
        INSERT INTO account_onboarding_cases(
            case_id, tenant_id, target_subject_id, source_system,
            source_account_id, stage, status, requested_history_start,
            reporting_currency, configuration_json, created_at, updated_at
        ) VALUES (
            %s, %s, %s, 'existing_v11', %s, 'data_import', 'in_progress',
            %s, %s, %s::jsonb, now(), now()
        )
        ON CONFLICT (tenant_id, source_system, source_account_id) DO UPDATE SET
            updated_at = now()
        """,
        (
            case_id,
            tenant_id,
            account_id,
            account_id,
            start_date,
            discovery["base_currency"],
            json.dumps(
                {
                    "wave": 0,
                    "reporting_contract_status": "owner_confirmation_required",
                },
                sort_keys=True,
            ),
        ),
    )
    checks = {
        "source_account_discovered": (
            "pass",
            "The account is present in the integrity-checked v1.1 source export.",
            {"coverage": [discovery["nav_start_date"], discovery["nav_end_date"]]},
        ),
        "source_history_available": (
            "pass",
            "The source contains NAV and position history for Wave 0 validation.",
            discovery["row_counts"],
        ),
        "data_import_reconciled": (
            "pending",
            "Waiting for the account-only v2 import and row-count reconciliation.",
            {},
        ),
        "financial_contract_approved": (
            "pending",
            "Owner confirmation of the account reporting contract is required.",
            {},
        ),
        "website_preview_validated": (
            "pending",
            "The account remains hidden until its private website preview passes.",
            {},
        ),
        "owner_activation": (
            "pending",
            "Explicit owner activation is required after all other gates pass.",
            {},
        ),
    }
    for check_id, (status, message, evidence) in checks.items():
        connection.execute(
            """
            INSERT INTO account_onboarding_checks(
                tenant_id, case_id, check_id, status, message,
                evidence_json, checked_at
            ) VALUES (%s, %s, %s, %s, %s, %s::jsonb, now())
            ON CONFLICT (tenant_id, case_id, check_id) DO NOTHING
            """,
            (
                tenant_id,
                case_id,
                check_id,
                status,
                message,
                json.dumps(evidence, sort_keys=True),
            ),
        )
    return case_id


def mark_import_reconciled(
    connection,
    *,
    tenant_id: str,
    account_id: str,
    reconciliation: dict[str, Any],
) -> None:
    case_id = onboarding_case_id(
        tenant_id=tenant_id,
        source_system="existing_v11",
        source_account_id=account_id,
    )
    connection.execute(
        """
        UPDATE account_onboarding_checks
        SET status = 'pass',
            message = 'The account-only v2 import passed row-count and coverage reconciliation.',
            evidence_json = %s::jsonb,
            checked_at = now()
        WHERE tenant_id = %s AND case_id = %s
          AND check_id = 'data_import_reconciled'
        """,
        (json.dumps(reconciliation, sort_keys=True, default=str), tenant_id, case_id),
    )
    connection.execute(
        """
        UPDATE account_onboarding_cases
        SET stage = 'financial_contract', status = 'in_progress', updated_at = now()
        WHERE tenant_id = %s AND case_id = %s AND stage = 'data_import'
        """,
        (tenant_id, case_id),
    )
