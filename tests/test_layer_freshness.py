"""Per-layer freshness rules on synthetic observations, and how the autonomous
administrator reports them. No database or storage is touched here."""
from __future__ import annotations

import importlib.util
import json
import unittest
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from ibkr_etl import layer_freshness, raw_capture
from ibkr_etl.autonomous_admin import (
    LAYER_FRESHNESS_CHECK,
    AutonomyControl,
    layer_freshness_findings,
    plan_safe_repairs,
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
        "yahoo-crisis-history": cycle + timedelta(minutes=50),
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
        self.assertEqual((denied["state"], denied["late"]), ("unavailable", False))

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
        self.assertEqual(feeds, set(raw_capture.FEEDS))
        self.assertEqual({rule.feed for rule in RULES if rule.feed and rule.required},
                         {"yahoo-benchmark-history", "yahoo-crisis-history", "treasury-par-yield"})
        for target in ("public.raw_reports", "public.flex_rows", "public.nav_snapshots",
                       "public.position_snapshots", "public.market_data_prices", "public.benchmark_prices",
                       "public.risk_free_rates", "public.risk_analytics_snapshots",
                       "public.performance_comparison_daily", "public.crisis_scenario_results",
                       "public.crisis_replay_daily"):
            self.assertIn(target, {rule.target for rule in RULES})


class CollectTests(unittest.TestCase):
    def test_healthy_day_has_no_late_layers(self):
        world = _fresh_world(AFTER_CYCLE)
        calls = []
        results = collect(_Connection(world), now=AFTER_CYCLE, newest_blob_time=_blob_lister(world, calls=calls))
        summary = layer_freshness.summary(results)
        self.assertEqual(summary["late"], [])
        self.assertEqual(summary["unavailable"], [])
        self.assertIn("yahoo-benchmark-history/2026/10/16/", calls)
        self.assertEqual(layer_freshness_findings(results), [])

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
        self.assertEqual(results["bronze.market_data.treasury-par-yield"]["error"], "TimeoutError")
        self.assertEqual(results["silver.nav_snapshots"]["state"], "fresh")  # later queries still run
        self.assertFalse(any(r["late"] for r in results.values()))

    def test_without_storage_settings_blob_checks_are_unavailable(self):
        results = collect(_Connection(_fresh_world(AFTER_CYCLE)), now=AFTER_CYCLE)
        feed_states = {r["state"] for r in results if r.get("feed")}
        self.assertEqual(feed_states, {"unavailable"})

    def test_time_budget_stops_slow_checks(self):
        ticks = iter(range(0, 10000, 10))
        results = collect(_Connection(_fresh_world(AFTER_CYCLE)), now=AFTER_CYCLE,
                          time_budget_seconds=25, clock=lambda: next(ticks))
        self.assertEqual([r["error"] for r in results[3:5]], ["time budget used"] * 2)


class SupervisorReportingTests(unittest.TestCase):
    def test_late_required_layers_become_warning_incidents_without_actions(self):
        world = _fresh_world(AFTER_CYCLE)
        world["silver.nav_snapshots"] = "2026-10-09"
        results = collect(_Connection(world), now=AFTER_CYCLE, newest_blob_time=_blob_lister(world))
        findings = layer_freshness_findings(results)
        self.assertEqual([f.incident_key for f in findings], ["layer_freshness:silver.nav_snapshots"])
        finding = findings[0]
        self.assertEqual((finding.check_id, finding.severity, finding.autonomous_action),
                         (LAYER_FRESHNESS_CHECK, "warning", None))
        self.assertIn("Silver layer is late: public.nav_snapshots", finding.description)
        control = AutonomyControl(enabled=True, kill_switch=False, production_active=True,
                                  monthly_review_due_at=AFTER_CYCLE + timedelta(days=10))
        self.assertEqual(plan_safe_repairs(findings, control, recent_action_at={}, now=AFTER_CYCLE), [])

    def test_a_check_that_cannot_read_anything_raises_one_incident(self):
        results = [evaluate(rule, None, now=AFTER_CYCLE, error="OperationalError") for rule in RULES]
        findings = layer_freshness_findings(results)
        self.assertEqual([f.incident_key for f in findings], ["layer_freshness:unavailable"])

    def test_supervisor_hook_is_off_by_default(self):
        supervisor = _supervisor()

        with patch.dict("os.environ", {}, clear=True), \
                patch.object(layer_freshness, "run_read_only", side_effect=AssertionError("must not run")):
            self.assertEqual(supervisor._layer_freshness_findings("postgresql://unused", "t"), [])

    def test_supervisor_hook_never_raises(self):
        supervisor = _supervisor()

        with patch.dict("os.environ", {"LAYER_FRESHNESS_ENABLED": "true"}, clear=True), \
                patch.object(layer_freshness, "run_read_only", side_effect=RuntimeError("boom")), \
                patch("builtins.print") as printed:
            self.assertEqual(supervisor._layer_freshness_findings("postgresql://unused", "t"), [])
        self.assertIn('"status":"failed"', printed.call_args.args[0])

    def test_run_read_only_survives_an_unreachable_database(self):
        results = layer_freshness.run_read_only("postgresql://nobody@127.0.0.1:1/none", "t",
                                                now=AFTER_CYCLE, environ={})
        self.assertEqual(len(results), len(RULES))
        self.assertTrue(all(r["state"] == "unavailable" for r in results))
        self.assertTrue(any("OperationalError" in r["error"] for r in results if not r.get("feed")))


class BicepDefaultsTests(unittest.TestCase):
    """Applying the Bicep alone changes nothing: every new setting is empty or off."""

    def test_job_files_keep_raw_capture_off_by_default(self):
        for name in ("staging-benchmark-job.bicep", "staging-risk-free-job.bicep",
                     "staging-sector-classification-job.bicep", "staging-crisis-scenarios-job.bicep"):
            with self.subTest(name=name):
                text = (ROOT / "infra" / name).read_text(encoding="utf-8")
                self.assertIn("param rawMarketDataContainer string = ''", text)
                self.assertIn("var rawMarketDataEnv = empty(rawMarketDataContainer) ? [] : [", text)
                self.assertIn("], rawMarketDataEnv)", text)
                self.assertIn("{ name: 'MARKET_DATA_RAW_CONTAINER', value: rawMarketDataContainer }", text)
                self.assertIn("{ name: 'AZURE_CLIENT_ID', value: jobIdentity.properties.clientId }", text)
                self.assertEqual(text.count("MARKET_DATA_RAW_CONTAINER"), 1)

    def test_supervisor_file_keeps_layer_freshness_off_by_default(self):
        text = (ROOT / "infra" / "staging-autonomous-admin.bicep").read_text(encoding="utf-8")
        self.assertIn("param layerFreshnessEnabled bool = false", text)
        self.assertIn("var layerFreshnessEnv = !layerFreshnessEnabled ? [] : concat([", text)
        self.assertIn("], layerFreshnessEnv)", text)


if __name__ == "__main__":
    unittest.main()
