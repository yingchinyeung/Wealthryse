"""The onboarding worker job (ibkrv2stg-onboard-worker): see ibkr_etl.onboarding_worker.

    python scripts/run_onboarding_worker.py

Runs every 20 minutes and when the website starts it (Run now, Check now). Each step is the
function the daily and month-end jobs already call: the Kiwoom collector for the open cases only,
the reconciliation agent's results, the sector job's Yahoo lookup, the designed-report render and
the agent review's check. Prints one JSON summary with no keys, figures or full account numbers.

Kiwoom keys are read from Key Vault for this run only and never logged (as run_kiwoom_collector.py
reads them). Kiwoom pages go to bronze first and silver is computed from bronze's copy
(KIWOOM_BRONZE, KIWOOM_BRONZE_REQUIRED, KIWOOM_SILVER_FROM_BRONZE, all true on the job).
Exits 1 when a step failed, so the job's failed-execution alert shows it; the failure is also on
the case page.
"""

from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = PROJECT_ROOT / "src"
SCRIPTS = PROJECT_ROOT / "scripts"
for path in (SOURCE_ROOT, SCRIPTS):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from ibkr_etl import lineage  # noqa: E402
from ibkr_etl.db import DEFAULT_TENANT_ID, connect  # noqa: E402
from ibkr_etl.onboarding_worker import JOB_NAME, Case, Steps, run_worker  # noqa: E402

LOCK_KEY = "onboarding-worker"


def mask(value) -> str:
    return re.sub(r"(?<![A-Za-z0-9])\d{5,}(?![A-Za-z0-9])", lambda m: "*" * (len(m.group()) - 4) + m.group()[-4:],
                  str(value))


def _required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def _database():
    tenant_id = os.environ.get("PORTFOLIO_TENANT_ID", DEFAULT_TENANT_ID)
    url = _required("DATABASE_URL")
    return (lambda: connect(Path("/tmp/unused.sqlite3"), url, tenant_id)), tenant_id


def kiwoom_step(open_connection, tenant_id: str):
    """The daily collector's own run, limited to the given cases and connections."""

    from ibkr_etl.kiwoom_bronze import KiwoomBronze, enabled as bronze_enabled, required as bronze_required
    from ibkr_etl.kiwoom_bronze import silver_from_bronze
    from ibkr_etl.kiwoom_client import KiwoomClient
    from ibkr_etl.kiwoom_collector import Target, collector_enabled, run, snapshot_day
    from run_kiwoom_collector import read_secret, rebuild_check

    def step(case_ids: set[str], connection_ids: set[str], history: bool) -> list[dict]:
        if not collector_enabled():
            raise RuntimeError("KIWOOM_COLLECTOR_ENABLED is off on the onboarding worker")
        if os.environ.get("DEPLOYMENT_ENVIRONMENT", "").lower() != "internal-production":
            raise RuntimeError("The Kiwoom collector is restricted to internal production")
        if not (bronze_enabled() and bronze_required() and silver_from_bronze()):
            raise RuntimeError("The onboarding worker writes Kiwoom silver only from bronze; set KIWOOM_BRONZE, "
                               "KIWOOM_BRONZE_REQUIRED and KIWOOM_SILVER_FROM_BRONZE to true")
        vault_uri, client_id = _required("FLEX_TOKEN_VAULT_URI"), _required("AZURE_CLIENT_ID")
        include_us = os.environ.get("KIWOOM_INCLUDE_US", "true").strip().lower() != "false"

        def client_for(target: Target) -> KiwoomClient:
            keys = read_secret(vault_uri, target.secret_name, client_id)
            return KiwoomClient(environment=target.environment, app_key=keys["app_key"],
                                app_secret=keys["app_secret"])

        now = datetime.now(timezone.utc)
        results: list[dict] = []
        bronze = KiwoomBronze.from_environment(open_connection, tenant_id)
        connection = open_connection()
        try:
            if case_ids:
                results += run(connection, tenant_id, client_for=client_for, today=snapshot_day(now), now=now,
                               include_us=include_us, bronze=bronze, case_ids=case_ids, history=history)
            # A connection check: every account of the connection that no case above already loaded.
            if connection_ids:
                loaded = {str(item.get("case_id")) for item in results}
                checked = run(connection, tenant_id, client_for=client_for, today=snapshot_day(now), now=now,
                              include_us=include_us, bronze=bronze, connection_ids=connection_ids,
                              case_ids=None, history=history)
                results += [item for item in checked if str(item.get("case_id")) not in loaded
                            or item.get("case_id") is None]
        finally:
            connection.close()
            if bronze is not None:
                bronze.close()
        record = lineage.current()
        for table in ("position_snapshots", "cash_snapshots", "nav_snapshots", "fx_rates", "trades",
                      "cash_transactions"):
            rows = sum(int((item.get("rows") or {}).get(table, 0)) for item in results)
            if rows:
                record.output(f"silver.{table}", rows=rows, source="kiwoom_rest", via="bronze.kiwoom_responses")
        if bronze is not None and bronze.stats["written"]:
            record.input("bronze.kiwoom_responses", rows=bronze.stats["written"], source="kiwoom_rest")
        if bronze is not None and bronze.required:
            print(mask(json.dumps({"kiwoom_rebuild": rebuild_check(_required("DATABASE_URL"), tenant_id, results)},
                                  default=str)), flush=True)
        return results

    return step


def classify_step(open_connection):
    from ibkr_etl.composite_reports import HONG_KONG
    from ibkr_etl.sector_classification import refresh_sector_classifications_range

    def step(case: Case) -> dict:
        connection = open_connection()
        try:
            summary = refresh_sector_classifications_range(
                connection, subject_id=str(case.subject_id), start_date=case.history_start_date,
                end_date=datetime.now(HONG_KONG).date().isoformat())
            connection.commit()
        finally:
            connection.close()
        return {key: summary.get(key) for key in ("classified", "unclassified", "previously_classified")}

    return step


def render_step(open_connection, tenant_id: str):
    """The designed-report render the month-end job does, for the given cases only."""

    from azure.core.exceptions import ResourceExistsError

    from ibkr_etl.cloud_storage import download_blob_bytes, upload_bytes
    from ibkr_etl.composite_reports import HONG_KONG
    from ibkr_etl.designed_reports import run_designed_report_cases

    storage_url = _required("AZURE_STORAGE_ACCOUNT_URL")
    container = os.environ.get("INTERNAL_REPORT_CONTAINER", "internal-reports")

    def fetch(bucket: str, blob: str) -> bytes:
        return download_blob_bytes(storage_url, bucket, blob, maximum_bytes=64 * 1024 * 1024)

    def store(bucket: str, blob: str, content: bytes, content_type: str) -> None:
        # Content-addressed and immutable: an existing blob must already hold these bytes.
        try:
            stored = upload_bytes(storage_url, bucket, blob, content, overwrite=False, content_type=content_type)
        except ResourceExistsError:
            stored = fetch(bucket, blob) == content
        if not stored:
            raise RuntimeError("Private report storage refused the report")

    def step(case_ids: set[str]) -> list[dict]:
        connection = open_connection()
        try:
            return run_designed_report_cases(connection, tenant_id, container=container,
                                             today=datetime.now(HONG_KONG).date(), fetch=fetch, store=store,
                                             case_ids=case_ids)
        finally:
            connection.close()

    return step


def design_step(open_connection, tenant_id: str):
    """The built-in factsheet design the owner picked on the case page, submitted as the designer would."""

    from ibkr_etl.builtin_design_submission import submit_built_in_design

    def step(case_id: str, design_id: str) -> dict:
        connection = open_connection()
        try:
            result = submit_built_in_design(connection, tenant_id, case_id, design_id=design_id)
            connection.commit()
            return {key: result.get(key) for key in ("status", "design_id", "checks")}
        finally:
            connection.close()

    return step


def _with_connection(open_connection, work):
    def step() -> list[dict]:
        connection = open_connection()
        try:
            return work(connection)
        finally:
            connection.close()
    return step


def main() -> int:
    from ibkr_etl.onboarding_agent_reconcile import record_agent_reconciliations
    from ibkr_etl.onboarding_agent_review import record_agent_reviews

    open_connection, tenant_id = _database()
    connection = open_connection()
    try:
        locked = connection.execute("SELECT pg_try_advisory_lock(hashtext(?)) AS locked",
                                    (f"{LOCK_KEY}:{tenant_id}",)).fetchone()["locked"]
        if not locked:
            # Another worker run is going; it, or the next one, takes any new request.
            print(json.dumps({"status": "skipped", "reason": "another worker run is in progress"}), flush=True)
            return 0
        steps = Steps(
            kiwoom=kiwoom_step(open_connection, tenant_id),
            design=design_step(open_connection, tenant_id),
            reconcile=_with_connection(open_connection, lambda c: record_agent_reconciliations(c, tenant_id)),
            classify=classify_step(open_connection),
            render=render_step(open_connection, tenant_id),
            review=_with_connection(open_connection, lambda c: record_agent_reviews(c, tenant_id)),
        )
        summary = run_worker(connection, tenant_id, now=datetime.now(timezone.utc), steps=steps)
    finally:
        connection.close()
    print(mask(json.dumps(summary, default=str, sort_keys=True)), flush=True)
    failed = any(item.get("outcome") == "failed" for item in summary.get("steps", []))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(lineage.run_main(JOB_NAME, main))
