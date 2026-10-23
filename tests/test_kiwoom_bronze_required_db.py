"""Kiwoom bronze capture required (KIWOOM_BRONZE_REQUIRED=true), against a migrated PostgreSQL.

The collector's daily run for a KRW and a USD account with required capture: when bronze can store every page,
both accounts load and each account's bronze run rebuilds exactly as silver holds it (the collector script's
check after the run); when bronze stops storing, the account fails before silver changes and the next
account fails before Kiwoom is called.

Needs a disposable server: set AGENT_TEST_PG_ADMIN_DSN to a superuser DSN (CI starts one). Skipped otherwise.
Synthetic connections, accounts and answers only; no Kiwoom call is made.
"""
from __future__ import annotations

import importlib.util
import json
from datetime import timedelta
from pathlib import Path
from unittest import skipUnless

try:
    from test_kiwoom_bronze_history_db import ADMIN_DSN, TENANT, TwoKiwoomAccounts
    from test_kiwoom_conform import ACCOUNT, NOW, TODAY, US_ACCOUNT
except ImportError:  # run as tests.test_kiwoom_bronze_required_db
    from tests.test_kiwoom_bronze_history_db import ADMIN_DSN, TENANT, TwoKiwoomAccounts
    from tests.test_kiwoom_conform import ACCOUNT, NOW, TODAY, US_ACCOUNT

ROOT = Path(__file__).resolve().parents[1]
LATER = NOW + timedelta(hours=2)  # 19:20 in Seoul the same day: the same KRX and U.S. days as the load
MARKET = {ACCOUNT: "KRW", US_ACCOUNT: "USD"}


class StopsStoring:
    """A bronze connection whose page inserts fail once ``left["pages"]`` pages are stored (the database went
    away mid-run). Shared by every connection the writer opens, so a retry fails too."""

    def __init__(self, connection, left):
        self._connection = connection
        self._left = left

    def execute(self, sql, parameters=()):
        if "bronze.kiwoom_responses" in sql:
            if self._left["pages"] <= 0:
                raise RuntimeError("synthetic: the database went away")
            self._left["pages"] -= 1
        return self._connection.execute(sql, parameters)

    def __getattr__(self, name):
        return getattr(self._connection, name)


@skipUnless(ADMIN_DSN, "AGENT_TEST_PG_ADMIN_DSN is not set")
class RequiredCaptureTests(TwoKiwoomAccounts):
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

        return KiwoomBronze(connect, TENANT, clock=lambda: LATER, required=True, sleep=lambda _: None)

    def _run_status(self, run_id):
        status = self.db.execute("SELECT status FROM ingestion_runs WHERE run_id = ?", (run_id,)).fetchone()[0]
        self.db.rollback()
        return status

    def test_a_page_that_cannot_be_stored_fails_its_account_before_silver_changes(self):
        before, runs = self._silver(), self._bronze_runs()
        left = {"pages": 2}
        with self.assertLogs("ibkr_etl.kiwoom_bronze", "WARNING"):
            results = self._collect(self._bronze(lambda: StopsStoring(self._connect(), left)))
        self.assertEqual({market: item["status"] for market, item in results.items()},
                         {"KRW": "failed", "USD": "failed"})
        for item in results.values():
            self.assertTrue(item["error"].startswith("BronzeNotStored"), item["error"])
            self.assertNotIn(item["account"], item["error"])
        self.assertEqual(self._silver(), before, "silver is never written from an answer bronze did not store")
        # Only the first account's run was opened; it is marked failed. The second failed before Kiwoom.
        self.assertEqual(self._bronze_runs(), runs + 1)
        opened = [item for item in results.values() if item.get("bronze_run_id")]
        self.assertEqual(len(opened), 1)
        self.assertEqual(self._run_status(opened[0]["bronze_run_id"]), "failed")

    def test_with_every_page_stored_both_accounts_load_and_rebuild_as_silver_holds_them(self):
        results = self._collect(self._bronze(self._connect))
        self.assertEqual({market: item["status"] for market, item in results.items()},
                         {"KRW": "loaded", "USD": "loaded"})
        for item in results.values():
            self.assertEqual(self._run_status(item["bronze_run_id"]), "success")

        spec = importlib.util.spec_from_file_location("run_kiwoom_collector", ROOT / "scripts" / "run_kiwoom_collector.py")
        script = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(script)
        script.connect = lambda *_: self._connect()
        checked = script.rebuild_check("unused", TENANT, list(results.values()))
        self.assertEqual(sorted(item["market"] for item in checked), ["KRW", "USD"])
        for item in checked:
            with self.subTest(market=item["market"]):
                self.assertEqual(item["run_status"], "success")
                self.assertEqual(item["parts"]["movements"], "rebuilt")
                self.assertEqual(item["answers"]["missing"], 0)
                self.assertGreater(item["tables"]["trades"]["matched"] + item["tables"]["cash_transactions"]["matched"],
                                   0)
                self.assertTrue(item["clean"], item)
        printed = json.dumps(checked, default=str)
        self.assertNotIn(ACCOUNT, printed)
        self.assertNotIn(US_ACCOUNT, printed)
