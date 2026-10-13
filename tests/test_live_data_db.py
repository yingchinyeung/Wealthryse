"""Migration 063 and the live collector's role against a real PostgreSQL.

Needs a disposable server: set AGENT_TEST_PG_ADMIN_DSN to a superuser DSN
(CI starts one). Skipped otherwise. Creates and drops its own database and
roles, and writes synthetic accounts only.
"""
from __future__ import annotations

import os
import unittest
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ADMIN_DSN = os.environ.get("AGENT_TEST_PG_ADMIN_DSN", "")
ROOT = Path(__file__).resolve().parents[1]
TENANT = "00000000-0000-0000-0000-000000000001"
OTHER_TENANT = "00000000-0000-0000-0000-000000000002"
COLLECTOR = "ibkr_live_collector_trial"
OWNER = "live_test_owner"
WEB = "live_test_web"
TEST_PASSWORD = "live-data-test-only-password"  # throwaway roles in a disposable test server
NOW = datetime(2026, 10, 13, 14, 35, tzinfo=timezone.utc)
TOLERATED_MIGRATIONS = {"027_legacy_external_market_data_approval.sql", "056_specialist_audit_ledger.sql"}


def _dsn(database: str, user: str | None = None, tenant: str = TENANT) -> str:
    from psycopg.conninfo import conninfo_to_dict, make_conninfo

    params = conninfo_to_dict(ADMIN_DSN)
    params["dbname"] = database
    params["options"] = f"-c app.tenant_id={tenant}"
    if user:
        params["user"] = user
        params["password"] = TEST_PASSWORD
    return make_conninfo(**params)


def _account(account_id: str, observed_at: datetime, net: float = 1000.0) -> dict:
    return {
        "account_id": account_id, "observed_at": observed_at, "base_currency": "HKD",
        "net_liquidation": net, "total_cash": 100.0, "daily_pnl": 5.0, "unrealized_pnl": 50.0,
        "realized_pnl": None, "cash_by_currency": {"HKD": 60.0, "USD": 5.0},
    }


def _position(account_id: str, observed_at: datetime, conid: str = "265598", **overrides) -> dict:
    row = {
        "account_id": account_id, "observed_at": observed_at, "conid": conid, "symbol": "AAPL",
        "sec_type": "STK", "currency": "USD", "primary_exchange": "NASDAQ", "quantity": 100.0,
        "average_cost": 150.25, "market_price": 190.5, "market_value": 19050.0,
        "unrealized_pnl": 4025.0, "daily_pnl": 250.0,
    }
    row.update(overrides)
    return row


@unittest.skipUnless(ADMIN_DSN, "AGENT_TEST_PG_ADMIN_DSN is not set")
class LiveDataDatabaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import psycopg

        from ibkr_etl import live_collector_access as access

        cls.database = "live_test_" + uuid.uuid4().hex[:8]
        with psycopg.connect(ADMIN_DSN, autocommit=True) as admin:
            for role in (COLLECTOR, WEB):
                admin.execute(f"DROP ROLE IF EXISTS {role}")
            if admin.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (OWNER,)).fetchone() is None:
                # Like Azure: the schema owner is not a superuser, so forced RLS applies to it.
                admin.execute(f"CREATE ROLE {OWNER} LOGIN PASSWORD '{TEST_PASSWORD}'")
            admin.execute(f"CREATE ROLE {WEB} LOGIN PASSWORD '{TEST_PASSWORD}'")
            admin.execute(f"ALTER ROLE {WEB} SET default_transaction_read_only = on")
            admin.execute(f'CREATE DATABASE "{cls.database}" OWNER {OWNER}')
        with psycopg.connect(_dsn(cls.database), autocommit=True) as admin:
            admin.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")
        with psycopg.connect(_dsn(cls.database, OWNER)) as owner:
            for path in sorted((ROOT / "migrations" / "postgres").glob("*.sql")):
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
            for account in ("ULIVE1", "ULIVE2"):
                owner.execute(
                    "INSERT INTO accounts(account_id, base_currency, first_seen_at, last_seen_at, source_json) "
                    "VALUES (%s, 'HKD', '2026-01-01', '2026-01-01', '{}')", (account,))
            owner.execute(f"GRANT USAGE ON SCHEMA public TO {WEB}")
            owner.execute(f"GRANT SELECT ON ALL TABLES IN SCHEMA public TO {WEB}")
            owner.execute(f"GRANT EXECUTE ON FUNCTION public.app_tenant_id() TO {WEB}")
            owner.commit()
        with psycopg.connect(_dsn(cls.database), autocommit=True) as admin:
            # A superuser bypasses the tenants table's row-level security.
            admin.execute("INSERT INTO tenants(tenant_id, name, status, created_at) "
                          "VALUES (%s, 'Other', 'active', '2026-01-01')", (OTHER_TENANT,))
            access.provision(admin, password=TEST_PASSWORD, database=cls.database, role=COLLECTOR)
            cls.report = access.privilege_report(admin, COLLECTOR)

    @classmethod
    def tearDownClass(cls):
        import psycopg

        with psycopg.connect(ADMIN_DSN, autocommit=True) as admin:
            admin.execute(f'DROP DATABASE IF EXISTS "{cls.database}" WITH (FORCE)')
            for role in (COLLECTOR, WEB):
                admin.execute(f"DROP ROLE IF EXISTS {role}")

    def setUp(self):
        import psycopg

        with psycopg.connect(_dsn(self.database), autocommit=True) as admin:
            admin.execute("TRUNCATE live_gateway_status, live_account_values, live_positions, live_quotes, "
                          "live_executions, live_daily_bars, live_contracts")

    def _store(self, tenant: str = TENANT):
        import psycopg

        from ibkr_etl.db import PostgresConnection
        from ibkr_etl.live_collector import LiveStore

        def connect():
            connection = PostgresConnection(psycopg.connect(_dsn(self.database, COLLECTOR, tenant)))
            connection.execute("SELECT set_config('app.tenant_id', ?, false)", (tenant,))
            connection.commit()
            return connection

        store = LiveStore("unused", tenant, connect=connect)
        self.addCleanup(store.close)
        return store

    def _query(self, user: str, sql: str, parameters=(), tenant: str = TENANT):
        import psycopg

        with psycopg.connect(_dsn(self.database, user, tenant)) as connection:
            return connection.execute(sql, parameters).fetchall()

    # Roles ---------------------------------------------------------------

    def test_collector_role_is_least_privilege(self):
        from ibkr_etl import live_collector_access as access

        self.assertEqual(access.least_privilege_problems(self.report), [])

    def test_collector_cannot_read_or_write_other_tables(self):
        import psycopg

        for sql in ("SELECT * FROM accounts", "SELECT * FROM subject_live_summary",
                    "DELETE FROM trades", "INSERT INTO subject_feature_states(subject_id, feature_key, "
                    "feature_state, reason) VALUES ('ULIVE1', 'live_view', 'active', 'x')"):
            with self.subTest(sql=sql), self.assertRaises(psycopg.errors.InsufficientPrivilege):
                self._query(COLLECTOR, sql)

    def test_web_role_reads_live_data_but_cannot_write(self):
        import psycopg

        self._store().write_status({
            "observed_at": NOW, "gateway_state": "connected", "last_message_at": NOW,
            "market_data_lines_in_use": 3, "market_data_type": 1, "last_error_code": None,
            "last_error_message": None,
        })
        self.assertEqual(self._query(WEB, "SELECT gateway_state FROM live_gateway_status"), [("connected",)])
        with self.assertRaises(psycopg.errors.ReadOnlySqlTransaction):
            self._query(WEB, "DELETE FROM live_gateway_status")

    # Writes and views ------------------------------------------------------

    def test_store_connects_from_a_url_and_sets_the_tenant(self):
        from psycopg.conninfo import conninfo_to_dict, make_conninfo

        from ibkr_etl.live_collector import LiveStore

        params = conninfo_to_dict(_dsn(self.database, COLLECTOR))
        params.pop("options")  # the store sets the tenant itself
        store = LiveStore(make_conninfo(**params), TENANT)
        self.addCleanup(store.close)
        store.write_status({
            "observed_at": NOW, "gateway_state": "connected", "last_message_at": NOW,
            "market_data_lines_in_use": 0, "market_data_type": 1, "last_error_code": None,
            "last_error_message": None,
        })
        self.assertEqual(store.latest_bar_dates(), {})
        self.assertEqual(self._query(WEB, "SELECT tenant_id::text FROM live_gateway_status"), [(TENANT,)])

    def test_store_round_trip_through_the_views(self):
        from ibkr_etl.live_collector import COLLECTOR_VERSION

        store = self._store()
        earlier = NOW - timedelta(minutes=1)
        store.write_contracts([{
            "conid": "265598", "symbol": "AAPL", "local_symbol": "AAPL", "sec_type": "STK",
            "currency": "USD", "primary_exchange": "NASDAQ", "long_name": "APPLE INC",
            "industry": "Technology", "category": "Computers", "subcategory": "Computers",
            "is_benchmark": False, "refreshed_at": NOW,
        }])
        store.write_snapshot(_account("ULIVE1", earlier, 900.0), [_position("ULIVE1", earlier, quantity=90.0)])
        store.write_snapshot(_account("ULIVE1", NOW), [_position("ULIVE1", NOW)])
        store.write_snapshot(_account("ULIVE2", NOW, 50.0), [])  # an account with no positions
        for observed_at, last in ((earlier, 190.0), (NOW, 190.5)):
            store.write_quotes([{
                "conid": "265598", "observed_at": observed_at, "symbol": "AAPL", "currency": "USD",
                "last_price": last, "bid_price": 190.4, "ask_price": 190.6, "prior_close": 188.0,
                "market_data_type": 1,
            }])
        store.write_status({
            "observed_at": NOW, "gateway_state": "connected", "last_message_at": NOW,
            "market_data_lines_in_use": 1, "market_data_type": 1, "last_error_code": None,
            "last_error_message": None,
        })

        summary = self._query(WEB, "SELECT subject_id, observed_at, net_liquidation, cash_by_currency "
                                   "FROM subject_live_summary ORDER BY subject_id")
        self.assertEqual([(row[0], row[1], float(row[2])) for row in summary],
                         [("ULIVE1", NOW, 1000.0), ("ULIVE2", NOW, 50.0)])
        self.assertEqual(summary[0][3], {"HKD": 60.0, "USD": 5.0})
        positions = self._query(WEB, "SELECT subject_id, symbol, description, quantity, last_price, "
                                     "quote_observed_at FROM subject_live_positions")
        self.assertEqual([(r[0], r[1], r[2], float(r[3]), float(r[4]), r[5]) for r in positions],
                         [("ULIVE1", "AAPL", "APPLE INC", 100.0, 190.5, NOW)])
        version = self._query(WEB, "SELECT collector_version FROM live_gateway_status")
        self.assertEqual(version, [(COLLECTOR_VERSION,)])

    def test_snapshot_is_all_or_nothing(self):
        import psycopg

        store = self._store()
        with self.assertRaises(psycopg.errors.CheckViolation):
            store.write_snapshot(_account("ULIVE1", NOW), [_position("ULIVE1", NOW, currency="usd")])
        self.assertEqual(self._query(WEB, "SELECT count(*) FROM live_account_values"), [(0,)])
        store.write_snapshot(_account("ULIVE1", NOW), [_position("ULIVE1", NOW)])  # reconnects
        self.assertEqual(self._query(WEB, "SELECT count(*) FROM live_positions"), [(1,)])

    def test_unknown_account_is_refused(self):
        import psycopg

        with self.assertRaises(psycopg.errors.ForeignKeyViolation):
            self._store().write_snapshot(_account("UNKNOWN9", NOW), [])

    def test_execution_commission_fills_in_later(self):
        store = self._store()
        execution = {
            "exec_id": "0000e0d5.6705a1b2.01.01", "account_id": "ULIVE1", "executed_at": NOW,
            "conid": "265598", "symbol": "AAPL", "sec_type": "STK", "currency": "USD", "side": "BUY",
            "quantity": 10.0, "price": 190.1, "commission": None, "commission_currency": None,
            "realized_pnl": None, "seen_at": NOW,
        }
        store.write_executions([execution])
        later = NOW + timedelta(minutes=1)
        store.write_executions([dict(execution, commission=1.0, commission_currency="USD", seen_at=later)])
        store.write_executions([dict(execution, seen_at=later + timedelta(minutes=1))])  # no change
        rows = self._query(WEB, "SELECT commission, commission_currency, first_seen_at, updated_at "
                                "FROM live_executions")
        self.assertEqual([(float(r[0]), r[1], r[2], r[3]) for r in rows], [(1.0, "USD", NOW, later)])

    def test_bars_upsert_and_latest_dates(self):
        store = self._store()
        bar = {"conid": "265598", "what_to_show": "ADJUSTED_LAST", "bar_date": date(2026, 10, 12),
               "open_price": 188.2, "high_price": 191.0, "low_price": 187.9, "close_price": 190.2,
               "volume": 48000000.0, "currency": "USD", "fetched_at": NOW}
        store.write_bars([bar])
        store.write_bars([dict(bar, close_price=190.0)])
        self.assertEqual(store.latest_bar_dates(), {("265598", "ADJUSTED_LAST"): date(2026, 10, 12)})
        self.assertEqual(self._query(WEB, "SELECT close_price FROM live_daily_bars")[0][0], 190)

    def test_retention_purge_cascades_to_positions(self):
        store = self._store()
        old = NOW - timedelta(days=31)
        store.write_snapshot(_account("ULIVE1", old), [_position("ULIVE1", old)])
        store.write_snapshot(_account("ULIVE1", NOW), [_position("ULIVE1", NOW)])
        base = {"account_id": "ULIVE1", "conid": "265598", "symbol": "AAPL", "sec_type": "STK",
                "currency": "USD", "side": "SELL", "quantity": 1.0, "price": 1.0, "commission": None,
                "commission_currency": None, "realized_pnl": None, "seen_at": NOW}
        store.write_executions([dict(base, exec_id="old.01", executed_at=NOW - timedelta(days=400)),
                                dict(base, exec_id="recent.01", executed_at=old)])
        store.purge(NOW)
        self.assertEqual(self._query(WEB, "SELECT observed_at FROM live_account_values"), [(NOW,)])
        self.assertEqual(self._query(WEB, "SELECT observed_at FROM live_positions"), [(NOW,)])
        self.assertEqual(self._query(WEB, "SELECT exec_id FROM live_executions"), [("recent.01",)])

    # Tenants and switches --------------------------------------------------

    def test_rows_stay_inside_their_tenant(self):
        import psycopg

        status = {"observed_at": NOW, "gateway_state": "disconnected", "last_message_at": None,
                  "market_data_lines_in_use": 0, "market_data_type": 3, "last_error_code": None,
                  "last_error_message": None}
        self._store(OTHER_TENANT).write_status(status)
        self.assertEqual(self._query(WEB, "SELECT count(*) FROM live_gateway_status"), [(0,)])
        self.assertEqual(self._query(WEB, "SELECT count(*) FROM live_gateway_status", tenant=OTHER_TENANT), [(1,)])
        with self.assertRaises(psycopg.errors.InsufficientPrivilege):  # row-level security check
            self._query(COLLECTOR, "INSERT INTO live_gateway_status(tenant_id, observed_at, gateway_state, "
                                   "collector_version) VALUES (%s, now(), 'connected', 'x')", (OTHER_TENANT,))

    def test_live_view_is_a_feature_switch(self):
        import psycopg

        with psycopg.connect(_dsn(self.database, OWNER)) as owner:
            owner.execute("INSERT INTO platform_subjects(subject_id, subject_type, display_name, "
                          "source_account_id, lifecycle_status) VALUES ('ULIVE1', 'physical_account', "
                          "'Live test', 'ULIVE1', 'active') ON CONFLICT DO NOTHING")
            owner.execute("INSERT INTO subject_feature_states(subject_id, feature_key, feature_state, reason) "
                          "VALUES ('ULIVE1', 'live_view', 'preview', 'owner pilot')")
            with self.assertRaises(psycopg.errors.CheckViolation):
                owner.execute("INSERT INTO subject_feature_states(subject_id, feature_key, feature_state, "
                              "reason) VALUES ('ULIVE1', 'live_trading', 'active', 'x')")
            owner.rollback()


if __name__ == "__main__":
    unittest.main()
