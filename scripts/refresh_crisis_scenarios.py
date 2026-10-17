"""Daily crisis-scenarios refresh (2008, 2015-16, 2020 and 2022) for every active subject whose owner approved
sending its ticker symbols to Yahoo Finance. Runs as its own scheduled job after the daily cycle, so a Yahoo
problem here never fails the benchmark, derived or risk jobs.

Exits 1 when any subject fails or nothing is stored, so the failure shows on the job's execution.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
for folder in (PROJECT_ROOT / "src", PROJECT_ROOT / "scripts"):
    if str(folder) not in sys.path:
        sys.path.insert(0, str(folder))

from ibkr_etl import crisis_scenarios, raw_capture  # noqa: E402
from ibkr_etl.db import connect  # noqa: E402
from ibkr_etl.util import canonical_json  # noqa: E402
from refresh_provisional_benchmark import _crisis_scenario_subjects  # noqa: E402


def run(connection, *, ticker_factory=None) -> tuple[int, dict]:
    if not crisis_scenarios.website_enabled():
        return 0, {"status": "disabled", "switch": crisis_scenarios.SWITCH_NAME}
    accounts, composites = _crisis_scenario_subjects(connection)
    summary = crisis_scenarios.refresh(connection, accounts=accounts, composites=composites,
                                       ticker_factory=ticker_factory)
    ok = bool(summary["subjects"]) and not summary["failures"]
    return (0 if ok else 1), {"status": "success" if ok else "failed",
                              "requested": {"accounts_and_portfolios": len(accounts), "composites": len(composites)},
                              **summary}


def main() -> int:
    if os.environ.get("DEPLOYMENT_ENVIRONMENT", "").lower() not in {"staging", "internal-production"}:
        raise RuntimeError("The crisis-scenarios job may run only in staging or internal production")
    database_url = os.environ.get("DATABASE_URL", "")
    if not database_url.startswith(("postgres://", "postgresql://")):
        raise RuntimeError("DATABASE_URL must be a PostgreSQL connection URL")
    tenant_id = os.environ.get("PORTFOLIO_TENANT_ID", "00000000-0000-0000-0000-000000000001")
    connection = connect(Path("/tmp/unused.sqlite3"), database_url, tenant_id)
    try:
        # No ingestion_runs row: other jobs read the latest run there, and this job is not an ingestion.
        raw_capture.set_job_run_id(raw_capture.new_run_id("crisis-scenarios"))
        code, result = run(connection)
        print(canonical_json(result), flush=True)
        return code
    finally:
        connection.close()


if __name__ == "__main__":
    raise SystemExit(main())
