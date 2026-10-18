"""The release data report's figures command (scripts/release_data_figures.py) on synthetic data.

No database and no website: the page answers come from a fake that returns synthetic API
payloads shaped as the website's, and the certified rows from a fake connection.
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import release_data_figures as figures  # noqa: E402

_SPEC = importlib.util.spec_from_file_location("release_data_report", ROOT / "scripts" / "release_data_report.py")
report_tool = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(report_tool)

ACCOUNT = "U9400001"
PORTFOLIO = "test-portfolio"
DAY = "2026-10-15"
# Distinctive values, so a leak into the public summary would be found.
NAV = 1234567.891
MTD = 0.0123456789
HOLDING = "777001"


def payloads(nav=NAV, mtd=MTD, weight=0.4321987):
    period = lambda key, twr: {"key": key, "status": "pass", "twr": twr, "twr_annualized": None,  # noqa: E731
                               "irr_annualized": 0.0811, "net_flows": 2500.25}
    return {
        "summary": {"effective_as_of": DAY, "latest_nav": {"snapshot_date": DAY, "currency": "USD", "total_text": str(nav)},
                    "nav_coverage": {"observations": 345}, "current_positions": 7},
        "periods_with_day": {"currency": "USD", "flows_applicable": True,
                             "custom": {"key": "custom", "status": "pass", "twr": -0.00456},
                             "periods": [period("mtd", mtd), period("ytd", 0.0912),
                                         {"key": "qtd", "status": "empty", "twr": None}]},
        "holdings": {"holdings": [{"conid": HOLDING, "quantity_text": "150", "market_value_text": "65432.10",
                                   "reporting_market_value_text": "65432.10", "cost_basis_text": "60000",
                                   "unrealized_pnl_text": "5432.10", "unrealized_pnl_pct": 0.090535,
                                   "weight": weight}],
                     "gross_holdings_text": "65432.10",
                     "concentration": {"available": True, "largest": {"symbol": "ZZTEST", "weight": weight},
                                       "top_5_weight": weight, "top_10_weight": weight, "effective_holdings": 1.0,
                                       "currency_exposure": [{"currency": "usd", "weight": weight}]}},
        "sectors": {"available": True, "allocation": [{"sector": "Technology", "weight": 1.0}]},
        "cash": {"balances": [{"currency": "USD", "ending_cash_text": "9876.54", "settled_cash_text": "9876.54"}]},
        "accounts_overview": {"accounts": [{"account_id": ACCOUNT, "nav": nav, "nav_date": DAY, "mtd": mtd,
                                            "ytd": 0.0912}]},
    }


class FakePages:
    def __init__(self, answers):
        self.answers = answers
        self.unavailable = {}

    def call(self, name, subject, day):
        return self.answers.get(name)


class FakeCursor:
    def __init__(self, rows, names=None):
        self.rows = rows
        self.description = [type("Column", (), {"name": name})() for name in (names or [])]

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return self.rows


class FakeConnection:
    NAMES = ["subject_id", "figure_id", "period", "item", "benchmark_id", "as_of_date", "value", "value_text",
             "unit", "status"]

    def __init__(self, certified):
        self.certified = certified

    def execute(self, sql, params=None):
        if "to_regclass" in sql:
            return FakeCursor([("gold.v_certified_figures",)])
        subjects, days, wanted = params
        rows = [tuple(row.get(name) for name in self.NAMES) for row in self.certified
                if row["subject_id"] in subjects and row["as_of_date"] in days and row["figure_id"] in wanted]
        return FakeCursor(rows, self.NAMES)


def certified_from(answers, subject=ACCOUNT, day=DAY):
    """Certified rows equal to what the pages show today."""
    rows = []
    for page, read in figures.TODAY.items():
        if page not in ("overview", "holdings", "cash"):
            continue
        for (figure_id, period, item, benchmark), value in read(FakePages(answers), subject, day).items():
            unit = figures.register.figure(figure_id).unit
            text = unit in ("date", "text")
            rows.append({"subject_id": subject, "figure_id": figure_id, "period": period, "item": item,
                         "benchmark_id": benchmark, "as_of_date": day, "unit": unit,
                         "value": None if text else value, "value_text": value if text else None,
                         "status": "ok" if value is not None else "unavailable"})
    return rows


def run(answers, certified, pages=("overview", "holdings", "cash")):
    comparison = figures.compare(list(pages), FakePages(answers), FakeConnection(certified), {ACCOUNT: [DAY]})
    return figures.build_report(comparison, source="test")


class FiguresCommandTests(unittest.TestCase):
    def test_batches_name_their_pages_through_the_register(self):
        self.assertEqual(figures.pages_for(["1"]), ["overview", "holdings", "cash"])
        self.assertEqual(figures.pages_for(["3"], ["performance"]),
                         ["performance", "risk-snapshot", "risk-statistics", "combined-holdings"])
        with self.assertRaises(figures.FiguresError):
            figures.pages_for([], [])
        with self.assertRaises(figures.FiguresError):
            figures.pages_for(["9"])

    def test_every_page_with_figures_of_its_own_is_read(self):
        for page in figures.register.pages():
            if figures.own_figures(page):
                self.assertIn(page, figures.TODAY, page)

    def test_same_figures_match(self):
        answers = payloads()
        report = run(answers, certified_from(answers))
        self.assertEqual(report["differences"], [])
        self.assertGreater(report["pages"]["overview"]["matched"], 10)
        self.assertEqual(report["pages"]["overview"]["blank_on_both"], 3)  # qtd: twr, irr and flows are dashes
        self.assertEqual(report["pages"]["holdings"]["differences"], 0)
        self.assertEqual(report["not_compared"], {"overview.data_freshness": figures.NOT_COMPARED["overview.data_freshness"]})
        self.assertFalse(figures.has_differences(report))

    def test_tolerances_follow_the_unit(self):
        self.assertTrue(figures.same("money", 100.004, 100.0))
        self.assertFalse(figures.same("money", 100.006, 100.0))
        self.assertTrue(figures.same("ratio", 0.1, 0.1 * (1 + 1e-12)))
        self.assertFalse(figures.same("ratio", 0.1, 0.1 * (1 + 1e-8)))
        self.assertTrue(figures.same("date", "2026-10-15", "2026-10-15"))
        self.assertFalse(figures.same("text", "USD", "usd"))
        self.assertTrue(figures.same("number", None, None))
        self.assertFalse(figures.same("number", 1.0, None))

    def test_differences_are_named_and_kept_with_values_in_the_full_report(self):
        answers = payloads()
        certified = certified_from(payloads(nav=NAV + 1.0, weight=0.5))
        certified = [row for row in certified if row["figure_id"] != "cash.settled_cash"]
        certified.append({"subject_id": ACCOUNT, "figure_id": "overview.twr", "period": "qtd", "item": "",
                          "benchmark_id": "", "as_of_date": DAY, "unit": "ratio", "value": 0.02,
                          "value_text": None, "status": "ok"})
        report = run(answers, certified)
        kinds = {(item["figure_id"], item["kind"]) for item in report["differences"]}
        self.assertIn(("overview.nav", "different"), kinds)
        self.assertIn(("holdings.holding_weight", "different"), kinds)
        self.assertIn(("cash.settled_cash", "page_only"), kinds)
        self.assertIn(("overview.twr", "certified_only"), kinds)
        # The accounts list is checked too, at its own date.
        self.assertTrue(any(item["source"] == "accounts list" and item["figure_id"] == "overview.nav"
                            for item in report["differences"]))
        self.assertTrue(figures.has_differences(report))
        markdown = figures.render_markdown(report)
        self.assertIn("u…#1", markdown)
        self.assertNotIn(ACCOUNT, markdown + json.dumps(report))
        self.assertIn(f"{NAV:.10g}", markdown)  # the private report has the values

    def test_public_summary_has_no_values_dates_subjects_or_items(self):
        answers = payloads()
        certified = certified_from(payloads(nav=NAV + 1.0, mtd=MTD * 2, weight=0.5))
        certified = [row for row in certified if row["figure_id"] != "cash.ending_cash"]
        report = run(answers, certified)
        summary = figures.render_public_summary(report)
        self.assertTrue(report["differences"])
        for leak in (ACCOUNT, PORTFOLIO, DAY, "2026", HOLDING, "Technology", "ZZTEST", "u…#", "s…#",
                     "1234567", "1,234,567", "0.0123", "0.0246", "1.23%", "65432", "9876", "0.4321", "0.5",
                     "USD", "usd"):
            self.assertNotIn(leak, summary, leak)
        self.assertIn("`overview.nav`", summary)
        self.assertIn("`cash.ending_cash`", summary)
        self.assertIn("| overview | 1 |", summary)
        # Nothing but counts: every digit run in the summary is a count from the report.
        counts = {str(value) for value in (report["compared"], report["subjects"], report["dates"],
                                           len(report["differences"]))}
        for page in report["pages"].values():
            counts.update(str(page[name]) for name in ("batch", "compared", "matched", "blank_on_both", "differences"))
        for counts_by_kind in report["figures"].values():
            counts.update(str(value) for value in counts_by_kind.values())
        import re
        for number in re.findall(r"(?<![\w.])\d[\d,]*(?:\.\d+)?", summary.replace("`", " ")):
            self.assertIn(number.replace(",", ""), counts | {"5", "2", "3", "1"}, number)

    def test_dates_are_latest_month_ends_and_a_fixed_sample(self):
        days = ["2026-08-28", "2026-08-31", "2026-09-01", "2026-09-15", "2026-09-30", "2026-10-01", "2026-10-02"]
        chosen = figures.pick_dates(days, 1, "U1")
        self.assertIn("2026-10-02", chosen)
        self.assertIn("2026-08-31", chosen)
        self.assertIn("2026-09-30", chosen)
        self.assertEqual(len(chosen), 4)
        self.assertEqual(chosen, figures.pick_dates(list(reversed(days)), 1, "U1"))
        self.assertEqual(figures.pick_dates([], 5), [])

    def test_private_folder_is_never_inside_the_checkout(self):
        with self.assertRaises(figures.FiguresError):
            figures.private_folder(str(ROOT / "reports"))
        with tempfile.TemporaryDirectory() as folder:
            path = figures.write_private(figures.private_folder(folder), "r.md", "x")
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_command_writes_full_report_privately_and_prints_only_the_summary(self):
        answers = payloads()
        certified = certified_from(payloads(nav=NAV + 1.0))

        class Pages(FakePages):
            def __init__(self, url, tenant):
                super().__init__(answers)

        class Connection(FakeConnection):
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def execute(self, sql, params=None):
                if "SET TRANSACTION" in sql or "set_config" in sql:
                    return FakeCursor([])
                return super().execute(sql, params)

        with tempfile.TemporaryDirectory() as folder, mock.patch.dict("os.environ", {"COPY_URL": "postgresql://copy"}), \
                mock.patch("psycopg.connect", return_value=Connection(certified)), \
                mock.patch.object(figures, "WebPages", Pages), \
                mock.patch.object(report_tool, "_header", return_value={"taken_at": "now"}), \
                mock.patch.object(figures, "subjects_and_dates", return_value={ACCOUNT: [DAY]}):
            out = io.StringIO()
            summary = Path(folder) / "public" / "summary.md"
            summary.parent.mkdir()
            with contextlib.redirect_stdout(out):
                code = report_tool.main(["figures", "--batch", "1", "--url-env", "COPY_URL",
                                         "--private-dir", str(Path(folder) / "private"),
                                         "--public-summary", str(summary)])
            self.assertEqual(code, 1)
            printed = out.getvalue()
            self.assertNotIn(f"{NAV:.10g}", printed)
            self.assertNotIn(ACCOUNT, printed)
            full = (Path(folder) / "private" / "certified-figures-report.md").read_text(encoding="utf-8")
            self.assertIn(f"{NAV:.10g}", full)
            self.assertEqual((Path(folder) / "private" / "certified-figures-report.json").stat().st_mode & 0o777, 0o600)
            self.assertNotIn(f"{NAV:.10g}", summary.read_text(encoding="utf-8"))

    def test_command_needs_a_private_folder_and_a_url(self):
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                report_tool.main(["figures", "--batch", "1"])
        errors = io.StringIO()
        with tempfile.TemporaryDirectory() as folder, mock.patch.dict("os.environ", {}, clear=False), \
                contextlib.redirect_stderr(errors):
            code = report_tool.main(["figures", "--batch", "1", "--url-env", "NO_SUCH_URL_VARIABLE",
                                     "--private-dir", folder])
        self.assertEqual(code, 2)
        self.assertIn("NO_SUCH_URL_VARIABLE is not set", errors.getvalue())


class WebPagesTests(unittest.TestCase):
    def test_pages_answer_the_old_way_whatever_the_certified_setting(self):
        from ibkr_etl import certified_figures, web

        with mock.patch.dict("os.environ", {certified_figures.PAGES_SETTING: "overview=on"}), \
                mock.patch.object(web, "TENANT_ID", web.TENANT_ID):
            pages = figures.WebPages("postgresql://copy.invalid/db", "tenant-x")
            self.assertNotIn(certified_figures.PAGES_SETTING, figures.os.environ)
            self.assertEqual(figures.os.environ["DATABASE_URL"], "postgresql://copy.invalid/db")
            self.assertEqual((pages.web, web.TENANT_ID), (web, "tenant-x"))


class CertifiedViewComparisonTests(unittest.TestCase):
    def test_the_certified_view_is_compared_and_other_views_are_not(self):
        self.assertEqual([item["table"] for item in report_tool.compared_views()], ["gold.v_certified_figures"])
        self.assertNotIn("gold.v_certified_figures", report_tool.catalog_views())
        self.assertEqual(report_tool.compared_views(["silver.nav_snapshots"]), [])

    def test_a_rolled_back_build_shows_as_changed_figures_in_the_view(self):
        columns = [["tenant_id", "uuid"], ["subject_id", "text"], ["figure_id", "text"], ["period", "text"],
                   ["item", "text"], ["benchmark_id", "text"], ["as_of_date", "date"], ["value", "double precision"],
                   ["build_id", "uuid"], ["status", "text"]]
        key = report_tool.COMPARED_VIEWS["gold.v_certified_figures"]
        tenant = "00000000-0000-0000-0000-000000000001"

        def view(value, build):
            return report_tool.encode_table("gold.v_certified_figures", "gold", columns, key, [
                (tenant, ACCOUNT, "overview.nav", "", "", "", DAY, value, build, "ok")])

        header = {"format": report_tool.FORMAT, "version": report_tool.FORMAT_VERSION, "taken_at": "t",
                  "all_tenants": False, "default_tenant": True, "migrations": []}
        report = report_tool.build_report(("before", header, iter([view(NAV, "b2")])),
                                          ("after", header, iter([view(NAV - 5, "b1")])))
        self.assertEqual(report["summary"]["changed"], ["gold.v_certified_figures"])
        summary = report_tool.render_public_summary(report)
        self.assertIn("`gold.v_certified_figures`: 0 added, 0 removed, 1 changed.", summary)
        self.assertNotIn(ACCOUNT, summary)


if __name__ == "__main__":
    unittest.main()
