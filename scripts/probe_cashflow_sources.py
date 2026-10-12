"""Fixed-date read-only flow-policy verification; no external network calls."""
import json
import os
import sys
from collections import Counter
from decimal import Decimal
from datetime import date
from pathlib import Path
sys.path.insert(0, str(Path.cwd() / "src"))
import psycopg
from ibkr_etl.db import PostgresConnection, _hybrid_row_factory
from ibkr_etl.comparison_source_evidence import load_source_projection, verify_sources
from ibkr_etl.cashflow_source_evidence import load_cashflow_sources, verify_cashflow_returns

DIAGNOSTIC_CONTEXT = {}
SAFE_ERRORS = {"Invalid numerical evidence", "Ambiguous account FX", "Transfer scope mismatch",
    "Duplicate transfer identity", "FX scope mismatch", "Derived ledger scope mismatch",
    "Duplicate ledger event", "Invalid ledger flags", "Unsupported subject flow policy",
    "Unreviewed cash-flow currency conversion", "Unique comparison history required",
    "Comparison scope mismatch", "Comparison date scope mismatch", "Subject contract missing"}


def main():
    tenant = os.environ["PORTFOLIO_TENANT_ID"]
    with psycopg.connect(os.environ["DATABASE_URL"], connect_timeout=20,
            options="-c default_transaction_read_only=on -c statement_timeout=120000 -c lock_timeout=5000",
            row_factory=_hybrid_row_factory) as raw:
        raw.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        raw.execute("SELECT set_config('app.tenant_id',%s,true)", (tenant,))
        connection = PostgresConnection(raw)
        targets = raw.execute("""SELECT m.subject_id, m.benchmark_id FROM platform_subjects s
            JOIN portfolio_benchmark_mapping m ON m.tenant_id=s.tenant_id AND m.subject_id=s.subject_id
            WHERE s.tenant_id=%s AND s.lifecycle_status='active' AND m.effective_from<='2026-09-30'
            AND (m.effective_to IS NULL OR m.effective_to>='2026-09-30') ORDER BY m.subject_id, m.benchmark_id""", (tenant,)).fetchall()
        results = []
        summarized = set()
        for target in targets:
            DIAGNOSTIC_CONTEXT.update(subject_id=target["subject_id"], benchmark_id=target["benchmark_id"], stage="comparison_sources")
            data = load_source_projection(connection, tenant_id=tenant, subject_id=target["subject_id"],
                benchmark_id=target["benchmark_id"], as_of="2026-09-30")
            base = verify_sources(data, expected_date=date(2026, 9, 30))
            DIAGNOSTIC_CONTEXT["stage"] = "cashflow_load"
            cash = load_cashflow_sources(connection, tenant_id=tenant, subject_id=target["subject_id"], as_of="2026-09-30")
            if target["subject_id"] not in summarized:
                invalid_fx = Counter()
                for fx in cash["fx"]:
                    try:
                        value = Decimal(str(fx["rate_text"]))
                        reason = "nonfinite" if not value.is_finite() else "nonpositive" if value <= 0 else None
                    except ArithmeticError:
                        reason = "unparseable"
                    if reason:
                        invalid_fx[(fx["from_currency"], fx["to_currency"], reason)] += 1
                candidates = None
                if cash["contract"]["subject_type"] == "physical_account":
                    candidates = raw.execute("""SELECT COUNT(*) AS n FROM cash_transactions c
                        JOIN accounts a ON a.account_id=c.account_id
                        WHERE a.tenant_id=%s AND c.account_id=%s AND c.effective_date BETWEEN %s AND %s
                        AND (COALESCE(c.transaction_type,'') || ' ' || COALESCE(c.description,''))
                          ~* '(deposit|withdraw|transfer|contribution|redemption)'""",
                        (tenant, target["subject_id"], cash["contract"]["start"], cash["contract"]["as_of"])).fetchone()["n"]
                print("CASHFLOW_SOURCE " + json.dumps({"source_inventory": True, "subject_id": target["subject_id"],
                    "transfer_rows": len(cash["transfers"]), "ledger_rows": len(cash["ledger"]),
                    "cash_activity_external_flow_candidates": candidates,
                    "candidate_scan_is_not_classification": True,
                    "invalid_fx_groups": [{"from_currency": a, "to_currency": b, "reason": c, "count": n}
                        for (a, b, c), n in sorted(invalid_fx.items())]}), flush=True)
                summarized.add(target["subject_id"])
            DIAGNOSTIC_CONTEXT["stage"] = "cashflow_verify"
            result = verify_cashflow_returns(cash, data["comparisons"])
            result["source_projection_verified"] = base["source_projection_verified"]
            result["comparison_source_sha256"] = base["source_sha256"]
            result["comparison_sha256"] = base["comparison_sha256"]
            results.append(result)
            print("CASHFLOW_SOURCE " + json.dumps(result, sort_keys=True, allow_nan=False), flush=True)
        print("CASHFLOW_SOURCE " + json.dumps({"probe_complete": True, "mappings": len(results),
            "policies_verified": sum(r["implemented_flow_policy_verified"] and r["source_projection_verified"] for r in results),
            "database_read_only": raw.execute("SHOW transaction_read_only").fetchone()[0] == "on",
            "execution_authorized": False}), flush=True)
        raw.rollback()


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print("CASHFLOW_SOURCE " + json.dumps({"probe_complete": False,
            "error_type": type(error).__name__, "reason": str(error) if str(error) in SAFE_ERRORS else "redacted",
            **DIAGNOSTIC_CONTEXT, "execution_authorized": False}), flush=True)
        raise SystemExit(1)
