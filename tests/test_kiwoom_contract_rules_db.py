"""The Kiwoom quality rules in the silver data contracts, run against a real layered PostgreSQL.

Rows shaped as ibkr_etl.kiwoom_collector (a Korea account in KRW) and ibkr_etl.kiwoom_us_account
(the U.S. account in USD) write them break no rule; each rule counts its own fault and nothing else.
"""
from __future__ import annotations

import unittest
import uuid
from pathlib import Path

from test_medallion_layers_db import ADMIN_DSN, OWNER, _build, _connect, _drop

ROOT = Path(__file__).resolve().parents[1]
CONTRACTS = ROOT / "infra" / "medallion" / "contracts"
KOREA, US, IBKR = "KIWOOM-K1", "KIWOOM-U1", "U9100001"
DAYS = ("2026-10-13", "2026-10-14", "2026-10-15", "2026-10-16")
NEW_RULES = {
    "nav_snapshots": ("kiwoom_total_adds_up", "kiwoom_nav_in_account_currency",
                      "kiwoom_usd_positions_add_up_to_stock", "kiwoom_usd_cash_matches_cash_rows"),
    "trades": ("kiwoom_share_direction",),
    "cash_transactions": ("kiwoom_flow_direction",),
}


def _queries() -> dict[str, str]:
    from ibkr_etl import medallion_contracts as mc

    found = {}
    for table, names in NEW_RULES.items():
        contract = mc.load(CONTRACTS / "silver" / f"{table}.yaml")
        rules = {rule["name"]: rule["query"] for rule in mc.table_object(contract).get("quality", [])}
        for name in names:
            found[name] = rules[name]
    return found


@unittest.skipUnless(ADMIN_DSN, "AGENT_TEST_PG_ADMIN_DSN is not set")
class KiwoomContractRuleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.database = "kiwoom_rules_" + uuid.uuid4().hex[:8]
        _build(cls.database)
        cls.queries = _queries()

    @classmethod
    def tearDownClass(cls):
        _drop(cls.database)

    def setUp(self):
        self.db = _connect(self.database)
        self.addCleanup(self.db.close)
        self.addCleanup(self.db.rollback)  # every test's rows go away
        self.run_id = self.db.execute(
            "INSERT INTO ingestion_runs (run_type, started_at, status) VALUES ('kiwoom_rest_us_history', "
            "'2026-10-17T08:30:00+00:00', 'success') RETURNING run_id").fetchone()[0]
        for account, currency in ((KOREA, "KRW"), (US, "USD"), (IBKR, "USD")):
            self.db.execute(
                "INSERT INTO silver.accounts (account_id, base_currency, account_type, first_seen_at, last_seen_at, "
                "source_json) VALUES (%s, %s, %s, '2026-01-01', '2026-10-17', '{}')",
                (account, currency, None if account == IBKR else "kiwoom"))
        self.serial = 0
        for day in DAYS:
            # Korea: holdings + cash + accruals (the negative selling cost) = Kiwoom's total, to the won.
            self.nav(KOREA, day, "KRW", total="10000000", cash="2000000", stock="8016000", accruals="-16000")
            # U.S.: two holdings that add up to Kiwoom's securities value, USD cash, no selling cost.
            self.nav(US, day, "USD", total="1500.25", cash="300.25", stock="1200", accruals="0")
            self.position(US, day, "A", "700")
            self.position(US, day, "B", "500")
            self.cash(US, day, "USD", "300.25", "1")
        # An IBKR account is never judged by the Kiwoom rules.
        self.nav(IBKR, DAYS[-1], "USD", total="5", cash="1", stock="1", accruals="0")
        for kind, quantity in (("BUY", "10"), ("SELL", "-4"), ("TRANSFER_IN", "1"), ("TRANSFER_OUT", "-1")):
            self.trade(KOREA, kind, quantity)
        for kind, quantity in (("BUY", "3"), ("SELL", "-1"), ("CORPORATE_ACTION_IN", "2"),
                               ("CORPORATE_ACTION_OUT", "-6")):
            self.trade(US, kind, quantity)
        for kind, amount in (("DEPOSIT", "1000"), ("WITHDRAWAL", "-50"), ("DIVIDEND", "3.5"), ("INTEREST", "1"),
                             ("TAX", "-0.53"), ("FEE", "-1"), ("FX_EXCHANGE", "-200")):
            self.flow(KOREA, kind, amount)
        for kind, amount in (("DEPOSIT", "250"), ("WITHDRAWAL", "-20"), ("CORPORATE_ACTION_CASH", "12.4")):
            self.flow(US, kind, amount)
        self.trade(IBKR, "SELL", "5")  # IBKR's own conventions are not these rules' business
        self.flow(IBKR, "Deposits/Withdrawals", "-5")

    def _key(self) -> str:
        self.serial += 1
        return f"{self.serial:064x}"

    def nav(self, account, day, currency, *, total, cash, stock, accruals):
        self.db.execute(
            "INSERT INTO silver.nav_snapshots (account_id, snapshot_date, currency, asset_class, total_text, "
            "cash_text, stock_text, accruals_text, row_sha256, last_seen_run_id) VALUES "
            "(%s, %s, %s, 'TOTAL', %s, %s, %s, %s, %s, %s)",
            (account, day, currency, total, cash, stock, accruals, self._key(), self.run_id))

    def position(self, account, day, conid, value):
        self.db.execute(
            "INSERT INTO silver.position_snapshots (account_id, snapshot_date, conid, currency, market_value_text, "
            "row_sha256, last_seen_run_id) VALUES (%s, %s, %s, 'USD', %s, %s, %s)",
            (account, day, conid, value, self._key(), self.run_id))

    def cash(self, account, day, currency, amount, rate):
        self.db.execute(
            "INSERT INTO silver.cash_snapshots (account_id, snapshot_date, currency, ending_cash_text, "
            "settled_cash_text, fx_rate_to_base_text, row_sha256, last_seen_run_id) VALUES "
            "(%s, %s, %s, %s, %s, %s, %s, %s)", (account, day, currency, amount, amount, rate, self._key(), self.run_id))

    def trade(self, account, kind, quantity):
        key = self._key()
        self.db.execute(
            "INSERT INTO silver.trades (account_id, source_key, trade_id, trade_date, buy_sell, quantity_text, "
            "row_sha256, last_seen_run_id) VALUES (%s, %s, %s, '2026-10-14', %s, %s, %s, %s)",
            (account, key[-40:], key[-40:], kind, quantity, key, self.run_id))

    def flow(self, account, kind, amount):
        key = self._key()
        self.db.execute(
            "INSERT INTO silver.cash_transactions (account_id, source_key, transaction_id, effective_date, "
            "transaction_type, amount_text, row_sha256, last_seen_run_id) VALUES "
            "(%s, %s, %s, '2026-10-14', %s, %s, %s, %s)", (account, key[-40:], key[-40:], kind, amount, key, self.run_id))

    def counts(self) -> dict[str, int]:
        return {name: self.db.execute(query).fetchone()[0] for name, query in self.queries.items()}

    def broken(self) -> dict[str, int]:
        return {name: count for name, count in self.counts().items() if count}

    def test_rows_as_the_collectors_write_them_break_no_rule(self):
        self.assertEqual(self.broken(), {})

    def test_a_total_that_does_not_add_up(self):
        # To the won on a KRW row, to the cent on a USD row.
        self.db.execute("UPDATE silver.nav_snapshots SET total_text = '10000001' WHERE account_id = %s "
                        "AND snapshot_date = %s", (KOREA, DAYS[0]))
        self.assertEqual(self.broken(), {})
        self.db.execute("UPDATE silver.nav_snapshots SET total_text = '10000002' WHERE account_id = %s "
                        "AND snapshot_date = %s", (KOREA, DAYS[0]))
        self.db.execute("UPDATE silver.nav_snapshots SET total_text = '1500.27' WHERE account_id = %s "
                        "AND snapshot_date = %s", (US, DAYS[1]))
        self.assertEqual(self.broken(), {"kiwoom_total_adds_up": 2})

    def test_a_row_in_the_other_currency(self):
        self.nav(KOREA, "2026-10-12", "USD", total="1", cash="1", stock="0", accruals="0")
        self.assertEqual(self.broken(), {"kiwoom_nav_in_account_currency": 1})

    def test_us_positions_that_miss_kiwooms_value_except_the_two_newest_days(self):
        for day in DAYS:
            self.db.execute("UPDATE silver.position_snapshots SET market_value_text = '500.03' WHERE account_id = %s "
                            "AND snapshot_date = %s AND conid = 'B'", (US, day))
        # 3 cents over two positions is beyond a cent a position on 13 and 14 Oct; the latest two
        # sessions are still provisional at Kiwoom, so 15 and 16 Oct are left out.
        self.assertEqual(self.broken(), {"kiwoom_usd_positions_add_up_to_stock": 2})
        # A day stored without positions does not add up either.
        self.db.execute("DELETE FROM silver.position_snapshots WHERE account_id = %s AND snapshot_date = %s",
                        (US, DAYS[0]))
        self.assertEqual(self.broken(), {"kiwoom_usd_positions_add_up_to_stock": 2})

    def test_us_cash_rows_that_miss_the_nav_cash(self):
        # Won left on the account counts at its rate into USD: 13,800 KRW at 1/1380 is 10 USD.
        self.cash(US, DAYS[0], "KRW", "13800", "0.000724637681")
        self.assertEqual(self.broken(), {"kiwoom_usd_cash_matches_cash_rows": 1})
        self.db.execute("UPDATE silver.nav_snapshots SET cash_text = '310.25', total_text = '1510.25' "
                        "WHERE account_id = %s AND snapshot_date = %s", (US, DAYS[0]))
        self.assertEqual(self.broken(), {})

    def test_shares_moving_the_wrong_way(self):
        self.trade(KOREA, "SELL", "4")
        self.trade(US, "CORPORATE_ACTION_IN", "-2")
        self.trade(US, "SPLIT", "1")  # not a kind the collectors write
        self.assertEqual(self.broken(), {"kiwoom_share_direction": 3})

    def test_flows_with_the_wrong_sign(self):
        self.flow(US, "DEPOSIT", "-250")
        self.flow(KOREA, "FEE", "1")
        self.flow(KOREA, "FX_EXCHANGE", "200")  # an exchange carries Kiwoom's own sign
        self.assertEqual(self.broken(), {"kiwoom_flow_direction": 2})


if __name__ == "__main__":
    unittest.main()
