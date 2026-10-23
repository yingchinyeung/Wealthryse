"""Kiwoom's past days asked again into bronze (ibkr_etl.kiwoom_bronze_history), against a migrated PostgreSQL.

The collector first loads a KRW and a USD account the way it did before bronze capture began (no bronze),
so silver holds past days that bronze does not. The history re-ask then stores those days' answers with
silver untouched, and the conform check rebuilds every one of them exactly as silver holds it.

Needs a disposable server: set AGENT_TEST_PG_ADMIN_DSN to a superuser DSN (CI starts one). Skipped otherwise.
Synthetic connections, accounts and answers only; no Kiwoom call is made.
"""
from __future__ import annotations

import importlib.util
import io
import json
import os
import uuid
from contextlib import redirect_stdout
from datetime import timedelta
from pathlib import Path
from unittest import mock, skipUnless

try:
    from test_broker_onboarding_db import ADMIN_DSN, OWNER_ROLE, TENANT, _dsn, _MigratedDatabase
    from test_kiwoom_conform import (ACCOUNT, APP_KEY, APP_SECRET, NOW, START, TODAY, US_ACCOUNT, LiveKiwoom)
except ImportError:  # run as tests.test_kiwoom_bronze_history_db
    from tests.test_broker_onboarding_db import ADMIN_DSN, OWNER_ROLE, TENANT, _dsn, _MigratedDatabase
    from tests.test_kiwoom_conform import (ACCOUNT, APP_KEY, APP_SECRET, NOW, START, TODAY, US_ACCOUNT,
                                           LiveKiwoom)

ROOT = Path(__file__).resolve().parents[1]
SILVER = ("trades", "cash_transactions", "position_snapshots", "cash_snapshots", "nav_snapshots", "fx_rates")
SNAPSHOTS = ("position_snapshots", "cash_snapshots", "nav_snapshots")
LATER = NOW + timedelta(hours=2)  # 19:20 in Seoul the same day: the same KRX and U.S. days as the load


class TwoKiwoomAccounts(_MigratedDatabase):
    """A KRW and a USD Kiwoom account loaded by the collector before bronze capture existed (no tests here)."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        try:
            cls._load_without_bronze()
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

    @staticmethod
    def _client_for(_target):
        from ibkr_etl.kiwoom_client import KiwoomClient

        live = LiveKiwoom()
        return KiwoomClient(environment="mock", app_key=APP_KEY, app_secret=APP_SECRET, transport=live,
                            clock=live.clock, sleep=live.sleep, min_interval_seconds=0)

    @classmethod
    def _load_without_bronze(cls) -> None:
        """The collector's daily run for a KRW and a USD account, before bronze capture existed."""

        from ibkr_etl.kiwoom_collector import run
        from ibkr_etl.onboarding_v2_web import start_case

        db = cls._connect()
        try:
            for account, currency in ((ACCOUNT, "KRW"), (US_ACCOUNT, "USD")):
                connection_id = uuid.uuid4()
                db.execute(
                    "INSERT INTO broker_connections (connection_id, broker, environment, display_name, "
                    "key_vault_secret_name, credential_fingerprint, status) "
                    "VALUES (?, 'kiwoom_rest', 'mock', ?, ?, ?, 'pending_check')",
                    (connection_id, f"Kiwoom {connection_id.hex[:6]}", "broker-kiwoom-rest-" + connection_id.hex,
                     "0" * 16))
                db.execute("INSERT INTO broker_connection_accounts (connection_id, broker_account_id, "
                           "discovery_status) VALUES (?, ?, 'discovered')", (connection_id, account))
                start_case(db, TENANT, connection_id=str(connection_id), broker_account_id=account,
                           display_name="Synthetic " + currency, reporting_currency=currency,
                           history_start_date=START, actor=None, today=TODAY)
                db.commit()
                results = run(db, TENANT, client_for=cls._client_for, today=TODAY, now=NOW, include_us=True,
                              bronze=None)
                # One account is onboarded at a time: the first goes live (the website's step) before the next.
                db.execute("INSERT INTO platform_subjects (subject_id, subject_type, display_name, lifecycle_status) "
                           "SELECT target_subject_id, 'physical_account', 'Synthetic', 'onboarding' "
                           "FROM broker_onboarding_cases WHERE stage = 'load_and_reconcile' ON CONFLICT DO NOTHING")
                db.execute("UPDATE broker_onboarding_cases SET subject_id = target_subject_id, "
                           "stage = 'first_month_end', status = 'live', went_live_at = now() "
                           "WHERE stage = 'load_and_reconcile'")
                db.commit()
            assert [item["status"] for item in results] == ["loaded", "loaded"], results
        finally:
            db.close()

    def setUp(self):
        self.db = self._connect()
        self.addCleanup(self.db.close)

    def _silver(self) -> dict[str, list[str]]:
        found = {}
        for table in SILVER:
            rows = self.db.execute(f"SELECT * FROM {table} WHERE account_id LIKE 'KIWOOM-%'").fetchall()
            found[table] = sorted(json.dumps({name: row[name] for name in row.keys()}, default=str) for row in rows)
        self.db.rollback()
        return found

    def _bronze_runs(self) -> int:
        count = self.db.execute("SELECT count(*) AS n FROM ingestion_runs WHERE run_type = 'kiwoom_rest_bronze'"
                                ).fetchone()["n"]
        self.db.rollback()
        return int(count)


@skipUnless(ADMIN_DSN, "AGENT_TEST_PG_ADMIN_DSN is not set")
class KiwoomBronzeHistoryTests(TwoKiwoomAccounts):
    def _ask(self, **options):
        from ibkr_etl.kiwoom_bronze import KiwoomBronze
        from ibkr_etl.kiwoom_bronze_history import run

        connection = self._connect()
        bronze = KiwoomBronze(self._connect, TENANT, clock=lambda: LATER)
        try:
            connection.execute("SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY")
            connection.commit()
            results = run(connection, TENANT, client_for=self._client_for, bronze=bronze, now=lambda: LATER,
                          include_us=True, **{"limit": 250, "minutes": 10, **options})
            return sorted(results, key=lambda item: item["market"]), bronze
        finally:
            bronze.close()
            connection.close()

    def _check(self, run_id):
        from ibkr_etl.kiwoom_conform import check_run

        self.db.rollback()
        self.db.execute("SET TRANSACTION READ ONLY")
        try:
            return check_run(self.db, run_id)
        finally:
            self.db.rollback()

    def test_past_days_go_to_bronze_and_rebuild_as_silver_holds_them(self):
        before = self._silver()
        self.assertTrue(all(before[table] for table in SILVER))
        self.assertEqual(self._bronze_runs(), 0)

        # A limit of one day an account first: the rest is left for the next run.
        results, _ = self._ask(limit=1)
        self.assertEqual([(item["market"], item["status"], item["days_asked"]) for item in results],
                         [("KRW", "asked", 1), ("USD", "asked", 1)])
        self.assertEqual([item["days_left"] for item in results], [2, 3])

        results, bronze = self._ask()
        self.assertEqual([(item["market"], item["status"], item["days_planned"], item["days_asked"],
                           item["days_left"]) for item in results],
                         [("KRW", "asked", 2, 2, 0), ("USD", "asked", 3, 3, 0)])
        self.assertEqual(bronze.stats["failed"], 0)
        self.assertEqual(self._silver(), before, "silver is never written")
        self.assertNotIn(ACCOUNT, json.dumps(results))
        self.assertNotIn(US_ACCOUNT, json.dumps(results))

        for item, days in zip(results, (("2026-10-15", "2026-10-19"), ("2026-10-15", "2026-10-16", "2026-10-19"))):
            with self.subTest(market=item["market"]):
                checked = self._check(item["run_id"])
                self.assertEqual((checked["kind"], checked["market"], checked["run_status"]),
                                 ("load", item["market"], "success"))
                self.assertEqual(checked["parts"], {"movements": "rebuilt", "history": "rebuilt"})
                self.assertEqual(checked["history_days"]["replayed"], len(days))
                self.assertEqual(checked["answers"]["missing"], 0)
                for table in SNAPSHOTS:
                    self.assertEqual(checked["tables"][table]["differ"], 0, table)
                    self.assertEqual(checked["tables"][table]["only_in_rebuild"], 0, table)
                    self.assertEqual(checked["tables"][table]["only_in_silver"], 0, table)
                    self.assertGreater(checked["tables"][table]["matched"], 0, table)
                self.assertTrue(checked["clean"], checked)

        # Everything silver holds is in bronze now: another run asks for nothing and opens no bronze run.
        runs = self._bronze_runs()
        results, _ = self._ask()
        self.assertEqual([(item["market"], item["status"]) for item in results],
                         [("KRW", "nothing to ask"), ("USD", "nothing to ask")])
        self.assertEqual(self._bronze_runs(), runs)

    def test_out_of_time_opens_no_bronze_run(self):
        runs = self._bronze_runs()
        ticks = iter([0.0] + [1e9] * 50)  # the budget is spent before the first account
        results, _ = self._ask(minutes=1, monotonic=lambda: next(ticks))
        self.assertTrue(results)
        self.assertTrue(all(item["status"] in ("out of time", "nothing to ask") for item in results), results)
        self.assertEqual(self._bronze_runs(), runs)

    def test_the_days_of_a_run_that_failed_are_asked_again(self):
        # Runs after test_past_days_go_to_bronze_and_rebuild_as_silver_holds_them (name order): every past day
        # is in bronze. A run that fails part-way, as one stopped by a day the client could not value, may hold
        # a day's first answers but not the rest, so its days count as not held.
        self._ask()
        rows = self.db.execute(
            "SELECT k.run_id, count(DISTINCT k.request_key->>'qry_dt') AS days FROM bronze.kiwoom_responses k "
            "WHERE k.api_id = 'ka01690' AND k.request_key->>'account' = ? GROUP BY k.run_id ORDER BY days DESC",
            (ACCOUNT,)).fetchall()
        run_id, days = int(rows[0]["run_id"]), int(rows[0]["days"])
        self.db.execute("UPDATE ingestion_runs SET status = 'failed' WHERE run_id = ?", (run_id,))
        self.db.commit()
        try:
            results, _ = self._ask()
            self.assertEqual([(item["market"], item["status"], item["days_asked"]) for item in results],
                             [("KRW", "asked", days), ("USD", "nothing to ask", 0)])
            checked = self._check(results[0]["run_id"])
            self.assertEqual(checked["parts"], {"movements": "rebuilt", "history": "rebuilt"})
            self.assertEqual(checked["history_days"]["replayed"], days)
            self.assertTrue(checked["clean"], checked)
            results, _ = self._ask()
            self.assertEqual([item["status"] for item in results], ["nothing to ask", "nothing to ask"])
        finally:
            self.db.rollback()
            self.db.execute("UPDATE ingestion_runs SET status = 'success' WHERE run_id = ?", (run_id,))
            self.db.commit()

    def test_the_script_refuses_without_bronze_capture(self):
        spec = importlib.util.spec_from_file_location("run_kiwoom_bronze_history",
                                                      ROOT / "scripts" / "run_kiwoom_bronze_history.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        for environ in ({"KIWOOM_BRONZE": "false", "KIWOOM_COLLECTOR_ENABLED": "true"},
                        {"KIWOOM_BRONZE": "true", "KIWOOM_COLLECTOR_ENABLED": "true",
                         "DEPLOYMENT_ENVIRONMENT": "staging"}):
            out = io.StringIO()
            with mock.patch.dict(os.environ, environ, clear=True), redirect_stdout(out):
                code = module.main([])
            with self.subTest(environ=environ):
                self.assertEqual(code, 2)
                self.assertEqual(json.loads(out.getvalue())["status"], "error")
