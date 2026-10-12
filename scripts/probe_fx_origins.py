"""One-off, database-enforced read-only FX lineage and currency exposure probe."""
import json
import os
import sys
from collections import Counter
from pathlib import Path
sys.path.insert(0, str(Path.cwd() / "src"))
import psycopg
from ibkr_etl.db import _hybrid_row_factory
from ibkr_etl.fx_source_evidence import trace_observation, rate_status
from ibkr_etl.fx_selection import latest_fx_quote, InvalidFxObservation


def main():
    tenant = os.environ["PORTFOLIO_TENANT_ID"]
    with psycopg.connect(os.environ["DATABASE_URL"], connect_timeout=20,
            options="-c default_transaction_read_only=on -c statement_timeout=120000 -c lock_timeout=5000",
            row_factory=_hybrid_row_factory) as raw:
        raw.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        raw.execute("SELECT set_config('app.tenant_id',%s,true)", (tenant,))
        subjects = raw.execute("""SELECT subject_id FROM platform_subjects WHERE tenant_id=%s
            AND lifecycle_status='active' AND subject_type='physical_account' ORDER BY subject_id""", (tenant,)).fetchall()
        totals = Counter()
        for subject in subjects:
            account = subject["subject_id"]
            rates = raw.execute("""SELECT f.* FROM fx_rates f JOIN accounts a ON a.account_id=f.account_id
                WHERE a.tenant_id=%s AND f.account_id=%s AND f.rate_date<='2026-09-30'
                ORDER BY f.rate_date,f.from_currency,f.to_currency""", (tenant, account)).fetchall()
            groups = {}
            history = {}
            invalid_run_ids = set()
            for rate in rates:
                history.setdefault((rate["from_currency"], rate["to_currency"]), []).append((rate["rate_date"], rate["rate_text"]))
                if rate_status(rate["rate_text"]) == "positive":
                    continue
                invalid_run_ids.add(rate["last_seen_run_id"])
                sources = raw.execute("""SELECT f.account_id,f.report_date,f.record_type,f.row_sha256,f.data_json,
                    f.last_seen_run_id,r.status AS ingestion_status,r.completed_at AS ingestion_completed_at
                    FROM flex_rows f LEFT JOIN ingestion_runs r ON r.tenant_id=f.tenant_id AND r.run_id=f.last_seen_run_id
                    WHERE f.tenant_id=%s AND f.account_id=%s AND f.report_date=%s AND f.row_sha256=%s""",
                    (tenant, account, rate["rate_date"], rate["row_sha256"])).fetchall()
                check = trace_observation(dict(rate), [dict(s) for s in sources])
                key = (rate["from_currency"], rate["to_currency"])
                group = groups.setdefault(key, {"observations": 0, "traced": 0, "issues": Counter(), "statuses": Counter(),
                    "missing_sources": 0, "ambiguous_sources": 0,
                    "first_date": rate["rate_date"], "last_date": rate["rate_date"]})
                group["observations"] += 1
                group["traced"] += check["trace_verified"]
                group["issues"].update(check["issues"])
                group["missing_sources"] += len(sources) == 0
                group["ambiguous_sources"] += len(sources) > 1
                group["statuses"].update([check["normalized_status"]])
                group["last_date"] = rate["rate_date"]
            for (native, target), group in groups.items():
                # Counts only, not amounts or descriptions. The entire stored
                # history through the cutoff is checked, not just invalid dates.
                exposures = {}
                for table in ("position_snapshots", "cash_snapshots", "cash_transactions", "trades"):
                    date_column = {"position_snapshots": "snapshot_date", "cash_snapshots": "snapshot_date",
                                   "cash_transactions": "effective_date", "trades": "trade_date"}[table]
                    exposures[table] = raw.execute("SELECT COUNT(*) AS n FROM " + table + " p JOIN accounts a ON a.account_id=p.account_id "
                        "WHERE a.tenant_id=%s AND p.account_id=%s AND p.currency=%s AND p." + date_column + "<=%s",
                        (tenant, account, native, "2026-09-30")).fetchone()["n"]
                print("FX_ORIGIN " + json.dumps({"account_id": account, "from_currency": native,
                    "to_currency": target, **group, "currency_row_counts": exposures,
                    "raw_xml_verified": False}, sort_keys=True), flush=True)
                totals["observations"] += group["observations"]
                totals["traced"] += group["traced"]
            lineage = raw.execute("""SELECT run_type,status,COUNT(*) AS runs FROM ingestion_runs
                WHERE tenant_id=%s AND run_id=ANY(%s) GROUP BY run_type,status ORDER BY run_type,status""",
                (tenant, sorted(invalid_run_ids))).fetchall()
            base = raw.execute("SELECT base_currency FROM accounts WHERE tenant_id=%s AND account_id=%s", (tenant, account)).fetchone()["base_currency"]
            demanded = set()
            for table, column in (("position_snapshots", "snapshot_date"), ("cash_snapshots", "snapshot_date"),
                                  ("cash_transactions", "effective_date"), ("trades", "trade_date")):
                demanded.update((r["day"], r["currency"]) for r in raw.execute("SELECT DISTINCT p." + column + " AS day,p.currency FROM " + table +
                    " p JOIN accounts a ON a.account_id=p.account_id WHERE a.tenant_id=%s AND p.account_id=%s AND p." + column + "<=%s",
                    (tenant, account, "2026-09-30")).fetchall())
            selection = Counter()
            missing_currencies = Counter()
            for day, currency in demanded:
                if currency == base:
                    selection["identity_conversion"] += 1
                    continue
                values = history.get((currency, base), [])
                valid = [(d,v) for d,v in values if rate_status(v)=="positive"]
                old = latest_fx_quote(valid, day)
                try:
                    new = latest_fx_quote(values, day)
                except InvalidFxObservation:
                    selection["blocked_invalid"] += 1
                    continue
                selection["matching_available" if new is not None else "matching_missing"] += 1
                if new is None:
                    missing_currencies[str(currency)] += 1
                if old != new:
                    selection["different_selection"] += 1
            print("FX_ORIGIN " + json.dumps({"account_id": account, "selection_rehearsal": True,
                "demanded_currency_dates": len(demanded), "selection_counts": selection,
                "missing_currency_counts": missing_currencies,
                "invalid_rate_import_runs": [dict(r) for r in lineage],
                "guard_deployed": False}, sort_keys=True), flush=True)
        print("FX_ORIGIN " + json.dumps({"probe_complete": True, **totals,
            "database_read_only": raw.execute("SHOW transaction_read_only").fetchone()[0] == "on",
            "execution_authorized": False}), flush=True)
        raw.rollback()


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print("FX_ORIGIN " + json.dumps({"probe_complete": False, "error_type": type(error).__name__}), flush=True)
        raise SystemExit(1)
