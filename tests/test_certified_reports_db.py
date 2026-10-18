"""Monthly reports and Performance › Attribution on certified figures, on a real PostgreSQL.

Needs a disposable server: set AGENT_TEST_PG_ADMIN_DSN to a superuser DSN (CI starts one).
Skipped otherwise. The database is test_certified_build_db's (every migration, one synthetic
account with four and a half months of NAV, two holdings and cash, throwaway logins), with a
second benchmark and the month-end registration the month-end job needs, and one published
certified build. Synthetic data only.

It checks that the Attribution figures are the page's own, that a month-end report made with
MONTHLY_REPORTS_CERTIFIED=fallback is byte for byte today's (the same snapshot id), that a failed
certified read leaves the report and its transaction alone, and that the rehearsal tool
(scripts/compare_monthly_report_figures.py) writes nothing and prints counts only, and makes a
subject that went live through the new onboarding with that path's own arguments.
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import logging
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import test_certified_build_db as base
import test_medallion_layers_db as layers
from ibkr_etl import certified_build as build
from ibkr_etl import certified_figures as cf
from ibkr_etl import certified_pages, certified_reports, reporting_snapshot
from ibkr_etl.util import canonical_json, sha256_text

ADMIN_DSN, ROOT, TENANT, SUBJECT = base.ADMIN_DSN, base.ROOT, base.TENANT, base.SUBJECT
LAST = base.LAST.isoformat()
MONTH_ENDS = ("2026-06-30", "2026-07-31", "2026-08-31", "2026-09-30")
PERIOD_START, PERIOD_END = base.FIRST.isoformat(), "2026-09-30"


def setUpModule():
    layers.setUpModule()


def _second_benchmark(database: str) -> None:
    """A secondary benchmark, as every month-end report needs, refreshed by the job's own code."""
    import pandas as pd
    import random

    from ibkr_etl.db import connect
    from ibkr_etl.provisional_benchmark import refresh_and_materialize

    with layers._connect(database) as owner:
        owner.execute("INSERT INTO benchmark_master (benchmark_id, symbol, name, provider, currency, return_convention, "
                      "license_status, created_at, updated_at) VALUES ('SYNIDX2', '^SYN2', 'Second synthetic index', "
                      "'test', 'USD', 'price', 'approved', '2026-01-01', '2026-01-01')")
        owner.execute("INSERT INTO portfolio_benchmark_mapping (subject_id, benchmark_id, mapping_type, effective_from, "
                      "reporting_currency, created_at) VALUES (%s, 'SYNIDX2', 'secondary', '2026-01-01', 'USD', "
                      "'2026-01-01')", (SUBJECT,))
        owner.execute("SELECT set_config('app.tenant_id', %s, false)", (TENANT,))
        owner.execute("INSERT INTO subject_operating_registrations (registration_id, subject_id, capability, "
                      "operating_state, effective_from, configuration_sha256) VALUES "
                      "(gen_random_uuid(), %s, 'month_end_reporting', 'active', '2026-06-01', %s)",
                      (SUBJECT, "0" * 64))
        owner.commit()
    days = base._business_days(base.FIRST, base.LAST)
    frames = {}
    for symbol, seed, level in (("^SYN", 5, 5000.0), ("^SYN2", 7, 1200.0)):
        rng, closes = random.Random(seed), []
        for _day in days:
            level *= 1 + rng.gauss(0.0003, 0.008)
            closes.append(level)
        frames[symbol] = pd.DataFrame({"Close": closes, "Adj Close": closes, "Volume": [1] * len(days)},
                                      index=pd.DatetimeIndex(days, name="Date"))

    class Ticker:
        def __init__(self, symbol):
            self.symbol = symbol

        def history(self, **_kwargs):
            return frames[self.symbol]

    connection = connect(Path("/tmp/unused.sqlite3"), layers._url(database, layers.OWNER), TENANT)
    try:
        refresh_and_materialize(connection, account_id=SUBJECT, start_date=base.FIRST, end_date=base.LAST,
                                ticker_factory=Ticker)
        connection.commit()
    finally:
        connection.close()


def _live_onboarding_case(database: str, *, history_start: str) -> None:
    """SUBJECT as a broker account that went live through the new onboarding, in the factsheet
    design approved with its historical reports, from a later history start than its registration."""
    import uuid

    from ibkr_etl.composite_reports import CHECK_ID
    from ibkr_etl.designed_reports import engine

    template = b"%PDF synthetic owner template"
    template_sha256 = sha256_text(template.decode())
    spec = {"design_id": "synthetic-design", "title": "Synthetic design", "currencies": ["USD"]}
    spec_sha256 = engine.spec_sha256(spec)
    connection_id, case_id, draft_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    with layers._connect(database) as owner:
        owner.execute("SELECT set_config('app.tenant_id', %s, false)", (TENANT,))
        owner.execute("INSERT INTO broker_connections (connection_id, tenant_id, broker, environment, display_name, "
                      "key_vault_secret_name, credential_fingerprint, credential_expires_on, status) VALUES "
                      "(%s, %s, 'ibkr_flex', 'live', 'Synthetic connection', %s, %s, '2027-06-30', 'healthy')",
                      (connection_id, TENANT, "ibkr-flex-source-" + "0" * 32, "0" * 16))
        owner.execute("INSERT INTO broker_connection_accounts (tenant_id, connection_id, broker_account_id, "
                      "discovery_status) VALUES (%s, %s, %s, 'live')", (TENANT, connection_id, SUBJECT))
        owner.execute("INSERT INTO broker_onboarding_cases (case_id, tenant_id, source_kind, connection_id, broker, "
                      "broker_account_id, target_subject_id, display_name, reporting_currency, history_start_date, "
                      "subject_id, stage, status, went_live_at) VALUES (%s, %s, 'broker_account', %s, 'ibkr_flex', "
                      "%s, %s, 'Synthetic account', 'USD', %s, %s, 'first_month_end', 'live', '2026-07-01')",
                      (case_id, TENANT, connection_id, SUBJECT, SUBJECT, history_start, SUBJECT))
        owner.execute("INSERT INTO factsheet_design_files (tenant_id, sha256, content_type, byte_count, content, "
                      "created_at) VALUES (%s, %s, 'application/pdf', %s, %s, '2026-06-20')",
                      (TENANT, template_sha256, len(template), template))
        owner.execute("INSERT INTO factsheet_design_drafts (draft_id, tenant_id, subject_id, title, template_sha256, "
                      "more_examples_json, status, current_version, build_json, created_by, created_at, updated_at) "
                      "VALUES (%s, %s, %s, 'Synthetic design', %s, '[]', 'submitted', 1, '{}', 'test', "
                      "'2026-06-20', '2026-06-20')", (draft_id, TENANT, SUBJECT, template_sha256))
        owner.execute("INSERT INTO factsheet_design_versions (tenant_id, draft_id, version, spec_json, spec_sha256, "
                      "source, summary, changes_json, checks_json, created_by, created_at) VALUES "
                      "(%s, %s, 1, %s, %s, 'builder', 'Drafted', '[]', '{}', 'test', '2026-06-20')",
                      (TENANT, draft_id, canonical_json(spec), spec_sha256))
        design = {"draft_id": str(draft_id), "version": 1, "spec_sha256": spec_sha256}
        for kind, terms in (("historical_reports", {"checks": {CHECK_ID: {"evidence": {"design": design}}}}),
                            ("go_live", {"confirmation": "synthetic"})):
            owner.execute("INSERT INTO broker_onboarding_approvals (tenant_id, case_id, approval_kind, terms_json, "
                          "terms_sha256, approved_by_identity_sha256) VALUES (%s, %s, %s, %s::jsonb, %s, %s)",
                          (TENANT, case_id, kind, canonical_json(terms), sha256_text(canonical_json(terms)), "0" * 64))
        owner.commit()


def _rehearsal_script():
    spec = importlib.util.spec_from_file_location("compare_monthly_report_figures_under_test",
                                                  ROOT / "scripts" / "compare_monthly_report_figures.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _digest(payload) -> str:
    return sha256_text(canonical_json(payload))


@unittest.skipUnless(ADMIN_DSN, "AGENT_TEST_PG_ADMIN_DSN is not set")
class ReportsOnCertifiedFiguresTests(base._BuildDatabase):
    """In order: the Attribution figures, the page, the reports both ways, a failed read, the rehearsal."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                _second_benchmark(cls.database)
            lines: list[str] = []
            code = build.run(database_url=cls.app_url, web=cls.web, out=lines.append, now=base.NOW)
            assert code == 0, lines
        except Exception:
            super().tearDownClass()
            raise

    @contextlib.contextmanager
    def _report_connection(self):
        """The month-end job's connection: the app login, through the PostgreSQL adapter."""
        from ibkr_etl.db import connect

        connection = connect(Path("/tmp/unused.sqlite3"), self.app_url, TENANT)
        try:
            yield connection
        finally:
            connection.rollback()
            connection.close()

    def _make(self, connection, selected: str, *, period_end: str = PERIOD_END):
        lines: list[str] = []
        payload, quality, _allowed, _blockers, comparison = reporting_snapshot.build_month_end_payload(
            connection, tenant_id=TENANT, subject_id=SUBJECT, period_start=PERIOD_START, period_end=period_end,
            reporting_currency="USD", environ={certified_reports.SETTING: selected}, out=lines.append)
        connection.rollback()
        return payload, quality, comparison, lines

    def test_1_attribution_is_certified_on_each_months_last_date_and_the_newest(self):
        [(status,)] = self._owner("SELECT status FROM gold.certified_builds")
        self.assertEqual(status, "published")
        published = self._published()
        attribution = [key for key in published if key[1].startswith("attribution.")]
        self.assertEqual(sorted({key[5] for key in attribution}), [*MONTH_ENDS, LAST])  # never mid-month
        user = build.BUILD_USER
        for as_of in (*MONTH_ENDS, LAST):
            with self.subTest(as_of=as_of):
                period = f"month_{as_of[:7]}"
                page = self.web.monthly_attribution_view(SUBJECT, month=as_of[:7], as_of=as_of, user=user)
                rows = [key for key in attribution if key[5] == as_of]
                self.assertTrue({key[2] for key in rows} == {period})
                if not page.get("available"):
                    self.assertEqual(sorted(key[1] for key in rows),
                                     sorted(figure_id for figure_id, _ in certified_pages.ATTRIBUTION_TOTALS))
                    self.assertTrue(all(published[key]["status"] != "ok" for key in rows))
                    continue
                self.assertEqual(page["period_end"], as_of)
                for figure_id, name in certified_pages.ATTRIBUTION_TOTALS:
                    self.assertEqual(self._value(figure_id, as_of, period=period), page[name], figure_id)
                for entry in page["security_contributions"]:
                    self.assertEqual(self._value("attribution.holding_contribution", as_of, period=period,
                                                 item=str(entry["conid"])), entry["contribution"])
                for entry in page["sector_contributions"]:
                    self.assertEqual(self._value("attribution.sector_contribution", as_of, period=period,
                                                 item=str(entry["sector"])), entry["contribution"])
                self.assertEqual(len(rows), 3 + len(page["security_contributions"]) + len(page["sector_contributions"]))
        available = [as_of for as_of in MONTH_ENDS[1:]
                     if self.web.monthly_attribution_view(SUBJECT, month=as_of[:7], as_of=as_of, user=user)["available"]]
        self.assertEqual(available, list(MONTH_ENDS[1:]))  # the synthetic months after the first are made

    def test_2_the_page_shows_its_month_certified_and_otherwise_todays(self):
        user = build.BUILD_USER
        today = self.web.monthly_attribution_view(SUBJECT, month="2026-09", as_of=LAST, user=user)
        with patch.dict(os.environ, {cf.PAGES_SETTING: "attribution=on"}):
            shown = self.web.monthly_attribution_view(SUBJECT, month="2026-09", as_of=LAST, user=user)
            earlier = self.web.monthly_attribution_view(SUBJECT, month="2026-09", as_of="2026-09-15", user=user)
        self.assertEqual(shown["certified"]["as_of"], "2026-09-30")
        self.assertEqual({key: value for key, value in shown.items() if key != "certified"}, today)
        self.assertNotIn("certified", earlier)  # nothing certified for September on or before the 15th
        self.assertEqual(earlier, today)

    def test_3_fallback_makes_todays_report_byte_for_byte(self):
        with self._report_connection() as connection:
            made = {selected: self._make(connection, selected) for selected in certified_reports.MODES}
        off, fallback, on = made["off"], made["fallback"], made["on"]
        self.assertEqual(off[0]["data_as_of"], PERIOD_END)
        self.assertTrue(off[0]["monthly_reporting"]["available"])
        self.assertEqual((off[2], off[3]), (None, []))               # off: nothing read, nothing logged
        self.assertEqual(_digest(fallback[0]), _digest(off[0]))
        self.assertEqual(fallback[1], off[1])

        comparison = fallback[2]
        by_figure = {}
        for entry in comparison["fields"]:
            by_figure.setdefault(entry["figure_id"], set()).add(entry["status"])
        for figure_id in ("attribution.portfolio_return", "attribution.holdings_contribution",
                          "attribution.residual_contribution", "attribution.holding_contribution",
                          "attribution.sector_contribution", "overview.nav", "overview.positions"):
            self.assertEqual(by_figure.get(figure_id), {"equal"}, figure_id)
        self.assertEqual(comparison["used"], sum(entry["status"] == "equal" and entry["write"]
                                                 for entry in comparison["fields"]))
        [line] = fallback[3]
        self.assertTrue(line.startswith(certified_reports.LOG_PREFIX + " "))
        logged = json.loads(line.split(" ", 1)[1])
        self.assertEqual((logged["mode"], logged["subject"]), ("fallback", build.mask(SUBJECT)))
        self.assertNotIn(SUBJECT, line)
        nav = off[0]["portfolio_summary"]["latest_nav"]["total_text"]
        self.assertNotIn(str(nav), line)
        self.assertNotIn(str(off[0]["monthly_reporting"]["portfolio_monthly_return"]), line)

        # on: the certified values written; a field differs from today's only where its certified value does.
        changed = {entry["field_id"] for entry in on[2]["fields"]
                   if entry["write"] and entry["status"] in ("close", "different")}
        if not changed:
            self.assertEqual(_digest(on[0]), _digest(off[0]))
        self.assertEqual(on[0]["monthly_reporting"], off[0]["monthly_reporting"])

    def test_4_a_frozen_snapshot_has_the_same_id_either_way(self):
        made = {}
        for selected in ("off", "fallback"):  # the freeze commits: the second finds the first's row
            with self._report_connection() as connection, \
                    patch.dict(os.environ, {certified_reports.SETTING: selected}), \
                    contextlib.redirect_stdout(io.StringIO()) as printed:
                made[selected] = reporting_snapshot.freeze_month_end_snapshot(
                    connection, tenant_id=TENANT, subject_id=SUBJECT, period_start=PERIOD_START,
                    period_end=PERIOD_END, reporting_currency="USD")
            self.assertEqual(certified_reports.LOG_PREFIX in printed.getvalue(), selected == "fallback")
        self.assertEqual((made["off"].inserted, made["fallback"].inserted), (True, False))
        self.assertEqual((made["fallback"].snapshot_id, made["fallback"].data_sha256),
                         (made["off"].snapshot_id, made["off"].data_sha256))
        self.assertEqual(self._owner("SELECT count(*), min(status) FROM reporting_snapshots WHERE period_end = %s",
                                     PERIOD_END), [(1, "frozen")])

    def test_5_a_failed_read_leaves_the_report_and_its_transaction_alone(self):
        august = "2026-08-31"
        missing = certified_reports.READ_SQL.replace(certified_pages.VIEW, "gold.no_such_view")
        with self._report_connection() as connection:
            today = self._make(connection, "off", period_end=august)[0]
            with patch.object(certified_reports, "READ_SQL", missing), \
                    patch.dict(os.environ, {certified_reports.SETTING: "fallback"}), \
                    contextlib.redirect_stdout(io.StringIO()) as printed:
                payload, _, _, _, comparison = reporting_snapshot.build_month_end_payload(
                    connection, tenant_id=TENANT, subject_id=SUBJECT, period_start=PERIOD_START,
                    period_end=august, reporting_currency="USD")
                self.assertEqual(connection.execute("SELECT 1 AS one").fetchone()["one"], 1)  # still usable
                connection.rollback()
                # The freeze reads, fails to read the certified figures, and still stores today's snapshot.
                result = reporting_snapshot.freeze_month_end_snapshot(
                    connection, tenant_id=TENANT, subject_id=SUBJECT, period_start=PERIOD_START,
                    period_end=august, reporting_currency="USD")
        self.assertEqual(comparison["status"], "error")
        self.assertEqual(_digest(payload), _digest(today))
        self.assertEqual((result.inserted, result.data_sha256), (True, _digest(today)))
        logged = [json.loads(line.split(" ", 1)[1]) for line in printed.getvalue().splitlines()
                  if line.startswith(certified_reports.LOG_PREFIX)]
        self.assertEqual([(line["status"], line["error"]) for line in logged],
                         [("error", "UndefinedTable"), ("error", "UndefinedTable")])
        self.assertEqual(self._owner("SELECT data_sha256 FROM reporting_snapshots WHERE period_end = %s", august),
                         [(_digest(today),)])

    def test_6_the_rehearsal_writes_nothing_and_prints_counts_only(self):
        script = _rehearsal_script()
        # A frozen September snapshot, as the restored copy will have.
        with self._report_connection() as connection, contextlib.redirect_stdout(io.StringIO()):
            frozen = reporting_snapshot.freeze_month_end_snapshot(
                connection, tenant_id=TENANT, subject_id=SUBJECT, period_start=PERIOD_START,
                period_end=PERIOD_END, reporting_currency="USD")
            connection.commit()
        tables = ("reporting_snapshots", "gold.certified_figures", "gold.certified_builds", "data_quality_results")
        before = {table: self._owner(f"SELECT count(*) FROM {table}") for table in tables}

        # The session is read-only: a write fails instead of happening.
        connection = script._connect(self.app_url, TENANT)
        try:
            self.assertEqual(connection.execute("SHOW default_transaction_read_only").fetchone()[0], "on")
            with self.assertRaises(Exception) as caught:
                connection.execute("DELETE FROM reporting_snapshots")
            self.assertEqual(type(caught.exception).__name__, "ReadOnlySqlTransaction")
        finally:
            connection.rollback()
            connection.close()

        audit = logging.getLogger(script.QUIET_LOGGERS[0])
        level = audit.level
        self.addCleanup(audit.setLevel, level)
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "private" / "monthly-reports-2026-09.json"
            printed: list[str] = []
            with patch.dict(os.environ, {"DATABASE_URL": self.app_url, "PORTFOLIO_TENANT_ID": TENANT}), \
                    contextlib.redirect_stdout(io.StringIO()) as stray:
                code = script.main(["--month", "2026-09", "--out", str(target)], out=printed.append)
            self.assertEqual(code, 0, printed)
            self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(target.parent.stat().st_mode) & 0o077, 0)
            full = json.loads(target.read_text())
        self.assertEqual({table: self._owner(f"SELECT count(*) FROM {table}") for table in tables}, before)

        text = "\n".join(printed) + stray.getvalue()
        self.assertIn("Subjects: 1; made with today's code: 1; could not be made today either: 0; "
                      "failed only with the setting: 0.", text)
        self.assertIn("Identical with MONTHLY_REPORTS_CERTIFIED off and fallback: 1 of 1.", text)
        self.assertIn("Today's payload equals the stored frozen snapshot: 1 of 1 subjects that have one.", text)
        self.assertIn("monthly_reporting.portfolio_monthly_return = attribution.portfolio_return: 1 / 0 / 0 / 0", text)
        self.assertNotIn(SUBJECT, text)
        self.assertNotIn("U93", text)
        [nav] = [entry["today"] for entry in full["subjects"][0]["comparison"]["fields"]
                 if entry["field_id"] == "portfolio_summary.latest_nav.total_text"]
        self.assertNotIn(str(nav), text)

        [record] = full["subjects"]
        self.assertEqual(record["subject_id"], SUBJECT)
        self.assertEqual(record["digests"]["off"], record["digests"]["fallback"])
        self.assertEqual(record["digests"]["off"], frozen.data_sha256)
        self.assertEqual(record["stored_snapshot"]["snapshot_id"], frozen.snapshot_id)
        self.assertTrue(record["stored_snapshot"]["same_as_off"])
        self.assertEqual(full["summary"]["identical_fallback"], 1)
        self.assertTrue(all(line.startswith(certified_reports.LOG_PREFIX) for lines in record["log_lines"].values()
                            for line in lines))


    def test_7_the_rehearsal_makes_a_live_onboarding_subject_with_that_paths_arguments(self):
        from ibkr_etl import onboarding_month_end

        script = _rehearsal_script()
        _live_onboarding_case(self.database, history_start="2026-06-15")
        with self._report_connection() as connection:
            [case] = onboarding_month_end.live_cases(connection, TENANT)
        self.assertEqual((case["target_subject_id"], str(case["history_start_date"])), (SUBJECT, "2026-06-15"))

        tables = ("reporting_snapshots", "gold.certified_figures", "broker_onboarding_cases",
                  "broker_onboarding_approvals", "data_quality_results")
        before = {table: self._owner(f"SELECT count(*) FROM {table}") for table in tables}
        audit = logging.getLogger(script.QUIET_LOGGERS[0])
        self.addCleanup(audit.setLevel, audit.level)
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "private" / "monthly-reports-2026-07.json"
            printed: list[str] = []
            with patch.dict(os.environ, {"DATABASE_URL": self.app_url, "PORTFOLIO_TENANT_ID": TENANT}), \
                    contextlib.redirect_stdout(io.StringIO()) as stray:
                code = script.main(["--month", "2026-07", "--out", str(target)], out=printed.append)
            self.assertEqual(code, 0, printed)
            full = json.loads(target.read_text())
        self.assertEqual({table: self._owner(f"SELECT count(*) FROM {table}") for table in tables}, before)

        text = "\n".join(printed) + stray.getvalue()
        # July has no frozen snapshot: the month-end job's list makes it from the registration's history
        # start, and the onboarding path (designed_reports) from the case's.
        self.assertIn("Subjects: 2; made with today's code: 2; could not be made today either: 0; "
                      "failed only with the setting: 0.", text)
        self.assertIn("Live new-onboarding subjects (onboarding_month_end): 1; made with that path's arguments: 1 "
                      "(identical off and fallback: 1); its report profile or design fails today: 0; not made by "
                      "that path for this month: already frozen 0, waiting for data 0, no completed month 0; the "
                      "same as the month-end job's: 0.", text)
        self.assertIn("Identical with MONTHLY_REPORTS_CERTIFIED off and fallback: 2 of 2.", text)
        self.assertNotIn(SUBJECT, text)
        self.assertNotIn("U93", text)
        self.assertNotIn("2026-06-15", text)
        self.assertNotIn(str(case["case_id"]), text)

        records = {record["source"]: record for record in full["subjects"]}
        self.assertEqual(set(records), {"month_end_job", "onboarding"})
        onboarding = records["onboarding"]
        self.assertEqual((onboarding["subject_id"], onboarding["case_id"], onboarding["design"]),
                         (SUBJECT, str(case["case_id"]), "designed"))
        self.assertEqual((onboarding["period_start"], onboarding["reporting_currency"]), ("2026-06-15", "USD"))
        self.assertEqual(records["month_end_job"]["period_start"], PERIOD_START)
        for record in records.values():
            self.assertEqual(record["digests"]["off"], record["digests"]["fallback"])
            self.assertEqual(record["errors"], {})
        self.assertNotEqual(onboarding["digests"]["off"], records["month_end_job"]["digests"]["off"])
        self.assertEqual(full["summary"]["onboarding_cases"]["live"], 1)

        # September is frozen (test 4): the onboarding path reuses that snapshot and makes nothing.
        connection = script._connect(self.app_url, TENANT)
        try:
            listed = script.subjects(connection, tenant_id=TENANT, period_end=PERIOD_END)
            live, counts = script.onboarding_subjects(connection, tenant_id=TENANT, period_end=PERIOD_END,
                                                      listed=listed)
        finally:
            connection.rollback()
            connection.close()
        self.assertEqual((live, counts), ([], {"frozen_already": 1, "live": 1, "profile_errors": 0, "rehearsed": 0}))

if __name__ == "__main__":
    unittest.main()
