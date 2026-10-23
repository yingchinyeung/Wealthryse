"""Data contracts for the silver and gold tables (infra/medallion/contracts, ODCS v3).

Offline checks: every silver and gold object in the catalog has a well-formed contract in
the right layer, its freshness promise matches ibkr_etl.layer_freshness, and the
comparison that CI's agent-database job runs against a migrated schema classifies
changes correctly. tests/test_medallion_contracts_db.py does the schema comparison.
"""
from __future__ import annotations

import copy
import importlib.util
import re
import unittest
from pathlib import Path

from ibkr_etl import layer_freshness
from ibkr_etl import medallion_contracts as mc

ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "infra" / "medallion" / "catalog.json"
CONTRACTS = ROOT / "infra" / "medallion" / "contracts"
VERSION_COLUMNS = ("calculation_version", "analytics_version", "formula_version")


def _script():
    spec = importlib.util.spec_from_file_location("medallion_contracts_script",
                                                  ROOT / "scripts" / "medallion_contracts.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _rules(contract: dict) -> list[dict]:
    obj = mc.table_object(contract)
    rules = list(obj.get("quality") or [])
    for prop in obj.get("properties") or []:
        rules += prop.get("quality") or []
    return rules


class ContractFileTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog = mc.catalog_objects(CATALOG)
        cls.contracts = {path: mc.load(path) for path in sorted(CONTRACTS.rglob("*.yaml"))}

    def test_every_silver_and_gold_object_has_a_contract(self):
        self.assertTrue(self.catalog)
        missing = [f"{entry['layer']}/{name}.yaml" for name, entry in sorted(self.catalog.items())
                   if not mc.contract_path(CONTRACTS, entry["layer"], name).exists()]
        self.assertEqual(missing, [], "Write a contract for each (python scripts/medallion_contracts.py generate)")

    def test_no_contract_without_a_catalog_entry(self):
        expected = {mc.contract_path(CONTRACTS, entry["layer"], name) for name, entry in self.catalog.items()}
        self.assertEqual(sorted(str(p.relative_to(ROOT)) for p in set(self.contracts) - expected), [])

    def test_contracts_are_well_formed(self):
        for path, contract in self.contracts.items():
            with self.subTest(path=path.name):
                self.assertEqual(mc.validate(contract), [])

    def test_layer_and_names_match_the_catalog(self):
        for path, contract in self.contracts.items():
            entry = self.catalog.get(path.stem)
            with self.subTest(path=path.name):
                self.assertIsNotNone(entry)
                layer = entry["layer"]
                self.assertEqual(path.parent.name, layer)
                self.assertEqual(contract["domain"], layer)
                self.assertEqual(contract["name"], path.stem)
                self.assertEqual(contract["id"], f"wealthryse.{layer}.{path.stem}")
                obj = mc.table_object(contract)
                self.assertEqual(obj["physicalName"], f"{layer}.{path.stem}")
                self.assertEqual(obj["physicalType"], "view" if entry["kind"] == "view" else "table")
                self.assertEqual(mc.custom(contract.get("customProperties"), "source"), entry["source"])

    def test_freshness_promise_matches_layer_freshness(self):
        # A table's first rule is its freshness promise (scripts/medallion_contracts.py _sla); a later
        # rule on the same table, such as one Kiwoom account's NAV date, is an extra check.
        rules: dict = {}
        for rule in layer_freshness.RULES:
            if rule.layer in mc.LAYERS:
                rules.setdefault(rule.target.split(".", 1)[1], rule)
        for path, contract in self.contracts.items():
            with self.subTest(path=path.name):
                rule = rules.get(path.stem)
                sla = {item["property"]: item for item in contract.get("slaProperties") or []}
                if rule is None:
                    self.assertEqual(sla, {})
                    continue
                self.assertEqual(sla["timeOfAvailability"]["value"], f"{rule.due_utc:%H:%M}+00:00")
                latency = sla["latency"]
                if rule.measure == layer_freshness.AS_OF:
                    self.assertEqual((latency["value"], latency["unit"]), (rule.max_age.days, "d"))
                else:
                    self.assertEqual((latency["value"], latency["unit"]),
                                     (int(rule.max_age.total_seconds() // 3600), "h"))
                column = latency["element"].split(".", 1)[1]
                self.assertIn(f"MAX({column})", rule.query)
                self.assertEqual(mc.custom(contract["customProperties"], "freshnessCheck")["checkId"], rule.check_id)

    def test_gold_tables_name_their_calculation_version_column(self):
        for path, contract in self.contracts.items():
            if contract["domain"] != "gold":
                continue
            columns = [p["name"] for p in mc.table_object(contract)["properties"]]
            named = mc.custom(contract.get("customProperties"), "calculationVersionColumn")
            with self.subTest(path=path.name):
                if any(c in columns for c in VERSION_COLUMNS):
                    self.assertIn(named, columns)
                else:
                    self.assertIsNone(named)

    def test_quality_rules_are_counts_against_their_own_table(self):
        for path, contract in self.contracts.items():
            physical = mc.table_object(contract)["physicalName"]
            for rule in _rules(contract):
                with self.subTest(path=path.name, rule=rule["name"]):
                    self.assertEqual(rule["type"], "sql")
                    self.assertTrue(rule["query"].startswith("SELECT COUNT(*) FROM "))
                    self.assertIn(physical, rule["query"])
                    self.assertEqual(rule.get("mustBe"), 0)
                    # Rules run whatever the search_path, so every table is named with its layer.
                    for name in re.findall(r"\b(?:FROM|JOIN)\s+([a-z_][a-z0-9_.]*)", rule["query"]):
                        self.assertIn(name.split(".")[0] if "." in name else "", mc.LAYERS, name)

    def test_no_account_numbers_or_email_addresses(self):
        for path in sorted(CONTRACTS.rglob("*")):
            if path.is_file():
                text = path.read_text(encoding="utf-8")
                with self.subTest(path=path.name):
                    self.assertIsNone(re.search(r"\bU\d{5,}\b", text))
                    self.assertNotIn("@", text)


class ValidatorTests(unittest.TestCase):
    def setUp(self):
        self.good = mc.load(CONTRACTS / "silver" / "fx_rates.yaml")

    def test_a_real_contract_is_valid(self):
        self.assertEqual(mc.validate(self.good), [])

    def test_shape_errors_are_reported(self):
        cases = {
            "apiVersion": lambda c: c.update(apiVersion="v2.2.0"),
            "version": lambda c: c.update(version="1.0"),
            "status": lambda c: c.update(status="live"),
            "unknown keys": lambda c: c.update(owner="someone"),
            "severity": lambda c: mc.table_object(c)["properties"][1]["quality"][0].update(severity="info"),
            "mustBe": lambda c: mc.table_object(c)["properties"][1]["quality"][0].pop("mustBe"),
            "primaryKeyPosition": lambda c: mc.table_object(c)["properties"][1].update(primaryKeyPosition=7),
            "classification": lambda c: mc.table_object(c)["properties"][0].update(classification="secret"),
            "email": lambda c: c["team"][0].update(username="someone@example.com"),
            "team": lambda c: c.update(team=[]),
            "layer schema": lambda c: mc.table_object(c).update(physicalName="public.fx_rates"),
            "appears twice": lambda c: mc.table_object(c)["properties"].append(
                copy.deepcopy(mc.table_object(c)["properties"][0])),
        }
        for expected, change in cases.items():
            contract = copy.deepcopy(self.good)
            change(contract)
            with self.subTest(case=expected):
                errors = mc.validate(contract)
                self.assertTrue(any(expected in error for error in errors), errors)


def _structure(columns, key=(), unique=(), kind="table"):
    return {"physicalType": kind, "primaryKey": list(key), "unique": list(unique),
            "columns": [{"name": n, "physicalType": t, "required": r} for n, t, r in columns]}


class CompareTests(unittest.TestCase):
    def setUp(self):
        self.contract = {"name": "t", "schema": [{
            "physicalName": "silver.t", "physicalType": "table",
            "properties": [
                {"name": "id", "physicalType": "text", "required": True, "unique": True, "primaryKey": True,
                 "primaryKeyPosition": 1},
                {"name": "amount", "physicalType": "numeric(12,2)", "required": True, "unique": False,
                 "primaryKey": False, "primaryKeyPosition": -1},
                {"name": "note", "physicalType": "text", "required": False, "unique": False, "primaryKey": False,
                 "primaryKeyPosition": -1},
            ]}]}
        self.same = [("id", "text", True), ("amount", "numeric(12,2)", True), ("note", "text", False)]

    def levels(self, actual):
        return sorted(d.level for d in mc.compare(self.contract, actual))

    def test_matching_schema_has_no_differences(self):
        self.assertEqual(mc.compare(self.contract, _structure(self.same, ["id"], ["id"])), [])

    def test_added_column_is_minor(self):
        actual = _structure([*self.same, ("extra", "text", False)], ["id"], ["id"])
        self.assertEqual(self.levels(actual), [mc.MINOR])

    def test_removed_or_renamed_column_is_major(self):
        actual = _structure([self.same[0], self.same[1], ("memo", "text", False)], ["id"], ["id"])
        self.assertEqual(self.levels(actual), [mc.MAJOR, mc.MINOR])

    def test_type_changes(self):
        narrowed = _structure([self.same[0], ("amount", "integer", True), self.same[2]], ["id"], ["id"])
        widened = _structure([self.same[0], ("amount", "numeric(14,2)", True), self.same[2]], ["id"], ["id"])
        self.assertEqual(self.levels(narrowed), [mc.MAJOR])
        self.assertEqual(self.levels(widened), [mc.MINOR])

    def test_nullability(self):
        relaxed = _structure([self.same[0], ("amount", "numeric(12,2)", False), self.same[2]], ["id"], ["id"])
        tightened = _structure([*self.same[:2], ("note", "text", True)], ["id"], ["id"])
        self.assertEqual(self.levels(relaxed), [mc.MAJOR])
        self.assertEqual(self.levels(tightened), [mc.MINOR])

    def test_key_and_kind_changes_are_major(self):
        self.assertIn(mc.MAJOR, self.levels(_structure(self.same, ["id", "note"], [])))
        self.assertIn(mc.MAJOR, self.levels(_structure(self.same, ["id"], ["id"], kind="view")))
        self.assertEqual(self.levels(None), [mc.MAJOR])

    def test_advice_says_which_version_step(self):
        major = mc.advice(mc.compare(self.contract, None))
        minor = mc.advice(mc.compare(self.contract, _structure([*self.same, ("x", "text", False)], ["id"], ["id"])))
        self.assertIn("raise its version by a MAJOR step", major)
        self.assertIn("raise its version by a MINOR step", minor)
        self.assertIn("infra/medallion/contracts/", minor)
        self.assertEqual(mc.advice([]), "")

    def test_logical_types(self):
        for physical, logical in [("text", "string"), ("uuid", "string"), ("bigint", "integer"),
                                  ("double precision", "number"), ("numeric(20,8)", "number"),
                                  ("boolean", "boolean"), ("jsonb", "object"), ("text[]", "array"),
                                  ("timestamp with time zone", "date"), ("date", "date")]:
            self.assertEqual(mc.logical_type(physical), logical, physical)


class GeneratorMergeTests(unittest.TestCase):
    def test_structure_is_rewritten_and_hand_written_parts_are_kept(self):
        script = _script()
        existing = mc.load(CONTRACTS / "silver" / "fx_rates.yaml")
        entry = mc.catalog_objects(CATALOG)["fx_rates"]
        actual = {
            "physicalType": "table", "primaryKey": ["account_id", "rate_date", "from_currency", "to_currency"],
            "unique": [], "views": [], "columnComments": {},
            "columns": [{"name": p["name"], "physicalType": p["physicalType"], "required": p["required"]}
                        for p in mc.table_object(existing)["properties"]]
                       + [{"name": "added_col", "physicalType": "text", "required": False}],
        }
        merged = script.merge("fx_rates", entry, actual, copy.deepcopy(existing), {"readers": [], "writers": []})
        self.assertEqual(mc.validate(merged), [])
        self.assertEqual(merged["version"], existing["version"])
        self.assertEqual(merged["description"], existing["description"])
        props = {p["name"]: p for p in mc.table_object(merged)["properties"]}
        old = {p["name"]: p for p in mc.table_object(existing)["properties"]}
        self.assertEqual(props["rate_text"]["quality"], old["rate_text"]["quality"])
        self.assertEqual(props["rate_date"]["logicalType"], "date")
        self.assertEqual(props["account_id"]["classification"], "restricted")
        self.assertEqual(props["added_col"]["primaryKeyPosition"], -1)
        self.assertEqual(mc.table_object(merged)["quality"], mc.table_object(existing)["quality"])


if __name__ == "__main__":
    unittest.main()
