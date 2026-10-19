"""Per-layer freshness rules on synthetic observations, and how the autonomous
administrator reports them. No database or storage is touched here."""
from __future__ import annotations

import importlib.util
import json
import threading
import time as clock_time
import unittest
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, patch

from ibkr_etl import layer_freshness, raw_capture
from ibkr_etl.autonomous_admin import (
    LAYER_FRESHNESS_CHECK,
    AutonomyControl,
    plan_safe_repairs,
    review_layer_freshness,
)
from ibkr_etl.layer_freshness import AS_OF, LOADED, RULES, Rule, collect, evaluate, last_due

ROOT = Path(__file__).resolve().parents[1]


def _supervisor():
    spec = importlib.util.spec_from_file_location(
        "autonomous_supervisor", ROOT / "scripts" / "run_autonomous_administrator.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# Friday 16 Oct 2026, 10:30 UTC: after the daily cycle.
AFTER_CYCLE = datetime(2026, 10, 16, 10, 30, tzinfo=timezone.utc)
RULE = {rule.check_id: rule for rule in RULES}
BLOBS_ON = {"LAYER_FRESHNESS_BLOB_CHECKS": "true"}


def _fresh_world(now: datetime) -> dict:
    """What a healthy platform shows after the daily cycle on `now`'s date."""
    today = now.date()
    cycle = datetime.combine(today, time(8, 31), tzinfo=timezone.utc)
    risk_free = datetime.combine(today - timedelta(days=1), time(23, 4), tzinfo=timezone.utc)
    return {
        "bronze.raw_reports": (cycle - timedelta(minutes=29)).isoformat(),
        "bronze.flex_rows": (cycle - timedelta(minutes=28)).isoformat(),
        "silver.nav_snapshots": (today - timedelta(days=1)).isoformat(),
        "silver.position_snapshots": (today - timedelta(days=1)).isoformat(),
        "silver.benchmark_prices": cycle.isoformat(),
        "silver.market_data_prices": (today - timedelta(days=1)).isoformat(),
        "silver.risk_free_rates": risk_free.isoformat(),
        "gold.risk_analytics_snapshots": (today - timedelta(days=1)).isoformat(),
        "gold.performance_comparison_daily": (today - timedelta(days=1)).isoformat(),
        "gold.crisis_scenario_results": (today - timedelta(days=1)).isoformat(),
        "gold.crisis_replay_daily": (today - timedelta(days=1)).isoformat(),
        "yahoo-benchmark-history": cycle,
        "treasury-par-yield": risk_free,
    }


class _Connection:
    """Answers each rule's query from a dict keyed by check_id; can fail per query."""

    def __init__(self, values: dict, failures: dict | None = None):
        self.by_query = {rule.query: rule.check_id for rule in RULES if rule.query}
        self.values = values
        self.failures = failures or {}
        self.queries = []

    def execute(self, query):
        self.queries.append(query)
        check_id = self.by_query[query]
        if check_id in self.failures:
            raise self.failures[check_id]
        value = self.values.get(check_id)
        return _Cursor(None if value is None else {"observed": value})


class _Cursor:
    def __init__(self, row):
        self.row = row

    def fetchone(self):
        return self.row


def _blob_lister(values: dict, *, calls: list | None = None):
    def newest(prefix):
        if calls is not None:
            calls.append(prefix)
        feed, year, month, day = prefix.rstrip("/").split("/")
        observed = values.get(feed)
        if observed is not None and observed.date() == date(int(year), int(month), int(day)):
            return observed
        return None
    return newest


class DueTimeTests(unittest.TestCase):
    def test_before_the_due_time_yesterdays_cycle_is_judged(self):
        self.assertEqual(last_due(datetime(2026, 10, 16, 9, 0, tzinfo=timezone.utc), time(9, 30)),
                         datetime(2026, 10, 15, 9, 30, tzinfo=timezone.utc))
        self.assertEqual(last_due(datetime(2026, 10, 16, 9, 30, tzinfo=timezone.utc), time(9, 30)),
                         datetime(2026, 10, 16, 9, 30, tzinfo=timezone.utc))

    def test_naive_times_are_refused(self):
        with self.assertRaises(ValueError):
            last_due(datetime(2026, 10, 16, 9, 0), time(9, 30))


class RuleTests(unittest.TestCase):
    loaded = Rule("t.loaded", "silver", "public.x", LOADED, time(9, 30), timedelta(hours=6), query="q")
    as_of = Rule("t.as_of", "silver", "public.x", AS_OF, time(9, 30), timedelta(days=4), query="q")

    def test_loaded_at_must_fall_within_max_age_before_the_due_time(self):
        self.assertEqual(evaluate(self.loaded, "2026-10-16T08:31:00+00:00", now=AFTER_CYCLE)["state"], "fresh")
        late = evaluate(self.loaded, "2026-10-15T08:31:00+00:00", now=AFTER_CYCLE)
        self.assertEqual((late["state"], late["late"]), ("late", True))
        self.assertEqual(late["earliest_expected"], "2026-10-16T03:30:00+00:00")
        self.assertEqual(late["age_hours"], 25.0)
        # Before today's cycle is due, yesterday's run is still current.
        morning = datetime(2026, 10, 16, 7, 0, tzinfo=timezone.utc)
        self.assertEqual(evaluate(self.loaded, "2026-10-15T08:31:00+00:00", now=morning)["state"], "fresh")
        # Text without a zone is UTC, as utc_now_iso and Postgres text columns store it.
        self.assertEqual(evaluate(self.loaded, "2026-10-16T08:31:00", now=AFTER_CYCLE)["state"], "fresh")

    def test_as_of_date_allows_weekends_and_holidays(self):
        monday = datetime(2026, 10, 19, 10, 0, tzinfo=timezone.utc)
        self.assertEqual(evaluate(self.as_of, "2026-10-16", now=monday)["state"], "fresh")  # Friday
        self.assertEqual(evaluate(self.as_of, "2026-10-15", now=monday)["state"], "fresh")  # 4 days
        stale = evaluate(self.as_of, "2026-10-14", now=monday)
        self.assertEqual((stale["state"], stale["age_days"], stale["late"]), ("late", 5, True))

    def test_missing_future_and_unreadable(self):
        self.assertEqual(evaluate(self.loaded, None, now=AFTER_CYCLE)["state"], "missing")
        self.assertTrue(evaluate(self.loaded, None, now=AFTER_CYCLE)["late"])
        self.assertEqual(evaluate(self.loaded, "2026-10-17T08:00:00+00:00", now=AFTER_CYCLE)["state"], "future")
        # Asian market dates can be a day ahead of UTC.
        self.assertEqual(evaluate(self.as_of, "2026-10-17", now=AFTER_CYCLE)["state"], "fresh")
        self.assertEqual(evaluate(self.as_of, "2026-10-18", now=AFTER_CYCLE)["state"], "future")
        unreadable = evaluate(self.as_of, "not a date", now=AFTER_CYCLE)
        self.assertEqual((unreadable["state"], unreadable["late"]), ("unavailable", False))
        denied = evaluate(self.as_of, None, now=AFTER_CYCLE, error="InsufficientPrivilege (42501)")
        self.assertEqual((denied["state"], denied["late"], denied["error_scope"]), ("unavailable", False, "check"))

    def test_optional_checks_are_reported_but_never_late(self):
        rule = RULE["bronze.market_data.yahoo-sector-search"]
        result = evaluate(rule, None, now=AFTER_CYCLE)
        self.assertEqual((result["state"], result["late"]), ("missing", False))
        json.dumps(result)  # plain JSON for incident evidence


class RuleCatalogTests(unittest.TestCase):
    """The rules follow infra/medallion/catalog.json and the raw-capture feeds."""

    def test_rules_match_the_catalog_layers(self):
        catalog = json.loads((ROOT / "infra" / "medallion" / "catalog.json").read_text(encoding="utf-8"))
        for rule in RULES:
            with self.subTest(rule=rule.check_id):
                entry = catalog["postgres"].get(rule.target) or catalog["blob_containers"].get(rule.target)
                self.assertIsNotNone(entry, "target must be in the catalog")
                self.assertEqual(entry["layer"], rule.layer)
                self.assertTrue(rule.check_id.startswith(rule.layer + "."))
                self.assertIn(rule.measure, {LOADED, AS_OF})
                self.assertEqual(rule.feed is None, rule.query is not None)
                if rule.query:
                    self.assertIn(" FROM " + rule.target.split(".", 1)[1], rule.query)
                    self.assertTrue(rule.query.lstrip().upper().startswith("SELECT"))

    def test_every_layer_and_feed_is_covered(self):
        self.assertEqual(len({rule.check_id for rule in RULES}), len(RULES))
        self.assertEqual({rule.layer for rule in RULES}, {"bronze", "silver", "gold"})
        feeds = {rule.feed for rule in RULES if rule.feed}
        # Crisis windows are finished history (one file each, under history/), so no
        # new file is expected daily and the gold crisis checks watch that job instead.
        # So are the Kenneth French files (phase 3): silver.factor_returns watches that job.
        self.assertEqual(feeds, set(raw_capture.FEEDS) - {"yahoo-crisis-history", "kenneth-french"})
        self.assertEqual({rule.feed for rule in RULES if rule.feed and rule.required},
                         {"yahoo-benchmark-history", "treasury-par-yield"})
        for target in ("public.raw_reports", "public.flex_rows", "public.nav_snapshots",
                       "public.position_snapshots", "public.market_data_prices", "public.benchmark_prices",
                       "public.risk_free_rates", "public.risk_analytics_snapshots",
                       "public.performance_comparison_daily", "public.crisis_scenario_results",
                       "public.crisis_replay_daily"):
            self.assertIn(target, {rule.target for rule in RULES})

    def test_blob_checks_have_their_own_switch_off_by_default(self):
        self.assertFalse(layer_freshness.blob_checks_enabled({}))
        self.assertFalse(any(rule.feed for rule in layer_freshness.active_rules({})))
        self.assertEqual(len(layer_freshness.active_rules({})), len([r for r in RULES if r.query]))
        self.assertEqual(layer_freshness.active_rules(BLOBS_ON), RULES)


class CollectTests(unittest.TestCase):
    def test_healthy_day_has_no_late_layers(self):
        world = _fresh_world(AFTER_CYCLE)
        calls = []
        results = collect(_Connection(world), now=AFTER_CYCLE, newest_blob_time=_blob_lister(world, calls=calls))
        summary = layer_freshness.summary(results)
        self.assertEqual(summary["late"], [])
        self.assertEqual(summary["unavailable"], [])
        self.assertIn("yahoo-benchmark-history/2026/10/16/", calls)
        self.assertEqual(review_layer_freshness(results, now=AFTER_CYCLE).findings, ())

    def test_a_missed_benchmark_job_shows_in_bronze_and_silver(self):
        world = _fresh_world(AFTER_CYCLE)
        yesterday = _fresh_world(AFTER_CYCLE - timedelta(days=1))
        world["yahoo-benchmark-history"] = yesterday["yahoo-benchmark-history"]
        world["silver.benchmark_prices"] = yesterday["silver.benchmark_prices"]
        results = collect(_Connection(world), now=AFTER_CYCLE, newest_blob_time=_blob_lister(world))
        self.assertEqual(layer_freshness.summary(results)["late"],
                         ["bronze.market_data.yahoo-benchmark-history", "silver.benchmark_prices"])

    def test_query_and_storage_errors_become_unavailable_not_late(self):
        world = _fresh_world(AFTER_CYCLE)

        class InsufficientPrivilege(Exception):
            sqlstate = "42501"

        connection = _Connection(world, {"bronze.flex_rows": InsufficientPrivilege("denied")})

        def broken_storage(prefix):
            raise TimeoutError("storage timed out")

        results = {r["check_id"]: r for r in collect(connection, now=AFTER_CYCLE, newest_blob_time=broken_storage)}
        self.assertEqual(results["bronze.flex_rows"]["state"], "unavailable")
        self.assertEqual(results["bronze.flex_rows"]["error"], "InsufficientPrivilege (42501)")
        self.assertEqual(results["bronze.flex_rows"]["error_scope"], "check")
        self.assertEqual(results["bronze.market_data.treasury-par-yield"]["error"], "TimeoutError")
        self.assertEqual(results["silver.nav_snapshots"]["state"], "fresh")  # later queries still run
        self.assertFalse(any(r["late"] for r in results.values()))

    def test_without_storage_settings_blob_checks_are_unavailable(self):
        results = collect(_Connection(_fresh_world(AFTER_CYCLE)), now=AFTER_CYCLE)
        feeds = [r for r in results if r.get("feed")]
        self.assertEqual({(r["state"], r["error_scope"]) for r in feeds}, {("unavailable", "session")})

    def test_time_budget_stops_slow_checks(self):
        ticks = iter(range(0, 10000, 10))
        results = collect(_Connection(_fresh_world(AFTER_CYCLE)), now=AFTER_CYCLE,
                          time_budget_seconds=25, clock=lambda: next(ticks))
        self.assertEqual([r["error"] for r in results[3:5]], ["time budget used"] * 2)


class RunReadOnlyTests(unittest.TestCase):
    def test_an_unreachable_database_is_reported_not_raised(self):
        results = layer_freshness.run_read_only("postgresql://nobody@127.0.0.1:1/none", "t",
                                                now=AFTER_CYCLE, environ={})
        self.assertEqual(len(results), len(layer_freshness.active_rules({})))
        self.assertTrue(all(r["state"] == "unavailable" for r in results))
        self.assertTrue(all("OperationalError" in r["error"] for r in results))
        self.assertEqual({r["error_scope"] for r in results}, {"session"})

    def test_the_time_budget_is_a_hard_limit_including_the_connection(self):
        release = threading.Event()
        self.addCleanup(release.set)

        def hung_connect(*_args, **_kwargs):
            release.wait(60)
            raise RuntimeError("released")

        with patch.object(layer_freshness, "open_read_only", side_effect=hung_connect):
            started = clock_time.monotonic()
            results = layer_freshness.run_read_only("postgresql://unused", "t", now=AFTER_CYCLE,
                                                    environ={}, time_budget_seconds=0.5)
        self.assertLess(clock_time.monotonic() - started, 0.5 + 0.25)
        self.assertEqual(len(results), len(layer_freshness.active_rules({})))
        self.assertTrue(all(r["error"].startswith("TimeoutError") for r in results))
        self.assertEqual({r["error_scope"] for r in results}, {"session"})

    def test_checks_finished_before_the_limit_are_kept(self):
        world = _fresh_world(AFTER_CYCLE)
        release = threading.Event()
        self.addCleanup(release.set)

        class _Slow(_Connection):
            def execute(self, query):
                if self.by_query[query] == "silver.nav_snapshots":
                    release.wait(60)
                return super().execute(query)

            def rollback(self):
                pass

            def close(self):
                pass

        with patch.object(layer_freshness, "open_read_only", return_value=_Slow(world)):
            results = {r["check_id"]: r for r in layer_freshness.run_read_only(
                "postgresql://unused", "t", now=AFTER_CYCLE, environ={}, time_budget_seconds=0.5)}
        self.assertEqual(results["bronze.raw_reports"]["state"], "fresh")
        self.assertTrue(results["silver.nav_snapshots"]["error"].startswith("TimeoutError"))
        self.assertTrue(results["gold.crisis_replay_daily"]["error"].startswith("TimeoutError"))

    def test_blob_checks_stay_off_until_switched_on(self):
        lister = Mock(side_effect=AssertionError("storage must not be listed"))
        with patch.object(layer_freshness, "open_read_only", return_value=_Connection(_fresh_world(AFTER_CYCLE))), \
                patch.object(layer_freshness, "_blob_lister", return_value=(lister, None)) as blob_lister:
            results = layer_freshness.run_read_only("postgresql://unused", "t", now=AFTER_CYCLE,
                                                    environ={"MARKET_DATA_RAW_CONTAINER": "bronze-market-data",
                                                             "AZURE_STORAGE_ACCOUNT_URL": "https://x"})
        blob_lister.assert_not_called()
        self.assertFalse(any(r.get("feed") for r in results))

    def test_the_freshness_url_overrides_the_administrators_database_url(self):
        supervisor = _supervisor()
        seen = []

        def run(url, tenant, **_kwargs):
            seen.append(url)
            return []

        with patch.object(supervisor, "_open_layer_freshness_incidents", return_value={}), \
                patch.object(layer_freshness, "run_read_only", side_effect=run), patch("builtins.print"):
            with patch.dict("os.environ", {"LAYER_FRESHNESS_ENABLED": "true"}, clear=True):
                supervisor._layer_freshness_review(None, "postgresql://administrator", "t")
            with patch.dict("os.environ", {"LAYER_FRESHNESS_ENABLED": "true",
                                           "LAYER_FRESHNESS_DATABASE_URL": "postgresql://reader"}, clear=True):
                supervisor._layer_freshness_review(None, "postgresql://administrator", "t")
        self.assertEqual(seen, ["postgresql://administrator", "postgresql://reader"])


def _results(world: dict, *, failures: dict | None = None, now: datetime = AFTER_CYCLE) -> list:
    return collect(_Connection(world, failures), now=now, newest_blob_time=_blob_lister(world))


class _Denied(Exception):
    sqlstate = "42501"


class SupervisorReportingTests(unittest.TestCase):
    def test_late_required_layers_become_warning_incidents_without_actions(self):
        world = _fresh_world(AFTER_CYCLE)
        world["silver.nav_snapshots"] = "2026-10-09"
        findings = review_layer_freshness(_results(world), now=AFTER_CYCLE).findings
        self.assertEqual([f.incident_key for f in findings], ["layer_freshness:silver.nav_snapshots"])
        finding = findings[0]
        self.assertEqual((finding.check_id, finding.severity, finding.autonomous_action),
                         (LAYER_FRESHNESS_CHECK, "warning", None))
        self.assertIn("Silver layer is late: public.nav_snapshots", finding.description)
        control = AutonomyControl(enabled=True, kill_switch=False, production_active=True,
                                  monthly_review_due_at=AFTER_CYCLE + timedelta(days=10))
        self.assertEqual(plan_safe_repairs(findings, control, recent_action_at={}, now=AFTER_CYCLE), [])

    def test_an_unreadable_required_check_is_info_for_an_hour_then_a_warning(self):
        world = _fresh_world(AFTER_CYCLE)
        denied = {"bronze.raw_reports": _Denied("denied")}
        key = "layer_freshness:unavailable:bronze.raw_reports"
        first = review_layer_freshness(_results(world, failures=denied), now=AFTER_CYCLE)
        self.assertEqual([(f.incident_key, f.severity) for f in first.findings], [(key, "info")])
        self.assertEqual(first.quiet, {key})
        since = first.findings[0].evidence["unavailable_since"]
        self.assertEqual(since, "2026-10-16T10:30:00+00:00")
        self.assertIn("_Denied (42501)", first.findings[0].description)
        stored = {key: {"severity": "info", "evidence": dict(first.findings[0].evidence)}}
        later = AFTER_CYCLE + timedelta(minutes=55)
        still = review_layer_freshness(_results(world, failures=denied, now=later), now=later, open_incidents=stored)
        self.assertEqual(still.findings[0].severity, "info")
        self.assertEqual(still.findings[0].evidence["unavailable_since"], since)  # carried forward
        hour = AFTER_CYCLE + timedelta(hours=1)
        escalated = review_layer_freshness(_results(world, failures=denied, now=hour), now=hour,
                                           open_incidents=stored)
        self.assertEqual(escalated.findings[0].severity, "warning")
        self.assertEqual((escalated.escalated, escalated.quiet), ({key}, frozenset()))
        self.assertIsNone(escalated.findings[0].autonomous_action)

    def test_optional_checks_never_raise_unreadable_incidents(self):
        results = [evaluate(RULE["bronze.market_data.yahoo-sector-search"], None, now=AFTER_CYCLE,
                            error="TimeoutError")]
        self.assertEqual(review_layer_freshness(results, now=AFTER_CYCLE).findings, ())

    def test_a_failed_session_is_one_incident_not_one_per_check(self):
        results = [evaluate(rule, None, now=AFTER_CYCLE, error="OperationalError", scope="session")
                   for rule in layer_freshness.active_rules({})]
        findings = review_layer_freshness(results, now=AFTER_CYCLE).findings
        self.assertEqual([f.incident_key for f in findings], ["layer_freshness:unavailable:session"])
        self.assertIn("OperationalError", findings[0].description)
        self.assertEqual(len(findings[0].evidence["checks"]), len([r for r in results if r["required"]]))

    def test_only_a_fresh_result_closes_a_late_incident(self):
        late_key = "layer_freshness:gold.crisis_scenario_results"
        tracker = "layer_freshness:unavailable:gold.crisis_scenario_results"
        open_incidents = {late_key: {"severity": "warning", "evidence": {}},
                          tracker: {"severity": "info", "evidence": {"unavailable_since": AFTER_CYCLE.isoformat()}},
                          "layer_freshness:bronze.market_data.yahoo-crisis-history": {"severity": "warning",
                                                                                      "evidence": {}}}
        world = _fresh_world(AFTER_CYCLE)
        unreadable = review_layer_freshness(
            _results(world, failures={"gold.crisis_scenario_results": _Denied("denied")}),
            now=AFTER_CYCLE, open_incidents=open_incidents)
        self.assertIn(late_key, unreadable.hold_open)
        self.assertIn(tracker, {f.incident_key for f in unreadable.findings})
        # A check that is no longer run (here a removed rule) does not hold its incident.
        self.assertNotIn("layer_freshness:bronze.market_data.yahoo-crisis-history", unreadable.hold_open)
        fresh = review_layer_freshness(_results(world), now=AFTER_CYCLE, open_incidents=open_incidents)
        self.assertEqual(fresh.hold_open, frozenset())
        self.assertEqual(fresh.findings, ())
        failed = review_layer_freshness(None, now=AFTER_CYCLE, open_incidents=open_incidents)
        self.assertEqual(failed.hold_open, frozenset(open_incidents))
        self.assertEqual([f.incident_key for f in failed.findings], ["layer_freshness:unavailable:session"])

    def test_supervisor_hook_is_off_by_default(self):
        supervisor = _supervisor()
        connection = Mock(side_effect=AssertionError("no query"))
        connection.execute.side_effect = AssertionError("no query")
        with patch.dict("os.environ", {}, clear=True), \
                patch.object(layer_freshness, "run_read_only", side_effect=AssertionError("must not run")):
            self.assertIsNone(supervisor._layer_freshness_review(connection, "postgresql://unused", "t"))
        connection.execute.assert_not_called()

    def test_supervisor_hook_never_raises(self):
        supervisor = _supervisor()
        open_incidents = {"layer_freshness:silver.nav_snapshots": {"severity": "warning", "evidence": {}}}
        with patch.dict("os.environ", {"LAYER_FRESHNESS_ENABLED": "true"}, clear=True), \
                patch.object(supervisor, "_open_layer_freshness_incidents", return_value=open_incidents), \
                patch.object(layer_freshness, "run_read_only", side_effect=RuntimeError("boom")), \
                patch("builtins.print") as printed:
            review = supervisor._layer_freshness_review(None, "postgresql://unused", "t", now=AFTER_CYCLE)
        self.assertIn('"status":"failed"', printed.call_args.args[0])
        self.assertEqual(review.hold_open, frozenset(open_incidents))
        self.assertEqual([f.severity for f in review.findings], ["info"])

    def test_reading_open_incidents_cannot_abort_the_supervisor_transaction(self):
        supervisor = _supervisor()
        connection = Mock()
        connection.execute.side_effect = RuntimeError("relation does not exist")
        self.assertEqual(supervisor._open_layer_freshness_incidents(connection), {})
        connection.transaction.assert_called_once_with()


class BicepDefaultsTests(unittest.TestCase):
    """Applying the Bicep alone changes nothing: the defaults match what runs live since 18 Oct 2026."""

    def test_job_files_default_to_the_live_raw_capture(self):
        for name in ("staging-benchmark-job.bicep", "staging-risk-free-job.bicep",
                     "staging-sector-classification-job.bicep", "staging-crisis-scenarios-job.bicep"):
            with self.subTest(name=name):
                text = (ROOT / "infra" / name).read_text(encoding="utf-8")
                self.assertIn("param rawMarketDataContainer string = 'bronze-market-data'", text)
                self.assertIn("var rawMarketDataEnv = empty(rawMarketDataContainer) ? [] : [", text)
                self.assertIn("], rawMarketDataEnv)", text)
                self.assertIn("{ name: 'MARKET_DATA_RAW_CONTAINER', value: rawMarketDataContainer }", text)
                self.assertIn("{ name: 'AZURE_CLIENT_ID', value: jobIdentity.properties.clientId }", text)
                self.assertEqual(text.count("MARKET_DATA_RAW_CONTAINER"), 1)

    def test_crisis_job_keeps_its_raw_capture_time_budget(self):
        text = (ROOT / "infra" / "staging-crisis-scenarios-job.bicep").read_text(encoding="utf-8")
        self.assertIn("{ name: 'MARKET_DATA_RAW_BUDGET_SECONDS', value: '120' }", text)

    def test_supervisor_file_has_layer_freshness_on_and_blob_checks_off_by_default(self):
        text = (ROOT / "infra" / "staging-autonomous-admin.bicep").read_text(encoding="utf-8")
        self.assertIn("param layerFreshnessEnabled bool = true", text)
        self.assertIn("param layerFreshnessBlobChecks bool = false", text)
        self.assertIn("var layerFreshnessBlobEnv = !layerFreshnessBlobChecks ? [] : concat([", text)
        self.assertIn("var layerFreshnessEnv = !layerFreshnessEnabled ? [] : concat([", text)
        self.assertIn("], layerFreshnessBlobEnv)", text)
        self.assertIn("], layerFreshnessEnv)", text)

    def test_jobs_the_medallion_release_moves_never_apply_migrations(self):
        # Their image carries migrations that are held back in production; only bootstrap applies migrations.
        for name in ("staging-institutional-daily.bicep", "staging-burn-in-monitoring.bicep",
                     "staging-autonomous-admin.bicep", "staging-derived-refresh.bicep",
                     "staging-benchmark-job.bicep", "staging-risk-free-job.bicep",
                     "staging-sector-classification-job.bicep", "staging-crisis-scenarios-job.bicep"):
            with self.subTest(name=name):
                text = (ROOT / "infra" / name).read_text(encoding="utf-8")
                self.assertEqual(text.count("IBKR_RUN_MIGRATIONS"), 1)
                self.assertIn("{ name: 'IBKR_RUN_MIGRATIONS', value: 'false' }", text)


if __name__ == "__main__":
    unittest.main()
