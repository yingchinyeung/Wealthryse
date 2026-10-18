"""Layer quality (medallion review item 7) on a real PostgreSQL built from every migration.

Needs a disposable server: set AGENT_TEST_PG_ADMIN_DSN to a superuser DSN (CI starts
one). Skipped otherwise. Creates and drops its own database and roles and writes
synthetic rows only. Builds the database as a new environment does, but leaves
072_quality_check_results.sql for bootstrap_postgres.py --only-migration, the step
production runs, so the logins get the grants it gives them. Then runs every check as
the owner and as the approval login (the autonomous administrator's DATABASE_URL),
records the history, reads the weekly report from it and runs the administrator's hook.
"""
from __future__ import annotations

import contextlib
import io
import os
import unittest
import uuid
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import test_medallion_layers_db as layers
from ibkr_etl import layer_freshness, layer_quality
from ibkr_etl.market_calendar import CALENDARS

ADMIN_DSN = layers.ADMIN_DSN
ROOT = Path(__file__).resolve().parents[1]
CONTRACTS = ROOT / "infra" / "medallion" / "contracts"
TENANT, OTHER_TENANT, OWNER = layers.TENANT, layers.OTHER_TENANT, layers.OWNER
APP, WEB, APPROVAL = "quality_test_app", "quality_test_web", "quality_test_approval"
MIGRATION = "072_quality_check_results.sql"
# Friday 16 Oct 2026, after the chain: data for Thursday 15 Oct, a session in both markets.
NOW = datetime(2026, 10, 16, 10, 31, tzinfo=timezone.utc)
FIRST_DAY, LAST_DAY = date(2026, 9, 1), date(2026, 10, 15)
ACCOUNTS = ("U9100001", "U9100002", "U9100003")  # synthetic
JUMPS, EMPTIED = "U9100003", "U9100002"  # NAV up 30% on the last day; no positions on the last day
ENVIRON = {layer_quality.CONTRACTS_DIR_ENV: str(CONTRACTS)}


def setUpModule():
    layers.setUpModule()  # migrations from this checkout, as CI tests the installed package


def _business_days() -> list[date]:
    days, day = [], FIRST_DAY
    while day <= LAST_DAY:
        if any(CALENDARS[market].is_session(day) for market in ("XHKG", "XNYS")):
            days.append(day)
        day += timedelta(days=1)
    return days


def stamp(moment: datetime, minutes: int = 0) -> str:
    """An ISO timestamp, as the jobs write them."""
    return (moment + timedelta(minutes=minutes)).isoformat()


def _seed(owner) -> None:
    """Six weeks of synthetic data on business days, written as the owner."""
    owner.execute("SET search_path = bronze, silver, gold, public")
    for account in ACCOUNTS:
        owner.execute("INSERT INTO accounts (account_id, first_seen_at, last_seen_at, source_json) "
                      "VALUES (%s, '2026-01-01', '2026-10-15', '{}')", (account,))
    for conid in ("1001", "1002"):
        owner.execute("INSERT INTO instruments (conid, symbol, first_seen_at, last_seen_at, source_json) "
                      "VALUES (%s, 'SYN', '2026-01-01', '2026-10-15', '{}')", (conid,))
    owner.execute("INSERT INTO benchmark_master (benchmark_id, symbol, name, currency, return_convention, "
                  "created_at, updated_at) VALUES ('SYN', '^SYN', 'Synthetic', 'USD', 'price', "
                  "'2026-01-01', '2026-01-01')")
    for number, day in enumerate(_business_days()):
        text, arrived = day.isoformat(), datetime.combine(day + timedelta(days=1), time(8, 1), tzinfo=timezone.utc)
        run = owner.execute(
            "INSERT INTO ingestion_runs (run_type, started_at, completed_at, status) "
            "VALUES ('synthetic-flex', %s, %s, 'success') RETURNING run_id", (stamp(arrived), stamp(arrived, 2))
        ).fetchone()[0]
        owner.execute("INSERT INTO raw_reports (run_id, received_at, content_sha256, byte_count, file_path) "
                      "VALUES (%s, %s, md5(%s) || md5(%s), 10, 'synthetic.xml')",
                      (run, stamp(arrived, 1), text, text))
        owner.execute("INSERT INTO flex_rows (account_id, section, record_type, source_key, row_sha256, "
                      "data_json, first_seen_run_id, last_seen_run_id) VALUES ('U9100001', 'NAV', 'r', %s, "
                      "md5(%s) || md5(%s), '{}', %s, %s)", (text, text, "flex", run, run))
        for account in ACCOUNTS:
            nav = 1300 if account == JUMPS and day == LAST_DAY else 1000 + number
            owner.execute("INSERT INTO nav_snapshots (account_id, snapshot_date, currency, total_text, "
                          "row_sha256, last_seen_run_id) VALUES (%s, %s, 'USD', %s, md5(%s) || md5(%s), %s)",
                          (account, text, f"{nav}.00", account + text, "nav", run))
            if account == EMPTIED and day == LAST_DAY:
                continue
            for conid in ("1001", "1002"):
                owner.execute("INSERT INTO position_snapshots (account_id, snapshot_date, conid, quantity_text, "
                              "row_sha256, last_seen_run_id) VALUES (%s, %s, %s, '10', md5(%s) || md5(%s), %s)",
                              (account, text, conid, account + text + conid, "position", run))
            owner.execute(
                "INSERT INTO performance_comparison_daily (subject_id, benchmark_id, observation_date, "
                "reporting_currency, portfolio_value, benchmark_level, calculation_version, quality_status, "
                "generated_at) VALUES (%s, 'SYN', %s, 'USD', 1, 1, 'v', 'pass', %s)",
                (account, text, stamp(arrived, 30)))
            owner.execute(
                "INSERT INTO risk_analytics_snapshots (subject_id, as_of_date, analytics_version, "
                "comparison_version, source_data_sha256, payload_sha256, payload_json, quality_status, "
                "generated_at, duration_ms, timings_json) VALUES (%s, %s, 'v', 'v', md5(%s) || md5(%s), "
                "md5(%s) || md5(%s), '{}', 'pass', %s, 1, '{}')",
                (account, text, account, text, text, account, stamp(arrived, 55)))
            for scenario in ("lehman_2008", "china_2015", "covid_2020", "rates_2022"):
                owner.execute(
                    "INSERT INTO crisis_scenario_results (subject_id, holdings_as_of, scenario_id, "
                    "calculation_version, reporting_currency, crisis_start, crisis_end, available, "
                    "maximum_drawdown, result_json, generated_at) VALUES (%s, %s, %s, 'v', 'USD', '2008-09-15', "
                    "'2009-03-09', true, -0.2, '{}', %s)", (account, text, scenario, stamp(arrived, 80)))
            for observed in ("2008-09-15", "2009-03-09"):
                owner.execute(
                    "INSERT INTO crisis_replay_daily (subject_id, holdings_as_of, observation_date, "
                    "normalized_value, drawdown, crisis_start, crisis_end, calculation_version, quality_status, "
                    "generated_at) VALUES (%s, %s, %s, 1, 0, '2008-09-15', '2009-03-09', 'v', 'pass', %s)",
                    (account, text, observed, stamp(arrived, 31)))
        owner.execute("INSERT INTO benchmark_prices (benchmark_id, observation_date, level, currency, "
                      "observation_status, provider, fetched_at, quality_status) VALUES "
                      "('SYN', %s, 100, 'USD', 'provisional_close', 'p', %s, 'pass')",
                      (text, stamp(arrived, 30)))
        owner.execute("INSERT INTO market_data_prices (instrument_id, price_date, symbol, instrument_name, "
                      "price_type, currency, price, source, source_url, fetched_at) VALUES ('syn', %s, 'SYN', "
                      "'Synthetic', 'unadjusted_close', 'USD', 1, 's', 'https://example.invalid', %s)",
                      (text, stamp(arrived, 50)))
        owner.execute("INSERT INTO risk_free_rates (rate_date, series_id, currency, tenor, annual_rate, "
                      "day_count_convention, provider, fetched_at, quality_status) VALUES "
                      "(%s, 'UST-PAR-3M', 'USD', '3-month', 0.04, 'x', 'p', %s, 'pass')",
                      (text, stamp(datetime.combine(day, time(23, 4), tzinfo=timezone.utc))))
        owner.execute("INSERT INTO trades (account_id, source_key, trade_date, row_sha256, last_seen_run_id) "
                      "VALUES ('U9100001', %s, %s, md5(%s) || md5(%s), %s)", (text, text, text, "trade", run))
    owner.execute("RESET search_path")
    owner.commit()


@unittest.skipUnless(ADMIN_DSN, "AGENT_TEST_PG_ADMIN_DSN is not set")
class LayerQualityDatabaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import psycopg

        cls.database = "quality_test_" + uuid.uuid4().hex[:8]
        with psycopg.connect(ADMIN_DSN, autocommit=True) as admin:
            for role in (APP, WEB, APPROVAL):
                admin.execute(f"DROP ROLE IF EXISTS {role}")
        cls.applied = layers._build(cls.database, skip=(MIGRATION,))
        with layers._connect(cls.database) as owner:
            _seed(owner)
            for text in layers._script("install_agent_schema").agent_schema_sql(owner):
                owner.execute(text)
            owner.commit()
        environment = {
            "DATABASE_ADMIN_URL": layers._url(cls.database, OWNER), "PORTFOLIO_TENANT_ID": TENANT,
            "DATABASE_APP_ROLE": APP, "DATABASE_APP_PASSWORD": layers.PASSWORD,
            "DATABASE_WEB_ROLE": WEB, "DATABASE_WEB_PASSWORD": layers.PASSWORD,
            "DATABASE_APPROVAL_ROLE": APPROVAL, "DATABASE_APPROVAL_PASSWORD": layers.PASSWORD,
        }
        output = io.StringIO()
        with patch.dict(os.environ, environment), contextlib.redirect_stdout(output):
            layers._script("bootstrap_postgres").main(["--only-migration", MIGRATION])
        cls.bootstrap_output = output.getvalue()

    @classmethod
    def tearDownClass(cls):
        layers._drop(cls.database, (APP, WEB, APPROVAL))

    def _url(self, role: str) -> str:
        return layers._url(self.database, role)

    def _daily(self, role: str) -> dict[str, dict]:
        results = layer_quality.run_daily(self._url(role), TENANT, layer_quality.plan(ENVIRON), now=NOW)
        return {result["check_id"]: result for result in results}

    def test_bootstrap_applies_072_with_history_rights_for_the_approval_login(self):
        import psycopg

        self.assertNotIn(MIGRATION, self.applied)
        self.assertIn(f"Migrations applied: {MIGRATION}.", self.bootstrap_output)
        with layers._connect(self.database) as owner:
            rls = owner.execute("SELECT relrowsecurity, relforcerowsecurity FROM pg_class "
                                "WHERE oid = 'public.quality_check_results'::regclass").fetchone()
            self.assertEqual(rls, (True, True))
            rights = owner.execute(
                "SELECT p FROM unnest(ARRAY['SELECT', 'INSERT', 'UPDATE', 'DELETE', 'TRUNCATE']) p "
                "WHERE has_table_privilege(%s, 'public.quality_check_results', p) ORDER BY 1", (APPROVAL,)).fetchall()
            self.assertEqual([r[0] for r in rights], ["INSERT", "SELECT"])
            # Only the counted column, never the row: a figure stays unreadable.
            for table, column in (("silver.trades", "trade_date"), ("gold.crisis_replay_components", None)):
                if column:
                    self.assertTrue(owner.execute("SELECT has_column_privilege(%s, %s, %s, 'SELECT')",
                                                  (APPROVAL, table, column)).fetchone()[0])
                self.assertFalse(owner.execute("SELECT has_table_privilege(%s, %s, 'SELECT')",
                                               (APPROVAL, table)).fetchone()[0])

    def test_history_is_append_only_and_kept_per_tenant(self):
        import psycopg

        one = {"check_id": "rule.silver.trades.probe", "kind": "rule", "layer": "silver", "target": "silver.trades",
               "state": "pass", "severity": None, "observed": 0, "detail": {}}
        written = datetime(2026, 9, 1, 12, tzinfo=timezone.utc)  # a cycle no other test reads
        for tenant in (TENANT, OTHER_TENANT):
            session = layer_quality.open_history(self._url(APPROVAL), tenant)
            try:
                layer_quality.record(session, [one], now=written, mode_name="shadow",
                                     source=layer_quality.PUBLISH_GATE)
                session.commit()
                today = layer_quality.read_today(session, written.date())
                self.assertEqual(today["gate_rules"], frozenset({"rule.silver.trades.probe"}))
                self.assertEqual(session.execute("SELECT count(*) AS n FROM quality_check_results "
                                                 "WHERE cycle_date = %s", (written.date(),)).fetchone()["n"], 1)
                with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                    session.execute("UPDATE quality_check_results SET state = 'fail'")
                session.rollback()
            finally:
                session.close()
        with layers._connect(self.database) as owner:
            for statement in ("UPDATE quality_check_results SET state = 'fail'", "DELETE FROM quality_check_results",
                              "TRUNCATE quality_check_results"):
                with self.subTest(statement.split()[0]), self.assertRaises(psycopg.errors.RaiseException):
                    owner.execute(statement)
                owner.rollback()
            with self.assertRaises(psycopg.errors.CheckViolation):
                owner.execute("INSERT INTO quality_check_results (checked_at, cycle_date, source, mode, kind, "
                              "check_id, layer, state) VALUES (now(), current_date, 'monitor', 'shadow', "
                              "'volume', 'x', 'silver', 'green')")

    def test_every_check_runs_for_a_reader_of_every_table(self):
        results = self._daily(OWNER)
        states = {}
        for check_id, result in results.items():
            states.setdefault(result["state"], set()).add(check_id)
        self.assertNotIn("unavailable", states)
        self.assertEqual(len([c for c in results if c.startswith("schema.")]), 35)
        self.assertEqual({c for c in states.get("fail", ()) if not c.startswith("value.")}, set())
        self.assertEqual(len([c for c in states["skipped"] if c.startswith("rule.")]), 8)  # need an RLS bypass
        nav = results["volume.silver.nav_snapshots"]
        self.assertEqual((nav["state"], nav["observed"], nav["detail"]["date"]), ("pass", 3, "2026-10-15"))
        self.assertEqual(results["volume.silver.position_snapshots"]["observed"], 4)
        self.assertEqual(results["volume.silver.trades"]["state"], "recorded")  # events: recorded, not judged
        self.assertEqual(results["volume.silver.accounts"]["observed"], 3)
        move = results["value.nav_move"]
        self.assertEqual((move["state"], move["severity"], move["observed"]), ("fail", "warning", 1))
        self.assertEqual([f["subject"] for f in move["detail"]["failed"]], ["***003"])
        emptied = results["value.positions_to_zero"]
        self.assertEqual((emptied["state"], emptied["severity"]), ("fail", "error"))
        self.assertEqual([f["subject"] for f in emptied["detail"]["failed"]], ["***002"])

    def test_schema_drift_and_broken_rows_are_found(self):
        with layers._connect(self.database) as owner:
            owner.execute("ALTER TABLE silver.cash_snapshots ADD COLUMN quality_probe text")
            owner.execute("UPDATE silver.nav_snapshots SET total_text = 'n/a' "
                          "WHERE account_id = 'U9100001' AND snapshot_date = '2026-09-01'")
            owner.commit()
        try:
            results = self._daily(OWNER)
        finally:
            with layers._connect(self.database) as owner:
                owner.execute("ALTER TABLE silver.cash_snapshots DROP COLUMN quality_probe")
                owner.execute("UPDATE silver.nav_snapshots SET total_text = '1000.00' "
                              "WHERE account_id = 'U9100001' AND snapshot_date = '2026-09-01'")
                owner.commit()
        drift = results["schema.silver.cash_snapshots"]
        self.assertEqual(drift["state"], "fail")
        self.assertIn("quality_probe", str(drift["detail"]["differences"]))
        broken = results["rule.silver.nav_snapshots.total_text_is_decimal"]
        self.assertEqual((broken["state"], broken["severity"], broken["observed"]), ("fail", "error", 1))
        self.assertEqual(results["schema.silver.nav_snapshots"]["state"], "pass")

    def test_the_administrator_records_history_and_the_weekly_report_reads_it(self):
        url = self._url(APPROVAL)
        # 08:40, before any target time: every layer ready, recorded once; no daily pass yet.
        early = layer_quality.run_in_administrator(url, TENANT, now=NOW.replace(hour=8, minute=40),
                                                   mode_name="shadow", environ=ENVIRON)
        self.assertNotIn("error", early)
        self.assertEqual({i["check_id"]: i["state"] for i in early["readiness"]},
                         {"bronze.ready": "ready", "silver.ready": "ready", "gold.ready": "ready"})
        self.assertEqual((early["daily"], early["history"]), (None, {"recorded": 3}))

        outcome = layer_quality.run_in_administrator(url, TENANT, now=NOW, mode_name="shadow", environ=ENVIRON)
        self.assertNotIn("error", outcome)
        daily = {r["check_id"]: r for r in outcome["daily"]}
        # The approval login counts every table and reads the value checks; contract rules on
        # tables it cannot read are 'unavailable' (never findings).
        volumes = {k: r["state"] for k, r in daily.items() if r["kind"] == "volume"}
        self.assertEqual(len(volumes), len(layer_quality.VOLUMES))
        self.assertNotIn("unavailable", volumes.values())
        self.assertEqual({daily["value.nav_move"]["state"], daily["value.positions_to_zero"]["state"]}, {"fail"})
        self.assertEqual({r["state"] for k, r in daily.items() if k.startswith("schema.")}, {"pass"})
        unreadable = {k for k, r in daily.items() if r["state"] == "unavailable"}
        self.assertTrue(unreadable and all(k.startswith("rule.") for k in unreadable))
        self.assertEqual({k for k, r in daily.items() if r["state"] == "fail"},
                         {"value.nav_move", "value.positions_to_zero"})
        freshness_rows = len([r for r in layer_freshness.RULES if r.query])
        self.assertEqual(outcome["history"]["recorded"], len(daily) + freshness_rows)

        # Five minutes later: readiness already recorded, the daily pass already ran.
        again = layer_quality.run_in_administrator(url, TENANT, now=NOW + timedelta(minutes=5),
                                                   mode_name="shadow", environ=ENVIRON)
        self.assertEqual((again["daily"], again["history"]), (None, {"recorded": 0}))

        session = layer_quality.open_history(url, TENANT)
        try:
            weekly = layer_quality.weekly_report(session, week_ending=date(2026, 10, 18))
        finally:
            session.close()
        targets = {t["target_id"]: t for t in weekly["targets"]}
        for target_id in ("bronze.ready", "silver.ready", "gold.ready", "gold.checks"):
            self.assertEqual((targets[target_id]["status"], targets[target_id]["week"]["met"]), ("met", 1))
        self.assertEqual(targets["silver.checks"]["status"], "missed")  # positions dropped to zero
        self.assertEqual(targets["silver.ready"]["week"]["unmeasured"], 4)  # 12-14 and 16 Oct: no history
        failed = {f["check_id"]: f for f in weekly["failed_checks"]}
        self.assertEqual(set(failed), {"value.nav_move", "value.positions_to_zero"})
        self.assertEqual(failed["value.positions_to_zero"]["table"], "silver.subject_position_series")
        self.assertEqual(weekly["unavailable_checks"]["count"], len(unreadable))
        text = layer_quality.render(weekly)
        self.assertIn("silver ready by 09:00 UTC on 99% of business days: MET", text)
        self.assertNotIn("***", text)
        self.assertNotIn("U91", text)

    def test_the_administrator_hook_in_shadow_records_quietly(self):
        import psycopg
        from psycopg.rows import dict_row

        supervisor = layers._script("run_autonomous_administrator")
        # Saturday 24 Oct 2026: Friday's data is due by the target times and never arrived.
        saturday = datetime(2026, 10, 24, 10, 31, tzinfo=timezone.utc)
        late = {"layer_quality:bronze.ready", "layer_quality:silver.ready", "layer_quality:gold.ready"}
        failed = {"layer_quality:value.nav_move", "layer_quality:value.positions_to_zero"}
        with psycopg.connect(layers._dsn(self.database, OWNER), row_factory=dict_row) as admin:
            admin.execute("DELETE FROM system_incidents")
            admin.commit()

            def run(now, mode):
                with patch.dict(os.environ, {"LAYER_QUALITY_MODE": mode, **ENVIRON}), \
                        patch("builtins.print") as printed:
                    review = supervisor._layer_quality_review(admin, self._url(APPROVAL), TENANT, now=now)
                lines = [str(c.args[0]) for c in printed.call_args_list]
                if review is None:
                    return None, lines, {}
                _, announce = supervisor._synchronize(admin, list(review.findings), now.isoformat(), review)
                admin.commit()
                rows = admin.execute("SELECT incident_key, severity, status FROM system_incidents").fetchall()
                return announce, lines, {r["incident_key"]: (r["severity"], r["status"]) for r in rows}

            self.assertEqual(run(saturday, "off"), (None, [], {}))
            announce, lines, incidents = run(saturday, "shadow")
            self.assertEqual(announce, set())  # shadow: recorded, never announced
            self.assertEqual(incidents, {key: ("info", "open") for key in late | failed})
            self.assertEqual(len(lines), 1)
            self.assertTrue(lines[0].startswith(layer_quality.LOG_PREFIX))
            self.assertNotIn("***", lines[0])
            self.assertNotIn("U91", lines[0])
            # Five minutes later, still late and no daily pass: everything stays open, quietly.
            announce, _, incidents = run(saturday + timedelta(minutes=5), "shadow")
            self.assertEqual((announce, incidents), (set(), {key: ("info", "open") for key in late | failed}))
            # Warn mode: a late layer becomes a warning and is announced once; the value
            # incidents stay info until the next daily pass.
            announce, _, incidents = run(saturday + timedelta(minutes=10), "warn")
            self.assertEqual(announce, late)
            self.assertEqual({k: v[0] for k, v in incidents.items()},
                             {**{k: "warning" for k in late}, **{k: "info" for k in failed}})
            admin.execute("DELETE FROM system_incidents")
            admin.commit()

    def test_the_dry_run_records_nothing(self):
        with layers._connect(self.database) as owner:
            before = owner.execute("SELECT count(*) FROM quality_check_results").fetchone()[0]
        environ = {**ENVIRON, "DATABASE_URL": self._url(APPROVAL), "PORTFOLIO_TENANT_ID": TENANT}
        result = layer_quality.dry_run(now=NOW, environ=environ)
        self.assertEqual(result["summary"]["history"], "dry run: nothing recorded")
        self.assertEqual(len(result["readiness"]), 3)
        with layers._connect(self.database) as owner:
            self.assertEqual(owner.execute("SELECT count(*) FROM quality_check_results").fetchone()[0], before)


if __name__ == "__main__":
    unittest.main()
