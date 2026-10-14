"""Synthetic qualification of composites reporting in the MS or T design. Nothing live is touched."""
import copy
from datetime import date, timedelta
import hashlib
import json
import math
import random
import sqlite3
import unittest
from unittest.mock import Mock, patch

from ibkr_etl import profiled_composite as pc
from ibkr_etl.alpha_world_ms_analytics import _analyze, simulate as ms_simulate
from ibkr_etl.benchmark_analytics import CALCULATION_VERSION
from ibkr_etl.hibor import expected_fixing_dates, period_average
from ibkr_etl.report_profiles import ReportProfileError, build_profile, profile_from_metadata
from ibkr_etl.risk_snapshots import (
    RiskInputsNotReady, compute_risk_snapshot, load_risk_inputs, risk_input_fingerprint, snapshot_read_status,
    store_risk_snapshot, validate_risk_input_readiness,
)
from ibkr_etl.subject_onboarding import configuration_sha256, fixed_unit_composite_definition
from ibkr_etl.util import canonical_json

SUBJECT = "alpha-world-asia"
INCEPTION = date(2025, 3, 3)
DESCRIPTION = "Long China and Hong Kong, hedged with the Hang Seng Index."


def definition(**changes):
    fields = dict(
        subject_id=SUBJECT, display_name="Alpha World Asia", base_currency="HKD",
        normalization_date=INCEPTION.isoformat(), constant_cash=58.0,
        components=[
            {"component_id": "hong-kong-china", "component_name": "China & Hong Kong", "component_type": "portfolio",
             "coefficient": 0.91, "source_currency": "USD", "price_convention": "nav"},
            {"component_id": "hang-seng-price-index", "component_name": "Hang Seng Index price index",
             "component_type": "benchmark", "coefficient": -0.49, "source_currency": "HKD",
             "price_convention": "price_index_close"},
        ],
    )
    fields.update(changes)
    return fixed_unit_composite_definition(**fields)


def record(style="alpha-world-ms", **changes):
    return build_profile(definition=definition(**changes), style=style, description=DESCRIPTION)


def profile(style="alpha-world-ms"):
    return profile_from_metadata(SUBJECT, {"report_profile": record(style)})


def snapshot(style="alpha-world-ms", end=date(2025, 3, 7)):
    chosen = profile(style)
    days, cursor = [], INCEPTION
    while cursor <= end:
        if cursor.weekday() < 5:
            days.append(cursor)
        cursor += timedelta(days=1)
    fixings = [{"rate_date": d.isoformat(), "annual_rate": .03 + .001 * i}
               for i, d in enumerate(expected_fixing_dates(days[0], days[-1]))]
    return {"subject_id": SUBJECT, "period_start": INCEPTION.isoformat(), "period_end": end.isoformat(),
            "data_as_of": days[-1].isoformat(), "reporting_currency": "HKD",
            "methodology_identity": pc.methodology_identity(chosen), "report_profile": chosen.as_dict(),
            "performance": {"series": [{"date": d.isoformat(), "currency": "HKD",
                                        "nav": 100 * (1 + .002 * i) * (1 + .004 * math.sin(i))}
                                       for i, d in enumerate(days)]},
            "risk": {"risk_free": period_average(fixings, days[0], days[-1])}}


def inputs(source):
    return {"subject_id": source["subject_id"], "subject_type": "composite_portfolio", "as_of": source["period_end"],
            "benchmark_id": None, "rows": [], "positions": [], "risk_free": source["risk"]["risk_free"],
            "crisis_replay": None, "profiled_hkd_snapshot": source}


class AnalyticsTests(unittest.TestCase):
    def test_same_arithmetic_as_ms_on_the_composites_own_history(self):
        for style, markets in (("alpha-world-ms", ("XNYS", "XHKG")),
                               ("alpha-world-t", ("XNYS", "XHKG", "XSHG", "XTAI"))):
            with self.subTest(style=style):
                s = snapshot(style)
                before = copy.deepcopy(s)
                metrics, daily, evidence = pc.analyze(s)
                expected = _analyze(s, subject_id=SUBJECT, version=pc.ANALYTICS_VERSION, markets=markets)
                self.assertEqual((metrics, daily), expected[:2])
                self.assertEqual(evidence, {**expected[2], "session_calendars": list(markets), "report_style": style})
                self.assertEqual(s, before)
                self.assertEqual(s["methodology_identity"]["contract_sha256"], configuration_sha256(definition()))

    def test_design_decides_which_closed_markets_break_a_daily_return(self):
        # Good Friday closes New York and Hong Kong but not Shanghai or Taipei.
        missing = "2025-04-18"
        for style, excluded in (("alpha-world-ms", []),
                                ("alpha-world-t", [{"from": "2025-04-17", "to": "2025-04-21",
                                                    "missing_sessions": [missing]}])):
            with self.subTest(style=style):
                s = snapshot(style, date(2025, 4, 22))
                s["performance"]["series"] = [r for r in s["performance"]["series"] if r["date"] != missing]
                _, _, evidence = pc.analyze(s)
                self.assertEqual(evidence["excluded_intervals"], excluded)

    def test_identity_currency_inception_profile_and_hibor_fail_closed(self):
        mutations = (
            lambda s: s.update(subject_id="alpha-world-ms"),
            lambda s: s.update(subject_id="alpha-world-other"),
            lambda s: s.pop("report_profile"),
            lambda s: s["report_profile"].update(style="alpha-world-t"),
            lambda s: s["report_profile"].update(markets=["XNYS"]),
            lambda s: s.update(reporting_currency="USD"),
            lambda s: s.update(period_start="2025-03-04"),
            lambda s: s["methodology_identity"].update(contract_sha256="0" * 64),
            lambda s: s["methodology_identity"].update(analytics_version="alpha-world-ms-factsheet-hkd-hibor-v3"),
            lambda s: s["performance"]["series"][1].update(currency="USD"),
            lambda s: s["performance"]["series"][1].update(nav=0),
            lambda s: s["risk"]["risk_free"].update(annual_rate=.5),
            lambda s: s["risk"]["risk_free"].update(rate_date="2025-03-04"),
            lambda s: s["risk"]["risk_free"].update(quality_status="fail"),
            lambda s: s["risk"]["risk_free"]["observations"].pop(1),
        )
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                s = snapshot()
                mutation(s)
                with self.assertRaises(ValueError):
                    pc.analyze(s)

    def test_errors_name_the_composite_not_ms(self):
        s = snapshot()
        s["data_as_of"] = "2025-03-06"
        with self.assertRaisesRegex(ValueError, "^The composite's last valuation"):
            pc.analyze(s)

    def test_simulation_seeds_on_the_composite(self):
        daily = (.002, -.001, .003)
        sample, mean = pc.simulate(daily, "2025-03-31", subject_id=SUBJECT, paths=5)
        seed = int(hashlib.sha256(f"{SUBJECT}|2025-03-31|{pc.ANALYTICS_VERSION}".encode()).hexdigest()[:16], 16)
        rng = random.Random(seed)
        self.assertEqual(sample, [math.prod(1 + daily[rng.randrange(3)] for _ in range(252)) - 1 for _ in range(5)])
        self.assertAlmostEqual(mean, sum(sample) / 5)
        self.assertNotEqual(sample, ms_simulate(daily, "2025-03-31", paths=5)[0])
        with self.assertRaises(ValueError):
            pc.simulate((float("nan"),), "2025-03-31", subject_id=SUBJECT, paths=1)


class RuntimeTests(unittest.TestCase):
    def test_payload_round_trips_and_names_its_design(self):
        source = snapshot("alpha-world-t", date(2025, 4, 4))
        before = copy.deepcopy(source)
        payload = pc.build_payload(inputs(source))
        self.assertEqual(source, before)
        self.assertTrue(pc.validate_payload(payload))
        self.assertEqual((payload["account_id"], payload["runtime_version"], payload["report_style"]),
                         (SUBJECT, pc.RUNTIME_VERSION, "alpha-world-t"))
        self.assertFalse(payload["publication_allowed"])
        self.assertIsNone(payload["crisis_replay"])
        self.assertIn("crisis_replay", payload["unavailable_capabilities"])
        self.assertEqual(payload["metrics"]["return_observations"], payload["analytics_evidence"]
                         ["daily_return_observations"])

    def test_tampered_payloads_and_inputs_are_refused(self):
        original = pc.build_payload(inputs(snapshot()))
        mutations = (
            lambda p: p.update(publication_allowed=True),
            lambda p: p.update(report_style="alpha-world-t"),
            lambda p: p.update(account_id="alpha-world-ms"),
            lambda p: p.update(runtime_version="alpha-world-ms-hkd-risk-runtime-v2"),
            lambda p: p["metrics"].update(total_return=.9),
            lambda p: p["source_snapshot"]["report_profile"].update(display_name="Someone else"),
            lambda p: p["source_snapshot"]["methodology_identity"].update(contract_sha256="0" * 64),
        )
        for mutate in mutations:
            with self.subTest(mutate=mutate):
                payload = copy.deepcopy(original)
                mutate(payload)
                with self.assertRaises(ValueError):
                    pc.validate_payload(payload)
        for change in ({"benchmark_id": "GSPC"}, {"crisis_replay": {"maximum_drawdown": -.1}},
                       {"rows": [{"x": 1}]}, {"subject_type": "portfolio"}, {"as_of": "2025-03-06"}):
            with self.subTest(change=change):
                with self.assertRaises(ValueError):
                    pc.build_payload({**inputs(snapshot()), **change})

    def test_stale_nav_cannot_stand_for_the_latest_session(self):
        source = snapshot()
        source["period_end"] = "2025-03-11"
        with self.assertRaisesRegex(ValueError, "latest required market session"):
            pc.build_payload(inputs(source))


class RiskSnapshotRoutingTests(unittest.TestCase):
    def record(self, payload):
        return {"payload_json": payload, "payload_sha256": hashlib.sha256(canonical_json(payload).encode()).hexdigest(),
                "quality_status": "warn", "comparison_version": CALCULATION_VERSION}

    def test_composite_inputs_never_reach_the_usd_benchmark_arithmetic(self):
        loaded = inputs(snapshot())
        connection = Mock()
        with patch("ibkr_etl.risk_snapshots.portfolio_risk_metrics") as legacy:
            payload, _, fingerprint = compute_risk_snapshot(
                connection, tenant_id="tenant", subject_id=SUBJECT, as_of=loaded["as_of"],
                loaded_inputs=(loaded, {"input_query_ms": 0}))
        legacy.assert_not_called()
        connection.execute.assert_not_called()
        self.assertEqual(payload, pc.build_payload(loaded))
        self.assertEqual(fingerprint, risk_input_fingerprint(loaded))
        self.assertEqual(snapshot_read_status(self.record(payload), subject_id=SUBJECT, as_of=loaded["as_of"]),
                         "current")
        store_risk_snapshot(connection, tenant_id="tenant", subject_id=SUBJECT, as_of=loaded["as_of"],
                            payload=payload, timings={}, source_data_sha256=fingerprint)
        self.assertIn("INSERT INTO risk_analytics_snapshots", connection.execute.call_args.args[0])

    def test_rehashed_tampering_reads_as_mismatch_and_is_never_stored(self):
        payload = pc.build_payload(inputs(snapshot()))
        payload["metrics"]["total_return"] = .9
        self.assertEqual(snapshot_read_status(self.record(payload), subject_id=SUBJECT, as_of=payload["as_of"]),
                         "composite_methodology_mismatch")
        connection = Mock()
        with self.assertRaisesRegex(RiskInputsNotReady, "^Composite output"):
            store_risk_snapshot(connection, tenant_id="tenant", subject_id=SUBJECT, as_of=payload["as_of"],
                                payload=payload, timings={}, source_data_sha256="0" * 64)
        connection.execute.assert_not_called()

    def test_supplied_inputs_pass_the_same_gate_and_every_value_moves_the_fingerprint(self):
        loaded = inputs(snapshot())
        validate_risk_input_readiness(loaded)
        with self.assertRaises(RiskInputsNotReady):
            validate_risk_input_readiness({**loaded, "benchmark_id": "GSPC"})
        with self.assertRaises(RiskInputsNotReady):
            compute_risk_snapshot(Mock(), tenant_id="tenant", subject_id=SUBJECT, as_of=loaded["as_of"],
                                  loaded_inputs=({**loaded, "crisis_replay": {}}, {"input_query_ms": 0}))
        corrected = copy.deepcopy(loaded)
        corrected["profiled_hkd_snapshot"]["performance"]["series"][2]["nav"] += .01
        self.assertNotEqual(risk_input_fingerprint(loaded), risk_input_fingerprint(corrected))


class StoredInputTests(unittest.TestCase):
    def setUp(self):
        self.c = sqlite3.connect(":memory:")
        self.c.row_factory = sqlite3.Row
        self.addCleanup(self.c.close)
        self.c.executescript("""
            CREATE TABLE platform_subjects(tenant_id,subject_id,subject_type,base_currency,reporting_currency,
                history_start_date,metadata_json);
            CREATE TABLE derived_portfolios(tenant_id,portfolio_id,base_currency);
            CREATE TABLE subject_calculation_definitions(tenant_id,subject_id,configuration_json,
                configuration_sha256,approval_status,effective_to);
            CREATE TABLE subject_nav_series(tenant_id,subject_id,snapshot_date,currency,total_text);
            CREATE TABLE risk_free_rates(tenant_id,rate_date,series_id,currency,tenor,annual_rate,quality_status);
        """)
        self.definition = definition()
        self.c.execute("INSERT INTO platform_subjects VALUES ('tenant',?,'composite_portfolio','HKD','HKD',?,?)",
                       (SUBJECT, INCEPTION.isoformat(), json.dumps({"report_profile": record()})))
        self.c.execute("INSERT INTO derived_portfolios VALUES ('tenant',?,'HKD')", (SUBJECT,))
        self.c.execute("INSERT INTO subject_calculation_definitions VALUES ('tenant',?,?,?,'approved',NULL)",
                       (SUBJECT, json.dumps(self.definition), configuration_sha256(self.definition)))
        s = snapshot()
        for row in s["performance"]["series"]:
            for currency, value in (("HKD", row["nav"]), ("USD", 999)):
                self.c.execute("INSERT INTO subject_nav_series VALUES ('tenant',?,?,?,?)",
                               (SUBJECT, row["date"], currency, str(value)))
        for row in s["risk"]["risk_free"]["observations"]:
            self.c.execute("INSERT INTO risk_free_rates VALUES ('tenant',?,'HKAB-HIBOR-1M','HKD','1-month',?,'pass')",
                           (row["rate_date"], row["annual_rate"]))
        self.c.execute("INSERT INTO subject_nav_series VALUES ('other',?,'2025-03-04','HKD','12345')", (SUBJECT,))
        self.c.commit()
        self.expected = s

    def test_load_is_tenant_scoped_hkd_only_and_read_only(self):
        before = list(self.c.iterdump())
        with self.assertRaisesRegex(ValueError, "explicit tenant"):
            pc.load_risk_snapshot(self.c, SUBJECT, "2025-03-07")
        s, metrics, evidence = pc.load_risk_snapshot(self.c, SUBJECT, "2025-03-07", tenant_id="tenant")
        self.assertEqual(s["performance"], self.expected["performance"])
        self.assertEqual(s["report_profile"], self.expected["report_profile"])
        self.assertEqual(s["methodology_identity"], self.expected["methodology_identity"])
        self.assertEqual(metrics, pc.analyze(self.expected)[0])
        self.assertEqual(evidence["report_style"], "alpha-world-ms")
        self.assertEqual(before, list(self.c.iterdump()))
        self.assertTrue(pc.is_profiled(self.c, SUBJECT, tenant_id="tenant"))
        self.assertFalse(pc.is_profiled(self.c, SUBJECT, tenant_id="other"))
        self.assertFalse(pc.is_profiled(self.c, "alpha-world-ms", tenant_id="tenant"))
        with self.assertRaises(ValueError):
            pc.load_risk_snapshot(self.c, SUBJECT, "2025-03-07", tenant_id="other")

    def test_risk_inputs_route_to_the_composite_loader(self):
        loaded, timings = load_risk_inputs(self.c, tenant_id="tenant", subject_id=SUBJECT, as_of="2025-03-07")
        self.assertEqual(loaded["profiled_hkd_snapshot"]["performance"], self.expected["performance"])
        self.assertIsNone(loaded["benchmark_id"])
        self.assertEqual(timings["tail_position_query_ms"], 0.)
        self.c.execute("UPDATE platform_subjects SET history_start_date='2025-03-04'")
        with self.assertRaisesRegex(RiskInputsNotReady, "Composite qualified stored inputs unavailable"):
            load_risk_inputs(self.c, tenant_id="tenant", subject_id=SUBJECT, as_of="2025-03-07")

    def test_ms_and_t_keep_their_own_contracts(self):
        for subject in ("alpha-world-ms", "alpha-world-t"):
            with self.assertRaisesRegex(ValueError, "their own approved contracts"):
                pc.require_contract(self.c, subject, tenant_id="tenant")

    def test_a_new_definition_without_a_new_profile_is_refused(self):
        changed = definition(constant_cash=60.0)
        self.c.execute("UPDATE subject_calculation_definitions SET configuration_json=?, configuration_sha256=?",
                       (json.dumps(changed), configuration_sha256(changed)))
        with self.assertRaisesRegex(ValueError, "different definition"):
            pc.require_contract(self.c, SUBJECT, tenant_id="tenant")

    def test_metadata_and_inception_must_match_the_profile(self):
        for statement in ("UPDATE platform_subjects SET history_start_date='2025-03-04'",
                          "UPDATE derived_portfolios SET base_currency='USD'",
                          "UPDATE platform_subjects SET subject_type='portfolio'",
                          "UPDATE platform_subjects SET metadata_json='{}'",
                          "UPDATE subject_calculation_definitions SET approval_status='pending'"):
            with self.subTest(statement=statement):
                self.c.execute("SAVEPOINT s")
                self.c.execute(statement)
                with self.assertRaises(ValueError):
                    pc.require_contract(self.c, SUBJECT, tenant_id="tenant")
                self.c.execute("ROLLBACK TO s")
                self.c.execute("RELEASE s")
        self.c.execute("DELETE FROM subject_nav_series WHERE snapshot_date=?", (INCEPTION.isoformat(),))
        with self.assertRaisesRegex(ValueError, "inception history"):
            pc.load_risk_snapshot(self.c, SUBJECT, "2025-03-07", tenant_id="tenant")

    def test_tampered_stored_profile_is_refused(self):
        stored = record()
        stored["description"] = "A description nobody approved."
        self.c.execute("UPDATE platform_subjects SET metadata_json=?", (json.dumps({"report_profile": stored}),))
        with self.assertRaisesRegex(ValueError, "integrity"):
            pc.require_contract(self.c, SUBJECT, tenant_id="tenant")


if __name__ == "__main__":
    unittest.main()


class DailyRefreshTests(unittest.TestCase):
    def test_design_composites_build_their_hkd_risk_snapshot_only(self):
        import run_self_service_derived_refresh as worker
        connection = Mock()
        connection.execute.return_value.fetchone.return_value = {"latest": "2025-03-07"}
        with (patch.object(worker, "is_profiled", return_value=True) as profiled,
              patch("ibkr_etl.profiled_composite.require_contract") as contract,
              patch.object(worker, "build_risk_snapshot", return_value={"status": "rebuilt"}) as risk,
              patch.object(worker, "refresh_and_materialize") as comparison,
              patch.object(worker, "refresh_composite_crisis_replay") as crisis):
            result = worker._refresh_analytics_if_approved(
                connection, {"subject_id": SUBJECT, "subject_type": "composite_portfolio"}, tenant_id="tenant")
        profiled.assert_called_once_with(connection, SUBJECT, tenant_id="tenant")
        contract.assert_called_once_with(connection, SUBJECT, tenant_id="tenant")
        risk.assert_called_once_with(connection, tenant_id="tenant", subject_id=SUBJECT, as_of="2025-03-07")
        comparison.assert_not_called()
        crisis.assert_not_called()
        self.assertFalse(result["benchmark_comparison"]["available"])
        self.assertFalse(result["onboarding_activated"])

    def test_other_composites_keep_the_existing_path(self):
        import run_self_service_derived_refresh as worker
        connection = Mock()
        connection.execute.return_value.fetchone.return_value = None
        with (patch.object(worker, "is_profiled", return_value=False),
              patch.object(worker, "build_risk_snapshot") as risk):
            self.assertIsNone(worker._refresh_analytics_if_approved(
                connection, {"subject_id": "studio-mix", "subject_type": "composite_portfolio"}, tenant_id="tenant"))
        risk.assert_not_called()

    def test_strict_hkd_rules_need_an_hkd_composite(self):
        from ibkr_etl.derived_portfolio import refresh_fixed_unit_composite
        with self.assertRaisesRegex(ValueError, "Only an HKD composite"):
            refresh_fixed_unit_composite(Mock(), definition(base_currency="USD"), strict_hkd=True)
