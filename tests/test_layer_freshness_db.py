"""The layer freshness queries against a real PostgreSQL with every migration applied.

Needs a disposable server: set AGENT_TEST_PG_ADMIN_DSN to a superuser DSN (CI starts
one). Skipped otherwise. Creates and drops its own database and roles and writes
synthetic rows only. Shows which checks the approval login (the autonomous
administrator's DATABASE_URL) can read today, the grants that make the rest readable,
and how the administrator records checks it cannot read.
"""
from __future__ import annotations

import importlib.util
import os
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from ibkr_etl import layer_freshness

ADMIN_DSN = os.environ.get("AGENT_TEST_PG_ADMIN_DSN", "")
ROOT = Path(__file__).resolve().parents[1]
TENANT = "00000000-0000-0000-0000-000000000001"
OWNER = "freshness_test_owner"
APPROVAL = "freshness_test_approval"
TEST_PASSWORD = "freshness-test-only-password"  # throwaway roles in a disposable test server
NOW = datetime(2026, 10, 16, 10, 30, tzinfo=timezone.utc)
TOLERATED_MIGRATIONS = {"027_legacy_external_market_data_approval.sql", "056_specialist_audit_ledger.sql"}

# The read grants scripts/bootstrap_postgres.py gives the approval role today, limited
# to the tables the freshness rules read.
TODAYS_APPROVAL_READS = (
    "GRANT SELECT ON TABLE accounts, nav_snapshots, benchmark_prices, risk_free_rates, position_snapshots, "
    "market_data_prices, performance_comparison_daily, ingestion_runs TO {role}"
)
# What the switch-on needs on top, so every check is readable. flex_rows gets only the
# columns the check uses, never the raw records.
FRESHNESS_GRANTS = (
    "GRANT SELECT (tenant_id, received_at) ON raw_reports TO {role}",
    "GRANT SELECT (tenant_id, flex_row_id, first_seen_run_id) ON flex_rows TO {role}",
    "GRANT SELECT (tenant_id, as_of_date) ON risk_analytics_snapshots TO {role}",
    "GRANT SELECT (tenant_id, holdings_as_of) ON crisis_scenario_results, crisis_replay_daily TO {role}",
)
UNREADABLE_TODAY = {"bronze.raw_reports", "bronze.flex_rows", "gold.risk_analytics_snapshots",
                    "gold.crisis_scenario_results", "gold.crisis_replay_daily"}


def _supervisor():
    spec = importlib.util.spec_from_file_location(
        "autonomous_supervisor", ROOT / "scripts" / "run_autonomous_administrator.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _dsn(database: str, user: str | None = None) -> str:
    from psycopg.conninfo import conninfo_to_dict, make_conninfo

    params = conninfo_to_dict(ADMIN_DSN)
    params["dbname"] = database
    params["options"] = f"-c app.tenant_id={TENANT}"
    if user:
        params["user"] = user
        params["password"] = TEST_PASSWORD
    return make_conninfo(**params)


def _seed(connection) -> None:
    """One healthy day of synthetic data, 16 Oct 2026."""
    run = connection.execute(
        "INSERT INTO ingestion_runs(run_type, started_at, completed_at, status) "
        "VALUES ('synthetic-flex', '2026-10-16T08:01:00+00:00', '2026-10-16T08:03:00+00:00', 'success') "
        "RETURNING run_id").fetchone()[0]
    connection.execute(
        "INSERT INTO raw_reports(run_id, received_at, content_sha256, byte_count, file_path) "
        "VALUES (%s, '2026-10-16T08:02:00+00:00', %s, 10, 'synthetic.xml')", (run, "a" * 64))
    connection.execute(
        "INSERT INTO flex_rows(account_id, section, record_type, source_key, row_sha256, data_json, "
        "first_seen_run_id, last_seen_run_id) VALUES ('U0000001', 'NAV', 'r', 'k', %s, '{}', %s, %s)",
        ("b" * 64, run, run))
    connection.execute(
        "INSERT INTO accounts(account_id, first_seen_at, last_seen_at, source_json) "
        "VALUES ('U0000001', '2026-01-01', '2026-10-16', '{}')")
    connection.execute(
        "INSERT INTO nav_snapshots(account_id, snapshot_date, currency, row_sha256, last_seen_run_id) "
        "VALUES ('U0000001', '2026-10-15', 'USD', %s, %s)", ("c" * 64, run))
    connection.execute(
        "INSERT INTO position_snapshots(account_id, snapshot_date, conid, row_sha256, last_seen_run_id) "
        "VALUES ('U0000001', '2026-10-15', '1', %s, %s)", ("d" * 64, run))
    connection.execute(
        "INSERT INTO benchmark_master(benchmark_id, symbol, name, currency, return_convention, "
        "created_at, updated_at) VALUES ('SYN', '^SYN', 'Synthetic', 'USD', 'price', '2026-01-01', '2026-01-01')")
    connection.execute(
        "INSERT INTO benchmark_prices(benchmark_id, observation_date, level, currency, observation_status, "
        "provider, fetched_at, quality_status) VALUES "
        "('SYN', '2026-10-15', 100, 'USD', 'provisional_close', 'p', '2026-10-16T08:31:00+00:00', 'warn'), "
        "('SYN', '2026-10-14', 99, 'USD', 'provisional_close', 'p', '2026-10-17T08:31:00+00:00', 'fail')")
    connection.execute(
        "INSERT INTO market_data_prices(instrument_id, price_date, symbol, instrument_name, price_type, "
        "currency, price, source, source_url, fetched_at) VALUES "
        "('syn', '2026-10-15', 'SYN', 'Synthetic', 'unadjusted_close', 'USD', 1, 's', 'https://x', "
        "'2026-10-16T08:51:00+00:00')")
    connection.execute(
        "INSERT INTO risk_free_rates(rate_date, series_id, currency, tenor, annual_rate, "
        "day_count_convention, provider, fetched_at, quality_status) VALUES "
        "('2026-10-15', 'UST-PAR-3M', 'USD', '3-month', 0.04, 'x', 'p', '2026-10-15T23:04:00+00:00', 'pass')")
    connection.execute(
        "INSERT INTO risk_analytics_snapshots(subject_id, as_of_date, analytics_version, comparison_version, "
        "source_data_sha256, payload_sha256, payload_json, quality_status, generated_at, duration_ms, "
        "timings_json) VALUES ('U0000001', '2026-10-15', 'v', 'v', %s, %s, '{}', 'pass', "
        "'2026-10-16T08:56:00+00:00', 1, '{}')", ("e" * 64, "f" * 64))
    connection.execute(
        "INSERT INTO performance_comparison_daily(subject_id, benchmark_id, observation_date, "
        "reporting_currency, portfolio_value, benchmark_level, calculation_version, quality_status, "
        "generated_at) VALUES ('U0000001', 'SYN', '2026-10-15', 'USD', 1, 1, 'v', 'pass', "
        "'2026-10-16T08:31:00+00:00')")
    connection.execute(
        "INSERT INTO crisis_scenario_results(subject_id, holdings_as_of, scenario_id, calculation_version, "
        "reporting_currency, crisis_start, crisis_end, available, result_json, generated_at) VALUES "
        "('U0000001', '2026-10-15', 'gfc', 'v', 'USD', '2008-09-15', '2009-03-09', true, '{}', "
        "'2026-10-16T09:21:00+00:00')")
    connection.execute(
        "INSERT INTO crisis_replay_daily(subject_id, holdings_as_of, observation_date, normalized_value, "
        "drawdown, crisis_start, crisis_end, calculation_version, quality_status, generated_at) VALUES "
        "('U0000001', '2026-10-15', '2008-09-15', 1, 0, '2008-09-15', '2009-03-09', 'v', 'warn', "
        "'2026-10-16T08:32:00+00:00')")


@unittest.skipUnless(ADMIN_DSN, "AGENT_TEST_PG_ADMIN_DSN is not set")
class LayerFreshnessDatabaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import psycopg

        cls.database = "freshness_test_" + uuid.uuid4().hex[:8]
        with psycopg.connect(ADMIN_DSN, autocommit=True) as admin:
            admin.execute(f"DROP ROLE IF EXISTS {APPROVAL}")
            if admin.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (OWNER,)).fetchone() is None:
                # Like Azure: the owner is not a superuser, so forced row-level security applies.
                admin.execute(f"CREATE ROLE {OWNER} LOGIN PASSWORD '{TEST_PASSWORD}'")
            admin.execute(f"CREATE ROLE {APPROVAL} LOGIN PASSWORD '{TEST_PASSWORD}'")
            admin.execute(f"ALTER ROLE {APPROVAL} SET default_transaction_read_only = off")
            admin.execute(f'CREATE DATABASE "{cls.database}" OWNER {OWNER}')
        with psycopg.connect(_dsn(cls.database), autocommit=True) as admin:
            admin.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")
        with psycopg.connect(_dsn(cls.database, OWNER)) as owner:
            for path in sorted((ROOT / "migrations" / "postgres").glob("*.sql")):
                if "_medallion_" in path.name:
                    # The approval login as it is before the layer moves, which bootstrap makes with
                    # their grants (test_medallion_layers_db and the S3 runs cover the moves).
                    continue
                try:
                    owner.execute(path.read_text(encoding="utf-8"))
                    owner.commit()
                except Exception:
                    owner.rollback()
                    if path.name not in TOLERATED_MIGRATIONS:
                        raise
                if owner.execute("SELECT to_regclass('public.tenants')").fetchone()[0] and \
                        owner.execute("SELECT 1 FROM tenants").fetchone() is None:
                    owner.execute("INSERT INTO tenants(tenant_id, name, status, created_at) "
                                  "VALUES (%s, 'Test', 'active', '2026-01-01')", (TENANT,))
                    owner.commit()
            _seed(owner)
            owner.execute(f"GRANT USAGE ON SCHEMA public TO {APPROVAL}")
            owner.execute(f"GRANT EXECUTE ON FUNCTION app_tenant_id() TO {APPROVAL}")
            owner.execute(TODAYS_APPROVAL_READS.format(role=APPROVAL))
            owner.commit()

    @classmethod
    def tearDownClass(cls):
        import psycopg

        with psycopg.connect(ADMIN_DSN, autocommit=True) as admin:
            admin.execute(f'DROP DATABASE IF EXISTS "{cls.database}" WITH (FORCE)')
            admin.execute(f"DROP ROLE IF EXISTS {APPROVAL}")

    def _results(self, user: str) -> dict:
        results = layer_freshness.run_read_only(_dsn(self.database, user), TENANT, now=NOW, environ={})
        return {r["check_id"]: r for r in results}

    def test_every_query_runs_and_judges_the_synthetic_day_fresh(self):
        # A login with the reads (here the owner) sees every check, through the same
        # read-only session the administrator opens.
        results = self._results(OWNER)
        # A rule on a held migration's table waits for it: that table is not in this database.
        self.assertEqual(len(results), len([r for r in layer_freshness.RULES if r.query and not r.held]))
        self.assertEqual({k: r["state"] for k, r in results.items() if r["state"] != "fresh"}, {})
        self.assertEqual(results["bronze.raw_reports"]["observed"], "2026-10-16T08:02:00+00:00")
        self.assertEqual(results["bronze.flex_rows"]["observed"], "2026-10-16T08:01:00+00:00")
        # A failed benchmark row never counts as fresh data.
        self.assertEqual(results["silver.benchmark_prices"]["observed"], "2026-10-16T08:31:00+00:00")
        self.assertEqual(results["gold.crisis_scenario_results"]["observed"], "2026-10-15")

    def test_the_session_refuses_writes(self):
        import psycopg

        session = layer_freshness.open_read_only(_dsn(self.database, OWNER), TENANT)
        try:
            settings = session.execute(
                "SELECT current_setting('transaction_read_only') AS read_only, "
                "current_setting('statement_timeout') AS statement, current_setting('lock_timeout') AS lock"
            ).fetchone()
            self.assertEqual((settings["read_only"], settings["statement"], settings["lock"]), ("on", "5s", "2s"))
            for statement in ("DELETE FROM raw_reports",
                              "UPDATE nav_snapshots SET currency = 'HKD'",
                              "INSERT INTO system_incidents(incident_key, check_id, severity, status, description, "
                              "evidence_json, first_seen_at, last_seen_at) VALUES "
                              "('x', 'x', 'info', 'open', 'x', '{}', 'x', 'x')"):
                with self.subTest(statement=statement.split()[0]):
                    with self.assertRaises(psycopg.errors.ReadOnlySqlTransaction):
                        with session.transaction():
                            session.execute(statement)
            # Nor can the session switch itself to read-write.
            with self.assertRaises(psycopg.errors.ActiveSqlTransaction):
                with session.transaction():
                    session.execute("SET TRANSACTION READ WRITE")
        finally:
            session.rollback()
            session.close()
        with psycopg.connect(_dsn(self.database, OWNER)) as owner:
            self.assertEqual(owner.execute("SELECT count(*) FROM raw_reports").fetchone()[0], 1)

    def test_approval_login_today_and_with_the_switch_on_grants(self):
        import psycopg

        today = self._results(APPROVAL)
        unreadable = {k for k, r in today.items() if r["state"] == "unavailable"}
        self.assertEqual(unreadable, UNREADABLE_TODAY)
        self.assertTrue(all("InsufficientPrivilege" in today[k]["error"] for k in unreadable))
        self.assertFalse(any(r["late"] for r in today.values()))
        with psycopg.connect(_dsn(self.database, OWNER)) as owner:
            for grant in FRESHNESS_GRANTS:
                owner.execute(grant.format(role=APPROVAL))
            owner.commit()
        try:
            granted = self._results(APPROVAL)
            self.assertEqual({k: r["state"] for k, r in granted.items() if r["state"] != "fresh"}, {})
            with psycopg.connect(_dsn(self.database, APPROVAL)) as approval:
                with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                    approval.execute("SELECT data_json FROM flex_rows")
        finally:
            with psycopg.connect(_dsn(self.database, OWNER)) as owner:
                owner.execute(f"REVOKE SELECT ON raw_reports, flex_rows, risk_analytics_snapshots, "
                              f"crisis_scenario_results, crisis_replay_daily FROM {APPROVAL}")
                for table, columns in (("raw_reports", "tenant_id, received_at"),
                                       ("flex_rows", "tenant_id, flex_row_id, first_seen_run_id"),
                                       ("risk_analytics_snapshots", "tenant_id, as_of_date"),
                                       ("crisis_scenario_results", "tenant_id, holdings_as_of"),
                                       ("crisis_replay_daily", "tenant_id, holdings_as_of")):
                    owner.execute(f"REVOKE SELECT ({columns}) ON {table} FROM {APPROVAL}")
                owner.commit()

    def test_administrator_holds_unreadable_checks_open_and_warns_after_an_hour(self):
        import psycopg
        from psycopg.rows import dict_row

        supervisor = _supervisor()
        late_key = "layer_freshness:gold.crisis_scenario_results"
        with psycopg.connect(_dsn(self.database, OWNER), row_factory=dict_row) as admin:
            admin.execute("DELETE FROM system_incidents")
            # An earlier run found the crisis results late.
            admin.execute(
                "INSERT INTO system_incidents(incident_key, check_id, severity, status, description, "
                "evidence_json, first_seen_at, last_seen_at) VALUES (%s, 'layer_freshness', 'warning', 'open', "
                "'Gold layer is late', '{}', '2026-10-16T10:00:00+00:00', '2026-10-16T10:00:00+00:00')",
                (late_key,))
            admin.commit()

            def run(now, database_url, extra=None):
                with patch.dict("os.environ", {"LAYER_FRESHNESS_ENABLED": "true", **(extra or {})}), \
                        patch("builtins.print"):
                    review = supervisor._layer_freshness_review(admin, database_url, TENANT, now=now)
                ids, announce = supervisor._synchronize(admin, list(review.findings), now.isoformat(), review)
                admin.commit()
                rows = admin.execute("SELECT incident_key, severity, status FROM system_incidents").fetchall()
                return announce, {row["incident_key"]: (row["severity"], row["status"]) for row in rows}

            trackers = {"layer_freshness:unavailable:" + check for check in UNREADABLE_TODAY}
            # The administrator's own login (the approval role) cannot read five checks.
            approval_url = _dsn(self.database, APPROVAL)
            announce, incidents = run(NOW, approval_url)
            self.assertEqual(incidents[late_key], ("warning", "open"))  # unreadable, so not closed
            self.assertEqual({k for k, (_, status) in incidents.items() if status == "open"} - {late_key}, trackers)
            self.assertEqual({incidents[k][0] for k in trackers}, {"info"})
            self.assertEqual(announce, set())  # info is recorded, not announced

            announce, incidents = run(NOW + timedelta(minutes=30), approval_url)
            self.assertEqual({incidents[k][0] for k in trackers}, {"info"})
            self.assertEqual(announce, set())

            announce, incidents = run(NOW + timedelta(minutes=61), approval_url)
            self.assertEqual({incidents[k] for k in trackers}, {("warning", "open")})
            self.assertEqual(announce, trackers)  # announced once, when they become warnings
            self.assertEqual(incidents[late_key], ("warning", "open"))

            # A login that can read everything: every check is fresh, so all close.
            announce, incidents = run(NOW + timedelta(minutes=70), approval_url,
                                      {"LAYER_FRESHNESS_DATABASE_URL": _dsn(self.database, OWNER)})
            self.assertEqual({status for _, status in incidents.values()}, {"resolved"})
            self.assertEqual(announce, set())


if __name__ == "__main__":
    unittest.main()
