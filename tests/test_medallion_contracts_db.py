"""Data contracts against the real schema: every silver and gold contract matches a database
built from migrations/postgres (then the held migrations the catalog names), and every quality
rule runs on it.

Needs a disposable server: set AGENT_TEST_PG_ADMIN_DSN to a superuser DSN (CI's
agent-database job starts one). Skipped otherwise. Creates and drops its own database
and role, and uses no data.

When a migration changes a silver or gold table, the comparison fails until the
contract under infra/medallion/contracts/ is updated and its version raised: a MAJOR
step for a removed or renamed column, a type change, a column that now allows nulls
or a key change; a MINOR step for an added column or a tightened rule.
`python scripts/medallion_contracts.py generate` rewrites the structural part.
"""
from __future__ import annotations

import importlib.util
import os
import unittest
import uuid
from pathlib import Path

ADMIN_DSN = os.environ.get("AGENT_TEST_PG_ADMIN_DSN", "")
ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "infra" / "medallion" / "catalog.json"
CONTRACTS = ROOT / "infra" / "medallion" / "contracts"
TENANT = "00000000-0000-0000-0000-000000000001"


def _script():
    spec = importlib.util.spec_from_file_location("medallion_contracts_script",
                                                  ROOT / "scripts" / "medallion_contracts.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@unittest.skipUnless(ADMIN_DSN, "AGENT_TEST_PG_ADMIN_DSN is not set")
class ContractSchemaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import psycopg

        from ibkr_etl import medallion_contracts as mc

        cls.mc = mc
        cls.script = _script()
        cls.database = f"contracts_{uuid.uuid4().hex[:10]}"
        cls.addClassCleanup(cls.script.drop, ADMIN_DSN, cls.database)
        cls.script.build(ADMIN_DSN, cls.database, ROOT / "migrations" / "postgres",
                         mc.held_migrations(CATALOG, ROOT))
        cls.catalog = mc.catalog_objects(CATALOG)
        cls.connection = psycopg.connect(**cls.script._params(ADMIN_DSN, cls.database, cls.script.OWNER, TENANT),
                                         autocommit=True)
        cls.addClassCleanup(cls.connection.close)
        cls.structure = cls.script.read_structure(cls.connection, cls.catalog)

    def _contract(self, name):
        entry = self.catalog[name]
        return self.mc.load(self.mc.contract_path(CONTRACTS, entry["layer"], name))

    def test_every_contract_matches_the_migrated_schema(self):
        diffs = []
        for name in sorted(self.catalog):
            diffs += self.mc.compare(self._contract(name), self.structure[name])
        self.assertEqual(diffs, [], self.mc.advice(diffs))

    def test_every_quality_rule_runs(self):
        """On an empty database each rule must run and count nothing: right table and column names, valid SQL."""
        ran = 0
        for name in sorted(self.catalog):
            obj = self.mc.table_object(self._contract(name))
            rules = list(obj.get("quality") or [])
            for prop in obj["properties"]:
                rules += prop.get("quality") or []
            for rule in rules:
                with self.subTest(table=name, rule=rule["name"]):
                    self.assertEqual(self.connection.execute(rule["query"]).fetchone()[0], rule["mustBe"])
                ran += 1
        self.assertGreater(ran, 0)

    def test_a_schema_change_is_caught(self):
        with self.connection.transaction(force_rollback=True):
            self.connection.execute("ALTER TABLE silver.fx_rates ADD COLUMN contract_probe TEXT")
            self.connection.execute("ALTER TABLE silver.fx_rates ALTER COLUMN rate_text DROP NOT NULL")
            changed = self.mc.introspect(self.connection, "silver.fx_rates")
        diffs = self.mc.compare(self._contract("fx_rates"), changed)
        self.assertEqual(sorted(d.level for d in diffs), [self.mc.MAJOR, self.mc.MINOR])
        self.assertIn("MAJOR step", self.mc.advice(diffs))


if __name__ == "__main__":
    unittest.main()
