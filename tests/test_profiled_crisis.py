"""Crisis replay for composites in the Alpha World design, on made-up data only."""
import copy
import json
import sqlite3
import unittest

from ibkr_etl import profiled_crisis as crisis
from ibkr_etl import profiled_reporting as pr
from ibkr_etl.alpha_world_ms_crisis import SOURCE_VERSION, market_sources
from ibkr_etl.internal_release import approve_internal_release
from ibkr_etl.subject_onboarding import configuration_sha256, fixed_unit_composite_definition

from test_profiled_composite import INCEPTION, SUBJECT, definition as asia_definition
from test_profiled_reporting import END, assemble, history

DAY = "2026-09-30"
ACCOUNT = "U00000001"
DAYS = market_sources()["calendar"]


def wide_definition(**changes):
    """Every sleeve kind: a portfolio, an account and both archived hedges."""
    components = [
        {"component_id": "hong-kong-china", "component_name": "China & Hong Kong", "component_type": "portfolio",
         "coefficient": 0.91, "source_currency": "USD", "price_convention": "nav"},
        {"component_id": ACCOUNT, "component_name": "Made-up account", "component_type": "account",
         "coefficient": 0.45, "source_currency": "USD", "price_convention": "nav"},
        {"component_id": "hang-seng-price-index", "component_name": "Hang Seng Index price index",
         "component_type": "benchmark", "coefficient": -0.49, "source_currency": "HKD",
         "price_convention": "price_index_close"},
        {"component_id": "spy-settlement", "component_name": "SPY official close", "component_type": "benchmark",
         "coefficient": -0.45, "source_currency": "USD", "price_convention": "unadjusted_official_close"},
    ]
    fields = dict(subject_id=SUBJECT, display_name="Alpha World Asia", base_currency="HKD",
                  normalization_date=INCEPTION.isoformat(), constant_cash=58.0, components=components)
    fields.update(changes)
    return fixed_unit_composite_definition(**fields)


def portfolio_records(day, subject="hong-kong-china"):
    """A stored sleeve replay with the archived prices it is rebuilt from."""
    return {
        "source_nav": [{"subject_id": subject, "snapshot_date": day, "currency": "USD", "total_text": "100"}],
        "source_positions": [{"subject_id": subject, "snapshot_date": day, "conid": "1", "currency": "USD",
                              "market_value_text": "80"}],
        "mappings": [{"subject_id": subject, "holdings_as_of": day, "conid": "1", "currency": "USD",
                      "price_type": "adjusted_close", "mapping_method": "direct", "source_symbol": "X",
                      "current_market_value": 80, "opening_weight": .8}],
        "replays": [{"subject_id": subject, "holdings_as_of": day, "observation_date": d,
                     "calculation_version": SOURCE_VERSION, "quality_status": "pass",
                     "normalized_value": 1 - .004 * i, "drawdown": -.004 * i} for i, d in enumerate(DAYS)],
        "source_fx": [],
        "prices": [{"symbol": "X", "price_date": d, "price": 100 - i / 2, "currency": "USD",
                    "price_type": "adjusted_close"} for i, d in enumerate(DAYS)],
    }


def account_records(day):
    """Exact broker holdings: a Taiwan-dollar Paramount line, so the proxy and broker FX both apply."""
    return {
        "source_nav": [{"subject_id": ACCOUNT, "snapshot_date": day, "currency": "USD", "total_text": "100"}],
        "source_positions": [{"subject_id": ACCOUNT, "snapshot_date": day, "conid": "804144296",
                              "currency": "TWD", "market_value_text": "2400"}],
        "broker_fx": [{"account_id": ACCOUNT, "rate_date": day, "from_currency": "TWD", "to_currency": "USD",
                       "rate_text": str(1 / 30)}],
        "mappings": [{"subject_id": ACCOUNT, "holdings_as_of": day, "conid": "804144296", "holding_symbol": "PSKY",
                      "currency": "USD", "price_type": "adjusted_close", "mapping_method": "direct",
                      "source_symbol": "PSKY", "current_market_value": 80, "opening_weight": .8}],
        "prices": [{"symbol": "SPY", "price_date": d, "price": 100 - i / 3, "currency": "USD",
                    "price_type": "adjusted_close"} for i, d in enumerate(DAYS)],
    }


def inputs(plan=None):
    plan = plan or wide_definition()
    return {"subject_id": SUBJECT, "reporting_currency": "HKD", "requested_as_of": DAY, "holdings_as_of": DAY,
            "contract_sha256": configuration_sha256(plan), "definition": plan, "nav": "100",
            "component_values": {"hong-kong-china": "91", ACCOUNT: "45", "hang-seng-price-index": "-49",
                                 "spy-settlement": "-45", "constant-cash": "58"},
            "records": {"accounts": account_records(DAY), "portfolios": portfolio_records(DAY)}}


class ReplayTests(unittest.TestCase):
    def test_every_sleeve_kind_replays_reproducibly_in_hkd(self):
        source = inputs()
        before = copy.deepcopy(source)
        replay = crisis.compute(source)
        self.assertEqual(source, before)
        self.assertEqual(crisis.validate(json.loads(json.dumps(replay))), replay)
        self.assertEqual((replay["observations"], replay["crisis_start"], replay["crisis_end"]),
                         (121, "2008-09-15", "2009-03-09"))
        self.assertEqual(replay["daily"][0]["normalized_value"], 1.0)
        self.assertLess(replay["maximum_drawdown"], 0)
        # Paramount takes the SPY proxy; the portfolio's X and both hedges are direct.
        self.assertEqual((replay["direct_components"], replay["proxy_components"]), (3, 1))
        mapping = replay["sleeve_evidence"][ACCOUNT]["components"][0]["mapping_evidence"]
        self.assertEqual((mapping["source_symbol"], mapping["proxy_policy_version"]), ("SPY", crisis.PROXY_VERSION))
        self.assertEqual((replay["qualification_status"], replay["publication_status"]),
                         ("qualified_internal", "blocked_provisional_market_data"))

    def test_a_tampered_result_is_refused(self):
        replay = crisis.compute(inputs())
        for key, value in (("maximum_drawdown", -.99), ("quality_status", "pass"), ("publication_status", "allowed"),
                           ("proxy_policy_version", "other"), ("market_source_sha256", "0" * 64)):
            with self.subTest(key=key):
                changed = copy.deepcopy(replay)
                changed[key] = value
                with self.assertRaises(ValueError):
                    crisis.validate(changed)
        with self.assertRaisesRegex(ValueError, "other components"):
            crisis.validate(replay, holdings=[{"conid": "constant-cash", "currency": "HKD", "market_value_text": "58"}])
        with self.assertRaisesRegex(ValueError, "another definition"):
            crisis.validate(replay, contract_sha256="0" * 64)

    def test_inputs_outside_the_approved_definition_fail_closed(self):
        changes = (
            lambda s: s.update(contract_sha256="0" * 64),
            lambda s: s.update(reporting_currency="USD"),
            lambda s: s.update(holdings_as_of="2026-10-01"),
            lambda s: s.update(nav="101"),
            lambda s: s["component_values"].update({"constant-cash": "59", "hong-kong-china": "90"}),
            lambda s: s["component_values"].pop(ACCOUNT),
            lambda s: s["records"]["portfolios"]["prices"].pop(),
            lambda s: s["records"]["accounts"]["broker_fx"].clear(),
        )
        for number, change in enumerate(changes):
            with self.subTest(change=number):
                source = inputs()
                change(source)
                with self.assertRaises((ValueError, KeyError)):
                    crisis.compute(source)

    def test_a_benchmark_without_an_archived_series_has_no_replay(self):
        plan = wide_definition()
        plan["components"][2] = {**plan["components"][2], "component_id": "csi-300-price-index",
                                 "component_name": "CSI 300 price index"}
        source = inputs(plan)
        source["component_values"]["csi-300-price-index"] = source["component_values"].pop("hang-seng-price-index")
        with self.assertRaisesRegex(ValueError, "No archived crisis-period series for CSI 300"):
            crisis.compute(source)


class ReportingTests(unittest.TestCase):
    def setUp(self):
        self.core, self.components = history()
        self.plan = asia_definition()
        snapshot = pr.enrich(self.core, self.components, self.plan)
        self.replay = crisis.build(self.plan, snapshot, {"accounts": None,
                                                         "portfolios": portfolio_records(self.core["data_as_of"])})

    def test_the_replay_clears_the_crisis_blocker_and_leaves_only_licence_flags(self):
        snapshot = pr.enrich(self.core, self.components, self.plan, self.replay)
        self.assertEqual(snapshot["risk"]["crisis_replay"], self.replay)
        payload, quality, publication_allowed, blockers = assemble(pr.tool_results(snapshot))
        self.assertEqual((quality, publication_allowed), ("warn", False))
        self.assertNotIn("risk:crisis_replay_unavailable", blockers)
        self.assertTrue(blockers and all(b.endswith("benchmark:not_licensed_for_publication") for b in blockers))
        approval = approve_internal_release(
            {"snapshot_id": "s", "subject_id": SUBJECT, "period_end": END.isoformat(), "status": "frozen",
             "quality_status": quality, "publication_blockers_json": json.dumps(blockers)},
            [{"report_type": "factsheet", "snapshot_id": "s", "status": "awaiting_approval",
              "output_sha256": "a" * 64, "generation_run_id": 1}], user_acceptance_recorded=True)
        self.assertEqual(approval.blockers, tuple(sorted(blockers)))

    def test_a_replay_for_other_components_is_refused(self):
        changed = copy.deepcopy(self.components)
        last = [row for row in changed if row["snapshot_date"] == self.core["data_as_of"]]
        for row in last:
            if row["component_id"] == "hang-seng-price-index":
                row["normalized_value"] *= 1.01
                row["contribution"] = row["coefficient"] * row["normalized_value"]
        with self.assertRaises(ValueError):
            pr.enrich(self.core, changed, self.plan, self.replay)


class StoredInputTests(unittest.TestCase):
    """The loaders read the composite's exact-date sleeve evidence and write nothing."""

    def setUp(self):
        from test_profiled_reporting import CollectTests
        case = CollectTests()
        case.setUp()
        self.addCleanup(case.doCleanups)
        self.c = case.c
        day = case.core["data_as_of"]
        evidence = portfolio_records(day)
        tables = {"source_nav": "subject_nav_series", "source_positions": "subject_position_series",
                  "mappings": "crisis_replay_components", "replays": "crisis_replay_daily",
                  "prices": "market_data_prices", "source_fx": "reference_fx_rates"}
        for key, table in tables.items():
            for row in evidence[key]:
                row = {"tenant_id": "tenant", **row, **({"instrument_id": "crisis-replay-X"} if key == "prices" else {})}
                self.c.execute(f"INSERT INTO {table} ({', '.join(row)}) VALUES ({', '.join('?' for _ in row)})",
                               tuple(row.values()))
        self.c.commit()

    def test_month_end_inputs_carry_the_qualified_replay(self):
        before = list(self.c.iterdump())
        snapshot = pr.load(self.c, SUBJECT, END.isoformat(), tenant_id="tenant")
        self.assertEqual(before, list(self.c.iterdump()))
        replay = snapshot["risk"]["crisis_replay"]
        self.assertEqual(replay["calculation_version"], crisis.VERSION)
        self.assertNotIn("risk:crisis_replay_unavailable", snapshot["publication_blockers"])

    def test_missing_sleeve_evidence_shows_the_replay_as_unavailable(self):
        self.c.execute("DELETE FROM crisis_replay_daily")
        snapshot = pr.load(self.c, SUBJECT, END.isoformat(), tenant_id="tenant")
        self.assertIsNone(snapshot["risk"]["crisis_replay"])
        self.assertIn("risk:crisis_replay_unavailable", snapshot["publication_blockers"])


class RuntimeAndDeckTests(unittest.TestCase):
    def test_the_risk_runtime_carries_and_rechecks_the_replay(self):
        from ibkr_etl import profiled_composite as pc
        from test_profiled_composite import inputs as risk_inputs, snapshot

        source = snapshot()
        nav = source["performance"]["series"][-1]["nav"]
        replay = crisis.compute({
            "subject_id": SUBJECT, "reporting_currency": "HKD", "requested_as_of": source["period_end"],
            "holdings_as_of": source["data_as_of"], "contract_sha256": configuration_sha256(asia_definition()),
            "definition": asia_definition(), "nav": str(nav),
            "component_values": {"hong-kong-china": str(nav - 58 + 49), "hang-seng-price-index": "-49",
                                 "constant-cash": "58"},
            "records": {"accounts": None, "portfolios": portfolio_records(source["data_as_of"])}})
        payload = pc.build_payload({**risk_inputs(source), "crisis_replay": replay})
        self.assertEqual(payload["metrics"]["crisis_maximum_drawdown"], replay["maximum_drawdown"])
        self.assertNotIn("crisis_replay", payload["unavailable_capabilities"])
        self.assertTrue(pc.validate_payload(json.loads(json.dumps(payload))))
        tampered = copy.deepcopy(payload)
        tampered["crisis_replay"]["maximum_drawdown"] = -.5
        with self.assertRaises(ValueError):
            pc.validate_payload(tampered)

    def test_the_deck_model_shows_the_replay(self):
        from ibkr_etl.composite_presentation import build_model
        from test_profiled_factsheet import payload

        frozen = payload("alpha-world-ms")
        frozen["risk"]["crisis_replay"] = crisis.build(
            asia_definition(), frozen, {"accounts": None, "portfolios": portfolio_records(frozen["data_as_of"])})
        model = build_model(frozen)
        self.assertTrue(model["crisis_available"])
        self.assertEqual(model["risk"]["crisis_maximum_drawdown"], frozen["risk"]["crisis_replay"]["maximum_drawdown"])
        self.assertIn("hypothetical HKD stress", model["identity"]["speaker_note"])
        frozen["holdings"]["holdings"][0]["market_value_text"] = "1"
        with self.assertRaises(ValueError):
            build_model(frozen)


if __name__ == "__main__":
    unittest.main()
