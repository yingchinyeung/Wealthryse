"""The certified-figures build (scripts/build_certified_figures.py) on a real PostgreSQL.

Needs a disposable server: set AGENT_TEST_PG_ADMIN_DSN to a superuser DSN (CI starts one).
Skipped otherwise. Builds its own database with every migration, one synthetic account with
four months of NAV, two holdings and cash, and throwaway logins from bootstrap_postgres.py.
The build writes as the app login, as the job will; the website handlers it is checked
against read as the web login. Synthetic data only.
"""
from __future__ import annotations

import contextlib
import functools
import importlib.util
import io
import math
import os
import random
import unittest
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import test_medallion_layers_db as layers
from ibkr_etl import certified_build as build
from ibkr_etl import certified_pages
from ibkr_etl import certified_figures as cf

ADMIN_DSN = os.environ.get("AGENT_TEST_PG_ADMIN_DSN", "")
ROOT = Path(__file__).resolve().parents[1]
TENANT = layers.TENANT
SUBJECT = "U9300001"
APP, WEB, APPROVAL = "cbuild_test_app", "cbuild_test_web", "cbuild_test_approval"
ROLES = (APP, WEB, APPROVAL)
FIRST, LAST = date(2026, 6, 1), date(2026, 10, 16)  # LAST is a Friday
NOW = datetime(2026, 10, 19, 1, 5, tzinfo=timezone.utc)  # 09:05 on Monday in Hong Kong: Friday's data is due
DEPOSIT_DAY = "2026-08-03"


def setUpModule():
    layers.setUpModule()


def _business_days(first: date, last: date) -> list[str]:
    days, day = [], first
    while day <= last:
        if day.weekday() < 5:
            days.append(day.isoformat())
        day += timedelta(days=1)
    return days


def _money(value: float) -> str:
    return f"{value:.2f}"


def _add_days(owner, run: int, days: list[str], rng: random.Random, start_nav: float) -> float:
    """NAV, two holdings (one in EUR) and cash for each day; returns the last NAV."""
    nav = start_nav
    for day in days:
        nav *= 1 + rng.gauss(0.0004, 0.009)
        if day == DEPOSIT_DAY:
            nav += 5000
        stock = nav * 0.8
        cash = nav - stock
        usd_value, eur_value = stock * 0.6, stock * 0.4
        eur_rate = 1.08 + rng.uniform(-0.01, 0.01)
        owner.execute(
            "INSERT INTO nav_snapshots (account_id, snapshot_date, currency, asset_class, total_text, cash_text, "
            "stock_text, row_sha256, last_seen_run_id) VALUES (%s, %s, 'USD', 'TOTAL', %s, %s, %s, md5(%s), %s)",
            (SUBJECT, day, _money(nav), _money(cash), _money(stock), day, run))
        for conid, currency, value in (("1001", "USD", usd_value), ("1002", "EUR", eur_value / eur_rate)):
            price = 100 + rng.uniform(-5, 5)
            quantity = round(value / price, 4)
            owner.execute(
                "INSERT INTO position_snapshots (account_id, snapshot_date, conid, currency, quantity_text, "
                "cost_basis_text, mark_price_text, market_value_text, unrealized_pnl_text, row_sha256, "
                "last_seen_run_id) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, md5(%s), %s)",
                (SUBJECT, day, conid, currency, f"{quantity:.4f}", _money(quantity * 95), _money(price),
                 _money(quantity * price), _money(quantity * (price - 95)), day + conid, run))
        owner.execute("INSERT INTO fx_rates (account_id, rate_date, from_currency, to_currency, rate_text, row_sha256, "
                      "last_seen_run_id) VALUES (%s, %s, 'EUR', 'USD', %s, md5(%s), %s)",
                      (SUBJECT, day, f"{eur_rate:.6f}", day, run))
        for currency, amount in (("USD", cash), ("BASE_SUMMARY", cash)):
            owner.execute(
                "INSERT INTO cash_snapshots (account_id, snapshot_date, currency, ending_cash_text, settled_cash_text, "
                "fx_rate_to_base_text, row_sha256, last_seen_run_id) VALUES (%s, %s, %s, %s, %s, '1', md5(%s), %s)",
                (SUBJECT, day, currency, _money(amount), _money(amount), day + currency, run))
    return nav


def _seed(owner) -> float:
    rng = random.Random(20261016)
    owner.execute("SELECT set_config('app.tenant_id', %s, false)", (TENANT,))
    run = owner.execute(
        "INSERT INTO ingestion_runs (run_type, started_at, completed_at, status, requested_to) VALUES "
        "('staging-snapshot-import', '2026-10-17', '2026-10-17', 'success', '2026-10-16') RETURNING run_id"
    ).fetchone()[0]
    owner.execute("INSERT INTO accounts (account_id, first_seen_at, last_seen_at, source_json) "
                  "VALUES (%s, '2026-06-01', '2026-10-17', '{}')", (SUBJECT,))
    owner.execute("INSERT INTO platform_subjects (subject_id, subject_type, display_name, base_currency, "
                  "reporting_currency, history_start_date, inception_date, lifecycle_status) VALUES "
                  "(%s, 'physical_account', 'Synthetic account', 'USD', 'USD', %s, %s, 'active')",
                  (SUBJECT, FIRST, FIRST))
    for feature in ("data_ingestion", "website_analytics"):
        owner.execute("INSERT INTO subject_feature_states (subject_id, feature_key, feature_state, reason) "
                      "VALUES (%s, %s, 'active', 'synthetic test subject')", (SUBJECT, feature))
    for conid, symbol, currency in (("1001", "SYNA", "USD"), ("1002", "SYNB", "EUR")):
        owner.execute("INSERT INTO instruments (conid, symbol, description, asset_category, currency, first_seen_at, "
                      "last_seen_at, source_json) VALUES (%s, %s, %s, 'STK', %s, '2026-06-01', '2026-10-17', '{}')",
                      (conid, symbol, f"Synthetic {symbol}", currency))
    owner.execute("INSERT INTO cash_transactions (account_id, source_key, transaction_id, effective_date, settle_date, "
                  "transaction_type, description, amount_text, currency, row_sha256, last_seen_run_id) VALUES "
                  "(%s, 'deposit-1', 'deposit-1', %s, %s, 'Deposits/Withdrawals', 'Synthetic deposit', '5000', "
                  "'USD', md5('deposit-1'), %s)", (SUBJECT, DEPOSIT_DAY, DEPOSIT_DAY, run))
    nav = _add_days(owner, run, _business_days(FIRST, LAST), rng, 100000.0)
    owner.commit()
    return nav


def _derive(database: str) -> None:
    """The benchmark comparison and the risk snapshot for the last day, by the jobs' own code
    with a synthetic index in place of Yahoo Finance."""
    import pandas as pd

    from ibkr_etl.db import connect
    from ibkr_etl.provisional_benchmark import refresh_and_materialize
    from ibkr_etl.risk_snapshots import build_risk_snapshot

    with layers._connect(database) as owner:
        owner.execute("INSERT INTO benchmark_master (benchmark_id, symbol, name, provider, currency, return_convention, "
                      "license_status, created_at, updated_at) VALUES ('SYNIDX', '^SYN', 'Synthetic index', 'test', "
                      "'USD', 'price', 'approved', '2026-01-01', '2026-01-01')")
        owner.execute("INSERT INTO portfolio_benchmark_mapping (subject_id, benchmark_id, mapping_type, effective_from, "
                      "reporting_currency, created_at) VALUES (%s, 'SYNIDX', 'primary', '2026-01-01', 'USD', "
                      "'2026-01-01')", (SUBJECT,))
        owner.commit()
    rng, level, closes = random.Random(5), 5000.0, []
    days = _business_days(FIRST, LAST)
    for _day in days:
        level *= 1 + rng.gauss(0.0003, 0.008)
        closes.append(level)
    frame = pd.DataFrame({"Close": closes, "Adj Close": closes, "Volume": [1] * len(days)},
                         index=pd.DatetimeIndex(days, name="Date"))

    class Ticker:
        def history(self, **_kwargs):
            return frame

    connection = connect(Path("/tmp/unused.sqlite3"), layers._url(database, layers.OWNER), TENANT)
    try:
        refresh_and_materialize(connection, account_id=SUBJECT, start_date=FIRST, end_date=LAST,
                                ticker_factory=lambda _symbol: Ticker())
        build_risk_snapshot(connection, tenant_id=TENANT, subject_id=SUBJECT, as_of=LAST.isoformat())
        connection.commit()
    finally:
        connection.close()


def _bootstrap(database: str) -> None:
    environment = {
        "DATABASE_ADMIN_URL": layers._url(database, layers.OWNER), "PORTFOLIO_TENANT_ID": TENANT,
        "DATABASE_APP_ROLE": APP, "DATABASE_APP_PASSWORD": layers.PASSWORD,
        "DATABASE_WEB_ROLE": WEB, "DATABASE_WEB_PASSWORD": layers.PASSWORD,
        "DATABASE_APPROVAL_ROLE": APPROVAL, "DATABASE_APPROVAL_PASSWORD": layers.PASSWORD,
    }
    with patch.dict(os.environ, environment), contextlib.redirect_stdout(io.StringIO()):
        layers._script("bootstrap_postgres").main([])


def _script():
    spec = importlib.util.spec_from_file_location("build_certified_figures_under_test",
                                                  ROOT / "scripts" / "build_certified_figures.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _BuildDatabase(unittest.TestCase):
    """A database of its own for each test class, with the helpers the tests share."""

    @classmethod
    def setUpClass(cls):
        import psycopg

        cls.database = f"cbuild_test_{uuid.uuid4().hex[:10]}"
        with psycopg.connect(ADMIN_DSN, autocommit=True) as admin:
            for role in ROLES:
                admin.execute(f"DROP ROLE IF EXISTS {role}")
        try:
            layers._build(cls.database)
            with layers._connect(cls.database) as owner:
                cls.last_nav = _seed(owner)
            with contextlib.redirect_stdout(io.StringIO()):
                _derive(cls.database)
            _bootstrap(cls.database)
        except Exception:
            layers._drop(cls.database, ROLES)
            raise
        cls.app_url = layers._url(cls.database, APP)
        cls.web_url = layers._url(cls.database, WEB)
        cls.settings = patch.dict(os.environ, {
            "DATABASE_URL": cls.web_url, "PORTFOLIO_TENANT_ID": TENANT, cf.DIR_SETTING: str(ROOT / "infra" / "medallion"),
            cf.PAGES_SETTING: "", cf.BUILD_SETTING: ""})
        cls.settings.start()
        cf.reload()
        cls.web = build.load_web()
        assert cls.web.TENANT_ID == TENANT

    @classmethod
    def tearDownClass(cls):
        cls.settings.stop()
        cf.reload()
        layers._drop(cls.database, ROLES)

    # -- helpers ------------------------------------------------------------------------------

    def _run(self, **kwargs) -> tuple[int, list[str]]:
        lines: list[str] = []
        code = build.run(database_url=self.app_url, web=self.web, out=lines.append, **{"now": NOW, **kwargs})
        return code, lines

    def _owner(self, sql: str, *args):
        with layers._connect(self.database) as owner:
            return owner.execute(sql, args).fetchall()

    def _builds(self):
        return self._owner("SELECT build_id, status, reason, checks FROM gold.certified_builds ORDER BY started_at, "
                           "finished_at")

    def _published(self) -> dict[tuple, dict]:
        with layers._connect(self.database, WEB) as web:
            return build.published_rows(web, [SUBJECT])

    def _value(self, figure_id: str, as_of: str, *, period: str = "", item: str = "", benchmark_id: str = ""):
        row = self._published()[(SUBJECT, figure_id, period, item, benchmark_id, as_of)]
        return row["value"] if row["value"] is not None else row["value_text"]



@unittest.skipUnless(ADMIN_DSN, "AGENT_TEST_PG_ADMIN_DSN is not set")
class CertifiedBuildTests(_BuildDatabase):
    """In order: off, dry run, first build, nothing new, held builds, a new day."""

    def test_1_off_by_default_it_does_nothing(self):
        script = _script()
        for setting in ({}, {cf.BUILD_SETTING: ""}, {cf.BUILD_SETTING: "off"}, {cf.BUILD_SETTING: "shadow"}):
            with self.subTest(setting=setting), patch.object(build, "run", side_effect=AssertionError("ran")), \
                    patch.object(script.lineage, "run_main", side_effect=AssertionError("lineage")), \
                    contextlib.redirect_stdout(io.StringIO()) as printed:
                self.assertEqual(script.entry([], environ=setting), 0)
            self.assertIn("off", printed.getvalue())
        self.assertEqual(self._builds(), [])

    def test_2_a_dry_run_writes_nothing_and_prints_only_counts(self):
        code, lines = self._run(dry_run=True)
        self.assertEqual(code, 0)
        self.assertEqual(self._builds(), [])
        text = "\n".join(lines)
        self.assertIn("Dry run: nothing written. Would publish.", text)
        self.assertNotIn(SUBJECT, text)
        self.assertNotIn(f"{self.last_nav:.2f}"[:-3], text)

    def test_3_the_build_publishes_the_pages_own_figures(self):
        code, lines = self._run()
        self.assertEqual(code, 0, lines)
        [(build_id, status, reason, checks)] = self._builds()
        self.assertEqual((status, reason), ("published", None), checks)
        self.assertEqual((checks["rule_failure_count"], checks["same_inputs_new_answer_count"],
                          checks["page_disagreements"], checks["reader_errors"]), (0, 0, {}, 0), checks)
        self.assertEqual((checks["passed"], checks["total"], checks["failed"]), (4, 4, []))
        published = self._published()
        days = _business_days(FIRST, LAST)
        self.assertEqual({key[5] for key in published}, set(days))
        figures = {key[1] for key in published}
        for expected in ("overview.nav", "overview.twr", "overview.day_change", "overview.data_freshness",
                         "holdings.market_value", "holdings.largest_holding", "holdings.currency_exposure",
                         "cash.ending_cash", "performance.monthly_return", "stats.sortino_ratio",
                         "stats.maximum_drawdown", "stats.positive_months", "benchmark.correlation",
                         "benchmark.rebased_level", "risk.var_95_daily", "risk.expected_max_drawdown_1y"):
            self.assertIn(expected, figures)
        self.assertTrue(all(cf.figure(figure_id) for figure_id in figures))

        # The stored figures are what the website's handlers give today, called as the website calls them.
        web, user = self.web, build.BUILD_USER
        for as_of in (LAST.isoformat(), "2026-08-31", "2026-08-03"):
            with self.subTest(as_of=as_of):
                summary = web.portfolio_summary(SUBJECT, as_of=as_of, user=user)
                self.assertEqual(self._value("overview.nav", as_of), float(summary["latest_nav"]["total_text"]))
                periods = web.performance_periods_view(SUBJECT, as_of=as_of, start=as_of, end=as_of, user=user)
                for period in periods["periods"]:
                    if period.get("twr") is not None:
                        self.assertEqual(self._value("overview.twr", as_of, period=period["key"]), period["twr"])
                self.assertEqual(self._value("overview.day_change", as_of), periods["custom"]["twr"])
                holdings = web.holdings(SUBJECT, as_of=as_of, user=user)
                for row in holdings["holdings"]:
                    self.assertEqual(self._value("holdings.market_value", as_of, item=str(row["conid"])),
                                     float(row["market_value_text"]))
                    self.assertEqual(self._value("holdings.holding_weight", as_of, item=str(row["conid"])),
                                     row["weight"])
                self.assertEqual(self._value("holdings.largest_holding", as_of),
                                 holdings["concentration"]["largest"]["weight"])
                cash = web.cash_activity(SUBJECT, as_of=as_of, from_date=as_of, to_date=as_of, user=user)
                for balance in cash["balances"]:
                    self.assertEqual(self._value("cash.ending_cash", as_of, item=str(balance["currency"])),
                                     float(balance["ending_cash_text"]))
                analytics = web.portfolio_analytics_view(SUBJECT, as_of=as_of, user=user)
                if analytics.get("available"):
                    stats = analytics["statistics"]
                    for figure_id, name in build.STATISTICS.items():
                        stored = self._published().get((SUBJECT, figure_id, "", "", "", as_of))
                        self.assertEqual(stored["value"], stats.get(name), figure_id)
                    self.assertEqual(self._value("stats.positive_months", as_of), stats["positive_month_ratio"])
                    self.assertEqual(self._value("stats.maximum_drawdown", as_of),
                                     analytics["drawdown"]["maximum_drawdown"])
        comparison = web.benchmark_comparison(SUBJECT, as_of=LAST.isoformat(), user=user)
        for figure_id, name in (("benchmark.correlation", "correlation"), ("benchmark.beta", "beta"),
                                ("benchmark.tracking_error", "tracking_error")):
            self.assertEqual(self._value(figure_id, LAST.isoformat(), benchmark_id="SYNIDX"),
                             comparison["metrics"][name])
        risk = web.risk_analytics(SUBJECT, as_of=LAST.isoformat(), user=user)
        self.assertEqual(risk["cache_status"], "hit")
        for figure_id, name in (("risk.var_95_daily", "var_95_daily"), ("risk.win_rate", "win_rate"),
                                ("risk.expected_max_drawdown_1y", "expected_maximum_drawdown_1y")):
            self.assertEqual(self._value(figure_id, LAST.isoformat()), risk["metrics"][name])
        # Every figure for every date: the readers on the web login give the stored rows exactly.
        with build.own_connections(web):
            subject = build.subjects(web, [SUBJECT])[0][0]
            for as_of in days:
                fresh = build.page_rows(web, subject, as_of, build.READERS, today_hk=build.hong_kong_today(NOW))
                stored = {key: row for key, row in published.items() if key[5] == as_of}
                self.assertEqual(sorted(row.key for row in fresh), sorted(stored), as_of)
                for row in fresh:
                    old = stored[row.key]
                    self.assertEqual(row.compared(), tuple(build._canonical(old[name]) for name in build.COMPARED),
                                     row.key)

    def test_4_a_second_run_with_nothing_new_writes_nothing(self):
        code, lines = self._run()
        self.assertEqual(code, 0)
        self.assertIn("Nothing changed, so no build is written.", lines)
        self.assertEqual(len(self._builds()), 1)

    def test_5_a_changed_answer_from_the_same_inputs_holds_the_build(self):
        before = self._value("stats.sortino_ratio", LAST.isoformat())
        original = self.web.portfolio_analytics_view

        def drifted(*args, **kwargs):
            payload = original(*args, **kwargs)
            if payload.get("statistics"):
                payload["statistics"] = {**payload["statistics"],
                                         "sortino_ratio": payload["statistics"]["sortino_ratio"] + 0.5}
            return payload

        with patch.object(self.web, "portfolio_analytics_view", drifted):
            code, lines = self._run()
        self.assertEqual(code, 0)
        build_id, status, reason, checks = self._builds()[-1]
        self.assertEqual(status, "held")
        self.assertIn("changed with the same inputs and version", reason)
        self.assertIn("stats.sortino_ratio", checks["same_inputs_new_answer"])
        self.assertEqual((checks["passed"], checks["failed"]), (3, ["same_inputs_same_answer"]))
        self.assertEqual(self._value("stats.sortino_ratio", LAST.isoformat()), before)  # readers keep the old one

    def test_6_a_figure_that_breaks_a_contract_rule_holds_the_build(self):
        original = self.web.portfolio_analytics_view

        def broken(*args, **kwargs):
            payload = original(*args, **kwargs)
            if payload.get("drawdown"):
                payload["drawdown"] = {**payload["drawdown"], "maximum_drawdown": 0.25}
            return payload

        with patch.object(self.web, "portfolio_analytics_view", broken):
            self._run()
        _build_id, status, reason, checks = self._builds()[-1]
        self.assertEqual(status, "held")
        self.assertIn("broke a contract rule", reason)
        self.assertEqual(checks["rule_failures"], ["stats.maximum_drawdown"])
        self.assertLessEqual(self._value("stats.maximum_drawdown", LAST.isoformat()), 0)

    def test_7_nothing_is_built_before_the_inputs_are_in(self):
        later = datetime(2026, 10, 21, 1, 5, tzinfo=timezone.utc)  # Tuesday's data is not in
        count = len(self._builds())
        code, lines = self._run(now=later)
        self.assertEqual(code, 0)
        self.assertIn("Inputs for 2026-10-20 are not in yet, so nothing new is built or published.", lines)
        self.assertEqual(len(self._builds()), count)

    def test_8_a_new_day_is_built_by_the_job_with_its_lineage_run(self):
        with layers._connect(self.database) as owner:
            run = owner.execute("SELECT max(run_id) FROM ingestion_runs").fetchone()[0]
            _add_days(owner, run, ["2026-10-19"], random.Random(1019), self.last_nav)
            owner.commit()
        count = len(self._builds())
        later = datetime(2026, 10, 20, 1, 5, tzinfo=timezone.utc)
        script = _script()
        settings = {cf.BUILD_SETTING: "on", "DATABASE_URL": self.app_url, "LINEAGE_RECORDING": "true"}
        with patch.dict(os.environ, settings), \
                patch.object(build, "run", functools.partial(build.run, now=later, web=self.web)), \
                contextlib.redirect_stdout(io.StringIO()) as printed:
            self.assertEqual(script.entry([]), 0)
        self.assertEqual(os.environ[cf.PAGES_SETTING], "")
        self.assertIn("published", printed.getvalue())
        builds = self._owner("SELECT build_id, status, lineage_run_id FROM gold.certified_builds ORDER BY started_at")
        self.assertEqual(len(builds), count + 1)
        build_id, status, run_id = builds[-1]
        self.assertEqual(status, "published")
        self.assertIsNotNone(run_id)
        written = self._owner("SELECT DISTINCT as_of_date::text, lineage_run_id = %s FROM gold.certified_figures "
                              "WHERE build_id = %s", run_id, build_id)
        self.assertEqual(written, [("2026-10-19", True)])
        job = self._owner("SELECT DISTINCT job_name FROM lineage_events WHERE run_id = %s", run_id)
        self.assertEqual(job, [(script.JOB_NAME,)])
        self.assertTrue(math.isfinite(self._value("overview.nav", "2026-10-19")))



class _Clock:
    """A clock that moves one second each time it is read."""

    def __init__(self):
        self.now = 0.0

    def __call__(self) -> float:
        self.now += 1.0
        return self.now


@unittest.skipUnless(ADMIN_DSN, "AGENT_TEST_PG_ADMIN_DSN is not set")
class TimeLimitTests(_BuildDatabase):
    """A run that reaches its time limit publishes what it read, and the next run carries on."""

    def test_the_history_is_filled_in_newest_first_over_several_runs(self):
        days = _business_days(FIRST, LAST)
        # Eight reads in each limited run (the clock is read once at the start and once per date).
        events, read_rows, check = [], build.page_rows, build.page_disagreements

        def recorded_read(web, subject, as_of, *args, **kwargs):
            events.append(("read", as_of))
            return read_rows(web, subject, as_of, *args, **kwargs)

        def recorded_check(web, subject, as_of, *args, **kwargs):
            events.append(("check", as_of))
            return check(web, subject, as_of, *args, **kwargs)

        with patch.object(build, "page_rows", recorded_read), patch.object(build, "page_disagreements", recorded_check):
            code, lines = self._run(minutes=8.5 / 60, clock=_Clock())
        self.assertEqual(code, 0, lines)
        # The last five oldest first; the pages are asked again on the newest date at once (the check's
        # own read), before the history.
        self.assertEqual(events, [("read", day) for day in days[-5:]] + [("check", days[-1]), ("read", days[-1])]
                         + [("read", day) for day in (days[-6], days[-7], days[-8])])
        self.assertIn(f"Read 8 of {len(days)} subject dates. Stopped after {8.5 / 60:g} minutes: the other "
                      f"{len(days) - 8} are left for the next run.", lines)
        [(_, status, _, checks)] = self._builds()
        self.assertEqual((status, checks["subject_dates_read"], checks["subject_dates_left"]),
                         ("published", 8, len(days) - 8))
        self.assertEqual(sorted({key[5] for key in self._published()}), days[-8:])   # one unbroken run back
        # A page asked for a date the build has not reached finds no certified figures, so it shows
        # today's; one asked for a date it has reached finds that date's.
        from psycopg.rows import dict_row

        with layers._connect(self.database, WEB, row_factory=dict_row) as web:
            for day, found in ((days[-9], None), (days[-8], days[-8])):
                certified = certified_pages.read_certified(web, tenant_id=TENANT, subject_id=SUBJECT,
                                                           figure_ids=["overview.nav"], as_of=day)
                self.assertEqual(None if certified is None else certified.as_of, found, day)

        code, lines = self._run(minutes=8.5 / 60, clock=_Clock())                    # the last five again, then three more
        self.assertEqual(code, 0, lines)
        self.assertEqual(sorted({key[5] for key in self._published()}), days[-11:])
        self.assertGreater(self._builds()[-1][3]["unchanged"], 0)

        code, lines = self._run()                                                    # no limit reached: the rest
        self.assertEqual(code, 0, lines)
        self.assertIn(f"Read {len(days) - 6} of {len(days) - 6} subject dates.", lines)
        self.assertEqual(sorted({key[5] for key in self._published()}), days)
        self.assertEqual([status for _, status, _, _ in self._builds()], ["published"] * 3)



@unittest.skipUnless(ADMIN_DSN, "AGENT_TEST_PG_ADMIN_DSN is not set")
class BenchmarkHolidayTests(_BuildDatabase):
    """A day the benchmark's market was shut has no comparison row; the page still shows the comparison
    through the day before, and the certified figures for that day are the same."""

    def test_the_figures_are_the_comparison_the_page_shows(self):
        days = _business_days(FIRST, LAST)
        shut = days[-20]
        with layers._connect(self.database) as owner:
            owner.execute("SELECT set_config('app.tenant_id', %s, false)", (TENANT,))
            owner.execute("DELETE FROM performance_comparison_daily WHERE subject_id = %s AND observation_date::text = %s",
                          (SUBJECT, shut))
            owner.commit()
        comparison = self.web.benchmark_comparison(SUBJECT, as_of=shut, user=build.BUILD_USER)
        self.assertTrue(comparison["available"])
        self.assertEqual(str(comparison["series"][-1]["date"]), days[-21])          # through the day before
        code, lines = self._run()
        self.assertEqual(code, 0, lines)
        [(_, status, _, checks)] = self._builds()
        self.assertEqual(status, "published", checks)
        for figure_id, name in (("benchmark.correlation", "correlation"), ("benchmark.beta", "beta"),
                                ("benchmark.tracking_error", "tracking_error")):
            self.assertIsNotNone(comparison["metrics"][name], name)
            self.assertEqual(self._value(figure_id, shut, benchmark_id="SYNIDX"), comparison["metrics"][name])
        [(source_key,)] = self._owner(
            "SELECT source_key FROM gold.certified_figures WHERE subject_id = %s AND figure_id = 'benchmark.beta' "
            "AND as_of_date = %s", SUBJECT, shut)
        self.assertEqual(source_key["observation_date"], days[-21])


def _gate(states: dict[str, str]):
    def check(job, *, since, environ, now):
        state = states.get(job, "pass")
        return {"verdict": state, "reasons": [f"{SUBJECT} {job}"] if state == "fail" else [],
                "reasons_total": 1 if state == "fail" else 0}
    return check


@unittest.skipUnless(ADMIN_DSN, "AGENT_TEST_PG_ADMIN_DSN is not set")
class GateTests(_BuildDatabase):
    """In order: a failed verdict holds a binding build, a passing one publishes, report mode only
    stores, and the real gate runs read-only against this database."""

    def _with(self, mode: str, **kwargs):
        with patch.dict(os.environ, {build.GATE_ENV: mode}):
            return self._run(**kwargs)

    def test_1_binding_holds_the_build_when_a_source_job_fails(self):
        code, lines = self._with("binding", gate_check=_gate({"derived-daily": "fail", "risk-snapshots": "warn"}))
        self.assertEqual(code, 0)
        _build_id, status, reason, checks = self._builds()[-1]
        self.assertEqual(status, "held")
        self.assertEqual(reason, "the publish gate failed for derived-daily")
        self.assertEqual(checks["failed"], ["publish_gate"])
        self.assertEqual((checks["publish_gate"]["verdict"], checks["publish_gate"]["mode"]), ("fail", "binding"))
        self.assertEqual(self._published(), {})   # readers see nothing new
        text = "\n".join(lines)
        self.assertIn("Publish gate (binding, since 2026-10-19T00:00Z): fail; benchmark-daily pass (0 reasons), "
                      "derived-daily fail (1 reasons), risk-snapshots warn (0 reasons)", text)
        self.assertNotIn(SUBJECT, text)

    def test_2_binding_publishes_when_nothing_fails(self):
        code, _lines = self._with("binding", gate_check=_gate({"derived-daily": "warn", "benchmark-daily": "skipped"}))
        self.assertEqual(code, 0)
        _build_id, status, reason, checks = self._builds()[-1]
        self.assertEqual((status, reason), ("published", None))
        self.assertEqual((checks["passed"], checks["total"]), (5, 5))
        self.assertEqual(checks["publish_gate"]["verdict"], "warn")
        self.assertTrue(self._published())

    def test_3_report_mode_only_records_the_verdict(self):
        code, lines = self._with("report", dry_run=True, gate_check=_gate({"crisis-scenarios-daily": "fail"}))
        self.assertEqual(code, 0)
        text = "\n".join(lines)
        self.assertIn("Publish gate (report", text)
        self.assertIn("Dry run: nothing written. Nothing to publish.", text)

    def test_4_the_real_gate_reads_this_database(self):
        code, lines = self._with("binding", dry_run=True)
        self.assertEqual(code, 0)
        line = next(l for l in lines if l.startswith("Publish gate (binding"))
        for job in build.GATE_JOBS:
            self.assertIn(job, line)
        self.assertNotIn("check failed", line)
        self.assertEqual(len(self._builds()), 2)   # still the two builds above: the gate writes nothing


if __name__ == "__main__":
    unittest.main()
