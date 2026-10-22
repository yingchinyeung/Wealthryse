"""Month-end inputs for a composite in the Alpha World design, on synthetic data only."""
import copy
from datetime import date, timedelta
import json
import math
import sqlite3
import unittest

from ibkr_etl import profiled_composite as pc
from ibkr_etl import profiled_reporting as pr
from ibkr_etl.hibor import expected_fixing_dates, period_average
from ibkr_etl.reporting_snapshot import ReportingSnapshotError, _collect_tool_results, assemble_month_end_payload
from ibkr_etl.subject_onboarding import configuration_sha256

from test_profiled_composite import INCEPTION, SUBJECT, definition, profile, record

END = date(2025, 4, 30)


def history(style="alpha-world-t"):
    """NAV that is exactly the sum of its dated components, as the composite refresh writes it."""
    chosen = profile(style)
    days, cursor = [], INCEPTION
    while cursor <= END:
        if cursor.weekday() < 5:
            days.append(cursor)
        cursor += timedelta(days=1)
    plan = definition()
    components, series = [], []
    for i, day in enumerate(days):
        levels = {"hong-kong-china": 100 * (1 + .003 * i) * (1 + .01 * math.sin(i)),
                  "hang-seng-price-index": 100 * (1 + .001 * i) * (1 + .008 * math.cos(i))}
        nav = plan["constant_cash"]
        for sleeve in plan["components"]:
            key, coefficient = sleeve["component_id"], sleeve["coefficient"]
            level = levels[key]
            fx = 7.8 if sleeve["source_currency"] == "USD" else 1.0
            components.append({"portfolio_id": SUBJECT, "snapshot_date": day.isoformat(), "component_id": key,
                               "base_currency": "HKD", "coefficient": coefficient, "source_value": level / fx,
                               "fx_rate_to_base": fx, "base_value": level / fx * fx, "normalized_value": level,
                               "contribution": coefficient * level,
                               "direction": "long" if coefficient > 0 else "short"})
            nav += coefficient * level
        components.append({"portfolio_id": SUBJECT, "snapshot_date": day.isoformat(), "component_id": "constant-cash",
                           "base_currency": "HKD", "coefficient": plan["constant_cash"],
                           "source_value": plan["constant_cash"], "fx_rate_to_base": 1.0,
                           "base_value": plan["constant_cash"], "normalized_value": 1.0,
                           "contribution": plan["constant_cash"], "direction": "cash"})
        series.append({"date": day.isoformat(), "currency": "HKD", "nav": nav})
    fixings = [{"rate_date": d.isoformat(), "annual_rate": .04 + .0001 * i}
               for i, d in enumerate(expected_fixing_dates(days[0], days[-1]))]
    core = {"subject_id": SUBJECT, "period_start": INCEPTION.isoformat(), "period_end": END.isoformat(),
            "data_as_of": days[-1].isoformat(), "reporting_currency": "HKD",
            "methodology_identity": pc.methodology_identity(chosen), "report_profile": chosen.as_dict(),
            "performance": {"series": series}, "risk": {"risk_free": period_average(fixings, days[0], days[-1])}}
    return core, components


def assemble(results):
    return assemble_month_end_payload(subject_id=SUBJECT, period_start=INCEPTION.isoformat(),
                                      period_end=END.isoformat(), reporting_currency="HKD", tool_results=results)


class EnrichTests(unittest.TestCase):
    def test_sleeves_reconcile_to_nav_and_the_month_bridges_to_its_return(self):
        core, components = history()
        before = copy.deepcopy((core, components))
        snapshot = pr.enrich(core, components, definition())
        self.assertEqual((core, components), before)
        self.assertEqual([h["description"] for h in snapshot["holdings"]["holdings"]],
                         ["China & Hong Kong", "Hang Seng Index price index", "Constant cash"])
        self.assertEqual(snapshot["portfolio_summary"]["account"]["alias"], "Alpha World Asia")
        monthly = snapshot["monthly_reporting"]
        self.assertAlmostEqual(sum(r["contribution"] for r in monthly["security_contributions"]),
                               monthly["portfolio_monthly_return"], places=12)
        self.assertEqual({row["sector"] for row in snapshot["sector_allocation"]["allocation"]},
                         {"Long account NAV", "Short price index", "Constant cash"})
        self.assertEqual(snapshot["risk"]["metrics"], pc.analyze(core)[0])
        self.assertIsNone(snapshot["risk"]["crisis_replay"])
        self.assertEqual(snapshot["formula_version"], pc.ANALYTICS_VERSION)

    def test_components_that_do_not_match_the_approved_definition_are_refused(self):
        mutations = (
            lambda c: c[0].update(coefficient=.9, contribution=.9 * c[0]["normalized_value"]),
            lambda c: c[0].update(portfolio_id="alpha-world-t"),
            lambda c: c[0].update(base_currency="USD"),
            lambda c: c[0].update(direction="short"),
            lambda c: c[2].update(contribution=59.0),
            lambda c: c[1].update(contribution=c[1]["contribution"] + 1),
            lambda c: c.pop(4),
            lambda c: c.append(dict(c[0])),
        )
        for mutate in mutations:
            with self.subTest(mutate=mutate):
                core, components = history()
                mutate(components)
                with self.assertRaises(ValueError):
                    pr.enrich(core, components, definition())


class AssemblyTests(unittest.TestCase):
    def test_payload_carries_profile_identity_and_full_history_metrics(self):
        core, components = history()
        payload, quality, publication_allowed, blockers = assemble(pr.tool_results(pr.enrich(core, components,
                                                                                            definition())))
        self.assertEqual(payload["report_profile"], core["report_profile"])
        self.assertEqual(payload["methodology_identity"], core["methodology_identity"])
        self.assertEqual(payload["formula_version"], pc.ANALYTICS_VERSION)
        self.assertEqual([p["date"][:7] for p in payload["month_end_performance"]], ["2025-03", "2025-04"])
        self.assertEqual(payload["risk"]["analytics_evidence"]["report_style"], "alpha-world-t")
        self.assertEqual(quality, "warn")
        self.assertFalse(publication_allowed)
        self.assertIn("risk:crisis_replay_unavailable", blockers)

    def test_a_changed_metric_profile_or_inception_is_refused(self):
        core, components = history()
        good = pr.tool_results(pr.enrich(core, components, definition()))
        mutations = (
            lambda r: r["risk"]["data"]["metrics"].update(sharpe_ratio=9.0),
            lambda r: r["report_profile"]["data"]["report_profile"].update(display_name="Someone else"),
            lambda r: r["report_profile"]["data"]["methodology_identity"].update(contract_sha256="0" * 64),
            lambda r: r["report_profile"]["data"]["report_profile"].update(inception="2025-03-04"),
            lambda r: r["summary"]["data"]["latest_nav"].update(currency="USD"),
        )
        for mutate in mutations:
            with self.subTest(mutate=mutate):
                results = copy.deepcopy(good)
                mutate(results)
                with self.assertRaises((ValueError, ReportingSnapshotError)):
                    assemble(results)


class CollectTests(unittest.TestCase):
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
            CREATE TABLE synthetic_portfolio_components(tenant_id,portfolio_id,snapshot_date,component_id,
                base_currency,coefficient,source_value,fx_rate_to_base,base_value,normalized_value,contribution,
                direction);
            CREATE TABLE subject_position_series(tenant_id,subject_id,snapshot_date,conid,currency,market_value_text);
            CREATE TABLE crisis_replay_components(tenant_id,subject_id,holdings_as_of,conid,currency,price_type,
                mapping_method,source_symbol,current_market_value,opening_weight,proxy_reason);
            CREATE TABLE crisis_replay_daily(tenant_id,subject_id,holdings_as_of,observation_date,calculation_version,
                quality_status,normalized_value,drawdown);
            CREATE TABLE market_data_prices(tenant_id,instrument_id,symbol,price_date,price,currency,price_type);
            CREATE TABLE reference_fx_rates(tenant_id,from_currency,to_currency,rate_date,rate,provider,quality_status);
        """)
        plan = definition()
        self.c.execute("INSERT INTO platform_subjects VALUES ('tenant',?,'composite_portfolio','HKD','HKD',?,?)",
                       (SUBJECT, INCEPTION.isoformat(), json.dumps({"report_profile": record("alpha-world-t")})))
        self.c.execute("INSERT INTO derived_portfolios VALUES ('tenant',?,'HKD')", (SUBJECT,))
        self.c.execute("INSERT INTO subject_calculation_definitions VALUES ('tenant',?,?,?,'approved',NULL)",
                       (SUBJECT, json.dumps(plan), configuration_sha256(plan)))
        core, components = history()
        for row in core["performance"]["series"]:
            self.c.execute("INSERT INTO subject_nav_series VALUES ('tenant',?,?,'HKD',?)",
                           (SUBJECT, row["date"], repr(row["nav"])))
        for row in core["risk"]["risk_free"]["observations"]:
            self.c.execute("INSERT INTO risk_free_rates VALUES ('tenant',?,'HKAB-HIBOR-1M','HKD','1-month',?,'pass')",
                           (row["rate_date"], row["annual_rate"]))
        for row in components:
            self.c.execute("INSERT INTO synthetic_portfolio_components VALUES ('tenant',?,?,?,?,?,?,?,?,?,?,?)",
                           tuple(row[k] for k in ("portfolio_id", "snapshot_date", "component_id", "base_currency",
                                                  "coefficient", "source_value", "fx_rate_to_base", "base_value",
                                                  "normalized_value", "contribution", "direction")))
        self.c.commit()
        self.core = core

    def test_month_end_collection_reads_the_composite_without_benchmark_mappings(self):
        before = list(self.c.iterdump())
        results = _collect_tool_results(self.c, tenant_id="tenant", subject_id=SUBJECT,
                                        period_end=END.isoformat(), reporting_currency="HKD")
        self.assertEqual(before, list(self.c.iterdump()))
        payload, _, _, _ = assemble(results)
        self.assertEqual(payload["report_profile"]["style"], "alpha-world-t")
        self.assertEqual(payload["performance"]["series"], self.core["performance"]["series"])

    def test_broken_inputs_stop_the_snapshot(self):
        self.c.execute("DELETE FROM synthetic_portfolio_components WHERE snapshot_date = '2025-04-30'")
        with self.assertRaisesRegex(ReportingSnapshotError, "component history dates"):
            _collect_tool_results(self.c, tenant_id="tenant", subject_id=SUBJECT,
                                  period_end=END.isoformat(), reporting_currency="HKD")


if __name__ == "__main__":
    unittest.main()
