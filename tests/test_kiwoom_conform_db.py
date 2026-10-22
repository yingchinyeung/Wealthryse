"""The Kiwoom conform check against a migrated PostgreSQL: the collector's own run, its answers stored in
bronze.kiwoom_responses, then the read-only rebuild and comparison.

Needs a disposable server: set AGENT_TEST_PG_ADMIN_DSN to a superuser DSN (CI starts one). Skipped otherwise.
Synthetic connection, account and answers only; no Kiwoom call is made.
"""
from __future__ import annotations

import importlib.util
import io
import json
import os
import uuid
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock, skipUnless

try:
    from test_broker_onboarding_db import ADMIN_DSN, OWNER_ROLE, TENANT, _dsn, _MigratedDatabase
    from test_kiwoom_conform import (ACCOUNT, APP_KEY, APP_SECRET, NOW, START, STARTED, TODAY, LiveKiwoom)
except ImportError:  # run as tests.test_kiwoom_conform_db
    from tests.test_broker_onboarding_db import ADMIN_DSN, OWNER_ROLE, TENANT, _dsn, _MigratedDatabase
    from tests.test_kiwoom_conform import (ACCOUNT, APP_KEY, APP_SECRET, NOW, START, STARTED, TODAY, LiveKiwoom)

ROOT = Path(__file__).resolve().parents[1]
SILVER = ("trades", "cash_transactions", "position_snapshots", "cash_snapshots", "nav_snapshots", "fx_rates")


@skipUnless(ADMIN_DSN, "AGENT_TEST_PG_ADMIN_DSN is not set")
class KiwoomConformDatabaseTests(_MigratedDatabase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        try:
            cls.run_id = cls._collect()
        except BaseException:
            cls.tearDownClass()  # unittest skips it when setUpClass fails
            raise

    @classmethod
    def _connect(cls):
        import psycopg

        from ibkr_etl.db import PostgresConnection, _hybrid_row_factory

        return PostgresConnection(psycopg.connect(_dsn(ADMIN_DSN, cls.database, OWNER_ROLE),
                                                  options=f"-c app.tenant_id={TENANT}",
                                                  row_factory=_hybrid_row_factory))

    @classmethod
    def _collect(cls) -> int:
        """One daily run of the collector for a synthetic KRW account, with bronze capture on."""

        from ibkr_etl.kiwoom_bronze import KiwoomBronze
        from ibkr_etl.kiwoom_client import KiwoomClient
        from ibkr_etl.kiwoom_collector import run
        from ibkr_etl.onboarding_v2_web import start_case

        db, live = cls._connect(), LiveKiwoom()
        bronze = KiwoomBronze(cls._connect, TENANT, clock=lambda: STARTED)

        def client_for(_target):
            return KiwoomClient(environment="mock", app_key=APP_KEY, app_secret=APP_SECRET, transport=live,
                                clock=live.clock, sleep=live.sleep, min_interval_seconds=0)

        try:
            connection_id = uuid.uuid4()
            db.execute(
                "INSERT INTO broker_connections (connection_id, broker, environment, display_name, "
                "key_vault_secret_name, credential_fingerprint, status) "
                "VALUES (?, 'kiwoom_rest', 'mock', ?, ?, ?, 'pending_check')",
                (connection_id, f"Kiwoom {connection_id.hex[:6]}", "broker-kiwoom-rest-" + connection_id.hex,
                 "0" * 16))
            db.execute("INSERT INTO broker_connection_accounts (connection_id, broker_account_id, discovery_status) "
                       "VALUES (?, ?, 'discovered')", (connection_id, ACCOUNT))
            start_case(db, TENANT, connection_id=str(connection_id), broker_account_id=ACCOUNT,
                       display_name="Korea stocks", reporting_currency="KRW", history_start_date=START, actor=None,
                       today=TODAY)
            db.commit()
            results = run(db, TENANT, client_for=client_for, today=TODAY, now=NOW, include_us=True, bronze=bronze)
            assert [item["status"] for item in results] == ["loaded"], results
            assert (bronze.stats["runs"], bronze.stats["failed"]) == (1, 0) and bronze.stats["written"] > 10, \
                bronze.summary()
            return int(db.execute("SELECT max(run_id) AS run_id FROM ingestion_runs "
                                  "WHERE run_type = 'kiwoom_rest_bronze'").fetchone()["run_id"])
        finally:
            bronze.close()
            db.close()

    def setUp(self):
        self.db = self._connect()
        self.addCleanup(self.db.close)

    def _check(self):
        from ibkr_etl.kiwoom_conform import check_run

        self.db.rollback()
        self.db.execute("SET TRANSACTION READ ONLY")  # any write would fail here
        try:
            return check_run(self.db, self.run_id)
        finally:
            self.db.rollback()

    def test_silver_matches_the_stored_answers(self):
        result = self._check()
        self.assertEqual((result["kind"], result["market"], result["run_status"]), ("load", "KRW", "success"))
        self.assertEqual(result["parts"], {"movements": "rebuilt", "history": "rebuilt"})
        self.assertEqual(sorted(result["tables"]), sorted(SILVER))
        for table, counts in result["tables"].items():
            with self.subTest(table=table):
                stored = self.db.execute(f"SELECT count(*) AS n FROM {table} WHERE account_id = ?",
                                         (f"KIWOOM-{ACCOUNT}",)).fetchone()["n"]
                self.assertGreater(stored, 0)
                self.assertEqual(counts, {"matched": stored, "differ": 0, "only_in_rebuild": 0, "only_in_silver": 0,
                                          "differ_columns": {}})
        self.assertEqual(result["answers"]["unused"], 0)
        self.assertTrue(result["clean"])
        self.assertNotIn(ACCOUNT, json.dumps(result))

    def test_latest_names_the_run(self):
        from ibkr_etl.kiwoom_conform import latest_runs

        self.assertEqual(latest_runs(self.db), [self.run_id])
        self.db.rollback()

    def _insert(self, table, row):
        marks = ", ".join("?" for _ in row)
        self.db.execute(f"INSERT INTO {table} ({', '.join(row)}) VALUES ({marks})", list(row.values()))

    def test_a_change_to_silver_shows(self):
        account = f"KIWOOM-{ACCOUNT}"
        trade = self.db.execute("SELECT source_key, quantity_text FROM trades WHERE account_id = ? "
                                "ORDER BY source_key LIMIT 1", (account,)).fetchone()
        cash = self.db.execute("SELECT * FROM cash_snapshots WHERE account_id = ? ORDER BY snapshot_date LIMIT 1",
                               (account,)).fetchone()
        movement = self.db.execute("SELECT * FROM cash_transactions WHERE account_id = ? LIMIT 1",
                                   (account,)).fetchone()
        self.db.execute("UPDATE trades SET quantity_text = '99' WHERE account_id = ? AND source_key = ?",
                        (account, trade["source_key"]))
        self.db.execute("DELETE FROM cash_snapshots WHERE account_id = ? AND snapshot_date = ? AND currency = ?",
                        (account, cash["snapshot_date"], cash["currency"]))
        self._insert("cash_transactions", {**{name: movement[name] for name in movement.keys()},
                                           "source_key": "not-from-kiwoom", "transaction_id": "not-from-kiwoom"})
        self.db.commit()
        try:
            tables = self._check()["tables"]
            self.assertEqual((tables["trades"]["differ"], tables["trades"]["differ_columns"]),
                             (1, {"quantity_text": 1}))
            self.assertEqual(tables["cash_snapshots"]["only_in_rebuild"], 1)
            self.assertEqual(tables["cash_transactions"]["only_in_silver"], 1)
        finally:
            self.db.execute("UPDATE trades SET quantity_text = ? WHERE account_id = ? AND source_key = ?",
                            (trade["quantity_text"], account, trade["source_key"]))
            self._insert("cash_snapshots", {name: cash[name] for name in cash.keys()})
            self.db.execute("DELETE FROM cash_transactions WHERE account_id = ? AND source_key = 'not-from-kiwoom'",
                            (account,))
            self.db.commit()

    def test_the_script_prints_counts_and_exits_zero(self):
        from psycopg.conninfo import conninfo_to_dict

        params = conninfo_to_dict(_dsn(ADMIN_DSN, self.database, OWNER_ROLE))
        url = (f"postgresql://{params['user']}:{params['password']}@{params.get('host', 'localhost')}:"
               f"{params.get('port', 5432)}/{params['dbname']}")
        spec = importlib.util.spec_from_file_location("run_kiwoom_conform", ROOT / "scripts" / "run_kiwoom_conform.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        out = io.StringIO()
        with mock.patch.dict(os.environ, {"DATABASE_URL": url, "PORTFOLIO_TENANT_ID": TENANT}), redirect_stdout(out):
            code = module.main(["check", "--latest"])
        printed = json.loads(out.getvalue())
        self.assertEqual(code, 0, printed)
        self.assertEqual([(run["run_id"], run["clean"]) for run in printed["runs"]], [(self.run_id, True)])
        self.assertNotIn(ACCOUNT, out.getvalue())
