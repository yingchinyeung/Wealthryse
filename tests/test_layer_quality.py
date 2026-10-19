"""Layer quality checks (medallion review item 7) on synthetic observations, and how the
autonomous administrator reports them. No database is touched here; test_layer_quality_db
runs the queries on PostgreSQL."""
from __future__ import annotations

import importlib.util
import json
import threading
import unittest
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, patch

from ibkr_etl import layer_freshness, layer_quality, medallion_contracts
from ibkr_etl.autonomous_admin import (
    LAYER_QUALITY_PREFIX,
    LayerFreshnessReview,
    combine_reviews,
    review_layer_quality,
)
from ibkr_etl.layer_quality import (
    MIN_BASELINE,
    MIN_VALUE_HISTORY,
    TARGETS,
    VOLUMES,
    Volume,
    judge_nav_moves,
    judge_positions,
    judge_volume,
    readiness,
    readiness_records,
)

ROOT = Path(__file__).resolve().parents[1]
CONTRACTS = ROOT / "infra" / "medallion" / "contracts"
# Tuesday 20 Oct 2026: Monday 19 Oct was a Hong Kong holiday (Chung Yeung) and a US session.
TUESDAY = date(2026, 10, 20)


def _supervisor():
    spec = importlib.util.spec_from_file_location(
        "autonomous_supervisor", ROOT / "scripts" / "run_autonomous_administrator.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _at(day: date, hour: int, minute: int = 0) -> datetime:
    return datetime.combine(day, time(hour, minute), tzinfo=timezone.utc)


def _freshness(day: date, *, data_day: date | None = None, loaded: datetime | None = None,
               unavailable: tuple[str, ...] = ()) -> list[dict]:
    """Freshness results as layer_freshness gives them, on `day`'s cycle."""
    data_day = data_day or day - timedelta(days=1)
    loaded = loaded or _at(day, 8, 25)
    results = []
    for rule in layer_freshness.RULES:
        if rule.feed:
            continue
        observed = loaded.isoformat() if rule.measure == layer_freshness.LOADED else data_day.isoformat()
        result = {"check_id": rule.check_id, "layer": rule.layer, "target": rule.target, "measure": rule.measure,
                  "required": rule.required, "state": "fresh", "observed": observed, "late": False}
        if rule.check_id in unavailable:
            result.update(state="unavailable", observed=None, error="InsufficientPrivilege (42501)")
        results.append(result)
    return results


def _sessions(start: date, end: date, *, markets=("XHKG", "XNYS")) -> list[date]:
    from ibkr_etl.market_calendar import CALENDARS

    days, day = [], start
    while day <= end:
        if any(CALENDARS[m].is_session(day) for m in markets):
            days.append(day)
        day += timedelta(days=1)
    return days


class ModeTests(unittest.TestCase):
    def test_off_unless_shadow_or_warn(self):
        for value, expected in (("", "off"), ("off", "off"), ("false", "off"), ("shadow", "shadow"),
                                ("SHADOW ", "shadow"), ("warn", "warn")):
            with self.subTest(value=value):
                self.assertEqual(layer_quality.mode({"LAYER_QUALITY_MODE": value}), (expected, None))
        self.assertEqual(layer_quality.mode({}), ("off", None))
        selected, note = layer_quality.mode({"LAYER_QUALITY_MODE": "enforce"})
        self.assertEqual(selected, "shadow")  # never anything stronger than asked for
        self.assertIn("unknown", note)

    def test_off_mode_does_nothing_in_the_administrator(self):
        supervisor = _supervisor()
        connection = Mock()
        connection.execute.side_effect = AssertionError("no query")
        with patch.dict("os.environ", {}, clear=True), \
                patch.object(layer_quality, "run_in_administrator", side_effect=AssertionError("must not run")), \
                patch.object(layer_quality, "open_history", side_effect=AssertionError("no connection")), \
                patch("builtins.print") as printed:
            self.assertIsNone(supervisor._layer_quality_review(connection, "postgresql://unused", "t"))
        connection.execute.assert_not_called()
        connection.transaction.assert_not_called()
        printed.assert_not_called()

    def test_off_mode_leaves_the_freshness_review_as_it_was(self):
        freshness = LayerFreshnessReview(hold_open=frozenset({"layer_freshness:x"}), quiet=frozenset({"q"}))
        self.assertIs(combine_reviews(freshness, None), freshness)
        self.assertIsNone(combine_reviews(None, None))

    def test_the_supervisor_hook_never_raises(self):
        supervisor = _supervisor()
        open_incidents = {LAYER_QUALITY_PREFIX + "volume.silver.trades": {"severity": "info", "evidence": {}}}
        with patch.dict("os.environ", {"LAYER_QUALITY_MODE": "shadow"}, clear=True), \
                patch.object(supervisor, "_open_layer_freshness_incidents", return_value=open_incidents), \
                patch.object(layer_quality, "run_in_administrator", side_effect=RuntimeError("boom")), \
                patch("builtins.print") as printed:
            review = supervisor._layer_quality_review(None, "postgresql://unused", "t", now=_at(TUESDAY, 11))
        self.assertIn('"status":"failed"', printed.call_args.args[0])
        self.assertEqual(review.hold_open, frozenset(open_incidents))
        self.assertEqual(review.findings, ())


class TargetTests(unittest.TestCase):
    def test_targets_are_defined_once_for_each_layer(self):
        rules = {rule.check_id: rule for rule in layer_freshness.RULES}
        ids = [target.target_id for target in TARGETS]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual({t.layer for t in TARGETS if t.kind == layer_quality.READY}, {"bronze", "silver", "gold"})
        for target in TARGETS:
            with self.subTest(target=target.target_id):
                self.assertTrue(0 < target.share <= 1)
                for check_id in target.checks:
                    self.assertEqual(rules[check_id].layer, target.layer)
                    self.assertIsNotNone(rules[check_id].query, "readiness reads database checks only")
        silver = next(t for t in TARGETS if t.target_id == "silver.ready")
        self.assertEqual((silver.ready_by, silver.share), (time(9, 0), 0.99))
        self.assertEqual(next(t for t in TARGETS if t.target_id == "gold.ready").ready_by, time(10, 0))

    def test_business_days_follow_the_repository_calendar(self):
        # Saturday's cycle brings Friday's statement; Sunday's and Monday's bring none.
        self.assertEqual(layer_quality.business_day_for(date(2026, 10, 17)), date(2026, 10, 16))
        self.assertIsNone(layer_quality.business_day_for(date(2026, 10, 18)))
        self.assertIsNone(layer_quality.business_day_for(date(2026, 10, 19)))
        # A Hong Kong holiday is still a US session, so IBKR's statement is expected.
        self.assertEqual(layer_quality.business_day_for(TUESDAY), date(2026, 10, 19))
        # Thanksgiving: no US session, nothing due the day after.
        self.assertIsNone(layer_quality.business_day_for(date(2026, 11, 27)))
        self.assertEqual(layer_quality.day_kind(date(2026, 10, 19)), "XNYS")
        self.assertEqual(layer_quality.day_kind(date(2026, 11, 26)), "XHKG")
        self.assertEqual(layer_quality.day_kind(date(2026, 10, 16)), "XHKG+XNYS")
        self.assertEqual(layer_quality.day_kind(date(2026, 10, 17)), "closed")
        self.assertEqual(layer_quality.day_kind(date(2031, 1, 6)), "unknown")


class CatalogCoverageTests(unittest.TestCase):
    def test_every_silver_and_gold_table_is_counted(self):
        objects = medallion_contracts.catalog_objects(ROOT / "infra" / "medallion" / "catalog.json")
        tables = {f"{entry['layer']}.{name}" for name, entry in objects.items() if entry["kind"] == "table"}
        self.assertEqual({v.target for v in VOLUMES}, tables)
        self.assertEqual(len(VOLUMES), len({v.check_id for v in VOLUMES}))
        # Tables created in their layer schema have no public view, so their count reads layer.table;
        # those a held migration creates are skipped until it has run.
        entries = json.loads((ROOT / "infra" / "medallion" / "catalog.json").read_text(encoding="utf-8"))["postgres"]
        in_layer = {key for key, value in entries.items() if key.split(".", 1)[0] == value.get("layer")}
        self.assertEqual({v.target for v in VOLUMES if v.qualified}, in_layer & tables)
        self.assertEqual({v.target for v in VOLUMES if v.held}, layer_quality.held_relations(CONTRACTS) & tables)

    def test_count_columns_are_contract_columns_and_dates_are_dates(self):
        contracts = {medallion_contracts.table_object(c)["physicalName"]: c
                     for c in medallion_contracts.load_all(CONTRACTS).values()}
        for rule in VOLUMES:
            with self.subTest(table=rule.table):
                props = {p["name"]: p for p in medallion_contracts.table_object(contracts[rule.target])["properties"]}
                self.assertIn(layer_quality.COUNT_COLUMNS[rule.relation], props)
                if rule.date_column:
                    self.assertEqual(props[rule.date_column]["logicalType"], "date")
                self.assertRegex(rule.table + (rule.date_column or ""), r"^[a-z_]+$")

    def test_contract_rules_are_all_planned_with_stable_ids(self):
        pass_plan = layer_quality.plan({layer_quality.CONTRACTS_DIR_ENV: str(CONTRACTS)})
        self.assertIsNone(pass_plan.contracts_error)
        # 46 contracts and 240 rules: the quant analytics table adds 9 rules, its two Desk views none.
        # Phase 3 part A adds 2 contracts and 9 rules (daily_volumes 3, factor_returns 6).
        self.assertEqual(len(pass_plan.contracts), 48)
        self.assertEqual(len(pass_plan.rules), 249)
        self.assertEqual(len({r.check_id for r in pass_plan.rules}), 249)
        self.assertEqual(sum(r.needs_rls_bypass for r in pass_plan.rules), 13)
        self.assertIn("silver.nav_snapshots_history", pass_plan.held)
        self.assertIn("public.silver_history_settings", pass_plan.held)
        self.assertIn("gold.quant_analytics_snapshots", pass_plan.held)
        self.assertEqual(layer_quality.rule_check_id("silver.nav_snapshots", "total_text_is_decimal"),
                         "rule.silver.nav_snapshots.total_text_is_decimal")
        gate = {"rule.silver.nav_snapshots.total_text_is_decimal"}
        planned = layer_quality.plan({layer_quality.CONTRACTS_DIR_ENV: str(CONTRACTS)}, gate_rules=gate).planned()
        self.assertNotIn("rule.silver.nav_snapshots.total_text_is_decimal", [p[0] for p in planned])

    def test_the_image_ships_the_contracts_at_medallion_dir(self):
        # Dockerfile.azure copies infra/medallion to /app/medallion and sets MEDALLION_DIR.
        dockerfile = (ROOT / "Dockerfile.azure").read_text(encoding="utf-8")
        self.assertIn("COPY infra/medallion ./medallion", dockerfile)
        self.assertIn("ENV MEDALLION_DIR=/app/medallion", dockerfile)
        ignored = (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
        self.assertLess(ignored.index("infra"), ignored.index("!infra/medallion"))
        found = layer_quality.contracts_dir({"MEDALLION_DIR": str(ROOT / "infra" / "medallion"),
                                             "IBKR_PROJECT_ROOT": "/nonexistent"})
        self.assertEqual(found, CONTRACTS)

    def test_without_contracts_schema_and_rules_are_unavailable(self):
        pass_plan = layer_quality.plan({layer_quality.CONTRACTS_DIR_ENV: "/nonexistent/contracts"})
        self.assertIn("not in this image", pass_plan.contracts_error)
        self.assertEqual(pass_plan.rules, ())


class VolumeTests(unittest.TestCase):
    rule = Volume("nav_snapshots", "silver", "snapshot_date")

    def _counts(self, end: date, rows: int = 10, *, start: date | None = None, **overrides) -> dict:
        start = start or end - timedelta(days=41)
        counts = {day.isoformat(): rows for day in _sessions(start, end)}
        counts.update({key.isoformat() if isinstance(key, date) else key: value for key, value in overrides.items()})
        return counts

    def test_a_typical_day_passes_and_the_first_weeks_are_learning(self):
        friday = date(2026, 10, 16)
        result = judge_volume(self.rule, self._counts(friday), today=date(2026, 10, 17))
        self.assertEqual((result["state"], result["observed"]), ("pass", 10))
        self.assertEqual(result["detail"]["day_kind"], "XHKG+XNYS")
        short = self._counts(friday, start=friday - timedelta(days=12))
        result = judge_volume(self.rule, short, today=date(2026, 10, 17))
        self.assertEqual(result["state"], "learning")
        self.assertLess(result["detail"]["baseline_days"], MIN_BASELINE)

    def test_missing_rows_fail_and_a_jump_is_a_warning(self):
        friday = date(2026, 10, 16)
        low = judge_volume(self.rule, self._counts(friday, **{friday.isoformat(): 3}), today=date(2026, 10, 17))
        self.assertEqual((low["state"], low["severity"], low["detail"]["direction"]), ("fail", "error", "low"))
        # Month-end packages and new accounts make jumps: reported, never an error.
        high = judge_volume(self.rule, self._counts(friday, **{friday.isoformat(): 40}), today=date(2026, 10, 17))
        self.assertEqual((high["state"], high["severity"]), ("fail", "warning"))

    def test_only_complete_days_are_judged(self):
        # On Tuesday the Asian closes are in before the US one: today's partial day is ignored.
        counts = self._counts(date(2026, 10, 19), **{TUESDAY.isoformat(): 2})
        result = judge_volume(self.rule, counts, today=TUESDAY)
        self.assertEqual(result["detail"]["date"], "2026-10-19")

    def test_holidays_compare_only_with_the_same_kind_of_day(self):
        # 19 Oct: Hong Kong closed, US open. Benchmarks have fewer rows; too few such days to judge.
        monday = date(2026, 10, 19)
        counts = self._counts(monday, **{monday.isoformat(): 3})
        result = judge_volume(Volume("benchmark_prices", "silver", "observation_date"), counts, today=TUESDAY)
        self.assertEqual((result["state"], result["detail"]["day_kind"]), ("learning", "XNYS"))
        # Weekends are compared with weekends only.
        saturday = date(2026, 10, 17)
        counts = self._counts(date(2026, 10, 16), **{saturday.isoformat(): 1})
        self.assertEqual(judge_volume(self.rule, counts, today=date(2026, 10, 18))["state"], "learning")

    def test_a_backfill_of_older_days_does_not_fail_today(self):
        # An account in onboarding loaded September only: older days hold four times the rows.
        friday = date(2026, 10, 16)
        counts = self._counts(friday)
        for day in _sessions(date(2026, 9, 4), date(2026, 9, 30)):
            counts[day.isoformat()] = 40
        result = judge_volume(self.rule, counts, today=date(2026, 10, 17))
        self.assertEqual(result["state"], "pass")
        self.assertEqual(result["detail"]["recent"], 10)

    def test_events_are_recorded_not_judged_and_tables_must_not_shrink(self):
        trades = Volume("trades", "silver", "trade_date", judged=False)
        friday = date(2026, 10, 16)
        result = judge_volume(trades, self._counts(friday, **{friday.isoformat(): 0}), today=date(2026, 10, 17))
        self.assertEqual(result["state"], "recorded")
        accounts = Volume("accounts", "silver")
        self.assertEqual(judge_volume(accounts, None, today=TUESDAY, total=5)["state"], "recorded")
        self.assertEqual(judge_volume(accounts, None, today=TUESDAY, total=5, previous_total=5)["state"], "pass")
        shrank = judge_volume(accounts, None, today=TUESDAY, total=4, previous_total=5)
        self.assertEqual((shrank["state"], shrank["severity"]), ("fail", "error"))
        self.assertEqual(judge_volume(self.rule, {}, today=TUESDAY)["state"], "recorded")


class ValueTests(unittest.TestCase):
    def test_nav_moves_need_history_and_report_beyond_the_band(self):
        rows = [
            {"subject_id": "U9100001", "currency": "USD", "dates": 20, "newest": "112", "previous": "100",
             "newest_date": "2026-10-16", "previous_date": "2026-10-15"},
            {"subject_id": "U9100002", "currency": "USD", "dates": 20, "newest": "105", "previous": "100",
             "newest_date": "2026-10-16", "previous_date": "2026-10-15"},
            # In onboarding: two dates only.
            {"subject_id": "U9100003", "currency": "USD", "dates": 2, "newest": "500", "previous": "100",
             "newest_date": "2026-10-16", "previous_date": "2026-10-15"},
        ]
        result = judge_nav_moves(rows)
        self.assertEqual((result["state"], result["severity"], result["observed"]), ("fail", "warning", 1))
        self.assertEqual(result["detail"]["failed"][0]["subject"], "***001")
        self.assertEqual((result["detail"]["subjects_checked"], result["detail"]["subjects_learning"]), (2, 1))
        self.assertNotIn("U9100001", json.dumps(result))
        self.assertEqual(judge_nav_moves(rows[2:])["state"], "learning")
        to_zero = [{"subject_id": "P1", "currency": "USD", "dates": 30, "newest": "0", "previous": "100",
                    "newest_date": "2026-10-16", "previous_date": "2026-10-15"}]
        self.assertEqual(judge_nav_moves(to_zero)["observed"], 1)
        # A closed account's last move is not reported again once its NAV is a week old.
        self.assertEqual(judge_nav_moves(rows, today=date(2026, 10, 23))["state"], "fail")
        stale = judge_nav_moves(rows, today=date(2026, 10, 24))
        self.assertEqual((stale["state"], stale["detail"]["subjects_stale"]), ("learning", 3))

    def test_positions_dropping_to_zero(self):
        established = {"subject_id": "U9100001", "nav_date": "2026-10-16", "positions": 0, "earlier_nav_dates": 25,
                       "held_nav_dates": 25, "last_held": "2026-10-15", "previous_nav_date": "2026-10-15"}
        result = judge_positions([established], today=date(2026, 10, 17))
        self.assertEqual((result["state"], result["severity"]), ("fail", "error"))
        self.assertEqual(result["detail"]["failed"][0], {"subject": "***001", "nav_date": "2026-10-16",
                                                         "last_held": "2026-10-15"})
        self.assertEqual(judge_positions([{**established, "positions": 12}])["state"], "pass")
        # Reported once: the next day it held none the day before either (sold out, or closed).
        next_day = {**established, "nav_date": "2026-10-19", "previous_nav_date": "2026-10-16",
                    "earlier_nav_dates": 26}
        self.assertEqual(judge_positions([next_day])["state"], "pass")
        # A subject with no NAV for over a week is not judged again.
        stale = judge_positions([established], today=date(2026, 10, 24))
        self.assertEqual((stale["state"], stale["detail"]["subjects_stale"]), ("learning", 1))
        # In onboarding, or positions only on some NAV dates (NAV on weekends): not judged.
        onboarding = {**established, "earlier_nav_dates": 3, "held_nav_dates": 3}
        weekends = {**established, "earlier_nav_dates": 35, "held_nav_dates": 25}
        result = judge_positions([onboarding, weekends])
        self.assertEqual((result["state"], result["detail"]["subjects_learning"]), ("learning", 2))
        self.assertGreaterEqual(MIN_VALUE_HISTORY, 10)


class ReadinessTests(unittest.TestCase):
    def _item(self, now: datetime, results) -> dict:
        return {i["check_id"]: i for i in readiness(results, now=now)}

    def test_waiting_ready_and_late(self):
        before = self._item(_at(TUESDAY, 7, 0), _freshness(TUESDAY, data_day=date(2026, 10, 16),
                                                           loaded=_at(TUESDAY - timedelta(days=1), 8, 25)))
        self.assertEqual({k: v["state"] for k, v in before.items()},
                         {"bronze.ready": "waiting", "silver.ready": "waiting", "gold.ready": "waiting"})
        self.assertEqual(before["silver.ready"]["business_day"], "2026-10-19")
        ready = self._item(_at(TUESDAY, 9, 5), _freshness(TUESDAY))
        self.assertEqual({v["state"] for v in ready.values()}, {"ready"})
        # The pull loaded, but benchmark-daily did not run: silver is late at 09:00, gold at 10:00.
        stale_benchmark = _freshness(TUESDAY)
        for result in stale_benchmark:
            if result["check_id"] == "silver.benchmark_prices":
                result["observed"] = _at(TUESDAY - timedelta(days=1), 8, 31).isoformat()
        late = self._item(_at(TUESDAY, 9, 1), stale_benchmark)
        self.assertEqual(late["silver.ready"]["state"], "late")
        self.assertFalse(late["silver.ready"]["checks"]["silver.benchmark_prices"]["ready"])
        self.assertEqual(late["bronze.ready"]["state"], "ready")

    def test_weekends_and_us_holidays_are_not_due(self):
        for cycle in (date(2026, 10, 18), date(2026, 10, 19), date(2026, 11, 27)):
            with self.subTest(cycle=cycle):
                items = readiness(_freshness(cycle, data_day=cycle - timedelta(days=4)), now=_at(cycle, 12))
                self.assertEqual({i["state"] for i in items}, {"not_due"})

    def test_an_unreadable_check_is_unavailable_never_late(self):
        items = self._item(_at(TUESDAY, 11), _freshness(TUESDAY, data_day=date(2026, 10, 16),
                                                        unavailable=("gold.crisis_replay_daily",)))
        self.assertEqual(items["gold.ready"]["state"], "unavailable")
        self.assertEqual(items["silver.ready"]["state"], "late")

    def test_each_state_is_recorded_once_a_day(self):
        late = [{"check_id": "silver.ready", "layer": "silver", "state": "late", "due": True,
                 "business_day": "2026-10-19", "ready_by": "x", "checks": {}}]
        rows = readiness_records(late, {})
        self.assertEqual([(r["check_id"], r["state"]) for r in rows], [("silver.ready", "late")])
        self.assertEqual(readiness_records(late, {"silver.ready": {"late"}}), [])
        ready = [{**late[0], "state": "ready"}]
        self.assertEqual([r["state"] for r in readiness_records(ready, {"silver.ready": {"late"}})], ["ready"])
        self.assertEqual(readiness_records(ready, {"silver.ready": {"late", "ready"}}), [])
        waiting = [{**late[0], "state": "waiting", "due": False}]
        self.assertEqual(readiness_records(waiting, {}), [])

    def test_freshness_results_become_history_rows(self):
        observed = _freshness(TUESDAY, unavailable=("gold.crisis_replay_daily",))
        observed[0] = {**observed[0], "state": "late", "late": True}
        rows = {r["check_id"]: r for r in layer_quality.freshness_results(observed)}
        self.assertEqual(set(rows), {r["check_id"] for r in observed})
        first = rows["bronze.raw_reports"]
        self.assertEqual((first["kind"], first["state"], first["severity"]), ("freshness", "fail", "error"))
        self.assertEqual(first["detail"]["freshness"], "late")
        self.assertEqual(rows["gold.crisis_replay_daily"]["state"], "unavailable")
        self.assertEqual(rows["silver.nav_snapshots"]["detail"]["latest"], "2026-10-19")


def _fail(check_id: str, severity: str, *, kind: str = "volume", layer: str = "silver") -> dict:
    return {"check_id": check_id, "kind": kind, "layer": layer, "target": "silver.nav_snapshots", "state": "fail",
            "severity": severity, "observed": 3,
            "detail": {"direction": "low", "date": "2026-10-19", "typical": 10,
                       "failed": [{"subject": "***001", "move_pct": 12.0}]}}


class ReviewTests(unittest.TestCase):
    late = {"check_id": "silver.ready", "layer": "silver", "state": "late", "due": True,
            "business_day": "2026-10-19", "ready_by": "2026-10-20T09:00:00+00:00",
            "checks": {"silver.benchmark_prices": {"ready": False}, "silver.nav_snapshots": {"ready": True}}}

    def test_shadow_findings_are_info_and_quiet(self):
        outcome = {"readiness": [self.late], "daily": [_fail("volume.silver.nav_snapshots", "error")]}
        review = review_layer_quality(outcome, warn=False)
        self.assertEqual({f.severity for f in review.findings}, {"info"})
        self.assertEqual(review.quiet, {f.incident_key for f in review.findings})
        self.assertTrue(all(f.autonomous_action is None for f in review.findings))
        self.assertIn("not ready by 09:00 UTC", review.findings[0].description)
        self.assertIn("silver.benchmark_prices", review.findings[0].description)

    def test_warn_raises_late_layers_and_error_checks_only(self):
        outcome = {"readiness": [self.late],
                   "daily": [_fail("volume.silver.nav_snapshots", "error"),
                             _fail("value.nav_move", "warning", kind="value")]}
        review = review_layer_quality(outcome, warn=True)
        severities = {f.incident_key: f.severity for f in review.findings}
        self.assertEqual(severities, {"layer_quality:silver.ready": "warning",
                                      "layer_quality:volume.silver.nav_snapshots": "warning",
                                      "layer_quality:value.nav_move": "info"})
        nav = next(f for f in review.findings if f.incident_key == "layer_quality:value.nav_move")
        self.assertEqual(nav.evidence["subjects"], ["***001"])
        self.assertNotIn("move_pct", json.dumps(nav.evidence))
        # An info incident that becomes a warning is announced.
        escalated = review_layer_quality(outcome, warn=True, open_incidents={
            "layer_quality:silver.ready": {"severity": "info", "evidence": {}}})
        self.assertEqual(escalated.escalated, {"layer_quality:silver.ready"})

    def test_incidents_close_only_when_their_check_passes(self):
        open_incidents = {key: {"severity": "info", "evidence": {}} for key in (
            "layer_quality:silver.ready", "layer_quality:volume.silver.nav_snapshots",
            "layer_quality:schema.silver.trades", "layer_freshness:silver.nav_snapshots")}
        # A run without the daily pass, layer still waiting: everything stays open.
        waiting = {**self.late, "state": "waiting"}
        review = review_layer_quality({"readiness": [waiting], "daily": None}, warn=False,
                                      open_incidents=open_incidents)
        self.assertEqual(review.hold_open, {k for k in open_incidents if k.startswith("layer_quality:")})
        # The daily pass: the volume check passes, the schema check could not be read.
        daily = [{"check_id": "volume.silver.nav_snapshots", "state": "pass", "detail": {}},
                 {"check_id": "schema.silver.trades", "state": "unavailable", "detail": {"error": "x"}}]
        ready = {**self.late, "state": "ready"}
        review = review_layer_quality({"readiness": [ready], "daily": daily}, warn=False,
                                      open_incidents=open_incidents)
        self.assertEqual(review.hold_open, {"layer_quality:schema.silver.trades"})
        # The check itself failed: everything stays open.
        review = review_layer_quality(None, warn=False, open_incidents=open_incidents)
        self.assertEqual(len(review.hold_open), 3)
        self.assertEqual(review.findings, ())


class _Session:
    """A read-only session where every count finds 7 rows and the named relations do not exist."""

    def __init__(self, absent=()):
        self.absent = set(absent)
        self.queries: list[str] = []

    def execute(self, query, params=None):
        self.queries.append(query)
        cursor = Mock()
        if "to_regclass" in query:
            cursor.fetchall.return_value = [{"relation": r} for r in params[0] if r in self.absent]
        elif "rolbypassrls" in query:
            cursor.fetchall.return_value = [{"bypass": False}]
        else:
            cursor.fetchall.return_value = [{"total": 7}]
        return cursor


class HeldTableTests(unittest.TestCase):
    def test_a_held_table_is_skipped_until_its_migration_has_run(self):
        held = Volume("nav_snapshots_history", "silver", count_column="version_id", qualified=True, held=True)
        plain = Volume("accounts", "silver", count_column="account_id")
        pass_plan = layer_quality.Plan(volumes=(held, plain))
        session = _Session(absent={"silver.nav_snapshots_history"})
        results = {r["check_id"]: r for r in layer_quality.measure_daily(
            session, pass_plan, now=_at(TUESDAY, 10, 30), results=[], time_budget_seconds=60)}
        self.assertEqual(results["volume.silver.nav_snapshots_history"]["state"], "skipped")
        self.assertIn("held migration", results["volume.silver.nav_snapshots_history"]["detail"]["reason"])
        self.assertEqual(results["volume.silver.accounts"]["observed"], 7)
        self.assertNotIn("nav_snapshots_history", " ".join(q for q in session.queries if "count(*)" in q))
        # Once the migration has run, the history is counted as layer.table.
        session = _Session()
        results = {r["check_id"]: r for r in layer_quality.measure_daily(
            session, pass_plan, now=_at(TUESDAY, 10, 30), results=[], time_budget_seconds=60)}
        self.assertEqual(results["volume.silver.nav_snapshots_history"]["observed"], 7)
        self.assertIn("SELECT count(*) AS total FROM silver.nav_snapshots_history", session.queries)

    def test_held_schema_and_rule_checks_are_skipped_too(self):
        pass_plan = layer_quality.plan({layer_quality.CONTRACTS_DIR_ENV: str(CONTRACTS)})
        pass_plan.volumes = ()
        session = _Session(absent=pass_plan.held)
        with patch.object(layer_quality, "judge_schema", return_value={"check_id": "x", "state": "pass"}), \
                patch("ibkr_etl.medallion_contracts.introspect", return_value={}):
            results = layer_quality.measure_daily(session, pass_plan, now=_at(TUESDAY, 10, 30), results=[],
                                                  time_budget_seconds=60)
        skipped = {r["check_id"] for r in results if r["state"] == "skipped"
                   and "held migration" in str(r["detail"].get("reason"))}
        self.assertIn("schema.silver.trades_history", skipped)
        self.assertTrue(any(c.startswith("rule.silver.trades_history.") for c in skipped))
        self.assertFalse(any("certified" in c for c in skipped))


class _History:
    """Stands in for open_history's session."""

    def __init__(self, today=None, fail: Exception | None = None):
        self.today = today or []
        self.fail = fail
        self.inserted: list = []
        self.committed = False

    def execute(self, query, params=None):
        if self.fail:
            raise self.fail
        cursor = Mock()
        cursor.fetchall.return_value = [dict(row) for row in self.today] if "UNION ALL" in query else []
        return cursor

    def cursor(self):
        history = self

        class _Cursor:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def executemany(self, query, rows):
                history.inserted.extend(rows)
        return _Cursor()

    def commit(self):
        self.committed = True

    def rollback(self):
        pass

    def close(self):
        pass


class AdministratorRunTests(unittest.TestCase):
    environ = {layer_quality.CONTRACTS_DIR_ENV: str(CONTRACTS)}

    def _run(self, now, history, *, daily=None, freshness=None, **options):
        calls = []

        def fake_daily(url, tenant, pass_plan, *, now):
            calls.append(pass_plan)
            return daily if daily is not None else [_fail("volume.silver.nav_snapshots", "error")]

        outcome = layer_quality.run_in_administrator(
            "postgresql://admin", "t", now=now, mode_name="shadow", environ=self.environ,
            freshness=freshness if freshness is not None else _freshness(now.date()),
            history=lambda url, tenant: history, daily=fake_daily, **options)
        return outcome, calls

    def test_the_daily_pass_runs_once_a_day_after_the_chain(self):
        history = _History()
        outcome, calls = self._run(_at(TUESDAY, 9, 30), history)
        self.assertEqual((outcome["daily"], calls), (None, []))
        self.assertEqual([row[5] for row in history.inserted], ["bronze.ready", "silver.ready", "gold.ready"])
        history = _History()
        outcome, calls = self._run(_at(TUESDAY, 10, 30), history)
        self.assertEqual(len(calls), 1)
        kinds = {row[4] for row in history.inserted}
        self.assertEqual(kinds, {"readiness", "freshness", "volume"})
        self.assertTrue(history.committed)
        self.assertEqual(outcome["history"]["recorded"], len(history.inserted))
        # Recorded already today: the next run skips it, and skips the readiness rows it has.
        history = _History(today=[{"kind": "daily", "check_id": "daily", "state": "ran", "source": "monitor"}] + [
            {"kind": "readiness", "check_id": t, "state": "ready", "source": "monitor"}
            for t in ("bronze.ready", "silver.ready", "gold.ready")])
        outcome, calls = self._run(_at(TUESDAY, 10, 35), history)
        self.assertEqual((calls, history.inserted), ([], []))

    def test_without_readable_history_only_the_first_run_after_the_chain_measures(self):
        history = _History(fail=RuntimeError("relation does not exist"))
        outcome, calls = self._run(_at(TUESDAY, 10, 31), history)
        self.assertEqual(len(calls), 1)
        self.assertEqual(outcome["history"], {"error": "RuntimeError"})
        outcome, calls = self._run(_at(TUESDAY, 10, 36), history)
        self.assertEqual(calls, [])

    def test_contract_rules_the_gate_ran_today_are_not_run_again(self):
        history = _History(today=[{"kind": "rule", "check_id": "rule.silver.trades.trade_date_is_iso_date",
                                   "state": "pass", "source": "publish_gate"}])
        outcome, calls = self._run(_at(TUESDAY, 10, 30), history)
        self.assertIn("rule.silver.trades.trade_date_is_iso_date", calls[0].gate_rules)
        self.assertNotIn("rule.silver.trades.trade_date_is_iso_date", [p[0] for p in calls[0].planned()])

    def test_the_time_budget_is_a_hard_limit(self):
        release = threading.Event()

        def slow_daily(url, tenant, pass_plan, *, now):
            release.wait(5)
            return []

        started = datetime.now()
        outcome = layer_quality.run_in_administrator(
            "postgresql://admin", "t", now=_at(TUESDAY, 10, 30), mode_name="shadow", environ=self.environ,
            freshness=_freshness(TUESDAY), history=lambda url, tenant: _History(), daily=slow_daily,
            time_budget_seconds=0.2)
        release.set()
        self.assertLess((datetime.now() - started).total_seconds(), 2)
        self.assertIn("TimeoutError", outcome["error"])

    def test_freshness_is_measured_here_when_its_own_check_is_off(self):
        measured = []

        def measure(url, tenant, *, now, environ, time_budget_seconds):
            measured.append((url, environ))
            return _freshness(now.date())

        outcome = layer_quality.run_in_administrator(
            "postgresql://admin", "t", now=_at(TUESDAY, 9, 30), mode_name="shadow",
            environ={layer_quality.DATABASE_URL_ENV: "postgresql://reader"}, history=lambda u, t: _History(),
            measure_freshness=measure)
        self.assertEqual(measured, [("postgresql://reader", {})])  # its own reader, never the blob checks
        self.assertEqual({i["state"] for i in outcome["readiness"]}, {"ready"})

    def test_the_log_line_names_checks_but_no_subjects_or_figures(self):
        daily = [judge_nav_moves([{"subject_id": "U9100001", "currency": "USD", "dates": 20, "newest": "150",
                                   "previous": "100", "newest_date": "2026-10-19",
                                   "previous_date": "2026-10-16"}])]
        outcome, _ = self._run(_at(TUESDAY, 10, 30), _History(), daily=daily)
        line = json.dumps(layer_quality.summary(outcome))
        self.assertIn("value.nav_move", line)
        for secret in ("U9100001", "***001", "150", "move_pct"):
            self.assertNotIn(secret, line)


class ReportTests(unittest.TestCase):
    @staticmethod
    def _rows() -> list[dict]:
        rows = []
        # Business days 12 to 16 Oct, judged on 13 to 17 Oct.
        for day in (13, 14, 15, 16, 17):
            cycle = date(2026, 10, day)
            rows.append({"cycle_date": cycle, "kind": "readiness", "check_id": "silver.ready", "layer": "silver",
                         "target": None, "state": "ready", "severity": None, "checked_at": _at(cycle, 8, 40),
                         "source": "monitor"})
            rows.append({"cycle_date": cycle, "kind": "daily", "check_id": "daily", "layer": "silver",
                         "target": None, "state": "ran", "severity": None, "checked_at": _at(cycle, 10, 30),
                         "source": "monitor"})
        # 15 Oct: silver late, ready at 09:40.
        rows = [r for r in rows if not (r["kind"] == "readiness" and r["cycle_date"] == date(2026, 10, 16))]
        rows += [{"cycle_date": date(2026, 10, 16), "kind": "readiness", "check_id": "silver.ready",
                  "layer": "silver", "target": None, "state": state, "severity": None,
                  "checked_at": _at(date(2026, 10, 16), hour, minute), "source": "monitor"}
                 for state, hour, minute in (("late", 9, 0), ("ready", 9, 40))]
        # 17 Oct: first seen ready at 11:00 (the monitor was not running at 09:00): unmeasured.
        rows = [r for r in rows if not (r["kind"] == "readiness" and r["cycle_date"] == date(2026, 10, 17))]
        rows.append({"cycle_date": date(2026, 10, 17), "kind": "readiness", "check_id": "silver.ready",
                     "layer": "silver", "target": None, "state": "ready", "severity": None,
                     "checked_at": _at(date(2026, 10, 17), 11), "source": "monitor"})
        rows.append({"cycle_date": date(2026, 10, 14), "kind": "volume", "check_id": "volume.silver.trades",
                     "layer": "silver", "target": "silver.trades", "state": "fail", "severity": "error",
                     "checked_at": _at(date(2026, 10, 14), 10, 30), "source": "monitor"})
        rows.append({"cycle_date": date(2026, 10, 14), "kind": "rule", "check_id": "rule.gold.x.y",
                     "layer": "gold", "target": "gold.x", "state": "unavailable", "severity": None,
                     "checked_at": _at(date(2026, 10, 14), 10, 30), "source": "monitor"})
        return rows

    def test_targets_are_met_missed_or_unmeasured(self):
        weekly = layer_quality.report(self._rows(), week_ending=date(2026, 10, 18))
        targets = {t["target_id"]: t for t in weekly["targets"]}
        silver = targets["silver.ready"]
        self.assertEqual(silver["week"], {"business_days": 5, "met": 3, "missed": 1, "unmeasured": 1,
                                          "share": 0.75})
        self.assertEqual(silver["status"], "missed")
        self.assertEqual(targets["gold.ready"]["status"], "not measured")
        checks = targets["silver.checks"]["week"]
        self.assertEqual((checks["met"], checks["missed"]), (4, 1))
        self.assertEqual(weekly["failed_checks"], [{"layer": "silver", "kind": "volume",
                                                    "check_id": "volume.silver.trades", "table": "silver.trades",
                                                    "severity": "error", "source": "monitor", "days": 1}])
        self.assertEqual(weekly["unavailable_checks"], {"count": 1, "check_ids": ["rule.gold.x.y"]})

    def test_the_text_report_holds_counts_and_names_only(self):
        text = layer_quality.render(layer_quality.report(self._rows(), week_ending=date(2026, 10, 18)))
        self.assertIn("silver ready by 09:00 UTC on 99% of business days: MISSED", text)
        self.assertIn("volume.silver.trades", text)
        self.assertNotIn("***", text)
        self.assertEqual(layer_quality._week_ending(date(2026, 10, 21)), date(2026, 10, 18))
        self.assertEqual(layer_quality._week_ending(date(2026, 10, 18)), date(2026, 10, 18))


class BicepTests(unittest.TestCase):
    def test_the_administrator_job_has_layer_quality_off_by_default(self):
        text = (ROOT / "infra" / "staging-autonomous-admin.bicep").read_text(encoding="utf-8")
        self.assertIn("param layerQualityMode string = 'off'", text)
        self.assertIn("var layerQualityEnv = layerQualityMode == 'off' ? [] : [", text)
        self.assertIn("{ name: 'LAYER_QUALITY_MODE', value: layerQualityMode }", text)
        self.assertIn("], layerFreshnessEnv), layerQualityEnv)", text)
        self.assertEqual(text.count("LAYER_QUALITY_MODE"), 1)


if __name__ == "__main__":
    unittest.main()
