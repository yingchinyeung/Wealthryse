from __future__ import annotations

import json
import os


EXPECTED_COUNTS = {
    "accounts": 1,
    "trades": 205,
    "cash_transactions": 639,
    "corporate_actions": 3,
    "position_snapshots": 17788,
    "cash_snapshots": 433,
    "nav_snapshots": 435,
    "fx_rates": 19929,
    "fx_transactions": 0,
}

EXPECTED_COVERAGE = {
    "nav_snapshots": ("snapshot_date", "2025-01-20", "2026-09-18"),
    "cash_snapshots": ("snapshot_date", "2025-01-20", "2026-09-18"),
    "position_snapshots": ("snapshot_date", "2025-01-21", "2026-09-18"),
    "trades": ("trade_date", "2025-01-21", "2026-09-08"),
    "cash_transactions": ("effective_date", "2025-02-05", "2026-09-17"),
    "corporate_actions": ("effective_date", "2025-10-30", "2026-06-12"),
    "fx_rates": ("rate_date", "2025-01-20", "2026-09-18"),
}

PLATFORM_TABLES = (
    "benchmark_master",
    "benchmark_prices",
    "reference_fx_rates",
    "risk_free_rates",
    "portfolio_benchmark_mapping",
    "performance_comparison_daily",
    "metric_definitions",
    "data_quality_results",
    "reporting_snapshots",
    "report_generation_runs",
    "app_users",
    "user_account_access",
    "agent_tool_audit",
    "approved_actions",
    "audit_events",
    "crisis_replay_components",
    "crisis_replay_daily",
    "instrument_classifications",
)


def main() -> int:
    database_url = os.environ.get("DATABASE_URL", "")
    tenant_id = os.environ.get(
        "PORTFOLIO_TENANT_ID", "00000000-0000-0000-0000-000000000001"
    )
    account_id = os.environ.get("PILOT_ACCOUNT_ID", "U16088571")
    if not database_url.startswith(("postgres://", "postgresql://")):
        raise RuntimeError("DATABASE_URL must be a PostgreSQL URL")

    import psycopg
    from psycopg.rows import dict_row

    checks: dict[str, object] = {}
    failures: list[str] = []
    with psycopg.connect(database_url, row_factory=dict_row) as connection:
        connection.execute(
            "SELECT set_config('app.tenant_id', %s, false)", (tenant_id,)
        )
        migrations = [
            row["version"]
            for row in connection.execute(
                "SELECT version FROM schema_migrations ORDER BY version"
            )
        ]
        checks["migrations"] = migrations
        if migrations != [
            "001_init.sql",
            "002_platform_reporting.sql",
            "003_pilot_benchmark_contract.sql",
            "004_provisional_benchmark_feed.sql",
            "005_sp500_total_return_primary.sql",
            "006_price_index_benchmarks.sql",
            "007_crisis_holdings_replay.sql",
            "008_controlled_admin_actions.sql",
            "009_reporting_snapshot_payload.sql",
            "010_provisional_sector_classifications.sql",
        ]:
            failures.append(f"Unexpected migrations: {migrations}")

        benchmark_rows = connection.execute(
            """
            SELECT b.benchmark_id, b.symbol, b.name, b.provider,
                   b.return_convention, b.currency, b.license_status,
                   m.subject_id, m.mapping_type, m.effective_from,
                   m.reporting_currency
            FROM benchmark_master b
            JOIN portfolio_benchmark_mapping m
              ON m.tenant_id = b.tenant_id AND m.benchmark_id = b.benchmark_id
            WHERE b.benchmark_id IN ('GSPC', 'IXIC')
            ORDER BY b.benchmark_id
            """
        ).fetchall()
        benchmark_contracts = {
            row["benchmark_id"]: dict(row) for row in benchmark_rows
        }
        checks["pilot_benchmark_contracts"] = benchmark_contracts
        expected_contracts = {
            "GSPC": ("^GSPC", "primary"),
            "IXIC": ("^IXIC", "secondary"),
        }
        for benchmark_id, (symbol, mapping_type) in expected_contracts.items():
            benchmark = benchmark_contracts.get(benchmark_id)
            expected = (
                account_id,
                symbol,
                "Yahoo Finance via yfinance",
                "price",
                "USD",
                mapping_type,
                "2025-01-20",
                "USD",
            )
            actual = None if benchmark is None else (
                benchmark["subject_id"],
                benchmark["symbol"],
                benchmark["provider"],
                benchmark["return_convention"],
                benchmark["currency"],
                benchmark["mapping_type"],
                benchmark["effective_from"],
                benchmark["reporting_currency"],
            )
            if actual != expected:
                failures.append(f"Pilot {benchmark_id} benchmark contract is missing or incorrect")
            if benchmark is not None and benchmark["license_status"] != "restricted":
                failures.append(
                    f"Pilot {benchmark_id} benchmark must remain restricted until provider approval"
                )

        retired_rows = connection.execute(
            """
            SELECT benchmark_id, active,
                   (SELECT COUNT(*) FROM portfolio_benchmark_mapping m
                    WHERE m.benchmark_id = b.benchmark_id
                      AND m.subject_id = %s) AS mappings
            FROM benchmark_master b
            WHERE benchmark_id IN ('XNDX', 'SP500TR')
            ORDER BY benchmark_id
            """,
            (account_id,),
        ).fetchall()
        retired = {row["benchmark_id"]: dict(row) for row in retired_rows}
        checks["retired_benchmark_contracts"] = retired
        for benchmark_id in ("XNDX", "SP500TR"):
            row = retired.get(benchmark_id)
            if row is None or row["active"] or row["mappings"] != 0:
                failures.append(f"The former {benchmark_id} benchmark was not retired cleanly")

        proxy = connection.execute(
            """
            SELECT benchmark_id, COUNT(*) AS observations, MIN(observation_date) AS first,
                   MAX(observation_date) AS latest,
                   MIN(quality_status) AS minimum_quality,
                   MAX(quality_status) AS maximum_quality
            FROM benchmark_prices
            WHERE benchmark_id IN ('GSPC', 'IXIC')
              AND observation_status = 'provisional_close'
              AND provider = 'Yahoo Finance via yfinance'
            GROUP BY benchmark_id
            """
        ).fetchall()
        proxies = {row["benchmark_id"]: dict(row) for row in proxy}
        checks["provisional_benchmarks"] = proxies
        for benchmark_id in ("GSPC", "IXIC"):
            row = proxies.get(benchmark_id, {})
            if (
                int(row.get("observations") or 0) < 300
                or row.get("first") != "2025-01-21"
                or row.get("latest") is None
                or row["latest"] < "2026-09-18"
                or row.get("minimum_quality") != "warn"
                or row.get("maximum_quality") != "warn"
            ):
                failures.append(
                    f"Provisional {benchmark_id} backfill is incomplete: {row}"
                )

        comparison = connection.execute(
            """
            SELECT benchmark_id, COUNT(*) AS observations, MIN(observation_date) AS first,
                   MAX(observation_date) AS latest,
                   MIN(quality_status) AS minimum_quality,
                   MAX(quality_status) AS maximum_quality
            FROM performance_comparison_daily
            WHERE subject_id = %s AND benchmark_id IN ('GSPC', 'IXIC')
              AND calculation_version = 'benchmark-comparison-v1'
            GROUP BY benchmark_id
            """,
            (account_id,),
        ).fetchall()
        comparisons = {row["benchmark_id"]: dict(row) for row in comparison}
        checks["provisional_comparisons"] = comparisons
        for benchmark_id in ("GSPC", "IXIC"):
            row = comparisons.get(benchmark_id, {})
            if (
                int(row.get("observations") or 0) < 300
                or row.get("first") != "2025-01-21"
                or row.get("latest") != "2026-09-18"
                or row.get("minimum_quality") != "warn"
                or row.get("maximum_quality") != "warn"
            ):
                failures.append(
                    f"Provisional {benchmark_id} comparison is incomplete: {row}"
                )

        accounts = [
            row["account_id"]
            for row in connection.execute("SELECT account_id FROM accounts ORDER BY account_id")
        ]
        checks["accounts"] = accounts
        if accounts != [account_id]:
            failures.append(f"Unexpected account set: {accounts}")

        row_counts: dict[str, int] = {}
        for table, expected in EXPECTED_COUNTS.items():
            if table == "accounts":
                count = len(accounts)
            else:
                count = int(
                    connection.execute(
                        f'SELECT COUNT(*) AS n FROM "{table}" WHERE account_id = %s',
                        (account_id,),
                    ).fetchone()["n"]
                )
            row_counts[table] = count
            if count != expected:
                failures.append(f"{table} count {count} != {expected}")
        checks["row_counts"] = row_counts

        coverage: dict[str, dict[str, object]] = {}
        for table, (date_column, expected_first, expected_last) in EXPECTED_COVERAGE.items():
            row = connection.execute(
                f'SELECT MIN("{date_column}") AS first, MAX("{date_column}") AS last FROM "{table}" WHERE account_id = %s',
                (account_id,),
            ).fetchone()
            coverage[table] = {"first": row["first"], "last": row["last"]}
            if (row["first"], row["last"]) != (expected_first, expected_last):
                failures.append(
                    f"{table} coverage {(row['first'], row['last'])} != {(expected_first, expected_last)}"
                )
        checks["coverage"] = coverage

        missing_tables = [
            table
            for table in PLATFORM_TABLES
            if connection.execute(
                "SELECT to_regclass(%s) AS table_name", (f"public.{table}",)
            ).fetchone()["table_name"]
            is None
        ]
        checks["missing_platform_tables"] = missing_tables
        if missing_tables:
            failures.append(f"Missing platform tables: {missing_tables}")

        rls_rows = connection.execute(
            """
            SELECT relname, relrowsecurity, relforcerowsecurity
            FROM pg_class
            WHERE relname = ANY(%s)
            ORDER BY relname
            """,
            (list(table for table in PLATFORM_TABLES if table != "metric_definitions"),),
        ).fetchall()
        rls_failures = [
            row["relname"]
            for row in rls_rows
            if not row["relrowsecurity"] or not row["relforcerowsecurity"]
        ]
        checks["rls_table_count"] = len(rls_rows)
        checks["rls_failures"] = rls_failures
        if rls_failures or len(rls_rows) != len(PLATFORM_TABLES) - 1:
            failures.append("Platform row-level-security verification failed")

        latest_run = connection.execute(
            """
            SELECT run_id, status, row_count, reconciliation_json
            FROM ingestion_runs
            WHERE run_type = 'staging-snapshot-import'
            ORDER BY run_id DESC LIMIT 1
            """
        ).fetchone()
        checks["latest_import"] = {
            "run_id": latest_run["run_id"] if latest_run else None,
            "status": latest_run["status"] if latest_run else None,
            "row_count": latest_run["row_count"] if latest_run else None,
        }
        if latest_run is None or latest_run["status"] != "success":
            failures.append("Latest staging snapshot import is not successful")

    result = {
        "status": "FAIL" if failures else "PASS",
        "account_id": account_id,
        "tenant_id": tenant_id,
        "checks": checks,
        "failures": failures,
    }
    print(json.dumps(result, sort_keys=True))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
