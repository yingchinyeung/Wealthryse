"""The U8 "as known on" proof on a real PostgreSQL: the worked example of docs/MEDALLION_AS_KNOWN_ON.md.

The held migrations (infra/medallion/drafts/u8_silver_history.sql, then u8_fx_event_key.sql) are
not in migrations/postgres, so nothing in production applies them. Needs a disposable server: set
AGENT_TEST_PG_ADMIN_DSN to a superuser DSN (CI starts one). Skipped otherwise. Builds its own
database with every migration in order, applies the held files as the owner, then loads made-up
Flex statements with today's loader (IBKR_FX_EVENT_KEY off). Synthetic data only.
tests/test_silver_history_db.py covers the backfill, the checks, the FX step and the undo.
"""
from __future__ import annotations

import os
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

import test_medallion_layers_db as layers
from ibkr_etl.config import Settings
from ibkr_etl.loader import fx_event_identity, fx_event_key as event_key
from ibkr_etl.service import ingest_content

ADMIN_DSN = os.environ.get("AGENT_TEST_PG_ADMIN_DSN", "")
ROOT = Path(__file__).resolve().parents[1]
HELD = (ROOT / "infra" / "medallion" / "drafts" / "u8_silver_history.sql",
        ROOT / "infra" / "medallion" / "drafts" / "u8_fx_event_key.sql")
TENANT, OTHER_TENANT = layers.TENANT, layers.OTHER_TENANT
ACCOUNT = "U9100001"
QUIET_ACCOUNT = "U9100009"
READER = "asof_test_reader"
TRACKED = ("trades", "cash_transactions", "fx_transactions", "fx_rates", "nav_snapshots")


def _element(tag: str, **attributes: str) -> str:
    return f"<{tag} " + " ".join(f'{key}="{value}"' for key, value in attributes.items()) + " />"


def statement(account: str, from_date: str, to_date: str, *, nav: dict[str, str], trades=(), cash=(),
              fx=()) -> bytes:
    """A made-up Activity Flex statement for one account."""
    parts = [f'<FlexStatement accountId="{account}" fromDate="{from_date}" toDate="{to_date}" period="Custom">',
             _element("AccountInformation", accountId=account, currency="USD", accountType="Individual"),
             "<EquitySummaryInBase>"]
    parts += [_element("EquitySummaryByReportDateInBase", accountId=account, reportDate=day, currency="USD",
                       total=total) for day, total in nav.items()]
    parts += ["</EquitySummaryInBase>", "<ConversionRates>"]
    parts += [_element("ConversionRate", reportDate=day, fromCurrency="HKD", toCurrency="USD", rate="0.1287")
              for day in nav]
    parts += ["</ConversionRates>", "<Trades>"]
    parts += [_element("Trade", accountId=account, currency="USD", assetCategory="STK", symbol="SYNA",
                       conid="9001", **row) for row in trades]
    parts += ["</Trades>", "<CashTransactions>"]
    parts += [_element("CashTransaction", accountId=account, currency="USD", conid="9001", **row) for row in cash]
    parts += ["</CashTransactions>", "<FxTransactions>"]
    parts += [_element("FxTransaction", accountId=account, functionalCurrency="USD", fxCurrency="HKD",
                       activityDescription="Forex Trade", **row) for row in fx]
    parts += ["</FxTransactions>", "</FlexStatement>"]
    return ('<?xml version="1.0" encoding="UTF-8"?><FlexQueryResponse queryName="Synthetic" type="AF">'
            f'<FlexStatements count="1">{"".join(parts)}</FlexStatements></FlexQueryResponse>').encode()


ORIGINAL_SALE = dict(tradeID="T100", ibExecID="E100.1", tradeDate="20261015", reportDate="20261015",
                     buySell="SELL", quantity="-100", tradePrice="50.00", proceeds="5000.00", netCash="4999.00")
# IBKR re-prices the sale the next day: a cancellation that reverses it (no trade id), then the
# same trade id booked again with a later report date, on the same trade date.
CANCELLATION = dict(tradeDate="20261015", reportDate="20261016", buySell="SELL (Ca.)", quantity="100",
                    tradePrice="50.00", proceeds="-5000.00", netCash="-4999.00")
REBOOKING = dict(tradeID="T100", ibExecID="E100.2", tradeDate="20261015", reportDate="20261016",
                 buySell="SELL", quantity="-100", tradePrice="49.80", proceeds="4980.00", netCash="4979.00")
DIVIDEND = dict(transactionID="C200", reportDate="20261014", dateTime="20261014;160000", type="Dividends",
                description="SYNA CASH DIVIDEND", amount="12.50")
FX_FIRST = dict(reportDate="20261015", dateTime="20261015;160000", quantity="1000", cost="-128.00",
                proceeds="128.70", realizedPL="0.70")
FX_RESTATED = dict(FX_FIRST, proceeds="128.60", realizedPL="0.60")


def fx_event_key(row: dict) -> str:
    """The loader's event key (IBKR_FX_EVENT_KEY): the FX row's identity without the amounts IBKR
    restates (proceeds, realised P&L), plus its occurrence in the statement (1 here)."""
    return event_key(fx_event_identity(row), 1)


@unittest.skipUnless(ADMIN_DSN, "AGENT_TEST_PG_ADMIN_DSN is not set")
class AsKnownOnDraftTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import psycopg

        cls.database = f"asof_test_{uuid.uuid4().hex[:10]}"
        try:
            layers._build(cls.database)
            with psycopg.connect(ADMIN_DSN, autocommit=True) as admin:
                if admin.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (READER,)).fetchone() is None:
                    admin.execute(f"CREATE ROLE {READER} LOGIN PASSWORD '{layers.PASSWORD}'")
            with layers._connect(cls.database) as owner:
                # A job login as bootstrap would grant it, before the migration copies SELECT to the history.
                owner.execute(f"GRANT USAGE ON SCHEMA silver, public TO {READER}")
                for table in TRACKED + ("accounts",):
                    owner.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON silver.{table} TO {READER}")
                owner.execute(f"GRANT SELECT ON public.accounts TO {READER}")  # the account check reads it
                for path in HELD:
                    owner.execute(path.read_text(encoding="utf-8"))
                    owner.commit()
        except Exception:
            layers._drop(cls.database, roles=(READER,))
            raise
        cls.url = layers._url(cls.database, layers.OWNER)
        cls.raw = tempfile.TemporaryDirectory()

    @classmethod
    def tearDownClass(cls):
        cls.raw.cleanup()
        layers._drop(cls.database, roles=(READER,))

    def setUp(self):
        self.environment = patch.dict(os.environ, {"IBKR_RUN_MIGRATIONS": "false", "IBKR_FLEX_LEGACY_KEY_COMPAT": "",
                                                   "IBKR_FX_EVENT_KEY": ""})
        self.environment.start()

    def tearDown(self):
        self.environment.stop()

    # --- helpers ---

    def _load(self, content: bytes) -> int:
        root = Path(self.raw.name)
        settings = Settings(project_root=root, flex_token="", flex_query_id="", database_path=root / "unused.sqlite3",
                            raw_report_dir=root / "raw", log_dir=root / "logs", timezone_name="Asia/Hong_Kong",
                            daily_lookback_days=14, user_agent="test", request_timeout_seconds=10,
                            max_wait_seconds=20, database_url=self.url, tenant_id=TENANT)
        return ingest_content(settings, content, run_type="asof-test")["run_id"]

    def _owner(self, tenant=TENANT):
        return layers._connect(self.database, tenant=tenant)

    def _set_recording(self, on: bool) -> None:
        with self._owner() as owner:
            owner.execute("UPDATE public.silver_history_settings SET recording = %s, changed_at = now()", (on,))

    def _now(self):
        with self._owner() as owner:
            return owner.execute("SELECT clock_timestamp()").fetchone()[0]

    def _rows(self, sql: str, *params, tenant=TENANT):
        with self._owner(tenant) as owner:
            return owner.execute(sql, params).fetchall()

    def _open_versions_match_tables(self, account: str) -> None:
        """Every table equals its open versions, apart from the run that last saw each row."""
        with self._owner() as owner:
            for table in TRACKED:
                columns = [row[0] for row in owner.execute(
                    "SELECT attname FROM pg_attribute WHERE attrelid = %s::regclass AND attnum > 0 "
                    "AND NOT attisdropped AND attname <> 'last_seen_run_id' ORDER BY attnum",
                    (f"silver.{table}",))]
                listed = ", ".join(columns)
                for left, right in ((f"silver.{table}", f"silver.{table}_as_known(now())"),
                                    (f"silver.{table}_as_known(now())", f"silver.{table}")):
                    extra = owner.execute(f"SELECT count(*) FROM (SELECT {listed} FROM {left} WHERE account_id = %s "
                                          f"EXCEPT ALL SELECT {listed} FROM {right} WHERE account_id = %s) d",
                                          (account, account)).fetchone()[0]
                    self.assertEqual(extra, 0, f"{table}: {left} has rows that {right} lacks")

    # --- the proof ---

    def test_a_recording_off_by_default_adds_no_history(self):
        self.assertEqual(self._rows("SELECT recording FROM public.silver_history_settings"), [(False,)])
        self._load(statement(QUIET_ACCOUNT, "20261014", "20261015", nav={"20261014": "50000.00"},
                             trades=[dict(ORIGINAL_SALE, ibExecID="Q1", tradeID="Q1")]))
        self.assertEqual(self._rows("SELECT count(*) FROM silver.trades WHERE account_id = %s", QUIET_ACCOUNT), [(1,)])
        for table in TRACKED:
            self.assertEqual(self._rows(f"SELECT count(*) FROM silver.{table}_history"), [(0,)], table)

    def test_b_corrections_become_versions_and_the_past_can_be_read_back(self):
        self._set_recording(True)
        try:
            # Pull of 16 Oct: the sale on 15 Oct, a dividend, an FX conversion, NAV.
            first_run = self._load(statement(
                ACCOUNT, "20261014", "20261015", nav={"20261014": "100000.00", "20261015": "100500.00"},
                trades=[ORIGINAL_SALE], cash=[DIVIDEND], fx=[FX_FIRST]))
            between = self._now()
            # Pull of 17 Oct: the window overlaps. IBKR cancels and rebooks the sale, re-sends the
            # FX row with new amounts, restates 15 Oct's NAV, and re-sends the dividend unchanged.
            second_run = self._load(statement(
                ACCOUNT, "20261014", "20261016",
                nav={"20261014": "100000.00", "20261015": "100480.00", "20261016": "100700.00"},
                trades=[ORIGINAL_SALE, CANCELLATION, REBOOKING], cash=[DIVIDEND], fx=[FX_RESTATED]))
        finally:
            self._set_recording(False)

        # Today's readers see exactly what they see now: the loader and the tables are unchanged.
        self.assertEqual(self._rows("SELECT buy_sell, proceeds_text FROM silver.trades WHERE account_id = %s "
                                    "ORDER BY proceeds_text", ACCOUNT),
                         [("SELL (Ca.)", "-5000.00"), ("SELL", "4980.00"), ("SELL", "5000.00")])

        # A cancellation and a rebooking arrive as new rows, each recorded when we learned of it,
        # valid on the trade date.
        versions = self._rows("SELECT buy_sell, valid_date::text, opened_by, recorded_run_id, recorded_to IS NULL "
                              "FROM silver.trades_history WHERE account_id = %s ORDER BY version_id", ACCOUNT)
        self.assertEqual(versions, [("SELL", "2026-10-15", "first", first_run, True),
                                    ("SELL (Ca.)", "2026-10-15", "first", second_run, True),
                                    ("SELL", "2026-10-15", "first", second_run, True)])
        net = "SELECT sum(proceeds_text::numeric)::text FROM silver.trades_as_known(%s) WHERE account_id = %s"
        self.assertEqual(self._rows(net, between, ACCOUNT), [("5000.00",)])
        self.assertEqual(self._rows(net, self._now(), ACCOUNT), [("4980.00",)])

        # A restated NAV is a new version; the old figure is still there to read back.
        nav = self._rows("SELECT total_text, opened_by, closed_by FROM silver.nav_snapshots_history "
                         "WHERE account_id = %s AND snapshot_date = '2026-10-15' ORDER BY version_id", ACCOUNT)
        self.assertEqual(nav, [("100500.00", "first", "restated"), ("100480.00", "restated", None)])
        as_known = "SELECT total_text FROM silver.nav_snapshots_as_known(%s) WHERE account_id = %s " \
                   "AND snapshot_date = '2026-10-15'"
        self.assertEqual(self._rows(as_known, between, ACCOUNT), [("100500.00",)])
        self.assertEqual(self._rows(as_known, self._now(), ACCOUNT), [("100480.00",)])

        # A row re-sent unchanged adds no version (every daily pull re-sends 14 days).
        self.assertEqual(self._rows("SELECT count(*) FROM silver.cash_transactions_history WHERE account_id = %s",
                                    ACCOUNT), [(1,)])
        self.assertEqual(self._rows("SELECT count(*) FROM silver.fx_rates_history WHERE account_id = %s "
                                    "AND rate_date = '2026-10-14'", ACCOUNT), [(1,)])

        # Today's FX duplicate: the re-sent FX row lands beside the original under a new content key.
        self.assertEqual(self._rows("SELECT count(*) FROM silver.fx_transactions WHERE account_id = %s",
                                    ACCOUNT), [(2,)])
        self._open_versions_match_tables(ACCOUNT)

        # The FX step: one row per FX event. The older report is merged into the newer one; both stay
        # in the history. (The planned loader does the same update for each later re-send.)
        before_fix = self._now()
        with self._owner() as owner:
            rows = owner.execute("SELECT * FROM silver.fx_transactions WHERE account_id = %s "
                                 "ORDER BY last_seen_run_id", (ACCOUNT,))
            names = [column.name for column in rows.description]
            older, newer = [dict(zip(names, row)) for row in rows.fetchall()]
            self.assertEqual(fx_event_key(older), fx_event_key(newer))
            owner.execute("UPDATE public.silver_history_settings SET recording = true")
            owner.execute("SELECT set_config('silver_history.reason', 'merged', true), "
                          "set_config('silver_history.note', 'FX step: same FX event re-sent with new amounts', true)")
            owner.execute("DELETE FROM silver.fx_transactions WHERE account_id = %s AND source_key = %s",
                          (ACCOUNT, older["source_key"]))
            owner.execute("UPDATE silver.fx_transactions SET event_key = %s WHERE account_id = %s AND source_key = %s",
                          (fx_event_key(newer), ACCOUNT, newer["source_key"]))
            owner.execute("UPDATE public.silver_history_settings SET recording = false")
        fx_now = "SELECT proceeds_text, realized_pnl_text FROM silver.fx_transactions_as_known(%s) " \
                 "WHERE account_id = %s ORDER BY proceeds_text"
        self.assertEqual(self._rows(fx_now, self._now(), ACCOUNT), [("128.60", "0.60")])
        self.assertEqual(self._rows(fx_now, before_fix, ACCOUNT), [("128.60", "0.60"), ("128.70", "0.70")])
        self.assertEqual(self._rows(fx_now, between, ACCOUNT), [("128.70", "0.70")])
        closed = self._rows("SELECT proceeds_text, opened_by, closed_by, opened_note IS NOT NULL, "
                            "closed_note IS NOT NULL FROM silver.fx_transactions_history WHERE account_id = %s "
                            "ORDER BY version_id", ACCOUNT)
        self.assertEqual(closed, [("128.70", "first", "merged", False, True),
                                  ("128.60", "first", "restated", False, True),
                                  ("128.60", "restated", None, True, False)])
        self._open_versions_match_tables(ACCOUNT)

        # The history is account-scoped like the tables: another tenant sees none of it.
        self.assertEqual(self._rows("SELECT count(*) FROM silver.trades_history", tenant=OTHER_TENANT), [(0,)])

    def test_c_history_is_append_only(self):
        import psycopg

        with self._owner() as owner:
            owner.execute("INSERT INTO silver.accounts (account_id, first_seen_at, last_seen_at, source_json) "
                          "VALUES ('U9100007', '2026-10-01', '2026-10-01', '{}') ON CONFLICT DO NOTHING")
            run = owner.execute("INSERT INTO ingestion_runs (run_type, started_at, status) "
                                "VALUES ('asof-test', '2026-10-17', 'success') RETURNING run_id").fetchone()[0]
            owner.execute("UPDATE public.silver_history_settings SET recording = true")
            owner.execute("INSERT INTO silver.nav_snapshots (account_id, snapshot_date, currency, asset_class, "
                          "total_text, row_sha256, last_seen_run_id) VALUES ('U9100007', '2026-10-15', 'USD', "
                          "'TOTAL', '10.00', %s, %s)", ("a" * 64, run))
            owner.execute("UPDATE public.silver_history_settings SET recording = false")
        refused = [
            "UPDATE silver.nav_snapshots_history SET total_text = '11.00' WHERE account_id = 'U9100007'",
            "DELETE FROM silver.nav_snapshots_history WHERE account_id = 'U9100007'",
            "TRUNCATE silver.nav_snapshots_history",
        ]
        for sql in refused:
            with self._owner() as owner, self.assertRaises(psycopg.errors.InsufficientPrivilege, msg=sql):
                owner.execute(sql)
        # A job login reads the history but cannot write it; its writes reach it only through the trigger.
        reader = psycopg.connect(layers._dsn(self.database, READER))
        try:
            self.assertEqual(reader.execute("SELECT count(*) FROM silver.nav_snapshots_history "
                                            "WHERE account_id = 'U9100007'").fetchone()[0], 1)
            with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                reader.execute("INSERT INTO silver.nav_snapshots_history SELECT * FROM silver.nav_snapshots_history "
                               "LIMIT 1")
            reader.rollback()
            with self._owner() as owner:
                owner.execute("UPDATE public.silver_history_settings SET recording = true")
            reader.execute("UPDATE silver.nav_snapshots SET total_text = '10.50' WHERE account_id = 'U9100007'")
            reader.commit()
        finally:
            reader.close()
            self._set_recording(False)
        self.assertEqual(self._rows("SELECT total_text, closed_by FROM silver.nav_snapshots_history "
                                    "WHERE account_id = 'U9100007' ORDER BY version_id"),
                         [("10.00", "restated"), ("10.50", None)])
        # TRUNCATE would bypass the row trigger, so it is refused on a tracked table while recording.
        self._set_recording(True)
        try:
            with self._owner() as owner, self.assertRaises(psycopg.errors.InsufficientPrivilege):
                owner.execute("TRUNCATE silver.nav_snapshots")
        finally:
            self._set_recording(False)


if __name__ == "__main__":
    unittest.main()
