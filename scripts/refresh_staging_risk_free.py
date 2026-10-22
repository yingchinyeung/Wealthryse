from __future__ import annotations

import json
import os
import sys
from datetime import date, datetime, timezone
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = PROJECT_ROOT / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from ibkr_etl.db import (  # noqa: E402
    connect,
    finish_run_failure,
    finish_run_success,
    start_run,
)
from ibkr_etl import lineage, raw_capture  # noqa: E402
from ibkr_etl.risk_free import refresh_treasury_3m_rates  # noqa: E402
from ibkr_etl.util import canonical_json, sha256_text  # noqa: E402


def main() -> int:
    if os.environ.get("DEPLOYMENT_ENVIRONMENT", "").lower() != "staging":
        raise RuntimeError("The staging risk-free job may run only in staging")
    database_url = os.environ.get("DATABASE_URL", "")
    if not database_url.startswith(("postgres://", "postgresql://")):
        raise RuntimeError("DATABASE_URL must be a PostgreSQL connection URL")
    tenant_id = os.environ.get(
        "PORTFOLIO_TENANT_ID", "00000000-0000-0000-0000-000000000001"
    )
    start = date.fromisoformat(os.environ.get("RISK_FREE_START_DATE", "2025-01-20"))
    connection = connect(Path("/tmp/unused.sqlite3"), database_url, tenant_id)
    run_id = start_run(connection, "staging-us-treasury-risk-free", start.isoformat(), None)
    raw_capture.set_job_run_id(run_id)
    lineage.current().facet(job_run_id=run_id)
    try:
        result = refresh_treasury_3m_rates(connection, start_date=start)
        if os.environ.get('HIBOR_LICENSE_SCOPE_CONFIRMED','').lower() == 'true':
            from ibkr_etl.hibor import refresh, latest_expected_fixing
            policy=latest_expected_fixing(now=datetime.now(timezone.utc))
            result['hkd_hibor']=refresh(connection,start,date.fromisoformat(policy['expected_date']),license_scope_confirmed=True)
            result['hkd_hibor']['publication_policy']=policy
        krw_error = None
        if os.environ.get("KOREA_CD_MONTHLY", "").lower() == "true":
            # Korea's won rate without a Bank of Korea key: the OECD monthly CD rate via FRED.
            from ibkr_etl.korea_cd_rate import refresh_monthly_cd_rates
            try:
                result["krw_cd_monthly"] = refresh_monthly_cd_rates(connection, start_date=start)
            except Exception as error:  # noqa: BLE001 - reported below and in the run record
                connection.rollback()
                krw_error = f"{type(error).__name__}: {' '.join(str(error).split())[:300]}"
                result["krw_cd_monthly"] = {"status": "failed", "error": krw_error}
        if os.environ.get("BOK_ECOS_API_KEY", "").strip():
            # Korea's won rate (Bank of Korea CD 91-day). The USD rows above are already committed,
            # so a Bank of Korea outage is reported and fails the run without undoing them.
            from ibkr_etl.korea_cd_rate import refresh_cd_rates
            try:
                result["krw_cd_91d"] = refresh_cd_rates(
                    connection, key=os.environ["BOK_ECOS_API_KEY"].strip(), start_date=start)
            except Exception as error:  # noqa: BLE001 - reported below and in the run record
                connection.rollback()
                krw_error = f"{type(error).__name__}: {' '.join(str(error).split())[:300]}"
                result["krw_cd_91d"] = {"status": "failed", "error": krw_error}
        payload = canonical_json(result)
        finish_run_success(
            connection,
            run_id,
            reference_code=str(result["series_id"]),
            raw_sha256=sha256_text(payload),
            raw_path=str(result["source_url"]),
            row_count=int(result["observations_loaded"]),
            reconciliation_json=payload,
        )
        loaded = int(result["observations_loaded"]) + sum(
            int((result.get(name) or {}).get("observations_loaded") or 0) for name in ("krw_cd_91d", "krw_cd_monthly"))
        lineage.current().output("silver.risk_free_rates", rows=loaded)
        print(json.dumps(result, sort_keys=True))
        return 1 if krw_error else 0
    except Exception as error:
        finish_run_failure(connection, run_id, str(error))
        raise
    finally:
        connection.close()


if __name__ == "__main__":
    raise SystemExit(lineage.run_main("ibkrv2stg-risk-free-daily", main))
