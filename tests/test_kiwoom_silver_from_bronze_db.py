"""Kiwoom silver written from bronze (KIWOOM_SILVER_FROM_BRONZE=true), against a migrated PostgreSQL.

The collector's daily run for a KRW and a USD account with every page read back from
bronze.kiwoom_responses as stored: both accounts load from bronze's copy and each account's bronze run rebuilds
exactly as silver holds it; when a page cannot be read back, the account fails before silver changes.

Needs a disposable server: set AGENT_TEST_PG_ADMIN_DSN to a superuser DSN (CI starts one). Skipped otherwise.
Synthetic connections, accounts and answers only; no Kiwoom call is made.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from unittest import skipUnless

try:
    from test_kiwoom_bronze_history_db import ADMIN_DSN, TENANT, TwoKiwoomAccounts
    from test_kiwoom_bronze_required_db import LATER, MARKET
    from test_kiwoom_conform import ACCOUNT, TODAY, US_ACCOUNT
except ImportError:  # run as tests.test_kiwoom_silver_from_bronze_db
    from tests.test_kiwoom_bronze_history_db import ADMIN_DSN, TENANT, TwoKiwoomAccounts
    from tests.test_kiwoom_bronze_required_db import LATER, MARKET
    from tests.test_kiwoom_conform import ACCOUNT, TODAY, US_ACCOUNT

ROOT = Path(__file__).resolve().parents[1]


class CannotReadBack:
    """A bronze connection that stores pages but cannot read them back (the read fails)."""

    def __init__(self, connection):
        self._connection = connection

    def execute(self, sql, parameters=()):
        if sql.startswith("SELECT") and "bronze.kiwoom_responses" in sql:
            raise RuntimeError("synthetic: the read-back failed")
        return self._connection.execute(sql, parameters)

    def __getattr__(self, name):
        return getattr(self._connection, name)


@skipUnless(ADMIN_DSN, "AGENT_TEST_PG_ADMIN_DSN is not set")
class SilverFromBronzeTests(TwoKiwoomAccounts):
    def _collect(self, bronze):
        from ibkr_etl.kiwoom_collector import run

        connection = self._connect()
        try:
            results = run(connection, TENANT, client_for=self._client_for, today=TODAY, now=LATER,
                          include_us=True, bronze=bronze)
        finally:
            bronze.close()
            connection.close()
        return {MARKET[item["account"]]: item for item in results}

    def _bronze(self, connect):
        from ibkr_etl.kiwoom_bronze import KiwoomBronze

        return KiwoomBronze(connect, TENANT, clock=lambda: LATER, required=True, read_back=True,
                            sleep=lambda _: None)

    def _run_status(self, run_id):
        status = self.db.execute("SELECT status FROM ingestion_runs WHERE run_id = ?", (run_id,)).fetchone()[0]
        self.db.rollback()
        return status

    def test_a_page_that_cannot_be_read_back_fails_its_account_before_silver_changes(self):
        before = self._silver()
        with self.assertLogs("ibkr_etl.kiwoom_bronze", "WARNING"):
            results = self._collect(self._bronze(lambda: CannotReadBack(self._connect())))
        self.assertEqual({market: item["status"] for market, item in results.items()},
                         {"KRW": "failed", "USD": "failed"})
        for item in results.values():
            self.assertTrue(item["error"].startswith("BronzeNotStored"), item["error"])
            self.assertNotIn(item["account"], item["error"])
        self.assertEqual(self._silver(), before, "silver is never written from a page not read back from bronze")

    def test_both_accounts_load_from_the_pages_bronze_stored_and_rebuild_as_silver_holds_them(self):
        bronze = self._bronze(self._connect)
        results = self._collect(bronze)
        self.assertEqual({market: item["status"] for market, item in results.items()},
                         {"KRW": "loaded", "USD": "loaded"})
        for item in results.values():
            self.assertEqual(item["silver_from"], "bronze.kiwoom_responses")
            self.assertEqual(self._run_status(item["bronze_run_id"]), "success")
        stats = bronze.summary()
        self.assertTrue(stats["silver_from_bronze"])
        self.assertGreater(stats["written"], 0)
        # Every page the client used was read back from bronze.
        self.assertEqual(stats["read_back"], stats["written"] + stats["repeated"])
        self.assertEqual(stats["failed"], 0)

        spec = importlib.util.spec_from_file_location("run_kiwoom_collector", ROOT / "scripts" / "run_kiwoom_collector.py")
        script = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(script)
        script.connect = lambda *_: self._connect()
        checked = script.rebuild_check("unused", TENANT, list(results.values()))
        self.assertEqual(sorted(item["market"] for item in checked), ["KRW", "USD"])
        for item in checked:
            with self.subTest(market=item["market"]):
                self.assertEqual(item["parts"]["movements"], "rebuilt")
                self.assertEqual(item["answers"]["missing"], 0)
                self.assertTrue(item["clean"], item)
        printed = json.dumps([checked, stats], default=str)
        self.assertNotIn(ACCOUNT, printed)
        self.assertNotIn(US_ACCOUNT, printed)
