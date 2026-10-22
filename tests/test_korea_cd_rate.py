"""Korea's won risk-free rate from Bank of Korea ECOS, on made-up answers only."""
from __future__ import annotations

import json
import sqlite3
import unittest
from datetime import date

from ibkr_etl import korea_cd_rate as cd

ITEMS = {"StatisticItemList": {"list_total_count": 3, "row": [
    {"STAT_CODE": "817Y002", "ITEM_CODE": "010101000", "ITEM_NAME": "콜금리(1일, 전체거래)", "CYCLE": "D"},
    {"STAT_CODE": "817Y002", "ITEM_CODE": "010502000", "ITEM_NAME": "CD(91일)", "CYCLE": "D"},
    {"STAT_CODE": "817Y002", "ITEM_CODE": "010200000", "ITEM_NAME": "국고채(3년)", "CYCLE": "D"},
]}}


def search(rows):
    return {"StatisticSearch": {"list_total_count": len(rows), "row": [
        {"STAT_CODE": "817Y002", "ITEM_CODE1": code, "ITEM_NAME1": "CD(91일)", "UNIT_NAME": "연%",
         "TIME": day, "DATA_VALUE": value} for code, day, value in rows]}}


class FakeEcos:
    def __init__(self, answer):
        self.answer = answer
        self.urls = []

    def __call__(self, url, timeout=30):
        self.urls.append(url)
        body = ITEMS if "/StatisticItemList/" in url else self.answer
        return json.dumps(body).encode()


def database():
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.execute("""CREATE TABLE risk_free_rates(tenant_id TEXT DEFAULT 't', rate_date TEXT, series_id TEXT,
        currency TEXT, tenor TEXT, annual_rate REAL, day_count_convention TEXT, provider TEXT, fetched_at TEXT,
        quality_status TEXT, PRIMARY KEY (tenant_id, rate_date, series_id))""")
    return connection


class CdRateTests(unittest.TestCase):
    def test_finds_the_cd_item_by_name_and_loads_percent_as_a_fraction(self):
        ecos = FakeEcos(search([("010502000", "20261019", "2.54"), ("010502000", "20261020", "2.55")]))
        connection = database()
        result = cd.refresh_cd_rates(connection, key="secret-key", start_date=date(2026, 10, 19),
                                     end_date=date(2026, 10, 20), get=ecos)
        self.assertEqual((result["item_code"], result["observations_loaded"]), ("010502000", 2))
        rows = [dict(r) for r in connection.execute(
            "SELECT rate_date, series_id, currency, tenor, annual_rate FROM risk_free_rates ORDER BY rate_date")]
        self.assertEqual(rows[1], {"rate_date": "2026-10-20", "series_id": "BOK-CD-91D", "currency": "KRW",
                                   "tenor": "91-day", "annual_rate": 0.0255})
        self.assertIn("/817Y002/D/20261019/20261020/010502000", ecos.urls[1])

    def test_rows_for_another_item_stop_the_load(self):
        ecos = FakeEcos(search([("010200000", "20261020", "2.90")]))
        with self.assertRaisesRegex(RuntimeError, "not 010502000"):
            cd.refresh_cd_rates(database(), key="k", start_date=date(2026, 10, 20), end_date=date(2026, 10, 20),
                                get=ecos)

    def test_an_item_list_without_exactly_one_cd_stops(self):
        with self.assertRaisesRegex(RuntimeError, "found 0"):
            cd.cd_item_code([{"ITEM_CODE": "1", "ITEM_NAME": "콜금리", "CYCLE": "D"}])

    def test_an_error_answer_and_an_empty_period_are_reported(self):
        with self.assertRaisesRegex(RuntimeError, "INFO-100"):
            cd.fetch_cd_rates("k", date(2026, 10, 1), date(2026, 10, 2),
                              get=FakeEcos({"RESULT": {"CODE": "INFO-100", "MESSAGE": "인증키가 유효하지 않습니다."}}))
        with self.assertRaisesRegex(RuntimeError, "No CD 91-day"):
            cd.fetch_cd_rates("k", date(2026, 10, 1), date(2026, 10, 2),
                              get=FakeEcos({"RESULT": {"CODE": "INFO-200", "MESSAGE": "해당하는 데이터가 없습니다."}}))

    def test_out_of_range_values_and_a_missing_key_are_refused(self):
        with self.assertRaisesRegex(ValueError, "validation range"):
            cd.parse_cd_rows(search([("X", "20261020", "45")])["StatisticSearch"]["row"], "X")
        with self.assertRaisesRegex(RuntimeError, cd.KEY_ENV):
            cd.fetch_cd_rates("", date(2026, 10, 1), date(2026, 10, 2), get=FakeEcos({}))

    def test_the_key_never_reaches_a_raw_capture(self):
        url = cd._url("StatisticSearch", "my/secret key", "817Y002")
        self.assertNotIn("secret", cd._redacted(url, "my/secret key"))


class RiskSnapshotRateTests(unittest.TestCase):
    def test_a_won_account_takes_the_won_rate_and_falls_back_to_treasury_until_it_loads(self):
        from ibkr_etl.risk_snapshots import risk_free_rate

        connection = database()
        connection.execute("INSERT INTO risk_free_rates VALUES ('t', '2026-10-20', 'UST-PAR-3M', 'USD', '3-month', "
                           "0.04, 'x', 'p', 'f', 'pass')")
        pick = lambda currency: dict(risk_free_rate(connection, tenant_id="t", reporting_currency=currency,  # noqa: E731
                                                    as_of="2026-10-21"))["currency"]
        self.assertEqual(pick("KRW"), "USD")
        connection.execute("INSERT INTO risk_free_rates VALUES ('t', '2026-10-19', 'BOK-CD-91D', 'KRW', '91-day', "
                           "0.0254, 'x', 'p', 'f', 'pass')")
        self.assertEqual(pick("KRW"), "KRW")      # its own rate, even a day older than the Treasury's
        self.assertEqual(pick("USD"), "USD")
        self.assertEqual(pick("HKD"), "USD")      # the HKD composites use HIBOR on their own path

    def test_a_won_rate_more_than_100_days_old_is_not_used(self):
        from ibkr_etl.risk_snapshots import risk_free_rate

        connection = database()
        connection.execute("INSERT INTO risk_free_rates VALUES ('t', '2026-10-20', 'UST-PAR-3M', 'USD', '3-month', "
                           "0.04, 'x', 'p', 'f', 'pass')")
        connection.execute("INSERT INTO risk_free_rates VALUES ('t', '2026-06-30', 'OECD-KR-CD-3M', 'KRW', "
                           "'3-month', 0.026, 'x', 'p', 'f', 'pass')")
        rate = lambda as_of: dict(risk_free_rate(connection, tenant_id="t", reporting_currency="KRW",  # noqa: E731
                                                 as_of=as_of))["currency"]
        self.assertEqual(rate("2026-09-30"), "KRW")
        self.assertEqual(rate("2026-10-21"), "USD")


FRED_CSV = b"observation_date,IR3TCD01KRM156N\n2026-06-01,2.61\n2026-07-01,.\n2026-08-01,2.55\n2026-12-01,2.50\n"


class MonthlyCdRateTests(unittest.TestCase):
    def test_each_month_is_stored_on_its_last_day_and_gaps_are_skipped(self):
        urls = []

        def get(url, timeout=30):
            urls.append(url)
            return FRED_CSV

        connection = database()
        result = cd.refresh_monthly_cd_rates(connection, start_date=date(2026, 1, 1), end_date=date(2026, 10, 21),
                                             get=get)
        self.assertEqual(result["observations_loaded"], 2)   # December is after the end date
        rows = [tuple(r) for r in connection.execute(
            "SELECT rate_date, series_id, currency, annual_rate FROM risk_free_rates ORDER BY rate_date")]
        self.assertEqual(rows, [("2026-06-30", "OECD-KR-CD-3M", "KRW", 0.0261),
                                ("2026-08-31", "OECD-KR-CD-3M", "KRW", 0.0255)])
        self.assertEqual(cd.parse_monthly_csv(FRED_CSV)[-1].rate_date, "2026-12-31")
        self.assertIn(cd.FRED_SERIES, urls[0])

    def test_a_slow_fred_answer_is_retried_before_the_load_fails(self):
        calls, waits = [], []

        def flaky(url, timeout=30):
            calls.append(timeout)
            if len(calls) < 3:
                raise TimeoutError("The read operation timed out")
            return FRED_CSV

        result = cd.refresh_monthly_cd_rates(database(), start_date=date(2026, 1, 1), end_date=date(2026, 10, 21),
                                             get=flaky, sleep=waits.append)
        self.assertEqual(result["observations_loaded"], 2)
        self.assertEqual((len(calls), waits), (3, [5, 20]))

        def down(url, timeout=30):
            raise TimeoutError("The read operation timed out")

        connection = database()
        with self.assertRaises(TimeoutError):
            cd.refresh_monthly_cd_rates(connection, start_date=date(2026, 1, 1), get=down, sleep=lambda _: None)
        self.assertEqual(connection.execute("SELECT COUNT(*) FROM risk_free_rates").fetchone()[0], 0)

    def test_a_wrong_file_is_not_retried(self):
        calls = []

        def wrong(url, timeout=30):
            calls.append(url)
            return b"DATE,IR3TCD01USM156N\n2026-06-01,5.1\n"

        with self.assertRaises(RuntimeError):
            cd.refresh_monthly_cd_rates(database(), start_date=date(2026, 1, 1), get=wrong, sleep=lambda _: None)
        self.assertEqual(len(calls), 1)

    def test_another_series_or_a_bad_value_stops_the_load(self):
        with self.assertRaisesRegex(RuntimeError, "not IR3TCD01KRM156N"):
            cd.parse_monthly_csv(b"DATE,IR3TCD01USM156N\n2026-06-01,5.1\n")
        with self.assertRaisesRegex(ValueError, "validation range"):
            cd.parse_monthly_csv(b"DATE,IR3TCD01KRM156N\n2026-06-01,61\n")


if __name__ == "__main__":
    unittest.main()
