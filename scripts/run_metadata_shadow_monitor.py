"""Restricted, shadow-only monitor. Bundle injects the three reviewed modules.

This is a system-operator metadata identity, not an end-user tenant identity.
It has no remediation tools, Flex access, report access or financial values.
"""
import hashlib
import json
import os
import sys
import types

import psycopg
from psycopg import sql
from psycopg.rows import dict_row


def module(name, source):
    result = types.ModuleType(name)
    sys.modules[name] = result
    exec(compile(source, name, "exec"), result.__dict__)
    return result


def verify_access(connection, access):
    identity = connection.execute("""SELECT current_user AS name, rolsuper, rolcreatedb,
        rolcreaterole, rolreplication, rolbypassrls FROM pg_roles WHERE rolname=current_user""").fetchone()
    if identity["name"] != "ibkr_metadata_monitor" or any(v for k, v in identity.items() if k != "name"):
        raise RuntimeError("Unexpected monitor database authority")
    if connection.execute("SELECT 1 FROM pg_auth_members WHERE member=(SELECT oid FROM pg_roles WHERE rolname=current_user)").fetchone():
        raise RuntimeError("Monitor role membership is not allowed")
    for table, column in access.DENIED_COLUMNS:
        try:
            with connection.transaction():
                connection.execute(sql.SQL("SELECT {} FROM public.{} LIMIT 0").format(
                    sql.Identifier(column), sql.Identifier(table)))
        except psycopg.errors.InsufficientPrivilege:
            continue
        raise RuntimeError("Monitor can access a prohibited column")
    if connection.execute("""SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname='public' AND c.relkind IN ('r','p','v','m') AND
        (has_table_privilege(current_user,c.oid,'INSERT') OR has_table_privilege(current_user,c.oid,'UPDATE')
         OR has_table_privilege(current_user,c.oid,'DELETE') OR has_table_privilege(current_user,c.oid,'TRUNCATE')) LIMIT 1""").fetchone():
        raise RuntimeError("Monitor has application write privileges")
    if connection.execute("""SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
        WHERE n.nspname='public' AND p.prosecdef AND has_function_privilege(current_user,p.oid,'EXECUTE') LIMIT 1""").fetchone():
        raise RuntimeError("Unreviewed SECURITY DEFINER access")


def main():
    collector = module("shadow_collector", SOURCE_COLLECTOR)
    access = module("shadow_access", SOURCE_ACCESS)
    policy = module("shadow_policy", SOURCE_POLICY)
    calendar = module("shadow_calendar", SOURCE_CALENDAR)
    with psycopg.connect(os.environ["DATABASE_URL"], connect_timeout=20,
                        options="-c default_transaction_read_only=on -c statement_timeout=60000 -c lock_timeout=5000",
                        row_factory=dict_row) as connection:
        connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        connection.execute("SELECT set_config('app.tenant_id', %s, true)",
                           (os.environ["PORTFOLIO_TENANT_ID"],))
        verify_access(connection, access)
        evidence = json.loads(json.dumps(collector.collect(connection), default=str))
    assessment = policy.assess(evidence)
    calendar_result = calendar.check_benchmark_dates(evidence)
    summary = policy.monitor_summary(assessment, calendar_result)
    # Bounded per-row records avoid large Log Analytics line truncation. No raw
    # database exception text, secret URLs, queue payloads or financial values.
    records = [("risk", row) for row in assessment["risk_date_coverage"]]
    records += [("finding", row) for row in assessment["findings"]]
    records += [("benchmark", row) for row in calendar_result["checks"]]
    for index, (kind, value) in enumerate(records):
        print("METADATA_MONITOR " + json.dumps({"kind":kind, "index":index,
            "captured_at":assessment["captured_at"], "value":value}, sort_keys=True), flush=True)
    print("METADATA_MONITOR " + json.dumps({"kind":"complete", "mode":"shadow",
        "captured_at":assessment["captured_at"], "records":len(records),
        "evidence_sha256":hashlib.sha256(json.dumps(evidence, sort_keys=True).encode()).hexdigest(),
        **summary, "inventory_complete":assessment["inventory_complete"],
        "denied_column_checks":len(access.DENIED_COLUMNS), "access_check":"passed",
        "calendar_version":calendar.CALENDAR_VERSION,
        "limits":["Not a financial reconciliation", "No remediation or incident paging",
                  "NAV, FX and risk-free absolute freshness unverified", "Only registered exchange-calendar coverage"]}, sort_keys=True), flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print("METADATA_MONITOR " + json.dumps({"kind":"error", "mode":"shadow",
            "error_type":type(error).__name__, "complete":False}), flush=True)
        sys.exit(1)
