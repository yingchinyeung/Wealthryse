"""The two Kiwoom accounts through quant_snapshots.load_inputs on a real PostgreSQL (docs/QUANT_ANALYTICS.md,
"Kiwoom accounts").

Needs a disposable server: set AGENT_TEST_PG_ADMIN_DSN to a superuser DSN (CI starts one). Skipped
otherwise. Builds its own database with every migration and writes three made-up accounts as the
loaders leave them: Korea iStrategy (won, account_type kiwoom), U.S. iStrategy (dollars, kiwoom)
and an IBKR account. Checks what load_inputs reads for each: the risk-free rate the Risk page uses
(Korea's CD rate for won), the U.S. account's won-to-dollar top-ups as flows, Kiwoom trades held
back from trade-by-trade analysis, and the factor model each account gets. Made-up ids only.
"""
from __future__ import annotations

import os
import unittest
import uuid
from pathlib import Path

import test_medallion_layers_db as layers
from ibkr_etl import quant_snapshots as qs

ADMIN_DSN = os.environ.get("AGENT_TEST_PG_ADMIN_DSN", "")
TENANT = layers.TENANT
KOREA, US_ISTRATEGY, IBKR = "KIWOOM-K0000001", "KIWOOM-U0000002", "U9100001"
AS_OF = "2026-10-16"
DAYS = ["2026-10-12", "2026-10-13", "2026-10-14", "2026-10-15", "2026-10-16"]


def _write(owner) -> None:
    owner.execute("SELECT set_config('app.tenant_id', %s, false)", (TENANT,))
    run = owner.execute("INSERT INTO ingestion_runs (run_type, started_at, completed_at, status) VALUES "
                        "('synthetic', %s, %s, 'success') RETURNING run_id", (AS_OF, AS_OF)).fetchone()[0]
    accounts = {  # account: (currency, account_type, bare account number, benchmark, holding, price)
        KOREA: ("KRW", "kiwoom", "60000001", "KS200", "KRX:005930", 70000.0),
        US_ISTRATEGY: ("USD", "kiwoom", "60000002", "GSPC", "US:AAPL", 230.0),
        IBKR: ("USD", "Individual", None, "GSPC", "265598", 230.0),
    }
    for account, (currency, kind, number, benchmark, conid, price) in accounts.items():
        owner.execute("INSERT INTO silver.accounts (account_id, base_currency, account_type, first_seen_at, "
                      "last_seen_at, source_json) VALUES (%s, %s, %s, '2025-03-03', %s, '{}')",
                      (account, currency, kind, AS_OF))
        owner.execute("INSERT INTO platform_subjects (subject_id, subject_type, display_name, source_account_id, "
                      "base_currency, reporting_currency, history_start_date, lifecycle_status) VALUES (%s, "
                      "'physical_account', 'Synthetic', %s, %s, %s, '2025-03-03', 'active')",
                      (account, number, currency, currency))
        owner.execute("INSERT INTO portfolio_benchmark_mapping (subject_id, benchmark_id, mapping_type, "
                      "effective_from, reporting_currency, created_at) VALUES (%s, %s, 'primary', '2025-03-03', "
                      "%s, '2026-01-01')", (account, benchmark, currency))
        owner.execute("INSERT INTO silver.instruments (conid, symbol, asset_category, currency, first_seen_at, "
                      "last_seen_at, source_json) VALUES (%s, %s, 'STK', %s, '2025-03-03', %s, '{}') "
                      "ON CONFLICT DO NOTHING", (conid, conid.split(":")[-1], currency, AS_OF))
        with owner.cursor() as cursor:
            cursor.executemany(
                "INSERT INTO gold.performance_comparison_daily (subject_id, benchmark_id, observation_date, "
                "reporting_currency, portfolio_value, benchmark_level, portfolio_return, benchmark_return, "
                "calculation_version, quality_status, generated_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, "
                "'benchmark-comparison-v1', 'pass', '2026-10-16T09:00:00+00:00')",
                [(account, benchmark, day, 100 * price, 100.0 + i, 100 * price, 0.001 * i, 0.001)
                 for i, day in enumerate(DAYS)])
            cursor.executemany(
                "INSERT INTO silver.position_snapshots (account_id, snapshot_date, conid, currency, quantity_text, "
                "mark_price_text, market_value_text, row_sha256, last_seen_run_id) VALUES (%s, %s, %s, %s, '100', "
                "%s, %s, 'p', %s)", [(account, day, conid, currency, str(price), str(100 * price), run)
                                     for day in DAYS])
            cursor.executemany(
                "INSERT INTO silver.nav_snapshots (account_id, snapshot_date, currency, total_text, row_sha256, "
                "last_seen_run_id) VALUES (%s, %s, %s, %s, 'n', %s)",
                [(account, day, currency, str(110 * price), run) for day in DAYS])
        # One buy each; Kiwoom gives no commission and a net price.
        owner.execute("INSERT INTO silver.trades (account_id, source_key, execution_id, trade_id, conid, trade_date, "
                      "trade_time, buy_sell, quantity_text, trade_price_text, commission_text, commission_currency, "
                      "currency, row_sha256, last_seen_run_id) VALUES (%s, %s, %s, %s, %s, '2026-10-13', '10:00:00', "
                      "'BUY', '10', %s, %s, %s, %s, 't', %s)",
                      (account, f"{account}-t1", f"{account}-e1", f"{account}-t1", conid, str(price),
                       None if kind == "kiwoom" else "-1.0", None if kind == "kiwoom" else currency, currency, run))
    cash = [  # account, date, type, description, amount, currency
        (US_ISTRATEGY, "2026-10-13", "DEPOSIT", "KRW to USD", "3000", "USD"),
        (US_ISTRATEGY, "2026-10-14", "WITHDRAWAL", "USD to KRW", "-500", "USD"),
        (US_ISTRATEGY, "2026-10-15", "DIVIDEND", "AAPL dividend", "4.2", "USD"),
        (KOREA, "2026-10-13", "DEPOSIT", "Transfer in", "1000000", "KRW"),
        (IBKR, "2026-10-13", "Deposits/Withdrawals", "Electronic fund transfer", "2000", "USD"),
        (IBKR, "2026-10-14", "DEPOSIT", "Not an IBKR label", "1", "USD"),
    ]
    with owner.cursor() as cursor:
        cursor.executemany(
            "INSERT INTO silver.cash_transactions (account_id, source_key, effective_date, transaction_type, "
            "description, amount_text, currency, row_sha256, last_seen_run_id) VALUES (%s, %s, %s, %s, %s, %s, %s, "
            "'c', %s)", [(account, f"{account}-{day}-{kind}", day, kind, text, amount, currency, run)
                         for account, day, kind, text, amount, currency in cash])
        cursor.executemany(
            "INSERT INTO silver.reference_fx_rates (rate_date, from_currency, to_currency, rate, provider, "
            "fetched_at, quality_status) VALUES (%s, 'USD', 'KRW', %s, 'synthetic', %s, 'warn')",
            [(day, 1380.0 + i, AS_OF) for i, day in enumerate(DAYS)])
        cursor.executemany(
            "INSERT INTO silver.risk_free_rates (rate_date, series_id, currency, tenor, annual_rate, "
            "day_count_convention, provider, fetched_at, quality_status) VALUES (%s, %s, %s, '3M', %s, "
            "'ACT/365', 'synthetic', %s, 'pass')",
            [(AS_OF, "UST-PAR-3M", "USD", 0.04, AS_OF), ("2026-09-01", "OECD-KR-CD-3M", "KRW", 0.0265, AS_OF)])
    owner.commit()


@unittest.skipUnless(ADMIN_DSN, "AGENT_TEST_PG_ADMIN_DSN is not set")
class KiwoomLoadInputsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.database = f"quant_kiwoom_{uuid.uuid4().hex[:10]}"
        try:
            layers._build(cls.database)
            with layers._connect(cls.database) as owner:
                _write(owner)
            from ibkr_etl.db import connect

            connection = connect(Path("/tmp/unused.sqlite3"), layers._url(cls.database, layers.OWNER), TENANT)
            try:
                cls.data = {subject: qs.load_inputs(connection, tenant_id=TENANT, subject_id=subject, as_of=AS_OF)
                            for subject in (KOREA, US_ISTRATEGY, IBKR)}
            finally:
                connection.close()
        except Exception:
            layers._drop(cls.database)
            raise

    @classmethod
    def tearDownClass(cls):
        layers._drop(cls.database)

    def test_each_account_takes_the_risk_pages_risk_free_rate(self):
        self.assertEqual(self.data[KOREA]["reporting_currency"], "KRW")
        self.assertEqual(self.data[KOREA]["risk_free"], {"rate_date": "2026-09-01", "annual_rate": 0.0265,
                                                         "currency": "KRW", "series_id": "OECD-KR-CD-3M"})
        for subject in (US_ISTRATEGY, IBKR):
            with self.subTest(subject=subject):
                self.assertEqual((self.data[subject]["risk_free"]["currency"],
                                  self.data[subject]["risk_free"]["series_id"]), ("USD", "UST-PAR-3M"))
        skill = qs.prepare_inputs(self.data[KOREA])["skill"]
        self.assertEqual((skill["risk_free_currency"], skill["risk_free_series"]), ("KRW", "OECD-KR-CD-3M"))
        self.assertAlmostEqual(skill["risk_free_annual_rate"], 0.0265)

    def test_the_us_accounts_top_ups_are_flows_and_koreas_cash_rows_stay_as_main_has_them(self):
        self.assertEqual(self.data[US_ISTRATEGY]["flows"], [
            {"date": "2026-10-13", "amount": "3000", "currency": "USD"},
            {"date": "2026-10-14", "amount": "-500", "currency": "USD"}])
        self.assertEqual(self.data[KOREA]["flows"], [])
        # An IBKR account reads its own labels: a bare DEPOSIT type is not one of them.
        self.assertEqual(self.data[IBKR]["flows"], [{"date": "2026-10-13", "amount": "2000", "currency": "USD"}])

    def test_kiwoom_trades_are_read_but_held_back_with_the_reason(self):
        for subject in (KOREA, US_ISTRATEGY):
            with self.subTest(subject=subject):
                data = self.data[subject]
                self.assertEqual(data["trade_detail_missing"], qs.TRADE_DETAIL_MISSING["kiwoom"])
                self.assertEqual(len(data["trades"]), 1)
                self.assertIsNone(data["trades"][0]["commission"])
                prepared = qs.prepare_inputs(data)
                self.assertEqual((prepared["execution"]["trades"], prepared["execution"]["no_trades"]),
                                 ([], qs.TRADE_DETAIL_MISSING["kiwoom"]))
                self.assertEqual(prepared["decisions"]["no_trades"], qs.TRADE_DETAIL_MISSING["kiwoom"])
        self.assertNotIn("trade_detail_missing", self.data[IBKR])
        self.assertEqual(len(qs.prepare_inputs(self.data[IBKR])["execution"]["trades"]), 1)

    def test_each_account_gets_its_factor_model_and_the_won_rates(self):
        self.assertEqual(qs.factor_model_for(self.data[KOREA], AS_OF), qs.KOREA_MARKET_MODEL)
        self.assertEqual(qs.factor_model_for(self.data[US_ISTRATEGY], AS_OF), "us")
        self.assertEqual(qs.factor_model_for(self.data[IBKR], AS_OF), "us")
        self.assertEqual({(row["from"], row["to"]) for row in self.data[KOREA]["fx"]}, {("USD", "KRW")})
        self.assertEqual([row["conid"] for row in self.data[KOREA]["positions"] if row["date"] == AS_OF],
                         ["KRX:005930"])

    def test_korea_holding_only_won_shows_no_currency_effect(self):
        payload = qs.build_currency(qs.prepare_inputs(self.data[KOREA])["currency"])
        self.assertEqual(payload["inputs"]["base_currency"], "KRW")
        self.assertTrue(payload["inputs"].get("single_currency"), payload["inputs"])


if __name__ == "__main__":
    unittest.main()
