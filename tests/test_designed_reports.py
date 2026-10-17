"""The factsheet design step of the new onboarding, without a database.

The report run itself, against PostgreSQL, is in test_designed_reports_db.py.
"""
import unittest
from unittest import mock

from fastapi import HTTPException

from ibkr_etl import designed_reports as dr
from ibkr_etl import onboarding_v2_web as v2
from ibkr_etl.onboarding_cases import OnboardingCase


class _Rows:
    def __init__(self, rows):
        self.rows = rows

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return self.rows


class _Answers:
    """Answers each query with the rows listed for the first table it names."""

    def __init__(self, **tables):
        self.tables = tables
        self.statements = []

    def execute(self, statement, parameters=()):
        self.statements.append((statement, parameters))
        table = next(name for name in self.tables if name in statement)
        rows = self.tables[table]
        if isinstance(rows, Exception):
            raise rows
        return _Rows(rows)


class MonthTests(unittest.TestCase):
    def test_a_broker_month_is_ready_with_a_recent_same_day_nav_and_holdings(self):
        for day, ready in (("2025-01-31", True), ("2025-01-24", True), ("2025-01-23", False), (None, False)):
            answers = _Answers(position_snapshots=[{"day": day}])
            self.assertEqual(dr.month_ready(answers, "broker_account", "U1234567", "2025-01-31"), ready, day)
            self.assertIn("asset_class = 'TOTAL'", answers.statements[0][0])

    def test_a_filtered_portfolio_reads_its_own_positions_and_nav(self):
        answers = _Answers(derived_portfolio_positions=[{"day": "2025-01-30"}])
        self.assertTrue(dr.month_ready(answers, "filtered_portfolio", "hk-only", "2025-01-31"))
        self.assertEqual(answers.statements[0][1], ("hk-only", "2025-01-31"))

    def test_the_check_says_what_it_waits_for(self):
        self.assertEqual(dr._message({"design": None, "reports": [], "waiting_for": None}),
                         ("pending", dr.WAITING_FOR_DESIGN))
        design = {"title": "Greater China"}
        self.assertEqual(dr._message({"design": design, "reports": [], "waiting_for": "2025-01-31"})[0], "pending")
        reports = [{"period_end": "2025-01-31"}, {"period_end": "2025-02-28"}]
        self.assertEqual(dr._message({"design": design, "reports": reports, "waiting_for": None}),
                         ("pass", "Factsheets for 2 months from 2025-01-31 to 2025-02-28 in your Greater China "
                                  "design, each read back and checked."))


def _case(stage="build_history", **changes):
    return OnboardingCase(case_id="c-1", broker="ibkr_flex", broker_account_id="U1234567", stage=stage, **changes)


class CasePageTests(unittest.TestCase):
    ROW = {"subject_id": "U1234567", "target_subject_id": "U1234567", "report_style": None}

    def design(self, connection=None, row=None, case=None, enabled=True):
        with mock.patch.object(v2, "factsheet_design_enabled", lambda: enabled):
            return v2._factsheet_design(connection or _Answers(factsheet_design_drafts=[]), "t",
                                        row or self.ROW, case or _case())

    def test_the_step_is_only_for_cases_without_a_built_in_design(self):
        self.assertIsNone(self.design(row={**self.ROW, "report_style": "alpha-world-ms"}))
        self.assertIsNone(self.design(enabled=False))
        self.assertIsNone(self.design(case=_case(cancelled=True)))
        self.assertIsNone(self.design(case=_case(stage="first_month_end")))

    def test_the_designer_opens_once_approval_one_registered_the_subject(self):
        early = self.design(row={**self.ROW, "subject_id": None}, case=_case(stage="load_and_reconcile"))
        self.assertEqual((early["open"], early["owner_action"]), (False, None))
        waiting = self.design()
        self.assertEqual((waiting["open"], waiting["designer_page"]), (True, "/accounts/U1234567/factsheet-designer"))
        self.assertIn("factsheet designer", waiting["owner_action"])
        done = self.design(_Answers(factsheet_design_drafts=[
            {"title": "Own", "status": "submitted", "current_version": 3, "updated_at": "2026-10-17T00:00:00+00:00"},
            {"title": "Older", "status": "review", "current_version": 1, "updated_at": "2026-10-16T00:00:00+00:00"}]))
        self.assertEqual((done["submitted"]["title"], done["owner_action"], len(done["designs"])), ("Own", None, 2))

    def test_an_unreadable_design_table_never_breaks_the_case_page(self):
        broken = self.design(_Answers(factsheet_design_drafts=RuntimeError("permission denied")))
        self.assertTrue(broken["unavailable"])

    def test_the_case_page_shows_the_design_card(self):
        self.assertIn('id="case-design"', v2.CASE_CONTENT)
        self.assertIn("renderDesign(c.factsheet_design)", v2.CASE_SCRIPT)


class DesignerAccessTests(unittest.TestCase):
    OWNER = {"role": "owner", "object_id": "o"}

    def access(self, subject_id, *, preview, case):
        from ibkr_etl import web
        with mock.patch.object(web, "_portfolio_access_context", lambda s, u: {"preview_mode": preview}), \
                mock.patch.object(web, "_onboarding_design_case", lambda s: case):
            return web._factsheet_designer_owner(subject_id, self.OWNER)

    def test_the_owner_designs_for_a_live_account_or_one_being_onboarded(self):
        from ibkr_etl import web
        self.access("U1234567", preview=False, case=None)
        self.access("U1234567", preview=True, case={"case_id": "c-1", "reporting_currency": "USD"})
        with self.assertRaises(HTTPException) as refused:
            self.access("U1234567", preview=True, case=None)
        self.assertEqual(refused.exception.status_code, 409)
        with self.assertRaises(HTTPException) as viewer:
            web._factsheet_designer_owner("U1234567", {"role": "viewer"})
        self.assertEqual(viewer.exception.status_code, 403)

    def test_only_a_new_onboarding_case_without_a_built_in_design_counts(self):
        from ibkr_etl import web
        with mock.patch.object(web, "onboarding_v2_enabled", lambda: False):
            self.assertIsNone(web._onboarding_design_case("U1234567"))
        source = __import__("inspect").getsource(web._onboarding_design_case)
        self.assertIn("report_style IS NULL", source)
        self.assertIn("stage IN ('request', 'load_and_reconcile', 'build_history')", source)


class LiveMonthEndTests(unittest.TestCase):
    """Subjects that went live from a case render their month-end like their approved history."""

    CASE = {"case_id": "c-1", "target_subject_id": "hk-composite", "report_style": "alpha-world-ms",
            "definition_sha256": "d", "source_kind": "composite_portfolio"}

    def run_month_end(self, case, *, fails=None):
        from ibkr_etl import onboarding_month_end as live
        calls = []

        def built_in(connection, tenant_id, case, **kwargs):
            calls.append(("built_in", case["target_subject_id"]))
            if fails:
                raise fails
            return {"reports": [{"period_end": "2025-03-31"}], "waiting_for": None}

        def designed(connection, tenant_id, case, **kwargs):
            calls.append(("designed", kwargs["design"]["title"]))
            return {"reports": [], "waiting_for": "2025-03-31"}

        class Connection:
            def rollback(self):
                calls.append(("rollback",))

        with mock.patch.object(live, "live_cases", lambda connection, tenant_id: [case]), \
                mock.patch.object(live.composite_reports, "render_case_reports", built_in), \
                mock.patch.object(live.designed_reports, "render_case_reports", designed), \
                mock.patch.object(live.designed_reports, "approved_design",
                                  lambda connection, tenant_id, case_id, subject_id: {"title": "Approved"}):
            results = live.run_live_month_end(Connection(), "t", container="c", today=None, fetch=None, store=None)
        return results, calls

    def test_a_built_in_design_and_an_owner_design_each_render_their_own_way(self):
        self.assertEqual(self.run_month_end(self.CASE), (
            [{"subject_id": "hk-composite", "status": "processed", "latest_month": "2025-03-31",
              "waiting_for": None}], [("built_in", "hk-composite")]))
        own = {**self.CASE, "target_subject_id": "U1234567", "report_style": None}
        self.assertEqual(self.run_month_end(own)[1], [("designed", "Approved")])

    def test_a_failure_is_reported_for_that_subject(self):
        results, calls = self.run_month_end(self.CASE, fails=RuntimeError("no snapshot\n for March"))
        self.assertEqual(results, [{"subject_id": "hk-composite", "status": "failed",
                                    "reason": "no snapshot for March"}])
        self.assertIn(("rollback",), calls)

    def test_the_month_end_job_hands_live_subjects_to_the_onboarding_run(self):
        from pathlib import Path
        scripts = Path(__file__).resolve().parents[1] / "scripts"
        registry = (scripts / "run_registry_month_end_packages.py").read_text()
        self.assertIn('if str(row["subject_id"]) in live_subjects:', registry)
        self.assertIn('"status": "onboarding_month_end"', registry)
        reports = (scripts / "render_onboarding_composite_reports.py").read_text()
        self.assertIn("run_live_month_end(", reports)
        self.assertIn("return 3 if failed else 0", reports)


if __name__ == "__main__":
    unittest.main()
