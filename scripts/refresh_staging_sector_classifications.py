from __future__ import annotations

import json
import os
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = PROJECT_ROOT / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from ibkr_etl import raw_capture  # noqa: E402
from ibkr_etl.db import connect  # noqa: E402
from ibkr_etl.sector_classification import (  # noqa: E402
    refresh_sector_classifications_range,
)
from ibkr_etl.util import canonical_json  # noqa: E402


def _approved(connection, account_id: str) -> bool:
    row = connection.execute(
        """
        SELECT 1
        FROM account_onboarding_approvals
        WHERE subject_id = ? AND approval_type = 'external_market_data'
          AND approval_status = 'approved' AND revoked_at IS NULL
          AND disclosure_version = 'yahoo-ticker-disclosure-v1'
        LIMIT 1
        """,
        (account_id,),
    ).fetchone()
    return row is not None


def _mark_risk_analytics_gate(
    connection, account_id: str, classification: dict
) -> None:
    """Pass the onboarding gate only when every deterministic input is ready."""

    readiness = connection.execute(
        """
        WITH latest AS (
            SELECT MAX(snapshot_date) AS snapshot_date
            FROM subject_position_series WHERE subject_id = ?
        )
        SELECT
          (SELECT COUNT(*) FROM performance_comparison_daily
           WHERE subject_id = ? AND quality_status IN ('pass', 'warn')) AS comparison_rows,
          (SELECT COUNT(*) FROM crisis_replay_daily c, latest l
           WHERE c.subject_id = ? AND c.holdings_as_of = l.snapshot_date) AS crisis_rows,
          (SELECT COUNT(*) FROM subject_position_series p
           WHERE p.subject_id = ?
             AND UPPER(COALESCE(p.currency, '')) <> 'USD'
             AND NOT EXISTS (
               SELECT 1 FROM reference_fx_rates r
               WHERE r.from_currency = UPPER(COALESCE(p.currency, ''))
                 AND r.to_currency = 'USD'
                 AND r.quality_status IN ('pass', 'warn')
                 AND r.rate_date::date <= p.snapshot_date::date
                 AND r.rate_date::date >= p.snapshot_date::date - INTERVAL '4 days'
             )) AS missing_fx_rows
        """,
        (account_id, account_id, account_id, account_id),
    ).fetchone()
    evidence = {
        "historical_unique_holdings": classification["historical_unique_holdings"],
        "classified": classification["classified"],
        "previously_classified": classification["previously_classified"],
        "unclassified": classification["unclassified"],
        "comparison_rows": int(readiness["comparison_rows"] or 0),
        "crisis_rows": int(readiness["crisis_rows"] or 0),
        "missing_fx_rows": int(readiness["missing_fx_rows"] or 0),
    }
    passed = (
        evidence["unclassified"] == 0
        and evidence["comparison_rows"] >= 30
        and evidence["crisis_rows"] > 0
        and evidence["missing_fx_rows"] == 0
    )
    connection.execute(
        """
        UPDATE account_onboarding_checks
        SET status = ?, message = ?, evidence_json = ?::jsonb, checked_at = now()
        WHERE case_id = (
            SELECT case_id FROM account_onboarding_cases
            WHERE target_subject_id = ?
        ) AND check_id = 'risk_analytics_validated'
        """,
        (
            "pass" if passed else "warn",
            (
                "Sector coverage, USD FX conversion, tail-loss inputs and crisis replay passed validation."
                if passed
                else "Risk analytics remain private because one or more deterministic inputs are incomplete."
            ),
            canonical_json(evidence),
            account_id,
        ),
    )
    connection.commit()


def main() -> int:
    if os.environ.get("DEPLOYMENT_ENVIRONMENT", "").lower() not in {
        "staging",
        "internal-production",
    }:
        raise RuntimeError("The sector-classification job may run only in staging or internal production")
    database_url = os.environ.get("DATABASE_URL", "")
    if not database_url.startswith(("postgres://", "postgresql://")):
        raise RuntimeError("DATABASE_URL must be a PostgreSQL connection URL")
    tenant_id = os.environ.get(
        "PORTFOLIO_TENANT_ID", "00000000-0000-0000-0000-000000000001"
    )
    configured_ids = os.environ.get("SECTOR_CLASSIFICATION_ACCOUNT_IDS", "").strip()
    connection = connect(Path("/tmp/unused.sqlite3"), database_url, tenant_id)
    if configured_ids:
        account_ids = [
            value.strip().upper()
            for value in configured_ids.split(",")
            if value.strip()
        ]
    else:
        account_ids = [
            str(row["subject_id"])
            for row in connection.execute(
                """
                SELECT r.subject_id
                FROM subject_operating_registrations r
                JOIN platform_subjects s
                  ON s.tenant_id = r.tenant_id AND s.subject_id = r.subject_id
                WHERE r.capability = 'sector_refresh'
                  AND r.operating_state = 'active' AND r.effective_to IS NULL
                  AND s.lifecycle_status = 'active'
                  AND s.subject_type IN ('physical_account', 'filtered_portfolio')
                ORDER BY r.subject_id
                """
            ).fetchall()
        ]
    if len(account_ids) != len(set(account_ids)):
        raise RuntimeError(
            "SECTOR_CLASSIFICATION_ACCOUNT_IDS must contain unique account IDs"
        )
    workers = int(os.environ.get("SECTOR_CLASSIFICATION_WORKERS", "4"))
    raw_capture.set_job_run_id(raw_capture.new_run_id("sector-classification"))

    try:
        results: list[dict] = []
        for account_id in account_ids:
            if not _approved(connection, account_id):
                raise RuntimeError(
                    f"No active website-recorded Yahoo approval exists for {account_id}"
                )
            row = connection.execute(
                """
                SELECT MIN(snapshot_date) AS first_date, MAX(snapshot_date) AS latest
                FROM subject_position_series WHERE subject_id = ?
                """,
                (account_id,),
            ).fetchone()
            start_date = str(row["first_date"] or "") if row else ""
            end_date = os.environ.get("SECTOR_CLASSIFICATION_AS_OF", "").strip()
            end_date = end_date or (str(row["latest"] or "") if row else "")
            if not start_date or not end_date:
                raise RuntimeError(
                    f"No position snapshot is available for {account_id}"
                )
            classification = refresh_sector_classifications_range(
                connection,
                subject_id=account_id,
                start_date=start_date,
                end_date=end_date,
                workers=workers,
            )
            _mark_risk_analytics_gate(connection, account_id, classification)
            results.append(classification)
        payload = {
            "status": "success",
            "accounts": results,
            "account_ids": account_ids,
            "historical_unique_holdings": sum(
                int(item["historical_unique_holdings"]) for item in results
            ),
            "tickers_transmitted": sum(
                int(item["tickers_transmitted"]) for item in results
            ),
            "unclassified": sum(int(item["unclassified"]) for item in results),
        }
        print(json.dumps(payload, sort_keys=True))
        return 0 if payload["unclassified"] == 0 else 2
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


if __name__ == "__main__":
    raise SystemExit(main())
