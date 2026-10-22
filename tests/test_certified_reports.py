"""Monthly reports on certified figures (MONTHLY_REPORTS_CERTIFIED): off, fallback and on, and
the read-only rehearsal command (scripts/compare_monthly_report_figures.py).

Synthetic data only: a month-end payload assembled by today's assemble_month_end_payload from
synthetic tool results, and certified rows from an in-memory stand-in for the view.
tests/test_certified_reports_db.py runs the same on PostgreSQL.
"""
from __future__ import annotations

import contextlib
import copy
import importlib.util
import io
import json
import os
import stat
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from ibkr_etl import certified_reports as reports
from ibkr_etl import reporting_snapshot
from ibkr_etl.reporting_snapshot import assemble_month_end_payload
from ibkr_etl.util import canonical_json, sha256_text

ROOT = Path(__file__).resolve().parents[1]
SUBJECT = "U9300001"
MONTH_END, DATA_AS_OF = "2026-09-30", "2026-09-30"
TENANT = "00000000-0000-0000-0000-000000000001"
# Distinctive values, so a leak into a log line or the printed summary would be found.
SHARPE, HOLDING_CONTRIBUTION = 0.8765432101, 0.0123456789


def _result(data, *, quality="pass"):
    return {"tool_name": "test", "subject_id": SUBJECT, "as_of": DATA_AS_OF, "quality_status": quality,
            "data": data, "citations": [{"table": "test"}], "generated_at": "2026-10-01T00:00:00+00:00"}


def _comparison(benchmark_id, shift=0.0):
    return {"benchmark": {"benchmark_id": benchmark_id, "provider": "test", "return_convention": "price",
                          "license_status": "approved"},
            "publication_allowed": True,
            "metrics": {"portfolio_total_return": 0.21 + shift, "benchmark_total_return": 0.17 + shift,
                        "excess_total_return": 0.04, "tracking_error": 0.051, "information_ratio": 0.78,
                        "beta": 0.93, "correlation": 0.88, "relative_win_rate": 0.54},
            "series": [{"date": "2026-07-31", "portfolio": 100.0, "benchmark": 100.0},
                       {"date": "2026-08-31", "portfolio": 101.5, "benchmark": 100.8},
                       {"date": "2026-09-30", "portfolio": 103.2, "benchmark": 101.1}]}


def tool_results():
    return {
        "summary": _result({"latest_nav": {"snapshot_date": DATA_AS_OF, "currency": "USD", "total_text": "1250.50"},
                            "positions": 3}),
        "performance": _result({"series": [], "observations": 0}),
        "holdings": _result({"holdings": [], "returned": 0}),
        "primary_benchmark": _result(_comparison("SYNIDX")),
        "secondary_benchmark": _result(_comparison("SYNTWO", 0.01)),
        "risk": _result({"metrics": {"total_return": 0.21, "annualized_return": 0.12, "sharpe_ratio": SHARPE,
                                     "cvar_95_daily": -0.021, "daily_expected_tail_loss": -0.021,
                                     "crisis_maximum_drawdown": -0.35},
                         "crisis_replay": {"holdings_as_of": DATA_AS_OF, "maximum_drawdown": -0.35,
                                           "calculation_version": "as-of-holdings-crisis-replay-v2"}}),
        "freshness": _result({"portfolio": {"nav_date": DATA_AS_OF}}),
        "sector_allocation": _result({"available": True, "publication_allowed": True, "gross_market_value": 1000.25,
                                      "allocation": [{"sector": "Technology", "weight": 0.6},
                                                     {"sector": "Energy", "weight": 0.4}]}),
        "monthly_reporting": {"quality_status": "pass", "data": {
            "available": True, "period_start": "2026-09-01", "period_end": DATA_AS_OF, "reporting_currency": "USD",
            "method_version": "monthly-reporting-attribution-v2-full-nav-coverage", "daily_observations": 21,
            "position_coverage": {"missing_position_dates": [], "residual_only_intervals": [],
                                  "matched_position_intervals": 21, "nav_intervals": 21},
            "portfolio_monthly_return": 0.0172, "equity_contribution": 0.0151, "residual_contribution": 0.0021,
            "security_contributions": [
                {"conid": "1002", "symbol": "SYNB", "description": "", "sector": "Energy", "contribution": -0.002},
                {"conid": "1001", "symbol": "SYNA", "description": "", "sector": "Technology",
                 "contribution": HOLDING_CONTRIBUTION}],
            "sector_contributions": [{"sector": "Energy", "contribution": -0.002},
                                     {"sector": "Technology", "contribution": HOLDING_CONTRIBUTION}],
            "cash_activity": {"total_dividends": 12.5}}},
    }


def payload():
    built, _quality, _allowed, _blockers = assemble_month_end_payload(
        subject_id=SUBJECT, period_start="2026-06-01", period_end=MONTH_END, reporting_currency="USD",
        tool_results=tool_results())
    return built


def rows_for(built, *, skip=(), change=None):
    """The certified rows a correct build would publish for the payload's fields."""
    change = change or {}
    rows = {}
    for field in reports.report_fields(built):
        key = field.slot.key
        if key[0] in skip:
            continue
        value = field.slot.get()
        value = change.get(key, value)
        rows[key] = {"figure_id": key[0], "period": key[1], "item": key[2], "benchmark_id": key[3],
                     "value": value, "value_text": None, "unit": reports.register.figure(key[0]).unit,
                     "status": "ok" if value is not None else "unavailable"}
    return list(rows.values())


class FakeConnection:
    """Answers the report's read like gold.v_certified_figures for one date; records savepoints."""

    def __init__(self, rows=(), fail=None):
        self.rows, self.fail = list(rows), fail
        self.calls: list = []
        self.savepoints = 0

    def execute(self, sql, params=None):
        self.calls.append((sql, params))
        if self.fail:
            raise self.fail
        return SimpleNamespace(fetchall=lambda: [dict(row) for row in self.rows])

    @contextlib.contextmanager
    def transaction(self):
        self.savepoints += 1
        yield


def apply(connection, built, mode, subject=SUBJECT):
    lines: list[str] = []
    result = reports.apply_certified(connection, tenant_id=TENANT, subject_id=subject, payload=built,
                                     environ={reports.SETTING: mode}, out=lines.append)
    return result, lines


def parsed(lines):
    assert len(lines) == 1, lines
    prefix, _, body = lines[0].partition(" ")
    assert prefix == reports.LOG_PREFIX
    return json.loads(body)


class SettingTests(unittest.TestCase):
    def test_modes(self):
        for value, expected in (({}, "off"), ({reports.SETTING: ""}, "off"), ({reports.SETTING: "maybe"}, "off"),
                                ({reports.SETTING: " Fallback "}, "fallback"), ({reports.SETTING: "ON"}, "on"),
                                ({reports.SETTING: "shadow"}, "off")):
            self.assertEqual(reports.mode(value), expected, value)

    def test_off_reads_nothing_logs_nothing_and_changes_nothing(self):
        built = payload()
        before = canonical_json(built)
        for environ in ({}, {reports.SETTING: ""}, {reports.SETTING: "off"}, {reports.SETTING: "nonsense"}):
            connection = FakeConnection(fail=AssertionError("read"))
            lines: list[str] = []
            with contextlib.redirect_stdout(io.StringIO()) as printed:
                result = reports.apply_certified(connection, tenant_id=TENANT, subject_id=SUBJECT, payload=built,
                                                 environ=environ, out=lines.append)
            self.assertIsNone(result)
            self.assertEqual((connection.calls, lines, printed.getvalue()), ([], [], ""))
            self.assertEqual(canonical_json(built), before)


class MappingTests(unittest.TestCase):
    def test_every_figure_the_report_shows_with_a_certified_version(self):
        built = payload()
        fields = reports.report_fields(built)
        by_id = {}
        for field in fields:
            by_id.setdefault(field.field_id, set()).add(field.slot.key)
        self.assertEqual(by_id["portfolio_summary.latest_nav.total_text"], {("overview.nav", "", "", "")})
        self.assertEqual(by_id["portfolio_summary.positions"], {("overview.positions", "", "", "")})
        self.assertEqual(by_id["risk.metrics.sharpe_ratio"], {("risk.sharpe_ratio", "", "", "")})
        self.assertEqual(by_id["risk.metrics.cvar_95_daily"], {("risk.etl_95_daily", "", "", "")})
        self.assertEqual(by_id["risk.metrics.daily_expected_tail_loss"], {("risk.etl_95_daily", "", "", "")})
        self.assertEqual(by_id["benchmark_comparisons[].metrics.beta"],
                         {("benchmark.beta", "", "", "SYNIDX"), ("benchmark.beta", "", "", "SYNTWO")})
        self.assertEqual(by_id["monthly_reporting.portfolio_monthly_return"],
                         {("attribution.portfolio_return", "month_2026-09", "", "")})
        self.assertEqual(by_id["monthly_reporting.security_contributions[].contribution"],
                         {("attribution.holding_contribution", "month_2026-09", "1001", ""),
                          ("attribution.holding_contribution", "month_2026-09", "1002", "")})
        self.assertEqual(by_id["monthly_reporting.sector_contributions[].contribution"],
                         {("attribution.sector_contribution", "month_2026-09", "Energy", ""),
                          ("attribution.sector_contribution", "month_2026-09", "Technology", "")})
        self.assertEqual(by_id["month_end_performance[].portfolio_monthly_return"],
                         {("performance.monthly_return", "month_2026-08", "", ""),
                          ("performance.monthly_return", "month_2026-09", "", "")})
        self.assertEqual(by_id["sector_allocation.allocation[].weight"],
                         {("holdings.sector_allocation", "", "Technology", ""),
                          ("holdings.sector_allocation", "", "Energy", "")})
        compared_only = {field.field_id for field in fields if not field.slot.write}
        self.assertEqual(compared_only, {"month_end_performance[].portfolio_monthly_return",
                                         "sector_allocation.gross_market_value", "sector_allocation.allocation[].weight"})
        # Every key names a certified figure, with a period and item the register allows.
        for field in fields:
            figure = reports.register.figure(field.figure_id)
            self.assertTrue(reports.register.period_allowed(figure, field.slot.key[1]), field.slot.key)
            self.assertEqual(bool(field.slot.key[2]), figure.item is not None, field.slot.key)

    def test_composite_attribution_and_own_designs_are_not_mapped(self):
        built = payload()
        del built["monthly_reporting"]["position_coverage"]
        self.assertFalse([field for field in reports.report_fields(built) if field.figure_id.startswith("attribution.")])
        self.assertTrue(reports.own_design("alpha-world-t", payload()))
        self.assertTrue(reports.own_design(SUBJECT, {**payload(), "report_profile": {"style": "x"}}))
        self.assertFalse(reports.own_design(SUBJECT, payload()))


class FallbackTests(unittest.TestCase):
    def test_all_equal_is_a_match_and_changes_nothing(self):
        built = payload()
        before = canonical_json(built)
        connection = FakeConnection(rows_for(built))
        result, lines = apply(connection, built, "fallback")
        self.assertEqual(canonical_json(built), before)
        line = parsed(lines)
        self.assertEqual(line["status"], "match")
        self.assertEqual(line["subject"], "u…01")
        self.assertEqual(line["different"] + line["missing"] + line["close"], 0)
        self.assertEqual(line["used"], line["compared"])
        self.assertEqual(connection.savepoints, 1)
        sql, params = connection.calls[0]
        self.assertIn("gold.v_certified_figures", sql)
        self.assertEqual(params[:3], (TENANT, SUBJECT, DATA_AS_OF))
        self.assertEqual(result["status"], "match")

    def test_any_difference_keeps_todays_report_byte_for_byte_and_logs_ids_and_counts_only(self):
        built = payload()
        before = canonical_json(built)
        close = ("risk.sharpe_ratio", "", "", "")
        different = ("attribution.holding_contribution", "month_2026-09", "1001", "")
        compared_only = ("holdings.sector_allocation", "", "Energy", "")
        rows = rows_for(built, skip=("benchmark.beta",), change={
            close: SHARPE * (1 + 1e-12), different: HOLDING_CONTRIBUTION + 0.001, compared_only: 0.41})
        result, lines = apply(FakeConnection(rows), built, "fallback")
        self.assertEqual(canonical_json(built), before)
        line = parsed(lines)
        self.assertEqual(line["status"], "differ")
        self.assertEqual(line["close_ids"], ["risk.sharpe_ratio"])
        self.assertEqual(line["different_ids"], ["attribution.holding_contribution"])
        self.assertEqual(line["missing_ids"], ["benchmark.beta"])
        self.assertEqual((line["close"], line["different"], line["missing"]), (1, 1, 2))
        self.assertEqual(line["kept_today"], 4)
        self.assertEqual(line["compared_only"]["different_ids"], ["holdings.sector_allocation"])
        text = lines[0]
        for leaked in (SUBJECT, "9300001", repr(SHARPE)[:8], repr(HOLDING_CONTRIBUTION)[:8], "1250.5", "Energy", "1001"):
            self.assertNotIn(leaked, text)
        statuses = {(entry["figure_id"], entry["item"], entry["benchmark_id"]): entry["status"]
                    for entry in result["fields"]}
        self.assertEqual(statuses[("risk.sharpe_ratio", "", "")], "close")
        self.assertEqual(statuses[("benchmark.beta", "", "SYNIDX")], "missing")

    def test_no_certified_figures_for_the_date(self):
        built = payload()
        before = canonical_json(built)
        _, lines = apply(FakeConnection([]), built, "fallback")
        self.assertEqual(canonical_json(built), before)
        line = parsed(lines)
        self.assertEqual((line["status"], line["equal"], line["used"]), ("none", 0, 0))

    def test_a_failed_read_leaves_todays_report_and_logs_the_error_type(self):
        built = payload()
        before = canonical_json(built)
        result, lines = apply(FakeConnection(fail=RuntimeError("relation does not exist")), built, "fallback")
        self.assertEqual(canonical_json(built), before)
        self.assertEqual(parsed(lines), {"mode": "fallback", "subject": "u…01", "status": "error",
                                         "error": "RuntimeError", "compared": 0})
        self.assertEqual(result["status"], "error")

    def test_own_designs_are_not_compared(self):
        built = payload()
        connection = FakeConnection(fail=AssertionError("read"))
        _, lines = apply(connection, built, "fallback", subject="alpha-world-ms")
        self.assertEqual(parsed(lines)["status"], "not_applicable")
        self.assertEqual(connection.calls, [])

    def test_the_line_is_printed_when_no_writer_is_given(self):
        built = payload()
        with contextlib.redirect_stdout(io.StringIO()) as printed:
            reports.apply_certified(FakeConnection(rows_for(built)), tenant_id=TENANT, subject_id=SUBJECT,
                                    payload=built, environ={reports.SETTING: "fallback"})
        self.assertTrue(printed.getvalue().startswith("CERTIFIED_REPORTS {"))


class OnTests(unittest.TestCase):
    def test_on_writes_certified_values_into_the_written_fields_only(self):
        built = payload()
        changed_attribution = ("attribution.holding_contribution", "month_2026-09", "1001", "")
        changed_nav = ("overview.nav", "", "", "")
        compared_only = ("holdings.sector_allocation", "", "Energy", "")
        rows = rows_for(built, change={changed_attribution: 0.02, changed_nav: 1300.0, compared_only: 0.41,
                                       ("benchmark.beta", "", "", "SYNIDX"): 0.95})
        result, lines = apply(FakeConnection(rows), built, "on")
        security = {row["conid"]: row for row in built["monthly_reporting"]["security_contributions"]}
        self.assertEqual(security["1001"]["contribution"], 0.02)
        self.assertEqual(built["portfolio_summary"]["latest_nav"]["total_text"], "1300")
        self.assertEqual(built["sector_allocation"]["allocation"][1]["weight"], 0.4)  # compared only
        # The primary comparison is one object in the payload, under both names.
        self.assertEqual(built["benchmark_comparisons"][0]["metrics"]["beta"], 0.95)
        self.assertEqual(built["performance_comparison"]["metrics"]["beta"], 0.95)
        self.assertEqual(parsed(lines)["status"], "differ")
        self.assertGreater(result["used"], 0)

    def test_on_keeps_todays_decimal_spelling_for_the_same_number(self):
        built = payload()
        apply(FakeConnection(rows_for(built)), built, "on")
        self.assertEqual(built["portfolio_summary"]["latest_nav"]["total_text"], "1250.50")
        self.assertEqual(canonical_json(built), canonical_json(payload()))


class FreezeTests(unittest.TestCase):
    """The freeze makes its payload through build_month_end_payload, with the setting applied."""

    def _freeze(self, environ):
        stored = {}

        class Connection(FakeConnection):
            def execute(self, sql, params=None):
                if "INSERT INTO reporting_snapshots" in sql:
                    stored["payload"] = params[9]
                    stored["sha"] = params[10]
                    return SimpleNamespace(fetchone=lambda: {"snapshot_id": params[0]})
                if "UPDATE reporting_snapshots" in sql:
                    return SimpleNamespace(fetchone=lambda: None)
                return super().execute(sql, params)

            def commit(self):
                stored["committed"] = True

        built = payload()
        connection = Connection(rows_for(built))
        with patch.object(reporting_snapshot, "_collect_tool_results", return_value=tool_results()), \
                patch.dict(os.environ, environ, clear=False), contextlib.redirect_stdout(io.StringIO()) as printed:
            if reports.SETTING not in environ:
                os.environ.pop(reports.SETTING, None)
            result = reporting_snapshot.freeze_month_end_snapshot(
                connection, tenant_id=TENANT, subject_id=SUBJECT, period_start="2026-06-01", period_end=MONTH_END,
                reporting_currency="USD")
        return result, stored, connection, printed.getvalue()

    def test_off_and_fallback_freeze_the_same_snapshot(self):
        off, stored_off, connection_off, printed_off = self._freeze({})
        fallback, stored_fallback, connection, printed = self._freeze({reports.SETTING: "fallback"})
        self.assertEqual(stored_off["payload"], stored_fallback["payload"])
        self.assertEqual((off.data_sha256, off.snapshot_id), (fallback.data_sha256, fallback.snapshot_id))
        self.assertEqual(stored_off["sha"], sha256_text(canonical_json(payload())))
        self.assertEqual([sql for sql, _ in connection_off.calls], [])  # off: no certified read
        self.assertEqual(printed_off, "")
        self.assertTrue(printed.startswith("CERTIFIED_REPORTS "))
        self.assertEqual(connection.savepoints, 1)

    def test_build_month_end_payload_matches_the_freeze_without_writing(self):
        connection = FakeConnection(fail=AssertionError("no read when off"))
        with patch.object(reporting_snapshot, "_collect_tool_results", return_value=tool_results()):
            built, quality, allowed, blockers, comparison = reporting_snapshot.build_month_end_payload(
                connection, tenant_id=TENANT, subject_id=SUBJECT, period_start="2026-06-01", period_end=MONTH_END,
                reporting_currency="USD", environ={})
        self.assertEqual(canonical_json(built), canonical_json(payload()))
        self.assertIsNone(comparison)
        self.assertEqual(connection.calls, [])


def _tool():
    spec = importlib.util.spec_from_file_location("compare_monthly_report_figures_under_test",
                                                  ROOT / "scripts" / "compare_monthly_report_figures.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class RehearsalToolTests(unittest.TestCase):
    """The rehearsal command with a stand-in database and payload builder."""

    class Database:
        def __init__(self, subjects, stored=None):
            self.subjects, self.stored = subjects, stored or {}
            self.rollbacks = self.closed = 0
            self.statements: list[str] = []

        def execute(self, sql, params=None):
            self.statements.append(sql)
            if "FROM platform_subjects" in sql:
                rows = [{"subject_id": subject, "history_start_date": "2026-06-01", "reporting_currency": "USD"}
                        for subject in self.subjects]
                return SimpleNamespace(fetchall=lambda: rows)
            if "FROM reporting_snapshots" in sql:
                row = self.stored.get(params[1])
                return SimpleNamespace(fetchone=lambda: row)
            if "to_regclass('public.broker_onboarding_cases')" in sql:
                return SimpleNamespace(fetchone=lambda: {"present": None})  # no new onboarding on this copy
            raise AssertionError(sql)

        def rollback(self):
            self.rollbacks += 1

        def close(self):
            self.closed += 1

    def setUp(self):
        self.tool = _tool()
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.out = os.path.join(self.folder.name, "private", "september.json")

    def builder(self, *, differ_on=(), fail=None):
        def build(connection, *, tenant_id, subject_id, period_start, period_end, reporting_currency, environ, out):
            if fail and subject_id in fail.get(environ[reports.SETTING], ()):
                raise reporting_snapshot.ReportingSnapshotError(f"no benchmark for {subject_id}")
            built = payload()
            built["subject_id"] = subject_id
            change = {("risk.sharpe_ratio", "", "", ""): SHARPE + 0.5} if subject_id in differ_on else {}
            comparison = reports.apply_certified(FakeConnection(rows_for(built, change=change)), tenant_id=tenant_id,
                                                 subject_id=subject_id, payload=built, environ=environ, out=out)
            return built, "pass", True, (), comparison
        return build

    def run_tool(self, database, build, argv=None):
        printed: list[str] = []
        with patch("ibkr_etl.reporting_snapshot.build_month_end_payload", build), \
                patch.dict(os.environ, {"PORTFOLIO_TENANT_ID": TENANT}):
            code = self.tool.main(argv or ["--month", "2026-09", "--out", self.out],
                                  connect=lambda url, tenant: database, out=printed.append)
        return code, "\n".join(printed)

    def test_identical_reports_print_counts_only_and_write_the_private_file(self):
        subjects = ["U9300001", "U9300002"]
        stored_sha = sha256_text(canonical_json({**payload(), "subject_id": "U9300001"}))
        database = self.Database(subjects, {"U9300001": {"snapshot_id": "s1", "period_start": "2026-06-01",
                                                         "reporting_currency": "USD", "data_sha256": stored_sha,
                                                         "status": "frozen"}})
        code, text = self.run_tool(database, self.builder(differ_on={"U9300002"}))
        self.assertEqual(code, 0, text)
        self.assertIn("Identical with MONTHLY_REPORTS_CERTIFIED off and fallback: 2 of 2.", text)
        self.assertIn("Identical with off and on (certified values written): 1 of 2.", text)
        self.assertIn("Today's payload equals the stored frozen snapshot: 1 of 1", text)
        self.assertIn("risk.metrics.sharpe_ratio = risk.sharpe_ratio: 1 / 0 / 1 / 0", text)
        for leaked in ("U93", "9300001", repr(SHARPE)[:8], "1250.5", "Technology"):
            self.assertNotIn(leaked, text)
        mode = stat.S_IMODE(os.stat(self.out).st_mode)
        self.assertEqual(mode, 0o600)
        written = json.loads(Path(self.out).read_text(encoding="utf-8"))
        self.assertEqual(written["summary"]["identical_fallback"], 2)
        self.assertEqual([record["subject_id"] for record in written["subjects"]], subjects)
        self.assertEqual(database.closed, 1)
        self.assertGreaterEqual(database.rollbacks, 3)
        self.assertFalse([sql for sql in database.statements if not sql.lstrip().upper().startswith("SELECT")
                          and "SELECT" not in sql.upper()])

    def test_a_report_today_cannot_make_is_counted_not_failed(self):
        build = self.builder(fail={"off": {"U9300002"}, "fallback": {"U9300002"}, "on": {"U9300002"}})
        code, text = self.run_tool(self.Database(["U9300001", "U9300002"]), build)
        self.assertEqual(code, 0, text)
        self.assertIn("could not be made today either: 1", text)
        self.assertIn("Errors by type: ReportingSnapshotError 1", text)
        self.assertNotIn("no benchmark", text)

    def test_a_report_that_fails_only_with_the_setting_fails_the_rehearsal(self):
        build = self.builder(fail={"fallback": {"U9300002"}})
        code, text = self.run_tool(self.Database(["U9300001", "U9300002"]), build)
        self.assertEqual(code, 1)
        self.assertIn("failed only with the setting: 1", text)
        self.assertIn("off and fallback: 1 of 2", text)

    def test_the_private_file_must_be_outside_the_repository(self):
        with self.assertRaises(self.tool.RehearsalError):
            self.tool.main(["--month", "2026-09", "--out", str(ROOT / "september.json")],
                           connect=lambda url, tenant: self.Database([]), out=lambda line: None)
        with self.assertRaises(self.tool.RehearsalError):
            self.tool.main(["--month", "2026-13", "--out", self.out], connect=lambda url, tenant: self.Database([]),
                           out=lambda line: None)
        self.assertFalse(os.path.exists(self.out))

    def test_only_the_named_subjects(self):
        database = self.Database(["U9300001", "U9300002"])
        code, text = self.run_tool(database, self.builder(),
                                   ["--month", "2026-09", "--out", self.out, "--subject", "U9300002"])
        self.assertEqual(code, 0)
        self.assertIn("Subjects: 1;", text)

    def test_live_new_onboarding_subjects_are_made_with_their_paths_arguments(self):
        """Subjects that went live through the new onboarding freeze their month through
        onboarding_month_end (composite_reports or designed_reports), with that path's arguments."""
        from ibkr_etl import composite_reports, designed_reports, onboarding_month_end
        from ibkr_etl.report_profiles import ReportProfileError

        def case(subject, *, start="2026-07-15", currency="HKD", style=None, kind="broker_account"):
            return {"case_id": f"case-{subject}", "source_kind": kind, "target_subject_id": subject,
                    "reporting_currency": currency, "history_start_date": start, "report_style": style,
                    "definition_sha256": "a" * 64 if style else None}

        cases = [case("U9300003"),                                             # designed: made
                 case("COMP-1", style="alpha-world-ms", kind="composite_portfolio"),  # composite: made
                 case("U9300004", start="2026-10-01"),                         # no completed month by then
                 case("U9300005"),                                             # already frozen for the month
                 case("U9300001", start="2026-06-01", currency="USD"),         # as the month-end job makes it
                 case("COMP-2", style="alpha-world-t", kind="composite_portfolio"),  # its profile fails today
                 case("U9300006"),                                             # waiting for its data
                 case("U9300007", currency="EUR")]                             # its approved design is not in EUR

        def profile(connection, subject_id, *, tenant_id=None):
            if subject_id == "COMP-2":
                raise ReportProfileError(f"{subject_id} has no approved definition in its report currency")
            return SimpleNamespace(built_in=False, style="alpha-world-ms", definition_sha256="a" * 64,
                                   inception="2026-01-05", reporting_currency="HKD")

        def assets(connection, style):  # both designs' approved files are stored
            return SimpleNamespace(template_version=f"{style}-v1")

        def design(connection, tenant_id, case_id, subject_id):  # each designed case's approved design
            return {"spec": {"currencies": ["HKD", "USD"]}}

        made: dict[str, tuple] = {}
        inner = self.builder()

        def build(connection, *, tenant_id, subject_id, period_start, period_end, reporting_currency, environ, out):
            made.setdefault(subject_id, (period_start, period_end, reporting_currency))
            return inner(connection, tenant_id=tenant_id, subject_id=subject_id, period_start=period_start,
                         period_end=period_end, reporting_currency=reporting_currency, environ=environ, out=out)

        with patch.object(onboarding_month_end, "live_cases", lambda connection, tenant_id: cases), \
                patch.object(composite_reports, "load_profile", profile), \
                patch.object(composite_reports, "design_assets", assets), \
                patch.object(designed_reports, "approved_design", design), \
                patch.object(reporting_snapshot, "active_month_end_snapshot_id",
                             lambda connection, *, tenant_id, subject_id, period_end:
                             "frozen" if subject_id == "U9300005" else None), \
                patch.object(composite_reports, "month_end_ready", lambda *args: True), \
                patch.object(designed_reports, "month_ready", lambda connection, kind, subject, end: subject != "U9300006"):
            code, text = self.run_tool(self.Database(["U9300001"]), build)
        self.assertEqual(code, 0, text)
        self.assertEqual(made, {"U9300001": ("2026-06-01", MONTH_END, "USD"),
                                "U9300003": ("2026-07-15", MONTH_END, "HKD"),
                                "COMP-1": ("2026-01-05", MONTH_END, "HKD")})
        self.assertIn("Subjects: 5; made with today's code: 3; could not be made today either: 2; "
                      "failed only with the setting: 0.", text)
        self.assertIn("Identical with MONTHLY_REPORTS_CERTIFIED off and fallback: 3 of 3.", text)
        self.assertIn("Live new-onboarding subjects (onboarding_month_end): 8; made with that path's arguments: 2 "
                      "(identical off and fallback: 2); its report profile or design fails today: 2; not made by that "
                      "path for this month: already frozen 1, waiting for data 1, no completed month 1; the same as the "
                      "month-end job's: 1.", text)
        self.assertIn("Errors by type: CompositeReportError 1, DesignedReportError 1", text)  # as that path raises
        for leaked in ("U93", "COMP-", "case-", "2026-07-15", "approved definition", "EUR"):
            self.assertNotIn(leaked, text)
        written = json.loads(Path(self.out).read_text(encoding="utf-8"))
        onboarding = {record["subject_id"]: record for record in written["subjects"] if record["source"] == "onboarding"}
        self.assertEqual(set(onboarding), {"U9300003", "COMP-1", "COMP-2", "U9300007"})
        self.assertEqual((onboarding["COMP-1"]["design"], onboarding["U9300003"]["design"]), ("composite", "designed"))
        self.assertTrue(onboarding["U9300003"]["identical_fallback"])
        self.assertFalse(onboarding["COMP-2"]["made_today"])

    def test_a_live_onboarding_report_that_fails_only_with_the_setting_fails_the_rehearsal(self):
        from ibkr_etl import designed_reports, onboarding_month_end

        live = [{"case_id": "c", "source_kind": "broker_account", "target_subject_id": "U9300003",
                 "reporting_currency": "USD", "history_start_date": "2026-07-15", "report_style": None,
                 "definition_sha256": None}]
        with patch.object(onboarding_month_end, "live_cases", lambda connection, tenant_id: live), \
                patch.object(reporting_snapshot, "active_month_end_snapshot_id", lambda connection, **kw: None), \
                patch.object(designed_reports, "approved_design", lambda *args: {"spec": {"currencies": ["USD"]}}), \
                patch.object(designed_reports, "month_ready", lambda *args: True):
            code, text = self.run_tool(self.Database(["U9300001"]), self.builder(fail={"fallback": {"U9300003"}}))
        self.assertEqual(code, 1)
        self.assertIn("failed only with the setting: 1", text)
        self.assertIn("off and fallback: 1 of 2", text)


if __name__ == "__main__":
    unittest.main()
