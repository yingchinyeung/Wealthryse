"""Performance › Attribution as certified figures: the register entries, the build's reader, the
page's certified reading (off, shadow, on) and the release data report's reader.

Synthetic data only. tests/test_certified_reports_db.py builds and reads them on PostgreSQL.
"""
from __future__ import annotations

import contextlib
import copy
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException

import test_certified_pages as page_tests
from ibkr_etl import certified_build as build
from ibkr_etl import certified_figures as cf
from ibkr_etl import certified_pages as pages
from ibkr_etl import web

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import release_data_figures as figures  # noqa: E402

SUBJECT = page_tests.SUBJECT
LATEST, EARLIER = page_tests.LATEST, page_tests.EARLIER
METHOD = "monthly-reporting-attribution-v2-full-nav-coverage"
FIGURES = ("attribution.portfolio_return", "attribution.holdings_contribution", "attribution.residual_contribution",
           "attribution.holding_contribution", "attribution.sector_contribution")


def answer(month="2026-10", period_end=LATEST, *, composite=False, available=True):
    """The Attribution page's answer, shaped as web.monthly_attribution_view returns it."""
    if not available:
        return {"account_id": SUBJECT, "month": month, "available": False, "quality_status": "fail",
                "message": "Insufficient common daily NAV and position observations for monthly attribution.",
                "period_start": f"{month}-01", "period_end": period_end, "method_version": METHOD}
    found = {"account_id": SUBJECT, "month": month, "quality_status": "pass", "message": "Monthly contributions.",
             "available": True, "period_start": f"{month}-01", "period_end": period_end, "reporting_currency": "USD",
             "method_version": "monthly-reporting-attribution-v1" if composite else METHOD, "daily_observations": 15,
             "portfolio_monthly_return": 0.0172, "equity_contribution": 0.0151, "residual_contribution": 0.0021,
             "security_contributions": [
                 {"conid": "1002", "symbol": "SYNB", "description": "Synthetic B", "sector": "Energy",
                  "contribution": -0.002},
                 {"conid": "1001", "symbol": "SYNA", "description": "Synthetic A", "sector": "Technology",
                  "contribution": 0.0171}],
             "sector_contributions": [{"sector": "Energy", "contribution": -0.002},
                                      {"sector": "Technology", "contribution": 0.0171}],
             "cash_activity": {"total_dividends": 12.5}}
    if not composite:
        found["position_coverage"] = {"missing_position_dates": [], "residual_only_intervals": [],
                                      "matched_position_intervals": 15, "nav_intervals": 15}
    return found


class RegisterTests(unittest.TestCase):
    def test_the_attribution_page_and_its_figures(self):
        page = cf.page("attribution")
        self.assertEqual((page.batch, page.routes, page.apis), (4, ("/accounts/{id}/performance/attribution",),
                                                                ("/api/portfolio/{id}/attribution",)))
        self.assertEqual(cf.batch_for_page("attribution").date.isoformat(), "2026-10-27")
        self.assertEqual([figure.id for figure in cf.figures_for_page("attribution")], list(FIGURES))
        labels = {figure.id: figure.label for figure in cf.figures_for_page("attribution")}
        self.assertEqual(labels["attribution.portfolio_return"], "Portfolio return (attribution basis)")
        self.assertEqual(labels["attribution.holding_contribution"], "Holding contribution")
        holding = cf.figure("attribution.holding_contribution")
        self.assertEqual((holding.kind, holding.item, holding.unit), ("per_period_item", "holding", "ratio"))
        self.assertTrue(cf.period_allowed(holding, "month_2026-09"))
        for period in ("", "ytd", "year_2026", "month_2026-13"):
            self.assertFalse(cf.period_allowed(holding, period), period)
        self.assertEqual(cf.calculation_version(holding, METHOD), METHOD)
        self.assertEqual(cf.calculation_version(holding, "monthly-reporting-attribution-v3"),
                         "monthly-reporting-attribution-v3+" + METHOD)
        # The other kinds are as before.
        self.assertFalse(cf.period_allowed(cf.figure("holdings.sector_allocation"), "month_2026-09"))
        self.assertTrue(cf.period_allowed(cf.figure("performance.monthly_return"), "month_2026-09"))
        self.assertEqual(cf.page_modes("attribution=on")["attribution"], "on")


# --- The build's reader ------------------------------------------------------------------------


class FakePages:
    def __init__(self, subject, as_of, payload):
        self.subject, self.as_of, self.payload = subject, as_of, payload
        self.months: list[str] = []

    def attribution(self, month):
        self.months.append(month)
        if isinstance(self.payload, Exception):
            raise self.payload
        return self.payload, "f" * 64


def _subject(kind="physical_account"):
    dates = ["2026-09-28", "2026-09-29", "2026-09-30", "2026-10-01", "2026-10-02", "2026-10-21"]
    return build.Subject(SUBJECT, {"reporting_currency": "USD", "subject_type": kind}, dates)


class BuildReaderTests(unittest.TestCase):
    def test_a_month_is_read_on_its_last_date_and_the_newest(self):
        for as_of, month in (("2026-09-30", "2026-09"), ("2026-10-21", "2026-10")):
            with self.subTest(as_of=as_of):
                fake = FakePages(_subject(), as_of, answer(month, as_of))
                rows = build.attribution_rows(fake)
                self.assertEqual(fake.months, [month])
                by_key = {(row.figure_id, row.period, row.item): row for row in rows}
                period = f"month_{month}"
                self.assertEqual(by_key[("attribution.portfolio_return", period, "")].value, 0.0172)
                self.assertEqual(by_key[("attribution.holdings_contribution", period, "")].value, 0.0151)
                self.assertEqual(by_key[("attribution.residual_contribution", period, "")].value, 0.0021)
                holding = by_key[("attribution.holding_contribution", period, "1001")]
                self.assertEqual((holding.value, holding.detail), (0.0171, {"symbol": "SYNA", "sector": "Technology"}))
                self.assertEqual(by_key[("attribution.sector_contribution", period, "Energy")].value, -0.002)
                self.assertEqual(len(rows), 7)
                for row in rows:
                    self.assertEqual((row.as_of_date, row.status, row.unit, row.calculation_version,
                                      row.input_sha256, row.source_table),
                                     (as_of, "ok", "ratio", METHOD, "f" * 64, "silver.subject_position_series"))
                    self.assertEqual((row.period_start, row.period_end, row.observations), (f"{month}-01", as_of, 15))

    def test_other_dates_and_composites_read_nothing(self):
        for subject, as_of in ((_subject(), "2026-09-29"), (_subject(), "2026-10-01"),
                               (_subject("composite_portfolio"), "2026-09-30")):
            fake = FakePages(subject, as_of, AssertionError("read"))
            self.assertEqual(build.attribution_rows(fake), [])
            self.assertEqual(fake.months, [])
        # A composite answer that reaches the reader anyway (no position coverage) gives no rows.
        self.assertEqual(build.attribution_rows(FakePages(_subject(), "2026-09-30",
                                                          answer("2026-09", "2026-09-30", composite=True))), [])

    def test_an_unavailable_month_is_three_unavailable_totals_with_the_reason(self):
        rows = build.attribution_rows(FakePages(_subject(), "2026-09-30", answer("2026-09", "2026-09-30",
                                                                                 available=False)))
        self.assertEqual([row.figure_id for row in rows], list(FIGURES[:3]))
        for row in rows:
            self.assertEqual((row.status, row.value, row.period), ("unavailable", None, "month_2026-09"))
            self.assertIn("Insufficient", row.reason)

    def test_the_page_error_is_counted_as_the_website_would_show_it(self):
        notes: dict = {}
        with patch.object(build, "Pages", lambda web, subject, as_of: FakePages(
                subject, as_of, build.PageUnavailable("400: Month must be YYYY-MM"))):
            self.assertEqual(build.page_rows(object(), _subject(), "2026-09-30", ["attribution"], notes=notes), [])
        self.assertEqual(notes, {"page_unavailable": {"attribution": 1}})

    def test_the_build_reads_the_page_and_fingerprints_every_row_it_read(self):
        queries = []

        class Cursor:
            def __init__(self, rows):
                self.rows, self.description, self.rowcount = rows, [("x",)], len(rows)

            def fetchall(self):
                return list(self.rows)

        class Connection:
            def __init__(self, rows):
                self.rows = rows

            def execute(self, sql, params=None, **kwargs):
                queries.append((sql, params, kwargs))
                return Cursor(self.rows)

        class Web:
            def __init__(self, rows):
                self.rows = rows
                self._database = self.database

            @contextlib.contextmanager
            def database(self):
                yield Connection(self.rows)

            def monthly_attribution_view(self, subject_id, *, month, as_of, user):
                with self._database() as connection:
                    first = connection.execute("SELECT ?", ("a",)).fetchall()
                    second = list(connection.execute("SELECT 2", prepare=False))
                return {"month": month, "as_of": as_of, "rows": first + second, "user": user["email"]}

        fake = Web([{"value": "1.5"}, {"value": "2.5"}])
        original = fake._database
        payload, digest = build.Pages(fake, _subject(), "2026-09-30").attribution("2026-09")
        self.assertEqual(payload["rows"], [{"value": "1.5"}, {"value": "2.5"}] * 2)
        self.assertEqual((payload["month"], payload["as_of"], payload["user"]),
                         ("2026-09", "2026-09-30", build.BUILD_USER["email"]))
        self.assertEqual(fake._database, original)
        self.assertEqual(queries[1][2], {"prepare": False})
        again = build.Pages(Web([{"value": "1.5"}, {"value": "2.5"}]), _subject(), "2026-09-30").attribution("2026-09")
        self.assertEqual(again[1], digest)
        changed = build.Pages(Web([{"value": "2.5"}, {"value": "1.5"}]), _subject(), "2026-09-30").attribution("2026-09")
        self.assertNotEqual(changed[1], digest)  # the order the engine adds up in is part of its inputs

    def test_the_page_is_a_reader_and_a_build_page(self):
        self.assertEqual(build.READERS["attribution"], (build.attribution_rows,))


# --- The page's certified reading ---------------------------------------------------------------


def _answer(day):
    return answer()  # the page works the month out whatever the as-of date, as today


class AttributionPageTests(page_tests._PageCase, unittest.TestCase):
    ENDPOINT = "attribution"

    def setUp(self):
        self.make, self.on_setting = _answer, "attribution=on"
        self.spec = pages.ENDPOINTS[self.ENDPOINT]
        self.calls = []

    def test_on_when_held_back_shows_the_last_published_with_a_note(self):
        # Overrides the common case: this answer cannot be rebuilt for an earlier date, so
        # figures from an earlier date are never shown; today's answer stays, logged as behind.
        rows = page_tests.certified_rows(self.ENDPOINT, answer(period_end=EARLIER), as_of=EARLIER)
        with self.assertLogs(page_tests.LOGGER, level="INFO") as captured:
            shown = self.serve(self.on_setting, page_tests.FakeView(rows))
        self.assertEqual(shown, answer())
        self.assertEqual(self.calls, [None])
        self.assertEqual([(line["mode"], line["status"]) for line in page_tests._log_lines(captured)],
                         [("on", "behind")])


class PeriodView(page_tests.FakeView):
    """Also keeps to the asked period, as READ_PERIOD_SQL does."""

    def __init__(self, rows=()):
        super().__init__(rows)
        self.periods: list = []
        self.sql: list = []

    def execute(self, sql, params=None):
        self.sql.append(sql)
        self.periods.append(params.get("period"))
        if "period" not in params:
            return super().execute(sql, params)
        in_period = page_tests.FakeView([row for row in self.rows if row["period"] == params["period"]])
        return in_period.execute(sql, params)


class AttributionReadTests(unittest.TestCase):
    def serve(self, setting, view, today):
        with patch.object(pages, "_connect", view.connect if view else page_tests._forbidden_connect):
            return pages.serve("attribution", tenant_id=page_tests.TENANT, subject_id=SUBJECT,
                               requested_as_of=LATEST, build=lambda day: copy.deepcopy(today),
                               environ={page_tests.SETTING: setting})

    def test_the_month_is_read_on_its_own(self):
        september = answer("2026-09", "2026-09-30")
        rows = page_tests.certified_rows("attribution", september, as_of="2026-09-30") + \
            page_tests.certified_rows("attribution", answer(), as_of=LATEST)
        view = PeriodView(rows)
        shown = self.serve("attribution=on", view, september)
        self.assertEqual(view.periods, ["month_2026-09"])
        self.assertIn("period = %(period)s", view.sql[0])
        self.assertEqual(shown["certified"]["as_of"], "2026-09-30")
        self.assertEqual({key: value for key, value in shown.items() if key != "certified"}, september)

    def test_composites_are_not_read(self):
        composite = answer(composite=True)
        self.assertEqual(self.serve("attribution=on", None, composite), composite)
        self.assertEqual(pages.ENDPOINTS["attribution"].slots(composite), [])
        unavailable = answer(available=False)
        self.assertEqual(self.serve("attribution=on", None, unavailable), unavailable)

    def test_the_other_pages_keep_their_reads(self):
        self.assertIsNone(pages.ENDPOINTS["summary"].period)
        self.assertFalse(pages.ENDPOINTS["summary"].exact)
        view = PeriodView()
        with patch.object(pages, "_connect", view.connect):
            pages._read_groups([pages.Group("overview", ("overview.nav",))], tenant_id=page_tests.TENANT,
                               subject_id=SUBJECT, as_of=LATEST, benchmark_id="")
        self.assertEqual(view.sql, [pages.READ_SQL])


class RouteTests(unittest.TestCase):
    def setUp(self):
        for patcher in (patch.object(web, "_portfolio_access_context", return_value=page_tests.ACCESS),
                        patch.object(web, "_database", page_tests._no_database),
                        patch.object(pages, "_connect", page_tests._forbidden_connect)):
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_unset_calls_todays_builder_once_and_returns_its_answer(self):
        with patch.dict("os.environ", {page_tests.SETTING: ""}), \
                patch.object(web, "_monthly_attribution", return_value=answer()) as builder:
            self.assertEqual(web.monthly_attribution_view(SUBJECT, month="2026-10", as_of=LATEST,
                                                          user=page_tests.VIEWER), answer())
        builder.assert_called_once_with(SUBJECT, page_tests.ACCESS, "2026-10")

    def test_the_checks_run_before_any_read(self):
        with patch.dict("os.environ", {page_tests.SETTING: "attribution=on"}), \
                patch.object(web, "_monthly_attribution", side_effect=AssertionError("built")):
            for month, as_of in (("2026-13", LATEST), ("2026-11", LATEST)):
                with self.assertRaises(HTTPException) as caught:
                    web.monthly_attribution_view(SUBJECT, month=month, as_of=as_of, user=page_tests.VIEWER)
                self.assertEqual(caught.exception.status_code, 400)

    def test_the_engine_is_called_for_the_calendar_month_as_before(self):
        with patch("ibkr_etl.monthly_reporting_analytics.build_monthly_reporting_analytics",
                   return_value={"quality_status": "pass", "message": "m", "data": {"available": True}}) as engine:
            found = web._monthly_attribution(SUBJECT, page_tests.ACCESS, "2026-02")
        self.assertEqual(engine.call_args.kwargs["period_end"], "2026-02-28")
        self.assertEqual(found, {"account_id": SUBJECT, "month": "2026-02", "quality_status": "pass",
                                 "message": "m", "available": True})
        with patch("ibkr_etl.monthly_reporting_analytics.build_monthly_reporting_analytics",
                   side_effect=RuntimeError("no data")):
            self.assertEqual(web._monthly_attribution(SUBJECT, page_tests.ACCESS, "2026-02"),
                             {"account_id": SUBJECT, "month": "2026-02", "available": False,
                              "quality_status": "fail", "message": "no data"})



class AttributionScriptTests(unittest.TestCase):
    def test_the_page_draws_the_certified_line(self):
        owner = {"email": "owner@example.com", "role": "owner"}
        account = web._render_account_page(owner, account_id=web.PILOT_ACCOUNT_ID, section="performance")
        self.assertIn("summary.innerHTML='';certifiedLine(summary,null);", account)
        self.assertIn(".map(metricCard).join('');certifiedLine(summary,r.certified);", account)


# --- The release data report's reader ------------------------------------------------------------


class Api:
    def __init__(self, view):
        self.view = view
        self.asked: list = []

    def call(self, name, subject, day):
        self.asked.append((name, subject, day))
        return self.view


class ReleaseReportTests(unittest.TestCase):
    def test_the_page_is_read_on_the_months_last_date(self):
        found = figures.attribution_today(Api(answer("2026-09", "2026-09-30")), SUBJECT, "2026-09-30")
        self.assertEqual(found[("attribution.portfolio_return", "month_2026-09", "", "")], 0.0172)
        self.assertEqual(found[("attribution.holding_contribution", "month_2026-09", "1001", "")], 0.0171)
        self.assertEqual(found[("attribution.sector_contribution", "month_2026-09", "Energy", "")], -0.002)
        self.assertEqual(len(found), 7)

    def test_other_dates_are_not_compared(self):
        self.assertIsNone(figures.attribution_today(Api(answer("2026-09", "2026-09-30")), SUBJECT, "2026-09-15"))
        self.assertEqual(figures.attribution_today(Api(None), SUBJECT, "2026-09-30"), {})
        self.assertEqual(figures.attribution_today(Api(answer(composite=True)), SUBJECT, LATEST), {})

    def test_compare_skips_a_date_the_page_cannot_show(self):
        certified = [{"subject_id": SUBJECT, "figure_id": "attribution.portfolio_return", "period": "month_2026-09",
                      "item": "", "benchmark_id": "", "as_of_date": "2026-09-15", "value": 0.01, "value_text": None,
                      "unit": "ratio", "status": "ok"}]
        api = Api(answer("2026-09", "2026-09-30"))
        comparison = figures.compare(["attribution"], api, _ReleaseConnection(certified), {SUBJECT: ["2026-09-15"]})
        self.assertEqual((comparison.compared["attribution"], comparison.differences), (0, []))
        self.assertEqual(api.asked, [("attribution", SUBJECT, "2026-09-15")])


class _ReleaseConnection:
    NAMES = ["subject_id", "figure_id", "period", "item", "benchmark_id", "as_of_date", "value", "value_text",
             "unit", "status"]

    def __init__(self, rows):
        self.rows = rows

    def execute(self, sql, params=None):
        class Cursor:
            def __init__(self, rows, names):
                self.rows = rows
                self.description = [type("Column", (), {"name": name})() for name in names]

            def fetchall(self):
                return self.rows

            def fetchone(self):
                return self.rows[0] if self.rows else None

        if "to_regclass" in sql:
            return Cursor([("gold.v_certified_figures",)], ["to_regclass"])
        if "min(as_of_date)" in sql:
            return Cursor([(SUBJECT, min(row["as_of_date"] for row in self.rows))], ["subject_id", "since"])
        return Cursor([tuple(row[name] for name in self.NAMES) for row in self.rows], self.NAMES)


if __name__ == "__main__":
    unittest.main()
